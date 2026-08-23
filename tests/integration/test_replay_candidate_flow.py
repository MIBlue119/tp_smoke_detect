from __future__ import annotations

from pathlib import Path

from tp_smoke_detect.adapters.artifacts.local import LocalArtifactStore
from tp_smoke_detect.adapters.messaging.in_memory import InMemoryMessageBus
from tp_smoke_detect.application.replay import ReplayWorker, synthetic_camera


def test_corrupt_and_unsupported_frames_are_structured_and_do_not_stop_replay(
    tmp_path: Path,
) -> None:
    png = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR" + (b"\x00" * 17)
    (tmp_path / "good.png").write_bytes(png)
    (tmp_path / "corrupt.png").write_bytes(b"not-an-image")
    (tmp_path / "clip.mp4").write_bytes(b"ftyp")
    store = LocalArtifactStore(tmp_path)
    bus = InMemoryMessageBus()
    box = {"x": 0.2, "y": 0.2, "width": 0.2, "height": 0.3, "confidence": 0.9}
    manifest = {
        "camera_id": "cam-1",
        "frames": [
            {"frame_id": "good", "artifact_id": "good.png", "pts_ns": 0, "person_box": box},
            {
                "frame_id": "corrupt",
                "artifact_id": "corrupt.png",
                "pts_ns": 200_000_000,
                "person_box": box,
            },
            {
                "frame_id": "unsupported",
                "artifact_id": "clip.mp4",
                "pts_ns": 400_000_000,
                "person_box": box,
            },
        ],
    }

    result = ReplayWorker(synthetic_camera(), store, bus, sampling_fps=5).replay(manifest)

    assert len(result.candidates) == 1
    assert len(bus.messages) == 1
    assert {error.code for error in result.errors} == {"corrupt_media", "unsupported_media"}
    assert {error.frame_id for error in result.errors} == {"corrupt", "unsupported"}
    assert result.sampled_frame_ids == ("good", "corrupt", "unsupported")


def test_artifact_store_rejects_traversal_and_symlink_escape(tmp_path: Path) -> None:
    root = tmp_path / "registered"
    root.mkdir()
    (tmp_path / "secret.png").write_bytes(b"secret")
    (root / "ok.png").write_bytes(b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR" + (b"\x00" * 17))
    (root / "escape.png").symlink_to(tmp_path / "secret.png")
    store = LocalArtifactStore(root)

    assert store.read_bytes("ok.png", require_image=True).startswith(b"\x89PNG")
    assert not store.exists("../secret.png")
    assert not store.exists("escape.png")
