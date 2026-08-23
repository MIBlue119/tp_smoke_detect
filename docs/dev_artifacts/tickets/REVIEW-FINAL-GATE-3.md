# REVIEW-FINAL-GATE-3 — release gate 3 remediation

Status: implementation complete; verification is recorded on the release branch

## Findings closed

- #1 restore rollback now assigns the previous-tree ownership only after the
  first live-tree rename succeeds; an injected first-rename failure preserves
  the untouched destination.
- #2 audio reconciliation records an explicit `uncertain` in-doubt state. It
  remains a resend fence, while the matching reservation token may still
  finalize the accepted controller result. Reconciliation keeps the original
  attempt timestamp and records its observation time in `reconciled_at` and
  the attempt history.
- #3 mounted camera YAML is reconciled on every bootstrap. Changed profiles
  overwrite the persisted zone/flags, and removed config-managed profiles are
  disabled and marked `deactivated_by_config`.
- #4 the audio API rejects inactive or disabled cameras before policy evaluation
  or controller I/O.
- #5 HTTP receipt acceptance is rejected at `accepted_at >= expires_at`,
  including the exact boundary.
- #6 canonical receipt `created_at` is immutable across reconciliation, so
  cooldown/hour/day windows retain the physical attempt time.
- #7 `scripts/qualify_cpu.py --check` runs the CPU gate and prints evidence
  without creating a durable artifact; the required clean-checkout command in
  `AGENTS.md` now uses this mode.

## Adversarial evidence

Focused tests cover fault-injected restore, owner I/O concurrent with
reconciliation, owner finalization after an in-doubt transition, mounted
profile change/removal across restarts, disabled-camera ASGI requests, exact
expiry, and read-only qualification. The complete verification receipt is
recorded under `docs/dev_artifacts/qualification/`.

## Verification

- `uv run ruff format --check .`: pass
- `uv run ruff check .`: pass
- `uv run mypy src tests`: pass (`79` source files)
- `uv run pytest -q`: `136 passed`, one expected target-hardware skip
- schema generation and tracked schema comparison: pass
- camera/policy validation, synthetic demo, and Compose config: pass
- CPU qualification receipt: `qualification/2026-08-24-rel-801-gate-3.md`
- native Debug and Release CMake/CTest: `1/1` pass each
- pinned CPU container build: pass

## Remaining qualification boundary

This closes software/reference release findings only. GPU/DeepStream capacity,
site audio approval, model quality, 24-hour soak, and external deployment gates
remain explicitly unrun as documented by INT-701.
