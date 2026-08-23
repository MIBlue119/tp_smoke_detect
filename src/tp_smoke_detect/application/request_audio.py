"""Application orchestration for policy-gated audio requests."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any, cast

from ..contracts import DecisionCompleted
from ..domain.policy.audio import (
    AudioPolicy,
    AudioPolicyContext,
    AudioPolicyResult,
    AudioReasonCode,
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
        reservation_token: str | None = None
        command = result.command
        if command is not None:
            if getattr(self.repository, "reserve_audio_receipt", None) is not None:
                reservation = self._record(
                    decision,
                    camera_id,
                    zone_id,
                    result,
                    None,
                    now,
                    playback_override={"status": "pending"},
                )
                if reservation and reservation.get("reservation_status") == "rejected":
                    result = AudioPolicyResult(
                        False,
                        AudioReasonCode(str(reservation["reservation_reason"])),
                        "suppressed",
                    )
                    self._record(decision, camera_id, zone_id, result, None, now)
                    return result
                if reservation and reservation.get("reservation_status") == "existing":
                    # The decision already has an accepted playback fact.
                    # Retain this retry as an audit attempt without issuing a
                    # second controller command.
                    result = AudioPolicyResult(
                        True, AudioReasonCode.ANNOUNCED, "announce_requested", command
                    )
                    self._record(
                        decision,
                        camera_id,
                        zone_id,
                        result,
                        None,
                        now,
                        playback_override={"status": "duplicate"},
                    )
                    return result
                pending_playback = reservation.get("playback") if reservation else None
                if isinstance(pending_playback, Mapping):
                    token = pending_playback.get("reservation_token")
                    if token is not None:
                        reservation_token = str(token)
            try:
                receipt = self.controller.send(command)
            except Exception:
                # Never strand a lease when an adapter raises before returning
                # a typed receipt.  Persist the terminal failure before
                # re-raising so the caller can retry after the bounded TTL.
                if reservation_token is not None:
                    self._record(
                        decision,
                        camera_id,
                        zone_id,
                        AudioPolicyResult(
                            False, AudioReasonCode.ADAPTER_ERROR, "suppressed", result.command
                        ),
                        None,
                        now,
                        playback_override={
                            "status": "failed",
                            "detail_code": "adapter_error",
                        },
                        reservation_token=reservation_token,
                    )
                raise
            if receipt.status in {PlaybackStatus.REJECTED, PlaybackStatus.EXPIRED}:
                result = AudioPolicyResult(
                    False,
                    # Adapter detail codes are bounded by the port and kept as
                    # a separate playback outcome for auditability.
                    result.reason_code,
                    "suppressed",
                    result.command,
                )
        self._record(
            decision,
            camera_id,
            zone_id,
            result,
            receipt,
            now,
            reservation_token=reservation_token,
        )
        return result

    def _record(
        self,
        decision: DecisionCompleted | Mapping[str, Any],
        camera_id: str,
        zone_id: str,
        result: AudioPolicyResult,
        receipt: AudioPlaybackReceipt | None,
        now: datetime,
        *,
        playback_override: dict[str, object] | None = None,
        reservation_token: str | None = None,
    ) -> dict[str, Any] | None:
        if self.repository is None:
            return None
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
            "playback": (receipt.model_dump(mode="json") if receipt else playback_override),
            "created_at": now.astimezone(UTC).isoformat(),
        }
        method_name = (
            "reserve_audio_receipt" if playback_override is not None else "put_audio_receipt"
        )
        if playback_override is not None and playback_override.get("status") == "duplicate":
            method_name = "put_audio_receipt"
        if reservation_token is not None:
            finalize = getattr(self.repository, "finalize_audio_receipt", None)
            if finalize is not None:
                return cast(
                    dict[str, Any], finalize(payload["receipt_id"], reservation_token, payload)
                )
        put_receipt = getattr(self.repository, method_name, None)
        if put_receipt is not None:
            if playback_override is not None and method_name == "reserve_audio_receipt":
                return cast(
                    dict[str, Any],
                    put_receipt(
                        payload,
                        cooldown_seconds=self.policy.config.cooldown_seconds,
                        hourly_audio_cap=self.policy.config.hourly_audio_cap,
                        daily_audio_cap=self.policy.config.daily_audio_cap,
                        reservation_ttl_seconds=self.policy.config.command_ttl_seconds,
                    ),
                )
            put_receipt(payload)
        return None


__all__ = ["AudioRequestService"]
