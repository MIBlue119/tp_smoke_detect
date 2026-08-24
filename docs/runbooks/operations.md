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

## GPU profile health and degraded mode

For the RTX 3090 lab profile, inspect every boundary before calling the
profile ready:

    docker compose --profile gpu-rtx3090 -f deploy/compose.yaml ps
    docker compose --profile gpu-rtx3090 -f deploy/compose.yaml logs --tail=100 \
      gpu-preflight baseline-model media-gpu candidate-consumer gpu-readiness
    curl --fail http://127.0.0.1:8000/metrics

`gpu-readiness` remains degraded until the immutable one-stream receipt is
`one-stream-ready`, Triton/core dependencies are healthy, and the candidate
consumer creates its shared readiness file. GPU OOM, queue expiry, model
timeout, revision mismatch, stalled input, or malformed output must leave the
affected camera/model degraded and suppress audio. The current checked-in
profile is `gpu-capable-scaffold-unqualified`; do not call it lab-qualified or
production-capable until GPU-107's real R11 and R12 receipts are attached.

## Backup and restore

The host-side command below is the executable deployment backup path. Run it
from the repository checkout with the Compose service healthy; it does not
require `uv` in the runtime image:

    python3 scripts/backup_restore.py backup-deployment \
      --compose-file deploy/compose.yaml \
      --service smoke-detect \
      --output /secure/backup/smoke-detect-<UTC>.tar.gz

The command executes the checked-in helper as `python` inside the service. It
uses SQLite's online backup API against the database in the `smoke_state`
Compose named volume, and includes the two authoritative read-only mounts
(`/etc/smoke-detect/policy.yaml` and `cameras.yaml`). The archive is published
with an atomic host-side rename. Raw media and event clips remain excluded.

For a restore drill, first verify into a fresh host staging directory:

    python3 scripts/backup_restore.py restore \
      --archive /secure/backup/smoke-detect-<UTC>.tar.gz \
      --destination /secure/restore/smoke-detect

Review `state/audit.sqlite3` with `PRAGMA integrity_check` and review the
restored `config/` files before applying them to the host paths mounted by
Compose. Stop the API before replacing the named-volume database, then stream
the verified archive through the runtime helper (again, no `uv` in the image):

    docker compose -f deploy/compose.yaml stop smoke-detect
    docker compose -f deploy/compose.yaml run --rm --no-deps -T \
      --entrypoint python smoke-detect \
      /app/scripts/backup_restore.py restore-runtime \
      --archive - --database /var/lib/smoke-detect/state/audit.sqlite3 \
      < /secure/backup/smoke-detect-<UTC>.tar.gz

The runtime restore uses an in-volume staging directory, checks SQLite
integrity, and atomically replaces only the database. Apply reviewed policy
and camera files on the host, restart Compose, and run the CPU/e2e and health
checks. Production PostgreSQL dumps remain a separate DBA-owned procedure.
