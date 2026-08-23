from __future__ import annotations

from pathlib import Path

from tp_smoke_detect.adapters.artifacts.local import LocalArtifactStore
from tp_smoke_detect.application.replay import ReplayWorker, synthetic_camera

PNG = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR" + (b"\x00" * 17)


def _store(tmp_path: Path, *names: str) -> LocalArtifactStore:
    for name in names:
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(PNG)
    return LocalArtifactStore(tmp_path)


def _frame(index: int, pts_ns: int, artifact_id: str) -> dict[str, object]:
    return {
        "frame_id": f"f-{index}",
        "artifact_id": artifact_id,
        "pts_ns": pts_ns,
        "track_id": "track-1",
        "person_box": {"x": 0.2, "y": 0.2, "width": 0.2, "height": 0.3, "confidence": 0.9},
    }


def test_sampling_clock_is_stable_and_does_not_duplicate_frames(tmp_path: Path) -> None:
    store = _store(tmp_path, "f0.png", "f1.png", "f2.png", "f3.png", "f4.png")
    manifest = {
        "camera_id": "cam-1",
        "camera_config_revision": "cam-1-r3",
        "source_fps": 10,
        "frames": [
            _frame(0, 0, "f0.png"),
            _frame(1, 100_000_000, "f1.png"),
            _frame(2, 200_000_000, "f2.png"),
            _frame(3, 300_000_000, "f3.png"),
            _frame(4, 400_000_000, "f4.png"),
        ],
    }

    first = ReplayWorker(synthetic_camera(), store, sampling_fps=5).replay(manifest)
    second = ReplayWorker(synthetic_camera(), store, sampling_fps=5).replay(manifest)

    assert first.sampled_frame_ids == ("f-0", "f-2", "f-4")
    assert [candidate.source_pts_ns for candidate in first.candidates] == [
        0,
        200_000_000,
        400_000_000,
    ]
    assert first.candidates == second.candidates
    assert len({candidate.source_pts_ns for candidate in first.candidates}) == 3
    assert first.candidates[1].capture_ts_ns == 200_000_000
    assert first.candidates[1].received_ts_ns == 200_000_000


def test_replay_event_identity_includes_recording_identity(tmp_path: Path) -> None:
    store = _store(tmp_path, "f0.png")
    base = {
        "camera_id": "cam-1",
        "recording_id": "recording-a",
        "frames": [_frame(0, 0, "f0.png")],
    }
    first = ReplayWorker(synthetic_camera(), store).replay(base)
    second = ReplayWorker(synthetic_camera(), store).replay(base | {"recording_id": "recording-b"})
    repeat = ReplayWorker(synthetic_camera(), store).replay(base)

    assert first.candidates[0].event_id != second.candidates[0].event_id
    assert first.candidates[0].correlation_id != second.candidates[0].correlation_id
    assert first.candidates[0].event_id == repeat.candidates[0].event_id


def test_legacy_replay_identity_includes_artifact_content(tmp_path: Path) -> None:
    first_root = tmp_path / "first"
    second_root = tmp_path / "second"
    _store(first_root, "f0.png")
    _store(second_root, "f0.png")
    (second_root / "f0.png").write_bytes(PNG + b"different-recording")
    manifest = {
        "camera_id": "cam-1",
        "frames": [_frame(0, 0, "f0.png")],
    }

    first = ReplayWorker(synthetic_camera(), LocalArtifactStore(first_root)).replay(manifest)
    second = ReplayWorker(synthetic_camera(), LocalArtifactStore(second_root)).replay(manifest)

    assert first.candidates[0].event_id != second.candidates[0].event_id
    assert first.candidates[0].correlation_id != second.candidates[0].correlation_id


def test_roi_and_excluded_zone_filter_candidates_without_media_leakage(tmp_path: Path) -> None:
    store = _store(tmp_path, "inside.png", "excluded.png", "outside.png")
    camera = synthetic_camera().model_validate(
        synthetic_camera().model_dump()
        | {
            "excluded_zones": [
                [
                    {"x": 0.2, "y": 0.2},
                    {"x": 0.5, "y": 0.2},
                    {"x": 0.5, "y": 0.6},
                    {"x": 0.2, "y": 0.6},
                ]
            ]
        }
    )
    manifest = {
        "camera_id": "cam-1",
        "frames": [
            _frame(0, 0, "inside.png")
            | {"person_box": {"x": 0.7, "y": 0.2, "width": 0.1, "height": 0.2, "confidence": 0.9}},
            _frame(1, 200_000_000, "excluded.png"),
            _frame(2, 400_000_000, "outside.png")
            | {"person_box": {"x": 1.0, "y": 0.2, "width": 0.1, "height": 0.2, "confidence": 0.9}},
        ],
    }

    result = ReplayWorker(camera, store, sampling_fps=5).replay(manifest)

    assert len(result.candidates) == 1
    assert result.candidates[0].artifact_ids == ["inside.png"]
    assert "f-1" in result.skipped_frame_ids
    assert "f-2" in result.skipped_frame_ids
    assert "fixture" not in result.candidates[0].model_dump_json()
