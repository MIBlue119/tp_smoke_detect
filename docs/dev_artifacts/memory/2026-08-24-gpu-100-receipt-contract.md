# GPU-100 memory: keep GPU evidence typed and fail closed

## Context

The GPU release needs DeepStream/TensorRT/Triton evidence to cross the existing
Python decision core without moving image bytes or model prose across the
boundary. Existing v1 candidate payloads are already used by CPU replay and
must remain readable during a staged rollout.

## Symptom

A naive extension could add arbitrary model dictionaries or reviewer text to
`track.candidate.v1`, let a timeout carry a positive score, or let a stale
model revision be mistaken for current evidence. An old strict consumer would
also reject a new producer payload if rollout order was not controlled.

## Discarded hypotheses

- Reusing the provider-port receipt directly: it lacks the candidate-level
  artifact revision, deadline outcome, and reviewer boundary required here.
- Making all new fields mandatory: this would break old v1 replay fixtures and
  mixed-version consumers.
- Accepting free-form reviewer explanations: they are unbounded, non-auditable,
  and can accidentally become a policy signal.

## Root cause

The original candidate contract carried aggregate observations but no
per-role execution receipt or deployment compatibility marker. The domain also
had no single, explicit candidate-to-observation mapping for reviewer output.

## Fix

`CandidateInferenceReceipt` now uses bounded role/status/reason/output/deadline
enums, immutable model and artifact revisions, UUID request correlation, and a
typed reviewer result. Candidate validation rejects duplicate roles, revision
mismatches, failed positive evidence, and reviewer observations without a
matching successful receipt. Native serialization mirrors the optional fields,
and the rollout order is documented as consumer first, queue drain, producer
last, with reverse rollback.

## Verification

The focused contract suite, generated schema comparison, native Debug/Release
CTest, and all CPU unit/integration/e2e gates pass. A failed native reviewer
receipt is parsed successfully and remains unavailable. GPU runtime and broker
rehearsal evidence are intentionally left to later tickets.

## Prevention

Keep candidate changes additive within v1; introduce `track.candidate.v2` for
breaking shapes. Require every new role to add a bounded enum, a typed result
or explicit unavailable state, a stale-revision test, a failed-evidence test,
and a native/Python fixture before a producer can publish it.

## Related commit

`0d1c67ca56f49bb0e659674c0b95e77d50260ef1`
