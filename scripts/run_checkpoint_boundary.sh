#!/usr/bin/env bash
set -euo pipefail

# Execute untrusted .pt loading in a non-root, no-network, read-only container.
repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
common_git_dir=$(git -C "$repo_root" rev-parse --git-common-dir)
project_root=$(cd "$(dirname "$common_git_dir")" && pwd)
input_root="$project_root/.local-demo-inputs/models"
output_root="$project_root/.local-demo-inputs/boundary"
image="tp-smoke-detect/demo-checkpoint-boundary:2026-08-24"
build=0
while (($#)); do
  case "$1" in
    --input-root) input_root=$2; shift 2 ;;
    --output-root) output_root=$2; shift 2 ;;
    --image) image=$2; shift 2 ;;
    --build) build=1; shift ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done
[[ -d "$input_root" && -d "$repo_root/containers/demo-checkpoint-boundary" ]] || { echo "input or boundary source directory is missing" >&2; exit 2; }
command -v docker >/dev/null || { echo "docker is required" >&2; exit 127; }
mkdir -p "$output_root"
# The disposable UID must be able to write only to this dedicated receipt
# directory; it is never the repository or a source/weight directory.
chmod 0777 "$output_root"
rm -f "$output_root/checkpoint-boundary-receipt.json"
if ((build)); then
  docker build --pull=false -t "$image" "$repo_root/containers/demo-checkpoint-boundary"
fi
docker image inspect "$image" >/dev/null 2>&1 || { echo "image $image is not present; rerun with --build" >&2; exit 78; }

docker run --rm --pull=never --network none --user 65532:65532 --read-only \
  --cap-drop ALL --security-opt no-new-privileges --pids-limit 256 --memory 6g \
  --env-file /dev/null --tmpfs /tmp:rw,noexec,nosuid,nodev,size=1g -e HOME=/tmp \
  -v "$input_root:/inputs:ro" -v "$output_root:/outputs:rw" "$image" \
  --input /inputs/yolo11n-pose-v8.3.0.pt \
  --input /inputs/heiher-smoking-detection-12a54cda.pt --output /outputs

receipt="$output_root/checkpoint-boundary-receipt.json"
image_id=$(docker image inspect --format '{{.Id}}' "$image")
python - "$receipt" "$image" "$image_id" <<'PY'
import hashlib, json, pathlib, sys
p = pathlib.Path(sys.argv[1])
v = json.loads(p.read_text())
assert v["status"] == "passed"
assert v["container_user"] == "65532:65532"
assert v["network"] == "none"
assert v["root_filesystem"] == "read-only"
assert v["inputs_mount"] == "read-only"
assert v["credentials"] == "not-provided"
assert len(v["checkpoints"]) == 2 and all(x["loaded"] for x in v["checkpoints"])
v["image_ref"] = sys.argv[2]
v["image_id"] = sys.argv[3]
v["invocation"] = ["--network", "none", "--user", "65532:65532", "--read-only", "inputs:ro", "outputs:rw"]
v.pop("receipt_sha256", None)
v["receipt_sha256"] = hashlib.sha256((json.dumps(v, sort_keys=True, indent=2) + "\n").encode()).hexdigest()
p.write_text(json.dumps(v, sort_keys=True, indent=2) + "\n")
print(f"checkpoint boundary passed: {p}")
PY
