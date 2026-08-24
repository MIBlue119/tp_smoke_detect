"""Shared fail-closed validation for GPU readiness receipt lineage."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any


def validate_one_stream_receipt(
    value: Any,
    *,
    manifest_sha256: str | None = None,
    image_digest: str | None = None,
    now: datetime | None = None,
    max_age: timedelta = timedelta(hours=24),
) -> list[str]:
    errors: list[str] = []
    if not isinstance(value, dict):
        return ["one-stream receipt must be a JSON object"]
    if value.get("schema_version") != "gpu.one-stream-receipt.v1":
        errors.append("one-stream receipt schema_version is not gpu.one-stream-receipt.v1")
    if value.get("status") != "one-stream-ready":
        errors.append("one-stream receipt is not one-stream-ready")
    if manifest_sha256 is not None and value.get("manifest_sha256") != manifest_sha256:
        errors.append("one-stream receipt manifest hash does not match active manifest")
    if image_digest is not None and value.get("image_digest") != image_digest:
        errors.append("one-stream receipt image digest does not match pinned image")
    if value.get("source_qualification") != "real-runtime":
        errors.append("one-stream receipt is not bound to the real runtime")
    runtime = value.get("runtime")
    if not isinstance(runtime, dict) or not all(
        isinstance(runtime.get(key), str) and runtime[key].strip()
        for key in ("deepstream", "tensorrt", "triton")
    ):
        errors.append("one-stream receipt runtime identity is incomplete")
    host = value.get("host")
    if not isinstance(host, dict) or not all(
        isinstance(host.get(key), str) and host[key].strip()
        for key in ("gpu", "driver", "compute_capability")
    ):
        errors.append("one-stream receipt host identity is incomplete")
    generated = value.get("generated_at")
    if not isinstance(generated, str):
        errors.append("one-stream receipt generated_at is required")
    else:
        try:
            generated_at = datetime.fromisoformat(generated.replace("Z", "+00:00"))
            current = now or datetime.now(UTC)
            if generated_at.tzinfo is None or current - generated_at > max_age:
                errors.append("one-stream receipt is stale")
            if generated_at - current > timedelta(minutes=5):
                errors.append("one-stream receipt is from the future")
        except ValueError:
            errors.append("one-stream receipt generated_at is invalid")
    return list(dict.fromkeys(errors))


__all__ = ["validate_one_stream_receipt"]
