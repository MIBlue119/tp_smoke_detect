# Deterministic retries and track gaps in domain aggregation

## Context

The metadata broker can retry messages and a camera stream can pause or emit
late observations. U2 must not turn either condition into an extra smoking
cycle or merge two disconnected people into one event.

## Symptom

A state machine driven by arrival order or wall-clock time can double-count a
cycle on broker retry, accept a late message as current evidence, or continue
an open hand-to-mouth approach after a long stream gap.

## Hypotheses

The likely causes were non-idempotent append operations, an implicit current
time dependency, and no explicit lifecycle boundary for stale track state.

## Root cause

Transport delivery order and wall time are not domain truth. Only source
timestamps and a stable event identity (or deterministic observation
fingerprint) can make replay and live processing equivalent.

## Fix

`DomainObservation` exposes a deterministic fingerprint; `CycleDetector` and
`TrackState` ignore duplicate and out-of-order observations. `TrackState`
resets its aggregation window and cycle detector when the source gap exceeds
the configured tolerance. All transitions and cycle intervals derive from
source nanoseconds.

## Verification

`tests/unit/domain/test_cycle_detector.py` proves duplicate and out-of-order
behavior; `test_track_state.py` proves explicit duplicate and gap reasons. The
full unit, contract, lint, format, and strict mypy gates passed for CORE-101.

## Prevention

Future adapters must preserve source timestamps and event IDs when mapping
provider output. They must not substitute receipt time or remove the event ID
before calling the domain facade. Changes to U2 should retain virtual-time
table tests.

## Related commit

Recorded with the CORE-101 feature commit.
