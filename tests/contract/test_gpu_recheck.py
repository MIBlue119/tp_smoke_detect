"""Adversarial probes for the GPU delivery and qualification boundaries."""

from __future__ import annotations

import json
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from scripts.gpu_receipts import (
    build_one_stream_receipt,
    sign_ed25519,
    validate_one_stream_receipt,
    verify_ed25519,
)
from scripts.qualify_gpu import (
    evaluate_telemetry,
    load_approved_runtime_manifest,
    parse_telemetry,
    replay_probe,
)


def test_qualification_requires_an_approved_media_root() -> None:
    result = replay_probe(Path("tests/load/replay-pack.yaml"), 1, 0, 10, None)
    assert result["status"] == "blocked"
    assert any("media_root is required" in error for error in result["errors"])


def test_short_self_declared_per_camera_telemetry_cannot_qualify() -> None:
    lines = [
        json.dumps(
            {
                "camera_id": f"camera-{index}",
                "scheduled_samples": 100,
                "processed_samples": 100,
                "dropped_samples": 0,
                "queue_depth": 0,
                "queue_age_ms": 0,
                "candidate_latency_ms": 1,
                "analysis_fps": 10,
            }
        )
        for index in range(20)
    ]
    errors = evaluate_telemetry(
        parse_telemetry(lines),
        streams=20,
        duration_seconds=0.01,
        required_duration_seconds=7200,
        analysis_fps=10,
    )
    assert any("duration" in error for error in errors)
    assert any("gpu_utilization" in error for error in errors)
    assert any("sample count" in error for error in errors)


def test_one_stream_receipt_requires_hashed_qualification_lineage() -> None:
    errors = validate_one_stream_receipt(
        {
            "schema_version": "gpu.one-stream-receipt.v1",
            "status": "one-stream-ready",
            "generated_at": "2026-08-24T00:00:00Z",
            "source_qualification": "real-runtime",
        }
    )
    assert any("embedded lineage" in error or "source receipt" in error for error in errors)


def test_minimal_signed_source_cannot_be_promoted_to_one_stream_ready() -> None:
    source = {
        "schema_version": "gpu.qualification-receipt.v1",
        "status": "qualified",
        "requested_gate": {"streams": 1},
        "runtime": {"status": "passed"},
        "replay": {"status": "ready"},
        "telemetry": {"provenance": {"trusted": True}},
        "fault_injection": {"status": "passed"},
    }
    receipt = build_one_stream_receipt(source, readiness_signing_key=Ed25519PrivateKey.generate())

    assert receipt["status"] == "blocked"
    assert receipt["validation_errors"]
    assert validate_one_stream_receipt(
        receipt,
        readiness_verify_key=Ed25519PrivateKey.generate().public_key(),
    )


def test_caller_injected_samples_never_satisfy_executor_capture_gate() -> None:
    metrics = parse_telemetry(
        [
            json.dumps(
                {
                    "schema_version": "gpu.telemetry.v1",
                    "producer": "tp.smoke-detect.gpu-runtime",
                    "receipt_id": "forged",
                    "runtime_pid": 123,
                    "sequence": 0,
                    "timestamp_ns": 1,
                    "camera_id": "camera-01",
                    "counters": {
                        "scheduled_samples": 100,
                        "processed_samples": 100,
                        "dropped_samples": 0,
                    },
                    "measurements": [{"timestamp_ns": 1}],
                    "model_revisions": {
                        "person_detector": "x",
                        "pose_landmarker": "x",
                        "hand_landmarker": "x",
                        "crop_classifier": "x",
                    },
                    "fault_isolation": {
                        "status": "passed",
                        "producer": "tp.smoke-detect.fault-harness",
                        "receipt_id": "forged",
                    },
                }
            )
        ],
        expected_runtime_pid=123,
        expected_runtime_start_ticks="1",
        expected_host_binding="host",
        expected_challenge="challenge",
        verify_key=Ed25519PrivateKey.generate().public_key().public_bytes_raw(),
    )
    metrics["external_gpu_samples"] = [{"gpu_utilization": 100}]

    errors = evaluate_telemetry(
        metrics,
        streams=1,
        duration_seconds=10,
        analysis_fps=10,
    )

    assert any("authenticated qualification-executor" in error for error in errors)


