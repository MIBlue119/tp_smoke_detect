from datetime import UTC, datetime
from uuid import uuid4

from tp_smoke_detect.adapters.audio.fake import FakeAudioController
from tp_smoke_detect.adapters.persistence.sqlite import SQLiteAuditRepository
from tp_smoke_detect.application.request_audio import AudioRequestService
from tp_smoke_detect.contracts import AudioCommand, DecisionCompleted, DecisionOutcome, RunMode
from tp_smoke_detect.domain.policy.audio import AudioPolicy, AudioPolicyConfig
from tp_smoke_detect.ports.audio import AudioPlaybackReceipt, PlaybackStatus


class _FailOnceAudioController:
    def __init__(self) -> None:
        self.calls = 0

    def send(self, command: AudioCommand) -> AudioPlaybackReceipt:
        self.calls += 1
        return AudioPlaybackReceipt(
            command_id=str(command.command_id),
            decision_id=str(command.decision_id),
            status=PlaybackStatus.FAILED if self.calls == 1 else PlaybackStatus.ACCEPTED,
            detail_code="temporary" if self.calls == 1 else None,
        )


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


def test_service_calls_adapter_only_after_policy_and_records_retry_outcomes() -> None:
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
    latest = repository.get_audio_receipt(f"audio:{decision.decision_id}")
    assert latest is not None
    assert latest["playback"]["status"] == "duplicate"
    attempts = repository.connection.execute(
        "SELECT attempt_no, outcome FROM audio_receipt_attempts WHERE receipt_id=? "
        "ORDER BY attempt_no",
        (f"audio:{decision.decision_id}",),
    ).fetchall()
    assert [(row[0], row[1]) for row in attempts] == [
        (1, "announce_requested"),
        (2, "announce_requested"),
    ]
    assert saved["playback"]["status"] == "accepted"


def test_failed_delivery_then_acceptance_is_latest_for_policy_history() -> None:
    adapter = _FailOnceAudioController()
    repository = SQLiteAuditRepository()
    service = AudioRequestService(
        AudioPolicy(AudioPolicyConfig(mode=RunMode.AUTOMATIC, audio_muted=False)),
        adapter,
        repository,
    )
    decision = _decision()
    service.request(decision, camera_id="cam-1", zone_id="zone-a", now=datetime.now(UTC))
    service.request(decision, camera_id="cam-1", zone_id="zone-a", now=datetime.now(UTC))
    latest = repository.get_audio_receipt(f"audio:{decision.decision_id}")
    assert latest is not None
    assert latest["playback"]["status"] == "accepted"
    attempts = repository.connection.execute(
        "SELECT outcome, playback FROM audio_receipt_attempts "
        "WHERE receipt_id=? ORDER BY attempt_no",
        (f"audio:{decision.decision_id}",),
    ).fetchall()
    assert len(attempts) == 2
    assert attempts[0][1].find('"status":"failed"') >= 0
    assert attempts[1][1].find('"status":"accepted"') >= 0
