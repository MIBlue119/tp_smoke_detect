# Coding-agent takeover guide

This repository is the on-premise AI core for smoking-behaviour detection. It
processes camera evidence locally, records auditable decisions, and may request
only catalogued neutral audio. It does not perform identity recognition,
ingest microphone audio, or send private media to remote services.

## Start here

1. Read `docs/draft.md` for the product intent and privacy boundaries.
2. Read the assigned unit and verification contract in
   `docs/dev_artifacts/2026-08-24-system-architecture-and-delivery-plan.md`.
3. Read the relevant runbook under `docs/runbooks/` before changing deployment,
   operations, incidents, or model-release behavior.
4. Claim or create `docs/dev_artifacts/tickets/<TICKET-ID>.md`; record research,
   verification, unresolved blockers, and the related commit.
5. Preserve the v1 contracts in `proto/smoke/v1/`,
   `src/tp_smoke_detect/contracts.py`, and `schemas/smoke/v1/`. A breaking
   contract change requires a new version and tech-lead review.

## Clean-checkout CPU path

Use Python 3.11 or 3.12 and the lockfile:

```bash
uv sync --group dev
uv run ruff format --check .
uv run ruff check .
uv run mypy src tests
uv run pytest tests/unit
uv run pytest tests/contract
uv run pytest tests/integration
uv run pytest tests/e2e
uv run smoke-detect schema --output schemas/smoke/v1
uv run smoke-detect validate-config configs/camera.example.yaml
uv run smoke-detect demo --fixture synthetic
uv run python scripts/qualify_cpu.py --check

# Native CPU reference qualification (run both optimization profiles)
cmake -S native/deepstream -B native/deepstream/build-debug \
  -DBUILD_TESTING=ON -DCMAKE_BUILD_TYPE=Debug
cmake --build native/deepstream/build-debug --parallel
ctest --test-dir native/deepstream/build-debug --output-on-failure
cmake -S native/deepstream -B native/deepstream/build-release \
  -DBUILD_TESTING=ON -DCMAKE_BUILD_TYPE=Release
cmake --build native/deepstream/build-release --parallel
ctest --test-dir native/deepstream/build-release --output-on-failure
```

The CPU profile needs no GPU, DeepStream, Triton, model weights, speaker,
private media, or public network. The default profile is simulation/shadow with
audio muted, `reject_on_unclear`, bounded queues, and no remote URL fetching.
Do not treat a CPU pass as evidence of 20-camera capacity or production model
quality.

## Ticket ownership and GitFlow

- `main` is release-only; `develop` is the integration branch.
- Create `feature/<ticket-id>-<slug>` from the current remote `develop` in a
  dedicated worktree. Release hardening uses `release/<version>`; production
  fixes use `hotfix/<slug>` from `main`.
- Feature agents own only their ticket files and implementation files named in
  the plan. The tech lead owns shared schema and migration changes after U1;
  propose contract changes before editing them.
- Use conventional commits. Stage explicit paths (`git add path/to/file`),
  never `git add .`. Inspect `git diff --check` and `git status` before
  commit.
- Push every accepted feature/release branch to `origin` and open/update review
  against the branch's GitFlow target. Do not merge to `main`, create release
  tags, or rewrite history from a worker branch.
- Before starting, inspect dirty files. Existing user work is not yours to
  commit. Stop and report a collision if a required file is already modified.

## Artifact contract

Every ticket has a concise result under `docs/dev_artifacts/tickets/`. Put
research under `research/`, qualification receipts under `qualification/`,
review receipts under `reviews/`, progress under `progress/`, and reusable
problem/solution notes under `memory/`. A material memory note contains:

`context`, `symptom`, `discarded hypotheses`, `root cause`, `fix`,
`verification`, `prevention`, and `related commit`.

Artifacts must be reproducible from repository-relative commands. Never record
host-specific virtualenv paths, credentials, private filesystem paths, raw or
identifying video, generated model weights, or sensitive screenshots.

## Safety boundaries

- Keep production examples in shadow mode with audio muted until silent-period,
  agency, legal/procurement, retention, model provenance, and physical-audio
  receipts are complete.
- Secrets enter through mounted secret files or the deployment secret store;
  they never appear in YAML examples, API responses, logs, commits, or
  artifacts. `deploy/secrets/` is ignored; use the checked-in `.example` only
  as a shape reference.
- Runtime egress is disabled or internal-only. Reject remote artifact URLs and
  absolute paths. Keep media in the approved local artifact store and honor
  separate raw-media, event-clip, and metadata retention.
- Do not add GPU packages, model downloads, weights, private media, or network
  calls to the base install. GPU/media work belongs behind the optional profile
  and its qualification receipt.
- Model releases require immutable hashes, parent checkpoints, origin, license,
  dataset manifest, evaluation, calibration, runtime requirements, SBOM, and a
  rollback target. Unknown provenance is a release blocker.

## Review handoff

Before declaring a unit complete, record exact commands and pass/fail results,
list unrun external gates honestly, update the ticket and progress receipt, and
ask the Sol reviewer to run the structured code review. Resolve high/critical
findings before shipping. A release branch is not a production release until
its checklist, review receipt, branch synchronization, and external approvals
are complete.
