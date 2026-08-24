#!/usr/bin/env python3
"""Validate a frozen annotation and its rendered media linkage."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from ml.demo.annotate import MAX_OUTPUT_BYTES, validate_media, write_receipt


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--annotation", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--receipt", type=Path, required=True)
    parser.add_argument("--max-bytes", type=int, default=MAX_OUTPUT_BYTES)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    receipt = validate_media(
        args.source,
        args.annotation,
        args.output,
        max_bytes=args.max_bytes,
    )
    write_receipt(args.receipt, receipt)
    print(json.dumps(receipt.to_dict(), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
