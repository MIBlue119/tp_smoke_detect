# VIDEO-302 media worker

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

## DeepStream profile

The NVIDIA adapter is deliberately opt-in:

```bash
cmake -S native/deepstream -B native/deepstream/build-gpu \
  -DTP_SMOKE_DETECT_ENABLE_DEEPSTREAM=ON \
  -DDEEPSTREAM_SDK_ROOT=/opt/nvidia/deepstream/deepstream
```

Configuration fails when the SDK root is missing. A successful compile is not
a runtime qualification. The production candidate is DeepStream 9.1 on
Ubuntu 24.04 with CUDA 13.2, TensorRT 10.16.0.72, GStreamer 1.24.2, and an
NVIDIA driver meeting the supported-stack matrix. Use the pinned image digest
and record the host receipt from `scripts/qualify_media_host.sh`.

The eventual DeepStream graph should keep RTSP/NVDEC, `nvstreammux`, detector,
and tracker on the GPU media plane; candidate publication crosses the typed
metadata boundary. Python policy and persistence remain outside this process.
No one-stream or 20-stream GPU claim is made by this checkout.
