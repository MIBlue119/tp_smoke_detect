"""Bounded MQTT delivery adapter for ``track.candidate.v1``.

The adapter is intentionally independent of the paho package at import time;
the CPU profile remains dependency-free.  A paho v2 client can be supplied by
the GPU deployment, while tests use the public ``submit`` method with explicit
ack/retry/dead-letter callbacks.
"""

from __future__ import annotations

import logging
import queue
import threading
from collections.abc import Callable
from copy import copy
from dataclasses import dataclass
from typing import Any

from ...application.candidate_processing import (
    CandidateMessageError,
    CandidateProcessingError,
    CandidateProcessingService,
)
from ...observability.health import HealthRegistry, HealthState
from ...observability.metrics import OperationalMetrics

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class MqttDelivery:
    """One broker delivery and its explicit lifecycle callbacks."""

    payload: bytes | str
    topic: str = "track.candidate.v1"
    message_id: str | None = None
    attempts: int = 0
    ack: Callable[[], None] = lambda: None
    retry: Callable[[int], None] = lambda _attempt: None
    dead_letter: Callable[[str], None] = lambda _reason: None


@dataclass(frozen=True, slots=True)
class MqttConsumerConfig:
    topic: str = "track.candidate.v1"
    dead_letter_topic: str = "track.candidate.v1.dead-letter"
    max_inflight: int = 32
    retry_limit: int = 3
    publish_timeout_seconds: float = 10.0

    def __post_init__(self) -> None:
        if not self.topic or not self.dead_letter_topic:
            raise ValueError("MQTT topics must not be empty")
        if self.max_inflight < 1:
            raise ValueError("max_inflight must be positive")
        if self.retry_limit < 0:
            raise ValueError("retry_limit must not be negative")
        if self.publish_timeout_seconds <= 0:
            raise ValueError("publish_timeout_seconds must be positive")


