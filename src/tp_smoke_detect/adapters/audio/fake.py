"""Deterministic audio adapter used by the CPU profile and contract tests."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from ...contracts import AudioCommand
from ...domain.policy.audio import DEFAULT_MESSAGE_CATALOG
from ...ports.audio import AudioPlaybackReceipt, PlaybackStatus


class FakeAudioController:
    """In-memory, idempotent controller; no speaker or media access."""

    def __init__(
        self, *, catalog: frozenset[str] | None = None, now: datetime | None = None
    ) -> None:
        self.catalog = catalog or frozenset(DEFAULT_MESSAGE_CATALOG)
        self.now = now
        self.commands: list[AudioCommand] = []
        self.receipts: dict[UUID, AudioPlaybackReceipt] = {}

    def send(self, command: AudioCommand) -> AudioPlaybackReceipt:
        now = self.now or datetime.now(UTC)
        if command.message_id not in self.catalog:
            return AudioPlaybackReceipt(
                command_id=str(command.command_id),
                decision_id=str(command.decision_id),
                status=PlaybackStatus.REJECTED,
                accepted_at=now,
                detail_code="unknown_message",
            )
        if now >= command.expires_at:
            return AudioPlaybackReceipt(
                command_id=str(command.command_id),
                decision_id=str(command.decision_id),
                status=PlaybackStatus.EXPIRED,
                accepted_at=now,
                detail_code="expired",
            )
        existing = self.receipts.get(command.command_id)
        if existing is not None:
            return existing.model_copy(update={"status": PlaybackStatus.DUPLICATE})
        receipt = AudioPlaybackReceipt(
            command_id=str(command.command_id),
            decision_id=str(command.decision_id),
            status=PlaybackStatus.ACCEPTED,
            accepted_at=now,
        )
        self.receipts[command.command_id] = receipt
        self.commands.append(command)
        return receipt


__all__ = ["FakeAudioController"]
