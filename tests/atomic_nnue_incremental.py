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
import re
import subprocess
import sys
from pathlib import Path


FROZEN_NET_SHA256 = "99dc67eabf26a64faeeca3a88b4c38597a840b8d4a874b9f2cf658c6f92a04a6"
DEFAULT_PROFILES = {
    "smoke": (4_096, 1, "DDB8196C6A0BE4A8"),
    "release": (1_000_000, 1_024, "8742E39B793C46AB"),
}
FINAL_SENTINEL_RE = re.compile(
    r"^LegacyAtomicV1 incremental gate passed: "
    r"mode=(?P<mode>smoke|release) "
    r"requested-random-operations=(?P<requested>\d+) "
    r"actual-random-operations=(?P<actual>\d+) "
    r"makes=(?P<makes>\d+) "
    r"undos=(?P<undos>\d+) "
    r"captures=(?P<captures>\d+) "
    r"capture-forced-refresh=(?P<forced_refresh>\d+) "
    r"perspective-refresh-white=(?P<refresh_white>\d+) "
    r"perspective-refresh-black=(?P<refresh_black>\d+) "
    r"full-refresh-comparisons=(?P<full_refresh>\d+) "
    r"state-signature=0x(?P<signature>[0-9A-F]{1,16})$"
)


class GateOutputError(ValueError):
    """The child exited successfully without proving the requested gate."""


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def validate_gate_output(
    output: str,
    *,
    mode: str,
    operations: int,
    full_refresh_interval: int,
) -> str:
    """Validate the final C++ sentinel and return its state signature."""

    lines = [line.strip() for line in output.splitlines() if line.strip()]
    if not lines:
        raise GateOutputError("child emitted no output")

    match = FINAL_SENTINEL_RE.fullmatch(lines[-1])
    if match is None:
        raise GateOutputError(
            "final non-empty line is not the LegacyAtomicV1 success sentinel: "
            f"{lines[-1]!r}"
        )

    child_mode = match.group("mode")
    requested = int(match.group("requested"))
    actual = int(match.group("actual"))
    makes = int(match.group("makes"))
    undos = int(match.group("undos"))
    forced_refresh = int(match.group("forced_refresh"))
    signature = match.group("signature")

    if child_mode != mode:
        raise GateOutputError(f"child reported mode={child_mode}, expected mode={mode}")
    if requested != operations or actual != operations:
        raise GateOutputError(
            "child operation count mismatch: "
            f"requested={requested} actual={actual} expected={operations}"
        )
    if makes + undos != actual:
        raise GateOutputError(
            f"child operation accounting mismatch: makes={makes} undos={undos} actual={actual}"
        )
    if forced_refresh != 0:
        raise GateOutputError(
            f"child reported capture-forced-refresh={forced_refresh}, expected 0"
        )

    default_operations, default_interval, default_signature = DEFAULT_PROFILES[mode]
    if operations == default_operations and full_refresh_interval == default_interval:
        if signature != default_signature:
            raise GateOutputError(
                f"default {mode} signature mismatch: "
                f"expected=0x{default_signature} actual=0x{signature}"
            )

    return signature


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

    default_operations, default_interval, _ = DEFAULT_PROFILES[args.mode]
    effective_operations = (
        args.operations if args.operations is not None else default_operations
    )
    effective_interval = (
        args.full_refresh_interval
        if args.full_refresh_interval is not None
        else default_interval
    )

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

    output = completed.stdout or ""
    if output:
        print(output, end="" if output.endswith("\n") else "\n")
    if completed.returncode:
        return completed.returncode

    try:
        validate_gate_output(
            output,
            mode=args.mode,
            operations=effective_operations,
            full_refresh_interval=effective_interval,
        )
    except GateOutputError as error:
        print(f"incremental gate output validation failed: {error}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
