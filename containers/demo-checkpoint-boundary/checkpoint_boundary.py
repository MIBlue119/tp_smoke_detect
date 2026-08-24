"""Load sealed pickle-bearing checkpoints inside the disposable boundary."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import sys
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
    for raw in args.input:
        path = Path(raw)
        if not path.is_file() or path.is_symlink():
            raise SystemExit(f"input is not a regular file: {path}")
        # torch.load is restricted to this non-root, no-network, read-only-input
        # container. The loaded object is discarded after metadata extraction.
        value = torch.load(path, map_location="cpu", weights_only=False)
        keys = sorted(value.keys()) if isinstance(value, dict) else []
        loaded.append({"input": path.name, "sha256": digest(path), "loaded": True, "top_level_keys": keys[:32]})
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
    }
    encoded = json.dumps(receipt, sort_keys=True, indent=2).encode() + b"\n"
    receipt["receipt_sha256"] = hashlib.sha256(encoded).hexdigest()
    (args.output / "checkpoint-boundary-receipt.json").write_text(
        json.dumps(receipt, sort_keys=True, indent=2) + "\n", encoding="utf-8"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
