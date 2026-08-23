"""Bounded qualification scaffolding; real capacity stays an explicit gate."""

from __future__ import annotations

import os

import pytest


@pytest.mark.gpu
def test_target_capacity_gate_is_opt_in() -> None:
    """Never report a fake 20-camera pass on a developer CPU host."""

    if os.getenv("SMOKE_RUN_GPU_QUALIFICATION") != "1":
        pytest.skip("target hardware qualification is not enabled")
    pytest.fail(
        "GPU qualification harness is intentionally not implemented in the CPU profile; "
        "run the target-host receipt and attach measured evidence"
    )
