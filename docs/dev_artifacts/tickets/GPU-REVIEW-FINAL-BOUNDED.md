# GPU final bounded review remediation

Date: 2026-08-24
Base: d791cb3

Implemented the five P1 closure items from the bounded Sol review:

- media publisher now parses the shipped DeepStream INI, resolves runtime
  bindings from the mounted manifest, and the GPU image packages
  `mosquitto_pub`;
- DeepStream candidates are emitted only after correlated typed pose, object,
  and smoke plugin metadata joins, with all model/artifact revisions bound;
- MQTT QoS1 retry/dead-letter replacement waits for broker publication
  confirmation before acknowledging the original delivery;
- qualification telemetry requires runtime-owned versioned receipts,
  monotonic timestamps, cumulative-counter cross-checks, per-role revisions,
  independent samples, and independently attested isolation;
- readiness is signed with a required trusted key, validates full qualification
  lineage, and the harness emits a canonical top-level one-stream receipt.

Verification:

- `uv run ruff format --check .`: pass
- `uv run ruff check .`: pass
- `uv run mypy src ml tests`: pass
- `uv run pytest -q`: 211 passed, 1 expected target-hardware skip
- native Release configure/build/CTest: pass
- shipped-config publisher probe: parses bindings and fails closed because the
  CPU build has no DeepStream SDK
- CPU and GPU-profile Compose rendering: pass

The actual DeepStream SDK, model bundle, broker, and signed receipt remain
external qualification inputs and were not fabricated in this checkout.
