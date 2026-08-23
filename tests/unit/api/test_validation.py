from pathlib import Path

import pytest
from fastapi import HTTPException

from tp_smoke_detect.api.app import _safe_artifact_path
from tp_smoke_detect.api.models import ArtifactCreate, AudioMuteCreate


def test_artifact_path_is_relative_to_registered_root() -> None:
    assert _safe_artifact_path("replay/clip.mp4", Path("/tmp/artifacts")) == "replay/clip.mp4"


@pytest.mark.parametrize("path", ["../clip.mp4", "/etc/passwd", "https://example.invalid/a.mp4"])
def test_artifact_path_rejects_escape_and_remote_urls(path: str) -> None:
    with pytest.raises(HTTPException) as error:
        _safe_artifact_path(path, Path("/tmp/artifacts"))
    assert error.value.status_code == 422


def test_artifact_schema_rejects_unknown_media_and_oversized_input() -> None:
    with pytest.raises(ValueError):
        ArtifactCreate(path="clip.mov", media_type="video/quicktime", size_bytes=1)  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        ArtifactCreate(path="clip.mp4", media_type="video/mp4", size_bytes=500_000_001)


def test_mute_requires_scope_id_for_scoped_mutes() -> None:
    request = AudioMuteCreate(scope="camera", scope_id="cam-1", actor="operator", reason="test")
    assert request.scope_id == "cam-1"
