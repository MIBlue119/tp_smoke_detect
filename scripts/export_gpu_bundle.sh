#!/usr/bin/env bash
# Export a reviewed, offline GPU bundle. Image acquisition happens before this
# step and references must already be imported by digest.
set -euo pipefail

output=""
while (($#)); do
  case "$1" in
    --output) [[ $# -ge 2 ]] || { echo "--output requires a directory" >&2; exit 2; }; output="$2"; shift 2 ;;
    *) echo "usage: $0 --output DIRECTORY" >&2; exit 2 ;;
  esac
done
[[ -n "$output" ]] || { echo "--output is required" >&2; exit 2; }
command -v docker >/dev/null 2>&1 || { echo "docker is required" >&2; exit 127; }
mkdir -p "$output/images" "$output/sbom"

images=(
  "${SMOKE_CORE_IMAGE:-python:3.11.11-slim-bookworm@sha256:081075da77b2b55c23c088251026fb69a7b2bf92471e491ff5fd75c192fd38e5}"
  "${SMOKE_MQTT_IMAGE:-eclipse-mosquitto:2.0.20}"
  "${SMOKE_PROMETHEUS_IMAGE:-prom/prometheus:v2.54.1}"
  "${SMOKE_GPU_MEDIA_IMAGE:-nvcr.io/nvidia/deepstream:7.0-triton-multiarch@sha256:c11befa808af8270e8ea0d0d7cc7cabbda08a2f496ee30b95f7acba5dce81759}"
)
for image in "${images[@]}"; do
  docker image inspect "$image" >/dev/null 2>&1 || { echo "image is not imported locally: $image" >&2; exit 78; }
  digest=$(docker image inspect --format '{{index .RepoDigests 0}}' "$image")
  [[ "$digest" == *"@sha256:"* ]] || { echo "image has no immutable digest: $image" >&2; exit 78; }
  safe=$(printf '%s' "$image" | tr '/:@' '___')
  docker save "$image" -o "$output/images/$safe.tar"
  command -v docker >/dev/null 2>&1 && docker sbom --help >/dev/null 2>&1 || { echo "docker sbom is required" >&2; exit 78; }
  docker sbom --format cyclonedx-json "$image" >"$output/sbom/$safe.cdx.json"
  printf '%s\t%s\t%s\n' "$image" "$digest" "$(sha256sum "$output/images/$safe.tar" | awk '{print $1}')" >>"$output/images.tsv"
done

cp deploy/compose.yaml deploy/image-pins.yaml "$output/"
cp -a configs model-repository deploy/prometheus "$output/"
printf '%s\n' "$(date -u +%FT%TZ)" >"$output/exported-at.txt"
(cd "$output" && sha256sum images.tsv compose.yaml image-pins.yaml >bundle.sha256)
echo "GPU offline bundle exported to $output"
