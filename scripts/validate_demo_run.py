"""Validate a private demo run receipt and capture a real CUDA runtime receipt.

This command is intentionally metadata-only.  It never downloads, loads a
checkpoint, reads a video, or accepts fake-provider telemetry.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

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
            if (
                model.get("format") == "pickle"
                and "never" not in str(model.get("pickle_load_policy", "")).lower()
            ):
                errors.append(f"models[{index}] pickle policy is not fail-closed")
    return tuple(dict.fromkeys(errors))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--receipt", type=Path, help="metadata-only demo receipt to validate")
    parser.add_argument("--runtime", action="store_true", help="print the actual CUDA runtime")
    args = parser.parse_args(argv)
    if args.runtime:
        try:
            receipt = runtime_receipt()
        except ModelAdapterError as exc:
            print(json.dumps({"status": "blocked", "errors": [str(exc)]}, indent=2))
            return 2
        print(json.dumps({"status": "passed", "runtime": receipt.to_dict()}, indent=2))
    if args.receipt:
        try:
            payload = json.loads(args.receipt.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            print(f"receipt read failed: {exc}", file=sys.stderr)
            return 2
        errors = validate_run_receipt(payload)
        if errors:
            print(json.dumps({"status": "failed", "errors": list(errors)}, indent=2))
            return 1
        print(json.dumps({"status": "passed"}, indent=2))
    if not args.runtime and not args.receipt:
        parser.error("one of --runtime or --receipt is required")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
