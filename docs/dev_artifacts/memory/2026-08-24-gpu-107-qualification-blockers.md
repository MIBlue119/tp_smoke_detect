# GPU-107 qualification blockers

## Context

The RTX 3090 lab qualification harness was implemented and executed against
the pinned DeepStream 7.0 Triton image, the checked-in model release manifest,
and the GPU replay-pack contract.

## Symptom

The host exposes the requested RTX 3090 and R580 driver, but the qualification
cannot start the real DeepStream graph. The exact image pull remains incomplete;
the model release contains unresolved metadata; and the replay pack is a
synthetic metadata fixture without local clip hashes.

## Discarded hypotheses

- Host CUDA 12.8 was not treated as proof of the container CUDA 12.2 runtime.
- A successful `nvidia-smi` host probe was not treated as proof of DeepStream,
  TensorRT, NVDEC, Triton, or model readiness.
- The existing metadata-only 20-stream JSON was not treated as representative
  media or a capacity result.
- A partial image layer cache was not treated as an imported digest-pinned
  image.

## Root cause

GPU-101 image import and GPU-103/GPU-106 external artifact gates are not yet
complete. No approved local replay bundle exists for GPU-107 to exercise.

## Fix

Added a machine-readable, fail-closed qualification runner. It records all
prerequisite identities and command outcomes, binds receipts to image/model/
replay hashes, requires complete telemetry from a reviewed real-runtime
command, and writes exact recovery steps instead of fabricating a pass.

## Verification

Five focused harness tests pass. The RTX 3090 host probe passes. The exact
image pull was attempted for bounded windows totaling approximately three
minutes and remains unimported. The resulting receipt is `blocked`.

## Prevention

Keep GPU qualification opt-in and separate from CPU CI. Require an imported
digest, SBOM/vulnerability disposition, complete model/engine receipts, sealed
consented local clips, real runtime telemetry, and explicit fault-injection
evidence before assigning a lab-qualified label.

## Related commit

Recorded with the GPU-107 implementation commit.
