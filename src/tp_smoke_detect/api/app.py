"""Thin FastAPI edge for the v1 operator and integration surface."""

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, cast
from uuid import UUID, uuid4

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Response, status
from fastapi.responses import PlainTextResponse

from ..adapters.audio.fake import FakeAudioController
from ..adapters.persistence.sqlite import SQLiteAuditRepository
from ..application.request_audio import AudioRequestService
from ..contracts import RunMode
from ..domain.policy.audio import AudioPolicy, AudioPolicyConfig
from ..observability.health import HealthRegistry, HealthState
from ..observability.metrics import OperationalMetrics
from ..observability.structured import correlation_id
from ..ports.repositories import AuditRepository
from ..settings import AppSettings, CameraProfile, load_settings
from .models import (
    ArtifactCreate,
    AudioMuteCreate,
    AudioRequestCreate,
    CameraUpdate,
    EvaluationCreate,
    EvaluationResponse,
    Page,
    ReviewCreate,
    SiteModeChange,
)


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _safe_artifact_path(path: str, root: Path) -> str:
    """Return a normalized path only when it stays in the registered root."""

    candidate = Path(path)
    if "://" in path or candidate.is_absolute():
        raise HTTPException(
            status_code=422, detail="remote and absolute artifact paths are forbidden"
        )
    resolved_root = root.expanduser().resolve()
    resolved = (resolved_root / candidate).resolve()
    try:
        resolved.relative_to(resolved_root)
    except ValueError as exc:
        raise HTTPException(
            status_code=422, detail="artifact path escapes registered root"
        ) from exc
    return resolved.relative_to(resolved_root).as_posix()


def _decision_for_evaluation(request: EvaluationCreate) -> dict[str, object]:
    if request.decision is not None:
        decision = request.decision.model_dump(mode="json")
        decision["decision_id"] = str(request.decision.decision_id)
        # A replay's requested mode is authoritative for its audit record.
        decision["mode"] = request.mode.value
        return decision
    return {
        "decision_id": str(uuid4()),
        "camera_id": request.camera_id,
        "track_id": "evaluation",
        "outcome": "rejected",
        "reason_codes": ["evaluation_pending_provider"],
        "evidence_channels": [],
        "model_revisions": {},
        "policy_revision": "default",
        "latency_ms": 0.0,
        "mode": request.mode.value,
        "audio_eligibility": False,
    }


def _evaluation_response(value: dict[str, object]) -> "EvaluationResponse":
    """Hide storage bookkeeping from the stable polling contract."""

    return EvaluationResponse.model_validate(
        {key: value[key] for key in ("evaluation_id", "status", "result") if key in value}
    )


