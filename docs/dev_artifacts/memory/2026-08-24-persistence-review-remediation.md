# Persistence review remediation memory

## Context

The independent release validator found four persistence defects: concurrent
requests corrupted a shared SQLite connection, retention left decision copies
in evaluations/audio receipts, restore trusted a tampered archive, and audio
retries retained only the first outcome.

## Symptom

Concurrent inserts raised SQLite interface/locking errors; expired decision IDs
remained queryable through linked tables; restore returned manifest hashes for
tampered bytes; a failed first playback prevented a later accepted playback
from contributing to safety caps and cooldowns.

## Root cause

The repository used one unlocked connection and independent read/insert
idempotency steps. Retention deleted only reviews/decisions. Restore wrote
members directly without comparing manifest inventory or digests. Audio used
`INSERT OR IGNORE` for a single immutable receipt ID.

## Fix

Use a repository-wide reentrant lock and atomic idempotency insert, delete all
linked rows transactionally, stage and verify a complete restore before path
swap, and maintain a latest receipt plus append-only attempt history.

## Verification and prevention

Proof-first tests cover a 1,000-write thread storm, a 100-request idempotency
storm, linked retention cleanup, tampered restore preservation, and
failed-to-accepted audio retry. Keep these tests in the CPU gate and require
manifest validation before any restore destination mutation.

## Related findings

VAL-004, VAL-006, VAL-007, VAL-008.
