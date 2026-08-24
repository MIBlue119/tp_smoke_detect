import json
from pathlib import Path

import pytest
from ml.datasets import (
    DatasetGovernance,
    DatasetItem,
    DatasetManifest,
    DatasetSplit,
    register_dataset,
    validate_split_leakage,
)
from ml.evaluation import (
    AblationConfig,
    AblationEvent,
    EvaluationEvent,
    PromotionThresholds,
    SealedEventMetadata,
    build_replay_manifest,
    build_sealed_event_set,
    evaluate_promotion,
    load_replay_manifest,
    run_ablation,
)
from ml.evaluation.replay import ReplayStreamSpec
from ml.training import CalibrationAccessError, calibrate_training_split

HASH = "a" * 64


def dataset_item(item_id: str, camera: str, *, person: str = "person-a") -> DatasetItem:
    return DatasetItem(
        item_id=item_id,
        event_id=f"event-{item_id}",
        camera_id=camera,
        track_id=f"track-{item_id}",
        person_id=person,
        capture_day="2026-01-01",
        label="smoking",
        source_hash=HASH,
        license="CC-BY-4.0",
        source="catalogue:fixture",
    )


def test_registration_requires_explicit_governance_and_has_no_media_path() -> None:
    records = [dataset_item("1", "camera-a")]
    with pytest.raises(ValueError, match="retention_disposition"):
        register_dataset(
            "site",
            "1",
            records,
            governance=DatasetGovernance(
                "CC-BY-4.0", "", "approved-local", "approved", "consent", "site-catalogue"
            ),
        )
    manifest = register_dataset(
        "site",
        "1",
        records,
        governance=DatasetGovernance(
            "CC-BY-4.0",
            "delete-after-90d",
            "approved-local",
            "approved",
            "consent-record-1",
            "site-catalogue",
        ),
    )
    assert manifest.governance is not None
    assert "/" not in manifest.governance.media_store


def test_split_leakage_validator_catches_manual_person_crossing() -> None:
    first = dataset_item("1", "camera-a", person="person-a")
    second = dataset_item("2", "camera-b", person="person-a")
    manifest = DatasetManifest(
        "site",
        "1",
        (first, second),
        splits=(
            DatasetSplit("train", ("1",), ("camera-a",)),
            DatasetSplit("sealed_test", ("2",), ("camera-b",)),
        ),
    )
    assert any("person_id" in error for error in validate_split_leakage(manifest))


def test_calibration_rejects_sealed_labels() -> None:
    event = EvaluationEvent("e1", "camera-a", "track-a", "smoking", "smoking", 0.9)
    with pytest.raises(CalibrationAccessError):
        calibrate_training_split([event], split_name="sealed_test", dataset_manifest_sha256=HASH)


def test_replay_manifest_is_hashed_and_loader_rejects_tampering(tmp_path: Path) -> None:
    stream = ReplayStreamSpec(
        "stream-01",
        "camera-01",
        "h264",
        "high",
        1920,
        1080,
        30,
        10,
        5000,
        60,
        0.2,
        2,
        0.1,
        0.02,
        {"phone": 0.03},
        source_kind="synthetic",
    )
    manifest = build_replay_manifest("workload", "1", (stream,))
    path = tmp_path / "replay.json"
    manifest.write(path)
    assert load_replay_manifest(path).digest == manifest.digest
    raw = json.loads(path.read_text())
    raw["streams"][0]["source_fps"] = 29
    path.write_text(json.dumps(raw))
    with pytest.raises(ValueError, match="checksum"):
        load_replay_manifest(path)


def test_promotion_is_fail_closed_when_sealed_coverage_is_insufficient() -> None:
    rows = (
        AblationEvent(
            EvaluationEvent("positive", "camera-a", "track-a", "smoking", "smoking", 0.9)
        ),
    )
    sealed = build_sealed_event_set(
        rows,
        (SealedEventMetadata("positive", "camera-a", "2026-01-01", "organic"),),
    )
    report = run_ablation(
        rows,
        (AblationConfig("without-vlm"),),
        sealed_event_set_sha256=sealed.event_set_sha256,
    )
    decision = evaluate_promotion(
        sealed,
        report,
        thresholds=PromotionThresholds(
            min_organic_positives=2,
            min_each_confounder=1,
            min_cameras=2,
            min_calendar_days=2,
        ),
    )
    assert not decision.allowed
    assert any("organic positive" in reason for reason in decision.reasons)
