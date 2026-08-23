"""A small, path-safe artifact store for replay and evaluation media.

The replay worker deals in artifact IDs rather than paths.  This adapter is
deliberately boring: an ID is a relative path below one configured root and
all filesystem resolution is checked after symlink expansion.  It therefore
cannot be used to accidentally read a camera host path or a remote URL.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO


class ArtifactError(Exception):
    """Base class for errors returned by the local artifact adapter."""


class ArtifactNotFoundError(ArtifactError):
    """The requested artifact does not exist below the registered root."""


class ArtifactOutsideRootError(ArtifactError):
    """An artifact ID would resolve outside the registered root."""


class UnsupportedArtifactError(ArtifactError):
    """The artifact extension is not a replay image format."""


SUPPORTED_IMAGE_SUFFIXES = frozenset({".bmp", ".jpeg", ".jpg", ".png", ".webp"})


@dataclass(frozen=True, slots=True)
class Artifact:
    """Metadata for an artifact registered in the local root."""

    artifact_id: str
    path: Path
    size: int


class LocalArtifactStore:
    """Read-only-by-default artifact access constrained to ``root``.

    ``register`` accepts a file supplied by a fixture creator and returns its
    root-relative ID.  Runtime reads should use ``read_bytes`` or ``open``;
    both perform the same containment check, including for symlinks.
    """

    def __init__(self, root: Path | str, *, create: bool = False) -> None:
        root_path = Path(root).expanduser()
        if create:
            root_path.mkdir(parents=True, exist_ok=True)
        if not root_path.exists() or not root_path.is_dir():
            raise ArtifactError(f"artifact root is not a directory: {root_path}")
        self.root = root_path.resolve(strict=True)

    def _resolve(self, artifact_id: str) -> Path:
        if not artifact_id or "\x00" in artifact_id:
            raise ArtifactOutsideRootError("artifact ID must be a non-empty relative path")
        candidate = (self.root / artifact_id).resolve(strict=False)
        try:
            candidate.relative_to(self.root)
        except ValueError as exc:
            raise ArtifactOutsideRootError(
                f"artifact is outside registered root: {artifact_id}"
            ) from exc
        return candidate

    def register(self, path: Path | str, *, artifact_id: str | None = None) -> Artifact:
        """Register an existing local file and return its safe relative ID."""

        source = Path(path).expanduser().resolve(strict=True)
        if not source.is_file():
            raise ArtifactNotFoundError(f"artifact is not a file: {path}")
        try:
            source.relative_to(self.root)
        except ValueError as exc:
            raise ArtifactOutsideRootError(f"artifact is outside registered root: {path}") from exc
        relative = artifact_id or source.relative_to(self.root).as_posix()
        target = self._resolve(relative)
        if target != source:
            raise ArtifactOutsideRootError("artifact ID does not point at the registered file")
        return Artifact(relative, target, target.stat().st_size)

    def locate(self, artifact_id: str, *, require_image: bool = False) -> Artifact:
        path = self._resolve(artifact_id)
        if not path.is_file():
            raise ArtifactNotFoundError(f"artifact not found: {artifact_id}")
        if require_image and path.suffix.lower() not in SUPPORTED_IMAGE_SUFFIXES:
            raise UnsupportedArtifactError(f"unsupported image format: {path.suffix or '<none>'}")
        return Artifact(artifact_id, path, path.stat().st_size)

    def read_bytes(self, artifact_id: str, *, require_image: bool = False) -> bytes:
        artifact = self.locate(artifact_id, require_image=require_image)
        return artifact.path.read_bytes()

    def open(self, artifact_id: str, *, require_image: bool = False) -> BinaryIO:
        artifact = self.locate(artifact_id, require_image=require_image)
        return artifact.path.open("rb")

    def exists(self, artifact_id: str) -> bool:
        try:
            return self._resolve(artifact_id).is_file()
        except ArtifactOutsideRootError:
            return False


__all__ = [
    "Artifact",
    "ArtifactError",
    "ArtifactNotFoundError",
    "ArtifactOutsideRootError",
    "LocalArtifactStore",
    "SUPPORTED_IMAGE_SUFFIXES",
    "UnsupportedArtifactError",
]
