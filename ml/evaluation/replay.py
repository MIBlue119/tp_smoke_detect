"""Metadata-only replay workload contracts for GPU qualification.

The manifest describes the expected 20-camera workload without embedding media,
credentials, or local paths.  A real qualification remains blocked until every
external clip has an approved SHA-256 receipt; synthetic rows are useful for
schema and scheduling tests only.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_CODECS = {"h264", "h265"}
_STATUSES = {"healthy", "stalled"}
_SOURCE_KINDS = {"external", "synthetic"}


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


@dataclass(frozen=True, slots=True)
class ReplayStreamSpec:
    stream_id: str
    camera_id: str
    codec: str
    profile: str
    width: int
    height: int
    source_fps: float
    analysis_fps: float
    bitrate_kbps: int
    gop_frames: int
    occupancy: float
    visible_persons: int
    eligible_crop_rate: float
    candidate_burst_rate: float
    confounder_rates: Mapping[str, float] = field(default_factory=dict)
    status: str = "healthy"
    source_kind: str = "external"
    clip_sha256: str | None = None

    def __post_init__(self) -> None:
        if not self.stream_id.strip() or not self.camera_id.strip():
            raise ValueError("stream_id and camera_id are required")
        if self.codec.lower() not in _CODECS:
            raise ValueError("codec must be h264 or h265")
        if not self.profile.strip() or self.width <= 0 or self.height <= 0:
            raise ValueError("profile and positive resolution are required")
        if self.source_fps <= 0 or self.analysis_fps <= 0:
            raise ValueError("FPS values must be positive")
        if self.analysis_fps > self.source_fps:
            raise ValueError("analysis_fps cannot exceed source_fps")
        if self.bitrate_kbps <= 0 or self.gop_frames <= 0 or self.visible_persons < 0:
            raise ValueError("bitrate, GOP, and visible_persons are invalid")
        for name, value in (
            ("occupancy", self.occupancy),
            ("eligible_crop_rate", self.eligible_crop_rate),
            ("candidate_burst_rate", self.candidate_burst_rate),
        ):
            if not 0 <= value <= 1:
                raise ValueError(f"{name} must be in [0, 1]")
        if self.status not in _STATUSES:
            raise ValueError("status must be healthy or stalled")
        if self.source_kind not in _SOURCE_KINDS:
            raise ValueError("source_kind must be external or synthetic")
        if self.source_kind == "external" and (
            self.clip_sha256 is None or not _SHA256.fullmatch(self.clip_sha256)
        ):
            raise ValueError("external streams require a SHA-256 clip receipt")
        if self.clip_sha256 is not None and not _SHA256.fullmatch(self.clip_sha256):
            raise ValueError("clip_sha256 must be a SHA-256 digest")
        if any(
            not key.strip() or not 0 <= value <= 1 for key, value in self.confounder_rates.items()
        ):
            raise ValueError("confounder rates must have non-empty names and values in [0, 1]")

    def to_dict(self) -> dict[str, Any]:
        return {
            "stream_id": self.stream_id,
            "camera_id": self.camera_id,
            "codec": self.codec.lower(),
            "profile": self.profile,
            "resolution": {"width": self.width, "height": self.height},
            "source_fps": float(self.source_fps),
            "analysis_fps": float(self.analysis_fps),
            "bitrate_kbps": self.bitrate_kbps,
            "gop_frames": self.gop_frames,
            "occupancy": self.occupancy,
            "visible_persons": self.visible_persons,
            "eligible_crop_rate": self.eligible_crop_rate,
            "candidate_burst_rate": self.candidate_burst_rate,
            "confounder_rates": dict(sorted(self.confounder_rates.items())),
            "status": self.status,
            "source_kind": self.source_kind,
            "clip_sha256": self.clip_sha256,
        }


@dataclass(frozen=True, slots=True)
class ReplayWorkloadManifest:
    workload_id: str
    version: str
    duration_hours: float
    streams: tuple[ReplayStreamSpec, ...]
    manifest_sha256: str = ""

    def __post_init__(self) -> None:
        if not self.workload_id.strip() or not self.version.strip():
            raise ValueError("workload_id and version are required")
        if self.duration_hours <= 0:
            raise ValueError("duration_hours must be positive")
        if not self.streams:
            raise ValueError("at least one replay stream is required")
        stream_ids = [stream.stream_id for stream in self.streams]
        camera_ids = [stream.camera_id for stream in self.streams]
        if len(set(stream_ids)) != len(stream_ids) or len(set(camera_ids)) != len(camera_ids):
            raise ValueError("stream and camera IDs must be unique")

    def payload(self) -> dict[str, Any]:
        return {
            "schema_version": "gpu.replay-workload.v1",
            "workload_id": self.workload_id,
            "version": self.version,
            "duration_hours": self.duration_hours,
            "stream_count": len(self.streams),
            "streams": [
                stream.to_dict() for stream in sorted(self.streams, key=lambda item: item.stream_id)
            ],
        }

    @property
    def digest(self) -> str:
        return hashlib.sha256(_canonical(self.payload()).encode("utf-8")).hexdigest()

    @property
    def qualification_state(self) -> str:
        if any(stream.source_kind == "external" for stream in self.streams):
            return (
                "ready"
                if all(stream.clip_sha256 for stream in self.streams)
                else "blocked_missing_clip_hash"
            )
        return "metadata_only_synthetic"

    def to_dict(self) -> dict[str, Any]:
        payload = self.payload()
        payload["manifest_sha256"] = self.digest
        payload["qualification_state"] = self.qualification_state
        return payload

    def to_json(self) -> str:
        return _canonical(self.to_dict()) + "\n"

    def write(self, path: str | Path) -> None:
        Path(path).write_text(self.to_json(), encoding="utf-8")


def build_replay_manifest(
    workload_id: str,
    version: str,
    streams: tuple[ReplayStreamSpec, ...],
    *,
    duration_hours: float = 2.0,
) -> ReplayWorkloadManifest:
    """Build a sealed manifest; external media hashes are never fabricated."""

    return ReplayWorkloadManifest(workload_id, version, duration_hours, streams)


def load_replay_manifest(path: str | Path) -> ReplayWorkloadManifest:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or raw.get("schema_version") != "gpu.replay-workload.v1":
        raise ValueError("unsupported replay workload manifest")
    streams: list[ReplayStreamSpec] = []
    for row in raw.get("streams", []):
        if not isinstance(row, dict):
            raise ValueError("replay stream must be an object")
        resolution = row.get("resolution")
        if not isinstance(resolution, dict):
            raise ValueError("stream resolution is required")
        streams.append(
            ReplayStreamSpec(
                stream_id=str(row["stream_id"]),
                camera_id=str(row["camera_id"]),
                codec=str(row["codec"]),
                profile=str(row["profile"]),
                width=int(resolution["width"]),
                height=int(resolution["height"]),
                source_fps=float(row["source_fps"]),
                analysis_fps=float(row["analysis_fps"]),
                bitrate_kbps=int(row["bitrate_kbps"]),
                gop_frames=int(row["gop_frames"]),
                occupancy=float(row["occupancy"]),
                visible_persons=int(row["visible_persons"]),
                eligible_crop_rate=float(row["eligible_crop_rate"]),
                candidate_burst_rate=float(row["candidate_burst_rate"]),
                confounder_rates=dict(row.get("confounder_rates", {})),
                status=str(row.get("status", "healthy")),
                source_kind=str(row.get("source_kind", "external")),
                clip_sha256=row.get("clip_sha256"),
            )
        )
    manifest = build_replay_manifest(
        str(raw["workload_id"]),
        str(raw["version"]),
        tuple(streams),
        duration_hours=float(raw["duration_hours"]),
    )
    expected = raw.get("manifest_sha256")
    if expected != manifest.digest:
        raise ValueError("replay workload manifest checksum mismatch")
    return manifest


__all__ = [
    "ReplayStreamSpec",
    "ReplayWorkloadManifest",
    "build_replay_manifest",
    "load_replay_manifest",
]
