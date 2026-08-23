from datetime import UTC, datetime, timedelta
from pathlib import Path

from tp_smoke_detect.adapters.persistence.sqlite import SQLiteAuditRepository
from tp_smoke_detect.application.retention import (
    FileArtifactDeleter,
    RetentionManager,
    RetentionPolicy,
)


def test_retention_has_separate_clocks_and_keeps_metadata(tmp_path: Path) -> None:
    repository = SQLiteAuditRepository()
    now = datetime(2026, 8, 24, tzinfo=UTC)
    old = (now - timedelta(days=10)).isoformat()
    clip = tmp_path / "clip.mp4"
    clip.write_bytes(b"clip")
    repository.put_artifact(
        {
            "artifact_id": "clip-1",
            "path": "clip.mp4",
            "media_type": "video/mp4",
            "media_class": "event_clip",
            "size_bytes": 4,
            "created_at": old,
        }
    )
    decision_id = "decision-retained"
    repository.put_decision(
        {
            "decision_id": decision_id,
            "camera_id": "camera-1",
            "track_id": "track-1",
            "outcome": "rejected",
            "reason_codes": ["quality"],
            "evidence_channels": [],
            "model_revisions": {},
            "policy_revision": "p1",
            "latency_ms": 1,
            "mode": "replay",
            "audio_eligibility": False,
            "created_at": old,
        }
    )
    result = RetentionManager(
        repository,
        FileArtifactDeleter(tmp_path),
        RetentionPolicy(raw_media_seconds=1, event_clip_seconds=2, metadata_seconds=1_000_000),
    ).run(now)
    assert result.deleted == 1
    assert not clip.exists()
    assert repository.get_decision(decision_id) is not None
    audits = repository.list_retention_audits("clip-1")
    assert len(audits) == 1
    assert audits[0]["status"] == "deleted"


def test_retention_retry_after_file_was_partially_deleted_is_idempotent(tmp_path: Path) -> None:
    repository = SQLiteAuditRepository()
    now = datetime(2026, 8, 24, tzinfo=UTC)
    repository.put_artifact(
        {
            "artifact_id": "missing-1",
            "path": "already-gone.mp4",
            "media_type": "video/mp4",
            "media_class": "event_clip",
            "size_bytes": 0,
            "created_at": (now - timedelta(days=2)).isoformat(),
        }
    )
    manager = RetentionManager(
        repository,
        FileArtifactDeleter(tmp_path),
        RetentionPolicy(event_clip_seconds=1),
    )
    first = manager.run(now)
    second = manager.run(now)
    assert first.deleted == 1
    assert second.scanned == 0
    assert len(repository.list_retention_audits("missing-1")) == 1
