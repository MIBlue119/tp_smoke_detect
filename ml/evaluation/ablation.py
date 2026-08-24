"""Reproducible baseline calibration and optional-VLM ablation contracts.

The evaluator is deliberately evidence-level and metadata-only.  It never opens
media, downloads a checkpoint, or calls a hosted model.  A caller supplies the
same sealed event rows to each profile and (optionally) a deterministic VLM
decision for those rows.  This makes the with/without-VLM comparison suitable
for CPU proof tests while leaving runtime adapters to the model-provider lane.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Literal

from ml.evaluation.metrics import EvaluationEvent, EventMetricReport, evaluate_events


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


class CropStrategy(StrEnum):
    """Comparable evidence crops; values are stable report contract identifiers."""

    FULL_FRAME = "full_frame"
    PERSON = "person_crop"
    HEAD_SHOULDER = "head_shoulder_crop"
    HAND_FACE = "hand_face_crop"


class VLMStatus(StrEnum):
    DISABLED = "disabled"
    OK = "ok"
    TIMEOUT = "timeout"
    MALFORMED = "malformed"
    UNCLEAR = "unclear"


_DEFAULT_CANDIDATES: tuple[float, ...] = tuple(
    round(candidate / 100, 2) for candidate in range(0, 101)
)


@dataclass(frozen=True, slots=True)
class ConfidenceInterval:
    """A two-sided Wilson interval for a bounded proportion."""

    estimate: float | None
    lower: float | None
    upper: float | None
    level: float = 0.95
    method: str = "wilson"

    def to_dict(self) -> dict[str, Any]:
        return {
            "estimate": self.estimate,
            "lower": self.lower,
            "upper": self.upper,
            "level": self.level,
            "method": self.method,
        }


def proportion_interval(successes: int, trials: int, *, level: float = 0.95) -> ConfidenceInterval:
    """Compute a deterministic Wilson interval without a statistics dependency."""

    if trials < 0 or successes < 0 or successes > trials:
        raise ValueError("successes must be between zero and trials")
    if not 0 < level < 1:
        raise ValueError("level must be between zero and one")
    if trials == 0:
        return ConfidenceInterval(None, None, None, level)
    # Keep the quantiles dependency-free and explicit for pre-registered
    # reports. Unknown levels are rejected instead of silently reporting an
    # interval at a different confidence level.
    quantiles = {
        0.90: 1.6448536269514722,
        0.95: 1.959963984540054,
        0.99: 2.5758293035489004,
    }
    z = next((value for key, value in quantiles.items() if math.isclose(level, key)), None)
    if z is None:
        raise ValueError("level must be one of 0.90, 0.95, or 0.99")
    estimate = successes / trials
    denominator = 1 + z * z / trials
    centre = (estimate + z * z / (2 * trials)) / denominator
    margin = (
        z
        * math.sqrt(estimate * (1 - estimate) / trials + z * z / (4 * trials * trials))
        / denominator
    )
    return ConfidenceInterval(estimate, max(0.0, centre - margin), min(1.0, centre + margin), level)


@dataclass(frozen=True, slots=True)
class CalibrationResult:
    """Threshold selected only from a non-sealed calibration split."""

    threshold: float
    method: str
    source_event_count: int
    source_digest: str
    target_precision: float | None = None
    calibration_state: str = "ok"

    def to_dict(self) -> dict[str, Any]:
        return {
            "threshold": self.threshold,
            "method": self.method,
            "source_event_count": self.source_event_count,
            "source_digest": self.source_digest,
            "target_precision": self.target_precision,
            "calibration_state": self.calibration_state,
        }


def _event_digest(events: Iterable[EvaluationEvent]) -> str:
    rows = [
        {
            "event_id": event.event_id,
            "camera_id": event.camera_id,
            "track_id": event.track_id,
            "true_label": event.true_label,
            "predicted_label": event.predicted_label,
            "score": event.score,
            "eligible": event.eligible,
        }
        for event in events
    ]
    return hashlib.sha256(
        _canonical(sorted(rows, key=lambda row: row["event_id"])).encode()
    ).hexdigest()


def calibrate_threshold(
    events: Iterable[EvaluationEvent],
    *,
    target_precision: float | None = None,
    candidates: tuple[float, ...] = _DEFAULT_CANDIDATES,
) -> CalibrationResult:
    """Select a deterministic score threshold from calibration events.

    The event labels are used only for this caller-supplied calibration split.
    Sealed events must be passed to :func:`run_ablation` after this function
    returns; the API intentionally has no combined calibration/evaluation call.
    """

    rows = tuple(events)
    if not candidates or any(not 0 <= value <= 1 for value in candidates):
        raise ValueError("candidates must contain scores in [0, 1]")
    ordered = tuple(sorted(set(candidates)))
    digest = _event_digest(rows)
    if not rows:
        return CalibrationResult(0.5, "f1", 0, digest, target_precision, "not_applicable")
    best: tuple[float, float, float] | None = None
    for threshold in ordered:
        selected = [event for event in rows if event.score >= threshold]
        tp = sum(event.true_label == "smoking" for event in selected)
        fp = sum(event.true_label != "smoking" for event in selected)
        positives = sum(event.true_label == "smoking" for event in rows)
        precision = tp / (tp + fp) if tp + fp else 0.0
        recall = tp / positives if positives else 0.0
        f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
        if target_precision is not None and precision < target_precision:
            continue
        candidate = (f1, precision, -threshold)
        if best is None or candidate > best:
            best = candidate
    if best is None:
        # A target that cannot be met must not silently claim calibration.
        return CalibrationResult(
            max(ordered), "target_precision", len(rows), digest, target_precision, "target_unmet"
        )
    return CalibrationResult(
        -best[2],
        "target_precision" if target_precision is not None else "f1",
        len(rows),
        digest,
        target_precision,
    )


@dataclass(frozen=True, slots=True)
class AblationEvent:
    """One sealed event and optional structured VLM evidence."""

    event: EvaluationEvent
    vlm_label: Literal["smoking", "not_smoking", "unclear"] | None = None
    vlm_score: float | None = None
    vlm_status: VLMStatus = VLMStatus.DISABLED
    vlm_revision: str | None = None

    def __post_init__(self) -> None:
        if self.vlm_score is not None and not 0 <= self.vlm_score <= 1:
            raise ValueError("vlm_score must be in [0, 1]")
        if self.vlm_status is VLMStatus.OK and self.vlm_label is None:
            raise ValueError("successful VLM evidence requires a label")


@dataclass(frozen=True, slots=True)
class AblationConfig:
    profile_id: str
    crop_strategy: CropStrategy = CropStrategy.PERSON
    use_vlm: bool = False
    threshold: float = 0.5
    vlm_revision: str | None = None
    calibration: CalibrationResult | None = None
    memory_high_water_gib: float | None = None
    candidate_p95_latency_ms: float | None = None
    provenance_approved: bool = False

    def __post_init__(self) -> None:
        if not self.profile_id.strip():
            raise ValueError("profile_id must not be empty")
        if not 0 <= self.threshold <= 1:
            raise ValueError("threshold must be in [0, 1]")
        if self.use_vlm and not self.vlm_revision:
            raise ValueError("VLM profiles require a revision")
        if self.memory_high_water_gib is not None and self.memory_high_water_gib < 0:
            raise ValueError("memory_high_water_gib must be non-negative")
        if self.candidate_p95_latency_ms is not None and self.candidate_p95_latency_ms < 0:
            raise ValueError("candidate_p95_latency_ms must be non-negative")


@dataclass(frozen=True, slots=True)
class MetricIntervals:
    precision: ConfidenceInterval
    recall: ConfidenceInterval
    f1: ConfidenceInterval

    def to_dict(self) -> dict[str, Any]:
        return {
            "precision": self.precision.to_dict(),
            "recall": self.recall.to_dict(),
            "f1": self.f1.to_dict(),
        }


def _intervals(report: EventMetricReport, *, level: float) -> MetricIntervals:
    precision = proportion_interval(
        report.true_positives,
        report.true_positives + report.false_positives,
        level=level,
    )
    recall = proportion_interval(
        report.true_positives,
        report.true_positives + report.false_negatives,
        level=level,
    )
    # F1 is a derived metric; use the conservative interval formed by the
    # endpoint harmonic means of precision/recall intervals.
    precision_lower = precision.lower
    precision_upper = precision.upper
    recall_lower = recall.lower
    recall_upper = recall.upper
    if (
        precision_lower is None
        or precision_upper is None
        or recall_lower is None
        or recall_upper is None
    ):
        f1 = ConfidenceInterval(None, None, None, level)
    else:
        estimate = report.f1
        lower = (
            2 * precision_lower * recall_lower / (precision_lower + recall_lower)
            if precision_lower + recall_lower
            else 0.0
        )
        upper = (
            2 * precision_upper * recall_upper / (precision_upper + recall_upper)
            if precision_upper + recall_upper
            else 0.0
        )
        f1 = ConfidenceInterval(estimate, lower, upper, level)
    return MetricIntervals(precision, recall, f1)


@dataclass(frozen=True, slots=True)
class AblationResult:
    config: AblationConfig
    report: EventMetricReport
    intervals: MetricIntervals
    sealed_event_ids: tuple[str, ...]
    source_digest: str

    def payload(self) -> dict[str, Any]:
        return {
            "profile_id": self.config.profile_id,
            "crop_strategy": self.config.crop_strategy.value,
            "use_vlm": self.config.use_vlm,
            "threshold": self.config.threshold,
            "vlm_revision": self.config.vlm_revision,
            "memory_high_water_gib": self.config.memory_high_water_gib,
            "candidate_p95_latency_ms": self.config.candidate_p95_latency_ms,
            "provenance_approved": self.config.provenance_approved,
            "report": self.report.to_dict(),
            "confidence_intervals": self.intervals.to_dict(),
            "sealed_event_ids": list(self.sealed_event_ids),
            "source_digest": self.source_digest,
        }

    def to_dict(self) -> dict[str, Any]:
        result = self.payload()
        result["result_sha256"] = hashlib.sha256(_canonical(result).encode()).hexdigest()
        return result


@dataclass(frozen=True, slots=True)
class AblationReport:
    results: tuple[AblationResult, ...]
    sealed_event_ids: tuple[str, ...]
    source_digest: str
    sealed_event_set_sha256: str | None = None

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "schema_version": "ml.ablation.v1",
            "sealed_event_ids": list(self.sealed_event_ids),
            "source_digest": self.source_digest,
            "sealed_event_set_sha256": self.sealed_event_set_sha256,
            "results": [result.to_dict() for result in self.results],
        }
        payload["report_sha256"] = hashlib.sha256(_canonical(payload).encode()).hexdigest()
        return payload

    def to_json(self) -> str:
        return _canonical(self.to_dict()) + "\n"


def run_ablation(
    events: Iterable[AblationEvent],
    configs: Iterable[AblationConfig],
    *,
    confidence_level: float = 0.95,
    sealed_event_set_sha256: str | None = None,
) -> AblationReport:
    """Evaluate fixed sealed rows with one or more deterministic profiles."""

    rows = tuple(events)
    profiles = tuple(configs)
    if not profiles:
        raise ValueError("at least one ablation profile is required")
    event_ids = tuple(sorted(row.event.event_id for row in rows))
    if len(set(event_ids)) != len(event_ids):
        raise ValueError("sealed event IDs must be unique")
    source_digest = _event_digest(row.event for row in rows)
    results: list[AblationResult] = []
    for config in profiles:
        projected: list[EvaluationEvent] = []
        for row in rows:
            baseline_positive = (
                row.event.predicted_label == "smoking" and row.event.score >= config.threshold
            )
            vlm_positive = not config.use_vlm or (
                row.vlm_status is VLMStatus.OK
                and row.vlm_label == "smoking"
                and row.vlm_revision == config.vlm_revision
            )
            projected.append(
                EvaluationEvent(
                    event_id=row.event.event_id,
                    camera_id=row.event.camera_id,
                    track_id=row.event.track_id,
                    true_label=row.event.true_label,
                    predicted_label=(
                        "smoking" if baseline_positive and vlm_positive else "background"
                    ),
                    score=row.event.score,
                    eligible=row.event.eligible,
                    triggered=baseline_positive and vlm_positive,
                    latency_ms=row.event.latency_ms,
                    start_ns=row.event.start_ns,
                    end_ns=row.event.end_ns,
                )
            )
        report = evaluate_events(projected)
        results.append(
            AblationResult(
                config,
                report,
                _intervals(report, level=confidence_level),
                event_ids,
                source_digest,
            )
        )
    return AblationReport(tuple(results), event_ids, source_digest, sealed_event_set_sha256)


@dataclass(frozen=True, slots=True)
class CropComparisonReport:
    results: tuple[AblationResult, ...]

    def __post_init__(self) -> None:
        strategies = [result.config.crop_strategy for result in self.results]
        if len(strategies) != len(set(strategies)):
            raise ValueError("crop strategies must be unique")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": "ml.crop-comparison.v1",
            "strategies": [result.to_dict() for result in self.results],
        }


def compare_crop_strategies(
    events_by_strategy: Mapping[CropStrategy, Iterable[AblationEvent]],
    *,
    threshold: float = 0.5,
    confidence_level: float = 0.95,
) -> CropComparisonReport:
    """Run identical evaluation contracts for each available crop strategy."""

    results: list[AblationResult] = []
    for strategy in sorted(events_by_strategy, key=lambda value: value.value):
        config = AblationConfig(f"crop-{strategy.value}", strategy, False, threshold)
        results.extend(
            run_ablation(
                events_by_strategy[strategy],
                (config,),
                confidence_level=confidence_level,
            ).results
        )
    return CropComparisonReport(tuple(results))


__all__ = [
    "AblationConfig",
    "AblationEvent",
    "AblationReport",
    "AblationResult",
    "CalibrationResult",
    "ConfidenceInterval",
    "CropComparisonReport",
    "CropStrategy",
    "MetricIntervals",
    "VLMStatus",
    "calibrate_threshold",
    "compare_crop_strategies",
    "proportion_interval",
    "run_ablation",
]


def main(argv: list[str] | None = None) -> int:
    """Emit a truthful receipt when a sealed event catalogue is unavailable.

    The command is intentionally useful in an offline bundle before site
    labels arrive: it reports ``blocked`` and never manufactures a metric.
    Runtime callers with approved sealed rows should use :func:`run_ablation`
    and :func:`evaluate_promotion` directly.
    """

    import argparse
    from pathlib import Path

    parser = argparse.ArgumentParser(description="Run metadata-only GPU ablation gates")
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--sealed", action="store_true")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    config_bytes = args.config.read_bytes()
    receipt: dict[str, Any] = {
        "schema_version": "ml.ablation-receipt.v1",
        "state": "blocked",
        "reason": "sealed event catalogue is not supplied; no metrics were generated",
        "config_sha256": hashlib.sha256(config_bytes).hexdigest(),
        "sealed_requested": args.sealed,
    }
    encoded = _canonical(receipt) + "\n"
    if args.output:
        args.output.write_text(encoded, encoding="utf-8")
    else:
        print(encoded, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
