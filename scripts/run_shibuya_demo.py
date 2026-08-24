#!/usr/bin/env python3
"""Run the sealed Shibuya baseline through both real CUDA model roles.

This is an operator-facing integration command, intentionally separate from the
CPU-safe service entry point.  It writes only metadata and run evidence into
the repository; source media, checkpoints, and rendered video remain in the
ignored local artifact root.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import subprocess
import time
from pathlib import Path
from typing import Any, cast

from ml.demo.acquisition import atomic_write_json, sha256_file, validate_video_file
from ml.demo.annotate import render_video, write_receipt
from ml.demo.contracts import (
    DEMO_SCHEMA_VERSION,
    DemoRunReceipt,
    ModelReceipt,
    RightsDisposition,
    SourceReceipt,
)
from ml.demo.fusion import (
    CigaretteDetection,
    DeterministicFusion,
    FrameDetections,
    FusionConfig,
    FusionResult,
    PersonDetection,
)
from ml.demo.models import (
    CigaretteCudaAdapter,
    ModelSpec,
    PoseCudaAdapter,
    runtime_receipt,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--acquisition-receipt", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--model-root", type=Path, required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--device", type=int, default=0)
    parser.add_argument("--max-frames", type=int, default=None)
    return parser


def _load_json(path: Path) -> dict[str, Any]:
    return cast(dict[str, Any], json.loads(path.read_text(encoding="utf-8")))


def _source_receipt(payload: dict[str, Any]) -> SourceReceipt:
    values = dict(payload["source"])
    values["rights_disposition"] = RightsDisposition(values["rights_disposition"])
    return SourceReceipt(**values)


def _model_receipts(payload: dict[str, Any]) -> tuple[ModelReceipt, ...]:
    receipts: list[ModelReceipt] = []
    for item in payload["models"]:
        values = dict(item)
        values["rights_disposition"] = RightsDisposition(values["rights_disposition"])
        receipts.append(ModelReceipt(**values))
    return tuple(receipts)


def _model_spec(receipt: ModelReceipt, model_root: Path) -> ModelSpec:
    path = model_root / receipt.local_artifact_id.removeprefix("models/")
    # The receipt uses a root-relative id; model-root points at the actual
    # ignored ``.../models`` directory supplied by the staging workflow.
    if receipt.local_artifact_id.startswith("models/"):
        path = model_root / receipt.local_artifact_id.split("/", 1)[1]
    return ModelSpec(
        role=receipt.role,
        revision=receipt.model_revision,
        path=path,
        sha256=receipt.artifact_sha256,
        size_bytes=receipt.artifact_size_bytes,
        class_names=("cigarette",) if receipt.role == "cigarette_detector" else ("person",),
    )


def _ffmpeg_version() -> str:
    try:
        result = subprocess.run(
            ["ffmpeg", "-version"], check=True, capture_output=True, text=True, timeout=10
        )
    except (OSError, subprocess.SubprocessError):
        return "unavailable"
    return result.stdout.splitlines()[0] if result.stdout else "unknown"


def _manual_review(
    frames: tuple[Any, ...], events: tuple[Any, ...], fps: float
) -> list[dict[str, Any]]:
    """Create fixed review anchors; judgments are filled by the human reviewer."""

    anchors = {
        frame.frame_index for frame in frames if frame.frame_index % max(1, round(fps * 2)) == 0
    }
    for event in events:
        for timestamp in (event.start_pts_ns, event.end_pts_ns):
            anchors.add(round(timestamp / 1_000_000_000 * fps))
    return [
        {
            "frame_index": index,
            "judgment": "unclear",
            "reason": "fixed sample pending manual visual review; raw model output is unchanged",
        }
        for index in sorted(anchors)
    ]


def run(args: argparse.Namespace) -> dict[str, Any]:
    acquisition = _load_json(args.acquisition_receipt)
    config = _load_json(args.config)
    source = _source_receipt(acquisition)
    models = _model_receipts(acquisition)
    if source.sha256 != sha256_file(args.source) or source.byte_size != args.source.stat().st_size:
        raise RuntimeError("source does not match the sealed acquisition receipt")
    validate_video_file(args.source, source)
    if args.device != 0:
        raise RuntimeError("the baseline demo is pinned to CUDA device 0")
    if (
        os.environ.get("DEMO_ISOLATED_RUNTIME") != "1"
        or os.environ.get("DEMO_NETWORK_DISABLED") != "1"
    ):
        raise RuntimeError("set DEMO_ISOLATED_RUNTIME=1 and DEMO_NETWORK_DISABLED=1")

    pose_receipt = next(item for item in models if item.role == "person_pose")
    cigarette_receipt = next(item for item in models if item.role == "cigarette_detector")
    pose = PoseCudaAdapter(
        _model_spec(pose_receipt, args.model_root),
        device_index=args.device,
        confidence=0.25,
    )
    cigarette = CigaretteCudaAdapter(
        _model_spec(cigarette_receipt, args.model_root),
        device_index=args.device,
        confidence=0.25,
    )
    runtime = runtime_receipt(args.device)

    try:
        import cv2  # type: ignore[import-not-found]
    except ImportError as exc:
        raise RuntimeError("OpenCV is required in the isolated GPU runtime") from exc
    capture = cv2.VideoCapture(str(args.source))
    if not capture.isOpened():
        raise RuntimeError("OpenCV could not decode the sealed source")
    fps = float(capture.get(cv2.CAP_PROP_FPS))
    width = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT))
    if fps <= 0 or width <= 0 or height <= 0:
        raise RuntimeError("source decoder returned invalid dimensions or frame rate")

    fusion_config = FusionConfig(
        config_revision=str(config["config_revision"]),
        entry_confidence=float(config["fusion"]["entry_confidence"]),
        exit_confidence=float(config["fusion"]["exit_confidence"]),
        persistence_frames=int(config["fusion"]["persistence_frames"]),
        exit_gap_frames=6,
        association_threshold=0.35,
        max_track_gap_frames=6,
        iou_match_threshold=0.20,
        pose_confidence=0.20,
    )
    fusion = DeterministicFusion(fusion_config)
    evidence: list[Any] = []
    frame_count = 0
    started = time.perf_counter()
    try:
        while True:
            ok, frame = capture.read()
            if not ok:
                break
            if args.max_frames is not None and frame_count >= args.max_frames:
                break
            pose_output = pose.infer(frame)
            cigarette_output = cigarette.infer(frame)
            persons = tuple(
                PersonDetection(item.box, item.keypoints, item.confidence)
                for item in pose_output.detections
                if item.class_id == 0
            )
            cigarettes = tuple(
                CigaretteDetection(item.box, item.confidence, index)
                for index, item in enumerate(cigarette_output.detections)
            )
            pts_ns = round(frame_count * 1_000_000_000 / fps)
            evidence.extend(
                fusion.process_frame(
                    FrameDetections(
                        frame_index=frame_count,
                        source_pts_ns=pts_ns,
                        persons=persons,
                        cigarettes=cigarettes,
                    )
                )
            )
            frame_count += 1
            if frame_count % 100 == 0:
                print(f"processed {frame_count} frames", flush=True)
    finally:
        capture.release()
    closed = fusion.process(())
    result = FusionResult(tuple(evidence), closed.events)
    elapsed = time.perf_counter() - started
    output_root = args.output_root / args.run_id
    output_root.mkdir(parents=True, exist_ok=True)
    annotation_path = output_root / "annotation.json"
    output_video = output_root / "annotated.mp4"
    media_receipt_path = output_root / "media-receipt.json"
    config_hash = sha256_file(args.config)
    run_receipt = DemoRunReceipt(
        run_id=args.run_id,
        schema_version=DEMO_SCHEMA_VERSION,
        source=source,
        models=models,
        runtime=runtime,
        config_revision=str(config["config_revision"]),
        config_sha256=config_hash,
        frames=tuple(result.frames),
        events=tuple(result.events),
        manual_review=tuple(_manual_review(result.frames, result.events, fps)),
    )
    annotation_payload = run_receipt.to_dict()
    atomic_write_json(annotation_path, annotation_payload)
    media_receipt = render_video(args.source, annotation_path, output_video)
    write_receipt(media_receipt_path, media_receipt)
    manifest = {
        "schema_version": "demo.run-manifest.v1",
        "run_id": args.run_id,
        "status": "completed",
        "source_artifact_id": source.local_artifact_id,
        "source_sha256": source.sha256,
        "annotation_artifact_id": "annotation.json",
        "annotation_sha256": sha256_file(annotation_path),
        "output_artifact_id": "annotated.mp4",
        "output_sha256": media_receipt.output_sha256,
        "output_bytes": media_receipt.output_bytes,
        "frames_processed": frame_count,
        "source_duration_seconds": source.duration_seconds,
        "fps": fps,
        "elapsed_seconds": elapsed,
        "throughput_fps": frame_count / elapsed if elapsed else 0.0,
        "runtime": runtime.to_dict(),
        "adapter_receipts": [pose.receipt().to_dict(), cigarette.receipt().to_dict()],
        "export_backends": {"onnx": "not_attempted_in_u5", "tensorrt": "not_attempted_in_u5"},
        "event_count": len(result.events),
        "event_states": sorted({event.state.value for event in result.events}),
        "manual_review_count": len(run_receipt.manual_review),
        "audio_enabled": False,
        "production_decision_created": False,
        "host": platform.node(),
        "ffmpeg": _ffmpeg_version(),
        "config_sha256": config_hash,
        "notes": [
            "Baseline demo only; not production qualified.",
            "Manual review anchors are initialized to unclear and must be reconciled by a human.",
            "No production decision, audio request, identity inference, or remote inference "
            "was created.",
        ],
    }
    atomic_write_json(output_root / "manifest.json", manifest)
    return {
        "run_id": args.run_id,
        "annotation": str(annotation_path),
        "video": str(output_video),
        "media_receipt": str(media_receipt_path),
        "manifest": str(output_root / "manifest.json"),
        "frames": frame_count,
        "events": len(result.events),
        "output_sha256": media_receipt.output_sha256,
        "output_bytes": media_receipt.output_bytes,
    }


def main() -> int:
    args = _parser().parse_args()
    print(json.dumps(run(args), sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
