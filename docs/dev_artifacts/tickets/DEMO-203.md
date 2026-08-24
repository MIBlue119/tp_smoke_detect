# DEMO-203 — Evidence-first annotation renderer

## Scope

Implemented the U4 renderer lane from the Shibuya baseline plan. The renderer
does not run inference and does not manufacture detections. It reads the
versioned `demo.video-annotation.v1` JSON and draws only the frozen frame
evidence, including rejected states and bounded reason codes.

## Delivered

- `ml/demo/annotate.py`: ffprobe media inspection, contract/semantic checks,
  deterministic raw-video decode → Pillow overlay → H.264/yuv420p encode,
  strict no-audio and sub-50 MB validation, atomic output and receipt writes.
- `scripts/annotate_video.py`: render CLI.
- `scripts/validate_demo_run.py`: pre-delivery validation CLI.
- `docs/runbooks/model-demo.md`: handoff/rerun instructions.
- `tests/integration/demo/`: synthetic redistributable source, positive,
  empty-result, malformed-evidence, hash, duration, and size-limit coverage.

The overlay keeps the permanent `BASELINE DEMO - NOT PRODUCTION QUALIFIED`
banner, GPU/model/run badge, person and cigarette boxes, pose landmarks,
track/evidence/confidence/state/reason text. Source bytes must match the JSON
receipt before either rendering or validation can proceed.

## Verification

```text
uv run ruff format --check ml/demo/annotate.py scripts/annotate_video.py scripts/validate_demo_run.py tests/integration/demo  PASS
uv run ruff check ml/demo/annotate.py scripts/annotate_video.py scripts/validate_demo_run.py tests/integration/demo  PASS
uv run mypy ml/demo/annotate.py scripts/annotate_video.py scripts/validate_demo_run.py  PASS
uv run pytest tests/integration/demo -q  6 passed
```

The tests use only a generated test pattern and never access the private
Shibuya source, model weights, CUDA, or network.

## Known boundary

The U2 model adapter also lists `scripts/validate_demo_run.py` in its planned
files. This branch keeps the validator focused on renderer/media concerns;
the integration owner must reconcile any U2 changes to that shared path rather
than blindly merging both versions.
