"""Small, dependency-light command line entry points used by agents and CI."""

from __future__ import annotations

import argparse
from pathlib import Path
from tempfile import TemporaryDirectory

from .adapters.artifacts.local import LocalArtifactStore
from .adapters.messaging.in_memory import InMemoryMessageBus
from .application.replay import ReplayWorker, synthetic_camera, synthetic_manifest
from .schema import write_schemas
from .settings import load_settings


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="smoke-detect")
    subparsers = parser.add_subparsers(dest="command", required=True)

    schema = subparsers.add_parser("schema", help="write canonical v1 JSON schemas")
    schema.add_argument("--output", type=Path, default=Path("schemas/smoke/v1"))

    validate = subparsers.add_parser("validate-config", help="validate an example YAML file")
    validate.add_argument("path", type=Path)

    demo = subparsers.add_parser("demo", help="CPU reference path placeholder")
    demo.add_argument("--fixture", choices=["synthetic"], default="synthetic")

    return parser


def main() -> int:
    args = build_parser().parse_args()
    if args.command == "schema":
        for path in write_schemas(args.output):
            print(path)
        return 0
    if args.command == "validate-config":
        settings = load_settings(args.path)
        print(f"validated {settings.service_name} ({len(settings.cameras)} cameras)")
        return 0
    if args.command == "demo":
        if args.fixture == "synthetic":
            with TemporaryDirectory(prefix="smoke-detect-replay-") as directory:
                root = Path(directory)
                fixture = root / "synthetic"
                fixture.mkdir()
                # Minimal valid PNG signatures are sufficient for the replay
                # adapter; pixel decoding belongs to the media worker.
                png = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR" + (b"\x00" * 17)
                for name in ("frame-000.png", "frame-005.png"):
                    (fixture / name).write_bytes(png)
                store = LocalArtifactStore(root)
                bus = InMemoryMessageBus()
                result = ReplayWorker(synthetic_camera(), store, bus).replay(synthetic_manifest())
                print(
                    f"CPU fixture ready: {args.fixture} "
                    f"candidates={len(result.candidates)} errors={len(result.errors)}"
                )
                return 0 if result.ok else 1
        print(f"CPU fixture ready: {args.fixture}")
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
