# Worktree and ignore integration hazards

## Context

The factory ran independent Luna tickets in Git worktrees and merged each verified branch into `develop`.

## Symptom

The first domain merge omitted `src/tp_smoke_detect/domain/models/`, and a later worker briefly created duplicate edits in the canonical checkout while its authoritative work remained in its worktree.

## Discarded hypotheses

- The domain imports were broken because MyPy could not resolve a namespace package.
- Git lost tracked files during the merge.
- The canonical edits were user work that had to be preserved separately.

## Root cause

The repository ignore rule `models/` matched model directories at every depth, so Git silently excluded the domain model package. The duplicate canonical edits were run-owned copies from a worker command that did not retain its assigned worktree directory.

## Fix

The ignore rule is anchored as `/models/`. The missing domain files were committed from the ticket worktree. Canonical duplicate edits were compared with the authoritative worktree result and removed before merging the ticket branch. Shared-file tickets are now integrated serially with a clean-tree check before each merge.

## Verification

The integrated repository passes Ruff format/lint, strict MyPy, the full Pytest suite, and the synthetic CPU replay demo. `git status --short` is checked before every merge.

## Prevention

- Anchor repository-root artifact ignores with a leading slash.
- Give every worker an absolute worktree path and require a clean final status.
- Treat shared persistence, API, migration, schema, and lock files as serial integration surfaces even when feature logic is independent.
- Compare actual branch contents and canonical status; do not trust a worker summary alone.

## Related commits

- `152ab4b` tracks the omitted domain model package and anchors the ignore rule.
- `25e3de9` records the integrated quality-gate repair.
- `6018e2d` reconciles the shared API surface for audio and observability.
