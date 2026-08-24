"""Opt-in assertions for a completed physical Shibuya run.

The default CI path stays CPU-safe and skips this test.  Operators can point
``SHIBUYA_DEMO_RUN_ROOT`` at an ignored run directory after the RTX run.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from ml.demo.annotate import validate_media
from ml.demo.contracts import validate_video_annotation


@pytest.mark.skipif(
    not os.environ.get("SHIBUYA_DEMO_RUN_ROOT"), reason="physical GPU run is opt-in"
)
def test_completed_shibuya_run_reconciles_metadata_and_media() -> None:
    root = Path(os.environ["SHIBUYA_DEMO_RUN_ROOT"])
    annotation = root / "annotation.json"
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    payload = json.loads(annotation.read_text(encoding="utf-8"))
    assert validate_video_annotation(payload) == ()
    assert manifest["status"] == "completed"
    assert manifest["frames_processed"] == 1248
    assert manifest["event_count"] == len(payload["events"])
    assert manifest["audio_enabled"] is False
    assert manifest["production_decision_created"] is False

    receipt = validate_media(
        Path(os.environ["SHIBUYA_DEMO_SOURCE"]),
        annotation,
        root / "annotated.mp4",
    )
    assert receipt.output_bytes < 50_000_000
    assert receipt.audio_streams == 0
