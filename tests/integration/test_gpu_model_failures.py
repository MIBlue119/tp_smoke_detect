from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from tp_smoke_detect.adapters.audio.fake import FakeAudioController
from tp_smoke_detect.adapters.persistence.sqlite import SQLiteAuditRepository
from tp_smoke_detect.application.candidate_processing import CandidateProcessingService
from tp_smoke_detect.application.request_audio import AudioRequestService
from tp_smoke_detect.contracts import (
    BoundingBox,
    CandidateEnvelope,
    CandidateInferenceReason,
    CandidateInferenceReceipt,
    CandidateInferenceRole,
    CandidateInferenceStatus,
    CandidateOutputSchema,
    DeadlineOutcome,
    Geometry,
    Observations,
    Quality,
    RunMode,
    SmokeObservation,
    Stage,
)
from tp_smoke_detect.domain.policy.audio import AudioPolicy, AudioPolicyConfig


def _stale_positive_candidate() -> CandidateEnvelope:
    now = datetime(2026, 8, 24, tzinfo=UTC)
    correlation_id = uuid4()
    smoke_receipt = CandidateInferenceReceipt.model_construct(
        role=CandidateInferenceRole.SMOKE,
        status=CandidateInferenceStatus.TIMEOUT,
        reason_code=CandidateInferenceReason.TIMEOUT,
        request_id=uuid4(),
        correlation_id=correlation_id,
        model_revision="smoke-r1",
        artifact_revision="bundle-r1",
        output_schema=CandidateOutputSchema.NONE,
        deadline_outcome=DeadlineOutcome.EXPIRED,
    )
    return CandidateEnvelope.model_construct(
        event_id=uuid4(),
        correlation_id=correlation_id,
        producer="deepstream-test",
        occurred_at=now,
        camera_id="camera-failure",
        track_id="track-1",
        camera_config_revision="camera-r1",
        source_pts_ns=1_000_000_000,
        capture_ts_ns=1_000_000_000,
        received_ts_ns=1_005_000_000,
        first_seen_at=now,
        last_seen_at=now,
        stage=Stage.COMPLETED,
        geometry=Geometry(
            person_box=BoundingBox(x=0.1, y=0.1, width=0.3, height=0.6, confidence=0.99)
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
            smoke=SmokeObservation(smoke_score=0.9, ember_score=0.8),
            independent_channels=["smoke"],
        ),
        inference_receipts=[smoke_receipt],
        model_revisions={CandidateInferenceRole.SMOKE: "smoke-r1"},
    )


def test_failed_gpu_receipt_never_becomes_positive_domain_evidence() -> None:
    repository = SQLiteAuditRepository()
    audio = AudioRequestService(
        AudioPolicy(AudioPolicyConfig(mode=RunMode.SHADOW, audio_muted=False)),
        FakeAudioController(),
        repository,
    )
    service = CandidateProcessingService(repository, audio)

    result = service.process_payload(_stale_positive_candidate())

    assert result.decision is not None
    assert result.decision.audio_eligibility is False
    assert not repository.list_audio_receipts()


def test_event_without_explicit_id_is_deduplicated_by_content_fingerprint() -> None:
    repository = SQLiteAuditRepository()
    audio = AudioRequestService(
        AudioPolicy(AudioPolicyConfig(mode=RunMode.SHADOW)),
        FakeAudioController(),
        repository,
    )
    service = CandidateProcessingService(repository, audio, require_gpu_receipts=False)
    candidate = _stale_positive_candidate().model_copy(update={"event_id": None})

    first = service.process_payload(candidate)
    second = service.process_payload(candidate)
    assert first.status == "completed"
    assert second.status == "duplicate"
    assert len(repository.list_decisions()) == 1
