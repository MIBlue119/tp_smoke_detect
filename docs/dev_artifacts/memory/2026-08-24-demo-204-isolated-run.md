# DEMO-204: isolated runtime and honest negative baseline

## Context

The integration run needed to load two pickle-bearing YOLO checkpoints on the
local RTX 3090 without inheriting credentials or fabricating a positive result.

## Symptom

The first smoke command reached CUDA but the adapter rejected model loading
because the parent process exported names matching secret/cookie patterns.

## Root cause

`require_isolated_environment()` intentionally fails closed when any sensitive
environment variable is present. A pair of boolean flags alone is not proof of
an isolated loader boundary.

## Fix

Run the operator command with `env -i`, retain only PATH/PYTHONPATH/HOME and the
two explicit isolation flags, and use local sealed files after acquisition.
The run manifest records the actual runtime and adapter receipts.

## Verification

The complete 1,248-frame run used `cuda:0` on an NVIDIA GeForce RTX 3090 with
`fake_provider=false` for both roles. It produced zero fusion events. Fixed
two-second samples were visually spot-checked and left `unclear`; no synthetic
boxes, threshold changes, or production decisions were introduced.

## Prevention

Keep the isolated environment requirement in the adapter and document the
`env -i` invocation in the model-demo runbook. Treat a zero-event result as a
valid baseline outcome and report uncertainty rather than converting it to a
success claim.
