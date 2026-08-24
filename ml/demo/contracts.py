"""Versioned, metadata-only contracts for the one-video baseline demo.

These dataclasses intentionally contain no raw pixels or host paths.  They are
the boundary shared by acquisition, model adapters, fusion, and annotation.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from enum import StrEnum
from typing import Any
from urllib.parse import urlparse

DEMO_SCHEMA_VERSION = "demo.video-annotation.v1"
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
REASON_CODES = frozenset(
    {
        "associated_cigarette",
        "below_threshold",
        "missing_pose",
        "unmatched_cigarette",
        "out_of_order_pts",
        "track_gap",
        "occluded",
        "identity_switch",
        "baseline_miss",
        "manual_review",
    }
)


class EventState(StrEnum):
    CANDIDATE = "candidate"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"
    UNCLEAR = "unclear"
    BASELINE_MISS = "baseline_miss"


class RightsDisposition(StrEnum):
    PRIVATE_USER_EVALUATION = "private_user_directed_evaluation_only"
    APPROVED_REUSABLE = "approved_reusable"
    BLOCKED = "blocked"


def _sha(value: str | None, name: str) -> None:
    if value is None or SHA256_RE.fullmatch(value) is None:
        raise ValueError(f"{name} must be a lowercase SHA-256 digest")


def _https(value: str, name: str) -> None:
    parsed = urlparse(value)
    if parsed.scheme != "https" or not parsed.netloc:
        raise ValueError(f"{name} must be an HTTPS URL")
    if parsed.username or parsed.password:
        raise ValueError(f"{name} must not contain credentials")


def _relative_id(value: str, name: str = "artifact_id") -> None:
    if not value or value.startswith(("/", "\\")) or "\\" in value:
        raise ValueError(f"{name} must be a relative POSIX identifier")
    if any(part in {"", ".", ".."} for part in value.split("/")):
        raise ValueError(f"{name} must not escape the artifact store")


@dataclass(frozen=True, slots=True)
class SourceReceipt:
    video_id: str
    canonical_url: str
    title: str
    uploader: str
    duration_seconds: float
    width: int
    height: int
    frame_rate: str
    video_codec: str
    audio_streams: int
    byte_size: int
    sha256: str
    acquisition_tool: str
    acquisition_tool_version: str
    source_revision: str
    rights_disposition: RightsDisposition
    license_note: str
    local_artifact_id: str

    def __post_init__(self) -> None:
        _https(self.canonical_url, "canonical_url")
        if self.video_id.strip() == "" or self.source_revision != self.video_id:
            raise ValueError("video_id and source_revision must identify the same source")
        if self.duration_seconds <= 0 or self.width <= 0 or self.height <= 0:
            raise ValueError("source media dimensions and duration must be positive")
        if self.audio_streams != 0:
            raise ValueError("demo source must be audio-free")
        if self.byte_size <= 0:
            raise ValueError("source byte_size must be positive")
        _sha(self.sha256, "source sha256")
        _relative_id(self.local_artifact_id, "source local_artifact_id")
        if self.rights_disposition is RightsDisposition.BLOCKED:
            raise ValueError("blocked source cannot be an approved receipt")
        if not self.license_note.strip():
            raise ValueError("source license_note is required")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self) | {"rights_disposition": self.rights_disposition.value}


@dataclass(frozen=True, slots=True)
class ModelReceipt:
    role: str
    artifact_id: str
    model_revision: str
    source_url: str
    model_card_license: str
    parent_license: str
    parent_license_url: str
    dataset_ancestry: str
    rights_disposition: RightsDisposition
    local_artifact_id: str
    artifact_size_bytes: int
    artifact_sha256: str
    format: str
    pickle_load_policy: str

    def __post_init__(self) -> None:
        if not self.role.strip() or not self.model_revision.strip():
            raise ValueError("model role and revision are required")
        _https(self.source_url, "model source_url")
        _https(self.parent_license_url, "parent_license_url")
        if self.artifact_size_bytes <= 0:
            raise ValueError("model artifact_size_bytes must be positive")
        _sha(self.artifact_sha256, "model artifact_sha256")
        _relative_id(self.local_artifact_id, "model local_artifact_id")
        if not self.model_card_license or not self.parent_license:
            raise ValueError("model and parent license terms are required")
        if not self.dataset_ancestry.strip():
            raise ValueError("dataset ancestry disposition is required")
        if self.format == "pickle" and "never" not in self.pickle_load_policy.lower():
            raise ValueError("pickle artifacts require an explicit never-load policy")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self) | {"rights_disposition": self.rights_disposition.value}


@dataclass(frozen=True, slots=True)
class RuntimeReceipt:
    runtime_id: str
    framework: str
    framework_version: str
    cuda_version: str
    device_name: str
    compute_capability: str
    device_index: int
    fake_provider: bool

    def __post_init__(self) -> None:
        if self.device_index < 0 or self.fake_provider:
            raise ValueError("demo runtime must identify a real CUDA device")
        for field_name in (
            "runtime_id",
            "framework",
            "framework_version",
            "cuda_version",
            "device_name",
            "compute_capability",
        ):
            if not getattr(self, field_name).strip():
                raise ValueError(f"runtime {field_name} is required")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class FrameEvidence:
    frame_index: int
    source_pts_ns: int
    track_id: str | None
    person_box: tuple[float, float, float, float] | None
    pose_keypoints: tuple[tuple[float, float, float], ...]
    cigarette_box: tuple[float, float, float, float] | None
    person_confidence: float | None
    cigarette_confidence: float | None
    association_score: float | None
    state: EventState
    reason_codes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.frame_index < 0 or self.source_pts_ns < 0:
            raise ValueError("frame index and source PTS must be non-negative")
        for score in (self.person_confidence, self.cigarette_confidence, self.association_score):
            if score is not None and not 0 <= score <= 1:
                raise ValueError("evidence scores must be between zero and one")
        if any(reason not in REASON_CODES for reason in self.reason_codes):
            raise ValueError("reason_codes contains an unsupported value")
        for box in (self.person_box, self.cigarette_box):
            if box is not None and any(not 0 <= value <= 1 for value in box):
                raise ValueError("boxes must use normalized coordinates")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self) | {
            "state": self.state.value,
            "person_box": list(self.person_box) if self.person_box else None,
            "cigarette_box": list(self.cigarette_box) if self.cigarette_box else None,
            "pose_keypoints": [list(point) for point in self.pose_keypoints],
        }


@dataclass(frozen=True, slots=True)
class EventInterval:
    event_id: str
    track_id: str
    start_pts_ns: int
    end_pts_ns: int
    state: EventState
    reason_codes: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.event_id.strip() or not self.track_id.strip():
            raise ValueError("event and track IDs are required")
        if self.start_pts_ns < 0 or self.end_pts_ns < self.start_pts_ns:
            raise ValueError("event interval timestamps are invalid")
        if any(reason not in REASON_CODES for reason in self.reason_codes):
            raise ValueError("event reason_codes contains an unsupported value")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self) | {"state": self.state.value}


@dataclass(frozen=True, slots=True)
class DemoRunReceipt:
    run_id: str
    schema_version: str
    source: SourceReceipt
    models: tuple[ModelReceipt, ...]
    runtime: RuntimeReceipt
    config_revision: str
    config_sha256: str
    frames: tuple[FrameEvidence, ...] = ()
    events: tuple[EventInterval, ...] = ()
    manual_review: tuple[Mapping[str, Any], ...] = ()
    audio_enabled: bool = False
    production_decision_created: bool = False

    def __post_init__(self) -> None:
        if self.schema_version != DEMO_SCHEMA_VERSION:
            raise ValueError("unsupported demo schema version")
        if not self.run_id.strip() or not self.config_revision.strip():
            raise ValueError("run_id and config_revision are required")
        _sha(self.config_sha256, "config_sha256")
        if len(self.models) < 2:
            raise ValueError("demo requires at least pose and cigarette model receipts")
        if self.audio_enabled or self.production_decision_created:
            raise ValueError(
                "baseline demo is shadow-only and cannot create audio/production decisions"
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "run_id": self.run_id,
            "source": self.source.to_dict(),
            "models": [item.to_dict() for item in self.models],
            "runtime": self.runtime.to_dict(),
            "config_revision": self.config_revision,
            "config_sha256": self.config_sha256,
            "frames": [item.to_dict() for item in self.frames],
            "events": [item.to_dict() for item in self.events],
            "manual_review": [dict(item) for item in self.manual_review],
            "audio_enabled": self.audio_enabled,
            "production_decision_created": self.production_decision_created,
        }


def validate_video_annotation(payload: Mapping[str, Any]) -> tuple[str, ...]:
    """Validate the metadata envelope before schema/media publication.

    The JSON schema is the interchange contract; this lightweight validator is
    intentionally dependency-free and catches unsafe fields before serialization.
    """

    errors: list[str] = []
    if payload.get("schema_version") != DEMO_SCHEMA_VERSION:
        errors.append("schema_version must be demo.video-annotation.v1")
    for forbidden in ("raw_pixels", "credentials", "absolute_host_path", "model_prose"):
        if forbidden in json.dumps(payload, sort_keys=True):
            errors.append(f"forbidden field/content: {forbidden}")
    if payload.get("audio_enabled") is not False:
        errors.append("audio_enabled must be false")
    if payload.get("production_decision_created") is not False:
        errors.append("production_decision_created must be false")
    source = payload.get("source")
    if not isinstance(source, Mapping):
        errors.append("source is required")
    else:
        try:
            source_values = dict(source)
            source_values["rights_disposition"] = RightsDisposition(
                source_values["rights_disposition"]
            )
            SourceReceipt(**source_values)
        except (TypeError, ValueError) as exc:
            errors.append(f"source: {exc}")
    models = payload.get("models")
    if not isinstance(models, list) or len(models) < 2:
        errors.append("models must contain at least two entries")
    else:
        for index, model in enumerate(models):
            try:
                model_values = dict(model)
                model_values["rights_disposition"] = RightsDisposition(
                    model_values["rights_disposition"]
                )
                ModelReceipt(**model_values)
            except (TypeError, ValueError) as exc:
                errors.append(f"models[{index}]: {exc}")
    return tuple(errors)


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def sha256_json(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


__all__ = [
    "DEMO_SCHEMA_VERSION",
    "DemoRunReceipt",
    "EventInterval",
    "EventState",
    "FrameEvidence",
    "ModelReceipt",
    "REASON_CODES",
    "RightsDisposition",
    "RuntimeReceipt",
    "SourceReceipt",
    "canonical_json",
    "sha256_json",
    "validate_video_annotation",
]
