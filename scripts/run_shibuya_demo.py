#!/usr/bin/env python3
"""Run the sealed Shibuya baseline through both real CUDA model roles.

This is an operator-facing integration command, intentionally separate from the
CPU-safe service entry point.  It writes only metadata and run evidence into
the repository; source media, checkpoints, and rendered video remain in the
ignored local artifact root.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import os
import platform
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, cast

from ml.demo.acquisition import atomic_write_json, sha256_file, validate_video_file
from ml.demo.annotate import render_video, write_receipt
from ml.demo.contracts import (
    DEMO_SCHEMA_VERSION,
    DemoRunReceipt,
    EventState,
    FrameEvidence,
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
    parser.add_argument("--safe-model-root", type=Path, required=True)
    parser.add_argument("--boundary-receipt", type=Path, required=True)
    parser.add_argument("--manual-review", type=Path, required=True)
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


def _model_spec(receipt: ModelReceipt, model_root: Path, boundary: dict[str, Any]) -> ModelSpec:
    safe = next(
        (item for item in boundary.get("safe_artifacts", []) if item.get("role") == receipt.role),
        None,
    )
    if not isinstance(safe, dict):
        raise RuntimeError(f"boundary has no safe artifact for {receipt.role}")
    path = model_root / str(safe["artifact_id"])
    return ModelSpec(
        role=receipt.role,
        revision=receipt.model_revision,
        path=path,
        sha256=str(safe["sha256"]),
        size_bytes=int(safe["size_bytes"]),
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


def _load_manual_review(path: Path, run_id: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Load a completed immutable review; never publish pending placeholders."""

    review = _load_json(path)
    if review.get("run_id") != run_id:
        raise RuntimeError("manual review run_id does not match the requested run")
    if os.environ.get("DEMO_PREPARE_REVIEW") != "1":
        for field in ("pre_final_annotation_sha256", "rendered_video_sha256"):
            if not isinstance(review.get(field), str) or len(review[field]) != 64:
                raise RuntimeError(f"manual review must claim {field} before publication")
    judgments = review.get("judgments")
    if not isinstance(judgments, list) or not judgments:
        raise RuntimeError("manual review must contain at least one completed judgment")
    allowed = {"true_candidate", "false_candidate", "visible_miss", "unclear"}
    normalized: list[dict[str, Any]] = []
    for item in judgments:
        if not isinstance(item, dict) or item.get("judgment") not in allowed:
            raise RuntimeError("manual review contains a missing or pending judgment")
        normalized.append(
            {
                key: item[key]
                for key in ("event_id", "frame_index", "judgment", "reason")
                if key in item
            }
        )
    return normalized, review


