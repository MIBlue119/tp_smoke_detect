#!/usr/bin/env bash
# Validate GPU-only Compose inputs before Docker creates any container.
set -euo pipefail

SCRIPT_DIR=$(cd -- "$(dirname -- "$0")" && pwd)
REPO_ROOT=$(cd -- "$SCRIPT_DIR/.." && pwd)
compose_file="$REPO_ROOT/deploy/compose.yaml"
key_file=${SMOKE_GPU_READINESS_SIGNING_KEY_FILE:-$REPO_ROOT/deploy/secrets/gpu_readiness_signing_key.txt}
executor_key_file=${SMOKE_GPU_EXECUTOR_VERIFY_KEY_FILE:-$REPO_ROOT/deploy/secrets/gpu_executor_verify_key.txt}
image_digest=${SMOKE_GPU_IMAGE_DIGEST:-}

if [[ -z "$image_digest" ]]; then
  echo "GPU Compose preflight failed: SMOKE_GPU_IMAGE_DIGEST is required" >&2
  echo "Set it to the digest embedded in the pinned DeepStream image and readiness receipt." >&2
  exit 78
fi
if [[ ! -r "$key_file" ]]; then
  echo "GPU Compose preflight failed: receipt signing key is unreadable: $key_file" >&2
  echo "Provision it from the site secret store; never commit the key." >&2
  exit 78
fi
if [[ ! -r "$executor_key_file" ]]; then
  echo "GPU Compose preflight failed: executor verification key is unreadable: $executor_key_file" >&2
  exit 78
fi

export SMOKE_GPU_IMAGE_DIGEST="$image_digest"
export SMOKE_GPU_READINESS_SIGNING_KEY_FILE="$key_file"
export SMOKE_GPU_EXECUTOR_VERIFY_KEY_FILE="$executor_key_file"
docker compose --profile gpu-rtx3090 -f "$compose_file" config --quiet
echo "GPU Compose preflight passed: digest and read-only receipt key are available."
