# REVIEW-DOMAIN-API

## Scope

Fixes the validated release-review findings VAL-001, VAL-002, VAL-003, VAL-011,
VAL-013, VAL-014, VAL-015, and VAL-016 on `fix/review-domain-api`.

## Changes

- VAL-001: audio requests derive `zone_id` from the persisted camera profile and
  use the server UTC clock. Caller-supplied context is no longer part of the API
  request model; configured camera profiles are seeded into the repository.
- VAL-002: mapping decisions must explicitly contain `audio_eligibility: true`
  in addition to a verified outcome.
- VAL-003: evidence aggregation ignores unknown channel names; only the canonical
  allowlist can satisfy independent-evidence requirements.
- VAL-011: candidate, decision, and audio v1 envelopes share required
  `event_id`, `correlation_id`, `producer`, and `occurred_at` metadata. Producers,
  protobuf ABI files, generated JSON schemas, replay fixtures, and tests were
  updated. The compatibility policy is explicit: old records may be migrated at
  a read boundary, but emitted v1 envelopes cannot omit delivery identity.
- VAL-013: OpenAPI regression coverage includes `/metrics` and
  `/v1/audio/requests`, its method, and its request schema.
- VAL-014: scoped mute invariant is enforced by `AudioMuteCreate` itself and has
  negative unit coverage.
- VAL-015: Ruff import diagnostics in the four release-added domain tests are
  fixed and the repository-wide lint gate passes.
- VAL-016: integration coverage now issues real ASGI requests for reviews and
  audio against SQLite, in addition to repository assertions.

## Verification

```text
uv run ruff check .
uv run ruff format --check .
uv run mypy src tests
uv run pytest -q
```

Result: `89 passed, 1 skipped` (the skipped test is the opt-in target hardware
qualification scaffold).

## Blockers

None for the assigned findings.
