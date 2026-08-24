"""Fail-closed staging helpers for the Shibuya demo.

Network access is explicit and all bytes are verified before promotion.  The
module never deserializes a downloaded checkpoint: ``.pt`` files are treated as
untrusted pickle containers and are only statically inspected in this lane.
"""

from __future__ import annotations

import hashlib
import json
import os
import ssl
import subprocess
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from .contracts import SourceReceipt, sha256_json


class AcquisitionBlockedError(ValueError):
    """An acquisition did not meet the explicit demo trust boundary."""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_relative(root: Path, relative: str) -> Path:
    if not relative or relative.startswith(("/", "\\")) or "\\" in relative:
        raise AcquisitionBlockedError("artifact path must be relative POSIX")
    if any(part in {"", ".", ".."} for part in PurePosixPath(relative).parts):
        raise AcquisitionBlockedError("artifact path escapes local artifact root")
    root = root.resolve()
    target = (root / PurePosixPath(relative)).resolve()
    try:
        target.relative_to(root)
    except ValueError as exc:
        raise AcquisitionBlockedError("artifact path escapes local artifact root") from exc
    return target


def _https_origin(url: str) -> tuple[str, str]:
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise AcquisitionBlockedError("network source must be HTTPS without credentials")
    return parsed.hostname, parsed.scheme


def atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(
        json.dumps(payload, sort_keys=True, indent=2, ensure_ascii=True) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def inspect_pickle_checkpoint(path: Path) -> dict[str, Any]:
    """Inspect a checkpoint without importing or unpickling its payload."""

    if path.suffix.lower() not in {".pt", ".pth", ".pkl"}:
        raise AcquisitionBlockedError("checkpoint is not a recognized pickle-bearing extension")
    if not path.is_file() or path.stat().st_size <= 0:
        raise AcquisitionBlockedError("checkpoint is missing or empty")
    is_zip = zipfile.is_zipfile(path)
    members: list[str] = []
    if is_zip:
        with zipfile.ZipFile(path) as archive:
            members = sorted(archive.namelist())[:100]
    return {
        "format": "torch-zip-pickle" if is_zip else "opaque-pickle-bearing",
        "pickle_load": "never in the acquisition process; disposable unprivileged conversion only",
        "static_zip_members": members,
        "unsafe_deserialization_warning": True,
    }


def download_verified(
    url: str,
    destination: Path,
    *,
    expected_sha256: str,
    expected_size: int,
    allow_network: bool,
    timeout_seconds: int = 60,
) -> None:
    """Download one immutable asset and promote only matching bytes."""

    if not allow_network:
        raise AcquisitionBlockedError(
            "network disabled; explicit acquisition acknowledgement required"
        )
    hostname, _ = _https_origin(url)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.name}.part")
    try:
        request = urllib.request.Request(url, headers={"User-Agent": "tp-smoke-detect-demo/1"})
        with urllib.request.urlopen(
            request, timeout=timeout_seconds, context=ssl.create_default_context()
        ) as response:
            final = urllib.parse.urlparse(response.geturl())
            if final.scheme != "https" or final.hostname != hostname:
                raise AcquisitionBlockedError("redirected outside the pinned HTTPS origin")
            total = 0
            with temporary.open("wb") as stream:
                while chunk := response.read(1024 * 1024):
                    total += len(chunk)
                    if total > expected_size:
                        raise AcquisitionBlockedError("download exceeded the expected size")
                    stream.write(chunk)
        if temporary.stat().st_size != expected_size or sha256_file(temporary) != expected_sha256:
            raise AcquisitionBlockedError("downloaded bytes do not match expected size/hash")
        os.replace(temporary, destination)
    except (OSError, urllib.error.URLError, ValueError) as exc:
        temporary.unlink(missing_ok=True)
        if isinstance(exc, AcquisitionBlockedError):
            raise
        raise AcquisitionBlockedError(str(exc)) from exc


def validate_video_file(path: Path, expected: SourceReceipt) -> dict[str, Any]:
    """Validate a local source with ffprobe and bind it to the source receipt."""

    if not path.is_file():
        raise AcquisitionBlockedError("source video is missing")
    if sha256_file(path) != expected.sha256 or path.stat().st_size != expected.byte_size:
        raise AcquisitionBlockedError("source bytes do not match the sealed receipt")
    try:
        completed = subprocess.run(
            ["ffprobe", "-v", "error", "-show_format", "-show_streams", "-of", "json", str(path)],
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
        )
        probe = json.loads(completed.stdout)
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError) as exc:
        raise AcquisitionBlockedError(f"ffprobe validation failed: {exc}") from exc
    streams = probe.get("streams", [])
    video = [item for item in streams if item.get("codec_type") == "video"]
    audio = [item for item in streams if item.get("codec_type") == "audio"]
    if len(video) != 1 or audio:
        raise AcquisitionBlockedError("source must contain exactly one video stream and no audio")
    stream = video[0]
    duration = float(stream.get("duration") or probe.get("format", {}).get("duration") or 0)
    actual = (stream.get("width"), stream.get("height"), stream.get("r_frame_rate"), duration)
    expected_values = (
        expected.width,
        expected.height,
        expected.frame_rate,
        expected.duration_seconds,
    )
    if (
        actual[0] != expected_values[0]
        or actual[1] != expected_values[1]
        or actual[2] != expected_values[2]
    ):
        raise AcquisitionBlockedError("source dimensions, frame rate, or stream shape changed")
    if abs(actual[3] - expected_values[3]) > 1 / 30:
        raise AcquisitionBlockedError("source duration changed beyond one frame")
    return {"sha256": expected.sha256, "byte_size": expected.byte_size, "ffprobe": probe}


@dataclass(frozen=True, slots=True)
class AcquisitionResult:
    status: str
    artifact_id: str
    sha256: str | None
    size_bytes: int | None
    errors: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "artifact_id": self.artifact_id,
            "sha256": self.sha256,
            "size_bytes": self.size_bytes,
            "errors": list(self.errors),
        }


def source_receipt_digest(receipt: SourceReceipt) -> str:
    return sha256_json(receipt.to_dict())


__all__ = [
    "AcquisitionBlockedError",
    "AcquisitionResult",
    "atomic_write_json",
    "download_verified",
    "inspect_pickle_checkpoint",
    "sha256_file",
    "source_receipt_digest",
    "validate_video_file",
]
