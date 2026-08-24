# DEMO-200 — sealed demo contracts and inputs

Status: complete for U1; final GPU inference intentionally not run.

## Scope

Frozen the metadata-only `demo.video-annotation.v1` contract, acquisition
helpers, baseline configuration, optional `yt-dlp` dependency, ignore rules,
and source/model receipts.  The source and weights remain in the local
artifact store and are not in Git.

## Sealed inputs

- Source: YouTube `GByZa0qbA8A`, title `Smoking areas in Shibuya Tokyo Japan`,
  uploader `4kocool`, 640×480 H.264, 30000/1001 FPS, 41.6416 seconds, no audio.
- Source bytes: 4,203,599 bytes,
  SHA-256 `32d3ffd7456d0a2f11e3b62a0330328e0541b7bb8e98ce733da5b3c1523facc5`.
- Acquisition: `yt-dlp 2026.08.19`, format `135`; YouTube did not expose a
  reusable license, so the receipt is private user-directed evaluation only.
- Pose: YOLO11n-pose official asset release `v8.3.0`, 6,255,593 bytes,
  SHA-256 `869e83fcdffdc7371fa4e34cd8e51c838cc729571d1635e5141e3075e9319dc0`.
- Cigarette: HEIher `smoking-detection` revision
  `12a54cda2ca031e2b96a486bc288e957e56f51c8`, 40,509,349 bytes,
  SHA-256 `0ef558d3cf049d0acbb3f2322bc9e4e53db1a107426e3669622176c30c054d82`.

Both checkpoints are Torch zip/pickle-bearing files.  Acquisition only lists
archive members and never deserializes them.  Any conversion must run in a
disposable unprivileged environment with no credentials or network access.

## Verification

```text
uv lock                                  PASS (yt-dlp 2026.8.19 locked)
uv run pytest tests/unit/ml/demo tests/contract/test_demo_schema.py -q  PASS
uv run ruff check ml/demo scripts/acquire_demo_assets.py tests/unit/ml/demo tests/contract/test_demo_schema.py  PASS
uv run mypy ml/demo scripts/acquire_demo_assets.py  PASS
uv run python scripts/acquire_demo_assets.py ...  PASS
ffprobe sealed source                    PASS (one video stream, no audio)
sha256 source and both models            PASS
```

The full model load, CUDA execution, export parity, fusion, annotation, manual
review, and courier delivery are owned by DEMO-201 through DEMO-205.
