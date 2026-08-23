# Review domain/API contract and ASGI testing

## Context

The release review found that audio routing trusted request context, persisted
decision mappings skipped explicit audio eligibility, unknown evidence names could
inflate the independent-channel count, and contract/API tests did not fully prove
the advertised surfaces.

## Symptom

A caller could submit a fresh zone or timestamp to influence cooldown, mute, and
cap decisions. A stored `verified` mapping without `audio_eligibility` could pass
the audio policy. Arbitrary evidence names could produce a verified decision.
OpenAPI and the API/database acceptance test were incomplete.

## Root cause

Policy inputs were treated as trusted request data; the mapping adapter had a
weaker eligibility predicate than typed decisions; evidence canonicalization used
unknown names as their own families; and integration tests called the repository
without crossing the ASGI boundary.

## Fix

Resolve routing from the persisted camera profile and time from the server clock;
require an explicit eligibility opt-in; allowlist canonical evidence families;
require shared delivery metadata on every emitted v1 envelope; move scoped-mute
validation into the Pydantic model; and exercise routes through a minimal ASGI
driver. Generated schemas and protobuf documentation are updated together.

## Verification

Ruff, mypy, and the full CPU test suite pass: 89 passed, 1 opt-in hardware test
skipped. Contract tests verify metadata is required and schemas match generation.

## Prevention

Keep API-to-database tests at the ASGI boundary, regenerate checked-in schemas
after contract edits, and treat request context as untrusted unless it is derived
from persisted configuration or a server-owned clock.

## Related ticket

`docs/dev_artifacts/tickets/REVIEW-DOMAIN-API.md`
