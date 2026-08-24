"""Low-cardinality Prometheus-compatible metrics.

The service does not depend on a Prometheus client in its CPU reference
install.  This small registry implements the subset needed by the API and
workers and emits the normal text exposition format.  Label names are an
allow-list by design: track IDs, decision IDs, URLs, and arbitrary model text
must never become time-series dimensions.
"""

from __future__ import annotations

import math
import threading
import time
from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass

from ..domain.models.decisions import ReasonCode

_FORBIDDEN_LABELS = {
    "camera_id",
    "camera_name",
    "device_id",
    "gpu_id",
    "gpu_uuid",
    "track_id",
    "decision_id",
    "artifact_id",
    "url",
    "raw_url",
    "request_id",
    "correlation_id",
}
_ALLOWED_LABELS = {
    "component",
    "outcome",
    "reason",
    "stage",
    "model_role",
    "mode",
    "media_class",
    "operation",
    "state",
    "status",
}
_BOUNDED_LABEL_VALUES = {
    "component": frozenset(
        {
            "api",
            "audio",
            "broker",
            "camera",
            "core",
            "database",
            "gpu",
            "media",
            "model",
            "nvdec",
            "retention",
            "triton",
            "other",
        }
    ),
    "media_class": frozenset({"raw", "event_clip", "metadata", "other"}),
    "mode": frozenset({"simulation", "replay", "shadow", "human_confirmed", "automatic", "other"}),
    "model_role": frozenset(
        {
            "person_detector",
            "pose",
            "hand_landmarker",
            "crop_classifier",
            "smoke_classifier",
            "reviewer",
            "other",
        }
    ),
    "operation": frozenset({"decode", "batch", "inference", "reconnect", "other"}),
    "outcome": frozenset(
        {
            "verified",
            "rejected",
            "unclear",
            "error",
            "met",
            "missed",
            "expired",
            "timeout",
            "other",
        }
    ),
    "reason": frozenset({"other"}),
    "stage": frozenset(
        {"detected", "quality", "pose", "object", "temporal", "review", "completed", "other"}
    ),
    "state": frozenset(
        {
            "healthy",
            "ready",
            "degraded",
            "unavailable",
            "unknown",
            "warming",
            "timeout",
            "failed",
            "other",
        }
    ),
    "status": frozenset(
        {
            "accepted",
            "duplicate",
            "rejected",
            "expired",
            "failed",
            "ok",
            "error",
            "timeout",
            "unavailable",
            "other",
        }
    ),
}
_DECISION_REASONS = frozenset(item.value for item in ReasonCode) | {
    "evaluation_pending_provider",
    "model_unavailable",
    "queue_saturated",
    "gpu_oom",
    "stream_stalled",
    "other",
}
_BOUNDED_LABEL_VALUES["reason"] = _DECISION_REASONS


def _escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace("\n", "\\n").replace('"', '\\"')


def _label_key(labels: Mapping[str, str]) -> tuple[tuple[str, str], ...]:
    return tuple(sorted((key, str(value)) for key, value in labels.items()))


@dataclass(frozen=True)
class _Definition:
    name: str
    help: str
    kind: str
    labels: tuple[str, ...]
    buckets: tuple[float, ...] = ()


