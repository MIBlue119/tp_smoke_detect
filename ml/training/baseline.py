"""CPU-safe frozen-feature baseline artifact and export/load verification.

This module represents a model release without shipping weights.  The feature
vector is supplied by an approved local provider and the baseline head is a
deterministic named-class score table.  It is useful for contract tests and
calibration rehearsals; it is not an accuracy claim for site cameras.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ml.datasets.manifest import NAMED_CLASSES


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


@dataclass(frozen=True, slots=True)
class BaselineConfig:
    model_id: str = "frozen-named-class-head"
    version: str = "0.1.0"
    embedding_revision: str = "fixture-embedding-v1"
    crop_strategy: str = "person_crop"
    classes: tuple[str, ...] = NAMED_CLASSES

    def __post_init__(self) -> None:
        if (
            not self.model_id.strip()
            or not self.version.strip()
            or not self.embedding_revision.strip()
        ):
            raise ValueError("model_id, version, and embedding_revision are required")
        if not self.classes:
            raise ValueError("classes must not be empty")


@dataclass(frozen=True, slots=True)
class BaselineArtifact:
    config: BaselineConfig
    weights: tuple[tuple[str, float], ...]
    artifact_sha256: str = ""

    def payload(self) -> dict[str, Any]:
        return {
            "schema_version": "ml.baseline-artifact.v1",
            "model_id": self.config.model_id,
            "version": self.config.version,
            "embedding_revision": self.config.embedding_revision,
            "crop_strategy": self.config.crop_strategy,
            "classes": list(self.config.classes),
            "weights": {key: value for key, value in self.weights},
        }

    @property
    def digest(self) -> str:
        return hashlib.sha256(_canonical(self.payload()).encode()).hexdigest()

    def to_dict(self) -> dict[str, Any]:
        payload = self.payload()
        payload["artifact_sha256"] = self.digest
        return payload

    def to_json(self) -> str:
        return _canonical(self.to_dict()) + "\n"

    def write(self, path: str | Path) -> None:
        Path(path).write_text(self.to_json(), encoding="utf-8")

    def predict(self, features: Mapping[str, float]) -> dict[str, float]:
        """Return stable scores from a metadata feature mapping."""

        outputs: dict[str, float] = {}
        for label, bias in self.weights:
            score = bias
            for name, value in sorted(features.items()):
                if not isinstance(value, (int, float)) or not 0 <= value <= 1:
                    raise ValueError(f"feature {name!r} must be a finite score in [0, 1]")
                digest = hashlib.sha256(f"{label}:{name}".encode()).digest()
                score += (digest[0] / 255 - 0.5) * float(value) * 0.1
            outputs[label] = round(min(1.0, max(0.0, score)), 8)
        return outputs


def build_baseline(
    *,
    config: BaselineConfig | None = None,
    weights: Mapping[str, float] | None = None,
) -> BaselineArtifact:
    selected = config or BaselineConfig()
    values = dict(weights or {label: 0.5 for label in selected.classes})
    if set(values) != set(selected.classes):
        raise ValueError("weights must contain exactly the configured classes")
    if any(not 0 <= value <= 1 for value in values.values()):
        raise ValueError("weights must be in [0, 1]")
    return BaselineArtifact(
        selected,
        tuple(sorted((key, float(value)) for key, value in values.items())),
    )


def export_baseline(
    path: str | Path,
    *,
    config: BaselineConfig | None = None,
    weights: Mapping[str, float] | None = None,
) -> BaselineArtifact:
    artifact = build_baseline(config=config, weights=weights)
    artifact.write(path)
    return artifact


def load_baseline(path: str | Path) -> BaselineArtifact:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or raw.get("schema_version") != "ml.baseline-artifact.v1":
        raise ValueError("unsupported baseline artifact")
    expected = raw.pop("artifact_sha256", None)
    config = BaselineConfig(
        model_id=str(raw["model_id"]),
        version=str(raw["version"]),
        embedding_revision=str(raw["embedding_revision"]),
        crop_strategy=str(raw["crop_strategy"]),
        classes=tuple(str(item) for item in raw["classes"]),
    )
    weights_raw = raw["weights"]
    if not isinstance(weights_raw, dict):
        raise ValueError("baseline weights must be an object")
    artifact = BaselineArtifact(
        config,
        tuple(sorted((str(key), float(value)) for key, value in weights_raw.items())),
    )
    if expected != artifact.digest:
        raise ValueError("baseline artifact checksum mismatch")
    return artifact


def verify_export_load(
    artifact: BaselineArtifact,
    samples: Sequence[Mapping[str, float]],
    path: str | Path,
) -> bool:
    """Export, load, and compare deterministic outputs for proof tests."""

    artifact.write(path)
    loaded = load_baseline(path)
    return all(artifact.predict(sample) == loaded.predict(sample) for sample in samples)


__all__ = [
    "BaselineArtifact",
    "BaselineConfig",
    "build_baseline",
    "export_baseline",
    "load_baseline",
    "verify_export_load",
]