def _environment_receipt() -> dict[str, Any]:
    """Capture a path-independent, package/SBOM receipt for the actual runner."""

    names = (
        "av",
        "jsonschema",
        "opencv-python-headless",
        "Pillow",
        "torch",
        "ultralytics",
    )
    packages: dict[str, str] = {}
    for name in names:
        try:
            packages[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            packages[name] = "missing"
    try:
        import torch

        packages["torch"] = str(torch.__version__)
    except ImportError:
        pass
    payload: dict[str, Any] = {
        "schema_version": "demo.python-runtime-receipt.v1",
        "python": sys.version.split()[0],
        "executable_basename": Path(sys.executable).name,
        "packages": packages,
        "sbom": "python-distribution-version-receipt",
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    payload["receipt_sha256"] = hashlib.sha256(canonical).hexdigest()
    return payload


def run(args: argparse.Namespace) -> dict[str, Any]:
    acquisition = _load_json(args.acquisition_receipt)
    config = _load_json(args.config)
    boundary = _load_json(args.boundary_receipt)
    if boundary.get("status") != "passed":
        raise RuntimeError("checkpoint boundary receipt is not passed")
    source = _source_receipt(acquisition)
    models = _model_receipts(acquisition)
    manual_review, review_payload = _load_manual_review(args.manual_review, args.run_id)
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
        _model_spec(pose_receipt, args.safe_model_root, boundary),
        device_index=args.device,
        confidence=0.25,
    )
    cigarette = CigaretteCudaAdapter(
        _model_spec(cigarette_receipt, args.safe_model_root, boundary),
        device_index=args.device,
        confidence=0.25,
    )
    runtime = runtime_receipt(args.device)

    try:
        import av
    except ImportError as exc:
        raise RuntimeError("PyAV is required in the pinned isolated GPU runtime") from exc
    container = av.open(str(args.source))
    stream = container.streams.video[0]
    fps = float(stream.average_rate or stream.base_rate or 0)
    width, height = int(stream.codec_context.width), int(stream.codec_context.height)
    if fps <= 0 or width <= 0 or height <= 0:
        raise RuntimeError("source decoder returned invalid dimensions or frame rate")
    time_base = stream.time_base

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
        for decoded in container.decode(video=0):
            if args.max_frames is not None and frame_count >= args.max_frames:
                break
            frame = decoded.to_ndarray(format="bgr24")
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
            if decoded.pts is not None and decoded.time_base is not None:
                pts_ns = round(float(decoded.pts * decoded.time_base) * 1_000_000_000)
            elif time_base is not None and decoded.pts is not None:
                pts_ns = round(float(decoded.pts * time_base) * 1_000_000_000)
            else:
                pts_ns = round(frame_count * 1_000_000_000 / fps)
            frame_evidence = fusion.process_frame(
                FrameDetections(
                    frame_index=frame_count,
                    source_pts_ns=pts_ns,
                    persons=persons,
                    cigarettes=cigarettes,
                )
            )
            evidence.extend(
                frame_evidence
                or (
                    FrameEvidence(
                        frame_count,
                        pts_ns,
                        None,
                        None,
                        (),
                        None,
                        None,
                        None,
                        None,
                        EventState.INSUFFICIENT_EVIDENCE,
                        ("missing_pose",),
                    ),
                )
            )
            frame_count += 1
            if frame_count % 100 == 0:
                print(f"processed {frame_count} frames", flush=True)
    finally:
        container.close()
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
        manual_review=tuple(manual_review),
    )
    annotation_payload = run_receipt.to_dict()
    atomic_write_json(annotation_path, annotation_payload)
    media_receipt = render_video(args.source, annotation_path, output_video)
    write_receipt(media_receipt_path, media_receipt)
    # The review is immutable companion evidence.  It is copied only after
    # the output hash exists, so the final manifest can bind all three files.
    if os.environ.get("DEMO_PREPARE_REVIEW") != "1" and review_payload["pre_final_annotation_sha256"] != sha256_file(annotation_path):
        raise RuntimeError("manual review annotation claim does not match pre-final evidence")
    if os.environ.get("DEMO_PREPARE_REVIEW") != "1" and review_payload["rendered_video_sha256"] != media_receipt.output_sha256:
        raise RuntimeError("manual review video claim does not match rendered output")
    review_path = output_root / "manual-review.json"
    review_path.write_bytes(args.manual_review.read_bytes())
    contact_sheet = output_root / "contact-sheet.jpg"
    subprocess.run(
        [
            "ffmpeg",
            "-loglevel",
            "error",
            "-y",
            "-i",
            str(output_video),
            "-vf",
            "fps=1/2,scale=320:-1,tile=5x5",
            "-frames:v",
            "1",
            str(contact_sheet),
        ],
        check=True,
    )
    environment = _environment_receipt()
    environment_path = output_root / "runtime-receipt.json"
    atomic_write_json(environment_path, environment)
    manifest = {
        "schema_version": "demo.run-manifest.v1",
        "run_id": args.run_id,
        "status": "completed",
        "source_artifact_id": source.local_artifact_id,
        "source_sha256": source.sha256,
        "annotation_artifact_id": "annotation.json",
        "annotation_sha256": sha256_file(annotation_path),
        "manual_review_artifact_id": "manual-review.json",
        "manual_review_sha256": sha256_file(review_path),
        "contact_sheet_artifact_id": "contact-sheet.jpg",
        "contact_sheet_sha256": sha256_file(contact_sheet),
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
        "checkpoint_boundary_artifact_id": args.boundary_receipt.name,
        "checkpoint_boundary_sha256": sha256_file(args.boundary_receipt),
        "safe_artifacts": boundary.get("safe_artifacts", []),
        "checkpoint_lineage": boundary.get("checkpoints", []),
        "runtime_receipt_artifact_id": "runtime-receipt.json",
        "runtime_receipt_sha256": sha256_file(environment_path),
        "notes": [
            "Baseline demo only; not production qualified.",
            "Manual review is a completed immutable companion; "
            "judgments are not pending placeholders.",
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
