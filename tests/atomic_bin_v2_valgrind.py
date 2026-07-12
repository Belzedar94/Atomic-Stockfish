#!/usr/bin/env python3
"""Exercise one real Atomic BIN V2 generator transaction under Valgrind."""

from __future__ import annotations

import argparse
import hashlib
from pathlib import Path
import shutil
import subprocess
import tempfile

from data_generator import (
    REPLAY_SEED,
    assert_atomic_bin_v2_dataset,
    assert_atomic_bin_v2_manifest,
    generation_command,
    require_file,
    setup_commands,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--generator", required=True, type=Path)
    parser.add_argument("--net", required=True, type=Path)
    parser.add_argument("--expected-net-sha256", required=True)
    parser.add_argument("--timeout", type=float, default=600.0)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    generator = require_file(args.generator, "data-generator binary")
    net = require_file(args.net, "Legacy Atomic V1 network")
    valgrind = shutil.which("valgrind")
    if valgrind is None:
        raise AssertionError("Valgrind is unavailable")

    net_sha256 = hashlib.sha256(net.read_bytes()).hexdigest().upper()
    if net_sha256 != args.expected_net_sha256.upper():
        raise AssertionError(
            "network SHA-256 mismatch: "
            f"expected {args.expected_net_sha256.upper()}, got {net_sha256}"
        )

    with tempfile.TemporaryDirectory(prefix="atomic-v2-valgrind-") as raw_root:
        root = Path(raw_root).resolve()
        output = root / "valgrind.atbin"
        commands = (
            *setup_commands(net),
            generation_command(
                output,
                records=1,
                data_format="atomic-bin-v2",
                seed=REPLAY_SEED,
            ),
            "quit",
        )
        completed = subprocess.run(
            [
                valgrind,
                "--error-exitcode=99",
                "--leak-check=full",
                "--show-leak-kinds=all",
                "--errors-for-leak-kinds=all",
                "--track-origins=yes",
                "--num-callers=30",
                str(generator),
            ],
            input="\n".join((*commands, "")),
            text=True,
            encoding="utf-8",
            errors="replace",
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=args.timeout,
            check=False,
        )
        if completed.returncode != 0:
            raise AssertionError(
                f"Valgrind generator exited {completed.returncode}:\n{completed.stdout}"
            )
        required = (
            "readyok",
            "INFO: generate_training_data finished.",
            "ERROR SUMMARY: 0 errors",
        )
        for marker in required:
            if marker not in completed.stdout:
                raise AssertionError(
                    f"Valgrind generator omitted {marker!r}:\n{completed.stdout}"
                )

        data = assert_atomic_bin_v2_dataset(output, 1, atomic960=False)
        manifest = Path(str(output) + ".manifest.json")
        assert_atomic_bin_v2_manifest(
            manifest,
            ((output, data, 1),),
            atomic960=False,
            net=net,
            net_sha256=net_sha256,
            root=root,
        )

    print("Atomic BIN V2 generator Valgrind gate passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
