#!/usr/bin/env bash
# Capture a reproducible, fail-closed GPU host and image-import receipt.
# This script never changes the host driver or operating system.
set -uo pipefail

SCRIPT_DIR=$(cd -- "$(dirname -- "$0")" && pwd)
REPO_ROOT=$(cd -- "$SCRIPT_DIR/.." && pwd)
DEFAULT_IMAGE="nvcr.io/nvidia/deepstream:7.0-triton-multiarch@sha256:c11befa808af8270e8ea0d0d7cc7cabbda08a2f496ee30b95f7acba5dce81759"
profile=rtx3090
output="$REPO_ROOT/docs/dev_artifacts/qualification/gpu-rtx3090-stack.md"
image=$(printenv GPU_QUALIFY_IMAGE 2>/dev/null || printf '%s' "$DEFAULT_IMAGE")
pull=1
run_container=1
registry_probe=1
pull_timeout=$(printenv GPU_QUALIFY_PULL_TIMEOUT 2>/dev/null || printf '%s' 120)

usage() {
  cat >&2 <<'EOF'
usage: scripts/qualify_gpu_host.sh [options]
  --profile PROFILE       Qualification profile (default: rtx3090)
  --output FILE           Markdown receipt path
  --image IMAGE           Exact digest-pinned image reference
  --no-pull               Do not pull; inspect only the local image cache
  --offline               Do not contact a registry; inspect only local evidence
  --no-container-probes   Do not execute container probes
EOF
}

while (($#)); do
  case "$1" in
    --profile) [[ $# -ge 2 ]] || { usage; exit 2; }; profile="$2"; shift 2 ;;
    --output) [[ $# -ge 2 ]] || { usage; exit 2; }; output="$2"; shift 2 ;;
    --image) [[ $# -ge 2 ]] || { usage; exit 2; }; image="$2"; shift 2 ;;
    --no-pull) pull=0; shift ;;
    --offline) pull=0; registry_probe=0; shift ;;
    --no-container-probes) run_container=0; shift ;;
    -h|--help) usage >&1; exit 0 ;;
    *) echo "unknown option: $1" >&2; usage; exit 2 ;;
  esac
done

if [[ "$profile" != "rtx3090" ]]; then
  echo "unsupported profile: $profile; only rtx3090 is defined" >&2
  exit 2
fi

mkdir -p "$(dirname -- "$output")"
tmp_dir=$(mktemp -d)
trap 'rm -rf -- "$tmp_dir"' EXIT
host_file="$tmp_dir/host.txt"
gpu_file="$tmp_dir/gpu.txt"
docker_file="$tmp_dir/docker.txt"
image_file="$tmp_dir/image.txt"
container_file="$tmp_dir/container.txt"
sbom_file="$tmp_dir/sbom.txt"
for file in "$host_file" "$gpu_file" "$docker_file" "$image_file" "$container_file" "$sbom_file"; do : >"$file"; done

host_status=pass
docker_status=fail
image_status=fail
container_status=not-run
manifest_status=fail
sbom_status=not-run

if command -v uname >/dev/null 2>&1; then
  uname -a >>"$host_file" 2>&1 || host_status=fail
else
  printf '%s\n' 'UNAVAILABLE: uname' >>"$host_file"
  host_status=fail
fi
if [[ -r /etc/os-release ]]; then
  awk -F= '/^(PRETTY_NAME|VERSION_ID|VERSION_CODENAME)=/{print}' /etc/os-release >>"$host_file"
else
  printf '%s\n' 'UNAVAILABLE: /etc/os-release' >>"$host_file"
  host_status=fail
fi
if command -v nvidia-smi >/dev/null 2>&1; then
  nvidia-smi --query-gpu=name,compute_cap,driver_version,memory.total --format=csv,noheader >"$gpu_file" 2>&1 || true
else
  printf '%s\n' 'UNAVAILABLE: nvidia-smi' >"$gpu_file"
