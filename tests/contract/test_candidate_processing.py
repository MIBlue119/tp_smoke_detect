from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from threading import Event
from time import monotonic, sleep
from typing import Any, Literal
from uuid import uuid4

import pytest

from tp_smoke_detect.adapters.audio.fake import FakeAudioController
from tp_smoke_detect.adapters.messaging.mqtt import (
    MqttCandidateConsumer,
    MqttConsumerConfig,
    MqttDelivery,
)
from tp_smoke_detect.adapters.persistence.sqlite import SQLiteAuditRepository
from tp_smoke_detect.application.candidate_processing import (
    CandidateMessageError,
    CandidateProcessingService,
)
from tp_smoke_detect.application.request_audio import AudioRequestService
from tp_smoke_detect.contracts import (
    BoundingBox,
    CandidateEnvelope,
    CandidateInferenceReceipt,
    CandidateInferenceRole,
    CandidateInferenceStatus,
    CandidateOutputSchema,
    DeadlineOutcome,
    Geometry,
    ObjectObservation,
    Observations,
    PoseObservation,
    Quality,
    RunMode,
    SmokeObservation,
    Stage,
    TemporalObservation,
)
from tp_smoke_detect.domain.cascade.engine import CascadePolicy
from tp_smoke_detect.domain.policy.audio import AudioPolicy, AudioPolicyConfig


def _candidate(
    *,
    event_id: Any | None = None,
    pts_ns: int = 1_000_000_000,
    retreat: bool = False,
    correlation_id: Any | None = None,
    object_label: Literal[
        "cigarette",
        "vape",
        "phone",
        "cup",
        "food",
        "pen_toothpick",
        "betel_quid",
        "background",
        "unknown",
    ] = "cigarette",
) -> CandidateEnvelope:
    now = datetime(2026, 8, 24, tzinfo=UTC)
    correlation_id = correlation_id or uuid4()
    roles = [
        (CandidateInferenceRole.DETECTOR, CandidateOutputSchema.DETECTOR_V1),
        (CandidateInferenceRole.POSE, CandidateOutputSchema.POSE_V1),
        (CandidateInferenceRole.OBJECT, CandidateOutputSchema.OBJECT_V1),
        (CandidateInferenceRole.SMOKE, CandidateOutputSchema.SMOKE_V1),
        (CandidateInferenceRole.TEMPORAL, CandidateOutputSchema.TEMPORAL_V1),
    ]
    revisions = {role: f"{role.value}-r1" for role, _schema in roles}
    receipts = [
        CandidateInferenceReceipt(
            role=role,
            status=CandidateInferenceStatus.OK,
            request_id=uuid4(),
            correlation_id=correlation_id,
            model_revision=revisions[role],
            artifact_revision="bundle-r1",
            output_schema=schema,
            deadline_outcome=DeadlineOutcome.MET,
            score=0.95,
        )
        for role, schema in roles
    ]
    return CandidateEnvelope(
        event_id=event_id or uuid4(),
        correlation_id=correlation_id,
        producer="deepstream-test",
        occurred_at=now,
        camera_id="camera-1",
        track_id="track-1",
        camera_config_revision="camera-r1",
        source_pts_ns=pts_ns,
        capture_ts_ns=pts_ns,
        received_ts_ns=pts_ns + 5_000_000,
        first_seen_at=now,
        last_seen_at=now + timedelta(milliseconds=100),
        stage=Stage.COMPLETED,
        geometry=Geometry(
            person_box=BoundingBox(x=0.1, y=0.1, width=0.3, height=0.6, confidence=0.99),
            roi_id="zone-1",
        ),
        quality=Quality(
            source_width=1920,
            source_height=1080,
            face_pixels=100,
            crop_pixels=500,
            illumination_profile="day",
            eligible=True,
            eligibility_reason="eligible",
        ),
        observations=Observations(
            pose=PoseObservation(
                hand_to_mouth_distance=0.03,
                mouth_dwell_ms=0 if retreat else 400,
                confidence=0.98,
            ),
            objects=[ObjectObservation(label=object_label, confidence=0.96)],
            smoke=SmokeObservation(smoke_score=0.9, ember_score=0.8),
            temporal=TemporalObservation(
                hand_retreat=retreat,
                cycle_interval_ms=1000 if retreat else None,
                persistence_ms=2500,
            ),
            independent_channels=["object", "smoke"],
        ),
        inference_receipts=receipts,
        model_revisions=revisions,
    )


