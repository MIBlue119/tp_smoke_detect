# GPU-102 DeepStream conditional-runtime memory

## Context

The GPU-102 media ticket had to add a real DeepStream 7.0 graph while the
development host did not have the proprietary SDK, GStreamer development
headers, or the pinned DeepStream image imported.

## Symptom

The existing adapter compiled as a CPU reference and only called `gst_init`
when enabled. That could not prove source ingest, NVDEC, batching, detector,
tracker, GPU pose/hands, Triton crop inference, metadata integrity, or camera
failure isolation.

## Discarded hypotheses

- Treating CUDA visibility or a successful CMake build as GPU qualification.
- Replacing DeepStream elements with `identity` or using a CPU pose fallback.
- Publishing raw frames/crops to the Python policy service for convenience.
- Adding model weights or TensorRT plans to Git to make the local build appear
  complete.

## Root cause

The proprietary runtime and model bundle are deployment inputs owned by
GPU-101/GPU-103, while the native boundary had no explicit graph contract or
readiness state to represent their absence.

## Fix

The adapter now has an explicit DS7 SDK/header/library CMake gate, a
programmatic `nvurisrcbin -> nvstreammux -> nvinfer -> nvtracker -> MediaPipe
GPU plugins -> nvinferserver` graph, typed receipt publication from NvDs
metadata, per-camera reconnect/bounded queues, and an `unqualified` readiness
state whenever runtime plugins, artifacts, or qualification receipts are
missing. MediaPipe GPU plugins are named boundaries rather than silently
substituted elements.

## Verification

CPU Debug and Release CTest both pass. The missing-SDK configuration fails with
the expected explicit error. The DS7 branch was not compiled because the image
and SDK are not imported; this remains a reproducible external gate rather than
an implied pass.

## Prevention

Keep `TP_SMOKE_DETECT_ENABLE_DEEPSTREAM` opt-in, require the imported DS7 image
and exact model/plugin bundle before GPU builds, bind readiness to machine-
readable qualification receipts, and preserve the CPU reference path for CI.

## Related commit

Recorded with the GPU-102 implementation commit.
