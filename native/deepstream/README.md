# GPU-102 DeepStream 7.0 media worker

This directory owns the native boundary between camera media and the Python
`track.candidate.v1` contract. The default build is a CPU reference path and
does not require CUDA, GStreamer, DeepStream, RTSP, model weights, or a broker.

## Reference build

```bash
cmake -S native/deepstream -B native/deepstream/build -DBUILD_TESTING=ON
cmake --build native/deepstream/build --parallel
ctest --test-dir native/deepstream/build --output-on-failure
```

`MediaWorker` maintains one bounded FIFO per configured camera. A full queue
drops the oldest sample and increments that camera's counter. Freshness and
reconnect state are per-camera, so a stalled stream does not mark healthy
streams degraded. `CandidateEnvelope::to_json()` emits only structured fields;
raw pixels and free-form model prose have no representation in the native type.
`CandidatePublisher` adds a second bounded FIFO at the broker boundary and
retains the first failed item for a later transport retry.

## DeepStream 7.0 GPU profile

The NVIDIA adapter is deliberately opt-in:

```bash
cmake -S native/deepstream -B native/deepstream/build-gpu \
  -DTP_SMOKE_DETECT_ENABLE_DEEPSTREAM=ON \
  -DDEEPSTREAM_SDK_ROOT=/opt/nvidia/deepstream/deepstream
```

Configuration fails when the SDK root, DeepStream metadata headers, or
`nvdsgst_meta` library is missing. A successful compile is not a runtime
qualification. The GPU release row is the digest-pinned DeepStream 7.0 dGPU
Triton image on Ubuntu 22.04/Ampere with the host and container identities
recorded by GPU-101. The native graph requires the runtime elements
`nvurisrcbin`, `nvstreammux`, `nvinfer`, `nvtracker`, `nvinferserver`,
`tpmediapipepose`, and `tpmediapipehands`. The last two are explicit MediaPipe
Tasks C++ GPU/EGL plugin boundaries; an identity element or CPU fallback is not
accepted.

The graph is assembled programmatically and uses `nvurisrcbin` for RTSP/file
sources, bounded `nvstreammux`, PeopleNet Transformer, NvDCF, MediaPipe pose
and hand plugins, and `gst-nvinferserver` for GPU-local SigLIP 2 crops. The
metadata pad probe publishes only the typed, pixels-free candidate envelope.
Source reconnect errors are scoped to the affected camera and candidate
publication is bounded by `CandidatePublisher`.

When a proprietary SDK or plugin is not imported, `start()` returns an
`unqualified:` error naming the missing runtime elements or artifacts. It does
not report GPU readiness and does not produce a qualification pass.

The graph configuration shapes live in `native/deepstream/config/`; exact
model artifacts and TensorRT plans are supplied by the immutable GPU-103 model
bundle and are intentionally absent from Git.

The DeepStream graph keeps RTSP/NVDEC, `nvstreammux`, detector, and tracker on
the GPU media plane; candidate publication crosses the typed metadata boundary.
Python policy and persistence remain outside this process.
No one-stream or 20-stream GPU claim is made by this checkout until GPU-107
produces the required receipts.

The production image invokes the repository-built `media_publisher` executable
(not the upstream `deepstream-app`). Its `--reference` mode is used by the
contract gate; the default mode constructs the DeepStream graph and publishes
typed candidates through the configured callback. The image must supply the
external model files and one-stream readiness receipt before the process is
allowed to start.
