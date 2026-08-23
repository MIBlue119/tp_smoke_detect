from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Event
from typing import Any
from uuid import uuid4

import pytest

from tp_smoke_detect.adapters.audio.http import HttpAudioController
from tp_smoke_detect.adapters.persistence.sqlite import (
    EvaluationClaimLostError,
    SQLiteAuditRepository,
)
from tp_smoke_detect.api.app import create_app
from tp_smoke_detect.application.request_audio import AudioRequestService
from tp_smoke_detect.contracts import AudioCommand, Point, RunMode
from tp_smoke_detect.domain.policy.audio import AudioPolicy, AudioPolicyConfig
from tp_smoke_detect.observability.metrics import OperationalMetrics
from tp_smoke_detect.ports.audio import AudioPlaybackReceipt, PlaybackStatus
from tp_smoke_detect.settings import AppSettings, CameraProfile, PolicySettings


def _asgi_json(
    app: Any,
    path: str,
    payload: dict[str, object],
    extra_headers: list[tuple[bytes, bytes]] | None = None,
) -> tuple[int, dict[str, Any]]:
    import asyncio
    import json

    body = json.dumps(payload).encode()
    sent: list[dict[str, Any]] = []
    received = False

    async def receive() -> dict[str, Any]:
        nonlocal received
        if received:
            return {"type": "http.disconnect"}
        received = True
        return {"type": "http.request", "body": body, "more_body": False}

    async def send(message: dict[str, Any]) -> None:
        sent.append(message)

    async def invoke() -> None:
        await app(
            {
                "type": "http",
                "http_version": "1.1",
                "method": "POST",
                "scheme": "http",
                "path": path,
                "raw_path": path.encode(),
                "query_string": b"",
                "headers": [
                    (b"host", b"test"),
                    (b"content-type", b"application/json"),
                    *(extra_headers or []),
                ],
                "server": ("test", 80),
                "client": ("test", 1),
            },
            receive,
            send,
        )

    asyncio.run(invoke())
    start = next(message for message in sent if message["type"] == "http.response.start")
    content = b"".join(
        message.get("body", b"") for message in sent if message["type"] == "http.response.body"
    )
    return int(start["status"]), json.loads(content)


def _decision(decision_id: str) -> dict[str, object]:
    return {
        "decision_id": decision_id,
        "camera_id": "cam-1",
        "track_id": "track-1",
        "outcome": "verified",
        "reason_codes": ["two_channels"],
        "evidence_channels": [{"name": "object", "score": 0.9, "positive": True}],
        "model_revisions": {"detector": "r1"},
        "policy_revision": "policy-1",
        "latency_ms": 120.0,
        "mode": "shadow",
        "audio_eligibility": False,
    }


def _eligible_decision_payload(decision_id: str) -> dict[str, object]:
    decision = _decision(decision_id)
    decision.update({"mode": "automatic", "audio_eligibility": True})
    decision.pop("audio_outcome", None)
    return decision


def test_operational_evaluation_rejects_caller_authored_decision() -> None:
    repository = SQLiteAuditRepository()
    app = create_app(
        repository,
        settings=AppSettings(
            policy=PolicySettings(mode=RunMode.AUTOMATIC, audio_muted=False), cameras=[]
        ),
    )
    decision_id = str(uuid4())
    status, response = _asgi_json(
        app,
        "/v1/evaluations",
        {
            "camera_id": "cam-1",
            "mode": "automatic",
            "decision": _eligible_decision_payload(decision_id),
        },
    )
    assert status == 422
    assert "Extra inputs are not permitted" in str(response)
    assert repository.connection.execute("SELECT COUNT(*) FROM decisions").fetchone()[0] == 0


def test_idempotency_key_rejects_a_different_request() -> None:
    repository = SQLiteAuditRepository()
    app = create_app(repository, settings=AppSettings(cameras=[]))
    headers = [(b"idempotency-key", b"same-key")]
    first_status, first = _asgi_json(
        app, "/v1/evaluations", {"camera_id": "cam-a", "mode": "replay"}, headers
    )
    second_status, second = _asgi_json(
        app, "/v1/evaluations", {"camera_id": "cam-b", "mode": "replay"}, headers
    )
    assert first_status == 202
    assert second_status == 409
    assert "idempotency" in str(second).lower()
    assert second.get("detail") != first.get("evaluation_id")


