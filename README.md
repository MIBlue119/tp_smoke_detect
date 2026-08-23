# TP Smoke Detect

TP Smoke Detect is an on-premise, auditable AI core for smoking-behaviour
detection. It processes camera evidence locally, records structured decisions,
and can request a catalogued neutral audio message through a separate adapter.
It does not identify people, ingest microphone audio, send private media to a
remote service, or make an enforcement decision.

## Release posture

The `0.1.0` release is the CPU/replay MVP-0 reference profile. The safe default
is `shadow` mode with audio muted and `reject_on_unclear`; an eligible event is
recorded as `would_announce` and no speaker command is sent. GPU throughput,
model quality, 24-hour soak, site retention, agency policy, legal/procurement,
and physical-audio gates remain separate qualification work. See the release
receipt in `docs/dev_artifacts/qualification/` before changing this posture.

## New-agent quickstart

From a clean checkout on Python 3.11 or 3.12:

```bash
uv sync --group dev
uv run ruff format --check .
uv run ruff check .
uv run mypy src tests
uv run pytest tests/unit
uv run pytest tests/contract
uv run smoke-detect schema --output schemas/smoke/v1
uv run smoke-detect validate-config configs/camera.example.yaml
uv run smoke-detect demo --fixture synthetic
```

The last command exercises the deterministic replay seam without a GPU,
camera, speaker, model weights, or network. Run `uv run pytest tests/e2e` for
the CPU deployment qualification. `uv.lock` is the source of truth; do not
replace it with an unconstrained install.

Read [`AGENTS.md`](AGENTS.md) before claiming a ticket. It describes GitFlow,
worktrees, file ownership, verification, and the artifact contract.

## Runbooks and repository map

- [`docs/runbooks/installation.md`](docs/runbooks/installation.md) — clean CPU
  install, air-gapped bundle, and Compose startup.
- [`docs/runbooks/operations.md`](docs/runbooks/operations.md) — health,
  metrics, modes, mutes, event review, and backup/restore.
- [`docs/runbooks/incident-response.md`](docs/runbooks/incident-response.md) —
  fail-safe response for stale cameras, queue pressure, model failures, and
  audio incidents.
- [`docs/runbooks/model-release.md`](docs/runbooks/model-release.md) —
  provenance, evaluation, promotion, rollback, and release blockers.
- `src/tp_smoke_detect/contracts.py` and `proto/smoke/v1/` — versioned v1
  contracts; breaking changes require a new version.
- `src/tp_smoke_detect/domain/` — deterministic cascade and policy.
- `src/tp_smoke_detect/api/` — FastAPI and generated OpenAPI surface.
- `native/deepstream/` — optional native/media boundary; CPU tests do not imply
  DeepStream or target-hardware qualification.
- `ml/` — metadata-only dataset, evaluation, and model-release contracts.
- `deploy/` — read-only, non-root reference deployment and local observability.
- `docs/dev_artifacts/` — tickets, research, qualification receipts, reviews,
  progress, and reusable memory. Never put credentials, raw video, generated
  weights, or identifying screenshots here.

## Modes and safety

`simulation` and `replay` are for deterministic development. `shadow` records
what would have happened while suppressing audio. `human_confirmed` requires an
operator workflow. `automatic` is a policy state, not a deployment shortcut:
it requires the site silent-period acceptance report, exact model/runtime
provenance, retention and agency approvals, and a physical audio qualification.
The checked-in production example remains shadow/muted.

Every queue is bounded. Model timeouts and unclear results fail closed. Camera
and event records use metadata and catalogued artifact IDs; private media stays
in the site artifact store under its approved retention policy.

## Common deployment checks

The default Compose profile is the SQLite-backed local API and Prometheus.
PostgreSQL and MQTT are present only in the `future-site` profile; media and
audio are explicit profiles. Before a site import, resolve every
`REQUIRED-BEFORE-RELEASE` digest in
[`deploy/image-pins.yaml`](deploy/image-pins.yaml), attach SBOM and vulnerability
dispositions, and preload the images on the air-gapped host.

```bash
docker compose -f deploy/compose.yaml config --quiet
docker compose -f deploy/compose.yaml up -d
curl --fail http://127.0.0.1:8000/health/live
curl --fail http://127.0.0.1:8000/health/ready
docker compose -f deploy/compose.yaml ps
docker compose -f deploy/compose.yaml logs --tail=100 smoke-detect
```

Do not enable `audio` or change the site mode as part of a smoke test. Use the
runbooks for an auditable change and rollback. Stop the stack with
`docker compose -f deploy/compose.yaml down` when the local qualification is
complete; preserve named volumes when a restore drill is required.

## License and release boundary

Application source is Apache-2.0. A production bundle must independently record
the license, origin, hash, SBOM, evaluation, runtime requirements, and rollback
target for every image, dependency, model, dataset, and weight. A publisher
name or a passing unit test is not a provenance or site-qualification receipt.
