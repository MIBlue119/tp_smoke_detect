# GPU review recheck remediation memory

## Context

The GPU release branch had a green CPU test suite while six production trust
boundaries remained untested: native publication, completed candidate evidence,
Paho acknowledgement/retry semantics, fabricated qualification telemetry,
readiness receipt lineage, and replay path containment.

## Symptom and root cause

The earlier implementation treated scaffolding and aggregate field presence as
runtime proof. The native process printed candidates instead of publishing;
the DeepStream probe ran before downstream role joins; MQTT code relied on a
message-level ACK and optional properties; qualification accepted short or
self-declared rows; readiness accepted a caller-supplied label; and media-root
containment was conditional.

## Fix

Use explicit fail-closed boundaries: `mosquitto_pub -q 1` with argv/exec, a
post-crop completed probe with unavailable role receipts, MQTT v5 User Properties
and `client.ack(mid, qos)`, measured per-camera telemetry and duration/sample
requirements, canonical source receipt SHA-256 lineage/HMAC support, and an
approved media root required for every replay.

## Prevention

Keep the adversarial probes in `tests/contract/test_gpu_recheck.py`, run them
before release review, and never turn a checked-in fixture, aggregate metric
name, or free-form readiness field into a qualification claim. Re-run on the
target GPU with real model, media, broker, and DeepStream assets before changing
the external qualification blocker.