def _service(repository: SQLiteAuditRepository) -> CandidateProcessingService:
    audio = AudioRequestService(
        AudioPolicy(
            AudioPolicyConfig(mode=RunMode.SHADOW, audio_muted=False, policy_revision="p-r1")
        ),
        FakeAudioController(),
        repository,
    )
    return CandidateProcessingService(
        repository,
        audio,
        cascade_policy=CascadePolicy(),
        zone_by_camera={"camera-1": "zone-1"},
    )


def test_candidate_service_deduplicates_and_emits_one_decision() -> None:
    repository = SQLiteAuditRepository()
    service = _service(repository)
    approach = _candidate(pts_ns=1_000_000_000)
    retreat = _candidate(
        pts_ns=2_000_000_000,
        retreat=True,
        correlation_id=approach.correlation_id,
    )

    first = service.process_payload(approach.model_dump_json())
    second = service.process_payload(retreat.model_dump_json())
    duplicate = service.process_payload(retreat.model_dump_json())

    assert first.status == "completed"
    assert second.status == "completed"
    assert duplicate.status == "duplicate"
    decisions = repository.list_decisions(camera_id="camera-1")
    assert len(decisions) == 2
    assert sum(item["outcome"] == "verified" for item in decisions) == 1
    receipts = repository.list_audio_receipts(camera_id="camera-1")
    assert len(receipts) == 1
    assert receipts[0]["outcome"] == "would_announce"


def test_missing_receipt_is_persisted_rejected_and_cannot_request_audio() -> None:
    repository = SQLiteAuditRepository()
    service = _service(repository)
    candidate = _candidate()
    payload = candidate.model_dump(mode="json")
    payload["inference_receipts"] = [
        item for item in payload["inference_receipts"] if item["role"] != "object"
    ]
    # Re-parse is intentionally done by the service so a wire payload is the
    # same path used by MQTT.  The contract itself remains additive; the core
    # applies the strict GPU receipt gate.
    result = service.process_payload(json.dumps(payload))

    assert result.status == "completed"
    assert result.decision is not None
    assert result.decision.outcome.value == "rejected"
    assert result.decision.audio_eligibility is False
    assert repository.list_audio_receipts() == []


def test_mismatched_receipt_correlation_fails_closed() -> None:
    repository = SQLiteAuditRepository()
    service = _service(repository)
    candidate = _candidate()
    payload = candidate.model_dump(mode="json")
    payload["inference_receipts"][0]["correlation_id"] = str(uuid4())

    result = service.process_payload(json.dumps(payload))

    assert result.decision is not None
    assert result.decision.outcome.value == "rejected"
    assert result.decision.audio_eligibility is False


def test_malformed_payload_is_a_poison_message() -> None:
    service = _service(SQLiteAuditRepository())
    with pytest.raises(CandidateMessageError):
        service.process_payload(b"not-json")


def test_mqtt_consumer_acknowledges_only_after_service_completion() -> None:
    repository = SQLiteAuditRepository()
    service = _service(repository)
    acknowledged = Event()
    delivery = MqttDelivery(
        payload=_candidate().model_dump_json(),
        ack=acknowledged.set,
        dead_letter=lambda reason: pytest.fail(reason),
    )
    consumer = MqttCandidateConsumer(service, config=MqttConsumerConfig(max_inflight=2))
    consumer.start(workers=1)
    assert consumer.submit(delivery)
    deadline = monotonic() + 2
    while not acknowledged.is_set() and monotonic() < deadline:
        sleep(0.01)
    consumer.stop()
    assert acknowledged.is_set()
    assert repository.list_decisions(camera_id="camera-1")


def test_mqtt_backpressure_requests_retry_without_blocking() -> None:
    service = _service(SQLiteAuditRepository())
    retries: list[int] = []
    consumer = MqttCandidateConsumer(service, config=MqttConsumerConfig(max_inflight=1))
    first = MqttDelivery(payload=_candidate().model_dump_json())
    second = MqttDelivery(
        payload=_candidate().model_dump_json(), retry=lambda attempt: retries.append(attempt)
    )
    assert consumer.submit(first)
    assert not consumer.submit(second)
    assert retries == [1]
