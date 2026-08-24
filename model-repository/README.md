# GPU model repository

This directory contains immutable metadata and Triton configuration only. It
does not contain model weights, TensorRT plans, tokenizer files, private media,
or credentials. The checked-in `manifest/model-release.json` is deliberately
blocked because the exact source revisions, content hashes, artifact sizes,
SBOMs, legal dispositions, exports, and rollback targets have not yet been
recorded in the approved local artifact store.

## Promotion sequence

1. Review each canonical source and license with procurement/legal.
2. Fill the exact source revision, expected byte size, and SHA-256 only from an
   approved staging download. Never infer a hash from a model name or URL.
3. Export the model and record the export hash, dataset-manifest hash,
   calibration hash, parent checkpoint, and model-card receipt.
4. Generate and scan an SBOM; record its hash and vulnerability disposition.
5. Build TensorRT engines in the pinned DeepStream 7.0 / CUDA 12.2 / TensorRT
   8.6.1.6 RTX 3090 profile. Bind each plan to model, export, input-contract,
   calibration, build-image, GPU compute capability, CUDA, TensorRT, and plan
   hashes.
6. Set a prior immutable rollback target and run the sealed evaluation plus
   rollback rehearsal before changing the promotion state.

The optional LFM2-VL reviewer remains disabled by default. It cannot be
acquired or loaded until its exact revision, LFM Open License disposition,
Python-backend image/SBOM, memory budget, timeout behavior, and target-GPU
qualification are complete.

Use the offline verifier to emit a fail-closed receipt:

```bash
uv run python scripts/verify_model_artifacts.py \
  --manifest model-repository/manifest/model-release.json \
  --receipt docs/dev_artifacts/qualification/model-artifact-receipts/gpu-103-baseline.json
```

Acquisition is intentionally a separate, explicit operation. It requires an
approved manifest and `--allow-network`; runtime services never invoke it:

```bash
uv run python scripts/verify_model_artifacts.py \
  --manifest model-repository/manifest/model-release.json \
  --artifact-root /approved/local/model-store \
  --acquire <approved-artifact-id> --allow-network
```
