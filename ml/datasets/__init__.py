"""Manifest-only dataset tooling for the local evaluation pipeline.

This package deliberately stores identifiers and hashes, never camera media.
"""

from .manifest import (
    DatasetItem,
    DatasetManifest,
    DatasetSplit,
    build_manifest,
    split_manifest,
)

__all__ = [
    "DatasetItem",
    "DatasetManifest",
    "DatasetSplit",
    "build_manifest",
    "split_manifest",
]
