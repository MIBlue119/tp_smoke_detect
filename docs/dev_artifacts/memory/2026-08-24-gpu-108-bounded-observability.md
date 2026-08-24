# GPU-108 memory: aggregate GPU observability without identity labels

## Context

The GPU release needs camera freshness, queue, NVDEC, Triton, model, and
degraded-mode telemetry while the product contract prohibits leaking track,
event, URL, or provider text into Prometheus series.

## Symptom

The original metrics registry allowed `camera_id` labels and accepted arbitrary
values for dimensions such as model role, component, and status. A large
camera fleet or unique provider error text could therefore create unbounded
series and make the GPU control plane less reliable during the same incidents
that operators need it most.

## Discarded hypotheses

- Hashing camera or GPU identifiers into labels: hashes still create one series
  per identity and only hide, rather than bound, cardinality.
- Removing camera context from all health responses: operators still need to
  identify the affected camera for isolation and recovery.
- Relying on dashboard conventions to suppress labels: cardinality must be
  enforced at metric construction, before scraping.

## Root cause

The initial CPU-oriented registry modeled camera freshness and queue depth as
per-camera Prometheus gauges and treated the label allow-list as a schema check
only. It did not constrain dimension values.

## Fix

- Reject camera/GPU/device identity labels at schema registration.
- Normalize every bounded dimension to a finite vocabulary, with `other` as
  the safe fallback.
- Keep camera identity in the in-memory health snapshot and aggregate metrics
  to max age/depth, stale count, and state counts.
- Add explicit GPU/NVDEC/memory/batch, Triton, readiness, deadline, restart,
  sample-loss, and suppressed-audio signals.
- Alert and dashboard only against aggregate or bounded-role metrics.

## Verification

`tests/integration/test_metrics.py` proves forbidden labels are rejected and
unique model/status values collapse to one series. The GPU degraded-mode tests
prove overload and model timeout remain visible, healthy camera state remains
isolated, and no audio command is created. Dashboard/rule validation rejects
camera, track, event, and GPU identity references. Focused result: 11 passed.

## Prevention

Keep metric registration centralized in `MetricRegistry`; require a bounded
vocabulary for every future label. Add a focused cardinality test whenever a
new metric dimension is introduced. Keep identity-bearing details in health
JSON or structured logs, never in Prometheus labels.

## Related commit

`dacc652d543e07c2cea0694b71367cbad888b991` — `feat(observability): add bounded gpu degraded telemetry`
