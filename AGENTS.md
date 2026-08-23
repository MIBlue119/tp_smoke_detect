# Coding-agent takeover guide

This repository is the on-premise AI core for smoking-behaviour detection. The
service processes camera evidence locally, records auditable decisions, and may
request only catalogued neutral audio. It does not perform identity recognition,
ingest microphone audio, or send private media to remote services.

## Start here

1. Read `docs/draft.md` for the product intent.
2. Read `docs/dev_artifacts/2026-08-24-system-architecture-and-delivery-plan.md` only
   for the assigned implementation unit and its verification contract.
3. Claim or create `docs/dev_artifacts/tickets/<TICKET-ID>.md`; record research,
   verification, and unresolved blockers under `docs/dev_artifacts/`.
4. Preserve the v1 contracts in `proto/smoke/v1/`, `src/tp_smoke_detect/contracts.py`,
   and `schemas/smoke/v1/`. A breaking contract change requires a new version.

## Standard CPU commands

Install the lockfile and development tools:

```bash
uv sync --group dev
```

Run the same gates used by CI:

```bash
uv run ruff format --check .
uv run ruff check .
uv run mypy src tests
uv run pytest tests/unit
uv run pytest tests/contract
uv run smoke-detect schema --output schemas/smoke/v1
uv run smoke-detect validate-config configs/camera.example.yaml
uv run smoke-detect demo --fixture synthetic
```

The default profile is CPU-safe. GPU, DeepStream, model weights, and private
media are optional and must never be added to the base install or Git history.

## GitFlow and scope

- `main` is release-only; `develop` is the integration branch.
- Work on `feature/<ticket-id>-<slug>` from remote `develop` in a dedicated
  worktree. Push the branch and open a review against `develop`.
- Feature agents own only their ticket files. The tech lead owns shared schema
  migrations after U1; propose contract changes before editing them.
- Use conventional commits. Stage explicit paths, never `git add .`.

## Safety and artifacts

Runtime defaults are simulation, audio muted, reject-on-unclear, bounded queues,
and no remote URL fetching. Do not commit credentials, raw/identifying video,
generated model weights, or sensitive screenshots. Every ticket needs a concise
result in `docs/dev_artifacts/tickets/`; material problems need a reusable note
in `docs/dev_artifacts/memory/` with context, symptom, hypotheses, root cause,
fix, verification, prevention, and related commit.
