# MLOPS-401 — Dataset, evaluation, and registry contracts

## Scope delivered

- `ml/datasets/manifest.py` stores metadata-only dataset items with SHA-256
  content identifiers, documented source/license fields, named confusion
  classes, deterministic canonical serialization, and camera-grouped splits.
- `ml/evaluation/metrics.py` aggregates camera/track sessions at event level,
  reports precision/recall/F1, latency, eligibility coverage, calibration bins,
  and a named trigger-rate/state for each confusion class.
- `ml/registry/release.py` defines immutable model release metadata and local
  promotion validation for model, dataset, evaluation, calibration, SBOM,
  provenance, runtime, and rollback hashes.
- No media, weights, URLs, credentials, or private paths are stored in Git.

## Verification

Commands run on 2026-08-24:

```text
uv run ruff format ml tests/unit/ml
uv run ruff check ml tests/unit/ml
uv run mypy ml tests/unit/ml
uv run pytest -q tests/unit/ml       # 6 passed
```

Proof-first tests cover camera/track leakage prevention, deterministic output,
invalid provenance, empty classes, no-positive metrics, continuous-session
deduplication, named confusion trigger rates, and release promotion/rollback
gates.

## Known integration note

The repository-wide `ruff check .` currently reports four pre-existing import
ordering errors in domain tests; none are in this ticket's files. The full
existing suite passed (`35 passed`) before the ticket tests were added.
