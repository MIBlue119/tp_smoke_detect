#!/usr/bin/env bash
# Import an exported bundle without registry access. Archive hashes and SBOMs
# are checked before any image is loaded.
set -euo pipefail

bundle="${1:-}"
[[ -n "$bundle" && -d "$bundle" ]] || { echo "usage: $0 BUNDLE_DIRECTORY" >&2; exit 2; }
command -v docker >/dev/null 2>&1 || { echo "docker is required" >&2; exit 127; }
(cd "$bundle" && sha256sum -c bundle.sha256)
[[ -s "$bundle/images.tsv" ]] || { echo "images.tsv is missing" >&2; exit 78; }
shopt -s nullglob
archives=("$bundle"/images/*.tar)
sboms=("$bundle"/sbom/*.cdx.json)
(( ${#archives[@]} > 0 )) || { echo "no image archives found" >&2; exit 78; }
(( ${#sboms[@]} == ${#archives[@]} )) || { echo "each image requires one SBOM" >&2; exit 78; }
for archive in "${archives[@]}"; do docker load --input "$archive" >/dev/null; done
while IFS=$'\t' read -r image digest archive_sha; do
  [[ -n "$image" && "$digest" == *"@sha256:"* ]] || { echo "invalid image receipt" >&2; exit 78; }
  actual=$(docker image inspect --format '{{index .RepoDigests 0}}' "$image")
  [[ "$actual" == "$digest" ]] || { echo "digest mismatch after import for $image" >&2; exit 78; }
done <"$bundle/images.tsv"
echo "GPU offline bundle imported and digest-verified"
