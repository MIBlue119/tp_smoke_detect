"""GPU-107 harness contract tests; no test here claims target hardware."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from scripts.qualify_gpu import evaluate_telemetry, parse_telemetry, replay_probe, run_runtime

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
    assert "external_gpu_samples" not in metrics
    assert metrics["executor_capture"]["trusted"] is False


def test_runtime_does_not_expose_signing_key_or_challenge_to_child(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "must-not-cross")
    monkeypatch.setenv("QUALIFICATION_TOKEN", "must-not-cross")
    result, _metrics = run_runtime(
        [
            sys.executable,
            "-c",
            "import json, os; print(json.dumps(dict(os.environ)), flush=True)",
        ],
        timeout=1.0,
        sample_interval=0.1,
        executor_signing_key=Ed25519PrivateKey.generate(),
    )
    assert result.status == "passed"
    assert '"SMOKE_GPU_TELEMETRY_SIGNING_KEY_FILE"' not in result.stdout_tail
    assert '"SMOKE_GPU_TELEMETRY_CHALLENGE"' not in result.stdout_tail
    assert '"SMOKE_GPU_EXECUTOR_SIGNING_KEY_FILE"' not in result.stdout_tail
    assert "AWS_SECRET_ACCESS_KEY" not in result.stdout_tail
    assert "QUALIFICATION_TOKEN" not in result.stdout_tail


def test_telemetry_thresholds_reject_field_name_only_or_slow_runtime() -> None:
    metrics = parse_telemetry(
        [
            json.dumps(
                {
                    "camera_id": "camera-01",
                    "scheduled_samples": 100,
                    "processed_samples": 90,
                    "dropped_samples": 10,
                    "queue_depth": 2000,
                    "queue_age_ms": 9000,
                    "candidate_latency_ms": 9001,
                    "analysis_fps": 5,
                }
            )
        ]
    )
    errors = evaluate_telemetry(metrics, streams=1, duration_seconds=10, analysis_fps=10)
    assert any("99%" in error for error in errors)
    assert any("p95" in error for error in errors)
    assert any("queue depth" in error for error in errors)


def test_replay_probe_hashes_clip_bytes_and_rejects_tampering(tmp_path: Path) -> None:
    clip = tmp_path / "camera-01.mp4"
    clip.write_bytes(b"original")
    manifest = tmp_path / "sealed.yaml"
    manifest.write_text(
        "schema_version: gpu.replay-workload.v1\n"
        "qualification_state: sealed\nstream_count: 1\nduration_hours: 1\n"
        "streams:\n  - stream_id: stream-01\n    source_kind: external\n"
        "    source_path: camera-01.mp4\n    clip_sha256: " + "0" * 64 + "\n"
        "    analysis_fps: 10\n",
        encoding="utf-8",
    )
    receipt = replay_probe(manifest, 1, 0, 10, tmp_path)
    assert any("clip_sha256" in error and "match" in error for error in receipt["errors"])
