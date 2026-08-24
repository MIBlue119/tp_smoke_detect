"""Live, pixels-free candidate processing for the GPU profile.

The native media worker owns decode, crops, and all GPU-local inference.  This
module accepts only the typed ``track.candidate.v1`` envelope, turns it into a
domain observation, and is the sole owner of decision persistence and the
audio-policy boundary.  A malformed or incomplete receipt is deliberately
converted into a rejected decision; it is never treated as missing positive
evidence.
"""

from __future__ import annotations

import hashlib
import json
import threading
import time
from collections.abc import Callable, Mapping
from contextlib import suppress
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Any, Literal
from uuid import NAMESPACE_URL, uuid5

from pydantic import ValidationError

from ..contracts import (
    CandidateEnvelope,
    CandidateInferenceRole,
    CandidateInferenceStatus,
    DecisionCompleted,
    DecisionOutcome,
)
from ..domain.cascade.engine import CascadeEngine, CascadePolicy
from ..domain.models.decisions import CascadeDecision
from ..domain.models.observations import DomainObservation
from ..observability.health import HealthRegistry, HealthState
from ..observability.metrics import OperationalMetrics
from ..ports.repositories import AuditRepository
from .request_audio import AudioRequestService

ProcessingStatus = Literal["completed", "duplicate", "rejected"]


class CandidateMessageError(ValueError):
    """A poison candidate that must be quarantined, not retried forever."""


class CandidateProcessingError(RuntimeError):
    """A transient dependency failure; the broker delivery should be retried."""


@dataclass(frozen=True, slots=True)
class CandidateProcessingResult:
    """Durable outcome of one candidate delivery."""

    status: ProcessingStatus
    event_key: str
    decision: DecisionCompleted | None = None
    audio_outcome: str | None = None
    reason: str | None = None


def _utc_now() -> datetime:
    return datetime.now(UTC)


