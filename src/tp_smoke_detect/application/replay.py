"""Replay-first media worker used by the CPU reference path.

The worker intentionally operates on a manifest of image artifacts.  It does
not decode video itself (the production DeepStream worker owns that boundary),
but it exercises the same candidate contract, ROI/quality gates, deterministic
sampling clock, and metadata-only message handoff.
"""

from __future__ import annotations

import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, Protocol, cast
from uuid import NAMESPACE_URL, uuid5

from ..adapters.artifacts.local import (
    ArtifactError,
    LocalArtifactStore,
    UnsupportedArtifactError,
)
from ..contracts import (
    BoundingBox,
    CandidateEnvelope,
    Geometry,
    Observations,
    Quality,
    Stage,
)
from ..settings import CameraProfile


class CandidatePublisher(Protocol):
    def publish(self, candidate: CandidateEnvelope, *, topic: str = "track.candidate.v1") -> Any:
        """Publish one metadata-only candidate."""


class CorruptMediaError(ValueError):
    """A supported image artifact has a missing or truncated header."""


@dataclass(frozen=True, slots=True)
class ReplayError:
    """A recoverable error attached to one frame or manifest item."""

    frame_id: str
    code: str
    message: str


@dataclass(frozen=True, slots=True)
class ReplayFrame:
    """Normalized frame descriptor from a replay manifest."""

    frame_id: str
    artifact_id: str
    pts_ns: int
    track_id: str
    person_box: Mapping[str, Any]
    source_width: int
    source_height: int
    face_pixels: float | None = None
    crop_pixels: float | None = None
    illumination_profile: str = "unknown"
    observations: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ReplayManifest:
    """A deterministic image-sequence manifest.

    Manifest timestamps are relative to ``capture_start_ts_ns``.  Keeping the
    clock in the manifest, rather than using wall time, makes repeated runs
    comparable and suitable for contract tests.
    """

    camera_id: str
    camera_config_revision: str
    frames: tuple[ReplayFrame, ...]
    source_fps: float = 30.0
    capture_start_ts_ns: int = 0

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> ReplayManifest:
        camera_id = _required_text(value, "camera_id")
        revision = str(value.get("camera_config_revision", f"{camera_id}-r1"))
        source_fps = _positive_number(value.get("source_fps", 30.0), "source_fps")
        start_ns = _non_negative_int(value.get("capture_start_ts_ns", 0), "capture_start_ts_ns")
        raw_frames = value.get("frames")
        if not isinstance(raw_frames, Sequence) or isinstance(raw_frames, (str, bytes, bytearray)):
            raise ValueError("manifest frames must be a list")
        frames: list[ReplayFrame] = []
        for index, raw in enumerate(raw_frames):
            if not isinstance(raw, Mapping):
                raise ValueError(f"frame {index} must be an object")
            frame_id = str(raw.get("frame_id", f"frame-{index:06d}"))
            artifact_id = raw.get("artifact_id", raw.get("path"))
            if not isinstance(artifact_id, str) or not artifact_id:
                raise ValueError(f"frame {frame_id} requires artifact_id")
            pts = raw.get("pts_ns")
            if pts is None:
                pts = int(index * 1_000_000_000 / source_fps)
            box = raw.get("person_box", raw.get("geometry"))
            if isinstance(box, Mapping) and "person_box" in box:
                box = box["person_box"]
            if not isinstance(box, Mapping):
                raise ValueError(f"frame {frame_id} requires person_box")
            observations = raw.get("observations", {})
            if not isinstance(observations, Mapping):
                raise ValueError(f"frame {frame_id} observations must be an object")
            frames.append(
                ReplayFrame(
                    frame_id=frame_id,
                    artifact_id=artifact_id,
                    pts_ns=_non_negative_int(pts, f"frame {frame_id} pts_ns"),
                    track_id=str(raw.get("track_id", "track-1")),
                    person_box=box,
                    source_width=_positive_int(
                        raw.get("source_width", value.get("source_width", 1920)), "source_width"
                    ),
                    source_height=_positive_int(
                        raw.get("source_height", value.get("source_height", 1080)), "source_height"
                    ),
                    face_pixels=_optional_number(raw.get("face_pixels")),
                    crop_pixels=_optional_number(raw.get("crop_pixels")),
                    illumination_profile=str(
                        raw.get(
                            "illumination_profile", value.get("illumination_profile", "unknown")
                        )
                    ),
                    observations=observations,
                )
            )
        return cls(camera_id, revision, tuple(frames), source_fps, start_ns)

    @classmethod
    def from_json(cls, payload: bytes | str) -> ReplayManifest:
        value = json.loads(payload)
        if not isinstance(value, Mapping):
            raise ValueError("manifest root must be an object")
        return cls.from_mapping(value)


