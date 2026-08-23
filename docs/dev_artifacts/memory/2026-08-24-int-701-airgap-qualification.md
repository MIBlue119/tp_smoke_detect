# INT-701 — Air-gap deployment and honest qualification receipts

## Context

The CPU/replay MVP needed a deployment seam that a new agent can run without a
GPU, public network, speaker, private media, or external model endpoint, while
still making the eventual production qualification auditable.

## Symptom

A compose file alone can look like a production qualification even when its
images are mutable tags, its network can egress, or its test path never proves
the audio and audit behavior. Backups can also accidentally restore expired
clips and symlinked files.

## Discarded hypotheses

- Treating `latest` image tags as sufficient provenance.
- Calling a skipped GPU test a capacity pass.
- Copying the entire artifact directory into a backup.
- Adding HTTP client dependencies solely to test the in-process API seam.

## Root cause

Deployment and qualification have separate trust boundaries: the CPU vertical
slice is deterministic, while image digest/SBOM, broker/database process,
hardware throughput, model quality, and site audio require independent
receipts. The first container smoke also exposed that the API's development
dependencies did not include an ASGI server and that a read-only image cannot
create an ignored relative artifact/database path. Runtime defaults must be
constrained even when those receipts are missing.

## Fix

- Compose uses an internal-only network, non-root/read-only containers, dropped
  capabilities, bounded queues, loopback-only operator ports, explicit service
  healthchecks, and profiles for hardware-dependent services.
- `image-pins.yaml` marks unresolved digests as a release blocker instead of
  inventing a digest.
- `qualify_cpu.py` runs the replay → cascade → SQLite audit → shadow/fake audio
  → review path and writes a receipt listing every unrun gate.
- `backup_restore.py` records hashes, rejects traversal/symlink members, and
  excludes media names while retaining state/config/model-reference files.
- `uvicorn` is a locked runtime dependency; `database` and `artifact_root` are
  explicit settings, with `/tmp` defaults for read-only smoke and compose
  mounts for persistence.

## Verification

- 85 unit/contract/integration/e2e tests pass.
- `docker compose ... config --quiet` passes.
- CPU qualification receipt passes with six explicit unrun production gates.
- Backup/restore smoke restored state/config and omitted a private media file.
- `mypy src tests` passes.
- The built image starts as UID 10001 with a read-only root and passes the
  local `/health/live` probe.

## Prevention

Keep the CPU receipt and production qualification receipts separate. Require
resolved image digests, SBOMs, target-host measurements, sealed metrics, and
policy sign-off before changing the default from shadow/muted to automatic.
Always run the built-image health probe; a passing Python test suite does not
prove that the container contains its process launcher or has writable mounts.

## Related commit

To be filled with the INT-701 commit SHA after branch publication.
