# MODEL-201 — Model provider framework

Status: implemented on `feature/model-201-provider-framework`
Unit: U4
Traceability: R2, R8, R10, R11; F1; AE7; KTD2, KTD7, KTD9, KTD10

## Delivered

- Added typed inference roles and role-specific constrained result models for pose,
  object, smoke, temporal, chewing veto, and optional VLM verification.
- Added metadata-only request and structured receipt envelopes. Failed receipts never
  carry a result; successful receipts must carry a result matching their role.
- Added deterministic fake provider with stable fixture output and explicit timeout
  and malformed-result paths.
- Added dependency-optional ONNX and OpenAI-compatible adapter seams. Neither imports
  a runtime package or stores model prose; unavailable, timeout, malformed, OOM,
  runtime, and revision failures become structured receipts.
- Added `ModelRouter` with per-role routing, role readiness, process liveness, model
  revision checks, and isolated circuit breakers.

## Verification

Red-first evidence: before implementation, the new contract tests failed at collection
with `ModuleNotFoundError` for `tp_smoke_detect.ports.inference` and the adapter
modules. The first behavior pass also exposed that non-strict Pydantic feature unions
accepted `bytes`; strict scalar feature types now reject private media payloads.

Passing evidence:

```text
uv run pytest -q tests/contract/test_inference_providers.py tests/unit/adapters tests/integration/test_model_timeout.py
9 passed
uv run pytest -q tests/unit tests/contract tests/integration
21 passed
uv run ruff check .
All checks passed!
uv run mypy src tests
Success: no issues found in 21 source files
```

No model weights, private media, credentials, or GPU dependencies were added. Adapter
runtime installation and model provenance remain release-gated work for the MLOps and
qualification units.

## Unresolved / handoff

- ONNX/Triton runner construction is intentionally injected by the GPU/runtime lane;
  the CPU package remains dependency optional.
- The integration owner should run the full contract and vertical-slice gates after
  merging this branch with U2/U3.
