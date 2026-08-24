from __future__ import annotations

import hashlib
import os
from pathlib import Path

import ml.demo.models as models
import pytest
from ml.demo.export import compare_outputs
from ml.demo.models import (
    Detection,
    InferenceOutput,
    ModelAdapterError,
    ModelSpec,
    runtime_fingerprint,
    runtime_receipt,
    sanitized_environment,
)


def _spec(tmp_path: Path, role: str = "person_pose") -> ModelSpec:
    model = tmp_path / "model.pt"
    model.write_bytes(b"checkpoint")
    return ModelSpec(
        role=role,
        revision="pinned-r1",
        path=model,
        sha256=hashlib.sha256(b"checkpoint").hexdigest(),
        size_bytes=len(b"checkpoint"),
    )


def test_model_spec_rejects_changed_bytes_before_loader(tmp_path: Path) -> None:
    spec = _spec(tmp_path)
    spec.path.write_bytes(b"tampered")
    with pytest.raises(ModelAdapterError, match="size mismatch"):
        spec.validate()


def test_host_loader_rejects_pickle_and_accepts_onnx_without_pickle_load(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    checkpoint = tmp_path / "model.pt"
    checkpoint.write_bytes(b"pickle")
    spec = ModelSpec("person_pose", "r1", checkpoint, hashlib.sha256(b"pickle").hexdigest(), 6)
    with pytest.raises(ModelAdapterError, match="refuses pickle"):
        models._load_yolo(spec, enforce_isolation=False)
    safe = tmp_path / "model.onnx"
    safe.write_bytes(b"onnx")
    safe_spec = ModelSpec("person_pose", "r1", safe, hashlib.sha256(b"onnx").hexdigest(), 4)
    sentinel = object()
    monkeypatch.setattr("ultralytics.YOLO", lambda *args, **kwargs: sentinel)
    assert models._load_yolo(safe_spec, enforce_isolation=False) is sentinel


def test_sanitized_environment_removes_secret_names() -> None:
    clean = sanitized_environment(
        {"PATH": "/bin", "OPENAI_API_KEY": "secret", "COOKIE_FILE": "cookie"}
    )
    assert "OPENAI_API_KEY" not in clean
    assert "COOKIE_FILE" not in clean
    assert clean["DEMO_ISOLATED_RUNTIME"] == "1"
    assert clean["DEMO_NETWORK_DISABLED"] == "1"


def test_runtime_receipt_does_not_accept_cpu_or_fake_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Cuda:
        @staticmethod
        def is_available() -> bool:
            return False

    class Torch:
        cuda = Cuda()

    monkeypatch.setattr("ml.demo.models._torch", lambda: Torch())
    with pytest.raises(ModelAdapterError, match="CUDA is unavailable"):
        runtime_receipt()


def test_parity_is_conservative_and_bounded() -> None:
    detection = Detection((0.1, 0.1, 0.2, 0.2), 0.9, 0)
    reference = InferenceOutput("cigarette_detector", "r1", (detection,), 1.0, "cuda:0")
    same = compare_outputs([reference], [reference], backend="pytorch")
    assert same.status == "passed"
    changed = InferenceOutput(
        "cigarette_detector", "r1", (Detection((0.5, 0.5, 0.6, 0.6), 0.9, 0),), 1.0, "cuda:0"
    )
    failed = compare_outputs([reference], [changed], backend="onnx")
    assert failed.status == "failed"
    assert failed.max_box_delta is not None and failed.max_box_delta > 0.02


def test_runtime_fingerprint_is_stable() -> None:
    class Receipt:
        framework = "pytorch"
        framework_version = "2.9.1"
        cuda_version = "12.8"
        device_name = "RTX 3090"
        compute_capability = "8.6"
        device_index = 0

    first = runtime_fingerprint(Receipt())  # type: ignore[arg-type]
    assert first == runtime_fingerprint(Receipt())  # type: ignore[arg-type]
    assert len(first) == 64


def test_isolation_flags_are_not_inherited_from_parent_secrets() -> None:
    assert all(
        "KEY" not in key for key in sanitized_environment(os.environ) if key == "OPENAI_API_KEY"
    )
