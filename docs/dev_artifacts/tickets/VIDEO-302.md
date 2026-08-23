# VIDEO-302 — DeepStream production media worker

Status: implemented as CPU-reference/native boundary; GPU qualification open.

## Delivered

- C++20 `MediaWorker` with a bounded FIFO and counters per camera.
- Broker-neutral bounded candidate publisher with retry-preserving outage behavior.
- Independent freshness, degraded, and reconnecting states per stream.
- Typed candidate envelope serializer for `track.candidate.v1`; raw pixels and
  free-form model prose cannot be represented.
- Explicit opt-in DeepStream adapter and CMake SDK-root gate.
- CPU-native CTest lifecycle/contract tests and a candidate JSON fixture.
- `deploy/Dockerfile.media` with separate `reference` and `deepstream` targets.
- `scripts/qualify_media_host.sh` and a qualification receipt template.

## Verification

```text
cmake -S native/deepstream -B native/deepstream/build -DBUILD_TESTING=ON  PASS
cmake --build native/deepstream/build --parallel                    PASS
ctest --test-dir native/deepstream/build --output-on-failure         1 passed
```

The DeepStream SDK/GPU/RTSP/Triton gates were not available and remain
unclaimed. See `docs/dev_artifacts/qualification/VIDEO-302-hardware-qualification.md`.
