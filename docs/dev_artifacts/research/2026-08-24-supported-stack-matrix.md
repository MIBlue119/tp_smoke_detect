# RQ2: supported and pinnable stack matrix

**Date:** 2026-08-24  
**Question:** Which exact Python, FastAPI, Pydantic, PyTorch, ONNX Runtime, Triton backend/client, and NVIDIA runtime versions can coexist with the verified DeepStream 9.1 GPU image?  
**Status:** researched; GPU import/stream qualification remains a lab gate  
**Owner:** Luna, framework research

## Executive recommendation

Do not build one environment that imports DeepStream, Triton, PyTorch, ONNX Runtime GPU, and vLLM together. Use four pinned environments with network APIs between them:

1. **`smoke-core` (CPU reference and production control plane):** Python 3.12, FastAPI 0.141.1, Pydantic 2.13.4, `pydantic-settings` 2.15.0, and CPU-only PyTorch/ONNX Runtime when those libraries are needed.
2. **`media-deepstream` (GPU media plane):** DeepStream 9.1 on Ubuntu 24.04 x86_64, CUDA 13.2, TensorRT 10.16.0.72, GStreamer 1.24.2, and NVIDIA driver 595.58.03 or newer as required by NVIDIA’s DeepStream row.
3. **`triton-vllm` (optional gated verifier):** the immutable `nvcr.io/nvidia/tritonserver:26.05-vllm-python-py3` image, resolved by digest in the deployment lock. Its official compatibility row is Python 3.12.3, vLLM 0.19.0 build, CUDA 13.2.1.009, and driver 595.58.03. The 26.05 release notes describe the image contents as vLLM 0.20.1, so the build receipt must record the actual imported version; do not install a second vLLM with pip.
4. **`tao-train` (offline training/export only):** `nvcr.io/nvidia/tao/tao-toolkit:7.1.0-pyt` or `:7.1.0-deploy`, not the running media or core image. NVIDIA lists Python 3.12, PyTorch 2.11.0a0, CUDA 13.2, and TensorRT 10.16.0.72 for the PyTorch container.

The first production candidate should therefore use the CPU reference path plus DeepStream/Triton over a typed boundary. ONNX Runtime GPU/TensorRT execution is not a supported shortcut for the DeepStream image: the official ONNX Runtime table documents TensorRT EP through TensorRT 10.9, not 10.16.

## Compatibility matrix

The labels mean: **documented** is stated by an upstream compatibility table; **pinnable** means a tag/version can be locked; **lab gate** means this repository still needs a build/import/stream receipt on the target host.

