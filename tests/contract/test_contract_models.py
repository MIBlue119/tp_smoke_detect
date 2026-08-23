from datetime import UTC, datetime
from uuid import uuid4

import pytest
from pydantic import ValidationError

from tp_smoke_detect.contracts import (
    AudioCommand,
    BoundingBox,
    CandidateEnvelope,
    DecisionCompleted,
    DecisionOutcome,
    EvidenceChannel,
    Geometry,
    Observations,
    Quality,
    RunMode,
    Stage,
)


def candidate() -> CandidateEnvelope:
    now = datetime.now(UTC)
    return CandidateEnvelope(
        event_id=uuid4(),
        correlation_id=uuid4(),
        producer="test",
        occurred_at=now,
        camera_id="cam-01",
        track_id="track-1",
        camera_config_revision="cam-01-r1",
        source_pts_ns=1,
        capture_ts_ns=2,
        received_ts_ns=3,
        first_seen_at=now,
        last_seen_at=now,
        stage=Stage.DETECTED,
        geometry=Geometry(
            person_box=BoundingBox(x=0.1, y=0.1, width=0.2, height=0.4, confidence=0.9)
        ),
        quality=Quality(
            source_width=1920,
            source_height=1080,
            face_pixels=80,
            crop_pixels=200,
            illumination_profile="day",
            eligible=True,
            eligibility_reason="within_camera_limits",
        ),
        observations=Observations(),
    )


def test_candidate_contains_no_raw_pixels() -> None:
    payload = candidate().model_dump(mode="json")
    assert "raw_pixels" not in payload
    assert payload["schema_version"] == "track.candidate.v1"


def test_v1_decision_and_audio_envelopes_are_typed() -> None:
    decision_id = uuid4()
    decision = DecisionCompleted(
        event_id=uuid4(),
        correlation_id=uuid4(),
        producer="test",
        occurred_at=datetime.now(UTC),
        decision_id=decision_id,
        camera_id="cam-01",
        track_id="track-1",
        outcome=DecisionOutcome.VERIFIED,
        reason_codes=["two_independent_channels"],
        evidence_channels=[EvidenceChannel(name="pose", score=0.9, positive=True)],
        policy_revision="policy-r1",
        latency_ms=20.5,
        mode=RunMode.SHADOW,
        audio_eligibility=False,
    )
    audio = AudioCommand(
        event_id=uuid4(),
        correlation_id=decision_id,
        producer="test",
        occurred_at=datetime.now(UTC),
        decision_id=decision_id,
        command_id=uuid4(),
        zone_id="lobby",
        message_id="smoke-reminder-zh-tw-01",
        volume_profile="default",
        expires_at=datetime.now(UTC),
        policy_revision="policy-r1",
    )
    assert decision.model_dump(mode="json")["decision_id"] == str(decision_id)
    assert audio.schema_version == "audio.command.v1"


def test_contract_rejects_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        CandidateEnvelope.model_validate({**candidate().model_dump(), "raw_pixels": "secret"})


def test_v1_envelopes_require_delivery_metadata() -> None:
    payload = candidate().model_dump(mode="json")
    payload.pop("event_id")
    with pytest.raises(ValidationError, match="event_id"):
        CandidateEnvelope.model_validate(payload)
