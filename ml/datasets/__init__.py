"""Manifest-only dataset tooling for the local evaluation pipeline.

This package deliberately stores identifiers and hashes, never camera media.
"""

from .manifest import (
    DATASET_CLASSES,
    HARD_NEGATIVE_TAXONOMY,
    DatasetGovernance,
    DatasetItem,
    DatasetManifest,
    DatasetSplit,
    build_manifest,
    register_dataset,
    split_manifest,
    validate_registration,
    validate_split_leakage,
)

__all__ = [
    "DatasetGovernance",
    "DatasetItem",
    "DatasetManifest",
    "DatasetSplit",
    "HARD_NEGATIVE_TAXONOMY",
    "DATASET_CLASSES",
    "build_manifest",
    "register_dataset",
    "split_manifest",
    "validate_registration",
    "validate_split_leakage",
]
