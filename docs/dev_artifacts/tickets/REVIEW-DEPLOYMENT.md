# REVIEW-DEPLOYMENT — release deployment hardening

Status: complete on `release/0.1.0`; release-owner review pending

## Scope

Resolved validated findings VAL-005 (unwired Compose dependencies), VAL-009
(ignored mounted configuration), VAL-010 (misleading ONNX timeout), and
VAL-012 (unrestricted HTTPS audio endpoint).

## Changes

- The default Compose profile now contains only the wired SQLite API and local
  Prometheus. PostgreSQL and MQTT remain declared under the explicit
  `future-site` profile with no default `depends_on` claim.
- Bootstrap merges the mounted policy and camera YAML files and then applies
  only known `SMOKE_DETECT_*` environment overrides. The Compose mounts are
  configured through `SMOKE_DETECT_POLICY_CONFIG` and
  `SMOKE_DETECT_CAMERAS_CONFIG`.
- ONNX deadline requests now require a cooperative runner accepting `deadline`
  or `timeout_ms`; non-cooperative runners fail closed as timeout without
  invoking an unbounded call.
- HTTP audio accepts loopback/private IPs by default and exact configured
  internal DNS names for either HTTP or HTTPS. Public HTTPS, unsafe DNS labels,
  credentials, query strings, and fragments are rejected.

## Proof-first verification

- `uv run pytest tests/unit/test_settings.py tests/unit/adapters/test_http_audio_policy.py tests/unit/adapters/test_runtime_boundaries.py -q` — 18 passed.
- `docker compose -f deploy/compose.yaml config --services` — default services
  are `smoke-detect` and `prometheus` only.
- `docker compose -f deploy/compose.yaml config --quiet` — pass.
- Full repository gates and release validation are recorded by the release
  owner after integration.

## Unresolved / handoff

The `future-site` PostgreSQL/MQTT profile is intentionally not a production
claim. It requires implemented adapters, acceptance tests, and resolved image
provenance before activation.
