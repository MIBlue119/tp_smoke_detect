#!/usr/bin/env python3
"""Run one replay manifest against a registered local artifact root."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from tp_smoke_detect.adapters.artifacts.local import LocalArtifactStore
from tp_smoke_detect.adapters.messaging.in_memory import InMemoryMessageBus
from tp_smoke_detect.application.replay import ReplayWorker, synthetic_camera


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifact-root", type=Path, required=True)
    parser.add_argument(
        "--manifest",
        required=True,
        help="root-relative JSON manifest ID; absolute paths and traversal are rejected",
    )
    parser.add_argument("--sampling-fps", type=float, default=None)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    store = LocalArtifactStore(args.artifact_root)
    bus = InMemoryMessageBus()
    result = ReplayWorker(synthetic_camera(), store, bus, sampling_fps=args.sampling_fps).replay(
        args.manifest
    )
    print(
        json.dumps(
            {
                "candidates": len(result.candidates),
                "errors": [
                    {"frame_id": error.frame_id, "code": error.code, "message": error.message}
                    for error in result.errors
                ],
                "sampled_frame_ids": result.sampled_frame_ids,
                "skipped_frame_ids": result.skipped_frame_ids,
            },
            sort_keys=True,
        )
    )
    return 0 if result.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
