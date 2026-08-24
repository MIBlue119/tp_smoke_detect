"""Machine-readable qualification receipt tests for GPU-107."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from scripts.qualify_gpu import main, replay_probe

ROOT = Path(__file__).parents[2]


def test_default_receipt_is_blocked_on_cpu_or_unqualified_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "sys.argv",
        [
            "qualify_gpu.py",
            "--no-pull",
            "--output",
            str(tmp_path / "receipt.json"),
            "--markdown-output",
            str(tmp_path / "receipt.md"),
        ],
    )
    assert main() == 1
    receipt = json.loads((tmp_path / "receipt.json").read_text(encoding="utf-8"))
    assert receipt["schema_version"] == "gpu.qualification-receipt.v1"
    assert receipt["status"] == "blocked"
    assert receipt["qualification_label"] == "unqualified"
    assert receipt["errors"]
    assert (tmp_path / "receipt.md").is_file()


def test_replay_manifest_hash_is_bound_even_when_gate_fails() -> None:
    receipt = replay_probe(ROOT / "tests/load/replay-pack.yaml", 1, 0.0, 10.0)
    assert len(receipt["manifest_sha256"]) == 64
    assert receipt["status"] == "blocked"
