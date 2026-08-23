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
import tarfile
import tempfile
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath

MEDIA_NAMES = frozenset({"artifacts", "media", "clips", "raw_media", "event_clip"})
MANIFEST_NAME = "backup-manifest.json"


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
            old_destination = destination.parent / f".{destination.name}.previous-{os.getpid()}"
            if old_destination.exists():
                shutil.rmtree(old_destination)
            os.replace(destination, old_destination)
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
    manifest = restore_backup(args.archive, args.destination)
    print(json.dumps({"destination": str(args.destination), **manifest}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