class MetricRegistry:
    """Thread-safe metrics registry with fixed label schemas."""

    def __init__(self) -> None:
        self._definitions: dict[str, _Definition] = {}
        self._values: dict[str, dict[tuple[tuple[str, str], ...], float]] = defaultdict(dict)
        self._lock = threading.RLock()

    def _register(
        self,
        name: str,
        help_text: str,
        kind: str,
        labels: tuple[str, ...],
        buckets: tuple[float, ...] = (),
    ) -> None:
        if not name.replace("_", "").isalnum() or not name[0].isalpha():
            raise ValueError(f"invalid metric name: {name}")
        if set(labels) & _FORBIDDEN_LABELS:
            raise ValueError("high-cardinality labels are forbidden")
        unknown = set(labels) - _ALLOWED_LABELS
        if unknown:
            raise ValueError(f"labels are not allowed in metrics: {sorted(unknown)}")
        definition = _Definition(name, help_text, kind, labels, buckets)
        with self._lock:
            previous = self._definitions.get(name)
            if previous is not None and previous != definition:
                raise ValueError(f"metric already registered with another schema: {name}")
            self._definitions[name] = definition

    def counter(self, name: str, help_text: str, labels: tuple[str, ...] = ()) -> None:
        self._register(name, help_text, "counter", labels)

    def gauge(self, name: str, help_text: str, labels: tuple[str, ...] = ()) -> None:
        self._register(name, help_text, "gauge", labels)

    def histogram(
        self,
        name: str,
        help_text: str,
        labels: tuple[str, ...] = (),
        buckets: tuple[float, ...] = (0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.0, 5.0, 10.0),
    ) -> None:
        self._register(name, help_text, "histogram", labels, tuple(sorted(buckets)))

    def _key(
        self, definition: _Definition, labels: Mapping[str, str]
    ) -> tuple[tuple[str, str], ...]:
        if set(labels) != set(definition.labels):
            raise ValueError(
                f"{definition.name} requires labels {definition.labels}, "
                f"got {tuple(sorted(labels))}"
            )
        normalized = {key: self._normalize_label(key, value) for key, value in labels.items()}
        return _label_key(normalized)

    @staticmethod
    def _normalize_label(key: str, value: str) -> str:
        """Map untrusted values to a finite vocabulary before they become labels."""

        bounded = _BOUNDED_LABEL_VALUES.get(key)
        if bounded is None:
            return str(value)
        candidate = str(value)
        return candidate if candidate in bounded else "other"

    def inc(self, name: str, amount: float = 1.0, **labels: str) -> None:
        if amount < 0 or not math.isfinite(amount):
            raise ValueError("counter increment must be finite and non-negative")
        with self._lock:
            definition = self._definitions[name]
            if definition.kind != "counter":
                raise TypeError(f"{name} is not a counter")
            key = self._key(definition, labels)
            self._values[name][key] = self._values[name].get(key, 0.0) + amount

    def set(self, name: str, value: float, **labels: str) -> None:
        if not math.isfinite(value):
            raise ValueError("gauge value must be finite")
        with self._lock:
            definition = self._definitions[name]
            if definition.kind != "gauge":
                raise TypeError(f"{name} is not a gauge")
            self._values[name][self._key(definition, labels)] = value

    def observe(self, name: str, value: float, **labels: str) -> None:
        if not math.isfinite(value):
            raise ValueError("histogram value must be finite")
        with self._lock:
            definition = self._definitions[name]
            if definition.kind != "histogram":
                raise TypeError(f"{name} is not a histogram")
            key = self._key(definition, labels)
            # Histograms are represented as sample sum/count internally; the
            # exposition method expands them into cumulative bucket samples.
            count_key = key + (("__count", ""),)
            sum_key = key + (("__sum", ""),)
            self._values[name][count_key] = self._values[name].get(count_key, 0.0) + 1
            self._values[name][sum_key] = self._values[name].get(sum_key, 0.0) + value
            for bucket in definition.buckets:
                bucket_key = key + (("__bucket", str(bucket)),)
                if value <= bucket:
                    self._values[name][bucket_key] = self._values[name].get(bucket_key, 0.0) + 1

    def render(self) -> str:
        """Return deterministic Prometheus text exposition."""

        lines: list[str] = []
        with self._lock:
            for name in sorted(self._definitions):
                definition = self._definitions[name]
                lines.extend(
                    (f"# HELP {name} {definition.help}", f"# TYPE {name} {definition.kind}")
                )
                values = self._values[name]
                if definition.kind != "histogram":
                    for key, value in sorted(values.items()):
                        lines.append(f"{name}{self._format_labels(key)} {value:g}")
                    continue
                grouped: dict[tuple[tuple[str, str], ...], dict[str, float]] = defaultdict(dict)
                for key, value in values.items():
                    base = tuple(
                        pair for pair in key if pair[0] not in {"__count", "__sum", "__bucket"}
                    )
                    marker = dict(key)
                    if "__count" in marker:
                        grouped[base]["count"] = value
                    elif "__sum" in marker:
                        grouped[base]["sum"] = value
                    else:
                        grouped[base][f"bucket:{marker['__bucket']}"] = value
                for base, aggregate in sorted(grouped.items()):
                    for bucket in definition.buckets:
                        value = aggregate.get(f"bucket:{bucket}", 0.0)
                        lines.append(
                            f"{name}_bucket{self._format_labels(base, le=str(bucket))} {value:g}"
                        )
                    lines.append(
                        f"{name}_bucket{self._format_labels(base, le='+Inf')} "
                        f"{aggregate.get('count', 0):g}"
                    )
                    lines.append(
                        f"{name}_count{self._format_labels(base)} {aggregate.get('count', 0):g}"
                    )
                    lines.append(
                        f"{name}_sum{self._format_labels(base)} {aggregate.get('sum', 0):g}"
                    )
        return "\n".join(lines) + ("\n" if lines else "")

    @staticmethod
    def _format_labels(key: tuple[tuple[str, str], ...], **extra: str) -> str:
        labels = dict((k, v) for k, v in key if not k.startswith("__"))
        labels.update(extra)
        if not labels:
            return ""
        rendered = ",".join(f'{name}="{_escape(value)}"' for name, value in sorted(labels.items()))
        return "{" + rendered + "}"


