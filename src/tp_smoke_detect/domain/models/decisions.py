"""Structured outputs from domain evaluation."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from ...contracts import DecisionOutcome, EvidenceChannel, Stage


class ReasonCode(StrEnum):
    """Stable machine-readable reasons; never use provider prose here."""

    QUALITY_REJECTED = "quality_rejected"
    POSE_REJECTED = "pose_rejected"
    NOSE_TOUCH = "veto_nose_touch"
    CHEWING = "veto_chewing"
    PHONE = "veto_phone"
    DRINK = "veto_drink"
    FOOD = "veto_food"
    PEN_OR_TOOTHPICK = "veto_pen_toothpick"
    VETO = "veto_other"
    CYCLE_DETECTED = "smoking_cycle_detected"
    PERSISTENCE_INSUFFICIENT = "temporal_persistence_insufficient"
    INDEPENDENT_EVIDENCE_INSUFFICIENT = "independent_evidence_insufficient"
    TWO_INDEPENDENT_CHANNELS = "two_independent_channels"
    VLM_UNCLEAR = "vlm_unclear"
    VLM_TIMEOUT = "vlm_timeout"
    VLM_MALFORMED = "vlm_malformed"
    OUT_OF_ORDER = "out_of_order_observation"
    DUPLICATE = "duplicate_observation"
    TRACK_GAP = "track_gap"
    VERIFIED = "verified"
    REJECTED = "rejected"
    UNCLEAR = "unclear"


@dataclass(frozen=True, slots=True)
class VetoSignal:
    name: str
    score: float = 1.0
    positive: bool = True


@dataclass(frozen=True, slots=True)
class EvidenceSignal:
    """One independent evidence family after canonicalization."""

    name: str
    score: float
    positive: bool

    def to_contract(self) -> EvidenceChannel:
        return EvidenceChannel(name=self.name, score=self.score, positive=self.positive)


@dataclass(frozen=True, slots=True)
class StageTransition:
    from_stage: Stage
    to_stage: Stage
    timestamp_ns: int
    reason_codes: tuple[ReasonCode, ...] = ()


@dataclass(frozen=True, slots=True)
class CascadeDecision:
    outcome: DecisionOutcome
    reason_codes: tuple[ReasonCode, ...]
    evidence_channels: tuple[EvidenceSignal, ...]
    stage: Stage
    audio_eligibility: bool
    cycle_count: int = 0
    model_revisions: tuple[tuple[str, str], ...] = ()

    @property
    def positive_channel_names(self) -> frozenset[str]:
        return frozenset(signal.name for signal in self.evidence_channels if signal.positive)


__all__ = [
    "CascadeDecision",
    "EvidenceSignal",
    "ReasonCode",
    "StageTransition",
    "VetoSignal",
]
