#!/usr/bin/env bash
set -u

# Capture an auditable host/image receipt without claiming unavailable hardware
# passed. Run this on the target host after image import.
output="docs/dev_artifacts/qualification/$(date -u +%F)-video-302-host.md"
media_image="${MEDIA_IMAGE:-}"
triton_image="${TRITON_IMAGE:-}"
while (($#)); do
  case "$1" in
    --output) output="$2"; shift 2 ;;
    --media-image) media_image="$2"; shift 2 ;;
    --triton-image) triton_image="$2"; shift 2 ;;
    *) echo "usage: $0 [--output FILE] [--media-image IMAGE] [--triton-image IMAGE]" >&2; exit 2 ;;
  esac
done
mkdir -p "$(dirname "$output")"
{
  echo "# VIDEO-302 media host qualification receipt"
  echo
  echo "Generated (UTC): $(date -u +%FT%TZ)"
  echo
  echo "> This is a capture script, not a pass decision. Missing commands are recorded as unavailable."
  echo
  echo '```text'
  if command -v uname >/dev/null; then uname -a; else echo "UNAVAILABLE uname"; fi
  if [[ -f /etc/os-release ]]; then cat /etc/os-release; else echo "UNAVAILABLE /etc/os-release"; fi
  if command -v nvidia-smi >/dev/null; then
    nvidia-smi --query-gpu=name,driver_version,memory.total --format=csv || true
  else echo "UNAVAILABLE nvidia-smi (GPU gate not run)"; fi
  if command -v docker >/dev/null; then
    for image in "$media_image" "$triton_image"; do
      [[ -n "$image" ]] || continue
      echo "image=$image"
      docker image inspect --format '{{index .RepoDigests 0}}' "$image" 2>&1 || true
    done
  else echo "UNAVAILABLE docker"; fi
  if command -v python3 >/dev/null; then python3 -VV; else echo "UNAVAILABLE python3"; fi
  if command -v deepstream-app >/dev/null; then deepstream-app --version; else echo "UNAVAILABLE deepstream-app"; fi
  if command -v tritonserver >/dev/null; then tritonserver --version; else echo "UNAVAILABLE tritonserver"; fi
  echo '```'
  echo
  echo "## Required follow-up"
  echo
  echo "- Run one replay stream, one Triton health/inference request, and the bounded 20-stream soak."
  echo "- Record p95 latency, dropped frames, GPU memory, queue depth, and verifier-failure isolation."
  echo "- A receipt with unavailable commands is evidence of an unrun gate, not qualification."
} >"$output"
echo "$output"
