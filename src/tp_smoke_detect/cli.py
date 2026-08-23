"""Small, dependency-light command line entry points used by agents and CI."""

from __future__ import annotations

import argparse
from pathlib import Path

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
        print(f"CPU fixture ready: {args.fixture}")
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
