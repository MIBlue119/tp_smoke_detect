from uuid import uuid4

from tp_smoke_detect.adapters.persistence.sqlite import SQLiteAuditRepository


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


def test_decisions_and_reviews_are_append_only() -> None:
    repository = SQLiteAuditRepository()
    decision_id = str(uuid4())
    repository.put_decision(_decision(decision_id))
    first = repository.append_review(
        {
            "review_id": str(uuid4()),
            "decision_id": decision_id,
            "label": "false_positive",
            "actor": "op",
        }
    )
    second = repository.append_review(
        {
            "review_id": str(uuid4()),
            "decision_id": decision_id,
            "label": "true_positive",
            "actor": "lead",
        }
    )
    assert first["review_id"] != second["review_id"]
    saved = repository.get_decision(decision_id)
    assert saved is not None
    assert saved["audio_outcome"] == "would_announce"
    assert len(repository.connection.execute("SELECT * FROM reviews").fetchall()) == 2


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
