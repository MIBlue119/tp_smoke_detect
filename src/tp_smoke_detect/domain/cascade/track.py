"""Track-level ordered aggregation."""

from __future__ import annotations

from dataclasses import dataclass, field

from ...contracts import Stage
from ..models.decisions import ReasonCode, StageTransition
from ..models.observations import DomainObservation
from .cycle import CycleDetector, CycleDetectorConfig


@dataclass(slots=True)
class TrackState:
    """Mutable state for one camera/track pair.

    The state is explicitly driven by source timestamps.  There is no call to
    ``datetime.now`` or a scheduler, which makes retries and virtual-time
    tests deterministic.
    """

    camera_id: str
    track_id: str
    gap_tolerance_ms: int = 2_000
    cycle_config: CycleDetectorConfig = field(default_factory=CycleDetectorConfig)
    stage: Stage = Stage.DETECTED
    first_seen_ns: int | None = None
    last_seen_ns: int | None = None
    observations: list[DomainObservation] = field(default_factory=list)
    transitions: list[StageTransition] = field(default_factory=list)
    _seen: set[tuple[object, ...]] = field(default_factory=set, repr=False)
    _cycle_detector: CycleDetector = field(init=False, repr=False)

    def __post_init__(self) -> None:
        if not self.camera_id or not self.track_id:
            raise ValueError("camera_id and track_id are required")
        if self.gap_tolerance_ms < 0:
            raise ValueError("gap_tolerance_ms must be non-negative")
        self._cycle_detector = CycleDetector(self.cycle_config)

    @property
    def cycle_count(self) -> int:
        return self._cycle_detector.count

    @property
    def persistence_ms(self) -> int:
        if self.first_seen_ns is None or self.last_seen_ns is None:
            return 0
        return (self.last_seen_ns - self.first_seen_ns) // 1_000_000

    def _transition(self, target: Stage, timestamp_ns: int, *reasons: ReasonCode) -> None:
        if self.stage == target:
            return
        transition = StageTransition(self.stage, target, timestamp_ns, tuple(reasons))
        self.transitions.append(transition)
        self.stage = target

    def ingest(self, observation: DomainObservation) -> tuple[ReasonCode, ...]:
        """Accept an observation and return ingestion facts.

        Duplicate and out-of-order messages are ignored.  A gap beyond the
        configured tolerance starts a new aggregation window while preserving
        the track identity; this prevents disconnected people from joining a
        single smoking cycle.
        """

        fingerprint = observation.fingerprint
        if fingerprint in self._seen:
            return (ReasonCode.DUPLICATE,)
        self._seen.add(fingerprint)

        if self.last_seen_ns is not None and observation.timestamp_ns < self.last_seen_ns:
            return (ReasonCode.OUT_OF_ORDER,)

        reasons: list[ReasonCode] = []
        if (
            self.last_seen_ns is not None
            and observation.timestamp_ns - self.last_seen_ns > self.gap_tolerance_ms * 1_000_000
        ):
            self.observations.clear()
            self._cycle_detector = CycleDetector(self.cycle_config)
            self.first_seen_ns = observation.timestamp_ns
            previous_stage = self.stage
            self.stage = Stage.DETECTED
            self.transitions.append(
                StageTransition(
                    previous_stage,
                    Stage.DETECTED,
                    observation.timestamp_ns,
                    (ReasonCode.TRACK_GAP,),
                )
            )
            reasons.append(ReasonCode.TRACK_GAP)
        elif self.first_seen_ns is None:
            self.first_seen_ns = observation.timestamp_ns

        self.last_seen_ns = observation.timestamp_ns
        self.observations.append(observation)
        self._cycle_detector.observe(observation)

        if not observation.quality_eligible:
            self._transition(Stage.COMPLETED, observation.timestamp_ns, ReasonCode.QUALITY_REJECTED)
        elif self.stage == Stage.DETECTED:
            self._transition(Stage.QUALITY, observation.timestamp_ns)
        if observation.quality_eligible and not observation.pose_eligible:
            self._transition(Stage.COMPLETED, observation.timestamp_ns, ReasonCode.POSE_REJECTED)
        elif observation.quality_eligible and self.stage == Stage.QUALITY:
            self._transition(Stage.POSE, observation.timestamp_ns)
        if observation.quality_eligible and observation.pose_eligible and self.stage == Stage.POSE:
            self._transition(Stage.OBJECT, observation.timestamp_ns)
        if self.stage == Stage.OBJECT and (
            observation.object_label is not None or observation.smoke_score > 0
        ):
            self._transition(Stage.TEMPORAL, observation.timestamp_ns)
        if self.stage == Stage.TEMPORAL and self.cycle_count > 0:
            self._transition(Stage.REVIEW, observation.timestamp_ns, ReasonCode.CYCLE_DETECTED)
        return tuple(reasons)


__all__ = ["TrackState"]
