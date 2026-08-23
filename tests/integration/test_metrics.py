from tp_smoke_detect.observability.metrics import MetricRegistry, OperationalMetrics


def test_metrics_use_fixed_low_cardinality_labels() -> None:
    metrics = OperationalMetrics()
    metrics.frame("camera-a", 100.0)
    metrics.decision("rejected", "quality")
    output = metrics.render()
    assert 'camera_id="camera-a"' in output
    assert "camera-a" in output
    assert "track-" not in output
    assert "decision_id" not in output


def test_registry_rejects_high_cardinality_label_schema() -> None:
    registry = MetricRegistry()
    try:
        registry.gauge("bad_metric", "bad", ("track_id",))
    except ValueError as error:
        assert "high-cardinality" in str(error)
    else:
        raise AssertionError("track IDs must not be metric labels")


def test_histogram_exposition_is_valid_and_deterministic() -> None:
    metrics = OperationalMetrics()
    metrics.stage_latency("quality", 0.2)
    output = metrics.render()
    assert "smoke_stage_latency_seconds_bucket" in output
    assert 'le="+Inf"' in output
