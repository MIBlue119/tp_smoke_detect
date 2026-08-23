"""Versioned, provider-neutral envelopes shared by every service lane.

The contracts intentionally contain features and references, never raw pixels or
free-form model prose.  Additive changes to v1 must keep existing fields and enum
values valid; breaking changes require a new versioned module and schema directory.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class ContractModel(BaseModel):
    """Base model with stable wire formatting and rejection of accidental fields."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True, str_strip_whitespace=True)


class Stage(StrEnum):
    DETECTED = "detected"
    QUALITY = "quality"
    POSE = "pose"
    OBJECT = "object"
    TEMPORAL = "temporal"
    REVIEW = "review"
    COMPLETED = "completed"


class DecisionOutcome(StrEnum):
    VERIFIED = "verified"
    REJECTED = "rejected"
    UNCLEAR = "unclear"
    ERROR = "error"


class RunMode(StrEnum):
    SIMULATION = "simulation"
    REPLAY = "replay"
    SHADOW = "shadow"
    HUMAN_CONFIRMED = "human_confirmed"
    AUTOMATIC = "automatic"


class Point(ContractModel):
    x: Annotated[float, Field(ge=0, le=1)]
    y: Annotated[float, Field(ge=0, le=1)]


class BoundingBox(ContractModel):
    x: Annotated[float, Field(ge=0, le=1)]
    y: Annotated[float, Field(ge=0, le=1)]
    width: Annotated[float, Field(gt=0, le=1)]
    height: Annotated[float, Field(gt=0, le=1)]
    confidence: Annotated[float, Field(ge=0, le=1)]


class Geometry(ContractModel):
    person_box: BoundingBox
    roi_id: str | None = None
    coverage_score: Annotated[float, Field(ge=0, le=1)] = 1.0


class Quality(ContractModel):
    source_width: Annotated[int, Field(gt=0)]
    source_height: Annotated[int, Field(gt=0)]
    face_pixels: Annotated[float, Field(ge=0)]
    crop_pixels: Annotated[float, Field(ge=0)]
    illumination_profile: Literal["day", "night", "mixed", "unknown"]
    eligible: bool
    eligibility_reason: str


class PoseObservation(ContractModel):
    hand_to_mouth_distance: Annotated[float, Field(ge=0)] | None = None
    mouth_dwell_ms: Annotated[int, Field(ge=0)] | None = None
    confidence: Annotated[float, Field(ge=0, le=1)]


class ObjectObservation(ContractModel):
    label: Literal[
        "cigarette",
        "vape",
        "phone",
        "cup",
        "food",
        "pen_toothpick",
        "betel_quid",
        "background",
        "unknown",
    ]
    confidence: Annotated[float, Field(ge=0, le=1)]


class SmokeObservation(ContractModel):
    smoke_score: Annotated[float, Field(ge=0, le=1)]
    ember_score: Annotated[float, Field(ge=0, le=1)]


class TemporalObservation(ContractModel):
    hand_retreat: bool
    cycle_interval_ms: Annotated[int, Field(ge=0)] | None = None
    persistence_ms: Annotated[int, Field(ge=0)]


class Observations(ContractModel):
    pose: PoseObservation | None = None
    objects: list[ObjectObservation] = Field(default_factory=list)
    smoke: SmokeObservation | None = None
    temporal: TemporalObservation | None = None
    independent_channels: list[str] = Field(default_factory=list)


class CandidateEnvelope(ContractModel):
    """The ``track.candidate.v1`` message represented as JSON."""

    schema_version: Literal["track.candidate.v1"] = "track.candidate.v1"
    camera_id: str = Field(min_length=1, max_length=128)
    track_id: str = Field(min_length=1, max_length=128)
    camera_config_revision: str = Field(min_length=1, max_length=128)
    source_pts_ns: Annotated[int, Field(ge=0)]
    capture_ts_ns: Annotated[int, Field(ge=0)]
    received_ts_ns: Annotated[int, Field(ge=0)]
    first_seen_at: datetime
    last_seen_at: datetime
    stage: Stage
    artifact_ids: list[str] = Field(default_factory=list)
    geometry: Geometry
    quality: Quality
    observations: Observations


class EvidenceChannel(ContractModel):
    name: str = Field(min_length=1, max_length=64)
    score: Annotated[float, Field(ge=0, le=1)]
    positive: bool


class DecisionCompleted(ContractModel):
    """The ``decision.completed.v1`` message represented as JSON."""

    schema_version: Literal["decision.completed.v1"] = "decision.completed.v1"
    decision_id: UUID
    camera_id: str = Field(min_length=1, max_length=128)
    track_id: str = Field(min_length=1, max_length=128)
    outcome: DecisionOutcome
    reason_codes: list[str] = Field(min_length=1)
    evidence_channels: list[EvidenceChannel] = Field(default_factory=list)
    model_revisions: dict[str, str] = Field(default_factory=dict)
    policy_revision: str = Field(min_length=1, max_length=128)
    latency_ms: Annotated[float, Field(ge=0)]
    mode: RunMode
    audio_eligibility: bool


class AudioCommand(ContractModel):
    """The ``audio.command.v1`` message represented as JSON."""

    schema_version: Literal["audio.command.v1"] = "audio.command.v1"
    command_id: UUID
    decision_id: UUID
    zone_id: str = Field(min_length=1, max_length=128)
    message_id: str = Field(min_length=1, max_length=128)
    volume_profile: str = Field(min_length=1, max_length=64)
    expires_at: datetime
    policy_revision: str = Field(min_length=1, max_length=128)


SchemaContract = CandidateEnvelope | DecisionCompleted | AudioCommand
