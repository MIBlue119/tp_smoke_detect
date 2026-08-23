# REVIEW-FINAL-FIX — final release review remediation

Status: implementation complete; release verification recorded below

## Findings closed

- FINAL-001: SQLite row decoding now normalizes persisted booleans, and the
  ASGI test proves an eligible persisted decision can announce.
- FINAL-002: automatic audio policy evaluation uses a database-level
  `BEGIN IMMEDIATE` reservation with durable cooldown/hour/day checks before
  adapter I/O; independent SQLite repositories/apps cannot reserve two slots.
- FINAL-003: accepted playback is immutable in the safety summary row while
  every retry remains in `audio_receipt_attempts`.
- FINAL-004: SQLite claims an idempotency key before decision creation and
  completes the placeholder atomically; concurrent ASGI retries create one
  evaluation and one decision.
- FINAL-005: API mute expiry requires an aware datetime; legacy persisted naive
  values are interpreted as UTC at the repository read boundary.
- FINAL-006: v1 metadata fields are optional for ingestion compatibility while
  current producers continue to emit metadata; generated v1 schemas and legacy
  fixture coverage are updated.
- FINAL-007: restore archive validation and extraction are inside the staging
  cleanup transaction, including missing-archive and integrity failures.
- FINAL-008: top-level complex environment variables use JSON decoding parity
  with `BaseSettings` after YAML bootstrap, and nested overrides are applied
  in a deterministic second pass regardless of environment insertion order.
- RELEASE-003: post-claim evaluation failures now become terminal,
  retryable `failed` rows; the next request atomically reclaims the key.

## Verification evidence

Focused proof-first tests cover two independent SQLite apps, both mixed
environment insertion orders, and a one-shot post-claim failure/retry. The
full unit/contract/integration/e2e suite passes with `113 passed`.

Full release commands and results are recorded in
`docs/dev_artifacts/qualification/2026-08-24-rel-801-final-fix.md`.

## Unresolved external gates

This ticket does not claim GPU/DeepStream capacity, site acceptance, image
digest/SBOM completion, model provenance, or production audio approval.
