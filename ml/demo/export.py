"""Optional ONNX/TensorRT export and fixed-frame parity receipts."""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .models import InferenceOutput, ModelAdapterError, YoloCudaAdapter


@dataclass(frozen=True, slots=True)
class ParityReceipt:
    status: str
    backend: str
    compared_frames: int
    max_box_delta: float | None
    max_confidence_delta: float | None
    errors: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "backend": self.backend,
            "compared_frames": self.compared_frames,
            "max_box_delta": self.max_box_delta,
            "max_confidence_delta": self.max_confidence_delta,
            "errors": list(self.errors),
        }


@dataclass(frozen=True, slots=True)
class ExportReceipt:
    backend: str
    status: str
    artifact_id: str
    path: str | None
    sha256: str | None
    size_bytes: int | None
    elapsed_ms: float
    parity: ParityReceipt | None
    errors: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "backend": self.backend,
            "status": self.status,
            "artifact_id": self.artifact_id,
            "path": self.path,
            "sha256": self.sha256,
            "size_bytes": self.size_bytes,
            "elapsed_ms": self.elapsed_ms,
            "parity": self.parity.to_dict() if self.parity else None,
            "errors": list(self.errors),
        }


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _safe_output(root: Path, path: Path) -> Path:
    root = root.resolve()
    path = path.resolve()
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise ModelAdapterError("export output escapes the artifact root") from exc
    return path


def compare_outputs(
    references: list[InferenceOutput],
    candidates: list[InferenceOutput],
    *,
    backend: str,
    box_tolerance: float = 0.02,
    confidence_tolerance: float = 0.05,
) -> ParityReceipt:
    """Compare deterministic ordered detections on fixed frames.

    A differing detection count or order is a hard failure.  This conservative
    rule prevents an export from entering a run manifest after silently
    changing association inputs.
    """

    if len(references) != len(candidates):
        return ParityReceipt(
            "failed",
            backend,
            min(len(references), len(candidates)),
            None,
            None,
            ("frame count mismatch",),
        )
    max_box = 0.0
    max_conf = 0.0
    errors: list[str] = []
    for index, (reference, candidate) in enumerate(zip(references, candidates, strict=True)):
        if reference.role != candidate.role:
            errors.append(f"frame {index}: role mismatch")
        if len(reference.detections) != len(candidate.detections):
            errors.append(f"frame {index}: detection count mismatch")
            continue
        for det_index, (left, right) in enumerate(
            zip(reference.detections, candidate.detections, strict=True)
        ):
            if left.class_id != right.class_id:
                errors.append(f"frame {index} detection {det_index}: class mismatch")
            box_delta = max(abs(a - b) for a, b in zip(left.box, right.box, strict=True))
            conf_delta = abs(left.confidence - right.confidence)
            max_box = max(max_box, box_delta)
            max_conf = max(max_conf, conf_delta)
            if box_delta > box_tolerance:
                errors.append(f"frame {index} detection {det_index}: box delta exceeds tolerance")
            if conf_delta > confidence_tolerance:
                errors.append(
                    f"frame {index} detection {det_index}: confidence delta exceeds tolerance"
                )
    return ParityReceipt(
        "passed" if not errors else "failed",
        backend,
        len(references),
        max_box,
        max_conf,
        tuple(errors),
    )


def export_adapter(
    adapter: YoloCudaAdapter,
    output_root: Path,
    *,
    backend: str,
    imgsz: int = 640,
    half: bool = True,
) -> ExportReceipt:
    """Attempt an export and return an explicit unavailable/failed receipt."""

    if backend not in {"onnx", "engine"}:
        raise ValueError("backend must be onnx or engine")
    output_root.mkdir(parents=True, exist_ok=True)
    artifact_id = f"{adapter.spec.role}-{adapter.spec.revision}-{backend}"
    started = time.perf_counter()
    try:
        exported = adapter.model.export(
            format=backend,
            imgsz=imgsz,
            half=half,
            device=f"cuda:{adapter.device_index}",
            verbose=False,
        )
        exported_path = _safe_output(output_root, Path(str(exported)))
        if not exported_path.is_file():
            raise ModelAdapterError("export returned a missing artifact")
        digest = _sha256(exported_path)
        target = _safe_output(output_root, output_root / exported_path.name)
        if target != exported_path:
            target.write_bytes(exported_path.read_bytes())
            digest = _sha256(target)
            exported_path = target
        return ExportReceipt(
            backend=backend,
            status="exported",
            artifact_id=artifact_id,
            path=exported_path.name,
            sha256=digest,
            size_bytes=exported_path.stat().st_size,
            elapsed_ms=(time.perf_counter() - started) * 1000,
            parity=None,
        )
    except ImportError as exc:
        return ExportReceipt(
            backend=backend,
            status="unavailable",
            artifact_id=artifact_id,
            path=None,
            sha256=None,
            size_bytes=None,
            elapsed_ms=(time.perf_counter() - started) * 1000,
            parity=None,
            errors=(f"optional export dependency unavailable: {exc}",),
        )
    except Exception as exc:
        return ExportReceipt(
            backend=backend,
            status="failed",
            artifact_id=artifact_id,
            path=None,
            sha256=None,
            size_bytes=None,
            elapsed_ms=(time.perf_counter() - started) * 1000,
            parity=None,
            errors=(f"{type(exc).__name__}: {exc}",),
        )


def write_receipt(path: Path, payload: dict[str, Any]) -> None:
    """Write export data atomically as metadata-only JSON."""

    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(json.dumps(payload, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


__all__ = ["ExportReceipt", "ParityReceipt", "compare_outputs", "export_adapter", "write_receipt"]
