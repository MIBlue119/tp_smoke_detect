from datetime import UTC, datetime
from uuid import uuid4

from tp_smoke_detect.adapters.audio.fake import FakeAudioController
from tp_smoke_detect.adapters.persistence.sqlite import SQLiteAuditRepository
from tp_smoke_detect.application.request_audio import AudioRequestService
from tp_smoke_detect.contracts import DecisionCompleted, DecisionOutcome, RunMode
from tp_smoke_detect.domain.policy.audio import AudioPolicy, AudioPolicyConfig


def _decision(mode: RunMode = RunMode.AUTOMATIC) -> DecisionCompleted:
    return DecisionCompleted(
        decision_id=uuid4(),
        camera_id="cam-1",
        track_id="track-1",
        outcome=DecisionOutcome.VERIFIED,
        reason_codes=["verified"],
        policy_revision="p1",
        latency_ms=1,
        mode=mode,
        audio_eligibility=True,
    )


def test_service_records_shadow_without_calling_audio_adapter() -> None:
    adapter = FakeAudioController()
    repository = SQLiteAuditRepository()
    service = AudioRequestService(
        AudioPolicy(AudioPolicyConfig(mode=RunMode.SHADOW, audio_muted=False)), adapter, repository
    )
    decision = _decision(RunMode.SHADOW)
    result = service.request(decision, camera_id="cam-1", zone_id="zone-a", now=datetime.now(UTC))
    assert result.outcome == "would_announce"
    assert adapter.commands == []
    receipt = repository.get_audio_receipt(f"audio:{decision.decision_id}")
    assert receipt is not None
    assert receipt["reason_code"] == "would_announce"


def test_service_calls_adapter_only_after_policy_and_receipt_is_immutable() -> None:
    adapter = FakeAudioController()
    repository = SQLiteAuditRepository()
    service = AudioRequestService(
        AudioPolicy(AudioPolicyConfig(mode=RunMode.AUTOMATIC, audio_muted=False)),
        adapter,
        repository,
    )
    decision = _decision()
    result = service.request(decision, camera_id="cam-1", zone_id="zone-a", now=datetime.now(UTC))
    assert result.allowed
    assert len(adapter.commands) == 1
    saved = repository.get_audio_receipt(f"audio:{decision.decision_id}")
    assert saved is not None
    service.request(decision, camera_id="cam-1", zone_id="zone-a", now=datetime.now(UTC))
    assert len(adapter.commands) == 1
    assert repository.get_audio_receipt(f"audio:{decision.decision_id}") == saved
