"""A virtual-time smoking-cycle state machine."""

from __future__ import annotations

from dataclasses import dataclass

from ..models.observations import DomainObservation


@dataclass(frozen=True, slots=True)
class CycleDetectorConfig:
    min_dwell_ms: int = 250
    min_cycle_interval_ms: int = 500
    max_cycle_interval_ms: int = 15_000

    def __post_init__(self) -> None:
        if self.min_dwell_ms < 0 or self.min_cycle_interval_ms < 0:
            raise ValueError("cycle thresholds must be non-negative")
        if self.max_cycle_interval_ms < self.min_cycle_interval_ms:
            raise ValueError("max_cycle_interval_ms must not be below minimum")


@dataclass(frozen=True, slots=True)
class SmokingCycle:
    approach_at_ns: int
    retreat_at_ns: int
    interval_ms: int


class CycleDetector:
    """Detect approach-dwell-retreat cycles without consulting wall time.

    Nose contact, unknown contact, and observations failing pose/quality are
    intentionally ignored.  The detector stores only the current open
    approach and completed cycle summaries.
    """

    def __init__(self, config: CycleDetectorConfig | None = None) -> None:
        self.config = config or CycleDetectorConfig()
        self._approach_at_ns: int | None = None
        self._cycles: list[SmokingCycle] = []
        self._last_timestamp_ns: int | None = None
        self._seen: set[tuple[object, ...]] = set()

    @property
    def cycles(self) -> tuple[SmokingCycle, ...]:
        return tuple(self._cycles)

    @property
    def count(self) -> int:
        return len(self._cycles)

    def observe(self, observation: DomainObservation) -> SmokingCycle | None:
        if observation.fingerprint in self._seen:
            return None
        self._seen.add(observation.fingerprint)
        if (
            self._last_timestamp_ns is not None
            and observation.timestamp_ns < self._last_timestamp_ns
        ):
            return None
        self._last_timestamp_ns = observation.timestamp_ns

        if not observation.quality_eligible or not observation.pose_eligible:
            return None
        if observation.contact_target != "mouth":
            if observation.hand_retreat:
                self._approach_at_ns = None
            return None

        if observation.hand_to_mouth and observation.mouth_dwell_ms >= self.config.min_dwell_ms:
            if self._approach_at_ns is None:
                self._approach_at_ns = observation.timestamp_ns
            return None

        if not observation.hand_retreat or self._approach_at_ns is None:
            return None
        interval_ms = (observation.timestamp_ns - self._approach_at_ns) // 1_000_000
        approach_at_ns = self._approach_at_ns
        self._approach_at_ns = None
        if (
            not self.config.min_cycle_interval_ms
            <= interval_ms
            <= self.config.max_cycle_interval_ms
        ):
            return None
        cycle = SmokingCycle(approach_at_ns, observation.timestamp_ns, interval_ms)
        self._cycles.append(cycle)
        return cycle


__all__ = ["CycleDetector", "CycleDetectorConfig", "SmokingCycle"]
