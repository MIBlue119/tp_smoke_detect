# Learning — final-pass safety and release-build closure

## Context

The release gate exercised the service across ASGI retries, independent SQLite
connections, failed audio controllers, mismatched HTTP receipts, unbounded metric
reasons, and an optimized native build. These paths are easy to miss when the
happy-path unit suite is green.

## Symptoms and root causes

- Evaluation requests trusted a caller-provided `audio_eligibility` bit, and
  idempotency keys had no request fingerprint or owner lease.
- A post-commit metrics exception reopened a completed evaluation, which could
  duplicate a decision on retry.
- Failed audio delivery was treated as announced, and expiry reconciliation
  released pending reservations while their controller call could still run.
- Prometheus reason labels accepted caller/provider text without a bounded map.
- The native test used `assert`, so Release builds dereferenced an empty optional
  after assertions were compiled out.

## Fix and prevention

Keep eligibility server-owned; bind retries to a canonical fingerprint and a
time-bounded claim lease; keep observational metrics outside the commit failure
path; count failed/uncertain audio attempts conservatively; never auto-release an
audio lease that may still have an active owner; map free-form reason codes to
`other`; and use explicit checks in tests that must run under `NDEBUG`.

Every one of these boundaries now has a focused regression test in
`tests/integration/test_final_release_hardening.py`. The release checklist must
run both Debug and Release native CTest profiles, not only the default build.

## Verification and related change

Verified with 124 Python tests, Ruff, MyPy, and native Debug/Release CTest. Related
ticket: `REVIEW-FINAL-PASS-2026-08-24`; release commit is recorded by the parent
release owner after push.
