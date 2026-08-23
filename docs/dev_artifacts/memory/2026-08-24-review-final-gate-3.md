# Learning — rollback safety, configuration authority, and in-doubt playback

## Context

The final release review fault-injected the restore path, mounted-camera
bootstrap, local audio controller boundary, and qualification workflow. It also
ran an owner thread concurrently with operator reconciliation.

## Symptom

A failed first restore rename could delete the live destination. Reconciliation
could hide a late accepted playback and overwrite the attempt timestamp.
Mounted YAML changes were ignored after the first boot, removed profiles stayed
active, and disabled cameras still reached the audio policy. Exact-expiry HTTP
receipts were accepted. The documented qualification command wrote an
untracked receipt into an otherwise clean checkout.

## Root cause

Rollback ownership was inferred before the filesystem move succeeded. Audio
expiry was modeled as terminal expiration instead of an explicit in-doubt state
with owner-token resolution. Bootstrap treated YAML as seed data rather than
authoritative state, and the audio endpoint trusted zone presence without the
operational flags. Receipt and qualification boundaries used inconsistent
timestamp/file-write semantics.

## Fix

Restore records the rollback tree only after the live rename succeeds. Audio
reconciliation transitions pending to `uncertain`, preserving the reservation
token and canonical `created_at`; matching owners can finalize, while retries
remain fenced. Bootstrap reconciles mounted profiles and deactivates removed
config-managed cameras. The audio choke point requires active and enabled
flags. HTTP expiry uses an inclusive boundary. `--check` makes qualification
read-only and is now the agent gate.

## Verification

Proof-first adversarial coverage includes first-rename fault injection,
threaded owner/reconciler interleaving, camera restart/removal, disabled-camera
ASGI rejection, exact expiry, and clean qualification. Full CPU, schema,
configuration, Compose, container, and native Debug/Release gates are run by
the release owner after this implementation commit.

## Prevention

For destructive filesystem workflows, assign rollback state only after each
irreversible step succeeds. For external side effects, keep owner-token CAS,
explicit in-doubt states, immutable physical-attempt timestamps, and append-only
attempt history. Treat mounted configuration as authoritative and enforce
operational flags at the final side-effect choke point. Keep documented clean
checkout commands read-only unless they write to an explicit ignored/temp path.

## Related ticket

`REVIEW-FINAL-GATE-3`.
