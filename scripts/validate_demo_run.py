#!/usr/bin/env python3
"""Validate demo metadata, CUDA runtime, or rendered media linkage."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from ml.demo.annotate import MAX_OUTPUT_BYTES, validate_media, write_receipt
from ml.demo.contracts import validate_video_annotation
from ml.demo.models import ModelAdapterError, runtime_receipt


def validate_run_receipt(payload: dict[str, Any]) -> tuple[str, ...]:
    errors = list(validate_video_annotation(payload))
    runtime = payload.get("runtime")
    if isinstance(runtime, dict):
        if runtime.get("fake_provider") is not False:
            errors.append("runtime.fake_provider must be false")
        if int(runtime.get("device_index", -1)) < 0:
            errors.append("runtime.device_index must identify a CUDA device")
    models = payload.get("models")
    if isinstance(models, list):
        for index, model in enumerate(models):
            if not isinstance(model, dict):
                continue
            policy = str(model.get("pickle_load_policy", "")).lower()
            if model.get("format") == "pickle" and "never" not in policy:
                errors.append(f"models[{index}] pickle policy is not fail-closed")
    return tuple(dict.fromkeys(errors))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--metadata-receipt", type=Path)
    parser.add_argument("--runtime", action="store_true")
    parser.add_argument("--source", type=Path)
    parser.add_argument("--annotation", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--media-receipt", type=Path)
    parser.add_argument("--max-bytes", type=int, default=MAX_OUTPUT_BYTES)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not (args.runtime or args.metadata_receipt or args.source):
        parser.error("select --runtime, --metadata-receipt, or media arguments")
    if args.runtime:
        try:
            receipt = runtime_receipt()
        except ModelAdapterError as exc:
            print(json.dumps({"status": "blocked", "errors": [str(exc)]}, indent=2))
            return 2
        print(json.dumps({"status": "passed", "runtime": receipt.to_dict()}, indent=2))
    if args.metadata_receipt:
        try:
            payload = json.loads(args.metadata_receipt.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            print(f"receipt read failed: {exc}", file=sys.stderr)
            return 2
        errors = validate_run_receipt(payload)
        if errors:
            print(json.dumps({"status": "failed", "errors": list(errors)}, indent=2))
            return 1
        print(json.dumps({"status": "passed"}, indent=2))
    if args.source:
        if not all((args.annotation, args.output, args.media_receipt)):
            parser.error("--source requires --annotation, --output, and --media-receipt")
        media_receipt = validate_media(
            args.source, args.annotation, args.output, max_bytes=args.max_bytes
        )
        write_receipt(args.media_receipt, media_receipt)
        print(json.dumps(media_receipt.to_dict(), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
