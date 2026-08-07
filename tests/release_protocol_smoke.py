#!/usr/bin/env python3
"""Minimal network-free UCI and XBoard smoke for packaged release binaries."""

from __future__ import annotations

import argparse
from pathlib import Path
import re
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


OPTION_FIXTURE = Path(__file__).resolve().parent / "fixtures" / "uci_options.txt"
SPSA_LINE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*,-?\d+,-?\d+,-?\d+,")


def advertised_options(transcript: str) -> list[str]:
    prefix = "option name "
    return [
        line[len(prefix) :].split(" type ", 1)[0]
        for line in transcript.splitlines()
        if line.startswith(prefix)
    ]


def require_option_surface(transcript: str, protocol: str) -> None:
    """Assert the option list is exactly the recorded one.

    The individual ``require`` calls above only prove that specific options are
    still present. They cannot see an option that should not be there, which is
    how a development build started advertising thirteen search parameters as
    UCI spin options without any gate objecting. A release binary's option list
    is a public contract, so compare it as a closed set.
    """
    expected = sorted(
        line.strip()
        for line in OPTION_FIXTURE.read_text(encoding="utf-8").splitlines()
        if line.strip()
    )
    found = sorted(advertised_options(transcript))
    if found != expected:
        unexpected = sorted(set(found) - set(expected))
        missing = sorted(set(expected) - set(found))
        raise AssertionError(
            f"{protocol} option list does not match {OPTION_FIXTURE.name}; "
            f"unexpected={unexpected} missing={missing}"
        )

    # `make tune=yes` also prints the SPSA input block on stdout before uciok.
    # No released binary may be built that way.
    leaked = [line for line in transcript.splitlines() if SPSA_LINE.match(line)]
    if leaked:
        raise AssertionError(
            f"{protocol} printed an SPSA parameter block, so this binary was "
            f"built with ATOMIC_TUNE_SEARCH: {leaked}"
        )


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
    require_option_surface(uci, "UCI")

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
