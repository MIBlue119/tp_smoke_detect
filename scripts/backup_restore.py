#!/usr/bin/env python3
"""Create and restore a media-free, path-safe deployment backup.

The CPU profile uses SQLite, while production may use PostgreSQL.  This tool
does not dump a live database itself; the deployment runbook supplies the
database dump in the source directory.  It packages state/configuration/model
references and deliberately excludes raw media and event clips.  Restores are
rejecting and never follow symlinks or archive traversal entries.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tarfile
import tempfile
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath

MEDIA_NAMES = frozenset({"artifacts", "media", "clips", "raw_media", "event_clip"})
MANIFEST_NAME = "backup-manifest.json"
DEFAULT_DATABASE = "/var/lib/smoke-detect/state/audit.sqlite3"
DEFAULT_POLICY_CONFIG = "/etc/smoke-detect/policy.yaml"
DEFAULT_CAMERAS_CONFIG = "/etc/smoke-detect/cameras.yaml"


def _excluded(relative: Path) -> bool:
    return any(part in MEDIA_NAMES for part in relative.parts)


def _iter_files(root: Path) -> Iterable[tuple[Path, Path]]:
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root)
        if _excluded(relative) or path.is_symlink() or not path.is_file():
            continue
        yield path, relative


def create_backup(source: Path, output: Path) -> dict[str, object]:
    source = source.expanduser().resolve(strict=True)
    if not source.is_dir():
        raise ValueError("backup source must be a directory")
    output = output.expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    files: list[dict[str, object]] = []
    with tarfile.open(output, "w:gz") as archive:
        for path, relative in _iter_files(source):
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            archive.add(path, arcname=relative.as_posix(), recursive=False)
            files.append(
                {"path": relative.as_posix(), "sha256": digest, "size": path.stat().st_size}
            )
        manifest: dict[str, object] = {
            "schema_version": "smoke-detect-backup.v1",
            "created_at": datetime.now(UTC).isoformat(),
            "source": source.name,
            "files": files,
            "excluded": "raw media and event clips (metadata remains in the database dump)",
        }
        encoded = json.dumps(manifest, indent=2, sort_keys=True).encode("utf-8")
        info = tarfile.TarInfo(MANIFEST_NAME)
        info.size = len(encoded)
        info.mtime = int(datetime.now(UTC).timestamp())
        import io

        archive.addfile(info, io.BytesIO(encoded))
    return manifest


def _sqlite_snapshot(source: Path, destination: Path) -> None:
    """Copy one consistent SQLite snapshot, including any live WAL pages."""

    source_connection = sqlite3.connect(source)
    destination_connection = sqlite3.connect(destination)
    try:
        source_connection.backup(destination_connection)
        result = destination_connection.execute("PRAGMA integrity_check").fetchone()
        if result != ("ok",):
            raise ValueError(f"SQLite snapshot failed integrity_check: {result!r}")
        destination_connection.commit()
    finally:
        destination_connection.close()
        source_connection.close()


def create_runtime_backup(
    database: Path,
    policy_config: Path,
    cameras_config: Path,
    output: Path,
) -> dict[str, object]:
    """Snapshot deployed SQLite state and mounted configs from inside Compose."""

    database = database.expanduser().resolve(strict=True)
    policy_config = policy_config.expanduser().resolve(strict=True)
    cameras_config = cameras_config.expanduser().resolve(strict=True)
    if not database.is_file() or not policy_config.is_file() or not cameras_config.is_file():
        raise ValueError("runtime backup inputs must be regular files")
    with tempfile.TemporaryDirectory(prefix="smoke-detect-runtime-backup-") as temporary:
        root = Path(temporary)
        state = root / "state"
        config = root / "config"
        state.mkdir()
        config.mkdir()
        _sqlite_snapshot(database, state / "audit.sqlite3")
        shutil.copyfile(policy_config, config / "policy.yaml")
        shutil.copyfile(cameras_config, config / "cameras.yaml")
        return create_backup(root, output)


def _atomic_write(path: Path, content: bytes) -> None:
    path = path.expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        temporary.write_bytes(content)
        with temporary.open("rb") as stream:
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def backup_deployment(
    compose_file: Path,
    service: str,
    output: Path,
    *,
    database: str = DEFAULT_DATABASE,
    policy_config: str = DEFAULT_POLICY_CONFIG,
    cameras_config: str = DEFAULT_CAMERAS_CONFIG,
) -> dict[str, object]:
    """Run the snapshot helper in the pinned runtime, then atomically publish it."""

    command = [
        "docker",
        "compose",
        "-f",
        str(compose_file.expanduser().resolve(strict=True)),
        "exec",
        "-T",
        service,
        "python",
        "/app/scripts/backup_restore.py",
        "backup-runtime",
        "--database",
        database,
        "--policy-config",
        policy_config,
        "--cameras-config",
        cameras_config,
        "--output",
        "-",
    ]
    completed = subprocess.run(command, check=False, capture_output=True)
    if completed.returncode != 0:
        detail = completed.stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError(f"runtime backup failed: {detail or completed.returncode}")
    if not completed.stdout.startswith(b"\x1f\x8b"):
        raise RuntimeError("runtime backup did not return a gzip archive")
    _atomic_write(output, completed.stdout)
    return {"archive": str(output), "size": output.stat().st_size, "service": service}


def restore_runtime(archive: Path, database: Path) -> dict[str, object]:
    """Restore only the SQLite member into a stopped service's named volume."""

    database = database.expanduser().resolve()
    database.parent.mkdir(parents=True, exist_ok=True)
    staging = database.parent / f".smoke-runtime-restore-{os.getpid()}"
    if staging.exists():
        raise ValueError(f"restore staging path already exists: {staging}")
    try:
        restore_backup(archive, staging)
        snapshot = staging / "state" / "audit.sqlite3"
        if not snapshot.is_file():
            raise ValueError("runtime backup does not contain state/audit.sqlite3")
        connection = sqlite3.connect(snapshot)
        try:
            result = connection.execute("PRAGMA integrity_check").fetchone()
        finally:
            connection.close()
        if result != ("ok",):
            raise ValueError(f"restored SQLite database failed integrity_check: {result!r}")
        os.replace(snapshot, database)
        return {
            "database": str(database),
            "mounted_configs": "verified_in_archive; apply from host restore staging",
            "integrity_check": "ok",
        }
    finally:
        if staging.exists():
            shutil.rmtree(staging)


