"""Real, provenance-bound YOLO CUDA adapters for the private demo.

This module is intentionally lazy: the base installation never imports
PyTorch, Ultralytics, OpenCV, or model weights.  A caller must explicitly opt
into the isolated conversion/runtime boundary before a pickle-bearing
checkpoint can be loaded.  The adapter returns typed, normalized detections;
it never emits a production decision or invokes audio.
"""

from __future__ import annotations

import hashlib
import os
import platform
import re
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from .acquisition import sha256_file
from .contracts import RuntimeReceipt

_SENSITIVE_ENV = re.compile(
    r"(?:secret|token|password|passwd|cookie|credential|api[_-]?key|access[_-]?key)",
    re.IGNORECASE,
)
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class ModelAdapterError(RuntimeError):
    """A model cannot be loaded or cannot prove a real CUDA execution."""


@dataclass(frozen=True, slots=True)
class ModelSpec:
    """Immutable model identity required before a checkpoint is loaded."""

    role: str
    revision: str
    path: Path
    sha256: str
    size_bytes: int
    class_names: tuple[str, ...] = ()

    def validate(self) -> None:
        if not self.role.strip() or not self.revision.strip():
            raise ModelAdapterError("model role and revision are required")
        if _SHA256.fullmatch(self.sha256) is None:
            raise ModelAdapterError("model sha256 must be a lowercase SHA-256 digest")
        if self.size_bytes <= 0:
            raise ModelAdapterError("model size_bytes must be positive")
        if not self.path.is_file():
            raise ModelAdapterError(f"model checkpoint is missing: {self.path.name}")
        actual_size = self.path.stat().st_size
        if actual_size != self.size_bytes:
            raise ModelAdapterError(
                f"model size mismatch: expected {self.size_bytes}, got {actual_size}"
            )
        actual_hash = sha256_file(self.path)
        if actual_hash != self.sha256:
            raise ModelAdapterError("model checkpoint SHA-256 does not match sealed receipt")


@dataclass(frozen=True, slots=True)
class Detection:
    """One normalized model detection; coordinates are x1,y1,x2,y2 in [0,1]."""

    box: tuple[float, float, float, float]
    confidence: float
    class_id: int
    keypoints: tuple[tuple[float, float, float], ...] = ()


@dataclass(frozen=True, slots=True)
class InferenceOutput:
    role: str
    model_revision: str
    detections: tuple[Detection, ...]
    elapsed_ms: float
    device: str


@dataclass(frozen=True, slots=True)
class AdapterReceipt:
    role: str
    model_revision: str
    artifact_sha256: str
    device: str
    frames: int
    elapsed_ms: float
    peak_vram_bytes: int
    fake_provider: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "role": self.role,
            "model_revision": self.model_revision,
            "artifact_sha256": self.artifact_sha256,
            "device": self.device,
            "frames": self.frames,
            "elapsed_ms": self.elapsed_ms,
            "peak_vram_bytes": self.peak_vram_bytes,
            "fake_provider": self.fake_provider,
        }


def sanitized_environment(base: Mapping[str, str] | None = None) -> dict[str, str]:
    """Return an environment suitable for a no-secret model-load subprocess."""

    source = dict(os.environ if base is None else base)
    clean = {key: value for key, value in source.items() if _SENSITIVE_ENV.search(key) is None}
    clean.update(
        {
            "DEMO_ISOLATED_RUNTIME": "1",
            "DEMO_NETWORK_DISABLED": "1",
            "PYTHONNOUSERSITE": "1",
        }
    )
    return clean


def require_isolated_environment() -> None:
    """Fail closed unless the caller explicitly proves the loader boundary."""

    if os.environ.get("DEMO_ISOLATED_RUNTIME") != "1":
        raise ModelAdapterError("checkpoint loading requires DEMO_ISOLATED_RUNTIME=1")
    if os.environ.get("DEMO_NETWORK_DISABLED") != "1":
        raise ModelAdapterError("checkpoint loading requires DEMO_NETWORK_DISABLED=1")
    leaked = sorted(key for key in os.environ if _SENSITIVE_ENV.search(key))
    if leaked:
        raise ModelAdapterError("sensitive environment variables leaked into model loader")


def _torch() -> Any:
    try:
        import torch
    except ImportError as exc:
        raise ModelAdapterError("PyTorch is required only in the optional GPU environment") from exc
    return torch


