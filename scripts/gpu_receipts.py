"""Shared fail-closed validation for GPU readiness receipt lineage."""

from __future__ import annotations

import base64
import hashlib
import json
from datetime import UTC, datetime, timedelta
from typing import Any, cast

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

QUALIFICATION_REQUIRED_KEYS = {
    "schema_version",
    "ticket",
    "generated_at",
    "status",
    "qualification_label",
    "profile",
    "claim_boundary",
    "requested_gate",
    "host",
    "image",
    "models",
    "replay",
    "runtime",
    "telemetry",
    "fault_injection",
    "commands",
    "errors",
    "recovery_steps",
}
EXECUTOR_ATTESTATION_SCHEMA = "gpu.executor-attestation.v1"


def _is_non_empty_string(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _is_sha256(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    candidate = value.removeprefix("sha256:")
    return len(candidate) == 64 and all(char in "0123456789abcdef" for char in candidate)


def validate_qualification_source(source: Any) -> list[str]:
    """Validate the complete, executor-produced source before receipt derivation.

    A signature authenticates bytes, not meaning.  This structural gate is
    intentionally strict so a caller cannot sign a small hand-written object
    containing only the fields used by the old readiness predicate.
    """

    errors: list[str] = []
    if not isinstance(source, dict):
        return ["qualification source must be a JSON object"]
    missing = sorted(QUALIFICATION_REQUIRED_KEYS - set(source))
    if missing:
        errors.append(f"qualification source is incomplete: missing {', '.join(missing)}")
    if source.get("schema_version") != "gpu.qualification-receipt.v1":
        errors.append("qualification source schema_version is invalid")
    if source.get("ticket") != "GPU-107":
        errors.append("qualification source ticket is invalid")
    if source.get("status") != "qualified":
        errors.append("qualification source is not qualified")
    if source.get("errors") != []:
        errors.append("qualification source errors must be empty")
    gate = source.get("requested_gate")
    if (
        not isinstance(gate, dict)
        or gate.get("streams") != 1
        or gate.get("real_runtime") is not True
    ):
        errors.append("qualification source must be a real one-stream gate")

    host = source.get("host")
    host_identity = host.get("identity") if isinstance(host, dict) else None
    if not isinstance(host_identity, dict) or not all(
        _is_non_empty_string(host_identity.get(key))
        for key in ("gpu", "driver", "compute_capability")
    ):
        errors.append("qualification source host identity is incomplete")

    image = source.get("image")
    if not isinstance(image, dict) or not _is_non_empty_string(image.get("image")):
        errors.append("qualification source image identity is missing")
    if not isinstance(image, dict) or not _is_sha256(image.get("digest")):
        errors.append("qualification source image digest is missing")

    models = source.get("models")
    if not isinstance(models, dict) or models.get("status") != "ready":
        errors.append("qualification source model readiness is missing")
    if not isinstance(models, dict) or not _is_sha256(models.get("manifest_sha256")):
        errors.append("qualification source model manifest hash is missing")

    replay = source.get("replay")
    if not isinstance(replay, dict) or replay.get("status") != "ready":
        errors.append("qualification source replay is not sealed and ready")
    if not isinstance(replay, dict) or not _is_sha256(replay.get("manifest_sha256")):
        errors.append("qualification source replay manifest hash is missing")
    policy = replay.get("media_root_policy") if isinstance(replay, dict) else None
    if not isinstance(policy, dict) or not _is_sha256(policy.get("root_sha256")):
        errors.append("qualification source replay media-root binding is missing")

    runtime = source.get("runtime")
    runtime_metrics = runtime.get("metrics") if isinstance(runtime, dict) else None
    runtime_command = runtime.get("command") if isinstance(runtime, dict) else None
    if not isinstance(runtime, dict) or runtime.get("status") != "passed":
        errors.append("qualification source runtime did not pass")
    if not isinstance(runtime_command, dict) or runtime_command.get("status") != "passed":
        errors.append("qualification source runtime command evidence is missing")
    if not isinstance(runtime_metrics, dict):
        errors.append("qualification source runtime metrics are missing")

    telemetry = source.get("telemetry")
    provenance = telemetry.get("provenance") if isinstance(telemetry, dict) else None
    capture = telemetry.get("executor_capture") if isinstance(telemetry, dict) else None
    if not isinstance(telemetry, dict) or telemetry.get("status") not in {None, "passed"}:
        errors.append("qualification source telemetry status is invalid")
    if not isinstance(provenance, dict) or provenance.get("trusted") is not True:
        errors.append("qualification source telemetry is not trusted runtime evidence")
    if not isinstance(capture, dict) or capture.get("trusted") is not True:
        errors.append("qualification source telemetry executor capture is not trusted")
    if not isinstance(capture, dict) or not all(
        _is_non_empty_string(capture.get(key)) for key in ("host_binding", "runtime_binding")
    ):
        errors.append("qualification source telemetry process/host binding is missing")
    if not isinstance(telemetry, dict) or not isinstance(telemetry.get("cameras"), dict):
        errors.append("qualification source per-camera telemetry is missing")
    elif len(telemetry["cameras"]) != 1:
        errors.append("qualification source must contain exactly one camera telemetry row")

    fault = source.get("fault_injection")
    fault_result = fault.get("result") if isinstance(fault, dict) else None
    if not isinstance(fault, dict) or fault.get("status") != "passed":
        errors.append("qualification source fault-isolation evidence is missing")
    if not isinstance(fault_result, dict) or fault_result.get("status") != "passed":
        errors.append("qualification source fault command evidence is missing")
    if not isinstance(fault_result, dict) or not _is_non_empty_string(
        fault_result.get("attestation")
    ):
        errors.append("qualification source fault attestation is missing")

    commands = source.get("commands")
    if not isinstance(commands, list) or not commands:
        errors.append("qualification source command evidence is missing")
    recovery = source.get("recovery_steps")
    if not isinstance(recovery, list) or not recovery:
        errors.append("qualification source recovery evidence is missing")
    attestation = source.get("executor_attestation")
    if not isinstance(attestation, dict):
        errors.append("qualification source executor attestation is missing")
    else:
        if attestation.get("schema_version") != EXECUTOR_ATTESTATION_SCHEMA:
            errors.append("qualification source executor attestation schema is invalid")
        if not _is_non_empty_string(attestation.get("signing_key_id")):
            errors.append("qualification source executor attestation key identity is missing")
        if not _is_sha256(attestation.get("source_sha256")):
            errors.append("qualification source executor attestation source hash is missing")
        if not isinstance(attestation.get("evidence"), dict):
            errors.append("qualification source executor attestation evidence is missing")
        if not _is_non_empty_string(attestation.get("signature_ed25519")):
            errors.append("qualification source executor attestation signature is missing")
    return list(dict.fromkeys(errors))


def canonical_receipt_bytes(value: Any) -> bytes:
    """Return the stable bytes used for receipt lineage and signatures."""

    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()


def receipt_sha256(value: Any) -> str:
    return hashlib.sha256(canonical_receipt_bytes(value)).hexdigest()


def canonical_telemetry_bytes(value: Any) -> bytes:
    """Canonicalize a telemetry row without its detached authentication tag."""

    if not isinstance(value, dict):
        return canonical_receipt_bytes(value)
    unsigned = dict(value)
    unsigned.pop("signature_ed25519", None)
    return canonical_receipt_bytes(unsigned)


def _private_key(value: bytes | Ed25519PrivateKey) -> Ed25519PrivateKey:
    if isinstance(value, Ed25519PrivateKey):
        return value
    if len(value) == 32:
        return Ed25519PrivateKey.from_private_bytes(value)
    loaded = serialization.load_pem_private_key(value, password=None)
    if not isinstance(loaded, Ed25519PrivateKey):
        raise ValueError("key is not an Ed25519 private key")
    return loaded


def _public_key(value: bytes | Ed25519PublicKey) -> Ed25519PublicKey:
    if isinstance(value, Ed25519PublicKey):
        return value
    if len(value) == 32:
        return Ed25519PublicKey.from_public_bytes(value)
    loaded = serialization.load_pem_public_key(value)
    if not isinstance(loaded, Ed25519PublicKey):
        raise ValueError("key is not an Ed25519 public key")
    return loaded


def sign_ed25519(value: Any, signing_key: bytes | Ed25519PrivateKey) -> str:
    """Sign canonical JSON with an Ed25519 private key, encoded as base64."""

    return base64.b64encode(_private_key(signing_key).sign(canonical_receipt_bytes(value))).decode(
        "ascii"
    )


def public_key_for_private(
    signing_key: bytes | Ed25519PrivateKey,
) -> Ed25519PublicKey:
    """Derive only the verifier half for an in-process host handoff."""

    return _private_key(signing_key).public_key()


def verify_ed25519(value: Any, signature: Any, verify_key: bytes | Ed25519PublicKey) -> bool:
    """Verify a canonical JSON Ed25519 signature without accepting shared secrets."""

    if not isinstance(signature, str):
        return False
    try:
        signature_bytes = base64.b64decode(signature.encode("ascii"), validate=True)
        _public_key(verify_key).verify(signature_bytes, canonical_receipt_bytes(value))
    except (ValueError, TypeError, InvalidSignature):
        return False
    return True


def _without_executor_attestation(source: Any) -> dict[str, Any] | None:
    if not isinstance(source, dict):
        return None
    unsigned = cast(dict[str, Any], json.loads(json.dumps(source, sort_keys=True)))
    unsigned.pop("executor_attestation", None)
    return unsigned


def sign_executor_artifact(
    source: dict[str, Any],
    evidence: dict[str, Any],
    *,
    signing_key: bytes | Ed25519PrivateKey,
    signing_key_id: str = "gpu-executor",
) -> dict[str, Any]:
    """Attach executor-owned evidence signed after independent cross-checks.

    The signed payload binds the complete qualification source (without this
    detached field) and the raw evidence.  The readiness builder verifies this
    with a separate verifier key before it can issue a readiness receipt.
    """

    unsigned = _without_executor_attestation(source)
    if unsigned is None:
        raise ValueError("qualification source must be an object")
    payload: dict[str, Any] = {
        "schema_version": EXECUTOR_ATTESTATION_SCHEMA,
        "signing_key_id": signing_key_id,
        "source_sha256": receipt_sha256(unsigned),
        "evidence_sha256": receipt_sha256(evidence),
        "evidence": json.loads(json.dumps(evidence, sort_keys=True)),
    }
    payload["signature_ed25519"] = sign_ed25519(payload, signing_key)
    return payload


def verify_executor_artifact(
    source: Any,
    *,
    verify_key: bytes | Ed25519PublicKey | None,
    signing_key_id: str = "gpu-executor",
) -> list[str]:
    """Verify detached executor evidence and its source/evidence relationships."""

    errors: list[str] = []
    if verify_key is None:
        return ["executor attestation verifier key is unavailable"]
    if not isinstance(source, dict):
        return ["qualification source must be an object"]
    attestation = source.get("executor_attestation")
    if not isinstance(attestation, dict):
        return ["qualification source executor attestation is missing"]
    if attestation.get("schema_version") != EXECUTOR_ATTESTATION_SCHEMA:
        errors.append("executor attestation schema is invalid")
    if attestation.get("signing_key_id") != signing_key_id:
        errors.append("executor attestation key identity is invalid")
    evidence = attestation.get("evidence")
    if not isinstance(evidence, dict):
        errors.append("executor attestation evidence is missing")
    else:
        if attestation.get("evidence_sha256") != receipt_sha256(evidence):
            errors.append("executor attestation evidence hash does not match")
        if evidence.get("schema_version") != "gpu.executor-evidence.v1":
            errors.append("executor evidence schema is invalid")
        if evidence.get("trusted") is not True:
            errors.append("executor evidence is not trusted")
        for key in ("host_binding", "runtime_binding", "runtime_identity", "samples_sha256"):
            if not _is_non_empty_string(evidence.get(key)):
                errors.append(f"executor evidence {key} is missing")
        if not isinstance(evidence.get("host_samples"), list) or not evidence["host_samples"]:
            errors.append("executor evidence host samples are missing")
        if not isinstance(evidence.get("service_metrics_sha256"), str):
            errors.append("executor evidence service metrics hash is missing")
        if evidence.get("fault_invariants") is not True:
            errors.append("executor fault invariants were not cross-checked")
    unsigned_attestation = dict(attestation)
    signature = unsigned_attestation.pop("signature_ed25519", None)
    if verify_key is None or not verify_ed25519(unsigned_attestation, signature, verify_key):
        errors.append("executor attestation signature is invalid")
    unsigned_source = _without_executor_attestation(source)
    if unsigned_source is None or attestation.get("source_sha256") != receipt_sha256(
        unsigned_source
    ):
        errors.append("executor attestation source hash does not match")
    return list(dict.fromkeys(errors))


def build_one_stream_receipt(
    qualification: dict[str, Any],
    *,
    readiness_signing_key: bytes | Ed25519PrivateKey | None = None,
    signing_key_id: str = "gpu-qualification",
    executor_verify_key: bytes | Ed25519PublicKey | None = None,
    executor_signing_key_id: str = "gpu-executor",
) -> dict[str, Any]:
    """Derive the readiness receipt only from a successful qualification receipt.

    The source receipt is embedded so consumers can hash exactly what was
    qualified; callers cannot replace it with a free-form source label.
    """

    source = json.loads(json.dumps(qualification, sort_keys=True))
    source_errors = validate_qualification_source(source)
    source_errors.extend(
        verify_executor_artifact(
            source,
            verify_key=executor_verify_key,
            signing_key_id=executor_signing_key_id,
        )
    )
    source_digest = receipt_sha256(source)
    ready = readiness_signing_key is not None and not source_errors
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
    if source_errors:
        result["validation_errors"] = source_errors
    if readiness_signing_key:
        result["signature_key_id"] = signing_key_id
        result["signature_ed25519"] = sign_ed25519(result, readiness_signing_key)
    return result


def validate_one_stream_receipt(
    value: Any,
    *,
    manifest_sha256: str | None = None,
    image_digest: str | None = None,
    now: datetime | None = None,
    max_age: timedelta = timedelta(hours=24),
    readiness_verify_key: bytes | Ed25519PublicKey | None = None,
    signing_key_id: str = "gpu-qualification",
    executor_verify_key: bytes | Ed25519PublicKey | None = None,
    executor_signing_key_id: str = "gpu-executor",
) -> list[str]:
    errors: list[str] = []
    if not isinstance(value, dict):
        return ["one-stream receipt must be a JSON object"]
    if value.get("schema_version") != "gpu.one-stream-receipt.v1":
        errors.append("one-stream receipt schema_version is not gpu.one-stream-receipt.v1")
    if value.get("status") != "one-stream-ready":
        errors.append("one-stream receipt is not one-stream-ready")
    if readiness_verify_key is None:
        errors.append("one-stream receipt requires a trusted signing key")
    if manifest_sha256 is not None and value.get("manifest_sha256") != manifest_sha256:
        errors.append("one-stream receipt manifest hash does not match active manifest")
    if image_digest is not None and value.get("image_digest") != image_digest:
        errors.append("one-stream receipt image digest does not match pinned image")
    source = value.get("source_qualification")
    if not isinstance(source, dict):
        errors.append("one-stream receipt must embed its qualification source receipt")
        errors.append("one-stream receipt is not bound to the real runtime")
    else:
        errors.extend(validate_qualification_source(source))
        errors.extend(
            verify_executor_artifact(
                source,
                verify_key=executor_verify_key,
                signing_key_id=executor_signing_key_id,
            )
        )
        if source.get("schema_version") != "gpu.qualification-receipt.v1":
            errors.append("one-stream source receipt has the wrong schema")
        if source.get("status") != "qualified":
            errors.append("one-stream source receipt is not qualified")
        if source.get("requested_gate", {}).get("streams") != 1:
            errors.append("one-stream source receipt is not a one-stream qualification")
        if source.get("runtime", {}).get("status") != "passed":
            errors.append("one-stream source runtime did not pass")
        if source.get("replay", {}).get("status") != "ready":
            errors.append("one-stream source replay is not sealed and ready")
        if source.get("errors"):
            errors.append("one-stream source qualification contains errors")
        if source.get("telemetry", {}).get("provenance", {}).get("trusted") is not True:
            errors.append("one-stream source telemetry is not trusted runtime evidence")
        if source.get("fault_injection", {}).get("status") != "passed":
            errors.append("one-stream source fault-isolation evidence is missing")
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
    if readiness_verify_key is not None:
        signature = value.get("signature_ed25519")
        if value.get("signature_key_id") != signing_key_id:
            errors.append("one-stream receipt signature key identity is invalid")
        unsigned = dict(value)
        unsigned.pop("signature_ed25519", None)
        if not verify_ed25519(unsigned, signature, readiness_verify_key):
            errors.append("one-stream receipt signature is invalid")
    return list(dict.fromkeys(errors))


__all__ = [
    "build_one_stream_receipt",
    "canonical_receipt_bytes",
    "canonical_telemetry_bytes",
    "receipt_sha256",
    "public_key_for_private",
    "sign_ed25519",
    "verify_ed25519",
    "sign_executor_artifact",
    "verify_executor_artifact",
    "validate_qualification_source",
    "validate_one_stream_receipt",
]
