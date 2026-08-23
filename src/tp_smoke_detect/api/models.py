"""Request and response models for REST v1.

These models are intentionally separate from broker contracts: the API may
accept operator metadata, while event payloads continue to use the canonical
versioned contracts in :mod:`tp_smoke_detect.contracts`.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from ..contracts import DecisionCompleted, RunMode
from ..settings import CameraProfile


class APIModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class ArtifactCreate(APIModel):
    artifact_id: str | None = Field(default=None, min_length=1, max_length=128)
    path: str = Field(min_length=1, max_length=1024)
    media_type: Literal["video/mp4", "video/webm", "image/jpeg", "image/png"]
    size_bytes: int = Field(ge=0, le=500_000_000)


class EvaluationCreate(APIModel):
    artifact_id: str | None = Field(default=None, min_length=1, max_length=128)
    camera_id: str = Field(min_length=1, max_length=128)
    mode: RunMode = RunMode.REPLAY
    decision: DecisionCompleted | None = None


class ReviewCreate(APIModel):
    label: Literal["true_positive", "false_positive", "false_negative", "unusable"]
    actor: str = Field(min_length=1, max_length=128)
    reason: str | None = Field(default=None, max_length=1000)


class SiteModeChange(APIModel):
    mode: RunMode
    reason: str = Field(min_length=1, max_length=1000)
    actor: str = Field(min_length=1, max_length=128)


class AudioMuteCreate(APIModel):
    scope: Literal["site", "zone", "camera"]
    scope_id: str | None = Field(default=None, max_length=128)
    expires_at: datetime | None = None
    reason: str = Field(min_length=1, max_length=1000)
    actor: str = Field(min_length=1, max_length=128)


class CameraUpdate(APIModel):
    profile: CameraProfile
    revision: str = Field(min_length=1, max_length=128)
    activate: bool = False


class Page(APIModel):
    items: list[dict[str, Any]]
    limit: int
    offset: int


class EvaluationResponse(APIModel):
    evaluation_id: UUID
    status: Literal["accepted", "running", "completed", "failed"]
    result: dict[str, Any] | None = None
