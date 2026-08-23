from ml.evaluation import (
    AblationConfig,
    AblationEvent,
    CropStrategy,
    EvaluationEvent,
    VLMStatus,
    calibrate_threshold,
    compare_crop_strategies,
    proportion_interval,
    run_ablation,
)


def row(event_id: str, label: str, score: float) -> EvaluationEvent:
    return EvaluationEvent(event_id, "cam-a", event_id, label, "smoking", score)


def test_calibration_is_deterministic_and_not_applicable_for_empty_split() -> None:
    rows = [row("a", "smoking", 0.9), row("b", "phone", 0.4)]
    first = calibrate_threshold(rows)
    second = calibrate_threshold(rows)
    assert first == second
    assert first.threshold == 0.41
    assert calibrate_threshold([]).calibration_state == "not_applicable"


def test_vlm_ablation_uses_same_sealed_events_and_fails_closed() -> None:
    events = [
        AblationEvent(row("positive", "smoking", 0.9), "smoking", 0.9, VLMStatus.OK, "vlm-fake"),
        AblationEvent(row("phone", "phone", 0.9), "not_smoking", 0.8, VLMStatus.OK, "vlm-fake"),
        AblationEvent(
            row("unclear", "smoking", 0.9),
            "smoking",
            0.8,
            VLMStatus.UNCLEAR,
            "vlm-fake",
        ),
    ]
    report = run_ablation(
        events,
        (
            AblationConfig("without-vlm", use_vlm=False),
            AblationConfig("with-vlm", use_vlm=True, vlm_revision="vlm-fake"),
        ),
    )
    assert report.results[0].report.true_positives == 2
    assert report.results[0].report.false_positives == 1
    assert report.results[1].report.true_positives == 1
    assert report.results[1].report.false_positives == 0
    assert report.results[0].sealed_event_ids == report.results[1].sealed_event_ids
    assert report.results[1].intervals.precision.lower is not None


def test_crop_comparison_has_one_contract_result_per_strategy() -> None:
    events = [AblationEvent(row("a", "smoking", 0.8))]
    report = compare_crop_strategies(
        {CropStrategy.FULL_FRAME: events, CropStrategy.HAND_FACE: events}
    )
    assert [result.config.crop_strategy for result in report.results] == [
        CropStrategy.FULL_FRAME,
        CropStrategy.HAND_FACE,
    ]


def test_wilson_interval_is_explicit_for_empty_and_bounded() -> None:
    assert proportion_interval(0, 0).lower is None
    interval = proportion_interval(1, 2)
    assert interval.lower is not None
    assert interval.estimate is not None
    assert interval.upper is not None
    assert 0 <= interval.lower <= interval.estimate <= interval.upper <= 1