fi
if command -v docker >/dev/null 2>&1; then
  docker_status=fail
  if docker info --format 'server={{.ServerVersion}} default={{.DefaultRuntime}}' >"$docker_file" 2>&1; then
    if docker info --format '{{json .Runtimes}}' | grep -q '"nvidia"'; then
      echo 'nvidia_runtime=present' >>"$docker_file"
      docker_status=pass
    else
      echo 'nvidia_runtime=missing' >>"$docker_file"
    fi
  fi
else
  printf '%s\n' 'UNAVAILABLE: docker' >"$docker_file"
fi

# Registry metadata is useful even when a large image cannot be imported.
image_ref=$(printf '%s' "$image" | cut -d@ -f1)
if [[ "$registry_probe" == 1 && "$docker_status" == pass ]] && docker manifest inspect "$image_ref" >"$tmp_dir/manifest.json" 2>"$tmp_dir/manifest.err"; then
  manifest_status=pass
  {
    echo 'registry_manifest=available'
    awk '/"digest":/ || /"architecture":/ || /"os":/ {gsub(/[",]/, ""); print}' "$tmp_dir/manifest.json"
  } >"$image_file"
else
  printf 'registry_manifest=%s\n' "$(if [[ "$registry_probe" == 1 ]]; then echo unavailable; else echo skipped-offline; fi)" >"$image_file"
  [[ -r "$tmp_dir/manifest.err" ]] && tr '\n' ' ' <"$tmp_dir/manifest.err" >>"$image_file"
fi

if [[ "$docker_status" == pass ]]; then
  if (( pull == 1 )); then
    if timeout "$pull_timeout" docker pull "$image" >"$tmp_dir/pull.log" 2>&1; then
      image_status=pass
      echo 'import=pull-succeeded' >>"$image_file"
    else
      echo 'import=pull-failed' >>"$image_file"
    fi
    tail -10 "$tmp_dir/pull.log" >>"$image_file" 2>/dev/null || true
  else
    printf '%s\n' 'import=skipped-by---no-pull' >>"$image_file"
  fi
  if docker image inspect --format 'repo_digests={{json .RepoDigests}} image_id={{.Id}}' "$image" >>"$tmp_dir/image-inspect.txt" 2>&1; then
    image_status=pass
    printf '%s\n' 'local_image=present' >>"$image_file"
    cat "$tmp_dir/image-inspect.txt" >>"$image_file"
  else
    printf '%s\n' 'local_image=absent-or-uninspectable' >>"$image_file"
    tail -2 "$tmp_dir/image-inspect.txt" >>"$image_file" 2>/dev/null || true
  fi
else
  printf '%s\n' 'import=not-run-docker-unavailable' >>"$image_file"
fi

if (( run_container == 1 )); then
  if [[ "$docker_status" == pass && "$image_status" == pass ]]; then
    # No host paths, sockets, credentials, or network services are mounted.
    if docker run --rm --pull=never --gpus all --network none --entrypoint nvidia-smi "$image" \
      --query-gpu=name,compute_cap,driver_version,memory.total --format=csv,noheader >"$container_file" 2>&1; then
      container_status=pass
    else
      container_status=fail
    fi
    docker run --rm --pull=never --gpus all --network none --entrypoint sh "$image" -c \
      'printf "deepstream-app="; command -v deepstream-app || true; printf " tritonserver="; command -v tritonserver || true; printf " trtexec="; command -v trtexec || true; printf "\n"' \
      >>"$container_file" 2>&1 || container_status=fail
  else
    container_status=blocked-image
    printf '%s\n' 'container probes blocked because exact image is not imported' >"$container_file"
  fi
else
  container_status=not-run
  printf '%s\n' 'container probes skipped by --no-container-probes' >"$container_file"
fi

if command -v docker >/dev/null 2>&1 && docker sbom --help >/dev/null 2>&1; then
  if [[ "$image_status" == pass ]] && docker sbom --format cyclonedx-json "$image" >"$sbom_file" 2>&1; then
    sbom_status=pass
  else
    sbom_status=failed
  fi
elif command -v docker >/dev/null 2>&1; then
  sbom_status=unavailable
fi