class MqttCandidateConsumer:
    """Threaded, bounded consumer with durable-completion acknowledgements."""

    def __init__(
        self,
        service: CandidateProcessingService,
        *,
        config: MqttConsumerConfig | None = None,
        client: Any | None = None,
        metrics: OperationalMetrics | None = None,
        health: HealthRegistry | None = None,
    ) -> None:
        self.service = service
        self.config = config or MqttConsumerConfig()
        self.client = client
        self.metrics = metrics
        self.health = health
        self._queue: queue.Queue[MqttDelivery] = queue.Queue(maxsize=self.config.max_inflight)
        self._stop = threading.Event()
        self._workers: list[threading.Thread] = []
        self._started = False
        self._lock = threading.RLock()
        if self.health is not None:
            self.health.set_component("candidate-broker", HealthState.UNKNOWN)

    @property
    def queue_depth(self) -> int:
        return self._queue.qsize()

    @property
    def ready(self) -> bool:
        return self._started and not self._stop.is_set() and self.service.readiness()

    def submit(self, delivery: MqttDelivery) -> bool:
        """Enqueue without blocking; false means broker backpressure applied."""

        if delivery.topic != self.config.topic:
            delivery.dead_letter("unexpected_topic")
            return False
        if self._stop.is_set():
            delivery.retry(delivery.attempts + 1)
            return False
        try:
            self._queue.put_nowait(delivery)
        except queue.Full:
            if self.metrics is not None:
                self.metrics.candidate_retry("backpressure")
            delivery.retry(delivery.attempts + 1)
            return False
        self._record_queue()
        return True

    def start(self, *, workers: int | None = None) -> None:
        with self._lock:
            if self._started:
                return
            count = workers or min(4, self.config.max_inflight)
            if count < 1:
                raise ValueError("workers must be positive")
            self._stop.clear()
            self._workers = [
                threading.Thread(target=self._run, name=f"candidate-worker-{i}", daemon=True)
                for i in range(count)
            ]
            for worker in self._workers:
                worker.start()
            self._started = True
            if self.client is not None:
                self._bind_client()
            if self.health is not None:
                self.health.set_component("candidate-broker", HealthState.HEALTHY)

    def stop(self, *, timeout: float = 10.0) -> None:
        """Stop intake and wait for queued work to finish or retry."""

        with self._lock:
            if not self._started:
                return
            self._stop.set()
            if self.client is not None:
                loop_stop = getattr(self.client, "loop_stop", None)
                if callable(loop_stop):
                    loop_stop()
        for worker in self._workers:
            worker.join(timeout=max(timeout, 0.0))
        with self._lock:
            self._workers = []
            self._started = False
            self._record_queue()
            if self.health is not None:
                self.health.set_component("candidate-broker", HealthState.UNKNOWN)

    def _run(self) -> None:
        while not self._stop.is_set() or not self._queue.empty():
            try:
                delivery = self._queue.get(timeout=0.1)
            except queue.Empty:
                continue
            try:
                self._handle(delivery)
            finally:
                self._queue.task_done()
                self._record_queue()

    def _handle(self, delivery: MqttDelivery) -> None:
        try:
            result = self.service.process_payload(delivery.payload)
        except CandidateMessageError as exc:
            delivery.dead_letter(type(exc).__name__)
            if self.metrics is not None:
                self.metrics.candidate_deadletter(type(exc).__name__)
            return
        except CandidateProcessingError as exc:
            if delivery.attempts < self.config.retry_limit:
                if self.metrics is not None:
                    self.metrics.candidate_retry(type(exc).__name__)
                delivery.retry(delivery.attempts + 1)
            else:
                delivery.dead_letter("retry_exhausted")
                if self.metrics is not None:
                    self.metrics.candidate_deadletter("retry_exhausted")
            return
        except Exception:
            logger.exception("unexpected candidate consumer failure")
            if delivery.attempts < self.config.retry_limit:
                delivery.retry(delivery.attempts + 1)
            else:
                delivery.dead_letter("unexpected_error")
            return
        delivery.ack()
        if self.metrics is not None and result.status == "duplicate":
            self.metrics.candidate_message("duplicate")

    def _record_queue(self) -> None:
        if self.metrics is not None:
            self.metrics.candidate_queue(self.queue_depth)

    def _bind_client(self) -> None:
        """Bind a paho-compatible client without importing paho in CPU mode."""

        assert self.client is not None

        def on_connect(
            client: Any, _userdata: Any, _flags: Any, reason_code: Any, *_args: Any
        ) -> None:
            if int(reason_code) != 0:
                if self.health is not None:
                    self.health.set_component(
                        "candidate-broker", HealthState.DEGRADED, message="broker connect failed"
                    )
                return
            client.subscribe(self.config.topic, qos=1)
            if self.health is not None:
                self.health.set_component("candidate-broker", HealthState.HEALTHY)

        def on_message(_client: Any, _userdata: Any, message: Any) -> None:
            def ack() -> None:
                self._ack_message(message)

            attempts = self._message_attempts(message)
            delivery = MqttDelivery(
                payload=message.payload,
                topic=str(message.topic),
                message_id=str(getattr(message, "mid", "")),
                attempts=attempts,
                ack=ack,
                retry=lambda attempt: self._republish(message, attempt),
                dead_letter=lambda reason: self._dead_letter(message, reason),
            )
            self.submit(delivery)

        self.client.on_connect = on_connect
        self.client.on_message = on_message
        loop_start = getattr(self.client, "loop_start", None)
        if callable(loop_start):
            loop_start()

    def _republish(self, message: Any, attempt: int) -> None:
        publish = getattr(self.client, "publish", None)
        if not callable(publish):
            return
        properties = self._with_attempt(getattr(message, "properties", None), attempt)
        if properties is None:
            logger.error("cannot persist MQTT retry attempt without MQTT v5 properties")
            return
        result = publish(
            self.config.topic, message.payload, qos=1, retain=False, properties=properties
        )
        if not self._publish_succeeded(result, timeout=self.config.publish_timeout_seconds):
            return
        self._ack_message(message)

    @staticmethod
    def _publish_succeeded(result: Any, *, timeout: float = 10.0) -> bool:
        """Require the broker PUBACK, not merely local client queueing.

        Paho's ``publish`` return code only reports that the message entered
        the client queue.  A delivery must not be acknowledged (or moved to a
        dead-letter topic) until the MQTTMessageInfo has observed PUBACK.
        Test doubles can implement the same contract with ``wait_for_publish``
        and ``is_published``; a bare ``None`` is deliberately a failure.
        """

        if result is None or int(getattr(result, "rc", 1)) != 0:
            return False
        wait_for_publish = getattr(result, "wait_for_publish", None)
        is_published = getattr(result, "is_published", None)
        if not callable(wait_for_publish):
            return bool(is_published()) if callable(is_published) else False
        try:
            wait_for_publish(timeout=max(0.1, timeout))
        except (RuntimeError, TimeoutError):
            return False
        if callable(is_published):
            try:
                return bool(is_published())
            except (RuntimeError, TypeError):
                return False
        # Paho versions that expose wait_for_publish without is_published
        # return None on success and raise on failure.
        return True

    @staticmethod
    def _message_attempts(message: Any) -> int:
        direct = getattr(message, "attempts", None)
        if isinstance(direct, int) and direct >= 0:
            return direct
        properties = getattr(message, "properties", None)
        for key, value in getattr(properties, "UserProperty", ()) or ():
            if str(key) == "x-smoke-delivery-attempt":
                try:
                    return max(0, int(value))
                except (TypeError, ValueError):
                    return 0
        return 0

    @staticmethod
    def _with_attempt(properties: Any, attempt: int) -> Any:
        if properties is None:
            try:
                from paho.mqtt.packettypes import PacketTypes
                from paho.mqtt.properties import Properties

                properties = Properties(PacketTypes.PUBLISH)  # type: ignore[no-untyped-call]
            except (ImportError, TypeError, ValueError):
                return None
        cloned = copy(properties)
        user_properties = [
            (str(key), str(value))
            for key, value in (getattr(cloned, "UserProperty", ()) or ())
            if str(key) != "x-smoke-delivery-attempt"
        ]
        user_properties.append(("x-smoke-delivery-attempt", str(attempt)))
        try:
            cloned.UserProperty = user_properties
        except (AttributeError, TypeError):
            return None
        return cloned

    def _ack_message(self, message: Any) -> None:
        """Acknowledge through Paho's client API after durable completion."""

        if self.client is None:
            return
        ack = getattr(self.client, "ack", None)
        if not callable(ack):
            logger.error("MQTT client does not expose manual acknowledgement API")
            return
        ack(int(message.mid), int(getattr(message, "qos", 1)))

    def _dead_letter(self, message: Any, reason: str) -> None:
        publish = getattr(self.client, "publish", None)
        if not callable(publish):
            return
        # Dead-letter payloads contain metadata only.  Even malformed input is
        # not copied to another topic, so a producer cannot smuggle pixels or
        # provider text into the audit/broker plane.
        import json

        payload = json.dumps(
            {
                "reason": reason,
                "topic": str(getattr(message, "topic", self.config.topic)),
                "message_id": str(getattr(message, "mid", "")),
            }
        ).encode()
        result = publish(self.config.dead_letter_topic, payload, qos=1, retain=False)
        if not self._publish_succeeded(result, timeout=self.config.publish_timeout_seconds):
            return
        self._ack_message(message)


__all__ = ["MqttCandidateConsumer", "MqttConsumerConfig", "MqttDelivery"]
