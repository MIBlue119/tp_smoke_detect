from typing import Any

from tp_smoke_detect.domain.models.observations import DomainObservation

from tp_smoke_detect.domain.cascade.cycle import CycleDetector, CycleDetectorConfig


def observation(timestamp_ms: int, **kwargs: Any) -> DomainObservation:
    return DomainObservation(timestamp_ns=timestamp_ms * 1_000_000, **kwargs)


def test_mouth_approach_dwell_retreat_forms_one_cycle() -> None:
    detector = CycleDetector(CycleDetectorConfig(min_dwell_ms=200, min_cycle_interval_ms=500))
    detector.observe(
        observation(
            0,
            hand_to_mouth=True,
            contact_target="mouth",
            mouth_dwell_ms=300,
        )
    )
    cycle = detector.observe(observation(1_000, contact_target="mouth", hand_retreat=True))

    assert cycle is not None
    assert cycle.interval_ms == 1_000
    assert detector.count == 1


def test_nose_touch_and_duplicate_retry_cannot_form_cycle() -> None:
    detector = CycleDetector()
    nose = observation(
        0,
        hand_to_mouth=True,
        contact_target="nose",
        mouth_dwell_ms=1_000,
        event_id="nose-1",
    )
    assert detector.observe(nose) is None
    assert detector.observe(nose) is None
    assert detector.observe(observation(1_000, contact_target="nose", hand_retreat=True)) is None
    assert detector.count == 0


def test_out_of_order_observation_is_ignored() -> None:
    detector = CycleDetector()
    detector.observe(
        observation(1_000, contact_target="mouth", hand_to_mouth=True, mouth_dwell_ms=300)
    )
    detector.observe(observation(500, contact_target="mouth", hand_retreat=True))
    assert detector.count == 0
