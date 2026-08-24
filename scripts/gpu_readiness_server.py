#!/usr/bin/env python3
"""Expose aggregate GPU readiness inside the Compose network."""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

RECEIPT = Path(
    os.environ.get("SMOKE_GPU_READINESS_RECEIPT", "/run/smoke-detect/gpu-readiness.json")
)
DEPENDENCIES = (
    "http://smoke-detect:8000/health/ready",
    "http://baseline-model:8000/v2/health/ready",
)


def readiness() -> tuple[bool, dict[str, Any]]:
    errors: list[str] = []
    try:
        value = json.loads(RECEIPT.read_text(encoding="utf-8"))
        if value.get("status") != "one-stream-ready":
            errors.append("one-stream receipt is not one-stream-ready")
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        errors.append(f"GPU receipt unavailable: {exc}")
    for url in DEPENDENCIES:
        try:
            with urllib.request.urlopen(url, timeout=1) as response:  # noqa: S310 - fixed internal URL
                if response.status != 200:
                    errors.append(f"dependency returned HTTP {response.status}: {url}")
        except (OSError, urllib.error.URLError) as exc:
            errors.append(f"dependency unavailable: {url}: {exc}")
    return not errors, {"status": "ready" if not errors else "degraded", "errors": errors}


class Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802 - stdlib handler API
        if self.path not in {"/health/ready", "/health/live"}:
            self.send_error(404)
            return
        ok, payload = (True, {"status": "ok"}) if self.path == "/health/live" else readiness()
        body = (json.dumps(payload, sort_keys=True) + "\n").encode()
        self.send_response(200 if ok else 503)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, _format: str, *_args: object) -> None:
        return


if __name__ == "__main__":
    ThreadingHTTPServer(
        ("0.0.0.0", int(os.environ.get("SMOKE_GPU_READINESS_PORT", "8080"))), Handler
    ).serve_forever()
