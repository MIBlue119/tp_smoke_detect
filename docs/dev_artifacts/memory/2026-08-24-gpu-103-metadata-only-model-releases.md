# GPU-103 memory: metadata-first model releases

## context

GPU model candidates are public and often large, mutable, multi-file, and
subject to separate commercial or research terms. The base service must stay
offline-safe and must not commit weights or plans.

## symptom

A model name or a reachable URL is insufficient evidence for acquisition,
promotion, or TensorRT readiness. A URL can move, a license can exclude the
agency use case, and an engine can load while targeting another GPU/runtime.

## discarded hypotheses

- Downloading model files during container startup would make deployment
  reproducible. It instead expands runtime egress and hides provenance.
- A source URL alone can serve as a model revision. It cannot establish bytes,
  parent lineage, or rollback identity.
- CUDA visibility proves TensorRT compatibility. It does not bind the plan to
  compute capability, TensorRT, calibration, input contract, or build image.

## root cause

The existing CPU registry records release hashes but did not yet express the
full acquisition and engine identity tuple needed by the mandatory GPU plan.

## fix

Added immutable dataclasses for acquisition records, model artifacts, engine
bindings, repository manifests, local verification receipts, and explicit
acquisition receipts. Added a real-source metadata catalog and Triton configs;
all unresolved fields intentionally fail validation. Acquisition is offline by
default and only permits an explicit, approved, hash-and-size-checked HTTPS
download.

## verification

Eight focused repository tests pass, the existing release/export tests pass,
Ruff and MyPy pass, and the checked-in catalog emits a deterministic blocked
receipt naming missing hashes, license approval, SBOM, lineage, engine, and
rollback evidence.

## prevention

Keep weights/plans outside Git, require the generated receipt in the release
checklist, never enable runtime URL fetching, and reject any TensorRT engine
whose identity hashes or target runtime differ from the model manifest.

## related commit

GPU-103 implementation commit (see `git log` on the feature branch).
