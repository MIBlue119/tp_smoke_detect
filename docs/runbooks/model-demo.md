# Private model baseline demo

This runbook describes the evidence-only rendering phase. Model inference is
performed separately; the renderer never imports a model runtime and never
creates detections.

## Run the sealed Shibuya baseline

The physical demo uses an isolated optional runtime. The command below expects
the source and checkpoints produced by DEMO-200 under the ignored
`.local-demo-inputs/` root. `env -i` is deliberate: the adapter rejects any
secret-like environment variable before loading a pickle-bearing checkpoint.

```bash
env -i PATH=/usr/bin:/bin:/usr/local/bin:/opt/cuda/bin \
  PYTHONPATH="$PWD" \
  HOME="$PWD/.local-demo-inputs/runtime-home" \
  DEMO_ISOLATED_RUNTIME=1 DEMO_NETWORK_DISABLED=1 \
  YOLO_CONFIG_DIR="$PWD/.local-demo-inputs/runtime-home/ultralytics" \
  .worktrees/feature/gpu-mandatory-release/artifacts/shibuya_demo/infer-venv/bin/python \
  scripts/run_shibuya_demo.py \
  --source .local-demo-inputs/source/shibuya-GByZa0qbA8A.mp4 \
  --acquisition-receipt docs/dev_artifacts/qualification/model-demo/acquisition-receipt.json \
  --config ml/configs/demo/shibuya-baseline.json \
  --output-root .local-demo-inputs/runs \
  --model-root .local-demo-inputs/models \
  --run-id shibuya-GByZa0qbA8A-r1
```

The run writes source/weights/video only below the ignored local artifact root.
The committed manifest, manual-review record, and report under
`docs/dev_artifacts/demo_runs/` contain hashes and artifact IDs, not host paths
or raw pixels. A zero-event run is valid evidence of a negative/uncertain
baseline; it is not a production accuracy claim.

## Render a frozen run

The source video and weights live outside Git under the local artifact root.
The source hash in the annotation JSON must match the source bytes.

```bash
uv run python scripts/annotate_video.py \
  --source local_artifacts/model-demo/source/shibuya.mp4 \
  --annotation local_artifacts/model-demo/runs/<run-id>/annotation.json \
  --output local_artifacts/model-demo/runs/<run-id>/annotated.mp4 \
  --receipt local_artifacts/model-demo/runs/<run-id>/media-receipt.json
```

The output is H.264/yuv420p, has no audio, keeps source dimensions and frame
rate, and carries the persistent `BASELINE DEMO - NOT PRODUCTION QUALIFIED`
banner. Every box, pose landmark, score, state, track ID, and bounded reason is
read from the immutable annotation JSON. An empty `frames`/`events` result is a
valid negative baseline result and is rendered as such.

## Validate before delivery

```bash
uv run python scripts/validate_demo_run.py \
  --source local_artifacts/model-demo/source/shibuya.mp4 \
  --annotation local_artifacts/model-demo/runs/<run-id>/annotation.json \
  --output local_artifacts/model-demo/runs/<run-id>/annotated.mp4 \
  --receipt local_artifacts/model-demo/runs/<run-id>/media-receipt.json
```

The validator checks JSON contract semantics, source hash linkage, one video
stream, no audio, H.264/yuv420p, dimensions, duration within one frame, and a
strictly sub-50 MB output. It writes a hash-bound media receipt atomically.

No output is sent if validation fails. A failed baseline, visible miss, or
unclear interval remains an honest result and must not be repaired by adding
synthetic boxes or changing thresholds after review.
