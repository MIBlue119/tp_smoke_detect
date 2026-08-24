# DEMO-201 — real CUDA model adapters and export parity

Status: implemented; real one-frame CUDA smoke passed on the approved RTX 3090.

## Delivered

- Lazy, provenance-bound YOLO adapters for the pinned `person_pose` and
  `cigarette_detector` roles.
- Exact size/SHA-256 verification before checkpoint load.
- Explicit `DEMO_ISOLATED_RUNTIME=1` and `DEMO_NETWORK_DISABLED=1` gates plus
  sensitive-environment scrubbing for pickle-bearing checkpoint loading.
- Normalized boxes, pose keypoints, bounded confidence values, model revision,
  CUDA device, frame count, elapsed time, and peak allocated VRAM receipts.
- Conservative fixed-frame output parity comparison and explicit ONNX/TensorRT
  export receipts.  An export is not accepted without parity evidence.
- Metadata-only run validator and opt-in real GPU tests.  CPU CI does not import
  PyTorch/Ultralytics or download weights.

## Verification

```text
uv run ruff check ...                         PASS
uv run ruff format --check ...                PASS
uv run mypy ml/demo scripts tests/gpu/demo    PASS
uv run pytest tests/unit/ml/demo tests/contract/test_demo_schema.py tests/gpu/demo -q
                                               12 passed, 2 expected skips
real sanitized RTX 3090 first-frame smoke     PASS
```

The physical smoke used the sealed U1 source and model hashes, PyTorch
2.9.1+cu128, CUDA 12.8, NVIDIA GeForce RTX 3090, compute capability 8.6, and
device `cuda:0`.  Pose produced 10 detections; the cigarette model produced 0
on the first 640×480 frame.  This is raw baseline evidence, not a smoking
decision or accuracy claim.  Peak allocated VRAM was 44,662,272 bytes for the
pose role and 186,845,184 bytes for the cigarette role.

ONNX and TensorRT export attempts were recorded as `unavailable` because the
isolated conversion runtime lacked `onnxscript` and TensorRT.  No export is in
the run manifest and no parity pass is claimed.

## External gates

- Full video extraction, fusion, annotation, manual review, and delivery remain
  DEMO-204/DEMO-205 integration work.
- TensorRT requires the approved production image/toolchain and a new export
  plus fixed-frame parity receipt.
- No weights, engines, source video, or annotated video are committed.
