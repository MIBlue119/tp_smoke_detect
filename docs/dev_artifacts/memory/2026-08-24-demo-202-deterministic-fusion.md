# DEMO-202 memory — keep evidence and temporal decisions separate

## Context

The one-video baseline needs spatial hand/mouth association and temporal
persistence, but it is not a production smoking decision and has no site
accuracy evidence.

## Symptom

A frame-level cigarette label can flicker, be counted for two people, or look
like a verified smoking event when the pose is missing or the person leaves the
frame.

## Root cause

Raw detector boxes have no identity, source-time hysteresis, or explicit
uncertainty semantics. Treating an unmatched box as a person-level result also
loses the reason a candidate was rejected.

## Fix

Use normalized head/wrist-to-mouth geometry, one-to-one deterministic
association, source-PTS-only tracking state, four-frame entry persistence, and
a bounded exit gap. Keep `candidate`, `insufficient_evidence`, and `unclear`
as separate states. Manual visible misses are emitted as a separate
`baseline_miss_event` and never rewrite raw model evidence.

## Verification

Nine CPU-safe unit tests cover stable IDs, tie-breaking, persistence,
out-of-order timestamps, disappearance gaps, deterministic replay, and manual
miss records. No GPU, model weight, or private video is used.

## Prevention

Do not add a production `verified` state to this module. U5 must preserve all
raw adapter evidence and use the same hashed thresholds when rendering the
Shibuya clip. Any threshold change requires a new config revision and run.
