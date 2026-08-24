from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from pathlib import Path

import pytest
from ml.registry.model_repository import (
    AcquisitionRecord,
    ArtifactVerification,
    EngineBinding,
    LicenseDisposition,
    ModelArtifact,
    ModelRepositoryManifest,
    acquire_model_artifact,
    verify_local_artifact,
)

HASH = "a" * 64
OTHER_HASH = "b" * 64


def source(*, approved: bool = True, expected_hash: str | None = HASH) -> AcquisitionRecord:
    return AcquisitionRecord(
        canonical_uri="https://example.invalid/models/artifact.bin",
        publisher="Example Publisher",
        source_revision="rev-1",
        license_id="Apache-2.0",
        license_uri="https://example.invalid/licenses/apache-2.0",
        license_disposition=(
            LicenseDisposition.APPROVED if approved else LicenseDisposition.PENDING
        ),
        expected_sha256=expected_hash,
        expected_size_bytes=5,
    )


def artifact(*, approved: bool = True, expected_hash: str | None = HASH) -> ModelArtifact:
    return ModelArtifact(
        artifact_id="siglip2-crop-head",
        role="crop_classifier",
        version="0.1.0",
        source=source(approved=approved, expected_hash=expected_hash),
        local_filename="siglip2-crop-head.safetensors",
        artifact_sha256=expected_hash,
        artifact_size_bytes=5,
        parent_checkpoint="google/siglip2-base-patch16-224@rev-1",
        dataset_manifest_sha256=HASH,
        export_sha256=HASH,
        sbom_filename="siglip2-crop-head.cdx.json",
        sbom_sha256=HASH,
        rollback_target="siglip2-crop-head:0.0.0",
    )


def engine() -> EngineBinding:
    return EngineBinding(
        engine_id="siglip2-crop-head-fp16-rtx3090",
        artifact_id="siglip2-crop-head",
        artifact_version="0.1.0",
        plan_filename="model.plan",
        plan_sha256=HASH,
        model_sha256=HASH,
        export_sha256=HASH,
        input_contract_sha256=OTHER_HASH,
        calibration_sha256=OTHER_HASH,
        build_image_sha256=HASH,
        precision="fp16",
        compute_capability="8.6",
        cuda_version="12.2",
        tensorrt_version="8.6.1.6",
        runtime_profile="gpu-rtx3090-ds7",
    )


def test_complete_manifest_is_canonical_and_valid() -> None:
    manifest = ModelRepositoryManifest(
        release_id="gpu-baseline",
        release_version="0.1.0",
        models=(artifact(),),
        engines=(engine(),),
    )
    assert manifest.validate() == ()
    payload = json.loads(manifest.to_json())
    assert payload["manifest_sha256"] == manifest.digest
    assert payload["models"][0]["source"]["expected_sha256"] == HASH


def test_missing_provenance_hash_license_or_rollback_blocks_manifest() -> None:
    incomplete = artifact(approved=False, expected_hash=None)
    manifest = ModelRepositoryManifest(
        release_id="gpu-baseline",
        release_version="0.1.0",
        models=(incomplete,),
        engines=(),
    )
    errors = manifest.validate()
    assert any("license disposition" in error for error in errors)
    assert any("expected_sha256" in error for error in errors)
    assert any("artifact_sha256" in error for error in errors)


def test_engine_binding_rejects_runtime_or_input_mismatch() -> None:
    invalid = replace(engine(), model_sha256=OTHER_HASH, plan_sha256=None)
    errors = invalid.validate(artifact_hash=HASH, export_hash=HASH)
    assert any("plan_sha256" in error for error in errors)
    assert any("model_sha256" in error for error in errors)


def test_engine_binding_hashes_tensor_rt_plan_bytes(tmp_path: Path) -> None:
    plan = tmp_path / "model.plan"
    plan.write_bytes(b"plan-bytes")
    bound = replace(engine(), plan_sha256=hashlib.sha256(b"plan-bytes").hexdigest())
    assert bound.validate(plan_root=tmp_path) == ()
    plan.write_bytes(b"tampered")
    assert any("plan_sha256" in error for error in bound.validate(plan_root=tmp_path))


def test_local_verification_checks_artifact_and_sbom_hashes(tmp_path: Path) -> None:
    model_path = tmp_path / "siglip2-crop-head.safetensors"
    model_path.write_bytes(b"model")
    sbom_path = tmp_path / "siglip2-crop-head.cdx.json"
    sbom_path.write_text("sbom", encoding="utf-8")
    verified = replace(
        artifact(),
        artifact_sha256=hashlib.sha256(b"model").hexdigest(),
        artifact_size_bytes=5,
        sbom_sha256=hashlib.sha256(b"sbom").hexdigest(),
    )
    verified = replace(
        verified,
        source=replace(
            verified.source,
            expected_sha256=verified.artifact_sha256,
            expected_size_bytes=verified.artifact_size_bytes,
        ),
    )
    receipt = verify_local_artifact(verified, tmp_path)
    assert isinstance(receipt, ArtifactVerification)
    assert receipt.status == "verified"
    assert receipt.artifact_sha256 == verified.artifact_sha256
    assert receipt.sbom_sha256 == verified.sbom_sha256


def test_local_verification_is_fail_closed_for_missing_or_wrong_artifacts(tmp_path: Path) -> None:
    receipt = verify_local_artifact(artifact(), tmp_path)
    assert receipt.status == "blocked"
    assert "artifact file is missing" in receipt.errors

    model_path = tmp_path / "siglip2-crop-head.safetensors"
    model_path.write_bytes(b"wrong")
    (tmp_path / "siglip2-crop-head.cdx.json").write_text("sbom", encoding="utf-8")
    wrong = verify_local_artifact(artifact(), tmp_path)
    assert wrong.status == "blocked"
    assert "artifact sha256 mismatch" in wrong.errors


def test_acquisition_requires_explicit_network_and_approved_metadata(tmp_path: Path) -> None:
    receipt = acquire_model_artifact(artifact(), tmp_path)
    assert receipt.status == "blocked"
    assert any("network acquisition is disabled" in error for error in receipt.errors)


def test_checked_in_catalog_has_integrity_receipt_but_is_not_promotable() -> None:
    manifest = ModelRepositoryManifest.read("model-repository/manifest/model-release.json")
    assert {model.artifact_id for model in manifest.models} == {
        "peoplenet-transformer",
        "mediapipe-pose-landmarker",
        "mediapipe-hand-landmarker",
        "siglip2-crop-head",
        "lfm2-vl-reviewer",
    }
    assert manifest.validate()


def test_absolute_and_parent_paths_are_rejected() -> None:
    for filename in ("/tmp/model", "../model"):
        with pytest.raises(ValueError, match="local_filename"):
            replace(artifact(), local_filename=filename)
