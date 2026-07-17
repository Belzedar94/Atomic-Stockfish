#!/usr/bin/env python3
"""Focused AtomicNNUEV3 ``Use NNUE=pure`` data-generator gate."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys
from typing import Sequence

from atomic_v2_generator_smoke import SmokeError, run_smoke


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--generator", required=True, type=Path)
    parser.add_argument("--net", required=True, type=Path)
    parser.add_argument("--expected-net-sha256", required=True)
    parser.add_argument("--timeout-seconds", type=float, default=180.0)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        sentinel = run_smoke(
            args.generator,
            args.net,
            args.expected_net_sha256,
            timeout=args.timeout_seconds,
            backend="AtomicNNUEV3",
        )
    except SmokeError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    print(sentinel)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
