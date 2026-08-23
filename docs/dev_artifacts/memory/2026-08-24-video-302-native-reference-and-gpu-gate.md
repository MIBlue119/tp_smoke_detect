# VIDEO-302 native reference and GPU gate

## Context

U7 needs a native media boundary while the repository's default acceptance path
must remain CPU-only. DeepStream 9.1, CUDA 13.2, TensorRT 10.16.0.72, and the
target NVIDIA driver are lab dependencies, not portable development tools.

## Problem

Adding GStreamer/DeepStream to the base build would make ordinary contract and
agent takeover checks depend on proprietary SDKs and target hardware.

## Decision and fix

Keep the stream lifecycle and v1 serialization in standard C++20. Compile the
DeepStream adapter only when `TP_SMOKE_DETECT_ENABLE_DEEPSTREAM=ON` and a real
`DEEPSTREAM_SDK_ROOT` exists. Use separate Docker targets so the reference
image never inherits the GPU stack.

## Verification

The reference CMake build and CTest pass locally. The opt-in profile has not
been compiled or run because the SDK/GPU are unavailable; this is recorded as
an explicit qualification gate rather than a simulated success.

## Prevention

Do not add NVIDIA libraries to the base Python/CPU profile. Run
`scripts/qualify_media_host.sh` on the accepted host, retain image digests and
hardware output, and complete one-stream plus bounded 20-stream measurements
before asserting R3 or AE6.
