"""Explicit component and per-camera readiness state.

Health is deliberately independent per camera.  A stalled camera cannot turn
the whole capture fleet unready, while a database or broker failure can still
degrade the service as a whole.
"""

from __future__ import annotations

import threading
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from .metrics import OperationalMetrics


class HealthState(StrEnum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNAVAILABLE = "unavailable"
    UNKNOWN = "unknown"


@dataclass
class ComponentHealth:
    name: str
    state: HealthState = HealthState.UNKNOWN
    last_success_at: datetime | None = None
    message: str | None = None
    details: dict[str, str] = field(default_factory=dict)


@dataclass
class CameraHealth:
    camera_id: str
    state: HealthState = HealthState.UNKNOWN
    last_good_frame_at: datetime | None = None
    reason: str | None = None


class HealthRegistry:
    """In-memory readiness snapshot safe for concurrent worker updates."""

    def __init__(self, metrics: OperationalMetrics | None = None) -> None:
        self.metrics = metrics
        self._components: dict[str, ComponentHealth] = {}
        self._cameras: dict[str, CameraHealth] = {}
        self._queue_depth: dict[str, int] = {}
        self._last_successful_activity: datetime | None = None
        self._lock = threading.RLock()

    def set_component(
        self,
        name: str,
        state: HealthState,
        *,
        last_success_at: datetime | None = None,
        message: str | None = None,
        details: dict[str, str] | None = None,
    ) -> ComponentHealth:
        if not name:
            raise ValueError("component name is required")
        with self._lock:
            previous = self._components.get(name)
            value = ComponentHealth(name, state, last_success_at, message, details or {})
            self._components[name] = value
            if (
                self.metrics
                and state is HealthState.DEGRADED
                and (previous is None or previous.state is not HealthState.DEGRADED)
            ):
                self.metrics.degraded(name, state.value)
            return value

    def mark_component(
        self, name: str, *, healthy: bool, message: str | None = None
    ) -> ComponentHealth:
        now = datetime.now(UTC) if healthy else None
        return self.set_component(
            name,
            HealthState.HEALTHY if healthy else HealthState.DEGRADED,
            last_success_at=now,
            message=message,
        )

    def set_camera(
        self,
        camera_id: str,
        state: HealthState,
        *,
        last_good_frame_at: datetime | None = None,
        reason: str | None = None,
    ) -> CameraHealth:
        if not camera_id:
            raise ValueError("camera_id is required")
        with self._lock:
            previous = self._cameras.get(camera_id)
            value = CameraHealth(camera_id, state, last_good_frame_at, reason)
            self._cameras[camera_id] = value
            if self.metrics:
                self.metrics.camera_state(
                    camera_id,
                    state.value,
                    frame_timestamp_seconds=(
                        last_good_frame_at.timestamp() if last_good_frame_at else None
                    ),
                )
                if state in {HealthState.DEGRADED, HealthState.UNAVAILABLE} and (
                    previous is None or previous.state is not state
                ):
                    self.metrics.degraded("camera", state.value)
            return value

    def record_frame(self, camera_id: str, at: datetime | None = None) -> CameraHealth:
        timestamp = at or datetime.now(UTC)
        with self._lock:
            self._last_successful_activity = timestamp
        return self.set_camera(camera_id, HealthState.HEALTHY, last_good_frame_at=timestamp)

    def set_queue_depth(self, camera_id: str, depth: int) -> None:
        if depth < 0:
            raise ValueError("queue depth cannot be negative")
        with self._lock:
            self._queue_depth[camera_id] = depth
            if self.metrics:
                self.metrics.queue(camera_id, depth)
                self.metrics.camera_state(
                    camera_id,
                    self._cameras.get(camera_id, CameraHealth(camera_id)).state.value,
                    queue_depth=depth,
                )

    def mark_camera_degraded(self, camera_id: str, reason: str) -> CameraHealth:
        current = self._cameras.get(camera_id)
        return self.set_camera(
            camera_id,
            HealthState.DEGRADED,
            last_good_frame_at=current.last_good_frame_at if current else None,
            reason=reason,
        )

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            components = {
                name: self._serialize(value) for name, value in sorted(self._components.items())
            }
            cameras = {
                name: self._serialize(value) for name, value in sorted(self._cameras.items())
            }
            component_bad = any(
                item.state in {HealthState.DEGRADED, HealthState.UNAVAILABLE}
                for item in self._components.values()
            )
            # Per-camera degradation is reported but does not make unrelated
            # cameras or the database readiness fail.
            return {
                "status": "degraded" if component_bad else "ready",
                "components": components,
                "cameras": cameras,
                "affected_camera_count": sum(
                    item.state in {HealthState.DEGRADED, HealthState.UNAVAILABLE}
                    for item in self._cameras.values()
                ),
                "queue_depth": dict(sorted(self._queue_depth.items())),
                "last_successful_activity": (
                    self._last_successful_activity.astimezone(UTC).isoformat()
                    if self._last_successful_activity
                    else None
                ),
            }

    @staticmethod
    def _serialize(value: ComponentHealth | CameraHealth) -> dict[str, Any]:
        result = asdict(value)
        for key, item in list(result.items()):
            if isinstance(item, datetime):
                result[key] = item.astimezone(UTC).isoformat()
            elif isinstance(item, HealthState):
                result[key] = item.value
        return result


__all__ = ["CameraHealth", "ComponentHealth", "HealthRegistry", "HealthState"]
