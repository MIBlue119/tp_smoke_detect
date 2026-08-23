from __future__ import annotations

import pytest

from tp_smoke_detect.adapters.audio.http import HttpAudioController


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
