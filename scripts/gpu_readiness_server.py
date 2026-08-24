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

import yaml
from scripts.gpu_receipts import validate_one_stream_receipt

RECEIPT = Path(
    os.environ.get("SMOKE_GPU_READINESS_RECEIPT", "/run/smoke-detect/gpu-readiness.json")
)
CANDIDATE_READY_FILE = Path(
    os.environ.get("SMOKE_GPU_CANDIDATE_READY_FILE", "/run/gpu-state/candidate-ready")
)
DEPENDENCIES = (
    "http://smoke-detect:8000/health/ready",
    "http://baseline-model:8000/v2/health/ready",
)
MANIFEST = Path(os.environ.get("SMOKE_MODEL_MANIFEST", "/models/manifest/model-release.json"))
IMAGE_PINS = Path(os.environ.get("SMOKE_IMAGE_PINS", "/bundle/image-pins.yaml"))
READINESS_KEY = Path(
    os.environ.get("SMOKE_GPU_READINESS_VERIFY_KEY", "/run/secrets/gpu_readiness_signing_key")
)
EXECUTOR_KEY = Path(
    os.environ.get("SMOKE_GPU_EXECUTOR_VERIFY_KEY", "/run/secrets/gpu_executor_verify_key")
)


def readiness() -> tuple[bool, dict[str, Any]]:
    errors: list[str] = []
    try:
        value = json.loads(RECEIPT.read_text(encoding="utf-8"))
        try:
            readiness_key = READINESS_KEY.read_bytes()
            executor_key = EXECUTOR_KEY.read_bytes()
        except OSError as exc:
            readiness_key = None
            executor_key = None
            errors.append(f"GPU signature verification keys unavailable: {exc}")
        manifest_sha256 = None
        image_digest = None
        try:
            from ml.registry.model_repository import ModelRepositoryManifest

            manifest_sha256 = ModelRepositoryManifest.read(MANIFEST).digest
            pins = yaml.safe_load(IMAGE_PINS.read_text(encoding="utf-8")) or {}
            image_digest = pins.get("images", {}).get("deepstream_triton_rtx3090", {}).get("digest")
        except (OSError, ValueError, yaml.YAMLError, AttributeError, TypeError) as exc:
            errors.append(f"GPU identity inputs unavailable: {exc}")
        errors.extend(
            validate_one_stream_receipt(
                value,
                manifest_sha256=manifest_sha256,
                image_digest=image_digest,
                signing_key=readiness_key,
                signing_key_id=os.environ.get("SMOKE_GPU_READINESS_KEY_ID", "gpu-readiness"),
                executor_signing_key=executor_key,
                executor_signing_key_id=os.environ.get("SMOKE_GPU_EXECUTOR_KEY_ID", "gpu-executor"),
            )
        )
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        errors.append(f"GPU receipt unavailable: {exc}")
    if not CANDIDATE_READY_FILE.is_file():
        errors.append(f"candidate consumer is not ready: {CANDIDATE_READY_FILE}")
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
