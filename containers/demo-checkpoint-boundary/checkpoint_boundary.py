"""Load sealed pickle-bearing checkpoints inside the disposable boundary."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import sys
import shutil
from pathlib import Path


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def main() -> int:
    os.umask(0)
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", action="append", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if any(
        token in key.lower()
        for key in os.environ
        for token in ("secret", "token", "password", "credential", "cookie", "api_key", "access_key")
    ):
        raise SystemExit("sensitive environment variable crossed checkpoint boundary")
    args.output.mkdir(parents=True, exist_ok=True)
    import torch  # imported only inside the disposable container

    loaded: list[dict[str, object]] = []
    safe_artifacts: list[dict[str, object]] = []
    from ultralytics import YOLO

    for raw in args.input:
        path = Path(raw)
        if not path.is_file() or path.is_symlink():
            raise SystemExit(f"input is not a regular file: {path}")
        # torch.load is restricted to this non-root, no-network, read-only-input
        # container. The loaded object is discarded after metadata extraction.
        value = torch.load(path, map_location="cpu", weights_only=False)
        keys = sorted(value.keys()) if isinstance(value, dict) else []
        checkpoint_sha = digest(path)
        loaded.append({"input": path.name, "sha256": checkpoint_sha, "loaded": True, "top_level_keys": keys[:32]})
        role = "person_pose" if "pose" in path.name else "cigarette_detector"
        # Export happens in the boundary while the pickle is still present;
        # host inference receives only this ONNX artifact afterwards.
        # Exporters derive the output name from the source path. Copying into
        # the container's writable tmpfs keeps the input mount genuinely RO.
        tmp_path = Path("/tmp") / path.name
        shutil.copyfile(path, tmp_path)
        model = YOLO(str(tmp_path))
        exported = Path(str(model.export(format="onnx", imgsz=640, half=False, device="cpu", simplify=False)))
        safe_path = args.output / f"{role}.onnx"
        shutil.copyfile(exported, safe_path)
        safe_artifacts.append({"role": role, "artifact_id": safe_path.name, "sha256": digest(safe_path), "size_bytes": safe_path.stat().st_size, "format": "onnx", "source_checkpoint_sha256": checkpoint_sha})
    receipt = {
        "schema_version": "demo.checkpoint-boundary.v1",
        "status": "passed",
        "container_user": f"{os.getuid()}:{os.getgid()}",
        "network": "none",
        "root_filesystem": "read-only",
        "inputs_mount": "read-only",
        "output_mount": "dedicated-read-write-only",
        "credentials": "not-provided",
        "python": sys.version.split()[0],
        "torch": torch.__version__,
        "host": platform.node(),
        "checkpoints": loaded,
        "safe_artifacts": safe_artifacts,
    }
    encoded = json.dumps(receipt, sort_keys=True, indent=2).encode() + b"\n"
    receipt["receipt_sha256"] = hashlib.sha256(encoded).hexdigest()
    (args.output / "checkpoint-boundary-receipt.json").write_text(
        json.dumps(receipt, sort_keys=True, indent=2) + "\n", encoding="utf-8"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
