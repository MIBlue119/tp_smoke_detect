from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

import pytest
from ml.demo.annotate import BANNER, AnnotationError, load_annotation, render_video


def _source(tmp_path: Path) -> Path:
    path = tmp_path / "source.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "testsrc=size=160x96:rate=5:duration=1",
            "-an",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            str(path),
        ],
        check=True,
    )
    return path


def _annotation(
    source: Path, *, frames: list[dict] | None = None, events: list[dict] | None = None
) -> Path:
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    payload = {
        "schema_version": "demo.video-annotation.v1",
        "run_id": "fixture-run",
        "source": {
            "video_id": "fixture-video",
            "canonical_url": "https://example.invalid/fixture",
            "title": "redistributable fixture",
            "uploader": "test",
            "duration_seconds": 1.0,
            "width": 160,
            "height": 96,
            "frame_rate": "5/1",
            "video_codec": "h264",
            "audio_streams": 0,
            "byte_size": source.stat().st_size,
            "sha256": digest,
            "acquisition_tool": "fixture",
            "acquisition_tool_version": "1",
            "source_revision": "fixture-video",
            "rights_disposition": "approved_reusable",
            "license_note": "synthetic test pattern",
            "local_artifact_id": "fixtures/source.mp4",
        },
        "models": [
            {
                "role": "person_pose",
                "artifact_id": "fixture-pose",
                "model_revision": "fixture-r1",
                "source_url": "https://example.invalid/pose",
                "model_card_license": "fixture",
                "parent_license": "fixture",
                "parent_license_url": "https://example.invalid/license",
                "dataset_ancestry": "synthetic",
                "rights_disposition": "approved_reusable",
                "local_artifact_id": "models/pose.pt",
                "artifact_size_bytes": 1,
                "artifact_sha256": "1" * 64,
                "format": "fixture",
                "pickle_load_policy": "never",
            },
            {
                "role": "cigarette_detector",
                "artifact_id": "fixture-cigarette",
                "model_revision": "fixture-r1",
                "source_url": "https://example.invalid/cigarette",
                "model_card_license": "fixture",
                "parent_license": "fixture",
                "parent_license_url": "https://example.invalid/license",
                "dataset_ancestry": "synthetic",
                "rights_disposition": "approved_reusable",
                "local_artifact_id": "models/cigarette.pt",
                "artifact_size_bytes": 1,
                "artifact_sha256": "2" * 64,
                "format": "fixture",
                "pickle_load_policy": "never",
            },
        ],
        "runtime": {
            "runtime_id": "fixture-cuda",
            "framework": "fixture",
            "framework_version": "1",
            "cuda_version": "fixture",
            "device_name": "RTX 3090 fixture",
            "compute_capability": "8.6",
            "device_index": 0,
            "fake_provider": False,
        },
        "config_revision": "fixture-r1",
        "config_sha256": "3" * 64,
        "frames": frames or [],
        "events": events or [],
        "manual_review": [],
        "audio_enabled": False,
        "production_decision_created": False,
    }
    path = source.parent / "annotation.json"
    path.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
    return path


def test_fixture_render_is_silent_and_hash_bound(tmp_path: Path) -> None:
    source = _source(tmp_path)
    annotation = _annotation(
        source,
        frames=[
            {
                "frame_index": 0,
                "source_pts_ns": 0,
                "track_id": "t1",
                "person_box": [0.1, 0.1, 0.7, 0.9],
                "pose_keypoints": [[0.4, 0.4, 0.9]],
                "cigarette_box": [0.45, 0.45, 0.5, 0.5],
                "person_confidence": 0.9,
                "cigarette_confidence": 0.8,
                "association_score": 0.75,
                "state": "candidate",
                "reason_codes": ["associated_cigarette"],
            },
        ],
        events=[
            {
                "event_id": "e1",
                "track_id": "t1",
                "start_pts_ns": 0,
                "end_pts_ns": 800_000_000,
                "state": "candidate",
                "reason_codes": ["associated_cigarette"],
            },
        ],
    )
    output = tmp_path / "annotated.mp4"
    receipt = render_video(source, annotation, output, max_bytes=1_000_000)
    assert output.is_file()
    assert receipt.no_audio_ok is True
    assert receipt.video_codec == "h264"
    assert receipt.pixel_format == "yuv420p"
    assert receipt.output_sha256 == hashlib.sha256(output.read_bytes()).hexdigest()
    assert receipt.annotation_sha256 == hashlib.sha256(annotation.read_bytes()).hexdigest()
    assert receipt.duration_ok is True


def test_empty_fixture_renders_banner_without_inventing_events(tmp_path: Path) -> None:
    source = _source(tmp_path)
    annotation = _annotation(source)
    output = tmp_path / "empty.mp4"
    receipt = render_video(source, annotation, output, max_bytes=1_000_000)
    assert load_annotation(annotation)["events"] == []
    assert BANNER == "BASELINE DEMO - NOT PRODUCTION QUALIFIED"
    assert receipt.audio_streams == 0


def test_source_hash_mismatch_blocks_render(tmp_path: Path) -> None:
    source = _source(tmp_path)
    annotation = _annotation(source)
    payload = json.loads(annotation.read_text(encoding="utf-8"))
    payload["source"]["sha256"] = "0" * 64
    annotation.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(AnnotationError, match="SHA-256"):
        render_video(source, annotation, tmp_path / "bad.mp4")


def test_oversized_render_is_not_published(tmp_path: Path) -> None:
    source = _source(tmp_path)
    annotation = _annotation(source)
    output = tmp_path / "too-large.mp4"
    with pytest.raises(AnnotationError, match="size limit"):
        render_video(source, annotation, output, max_bytes=100)
    assert not output.exists()
