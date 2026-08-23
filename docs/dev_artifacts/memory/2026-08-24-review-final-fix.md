# Learning — final review safety and compatibility fixes

## Context

The release review found defects that only appeared when SQLite persistence,
real ASGI requests, retries, and concurrent requests were combined.

## Symptom and hypotheses

Eligible persisted decisions were rejected as ineligible; concurrent audio
requests both announced; accepted playback disappeared after a duplicate;
concurrent evaluation retries left orphan decisions; naive mute expiry caused
repeatable 500s; v1 metadata broke old JSON; failed restores leaked staging;
and top-level JSON environment overrides failed after YAML bootstrap.

## Root cause

SQLite integers were compared by identity, policy read/send/write was split
across unlocked repository calls, the latest receipt row was treated as the
whole safety history, idempotency was claimed after decision creation, datetime
and settings source boundaries were incomplete, and restore cleanup started too
late.

## Fix and verification

The repository now normalizes booleans and mute timestamps, claims evaluation
keys before side effects, durably reserves audio before adapter I/O, preserves
accepted playback, and cleans staging in one outer `try/finally`. The API
serializes per-zone audio policy/send/record, and settings explicitly
JSON-decodes top-level complex overrides. v1 metadata is optional on ingestion
while producers retain metadata emission. Proof-first ASGI, concurrency,
compatibility, settings, and failure-cleanup tests pass.

## Prevention

For safety policy, test the complete ASGI path with real persistence and
concurrency; do not rely on isolated repository retry tests. Keep accepted
facts immutable and append later attempts. Any v1 contract addition must have
an old-payload validation test. Any staging restore must test every failure
before publication and assert no staging path remains.

Related ticket: `REVIEW-FINAL-FIX`.
