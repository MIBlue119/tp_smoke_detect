from __future__ import annotations

from ml.demo.contracts import validate_video_annotation


def test_demo_schema_rejects_audio_and_production_decisions() -> None:
    payload = {
        "schema_version": "demo.video-annotation.v1",
        "audio_enabled": True,
        "production_decision_created": False,
    }
    errors = validate_video_annotation(payload)
    assert "audio_enabled must be false" in errors
    assert any("source is required" in error for error in errors)


def test_demo_schema_rejects_forbidden_private_content() -> None:
    payload = {
        "schema_version": "demo.video-annotation.v1",
        "audio_enabled": False,
        "production_decision_created": False,
        "raw_pixels": "should never be present",
    }
    assert any("raw_pixels" in error for error in validate_video_annotation(payload))


def test_demo_contract_is_versioned_and_audio_shadow_only() -> None:
    assert validate_video_annotation(
        {
            "schema_version": "wrong.v1",
            "audio_enabled": False,
            "production_decision_created": False,
        }
    )[0].startswith("schema_version")
