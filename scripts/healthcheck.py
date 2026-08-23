#!/usr/bin/env python3
"""Small dependency-free container health probe.

The probe intentionally checks one endpoint only.  Readiness is a separate
operator decision because a degraded camera should not make the API process
unhealthy and cause an orchestrator restart loop.
"""

from __future__ import annotations

import argparse
import json
import sys
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:8000/health/live")
    parser.add_argument("--timeout", type=float, default=3.0)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    request = Request(args.url, headers={"Accept": "application/json"})
    try:
        with urlopen(request, timeout=args.timeout) as response:  # noqa: S310 - configured local URL
            if response.status != 200:
                print(f"health probe failed: HTTP {response.status}", file=sys.stderr)
                return 1
            payload = json.loads(response.read())
    except (HTTPError, URLError, TimeoutError, ValueError, OSError) as exc:
        print(f"health probe failed: {exc}", file=sys.stderr)
        return 1
    if not isinstance(payload, dict) or payload.get("status") != "ok":
        print(f"health probe returned unexpected payload: {payload!r}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
