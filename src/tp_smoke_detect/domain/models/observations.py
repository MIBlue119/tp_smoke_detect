"""Stable inputs accepted by the domain cascade."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal


@dataclass(frozen=True, slots=True)
class OptionalVlmResult:
    """A constrained optional verifier result.

    A provider timeout or malformed result is represented explicitly and can
    never be interpreted as a positive signal by the policy.
    """

    status: Literal["positive", "negative", "unclear", "timeout", "malformed"]
    score: float = 0.0
    revision: str = "unknown"

    def __post_init__(self) -> None:
        if not 0.0 <= self.score <= 1.0:
            raise ValueError("VLM score must be between 0 and 1")


@dataclass(frozen=True, slots=True)
class DomainObservation:
    """One timestamped, metadata-only observation for a tracked person.

    ``event_id`` is preferred for retry de-duplication.  When absent, the
    cascade derives a stable fingerprint from all fields, so replaying the
    same message cannot create another cycle.
    """

    timestamp_ns: int
    event_id: str | None = None
    quality_eligible: bool = True
    pose_eligible: bool = True
    hand_to_mouth: bool = False
    contact_target: Literal["mouth", "nose", "unknown"] = "unknown"
    mouth_dwell_ms: int = 0
    hand_retreat: bool = False
    object_label: str | None = None
    object_score: float = 0.0
    smoke_score: float = 0.0
    ember_score: float = 0.0
    persistence_ms: int = 0
    positive_channels: frozenset[str] = field(default_factory=frozenset)
    vetoes: frozenset[str] = field(default_factory=frozenset)
    vlm: OptionalVlmResult | None = None
    model_revisions: tuple[tuple[str, str], ...] = ()

    def __post_init__(self) -> None:
        if self.timestamp_ns < 0:
            raise ValueError("timestamp_ns must be non-negative")
        if self.mouth_dwell_ms < 0 or self.persistence_ms < 0:
            raise ValueError("durations must be non-negative")
        for value, name in (
            (self.object_score, "object_score"),
            (self.smoke_score, "smoke_score"),
            (self.ember_score, "ember_score"),
        ):
            if not 0.0 <= value <= 1.0:
                raise ValueError(f"{name} must be between 0 and 1")

    @property
    def fingerprint(self) -> tuple[object, ...]:
        """Return a deterministic retry key independent of object identity."""

        if self.event_id is not None:
            return ("event", self.event_id)
        return (
            "observation",
            self.timestamp_ns,
            self.quality_eligible,
            self.pose_eligible,
            self.hand_to_mouth,
            self.contact_target,
            self.mouth_dwell_ms,
            self.hand_retreat,
            self.object_label,
            self.object_score,
            self.smoke_score,
            self.ember_score,
            self.persistence_ms,
            tuple(sorted(self.positive_channels)),
            tuple(sorted(self.vetoes)),
            self.vlm,
        )


# A concise alias is useful to adapters and keeps the public domain vocabulary
# independent of any particular model-provider terminology.
Observation = DomainObservation

__all__ = ["DomainObservation", "Observation", "OptionalVlmResult"]