| Component | CPU/core profile | GPU/media profile | Evidence and decision |
|---|---|---|---|
| OS / architecture | Ubuntu 24.04 x86_64 | Ubuntu 24.04 x86_64 | DeepStream 9.1 dGPU prerequisite. Keep the control plane on the same OS family for operational parity. |
| Python | CPython 3.12.x; pin the patch version in the image | DeepStream Service Maker/Python only where required; do not embed core dependencies | Triton Python backend ships with Python 3.12 and requires a matching stub. Pin 3.12.3 for Triton-derived images. |
| FastAPI | 0.141.1 | Not installed in media/Triton images | Current official package metadata supports Python 3.12 and Pydantic v2. Keep `fastapi<0.142` in the first lock and update only after contract tests. |
| Pydantic | 2.13.4; `pydantic-settings` 2.15.0 | Not installed in media/Triton images | FastAPI is explicitly based on Pydantic v2. Use the same model package for API and domain schemas; do not use a Pydantic v1 compatibility shim. |
| PyTorch | 2.12.1 CPU wheel for deterministic tests/training stubs | Use the runtime’s own PyTorch only; for a standalone GPU worker use 2.12.1 `cu130` (stable) or the TAO image’s 2.11.0a0 | PyTorch’s 2.12 matrix lists Python >=3.10 and CUDA 13.0 as stable, CUDA 13.2 as experimental; the official previous-version page publishes 2.12.1 CPU and `cu130`/`cu132` commands. Prefer stable `cu130` because CUDA minor compatibility covers a CUDA 13.2 host. |
| ONNX Runtime | 1.27.0 CPU package | Do not use ORT TensorRT EP in `media-deepstream`; if an isolated CUDA EP worker is required, use ORT 1.27.x CUDA 13.0 package in its own image | ORT 1.27.x is documented for CUDA 13.0/cuDNN 9.x. Triton 26.05 bundles ORT 1.24.4, whose documented GPU package is CUDA 12.x. ORT’s TensorRT table stops at TRT 10.9. |
| DeepStream | N/A | 9.1; Ubuntu 24.04; CUDA 13.2; TensorRT 10.16.0.72; GStreamer 1.24.2; driver 595.58.03+ | NVIDIA’s installation page gives this exact dGPU prerequisite row. Rebuild all TensorRT engines after any CUDA/TRT change. |
| Triton server | Optional CPU-only fake/provider path; do not depend on Triton for CPU acceptance | `nvcr.io/nvidia/tritonserver:26.05-vllm-python-py3` by digest for vLLM; use a matching 26.05 SDK/client image for clients | Triton 26.05’s matrix gives Python 3.12.3, CUDA 13.2.1.009, driver 595.58.03; its release notes list ORT 1.24.4 and TRT 10.16.1.11. Keep it process/GPU isolated from DeepStream. |
| Triton backend | Fake provider and HTTP/gRPC client contract | vLLM backend 26.05; TensorRT backend only for engines built inside the exact Triton image | Do not share a TRT 10.16.0.72 engine blindly with a TRT 10.16.1.11 image. A backend image is the compatibility unit. |
| Triton client | Pin the client wheel from the same Triton 26.05 SDK artifact | `nvcr.io/nvidia/tritonserver:26.05-py3-sdk` by digest, or its extracted Python wheel | NVIDIA documents versioned SDK images and client release assets; avoid floating `tritonclient[all]` from PyPI for production. |
| vLLM | Not required | Use the vLLM shipped by the 26.05 image; do not pip-upgrade it | Official compatibility matrix reports a 0.19.0 build while the 26.05 release notes report vLLM 0.20.1. This documentation conflict is a release gate resolved by an image import receipt. |
| TAO | Offline only, if used | `tao-toolkit:7.1.0-pyt` for training/export; `:7.1.0-deploy` for deployment experiments | TAO 7.1 release notes list Python 3.12, PyTorch 2.11.0a0, CUDA 13.2, TRT 10.16.0.72, and vLLM 0.17.1 in its data-services container. Do not combine TAO’s Python environment with FastAPI. |
| NVIDIA driver | None | 595.58.03 minimum for the DeepStream/Triton 26.05 CUDA 13.2 row | One host driver must satisfy both images. Record `nvidia-smi` output and image digests in the qualification receipt. |

## Recommended lock profiles

### CPU reference (`smoke-core-cpu`)

```text
python = 3.12.x                 # exact patch selected by uv lock/image
fastapi = 0.141.1
pydantic = 2.13.4
pydantic-settings = 2.15.0
torch = 2.12.1                   # CPU index, only where needed
onnxruntime = 1.27.0           # CPU package
```

This profile must run all domain, API, replay, schema, and policy tests without NVIDIA libraries, CUDA, Triton, or model weights. It is the takeover baseline for new coding agents.

### GPU production candidate (`gpu-2026-08-ds91`)

```text
host_os = Ubuntu 24.04 x86_64
nvidia_driver >= 595.58.03
media_image = DeepStream 9.1 image, digest required
media_cuda = 13.2
media_tensorrt = 10.16.0.72
media_gstreamer = 1.24.2
triton_image = nvcr.io/nvidia/tritonserver:26.05-vllm-python-py3, digest required
triton_cuda = 13.2.1.009
triton_python = 3.12.3
triton_onnxruntime = 1.24.4 (image-owned; not the core ORT pin)
triton_client = 26.05 SDK artifact, digest/hash required
```

`smoke-core` remains a separate Python image using the CPU/core pins above. If GPU PyTorch is needed by a dedicated model worker, choose `torch==2.12.1` from the official `cu130` index and keep it out of the DeepStream process. Do not use the PyTorch `cu132` build as the first production default because upstream labels CUDA 13.2 experimental for 2.12.

## Conflicts and non-claims

