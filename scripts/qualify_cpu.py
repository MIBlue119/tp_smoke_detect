#!/usr/bin/env python3
"""Run the deterministic CPU end-to-end gate and write a qualification receipt.

The receipt is deliberately honest: this command proves replay/domain/audit/
policy/review behavior only.  It records GPU, 20-camera, 24-hour, broker,
PostgreSQL, model-quality, and external-audio gates as unrun unless the
corresponding independent qualification has been performed.
"""

from __future__ import annotations

import argparse
import subprocess
from datetime import UTC, datetime
from pathlib import Path

UNRUN_GATES = (
    "GPU/DeepStream/Triton one-stream qualification",
    "20-camera 1080p capacity and p95 latency",
    "24-hour soak and restart/fault-isolation run",
    "PostgreSQL and external MQTT deployment smoke test",
    "Model provenance, sealed accuracy, and confusion-class evaluation",
    "Physical/site audio adapter and automatic-mode approval",
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="receipt path (default: docs/dev_artifacts/qualification/<date>-int-701-cpu.md)",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="run the CPU gate and print the receipt without writing a file",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    output = args.output or Path(
        f"docs/dev_artifacts/qualification/{datetime.now(UTC).date().isoformat()}-int-701-cpu.md"
    )
    # Keep the receipt reproducible across checkouts.  The uv entry point
    # selects the locked project environment without leaking this host's
    # absolute .venv path into a durable artifact.
    command = ["uv", "run", "pytest", "tests/e2e", "-q"]
    completed = subprocess.run(command, check=False, capture_output=True, text=True)
    status = "PASS" if completed.returncode == 0 else "FAIL"
    lines = [
        "# INT-701 CPU qualification receipt",
        "",
        f"Generated (UTC): {datetime.now(UTC).isoformat()}",
        f"CPU end-to-end gate: **{status}**",
        "",
        "## Executed",
        "",
        f"`{' '.join(command)}`",
        "",
        "```text",
        completed.stdout.rstrip(),
        completed.stderr.rstrip(),
        "```",
        "",
        "The executed gate covers synthetic replay, metadata-only bus delivery, "
        "deterministic cascade decision, SQLite append-only audit, shadow-mode "
        "`would_announce`, fake-audio automatic policy, and operator review.",
        "",
        "## Explicitly unrun gates",
        "",
    ]
    lines.extend(f"- UNRUN: {item}" for item in UNRUN_GATES)
    lines.extend(
        [
            "",
            "## Conclusion",
            "",
            "This is an MVP-0 CPU/reference receipt. It is not evidence of "
            "20-camera throughput, model quality, GPU compatibility, 24-hour "
            "availability, or automatic public audio approval.",
            "",
        ]
    )
    receipt = "\n".join(lines)
    if args.check:
        print(receipt)
    else:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(receipt, encoding="utf-8")
    print(output)
    return completed.returncode


if __name__ == "__main__":
    raise SystemExit(main())
