# GPU-106 memory: fail-closed sealed evaluation and workload manifests

- **Context:** The GPU release needs local crop-head calibration, a no-VLM
  baseline versus optional reviewer comparison, and a reproducible 20-camera
  workload without committing media or weights.
- **Symptom:** Existing manifests only carried camera-grouped IDs; calibration
  and ablation APIs did not express governance, sealed coverage, reviewer
  resource limits, or a hashed workload contract.
- **Hypotheses:** Metadata-only governance and immutable event/workload digests
  can make the process reproducible while keeping pixels in the local store.
- **Root cause:** Dataset governance, sealed-label ownership, hard-negative
  vocabulary, replay workload identity, and promotion thresholds were implicit.
- **Fix:** Added registration gates, camera/track/person/day leakage checks,
  hard-negative taxonomy, non-sealed calibration receipts, sealed event-set and
  Wilson-interval ablations, reviewer memory/latency/license gates, promotion
  thresholds, and `replay-20-streams.json` with a stable SHA-256.
- **Verification:** Full Python gates pass (`188 passed, 1 skipped`), Ruff and
  mypy pass, native Debug/Release CTest pass, and the replay loader verifies
  the checked-in 20-stream digest and rejects tampering.
- **Prevention:** Keep dataset/media/weight acquisition outside Git. Require
  real clip hashes, explicit privacy/retention/consent dispositions, and sealed
  labels held outside training/calibration. Treat synthetic replay metadata as
  schema evidence only; never convert it into a load or quality claim.
- **Related commit:** `aced79d`.
