#!/usr/bin/env sh
set -eu
manifest=${SMOKE_MODEL_MANIFEST:-/models/manifest/model-release.json}
test -r "$manifest" || { echo "model manifest is missing: $manifest" >&2; exit 78; }
if grep -Eq '"(artifact_sha256|export_sha256|sbom_sha256|plan_sha256|model_sha256)"[[:space:]]*:[[:space:]]*null' "$manifest"; then
  echo "model manifest contains unresolved artifact or engine hashes" >&2
  exit 78
fi
command -v tritonserver >/dev/null 2>&1 || { echo "tritonserver is unavailable" >&2; exit 78; }
exec tritonserver --model-repository=/models --model-control-mode=none --strict-readiness=true --allow-http=true --http-port=8000 --allow-grpc=true --grpc-port=8001 --metrics-port=8002
