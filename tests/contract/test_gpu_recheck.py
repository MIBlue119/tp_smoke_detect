"""Adversarial probes for the GPU delivery and qualification boundaries."""

from __future__ import annotations

import json
from pathlib import Path

from scripts.gpu_receipts import validate_one_stream_receipt
from scripts.qualify_gpu import evaluate_telemetry, parse_telemetry, replay_probe


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
