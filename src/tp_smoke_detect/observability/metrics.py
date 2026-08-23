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
from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass

from ..domain.models.decisions import ReasonCode

_FORBIDDEN_LABELS = {
    "track_id",
    "decision_id",
    "artifact_id",
    "url",
    "raw_url",
    "request_id",
    "correlation_id",
}
_ALLOWED_LABELS = {
    "camera_id",
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
_DECISION_REASONS = frozenset(item.value for item in ReasonCode) | {
    "evaluation_pending_provider",
    "other",
}


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
        return _label_key(labels)

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
        r = self.registry
        r.gauge(
            "smoke_camera_last_good_frame_timestamp_seconds",
            "Unix timestamp of the last good frame",
            ("camera_id",),
        )
        r.gauge("smoke_camera_queue_depth", "Current bounded queue depth", ("camera_id",))
        r.histogram("smoke_stage_latency_seconds", "Stage processing latency", ("stage",))
        r.counter("smoke_decisions_total", "Completed decisions", ("outcome", "reason"))
        r.counter("smoke_model_requests_total", "Model requests", ("model_role", "status"))
        r.counter("smoke_audio_requests_total", "Audio requests", ("status",))
        r.counter(
            "smoke_degraded_transitions_total",
            "Component degraded transitions",
            ("component", "state"),
        )
        r.counter(
            "smoke_retention_operations_total", "Retention operations", ("media_class", "status")
        )

    def frame(self, camera_id: str, timestamp_seconds: float) -> None:
        self.registry.set(
            "smoke_camera_last_good_frame_timestamp_seconds", timestamp_seconds, camera_id=camera_id
        )

    def queue(self, camera_id: str, depth: int) -> None:
        self.registry.set("smoke_camera_queue_depth", depth, camera_id=camera_id)

    def stage_latency(self, stage: str, seconds: float) -> None:
        self.registry.observe("smoke_stage_latency_seconds", seconds, stage=stage)

    def decision(self, outcome: str, reason: str) -> None:
        bounded_reason = reason if reason in _DECISION_REASONS else "other"
        self.registry.inc("smoke_decisions_total", outcome=outcome, reason=bounded_reason)

    def model_request(self, model_role: str, status: str) -> None:
        self.registry.inc("smoke_model_requests_total", model_role=model_role, status=status)

    def audio_request(self, status: str) -> None:
        self.registry.inc("smoke_audio_requests_total", status=status)

    def degraded(self, component: str, state: str = "degraded") -> None:
        self.registry.inc("smoke_degraded_transitions_total", component=component, state=state)

    def retention(self, media_class: str, status: str) -> None:
        self.registry.inc(
            "smoke_retention_operations_total", media_class=media_class, status=status
        )

    def render(self) -> str:
        return self.registry.render()


__all__ = ["MetricRegistry", "OperationalMetrics"]
