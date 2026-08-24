"""GPU-107 harness contract tests; no test here claims target hardware."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from scripts.qualify_gpu import parse_telemetry, replay_probe, run_runtime

ROOT = Path(__file__).parents[2]


def test_checked_in_replay_pack_is_explicitly_blocked() -> None:
    receipt = replay_probe(ROOT / "tests/load/replay-pack.yaml", 1, 0.0, 10.0)
    assert receipt["status"] == "blocked"
    assert any("not sealed" in error for error in receipt["errors"])
    assert any("clip_sha256" in error for error in receipt["errors"])


def test_telemetry_summary_is_bounded_and_numeric() -> None:
    summary = parse_telemetry(
        [
            json.dumps({"camera_id": "camera-01", "queue_depth": 2, "candidate_latency_ms": 120}),
            json.dumps({"camera_id": "camera-01", "queue_depth": 4, "candidate_latency_ms": 240}),
            "not telemetry",
        ]
    )
    assert summary["samples"] == 2
    assert summary["queue_depth"]["max"] == 4
    assert summary["candidate_latency_ms"]["avg"] == 180
    assert "camera_id" in summary["fields"]


def test_runtime_timeout_is_recorded_without_claiming_success() -> None:
    result, metrics = run_runtime(
        [sys.executable, "-c", "import time; print('{}', flush=True); time.sleep(1)"],
        timeout=0.1,
        sample_interval=0.1,
    )
    assert result.status == "timeout"
    assert result.returncode is not None
    assert metrics["samples"] == 0
