# GPU review trust-boundary closure

## context

The GPU release branch had a native DeepStream scaffold and a qualification
harness, but a bounded review found that configuration, metadata, telemetry,
MQTT delivery, and startup readiness could be self-declared or lost in transit.

## symptom

The shipped INI did not provide values consumed by `media_publisher`; role
receipts were hard-coded unavailable; MQTT acknowledged local enqueue; any JSON
line could look like telemetry; and an unsigned self-authored one-stream file
could pass structural checks.

## discarded hypotheses

- A successful `publish()` return code is not a broker PUBACK.
- Graph position is not proof that a plugin produced an output.
- A field-name/count aggregate is not an independently measured runtime.
- A self-consistent hash is not trusted provenance without a trusted key.

## root cause

The boundaries lacked an explicit runtime-owned receipt ABI and durable
acknowledgement protocol. Configuration was written for `deepstream-app` while
the repository executable used a different flat parser.

## fix

The media publisher accepts the deployed INI with explicit runtime bindings;
the GPU image includes its MQTT client; plugins attach versioned fixed-width
typed role metadata; the callback joins correlated role outputs and revisions;
Paho publication waits for confirmation; telemetry includes provenance,
timestamps, samples, counters, and fault attestation; readiness uses HMAC and
the qualification source lineage and is emitted as a top-level artifact.

## verification

Run the GPU contract tests, full Python gates, native Release CTest, both Compose
config renders, and the shipped-config probe. GPU SDK/model/broker/hardware
execution remains an explicit external gate.

## prevention

Keep the metadata ABI and telemetry schema versioned. Reject unsigned receipts,
direct-only counters, missing model-role revisions, and local publish enqueue
as delivery proof in future reviews.

## related commit

Pending commit on `feature/gpu-mandatory-release`.
