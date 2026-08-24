#!/usr/bin/env python3
"""Run the fail-closed GPU-107 qualification gates.

The runner is deliberately useful on both a CPU checkout and the target host:
it emits a machine-readable receipt even when prerequisites are unavailable,
and it never turns a skipped command, synthetic replay, metadata-only model,
or partial DeepStream probe into a qualification pass.  Runtime execution is
opt-in and must be supplied by a reviewed local bundle or command; this script
does not download media or model artifacts.
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import re
import secrets
import shlex
import signal
import subprocess
import sys
import time
from collections.abc import Iterable
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from math import ceil
from pathlib import Path
from typing import Any

import yaml
from ml.registry.model_repository import ModelRepositoryManifest, verify_local_artifact
from scripts.gpu_receipts import build_one_stream_receipt, telemetry_hmac

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_IMAGE = (
    "nvcr.io/nvidia/deepstream:7.0-triton-multiarch@"
    "sha256:c11befa808af8270e8ea0d0d7cc7cabbda08a2f496ee30b95f7acba5dce81759"
)
DEFAULT_REPLAY = ROOT / "tests/load/replay-pack.yaml"
DEFAULT_MODEL_MANIFEST = ROOT / "model-repository/manifest/model-release.json"
DEFAULT_IMAGE_PINS = ROOT / "deploy/image-pins.yaml"
SHA256 = re.compile(r"^[0-9a-f]{64}$")
REQUIRED_ROLES = {"person_detector", "pose_landmarker", "hand_landmarker", "crop_classifier"}
TELEMETRY_FIELDS = {
    "scheduled_samples",
    "processed_samples",
    "dropped_samples",
    "queue_depth",
    "queue_age_ms",
    "candidate_latency_ms",
    "nvdec_utilization",
    "gpu_utilization",
    "memory_used_mib",
    "analysis_fps",
    "camera_id",
    "model_revision",
    "runtime_identity",
    "fault_isolation",
}


@dataclass(frozen=True)
class CommandResult:
    argv: tuple[str, ...]
    status: str
    returncode: int | None
    duration_seconds: float
    stdout_tail: str
    stderr_tail: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "argv": list(self.argv),
            "status": self.status,
            "returncode": self.returncode,
            "duration_seconds": round(self.duration_seconds, 3),
            "stdout_tail": self.stdout_tail,
            "stderr_tail": self.stderr_tail,
        }


def _tail(value: str, limit: int = 4000) -> str:
    value = value.replace("\x00", "")
    return value[-limit:]


def _text(value: str | bytes | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value


def run_command(argv: Iterable[str], timeout: float) -> CommandResult:
    command = tuple(str(item) for item in argv)
    started = time.monotonic()
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=max(0.1, timeout),
            check=False,
        )
    except FileNotFoundError as exc:
        return CommandResult(command, "unavailable", None, time.monotonic() - started, "", str(exc))
    except subprocess.TimeoutExpired as exc:
        return CommandResult(
            command,
            "timeout",
            None,
            time.monotonic() - started,
            _tail(_text(exc.stdout)),
            _tail(_text(exc.stderr)),
        )
    return CommandResult(
        command,
        "passed" if completed.returncode == 0 else "failed",
        completed.returncode,
        time.monotonic() - started,
        _tail(completed.stdout),
        _tail(completed.stderr),
    )


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def hash_json(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
    ).hexdigest()


def host_binding() -> str:
    """Return a non-identifying binding stable for this qualification process."""

    machine_id = ""
    with suppress(OSError):
        machine_id = Path("/etc/machine-id").read_text(encoding="utf-8").strip()
    return hashlib.sha256(f"{os.uname().nodename}:{machine_id}".encode()).hexdigest()


def process_start_ticks(pid: int) -> str:
    """Read Linux process start ticks, preventing PID reuse from passing a receipt."""

    raw = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
    fields = raw.rsplit(") ", 1)[1].split()
    return fields[19]


def runtime_binding(pid: int, start_ticks: str) -> str:
    return f"pid:{pid}:start:{start_ticks}"


def load_document(path: Path) -> tuple[Any | None, list[str]]:
    try:
        with path.open(encoding="utf-8") as stream:
            return yaml.safe_load(stream), []
    except (OSError, yaml.YAMLError) as exc:
        return None, [f"cannot read {path}: {exc}"]


def host_probe(timeout: float) -> tuple[dict[str, Any], list[CommandResult]]:
    checks: list[CommandResult] = []
    result: dict[str, Any] = {"status": "blocked", "gpu": None, "docker": None}
    smi = run_command(
        [
            "nvidia-smi",
            "--query-gpu=name,compute_cap,driver_version,memory.total",
            "--format=csv,noheader,nounits",
        ],
        timeout,
    )
    checks.append(smi)
    errors: list[str] = []
    gpu = smi.stdout_tail.strip().splitlines()[0] if smi.stdout_tail.strip() else ""
    result["gpu"] = {"raw": gpu, "command": smi.as_dict()}
    fields = [item.strip() for item in gpu.split(",")]
    if smi.status != "passed":
        errors.append(f"nvidia-smi unavailable: {smi.status}")
    elif len(fields) < 4 or "RTX 3090" not in fields[0] or fields[1] != "8.6":
        errors.append(f"expected RTX 3090 compute capability 8.6, observed: {gpu or 'none'}")
    elif not fields[2].startswith("580."):
        errors.append(f"expected R580 driver branch, observed: {fields[2]}")
    info = run_command(
        ["docker", "info", "--format", "server={{.ServerVersion}} default={{.DefaultRuntime}}"],
        timeout,
    )
    runtimes = run_command(
        ["docker", "info", "--format", "{{range $name, $_ := .Runtimes}}{{$name}} {{end}}"],
        timeout,
    )
    checks.extend((info, runtimes))
    result["docker"] = {"info": info.as_dict(), "runtimes": runtimes.as_dict()}
    if info.status != "passed":
        errors.append(f"Docker unavailable: {info.status}")
    runtime_names = runtimes.stdout_tail.split() if runtimes.status == "passed" else []
    result["docker"]["runtime_names"] = runtime_names
    if "nvidia" not in runtime_names:
        errors.append("Docker NVIDIA runtime is not registered")
    result["errors"] = errors
    result["status"] = "ready" if not errors else "blocked"
    return result, checks


def image_probe(
    image: str,
    *,
    pull: bool,
    timeout: float,
    host: dict[str, Any],
) -> tuple[dict[str, Any], list[CommandResult]]:
    checks: list[CommandResult] = []
    errors: list[str] = []
    result: dict[str, Any] = {"image": image, "status": "blocked", "pull": None, "inspect": None}
    if "@sha256:" not in image:
        errors.append("image must be digest-pinned with @sha256")
    if host.get("status") != "ready":
        errors.append("host/NVIDIA runtime prerequisite is blocked")
    if pull and not errors:
        pulled = run_command(["docker", "pull", image], timeout)
        checks.append(pulled)
        result["pull"] = pulled.as_dict()
        if pulled.status != "passed":
            errors.append(f"exact image pull did not complete: {pulled.status}")
    elif pull:
        result["pull"] = {"status": "not-run", "reason": "host prerequisite blocked"}
    inspect = run_command(
        ["docker", "image", "inspect", image, "--format", "{{json .RepoDigests}}"], timeout
    )
    checks.append(inspect)
    result["inspect"] = inspect.as_dict()
    if inspect.status != "passed":
        errors.append("exact digest-pinned image is not imported")
    elif image.split("@", 1)[1] not in inspect.stdout_tail:
        errors.append("imported image digest does not match requested digest")
    if not errors:
        gpu_probe = run_command(
            [
                "docker",
                "run",
                "--rm",
                "--pull=never",
                "--gpus",
                "all",
                "--network",
                "none",
                "--entrypoint",
                "nvidia-smi",
                image,
                "--query-gpu=name,compute_cap,driver_version,memory.total",
                "--format=csv,noheader,nounits",
            ],
            timeout,
        )
        tools = run_command(
            [
                "docker",
                "run",
                "--rm",
                "--pull=never",
                "--gpus",
                "all",
                "--network",
                "none",
                "--entrypoint",
                "sh",
                image,
                "-c",
                (
                    "for x in deepstream-app tritonserver gst-launch-1.0; "
                    'do printf \'%s=\' "$x"; command -v "$x" || true; done'
                ),
            ],
            timeout,
        )
        checks.extend((gpu_probe, tools))
        result["container"] = {"gpu": gpu_probe.as_dict(), "tools": tools.as_dict()}
        if gpu_probe.status != "passed":
            errors.append(f"container NVIDIA probe failed: {gpu_probe.status}")
        if tools.status != "passed" or "deepstream-app=" not in tools.stdout_tail:
            errors.append("container does not expose the required DeepStream runtime")
    result["errors"] = list(dict.fromkeys(errors))
    result["status"] = "ready" if not errors else "blocked"
    return result, checks


def model_probe(path: Path, artifact_root: Path | None) -> dict[str, Any]:
    result: dict[str, Any] = {"path": str(path), "status": "blocked", "manifest_sha256": None}
    errors: list[str] = []
    try:
        manifest = ModelRepositoryManifest.read(path)
        result["manifest_sha256"] = manifest.digest
        errors.extend(manifest.validate())
        roles = {model.role for model in manifest.models}
        missing = sorted(REQUIRED_ROLES - roles)
        if missing:
            errors.append(f"missing required model roles: {', '.join(missing)}")
        if artifact_root is None:
            errors.append("artifact root was not supplied; local model files cannot be verified")
        else:
            for model in manifest.models:
                verification = verify_local_artifact(model, artifact_root)
                errors.extend(f"{model.artifact_id}: {error}" for error in verification.errors)
            for engine in manifest.engines:
                errors.extend(engine.validate(plan_root=artifact_root))
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        errors.append(f"model manifest unavailable: {exc}")
    result["errors"] = list(dict.fromkeys(errors))
    result["status"] = "ready" if not errors else "blocked"
    return result


def replay_probe(
    path: Path,
    streams: int,
    duration_hours: float,
    analysis_fps: float,
    media_root: Path | None = None,
) -> dict[str, Any]:
    result: dict[str, Any] = {"path": str(path), "status": "blocked", "manifest_sha256": None}
    errors: list[str] = []
    try:
        if media_root is None:
            errors.append("media_root is required for every real qualification replay")
            media_root = Path("__missing-approved-media-root__")
        approved_root = media_root.resolve()
        result["media_root_policy"] = {
            "required": True,
            "root_sha256": hashlib.sha256(str(approved_root).encode()).hexdigest(),
        }
        raw = path.read_bytes()
        data = yaml.safe_load(raw) or {}
        result["manifest_sha256"] = hashlib.sha256(raw).hexdigest()
        if data.get("schema_version") != "gpu.replay-workload.v1":
            errors.append("replay manifest schema_version is not gpu.replay-workload.v1")
        if data.get("qualification_state") != "sealed":
            errors.append(
                "replay manifest is not sealed; metadata-only/synthetic packs cannot qualify"
            )
        if int(data.get("stream_count", 0)) < streams:
            errors.append(f"replay manifest has fewer than requested streams: {streams}")
        if float(data.get("duration_hours", 0)) < duration_hours:
            errors.append(f"replay manifest duration is shorter than requested {duration_hours}h")
        stream_rows = data.get("streams", [])
        if not isinstance(stream_rows, list) or len(stream_rows) < streams:
            errors.append("replay manifest stream rows are incomplete")
        for row in stream_rows[:streams] if isinstance(stream_rows, list) else []:
            stream_id = row.get("stream_id", "unknown")
            if row.get("source_kind") in {None, "synthetic", "metadata_only"}:
                errors.append(f"{stream_id}: source_kind is not an approved local replay")
            clip_hash = row.get("clip_sha256")
            if not isinstance(clip_hash, str) or not SHA256.fullmatch(clip_hash):
                errors.append(f"{stream_id}: clip_sha256 is missing or invalid")
            source = row.get("source_path")
            if not source:
                errors.append(f"{stream_id}: source_path is missing")
            else:
                source_path = Path(str(source))
                if source_path.is_absolute():
                    errors.append(f"{stream_id}: source_path must be relative to media root")
                    source_path = approved_root / "__invalid__"
                else:
                    source_path = (approved_root / source_path).resolve()
                    try:
                        source_path.relative_to(approved_root)
                    except ValueError:
                        errors.append(f"{stream_id}: source_path escapes media root")
                        source_path = approved_root / "__invalid__"
                if not source_path.is_file():
                    errors.append(f"{stream_id}: local source_path is not readable")
                else:
                    actual_hash = sha256_file(source_path)
                    if actual_hash != clip_hash:
                        errors.append(f"{stream_id}: clip_sha256 does not match source bytes")
            if float(row.get("analysis_fps", 0)) < analysis_fps:
                errors.append(f"{stream_id}: analysis_fps is below requested {analysis_fps}")
    except (OSError, TypeError, ValueError, yaml.YAMLError) as exc:
        errors.append(f"replay manifest unavailable: {exc}")
    result["errors"] = list(dict.fromkeys(errors))
    result["status"] = "ready" if not errors else "blocked"
    return result


def parse_telemetry(
    lines: Iterable[str],
    *,
    expected_runtime_pid: int | None = None,
    expected_runtime_start_ticks: str | None = None,
    expected_host_binding: str | None = None,
    expected_challenge: str | None = None,
    signing_key: bytes | None = None,
) -> dict[str, Any]:
    """Parse only runtime-owned telemetry while retaining rejected rows.

    A qualification command may read stdout from an arbitrary process, so a
    JSON object containing convenient counters is not evidence.  The deployed
    runtime emits ``gpu.telemetry.v1`` records with a process identity,
    monotonic timestamp/sequence, cumulative counters, measurement samples,
    model-role revisions, and an independently attested isolation receipt.
    Legacy/direct rows remain visible for diagnostics but are explicitly
    marked untrusted and can never qualify.
    """

    rows: list[dict[str, Any]] = []
    provenance_errors: list[str] = []
    timestamp_errors: list[str] = []
    trusted_count = 0
    trusted_timestamps: dict[str, list[int]] = {}
    required_counter_fields = {"scheduled_samples", "processed_samples", "dropped_samples"}
    required_roles = set(REQUIRED_ROLES)
    for line in lines:
        try:
            value = json.loads(line)
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
        if not isinstance(value, dict) or not (
            TELEMETRY_FIELDS.intersection(value) or value.get("schema_version")
        ):
            continue
        trusted = True
        reasons: list[str] = []
        if value.get("schema_version") != "gpu.telemetry.v1":
            reasons.append("telemetry schema_version is not gpu.telemetry.v1")
        if value.get("producer") != "tp-smoke-detect.gpu-runtime":
            reasons.append("telemetry producer is not the deployed GPU runtime")
        if not isinstance(value.get("receipt_id"), str) or not value["receipt_id"].strip():
            reasons.append("telemetry receipt_id is missing")
        if not isinstance(value.get("runtime_pid"), int) or value["runtime_pid"] <= 0:
            reasons.append("telemetry runtime_pid is missing")
        if expected_runtime_pid is None or value.get("runtime_pid") != expected_runtime_pid:
            reasons.append("telemetry runtime process is not the launched qualification process")
        if (
            not isinstance(value.get("runtime_start_ticks"), str)
            or not value["runtime_start_ticks"].strip()
        ):
            reasons.append("telemetry runtime start binding is missing")
        elif (
            expected_runtime_start_ticks is not None
            and value.get("runtime_start_ticks") != expected_runtime_start_ticks
        ):
            reasons.append("telemetry runtime start binding does not match the launched process")
        if not isinstance(value.get("host_binding"), str) or not value["host_binding"].strip():
            reasons.append("telemetry host binding is missing")
        elif (
            expected_host_binding is not None and value.get("host_binding") != expected_host_binding
        ):
            reasons.append("telemetry host binding does not match the qualification host")
        if (
            not isinstance(value.get("qualification_challenge"), str)
            or not value["qualification_challenge"].strip()
        ):
            reasons.append("telemetry qualification challenge is missing")
        elif (
            expected_challenge is not None
            and value.get("qualification_challenge") != expected_challenge
        ):
            reasons.append("telemetry qualification challenge does not match this run")
        signature = value.get("signature_hmac_sha256")
        if signing_key is None:
            reasons.append("telemetry signing key is unavailable")
        elif not isinstance(signature, str) or not hmac.compare_digest(
            signature, telemetry_hmac(value, signing_key)
        ):
            reasons.append("telemetry signature is invalid")
        if not isinstance(value.get("sequence"), int) or value["sequence"] < 0:
            reasons.append("telemetry sequence is missing")
        timestamp = value.get("timestamp_ns")
        if not isinstance(timestamp, int) or timestamp <= 0:
            reasons.append("telemetry timestamp_ns is missing")
        camera_id = str(value.get("camera_id", "")).strip()
        if not camera_id:
            reasons.append("telemetry camera_id is missing")
        counters = value.get("counters")
        if not isinstance(counters, dict):
            reasons.append("telemetry cumulative counters are missing")
            counters = {}
        if not required_counter_fields.issubset(counters):
            reasons.append("telemetry cumulative counters are incomplete")
        measurements = value.get("measurements")
        if not isinstance(measurements, list) or not measurements:
            reasons.append("telemetry independent measurement samples are missing")
            measurements = [value]
        model_revisions = value.get("model_revisions")
        if not isinstance(model_revisions, dict) or not required_roles.issubset(model_revisions):
            reasons.append("telemetry model-role revisions are incomplete")
        fault = value.get("fault_isolation")
        if (
            not isinstance(fault, dict)
            or fault.get("status") != "passed"
            or not str(fault.get("receipt_id", "")).strip()
            or fault.get("producer") != "tp-smoke-detect.fault-harness"
        ):
            reasons.append("telemetry fault-isolation receipt is not independently attested")
        if reasons:
            trusted = False
            provenance_errors.extend(reasons)
        previous_timestamp: int | None = None
        normalized: list[dict[str, Any]] = []
        for measurement in measurements:
            if not isinstance(measurement, dict):
                trusted = False
                provenance_errors.append("telemetry measurement sample is not an object")
                continue
            sample = dict(value)
            sample.update(measurement)
            for key, counter in counters.items():
                if key in sample and sample[key] != counter:
                    trusted = False
                    provenance_errors.append(f"telemetry aggregate mismatch for {key}")
                sample[key] = counter
            sample["model_revisions"] = model_revisions
            sample["fault_isolation"] = fault
            sample["_trusted"] = trusted
            sample["_measurement_timestamp_ns"] = sample.get("timestamp_ns")
            sample["_measurement_sequence"] = sample.get("sequence")
            measurement_timestamp = sample.get("timestamp_ns")
            if not isinstance(measurement_timestamp, int) or measurement_timestamp <= 0:
                trusted = False
                provenance_errors.append("telemetry measurement timestamp_ns is missing")
            elif previous_timestamp is not None and measurement_timestamp <= previous_timestamp:
                trusted = False
                timestamp_errors.append(f"{camera_id}: telemetry timestamps are not monotonic")
            previous_timestamp = (
                measurement_timestamp if isinstance(measurement_timestamp, int) else None
            )
            normalized.append(sample)
        if trusted:
            trusted_count += len(normalized)
            if camera_id:
                trusted_timestamps.setdefault(camera_id, []).extend(
                    int(item["timestamp_ns"])
                    for item in normalized
                    if isinstance(item.get("timestamp_ns"), int)
                )
        rows.extend(normalized)
    summary: dict[str, Any] = {
        "samples": len(rows),
        "fields": sorted({key for row in rows for key in row}),
        "provenance": {
            "trusted_samples": trusted_count,
            "trusted": trusted_count == len(rows) and len(rows) > 0,
            "errors": sorted(set(provenance_errors)),
        },
        "timestamp_errors": sorted(set(timestamp_errors)),
        "independent_sample_count": sum(
            1 for row in rows if isinstance(row.get("_measurement_timestamp_ns"), int)
        ),
    }
    for field in sorted(TELEMETRY_FIELDS):
        values = [
            float(row[field])
            for row in rows
            if isinstance(row.get(field), (int, float)) and not isinstance(row.get(field), bool)
        ]
        if values:
            summary[field] = {
                "min": min(values),
                "max": max(values),
                "avg": sum(values) / len(values),
            }
    for field in ("runtime_identity",):
        values = [str(row[field]).strip() for row in rows if str(row.get(field, "")).strip()]
        if values:
            summary[field] = values[-1]
    isolation = next(
        (
            row.get("fault_isolation")
            for row in rows
            if isinstance(row.get("fault_isolation"), dict)
        ),
        None,
    )
    if isolation is not None:
        summary["fault_isolation"] = isolation
    camera_ids = {str(row["camera_id"]) for row in rows if row.get("camera_id")}
    if camera_ids:
        summary["camera_count"] = len(camera_ids)
    role_revisions: dict[str, set[str]] = {}
    for row in rows:
        revisions = row.get("model_revisions")
        if isinstance(revisions, dict):
            for role, revision in revisions.items():
                if str(revision).strip():
                    role_revisions.setdefault(str(role), set()).add(str(revision).strip())
    if role_revisions:
        summary["model_revisions"] = {
            role: sorted(revisions) for role, revisions in sorted(role_revisions.items())
        }
    per_camera: dict[str, dict[str, Any]] = {}
    for camera_id in sorted(camera_ids):
        camera_rows = [row for row in rows if str(row.get("camera_id")) == camera_id]
        camera_summary: dict[str, Any] = {
            "samples": len(camera_rows),
            "fields": sorted({key for row in camera_rows for key in row}),
        }
        for field in sorted(TELEMETRY_FIELDS):
            values = [
                float(row[field])
                for row in camera_rows
                if isinstance(row.get(field), (int, float)) and not isinstance(row.get(field), bool)
            ]
            if values:
                camera_summary[field] = {
                    "min": min(values),
                    "max": max(values),
                    "avg": sum(values) / len(values),
                }
        for field in ("model_revision", "runtime_identity"):
            values = [
                str(row[field]).strip() for row in camera_rows if str(row.get(field, "")).strip()
            ]
            if values:
                camera_summary[field] = {"min": values[0], "max": values[-1]}
        camera_roles: dict[str, set[str]] = {}
        for row in camera_rows:
            revisions = row.get("model_revisions")
            if isinstance(revisions, dict):
                for role, revision in revisions.items():
                    if str(revision).strip():
                        camera_roles.setdefault(str(role), set()).add(str(revision).strip())
        if camera_roles:
            camera_summary["model_revisions"] = {
                role: sorted(revisions) for role, revisions in sorted(camera_roles.items())
            }
        per_camera[camera_id] = camera_summary
    summary["cameras"] = per_camera
    latency_values = [
        float(row["candidate_latency_ms"])
        for row in rows
        if isinstance(row.get("candidate_latency_ms"), (int, float))
    ]
    if latency_values:
        ordered = sorted(latency_values)
        summary["candidate_latency_p95_ms"] = ordered[
            min(len(ordered) - 1, int(len(ordered) * 0.95))
        ]
    return summary


def evaluate_telemetry(
    metrics: dict[str, Any],
    *,
    streams: int,
    duration_seconds: float,
    analysis_fps: float,
    required_duration_seconds: float = 0.0,
    sample_interval: float = 1.0,
) -> list[str]:
    """Evaluate measured values, not merely telemetry field names."""

    errors: list[str] = []
    provenance = metrics.get("provenance", {})
    if (
        not isinstance(provenance, dict)
        or provenance.get("trusted_samples") != metrics.get("samples")
        or not provenance.get("trusted")
    ):
        errors.append("telemetry provenance is untrusted; runtime-owned receipts are required")
    if metrics.get("timestamp_errors"):
        errors.extend(str(item) for item in metrics["timestamp_errors"])
    if metrics.get("independent_sample_count", 0) != metrics.get("samples", 0):
        errors.append("telemetry independent measurement sample count does not match rows")
    executor_capture = metrics.get("executor_capture")
    if not isinstance(executor_capture, dict) or executor_capture.get("trusted") is not True:
        errors.append(
            "authenticated qualification-executor GPU telemetry capture is required; "
            "caller-provided samples are not accepted"
        )
    elif not all(
        isinstance(executor_capture.get(key), str) and executor_capture[key].strip()
        for key in ("host_binding", "runtime_binding", "samples_sha256")
    ):
        errors.append("executor GPU telemetry capture binding or digest is incomplete")
    model_revisions = metrics.get("model_revisions")
    if not isinstance(model_revisions, dict) or not REQUIRED_ROLES.issubset(model_revisions):
        errors.append("all required model-role revisions must be present in runtime telemetry")
    required = {
        "scheduled_samples",
        "processed_samples",
        "dropped_samples",
        "queue_depth",
        "queue_age_ms",
        "candidate_latency_ms",
    }
    missing = sorted(required - set(metrics.get("fields", [])))
    if missing:
        errors.append(f"real runtime telemetry is incomplete: missing {', '.join(missing)}")
    for field in required:
        value = metrics.get(field, {}).get("max")
        if not isinstance(value, (int, float)) or not value >= 0:
            errors.append(f"telemetry field {field} is not finite and non-negative")
    scheduled = metrics.get("scheduled_samples", {}).get("max", 0)
    processed = metrics.get("processed_samples", {}).get("max", 0)
    dropped = metrics.get("dropped_samples", {}).get("max", 0)
    if scheduled <= 0:
        errors.append("scheduled_samples must be positive")
    else:
        if processed / scheduled < 0.99:
            errors.append("processed sample ratio is below 99%")
        if dropped / scheduled > 0.01:
            errors.append("dropped sample ratio exceeds 1%")
    if metrics.get("candidate_latency_p95_ms", float("inf")) > 8000:
        errors.append("candidate-to-decision p95 latency exceeds 8000ms")
    if metrics.get("queue_depth", {}).get("max", float("inf")) > 1024:
        errors.append("queue depth is unbounded above the 1024-sample qualification limit")
    if metrics.get("queue_age_ms", {}).get("max", float("inf")) > 8000:
        errors.append("queue age exceeds the 8000ms qualification limit")
    if metrics.get("camera_count", 0) != streams:
        errors.append(f"telemetry covers fewer than requested cameras: {streams}")
    cameras = metrics.get("cameras")
    if not isinstance(cameras, dict) or len(cameras) != streams:
        errors.append("per-camera telemetry rows are missing")
    required_samples = max(2, ceil(required_duration_seconds / max(sample_interval, 0.1)))
    if required_duration_seconds > 0 and duration_seconds + 0.5 < required_duration_seconds:
        errors.append("runtime duration is shorter than the requested qualification duration")
    for camera_id, camera in (cameras or {}).items():
        fields = set(camera.get("fields", []))
        missing_camera = sorted(
            (
                required
                | {
                    "analysis_fps",
                    "gpu_utilization",
                    "nvdec_utilization",
                    "model_revision",
                    "runtime_identity",
                }
            )
            - fields
        )
        if missing_camera:
            errors.append(
                f"{camera_id}: per-camera telemetry is incomplete: {', '.join(missing_camera)}"
            )
        if camera.get("samples", 0) < required_samples:
            errors.append(f"{camera_id}: telemetry sample count is below {required_samples}")
        fps = camera.get("analysis_fps", {}).get("min", -1)
        if not isinstance(fps, (int, float)) or fps < analysis_fps:
            errors.append(f"{camera_id}: per-camera analysis FPS is below requested {analysis_fps}")
        for field in ("gpu_utilization", "nvdec_utilization"):
            value = camera.get(field, {}).get("min", -1)
            if not isinstance(value, (int, float)) or value <= 0:
                errors.append(f"{camera_id}: measured {field} is absent or zero")
        if not str(camera.get("model_revision", {}).get("min", "")).strip():
            errors.append(f"{camera_id}: model role identity is missing")
        if not str(camera.get("runtime_identity", {}).get("min", "")).strip():
            errors.append(f"{camera_id}: runtime identity is missing")
    if (
        not isinstance(metrics.get("fault_isolation"), dict)
        or metrics["fault_isolation"].get("status") != "passed"
    ):
        errors.append("fault-isolation receipt is required and must pass")
    if not str(metrics.get("runtime_identity", "")).strip():
        errors.append("runtime identity is required")
    if duration_seconds < 0 or required_duration_seconds < 0:
        errors.append("runtime duration is invalid")
    return errors


def run_runtime(
    argv: list[str],
    timeout: float,
    sample_interval: float = 1.0,
    *,
    telemetry_signing_key_file: Path | None = None,
) -> tuple[CommandResult, dict[str, Any]]:
    started = time.monotonic()
    challenge = secrets.token_hex(24)
    binding_host = host_binding()
    signing_key: bytes | None = None
    if telemetry_signing_key_file is not None:
        try:
            signing_key = telemetry_signing_key_file.read_bytes()
        except OSError:
            signing_key = None
    child_env = os.environ.copy()
    child_env.update(
        {
            "SMOKE_GPU_TELEMETRY_CHALLENGE": challenge,
            "SMOKE_GPU_TELEMETRY_HOST_BINDING": binding_host,
        }
    )
    if telemetry_signing_key_file is not None:
        child_env["SMOKE_GPU_TELEMETRY_SIGNING_KEY_FILE"] = str(telemetry_signing_key_file)
    try:
        process = subprocess.Popen(
            argv,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=child_env,
        )
    except FileNotFoundError as exc:
        return CommandResult(tuple(argv), "unavailable", None, 0.0, "", str(exc)), {}
    try:
        start_ticks = process_start_ticks(process.pid)
    except (OSError, IndexError):
        start_ticks = ""
    binding_runtime = runtime_binding(process.pid, start_ticks) if start_ticks else ""
    output: list[str] = []
    error: list[str] = []
    telemetry: list[dict[str, Any]] = []
    deadline = started + timeout
    gpu_samples: list[dict[str, float]] = []
    while process.poll() is None and time.monotonic() < deadline:
        if process.stdout is not None:
            line = process.stdout.readline()
            if line:
                output.append(line)
                try:
                    value = json.loads(line)
                    if isinstance(value, dict) and (
                        TELEMETRY_FIELDS.intersection(value)
                        or value.get("schema_version") == "gpu.telemetry.v1"
                    ):
                        telemetry.append(value)
                except (TypeError, ValueError, json.JSONDecodeError):
                    pass
        sample = run_command(
            [
                "nvidia-smi",
                "--query-gpu=utilization.gpu,utilization.decoder,memory.used,memory.total",
                "--format=csv,noheader,nounits",
            ],
            min(2.0, sample_interval),
        )
        if sample.status == "passed":
            parts = [part.strip() for part in sample.stdout_tail.split(",")]
            if len(parts) >= 4:
                try:
                    gpu_sample = {
                        "timestamp_ns": time.time_ns(),
                        "gpu_utilization": float(parts[0]),
                        "nvdec_utilization": float(parts[1]),
                        "memory_used_mib": float(parts[2]),
                        "memory_total_mib": float(parts[3]),
                    }
                except ValueError:
                    gpu_sample = None
                if gpu_sample is not None:
                    gpu_samples.append(gpu_sample)
        time.sleep(max(0.1, sample_interval))
    timed_out = process.poll() is None
    if timed_out:
        process.send_signal(signal.SIGTERM)
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
    stdout, stderr = process.communicate()
    output.extend(stdout.splitlines(keepends=True))
    error.extend(stderr.splitlines(keepends=True))
    result = CommandResult(
        tuple(argv),
        "timeout" if timed_out else ("passed" if process.returncode == 0 else "failed"),
        process.returncode,
        time.monotonic() - started,
        _tail("".join(output)),
        _tail("".join(error)),
    )
    metrics = parse_telemetry(
        output,
        expected_runtime_pid=process.pid,
        expected_runtime_start_ticks=start_ticks,
        expected_host_binding=binding_host,
        expected_challenge=challenge,
        signing_key=signing_key,
    )
    if gpu_samples:
        metrics["executor_capture"] = {
            "trusted": bool(
                signing_key
                and binding_runtime
                and metrics.get("provenance", {}).get("trusted") is True
            ),
            "source": "qualification-executor:nvidia-smi",
            "host_binding": binding_host,
            "runtime_binding": binding_runtime,
            "samples_sha256": hash_json(gpu_samples),
            "sample_count": len(gpu_samples),
        }
        metrics["gpu_utilization"] = {
            "min": min(row["gpu_utilization"] for row in gpu_samples),
            "max": max(row["gpu_utilization"] for row in gpu_samples),
            "avg": sum(row["gpu_utilization"] for row in gpu_samples) / len(gpu_samples),
        }
        metrics["nvdec_utilization"] = {
            "min": min(row["nvdec_utilization"] for row in gpu_samples),
            "max": max(row["nvdec_utilization"] for row in gpu_samples),
            "avg": sum(row["nvdec_utilization"] for row in gpu_samples) / len(gpu_samples),
        }
    else:
        metrics["executor_capture"] = {
            "trusted": False,
            "source": "qualification-executor:nvidia-smi",
            "host_binding": binding_host,
            "runtime_binding": binding_runtime,
            "samples_sha256": "",
            "sample_count": 0,
        }
    return result, metrics


def run_attested_fault_runtime(
    argv: list[str],
    timeout: float,
    *,
    challenge: str,
    signing_key_file: Path | None,
) -> tuple[CommandResult, dict[str, Any] | None]:
    """Run fault isolation only when the child returns signed bound evidence."""

    key = None
    if signing_key_file is not None:
        try:
            key = signing_key_file.read_bytes()
        except OSError:
            key = None
    env = os.environ.copy()
    env["SMOKE_GPU_TELEMETRY_CHALLENGE"] = challenge
    env["SMOKE_GPU_TELEMETRY_HOST_BINDING"] = host_binding()
    if signing_key_file is not None:
        env["SMOKE_GPU_TELEMETRY_SIGNING_KEY_FILE"] = str(signing_key_file)
    started = time.monotonic()
    try:
        process = subprocess.Popen(
            argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env
        )
    except FileNotFoundError as exc:
        return CommandResult(tuple(argv), "unavailable", None, 0.0, "", str(exc)), None
    try:
        start_ticks = process_start_ticks(process.pid)
    except (OSError, IndexError):
        start_ticks = ""
    try:
        stdout, stderr = process.communicate(timeout=max(0.1, timeout))
        status = "passed" if process.returncode == 0 else "failed"
    except subprocess.TimeoutExpired as exc:
        process.kill()
        stdout, stderr = process.communicate()
        status = "timeout"
        stdout = (exc.stdout or "") + stdout
        stderr = (exc.stderr or "") + stderr
    result = CommandResult(
        tuple(argv),
        status,
        process.returncode,
        time.monotonic() - started,
        _tail(stdout),
        _tail(stderr),
    )
    if status != "passed" or key is None:
        return result, None
    if not start_ticks:
        return result, None
    expected_binding = runtime_binding(process.pid, start_ticks)
    for line in stdout.splitlines():
        try:
            value = json.loads(line)
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
        if not isinstance(value, dict) or value.get("schema_version") != "gpu.fault-attestation.v1":
            continue
        if (
            value.get("status") == "passed"
            and value.get("producer") == "tp-smoke-detect.fault-harness"
            and value.get("qualification_challenge") == challenge
            and value.get("runtime_pid") == process.pid
            and value.get("runtime_start_ticks") == start_ticks
            and value.get("runtime_binding") == expected_binding
            and value.get("host_binding") == env["SMOKE_GPU_TELEMETRY_HOST_BINDING"]
            and isinstance(value.get("signature_hmac_sha256"), str)
            and hmac.compare_digest(value["signature_hmac_sha256"], telemetry_hmac(value, key))
        ):
            return result, value
    return result, None


def build_receipt(args: argparse.Namespace) -> tuple[dict[str, Any], list[CommandResult]]:
    started = datetime.now(UTC)
    all_commands: list[CommandResult] = []
    host, commands = host_probe(args.command_timeout)
    all_commands.extend(commands)
    image, commands = image_probe(
        args.image, pull=args.pull, timeout=args.command_timeout, host=host
    )
    all_commands.extend(commands)
    models = model_probe(args.model_manifest, args.artifact_root)
    replay = replay_probe(
        args.replay_manifest,
        args.streams,
        args.duration_hours,
        args.analysis_fps,
        args.media_root,
    )
    prerequisites = [host, image, models, replay]
    errors = [error for item in prerequisites for error in item.get("errors", [])]
    runtime: dict[str, Any] = {"status": "not-run", "reason": "prerequisites blocked"}
    fault: dict[str, Any] = {"status": "not-run", "reason": "runtime not run"}
    if args.real_runtime and not errors:
        if not args.runtime_command:
            errors.append(
                "--real-runtime requires --runtime-command; "
                "no unreviewed default pipeline is assumed"
            )
        else:
            command_text = args.runtime_command.format(
                image=args.image,
                replay_manifest=str(args.replay_manifest),
                model_manifest=str(args.model_manifest),
            )
            runtime_argv = shlex.split(command_text)
            result, metrics = run_runtime(
                runtime_argv,
                args.runtime_timeout,
                args.sample_interval,
                telemetry_signing_key_file=args.telemetry_signing_key_file,
            )
            all_commands.append(result)
            runtime = {"status": result.status, "command": result.as_dict(), "metrics": metrics}
            if result.status != "passed":
                errors.append(f"real runtime did not complete: {result.status}")
            errors.extend(
                evaluate_telemetry(
                    metrics,
                    streams=args.streams,
                    duration_seconds=result.duration_seconds,
                    analysis_fps=args.analysis_fps,
                    required_duration_seconds=args.duration_hours * 3600,
                    sample_interval=args.sample_interval,
                )
            )
    elif args.real_runtime:
        runtime = {"status": "not-run", "reason": "prerequisites blocked"}
    if args.fault_injection:
        fault = {
            "status": "specified",
            "requested": args.fault_injection,
            "result": "not-run: real runtime prerequisites are not satisfied",
        }
        if args.real_runtime and not errors and args.fault_runtime_command:
            fault_command = args.fault_runtime_command.format(
                image=args.image,
                replay_manifest=str(args.replay_manifest),
                model_manifest=str(args.model_manifest),
                fault_injection=args.fault_injection,
            )
            fault_result, fault_attestation = run_attested_fault_runtime(
                shlex.split(fault_command),
                args.runtime_timeout,
                challenge=secrets.token_hex(24),
                signing_key_file=args.telemetry_signing_key_file,
            )
            all_commands.append(fault_result)
            fault["status"] = "passed" if fault_attestation is not None else "failed"
            fault["result"] = fault_result.as_dict()
            if fault_attestation is not None:
                fault["result"]["attestation"] = fault_attestation.get("signature_hmac_sha256")
            else:
                errors.append("fault-isolation command did not return authenticated bound evidence")
        elif args.real_runtime and not errors:
            fault["result"] = "requires an independently attested fault-runtime command"
            errors.append("fault injection requires --fault-runtime-command; no fault is inferred")
    status = "qualified" if not errors and runtime.get("status") == "passed" else "blocked"
    label = "gpu-lab-qualified" if status == "qualified" and args.streams == 20 else "unqualified"
    receipt: dict[str, Any] = {
        "schema_version": "gpu.qualification-receipt.v1",
        "ticket": "GPU-107",
        "generated_at": started.isoformat(),
        "status": status,
        "qualification_label": label,
        "profile": args.profile,
        "claim_boundary": "RTX 3090 lab only; never production 24-hour qualification",
        "requested_gate": {
            "streams": args.streams,
            "duration_hours": args.duration_hours,
            "analysis_fps": args.analysis_fps,
            "real_runtime": args.real_runtime,
        },
        "host": host,
        "image": image,
        "models": models,
        "replay": replay,
        "runtime": runtime,
        "telemetry": runtime.get(
            "metrics",
            {
                "status": "not-run",
                "sample_count": 0,
                "required_fields": sorted(TELEMETRY_FIELDS),
                "reason": runtime.get("reason", "runtime not run"),
            },
        ),
        "fault_injection": fault,
        "commands": [command.as_dict() for command in all_commands],
        "errors": list(dict.fromkeys(errors)),
        "recovery_steps": [
            (
                "Import the exact digest-pinned DeepStream 7.0 Triton image and retain "
                "its SBOM/vulnerability disposition."
            ),
            (
                "Provide approved local model files and complete model/engine hashes, "
                "licenses, lineage, calibration, and rollback receipts."
            ),
            (
                "Replace the metadata-only replay pack with consented local clips, "
                "source_path values, immutable clip hashes, and qualification_state=sealed."
            ),
            (
                "Run a reviewed runtime command that exercises NVDEC, all required model "
                "roles, Triton, CandidateProcessingService, audit persistence, and muted "
                "shadow audio."
            ),
            (
                "Re-run GPU-107 one-stream, then the 20-stream two-hour gate and explicit "
                "timeout/OOM/stall/mismatch/reset injections."
            ),
        ],
    }
    runtime_identity = runtime.get("metrics", {}).get("runtime_identity")
    host_raw = str(host.get("gpu", {}).get("raw", ""))
    host_fields = [item.strip() for item in host_raw.split(",")]
    host["identity"] = {
        "gpu": host_fields[0] if len(host_fields) > 0 else "",
        "driver": host_fields[2] if len(host_fields) > 2 else "",
        "compute_capability": host_fields[1] if len(host_fields) > 1 else "",
    }
    image_value = str(image.get("image", ""))
    image["digest"] = image_value.split("@sha256:")[-1] if "@sha256:" in image_value else ""
    if isinstance(runtime_identity, str) and runtime_identity:
        runtime["identity"] = {
            "runtime": runtime_identity,
            "deepstream": runtime_identity,
            "tensorrt": runtime_identity,
            "triton": runtime_identity,
        }
    signing_key = None
    if args.receipt_signing_key_file:
        try:
            signing_key = args.receipt_signing_key_file.read_bytes()
        except OSError as exc:
            receipt["errors"].append(f"receipt signing key cannot be read: {exc}")
    if status == "qualified" and args.streams == 1:
        receipt["one_stream"] = build_one_stream_receipt(
            receipt, signing_key=signing_key, signing_key_id=args.receipt_signing_key_id
        )
    return receipt, all_commands


def render_markdown(receipt: dict[str, Any]) -> str:
    lines = [
        "# GPU-107 GPU qualification receipt",
        "",
        f"- Status: **{receipt['status']}**",
        f"- Qualification label: **{receipt['qualification_label']}**",
        f"- Profile: `{receipt['profile']}`",
        f"- Generated: `{receipt['generated_at']}`",
        "",
        (
            "> This receipt is fail-closed. It never treats missing images, metadata-only "
            "models, synthetic replay, skipped commands, or partial probes as a pass."
        ),
        "",
    ]
    for title, key in (
        ("Host", "host"),
        ("Pinned image", "image"),
        ("Models", "models"),
        ("Replay", "replay"),
        ("Runtime", "runtime"),
        ("Telemetry", "telemetry"),
        ("Fault injection", "fault_injection"),
    ):
        lines.extend(
            [
                f"## {title}",
                "",
                "```json",
                json.dumps(receipt[key], indent=2, sort_keys=True),
                "```",
                "",
            ]
        )
    lines.extend(
        [
            "## Errors",
            "",
            *[f"- {error}" for error in receipt["errors"]],
            "",
            "## Recovery",
            "",
            *[f"- {step}" for step in receipt["recovery_steps"]],
            "",
        ]
    )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", default="rtx3090", choices=["rtx3090"])
    parser.add_argument("--image", default=DEFAULT_IMAGE)
    parser.add_argument("--replay-manifest", type=Path, default=DEFAULT_REPLAY)
    parser.add_argument("--model-manifest", type=Path, default=DEFAULT_MODEL_MANIFEST)
    parser.add_argument("--artifact-root", type=Path)
    parser.add_argument(
        "--media-root",
        type=Path,
        help="approved local replay root; source paths must remain below this directory",
    )
    parser.add_argument("--streams", type=int, choices=[1, 4, 8, 20], default=1)
    parser.add_argument("--duration-hours", type=float, default=0.0)
    parser.add_argument("--analysis-fps", type=float, default=10.0)
    parser.add_argument("--command-timeout", type=float, default=30.0)
    parser.add_argument("--runtime-timeout", type=float, default=7200.0)
    parser.add_argument("--sample-interval", type=float, default=1.0)
    parser.add_argument("--pull", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--real-runtime", action="store_true")
    parser.add_argument(
        "--runtime-command",
        help="reviewed command; placeholders: {image}, {replay_manifest}, {model_manifest}",
    )
    parser.add_argument(
        "--fault-injection",
        choices=[
            "stalled-source",
            "triton-timeout",
            "triton-restart",
            "malformed-output",
            "reviewer-oom",
            "engine-mismatch",
            "queue-expiry",
            "gpu-reset",
        ],
    )
    parser.add_argument(
        "--fault-runtime-command",
        help="independently attested fault command; placeholders include {fault_injection}",
    )
    parser.add_argument("--receipt-signing-key-file", type=Path)
    parser.add_argument("--receipt-signing-key-id", default="gpu-qualification")
    parser.add_argument(
        "--telemetry-signing-key-file",
        type=Path,
        help="read-only runtime signer key; separate from the readiness receipt key",
    )
    parser.add_argument("--one-stream-output", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--markdown-output", type=Path)
    args = parser.parse_args()
    if args.duration_hours <= 0:
        args.duration_hours = 2.0 if args.streams == 20 else 1.0
    receipt, _ = build_receipt(args)
    rendered = json.dumps(receipt, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    else:
        sys.stdout.write(rendered)
    if args.markdown_output:
        args.markdown_output.parent.mkdir(parents=True, exist_ok=True)
        args.markdown_output.write_text(render_markdown(receipt), encoding="utf-8")
    one_stream_output = args.one_stream_output
    if one_stream_output is None and args.output is not None:
        one_stream_output = args.output.with_name("one-stream-receipt.json")
    if one_stream_output and isinstance(receipt.get("one_stream"), dict):
        one_stream_output.parent.mkdir(parents=True, exist_ok=True)
        one_stream_output.write_text(
            json.dumps(receipt["one_stream"], indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
    return 0 if receipt["status"] == "qualified" else 1


if __name__ == "__main__":
    raise SystemExit(main())
