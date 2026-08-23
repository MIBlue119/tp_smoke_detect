from typing import Any

import pytest

from tp_smoke_detect.contracts import DecisionOutcome
from tp_smoke_detect.domain.cascade.engine import CascadeEngine
from tp_smoke_detect.domain.models.observations import DomainObservation, OptionalVlmResult


def cycle_observation(timestamp_ms: int, **kwargs: Any) -> DomainObservation:
    return DomainObservation(
        timestamp_ns=timestamp_ms * 1_000_000,
        persistence_ms=2_500,
        **kwargs,
    )


@pytest.mark.parametrize(
    ("veto", "reason"),
    [
        ("chewing", "veto_chewing"),
        ("phone", "veto_phone"),
        ("drink", "veto_drink"),
        ("food", "veto_food"),
        ("pen_toothpick", "veto_pen_toothpick"),
    ],
)
def test_veto_dominates_positive_object(veto: str, reason: str) -> None:
    engine = CascadeEngine("cam-a", "track-1")
    engine.ingest(
        cycle_observation(
            0,
            hand_to_mouth=True,
            contact_target="mouth",
            mouth_dwell_ms=300,
            object_label="cigarette",
            object_score=0.99,
            positive_channels=frozenset({"object", "smoke"}),
            vetoes=frozenset({veto}),
        )
    )
    decision = engine.ingest(
        cycle_observation(
            1_000,
            contact_target="mouth",
            hand_retreat=True,
            object_label="cigarette",
            object_score=0.99,
            smoke_score=0.9,
            positive_channels=frozenset({"object", "smoke"}),
            vetoes=frozenset({veto}),
        )
    )
    assert decision.outcome is DecisionOutcome.REJECTED
    assert reason in {code.value for code in decision.reason_codes}
    assert decision.audio_eligibility is False


def test_one_cycle_or_one_channel_is_not_verified() -> None:
    engine = CascadeEngine("cam-a", "track-1")
    engine.ingest(
        cycle_observation(
            0,
            hand_to_mouth=True,
            contact_target="mouth",
            mouth_dwell_ms=300,
            positive_channels=frozenset({"object"}),
        )
    )
    decision = engine.ingest(
        cycle_observation(
            1_000,
            contact_target="mouth",
            hand_retreat=True,
            positive_channels=frozenset({"object"}),
        )
    )
    assert decision.outcome is DecisionOutcome.REJECTED
    assert decision.audio_eligibility is False
    assert "independent_evidence_insufficient" in {code.value for code in decision.reason_codes}


def test_optional_vlm_timeout_fails_closed() -> None:
    engine = CascadeEngine("cam-a", "track-1")
    engine.ingest(
        cycle_observation(
            0,
            hand_to_mouth=True,
            contact_target="mouth",
            mouth_dwell_ms=300,
            positive_channels=frozenset({"object", "smoke"}),
            vlm=OptionalVlmResult("timeout", revision="vlm-7"),
        )
    )
    decision = engine.ingest(
        cycle_observation(
            1_000,
            contact_target="mouth",
            hand_retreat=True,
            positive_channels=frozenset({"object", "smoke"}),
            vlm=OptionalVlmResult("timeout", revision="vlm-7"),
        )
    )
    assert decision.outcome is DecisionOutcome.UNCLEAR
    assert decision.reason_codes[0].value == "vlm_timeout"
    assert decision.audio_eligibility is False
