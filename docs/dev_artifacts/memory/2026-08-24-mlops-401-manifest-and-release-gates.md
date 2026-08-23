# MLOPS-401: manifest-only MLOps contracts

- **Context:** The service needs local evaluation and model promotion without
  putting identifying camera media or generated weights in repository history.
- **Symptom:** A frame-level split can leak the same camera/track into sealed
  evaluation, and empty confusion classes can be misreported as zero risk.
- **Hypotheses:** Hashing content references and grouping by camera would make
  manifests reproducible; explicit nullable metric values would preserve the
  distinction between no observations and a measured zero rate.
- **Root cause:** Dataset identity, event aggregation, calibration, and model
  provenance had no repository-local contract.
- **Fix:** Added canonical metadata-only manifests, camera-grouped deterministic
  splits, event-level session aggregation, explicit `not_applicable` metric
  states, calibration bins, and immutable release/promotion validation.
- **Verification:** Six focused unit tests pass with Ruff and mypy; tests include
  empty datasets, no positives, missing provenance hashes, and mutable release
  collisions.
- **Prevention:** Keep hashes and approved source/license decisions in Git;
  retain media and weights only in the on-premise artifact/registry store. Do
  not promote a release without dataset/evaluation/calibration/SBOM hashes and
  a rollback target.
- **Related commit:** MLOPS-401 implementation commit (see branch history).
