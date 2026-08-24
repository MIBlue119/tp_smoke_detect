"""Shared fail-closed validation for GPU readiness receipt lineage."""

from __future__ import annotations

import hashlib
import hmac
import json
from datetime import UTC, datetime, timedelta
from typing import Any


def canonical_receipt_bytes(value: Any) -> bytes:
    """Return the stable bytes used for receipt lineage and signatures."""

    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()


def receipt_sha256(value: Any) -> str:
    return hashlib.sha256(canonical_receipt_bytes(value)).hexdigest()


def build_one_stream_receipt(
    qualification: dict[str, Any], *, signing_key: bytes | None = None
) -> dict[str, Any]:
    """Derive the readiness receipt only from a successful qualification receipt.

    The source receipt is embedded so consumers can hash exactly what was
    qualified; callers cannot replace it with a free-form source label.
    """

    source = json.loads(json.dumps(qualification, sort_keys=True))
    source_digest = receipt_sha256(source)
    ready = (
        source.get("schema_version") == "gpu.qualification-receipt.v1"
        and source.get("status") == "qualified"
        and source.get("requested_gate", {}).get("streams") == 1
        and source.get("runtime", {}).get("status") == "passed"
    )
    result: dict[str, Any] = {
        "schema_version": "gpu.one-stream-receipt.v1",
        "status": "one-stream-ready" if ready else "blocked",
        "generated_at": source.get("generated_at"),
        "source_qualification": source,
        "source_qualification_schema": "gpu.qualification-receipt.v1",
        "source_qualification_sha256": source_digest,
        "manifest_sha256": source.get("models", {}).get("manifest_sha256"),
        "image_digest": source.get("image", {}).get("digest"),
        "runtime": source.get("runtime", {}).get("identity", {}),
        "host": source.get("host", {}).get("identity", {}),
        "media_root_policy": source.get("replay", {}).get("media_root_policy"),
    }
    if signing_key:
        result["signature_hmac_sha256"] = hmac.new(
            signing_key, canonical_receipt_bytes(result), hashlib.sha256
        ).hexdigest()
    return result


def validate_one_stream_receipt(
    value: Any,
    *,
    manifest_sha256: str | None = None,
    image_digest: str | None = None,
    now: datetime | None = None,
    max_age: timedelta = timedelta(hours=24),
    signing_key: bytes | None = None,
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
    source = value.get("source_qualification")
    if not isinstance(source, dict):
        errors.append("one-stream receipt must embed its qualification source receipt")
        errors.append("one-stream receipt is not bound to the real runtime")
    else:
        if source.get("schema_version") != "gpu.qualification-receipt.v1":
            errors.append("one-stream source receipt has the wrong schema")
        if source.get("status") != "qualified":
            errors.append("one-stream source receipt is not qualified")
        if source.get("requested_gate", {}).get("streams") != 1:
            errors.append("one-stream source receipt is not a one-stream qualification")
        if source.get("runtime", {}).get("status") != "passed":
            errors.append("one-stream source runtime did not pass")
        if value.get("source_qualification_sha256") != receipt_sha256(source):
            errors.append("one-stream source receipt hash does not match embedded lineage")
    source_schema = value.get("source_qualification_schema")
    if source_schema != "gpu.qualification-receipt.v1":
        errors.append("one-stream source qualification schema is missing")
    source_digest = value.get("source_qualification_sha256")
    if not isinstance(source_digest, str) or len(source_digest) != 64:
        errors.append("one-stream source qualification hash is missing")
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
    if signing_key is not None:
        signature = value.get("signature_hmac_sha256")
        unsigned = dict(value)
        unsigned.pop("signature_hmac_sha256", None)
        expected = hmac.new(
            signing_key, canonical_receipt_bytes(unsigned), hashlib.sha256
        ).hexdigest()
        if not isinstance(signature, str) or not hmac.compare_digest(signature, expected):
            errors.append("one-stream receipt signature is invalid")
    return list(dict.fromkeys(errors))


__all__ = [
    "build_one_stream_receipt",
    "canonical_receipt_bytes",
    "receipt_sha256",
    "validate_one_stream_receipt",
]
