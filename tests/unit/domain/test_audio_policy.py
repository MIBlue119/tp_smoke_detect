from datetime import UTC, datetime, time, timedelta
from typing import Any, cast
from uuid import uuid4

import pytest

from tp_smoke_detect.contracts import DecisionCompleted, DecisionOutcome, RunMode
from tp_smoke_detect.domain.policy.audio import (
    AudioPolicy,
    AudioPolicyConfig,
    AudioPolicyContext,
    AudioReasonCode,
)


def _decision(*, eligible: bool = True) -> DecisionCompleted:
    return DecisionCompleted(
        decision_id=uuid4(),
        camera_id="cam-1",
        track_id="track-1",
        outcome=DecisionOutcome.VERIFIED if eligible else DecisionOutcome.REJECTED,
        reason_codes=["verified" if eligible else "rejected"],
        policy_revision="p1",
        latency_ms=1,
        mode=RunMode.AUTOMATIC,
        audio_eligibility=eligible,
    )


def _context(now: datetime, **kwargs: object) -> AudioPolicyContext:
    return AudioPolicyContext(now=now, camera_id="cam-1", zone_id="zone-a", **cast(Any, kwargs))


def _policy(**kwargs: object) -> AudioPolicy:
    return AudioPolicy(
        AudioPolicyConfig(mode=RunMode.AUTOMATIC, audio_muted=False, **cast(Any, kwargs))
    )


def test_eligible_automatic_decision_gets_catalogue_command_and_stable_id() -> None:
    now = datetime(2026, 8, 24, 1, 0, tzinfo=UTC)
    decision = _decision()
    result = _policy().evaluate(decision, _context(now))
    again = _policy().evaluate(decision, _context(now))
    assert result.allowed is True
    assert result.reason_code is AudioReasonCode.ANNOUNCED
    assert result.command is not None
    assert result.command.message_id == "smoke-reminder-neutral-01"
    assert again.command is not None
    assert result.command.command_id == again.command.command_id


@pytest.mark.parametrize(
    ("kwargs", "reason"),
    [
        ({"site_muted": True}, AudioReasonCode.MANUAL_MUTE),
        ({"zone_muted": True}, AudioReasonCode.MANUAL_MUTE),
        ({"camera_muted": True}, AudioReasonCode.MANUAL_MUTE),
        ({"camera_suspended": True}, AudioReasonCode.CAMERA_SUSPENDED),
        ({"hourly_count": 1}, AudioReasonCode.HOURLY_CAP),
        ({"daily_count": 1}, AudioReasonCode.DAILY_CAP),
    ],
)
def test_suppression_paths_never_create_a_command(
    kwargs: dict[str, object], reason: AudioReasonCode
) -> None:
    now = datetime(2026, 8, 24, 1, 0, tzinfo=UTC)
    policy = _policy(hourly_audio_cap=1, daily_audio_cap=1)
    result = policy.evaluate(_decision(), _context(now, **kwargs))
    assert result.allowed is False
    assert result.reason_code is reason
    assert result.command is None


def test_manual_mute_and_suspension_precede_ineligible_evidence_and_audio_muting() -> None:
    now = datetime(2026, 8, 24, 1, 0, tzinfo=UTC)
    policy = AudioPolicy(AudioPolicyConfig(mode=RunMode.AUTOMATIC, audio_muted=True))
    result = policy.evaluate(
        _decision(eligible=False), _context(now, site_muted=True, camera_suspended=True)
    )
    assert result.reason_code is AudioReasonCode.MANUAL_MUTE


def test_shadow_records_would_announce_without_command() -> None:
    now = datetime(2026, 8, 24, 1, 0, tzinfo=UTC)
    policy = AudioPolicy(AudioPolicyConfig(mode=RunMode.SHADOW, audio_muted=False))
    result = policy.evaluate(_decision(), _context(now))
    assert result.outcome == "would_announce"
    assert result.reason_code is AudioReasonCode.WOULD_ANNOUNCE
    assert result.command is None


def test_cooldown_is_zone_local_and_quiet_hours_support_overnight_window() -> None:
    now = datetime(2026, 8, 24, 23, 30, tzinfo=UTC)
    policy = _policy(cooldown_seconds=600, quiet_hours=(time(22), time(6)))
    quiet = policy.evaluate(_decision(), _context(now))
    assert quiet.reason_code is AudioReasonCode.QUIET_HOURS

    policy = _policy(cooldown_seconds=600)
    recent = policy.evaluate(
        _decision(),
        _context(now, announced_at=(now - timedelta(seconds=1),)),
    )
    other_zone = policy.evaluate(
        _decision(),
        AudioPolicyContext(now=now, camera_id="cam-1", zone_id="zone-b"),
    )
    assert recent.reason_code is AudioReasonCode.ZONE_COOLDOWN
    assert other_zone.reason_code is AudioReasonCode.ANNOUNCED


def test_caps_are_checked_before_command_creation() -> None:
    now = datetime(2026, 8, 24, 1, 0, tzinfo=UTC)
    result = _policy(hourly_audio_cap=0).evaluate(_decision(), _context(now))
    assert result.reason_code is AudioReasonCode.HOURLY_CAP
    assert result.command is None
