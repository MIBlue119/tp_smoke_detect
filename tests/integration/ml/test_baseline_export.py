from pathlib import Path

from ml.evaluation import (
    AblationConfig,
    AblationEvent,
    EvaluationEvent,
    calibrate_threshold,
    run_ablation,
)
from ml.registry import (
    ModelReleaseManifest,
    Provenance,
    build_model_card,
    build_provenance_report,
)
from ml.training import build_baseline, load_baseline, verify_export_load

HASH = "a" * 64


def test_baseline_export_load_reproduces_reference_outputs(tmp_path: Path) -> None:
    other_classes = (
        "betel_quid",
        "phone",
        "drink",
        "food",
        "nose_touch",
        "pen_toothpick",
        "steam",
        "vape",
        "heated_tobacco",
        "background",
        "unknown",
    )
    artifact = build_baseline(weights={"smoking": 0.8, **{label: 0.1 for label in other_classes}})
    path = tmp_path / "baseline.json"
    assert verify_export_load(artifact, ({"hand_distance": 0.2}, {"hand_distance": 0.8}), path)
    assert load_baseline(path).digest == artifact.digest


def test_model_card_and_provenance_keep_fixture_evidence_explicit(tmp_path: Path) -> None:
    event = EvaluationEvent("e1", "cam-a", "track-a", "smoking", "smoking", 0.9)
    calibration = calibrate_threshold([event])
    evaluation = run_ablation([AblationEvent(event)], [AblationConfig("without-vlm")])
    release = ModelReleaseManifest(
        "smoke-head",
        "0.1.0",
        HASH,
        HASH,
        HASH,
        HASH,
        "cpu-reference",
        Provenance("fixture:local", "Apache-2.0", "approved", HASH),
        "smoke-head:0.0.0",
        sbom_sha256=HASH,
    )
    card = build_model_card(release, evaluation=evaluation, calibration=calibration)
    provenance = build_provenance_report(release, model_card=card)
    assert card.to_dict()["evidence_level"] == "fixture_only"
    assert provenance["qualification_state"] == "lab_required"
    card.write(tmp_path / "model-card.json")