class CandidateProcessingService:
    """Validate, deduplicate, aggregate, persist, and policy-gate candidates.

    The lock intentionally covers the short cascade/persistence decision path
    for a camera/track.  This gives a single ordered state per track even when
    an MQTT client dispatches duplicate deliveries concurrently.  External
    audio I/O is also serialized per event, while unrelated camera tracks can
    continue in separate consumer workers.
    """

    def __init__(
        self,
        repository: AuditRepository,
        audio_service: AudioRequestService,
        *,
        cascade_policy: CascadePolicy | None = None,
        zone_by_camera: Mapping[str, str] | None = None,
        expected_revisions: Mapping[CandidateInferenceRole | str, str] | None = None,
        require_gpu_receipts: bool = True,
        clock: Callable[[], datetime] = _utc_now,
        metrics: OperationalMetrics | None = None,
        health: HealthRegistry | None = None,
        max_tracks: int = 4096,
    ) -> None:
        if max_tracks < 1:
            raise ValueError("max_tracks must be positive")
        self.repository = repository
        self.audio_service = audio_service
        self.cascade_policy = cascade_policy or CascadePolicy()
        self.zone_by_camera = dict(zone_by_camera or {})
        self.expected_revisions = {
            CandidateInferenceRole(str(role)): revision
            for role, revision in (expected_revisions or {}).items()
        }
        self.require_gpu_receipts = require_gpu_receipts
        self.clock = clock
        self.metrics = metrics
        self.health = health
        self.max_tracks = max_tracks
        self._lock = threading.RLock()
        self._cascades: dict[tuple[str, str], CascadeEngine] = {}
        self._completed: set[str] = set()
        if self.health is not None:
            self.health.set_component("candidate-processing", HealthState.HEALTHY)
        if self.metrics is not None:
            self.metrics.candidate_ready(True)

    @property
    def active_track_count(self) -> int:
        with self._lock:
            return len(self._cascades)

    def readiness(self) -> bool:
        """Return core readiness without probing the broker or model runtime."""

        try:
            ready = self.repository.health()
        except Exception:
            ready = False
        if self.metrics is not None:
            self.metrics.candidate_ready(ready)
        if self.health is not None:
            self.health.set_component(
                "candidate-processing",
                HealthState.HEALTHY if ready else HealthState.DEGRADED,
                message=None if ready else "repository health check failed",
            )
        return ready

    def process_payload(
        self, payload: bytes | str | Mapping[str, Any] | CandidateEnvelope
    ) -> CandidateProcessingResult:
        """Process one MQTT payload, raising only for transient dependencies."""

        started = time.monotonic()
        try:
            candidate = self._parse_payload(payload)
        except (CandidateMessageError, ValidationError) as exc:
            self._record_terminal("rejected", started)
            raise CandidateMessageError(str(exc)) from exc

        event_key = self._event_key(candidate)
        with self._lock:
            if event_key in self._completed:
                self._record_terminal("duplicate", started)
                return CandidateProcessingResult("duplicate", event_key, reason="already_completed")

            try:
                self._validate_metadata(candidate)
                evidence_ok, evidence_reason = self._validate_gpu_evidence(candidate)
                track_key = (candidate.camera_id, candidate.track_id)
                cascade = self._cascades.get(track_key)
                if cascade is None:
                    if len(self._cascades) >= self.max_tracks:
                        # Evicting state could join two unrelated track
                        # histories.  Fail closed and let the producer retry
                        # after the configured track lifecycle has drained.
                        raise CandidateProcessingError("candidate track state capacity reached")
                    cascade = CascadeEngine(
                        candidate.camera_id, candidate.track_id, self.cascade_policy
                    )
                    self._cascades[track_key] = cascade

                observation = candidate.to_domain_observation()
                if not evidence_ok:
                    observation = self._fail_closed_observation(observation)
                cascade_decision = cascade.ingest(observation)
                decision = self._decision(candidate, cascade_decision, evidence_ok)

                existing = self.repository.get_decision(str(decision.decision_id))
                if existing is not None:
                    decision = DecisionCompleted.model_validate(existing)
                else:
                    try:
                        self.repository.put_decision(decision.model_dump(mode="json"))
                    except Exception as exc:
                        # A concurrent process may have won the deterministic
                        # decision ID.  Read it before classifying the error as
                        # transient; other database errors must be retried.
                        existing = self.repository.get_decision(str(decision.decision_id))
                        if existing is None:
                            raise CandidateProcessingError("decision persistence failed") from exc
                        decision = DecisionCompleted.model_validate(existing)

                audio_outcome: str | None = None
                if decision.outcome is DecisionOutcome.VERIFIED and decision.audio_eligibility:
                    audio_outcome = self._request_audio(candidate, decision)
                self._completed.add(event_key)
                if self.metrics is not None:
                    with suppress(Exception):
                        self.metrics.decision(
                            decision.outcome.value,
                            decision.reason_codes[0] if decision.reason_codes else "other",
                        )
                self._record_terminal("completed", started)
                return CandidateProcessingResult(
                    "completed",
                    event_key,
                    decision,
                    audio_outcome,
                    evidence_reason,
                )
            except CandidateProcessingError:
                self._record_terminal("retry", started)
                raise
            except Exception as exc:
                self._record_terminal("retry", started)
                raise CandidateProcessingError("candidate processing dependency failed") from exc

    def _parse_payload(
        self, payload: bytes | str | Mapping[str, Any] | CandidateEnvelope
    ) -> CandidateEnvelope:
        if isinstance(payload, CandidateEnvelope):
            return payload
        value: Any = payload
        if isinstance(payload, (bytes, str)):
            try:
                value = json.loads(payload)
            except json.JSONDecodeError as exc:
                raise CandidateMessageError("candidate payload is not valid JSON") from exc
        if not isinstance(value, Mapping):
            raise CandidateMessageError("candidate payload must be a JSON object")
        try:
            return CandidateEnvelope.model_validate(value)
        except ValidationError:
            raise

    @staticmethod
    def _event_key(candidate: CandidateEnvelope) -> str:
        if candidate.event_id is not None:
            return str(candidate.event_id)
        canonical = json.dumps(
            candidate.model_dump(mode="json"), sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        return hashlib.sha256(canonical).hexdigest()

    @staticmethod
    def _validate_metadata(candidate: CandidateEnvelope) -> None:
        if candidate.received_ts_ns < candidate.capture_ts_ns:
            raise CandidateMessageError("candidate received timestamp precedes capture timestamp")
        if candidate.last_seen_at < candidate.first_seen_at:
            raise CandidateMessageError("candidate last_seen_at precedes first_seen_at")

    def _validate_gpu_evidence(self, candidate: CandidateEnvelope) -> tuple[bool, str | None]:
        receipts = {item.role: item for item in candidate.inference_receipts}
        if not self.require_gpu_receipts and not receipts:
            return True, None
        if candidate.correlation_id is None and any(
            item.status is CandidateInferenceStatus.OK for item in receipts.values()
        ):
            return False, "missing_candidate_correlation_id"
        for receipt in receipts.values():
            if receipt.status is CandidateInferenceStatus.OK:
                if receipt.correlation_id != candidate.correlation_id:
                    return False, "receipt_correlation_mismatch"
                expected = self.expected_revisions.get(receipt.role)
                if expected is not None and receipt.model_revision != expected:
                    return False, "receipt_revision_mismatch"

        positive_roles: set[CandidateInferenceRole] = {
            CandidateInferenceRole.DETECTOR,
        }
        if candidate.observations.pose is not None:
            positive_roles.add(CandidateInferenceRole.POSE)
        if any(
            item.label not in {"background", "unknown"} for item in candidate.observations.objects
        ):
            positive_roles.add(CandidateInferenceRole.OBJECT)
        if candidate.observations.smoke is not None:
            positive_roles.add(CandidateInferenceRole.SMOKE)
        if candidate.observations.temporal is not None:
            positive_roles.add(CandidateInferenceRole.TEMPORAL)
        if candidate.observations.reviewer is not None:
            positive_roles.add(CandidateInferenceRole.REVIEWER)
        if self.require_gpu_receipts:
            for role in positive_roles:
                required_receipt = receipts.get(role)
                if (
                    required_receipt is None
                    or required_receipt.status is not CandidateInferenceStatus.OK
                ):
                    return False, f"missing_or_failed_{role.value}_receipt"
        for role, revision in candidate.model_revisions.items():
            mapped_receipt = receipts.get(role)
            if (
                mapped_receipt is not None
                and mapped_receipt.status is CandidateInferenceStatus.OK
                and mapped_receipt.model_revision != revision
            ):
                return False, "candidate_revision_mismatch"
        return True, None

    @staticmethod
    def _fail_closed_observation(observation: DomainObservation) -> DomainObservation:
        return replace(
            observation,
            quality_eligible=False,
            pose_eligible=False,
            hand_to_mouth=False,
            object_label=None,
            object_score=0.0,
            smoke_score=0.0,
            ember_score=0.0,
            persistence_ms=0,
            positive_channels=frozenset(),
            vlm=None,
        )

    def _decision(
        self, candidate: CandidateEnvelope, result: CascadeDecision, evidence_ok: bool
    ) -> DecisionCompleted:
        event_key = self._event_key(candidate)
        decision_id = uuid5(NAMESPACE_URL, f"tp-smoke-detect/decision/{event_key}")
        policy_revision = self.audio_service.policy.config.policy_revision
        mode = self.audio_service.policy.config.mode
        audio_eligible = bool(evidence_ok and result.audio_eligibility)
        return DecisionCompleted(
            event_id=candidate.event_id,
            correlation_id=candidate.correlation_id,
            producer="tp-smoke-detect.candidate-processing",
            occurred_at=candidate.occurred_at or candidate.last_seen_at,
            decision_id=decision_id,
            camera_id=candidate.camera_id,
            track_id=candidate.track_id,
            outcome=result.outcome,
            reason_codes=[item.value for item in result.reason_codes],
            evidence_channels=[item.to_contract() for item in result.evidence_channels],
            model_revisions=dict(result.model_revisions),
            policy_revision=policy_revision,
            latency_ms=max(0.0, (candidate.received_ts_ns - candidate.capture_ts_ns) / 1_000_000),
            mode=mode,
            audio_eligibility=audio_eligible,
        )

    def _request_audio(self, candidate: CandidateEnvelope, decision: DecisionCompleted) -> str:
        zone_id = self.zone_by_camera.get(
            candidate.camera_id, candidate.geometry.roi_id or "default"
        )
        try:
            result = self.audio_service.request(
                decision,
                camera_id=candidate.camera_id,
                zone_id=zone_id,
                now=self.clock().astimezone(UTC),
            )
        except Exception as exc:
            raise CandidateProcessingError("audio policy persistence failed") from exc
        return result.outcome

    def _record_terminal(self, status: str, started: float) -> None:
        elapsed = max(0.0, time.monotonic() - started)
        if self.metrics is not None:
            self.metrics.candidate_processing(status, elapsed)
            if status in {"completed", "duplicate", "rejected"}:
                self.metrics.candidate_message(status)


def build_candidate_processing_service(
    repository: AuditRepository,
    *,
    settings: Any,
    audio_controller: Any,
    metrics: OperationalMetrics | None = None,
    health: HealthRegistry | None = None,
) -> CandidateProcessingService:
    """Bootstrap the core service from validated ``AppSettings``-like data."""

    from ..domain.policy.audio import AudioPolicy, AudioPolicyConfig

    policy = settings.policy
    quiet_hours = None
    if policy.quiet_hours_start is not None and policy.quiet_hours_end is not None:
        quiet_hours = (policy.quiet_hours_start, policy.quiet_hours_end)
    audio = AudioRequestService(
        AudioPolicy(
            AudioPolicyConfig(
                mode=policy.mode,
                audio_muted=policy.audio_muted,
                cooldown_seconds=policy.cooldown_seconds,
                hourly_audio_cap=policy.hourly_audio_cap,
                daily_audio_cap=policy.daily_audio_cap,
                quiet_hours=quiet_hours,
                policy_revision=policy.policy_revision,
                message_id=policy.audio_message_id,
                command_ttl_seconds=policy.audio_command_ttl_seconds,
            )
        ),
        audio_controller,
        repository,
    )
    zones = {profile.camera_id: profile.zone_id for profile in settings.cameras}
    candidate = settings.candidate
    return CandidateProcessingService(
        repository,
        audio,
        zone_by_camera=zones,
        require_gpu_receipts=candidate.require_gpu_receipts,
        metrics=metrics,
        health=health,
    )


__all__ = [
    "CandidateMessageError",
    "CandidateProcessingError",
    "CandidateProcessingResult",
    "CandidateProcessingService",
    "build_candidate_processing_service",
]
