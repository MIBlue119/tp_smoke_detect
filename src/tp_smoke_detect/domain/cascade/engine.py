"""Facade for deterministic track aggregation and final policy evaluation."""

from __future__ import annotations

from dataclasses import dataclass

from ..models.decisions import CascadeDecision, ReasonCode
from ..models.observations import DomainObservation
from ..policy.evidence import EvidencePolicy, EvidencePolicyConfig
from .cycle import CycleDetectorConfig
from .track import TrackState


@dataclass(frozen=True, slots=True)
class CascadePolicy:
    gap_tolerance_ms: int = 2_000
    cycle: CycleDetectorConfig = CycleDetectorConfig()
    evidence: EvidencePolicyConfig = EvidencePolicyConfig()


class CascadeEngine:
    """Provider-neutral domain port used by replay and live adapters."""

    def __init__(
        self,
        camera_id: str,
        track_id: str,
        policy: CascadePolicy | None = None,
    ) -> None:
        self.policy = policy or CascadePolicy()
        self.track = TrackState(
            camera_id=camera_id,
            track_id=track_id,
            gap_tolerance_ms=self.policy.gap_tolerance_ms,
            cycle_config=self.policy.cycle,
        )
        self.evidence_policy = EvidencePolicy(self.policy.evidence)
        self._last_ingest_reasons: tuple[ReasonCode, ...] = ()

    @property
    def last_ingest_reasons(self) -> tuple[ReasonCode, ...]:
        return self._last_ingest_reasons

    def ingest(self, observation: DomainObservation) -> CascadeDecision:
        self._last_ingest_reasons = self.track.ingest(observation)
        if (
            ReasonCode.OUT_OF_ORDER in self._last_ingest_reasons
            or ReasonCode.DUPLICATE in self._last_ingest_reasons
        ):
            return self.evaluate()
        return self.evaluate()

    def evaluate(self) -> CascadeDecision:
        model_revisions = tuple(
            sorted({pair for item in self.track.observations for pair in item.model_revisions})
        )
        return self.evidence_policy.evaluate(
            self.track.observations,
            cycle_count=self.track.cycle_count,
            model_revisions=model_revisions,
        )


__all__ = ["CascadeEngine", "CascadePolicy"]
