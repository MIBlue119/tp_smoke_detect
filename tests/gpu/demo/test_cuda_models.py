"""Opt-in real CUDA tests for sealed local demo artifacts.

Normal CI skips these tests.  Set DEMO_RUN_GPU=1 in a sanitized, offline
environment to prove real model execution on the approved host.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from ml.demo.acquisition import sha256_file
from ml.demo.models import CigaretteCudaAdapter, ModelSpec, PoseCudaAdapter, runtime_receipt

INPUTS = Path(os.environ.get("DEMO_INPUT_ROOT", ".local-demo-inputs"))


def _real_gpu_ready() -> bool:
    if os.environ.get("DEMO_RUN_GPU") != "1":
        return False
    try:
        import torch  # type: ignore[import-not-found]

        return bool(torch.cuda.is_available())
    except ImportError:
        return False


pytestmark = pytest.mark.skipif(not _real_gpu_ready(), reason="opt-in sealed CUDA test")


def test_sealed_models_execute_on_cuda() -> None:
    import cv2  # type: ignore[import-not-found]

    # Decode the first frame; this remains outside the CPU-safe test path.
    capture = cv2.VideoCapture(str(INPUTS / "source" / "shibuya-GByZa0qbA8A.mp4"))
    ok, frame = capture.read()
    capture.release()
    assert ok and frame is not None
    pose_path = INPUTS / "models" / "yolo11n-pose-v8.3.0.pt"
    cigarette_path = INPUTS / "models" / "heiher-smoking-detection-12a54cda.pt"
    pose = PoseCudaAdapter(
        ModelSpec(
            "person_pose",
            "ultralytics-assets-release-v8.3.0/yolo11n-pose.pt",
            pose_path,
            sha256_file(pose_path),
            pose_path.stat().st_size,
        )
    )
    cigarette = CigaretteCudaAdapter(
        ModelSpec(
            "cigarette_detector",
            "12a54cda2ca031e2b96a486bc288e957e56f51c8",
            cigarette_path,
            sha256_file(cigarette_path),
            cigarette_path.stat().st_size,
        )
    )
    pose_result = pose.infer(frame)
    cigarette_result = cigarette.infer(frame)
    assert pose_result.device == cigarette_result.device == "cuda:0"
    assert pose.receipt().fake_provider is False
    assert cigarette.receipt().fake_provider is False
    assert runtime_receipt().device_name
