import asyncio
import json
from concurrent.futures import ThreadPoolExecutor
from typing import Any
from uuid import uuid4

from tp_smoke_detect.adapters.persistence.sqlite import SQLiteAuditRepository
from tp_smoke_detect.api.app import create_app
from tp_smoke_detect.contracts import RunMode
from tp_smoke_detect.settings import AppSettings, PolicySettings


def _asgi_json(
    app: Any,
    path: str,
    payload: dict[str, object],
    extra_headers: list[tuple[bytes, bytes]] | None = None,
) -> tuple[int, dict[str, Any]]:
    """Issue a real ASGI request without adding an HTTP client dependency."""

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
        "audio_outcome": "would_announce",
    }


def test_decisions_and_reviews_are_append_only_through_asgi() -> None:
    repository = SQLiteAuditRepository()
    decision_id = str(uuid4())
    repository.put_decision(_decision(decision_id))
    app = create_app(
        repository,
        settings=AppSettings(
            policy=PolicySettings(audio_muted=True),
            cameras=[],
        ),
    )
    status, response = _asgi_json(
        app,
        f"/v1/events/{decision_id}/reviews",
        {"label": "false_positive", "actor": "op"},
    )
    assert status == 201
    assert response["decision_id"] == decision_id
    status, response = _asgi_json(
        app,
        f"/v1/events/{decision_id}/reviews",
        {"label": "true_positive", "actor": "lead"},
    )
    assert status == 201
    assert response["decision_id"] == decision_id
    saved = repository.get_decision(decision_id)
    assert saved is not None
    assert saved["audio_outcome"] == "would_announce"
    assert len(repository.connection.execute("SELECT * FROM reviews").fetchall()) == 2


def test_audio_request_api_uses_persisted_camera_zone_and_policy_eligibility() -> None:
    repository = SQLiteAuditRepository()
    decision_id = str(uuid4())
    repository.put_decision(_decision(decision_id))
    repository.upsert_camera(
        {"camera_id": "cam-1", "zone_id": "persisted-zone", "revision": "r1", "active": True}
    )
    app = create_app(
        repository,
        settings=AppSettings(
            policy=PolicySettings(audio_muted=True),
            cameras=[],
        ),
    )
    status, response = _asgi_json(app, "/v1/audio/requests", {"decision_id": decision_id})
    assert status == 201
    assert response["reason_code"] == "audio_muted"


def test_audio_request_api_allows_persisted_eligible_decision() -> None:
    repository = SQLiteAuditRepository()
    decision_id = str(uuid4())
    decision = _decision(decision_id)
    decision.update({"mode": "automatic", "audio_eligibility": True})
    repository.put_decision(decision)
    repository.upsert_camera(
        {"camera_id": "cam-1", "zone_id": "persisted-zone", "revision": "r1", "active": True}
    )
    app = create_app(
        repository,
        settings=AppSettings(
            policy=PolicySettings(mode=RunMode.AUTOMATIC, audio_muted=False, cooldown_seconds=600),
            cameras=[],
        ),
    )

    status, response = _asgi_json(app, "/v1/audio/requests", {"decision_id": decision_id})

    assert status == 201
    assert response["reason_code"] == "announced"
    assert len(app.state.audio_controller.commands) == 1


def test_concurrent_audio_requests_respect_zone_cooldown() -> None:
    repository = SQLiteAuditRepository()
    decision_ids = [str(uuid4()), str(uuid4())]
    for decision_id in decision_ids:
        decision = _decision(decision_id)
        decision.update({"mode": "automatic", "audio_eligibility": True})
        repository.put_decision(decision)
    repository.upsert_camera(
        {"camera_id": "cam-1", "zone_id": "shared-zone", "revision": "r1", "active": True}
    )
    app = create_app(
        repository,
        settings=AppSettings(
            policy=PolicySettings(mode=RunMode.AUTOMATIC, audio_muted=False, cooldown_seconds=600),
            cameras=[],
        ),
    )

    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(
            pool.map(
                lambda decision_id: _asgi_json(
                    app, "/v1/audio/requests", {"decision_id": decision_id}
                ),
                decision_ids,
            )
        )

    assert sorted(response[1]["reason_code"] for response in responses) == [
        "announced",
        "zone_cooldown",
    ]
    assert len(app.state.audio_controller.commands) == 1


def test_concurrent_evaluation_retries_claim_before_decision_side_effect() -> None:
    repository = SQLiteAuditRepository()
    app = create_app(repository, settings=AppSettings(cameras=[]))
    key = b"same-evaluation"

    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(
            pool.map(
                lambda _: _asgi_json(
                    app,
                    "/v1/evaluations",
                    {"camera_id": "cam-1", "mode": "replay"},
                    [(b"idempotency-key", key)],
                ),
                range(2),
            )
        )

    evaluation_ids = {response[1]["evaluation_id"] for response in responses}
    assert len(evaluation_ids) == 1
    assert repository.connection.execute("SELECT COUNT(*) FROM evaluations").fetchone()[0] == 1
    assert repository.connection.execute("SELECT COUNT(*) FROM decisions").fetchone()[0] == 1


def test_audio_mute_rejects_naive_expiry_before_persisting() -> None:
    repository = SQLiteAuditRepository()
    app = create_app(repository, settings=AppSettings(cameras=[]))

    status, response = _asgi_json(
        app,
        "/v1/audio/mute",
        {
            "scope": "site",
            "expires_at": "2099-01-01T00:00:00",
            "reason": "maintenance",
            "actor": "operator",
        },
    )

    assert status == 422
    assert "timezone" in json.dumps(response)
    assert repository.list_mutes() == []


def test_idempotent_evaluation_lookup_returns_same_record() -> None:
    repository = SQLiteAuditRepository()
    evaluation_id = str(uuid4())
    repository.put_evaluation(
        {
            "evaluation_id": evaluation_id,
            "status": "completed",
            "idempotency_key": "same-request",
            "result": {"ok": True},
        }
    )
    saved = repository.get_evaluation_by_idempotency("same-request")
    assert saved is not None
    assert saved["evaluation_id"] == evaluation_id
