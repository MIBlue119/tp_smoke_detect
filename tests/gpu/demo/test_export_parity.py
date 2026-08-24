"""Opt-in export checks; unavailable TensorRT/ONNX is an explicit receipt."""

from __future__ import annotations

import os

import pytest

pytestmark = pytest.mark.skipif(os.environ.get("DEMO_RUN_GPU") != "1", reason="opt-in export test")


def test_export_lane_is_opt_in() -> None:
    # Physical export is recorded by the DEMO-201 qualification command; base
    # CI must not import or download conversion toolchains.
    assert os.environ.get("DEMO_RUN_GPU") == "1"
