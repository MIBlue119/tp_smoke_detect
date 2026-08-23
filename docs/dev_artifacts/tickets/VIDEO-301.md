# VIDEO-301 — Replay media worker

Status: implemented; release hardening follow-up applied on `release/0.1.0`.

## Scope delivered

- Deterministic manifest-driven image-sequence replay with a fixed sampling
  clock, stable source/capture/received timestamps, track IDs, and v1
  `CandidateEnvelope` output.
- `LocalArtifactStore` constrained to one resolved registered root, including
  traversal and symlink escape rejection; supported image suffix and header
  validation; no raw bytes enter candidate messages.
- Ordered synchronous `InMemoryMessageBus` for CPU-only candidate delivery.
- `scripts/replay_fixture.py` entry point for running a root-relative manifest
  directly from a coding-agent shell.
- ROI and excluded-zone filtering, optical quality eligibility, and structured
  per-frame errors for missing, corrupt/truncated, and unsupported media.
- Synthetic CLI fixture: `uv run smoke-detect demo --fixture synthetic`.
- Proof-first unit/integration tests and a fixture README documenting the
  synthetic/redistributable-media boundary.

## Verification

- `uv run pytest tests/unit tests/contract tests/integration` — **39 passed**.
- Focused replay tests — **5 passed**, including direct-manifest recording ID
  separation.
- `uv run smoke-detect demo --fixture synthetic` — `candidates=2 errors=0`.
- Changed-file Ruff and `uv run mypy src tests` — **pass**.
- Full `ruff format --check .` — **pass**.
- Full `ruff check .` — **red: 4 pre-existing import-order diagnostics** in
  `tests/unit/domain/{test_cycle_detector,test_evidence_policy,test_track_state,test_vetoes}.py`;
  no diagnostics in VIDEO-301 paths.

## Open boundaries

Video-container decoding and GPU/RTSP ingestion remain VIDEO-302 scope.  The
replay worker deliberately reports those codecs as `unsupported_media` rather
than adding OpenCV/FFmpeg or GPU dependencies to the CPU profile.

Release hardening: direct `ReplayManifest(recording_id=...)` construction now
preserves and validates the supplied identity; omitted IDs retain the
artifact-content-derived legacy behavior.

Related commits: `1f8d036`, release hardening follow-up pending.
