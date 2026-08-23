# MODEL-202 — Baseline model and VLM ablation

Status: implemented locally on `feature/model-202-baseline-ablation`
Unit: U10
Traceability: R2, R3, R5, R8, R11, R12; KTD7, KTD10; RQ1/RQ4/RQ5/RQ7/RQ8

## Delivered

- Added `ml.evaluation.ablation` with deterministic calibration from a caller-
  supplied non-sealed split, fixed sealed-event IDs, optional VLM fail-closed
  projection, crop-strategy comparison, and Wilson confidence intervals.
- Added `ml.training` CPU-only deterministic named-class baseline artifact
  export/load and byte-stable output verification. No weights or media are
  bundled.
- Added `ml.registry.model_card` model-card and provenance receipt generation;
  reports retain release hashes and explicitly mark `fixture_only` evidence and
  `lab_required` qualification.
- Added `ml/configs/baseline/cpu-reference.json` documenting named classes,
  crop variants, VLM failure policy, and required lab gates.
- Added proof-first unit and integration tests for calibration, with/without-VLM
  equivalence and fail-closed behavior, crop reports, confidence bounds,
  checksum-verified export/load, and model-card provenance output.

## Verification

```text
uv run pytest -q tests/unit/ml tests/integration/ml  # 12 passed
uv run pytest -q tests/unit tests/integration tests/contract  # 60 passed
uv run ruff format --check ml tests  # 34 files already formatted
uv run ruff check ml tests/unit/ml tests/integration/ml  # All checks passed
uv run mypy ml tests/unit/ml tests/integration/ml  # Success
uv run mypy src tests ml  # Success
```

Repository-wide `uv run ruff check .` still reports four pre-existing import
ordering diagnostics in `tests/unit/domain/`; no MODEL-202 file is involved.

## Explicit lab gates

This ticket does not download datasets or model checkpoints and does not claim
site precision, recall, latency, or 20-stream capacity. Before any promotion,
the team must provide a consented camera/site sealed event set, exact approved
model and dataset provenance/licence receipts, target-GPU runtime import and
latency/memory measurements, and the silent-period acceptance report. Automatic
audio remains outside this CPU artifact's authority.
