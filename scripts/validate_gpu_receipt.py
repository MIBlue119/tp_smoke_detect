#!/usr/bin/env python3
"""Validate the authenticated one-stream receipt consumed by media-entrypoint."""

from __future__ import annotations

import argparse
from pathlib import Path

try:
    from scripts.gpu_receipts import validate_one_stream_receipt
except ModuleNotFoundError:  # container entrypoint copies the helper beside us
    from gpu_receipts import validate_one_stream_receipt


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--receipt", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--image-digest", required=True)
    parser.add_argument("--signing-key", type=Path, required=True)
    parser.add_argument("--signing-key-id", default="gpu-qualification")
    args = parser.parse_args()
    try:
        import json

        value = json.loads(args.receipt.read_text(encoding="utf-8"))
        key = args.signing_key.read_bytes()
        import hashlib

        manifest_hash = hashlib.sha256(args.manifest.read_bytes()).hexdigest()
    except (OSError, ValueError) as exc:
        print(f"GPU receipt cannot be read: {exc}")
        return 78
    errors = validate_one_stream_receipt(
        value,
        manifest_sha256=manifest_hash,
        image_digest=args.image_digest,
        signing_key=key,
        signing_key_id=args.signing_key_id,
    )
    if errors:
        for error in errors:
            print(error)
        return 78
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
