# GPU-104 candidate boundary memory

## Context

The GPU media plane needs to hand a high-rate, at-least-once candidate stream to
the deterministic Python core without sharing raw pixels or provider output.

## Symptom

The repository had only a synchronous in-memory replay bus and an e2e callback.
There was no durable MQTT acknowledgement point, no per-track live cascade
state, and no strict consumer behavior for incomplete model receipts.

## Root cause and decision

The candidate envelope is the correct policy boundary: native media owns GPU
decode/crops/Triton and sends typed role receipts; the core validates receipt
correlation and revisions before mapping into domain observations. A successful
receipt must correlate to the candidate envelope, and a missing/failed positive
role is converted into a quality-rejected observation. The MQTT adapter therefore
acknowledges only after decision and audio facts are durable, retries transient
repository/audio failures, and dead-letters poison messages.

## Fix

`CandidateProcessingService` now uses deterministic UUID5 decision IDs and an
event/content fingerprint, one `CascadeEngine` per camera/track, repository
lookups for restart-safe deduplication, and a shadow-safe `AudioRequestService`
choke point. `MqttCandidateConsumer` uses a bounded queue and worker pool;
dead-letter messages contain only topic, message ID, and reason, never the
malformed payload.

## Verification and prevention

Contract/integration tests cover duplicate candidates, malformed JSON, missing
and mismatched receipts, failed evidence, shadow audio, ack ordering, retry and
backpressure. The full suite passed with native Debug/Release CTest. Keep raw
pixels and crop inference in the native media plane; do not add provider SDKs or
unbounded identifiers to the Python candidate service.

Related ticket: `docs/dev_artifacts/tickets/GPU-104.md`.