os_ok=fail
arch_ok=fail
gpu_model_ok=fail
driver_ok=fail
if grep -q 'VERSION_ID="22.04"' "$host_file" || grep -q '^VERSION_ID=22.04' "$host_file"; then os_ok=pass; fi
if uname -m 2>/dev/null | grep -qx 'x86_64'; then arch_ok=pass; fi
if grep -qi 'RTX 3090' "$gpu_file" && grep -q '8.6' "$gpu_file"; then gpu_model_ok=pass; fi
if grep -Eq '(^|[^0-9])580\.[0-9]+' "$gpu_file"; then driver_ok=pass; fi

overall=unqualified
if [[ "$host_status" == pass && "$os_ok" == pass && "$arch_ok" == pass && "$gpu_model_ok" == pass && "$driver_ok" == pass \
  && "$docker_status" == pass && "$manifest_status" == pass && "$image_status" == pass \
  && "$container_status" == pass && "$sbom_status" == pass ]]; then
  overall=host-and-image-ready
fi

redact() {
  sed -E 's#(https?://)[^ ]+#\1<redacted>#g; s#(/[^ ]*/)?\.docker/[^ ]+#<redacted>#g' "$1" | head -80
}

{
  echo "# GPU-101 RTX 3090 stack qualification receipt"
  echo
  echo "Generated (UTC): $(date -u +%FT%TZ)"
  echo
  echo "> Profile: $profile; image: $image. This receipt is fail-closed and does not constitute the one-stream or 20-stream GPU qualification owned by GPU-107."
  echo
  echo "## Overall status"
  echo
  echo "- **$overall**"
  echo "- Host probe: $host_status; OS: $os_ok; architecture: $arch_ok; GPU/compute capability: $gpu_model_ok; driver branch: $driver_ok."
  echo "- Docker/NVIDIA runtime: $docker_status; registry manifest: $manifest_status; exact image import: $image_status; container GPU probe: $container_status; SBOM: $sbom_status."
  echo
  echo "## Pinned runtime identity"
  echo
  echo '| Field | Expected | Evidence |'
  echo '|---|---|---|'
  echo '| Host OS | Ubuntu 22.04 x86_64 | captured below |'
  echo '| GPU | NVIDIA GeForce RTX 3090 / compute capability 8.6 | captured below |'
  echo '| Driver | R580 | captured below |'
  echo '| Container | DeepStream 7.0 Triton multiarch, linux/amd64 | image-pins.yaml and manifest probe |'
  echo '| Container CUDA | 12.2 | NVIDIA DeepStream 7.0 compatibility row |'
  echo '| Container TensorRT | 8.6.1.6 | NVIDIA DeepStream 7.0 compatibility row |'
  echo '| Bundled Triton | 23.10 | DeepStream 7.0 release notes |'
  echo '| SBOM | required before release | command/status below |'
  echo
  echo "## Host evidence"
  echo
  echo 'text-evidence-start'; redact "$host_file"; redact "$gpu_file"; redact "$docker_file"; echo 'text-evidence-end'
  echo
  echo "## Image/import evidence"
  echo
  echo 'text-evidence-start'; redact "$image_file"; echo 'text-evidence-end'
  echo
  echo "## Container evidence"
  echo
  echo 'text-evidence-start'; redact "$container_file"; echo 'text-evidence-end'
  echo
  echo "## SBOM evidence"
  echo
  echo "- Command: docker sbom --format cyclonedx-json $image"
  echo "- Result: $sbom_status"
  echo
  echo "## Release interpretation"
  echo
  echo "- Missing image, SBOM, failed container probe, or unsupported host keeps GPU readiness false."
  echo "- The optional LFM2-VL reviewer image is not part of this receipt and is not silently substituted."
  echo "- GPU-107 must still execute real NVDEC, the R5 model roles, Triton, deterministic decisions, audit persistence, and muted shadow audio before any lab claim."
  echo "- Reproduce with: scripts/qualify_gpu_host.sh --profile rtx3090."
} >"$output"

printf '%s\n' "$output"
[[ "$overall" == host-and-image-ready ]]
