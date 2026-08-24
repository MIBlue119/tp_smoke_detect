#!/usr/bin/env python3
"""Verify or explicitly acquire GPU model artifacts.

The command is offline by default and never runs as part of a service start.
``--allow-network`` is an operator acknowledgement that the checked-in
canonical source, legal disposition, expected size, and expected hash have
already been reviewed.  A missing field always produces a blocked receipt.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from ml.registry.model_repository import (
    ModelRepositoryManifest,
    acquire_model_artifact,
    verify_local_artifact,
)


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--manifest",
        type=Path,
        default=Path("model-repository/manifest/model-release.json"),
    )
    parser.add_argument(
        "--artifact-root",
        type=Path,
        default=None,
        help="approved local artifact store; never a remote URL",
    )
    parser.add_argument("--receipt", type=Path, default=None)
    parser.add_argument("--acquire", metavar="ARTIFACT_ID", default=None)
    parser.add_argument("--allow-network", action="store_true")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    receipt: dict[str, Any] = {
        "schema_version": "ml.model-artifact-receipt.v1",
        "status": "blocked",
        "manifest": str(args.manifest),
        "manifest_sha256": None,
        "models": [],
        "engines": [],
        "errors": [],
    }
    try:
        manifest = ModelRepositoryManifest.read(args.manifest)
    except (OSError, ValueError, json.JSONDecodeError) as error:
        receipt["errors"] = [f"manifest could not be loaded: {error}"]
        return _finish(receipt, args.receipt)
    receipt["manifest_sha256"] = manifest.digest
    errors = list(manifest.validate())
    selected = None
    if args.acquire:
        selected = next(
            (model for model in manifest.models if model.artifact_id == args.acquire), None
        )
        if selected is None:
            errors.append(f"unknown artifact requested for acquisition: {args.acquire}")
        elif args.artifact_root is None:
            errors.append("--artifact-root is required for acquisition")
        else:
            acquisition = acquire_model_artifact(
                selected,
                args.artifact_root,
                allow_network=args.allow_network,
            )
            if acquisition.status != "verified":
                errors.extend(f"{selected.artifact_id}: {error}" for error in acquisition.errors)
    elif args.allow_network:
        errors.append("--allow-network requires --acquire ARTIFACT_ID")
    artifact_root = args.artifact_root
    for model in manifest.models:
        verification = (
            verify_local_artifact(model, artifact_root) if artifact_root is not None else None
        )
        model_receipt: dict[str, Any] = {
            "artifact_id": model.artifact_id,
            "role": model.role,
            "version": model.version,
            "source_revision": model.source.source_revision,
            "license_id": model.source.license_id,
            "license_disposition": model.source.license_disposition.value,
            "artifact_sha256": model.artifact_sha256,
            "export_sha256": model.export_sha256,
            "sbom_sha256": model.sbom_sha256,
            "rollback_target": model.rollback_target,
            "verification": verification.to_dict() if verification else {"status": "not-run"},
        }
        receipt["models"].append(model_receipt)
        if verification is None:
            errors.append(f"{model.artifact_id}: artifact store was not provided")
        elif verification.status != "verified":
            errors.extend(f"{model.artifact_id}: {error}" for error in verification.errors)
    for engine in manifest.engines:
        engine_errors = list(engine.validate())
        receipt["engines"].append(
            {
                "engine_id": engine.engine_id,
                "artifact_id": engine.artifact_id,
                "runtime_profile": engine.runtime_profile,
                "precision": engine.precision,
                "compute_capability": engine.compute_capability,
                "cuda_version": engine.cuda_version,
                "tensorrt_version": engine.tensorrt_version,
                "status": "verified" if not engine_errors else "blocked",
                "errors": engine_errors,
            }
        )
        errors.extend(f"{engine.engine_id}: {error}" for error in engine_errors)
    receipt["errors"] = list(dict.fromkeys(errors))
    receipt["status"] = "verified" if not receipt["errors"] else "blocked"
    return _finish(receipt, args.receipt)


def _finish(receipt: dict[str, Any], output: Path | None) -> int:
    rendered = json.dumps(receipt, sort_keys=True, indent=2) + "\n"
    if output is None:
        sys.stdout.write(rendered)
    else:
        _write_json(output, receipt)
        sys.stdout.write(f"{output}\n")
    return 0 if receipt["status"] == "verified" else 1


if __name__ == "__main__":
    raise SystemExit(main())