def runtime_receipt(device_index: int = 0) -> RuntimeReceipt:
    """Capture the actual CUDA runtime identity; never returns a fake receipt."""

    torch = _torch()
    if not torch.cuda.is_available():
        raise ModelAdapterError("CUDA is unavailable")
    if device_index < 0 or device_index >= torch.cuda.device_count():
        raise ModelAdapterError(f"CUDA device index is unavailable: {device_index}")
    name = str(torch.cuda.get_device_name(device_index))
    capability = ".".join(str(part) for part in torch.cuda.get_device_capability(device_index))
    return RuntimeReceipt(
        runtime_id=f"torch-{torch.__version__}-{torch.version.cuda or 'unknown'}-{platform.node()}",
        framework="pytorch",
        framework_version=str(torch.__version__),
        cuda_version=str(torch.version.cuda or "unknown"),
        device_name=name,
        compute_capability=capability,
        device_index=device_index,
        fake_provider=False,
    )


def _load_yolo(spec: ModelSpec, *, enforce_isolation: bool) -> Any:
    spec.validate()
    if spec.path.suffix.lower() != ".onnx":
        raise ModelAdapterError(
            "host inference refuses pickle checkpoints; supply a safe ONNX artifact"
        )
    if enforce_isolation:
        require_isolated_environment()
    try:
        from ultralytics import YOLO  # type: ignore[import-untyped]
    except ImportError as exc:
        raise ModelAdapterError(
            "Ultralytics is required only in the optional isolated GPU environment"
        ) from exc
    try:
        task = "pose" if spec.role == "person_pose" else "detect"
        return YOLO(str(spec.path), task=task)
    except Exception as exc:  # model code is third-party and may raise many types
        raise ModelAdapterError(f"checkpoint load failed for role {spec.role}") from exc


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    detach = getattr(value, "detach", None)
    if callable(detach):
        value = detach()
    cpu = getattr(value, "cpu", None)
    if callable(cpu):
        value = cpu()
    tolist = getattr(value, "tolist", None)
    if callable(tolist):
        return cast(list[Any], tolist())
    return list(value)


def _score(value: Any) -> float:
    score = float(value)
    if not 0 <= score <= 1:
        raise ModelAdapterError("model confidence is outside [0,1]")
    return score


def _box(values: Sequence[Any], width: int, height: int) -> tuple[float, float, float, float]:
    if len(values) != 4 or width <= 0 or height <= 0:
        raise ModelAdapterError("model returned an invalid bounding box")
    raw = tuple(float(value) for value in values)
    raw_box = (raw[0], raw[1], raw[2], raw[3])
    normalized = (
        raw_box
        if max(raw_box) <= 1.0
        else (raw_box[0] / width, raw_box[1] / height, raw_box[2] / width, raw_box[3] / height)
    )
    if (
        any(not 0 <= value <= 1 for value in normalized)
        or normalized[2] < normalized[0]
        or normalized[3] < normalized[1]
    ):
        raise ModelAdapterError("model returned an out-of-bounds bounding box")
    return normalized


def _keypoints(result: Any, index: int) -> tuple[tuple[float, float, float], ...]:
    points = getattr(getattr(result, "keypoints", None), "xyn", None)
    if points is None:
        return ()
    rows = _as_list(points)
    if index >= len(rows):
        return ()
    confidence = getattr(getattr(result, "keypoints", None), "conf", None)
    confidence_rows = _as_list(confidence)
    row = rows[index]
    conf_row = confidence_rows[index] if index < len(confidence_rows) else []
    parsed: list[tuple[float, float, float]] = []
    for point_index, point in enumerate(row):
        if len(point) < 2:
            raise ModelAdapterError("model returned malformed pose keypoint")
        score = float(conf_row[point_index]) if point_index < len(conf_row) else 0.0
        parsed.append((float(point[0]), float(point[1]), _score(score)))
    return tuple(parsed)


def _parse_result(result: Any, width: int, height: int, role: str) -> tuple[Detection, ...]:
    boxes = getattr(result, "boxes", None)
    if boxes is None:
        return ()
    xyxy = _as_list(getattr(boxes, "xyxy", None))
    confidences = _as_list(getattr(boxes, "conf", None))
    classes = _as_list(getattr(boxes, "cls", None))
    detections: list[Detection] = []
    for index, coordinates in enumerate(xyxy):
        if index >= len(confidences) or index >= len(classes):
            raise ModelAdapterError("model boxes, confidences, and classes have different lengths")
        detections.append(
            Detection(
                box=_box(coordinates, width, height),
                confidence=_score(confidences[index]),
                class_id=int(classes[index]),
                keypoints=_keypoints(result, index) if role == "person_pose" else (),
            )
        )
    return tuple(detections)


