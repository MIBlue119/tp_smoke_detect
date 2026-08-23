# Operations runbook

The operator surface is the local FastAPI API, /metrics, and the Compose
health state. Decisions are append-only and expose structured outcomes,
reasons, evidence channels, policy revision, and model revisions. Do not use
free-form model output as an operational command.

## Safe operating state

The checked-in production example is shadow with audio_muted: true and
reject_on_unclear: true. In shadow mode the service records would_announce for
an eligible event but the audio adapter receives no command. Keep this state
until silent-period acceptance, policy, provenance, retention, and
physical-audio receipts are approved.

## Health and metrics

    curl --fail http://127.0.0.1:8000/health/live
    curl --fail http://127.0.0.1:8000/health/ready
    curl --fail http://127.0.0.1:8000/metrics
    docker compose -f deploy/compose.yaml ps

live only proves the process responds. ready reports database state,
component health, affected cameras, queue depth, and last successful activity.
A degraded camera should not restart the whole API. Investigate stream
freshness, queue depth, stage latency, rejection reasons, model revisions,
audio requests, and degraded-mode transitions in /metrics and local logs.

## Event review and configuration

    curl --fail 'http://127.0.0.1:8000/v1/events?limit=100'
    curl --fail 'http://127.0.0.1:8000/v1/events?outcome=verified&limit=100'
    curl --fail http://127.0.0.1:8000/v1/models
    curl --fail http://127.0.0.1:8000/v1/cameras

Review a decision only after confirming the event identifier and local
retention authority. The API stores the review and actor as append-only audit
data:

    curl --fail -X POST http://127.0.0.1:8000/v1/events/<DECISION_ID>/reviews \
      -H 'Content-Type: application/json' \
      -d '{"label":"false_positive","actor":"operator-id","reason":"nose touch"}'

Camera changes use a complete CameraProfile, a human revision, and an explicit
activate decision. Validate YAML first; never paste RTSP credentials into a
camera profile or URL into an artifact field.

## Mute and mode changes

Create a site mute during investigation:

    curl --fail -X POST http://127.0.0.1:8000/v1/audio/mute \
      -H 'Content-Type: application/json' \
      -d '{"scope":"site","reason":"incident INC-<ID>","actor":"operator-id"}'

For a camera or zone mute include scope_id; use expires_at when the incident
owner has a known expiry. Record the incident and verify the mute in the audit
store. A mode change is an auditable action:

    curl --fail -X POST http://127.0.0.1:8000/v1/site-mode \
      -H 'Content-Type: application/json' \
      -d '{"mode":"shadow","reason":"release default","actor":"operator-id"}'

Changing to automatic is prohibited by this runbook until every release gate
is attached to the site change record. A request to /v1/audio/requests must be
expected to return a suppression reason when muted, shadowed, unclear,
cooldown-limited, over cap, or suspended.

## Backup and restore

The helper creates a path-safe, media-free archive. It retains state,
configuration, and model references but excludes raw media and event clips:

    uv run python scripts/backup_restore.py backup \
      --source /var/lib/smoke-detect \
      --output /secure/backup/smoke-detect-<UTC>.tar.gz
    uv run python scripts/backup_restore.py restore \
      --archive /secure/backup/smoke-detect-<UTC>.tar.gz \
      --destination /var/lib/smoke-detect-restore

Production PostgreSQL dumps are owned by the database administrator and must
be supplied to the restore drill separately. Verify the manifest hash and
confirm that no archive member is media, a symlink, absolute, or traversing
before replacing state. Run the CPU/e2e and health checks after restore.
