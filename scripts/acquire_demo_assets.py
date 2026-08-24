#!/usr/bin/env python3
"""Acquire and seal the private Shibuya demo inputs.

This command is intentionally explicit.  It never runs during service start,
never loads a pickle checkpoint, and writes only metadata receipts to Git-safe
locations.  Use a local source path after an operator has obtained the named
video, or pass ``--download-models`` for the two pinned public checkpoints.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, cast

from ml.demo.acquisition import (
    AcquisitionBlockedError,
    atomic_write_json,
    download_verified,
    inspect_pickle_checkpoint,
    sha256_file,
)
from ml.demo.contracts import ModelReceipt, RightsDisposition, SourceReceipt, sha256_json

VIDEO_ID = "GByZa0qbA8A"
VIDEO_URL = "https://www.youtube.com/watch?v=GByZa0qbA8A"
POSE: dict[str, Any] = {
    "role": "person_pose",
    "artifact_id": "yolo11n-pose-v8.3.0",
    "model_revision": "ultralytics-assets-release-v8.3.0/yolo11n-pose.pt",
    "source_url": "https://github.com/ultralytics/assets/releases/download/v8.3.0/yolo11n-pose.pt",
    "model_card_license": "AGPL-3.0-or-later-or-Enterprise",
    "parent_license": "Ultralytics YOLO11 official terms",
    "parent_license_url": "https://www.ultralytics.com/license",
    "dataset_ancestry": (
        "Official release asset; upstream training ancestry is not independently "
        "re-audited in this private demo"
    ),
    "local_artifact_id": "models/yolo11n-pose-v8.3.0.pt",
    "artifact_size_bytes": 6255593,
    "artifact_sha256": "869e83fcdffdc7371fa4e34cd8e51c838cc729571d1635e5141e3075e9319dc0",
    "format": "pickle",
    "pickle_load_policy": "never load in acquisition; disposable unprivileged conversion only",
}
CIGARETTE: dict[str, Any] = {
    "role": "cigarette_detector",
    "artifact_id": "heiher-smoking-detection-12a54cda",
    "model_revision": "12a54cda2ca031e2b96a486bc288e957e56f51c8",
    "source_url": "https://huggingface.co/HEIher/smoking-detection/resolve/12a54cda2ca031e2b96a486bc288e957e56f51c8/best.pt",
    "model_card_license": "MIT (model card; not a substitute for parent terms)",
    "parent_license": "Ultralytics YOLO11 terms: AGPL-3.0-or-later-or-Enterprise",
    "parent_license_url": "https://www.ultralytics.com/license",
    "dataset_ancestry": (
        "Roboflow ancestry disclosed by model card; dataset redistribution terms "
        "not independently verified"
    ),
    "local_artifact_id": "models/heiher-smoking-detection-12a54cda.pt",
    "artifact_size_bytes": 40509349,
    "artifact_sha256": "0ef558d3cf049d0acbb3f2322bc9e4e53db1a107426e3669622176c30c054d82",
    "format": "pickle",
    "pickle_load_policy": "never load in acquisition; disposable unprivileged conversion only",
}


def _probe(path: Path) -> dict[str, Any]:
    completed = subprocess.run(
        ["ffprobe", "-v", "error", "-show_format", "-show_streams", "-of", "json", str(path)],
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
    return cast(dict[str, Any], json.loads(completed.stdout))


def _source_receipt(path: Path, metadata: dict[str, Any], artifact_id: str) -> SourceReceipt:
    probe = _probe(path)
    streams = probe.get("streams", [])
    videos = [item for item in streams if item.get("codec_type") == "video"]
    audios = [item for item in streams if item.get("codec_type") == "audio"]
    if len(videos) != 1 or audios:
        raise AcquisitionBlockedError("source must have exactly one video stream and no audio")
    stream = videos[0]
    duration = float(stream.get("duration") or probe.get("format", {}).get("duration") or 0)
    return SourceReceipt(
        video_id=VIDEO_ID,
        canonical_url=VIDEO_URL,
        title=str(metadata.get("title", "Smoking areas in Shibuya Tokyo Japan")),
        uploader=str(metadata.get("uploader", "4kocool")),
        duration_seconds=duration,
        width=int(stream["width"]),
        height=int(stream["height"]),
        frame_rate=str(stream["r_frame_rate"]),
        video_codec=str(stream["codec_name"]),
        audio_streams=0,
        byte_size=path.stat().st_size,
        sha256=sha256_file(path),
        acquisition_tool="yt-dlp",
        acquisition_tool_version=str(metadata.get("_version", {}).get("version", "unknown")),
        source_revision=VIDEO_ID,
        rights_disposition=RightsDisposition.PRIVATE_USER_EVALUATION,
        license_note=(
            "YouTube metadata exposes no reusable license; retain source/output for "
            "the named private user-directed evaluation only."
        ),
        local_artifact_id=artifact_id,
    )


def _model_receipt(spec: dict[str, Any]) -> ModelReceipt:
    return ModelReceipt(rights_disposition=RightsDisposition.PRIVATE_USER_EVALUATION, **spec)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifact-root", type=Path, default=Path("local_artifacts/model-demo"))
    parser.add_argument("--source-path", type=Path, required=True)
    parser.add_argument("--source-metadata", type=Path, required=True)
    parser.add_argument(
        "--receipt",
        type=Path,
        default=Path("docs/dev_artifacts/qualification/model-demo/acquisition-receipt.json"),
    )
    parser.add_argument("--download-models", action="store_true")
    parser.add_argument("--allow-network", action="store_true")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        metadata = json.loads(args.source_metadata.read_text(encoding="utf-8"))
        if metadata.get("id") != VIDEO_ID or metadata.get("webpage_url") != VIDEO_URL:
            raise AcquisitionBlockedError("source metadata is not the named Shibuya video")
        root = args.artifact_root.resolve()
        source_id = "source/shibuya-GByZa0qbA8A.mp4"
        source_path = root / source_id
        source_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(args.source_path, source_path)
        source = _source_receipt(source_path, metadata, source_id)
        models: list[ModelReceipt] = []
        for spec in (POSE, CIGARETTE):
            target = root / spec["local_artifact_id"]
            if args.download_models:
                download_verified(
                    spec["source_url"],
                    target,
                    expected_sha256=spec["artifact_sha256"],
                    expected_size=spec["artifact_size_bytes"],
                    allow_network=args.allow_network,
                )
            if not target.is_file():
                raise AcquisitionBlockedError(
                    f"model bytes are missing: {spec['local_artifact_id']}"
                )
            if (
                target.stat().st_size != spec["artifact_size_bytes"]
                or sha256_file(target) != spec["artifact_sha256"]
            ):
                raise AcquisitionBlockedError(
                    f"model bytes do not match the pinned receipt: {spec['artifact_id']}"
                )
            inspection = inspect_pickle_checkpoint(target)
            models.append(_model_receipt(spec))
            spec["static_inspection"] = inspection
        payload = {
            "schema_version": "demo.acquisition-receipt.v1",
            "status": "verified",
            "source": source.to_dict(),
            "models": [model.to_dict() for model in models],
            "static_pickle_inspection": {
                spec["artifact_id"]: spec["static_inspection"] for spec in (POSE, CIGARETTE)
            },
            "private_evaluation_only": True,
            "receipt_sha256": None,
        }
        payload["receipt_sha256"] = sha256_json(
            {key: value for key, value in payload.items() if key != "receipt_sha256"}
        )
        atomic_write_json(args.receipt, payload)
        print(args.receipt)
        return 0
    except (
        AcquisitionBlockedError,
        OSError,
        KeyError,
        TypeError,
        ValueError,
        json.JSONDecodeError,
        subprocess.SubprocessError,
    ) as exc:
        print(f"blocked: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
