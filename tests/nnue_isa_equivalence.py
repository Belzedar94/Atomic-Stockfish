#!/usr/bin/env python3
"""Compare deterministic LegacyAtomicV1 accumulator state across native ISAs."""

from __future__ import annotations

import argparse
import pathlib
import re
import subprocess
import sys


SIGNATURE_RE = re.compile(
    r"^LegacyAtomicV1 incremental gate passed:.* state-signature=0x([0-9A-F]+)\s*$",
    re.MULTILINE,
)


def binary_argument(value: str) -> tuple[str, pathlib.Path]:
    label, separator, path = value.partition("=")
    if not separator or not label or not path:
        raise argparse.ArgumentTypeError("--binary must be LABEL=PATH")
    return label, pathlib.Path(path).resolve()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Require byte-exact LegacyAtomicV1 accumulator signatures across ISAs"
    )
    parser.add_argument(
        "--binary",
        action="append",
        required=True,
        type=binary_argument,
        metavar="LABEL=PATH",
        help="incremental-test executable built for one ISA (repeat at least twice)",
    )
    parser.add_argument("--net", required=True, type=pathlib.Path)
    parser.add_argument("--operations", type=int, default=4096)
    parser.add_argument("--timeout", type=float, default=300.0)
    args = parser.parse_args()

    if len(args.binary) < 2:
        parser.error("at least two --binary arguments are required")
    if args.operations <= 0 or args.operations % 8:
        parser.error("--operations must be a positive multiple of 8")

    net = args.net.resolve()
    if not net.is_file():
        parser.error(f"network does not exist: {net}")

    signatures: dict[str, str] = {}
    for label, binary in args.binary:
        if label in signatures:
            parser.error(f"duplicate binary label: {label}")
        if not binary.is_file():
            parser.error(f"binary does not exist: {binary}")

        command = [
            str(binary),
            "--net",
            str(net),
            "--mode",
            "smoke",
            "--operations",
            str(args.operations),
            "--full-refresh-interval",
            "1",
        ]
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=args.timeout,
            check=False,
        )
        if completed.returncode:
            sys.stderr.write(completed.stdout)
            sys.stderr.write(completed.stderr)
            raise SystemExit(f"{label} failed with exit code {completed.returncode}")

        match = SIGNATURE_RE.search(completed.stdout)
        if not match:
            sys.stderr.write(completed.stdout)
            raise SystemExit(f"{label} did not emit the required state signature")

        signatures[label] = match.group(1)
        print(f"PASS {label}: state-signature=0x{signatures[label]}")

    reference_label, reference_signature = next(iter(signatures.items()))
    mismatches = {
        label: signature
        for label, signature in signatures.items()
        if signature != reference_signature
    }
    if mismatches:
        details = ", ".join(f"{label}=0x{signature}" for label, signature in mismatches.items())
        raise SystemExit(
            f"ISA accumulator mismatch: {reference_label}=0x{reference_signature}; {details}"
        )

    print(
        "LegacyAtomicV1 ISA equivalence passed: "
        f"implementations={len(signatures)} operations={args.operations} "
        f"state-signature=0x{reference_signature}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
