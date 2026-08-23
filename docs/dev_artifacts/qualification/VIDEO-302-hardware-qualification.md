# VIDEO-302 hardware qualification gate

Status: **not run on this checkout**

The repository contains a CPU reference build and a compile-time DeepStream
adapter boundary. No NVIDIA SDK, target GPU, RTSP camera, Triton service, or
DeepStream image was available during this implementation. Therefore this
artifact intentionally does not claim one-stream smoke, 20-stream capacity,
VRAM limits, p95 latency, or 24-hour stability.

## Required receipt

Run on the accepted host after importing a digest-pinned media image:

```bash
MEDIA_IMAGE='nvcr.io/nvidia/deepstream:9.1-devel@sha256:<digest>' \
TRITON_IMAGE='nvcr.io/nvidia/tritonserver:26.05-vllm-python-py3@sha256:<digest>' \
  scripts/qualify_media_host.sh
```

Then record host OS/architecture, GPU model, driver, image digests, imported
library versions, one replay stream, one Triton health/inference request, and
the bounded 20-stream soak. Include p95 candidate latency, dropped frames,
per-camera queue depth, GPU/VRAM and NVDEC utilization, stream recovery time,
and verifier-failure isolation.

The supported-stack matrix is the source for the candidate version row. A
qualification receipt must be produced before using the DeepStream target for
production or asserting AE6/R3 capacity.
