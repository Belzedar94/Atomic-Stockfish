#!/usr/bin/env python3
"""Run the Atomic runtime contract under sanitizers or Valgrind.

The executable is expected to have been built with the requested sanitizer by
the caller. This script supplies deterministic Atomic/Atomic960, NNUE and
multi-threaded protocol workloads and treats every diagnostic as a failure.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_NET = REPO_ROOT.parent / "atomic_run3b_e202_l05.nnue"
BAD_MARKERS = (
    "AddressSanitizer",
    "UndefinedBehaviorSanitizer",
    "ThreadSanitizer",
    "runtime error:",
    "Assertion failed",
    "ERROR SUMMARY: 1",
    "ERROR SUMMARY: 2",
    "ERROR SUMMARY: 3",
)


def prefix(args: argparse.Namespace) -> list[str]:
    if args.valgrind:
        return [
            "valgrind",
            "--error-exitcode=99",
            "--leak-check=full",
            "--errors-for-leak-kinds=definite,indirect,possible",
        ]
    if args.valgrind_thread:
        return ["valgrind", "--tool=helgrind", "--error-exitcode=99"]
    return []


def run_protocol(
    command: list[str], transcript: str, timeout: float, environment: dict[str, str]
) -> str:
    completed = subprocess.run(
        command,
        input=transcript,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        env=environment,
        check=False,
    )
    if completed.returncode != 0:
        raise AssertionError(
            f"instrumented process exited {completed.returncode}: {' '.join(command)}\n"
            f"{completed.stdout}"
        )
    for marker in BAD_MARKERS:
        if marker in completed.stdout:
            raise AssertionError(f"instrumentation diagnostic {marker!r}:\n{completed.stdout}")
    return completed.stdout


def uci_transcript(net: Path, threads: int) -> str:
    return "\n".join(
        (
            "uci",
            f"setoption name Threads value {threads}",
            "setoption name Hash value 16",
            "setoption name Use NNUE value false",
            "isready",
            "position startpos",
            "go depth 3",
            "position fen 7k/8/8/3pP3/8/8/8/K7 w - d6 0 1",
            "go perft 3",
            "position fen 6rk/8/8/8/8/8/8/K5R1 w - - 0 1",
            "go depth 2 searchmoves g1g8",
            "setoption name UCI_Chess960 value true",
            "position fen Rr2k1rR/3K4/3p4/8/8/8/7P/8 w kq - 0 1",
            "go perft 2",
            "setoption name UCI_Chess960 value false",
            f"setoption name EvalFile value {net}",
            "setoption name Use NNUE value true",
            "position startpos",
            "go depth 3",
            "setoption name Use NNUE value pure",
            "position startpos moves e2e4 e7e6",
            "go depth 3",
            "quit",
            "",
        )
    )


def xboard_transcript() -> str:
    return "\n".join(
        (
            "xboard",
            "protover 2",
            "option Use NNUE=false",
            "new",
            "force",
            "usermove e2e4",
            "undo",
            "setboard 6rk/8/8/8/8/8/8/K5R1 w - - 0 1",
            "go",
            "ping 17",
            "quit",
            "",
        )
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--valgrind", action="store_true")
    modes.add_argument("--valgrind-thread", action="store_true")
    modes.add_argument("--sanitizer-undefined", action="store_true")
    modes.add_argument("--sanitizer-thread", action="store_true")
    modes.add_argument("--none", action="store_true")
    parser.add_argument("stockfish_path", type=Path)
    parser.add_argument("--eval-file", type=Path, default=DEFAULT_NET)
    parser.add_argument("--timeout", type=float, default=180.0)
    args = parser.parse_args()

    executable = args.stockfish_path.resolve()
    net = args.eval_file.resolve()
    if not executable.is_file() or not net.is_file():
        parser.error("engine and frozen Atomic NNUE must exist")

    command_prefix = prefix(args)
    if command_prefix and shutil.which(command_prefix[0]) is None:
        parser.error(f"required tool is unavailable: {command_prefix[0]}")

    environment = os.environ.copy()
    environment.setdefault("ASAN_OPTIONS", "detect_leaks=1:halt_on_error=1")
    environment.setdefault("UBSAN_OPTIONS", "halt_on_error=1:print_stacktrace=1")
    environment.setdefault("TSAN_OPTIONS", "halt_on_error=1")
    command = [*command_prefix, str(executable)]
    threads = 2 if args.valgrind_thread or args.sanitizer_thread else 1

    uci_output = run_protocol(
        command, uci_transcript(net, threads), args.timeout, environment
    )
    required = ("uciok", "readyok", "bestmove g1g8", "NNUE evaluation using")
    for marker in required:
        if marker not in uci_output:
            raise AssertionError(f"missing UCI marker {marker!r}:\n{uci_output}")

    xboard_output = run_protocol(command, xboard_transcript(), args.timeout, environment)
    for marker in ('variants="atomic"', "pong 17"):
        if marker not in xboard_output:
            raise AssertionError(f"missing XBoard marker {marker!r}:\n{xboard_output}")

    print(f"Atomic instrumented runtime passed: threads={threads}, prefix={command_prefix}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
