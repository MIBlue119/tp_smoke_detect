"""Structured model-card and provenance report generation.

Reports describe the release inputs and evidence state.  They intentionally do
not infer accuracy, legality, or hardware qualification from a model name.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ml.evaluation.ablation import AblationReport, CalibrationResult
from ml.registry.release import ModelReleaseManifest


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


@dataclass(frozen=True, slots=True)
class ModelCard:
    model_id: str
    version: str
    intended_use: str
    limitations: tuple[str, ...]
    release: dict[str, Any]
    evaluation: dict[str, Any]
    calibration: dict[str, Any]
    evidence_level: str = "fixture_only"

    def payload(self) -> dict[str, Any]:
        return {
            "schema_version": "ml.model-card.v1",
            "model_id": self.model_id,
            "version": self.version,
            "intended_use": self.intended_use,
            "limitations": list(self.limitations),
            "release": self.release,
            "evaluation": self.evaluation,
            "calibration": self.calibration,
            "evidence_level": self.evidence_level,
        }

    @property
    def report_sha256(self) -> str:
        return hashlib.sha256(_canonical(self.payload()).encode()).hexdigest()

    def to_dict(self) -> dict[str, Any]:
        payload = self.payload()
        payload["report_sha256"] = self.report_sha256
        return payload

    def to_json(self) -> str:
        return _canonical(self.to_dict()) + "\n"

    def write(self, path: str | Path) -> None:
        Path(path).write_text(self.to_json(), encoding="utf-8")


def build_model_card(
    release: ModelReleaseManifest,
    *,
    evaluation: AblationReport,
    calibration: CalibrationResult,
    intended_use: str = (
        "Metadata-only smoking-behaviour evidence classification; not identity or enforcement."
    ),
    limitations: tuple[str, ...] = (
        "Fixture and metadata-only evidence; no site-camera accuracy claim.",
        "No model weights, GPU runtime, or private media are included in this artifact.",
        "Automatic audio requires a separate silent-period site acceptance gate.",
    ),
) -> ModelCard:
    return ModelCard(
        release.model_id,
        release.version,
        intended_use,
        limitations,
        release.to_dict(),
        evaluation.to_dict(),
        calibration.to_dict(),
    )


def build_provenance_report(
    release: ModelReleaseManifest, *, model_card: ModelCard
) -> dict[str, Any]:
    """Return a machine-readable provenance receipt for audit tooling."""

    return {
        "schema_version": "ml.provenance-report.v1",
        "release_sha256": release.release_sha256,
        "model_card_sha256": model_card.report_sha256,
        "model_id": release.model_id,
        "version": release.version,
        "origin": release.provenance.origin,
        "license": release.provenance.license,
        "decision": release.provenance.decision,
        "source_hash": release.provenance.source_hash,
        "parent_checkpoint": release.parent_checkpoint,
        "dependencies": list(release.provenance.dependencies),
        "runtime_profile": release.runtime_profile,
        "evidence_level": model_card.evidence_level,
        "qualification_state": "lab_required",
    }


def write_provenance_report(path: str | Path, report: dict[str, Any]) -> None:
    Path(path).write_text(_canonical(report) + "\n", encoding="utf-8")


__all__ = [
    "ModelCard",
    "build_model_card",
    "build_provenance_report",
    "write_provenance_report",
]