def test_stale_evaluation_claim_is_reclaimed_with_same_fingerprint() -> None:
    repository = SQLiteAuditRepository()
    first = repository.claim_evaluation("old", "lease-key", "cam-1", "fingerprint")
    assert first is None
    repository.connection.execute(
        "UPDATE evaluations SET claim_expires_at=? WHERE idempotency_key=?",
        ("2000-01-01T00:00:00+00:00", "lease-key"),
    )
    repository.connection.commit()
    replacement = repository.claim_evaluation("new", "lease-key", "cam-1", "fingerprint")
    assert replacement is None
    saved = repository.get_evaluation_by_idempotency("lease-key")
    assert saved is not None
    assert saved["evaluation_id"] == "new"


def test_reclaimed_evaluation_owner_is_fenced_before_decision_insert() -> None:
    repository = SQLiteAuditRepository()
    assert repository.claim_evaluation("old", "fence-key", "cam-1", "fingerprint") is None
    repository.connection.execute(
        "UPDATE evaluations SET claim_expires_at=? WHERE idempotency_key=?",
        ("2000-01-01T00:00:00+00:00", "fence-key"),
    )
    repository.connection.commit()
    assert repository.claim_evaluation("new", "fence-key", "cam-1", "fingerprint") is None
    decision = _decision(str(uuid4()))
    evaluation = {
        "evaluation_id": "old",
        "status": "completed",
        "result": {"decision": decision},
        "idempotency_key": "fence-key",
        "request_fingerprint": "fingerprint",
        "camera_id": "cam-1",
    }
    with pytest.raises(EvaluationClaimLostError):
        repository.put_decision_and_evaluation(decision, evaluation)
    assert repository.connection.execute("SELECT COUNT(*) FROM decisions").fetchone()[0] == 0


def test_completed_evaluation_is_not_failed_when_metric_recording_raises() -> None:
    repository = SQLiteAuditRepository()
    app = create_app(repository, settings=AppSettings(cameras=[]))
    original = app.state.metrics.decision

    def fail_metric(*args: object, **kwargs: object) -> None:
        raise RuntimeError("metrics unavailable")

    app.state.metrics.decision = fail_metric
    headers = [(b"idempotency-key", b"metric-key")]
    status, response = _asgi_json(app, "/v1/evaluations", {"camera_id": "cam-1"}, headers)
    assert status == 202
    saved = repository.get_evaluation_by_idempotency("metric-key")
    assert saved is not None
    assert saved["status"] == "completed"
    app.state.metrics.decision = original
    status, response = _asgi_json(app, "/v1/evaluations", {"camera_id": "cam-1"}, headers)
    assert status == 202
    assert response["evaluation_id"] == saved["evaluation_id"]
    assert repository.connection.execute("SELECT COUNT(*) FROM decisions").fetchone()[0] == 1


def test_expired_audio_reservation_requires_explicit_reconciliation_and_never_resends() -> None:
    repository = SQLiteAuditRepository()
    now = datetime.now(UTC)
    repository.reserve_audio_receipt(
        {
            "receipt_id": "audio:expired",
            "decision_id": "expired",
            "camera_id": "cam-1",
            "zone_id": "zone-a",
            "created_at": (now - timedelta(seconds=10)).isoformat(),
        },
        reservation_ttl_seconds=1,
    )
    reconciled = repository.reconcile_expired_audio_receipt(
        "audio:expired", now=now, actor="operator-1", reason="worker host restarted"
    )
    assert reconciled["playback"]["status"] == "uncertain"
    assert reconciled["reconciled_by"] == "operator-1"
    assert reconciled["reconciliation_reason"] == "worker host restarted"
    retry = repository.reserve_audio_receipt(
        {
            "receipt_id": "audio:expired",
            "decision_id": "expired",
            "camera_id": "cam-1",
            "zone_id": "zone-a",
            "created_at": now.isoformat(),
        },
        reservation_ttl_seconds=30,
    )
    assert retry == {
        "reservation_status": "rejected",
        "reservation_reason": "reservation_expired",
    }


