# Installation runbook

This runbook installs the CPU/reference profile and describes the controlled
path to an air-gapped Compose bundle. It does not approve a production site,
GPU profile, model release, or automatic audio.

## 1. Prerequisites

- Linux or another supported Python host with Python 3.11 or 3.12.
- uv for the locked Python environment.
- Git for source checkout.
- Docker Engine and Compose v2 only for the container profile.
- A site secret store for deployment secrets; never commit the secret file.

The CPU path requires no NVIDIA driver, DeepStream, Triton, camera, speaker,
model weights, or public-network access after the lockfile and dependencies
are available.

## 2. Clean CPU install

From the repository root:

    git status --short
    uv sync --group dev
    uv run smoke-detect schema --output schemas/smoke/v1
    uv run smoke-detect validate-config configs/camera.example.yaml
    uv run smoke-detect demo --fixture synthetic
    uv run pytest tests/unit tests/contract tests/integration tests/e2e -q

The schema command must not produce an unreviewed contract diff. The synthetic
demo uses temporary in-memory fixture media and prints candidates=2 errors=0.
If the environment cannot install from its local cache, stop and stage the
approved wheelhouse rather than enabling runtime network access.

## 3. Local Compose reference deployment

The default profile is a CPU/reference deployment: the API uses its wired
SQLite audit repository and Prometheus scrapes local metrics. PostgreSQL and
MQTT are not started because this release does not ship their adapters. They
remain available only under the explicitly future `future-site` profile.
For a disposable local smoke test:

    docker compose -f deploy/compose.yaml config --quiet
    docker compose -f deploy/compose.yaml up -d
    curl --fail http://127.0.0.1:8000/health/live
    curl --fail http://127.0.0.1:8000/health/ready

The Compose network is internal;
the API and Prometheus ports bind to loopback by default. The read-only policy
and camera YAML mounts are loaded at API bootstrap; `SMOKE_DETECT_*` values in
Compose override only explicitly named YAML fields.

Inspect startup:

    docker compose -f deploy/compose.yaml ps
    docker compose -f deploy/compose.yaml logs --tail=100 smoke-detect
    docker compose -f deploy/compose.yaml logs --tail=100 prometheus

Do not start the audio profile or change the site mode during installation.
The media profile is a reference boundary and is not DeepStream qualification.

## 4. Air-gapped bundle gate

The release owner must complete these steps on a connected staging host, then
transfer only the reviewed bundle and an approved image archive:

1. Resolve every REQUIRED-BEFORE-RELEASE digest in deploy/image-pins.yaml;
   record the source registry and retrieval time in the release receipt.
2. Scan each image and the Python/native dependency trees; attach the SBOM and
   signed vulnerability dispositions.
3. Build with the pinned base image and lockfile:

       docker compose -f deploy/compose.yaml build --pull=false
       docker save approved-image-digests -o site-image-bundle.tar

4. Verify the archive hash out of band and import it into the site registry.
5. On the offline host, run docker compose config --quiet, start only the
   default profile, and execute the CPU and backup/restore receipts.
6. Record host, image digests, configuration revision, and reviewer in
   docs/dev_artifacts/qualification/. Do not record secret values or media.

Compose config can prove syntax, but cannot prove image provenance, target
throughput, model quality, legal approval, or public-audio readiness.
Production installation remains blocked while an image digest, SBOM,
model-origin, retention, or agency gate is unresolved. The future-site profile
must not be enabled until PostgreSQL/MQTT adapters and their acceptance tests
are released.

## 5. Uninstall and preserve evidence

    docker compose -f deploy/compose.yaml down

Do not use down -v on a site host until the retention owner approves it and a
media-free backup/restore receipt exists. Keep the release receipt and audit
database; expired media must be handled by the retention job, not by manually
copying the artifact directory into Git.
