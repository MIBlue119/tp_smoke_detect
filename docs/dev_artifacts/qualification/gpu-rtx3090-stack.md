# GPU-101 RTX 3090 stack qualification receipt

Generated (UTC): 2026-08-24T00:16:19Z

> Profile: rtx3090; image: nvcr.io/nvidia/deepstream:7.0-triton-multiarch@sha256:c11befa808af8270e8ea0d0d7cc7cabbda08a2f496ee30b95f7acba5dce81759. This receipt is fail-closed and does not constitute the one-stream or 20-stream GPU qualification owned by GPU-107.

## Overall status

- **unqualified**
- Host probe: pass; OS: pass; architecture: pass; GPU/compute capability: pass; driver branch: pass.
- Docker/NVIDIA runtime: pass; registry manifest: pass; exact image import: fail; container GPU probe: blocked-image; SBOM: failed.

## Pinned runtime identity

| Field | Expected | Evidence |
|---|---|---|
| Host OS | Ubuntu 22.04 x86_64 | captured below |
| GPU | NVIDIA GeForce RTX 3090 / compute capability 8.6 | captured below |
| Driver | R580 | captured below |
| Container | DeepStream 7.0 Triton multiarch, linux/amd64 | image-pins.yaml and manifest probe |
| Container CUDA | 12.2 | NVIDIA DeepStream 7.0 compatibility row |
| Container TensorRT | 8.6.1.6 | NVIDIA DeepStream 7.0 compatibility row |
| Bundled Triton | 23.10 | DeepStream 7.0 release notes |
| SBOM | required before release | command/status below |

## Host evidence

text-evidence-start
Linux tesla 6.8.0-106-generic #106~22.04.1-Ubuntu SMP PREEMPT_DYNAMIC Fri Mar  6 08:44:59 UTC  x86_64 x86_64 x86_64 GNU/Linux
PRETTY_NAME="Ubuntu 22.04.5 LTS"
VERSION_ID="22.04"
VERSION_CODENAME=jammy
NVIDIA GeForce RTX 3090, 8.6, 580.126.09, 24576 MiB
server=28.3.3 default=runc
nvidia_runtime=present
text-evidence-end

## Image/import evidence

text-evidence-start
registry_manifest=available
         digest: sha256:34e56b46f00a2c79acb42bfd4f3a977753607a99179520744389ce40ce96eb59
            architecture: amd64
            os: linux
         digest: sha256:3579dec5816b1df3d327f8b8c62b0cc55579ba912a32f919d3942c99edfdb9cd
            architecture: arm64
            os: linux
import=pull-failed
fcd74f2f599b: Waiting
e1a16d35e1c1: Waiting
237fe91f3a5d: Pulling fs layer
fcfb425631fb: Waiting
1cf4294f0699: Pulling fs layer
237fe91f3a5d: Waiting
707e32e9fc56: Download complete
894330fe1bf5: Verifying Checksum
894330fe1bf5: Download complete
707e32e9fc56: Pull complete
local_image=absent-or-uninspectable

Error response from daemon: No such image: nvcr.io/nvidia/deepstream:7.0-triton-multiarch@sha256:c11befa808af8270e8ea0d0d7cc7cabbda08a2f496ee30b95f7acba5dce81759
text-evidence-end

## Container evidence

text-evidence-start
container probes blocked because exact image is not imported
text-evidence-end

## SBOM evidence

- Command: docker sbom --format cyclonedx-json nvcr.io/nvidia/deepstream:7.0-triton-multiarch@sha256:c11befa808af8270e8ea0d0d7cc7cabbda08a2f496ee30b95f7acba5dce81759
- Result: failed

## Release interpretation

- Missing image, SBOM, failed container probe, or unsupported host keeps GPU readiness false.
- The optional LFM2-VL reviewer image is not part of this receipt and is not silently substituted.
- GPU-107 must still execute real NVDEC, the R5 model roles, Triton, deterministic decisions, audit persistence, and muted shadow audio before any lab claim.
- Reproduce with: scripts/qualify_gpu_host.sh --profile rtx3090.
