"""Deterministic, privacy-preserving dataset manifests and grouped splits."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

NAMED_CLASSES: tuple[str, ...] = (
    "smoking",
    "betel_quid",
    "phone",
    "drink",
    "food",
    "nose_touch",
    "pen_toothpick",
    "steam",
    "vape",
    "heated_tobacco",
    "background",
    "unknown",
)

# Stable vocabulary for named hard negatives.  A free-form label is not
# promotion-safe because it cannot be compared across sealed evaluations.
HARD_NEGATIVE_TAXONOMY: tuple[str, ...] = (
    "nose_touch",
    "betel_quid",
    "phone",
    "drink",
    "food",
    "pen_toothpick",
    "steam",
    "vape",
    "heated_tobacco",
    "occlusion",
    "out_of_coverage",
)
DATASET_CLASSES: tuple[str, ...] = NAMED_CLASSES + ("occlusion", "out_of_coverage")
_UNKNOWN_LICENSES = {"", "unknown", "undocumented", "proprietary-unknown"}
_UNKNOWN_DISPOSITIONS = {"", "unknown", "undocumented", "not_recorded"}


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def sha256_file(path: str | Path) -> str:
    """Return the SHA-256 digest of a local file without copying it into a manifest."""

    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


@dataclass(frozen=True, slots=True)
class DatasetItem:
    """One metadata-only event/frame reference.

    ``source_hash`` identifies content held in an approved local store. It is
    intentionally not a filesystem path or URL, so manifests are safe to commit.
    """

    item_id: str
    event_id: str
    camera_id: str
    track_id: str
    label: str
    source_hash: str
    license: str
    source: str
    timestamp_ns: int = 0
    duration_ms: int = 0
    eligible: bool = True
    metadata: Mapping[str, str] = field(default_factory=dict)
    person_id: str | None = None
    capture_day: str | None = None
    cohort: str = "organic"

    def __post_init__(self) -> None:
        for name in (
            "item_id",
            "event_id",
            "camera_id",
            "track_id",
            "source_hash",
            "license",
            "source",
        ):
            if not getattr(self, name).strip():
                raise ValueError(f"{name} must not be empty")
        if self.source.startswith(("http://", "https://", "/")) or "/" in self.source:
            raise ValueError("source must be a catalogue identifier, not a URL or path")
        if len(self.source_hash) != 64 or any(
            c not in "0123456789abcdef" for c in self.source_hash.lower()
        ):
            raise ValueError("source_hash must be a SHA-256 hex digest")
        if self.license.strip().lower() in _UNKNOWN_LICENSES:
            raise ValueError("license must be documented")
        if self.timestamp_ns < 0 or self.duration_ms < 0:
            raise ValueError("timestamps and durations must be non-negative")
        if self.cohort not in {"organic", "staged", "unknown"}:
            raise ValueError("cohort must be organic, staged, or unknown")
        if self.person_id is not None and not self.person_id.strip():
            raise ValueError("person_id must not be empty when provided")
        if self.capture_day is not None and not self.capture_day.strip():
            raise ValueError("capture_day must not be empty when provided")

    def to_dict(self) -> dict[str, Any]:
        return {
            "item_id": self.item_id,
            "event_id": self.event_id,
            "camera_id": self.camera_id,
            "track_id": self.track_id,
            "label": self.label,
            "source_hash": self.source_hash,
            "license": self.license,
            "source": self.source,
            "timestamp_ns": self.timestamp_ns,
            "duration_ms": self.duration_ms,
            "eligible": self.eligible,
            "metadata": dict(sorted(self.metadata.items())),
            "person_id": self.person_id,
            "capture_day": self.capture_day,
            "cohort": self.cohort,
        }


@dataclass(frozen=True, slots=True)
class DatasetGovernance:
    """Explicit provenance, privacy, consent, and retention dispositions."""

    license: str
    retention_disposition: str
    privacy_disposition: str
    provenance_disposition: str
    consent_basis: str
    source_catalogue: str
    media_store: str = "approved-local-store"

    def validate(self) -> tuple[str, ...]:
        errors: list[str] = []
        if self.license.strip().lower() in _UNKNOWN_LICENSES:
            errors.append("governance.license must be documented")
        if self.retention_disposition.strip().lower() in _UNKNOWN_DISPOSITIONS:
            errors.append("governance.retention_disposition is required")
        if self.privacy_disposition.strip().lower() in _UNKNOWN_DISPOSITIONS:
            errors.append("governance.privacy_disposition is required")
        if self.provenance_disposition not in {"approved", "conditional", "rejected"}:
            errors.append(
                "governance.provenance_disposition must be approved, conditional, or rejected"
            )
        elif self.provenance_disposition != "approved":
            errors.append("governance.provenance_disposition must be approved for registration")
        if self.consent_basis.strip().lower() in _UNKNOWN_DISPOSITIONS:
            errors.append("governance.consent_basis is required")
        if self.source_catalogue.strip().lower() in _UNKNOWN_DISPOSITIONS:
            errors.append("governance.source_catalogue is required")
        if self.media_store.startswith(("http://", "https://")) or "/" in self.media_store:
            errors.append("governance.media_store must be a catalogue name, not a path or URL")
        return tuple(errors)

    def to_dict(self) -> dict[str, str]:
        return {
            "license": self.license,
            "retention_disposition": self.retention_disposition,
            "privacy_disposition": self.privacy_disposition,
            "provenance_disposition": self.provenance_disposition,
            "consent_basis": self.consent_basis,
            "source_catalogue": self.source_catalogue,
            "media_store": self.media_store,
        }


@dataclass(frozen=True, slots=True)
class DatasetSplit:
    """A deterministic split containing item IDs, not media."""

    name: str
    item_ids: tuple[str, ...]
    camera_ids: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "item_ids": list(self.item_ids),
            "camera_ids": list(self.camera_ids),
        }


@dataclass(frozen=True, slots=True)
class DatasetManifest:
    dataset_id: str
    version: str
    items: tuple[DatasetItem, ...]
    classes: tuple[str, ...] = NAMED_CLASSES
    splits: tuple[DatasetSplit, ...] = ()
    manifest_sha256: str = ""
    governance: DatasetGovernance | None = None

    def __post_init__(self) -> None:
        if not self.dataset_id.strip() or not self.version.strip():
            raise ValueError("dataset_id and version must not be empty")
        if len({item.item_id for item in self.items}) != len(self.items):
            raise ValueError("item_id values must be unique")
        if any(item.label not in self.classes for item in self.items):
            raise ValueError("item label is not in the manifest classes")

    def payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "dataset_id": self.dataset_id,
            "version": self.version,
            "classes": list(self.classes),
            "items": [
                item.to_dict() for item in sorted(self.items, key=lambda value: value.item_id)
            ],
            "splits": [
                split.to_dict() for split in sorted(self.splits, key=lambda value: value.name)
            ],
        }
        if self.governance is not None:
            payload["governance"] = self.governance.to_dict()
        return payload

    @property
    def digest(self) -> str:
        return hashlib.sha256(_canonical(self.payload()).encode("utf-8")).hexdigest()

    def to_dict(self) -> dict[str, Any]:
        result = self.payload()
        result["manifest_sha256"] = self.digest
        return result

    def to_json(self) -> str:
        return _canonical(self.to_dict()) + "\n"

    def write(self, path: str | Path) -> None:
        Path(path).write_text(self.to_json(), encoding="utf-8")


def build_manifest(
    dataset_id: str,
    version: str,
    records: Iterable[DatasetItem | Mapping[str, Any]],
    *,
    classes: tuple[str, ...] = DATASET_CLASSES,
) -> DatasetManifest:
    """Build a sorted manifest from records and reject unsupported provenance."""

    items = tuple(
        record if isinstance(record, DatasetItem) else DatasetItem(**record) for record in records
    )
    return DatasetManifest(dataset_id, version, items, classes=classes)


def split_manifest(
    manifest: DatasetManifest,
    *,
    ratios: Mapping[str, float] | None = None,
    seed: int = 0,
) -> DatasetManifest:
    """Split by camera group, keeping every track from a camera in one split.

    The assignment is hash based rather than RNG-state based; adding a record
    cannot reshuffle existing camera groups and repeated runs are byte stable.
    ``seed`` is included in the hash to support an intentional, reproducible
    reshuffle for a new manifest version.
    """

    selected = dict(ratios or {"train": 0.7, "validation": 0.15, "sealed_test": 0.15})
    if not selected or any(value <= 0 for value in selected.values()):
        raise ValueError("split ratios must be positive")
    total = sum(selected.values())
    if abs(total - 1.0) > 1e-9:
        raise ValueError("split ratios must sum to 1")
    names = tuple(selected)
    cameras = sorted({item.camera_id for item in manifest.items})
    groups: dict[str, list[DatasetItem]] = {camera: [] for camera in cameras}
    for item in manifest.items:
        groups[item.camera_id].append(item)
    cumulative: list[tuple[str, float]] = []
    cursor = 0.0
    for name in names:
        cursor += selected[name]
        cumulative.append((name, cursor))
    grouped: dict[str, list[DatasetItem]] = {name: [] for name in names}
    for camera in cameras:
        digest = hashlib.sha256(f"{seed}:{manifest.dataset_id}:{camera}".encode()).hexdigest()
        unit = int(digest[:16], 16) / 2**64
        split_name = next(name for name, threshold in cumulative if unit < threshold)
        grouped[split_name].extend(groups[camera])
    splits = tuple(
        DatasetSplit(
            name,
            tuple(sorted(item.item_id for item in grouped[name])),
            tuple(sorted({item.camera_id for item in grouped[name]})),
        )
        for name in names
    )
    return DatasetManifest(
        manifest.dataset_id,
        manifest.version,
        manifest.items,
        manifest.classes,
        splits,
        manifest.manifest_sha256,
        manifest.governance,
    )


def validate_split_leakage(manifest: DatasetManifest) -> tuple[str, ...]:
    """Return leakage errors for camera, track, person, and capture-day groups."""

    item_split: dict[str, str] = {}
    for split in manifest.splits:
        for item_id in split.item_ids:
            if item_id in item_split:
                return (f"item {item_id} appears in multiple splits",)
            item_split[item_id] = split.name
    items = {item.item_id: item for item in manifest.items}
    dimensions: dict[str, dict[str, set[str]]] = {
        "camera_id": {},
        "track_id": {},
        "person_id": {},
        "capture_day": {},
    }
    for item_id, split_name in item_split.items():
        item = items.get(item_id)
        if item is None:
            return (f"split references unknown item {item_id}",)
        values = {
            "camera_id": item.camera_id,
            "track_id": item.track_id,
            "person_id": item.person_id,
            "capture_day": item.capture_day,
        }
        for dimension, value in values.items():
            if value is not None:
                dimensions[dimension].setdefault(value, set()).add(split_name)
    return tuple(
        f"{dimension} {value} crosses splits {sorted(split_names)}"
        for dimension, values in dimensions.items()
        for value, split_names in values.items()
        if len(split_names) > 1
    )


def validate_registration(manifest: DatasetManifest) -> tuple[str, ...]:
    """Validate all metadata-only gates required before dataset promotion."""

    errors = list(
        manifest.governance.validate()
        if manifest.governance is not None
        else ("dataset governance is required for registration",)
    )
    errors.extend(validate_split_leakage(manifest))
    if any(item.cohort == "unknown" for item in manifest.items):
        errors.append("item cohort must be organic or staged")
    return tuple(dict.fromkeys(errors))


def register_dataset(
    dataset_id: str,
    version: str,
    records: Iterable[DatasetItem | Mapping[str, Any]],
    *,
    governance: DatasetGovernance,
    classes: tuple[str, ...] = DATASET_CLASSES,
    ratios: Mapping[str, float] | None = None,
    seed: int = 0,
) -> DatasetManifest:
    """Create a registered manifest and fail closed on missing dispositions."""

    manifest = split_manifest(
        build_manifest(dataset_id, version, records, classes=classes),
        ratios=ratios,
        seed=seed,
    )
    registered = DatasetManifest(
        manifest.dataset_id,
        manifest.version,
        manifest.items,
        manifest.classes,
        manifest.splits,
        manifest.manifest_sha256,
        governance,
    )
    errors = validate_registration(registered)
    if errors:
        raise ValueError("dataset registration blocked: " + "; ".join(errors))
    return registered
