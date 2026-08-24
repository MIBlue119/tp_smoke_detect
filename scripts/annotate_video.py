#!/usr/bin/env python3
"""Render frozen demo evidence into a labelled, silent MP4."""

from __future__ import annotations

import argparse
from pathlib import Path

from ml.demo.annotate import MAX_OUTPUT_BYTES, render_video, write_receipt


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True, help="sealed local source MP4")
    parser.add_argument("--annotation", type=Path, required=True, help="frozen evidence JSON")
    parser.add_argument("--output", type=Path, required=True, help="output MP4")
    parser.add_argument("--receipt", type=Path, required=True, help="media receipt JSON")
    parser.add_argument("--max-bytes", type=int, default=MAX_OUTPUT_BYTES)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    receipt = render_video(
        args.source,
        args.annotation,
        args.output,
        max_bytes=args.max_bytes,
    )
    write_receipt(args.receipt, receipt)
    print(
        f"rendered {args.output} ({receipt.output_bytes} bytes, "
        f"sha256={receipt.output_sha256}, audio_streams={receipt.audio_streams})"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
