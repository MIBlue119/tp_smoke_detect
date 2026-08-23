"""Deterministic, fail-closed policy for neutral audio announcements.

The cascade decides whether an event is smoking; this module decides whether a
verified decision may become an audio command.  It deliberately knows only
catalogue identifiers, never text, paths, or provider SDKs.  All inputs that
depend on time are explicit so replay and unit tests are deterministic.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, time, timedelta
from enum import StrEnum
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from ...contracts import AudioCommand, DecisionCompleted, DecisionOutcome, RunMode
from ..models.decisions import CascadeDecision


class AudioReasonCode(StrEnum):
    """Stable reasons recorded in the immutable decision/audio receipt."""

    MANUAL_MUTE = "manual_mute"
    CAMERA_SUSPENDED = "camera_suspended"
    AUDIO_MUTED = "audio_muted"
    MODE_DISABLED = "mode_disabled"
    DECISION_INELIGIBLE = "decision_ineligible"
    ZONE_COOLDOWN = "zone_cooldown"
    HOURLY_CAP = "hourly_cap"
    DAILY_CAP = "daily_cap"
    QUIET_HOURS = "quiet_hours"
    WOULD_ANNOUNCE = "would_announce"
    ANNOUNCED = "announced"
    ADAPTER_REJECTED = "adapter_rejected"
    ADAPTER_ERROR = "adapter_error"
    UNKNOWN_MESSAGE = "unknown_message"
    EXPIRED = "expired"


DEFAULT_MESSAGE_CATALOG: Mapping[str, str] = {
    "smoke-reminder-neutral-01": "catalogue://smoke-reminder-neutral-01",
}


@dataclass(frozen=True, slots=True)
class AudioPolicyConfig:
    """Runtime policy values.  Safe defaults cannot emit public audio."""

    mode: RunMode = RunMode.SIMULATION
    audio_muted: bool = True
    cooldown_seconds: int = 60
    hourly_audio_cap: int = 10
    daily_audio_cap: int = 100
    quiet_hours: tuple[time, time] | None = None
    policy_revision: str = "default"
    message_id: str = "smoke-reminder-neutral-01"
    volume_profile: str = "default"
    command_ttl_seconds: int = 30
    catalog: Mapping[str, str] = field(default_factory=lambda: DEFAULT_MESSAGE_CATALOG)

    def __post_init__(self) -> None:
        if self.cooldown_seconds < 0 or self.hourly_audio_cap < 0 or self.daily_audio_cap < 0:
            raise ValueError("audio limits must be non-negative")
        if self.command_ttl_seconds <= 0:
            raise ValueError("command_ttl_seconds must be positive")
        if not self.policy_revision or not self.message_id or not self.volume_profile:
            raise ValueError("audio policy identifiers must not be empty")
        if self.message_id not in self.catalog:
            raise ValueError("message_id must be present in the fixed audio catalog")


@dataclass(frozen=True, slots=True)
class AudioPolicyContext:
    """Explicit state consulted by :class:`AudioPolicy`.

    ``announced_at`` is the history for the zone; the policy does not consult
    wall-clock time or a database on its own.  Mute/suspension are separate
    vetoes so their precedence is visible in tests and audit records.
    """

    now: datetime
    camera_id: str
    zone_id: str
    announced_at: tuple[datetime, ...] = ()
    hourly_count: int = 0
    daily_count: int = 0
    site_muted: bool = False
    zone_muted: bool = False
    camera_muted: bool = False
    camera_suspended: bool = False

    def __post_init__(self) -> None:
        if self.now.tzinfo is None:
            raise ValueError("audio policy timestamps must be timezone-aware")
        if self.hourly_count < 0 or self.daily_count < 0:
            raise ValueError("audio counts must be non-negative")


@dataclass(frozen=True, slots=True)
class AudioPolicyResult:
    allowed: bool
    reason_code: AudioReasonCode
    outcome: str
    command: AudioCommand | None = None

    @property
    def audio_outcome(self) -> str:
        return self.outcome


def _decision_is_eligible(
    decision: DecisionCompleted | CascadeDecision | Mapping[str, Any],
) -> bool:
    if isinstance(decision, CascadeDecision):
        return decision.outcome is DecisionOutcome.VERIFIED and decision.audio_eligibility
    if isinstance(decision, DecisionCompleted):
        return decision.outcome is DecisionOutcome.VERIFIED and decision.audio_eligibility
    outcome = decision.get("outcome")
    eligible = decision.get("audio_eligibility") is True
    return eligible and (
        outcome == DecisionOutcome.VERIFIED or outcome == DecisionOutcome.VERIFIED.value
    )


def _decision_id(decision: DecisionCompleted | CascadeDecision | Mapping[str, Any]) -> UUID:
    if isinstance(decision, CascadeDecision):
        return uuid4()
    value = (
        decision.decision_id
        if isinstance(decision, DecisionCompleted)
        else decision.get("decision_id")
    )
    if value is None:
        raise ValueError("audio decisions require decision_id")
    return value if isinstance(value, UUID) else UUID(str(value))


class AudioPolicy:
    """Apply suppression precedence and create a bounded catalogue command."""

    def __init__(self, config: AudioPolicyConfig | None = None) -> None:
        self.config = config or AudioPolicyConfig()

    def evaluate(
        self,
        decision: DecisionCompleted | CascadeDecision | Mapping[str, Any],
        context: AudioPolicyContext,
    ) -> AudioPolicyResult:
        # Safety vetoes intentionally precede evidence and operating mode.  An
        # operator's mute or a false-announcement suspension must win even if a
        # malformed caller claims an otherwise eligible decision.
        if context.site_muted or context.zone_muted or context.camera_muted:
            return AudioPolicyResult(False, AudioReasonCode.MANUAL_MUTE, "suppressed")
        if context.camera_suspended:
            return AudioPolicyResult(False, AudioReasonCode.CAMERA_SUSPENDED, "suppressed")
        if self.config.audio_muted:
            return AudioPolicyResult(False, AudioReasonCode.AUDIO_MUTED, "suppressed")
        if not _decision_is_eligible(decision):
            return AudioPolicyResult(False, AudioReasonCode.DECISION_INELIGIBLE, "suppressed")

        mode = self.config.mode
        if mode is RunMode.SHADOW:
            return AudioPolicyResult(False, AudioReasonCode.WOULD_ANNOUNCE, "would_announce")
        if mode is not RunMode.AUTOMATIC:
            return AudioPolicyResult(False, AudioReasonCode.MODE_DISABLED, "suppressed")

        now = context.now.astimezone(UTC)
        if self._in_quiet_hours(now.timetz().replace(tzinfo=None)):
            return AudioPolicyResult(False, AudioReasonCode.QUIET_HOURS, "suppressed")
        if context.hourly_count >= self.config.hourly_audio_cap:
            return AudioPolicyResult(False, AudioReasonCode.HOURLY_CAP, "suppressed")
        if context.daily_count >= self.config.daily_audio_cap:
            return AudioPolicyResult(False, AudioReasonCode.DAILY_CAP, "suppressed")
        if self._zone_on_cooldown(context):
            return AudioPolicyResult(False, AudioReasonCode.ZONE_COOLDOWN, "suppressed")

        command_id = uuid5(NAMESPACE_URL, f"tp-smoke-detect/audio/{_decision_id(decision)}")
        command = AudioCommand(
            event_id=uuid4(),
            correlation_id=_decision_id(decision),
            producer="tp-smoke-detect.audio-policy",
            occurred_at=now,
            command_id=command_id,
            decision_id=_decision_id(decision),
            zone_id=context.zone_id,
            message_id=self.config.message_id,
            volume_profile=self.config.volume_profile,
            expires_at=now + timedelta(seconds=self.config.command_ttl_seconds),
            policy_revision=self.config.policy_revision,
        )
        return AudioPolicyResult(True, AudioReasonCode.ANNOUNCED, "announce_requested", command)

    def _zone_on_cooldown(self, context: AudioPolicyContext) -> bool:
        if self.config.cooldown_seconds == 0:
            return False
        cutoff = context.now - timedelta(seconds=self.config.cooldown_seconds)
        return any(item >= cutoff for item in context.announced_at)

    def _in_quiet_hours(self, current: time) -> bool:
        if self.config.quiet_hours is None:
            return False
        start, end = self.config.quiet_hours
        if start == end:
            return True
        if start < end:
            return start <= current < end
        return current >= start or current < end


__all__ = [
    "AudioPolicy",
    "AudioPolicyConfig",
    "AudioPolicyContext",
    "AudioPolicyResult",
    "AudioReasonCode",
    "DEFAULT_MESSAGE_CATALOG",
]
