from tp_smoke_detect.adapters.persistence.sqlite import SQLiteAuditRepository
from tp_smoke_detect.api.app import create_app


def test_openapi_exposes_stable_v1_surface() -> None:
    paths = create_app(SQLiteAuditRepository()).openapi()["paths"]
    expected = {
        "/health/live",
        "/health/ready",
        "/v1/capabilities",
        "/v1/cameras",
        "/v1/cameras/{camera_id}",
        "/v1/artifacts",
        "/v1/evaluations",
        "/v1/evaluations/{evaluation_id}",
        "/v1/events",
        "/v1/events/{decision_id}/reviews",
        "/v1/site-mode",
        "/v1/audio/mute",
        "/v1/models",
    }
    assert expected <= set(paths)
