# GPU-101 image import blocker

## Context

GPU-101 freezes the mandatory RTX 3090 lab row and verifies that the offline
runtime can expose a real NVIDIA device before downstream DeepStream, model,
deployment, and load tickets proceed.

## Symptom

The host was compatible with the planned row, but pulling the exact
`nvcr.io/nvidia/deepstream:7.0-triton-multiarch` image did not complete within a
bounded probe window. The image was absent from the local Docker cache, so no
container CUDA, DeepStream, Triton, or SBOM evidence existed.

## Discarded hypotheses

- The host driver was not the cause: `nvidia-smi` reported RTX 3090 compute
  capability 8.6 and driver 580.126.09 on Ubuntu 22.04.5.
- Host CUDA 12.8 was not treated as proof of the container CUDA 12.2 row.
- A tag-only pull or DeepStream 9.1 substitution was not accepted.

## Root cause

The large NVIDIA container image was not imported into the local cache during
the bounded network pull. Registry metadata was available and resolved to the
immutable manifest-list digest, but layer download completion was not proven.

## Fix

Added a digest-pinned image row and a fail-closed qualification script. It
records host, runtime, manifest, import, container, and SBOM status separately
and returns non-zero unless every required evidence boundary is present.

## Verification

The script's current receipt is explicitly `unqualified`; downstream service
readiness cannot infer a pass from host CUDA visibility. Re-run after importing
the pinned image, then execute GPU-107's one-stream and 20-stream tests.

## Prevention

Use the checked-in command with the exact digest, retain the image SBOM in the
offline bundle, and require a successful container `nvidia-smi` plus runtime
command probe before opening the GPU readiness gate.

## Related commit

Related commit: `d879159`.
