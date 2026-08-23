from __future__ import annotations

import importlib.util
import io
import tarfile
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import ModuleType
from typing import cast
from uuid import uuid4

from tp_smoke_detect.adapters.persistence.sqlite import SQLiteAuditRepository


def _decision(decision_id: str, *, created_at: str | None = None) -> dict[str, object]:
    return {
        "decision_id": decision_id,
        "camera_id": "camera-1",
        "track_id": "track-1",
        "outcome": "verified",
        "reason_codes": ["verified"],
        "evidence_channels": [],
        "model_revisions": {},
        "policy_revision": "p1",
        "latency_ms": 1,
        "mode": "replay",
        "audio_eligibility": True,
        "created_at": created_at or datetime.now(UTC).isoformat(),
    }


def test_single_connection_is_safe_for_concurrent_writes() -> None:
    repository = SQLiteAuditRepository()

    def write(index: int) -> None:
        repository.put_decision(_decision(f"decision-{index}"))

    with ThreadPoolExecutor(max_workers=20) as pool:
        list(pool.map(write, range(1_000)))
    assert len(repository.list_decisions(limit=2_000)) == 1_000


def test_idempotency_key_is_claimed_atomically_under_retry_storm() -> None:
    repository = SQLiteAuditRepository()

    def write(index: int) -> dict[str, object]:
        return cast(
            dict[str, object],
            repository.put_evaluation(
                {
                    "evaluation_id": str(uuid4()),
                    "status": "pending",
                    "idempotency_key": "same-request",
                    "result": {"attempt": index},
                }
            ),
        )

    with ThreadPoolExecutor(max_workers=20) as pool:
        results = list(pool.map(write, range(100)))
    assert len({str(item["evaluation_id"]) for item in results}) == 1
    assert repository.connection.execute("SELECT COUNT(*) FROM evaluations").fetchone()[0] == 1


def test_metadata_expiry_removes_all_event_copies_in_one_transaction() -> None:
    repository = SQLiteAuditRepository()
    old = (datetime.now(UTC) - timedelta(days=2)).isoformat()
    decision_id = "expired-decision"
    repository.put_decision(_decision(decision_id, created_at=old))
    repository.append_review(
        {
            "review_id": "review-1",
            "decision_id": decision_id,
            "label": "x",
            "actor": "test",
            "created_at": old,
        }
    )
    repository.put_evaluation(
        {
            "evaluation_id": "evaluation-1",
            "status": "complete",
            "result": {"decision": {"decision_id": decision_id}},
            "created_at": old,
        }
    )
    repository.put_audio_receipt(
        {
            "receipt_id": "audio:expired-decision",
            "decision_id": decision_id,
            "camera_id": "camera-1",
            "zone_id": "zone-1",
            "outcome": "announced",
            "reason_code": "announced",
            "created_at": old,
        }
    )
    assert (
        repository.delete_expired_metadata(
            cutoff=datetime.now(UTC) - timedelta(days=1), now=datetime.now(UTC)
        )
        == 1
    )
    for table in (
        "decisions",
        "reviews",
        "evaluations",
        "audio_receipts",
        "audio_receipt_attempts",
    ):
        assert repository.connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0


def _backup_module() -> ModuleType:
    path = Path(__file__).parents[2] / "scripts" / "backup_restore.py"
    spec = importlib.util.spec_from_file_location("backup_restore", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_restore_rejects_tampering_before_publishing_destination(tmp_path: Path) -> None:
    backup = _backup_module()
    source = tmp_path / "source"
    source.mkdir()
    (source / "config.json").write_text('{"safe":true}')
    archive_path = tmp_path / "backup.tar.gz"
    backup.create_backup(source, archive_path)

    tampered_path = tmp_path / "tampered.tar.gz"
    with (
        tarfile.open(archive_path, "r:gz") as reader,
        tarfile.open(tampered_path, "w:gz") as writer,
    ):
        for member in reader:
            stream = reader.extractfile(member) if member.isfile() else None
            payload = stream.read() if stream is not None else b""
            if member.name == "config.json":
                payload = b'{"safe":false}'
            member.size = len(payload)
            writer.addfile(member, io.BytesIO(payload))

    destination = tmp_path / "live"
    destination.mkdir()
    (destination / "keep.txt").write_text("untouched")
    try:
        backup.restore_backup(tampered_path, destination)
    except ValueError as exc:
        assert "integrity" in str(exc)
    else:
        raise AssertionError("tampered backup was accepted")
    assert (destination / "keep.txt").read_text() == "untouched"
    assert not (destination / "config.json").exists()
    assert list(tmp_path.glob(".live.restore-*")) == []


def test_restore_cleans_staging_when_archive_is_missing(tmp_path: Path) -> None:
    backup = _backup_module()
    destination = tmp_path / "live"
    destination.mkdir()

    try:
        backup.restore_backup(tmp_path / "missing.tar.gz", destination)
    except (FileNotFoundError, tarfile.ReadError):
        pass
    else:
        raise AssertionError("missing backup was accepted")

    assert list(tmp_path.glob(".live.restore-*")) == []
