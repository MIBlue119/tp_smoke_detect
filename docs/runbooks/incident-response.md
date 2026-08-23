# Incident-response runbook

The system is fail-closed. Preserve the audit trail, suppress audio first,
and isolate the affected camera or model stage before attempting recovery.
Never copy private frames, clips, credentials, or identifying screenshots into
the repository or an artifact receipt.

## First five minutes

1. Record incident ID, UTC time, operator, release commit, configuration and
   model revisions, affected cameras/zones, and the observed symptom.
2. Mute the site and confirm the API response:

       curl --fail -X POST http://127.0.0.1:8000/v1/audio/mute \
         -H 'Content-Type: application/json' \
         -d '{"scope":"site","reason":"incident INC-<ID>","actor":"<operator>"}'
       curl --fail http://127.0.0.1:8000/health/ready

3. Capture metadata-only evidence: /metrics, /health/ready, recent event
   IDs/reasons, logs with secrets redacted, and container image IDs.
4. Keep the service in shadow or simulation; do not delete the database or
   artifact store while investigating.
5. Open or update docs/dev_artifacts/tickets/<TICKET-ID>.md; add a memory note
   when the root cause and prevention are known.

## Stale camera, queue pressure, or degraded stream

Check freshness and queue metrics per camera. Do not restart the entire stack
for one failed stream. Disable or isolate only the affected camera after
recording the camera revision and reason. If queues approach their configured
bound, preserve recent candidates, reject new work according to the configured
drop policy, and leave audio suppressed. A restart is justified only after
capturing the health/log receipt and confirming the database volume is mounted.

Recovery requires a fresh frame deadline, normal queue depth, and an explicit
operator review. Record the last-good-frame time and recovery time.

## Model timeout, malformed output, or release mismatch

Treat timeout, unclear, malformed output, missing revision, missing hash, or
runtime mismatch as review_unavailable/rejected behavior. Do not retry
unboundedly and do not enable a fallback model absent from the release
manifest. Confirm the active model list:

    curl --fail http://127.0.0.1:8000/v1/models
    curl --fail http://127.0.0.1:8000/metrics
    docker compose -f deploy/compose.yaml logs --tail=200 smoke-detect

If the model cannot be proven to be the approved immutable release, stop model
processing, remain in shadow/muted mode, and invoke model-release.md rollback.
Never download weights from a remote URL during an incident.

## Unexpected or repeated audio

Mute the site immediately, record every affected decision and audio receipt,
and preserve the policy/configuration revision. Do not disconnect or modify a
speaker device before the audio owner captures its local diagnostic. Confirm
that shadow/muted state is recorded:

    curl --fail -X POST http://127.0.0.1:8000/v1/site-mode \
      -H 'Content-Type: application/json' \
      -d '{"mode":"shadow","reason":"audio incident INC-<ID>","actor":"<operator>"}'
    curl --fail -X POST http://127.0.0.1:8000/v1/audio/mute \
      -H 'Content-Type: application/json' \
      -d '{"scope":"site","reason":"audio incident INC-<ID>","actor":"<operator>"}'

Do not return to automatic mode until policy, audio, and release owners sign a
corrective receipt and a silent replay demonstrates zero commands.

## Data, security, or integrity concern

Stop exports and remote access. Preserve metadata, audit records, hashes,
container IDs, and access logs; do not duplicate raw media. Rotate any exposed
secret through the site secret store, never by editing a tracked example.
Use the media-free backup helper only after the retention/security owner
approves the destination. Escalate legal, privacy, and procurement questions
to their designated owners; this runbook is not legal advice.

## Closure

Close only after the service is healthy, audio is still safely muted/shadowed,
the affected camera/model is independently verified, and the incident record
contains timeline, evidence, root cause, fix, verification, prevention, and
related commit. Link the memory note and qualification receipt, with no private
media or secrets.
