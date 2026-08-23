from __future__ import annotations

import socket
from urllib.error import URLError

import pytest

from tp_smoke_detect.adapters.audio.http import (
    HttpAudioController,
    _NoRedirect,
    _resolve_safe_addresses,
    _safe_address,
)


@pytest.mark.parametrize("endpoint", ["https://example.com/audio", "http://public.example/audio"])
def test_http_audio_rejects_public_hosts_for_both_schemes(endpoint: str) -> None:
    with pytest.raises(ValueError, match="internal allowlist"):
        HttpAudioController(endpoint)


def test_http_audio_accepts_explicit_internal_dns_allowlist_over_https() -> None:
    adapter = HttpAudioController(
        "https://audio-worker.site.internal/v1/play",
        allowed_hosts={"audio-worker.site.internal"},
    )
    assert adapter.endpoint.startswith("https://audio-worker.site.internal/")


@pytest.mark.parametrize(
    "endpoint",
    [
        "https://user:password@audio-worker.site.internal/play",
        "https://audio_worker.site.internal/play",
        "https://audio-worker.site.internal/play?redirect=https://example.com",
    ],
)
def test_http_audio_rejects_unsafe_endpoint_forms(endpoint: str) -> None:
    with pytest.raises(ValueError):
        HttpAudioController(endpoint, allowed_hosts={"audio-worker.site.internal"})


def test_http_audio_rejects_public_address_after_dns_rebinding(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *_args, **_kwargs: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", 8080))],
    )
    with pytest.raises(ValueError, match="public or unsafe"):
        _resolve_safe_addresses("audio-worker.site.internal", 8080)


@pytest.mark.parametrize(
    "address",
    [
        "169.254.169.254",
        "0.0.0.0",
        "192.0.2.1",
        "198.18.0.1",
        "fe80::1",
        "::",
        "ff02::1",
        "2001:db8::1",
    ],
)
def test_http_audio_rejects_special_purpose_addresses(address: str) -> None:
    assert not _safe_address(address)


def test_http_audio_redirects_are_disabled() -> None:
    with pytest.raises(URLError, match="redirects are disabled"):
        _NoRedirect().redirect_request(None, "http://public.example", {}, 302, "redirect", {})
