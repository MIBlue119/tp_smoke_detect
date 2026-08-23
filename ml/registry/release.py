"""Supply-chain and immutable model release contracts.

The registry adapter (MLflow or another on-premise service) can store this
document, but promotion is validated locally first so disconnected operation is
deterministic and auditable.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

_SHA256 = re.compile(r"^[0-9a-f]{64}$")


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


@dataclass(frozen=True, slots=True)
class Provenance:
    origin: str
    license: str
    decision: str
    source_hash: str
    dependencies: tuple[str, ...] = ()

    def validate(self, prefix: str = "provenance") -> list[str]:
        errors: list[str] = []
        if not self.origin.strip():
            errors.append(f"{prefix}.origin is required")
        if not self.license.strip() or self.license.lower() in {"unknown", "proprietary-unknown"}:
            errors.append(f"{prefix}.license must be documented")
        if self.decision not in {"approved", "conditional", "rejected"}:
            errors.append(f"{prefix}.decision must be approved, conditional, or rejected")
        if not _SHA256.fullmatch(self.source_hash):
            errors.append(f"{prefix}.source_hash must be a SHA-256 digest")
        return errors

    def to_dict(self) -> dict[str, Any]:
        return {
            "origin": self.origin,
            "license": self.license,
            "decision": self.decision,
            "source_hash": self.source_hash,
            "dependencies": list(self.dependencies),
        }


@dataclass(frozen=True, slots=True)
class ModelReleaseManifest:
    model_id: str
    version: str
    model_sha256: str
    dataset_manifest_sha256: str
    evaluation_report_sha256: str
    calibration_report_sha256: str
    runtime_profile: str
    provenance: Provenance
    rollback_target: str | None
    parent_checkpoint: str | None = None
    sbom_sha256: str | None = None
    aliases: tuple[str, ...] = ()
    metadata: dict[str, str] = field(default_factory=dict)

    def payload(self) -> dict[str, Any]:
        return {
            "schema_version": "model.release.v1",
            "model_id": self.model_id,
            "version": self.version,
            "model_sha256": self.model_sha256,
            "dataset_manifest_sha256": self.dataset_manifest_sha256,
            "evaluation_report_sha256": self.evaluation_report_sha256,
            "calibration_report_sha256": self.calibration_report_sha256,
            "runtime_profile": self.runtime_profile,
            "provenance": self.provenance.to_dict(),
            "rollback_target": self.rollback_target,
            "parent_checkpoint": self.parent_checkpoint,
            "sbom_sha256": self.sbom_sha256,
            "aliases": sorted(self.aliases),
            "metadata": dict(sorted(self.metadata.items())),
        }

    @property
    def release_sha256(self) -> str:
        return hashlib.sha256(_canonical(self.payload()).encode("utf-8")).hexdigest()

    def to_dict(self) -> dict[str, Any]:
        result = self.payload()
        result["release_sha256"] = self.release_sha256
        return result

    def to_json(self) -> str:
        return _canonical(self.to_dict()) + "\n"

    def write(self, path: str | Path) -> None:
        Path(path).write_text(self.to_json(), encoding="utf-8")


@dataclass(frozen=True, slots=True)
class PromotionResult:
    allowed: bool
    errors: tuple[str, ...]
    release_sha256: str


def validate_release_manifest(manifest: ModelReleaseManifest) -> tuple[str, ...]:
    """Validate required hashes, provenance, and release safety gates."""

    errors: list[str] = []
    for name in (
        "model_sha256",
        "dataset_manifest_sha256",
        "evaluation_report_sha256",
        "calibration_report_sha256",
    ):
        if not _SHA256.fullmatch(getattr(manifest, name)):
            errors.append(f"{name} must be a SHA-256 digest")
    if manifest.sbom_sha256 is not None and not _SHA256.fullmatch(manifest.sbom_sha256):
        errors.append("sbom_sha256 must be a SHA-256 digest")
    if (
        not manifest.model_id.strip()
        or not manifest.version.strip()
        or not manifest.runtime_profile.strip()
    ):
        errors.append("model_id, version, and runtime_profile are required")
    if manifest.provenance.decision != "approved":
        errors.append("provenance decision must be approved for promotion")
    errors.extend(manifest.provenance.validate())
    if not manifest.rollback_target:
        errors.append("rollback_target is required")
    if "champion" in manifest.aliases and not manifest.rollback_target:
        errors.append("champion release requires rollback_target")
    return tuple(dict.fromkeys(errors))


def validate_promotion(
    manifest: ModelReleaseManifest,
    *,
    existing_releases: Iterable[ModelReleaseManifest] = (),
) -> PromotionResult:
    """Validate a candidate and reject mutable/version/hash collisions."""

    errors = list(validate_release_manifest(manifest))
    for existing in existing_releases:
        if existing.model_id == manifest.model_id and existing.version == manifest.version:
            if existing.release_sha256 != manifest.release_sha256:
                errors.append("release version already exists with different content")
            else:
                errors.append("release version already exists; releases are immutable")
    return PromotionResult(not errors, tuple(dict.fromkeys(errors)), manifest.release_sha256)
