from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import yaml

from tp_smoke_detect.adapters.audio.fake import FakeAudioController
from tp_smoke_detect.application.request_audio import AudioRequestService
from tp_smoke_detect.contracts import DecisionCompleted, DecisionOutcome, RunMode
from tp_smoke_detect.domain.policy.audio import AudioPolicy, AudioPolicyConfig, AudioReasonCode
from tp_smoke_detect.observability.health import HealthRegistry, HealthState
from tp_smoke_detect.observability.metrics import OperationalMetrics


def _eligible_decision() -> DecisionCompleted:
    from uuid import uuid4

    return DecisionCompleted(
        decision_id=uuid4(),
        camera_id="camera-healthy",
        track_id="track-private",
        outcome=DecisionOutcome.VERIFIED,
        reason_codes=["verified"],
        policy_revision="p1",
        latency_ms=100,
        mode=RunMode.AUTOMATIC,
        audio_eligibility=True,
    )


def test_gpu_overload_and_model_failure_are_visible_and_fail_closed() -> None:
    metrics = OperationalMetrics()
    health = HealthRegistry(metrics)
    now = datetime.now(UTC)
    health.record_frame("camera-healthy", now)
    health.record_frame("camera-stalled", now)
    health.set_queue_depth("camera-stalled", 25)
    health.mark_camera_degraded("camera-stalled", "queue_saturated")
    metrics.gpu(
        utilization_ratio=0.99,
        memory_used_bytes=23_000_000_000,
        memory_total_bytes=24_000_000_000,
        memory_high_water_bytes=23_500_000_000,
        nvdec_utilization_ratio=0.95,
        batch_fill_ratio=0.25,
    )
    metrics.model_readiness("reviewer", ready=False, state="timeout")
    metrics.inference_failure("reviewer", "timeout")
    metrics.deadline("review", "expired")
    metrics.audio_suppressed("model_unavailable")

    snapshot = health.snapshot()
    rendered = metrics.render()
    assert snapshot["status"] == "ready"
    assert snapshot["cameras"]["camera-healthy"]["state"] == HealthState.HEALTHY
    assert snapshot["cameras"]["camera-stalled"]["state"] == HealthState.DEGRADED
    assert "camera-stalled" not in rendered
    assert "smoke_gpu_memory_pressure 1" in rendered
    assert 'smoke_model_readiness{model_role="reviewer",state="timeout"} 0' in rendered
    assert 'smoke_inference_failures_total{model_role="reviewer",status="timeout"} 1' in rendered
    assert 'smoke_deadline_outcomes_total{outcome="expired",stage="review"} 1' in rendered
    assert 'reason="model_unavailable"' in rendered


def test_degraded_model_cannot_create_audio_command() -> None:
    metrics = OperationalMetrics()
    metrics.model_readiness("reviewer", ready=False, state="unavailable")
    metrics.audio_suppressed("model_unavailable")
    service = AudioRequestService(
        AudioPolicy(AudioPolicyConfig(mode=RunMode.AUTOMATIC, audio_muted=True)),
        FakeAudioController(),
    )

    result = service.request(
        _eligible_decision(),
        camera_id="camera-healthy",
        zone_id="zone-a",
        now=datetime.now(UTC),
        camera_suspended=True,
    )

    assert result.allowed is False
    assert result.reason_code is AudioReasonCode.CAMERA_SUSPENDED
    assert result.command is None


def test_one_camera_restart_keeps_other_camera_and_core_ready() -> None:
    metrics = OperationalMetrics()
    health = HealthRegistry(metrics)
    now = datetime.now(UTC)
    health.record_frame("camera-a", now)
    health.record_frame("camera-b", now)
    health.set_component("core", HealthState.HEALTHY, last_success_at=now)
    health.set_component("media", HealthState.DEGRADED, message="camera-b restart")
    health.mark_camera_degraded("camera-b", "restart")
    metrics.restart("media")

    snapshot = health.snapshot()
    assert snapshot["components"]["core"]["state"] == HealthState.HEALTHY
    assert snapshot["cameras"]["camera-a"]["state"] == HealthState.HEALTHY
    assert snapshot["cameras"]["camera-b"]["state"] == HealthState.DEGRADED
    assert snapshot["status"] == "degraded"
    assert 'smoke_process_restarts_total{component="media"} 1' in metrics.render()


def test_prometheus_rules_and_dashboard_use_bounded_metric_surfaces() -> None:
    repository_root = Path(__file__).parents[2]
    rules = yaml.safe_load((repository_root / "deploy/prometheus/rules.yml").read_text())
    dashboard = json.loads((repository_root / "deploy/prometheus/dashboard.json").read_text())
    rule_text = (repository_root / "deploy/prometheus/rules.yml").read_text()
    dashboard_text = (repository_root / "deploy/prometheus/dashboard.json").read_text()

    assert rules["groups"]
    assert dashboard["panels"]
    assert "camera_id" not in rule_text
    assert "camera_id" not in dashboard_text
    for panel in dashboard["panels"]:
        for target in panel.get("targets", []):
            assert "track_id" not in target["expr"]
            assert "event_id" not in target["expr"]
            assert "gpu_id" not in target["expr"]