def test_readiness_rejects_fabricated_dict_without_executor_attestation() -> None:
    source = {
        "schema_version": "gpu.qualification-receipt.v1",
        "ticket": "GPU-107",
        "generated_at": "2026-08-24T00:00:00Z",
        "status": "qualified",
        "qualification_label": "gpu-lab-qualified",
        "profile": "rtx3090",
        "claim_boundary": "lab",
        "requested_gate": {"streams": 1, "real_runtime": True},
        "host": {"identity": {"gpu": "RTX 3090", "driver": "580", "compute_capability": "8.6"}},
        "image": {"image": "runtime@sha256:" + "a" * 64, "digest": "a" * 64},
        "models": {"status": "ready", "manifest_sha256": "b" * 64},
        "replay": {
            "status": "ready",
            "manifest_sha256": "c" * 64,
            "media_root_policy": {"root_sha256": "d" * 64},
        },
        "runtime": {"status": "passed", "command": {"status": "passed"}, "metrics": {}},
        "telemetry": {
            "provenance": {"trusted": True},
            "executor_capture": {"trusted": True, "host_binding": "h", "runtime_binding": "r"},
            "cameras": {"camera-01": {}},
        },
        "fault_injection": {
            "status": "passed",
            "result": {"status": "passed", "attestation": "fake"},
        },
        "commands": [{"status": "passed"}],
        "errors": [],
        "recovery_steps": ["none"],
    }
    receipt = build_one_stream_receipt(source, readiness_signing_key=Ed25519PrivateKey.generate())
    assert receipt["status"] == "blocked"
    assert any("executor attestation" in error for error in receipt["validation_errors"])


def test_approved_manifest_is_required_for_qualifying_runtime(tmp_path: Path) -> None:
    manifest = tmp_path / "runtime.yaml"
    manifest.write_text(
        "schema_version: gpu.qualification-executor-manifest.v1\n"
        "profile: rtx3090\nqualifying: true\n"
        "image: runtime@sha256:" + "a" * 64 + "\n"
        "runtime:\n  identity: gpu-runtime-v1\n"
        "  command: [docker, run, --rm, 'runtime@sha256:" + "a" * 64 + "']\n"
        "faults: {}\n",
        encoding="utf-8",
    )
    _loaded, errors = load_approved_runtime_manifest(manifest, profile="rtx3090")
    assert any("canonical path" in error for error in errors)


def test_checked_in_manifest_is_the_only_qualifying_manifest() -> None:
    loaded, errors = load_approved_runtime_manifest(
        Path("configs/gpu-qualification-manifest.yaml"), profile="rtx3090"
    )
    assert errors == []
    assert loaded is not None
    assert loaded["runtime"]["identity"] == "tp-smoke-detect.gpu-runtime.v1"


def test_dev_manifest_cannot_be_used_for_qualification(tmp_path: Path) -> None:
    manifest = tmp_path / "dev.yaml"
    manifest.write_text(
        "schema_version: gpu.qualification-executor-manifest.v1\n"
        "profile: rtx3090\nqualifying: false\n"
        "image: runtime@sha256:" + "a" * 64 + "\n"
        "runtime:\n  identity: gpu-runtime-v1\n  command: [echo, dev]\n",
        encoding="utf-8",
    )
    _loaded, errors = load_approved_runtime_manifest(manifest, profile="rtx3090")
    assert any("canonical path" in error for error in errors)


def test_ed25519_public_verifier_cannot_forge_or_sign_with_public_material() -> None:
    private = Ed25519PrivateKey.generate()
    public = private.public_key()
    payload = {"status": "qualified", "profile": "rtx3090"}
    signature = sign_ed25519(payload, private)

    assert verify_ed25519(payload, signature, public)
    assert not verify_ed25519({**payload, "profile": "other"}, signature, public)