def create_app(
    repository: AuditRepository | None = None,
    *,
    settings: AppSettings | None = None,
    artifact_root: Path | str | None = None,
    audio_controller: object | None = None,
) -> FastAPI:
    """Build an app with explicit dependencies, suitable for tests and ASGI."""

    # Keep bootstrap configuration explicit and side-effect free for tests,
    # while allowing the container's read-only YAML mounts to be authoritative
    # before normal SMOKE_DETECT_* environment overrides are applied.
    app_settings = settings or load_settings()
    repo: AuditRepository = repository or SQLiteAuditRepository(app_settings.database)
    root = Path(artifact_root or app_settings.artifact_root)
    root.mkdir(parents=True, exist_ok=True)
    app = FastAPI(title="TP Smoke Detect AI Core", version="1.0.0", openapi_url="/openapi.json")
    app.state.repository = repo
    app.state.artifact_root = root
    app.state.settings = app_settings
    app.state.audio_controller = audio_controller or FakeAudioController()
    app.state.metrics = OperationalMetrics()
    app.state.health = HealthRegistry(app.state.metrics)
    app.state.health.set_component("database", HealthState.HEALTHY)

    # YAML camera profiles are authoritative configuration, so make them
    # available through the same persisted lookup used by audio policy.
    for profile in app_settings.cameras:
        if getattr(repo, "get_camera", lambda _camera_id: None)(profile.camera_id) is None:
            revision = hashlib.sha256(
                json.dumps(profile.model_dump(mode="json"), sort_keys=True).encode()
            ).hexdigest()[:16]
            repo.upsert_camera(
                {**profile.model_dump(mode="json"), "revision": revision, "active": profile.enabled}
            )

    def get_repository() -> AuditRepository:
        return cast(AuditRepository, app.state.repository)

    Repo = Annotated[AuditRepository, Depends(get_repository)]

    @app.get("/health/live", tags=["health"])
    def live() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/health/ready", tags=["health"])
    def ready(repository: Repo) -> dict[str, object]:
        healthy = repository.health()
        app.state.health.set_component(
            "database",
            HealthState.HEALTHY if healthy else HealthState.DEGRADED,
            message=None if healthy else "database health check failed",
        )
        snapshot = app.state.health.snapshot()
        payload: dict[str, object] = {
            "status": "ready" if healthy else "degraded",
            # Keep the original boolean database component for API clients;
            # richer states live in the additive component_health field.
            "components": {"database": healthy},
            "component_health": snapshot["components"],
            "cameras": snapshot["cameras"],
            "affected_camera_count": snapshot["affected_camera_count"],
            "queue_depth": snapshot["queue_depth"],
            "last_successful_activity": snapshot["last_successful_activity"],
        }
        if not healthy:
            raise HTTPException(status_code=503, detail=payload)
        return payload

    @app.get("/metrics", response_class=PlainTextResponse, tags=["health"])
    def metrics() -> Response:
        """Expose bounded operational metrics for a local Prometheus scrape."""

        with correlation_id():
            return PlainTextResponse(
                app.state.metrics.render(),
                media_type="text/plain; version=0.0.4; charset=utf-8",
            )

    @app.get("/v1/capabilities", tags=["service"])
    def capabilities() -> dict[str, object]:
        return {
            "api_version": "v1",
            "schemas": ["track.candidate.v1", "decision.completed.v1", "audio.command.v1"],
            "modes": [mode.value for mode in RunMode],
            "stages": ["detected", "quality", "pose", "object", "temporal", "review", "completed"],
            "model_roles": [],
        }

    @app.get("/v1/cameras", tags=["cameras"])
    def cameras(repository: Repo) -> list[dict[str, object]]:
        return repository.list_cameras()

    @app.put("/v1/cameras/{camera_id}", tags=["cameras"])
    def update_camera(
        camera_id: str,
        request: CameraUpdate | CameraProfile,
        repository: Repo,
    ) -> dict[str, object]:
        # ``CameraUpdate`` is the explicit API shape.  Accepting the canonical
        # profile directly keeps PUT ergonomic for operators and older clients.
        if isinstance(request, CameraUpdate):
            profile = request.profile
            revision = request.revision
            active = request.activate
        else:
            profile = request
            revision = hashlib.sha256(
                json.dumps(profile.model_dump(mode="json"), sort_keys=True).encode()
            ).hexdigest()[:16]
            active = False
        if profile.camera_id != camera_id:
            raise HTTPException(status_code=422, detail="camera_id must match path")
        payload = profile.model_dump(mode="json")
        payload.update({"revision": revision, "active": active})
        return repository.upsert_camera(payload)

    @app.post("/v1/artifacts", status_code=status.HTTP_201_CREATED, tags=["artifacts"])
    def create_artifact(request: ArtifactCreate, repository: Repo) -> dict[str, object]:
        relative_path = _safe_artifact_path(request.path, app.state.artifact_root)
        artifact = request.model_dump()
        artifact["artifact_id"] = artifact["artifact_id"] or str(uuid4())
        artifact["path"] = relative_path
        return repository.put_artifact(artifact)

    @app.post(
        "/v1/evaluations", response_model=EvaluationResponse, status_code=202, tags=["evaluations"]
    )
    def create_evaluation(
        request: EvaluationCreate,
        repository: Repo,
        idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
    ) -> EvaluationResponse:
        if idempotency_key:
            existing = repository.get_evaluation_by_idempotency(idempotency_key)
            if existing:
                return _evaluation_response(existing)
        if request.artifact_id and repository.get_artifact(request.artifact_id) is None:
            raise HTTPException(status_code=404, detail="artifact not found")
        evaluation_id = uuid4()
        decision = _decision_for_evaluation(request)
        # Persisting a completed deterministic result makes the CPU replay path
        # pollable while a future worker can replace this with running status.
        if request.mode is RunMode.SHADOW:
            decision["audio_eligibility"] = False
            decision["audio_outcome"] = (
                "would_announce"
                if request.decision and request.decision.audio_eligibility
                else None
            )
        saved_decision = repository.put_decision(decision)
        app.state.metrics.decision(
            str(saved_decision["outcome"]), str(saved_decision["reason_codes"][0])
        )
        result = {"decision": saved_decision}
        saved = repository.put_evaluation(
            {
                "evaluation_id": str(evaluation_id),
                "status": "completed",
                "result": result,
                "idempotency_key": idempotency_key,
                "camera_id": request.camera_id,
            }
        )
        return _evaluation_response(saved)

    @app.get(
        "/v1/evaluations/{evaluation_id}", response_model=EvaluationResponse, tags=["evaluations"]
    )
    def get_evaluation(evaluation_id: UUID, repository: Repo) -> EvaluationResponse:
        result = repository.get_evaluation(str(evaluation_id))
        if result is None:
            raise HTTPException(status_code=404, detail="evaluation not found")
        return _evaluation_response(result)

    @app.get("/v1/events", response_model=Page, tags=["events"])
    def events(
        repository: Repo,
        camera_id: str | None = None,
        outcome: str | None = None,
        reason: str | None = None,
        after: datetime | None = None,
        before: datetime | None = None,
        limit: Annotated[int, Query(ge=1, le=500)] = 100,
        offset: Annotated[int, Query(ge=0)] = 0,
    ) -> Page:
        return Page(
            items=repository.list_decisions(
                camera_id=camera_id,
                outcome=outcome,
                reason=reason,
                after=after,
                before=before,
                limit=limit,
                offset=offset,
            ),
            limit=limit,
            offset=offset,
        )

    @app.post("/v1/events/{decision_id}/reviews", status_code=201, tags=["events"])
    def review(decision_id: UUID, request: ReviewCreate, repository: Repo) -> dict[str, object]:
        if repository.get_decision(str(decision_id)) is None:
            raise HTTPException(status_code=404, detail="decision not found")
        return repository.append_review(
            {"review_id": str(uuid4()), "decision_id": str(decision_id), **request.model_dump()}
        )

    @app.post("/v1/site-mode", status_code=201, tags=["policy"])
    def site_mode(request: SiteModeChange, repository: Repo) -> dict[str, object]:
        return repository.append_mode_change(
            {"change_id": str(uuid4()), **request.model_dump(mode="json")}
        )

    @app.post("/v1/audio/mute", status_code=201, tags=["policy"])
    def mute(request: AudioMuteCreate, repository: Repo) -> dict[str, object]:
        return repository.put_mute({"mute_id": str(uuid4()), **request.model_dump(mode="json")})

    @app.post("/v1/audio/requests", status_code=201, tags=["policy"])
    def request_audio(request: AudioRequestCreate, repository: Repo) -> dict[str, object]:
        decision = repository.get_decision(str(request.decision_id))
        if decision is None:
            raise HTTPException(status_code=404, detail="decision not found")
        camera_id = str(decision["camera_id"])
        camera = repository.get_camera(camera_id)
        if camera is None or not camera.get("zone_id"):
            raise HTTPException(status_code=409, detail="camera profile with zone_id is required")
        zone_id = str(camera["zone_id"])
        policy_settings = app.state.settings.policy
        quiet_hours = None
        if (
            policy_settings.quiet_hours_start is not None
            and policy_settings.quiet_hours_end is not None
        ):
            quiet_hours = (policy_settings.quiet_hours_start, policy_settings.quiet_hours_end)
        current_mode = repository.current_mode()
        mode = policy_settings.mode
        if current_mode and current_mode.get("mode") in {item.value for item in RunMode}:
            mode = RunMode(str(current_mode["mode"]))
        policy = AudioPolicy(
            AudioPolicyConfig(
                mode=mode,
                audio_muted=policy_settings.audio_muted,
                cooldown_seconds=policy_settings.cooldown_seconds,
                hourly_audio_cap=policy_settings.hourly_audio_cap,
                daily_audio_cap=policy_settings.daily_audio_cap,
                quiet_hours=quiet_hours,
                policy_revision=policy_settings.policy_revision,
                message_id=policy_settings.audio_message_id,
                command_ttl_seconds=policy_settings.audio_command_ttl_seconds,
            )
        )
        # Time and routing context are server-controlled.  Never accept a
        # caller timestamp or zone because both can bypass policy limits.
        now = _utc_now()
        active_mutes = [
            mute
            for mute in repository.list_mutes()
            if mute.get("expires_at") is None
            or datetime.fromisoformat(str(mute["expires_at"])) > now
        ]
        site_muted = any(mute.get("scope") == "site" for mute in active_mutes)
        zone_muted = any(
            mute.get("scope") == "zone" and mute.get("scope_id") == zone_id for mute in active_mutes
        )
        camera_muted = any(
            mute.get("scope") == "camera" and mute.get("scope_id") == camera_id
            for mute in active_mutes
        )
        receipts = repository.list_audio_receipts(zone_id=zone_id)
        accepted = [
            item for item in receipts if (item.get("playback") or {}).get("status") == "accepted"
        ]
        announced_at = tuple(datetime.fromisoformat(str(item["created_at"])) for item in accepted)
        hour_start = now.timestamp() - 3600
        hourly_count = sum(
            datetime.fromisoformat(str(item["created_at"])).timestamp() >= hour_start
            for item in accepted
        )
        daily_count = sum(
            datetime.fromisoformat(str(item["created_at"])).date() == now.date()
            for item in accepted
        )
        result = AudioRequestService(policy, app.state.audio_controller, repository).request(
            decision,
            camera_id=camera_id,
            zone_id=zone_id,
            now=now,
            announced_at=announced_at,
            hourly_count=hourly_count,
            daily_count=daily_count,
            site_muted=site_muted,
            zone_muted=zone_muted,
            camera_muted=camera_muted,
            camera_suspended=repository.false_announcement_count(camera_id) > 0,
        )
        return {
            "decision_id": str(request.decision_id),
            "outcome": result.outcome,
            "reason_code": result.reason_code.value,
            "command": result.command.model_dump(mode="json") if result.command else None,
        }

    @app.get("/v1/models", tags=["models"])
    def models(repository: Repo) -> list[dict[str, object]]:
        return repository.list_models()

    return app


app = create_app()

__all__ = ["app", "create_app"]
