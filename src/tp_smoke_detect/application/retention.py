"""Separate-clock retention with durable, idempotent deletion auditing."""

from __future__ import annotations

import contextlib
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Protocol

from ..observability.metrics import OperationalMetrics

MEDIA_CLASSES = ("raw_media", "event_clip", "metadata")


@dataclass(frozen=True)
class RetentionPolicy:
    """Retention periods in seconds for each independently governed class."""

    raw_media_seconds: int = 7 * 24 * 60 * 60
    event_clip_seconds: int = 30 * 24 * 60 * 60
    metadata_seconds: int = 365 * 24 * 60 * 60

    def __post_init__(self) -> None:
        if any(value < 0 for value in self.periods.values()):
            raise ValueError("retention periods must be non-negative")

    @property
    def periods(self) -> dict[str, int]:
        return {
            "raw_media": self.raw_media_seconds,
            "event_clip": self.event_clip_seconds,
            "metadata": self.metadata_seconds,
        }

    def cutoff(self, media_class: str, now: datetime) -> datetime:
        try:
            seconds = self.periods[media_class]
        except KeyError as exc:
            raise ValueError(f"unsupported media class: {media_class}") from exc
        return now - timedelta(seconds=seconds)


class RetentionRepository(Protocol):
    def list_retention_candidates(
        self, *, cutoffs: dict[str, datetime], now: datetime
    ) -> list[dict[str, Any]]: ...

    def finalize_artifact_deletion(
        self, artifact_id: str, *, media_class: str, deleted_at: datetime
    ) -> bool: ...

    def append_retention_audit(self, audit: dict[str, Any]) -> dict[str, Any]: ...


class ArtifactDeleter(Protocol):
    def delete(self, path: str) -> bool: ...


class FileArtifactDeleter:
    """Delete only files below a configured artifact root."""

    def __init__(self, root: Path | str) -> None:
        self.root = Path(root).expanduser().resolve()

    def delete(self, path: str) -> bool:
        candidate = (self.root / path).resolve()
        try:
            candidate.relative_to(self.root)
        except ValueError as exc:
            raise ValueError("artifact path escapes registered root") from exc
        try:
            candidate.unlink()
        except FileNotFoundError:
            # Missing files are an idempotent success.  The durable audit still
            # records that the retention job completed the deletion operation.
            return False
        return True


@dataclass(frozen=True)
class RetentionRunResult:
    scanned: int
    deleted: int
    already_missing: int
    failed: int
    metadata_deleted: int = 0


class RetentionManager:
    """Run one bounded retention pass; safe to retry after any partial failure."""

    def __init__(
        self,
        repository: RetentionRepository,
        deleter: ArtifactDeleter,
        policy: RetentionPolicy | None = None,
        *,
        metrics: OperationalMetrics | None = None,
    ) -> None:
        self.repository = repository
        self.deleter = deleter
        self.policy = policy or RetentionPolicy()
        self.metrics = metrics

    def run(self, now: datetime | None = None) -> RetentionRunResult:
        current = (now or datetime.now(UTC)).astimezone(UTC)
        cutoffs = {name: self.policy.cutoff(name, current) for name in MEDIA_CLASSES}
        candidates = self.repository.list_retention_candidates(cutoffs=cutoffs, now=current)
        deleted = 0
        already_missing = 0
        failed = 0
        for artifact in candidates:
            artifact_id = str(artifact["artifact_id"])
            media_class = str(artifact.get("media_class", "event_clip"))
            if media_class not in MEDIA_CLASSES[:2]:
                # Metadata is retained in the audit store, not filesystem
                # artifacts; an adapter may handle it through the optional hook.
                continue
            try:
                deleted_now = self.deleter.delete(str(artifact["path"]))
                if deleted_now is False:
                    already_missing += 1
                self.repository.finalize_artifact_deletion(
                    artifact_id, media_class=media_class, deleted_at=current
                )
                self._metric(media_class, "deleted")
                deleted += 1
            except Exception as exc:  # noqa: BLE001 - one artifact must not stop the pass
                failed += 1
                self._metric(media_class, "failed")
                with contextlib.suppress(Exception):
                    self.repository.append_retention_audit(
                        {
                            "artifact_id": artifact_id,
                            "media_class": media_class,
                            "action": "delete",
                            "status": "failed",
                            "error": type(exc).__name__,
                            "created_at": current,
                        }
                    )
                    # The next run retries the artifact.  Do not conceal the
                    # original error behind an audit-store error.
        metadata_deleted = self._delete_metadata(cutoffs["metadata"], current)
        return RetentionRunResult(
            scanned=len(candidates),
            deleted=deleted,
            already_missing=already_missing,
            failed=failed,
            metadata_deleted=metadata_deleted,
        )

    def _delete_metadata(self, cutoff: datetime, now: datetime) -> int:
        method = getattr(self.repository, "delete_expired_metadata", None)
        if method is None:
            return 0
        try:
            count = int(method(cutoff=cutoff, now=now))
            self._metric("metadata", "deleted")
            return count
        except Exception:
            self._metric("metadata", "failed")
            return 0

    def _metric(self, media_class: str, status: str) -> None:
        if self.metrics:
            self.metrics.retention(media_class, status)


__all__ = [
    "ArtifactDeleter",
    "FileArtifactDeleter",
    "MEDIA_CLASSES",
    "RetentionManager",
    "RetentionPolicy",
    "RetentionRepository",
    "RetentionRunResult",
]
