"""Sealed event coverage and model-promotion gates for GPU-106."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from ml.datasets.manifest import HARD_NEGATIVE_TAXONOMY
from ml.evaluation.ablation import AblationEvent, AblationReport, run_ablation


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _row_to_dict(row: AblationEvent) -> dict[str, Any]:
    event = row.event
    return {
        "event_id": event.event_id,
        "camera_id": event.camera_id,
        "track_id": event.track_id,
        "true_label": event.true_label,
        "predicted_label": event.predicted_label,
        "score": event.score,
        "eligible": event.eligible,
        "vlm_label": row.vlm_label,
        "vlm_score": row.vlm_score,
        "vlm_status": row.vlm_status.value,
        "vlm_revision": row.vlm_revision,
    }


@dataclass(frozen=True, slots=True)
class SealedEventMetadata:
    event_id: str
    camera_id: str
    capture_day: str
    cohort: str

    def __post_init__(self) -> None:
        if not self.event_id.strip() or not self.camera_id.strip() or not self.capture_day.strip():
            raise ValueError("sealed event metadata requires event, camera, and capture day")
        if self.cohort not in {"organic", "staged"}:
            raise ValueError("sealed event cohort must be organic or staged")

    def to_dict(self) -> dict[str, str]:
        return {
            "event_id": self.event_id,
            "camera_id": self.camera_id,
            "capture_day": self.capture_day,
            "cohort": self.cohort,
        }


@dataclass(frozen=True, slots=True)
class SealedEvaluationSet:
    """The only input accepted by the promotion evaluator.

    Labels are held in ``AblationEvent`` rows supplied by the sealed-label
    owner.  The set is immutable and its digest is carried into every result;
    training APIs never accept this type.
    """

    rows: tuple[AblationEvent, ...]
    metadata: tuple[SealedEventMetadata, ...]
    event_set_sha256: str

    def __post_init__(self) -> None:
        ids = tuple(row.event.event_id for row in self.rows)
        metadata_ids = tuple(entry.event_id for entry in self.metadata)
        if not ids or len(set(ids)) != len(ids):
            raise ValueError("sealed event IDs must be non-empty and unique")
        if set(ids) != set(metadata_ids) or len(set(metadata_ids)) != len(metadata_ids):
            raise ValueError("sealed metadata must cover each event exactly once")
        expected = hashlib.sha256(
            _canonical(
                {
                    "rows": sorted(
                        (_row_to_dict(row) for row in self.rows), key=lambda item: item["event_id"]
                    ),
                    "metadata": [
                        entry.to_dict()
                        for entry in sorted(self.metadata, key=lambda item: item.event_id)
                    ],
                }
            ).encode("utf-8")
        ).hexdigest()
        if expected != self.event_set_sha256:
            raise ValueError("sealed event-set checksum mismatch")

    @property
    def metadata_by_id(self) -> dict[str, SealedEventMetadata]:
        return {entry.event_id: entry for entry in self.metadata}


def build_sealed_event_set(
    rows: Iterable[AblationEvent], metadata: Iterable[SealedEventMetadata]
) -> SealedEvaluationSet:
    selected_rows = tuple(rows)
    selected_metadata = tuple(metadata)
    payload = {
        "rows": sorted(
            (_row_to_dict(row) for row in selected_rows), key=lambda item: item["event_id"]
        ),
        "metadata": [
            entry.to_dict() for entry in sorted(selected_metadata, key=lambda item: item.event_id)
        ],
    }
    digest = hashlib.sha256(_canonical(payload).encode("utf-8")).hexdigest()
    return SealedEvaluationSet(selected_rows, selected_metadata, digest)


@dataclass(frozen=True, slots=True)
class PromotionThresholds:
    min_precision: float = 0.80
    min_recall: float = 0.70
    min_organic_positives: int = 100
    min_each_confounder: int = 100
    min_cameras: int = 4
    min_calendar_days: int = 14
    min_vlm_fp_reduction: float = 0.20
    max_vlm_recall_loss: float = 0.02
    max_vlm_latency_delta_ms: float = 2000.0
    max_vlm_memory_gib: float = 22.0

    def __post_init__(self) -> None:
        if not 0 < self.min_precision <= 1 or not 0 < self.min_recall <= 1:
            raise ValueError("precision and recall thresholds must be in (0, 1]")
        if any(
            value < 0
            for value in (
                self.min_organic_positives,
                self.min_each_confounder,
                self.min_cameras,
                self.min_calendar_days,
                self.max_vlm_latency_delta_ms,
                self.max_vlm_memory_gib,
            )
        ):
            raise ValueError("promotion counts and limits must be non-negative")


@dataclass(frozen=True, slots=True)
class PromotionDecision:
    allowed: bool
    state: str
    reasons: tuple[str, ...]
    baseline_profile: str
    reviewer_profile: str | None
    source_digest: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": "ml.promotion-decision.v1",
            "allowed": self.allowed,
            "state": self.state,
            "reasons": list(self.reasons),
            "baseline_profile": self.baseline_profile,
            "reviewer_profile": self.reviewer_profile,
            "source_digest": self.source_digest,
        }


def _organic_rows(sealed: SealedEvaluationSet) -> tuple[AblationEvent, ...]:
    by_id = sealed.metadata_by_id
    return tuple(row for row in sealed.rows if by_id[row.event.event_id].cohort == "organic")


def evaluate_promotion(
    sealed: SealedEvaluationSet,
    report: AblationReport,
    *,
    baseline_profile: str = "without-vlm",
    reviewer_profile: str | None = None,
    thresholds: PromotionThresholds | None = None,
) -> PromotionDecision:
    """Apply event-level quality, coverage, reviewer, and provenance gates."""

    limits = thresholds or PromotionThresholds()
    reasons: list[str] = []
    if report.sealed_event_set_sha256 != sealed.event_set_sha256:
        reasons.append("ablation report is missing or has a different sealed event-set hash")
    if report.source_digest != _event_digest_for_rows(sealed.rows):
        reasons.append("ablation report does not match sealed event set")
    if report.sealed_event_ids != tuple(sorted(row.event.event_id for row in sealed.rows)):
        reasons.append("ablation report event IDs do not match sealed event set")
    organic = _organic_rows(sealed)
    metadata = sealed.metadata_by_id
    positive_count = sum(row.event.true_label == "smoking" for row in organic)
    camera_count = len({metadata[row.event.event_id].camera_id for row in organic})
    day_count = len({metadata[row.event.event_id].capture_day for row in organic})
    if positive_count < limits.min_organic_positives:
        reasons.append(f"organic positive events {positive_count} < {limits.min_organic_positives}")
    if camera_count < limits.min_cameras:
        reasons.append(f"organic cameras {camera_count} < {limits.min_cameras}")
    if day_count < limits.min_calendar_days:
        reasons.append(f"organic calendar days {day_count} < {limits.min_calendar_days}")
    for label in HARD_NEGATIVE_TAXONOMY:
        count = sum(row.event.true_label == label for row in organic)
        if count < limits.min_each_confounder:
            reasons.append(
                f"organic confounder {label} events {count} < {limits.min_each_confounder}"
            )

    configs = {result.config.profile_id: result.config for result in report.results}
    if baseline_profile not in configs:
        reasons.append(f"baseline profile {baseline_profile!r} is missing")
        baseline_result = None
    else:
        baseline_result = next(
            result for result in report.results if result.config.profile_id == baseline_profile
        )
        if not baseline_result.config.provenance_approved:
            reasons.append("baseline provenance/license disposition is not approved")
        if baseline_result.config.candidate_p95_latency_ms is None:
            reasons.append("baseline candidate latency receipt is missing")
        organic_report = run_ablation(organic, (baseline_result.config,)).results[0].report
        if organic_report.precision is None or organic_report.precision < limits.min_precision:
            reasons.append("organic baseline precision is below promotion threshold")
        if organic_report.recall is None or organic_report.recall < limits.min_recall:
            reasons.append("organic baseline recall is below promotion threshold")

    reviewer_config = configs.get(reviewer_profile) if reviewer_profile else None
    if reviewer_profile and reviewer_config is None:
        reasons.append(f"reviewer profile {reviewer_profile!r} is missing")
    if reviewer_config is not None:
        if not reviewer_config.use_vlm or not reviewer_config.provenance_approved:
            reasons.append("reviewer provenance/license disposition is not approved")
        if reviewer_config.memory_high_water_gib is None:
            reasons.append("reviewer memory receipt is missing")
        elif reviewer_config.memory_high_water_gib > limits.max_vlm_memory_gib:
            reasons.append("reviewer memory high-water exceeds promotion threshold")
        if reviewer_config.candidate_p95_latency_ms is None:
            reasons.append("reviewer candidate latency receipt is missing")
        if baseline_result is not None:
            base_organic = run_ablation(organic, (baseline_result.config,)).results[0].report
            reviewer_organic = run_ablation(organic, (reviewer_config,)).results[0].report
            if base_organic.false_positives:
                reduction = (
                    base_organic.false_positives - reviewer_organic.false_positives
                ) / base_organic.false_positives
                if reduction < limits.min_vlm_fp_reduction:
                    reasons.append("reviewer false-positive reduction is below threshold")
            else:
                reasons.append("reviewer false-positive reduction is not measurable")
            if (
                base_organic.recall is None
                or reviewer_organic.recall is None
                or reviewer_organic.recall < base_organic.recall - limits.max_vlm_recall_loss
            ):
                reasons.append("reviewer recall loss exceeds promotion threshold")
            if (
                reviewer_config.candidate_p95_latency_ms is not None
                and baseline_result.config.candidate_p95_latency_ms is not None
                and reviewer_config.candidate_p95_latency_ms
                - baseline_result.config.candidate_p95_latency_ms
                > limits.max_vlm_latency_delta_ms
            ):
                reasons.append("reviewer candidate latency delta exceeds promotion threshold")

    return PromotionDecision(
        not reasons,
        "promotable" if not reasons else "blocked",
        tuple(dict.fromkeys(reasons)),
        baseline_profile,
        reviewer_profile,
        sealed.event_set_sha256,
    )


def _event_digest_for_rows(rows: Iterable[AblationEvent]) -> str:
    payload = [
        {
            "event_id": row.event.event_id,
            "camera_id": row.event.camera_id,
            "track_id": row.event.track_id,
            "true_label": row.event.true_label,
            "predicted_label": row.event.predicted_label,
            "score": row.event.score,
            "eligible": row.event.eligible,
        }
        for row in rows
    ]
    return hashlib.sha256(
        _canonical(sorted(payload, key=lambda item: item["event_id"])).encode("utf-8")
    ).hexdigest()


__all__ = [
    "PromotionDecision",
    "PromotionThresholds",
    "SealedEventMetadata",
    "SealedEvaluationSet",
    "build_sealed_event_set",
    "evaluate_promotion",
]
