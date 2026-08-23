"""Evidence aggregation and veto precedence."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from ...contracts import DecisionOutcome, Stage
from ..models.decisions import CascadeDecision, EvidenceSignal, ReasonCode
from ..models.observations import DomainObservation

_VETO_REASON: dict[str, ReasonCode] = {
    "nose": ReasonCode.NOSE_TOUCH,
    "nose_touch": ReasonCode.NOSE_TOUCH,
    "chewing": ReasonCode.CHEWING,
    "betel_nut": ReasonCode.CHEWING,
    "betel_quid": ReasonCode.CHEWING,
    "phone": ReasonCode.PHONE,
    "drink": ReasonCode.DRINK,
    "cup": ReasonCode.DRINK,
    "food": ReasonCode.FOOD,
    "pen": ReasonCode.PEN_OR_TOOTHPICK,
    "toothpick": ReasonCode.PEN_OR_TOOTHPICK,
    "pen_toothpick": ReasonCode.PEN_OR_TOOTHPICK,
}

_CHANNEL_ALIASES: dict[str, str] = {
    "object": "object",
    "cigarette": "object",
    "vape": "object",
    "heated_tobacco": "object",
    "smoke": "smoke",
    "ember": "smoke",
    "temporal": "temporal",
    "cycle": "temporal",
    "vlm": "vlm",
}


@dataclass(frozen=True, slots=True)
class EvidencePolicyConfig:
    min_cycles: int = 1
    min_persistence_ms: int = 2_000
    min_independent_channels: int = 2
    reject_on_unclear: bool = True

    def __post_init__(self) -> None:
        if self.min_cycles < 1 or self.min_persistence_ms < 0 or self.min_independent_channels < 1:
            raise ValueError("evidence thresholds are invalid")


class EvidencePolicy:
    """Apply vetoes, temporal requirements, and independent channel rules."""

    def __init__(self, config: EvidencePolicyConfig | None = None) -> None:
        self.config = config or EvidencePolicyConfig()

    @staticmethod
    def _canonical_channels(
        observations: Iterable[DomainObservation],
    ) -> tuple[EvidenceSignal, ...]:
        scores: dict[str, float] = {}
        for observation in observations:
            for channel in observation.positive_channels:
                canonical = _CHANNEL_ALIASES.get(channel, channel)
                scores[canonical] = max(scores.get(canonical, 0.0), 1.0)
            if observation.object_label in {"cigarette", "vape", "heated_tobacco"}:
                scores["object"] = max(scores.get("object", 0.0), observation.object_score)
            if observation.smoke_score > 0:
                scores["smoke"] = max(scores.get("smoke", 0.0), observation.smoke_score)
        return tuple(
            EvidenceSignal(name=name, score=score, positive=score > 0)
            for name, score in sorted(scores.items())
        )

    @staticmethod
    def _veto_reason(observations: Iterable[DomainObservation]) -> ReasonCode | None:
        for observation in observations:
            for veto in sorted(observation.vetoes):
                if veto in _VETO_REASON:
                    return _VETO_REASON[veto]
            if observation.contact_target == "nose":
                return ReasonCode.NOSE_TOUCH
            if observation.object_label in _VETO_REASON:
                return _VETO_REASON[observation.object_label]
        return None

    def evaluate(
        self,
        observations: Iterable[DomainObservation],
        *,
        cycle_count: int,
        model_revisions: tuple[tuple[str, str], ...] = (),
    ) -> CascadeDecision:
        items = tuple(observations)
        evidence = self._canonical_channels(items)
        veto = self._veto_reason(items)
        if veto is not None:
            return CascadeDecision(
                outcome=DecisionOutcome.REJECTED,
                reason_codes=(veto, ReasonCode.REJECTED),
                evidence_channels=evidence,
                stage=Stage.COMPLETED,
                audio_eligibility=False,
                cycle_count=cycle_count,
                model_revisions=model_revisions,
            )

        if any(not item.quality_eligible for item in items):
            return CascadeDecision(
                outcome=DecisionOutcome.REJECTED,
                reason_codes=(ReasonCode.QUALITY_REJECTED, ReasonCode.REJECTED),
                evidence_channels=evidence,
                stage=Stage.COMPLETED,
                audio_eligibility=False,
                cycle_count=cycle_count,
                model_revisions=model_revisions,
            )
        if any(not item.pose_eligible for item in items):
            return CascadeDecision(
                outcome=DecisionOutcome.REJECTED,
                reason_codes=(ReasonCode.POSE_REJECTED, ReasonCode.REJECTED),
                evidence_channels=evidence,
                stage=Stage.COMPLETED,
                audio_eligibility=False,
                cycle_count=cycle_count,
                model_revisions=model_revisions,
            )

        if any(
            item.vlm is not None and item.vlm.status in {"timeout", "malformed", "unclear"}
            for item in items
        ):
            vlm_statuses = {item.vlm.status for item in items if item.vlm is not None}
            reason = (
                ReasonCode.VLM_TIMEOUT
                if "timeout" in vlm_statuses
                else ReasonCode.VLM_MALFORMED
                if "malformed" in vlm_statuses
                else ReasonCode.VLM_UNCLEAR
            )
            if self.config.reject_on_unclear:
                return CascadeDecision(
                    outcome=DecisionOutcome.UNCLEAR,
                    reason_codes=(reason, ReasonCode.UNCLEAR),
                    evidence_channels=evidence,
                    stage=Stage.COMPLETED,
                    audio_eligibility=False,
                    cycle_count=cycle_count,
                    model_revisions=model_revisions,
                )

        persistence_ms = max((item.persistence_ms for item in items), default=0)
        if cycle_count < self.config.min_cycles or persistence_ms < self.config.min_persistence_ms:
            return CascadeDecision(
                outcome=DecisionOutcome.REJECTED,
                reason_codes=(ReasonCode.PERSISTENCE_INSUFFICIENT, ReasonCode.REJECTED),
                evidence_channels=evidence,
                stage=Stage.COMPLETED,
                audio_eligibility=False,
                cycle_count=cycle_count,
                model_revisions=model_revisions,
            )

        positive = tuple(signal for signal in evidence if signal.positive)
        if len(positive) < self.config.min_independent_channels:
            return CascadeDecision(
                outcome=DecisionOutcome.REJECTED,
                reason_codes=(ReasonCode.INDEPENDENT_EVIDENCE_INSUFFICIENT, ReasonCode.REJECTED),
                evidence_channels=evidence,
                stage=Stage.COMPLETED,
                audio_eligibility=False,
                cycle_count=cycle_count,
                model_revisions=model_revisions,
            )

        return CascadeDecision(
            outcome=DecisionOutcome.VERIFIED,
            reason_codes=(
                ReasonCode.CYCLE_DETECTED,
                ReasonCode.TWO_INDEPENDENT_CHANNELS,
                ReasonCode.VERIFIED,
            ),
            evidence_channels=evidence,
            stage=Stage.COMPLETED,
            audio_eligibility=True,
            cycle_count=cycle_count,
            model_revisions=model_revisions,
        )


__all__ = ["EvidencePolicy", "EvidencePolicyConfig"]
