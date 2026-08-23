"""Application orchestration for policy-gated audio requests."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from ..contracts import DecisionCompleted
from ..domain.policy.audio import (
    AudioPolicy,
    AudioPolicyContext,
    AudioPolicyResult,
)
from ..ports.audio import AudioController, AudioPlaybackReceipt, PlaybackStatus


class AudioRequestService:
    """One policy choke point between completed decisions and audio adapters."""

    def __init__(
        self,
        policy: AudioPolicy,
        controller: AudioController,
        repository: Any | None = None,
    ) -> None:
        self.policy = policy
        self.controller = controller
        self.repository = repository

    def request(
        self,
        decision: DecisionCompleted | Mapping[str, Any],
        *,
        camera_id: str,
        zone_id: str,
        now: datetime,
        announced_at: tuple[datetime, ...] = (),
        hourly_count: int = 0,
        daily_count: int = 0,
        site_muted: bool = False,
        zone_muted: bool = False,
        camera_muted: bool = False,
        camera_suspended: bool = False,
    ) -> AudioPolicyResult:
        context = AudioPolicyContext(
            now=now,
            camera_id=camera_id,
            zone_id=zone_id,
            announced_at=announced_at,
            hourly_count=hourly_count,
            daily_count=daily_count,
            site_muted=site_muted,
            zone_muted=zone_muted,
            camera_muted=camera_muted,
            camera_suspended=camera_suspended,
        )
        result = self.policy.evaluate(decision, context)
        receipt: AudioPlaybackReceipt | None = None
        if result.command is not None:
            receipt = self.controller.send(result.command)
            if receipt.status in {PlaybackStatus.REJECTED, PlaybackStatus.EXPIRED}:
                result = AudioPolicyResult(
                    False,
                    # Adapter detail codes are bounded by the port and kept as
                    # a separate playback outcome for auditability.
                    result.reason_code,
                    "suppressed",
                    result.command,
                )
        self._record(decision, camera_id, zone_id, result, receipt, now)
        return result

    def _record(
        self,
        decision: DecisionCompleted | Mapping[str, Any],
        camera_id: str,
        zone_id: str,
        result: AudioPolicyResult,
        receipt: AudioPlaybackReceipt | None,
        now: datetime,
    ) -> None:
        if self.repository is None:
            return
        decision_id = str(
            decision.decision_id
            if isinstance(decision, DecisionCompleted)
            else decision["decision_id"]
        )
        payload: dict[str, object] = {
            "receipt_id": f"audio:{decision_id}",
            "decision_id": decision_id,
            "camera_id": camera_id,
            "zone_id": zone_id,
            "outcome": result.outcome,
            "reason_code": result.reason_code.value,
            "command_id": str(result.command.command_id) if result.command else None,
            "playback": receipt.model_dump(mode="json") if receipt else None,
            "created_at": now.astimezone(UTC).isoformat(),
        }
        put_receipt = getattr(self.repository, "put_audio_receipt", None)
        if put_receipt is not None:
            put_receipt(payload)


__all__ = ["AudioRequestService"]