def test_reconciliation_is_in_doubt_but_matching_owner_can_finalize() -> None:
    repository = SQLiteAuditRepository()
    now = datetime.now(UTC)
    original_created = now - timedelta(seconds=10)
    reserved = repository.reserve_audio_receipt(
        {
            "receipt_id": "audio:reconcile-owner",
            "decision_id": "reconcile-owner",
            "camera_id": "cam-1",
            "zone_id": "zone-a",
            "created_at": original_created.isoformat(),
        },
        reservation_ttl_seconds=1,
    )
    token = reserved["playback"]["reservation_token"]
    reconciled = repository.reconcile_expired_audio_receipt(
        "audio:reconcile-owner", now=now, actor="operator-1"
    )
    assert reconciled["playback"]["status"] == "uncertain"
    assert reconciled["created_at"] == original_created.isoformat()
    finalized = repository.finalize_audio_receipt(
        "audio:reconcile-owner",
        str(token),
        {
            "decision_id": "reconcile-owner",
            "camera_id": "cam-1",
            "zone_id": "zone-a",
            "outcome": "announce_requested",
            "reason_code": "announced",
            "command_id": "command-1",
            "playback": {"status": "accepted"},
            "created_at": now.isoformat(),
        },
    )
    assert finalized["playback"]["status"] == "accepted"
    attempts = repository.connection.execute(
        "SELECT outcome, playback FROM audio_receipt_attempts "
        "WHERE receipt_id=? ORDER BY attempt_no",
        ("audio:reconcile-owner",),
    ).fetchall()
    assert [row[0] for row in attempts] == ["reserved", "suppressed", "announce_requested"]
    assert '"status":"uncertain"' in attempts[1][1]
    assert '"status":"accepted"' in attempts[2][1]


class _BlockingAcceptAudio:
    def __init__(self) -> None:
        self.started = Event()
        self.release = Event()

    def send(self, command: AudioCommand) -> AudioPlaybackReceipt:
        self.started.set()
        assert self.release.wait(timeout=5)
        return AudioPlaybackReceipt(
            command_id=str(command.command_id),
            decision_id=str(command.decision_id),
            status=PlaybackStatus.ACCEPTED,
        )


def test_reconciliation_during_owner_io_keeps_accepted_audit_fact() -> None:
    repository = SQLiteAuditRepository()
    controller = _BlockingAcceptAudio()
    service = AudioRequestService(
        AudioPolicy(
            AudioPolicyConfig(mode=RunMode.AUTOMATIC, audio_muted=False, command_ttl_seconds=1)
        ),
        controller,
        repository,
    )
    decision_id = str(uuid4())
    decision = _eligible_decision_payload(decision_id)
    started_at = datetime.now(UTC)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(
            service.request,
            decision,
            camera_id="cam-1",
            zone_id="zone-a",
            now=started_at,
        )
        assert controller.started.wait(timeout=5)
        receipt_id = f"audio:{decision_id}"
        reconciled = repository.reconcile_expired_audio_receipt(
            receipt_id, now=started_at + timedelta(seconds=2)
        )
        assert reconciled["playback"]["status"] == "uncertain"
        controller.release.set()
        result = future.result(timeout=5)
    assert result.allowed is True
    saved = repository.get_audio_receipt(f"audio:{decision_id}")
    assert saved is not None
    assert saved["playback"]["status"] == "accepted"
    assert saved["created_at"] == started_at.isoformat()


def test_mounted_camera_profiles_reconcile_updates_and_deactivates_removed_profiles() -> None:
    repository = SQLiteAuditRepository()
    camera = CameraProfile(
        camera_id="cam-mounted",
        zone_id="lobby",
        roi=[Point(x=0, y=0), Point(x=1, y=0), Point(x=1, y=1)],
    )
    create_app(repository, settings=AppSettings(cameras=[camera]))
    changed = camera.model_copy(update={"zone_id": "loading", "enabled": False})
    create_app(repository, settings=AppSettings(cameras=[changed]))
    saved = repository.get_camera("cam-mounted")
    assert saved is not None
    assert saved["zone_id"] == "loading"
    assert saved["enabled"] is False
    assert saved["active"] is False
    create_app(repository, settings=AppSettings(cameras=[]))
    removed = repository.get_camera("cam-mounted")
    assert removed is not None
    assert removed["enabled"] is False
    assert removed["active"] is False
    assert removed["deactivated_by_config"] is True


