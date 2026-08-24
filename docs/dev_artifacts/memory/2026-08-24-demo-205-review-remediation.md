# DEMO-205 review remediation memory

## Context

The first Shibuya baseline run was truthful about real CUDA inference, but Sol
found that the checkpoint trust boundary, schema publication gate, manual-review
lineage, overlay readability, runtime reproducibility, hysteresis semantics,
and decoder timestamps were not strong enough for handoff.

## Symptom and root cause

The runner loaded pickle-bearing checkpoints in the caller environment, used a
lightweight validator instead of the checked-in schema, initialized review
anchors as pending placeholders, synthesized PTS from frame number, and let an
active track remain positive below its exit threshold. The renderer repeated a
bottom panel for every entry and did not show source time or exact model
identity.

## Fix and verification

`run_checkpoint_boundary.sh` now builds/runs a non-root, no-network, read-only
container and proved both real `.pt` files load. `validate_video_annotation`
uses `schemas/demo/video-annotation.v1.json`; required/additional-property
regressions are covered. The runner consumes a completed manual-review file,
uses PyAV PTS/time-base, captures a package runtime receipt, and writes a
hash-linked manifest. The overlay is capped at four prioritized boxes with one
summary panel. Fusion tests cover low-score exit hysteresis.

The complete rerun on an NVIDIA GeForce RTX 3090 processed 1,248 frames in
41.6416 seconds, produced a silent 10,899,217-byte H.264 MP4, and emitted zero
candidate intervals. The visual review remains `unclear` at all 21 fixed
anchors; that is not evidence that no smoking occurs in the whole clip.

## Prevention

Keep source/video/checkpoint files under `.local-demo-inputs/` only. Require a
passed boundary receipt, a completed review companion, schema validation, and
hash-linked media before courier delivery. Never train or fine-tune from this
single evaluation clip.
