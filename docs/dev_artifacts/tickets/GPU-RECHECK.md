# GPU review recheck remediation

## Scope

Closed the six findings in the bounded GPU review recheck at
`/tmp/compound-engineering-1000/ce-code-review/20260824-gpu-review-recheck/report.md`:

- native DeepStream publisher now consumes a bound config and publishes QoS1
  candidates through `mosquitto_pub`; stdout remains reserved for the explicit
  reference fixture;
- the DeepStream probe is attached after the crop stage, emits `completed`, and
  uses manifest-bound revisions; unavailable roles are never labelled `ok`;
- the Python consumer requires Paho MQTT v5, persists bounded retry attempts in
  User Properties, ACKs with `client.ack(mid, qos)`, and ACKs only after retry or
  DLQ publication succeeds;
- qualification requires a mandatory approved media root, actual duration and
  sample cadence, per-camera throughput/queue/latency/GPU/NVDEC telemetry,
  runtime/model identity, and a passed fault-isolation receipt;
- one-stream readiness is generated only from a successful qualification
  receipt and carries canonical SHA-256 lineage plus optional HMAC signing;
- adversarial tests cover short fabricated telemetry, missing media roots, and
  free-form readiness receipts.

## Verification

- `uv run pytest -q tests/contract/test_gpu_recheck.py tests/gpu/test_one_stream.py tests/load/test_gpu_qualification.py tests/unit/test_gpu_bundle.py tests/contract/test_candidate_processing.py tests/contract/test_native_fixture.py` — 26 passed.
- `cmake --build native/deepstream/build-review --parallel` — passed.
- `ctest --test-dir native/deepstream/build-review --output-on-failure` — 1 passed.
- `uv run ruff format --check ...` and `uv run ruff check ...` — passed.
- Paho runtime probe confirmed MQTT protocol 5 and `Client.ack` availability.

The existing untracked `native/deepstream/build-review/` directory was preserved
and is not part of the commit.
