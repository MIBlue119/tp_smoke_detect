from pathlib import Path

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
