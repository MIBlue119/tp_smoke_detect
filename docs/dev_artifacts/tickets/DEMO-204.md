# DEMO-204 — Shibuya RTX 3090 integration run

## Status

Implemented and executed on the sealed local inputs. Final courier delivery is
owned by DEMO-205 after Sol's final review.

## Delivered

- Added `scripts/run_shibuya_demo.py`, an isolated end-to-end command that validates the source receipt, loads both pinned checkpoints through the typed CUDA adapters, feeds deterministic fusion, writes immutable annotation JSON, renders the no-audio MP4, and records GPU/media receipts.
- Added metadata-only run manifest, manual-review record, and baseline report under `docs/dev_artifacts/demo_runs/shibuya-GByZa0qbA8A-r1/`.
- Added the fixed-sample review boundary: no threshold changes or synthetic detections after seeing the clip.

## Verification

- Full run: 1,248 frames / 41.6416 seconds, both roles on RTX 3090 CUDA, fake provider false.
- Output: H.264/yuv420p, no audio, 1,248 frames, 41.6416 seconds, 16,222,433 bytes.
- Events: zero candidate intervals; reported as a negative/uncertain baseline, not success.
- `scripts/validate_demo_run.py --metadata-receipt`: passed.
- `scripts/validate_demo_run.py --source ...`: passed.

## Blockers and follow-up

- ONNX/TensorRT parity was not attempted in this physical run; direct PyTorch CUDA is the explicit fallback.
- Telegram submission remains pending the parent orchestrator's Sol review and courier availability.
