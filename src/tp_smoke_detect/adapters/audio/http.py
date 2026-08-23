"""Small safe HTTP adapter for a local audio worker.

Only the versioned command JSON is sent.  The adapter accepts no arbitrary
message text, validates expiry locally, and keeps one idempotency key per
command.  URL policy belongs to deployment; HTTPS or loopback HTTP is allowed
for the site-local worker, while remote HTTP endpoints are rejected.
"""

from __future__ import annotations

import ipaddress
import json
import socket
from datetime import UTC, datetime
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from ...contracts import AudioCommand
from ...domain.policy.audio import DEFAULT_MESSAGE_CATALOG
from ...ports.audio import AudioPlaybackReceipt, PlaybackStatus


def _is_local_host(host: str) -> bool:
    if host in {"localhost", "127.0.0.1", "::1"}:
        return True
    try:
        return ipaddress.ip_address(host).is_private
    except ValueError:
        try:
            return all(
                ipaddress.ip_address(item[4][0]).is_private
                for item in socket.getaddrinfo(host, None)
            )
        except (OSError, ValueError):
            return False


class HttpAudioController:
    def __init__(
        self,
        endpoint: str,
        *,
        timeout_seconds: float = 2.0,
        catalog: frozenset[str] | None = None,
    ) -> None:
        parsed = urlparse(endpoint)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("audio endpoint must be an HTTP(S) URL")
        if parsed.scheme == "http" and not _is_local_host(parsed.hostname or ""):
            raise ValueError("plain HTTP audio endpoints must be site-local")
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        self.endpoint = endpoint
        self.timeout_seconds = timeout_seconds
        self.catalog = catalog or frozenset(DEFAULT_MESSAGE_CATALOG)

    def send(self, command: AudioCommand) -> AudioPlaybackReceipt:
        now = datetime.now(UTC)
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
        payload = json.dumps(command.model_dump(mode="json"), separators=(",", ":")).encode()
        request = Request(
            self.endpoint,
            data=payload,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "Idempotency-Key": str(command.command_id),
            },
        )
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:
                body = json.loads(response.read().decode("utf-8"))
            return AudioPlaybackReceipt.model_validate(body)
        except TimeoutError:
            return AudioPlaybackReceipt(
                command_id=str(command.command_id),
                decision_id=str(command.decision_id),
                status=PlaybackStatus.FAILED,
                accepted_at=now,
                detail_code="timeout",
            )
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            return AudioPlaybackReceipt(
                command_id=str(command.command_id),
                decision_id=str(command.decision_id),
                status=PlaybackStatus.FAILED,
                accepted_at=now,
                detail_code="adapter_error",
            )


__all__ = ["HttpAudioController"]
