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

## 5. RTX 3090 GPU profile (lab only)

The GPU profile is opt-in and remains shadow/audio-muted. It uses the pinned
DeepStream 7.0/Triton 23.10 RTX 3090 row from `deploy/image-pins.yaml`. It does
not qualify NGC availability, model quality, 20-camera capacity, or production
hardware. Import approved image archives and the model store before starting:

    scripts/import_gpu_bundle.sh /approved/tp-smoke-detect-gpu-bundle
    export SMOKE_GPU_IMAGE_DIGEST=c11befa808af8270e8ea0d0d7cc7cabbda08a2f496ee30b95f7acba5dce81759
    export SMOKE_GPU_RECEIPT_SIGNING_KEY_FILE=/run/site-secrets/tp-smoke-detect/gpu_receipt_signing_key
    scripts/preflight_gpu_compose.sh
    docker compose --profile gpu-rtx3090 -f deploy/compose.yaml up -d

`scripts/preflight_gpu_compose.sh` must pass before Docker is started. It fails
with an actionable message when `SMOKE_GPU_IMAGE_DIGEST` or the read-only
receipt signing key is absent. The key is mounted only into `media-gpu` at
`/run/secrets/gpu_receipt_signing_key`; provision it from the site secret store
using `deploy/secrets/gpu_receipt_signing_key.txt.example` as a shape reference.
The telemetry/fault runtime signer used by GPU-107 is a separate key and must
never be reused as a general application configuration secret.

Startup is fail-closed. `gpu-preflight` requires resolved image digests, SBOM
receipts, complete model/engine hashes, and a separately produced
`docs/dev_artifacts/qualification/gpu-readiness.json` with status
`one-stream-ready`. Missing artifacts keep `baseline-model`, `media-gpu`, and
the aggregate `gpu-readiness` service unhealthy. The optional `gpu-vlm` profile
is disabled and exits unless a separately qualified reviewer image is supplied.

Inspect independent readiness and GPU logs:

    docker compose --profile gpu-rtx3090 -f deploy/compose.yaml ps
    docker compose --profile gpu-rtx3090 -f deploy/compose.yaml logs --tail=100 gpu-preflight baseline-model media-gpu candidate-consumer gpu-readiness
    curl --fail http://127.0.0.1:8000/health/ready

The Compose GPU network is internal and has no MQTT or Triton host ports. Only
media, baseline model, and the optional reviewer receive the reserved NVIDIA
device. Camera/replay input, model repository, and retained artifacts are
read-only mounts; model loaders set offline mode and never fetch a URL.

Create a bundle only after image import, SBOM, and vulnerability review:

    scripts/export_gpu_bundle.sh --output /approved/tp-smoke-detect-gpu-bundle

This release still requires GPU-107's real one-stream and 20-stream receipts;
an available CUDA device or a successful Compose parse is not qualification.

## 6. GPU vertical-slice handoff (CPU-runnable)

The checked-in integration test exercises the metadata-only contract between
the DeepStream-shaped producer, candidate consumer, deterministic core, audit
store, bounded metrics, and muted shadow audio:

    uv run pytest tests/e2e/test_gpu_vertical_slice.py -q

This proves wiring and fail-closed behavior only. It does not execute NVDEC,
TensorRT, Triton, model weights, or real camera media. The candidate consumer
must load `configs/runtime.gpu-rtx3090.yaml`; that file intentionally contains
only `AppSettings` fields. Runtime/model identity belongs to
`configs/model-profile-gpu-rtx3090.yaml` and the immutable model manifest.

The GPU readiness server also requires the candidate consumer's shared
`/run/gpu-state/candidate-ready` file, in addition to the one-stream receipt
and core/Triton readiness. A missing or stale readiness boundary remains
unready.

## 7. Uninstall and preserve evidence

    docker compose -f deploy/compose.yaml down

Do not use down -v on a site host until the retention owner approves it and a
media-free backup/restore receipt exists. Keep the release receipt and audit
database; expired media must be handled by the retention job, not by manually
copying the artifact directory into Git.
