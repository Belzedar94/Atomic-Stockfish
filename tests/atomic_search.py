#!/usr/bin/env python3
"""Deterministic black-box regressions for Atomic terminal captures in search.

The first fixture protects against move-count, futility, and SEE pruning hiding
an explosive mate when it is the third capture considered in qsearch. The
remaining fixtures cover every terminal-capture encoding accepted by
``Position::atomic_wins``: direct king capture, adjacent by-catch, en passant,
and capture-promotion.
"""

from __future__ import annotations

import argparse
import queue
import re
import subprocess
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Callable


REPO_ROOT = Path(__file__).resolve().parents[1]
SCORE_RE = re.compile(r"\bscore mate (-?\d+)\b")
PV_RE = re.compile(r"\bpv(?:\s+(.+))?$")


@dataclass(frozen=True)
class SearchCase:
    name: str
    fen: str
    searchmove: str
    expected_mate: int
    expected_pv: tuple[str, ...]


SEARCH_CASES = (
    SearchCase(
        name="third qsearch capture is explosive mate",
        fen="4K1R1/RR6/8/8/8/8/qqq3p1/7k b - - 0 1",
        searchmove="c2c3",
        expected_mate=-1,
        expected_pv=("c2c3", "g8g2"),
    ),
    SearchCase(
        name="direct king capture",
        fen="7k/7R/8/8/8/8/8/K7 w - - 0 1",
        searchmove="h7h8",
        expected_mate=1,
        expected_pv=("h7h8",),
    ),
    SearchCase(
        name="adjacent king bycatch",
        fen="6rk/8/8/8/8/8/8/K5R1 w - - 0 1",
        searchmove="g1g8",
        expected_mate=1,
        expected_pv=("g1g8",),
    ),
    SearchCase(
        name="en-passant king bycatch",
        fen="8/2k5/8/3pP3/8/8/8/K7 w - d6 0 1",
        searchmove="e5d6",
        expected_mate=1,
        expected_pv=("e5d6",),
    ),
    SearchCase(
        name="capture-promotion king bycatch",
        fen="6kr/6P1/8/8/8/8/8/K7 w - - 0 1",
        searchmove="g7h8q",
        expected_mate=1,
        expected_pv=("g7h8q",),
    ),
)


class UciProcess:
    def __init__(
        self,
        executable: Path,
        timeout: float,
        eval_file: Path | None = None,
        use_nnue: str | None = None,
    ) -> None:
        self.executable = executable
        self.timeout = timeout
        self.process = subprocess.Popen(
            [str(executable)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
        )
        assert self.process.stdin is not None
        assert self.process.stdout is not None
        self.lines: queue.Queue[str | None] = queue.Queue()
        self.reader = threading.Thread(target=self._read_output, daemon=True)
        self.reader.start()

        self.send("uci")
        self.read_until(lambda line: line == "uciok")
        self.send("setoption name Threads value 1")
        self.send("setoption name Hash value 16")
        if eval_file is not None:
            self.send(f"setoption name EvalFile value {eval_file}")
        if use_nnue is not None:
            self.send(f"setoption name Use NNUE value {use_nnue}")
        self.ready()

    def _read_output(self) -> None:
        assert self.process.stdout is not None
        for line in self.process.stdout:
            self.lines.put(line.rstrip("\r\n"))
        self.lines.put(None)

    def send(self, command: str) -> None:
        if self.process.poll() is not None:
            raise RuntimeError(f"{self.executable} exited with {self.process.returncode}")
        assert self.process.stdin is not None
        self.process.stdin.write(command + "\n")
        self.process.stdin.flush()

    def read_until(self, predicate: Callable[[str], bool]) -> list[str]:
        output: list[str] = []
        while True:
            try:
                line = self.lines.get(timeout=self.timeout)
            except queue.Empty as exc:
                raise TimeoutError(
                    f"Timed out waiting for {self.executable}; last output: {output[-8:]}"
                ) from exc
            if line is None:
                raise RuntimeError(
                    f"{self.executable} exited with {self.process.poll()}; "
                    f"last output: {output[-8:]}"
                )
            output.append(line)
            if predicate(line):
                return output

    def ready(self) -> None:
        self.send("isready")
        self.read_until(lambda line: line == "readyok")

    def search(self, test: SearchCase) -> list[str]:
        self.send("ucinewgame")
        self.ready()
        self.send(f"position fen {test.fen}")
        self.send(f"go depth 1 searchmoves {test.searchmove}")
        return self.read_until(lambda line: line.startswith("bestmove "))

    def close(self) -> None:
        if self.process.poll() is not None:
            return
        try:
            self.send("quit")
            self.process.wait(timeout=self.timeout)
        except (BrokenPipeError, subprocess.TimeoutExpired):
            self.process.kill()
            self.process.wait()

    def __enter__(self) -> "UciProcess":
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()


def check_case(engine: UciProcess, test: SearchCase) -> None:
    output = engine.search(test)
    info_lines = [line for line in output if line.startswith("info ") and " score " in line]
    if not info_lines:
        raise AssertionError(f"{test.name}: search emitted no scored info line: {output}")

    final_info = info_lines[-1]
    score_match = SCORE_RE.search(final_info)
    if score_match is None:
        raise AssertionError(f"{test.name}: expected mate score, got: {final_info}")

    mate = int(score_match.group(1))
    if mate != test.expected_mate:
        raise AssertionError(
            f"{test.name}: expected mate {test.expected_mate}, got mate {mate}: {final_info}"
        )

    pv_match = PV_RE.search(final_info)
    pv = tuple(pv_match.group(1).split()) if pv_match and pv_match.group(1) else ()
    if pv[: len(test.expected_pv)] != test.expected_pv:
        raise AssertionError(
            f"{test.name}: expected PV prefix {' '.join(test.expected_pv)}, "
            f"got {' '.join(pv)}: {final_info}"
        )

    bestmove = output[-1].split()
    if len(bestmove) < 2 or bestmove[1] != test.searchmove:
        raise AssertionError(
            f"{test.name}: expected bestmove {test.searchmove}, got: {output[-1]}"
        )

    print(f"PASS {test.name}: mate {mate}, pv {' '.join(pv)}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--candidate",
        type=Path,
        default=REPO_ROOT / "src" / "atomic-stockfish-search-release.exe",
    )
    parser.add_argument("--timeout", type=float, default=10.0)
    parser.add_argument(
        "--eval-file",
        type=Path,
        help="NNUE to load before search (resolved to an absolute path)",
    )
    parser.add_argument(
        "--use-nnue",
        choices=("false", "true", "pure"),
        help="set the Atomic Use NNUE mode before search",
    )
    args = parser.parse_args()

    candidate = args.candidate.resolve()
    if not candidate.is_file():
        parser.error(f"candidate does not exist: {candidate}")

    eval_file = args.eval_file.resolve() if args.eval_file is not None else None
    if eval_file is not None and not eval_file.is_file():
        parser.error(f"eval file does not exist: {eval_file}")

    with UciProcess(candidate, args.timeout, eval_file, args.use_nnue) as engine:
        for test in SEARCH_CASES:
            check_case(engine, test)

    print(f"Atomic search regressions passed: {len(SEARCH_CASES)}/{len(SEARCH_CASES)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
