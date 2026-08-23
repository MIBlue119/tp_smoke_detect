"""Small safe HTTP adapter for a local audio worker.

Only the versioned command JSON is sent.  The adapter accepts no arbitrary
message text, validates expiry locally, and keeps one idempotency key per
command.  HTTPS is not an egress boundary: loopback/private hosts are allowed
by default and DNS names require an exact configured internal allowlist entry.
"""

from __future__ import annotations

import http.client
import ipaddress
import json
import re
import socket
from datetime import UTC, datetime
from typing import Any
from urllib.error import URLError
from urllib.parse import urlparse
from urllib.request import (
    HTTPHandler,
    HTTPRedirectHandler,
    HTTPSHandler,
    ProxyHandler,
    Request,
    build_opener,
)

from ...contracts import AudioCommand
from ...domain.policy.audio import DEFAULT_MESSAGE_CATALOG
from ...ports.audio import AudioPlaybackReceipt, PlaybackStatus

_APPROVED_LOCAL_NETWORKS = (
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("fc00::/7"),
)


def _is_local_host(host: str) -> bool:
    try:
        return _safe_address(host)
    except ValueError:
        try:
            return all(_safe_address(str(item[4][0])) for item in socket.getaddrinfo(host, None))
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


def _safe_address(value: str) -> bool:
    address = ipaddress.ip_address(value)
    return any(address in network for network in _APPROVED_LOCAL_NETWORKS)


def _resolve_safe_addresses(host: str, port: int) -> tuple[str, ...]:
    """Resolve immediately before I/O and reject public/mixed destinations."""

    try:
        records = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except (OSError, ValueError) as exc:
        raise ValueError("audio endpoint host cannot be resolved") from exc
    addresses: list[str] = []
    for record in records:
        raw = str(record[4][0])
        try:
            safe = _safe_address(raw)
        except ValueError:
            safe = False
        if not safe:
            raise ValueError("audio endpoint resolved to a public or unsafe address")
        if raw not in addresses:
            addresses.append(raw)
    if not addresses:
        raise ValueError("audio endpoint has no stream address")
    return tuple(addresses)


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *_args: object, **_kwargs: object) -> None:
        raise URLError("audio endpoint redirects are disabled")


class _PinnedHTTPConnection(http.client.HTTPConnection):
    def __init__(self, host: str, address: str, **kwargs: Any) -> None:
        super().__init__(host, **kwargs)
        self._pinned_address = address
        self._tp_source_address = kwargs.get("source_address")
        self._tp_tunnel_host = getattr(self, "_tunnel_host", None)

    def connect(self) -> None:
        self.sock = socket.create_connection(
            (self._pinned_address, self.port), self.timeout, self._tp_source_address
        )
        if self._tp_tunnel_host:
            tunnel = getattr(self, "_tunnel")  # noqa: B009
            tunnel()


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    def __init__(self, host: str, address: str, **kwargs: Any) -> None:
        super().__init__(host, **kwargs)
        self._pinned_address = address
        self._tp_source_address = kwargs.get("source_address")
        self._tp_tunnel_host = getattr(self, "_tunnel_host", None)
        self._tp_context = getattr(self, "_context")  # noqa: B009

    def connect(self) -> None:
        self.sock = socket.create_connection(
            (self._pinned_address, self.port), self.timeout, self._tp_source_address
        )
        if self._tp_tunnel_host:
            tunnel = getattr(self, "_tunnel")  # noqa: B009
            tunnel()
        self.sock = self._tp_context.wrap_socket(self.sock, server_hostname=self.host)


class _PinnedHTTPHandler(HTTPHandler):
    def __init__(self, address: str) -> None:
        super().__init__()
        self.address = address

    def http_open(self, request: Request):  # type: ignore[no-untyped-def]
        return self.do_open(
            lambda host, **kwargs: _PinnedHTTPConnection(host, self.address, **kwargs), request
        )


class _PinnedHTTPSHandler(HTTPSHandler):
    def __init__(self, address: str) -> None:
        super().__init__()
        self.address = address

    def https_open(self, request: Request):  # type: ignore[no-untyped-def]
        return self.do_open(
            lambda host, **kwargs: _PinnedHTTPSConnection(host, self.address, **kwargs), request
        )


def urlopen(request: Request, *, timeout: float) -> Any:
    """Open one request without proxying, redirects, or a second DNS lookup."""

    address = getattr(request, "_tp_pinned_address", None)
    if not isinstance(address, str):
        raise ValueError("audio request is missing a pinned destination")
    opener = build_opener(
        ProxyHandler({}), _NoRedirect(), _PinnedHTTPHandler(address), _PinnedHTTPSHandler(address)
    )
    return opener.open(request, timeout=timeout)


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
        try:
            literal = ipaddress.ip_address(host)
        except ValueError:
            literal = None
        if literal is not None and not _safe_address(host):
            raise ValueError("audio endpoint host is not internal")
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
            parsed = urlparse(self.endpoint)
            addresses = _resolve_safe_addresses(
                parsed.hostname or "",
                parsed.port or (443 if parsed.scheme == "https" else 80),
            )
            # The custom opener connects to this exact address while retaining
            # the configured hostname for Host and TLS SNI.
            request._tp_pinned_address = addresses[0]  # type: ignore[attr-defined]
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
            if receipt_at >= command.expires_at.astimezone(UTC):
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
