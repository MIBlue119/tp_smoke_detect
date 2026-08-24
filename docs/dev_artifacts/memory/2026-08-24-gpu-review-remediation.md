# GPU review remediation memory

## Context

The release review found that a nominal DeepStream build could still run the
upstream `deepstream-app`, emit a non-v1 candidate, and accept readiness or
qualification claims that were not bound to runtime or artifact bytes.

## Root causes

The media archive was treated as the runtime executable, retry state lived only
in a callback closure, and several gates checked field presence or hash syntax
instead of the underlying bytes. The candidate compatibility field
`independent_channels` was also treated as producer authority.

## Fix

Build and invoke `media_publisher`; serialize nested v1 geometry and canonical
roles; carry MQTT attempts in broker user properties; recover durable decisions
before cascade ingestion; inject active manifest revisions; derive safety
channels from successful receipts; and validate telemetry, receipt identity,
TensorRT plans, replay clips, promotion provenance, and every bundle member.

## Prevention

Keep the adversarial contract tests alongside the release gates. A future
producer or receipt change must add a parsed-wire test and a tamper/restart
negative test before promotion. A successful GPU claim requires external
hardware, image, model, and sealed-media evidence in addition to these CPU
tests.
