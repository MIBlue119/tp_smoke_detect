import asyncio
import json
import multiprocessing
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier
from typing import Any, cast
from uuid import uuid4

import pytest

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


def _claim_failed_evaluation_process(
    database: str, barrier: Any, results: Any, evaluation_id: str
) -> None:
    repository = SQLiteAuditRepository(database)
    barrier.wait()
    claimed = repository.claim_evaluation(evaluation_id, "failed-cross-process", "cam-1")
    results.put(claimed is None)
    repository.close()


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


def test_failed_evaluation_reclaim_is_one_winner_across_process_connections(
    tmp_path: Path,
) -> None:
    database = tmp_path / "failed-reclaim.sqlite"
    repository = SQLiteAuditRepository(database)
    repository.put_evaluation(
        {
            "evaluation_id": "failed-original",
            "status": "failed",
            "idempotency_key": "failed-cross-process",
            "result": {"error": "temporary"},
        }
    )
    context = multiprocessing.get_context("fork")
    barrier = context.Barrier(2)
    results = context.Queue()
    workers = [
        context.Process(
            target=_claim_failed_evaluation_process,
            args=(str(database), barrier, results, f"replacement-{index}"),
        )
        for index in range(2)
    ]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(timeout=10)
        assert worker.exitcode == 0
    assert sorted(results.get() for _ in workers) == [False, True]
    assert (
        repository.connection.execute(
            "SELECT COUNT(*) FROM evaluations WHERE idempotency_key=?",
            ("failed-cross-process",),
        ).fetchone()[0]
        == 1
    )


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


def test_separate_apps_sharing_sqlite_reserve_audio_once(tmp_path: Path) -> None:
    database = tmp_path / "shared.sqlite"
    first = SQLiteAuditRepository(database)
    second = SQLiteAuditRepository(database)
    decisions = [str(uuid4()), str(uuid4())]
    for decision_id in decisions:
        decision = _decision(decision_id)
        decision.update({"mode": "automatic", "audio_eligibility": True})
        first.put_decision(decision)
    first.upsert_camera(
        {"camera_id": "cam-1", "zone_id": "shared-zone", "revision": "r1", "active": True}
    )

    class BarrierRepository(SQLiteAuditRepository):
        def __init__(self, database: Path, barrier: Barrier) -> None:
            super().__init__(database)
            self.barrier = barrier

        def list_audio_receipts(self, **kwargs: Any) -> list[dict[str, Any]]:
            result = super().list_audio_receipts(**kwargs)
            self.barrier.wait(timeout=5)
            return cast(list[dict[str, Any]], result)

    barrier = Barrier(2)
    first.close()
    second.close()
    first = BarrierRepository(database, barrier)
    second = BarrierRepository(database, barrier)
    settings = AppSettings(
        policy=PolicySettings(
            mode=RunMode.AUTOMATIC,
            audio_muted=False,
            cooldown_seconds=600,
            hourly_audio_cap=1,
            daily_audio_cap=1,
        ),
        cameras=[],
    )
    apps = [create_app(first, settings=settings), create_app(second, settings=settings)]

    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(
            pool.map(
                lambda pair: _asgi_json(pair[0], "/v1/audio/requests", {"decision_id": pair[1]}),
                zip(apps, decisions, strict=True),
            )
        )

    assert sorted(response[1]["reason_code"] for response in responses) == [
        "announced",
        "hourly_cap",
    ]
    assert sum(len(app.state.audio_controller.commands) for app in apps) == 1
    rows = first.connection.execute(
        "SELECT playback FROM audio_receipts WHERE zone_id=?", ("shared-zone",)
    ).fetchall()
    assert sum(json.loads(row[0]).get("status") == "accepted" for row in rows if row[0]) == 1


def test_post_claim_failure_is_failed_then_retryable() -> None:
    class FailOnceRepository(SQLiteAuditRepository):
        failed = False

        def put_evaluation(self, evaluation: dict[str, object]) -> dict[str, object]:
            if evaluation.get("status") == "completed" and not self.failed:
                self.failed = True
                raise RuntimeError("injected evaluation write failure")
            return cast(dict[str, object], super().put_evaluation(evaluation))

    repository = FailOnceRepository()
    app = create_app(repository, settings=AppSettings(cameras=[]))
    headers = [(b"idempotency-key", b"retryable-evaluation")]
    with pytest.raises(RuntimeError, match="injected"):
        _asgi_json(
            app,
            "/v1/evaluations",
            {"camera_id": "cam-1", "mode": "replay"},
            headers,
        )
    failed = repository.get_evaluation_by_idempotency("retryable-evaluation")
    assert failed is not None
    assert failed["status"] == "failed"
    assert failed["result"] == {"error": "evaluation_failed", "retryable": True}

    status, response = _asgi_json(
        app,
        "/v1/evaluations",
        {"camera_id": "cam-1", "mode": "replay"},
        headers,
    )
    assert status == 202
    assert response["status"] == "completed"
    assert repository.connection.execute("SELECT COUNT(*) FROM decisions").fetchone()[0] == 1
    metrics = app.state.metrics.render()
    assert (
        'smoke_decisions_total{outcome="rejected",reason="evaluation_pending_provider"} 1'
        in metrics
    )
