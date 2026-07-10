#!/usr/bin/env python3
"""Run the LegacyAtomicV1 incremental-accumulator gate with the frozen net.

The wrapper owns the artifact-integrity check so the C++ gate can concentrate
on accumulator correctness.  It refuses to execute with a renamed or modified
network, then passes the absolute path to the test binary.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import subprocess
import sys
from pathlib import Path


FROZEN_NET_SHA256 = "99dc67eabf26a64faeeca3a88b4c38597a840b8d4a874b9f2cf658c6f92a04a6"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Verify LegacyAtomicV1 incremental updates against full refreshes"
    )
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--net", type=Path, required=True)
    parser.add_argument("--mode", choices=("smoke", "release"), default="smoke")
    parser.add_argument("--operations", type=int)
    parser.add_argument("--full-refresh-interval", type=int)
    parser.add_argument("--timeout", type=float)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    binary = args.binary.expanduser().resolve()
    net = args.net.expanduser().resolve()

    if not binary.is_file():
        raise SystemExit(f"incremental test binary not found: {binary}")
    if not net.is_file():
        raise SystemExit(f"LegacyAtomicV1 network not found: {net}")

    actual_sha = sha256(net)
    if actual_sha != FROZEN_NET_SHA256:
        raise SystemExit(
            "LegacyAtomicV1 network SHA-256 mismatch:\n"
            f"  expected: {FROZEN_NET_SHA256.upper()}\n"
            f"  actual:   {actual_sha.upper()}\n"
            f"  file:     {net}"
        )

    command = [str(binary), "--net", str(net), "--mode", args.mode]
    if args.operations is not None:
        if args.operations <= 0:
            raise SystemExit("--operations must be positive")
        command.extend(("--operations", str(args.operations)))
    if args.full_refresh_interval is not None:
        if args.full_refresh_interval <= 0:
            raise SystemExit("--full-refresh-interval must be positive")
        command.extend(("--full-refresh-interval", str(args.full_refresh_interval)))

    timeout = args.timeout if args.timeout is not None else (7200.0 if args.mode == "release" else 300.0)
    startup: dict[str, int] = {}
    if os.name == "nt":
        startup["creationflags"] = subprocess.CREATE_NO_WINDOW

    print(
        "LegacyAtomicV1 frozen network verified: "
        f"SHA-256={actual_sha.upper()} mode={args.mode}",
        flush=True,
    )
    try:
        completed = subprocess.run(
            command,
            timeout=timeout,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            **startup,
        )
    except subprocess.TimeoutExpired as error:
        raise SystemExit(f"incremental gate timed out after {timeout:g}s") from error
    except OSError as error:
        raise SystemExit(f"could not start incremental gate: {error}") from error

    if completed.stdout:
        print(completed.stdout, end="" if completed.stdout.endswith("\n") else "\n")
    return completed.returncode


if __name__ == "__main__":
    sys.exit(main())
