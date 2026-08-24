#!/usr/bin/env sh
set -eu
receipt=${SMOKE_GPU_READINESS_RECEIPT:-/run/smoke-detect/gpu-readiness.json}
model_manifest=${SMOKE_MODEL_MANIFEST:-/models/manifest/model-release.json}
receipt_key=${SMOKE_GPU_RECEIPT_SIGNING_KEY:-/run/secrets/gpu_receipt_signing_key}
receipt_key_id=${SMOKE_GPU_RECEIPT_SIGNING_KEY_ID:-gpu-qualification}
image_digest=${SMOKE_GPU_IMAGE_DIGEST:-}
media_config=${SMOKE_GPU_MEDIA_CONFIG:-/etc/smoke-detect/deepstream/config.txt}
executable=${SMOKE_GPU_MEDIA_EXECUTABLE:-/opt/smoke-detect/bin/media-publisher}
test -r "$receipt" || { echo "GPU readiness receipt is missing: $receipt" >&2; exit 78; }
test -r "$model_manifest" || { echo "model manifest is missing: $model_manifest" >&2; exit 78; }
test -r "$receipt_key" || { echo "GPU receipt signing key is missing: $receipt_key" >&2; exit 78; }
test -n "$image_digest" || { echo "GPU image digest binding is missing" >&2; exit 78; }
test -r "$media_config" || { echo "DeepStream config is missing: $media_config" >&2; exit 78; }
command -v "$executable" >/dev/null 2>&1 || { echo "DeepStream executable is unavailable: $executable" >&2; exit 78; }
PYTHONPATH=/opt/smoke-detect/receipt python3 /opt/smoke-detect/receipt/validate_gpu_receipt.py \
  --receipt "$receipt" --manifest "$model_manifest" --image-digest "$image_digest" \
  --signing-key "$receipt_key" --signing-key-id "$receipt_key_id" || exit 78
manifest_revision=${SMOKE_MANIFEST_REVISION:-$(sha256sum "$model_manifest" | awk '{print $1}')}
export SMOKE_MANIFEST_REVISION="$manifest_revision"
export SMOKE_ARTIFACT_REVISION="${SMOKE_ARTIFACT_REVISION:-$manifest_revision}"
export SMOKE_DETECTOR_MODEL_REVISION="${SMOKE_DETECTOR_MODEL_REVISION:-$manifest_revision:person_detector}"
export SMOKE_POSE_MODEL_REVISION="${SMOKE_POSE_MODEL_REVISION:-$manifest_revision:pose_landmarker}"
export SMOKE_HAND_MODEL_REVISION="${SMOKE_HAND_MODEL_REVISION:-$manifest_revision:hand_landmarker}"
export SMOKE_CROP_MODEL_REVISION="${SMOKE_CROP_MODEL_REVISION:-$manifest_revision:crop_classifier}"
export SMOKE_CAMERA_CONFIG_REVISION="${SMOKE_CAMERA_CONFIG_REVISION:-camera-config:rtx3090}"
export SMOKE_CANDIDATE_TOPIC="${SMOKE_CANDIDATE_TOPIC:-track.candidate.v1}"
export SMOKE_BROKER_HOST="${SMOKE_BROKER_HOST:-candidate-broker}"
export SMOKE_BROKER_PORT="${SMOKE_BROKER_PORT:-1883}"
exec "$executable" --config "$media_config"
