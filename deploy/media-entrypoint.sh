#!/usr/bin/env sh
set -eu
receipt=${SMOKE_GPU_READINESS_RECEIPT:-/run/smoke-detect/gpu-readiness.json}
model_manifest=${SMOKE_MODEL_MANIFEST:-/models/manifest/model-release.json}
media_config=${SMOKE_GPU_MEDIA_CONFIG:-/etc/smoke-detect/deepstream/config.txt}
executable=${SMOKE_GPU_MEDIA_EXECUTABLE:-deepstream-app}
test -r "$receipt" || { echo "GPU readiness receipt is missing: $receipt" >&2; exit 78; }
grep -q '"status"[[:space:]]*:[[:space:]]*"one-stream-ready"' "$receipt" || { echo "GPU receipt is not one-stream-ready" >&2; exit 78; }
test -r "$model_manifest" || { echo "model manifest is missing: $model_manifest" >&2; exit 78; }
test -r "$media_config" || { echo "DeepStream config is missing: $media_config" >&2; exit 78; }
command -v "$executable" >/dev/null 2>&1 || { echo "DeepStream executable is unavailable: $executable" >&2; exit 78; }
exec "$executable" -c "$media_config"