def test_inactive_camera_cannot_trigger_audio() -> None:
    repository = SQLiteAuditRepository()
    controller = _AlwaysFailAudio()
    repository.upsert_camera(
        {
            "camera_id": "cam-1",
            "zone_id": "zone-a",
            "enabled": False,
            "active": False,
            "revision": "r1",
        }
    )
    decision_id = str(uuid4())
    repository.put_decision(_eligible_decision_payload(decision_id))
    app = create_app(
        repository,
        settings=AppSettings(policy=PolicySettings(mode=RunMode.AUTOMATIC, audio_muted=False)),
        audio_controller=controller,
    )
    response_status, response = _asgi_json(app, "/v1/audio/requests", {"decision_id": decision_id})
    assert response_status == 409
    assert "inactive" in str(response).lower()
    assert controller.calls == 0


class _AlwaysFailAudio:
    def __init__(self) -> None:
        self.calls = 0

    def send(self, command: AudioCommand) -> AudioPlaybackReceipt:
        self.calls += 1
        return AudioPlaybackReceipt(
            command_id=str(command.command_id),
            decision_id=str(command.decision_id),
            status=PlaybackStatus.FAILED,
            detail_code="worker_unavailable",
        )


def test_failed_audio_attempt_is_suppressed_and_consumes_cap() -> None:
    repository = SQLiteAuditRepository()
    controller = _AlwaysFailAudio()
    service = AudioRequestService(
        AudioPolicy(
            AudioPolicyConfig(
                mode=RunMode.AUTOMATIC,
                audio_muted=False,
                hourly_audio_cap=1,
                daily_audio_cap=1,
            )
        ),
        controller,
        repository,
    )
    decision = _eligible_decision_payload(str(uuid4()))
    first = service.request(decision, camera_id="cam-1", zone_id="zone-a", now=datetime.now(UTC))
    second = service.request(decision, camera_id="cam-1", zone_id="zone-a", now=datetime.now(UTC))
    assert first.allowed is False
    assert first.outcome == "suppressed"
    assert second.reason_code.value == "hourly_cap"
    assert controller.calls == 1


def test_expired_audio_lease_remains_a_safety_fence() -> None:
    repository = SQLiteAuditRepository()
    now = datetime.now(UTC)
    first = repository.reserve_audio_receipt(
        {
            "receipt_id": "audio:in-flight",
            "decision_id": "in-flight",
            "camera_id": "cam-1",
            "zone_id": "zone-a",
            "created_at": (now - timedelta(seconds=10)).isoformat(),
        },
        hourly_audio_cap=1,
        daily_audio_cap=1,
        reservation_ttl_seconds=1,
    )
    assert first["playback"]["status"] == "pending"
    second = repository.reserve_audio_receipt(
        {
            "receipt_id": "audio:contender",
            "decision_id": "contender",
            "camera_id": "cam-2",
            "zone_id": "zone-a",
            "created_at": now.isoformat(),
        },
        hourly_audio_cap=1,
        daily_audio_cap=1,
        reservation_ttl_seconds=1,
    )
    assert second["reservation_status"] == "rejected"
    assert repository.get_audio_receipt("audio:in-flight")["playback"]["status"] == "pending"


def test_decision_metric_maps_arbitrary_reason_to_other() -> None:
    metrics = OperationalMetrics()
    for index in range(150):
        metrics.decision("verified", f"caller-reason-{index}")
    output = metrics.render()
    decision_lines = [
        line for line in output.splitlines() if line.startswith("smoke_decisions_total{")
    ]
    assert len(decision_lines) == 1
    assert 'reason="other"' in output
    assert "caller-reason-" not in output


class _Response:
    def __init__(self, body: bytes) -> None:
        self.body = body

    def __enter__(self) -> _Response:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def read(self) -> bytes:
        return self.body


