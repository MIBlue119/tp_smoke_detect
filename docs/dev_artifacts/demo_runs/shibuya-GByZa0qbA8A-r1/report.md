# Shibuya RTX 3090 baseline report

Run `shibuya-GByZa0qbA8A-r1` is a private, evidence-only baseline demonstration.
It is not a production qualification, accuracy evaluation, identity system, or
audio trigger.

## Result

- Source: `GByZa0qbA8A`, 41.6416 seconds, 640x480, 30000/1001 FPS, SHA-256 `32d3ffd7456d0a2f11e3b62a0330328e0541b7bb8e98ce733da5b3c1523facc5`.
- Processed: 1,248 frames with both model roles on `cuda:0`, NVIDIA GeForce RTX 3090, compute capability 8.6, PyTorch 2.13.0+cu130.
- Pose role: 1,248 frames, 145,785,856-byte peak allocated VRAM, 14,038.81 ms accumulated inference.
- Cigarette role: 1,248 frames, 211,421,184-byte peak allocated VRAM, 11,580.60 ms accumulated inference.
- Fusion events: 0 candidate intervals; no production decision or audio request.
- Manual spot-check: 21 fixed two-second samples, 0 true candidates, 0 false candidates, 0 confidently visible misses, 21 unclear. The contact sheet showed people and pose overlays but no confident cigarette box; this must not be interpreted as proof that the source contains no smoking.
- Export: ONNX/TensorRT were not attempted in U5; PyTorch CUDA is the actual baseline path.

## Media

- Annotated output: `annotated.mp4`, SHA-256 `c28d3640cdc128c21c523419f9e586cba9b15be840a9e3f27654154698a04dd1`.
- Size: 16,222,433 bytes, below the 50,000,000-byte courier limit.
- Validation: H.264, yuv420p, 640x480, 30000/1001 FPS, 41.6416 seconds, 1,248 frames, 0 audio streams.
- Annotation JSON SHA-256: `68742d8a426431c6b8838cf4072284439c6198bcafb48825118ebe5a8ff99416`.

## Limitations

The external cigarette checkpoint is a weak, uncalibrated baseline with model-card
MIT language and upstream YOLO11 terms retained in the acquisition receipt. The
source is retained for the named private evaluation only. The result does not
support a production accuracy claim, a legal/licensing conclusion, or a claim
that visible smoking was absent from the full clip.