class OperationalMetrics:
    """Named service metrics shared by API, workers, and health probes."""

    def __init__(self, registry: MetricRegistry | None = None) -> None:
        self.registry = registry or MetricRegistry()
        self._camera_last_good_frame: dict[str, float] = {}
        self._camera_queue_depth: dict[str, int] = {}
        self._camera_queue_age: dict[str, float] = {}
        self._camera_state: dict[str, str] = {}
        self._camera_lock = threading.RLock()
        r = self.registry
        r.gauge(
            "smoke_camera_last_good_frame_timestamp_seconds",
            "Unix timestamp of the latest good frame across cameras",
        )
        r.gauge("smoke_camera_frame_age_seconds", "Maximum age of a camera frame")
        r.gauge("smoke_camera_stale_count", "Number of cameras beyond the freshness deadline")
        r.gauge("smoke_camera_queue_depth", "Maximum bounded queue depth across cameras")
        r.gauge("smoke_camera_queue_age_seconds", "Maximum age of queued camera work")
        r.gauge("smoke_camera_streams", "Camera count by bounded health state", ("state",))
        r.counter("smoke_camera_sample_loss_total", "Scheduled camera samples not processed")
        r.histogram("smoke_stage_latency_seconds", "Stage processing latency", ("stage",))
        r.counter("smoke_decisions_total", "Completed decisions", ("outcome", "reason"))
        r.counter("smoke_model_requests_total", "Model requests", ("model_role", "status"))
        r.gauge(
            "smoke_model_readiness",
            "Model readiness by bounded role and state",
            ("model_role", "state"),
        )
        r.gauge("smoke_triton_pending_requests", "Pending Triton requests", ("model_role",))
        r.counter(
            "smoke_inference_failures_total",
            "Inference failures by bounded role and status",
            ("model_role", "status"),
        )
        r.counter(
            "smoke_deadline_outcomes_total",
            "Stage deadline outcomes",
            ("stage", "outcome"),
        )
        r.counter("smoke_process_restarts_total", "Component process restarts", ("component",))
        r.counter(
            "smoke_audio_suppressed_total",
            "Audio requests suppressed by degraded safety",
            ("reason",),
        )
        r.gauge("smoke_gpu_utilization_ratio", "GPU utilization ratio")
        r.gauge("smoke_gpu_memory_used_bytes", "GPU memory currently used")
        r.gauge("smoke_gpu_memory_total_bytes", "GPU memory capacity")
        r.gauge("smoke_gpu_memory_high_water_bytes", "GPU memory high-water mark")
        r.gauge("smoke_gpu_memory_pressure", "GPU memory pressure flag")
        r.gauge("smoke_gpu_temperature_celsius", "GPU temperature")
        r.gauge("smoke_gpu_power_watts", "GPU power draw")
        r.gauge("smoke_nvdec_utilization_ratio", "NVDEC utilization ratio")
        r.gauge("smoke_batch_fill_ratio", "Media batch fill ratio")
        r.counter("smoke_audio_requests_total", "Audio requests", ("status",))
        r.counter(
            "smoke_candidate_messages_total",
            "Candidate messages by terminal handling status",
            ("status",),
        )
        r.counter(
            "smoke_candidate_retries_total",
            "Candidate delivery retries",
            ("reason",),
        )
        r.counter(
            "smoke_candidate_deadletters_total",
            "Candidate messages quarantined as poison messages",
            ("reason",),
        )
        r.gauge("smoke_candidate_queue_depth", "Candidate consumer queue depth")
        r.gauge("smoke_candidate_ready", "Candidate processor readiness")
        r.histogram(
            "smoke_candidate_processing_seconds",
            "Candidate processing duration",
            ("status",),
        )
        r.counter(
            "smoke_degraded_transitions_total",
            "Component degraded transitions",
            ("component", "state"),
        )
        r.counter(
            "smoke_retention_operations_total", "Retention operations", ("media_class", "status")
        )

    def frame(self, camera_id: str, timestamp_seconds: float) -> None:
        if not camera_id:
            raise ValueError("camera_id is required")
        if not math.isfinite(timestamp_seconds) or timestamp_seconds < 0:
            raise ValueError("frame timestamp must be finite and non-negative")
        with self._camera_lock:
            self._camera_last_good_frame[camera_id] = timestamp_seconds
            self._camera_state[camera_id] = "healthy"
            self._refresh_camera_aggregates()

    def queue(self, camera_id: str, depth: int, age_seconds: float = 0.0) -> None:
        if not camera_id:
            raise ValueError("camera_id is required")
        if depth < 0:
            raise ValueError("queue depth cannot be negative")
        if not math.isfinite(age_seconds) or age_seconds < 0:
            raise ValueError("queue age must be finite and non-negative")
        with self._camera_lock:
            self._camera_queue_depth[camera_id] = depth
            self._camera_queue_age[camera_id] = age_seconds
            self._refresh_camera_aggregates()

    def camera_state(
        self,
        camera_id: str,
        state: str,
        *,
        frame_timestamp_seconds: float | None = None,
        queue_depth: int | None = None,
        queue_age_seconds: float | None = None,
    ) -> None:
        """Record camera state while keeping camera identity out of metrics labels."""

        if not camera_id:
            raise ValueError("camera_id is required")
        with self._camera_lock:
            self._camera_state[camera_id] = state
            if frame_timestamp_seconds is not None:
                self._camera_last_good_frame[camera_id] = frame_timestamp_seconds
            if queue_depth is not None:
                self._camera_queue_depth[camera_id] = queue_depth
            if queue_age_seconds is not None:
                self._camera_queue_age[camera_id] = queue_age_seconds
            self._refresh_camera_aggregates()

    def _refresh_camera_aggregates(self, *, now: float | None = None) -> None:
        current = time.time() if now is None else now
        frames = tuple(self._camera_last_good_frame.values())
        ages = tuple(max(0.0, current - item) for item in frames)
        queue_depths = tuple(self._camera_queue_depth.values())
        queue_ages = tuple(self._camera_queue_age.values())
        self.registry.set(
            "smoke_camera_last_good_frame_timestamp_seconds", max(frames, default=0.0)
        )
        self.registry.set("smoke_camera_frame_age_seconds", max(ages, default=0.0))
        self.registry.set("smoke_camera_stale_count", sum(age > 30.0 for age in ages))
        self.registry.set("smoke_camera_queue_depth", max(queue_depths, default=0))
        self.registry.set("smoke_camera_queue_age_seconds", max(queue_ages, default=0.0))
        states: dict[str, int] = defaultdict(int)
        for state in self._camera_state.values():
            bounded = state if state in _BOUNDED_LABEL_VALUES["state"] else "other"
            states[bounded] += 1
        for state in _BOUNDED_LABEL_VALUES["state"]:
            self.registry.set("smoke_camera_streams", states.get(state, 0), state=state)

    def stage_latency(self, stage: str, seconds: float) -> None:
        self.registry.observe("smoke_stage_latency_seconds", seconds, stage=stage)

    def decision(self, outcome: str, reason: str) -> None:
        bounded_reason = reason if reason in _DECISION_REASONS else "other"
        bounded_outcome = outcome if outcome in _BOUNDED_LABEL_VALUES["outcome"] else "other"
        self.registry.inc("smoke_decisions_total", outcome=bounded_outcome, reason=bounded_reason)

    def model_request(self, model_role: str, status: str) -> None:
        self.registry.inc("smoke_model_requests_total", model_role=model_role, status=status)

    def model_readiness(self, model_role: str, *, ready: bool, state: str | None = None) -> None:
        readiness = state or ("ready" if ready else "unavailable")
        self.registry.set(
            "smoke_model_readiness",
            1.0 if ready else 0.0,
            model_role=model_role,
            state=readiness,
        )

    def triton_pending(self, model_role: str, pending: int) -> None:
        if pending < 0:
            raise ValueError("pending requests cannot be negative")
        self.registry.set("smoke_triton_pending_requests", pending, model_role=model_role)

    def inference_failure(self, model_role: str, status: str = "error") -> None:
        self.registry.inc("smoke_inference_failures_total", model_role=model_role, status=status)

    def deadline(self, stage: str, outcome: str) -> None:
        self.registry.inc("smoke_deadline_outcomes_total", stage=stage, outcome=outcome)

    def restart(self, component: str) -> None:
        self.registry.inc("smoke_process_restarts_total", component=component)

    def sample_loss(self, amount: float = 1.0) -> None:
        self.registry.inc("smoke_camera_sample_loss_total", amount)

    def audio_suppressed(self, reason: str) -> None:
        self.registry.inc("smoke_audio_suppressed_total", reason=reason)

    def gpu(
        self,
        *,
        utilization_ratio: float,
        memory_used_bytes: float,
        memory_total_bytes: float,
        memory_high_water_bytes: float | None = None,
        temperature_celsius: float = 0.0,
        power_watts: float = 0.0,
        nvdec_utilization_ratio: float = 0.0,
        batch_fill_ratio: float = 0.0,
    ) -> None:
        values = {
            "utilization_ratio": utilization_ratio,
            "memory_used_bytes": memory_used_bytes,
            "memory_total_bytes": memory_total_bytes,
            "temperature_celsius": temperature_celsius,
            "power_watts": power_watts,
            "nvdec_utilization_ratio": nvdec_utilization_ratio,
            "batch_fill_ratio": batch_fill_ratio,
        }
        if memory_high_water_bytes is not None:
            values["memory_high_water_bytes"] = memory_high_water_bytes
        if any(not math.isfinite(value) or value < 0 for value in values.values()):
            raise ValueError("GPU telemetry must be finite and non-negative")
        if utilization_ratio > 1 or nvdec_utilization_ratio > 1 or batch_fill_ratio > 1:
            raise ValueError("GPU utilization and fill ratios must be between zero and one")
        if memory_total_bytes <= 0 or memory_used_bytes > memory_total_bytes:
            raise ValueError(
                "GPU memory totals must be positive and used memory cannot exceed total"
            )
        self.registry.set("smoke_gpu_utilization_ratio", utilization_ratio)
        self.registry.set("smoke_gpu_memory_used_bytes", memory_used_bytes)
        self.registry.set("smoke_gpu_memory_total_bytes", memory_total_bytes)
        self.registry.set(
            "smoke_gpu_memory_high_water_bytes",
            max(memory_high_water_bytes or 0.0, memory_used_bytes),
        )
        self.registry.set(
            "smoke_gpu_memory_pressure", float(memory_used_bytes / memory_total_bytes >= 0.9)
        )
        self.registry.set("smoke_gpu_temperature_celsius", temperature_celsius)
        self.registry.set("smoke_gpu_power_watts", power_watts)
        self.registry.set("smoke_nvdec_utilization_ratio", nvdec_utilization_ratio)
        self.registry.set("smoke_batch_fill_ratio", batch_fill_ratio)

    def audio_request(self, status: str) -> None:
        self.registry.inc("smoke_audio_requests_total", status=status)

    def candidate_message(self, status: str) -> None:
        self.registry.inc("smoke_candidate_messages_total", status=status)

    def candidate_retry(self, reason: str) -> None:
        self.registry.inc("smoke_candidate_retries_total", reason=reason)

    def candidate_deadletter(self, reason: str) -> None:
        self.registry.inc("smoke_candidate_deadletters_total", reason=reason)

    def candidate_queue(self, depth: int) -> None:
        self.registry.set("smoke_candidate_queue_depth", float(depth))

    def candidate_ready(self, ready: bool) -> None:
        self.registry.set("smoke_candidate_ready", 1.0 if ready else 0.0)

    def candidate_processing(self, status: str, seconds: float) -> None:
        self.registry.observe("smoke_candidate_processing_seconds", seconds, status=status)

    def degraded(self, component: str, state: str = "degraded") -> None:
        self.registry.inc("smoke_degraded_transitions_total", component=component, state=state)

    def retention(self, media_class: str, status: str) -> None:
        self.registry.inc(
            "smoke_retention_operations_total", media_class=media_class, status=status
        )

    def render(self) -> str:
        with self._camera_lock:
            self._refresh_camera_aggregates()
        return self.registry.render()


__all__ = ["MetricRegistry", "OperationalMetrics"]
