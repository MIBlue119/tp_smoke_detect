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
import json
import re
import shlex
import signal
import subprocess
import sys
import time
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml
from ml.registry.model_repository import ModelRepositoryManifest, verify_local_artifact

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
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        errors.append(f"model manifest unavailable: {exc}")
    result["errors"] = list(dict.fromkeys(errors))
    result["status"] = "ready" if not errors else "blocked"
    return result


def replay_probe(
    path: Path, streams: int, duration_hours: float, analysis_fps: float
) -> dict[str, Any]:
    result: dict[str, Any] = {"path": str(path), "status": "blocked", "manifest_sha256": None}
    errors: list[str] = []
    try:
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
            elif not Path(source).is_file():
                errors.append(f"{stream_id}: local source_path is not readable")
            if float(row.get("analysis_fps", 0)) < analysis_fps:
                errors.append(f"{stream_id}: analysis_fps is below requested {analysis_fps}")
    except (OSError, TypeError, ValueError, yaml.YAMLError) as exc:
        errors.append(f"replay manifest unavailable: {exc}")
    result["errors"] = list(dict.fromkeys(errors))
    result["status"] = "ready" if not errors else "blocked"
    return result


def parse_telemetry(lines: Iterable[str]) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    for line in lines:
        try:
            value = json.loads(line)
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
        if isinstance(value, dict) and TELEMETRY_FIELDS.intersection(value):
            rows.append(value)
    summary: dict[str, Any] = {
        "samples": len(rows),
        "fields": sorted({key for row in rows for key in row}),
    }
    for field in sorted(TELEMETRY_FIELDS):
        values = [float(row[field]) for row in rows if isinstance(row.get(field), (int, float))]
        if values:
            summary[field] = {
                "min": min(values),
                "max": max(values),
                "avg": sum(values) / len(values),
            }
    return summary


def run_runtime(
    argv: list[str], timeout: float, sample_interval: float = 1.0
) -> tuple[CommandResult, dict[str, Any]]:
    started = time.monotonic()
    try:
        process = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    except FileNotFoundError as exc:
        return CommandResult(tuple(argv), "unavailable", None, 0.0, "", str(exc)), {}
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
                    if isinstance(value, dict) and TELEMETRY_FIELDS.intersection(value):
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
    metrics = parse_telemetry(output)
    if gpu_samples:
        metrics["gpu_samples"] = gpu_samples
    return result, metrics


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
        args.replay_manifest, args.streams, args.duration_hours, args.analysis_fps
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
            result, metrics = run_runtime(runtime_argv, args.runtime_timeout, args.sample_interval)
            all_commands.append(result)
            runtime = {"status": result.status, "command": result.as_dict(), "metrics": metrics}
            required = {
                "scheduled_samples",
                "processed_samples",
                "dropped_samples",
                "candidate_latency_ms",
            }
            missing = sorted(required - set(metrics.get("fields", [])))
            if result.status != "passed":
                errors.append(f"real runtime did not complete: {result.status}")
            if missing:
                errors.append(f"real runtime telemetry is incomplete: missing {', '.join(missing)}")
    elif args.real_runtime:
        runtime = {"status": "not-run", "reason": "prerequisites blocked"}
    if args.fault_injection:
        fault = {
            "status": "specified",
            "requested": args.fault_injection,
            "result": "not-run: real runtime prerequisites are not satisfied",
        }
        if args.real_runtime and not errors and args.runtime_command:
            fault["result"] = "requires a runtime-specific injection command; no fault is inferred"
            errors.append(
                "fault injection was requested but no reviewed injection command was supplied"
            )
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
    parser.add_argument("--output", type=Path)
    parser.add_argument("--markdown-output", type=Path)
    args = parser.parse_args()
    if args.duration_hours <= 0:
        args.duration_hours = 2.0 if args.streams == 20 else 0.0
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
    return 0 if receipt["status"] == "qualified" else 1


if __name__ == "__main__":
    raise SystemExit(main())
