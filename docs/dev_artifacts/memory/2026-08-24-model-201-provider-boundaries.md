# MODEL-201 provider boundary lessons

## Context

The U4 provider framework had to support deterministic CPU fixtures and optional
ONNX/OpenAI-compatible runtimes without allowing a provider failure to become domain
evidence or a process-wide outage.

## Symptom

Initial Pydantic feature fields used ordinary scalar unions. Pydantic coercion accepted
binary values as strings, which could make a metadata-only request carry private media.
The deterministic fake also used the receipt's wall-clock completion timestamp, so
repeating the same fixture did not produce an equal receipt.

## Root cause

Coercive validation is unsafe at a privacy boundary, and wall-clock fields are
inherently nondeterministic even when inference itself is deterministic.

## Fix

- Use strict scalar feature types and reject non-finite numeric feature values.
- Keep the fake provider's receipt completion timestamp fixed at the Unix epoch.
- Validate every successful receipt's result class against its declared role.
- Convert timeout, malformed, OOM, runtime, and revision failures into result-less
  receipts; keep circuit state per role.

## Verification

Contract and adapter tests cover binary-feature rejection, NaN rejection, deterministic
replay, role/result mismatch, timeout, malformed enum, optional runtime unavailability,
revision mismatch, readiness, and circuit opening. Full U4 tests and project unit,
contract, integration, lint, and type gates pass.

## Prevention

Treat provider output and feature envelopes as strict wire contracts. Add a proof-first
test whenever a new adapter or result role is introduced, and do not put runtime
packages or model weights in the CPU dependency group.

Related ticket: MODEL-201
