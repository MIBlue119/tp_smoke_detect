from datetime import UTC, datetime

from tp_smoke_detect.observability.health import HealthRegistry, HealthState


def test_stalled_camera_is_isolated_from_healthy_cameras() -> None:
    health = HealthRegistry()
    now = datetime.now(UTC)
    health.record_frame("camera-healthy", now)
    health.record_frame("camera-stalled", now)
    health.mark_camera_degraded("camera-stalled", "freshness_deadline")

    snapshot = health.snapshot()
    assert snapshot["status"] == "ready"
    assert snapshot["affected_camera_count"] == 1
    assert snapshot["cameras"]["camera-healthy"]["state"] == HealthState.HEALTHY
    assert snapshot["cameras"]["camera-stalled"]["state"] == HealthState.DEGRADED
    assert snapshot["cameras"]["camera-stalled"]["last_good_frame_at"] == now.isoformat()


def test_dependency_failure_changes_readiness() -> None:
    health = HealthRegistry()
    health.set_component("model", HealthState.DEGRADED, message="timeout")
    snapshot = health.snapshot()
    assert snapshot["status"] == "degraded"
    assert snapshot["components"]["model"]["state"] == "degraded"
