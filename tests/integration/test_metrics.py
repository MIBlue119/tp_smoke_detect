from tp_smoke_detect.observability.metrics import MetricRegistry, OperationalMetrics


def test_metrics_use_fixed_low_cardinality_labels() -> None:
    metrics = OperationalMetrics()
    metrics.frame("camera-a", 100.0)
    metrics.queue("camera-a", 4, age_seconds=0.4)
    metrics.gpu(
        utilization_ratio=0.75,
        memory_used_bytes=8_000_000_000,
        memory_total_bytes=24_000_000_000,
        nvdec_utilization_ratio=0.4,
        batch_fill_ratio=0.8,
    )
    metrics.decision("rejected", "quality")
    output = metrics.render()
    assert "camera_id" not in output
    assert "camera-a" not in output
    assert "track-" not in output
    assert "decision_id" not in output
    assert "smoke_gpu_utilization_ratio 0.75" in output
    assert "smoke_nvdec_utilization_ratio 0.4" in output


def test_registry_rejects_high_cardinality_label_schema() -> None:
    registry = MetricRegistry()
    try:
        registry.gauge("bad_metric", "bad", ("track_id",))
    except ValueError as error:
        assert "high-cardinality" in str(error)
    else:
        raise AssertionError("track IDs must not be metric labels")

    for forbidden in ("camera_id", "gpu_id", "gpu_uuid", "device_id"):
        try:
            registry.gauge(f"bad_{forbidden}", "bad", (forbidden,))
        except ValueError as error:
            assert "high-cardinality" in str(error)
        else:
            raise AssertionError(f"{forbidden} must not be a metric label")


def test_untrusted_label_values_are_normalized_to_bounded_vocabularies() -> None:
    registry = MetricRegistry()
    registry.counter("bounded", "bounded", ("model_role", "status"))
    registry.inc(
        "bounded",
        model_role="reviewer-with-unique-request-id-1",
        status="provider-error-with-raw-text-1",
    )
    registry.inc(
        "bounded",
        model_role="reviewer-with-unique-request-id-2",
        status="provider-error-with-raw-text-2",
    )
    output = registry.render()
    assert output.count("bounded{") == 1
    assert 'model_role="other"' in output
    assert 'status="other"' in output
    assert "unique-request-id" not in output
    assert "raw-text" not in output


def test_gpu_and_triton_measurements_are_present_with_bounded_dimensions() -> None:
    metrics = OperationalMetrics()
    metrics.model_readiness("crop_classifier", ready=True)
    metrics.model_readiness("arbitrary-model-revision-1", ready=False)
    metrics.triton_pending("reviewer", 3)
    metrics.inference_failure("reviewer", "timeout")
    metrics.deadline("review", "expired")
    metrics.sample_loss(2)
    metrics.restart("triton")
    output = metrics.render()
    assert 'smoke_model_readiness{model_role="crop_classifier",state="ready"} 1' in output
    assert 'model_role="other"' in output
    assert 'smoke_triton_pending_requests{model_role="reviewer"} 3' in output
    assert 'smoke_inference_failures_total{model_role="reviewer",status="timeout"} 1' in output
    assert 'smoke_deadline_outcomes_total{outcome="expired",stage="review"} 1' in output
    assert "smoke_camera_sample_loss_total 2" in output
    assert 'smoke_process_restarts_total{component="triton"} 1' in output


def test_histogram_exposition_is_valid_and_deterministic() -> None:
    metrics = OperationalMetrics()
    metrics.stage_latency("quality", 0.2)
    output = metrics.render()
    assert "smoke_stage_latency_seconds_bucket" in output
    assert 'le="+Inf"' in output
