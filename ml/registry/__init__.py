"""Immutable release metadata and promotion gates."""

from .model_card import (
    ModelCard,
    build_model_card,
    build_provenance_report,
    write_provenance_report,
)
from .model_repository import (
    AcquisitionReceipt,
    AcquisitionRecord,
    ArtifactVerification,
    EngineBinding,
    LicenseDisposition,
    ModelArtifact,
    ModelRepositoryManifest,
    acquire_model_artifact,
    verify_local_artifact,
)
from .release import (
    ModelReleaseManifest,
    PromotionResult,
    Provenance,
    validate_promotion,
    validate_release_manifest,
)

__all__ = [
    "ModelCard",
    "ModelReleaseManifest",
    "PromotionResult",
    "Provenance",
    "build_model_card",
    "build_provenance_report",
    "validate_promotion",
    "validate_release_manifest",
    "write_provenance_report",
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
