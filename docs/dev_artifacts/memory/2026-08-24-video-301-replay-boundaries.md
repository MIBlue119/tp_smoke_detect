# VIDEO-301 replay boundaries and deterministic evidence

## Context

The CPU vertical slice needed to exercise F1 without a GPU, RTSP broker, or
private camera media.  Replay input must remain auditable and safe for agents
working from a shared artifact root.

## Symptom

Naive replay implementations tend to use wall-clock timestamps, pass absolute
filesystem paths through messages, decode every frame opportunistically, or
abort the entire run when one frame is truncated.  Those choices make replay
results non-repeatable and can leak local paths/media.

## Discarded hypotheses

- Add OpenCV/FFmpeg to the base CPU install: rejected because media-container
  decoding belongs to the DeepStream worker and would expand the dependency
  and provenance surface.
- Treat a path string as an artifact identity: rejected because `..`, absolute
  paths, and symlink targets can escape the registered root.
- Use `datetime.now()` for capture metadata: rejected because replay equality
  and retry/idempotency tests require a manifest-owned clock.

## Root cause

The existing v1 candidate contract intentionally carries metadata and artifact
IDs, but no media decoder or artifact trust boundary existed yet.

## Fix

`ReplayManifest` derives timestamps from fixed PTS values; `ReplayWorker` uses
camera-profile sampling/ROI gates and isolates frame errors; `LocalArtifactStore`
resolves and checks real paths under one root; `InMemoryMessageBus` accepts only
typed candidate envelopes.  Unsupported containers and corrupt headers become
structured errors while valid frames continue.

## Verification

Sampling, byte-equivalent repeated replay, ROI/exclusion filtering, traversal
and symlink rejection, corrupt/unsupported media continuation, and the
synthetic CLI are covered by the VIDEO-301 tests.  Full CPU unit/contract/
integration tests pass.

## Prevention

Keep replay manifests root-relative and synthetic/redistributable.  Do not add
raw bytes, arbitrary paths, wall-clock fields, or a decoder dependency to the
CPU reference path.  Add a new versioned contract before changing candidate
fields.

## Follow-up: public manifest identity

Directly constructing `ReplayManifest` is a supported API path.  A hidden
provenance flag cannot safely distinguish a caller's explicit ID from a
legacy default.  Use `recording_id=None` as the legacy sentinel, preserve and
validate concrete IDs, and derive the artifact-content identity only when the
sentinel is present.  Regression coverage must compare two direct manifests
with otherwise identical frames.

Related commit: `1f8d036`; release hardening follow-up pending.
