import json
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

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


class _RaisingAudioController:
    def send(self, command: AudioCommand) -> AudioPlaybackReceipt:
        raise RuntimeError("controller unavailable")


def _decision(mode: RunMode = RunMode.AUTOMATIC) -> DecisionCompleted:
    return DecisionCompleted(
        event_id=uuid4(),
        correlation_id=uuid4(),
        producer="test",
        occurred_at=datetime.now(UTC),
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
    # Accepted playback is immutable safety history; the retry is retained in
    # audio_receipt_attempts without erasing cap/cooldown evidence.
    assert latest["playback"]["status"] == "accepted"
    attempt_statuses = [
        json.loads(row["playback"])["status"]
        for row in repository.connection.execute(
            "SELECT playback FROM audio_receipt_attempts ORDER BY attempt_no"
        ).fetchall()
    ]
    assert attempt_statuses == ["pending", "accepted", "duplicate"]
    attempts = repository.connection.execute(
        "SELECT attempt_no, outcome FROM audio_receipt_attempts WHERE receipt_id=? "
        "ORDER BY attempt_no",
        (f"audio:{decision.decision_id}",),
    ).fetchall()
    assert [(row[0], row[1]) for row in attempts] == [
        (1, "reserved"),
        (2, "announce_requested"),
        (3, "announce_requested"),
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
    assert len(attempts) == 3
    assert attempts[0][1].find('"status":"pending"') >= 0
    assert attempts[1][1].find('"status":"failed"') >= 0
    assert attempts[2][1].find('"status":"accepted"') >= 0


def test_controller_exception_finalizes_reservation_before_reraising() -> None:
    repository = SQLiteAuditRepository()
    service = AudioRequestService(
        AudioPolicy(AudioPolicyConfig(mode=RunMode.AUTOMATIC, audio_muted=False)),
        _RaisingAudioController(),
        repository,
    )
    decision = _decision()
    with pytest.raises(RuntimeError, match="controller unavailable"):
        service.request(decision, camera_id="cam-1", zone_id="zone-a", now=datetime.now(UTC))
    saved = repository.get_audio_receipt(f"audio:{decision.decision_id}")
    assert saved is not None
    assert saved["playback"]["status"] == "failed"
    assert saved["reason_code"] == "adapter_error"


def test_pending_contender_cannot_clear_owner_and_stale_pending_is_released() -> None:
    repository = SQLiteAuditRepository()
    now = datetime.now(UTC)
    first = repository.reserve_audio_receipt(
        {
            "receipt_id": "audio:first",
            "decision_id": "first",
            "camera_id": "cam-1",
            "zone_id": "zone-a",
            "created_at": now.isoformat(),
        },
        cooldown_seconds=600,
        hourly_audio_cap=1,
        daily_audio_cap=1,
        reservation_ttl_seconds=30,
    )
    contender = repository.reserve_audio_receipt(
        {
            "receipt_id": "audio:contender",
            "decision_id": "contender",
            "camera_id": "cam-1",
            "zone_id": "zone-a",
            "created_at": now.isoformat(),
        },
        cooldown_seconds=600,
        hourly_audio_cap=1,
        daily_audio_cap=1,
        reservation_ttl_seconds=30,
    )
    assert contender["reservation_status"] == "rejected"
    owner = repository.get_audio_receipt("audio:first")
    assert owner is not None
    assert owner["playback"]["status"] == "pending"
    assert first["playback"]["reservation_token"] != contender.get("reservation_token")

    stale = repository.reserve_audio_receipt(
        {
            "receipt_id": "audio:stale",
            "decision_id": "stale",
            "camera_id": "cam-1",
            "zone_id": "zone-b",
            "created_at": (now - timedelta(seconds=10)).isoformat(),
        },
        reservation_ttl_seconds=1,
    )
    fresh = repository.reserve_audio_receipt(
        {
            "receipt_id": "audio:fresh",
            "decision_id": "fresh",
            "camera_id": "cam-1",
            "zone_id": "zone-b",
            "created_at": now.isoformat(),
        },
        reservation_ttl_seconds=30,
    )
    assert stale["playback"]["status"] == "pending"
    assert fresh["playback"]["status"] == "pending"
    assert repository.get_audio_receipt("audio:stale")["playback"]["status"] == "released"
    assert repository.get_audio_receipt("audio:fresh")["playback"]["status"] == "pending"