@dataclass(frozen=True, slots=True)
class ReplayResult:
    """All outcomes from one replay, including recoverable media failures."""

    candidates: tuple[CandidateEnvelope, ...]
    errors: tuple[ReplayError, ...]
    sampled_frame_ids: tuple[str, ...]
    skipped_frame_ids: tuple[str, ...]

    @property
    def ok(self) -> bool:
        return not self.errors


class ReplayWorker:
    """Convert a manifest into deterministic candidate events."""

    def __init__(
        self,
        camera: CameraProfile,
        artifacts: LocalArtifactStore,
        publisher: CandidatePublisher | None = None,
        *,
        sampling_fps: float | None = None,
        transport_delay_ns: int = 0,
    ) -> None:
        self.camera = camera
        self.artifacts = artifacts
        self.publisher = publisher
        self.sampling_fps = sampling_fps or camera.analysis_fps
        if self.sampling_fps <= 0:
            raise ValueError("sampling_fps must be greater than zero")
        if transport_delay_ns < 0:
            raise ValueError("transport_delay_ns must not be negative")
        self.transport_delay_ns = transport_delay_ns

    def replay(self, source: ReplayManifest | Mapping[str, Any] | Path | str) -> ReplayResult:
        """Replay ``source`` while isolating malformed frames from good ones."""

        try:
            manifest = self._load_manifest(source)
        except (ArtifactError, OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
            return ReplayResult(
                (), (ReplayError("manifest", "invalid_manifest", str(exc)),), (), ()
            )

        candidates: list[CandidateEnvelope] = []
        errors: list[ReplayError] = []
        sampled: list[str] = []
        skipped: list[str] = []
        period_ns = 1_000_000_000 / self.sampling_fps
        next_sample = 0.0
        last_sample_pts: int | None = None
        seen_frame_ids: set[str] = set()

        for frame in sorted(manifest.frames, key=lambda item: (item.pts_ns, item.frame_id)):
            if frame.frame_id in seen_frame_ids:
                errors.append(
                    ReplayError(frame.frame_id, "duplicate_frame_id", "frame ID repeated")
                )
                continue
            seen_frame_ids.add(frame.frame_id)
            if frame.pts_ns + 1e-6 < next_sample:
                skipped.append(frame.frame_id)
                continue
            if last_sample_pts == frame.pts_ns:
                skipped.append(frame.frame_id)
                continue
            sampled.append(frame.frame_id)
            last_sample_pts = frame.pts_ns
            next_sample = frame.pts_ns + period_ns
            try:
                candidate = self._candidate(manifest, frame)
                candidates.append(candidate)
                if self.publisher is not None:
                    self.publisher.publish(candidate)
            except _SkipCandidate:
                skipped.append(frame.frame_id)
            except UnsupportedArtifactError as exc:
                errors.append(ReplayError(frame.frame_id, "unsupported_media", str(exc)))
            except ArtifactError as exc:
                errors.append(ReplayError(frame.frame_id, "media_error", str(exc)))
            except CorruptMediaError as exc:
                errors.append(ReplayError(frame.frame_id, "corrupt_media", str(exc)))
            except (ValueError, TypeError) as exc:
                errors.append(ReplayError(frame.frame_id, "invalid_frame", str(exc)))

        return ReplayResult(tuple(candidates), tuple(errors), tuple(sampled), tuple(skipped))

    run = replay
    process_manifest = replay

    def _load_manifest(
        self, source: ReplayManifest | Mapping[str, Any] | Path | str
    ) -> ReplayManifest:
        if isinstance(source, ReplayManifest):
            return source
        if isinstance(source, Mapping):
            return ReplayManifest.from_mapping(source)
        path = str(source)
        payload = self.artifacts.read_bytes(path)
        return ReplayManifest.from_json(payload)

    def _candidate(self, manifest: ReplayManifest, frame: ReplayFrame) -> CandidateEnvelope:
        # Accessing the bytes is an intentional corruption/codec check.  The
        # worker never puts them into a message or retains them in a result.
        payload = self.artifacts.read_bytes(frame.artifact_id, require_image=True)
        if not _valid_image_header(frame.artifact_id, payload):
            raise CorruptMediaError("image header is missing or truncated")
        box = BoundingBox.model_validate(dict(frame.person_box))
        center_x = box.x + box.width / 2
        center_y = box.y + box.height / 2
        if not _inside_polygon(center_x, center_y, self.camera.roi):
            raise _SkipCandidate("outside_roi")
        if any(
            _inside_polygon(center_x, center_y, polygon) for polygon in self.camera.excluded_zones
        ):
            raise _SkipCandidate("excluded_zone")
        face_pixels = (
            frame.face_pixels if frame.face_pixels is not None else box.width * frame.source_width
        )
        crop_pixels = (
            frame.crop_pixels
            if frame.crop_pixels is not None
            else box.width * frame.source_width * box.height * frame.source_height
        )
        eligible = (
            face_pixels >= self.camera.min_face_pixels
            and crop_pixels >= self.camera.min_crop_pixels
        )
        quality = Quality(
            source_width=frame.source_width,
            source_height=frame.source_height,
            face_pixels=face_pixels,
            crop_pixels=crop_pixels,
            illumination_profile=_illumination(frame.illumination_profile),
            eligible=eligible,
            eligibility_reason="within_camera_limits" if eligible else "below_optical_limits",
        )
        observations = Observations.model_validate(dict(frame.observations))
        capture_ts_ns = manifest.capture_start_ts_ns + frame.pts_ns
        occurred = datetime.fromtimestamp(capture_ts_ns / 1_000_000_000, tz=UTC)
        return CandidateEnvelope(
            event_id=uuid5(
                NAMESPACE_URL,
                f"tp-smoke-detect/replay/{manifest.camera_id}/{frame.track_id}/{capture_ts_ns}",
            ),
            correlation_id=uuid5(
                NAMESPACE_URL, f"tp-smoke-detect/replay/{manifest.camera_id}/{frame.track_id}"
            ),
            producer="tp-smoke-detect.replay",
            occurred_at=occurred,
            camera_id=manifest.camera_id,
            track_id=frame.track_id,
            camera_config_revision=manifest.camera_config_revision,
            source_pts_ns=frame.pts_ns,
            capture_ts_ns=capture_ts_ns,
            received_ts_ns=capture_ts_ns + self.transport_delay_ns,
            first_seen_at=occurred,
            last_seen_at=occurred,
            stage=Stage.DETECTED,
            artifact_ids=[frame.artifact_id],
            geometry=Geometry(person_box=box),
            quality=quality,
            observations=observations,
        )


class _SkipCandidate(Exception):
    """Internal control flow for ROI filtering (not a media failure)."""


def synthetic_manifest() -> dict[str, Any]:
    """Return a tiny redistributable fixture manifest for the CLI demo."""

    return {
        "camera_id": "synthetic-camera",
        "camera_config_revision": "synthetic-camera-r1",
        "source_fps": 10,
        "source_width": 640,
        "source_height": 480,
        "frames": [
            {
                "frame_id": "synthetic-000",
                "artifact_id": "synthetic/frame-000.png",
                "pts_ns": 0,
                "track_id": "track-synthetic-1",
                "person_box": {
                    "x": 0.2,
                    "y": 0.2,
                    "width": 0.25,
                    "height": 0.5,
                    "confidence": 0.99,
                },
                "observations": {"pose": {"confidence": 0.9}},
            },
            {
                "frame_id": "synthetic-005",
                "artifact_id": "synthetic/frame-005.png",
                "pts_ns": 500_000_000,
                "track_id": "track-synthetic-1",
                "person_box": {
                    "x": 0.2,
                    "y": 0.2,
                    "width": 0.25,
                    "height": 0.5,
                    "confidence": 0.99,
                },
                "observations": {"pose": {"confidence": 0.9}},
            },
        ],
    }


def synthetic_camera() -> CameraProfile:
    return CameraProfile.model_validate(
        {
            "camera_id": "synthetic-camera",
            "zone_id": "synthetic-zone",
            "analysis_fps": 5,
            "roi": [{"x": 0, "y": 0}, {"x": 1, "y": 0}, {"x": 1, "y": 1}, {"x": 0, "y": 1}],
        }
    )


def _required_text(value: Mapping[str, Any], key: str) -> str:
    item = value.get(key)
    if not isinstance(item, str) or not item.strip():
        raise ValueError(f"manifest requires {key}")
    return item.strip()


def _positive_number(value: Any, key: str) -> float:
    result = float(value)
    if not math.isfinite(result) or result <= 0:
        raise ValueError(f"{key} must be greater than zero")
    return result


def _positive_int(value: Any, key: str) -> int:
    result = _non_negative_int(value, key)
    if result == 0:
        raise ValueError(f"{key} must be greater than zero")
    return result


def _non_negative_int(value: Any, key: str) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{key} must be an integer")
    result = int(value)
    if result < 0 or float(value) != result:
        raise ValueError(f"{key} must be a non-negative integer")
    return result


def _optional_number(value: Any) -> float | None:
    if value is None:
        return None
    result = float(value)
    if not math.isfinite(result) or result < 0:
        raise ValueError("pixel counts must be non-negative finite numbers")
    return result


def _illumination(value: str) -> Literal["day", "night", "mixed", "unknown"]:
    if value in {"day", "night", "mixed", "unknown"}:
        return cast(Literal["day", "night", "mixed", "unknown"], value)
    return "unknown"


def _valid_image_header(artifact_id: str, payload: bytes) -> bool:
    suffix = Path(artifact_id).suffix.lower()
    if not payload:
        return False
    if suffix == ".png":
        return (
            len(payload) >= 33
            and payload.startswith(b"\x89PNG\r\n\x1a\n")
            and payload[8:12] == b"\x00\x00\x00\r"
            and payload[12:16] == b"IHDR"
        )
    if suffix in {".jpg", ".jpeg"}:
        return payload.startswith(b"\xff\xd8") and payload.endswith(b"\xff\xd9")
    if suffix == ".bmp":
        return payload.startswith(b"BM")
    if suffix == ".webp":
        return payload[:4] == b"RIFF" and payload[8:12] == b"WEBP"
    return False


def _inside_polygon(x: float, y: float, polygon: Sequence[Any]) -> bool:
    """Ray-casting containment with boundary points treated as inside."""

    inside = False
    previous = polygon[-1]
    for current in polygon:
        x1, y1 = float(previous.x), float(previous.y)
        x2, y2 = float(current.x), float(current.y)
        if _on_segment(x, y, x1, y1, x2, y2):
            return True
        if (y1 > y) != (y2 > y):
            crossing_x = (x2 - x1) * (y - y1) / (y2 - y1) + x1
            if x < crossing_x:
                inside = not inside
        previous = current
    return inside


def _on_segment(x: float, y: float, x1: float, y1: float, x2: float, y2: float) -> bool:
    cross = (x - x1) * (y2 - y1) - (y - y1) * (x2 - x1)
    if abs(cross) > 1e-9:
        return False
    return (
        min(x1, x2) - 1e-9 <= x <= max(x1, x2) + 1e-9
        and min(y1, y2) - 1e-9 <= y <= max(y1, y2) + 1e-9
    )


__all__ = [
    "CorruptMediaError",
    "ReplayError",
    "ReplayFrame",
    "ReplayManifest",
    "ReplayResult",
    "ReplayWorker",
    "synthetic_camera",
    "synthetic_manifest",
]
