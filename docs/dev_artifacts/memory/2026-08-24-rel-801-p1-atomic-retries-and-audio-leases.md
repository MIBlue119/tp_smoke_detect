# Release P1 memory: atomic retries and audio leases

## Context

The final release review found that evaluation retries and audio delivery
reservations were only serialized inside one process. Independent SQLite
connections could both reclaim one failed evaluation, and an audio contender
could overwrite an in-flight sender's pending receipt.

## Symptom and hypotheses

Failed evaluation retries could produce two decisions and two metric effects.
Audio controller exceptions left a pending row consuming cooldown/capacity, and
contenders could clear that pending summary. The likely causes were a deferred
claim read before the write lock and receipt updates without an owner CAS.

## Root cause

`claim_evaluation` selected a failed row before acquiring a write lock, then
deleted by idempotency key. Decision and evaluation writes committed separately.
Audio playback was represented only by a shared summary status, with no sender
identity or expiry.

## Fix and verification

`BEGIN IMMEDIATE` now encloses failed-row read/reclaim/insert. The SQLite API
commits decision plus evaluation atomically and emits the decision metric only
after success. Audio pending rows include a UUID owner token and expiry;
`finalize_audio_receipt` performs token-matched CAS, exceptions finalize as
failed, and reservation attempts reconcile expired pending rows as released.
Integration tests cover two process connections, retry cardinality and metric
counts, owner contention, raising controllers, and stale pending leases.

## Prevention

Any retryable claim must acquire its lock before reading state. Any external
side effect reservation must include an owner token, bounded lease, and a
terminal CAS API. Keep failure-path tests asserting both durable state and
side-effect cardinality.

Related ticket: `REL-801`.