def test_http_audio_rejects_receipt_for_a_different_command(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "tp_smoke_detect.adapters.audio.http.urlopen",
        lambda *args, **kwargs: _Response(
            b'{"command_id":"wrong","decision_id":"wrong","status":"accepted"}'
        ),
    )
    adapter = HttpAudioController("http://127.0.0.1/audio")
    command = AudioCommand(
        event_id=uuid4(),
        correlation_id=uuid4(),
        producer="test",
        occurred_at=datetime.now(UTC),
        command_id=uuid4(),
        decision_id=uuid4(),
        zone_id="zone-a",
        message_id="smoke-reminder-neutral-01",
        volume_profile="default",
        expires_at=datetime.now(UTC) + timedelta(seconds=30),
        policy_revision="p1",
    )
    receipt = adapter.send(command)
    assert receipt.status is PlaybackStatus.FAILED
    assert receipt.detail_code == "adapter_error"


def test_http_audio_rejects_accepted_receipt_after_command_expiry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    command = AudioCommand(
        event_id=uuid4(),
        correlation_id=uuid4(),
        producer="test",
        occurred_at=datetime.now(UTC),
        command_id=uuid4(),
        decision_id=uuid4(),
        zone_id="zone-a",
        message_id="smoke-reminder-neutral-01",
        volume_profile="default",
        expires_at=datetime.now(UTC) + timedelta(seconds=30),
        policy_revision="p1",
    )
    late_at = (command.expires_at + timedelta(seconds=1)).isoformat()
    late_body = (
        f'{{"command_id":"{command.command_id}","decision_id":"{command.decision_id}",'
        f'"status":"accepted","accepted_at":"{late_at}"}}'
    ).encode()
    monkeypatch.setattr(
        "tp_smoke_detect.adapters.audio.http.urlopen",
        lambda *args, **kwargs: _Response(late_body),
    )
    receipt = HttpAudioController("http://127.0.0.1/audio").send(command)
    assert receipt.status is PlaybackStatus.EXPIRED
    assert receipt.detail_code == "late_receipt"


def test_http_audio_rejects_receipt_exactly_at_command_expiry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    command = AudioCommand(
        event_id=uuid4(),
        correlation_id=uuid4(),
        producer="test",
        occurred_at=datetime.now(UTC),
        command_id=uuid4(),
        decision_id=uuid4(),
        zone_id="zone-a",
        message_id="smoke-reminder-neutral-01",
        volume_profile="default",
        expires_at=datetime.now(UTC) + timedelta(seconds=30),
        policy_revision="p1",
    )
    exact_body = (
        f'{{"command_id":"{command.command_id}","decision_id":"{command.decision_id}",'
        f'"status":"accepted","accepted_at":"{command.expires_at.isoformat()}"}}'
    ).encode()
    monkeypatch.setattr(
        "tp_smoke_detect.adapters.audio.http.urlopen",
        lambda *args, **kwargs: _Response(exact_body),
    )
    receipt = HttpAudioController("http://127.0.0.1/audio").send(command)
    assert receipt.status is PlaybackStatus.EXPIRED
    assert receipt.detail_code == "late_receipt"


def test_http_audio_rejects_receipt_with_naive_timestamp(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    command = AudioCommand(
        event_id=uuid4(),
        correlation_id=uuid4(),
        producer="test",
        occurred_at=datetime.now(UTC),
        command_id=uuid4(),
        decision_id=uuid4(),
        zone_id="zone-a",
        message_id="smoke-reminder-neutral-01",
        volume_profile="default",
        expires_at=datetime.now(UTC) + timedelta(seconds=30),
        policy_revision="p1",
    )
    naive_body = (
        f'{{"command_id":"{command.command_id}","decision_id":"{command.decision_id}",'
        '"status":"accepted","accepted_at":"2026-08-24T00:00:00"}'
    ).encode()
    monkeypatch.setattr(
        "tp_smoke_detect.adapters.audio.http.urlopen",
        lambda *args, **kwargs: _Response(naive_body),
    )
    receipt = HttpAudioController("http://127.0.0.1/audio").send(command)
    assert receipt.status is PlaybackStatus.FAILED
    assert receipt.detail_code == "invalid_receipt_time"
