#!/usr/bin/env python3
"""Offline GPU bundle preflight; incomplete artifacts always remain blocked."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any

import yaml
from ml.registry.model_repository import ModelRepositoryManifest, verify_local_artifact
from scripts.gpu_receipts import validate_one_stream_receipt

SHA256 = re.compile(r"^sha256:[0-9a-f]{64}$")
REQUIRED_IMAGES = ("core", "mqtt", "prometheus", "deepstream_triton_rtx3090")


def _file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_bundle(
    pins_path: Path,
    manifest_path: Path,
    *,
    artifact_root: Path | None = None,
    one_stream_receipt: Path | None = None,
    image_archive: Path | None = None,
    sbom_root: Path | None = None,
) -> dict[str, Any]:
    errors: list[str] = []
    try:
        raw = yaml.safe_load(pins_path.read_text(encoding="utf-8"))
        pins = raw if isinstance(raw, dict) else {}
    except (OSError, yaml.YAMLError) as exc:
        pins, errors = {}, [f"image pins unavailable: {exc}"]
    images = pins.get("images", {})
    if not isinstance(images, dict):
        images, errors = {}, [*errors, "image pins.images must be a mapping"]
    image_receipts: list[dict[str, Any]] = []
    for image_id in REQUIRED_IMAGES:
        item = images.get(image_id)
        if not isinstance(item, dict):
            errors.append(f"missing image pin: {image_id}")
            continue
        digest = item.get("digest")
        valid = isinstance(digest, str) and SHA256.fullmatch(digest) is not None
        if not valid:
            errors.append(f"{image_id}: digest is unresolved")
        pinned = str(item.get("pinned_reference", ""))
        if pinned and str(digest) not in pinned:
            errors.append(f"{image_id}: pinned_reference does not contain digest")
        if item.get("sbom") in {None, "", "REQUIRED-BEFORE-RELEASE"}:
            errors.append(f"{image_id}: SBOM receipt is unresolved")
        image_receipts.append(
            {"id": image_id, "status": "ok" if valid else "blocked", "digest": digest}
        )

    manifest_receipt: dict[str, Any] = {"path": str(manifest_path), "status": "blocked"}
    try:
        manifest = ModelRepositoryManifest.read(manifest_path)
        manifest_errors = list(manifest.validate())
        errors.extend(f"model manifest: {error}" for error in manifest_errors)
        if artifact_root is None and manifest.models:
            errors.append("model artifact root was not supplied")
        if artifact_root is not None:
            for model in manifest.models:
                result = verify_local_artifact(model, artifact_root)
                errors.extend(f"{model.artifact_id}: {error}" for error in result.errors)
            for engine in manifest.engines:
                errors.extend(engine.validate(plan_root=artifact_root))
        manifest_receipt.update(
            {"status": "ok" if not manifest_errors else "blocked", "digest": manifest.digest}
        )
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        errors.append(f"model manifest unavailable: {exc}")
        manifest_receipt["error"] = str(exc)

    archive_receipt: dict[str, Any] = {"status": "not-provided"}
    if image_archive is not None:
        if image_archive.is_file() and image_archive.stat().st_size:
            archive_receipt = {"status": "present", "sha256": _file_hash(image_archive)}
        else:
            errors.append(f"image archive is missing or empty: {image_archive}")
            archive_receipt = {"status": "missing"}
    sbom_receipt: dict[str, Any] = {"status": "not-provided"}
    if sbom_root is not None:
        files = (
            sorted(path for path in sbom_root.rglob("*") if path.is_file())
            if sbom_root.is_dir()
            else []
        )
        sbom_receipt = {"status": "present" if files else "missing", "files": len(files)}
        if not files:
            errors.append(f"SBOM directory is missing or empty: {sbom_root}")
    stream_receipt: dict[str, Any] = {"status": "not-provided"}
    if one_stream_receipt is not None:
        try:
            value = json.loads(one_stream_receipt.read_text(encoding="utf-8"))
            stream_receipt = value if isinstance(value, dict) else {"status": "invalid"}
            expected_image = None
            deepstream_pin = images.get("deepstream_triton_rtx3090")
            if isinstance(deepstream_pin, dict):
                expected_image = deepstream_pin.get("digest")
            errors.extend(
                validate_one_stream_receipt(
                    stream_receipt,
                    manifest_sha256=manifest_receipt.get("digest"),
                    image_digest=expected_image,
                )
            )
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            errors.append(f"one-stream receipt unavailable: {exc}")
    errors = list(dict.fromkeys(errors))
    return {
        "schema_version": "gpu.offline-bundle-receipt.v1",
        "status": "one-stream-ready"
        if not errors and one_stream_receipt is not None
        else "blocked",
        "offline": True,
        "errors": errors,
        "images": image_receipts,
        "model_manifest": manifest_receipt,
        "image_archive": archive_receipt,
        "sbom": sbom_receipt,
        "one_stream": stream_receipt,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pins", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--artifact-root", type=Path)
    parser.add_argument("--one-stream-receipt", type=Path)
    parser.add_argument("--image-archive", type=Path)
    parser.add_argument("--sbom-root", type=Path)
    parser.add_argument("--receipt", type=Path)
    args = parser.parse_args()
    receipt = verify_bundle(
        args.pins,
        args.manifest,
        artifact_root=args.artifact_root,
        one_stream_receipt=args.one_stream_receipt,
        image_archive=args.image_archive,
        sbom_root=args.sbom_root,
    )
    rendered = json.dumps(receipt, indent=2, sort_keys=True) + "\n"
    if args.receipt is None:
        sys.stdout.write(rendered)
    else:
        args.receipt.parent.mkdir(parents=True, exist_ok=True)
        args.receipt.write_text(rendered, encoding="utf-8")
        print(args.receipt)
    return 0 if receipt["status"] == "one-stream-ready" else 1


if __name__ == "__main__":
    raise SystemExit(main())
