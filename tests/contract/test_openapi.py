from tp_smoke_detect.adapters.persistence.sqlite import SQLiteAuditRepository
from tp_smoke_detect.api.app import create_app


def test_openapi_exposes_stable_v1_surface() -> None:
    paths = create_app(SQLiteAuditRepository()).openapi()["paths"]
    expected = {
        "/health/live",
        "/health/ready",
        "/metrics",
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
        "/v1/audio/requests",
        "/v1/models",
    }
    assert expected <= set(paths)
    assert "post" in paths["/v1/audio/requests"]
    request_schema = paths["/v1/audio/requests"]["post"]["requestBody"]["content"][
        "application/json"
    ]["schema"]
    assert request_schema["$ref"].endswith("/AudioRequestCreate")
    assert "get" in paths["/metrics"]
