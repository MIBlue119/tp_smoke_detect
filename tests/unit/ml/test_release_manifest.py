from ml.registry import (
    ModelReleaseManifest,
    Provenance,
    validate_promotion,
    validate_release_manifest,
)

HASH = "b" * 64


def release(
    *, decision: str = "approved", rollback: str | None = "model:0"
) -> ModelReleaseManifest:
    return ModelReleaseManifest(
        model_id="smoke-head",
        version="1.0.0",
        model_sha256=HASH,
        dataset_manifest_sha256=HASH,
        evaluation_report_sha256=HASH,
        calibration_report_sha256=HASH,
        runtime_profile="cpu-reference",
        provenance=Provenance("fixture:public", "Apache-2.0", decision, HASH),
        rollback_target=rollback,
        sbom_sha256=HASH,
    )


def test_valid_release_is_immutable_and_promotion_ready() -> None:
    candidate = release()
    assert validate_release_manifest(candidate) == ()
    result = validate_promotion(candidate)
    assert result.allowed
    assert result.release_sha256 == candidate.release_sha256
    assert candidate.to_json() == candidate.to_json()


def test_unknown_provenance_and_missing_rollback_block_promotion() -> None:
    candidate = release(decision="conditional", rollback=None)
    errors = validate_release_manifest(candidate)
    assert "provenance decision must be approved for promotion" in errors
    assert "rollback_target is required" in errors
    assert not validate_promotion(candidate).allowed
