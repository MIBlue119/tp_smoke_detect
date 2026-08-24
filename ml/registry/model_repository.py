"""Fail-closed model acquisition, repository, and TensorRT identity contracts.

The GPU profile consumes this module at build and qualification time only.  It
never downloads a model at runtime.  A repository entry is metadata-only until
an operator records an exact content hash, license disposition, SBOM hash, and
runtime binding in an approved local artifact store.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import ssl
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path, PurePosixPath
from typing import Any

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_HTTPS = "https"
_ROLES = {
    "person_detector",
    "pose_landmarker",
    "hand_landmarker",
    "crop_classifier",
    "reviewer_vlm",
}
_PRECISIONS = {"fp16", "int8"}


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _valid_hash(value: str | None) -> bool:
    return value is not None and _SHA256.fullmatch(value) is not None


def _safe_filename(value: str, field: str = "local_filename") -> list[str]:
    errors: list[str] = []
    if not value.strip():
        return [f"{field} is required"]
    if "\\" in value:
        errors.append(f"{field} must use a relative POSIX path")
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or "." in path.parts:
        errors.append(f"{field} must stay within the artifact root")
    if not errors and any(not part.strip() for part in path.parts):
        errors.append(f"{field} contains an empty path component")
    return errors


class LicenseDisposition(StrEnum):
    """Procurement/legal outcome for an artifact source."""

    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


@dataclass(frozen=True, slots=True)
class AcquisitionRecord:
    """Canonical public source and legal evidence for one artifact."""

    canonical_uri: str
    publisher: str
    source_revision: str
    license_id: str
    license_uri: str
    license_disposition: LicenseDisposition
    expected_sha256: str | None
    expected_size_bytes: int | None

    def validate(self, prefix: str = "source") -> tuple[str, ...]:
        errors: list[str] = []
        parsed = urllib.parse.urlparse(self.canonical_uri)
        if parsed.scheme != _HTTPS or not parsed.netloc:
            errors.append(f"{prefix}.canonical_uri must be an HTTPS URL")
        if parsed.username or parsed.password:
            errors.append(f"{prefix}.canonical_uri must not contain credentials")
        if not self.publisher.strip():
            errors.append(f"{prefix}.publisher is required")
        if not self.source_revision.strip():
            errors.append(f"{prefix}.source_revision is required")
        license_url = urllib.parse.urlparse(self.license_uri)
        if license_url.scheme != _HTTPS or not license_url.netloc:
            errors.append(f"{prefix}.license_uri must be an HTTPS URL")
        if not self.license_id.strip():
            errors.append(f"{prefix}.license_id is required")
        if self.license_disposition is not LicenseDisposition.APPROVED:
            errors.append(f"{prefix}.license disposition must be approved")
        if not _valid_hash(self.expected_sha256):
            errors.append(f"{prefix}.expected_sha256 must be a SHA-256 digest")
        if self.expected_size_bytes is None or self.expected_size_bytes <= 0:
            errors.append(f"{prefix}.expected_size_bytes must be positive")
        return tuple(errors)

    def to_dict(self) -> dict[str, Any]:
        return {
            "canonical_uri": self.canonical_uri,
            "publisher": self.publisher,
            "source_revision": self.source_revision,
            "license_id": self.license_id,
            "license_uri": self.license_uri,
            "license_disposition": self.license_disposition.value,
            "expected_sha256": self.expected_sha256,
            "expected_size_bytes": self.expected_size_bytes,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> AcquisitionRecord:
        return cls(
            canonical_uri=str(raw.get("canonical_uri", "")),
            publisher=str(raw.get("publisher", "")),
            source_revision=str(raw.get("source_revision", "")),
            license_id=str(raw.get("license_id", "")),
            license_uri=str(raw.get("license_uri", "")),
            license_disposition=LicenseDisposition(str(raw.get("license_disposition", "pending"))),
            expected_sha256=(
                str(raw["expected_sha256"]) if raw.get("expected_sha256") is not None else None
            ),
            expected_size_bytes=(
                int(raw["expected_size_bytes"])
                if raw.get("expected_size_bytes") is not None
                else None
            ),
        )


@dataclass(frozen=True, slots=True)
class ModelArtifact:
    """Immutable metadata for a staged model, export, or task artifact."""

    artifact_id: str
    role: str
    version: str
    source: AcquisitionRecord
    local_filename: str
    artifact_sha256: str | None
    artifact_size_bytes: int | None
    parent_checkpoint: str | None
    dataset_manifest_sha256: str | None
    export_sha256: str | None
    sbom_filename: str | None
    sbom_sha256: str | None
    rollback_target: str | None

    def __post_init__(self) -> None:
        path_errors = _safe_filename(self.local_filename, "local_filename")
        if path_errors:
            raise ValueError("; ".join(path_errors))
        if self.sbom_filename is not None:
            path_errors = _safe_filename(self.sbom_filename, "sbom_filename")
            if path_errors:
                raise ValueError("; ".join(path_errors))

    def validate(self, prefix: str = "artifact") -> tuple[str, ...]:
        errors: list[str] = []
        for field_name in ("artifact_id", "role", "version"):
            if not getattr(self, field_name).strip():
                errors.append(f"{prefix}.{field_name} is required")
        if self.role not in _ROLES:
            errors.append(f"{prefix}.role is unsupported: {self.role}")
        errors.extend(_safe_filename(self.local_filename, f"{prefix}.local_filename"))
        errors.extend(self.source.validate(f"{prefix}.source"))
        if not _valid_hash(self.artifact_sha256):
            errors.append(f"{prefix}.artifact_sha256 must be a SHA-256 digest")
        if self.artifact_size_bytes is None or self.artifact_size_bytes <= 0:
            errors.append(f"{prefix}.artifact_size_bytes must be positive")
        if self.source.expected_sha256 != self.artifact_sha256:
            errors.append(f"{prefix}.artifact_sha256 must match source.expected_sha256")
        if self.source.expected_size_bytes != self.artifact_size_bytes:
            errors.append(f"{prefix}.artifact_size_bytes must match source.expected_size_bytes")
        if not self.parent_checkpoint or not self.parent_checkpoint.strip():
            errors.append(f"{prefix}.parent_checkpoint is required")
        if not _valid_hash(self.dataset_manifest_sha256):
            errors.append(f"{prefix}.dataset_manifest_sha256 must be a SHA-256 digest")
        if not _valid_hash(self.export_sha256):
            errors.append(f"{prefix}.export_sha256 must be a SHA-256 digest")
        if not self.sbom_filename:
            errors.append(f"{prefix}.sbom_filename is required")
        else:
            errors.extend(_safe_filename(self.sbom_filename, f"{prefix}.sbom_filename"))
        if not _valid_hash(self.sbom_sha256):
            errors.append(f"{prefix}.sbom_sha256 must be a SHA-256 digest")
        if not self.rollback_target or not self.rollback_target.strip():
            errors.append(f"{prefix}.rollback_target is required")
        elif self.rollback_target == f"{self.artifact_id}:{self.version}":
            errors.append(f"{prefix}.rollback_target must identify a prior immutable version")
        return tuple(dict.fromkeys(errors))

    def to_dict(self) -> dict[str, Any]:
        return {
            "artifact_id": self.artifact_id,
            "role": self.role,
            "version": self.version,
            "source": self.source.to_dict(),
            "local_filename": self.local_filename,
            "artifact_sha256": self.artifact_sha256,
            "artifact_size_bytes": self.artifact_size_bytes,
            "parent_checkpoint": self.parent_checkpoint,
            "dataset_manifest_sha256": self.dataset_manifest_sha256,
            "export_sha256": self.export_sha256,
            "sbom_filename": self.sbom_filename,
            "sbom_sha256": self.sbom_sha256,
            "rollback_target": self.rollback_target,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> ModelArtifact:
        return cls(
            artifact_id=str(raw.get("artifact_id", "")),
            role=str(raw.get("role", "")),
            version=str(raw.get("version", "")),
            source=AcquisitionRecord.from_dict(raw.get("source", {})),
            local_filename=str(raw.get("local_filename", "")),
            artifact_sha256=(
                str(raw["artifact_sha256"]) if raw.get("artifact_sha256") is not None else None
            ),
            artifact_size_bytes=(
                int(raw["artifact_size_bytes"])
                if raw.get("artifact_size_bytes") is not None
                else None
            ),
            parent_checkpoint=(
                str(raw["parent_checkpoint"]) if raw.get("parent_checkpoint") is not None else None
            ),
            dataset_manifest_sha256=(
                str(raw["dataset_manifest_sha256"])
                if raw.get("dataset_manifest_sha256") is not None
                else None
            ),
            export_sha256=(
                str(raw["export_sha256"]) if raw.get("export_sha256") is not None else None
            ),
            sbom_filename=(
                str(raw["sbom_filename"]) if raw.get("sbom_filename") is not None else None
            ),
            sbom_sha256=(str(raw["sbom_sha256"]) if raw.get("sbom_sha256") is not None else None),
            rollback_target=(
                str(raw["rollback_target"]) if raw.get("rollback_target") is not None else None
            ),
        )


@dataclass(frozen=True, slots=True)
class EngineBinding:
    """TensorRT identity; every input to an engine build is hash-bound."""

    engine_id: str
    artifact_id: str
    artifact_version: str
    plan_filename: str
    plan_sha256: str | None
    model_sha256: str | None
    export_sha256: str | None
    input_contract_sha256: str | None
    calibration_sha256: str | None
    build_image_sha256: str | None
    precision: str
    compute_capability: str
    cuda_version: str
    tensorrt_version: str
    runtime_profile: str

    def __post_init__(self) -> None:
        path_errors = _safe_filename(self.plan_filename, "plan_filename")
        if path_errors:
            raise ValueError("; ".join(path_errors))

    def validate(
        self,
        *,
        artifact_hash: str | None = None,
        export_hash: str | None = None,
        plan_root: Path | None = None,
        prefix: str = "engine",
    ) -> tuple[str, ...]:
        errors: list[str] = []
        for field_name in (
            "engine_id",
            "artifact_id",
            "artifact_version",
            "precision",
            "compute_capability",
            "cuda_version",
            "tensorrt_version",
            "runtime_profile",
        ):
            if not getattr(self, field_name).strip():
                errors.append(f"{prefix}.{field_name} is required")
        if self.precision not in _PRECISIONS:
            errors.append(f"{prefix}.precision must be fp16 or int8")
        errors.extend(_safe_filename(self.plan_filename, f"{prefix}.plan_filename"))
        for field_name in (
            "plan_sha256",
            "model_sha256",
            "export_sha256",
            "input_contract_sha256",
            "calibration_sha256",
            "build_image_sha256",
        ):
            if not _valid_hash(getattr(self, field_name)):
                errors.append(f"{prefix}.{field_name} must be a SHA-256 digest")
        if artifact_hash is not None and self.model_sha256 != artifact_hash:
            errors.append(f"{prefix}.model_sha256 does not match model artifact")
        if export_hash is not None and self.export_sha256 != export_hash:
            errors.append(f"{prefix}.export_sha256 does not match model export")
        if plan_root is not None:
            plan_path = plan_root / self.plan_filename
            try:
                resolved_root = plan_root.resolve()
                resolved_plan = plan_path.resolve()
                resolved_plan.relative_to(resolved_root)
                if not resolved_plan.is_file():
                    errors.append(f"{prefix}.plan_filename is missing from the artifact root")
                elif _hash_file(resolved_plan) != self.plan_sha256:
                    errors.append(f"{prefix}.plan_sha256 does not match local TensorRT plan bytes")
            except (OSError, ValueError) as exc:
                errors.append(f"{prefix}.plan_filename cannot be verified: {exc}")
        return tuple(dict.fromkeys(errors))

    def to_dict(self) -> dict[str, Any]:
        return {
            "engine_id": self.engine_id,
            "artifact_id": self.artifact_id,
            "artifact_version": self.artifact_version,
            "plan_filename": self.plan_filename,
            "plan_sha256": self.plan_sha256,
            "model_sha256": self.model_sha256,
            "export_sha256": self.export_sha256,
            "input_contract_sha256": self.input_contract_sha256,
            "calibration_sha256": self.calibration_sha256,
            "build_image_sha256": self.build_image_sha256,
            "precision": self.precision,
            "compute_capability": self.compute_capability,
            "cuda_version": self.cuda_version,
            "tensorrt_version": self.tensorrt_version,
            "runtime_profile": self.runtime_profile,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> EngineBinding:
        return cls(
            engine_id=str(raw.get("engine_id", "")),
            artifact_id=str(raw.get("artifact_id", "")),
            artifact_version=str(raw.get("artifact_version", "")),
            plan_filename=str(raw.get("plan_filename", "")),
            plan_sha256=str(raw["plan_sha256"]) if raw.get("plan_sha256") is not None else None,
            model_sha256=str(raw["model_sha256"]) if raw.get("model_sha256") is not None else None,
            export_sha256=(
                str(raw["export_sha256"]) if raw.get("export_sha256") is not None else None
            ),
            input_contract_sha256=(
                str(raw["input_contract_sha256"])
                if raw.get("input_contract_sha256") is not None
                else None
            ),
            calibration_sha256=(
                str(raw["calibration_sha256"])
                if raw.get("calibration_sha256") is not None
                else None
            ),
            build_image_sha256=(
                str(raw["build_image_sha256"])
                if raw.get("build_image_sha256") is not None
                else None
            ),
            precision=str(raw.get("precision", "")),
            compute_capability=str(raw.get("compute_capability", "")),
            cuda_version=str(raw.get("cuda_version", "")),
            tensorrt_version=str(raw.get("tensorrt_version", "")),
            runtime_profile=str(raw.get("runtime_profile", "")),
        )


@dataclass(frozen=True, slots=True)
class ModelRepositoryManifest:
    """A deterministic, immutable model repository release description."""

    release_id: str
    release_version: str
    models: tuple[ModelArtifact, ...]
    engines: tuple[EngineBinding, ...]
    schema_version: str = "ml.model-repository.v1"

    @property
    def payload(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "release_id": self.release_id,
            "release_version": self.release_version,
            "models": [
                model.to_dict() for model in sorted(self.models, key=lambda item: item.artifact_id)
            ],
            "engines": [
                engine.to_dict() for engine in sorted(self.engines, key=lambda item: item.engine_id)
            ],
        }

    @property
    def digest(self) -> str:
        return hashlib.sha256(_canonical(self.payload).encode("utf-8")).hexdigest()

    def to_dict(self) -> dict[str, Any]:
        result = self.payload
        result["manifest_sha256"] = self.digest
        return result

    def to_json(self) -> str:
        return _canonical(self.to_dict()) + "\n"

    def write(self, path: str | Path) -> None:
        destination = Path(path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_name(f".{destination.name}.tmp")
        temporary.write_text(self.to_json(), encoding="utf-8")
        os.replace(temporary, destination)

    def validate(self) -> tuple[str, ...]:
        errors: list[str] = []
        if self.schema_version != "ml.model-repository.v1":
            errors.append("unsupported model repository schema_version")
        if not self.release_id.strip() or not self.release_version.strip():
            errors.append("release_id and release_version are required")
        artifact_ids = [model.artifact_id for model in self.models]
        if len(set(artifact_ids)) != len(artifact_ids):
            errors.append("model artifact IDs must be unique")
        engine_ids = [engine.engine_id for engine in self.engines]
        if len(set(engine_ids)) != len(engine_ids):
            errors.append("engine IDs must be unique")
        for model in self.models:
            errors.extend(model.validate(f"models[{model.artifact_id!r}]"))
        model_by_id = {model.artifact_id: model for model in self.models}
        for engine in self.engines:
            prefix = f"engines[{engine.engine_id!r}]"
            selected_model = model_by_id.get(engine.artifact_id)
            if selected_model is None:
                errors.append(f"{prefix}.artifact_id does not reference a model")
                errors.extend(engine.validate(prefix=prefix))
            else:
                errors.extend(
                    engine.validate(
                        artifact_hash=selected_model.artifact_sha256,
                        export_hash=selected_model.export_sha256,
                        prefix=prefix,
                    )
                )
                if engine.artifact_version != selected_model.version:
                    errors.append(f"{prefix}.artifact_version does not match model version")
        return tuple(dict.fromkeys(errors))

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> ModelRepositoryManifest:
        models_raw = raw.get("models", [])
        engines_raw = raw.get("engines", [])
        if not isinstance(models_raw, list) or not isinstance(engines_raw, list):
            raise ValueError("models and engines must be arrays")
        return cls(
            release_id=str(raw.get("release_id", "")),
            release_version=str(raw.get("release_version", "")),
            models=tuple(ModelArtifact.from_dict(item) for item in models_raw),
            engines=tuple(EngineBinding.from_dict(item) for item in engines_raw),
            schema_version=str(raw.get("schema_version", "")),
        )

    @classmethod
    def read(cls, path: str | Path) -> ModelRepositoryManifest:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError("model repository manifest must be a JSON object")
        expected_digest = raw.get("manifest_sha256")
        manifest = cls.from_dict(raw)
        if expected_digest != manifest.digest:
            raise ValueError("model repository manifest checksum mismatch")
        return manifest


@dataclass(frozen=True, slots=True)
class ArtifactVerification:
    """Machine-readable local verification receipt."""

    artifact_id: str
    status: str
    artifact_sha256: str | None
    sbom_sha256: str | None
    errors: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "artifact_id": self.artifact_id,
            "status": self.status,
            "artifact_sha256": self.artifact_sha256,
            "sbom_sha256": self.sbom_sha256,
            "errors": list(self.errors),
        }


@dataclass(frozen=True, slots=True)
class AcquisitionReceipt:
    """Receipt for an explicit, operator-authorized acquisition attempt."""

    artifact_id: str
    status: str
    destination: str | None
    errors: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "artifact_id": self.artifact_id,
            "status": self.status,
            "destination": self.destination,
            "errors": list(self.errors),
        }


def _within(root: Path, relative: str) -> Path:
    errors = _safe_filename(relative)
    if errors:
        raise ValueError("; ".join(errors))
    root_resolved = root.resolve()
    path = (root_resolved / PurePosixPath(relative)).resolve()
    try:
        path.relative_to(root_resolved)
    except ValueError as error:
        raise ValueError("path escapes artifact root") from error
    return path


def verify_local_artifact(spec: ModelArtifact, artifact_root: str | Path) -> ArtifactVerification:
    """Verify staged bytes and SBOMs without modifying the artifact store."""

    errors = list(spec.validate())
    root = Path(artifact_root)
    try:
        artifact_path = _within(root, spec.local_filename)
        sbom_path = _within(root, spec.sbom_filename or "")
    except ValueError as error:
        return ArtifactVerification(spec.artifact_id, "blocked", None, None, (str(error),))
    actual_artifact: str | None = None
    actual_sbom: str | None = None
    if not artifact_path.is_file():
        errors.append("artifact file is missing")
    else:
        actual_artifact = _hash_file(artifact_path)
        if spec.artifact_sha256 is None or actual_artifact != spec.artifact_sha256:
            errors.append("artifact sha256 mismatch")
        if (
            spec.artifact_size_bytes is not None
            and artifact_path.stat().st_size != spec.artifact_size_bytes
        ):
            errors.append("artifact size mismatch")
    if not sbom_path.is_file():
        errors.append("SBOM file is missing")
    else:
        actual_sbom = _hash_file(sbom_path)
        if spec.sbom_sha256 is None or actual_sbom != spec.sbom_sha256:
            errors.append("SBOM sha256 mismatch")
    return ArtifactVerification(
        spec.artifact_id,
        "verified" if not errors else "blocked",
        actual_artifact,
        actual_sbom,
        tuple(dict.fromkeys(errors)),
    )


def acquire_model_artifact(
    spec: ModelArtifact,
    artifact_root: str | Path,
    *,
    allow_network: bool = False,
    timeout_seconds: int = 30,
) -> AcquisitionReceipt:
    """Acquire one artifact only after explicit legal/hash approval.

    The default is offline and fail-closed.  The caller must opt in to network
    access; runtime services never call this function.  Downloads are written
    atomically and verified before the temporary file is promoted.
    """

    errors = list(spec.validate())
    if not allow_network:
        errors.append("network acquisition is disabled; pass --allow-network explicitly")
    if errors:
        return AcquisitionReceipt(spec.artifact_id, "blocked", None, tuple(dict.fromkeys(errors)))
    destination = _within(Path(artifact_root), spec.local_filename)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.name}.part")
    parsed = urllib.parse.urlparse(spec.source.canonical_uri)
    try:
        request = urllib.request.Request(
            spec.source.canonical_uri,
            headers={"User-Agent": "tp-smoke-detect-artifact-verifier/1"},
        )
        context = ssl.create_default_context()
        with urllib.request.urlopen(request, timeout=timeout_seconds, context=context) as response:
            final = urllib.parse.urlparse(response.geturl())
            if final.scheme != _HTTPS or final.hostname != parsed.hostname:
                raise ValueError("redirected outside the canonical HTTPS origin")
            with temporary.open("wb") as stream:
                total = 0
                while chunk := response.read(1024 * 1024):
                    total += len(chunk)
                    if spec.artifact_size_bytes is not None and total > spec.artifact_size_bytes:
                        raise ValueError("download exceeded expected artifact size")
                    stream.write(chunk)
        actual = _hash_file(temporary)
        if actual != spec.artifact_sha256 or temporary.stat().st_size != spec.artifact_size_bytes:
            raise ValueError("downloaded artifact hash or size does not match manifest")
        os.replace(temporary, destination)
    except (OSError, ValueError, urllib.error.URLError) as error:
        temporary.unlink(missing_ok=True)
        return AcquisitionReceipt(spec.artifact_id, "blocked", None, (str(error),))
    return AcquisitionReceipt(spec.artifact_id, "verified", spec.local_filename, ())


__all__ = [
    "AcquisitionReceipt",
    "AcquisitionRecord",
    "ArtifactVerification",
    "EngineBinding",
    "LicenseDisposition",
    "ModelArtifact",
    "ModelRepositoryManifest",
    "acquire_model_artifact",
    "verify_local_artifact",
]
