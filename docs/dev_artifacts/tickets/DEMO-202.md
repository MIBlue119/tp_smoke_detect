# DEMO-202 — Deterministic spatial and temporal fusion

Status: implemented on `feature/DEMO-202-fusion`

## Delivered

- Added typed, normalized-coordinate `PersonDetection`, `CigaretteDetection`,
  and `FrameDetections` inputs in `ml/demo/fusion.py`.
- Added a deterministic IoU tracker with stable `track-000001` IDs when the
  pose provider does not supply a tracker ID. One cigarette box is assigned to
  at most one person using score, lexical track ID, and detector index ties.
- Added head and wrist-to-mouth spatial association using pose keypoints and
  bounded geometry/confidence scores.
- Added source-PTS-driven temporal hysteresis: four persistent entry frames,
  a six-frame exit gap, explicit `candidate`, `insufficient_evidence`, and
  `unclear` states, and deterministic event IDs.
- Added a separate `baseline_miss_event` helper for manual review. It cannot
  modify raw evidence or create a production decision.
- Added fixture-only tests for stable tracking/tie breaks, geometry validation,
  persistence, out-of-order PTS, disappearance gaps, byte-stable replay, and
  manual baseline misses.

## Verification

```text
uv run pytest -q tests/unit/ml/demo/test_spatial_association.py tests/unit/ml/demo/test_temporal_fusion.py
9 passed
uv run ruff check ml/demo/fusion.py ml/demo/__init__.py tests/unit/ml/demo/test_spatial_association.py tests/unit/ml/demo/test_temporal_fusion.py
All checks passed
uv run mypy ml/demo tests/unit/ml/demo
Success: no issues found
```

The implementation is CPU-safe and does not load model weights, access media,
or claim real inference. U5 must connect the U2 adapter output and retain the
raw per-frame evidence before rendering.
