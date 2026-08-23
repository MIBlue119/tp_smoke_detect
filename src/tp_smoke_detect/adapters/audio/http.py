"""Small safe HTTP adapter for a local audio worker.

Only the versioned command JSON is sent.  The adapter accepts no arbitrary
message text, validates expiry locally, and keeps one idempotency key per
command.  HTTPS is not an egress boundary: loopback/private hosts are allowed
by default and DNS names require an exact configured internal allowlist entry.
"""

from __future__ import annotations

import ipaddress
import json
import re
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


_DNS_HOST = re.compile(
    r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)*$"
)


def _normalize_host(host: str) -> str:
    normalized = host.rstrip(".").lower()
    if not normalized:
        raise ValueError("audio endpoint host is required")
    try:
        ipaddress.ip_address(normalized)
    except ValueError:
        if len(normalized) > 253 or _DNS_HOST.fullmatch(normalized) is None:
            raise ValueError("audio endpoint host is not DNS-safe") from None
    return normalized


class HttpAudioController:
    def __init__(
        self,
        endpoint: str,
        *,
        timeout_seconds: float = 2.0,
        catalog: frozenset[str] | None = None,
        allowed_hosts: frozenset[str] | set[str] | tuple[str, ...] | None = None,
    ) -> None:
        parsed = urlparse(endpoint)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.netloc
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("audio endpoint must be an HTTP(S) URL")
        host = _normalize_host(parsed.hostname or "")
        configured_hosts = frozenset(_normalize_host(item) for item in (allowed_hosts or ()))
        # HTTPS does not make a public host safe.  Keep the default reference
        # adapter loopback/private-only and require exact operator allowlisting
        # for internal DNS names (avoiding suffix or wildcard matches).
        if not _is_local_host(host) and host not in configured_hosts:
            raise ValueError("audio endpoint host is not in the internal allowlist")
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        self.endpoint = endpoint
        self.timeout_seconds = timeout_seconds
        self.catalog = catalog or frozenset(DEFAULT_MESSAGE_CATALOG)
        self.allowed_hosts = configured_hosts

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
            receipt = AudioPlaybackReceipt.model_validate(body)
            if receipt.command_id != str(command.command_id) or receipt.decision_id != str(
                command.decision_id
            ):
                return AudioPlaybackReceipt(
                    command_id=str(command.command_id),
                    decision_id=str(command.decision_id),
                    status=PlaybackStatus.FAILED,
                    accepted_at=now,
                    detail_code="adapter_error",
                )
            if receipt.accepted_at.tzinfo is None or receipt.accepted_at.utcoffset() is None:
                return AudioPlaybackReceipt(
                    command_id=str(command.command_id),
                    decision_id=str(command.decision_id),
                    status=PlaybackStatus.FAILED,
                    accepted_at=now,
                    detail_code="invalid_receipt_time",
                )
            receipt_at = receipt.accepted_at.astimezone(UTC)
            if receipt_at > command.expires_at.astimezone(UTC):
                return AudioPlaybackReceipt(
                    command_id=str(command.command_id),
                    decision_id=str(command.decision_id),
                    status=PlaybackStatus.EXPIRED,
                    accepted_at=receipt_at,
                    detail_code="late_receipt",
                )
            return receipt
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
