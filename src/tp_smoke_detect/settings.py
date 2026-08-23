"""Safe runtime and camera configuration loaded from environment or YAML."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from .contracts import Point, RunMode


class CameraProfile(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    camera_id: str = Field(min_length=1, max_length=128)
    zone_id: str = Field(min_length=1, max_length=128)
    enabled: bool = True
    analysis_fps: Annotated[float, Field(gt=0, le=30)] = 5.0
    roi: list[Point] = Field(min_length=3)
    excluded_zones: list[list[Point]] = Field(default_factory=list)
    min_face_pixels: Annotated[int, Field(ge=0)] = 32
    min_crop_pixels: Annotated[int, Field(ge=0)] = 64
    day_night: Literal["day", "night", "auto"] = "auto"

    @field_validator("roi")
    @classmethod
    def reject_duplicate_roi_vertices(cls, value: list[Point]) -> list[Point]:
        if len({(point.x, point.y) for point in value}) < 3:
            raise ValueError("polygon must contain at least three distinct vertices")
        return value

    @field_validator("excluded_zones")
    @classmethod
    def reject_duplicate_excluded_vertices(cls, value: list[list[Point]]) -> list[list[Point]]:
        for polygon in value:
            if len({(point.x, point.y) for point in polygon}) < 3:
                raise ValueError("polygon must contain at least three distinct vertices")
        return value


class PolicySettings(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    mode: RunMode = RunMode.SIMULATION
    audio_muted: bool = True
    reject_on_unclear: bool = True
    min_persistence_ms: Annotated[int, Field(ge=0)] = 2000
    min_independent_channels: Annotated[int, Field(ge=1)] = 2
    queue_max_per_camera: Annotated[int, Field(ge=1)] = 32
    cooldown_seconds: Annotated[int, Field(ge=0)] = 60
    hourly_audio_cap: Annotated[int, Field(ge=0)] = 10
    daily_audio_cap: Annotated[int, Field(ge=0)] = 100


class AppSettings(BaseSettings):
    """Application settings; defaults are simulation and audio-muted by design."""

    model_config = SettingsConfigDict(
        env_prefix="SMOKE_DETECT_",
        env_nested_delimiter="__",
        extra="forbid",
        case_sensitive=False,
    )

    service_name: str = "tp-smoke-detect"
    environment: Literal["development", "test", "staging", "production"] = "development"
    policy: PolicySettings = Field(default_factory=PolicySettings)
    cameras: list[CameraProfile] = Field(default_factory=list)


def load_settings(path: Path | str | None = None) -> AppSettings:
    """Load optional YAML then apply environment overrides through Pydantic."""

    if path is None:
        return AppSettings()
    config_path = Path(path)
    with config_path.open("r", encoding="utf-8") as handle:
        document = yaml.safe_load(handle) or {}
    if not isinstance(document, dict):
        raise ValueError("configuration root must be a YAML mapping")
    return AppSettings.model_validate(document)


__all__ = ["AppSettings", "CameraProfile", "PolicySettings", "load_settings"]
