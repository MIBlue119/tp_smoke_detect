from __future__ import annotations

import importlib.util
import io
import sqlite3
import subprocess
import sys
import tarfile
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import ModuleType
from typing import cast
from uuid import uuid4

import pytest

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


def _qualification_module() -> ModuleType:
    path = Path(__file__).parents[2] / "scripts" / "qualify_cpu.py"
    spec = importlib.util.spec_from_file_location("qualify_cpu", path)
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


def test_restore_first_publish_rename_failure_preserves_live_destination(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A fault before the live rename must not make rollback delete live data."""

    backup = _backup_module()
    source = tmp_path / "source"
    source.mkdir()
    (source / "config.json").write_text('{"safe":true}', encoding="utf-8")
    archive_path = tmp_path / "backup.tar.gz"
    backup.create_backup(source, archive_path)
    destination = tmp_path / "live"
    destination.mkdir()
    (destination / "keep.txt").write_text("untouched", encoding="utf-8")
    original_replace = backup.os.replace

    def fail_live_rename(old: str | Path, new: str | Path) -> None:
        if Path(old) == destination:
            raise OSError("injected first rename failure")
        original_replace(old, new)

    monkeypatch.setattr(backup.os, "replace", fail_live_rename)
    with pytest.raises(OSError, match="first rename"):
        backup.restore_backup(archive_path, destination)
    assert (destination / "keep.txt").read_text(encoding="utf-8") == "untouched"
    assert list(tmp_path.glob(".live.previous-*")) == []


def test_runtime_backup_is_a_consistent_sqlite_snapshot_with_mounted_configs(
    tmp_path: Path,
) -> None:
    backup = _backup_module()
    database = tmp_path / "state" / "audit.sqlite3"
    database.parent.mkdir()
    connection = sqlite3.connect(database)
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("CREATE TABLE audit (id INTEGER PRIMARY KEY, value TEXT)")
    connection.execute("INSERT INTO audit(value) VALUES ('before-backup')")
    connection.commit()
    policy = tmp_path / "policy.yaml"
    cameras = tmp_path / "cameras.yaml"
    policy.write_text("policy:\n  mode: shadow\n", encoding="utf-8")
    cameras.write_text("cameras: []\n", encoding="utf-8")
    archive = tmp_path / "runtime.tar.gz"
    backup.create_runtime_backup(database, policy, cameras, archive)
    connection.execute("INSERT INTO audit(value) VALUES ('after-backup')")
    connection.commit()
    connection.close()

    restored = tmp_path / "restored"
    backup.restore_backup(archive, restored)
    restored_db = sqlite3.connect(restored / "state" / "audit.sqlite3")
    assert restored_db.execute("PRAGMA integrity_check").fetchone() == ("ok",)
    assert restored_db.execute("SELECT value FROM audit").fetchall() == [("before-backup",)]
    restored_db.close()
    assert (restored / "config" / "policy.yaml").read_text(encoding="utf-8").startswith("policy:")
    assert (restored / "config" / "cameras.yaml").read_text(encoding="utf-8") == "cameras: []\n"


def test_backup_deployment_streams_archive_to_atomic_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    backup = _backup_module()
    output = tmp_path / "deployment.tar.gz"
    compose_file = tmp_path / "compose.yaml"
    compose_file.write_text("services: {}\n", encoding="utf-8")
    payload = b"\x1f\x8b" + (b"streamed archive" * 100_000)

    def fake_run(*_args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        stdout = kwargs["stdout"]
        assert hasattr(stdout, "write")
        stdout.write(payload)
        return subprocess.CompletedProcess([], 0, "", "")

    monkeypatch.setattr(backup.subprocess, "run", fake_run)
    result = backup.backup_deployment(compose_file, "smoke-detect", output)

    assert result["size"] == len(payload)
    assert output.read_bytes() == payload


def test_runtime_restore_replaces_database_atomically_after_integrity_check(
    tmp_path: Path,
) -> None:
    backup = _backup_module()
    source = tmp_path / "source.sqlite3"
    connection = sqlite3.connect(source)
    connection.execute("CREATE TABLE audit (value TEXT)")
    connection.execute("INSERT INTO audit VALUES ('restored')")
    connection.commit()
    connection.close()
    policy = tmp_path / "policy.yaml"
    cameras = tmp_path / "cameras.yaml"
    policy.write_text("policy: {}\n", encoding="utf-8")
    cameras.write_text("cameras: []\n", encoding="utf-8")
    archive = tmp_path / "runtime.tar.gz"
    backup.create_runtime_backup(source, policy, cameras, archive)
    live = tmp_path / "volume" / "audit.sqlite3"
    live.parent.mkdir()
    live.write_bytes(b"not a database")

    result = backup.restore_runtime(archive, live)
    assert result["integrity_check"] == "ok"
    restored = sqlite3.connect(live)
    assert restored.execute("SELECT value FROM audit").fetchone() == ("restored",)
    restored.close()
    assert not list(live.parent.glob(".smoke-runtime-restore-*"))


def test_cpu_qualification_check_is_read_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    qualification = _qualification_module()
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        qualification.subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args[0], 0, "1 passed", ""),
    )
    monkeypatch.setattr(sys, "argv", ["qualify_cpu.py", "--check"])
    assert qualification.main() == 0
    assert not (tmp_path / "docs").exists()
