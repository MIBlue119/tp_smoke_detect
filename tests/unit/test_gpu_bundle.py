from pathlib import Path

from scripts.gpu_receipts import validate_one_stream_receipt
from scripts.verify_gpu_bundle import verify_bundle

ROOT = Path(__file__).parents[2]


def test_checked_in_gpu_bundle_is_blocked_until_real_receipts_exist() -> None:
    receipt = verify_bundle(
        ROOT / "deploy/image-pins.yaml",
        ROOT / "model-repository/manifest/model-release.json",
    )

    assert receipt["status"] == "blocked"
    assert receipt["offline"] is True
    assert any("digest" in error or "model manifest" in error for error in receipt["errors"])


def test_one_stream_receipt_cannot_bypass_unresolved_models(tmp_path: Path) -> None:
    one_stream = tmp_path / "one-stream.json"
    one_stream.write_text('{"status":"one-stream-ready"}\n', encoding="utf-8")
    receipt = verify_bundle(
        ROOT / "deploy/image-pins.yaml",
        ROOT / "model-repository/manifest/model-release.json",
        one_stream_receipt=one_stream,
    )

    assert receipt["status"] == "blocked"
    assert receipt["one_stream"]["status"] == "one-stream-ready"


def test_gpu_compose_has_internal_isolated_services() -> None:
    compose = (ROOT / "deploy/compose.yaml").read_text(encoding="utf-8")

    assert "gpu-rtx3090" in compose
    assert "baseline-model" in compose
    assert "candidate-consumer" in compose
    assert "gpu-readiness" in compose
    assert "internal: true" in compose
    assert "capabilities: [gpu]" in compose
    assert "./../model-repository:/models:ro" in compose
    assert "./triton-entrypoint.sh:/opt/smoke-detect/bin/triton-entrypoint:ro" in compose
    assert "gpu_runtime_state:/run/gpu-state" in compose
    assert "SMOKE_GPU_CANDIDATE_READY_FILE: /run/gpu-state/candidate-ready" in compose
    assert "/opt/smoke-detect/bin/media-publisher" in compose
    assert "test -r /run/gpu-state/candidate-ready" in compose
    assert "SMOKE_GPU_IMAGE_DIGEST" in compose
    assert "gpu_receipt_signing_key" in compose
    assert "mode: 0400" in compose


def test_gpu_compose_preflight_fails_before_docker_when_secret_is_absent(tmp_path: Path) -> None:
    import os
    import subprocess

    env = os.environ.copy()
    env["SMOKE_GPU_IMAGE_DIGEST"] = "sha256:" + "a" * 64
    env["SMOKE_GPU_RECEIPT_SIGNING_KEY_FILE"] = str(tmp_path / "missing-key")
    result = subprocess.run(
        [str(ROOT / "scripts/preflight_gpu_compose.sh")],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 78
    assert "receipt signing key is unreadable" in result.stderr


def test_readiness_receipt_requires_bound_runtime_identity_and_freshness() -> None:
    errors = validate_one_stream_receipt(
        {
            "schema_version": "gpu.one-stream-receipt.v1",
            "status": "one-stream-ready",
            "manifest_sha256": "a" * 64,
            "image_digest": "sha256:" + "b" * 64,
            "source_qualification": "metadata-only",
            "generated_at": "2020-01-01T00:00:00Z",
        },
        manifest_sha256="a" * 64,
        image_digest="sha256:" + "b" * 64,
    )
    assert any("real runtime" in error for error in errors)
    assert any("stale" in error for error in errors)
