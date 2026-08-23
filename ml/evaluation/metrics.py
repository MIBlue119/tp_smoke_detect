"""Deterministic event-level metrics for smoking and confusion classes."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from ml.datasets.manifest import NAMED_CLASSES


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


@dataclass(frozen=True, slots=True)
class EvaluationEvent:
    event_id: str
    camera_id: str
    track_id: str
    true_label: str
    predicted_label: str
    score: float
    eligible: bool = True
    triggered: bool | None = None
    latency_ms: float = 0.0
    start_ns: int = 0
    end_ns: int = 0

    @property
    def did_trigger(self) -> bool:
        return self.triggered if self.triggered is not None else self.predicted_label == "smoking"


@dataclass(frozen=True, slots=True)
class CalibrationBin:
    lower: float
    upper: float
    count: int
    mean_score: float | None
    empirical_rate: float | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "lower": self.lower,
            "upper": self.upper,
            "count": self.count,
            "mean_score": self.mean_score,
            "empirical_rate": self.empirical_rate,
        }


@dataclass(frozen=True, slots=True)
class EventMetricReport:
    total_events: int
    eligible_events: int
    excluded_events: int
    true_positives: int
    false_positives: int
    false_negatives: int
    precision: float | None
    recall: float | None
    f1: float | None
    metric_state: str
    confusion_trigger_rates: dict[str, float | None]
    confusion_states: dict[str, str]
    calibration: tuple[CalibrationBin, ...]
    mean_latency_ms: float | None
    report_sha256: str = ""

    def payload(self) -> dict[str, Any]:
        return {
            "total_events": self.total_events,
            "eligible_events": self.eligible_events,
            "excluded_events": self.excluded_events,
            "true_positives": self.true_positives,
            "false_positives": self.false_positives,
            "false_negatives": self.false_negatives,
            "precision": self.precision,
            "recall": self.recall,
            "f1": self.f1,
            "metric_state": self.metric_state,
            "confusion_trigger_rates": dict(sorted(self.confusion_trigger_rates.items())),
            "confusion_states": dict(sorted(self.confusion_states.items())),
            "calibration": [entry.to_dict() for entry in self.calibration],
            "mean_latency_ms": self.mean_latency_ms,
        }

    @property
    def digest(self) -> str:
        return hashlib.sha256(_canonical(self.payload()).encode("utf-8")).hexdigest()

    def to_dict(self) -> dict[str, Any]:
        result = self.payload()
        result["report_sha256"] = self.digest
        return result

    def to_json(self) -> str:
        return _canonical(self.to_dict()) + "\n"


def aggregate_events(
    events: Iterable[EvaluationEvent], *, gap_ms: int = 120_000
) -> tuple[EvaluationEvent, ...]:
    """Collapse contiguous observations into one event per camera/track session.

    The first event supplies identity and truth; the aggregate uses the highest
    score, a positive trigger if any observation triggered, and the complete
    session latency span. This makes a 120-second continuous session one event.
    """

    if gap_ms < 0:
        raise ValueError("gap_ms must be non-negative")
    ordered = sorted(
        events, key=lambda event: (event.camera_id, event.track_id, event.start_ns, event.event_id)
    )
    result: list[EvaluationEvent] = []
    current: list[EvaluationEvent] = []
    key: tuple[str, str] | None = None
    previous_end = -1
    for event in ordered:
        event_key = (event.camera_id, event.track_id)
        if current and (event_key != key or event.start_ns - previous_end > gap_ms * 1_000_000):
            result.append(_merge(current))
            current = []
        current.append(event)
        key = event_key
        previous_end = max(previous_end, event.end_ns or event.start_ns)
    if current:
        result.append(_merge(current))
    return tuple(result)


def _merge(events: list[EvaluationEvent]) -> EvaluationEvent:
    first = events[0]
    best = max(events, key=lambda event: event.score)
    return EvaluationEvent(
        event_id=first.event_id,
        camera_id=first.camera_id,
        track_id=first.track_id,
        true_label=first.true_label,
        predicted_label=best.predicted_label,
        score=best.score,
        eligible=all(event.eligible for event in events),
        triggered=any(event.did_trigger for event in events),
        latency_ms=max(event.latency_ms for event in events),
        start_ns=min(event.start_ns for event in events),
        end_ns=max(event.end_ns for event in events),
    )


def _calibration(events: list[EvaluationEvent], bins: int) -> tuple[CalibrationBin, ...]:
    if bins <= 0:
        raise ValueError("bins must be positive")
    result: list[CalibrationBin] = []
    for index in range(bins):
        lower = index / bins
        upper = (index + 1) / bins
        bucket = [
            event
            for event in events
            if lower <= event.score <= 1
            if index == bins - 1 or event.score < upper
        ]
        result.append(
            CalibrationBin(
                lower,
                upper,
                len(bucket),
                sum(event.score for event in bucket) / len(bucket) if bucket else None,
                sum(event.did_trigger for event in bucket) / len(bucket) if bucket else None,
            )
        )
    return tuple(result)


def evaluate_events(
    events: Iterable[EvaluationEvent],
    *,
    confusion_classes: tuple[str, ...] = NAMED_CLASSES[1:10],
    calibration_bins: int = 10,
    aggregate: bool = True,
) -> EventMetricReport:
    """Return explicit ``not_applicable`` states for empty classes/no positives."""

    rows = list(aggregate_events(events)) if aggregate else list(events)
    eligible = [event for event in rows if event.eligible]
    positives = [event for event in eligible if event.true_label == "smoking"]
    tp = sum(event.true_label == "smoking" and event.did_trigger for event in eligible)
    fp = sum(event.true_label != "smoking" and event.did_trigger for event in eligible)
    fn = sum(event.true_label == "smoking" and not event.did_trigger for event in eligible)
    precision = tp / (tp + fp) if tp + fp else None
    recall = tp / (tp + fn) if positives else None
    f1 = (
        2 * precision * recall / (precision + recall)
        if precision is not None and recall is not None and precision + recall
        else None
    )
    # Recall is measurable whenever positives exist, even if the model never
    # triggers. Conversely, a false-positive-only set cannot claim recall.
    state = "ok" if positives else "not_applicable"
    rates: dict[str, float | None] = {}
    states: dict[str, str] = {}
    for label in confusion_classes:
        class_events = [event for event in eligible if event.true_label == label]
        rates[label] = (
            sum(event.did_trigger for event in class_events) / len(class_events)
            if class_events
            else None
        )
        states[label] = "ok" if class_events else "not_applicable"
    return EventMetricReport(
        len(rows),
        len(eligible),
        len(rows) - len(eligible),
        tp,
        fp,
        fn,
        precision,
        recall,
        f1,
        state,
        rates,
        states,
        _calibration(eligible, calibration_bins),
        sum(event.latency_ms for event in eligible) / len(eligible) if eligible else None,
    )
