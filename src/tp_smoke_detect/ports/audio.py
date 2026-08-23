"""Typed boundary for the independent local audio worker."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field

from ..contracts import AudioCommand


class PlaybackStatus(StrEnum):
    ACCEPTED = "accepted"
    DUPLICATE = "duplicate"
    REJECTED = "rejected"
    EXPIRED = "expired"
    FAILED = "failed"


class AudioPlaybackReceipt(BaseModel):
    """Structured adapter result; never contains provider prose or raw media."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    command_id: str
    decision_id: str
    status: PlaybackStatus
    accepted_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    detail_code: str | None = None


class AudioController(Protocol):
    """The only operation by which application code may request playback."""

    def send(self, command: AudioCommand) -> AudioPlaybackReceipt:
        """Submit one bounded, catalogued command."""


AudioPort = AudioController

__all__ = ["AudioController", "AudioPlaybackReceipt", "AudioPort", "PlaybackStatus"]
