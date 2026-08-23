from ml.evaluation import EvaluationEvent, aggregate_events, evaluate_events


def event(
    event_id: str, true_label: str, predicted: str, score: float, *, start: int = 0
) -> EvaluationEvent:
    return EvaluationEvent(
        event_id,
        "cam-a",
        "track-a",
        true_label,
        predicted,
        score,
        start_ns=start,
        end_ns=start + 1_000_000,
    )


def test_continuous_session_is_counted_once_and_confusion_rates_are_named() -> None:
    events = [
        event("frame-1", "smoking", "smoking", 0.8),
        event("frame-2", "smoking", "smoking", 0.9, start=120_000_000_000),
        event("phone-1", "phone", "smoking", 0.7, start=500_000_000_000),
    ]
    report = evaluate_events(events, confusion_classes=("phone", "nose_touch"))
    assert report.total_events == 2
    assert report.true_positives == 1
    assert report.confusion_trigger_rates == {"nose_touch": None, "phone": 1.0}
    assert report.confusion_states["nose_touch"] == "not_applicable"


def test_empty_and_no_positive_sets_are_not_misleading_zeroes() -> None:
    report = evaluate_events([], confusion_classes=("phone",))
    assert report.metric_state == "not_applicable"
    assert report.precision is None
    assert report.recall is None
    assert report.confusion_trigger_rates["phone"] is None
    assert len(aggregate_events([])) == 0