1. **DeepStream 9.1 vs ORT TensorRT EP:** DeepStream requires TRT 10.16.0.72, while ORT’s published TensorRT EP matrix reaches TRT 10.9. There is no official evidence here that ORT 1.27 can load TRT 10.16. Do not claim this combination is supported.
2. **Triton 26.05 vs DeepStream patch level:** Triton’s release notes list TRT 10.16.1.11, not DeepStream’s TRT 10.16.0.72. Keep the images separate and build engines in the image that serves them.
3. **vLLM metadata conflict:** Triton’s compatibility table reports a 0.19.0 vLLM build for the 26.05 image; the release notes list vLLM 0.20.1. The lock must use the image digest and record `import vllm; print(vllm.__version__)`; a floating pip dependency is prohibited.
4. **TAO is not the service runtime:** TAO’s PyTorch 2.11.0a0 and vLLM 0.17.1 are image-specific training/data-service pins. They are not a reason to downgrade the core API or Triton image.
5. **Python package “latest” is not a qualification:** FastAPI/Pydantic/ORT/PyTorch releases move independently. Every update requires a new lock, import receipt, API contract test, and (for GPU images) DeepStream/Triton smoke test.

## Required qualification receipt

RQ2 is research-complete but not hardware-qualified on this checkout. U7/U10 must produce `docs/dev_artifacts/qualification/<date>-rq2-build-import.md` containing:

```bash
uname -a
cat /etc/os-release
nvidia-smi --query-gpu=name,driver_version,memory.total --format=csv
docker image inspect --format '{{index .RepoDigests 0}}' "$MEDIA_IMAGE" "$TRITON_IMAGE"
python -VV
python -c 'import fastapi,pydantic; print(fastapi.__version__,pydantic.__version__)'
python -c 'import torch; print(torch.__version__, torch.version.cuda, torch.cuda.is_available())'
python -c 'import onnxruntime as ort; print(ort.__version__, ort.get_available_providers())'
python -c 'import tritonclient; print(tritonclient.__version__)'
python -c 'import vllm; print(vllm.__version__)'  # Triton vLLM image only
deepstream-app --version
tritonserver --version
```

The GPU gate additionally runs one replay stream, one Triton health/inference request, and a bounded 20-stream soak. It records p95 latency, dropped frames, GPU memory, queue depth, and whether a verifier failure leaves DeepStream healthy.

## Sources (official primary documentation)

- [NVIDIA DeepStream installation and platform compatibility](https://docs.nvidia.com/metropolis/deepstream/dev-guide/text/DS_Installation.html)
- [NVIDIA DeepStream 9.1 migration guide](https://docs.nvidia.com/metropolis/deepstream/dev-guide/text/DS_Migration_guide.html)
- [NVIDIA Triton release compatibility matrix](https://docs.nvidia.com/deeplearning/triton-inference-server/user-guide/docs/introduction/compatibility.html)
- [NVIDIA Triton 26.05 release notes](https://docs.nvidia.com/deeplearning/triton-inference-server/release-notes/rel-26-05.html)
- [NVIDIA Triton Python backend Python-version rules](https://docs.nvidia.com/deeplearning/triton-inference-server/user-guide/docs/python_backend/README.html)
- [NVIDIA Triton client distribution and SDK images](https://github.com/triton-inference-server/client)
- [NVIDIA TAO 7.1 release notes and compute stack](https://docs.nvidia.com/tao/tao-toolkit/latest/text/release_notes.html)
- [PyTorch release compatibility matrix](https://github.com/pytorch/pytorch/blob/main/RELEASE.md)
- [PyTorch previous-version install commands](https://pytorch.org/get-started/previous-versions/)
- [ONNX Runtime CUDA execution-provider requirements](https://onnxruntime.ai/docs/execution-providers/CUDA-ExecutionProvider.html)
- [ONNX Runtime TensorRT execution-provider requirements](https://onnxruntime.ai/docs/execution-providers/TensorRT-ExecutionProvider.html)
- [FastAPI release notes](https://github.com/fastapi/fastapi/blob/master/docs/en/docs/release-notes.md)
- [FastAPI package metadata](https://pypi.org/project/fastapi/)
- [Pydantic package metadata](https://pypi.org/project/pydantic/)
- [Pydantic Settings package metadata](https://pypi.org/project/pydantic-settings/)

## Handoff tickets

- **RQ2-A / U1:** add CPU/core pins and a hash-locked `uv.lock`; include the import commands above in CI.
- **RQ2-B / U7:** build DeepStream 9.1 media image and record host/image compatibility on L40S and accepted pilot hardware.
- **RQ2-C / U10:** pull Triton 26.05 vLLM and SDK images by digest; resolve the vLLM 0.19/0.20.1 documentation discrepancy from the container itself.
- **RQ2-D / U11:** run the 20-stream and verifier-isolation soak; do not promote automatic audio until the receipt passes.
- **RQ2-E / release gate:** reject any TensorRT engine whose build image/version differs from its serving image unless rebuilt and requalified.