class YoloCudaAdapter:
    """Common lazy adapter for the pinned pose and cigarette YOLO roles."""

    def __init__(
        self,
        spec: ModelSpec,
        *,
        device_index: int = 0,
        confidence: float = 0.25,
        enforce_isolation: bool = True,
    ) -> None:
        if not 0 < confidence <= 1:
            raise ValueError("confidence must be in (0,1]")
        self.spec = spec
        self.device_index = device_index
        self.confidence = confidence
        self.enforce_isolation = enforce_isolation
        self._model: Any | None = None
        self._frames = 0
        self._elapsed_ms = 0.0
        self._peak_vram_bytes = 0

    @property
    def model(self) -> Any:
        if self._model is None:
            self._model = _load_yolo(self.spec, enforce_isolation=self.enforce_isolation)
        return self._model

    def warmup(self, frame: Any) -> InferenceOutput:
        return self.infer(frame)

    def infer(self, frame: Any) -> InferenceOutput:
        torch = _torch()
        if not torch.cuda.is_available():
            raise ModelAdapterError("CUDA is unavailable")
        shape = getattr(frame, "shape", None)
        if not shape or len(shape) < 2:
            raise ModelAdapterError("frame must expose height and width")
        height, width = int(shape[0]), int(shape[1])
        if height <= 0 or width <= 0:
            raise ModelAdapterError("frame dimensions must be positive")
        device = f"cuda:{self.device_index}"
        torch_device = torch.device(device)
        with torch.cuda.device(torch_device):
            torch.cuda.reset_peak_memory_stats()
        start = time.perf_counter()
        try:
            results = self.model.predict(
                source=frame,
                device=device,
                conf=self.confidence,
                verbose=False,
                stream=False,
            )
            torch.cuda.synchronize(torch_device)
        except Exception as exc:
            raise ModelAdapterError(f"CUDA inference failed for role {self.spec.role}") from exc
        elapsed = (time.perf_counter() - start) * 1000
        if not results:
            detections: tuple[Detection, ...] = ()
        else:
            detections = _parse_result(results[0], width, height, self.spec.role)
        self._frames += 1
        self._elapsed_ms += elapsed
        self._peak_vram_bytes = max(self._peak_vram_bytes, int(torch.cuda.max_memory_allocated()))
        return InferenceOutput(
            role=self.spec.role,
            model_revision=self.spec.revision,
            detections=detections,
            elapsed_ms=elapsed,
            device=device,
        )

    def receipt(self) -> AdapterReceipt:
        return AdapterReceipt(
            role=self.spec.role,
            model_revision=self.spec.revision,
            artifact_sha256=self.spec.sha256,
            device=f"cuda:{self.device_index}",
            frames=self._frames,
            elapsed_ms=self._elapsed_ms,
            peak_vram_bytes=self._peak_vram_bytes,
            fake_provider=False,
        )


class PoseCudaAdapter(YoloCudaAdapter):
    """YOLO11 pose adapter; each detection includes normalized keypoints."""

    def __init__(self, spec: ModelSpec, **kwargs: Any) -> None:
        if spec.role != "person_pose":
            raise ValueError("pose adapter requires model role person_pose")
        super().__init__(spec, **kwargs)


class CigaretteCudaAdapter(YoloCudaAdapter):
    """HEIher single-class cigarette detector adapter."""

    def __init__(self, spec: ModelSpec, **kwargs: Any) -> None:
        if spec.role != "cigarette_detector":
            raise ValueError("cigarette adapter requires model role cigarette_detector")
        super().__init__(spec, **kwargs)


def runtime_fingerprint(receipt: RuntimeReceipt) -> str:
    """Stable hash for a runtime receipt, suitable for a run manifest."""

    payload = "|".join(
        (
            receipt.framework,
            receipt.framework_version,
            receipt.cuda_version,
            receipt.device_name,
            receipt.compute_capability,
            str(receipt.device_index),
        )
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


__all__ = [
    "AdapterReceipt",
    "CigaretteCudaAdapter",
    "Detection",
    "InferenceOutput",
    "ModelAdapterError",
    "ModelSpec",
    "PoseCudaAdapter",
    "YoloCudaAdapter",
    "require_isolated_environment",
    "runtime_fingerprint",
    "runtime_receipt",
    "sanitized_environment",
]
