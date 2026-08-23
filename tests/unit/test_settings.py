from pathlib import Path

import pytest
from pydantic import ValidationError

from tp_smoke_detect.contracts import RunMode
from tp_smoke_detect.settings import AppSettings, load_settings


def valid_camera() -> dict[str, object]:
    return {
        "camera_id": "cam-01",
        "zone_id": "lobby",
        "roi": [
            {"x": 0.0, "y": 0.0},
            {"x": 1.0, "y": 0.0},
            {"x": 1.0, "y": 1.0},
        ],
    }


def test_defaults_are_cpu_safe_and_audio_muted() -> None:
    settings = AppSettings()

    assert settings.policy.mode is RunMode.SIMULATION
    assert settings.policy.audio_muted is True
    assert settings.cameras == []


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("policy", {"queue_max_per_camera": 0}),
        ("policy", {"min_independent_channels": -1}),
        ("cameras", [{**valid_camera(), "min_face_pixels": -1}]),
    ],
)
def test_negative_limits_are_rejected(field: str, value: object) -> None:
    payload: dict[str, object] = {field: value}
    with pytest.raises(ValidationError) as error:
        AppSettings.model_validate(payload)

    assert field in str(error.value)


def test_invalid_camera_polygon_has_field_level_error() -> None:
    camera = valid_camera()
    camera["roi"] = [{"x": 0.1, "y": 0.1}] * 3

    with pytest.raises(ValidationError) as error:
        AppSettings.model_validate({"cameras": [camera]})

    assert "roi" in str(error.value)
    assert "distinct vertices" in str(error.value)


def test_unknown_mode_is_rejected() -> None:
    with pytest.raises(ValidationError) as error:
        AppSettings.model_validate({"policy": {"mode": "live"}})

    assert "mode" in str(error.value)


def test_example_yaml_files_load() -> None:
    for path in Path("configs").glob("*.example.yaml"):
        settings = load_settings(path)
        assert settings.service_name == "tp-smoke-detect"


def test_mounted_policy_and_camera_yaml_merge_before_environment(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    policy = tmp_path / "policy.yaml"
    policy.write_text("policy:\n  mode: shadow\n  audio_muted: false\n", encoding="utf-8")
    cameras = tmp_path / "cameras.yaml"
    cameras.write_text(
        "cameras:\n  - camera_id: cam-mounted\n    zone_id: lobby\n"
        "    roi: [{x: 0, y: 0}, {x: 1, y: 0}, {x: 1, y: 1}]\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("SMOKE_DETECT_ENVIRONMENT", "production")
    monkeypatch.setenv("SMOKE_DETECT_POLICY__AUDIO_MUTED", "true")

    settings = load_settings(policy_path=policy, cameras_path=cameras)

    assert settings.environment == "production"
    assert settings.policy.mode is RunMode.SHADOW
    assert settings.policy.audio_muted is True
    assert [camera.camera_id for camera in settings.cameras] == ["cam-mounted"]
