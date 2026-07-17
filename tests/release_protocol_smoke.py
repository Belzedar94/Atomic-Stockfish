#!/usr/bin/env python3
"""Minimal network-free UCI and XBoard smoke for packaged release binaries."""

from __future__ import annotations

import argparse
from pathlib import Path
import subprocess


def run_engine(engine: Path, protocol: str, commands: str, timeout: float) -> str:
    completed = subprocess.run(
        [str(engine)],
        input=commands,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        timeout=timeout,
        check=False,
    )
    transcript = completed.stdout + completed.stderr
    if completed.returncode != 0:
        raise AssertionError(
            f"{protocol} release smoke exited {completed.returncode}:\n{transcript}"
        )
    return transcript


def require(transcript: str, marker: str, protocol: str) -> None:
    if marker not in transcript:
        raise AssertionError(f"{protocol} omitted {marker!r}:\n{transcript}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--engine", type=Path, required=True)
    parser.add_argument("--version", default="")
    parser.add_argument("--timeout", type=float, default=45.0)
    args = parser.parse_args()

    engine = args.engine.resolve(strict=True)
    expected_name = "Atomic-Stockfish" + (f" {args.version}" if args.version else "")

    uci = run_engine(
        engine,
        "UCI",
        "\n".join(
            (
                "uci",
                "setoption name UCI_Variant value atomic",
                "setoption name Use NNUE value false",
                "isready",
                "ucinewgame",
                "position startpos",
                "go depth 2",
                "quit",
                "",
            )
        ),
        args.timeout,
    )
    require(uci, "id name " + expected_name, "UCI")
    require(
        uci,
        "id author the Atomic-Stockfish developers (see AUTHORS file)",
        "UCI",
    )
    require(uci, "option name UCI_Variant type combo default atomic var atomic", "UCI")
    require(uci, "option name Use NNUE type combo", "UCI")
    require(uci, "readyok", "UCI")
    require(uci, "bestmove ", "UCI")

    xboard = run_engine(
        engine,
        "XBoard",
        "xboard\nprotover 2\nping 1701\nquit\n",
        args.timeout,
    )
    if args.version:
        require(xboard, 'myname="' + expected_name + '"', "XBoard")
    else:
        require(xboard, 'myname="Atomic-Stockfish ', "XBoard")
    require(xboard, 'variants="atomic"', "XBoard")
    require(xboard, "pong 1701", "XBoard")

    print(f"release protocol smoke passed: {expected_name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