def _safe_member(member: tarfile.TarInfo) -> None:
    name = PurePosixPath(member.name)
    if name.is_absolute() or ".." in name.parts or not member.isfile():
        raise ValueError(f"unsafe backup member: {member.name}")
    if _excluded(Path(*name.parts)):
        raise ValueError(f"media member is forbidden in a deployment restore: {member.name}")


def restore_backup(archive_path: Path, destination: Path) -> dict[str, object]:
    destination = destination.expanduser().resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{destination.name}.restore-", dir=destination.parent))
    old_destination: Path | None = None
    try:
        with tarfile.open(archive_path, "r:gz") as archive:
            members = archive.getmembers()
            for member in members:
                _safe_member(member)
            names = [member.name for member in members]
            if len(names) != len(set(names)):
                raise ValueError("backup contains duplicate members")
            manifest_member = next((item for item in members if item.name == MANIFEST_NAME), None)
            if manifest_member is None:
                raise ValueError("backup manifest is missing")
            manifest_raw = archive.extractfile(manifest_member)
            if manifest_raw is None:
                raise ValueError("backup manifest cannot be read")
            manifest = json.loads(manifest_raw.read())
            if (
                not isinstance(manifest, dict)
                or manifest.get("schema_version") != "smoke-detect-backup.v1"
            ):
                raise ValueError("unsupported backup manifest")
            entries = manifest.get("files")
            if not isinstance(entries, list):
                raise ValueError("backup manifest files must be a list")
            expected: dict[str, tuple[int, str]] = {}
            for entry in entries:
                if not isinstance(entry, dict):
                    raise ValueError("backup manifest contains an invalid file entry")
                name = entry.get("path")
                digest = entry.get("sha256")
                size = entry.get("size")
                if (
                    not isinstance(name, str)
                    or name == MANIFEST_NAME
                    or not isinstance(digest, str)
                    or len(digest) != 64
                    or not isinstance(size, int)
                    or size < 0
                ):
                    raise ValueError("backup manifest contains invalid inventory metadata")
                if name in expected:
                    raise ValueError(f"backup manifest contains duplicate file: {name}")
                _safe_member(tarfile.TarInfo(name))
                expected[name] = (size, digest)
            actual_members = {member.name for member in members if member.name != MANIFEST_NAME}
            if actual_members != set(expected):
                missing = sorted(set(expected) - actual_members)
                unexpected = sorted(actual_members - set(expected))
                raise ValueError(
                    f"backup inventory mismatch: missing={missing}, unexpected={unexpected}"
                )
            # Extract and verify everything in an isolated directory.  The live
            # destination is untouched until every byte has passed the manifest.
            for member in members:
                if member.name == MANIFEST_NAME:
                    continue
                target = (staging / member.name).resolve()
                target.relative_to(staging)
                target.parent.mkdir(parents=True, exist_ok=True)
                stream = archive.extractfile(member)
                if stream is None:
                    raise ValueError(f"cannot read backup member: {member.name}")
                content = stream.read()
                size, digest = expected[member.name]
                actual_digest = hashlib.sha256(content).hexdigest()
                if len(content) != size or actual_digest != digest:
                    raise ValueError(f"backup integrity check failed: {member.name}")
                target.write_bytes(content)
                os.chmod(target, member.mode & 0o777)
        if destination.exists():
            previous_destination = (
                destination.parent / f".{destination.name}.previous-{os.getpid()}"
            )
            if previous_destination.exists():
                shutil.rmtree(previous_destination)
            # Assign rollback ownership only after this rename succeeds.  If
            # it faults, the live destination is still completely untouched.
            os.replace(destination, previous_destination)
            old_destination = previous_destination
        os.replace(staging, destination)
        staging = Path()
        if old_destination is not None:
            shutil.rmtree(old_destination)
    except Exception:
        if destination.exists() and old_destination is not None:
            shutil.rmtree(destination)
        if old_destination is not None and old_destination.exists():
            os.replace(old_destination, destination)
        raise
    finally:
        if staging != Path() and staging.exists():
            shutil.rmtree(staging)
    return manifest


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    backup = subparsers.add_parser("backup", help="create a media-free backup archive")
    backup.add_argument("--source", type=Path, required=True)
    backup.add_argument("--output", type=Path, required=True)
    runtime = subparsers.add_parser(
        "backup-runtime", help="snapshot deployed SQLite and mounted configs (container use)"
    )
    runtime.add_argument("--database", type=Path, default=Path(DEFAULT_DATABASE))
    runtime.add_argument("--policy-config", type=Path, default=Path(DEFAULT_POLICY_CONFIG))
    runtime.add_argument("--cameras-config", type=Path, default=Path(DEFAULT_CAMERAS_CONFIG))
    runtime.add_argument("--output", type=Path, required=True)
    deployment = subparsers.add_parser(
        "backup-deployment", help="create a deployment backup from the host"
    )
    deployment.add_argument("--compose-file", type=Path, required=True)
    deployment.add_argument("--service", default="smoke-detect")
    deployment.add_argument("--database", default=DEFAULT_DATABASE)
    deployment.add_argument("--policy-config", default=DEFAULT_POLICY_CONFIG)
    deployment.add_argument("--cameras-config", default=DEFAULT_CAMERAS_CONFIG)
    deployment.add_argument("--output", type=Path, required=True)
    runtime_restore = subparsers.add_parser(
        "restore-runtime", help="restore SQLite into a stopped runtime volume"
    )
    runtime_restore.add_argument("--archive", type=Path, required=True)
    runtime_restore.add_argument("--database", type=Path, default=Path(DEFAULT_DATABASE))
    restore = subparsers.add_parser("restore", help="restore a media-free backup archive")
    restore.add_argument("--archive", type=Path, required=True)
    restore.add_argument("--destination", type=Path, required=True)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if args.command == "backup":
        manifest = create_backup(args.source, args.output)
        print(json.dumps({"archive": str(args.output), **manifest}, sort_keys=True))
        return 0
    if args.command == "backup-runtime":
        with tempfile.TemporaryDirectory(prefix="smoke-detect-runtime-output-") as temporary:
            temporary_output = Path(temporary) / "backup.tar.gz"
            manifest = create_runtime_backup(
                args.database, args.policy_config, args.cameras_config, temporary_output
            )
            payload = temporary_output.read_bytes()
            if str(args.output) == "-":
                sys.stdout.buffer.write(payload)
            else:
                _atomic_write(args.output, payload)
                print(json.dumps({"archive": str(args.output), **manifest}, sort_keys=True))
        return 0
    if args.command == "backup-deployment":
        result = backup_deployment(
            args.compose_file,
            args.service,
            args.output,
            database=args.database,
            policy_config=args.policy_config,
            cameras_config=args.cameras_config,
        )
        print(json.dumps(result, sort_keys=True))
        return 0
    if args.command == "restore-runtime":
        archive = args.archive
        temporary: Path | None = None
        if str(archive) == "-":
            descriptor, temporary_name = tempfile.mkstemp(
                prefix="smoke-detect-restore-", suffix=".tar.gz"
            )
            os.close(descriptor)
            temporary = Path(temporary_name)
            temporary.write_bytes(sys.stdin.buffer.read())
            archive = temporary
        try:
            print(json.dumps(restore_runtime(archive, args.database), sort_keys=True))
        finally:
            if temporary is not None and temporary.exists():
                temporary.unlink()
        return 0
    manifest = restore_backup(args.archive, args.destination)
    print(json.dumps({"destination": str(args.destination), **manifest}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
