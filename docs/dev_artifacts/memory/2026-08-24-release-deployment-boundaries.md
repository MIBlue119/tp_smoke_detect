# Release deployment boundaries

## Context

The release Compose file declared PostgreSQL and MQTT as startup dependencies
although the CPU core constructed SQLite and never published to MQTT. It also
mounted policy/camera YAML that the ASGI bootstrap ignored, accepted public
HTTPS audio URLs, and exposed an ONNX timeout argument that could not cancel a
synchronous runner.

## Symptom

The default profile could appear production-ready while using different
storage and configuration than the operator expected. A public HTTPS endpoint
was accepted solely because TLS was enabled, and a slow ONNX callable could
return success after its requested deadline.

## Root cause

Deployment declarations had outpaced implemented adapters. Settings loading
handled YAML and environment independently rather than defining precedence.
The HTTP adapter treated transport security as host authorization, and an
in-process Python callable has no safe hard-cancellation primitive.

## Fix

Keep only wired services in the default profile and isolate future services
under `future-site`. Merge mounted YAML first, then known environment values.
Use loopback/private IPs or exact operator DNS allowlist entries for HTTP audio
under either scheme. Require ONNX runners to cooperate with a deadline (or
fail closed before invocation).

## Verification

The deployment ticket contains the unit-test and Compose evidence. The
release owner must rerun the complete unit/contract/integration/e2e and static
gates before publishing the release SHA.

## Prevention

Every new Compose dependency needs an implemented adapter and an acceptance
test proving application traffic. Every mounted configuration needs a
bootstrap test proving precedence. Treat HTTPS, callable timeout parameters,
and image tags as insufficient security or deadline evidence by themselves.

## Related commit

To be filled after the release-owner commit is created.
