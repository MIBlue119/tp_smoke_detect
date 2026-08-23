"""Safe runtime and camera configuration loaded from environment or YAML."""

from __future__ import annotations

import json
import os
from datetime import time
from pathlib import Path
from typing import Annotated, Any, Literal

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
    quiet_hours_start: time | None = None
    quiet_hours_end: time | None = None
    policy_revision: str = Field(default="default", min_length=1, max_length=128)
    audio_message_id: str = Field(default="smoke-reminder-neutral-01", min_length=1, max_length=128)
    audio_command_ttl_seconds: Annotated[int, Field(gt=0)] = 30


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
    # SQLite is the CPU/reference persistence adapter.  Production deployments
    # may provide the PostgreSQL adapter through the same repository port; the
    # default remains in-memory so importing the ASGI app is side-effect free.
    database: str = ":memory:"
    artifact_root: str = "artifacts"
    policy: PolicySettings = Field(default_factory=PolicySettings)
    cameras: list[CameraProfile] = Field(default_factory=list)


def _merge_mapping(target: dict[str, Any], source: dict[str, Any]) -> None:
    """Merge nested YAML mappings without allowing a file to erase siblings."""

    for key, value in source.items():
        if isinstance(value, dict) and isinstance(target.get(key), dict):
            _merge_mapping(target[key], value)
        else:
            target[key] = value


def _set_nested(mapping: dict[str, Any], keys: list[str], value: Any) -> None:
    current = mapping
    for key in keys[:-1]:
        next_value = current.get(key)
        if not isinstance(next_value, dict):
            next_value = {}
            current[key] = next_value
        current = next_value
    current[keys[-1]] = value


def _environment_overrides(document: dict[str, Any]) -> dict[str, Any]:
    """Return YAML plus only known SMOKE_DETECT_* environment overrides.

    Pydantic's settings source cannot be layered after an arbitrary YAML
    document, so we preserve its normal nested delimiter and let the final
    model validation perform the same scalar coercion (bools, integers,
    times, and JSON lists) as ``BaseSettings``.
    """

    known = {"service_name", "environment", "database", "artifact_root", "policy", "cameras"}
    overrides: list[tuple[list[str], Any]] = []
    for name, raw_value in os.environ.items():
        if not name.startswith("SMOKE_DETECT_") or name in {
            "SMOKE_DETECT_CONFIG",
            "SMOKE_DETECT_POLICY_CONFIG",
            "SMOKE_DETECT_CAMERAS_CONFIG",
        }:
            continue
        suffix = name.removeprefix("SMOKE_DETECT_")
        keys = [part.lower() for part in suffix.split("__")]
        if keys[0] not in known:
            continue
        value: Any = raw_value
        # Preserve BaseSettings' JSON decoding for top-level complex fields
        # while retaining scalar coercion for nested values.
        if len(keys) == 1 and keys[0] in {"policy", "cameras"}:
            try:
                value = json.loads(raw_value)
            except json.JSONDecodeError:
                value = raw_value
        overrides.append((keys, value))

    # BaseSettings applies nested variables after top-level JSON.  Keep those
    # source classes separate so os.environ insertion order cannot change a
    # safety setting such as audio_muted; sort the second pass for stable
    # behavior when nested paths overlap.
    for keys, value in sorted(overrides, key=lambda item: (len(item[0]), item[0])):
        _set_nested(document, keys, value)
    return document


def _read_yaml(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        document = yaml.safe_load(handle) or {}
    if not isinstance(document, dict):
        raise ValueError(f"configuration root must be a YAML mapping: {path}")
    return document


def load_settings(
    path: Path | str | None = None,
    *,
    policy_path: Path | str | None = None,
    cameras_path: Path | str | None = None,
) -> AppSettings:
    """Load YAML configuration, then apply explicit environment overrides.

    ``SMOKE_DETECT_CONFIG`` can point at one combined file.  Deployments that
    keep policy and camera profiles as separate read-only mounts may provide
    ``SMOKE_DETECT_POLICY_CONFIG`` and ``SMOKE_DETECT_CAMERAS_CONFIG``.  The
    latter files are merged before environment values, so operators can safely
    change a scalar without silently discarding the mounted camera list.
    """

    document: dict[str, Any] = {}
    candidates = (
        path or os.environ.get("SMOKE_DETECT_CONFIG"),
        policy_path or os.environ.get("SMOKE_DETECT_POLICY_CONFIG"),
        cameras_path or os.environ.get("SMOKE_DETECT_CAMERAS_CONFIG"),
    )
    for candidate in candidates:
        if candidate is not None:
            _merge_mapping(document, _read_yaml(Path(candidate)))
    return AppSettings.model_validate(_environment_overrides(document))


__all__ = ["AppSettings", "CameraProfile", "PolicySettings", "load_settings"]
