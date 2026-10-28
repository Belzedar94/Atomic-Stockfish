#!/usr/bin/env python3
"""Deterministic black-box regressions for Atomic terminal and tactical search.

The suite protects quiet Atomic checks/evasions, every terminal-capture
encoding accepted by ``Position::atomic_wins``, and explosive captures whose
material cannot be bounded by orthodox main-search or qsearch futility.

The engine evaluates with NNUE only. The expectations were recorded with the
material-count evaluation the engine used to carry, so by default the suite
builds the material-only AtomicNNUEV2 test network, which evaluates exactly
like that count on these standard Atomic positions. ``--eval-file`` with
``--expectations reference`` checks a trained network instead.
"""

from __future__ import annotations

import argparse
import importlib.util
import queue
import re
import subprocess
import tempfile
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Callable


REPO_ROOT = Path(__file__).resolve().parents[1]
MATERIAL_NET_SOURCE = Path(__file__).resolve().with_name("create_synthetic_material_nnue.py")
SCORE_RE = re.compile(r"\bscore mate (-?\d+)\b")
CP_RE = re.compile(r"\bscore cp (-?\d+)\b")
PV_RE = re.compile(r"\bpv(?:\s+(.+))?$")


@dataclass(frozen=True)
class SearchCase:
    name: str
    fen: str
    searchmove: str
    expected_mate: int | None
    expected_pv: tuple[str, ...]
    expected_cp: int | None = None
    depth: int = 1
    force_searchmove: bool = True
    # Expectations that differ under a trained reference network.
    reference_bestmove: str | None = None
    reference_pv: tuple[str, ...] | None = None


SEARCH_CASES = (
    SearchCase(
        name="quiet atomic check remains forcing in qsearch",
        fen="B7/Rk6/8/8/8/8/7Q/4K3 w - - 0 1",
        searchmove="h2b8",
        expected_mate=1,
        expected_pv=("h2b8",),
    ),
    SearchCase(
        name="Atomic mate takes priority over the fifty-move draw",
        fen="B7/Rk6/8/8/8/8/7Q/4K3 w - - 99 1",
        searchmove="h2b8",
        expected_mate=1,
        expected_pv=("h2b8",),
    ),
    SearchCase(
        name="qsearch scores a quiet-move stalemate as draw",
        fen="KQ6/Rk6/8/3B4/8/8/8/8 w - - 0 1",
        searchmove="d5c6",
        expected_mate=None,
        expected_pv=("d5c6",),
        expected_cp=0,
    ),
    SearchCase(
        name="move picker preserves an internal quiet Atomic check",
        fen="k7/8/8/8/1N6/8/7Q/4K1B1 b - - 0 1",
        searchmove="a8b7",
        expected_mate=-1,
        expected_pv=("a8b7", "h2b8"),
        depth=2,
        force_searchmove=False,
    ),
    SearchCase(
        name="analysis FEN preserves an existing check after a quiet move",
        fen="k7/8/8/8/8/8/7B/RN5K w - - 0 1",
        searchmove="b1c3",
        expected_mate=None,
        expected_pv=("b1c3", "a8b7"),
        depth=2,
    ),
    SearchCase(
        name="qsearch explores quiet atomic check evasions",
        fen="7k/8/8/8/8/8/R7/K7 w - - 0 1",
        searchmove="a2h2",
        expected_mate=None,
        expected_pv=("a2h2",),
    ),
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
    # The winning capture is below the root: depth 1 reaches qsearch,
    # while depth 2 reaches the main search. Both must retain it in the PV.
    *(
        SearchCase(
            name=f"{name} at depth {depth}",
            fen=fen,
            searchmove=searchmove,
            expected_mate=-1,
            expected_pv=(searchmove, explosion),
            depth=depth,
        )
        for depth in (1, 2)
        for name, fen, searchmove, explosion in (
            (
                "PV includes the en-passant king explosion",
                "8/2kp4/8/4P3/8/8/8/K7 b - - 0 1",
                "d7d5",
                "e5d6",
            ),
            (
                "PV includes the capture-promotion king explosion",
                "6k1/6Pr/8/8/8/8/8/K7 b - - 0 1",
                "h7h8",
                "g7h8q",
            ),
            (
                "PV includes a pinned piece's winning explosion",
                "1r4k1/7r/8/8/8/8/1B6/1K6 b - - 0 1",
                "h7h8",
                "b2h8",
            ),
        )
    ),
    SearchCase(
        name="main capture futility preserves explosive non-pawn bycatch",
        fen="2n5/7k/1p2p2p/Q2PQ1R1/2B1R3/8/8/K5N1 w - - 0 1",
        searchmove="g1h3",
        expected_mate=None,
        expected_pv=("g1h3", "e6d5"),
        depth=2,
    ),
    SearchCase(
        name="main capture futility preserves explosive en passant bycatch",
        fen="2n5/7k/1p6/Q7/4p3/2Q5/2RP4/K7 w - - 0 1",
        searchmove="d2d4",
        expected_mate=None,
        expected_pv=("d2d4", "e4d3"),
        depth=2,
    ),
    SearchCase(
        name="qsearch capture futility preserves explosive non-pawn bycatch",
        fen="2n5/7k/1p2p2p/Q2PQ1R1/2B1R3/8/8/K5N1 w - - 0 1",
        searchmove="g1h3",
        expected_mate=None,
        expected_pv=("g1h3", "e6d5"),
        depth=1,
    ),
    SearchCase(
        name="qsearch capture futility preserves explosive en passant bycatch",
        fen="2n5/7k/1p6/Q7/4p3/2Q5/2RP4/K7 w - - 0 1",
        searchmove="d2d4",
        expected_mate=None,
        expected_pv=("d2d4", "e4d3"),
        depth=1,
    ),
    # The fixture exists to show that the Atomic null-move reduction is shallow
    # enough to still search the tactical defense: the load-bearing assertion is
    # that the engine picks g7g5 rather than the block-3 f7f6. The reply pinned
    # after it is White's answer at this depth: spsa90 moved it from d2d4 to
    # d2d3 by taking AtomicNmpDepthDiv from 3 to 4, and the blast-check guard on
    # child-node futility pruning moved it back to d2d4.
    SearchCase(
        name="Atomic null-move reduction searches the tactical defense",
        fen="rnbqkbnr/pppp1ppp/8/4p3/4P3/5N2/PPPP1PPP/RNBQKB1R b KQkq - 1 2",
        searchmove="g7g5",
        expected_mate=None,
        expected_pv=("g7g5", "d2d4"),
        depth=14,
        force_searchmove=False,
        reference_bestmove="f8b4",
        reference_pv=("f8b4", "c2c3"),
    ),
)


def create_material_network(directory: Path) -> Path:
    spec = importlib.util.spec_from_file_location("create_synthetic_material_nnue", MATERIAL_NET_SOURCE)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {MATERIAL_NET_SOURCE}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    path = directory / "atomic-material.nnue"
    module.create_network(path)
    return path


class UciProcess:
    def __init__(
        self,
        executable: Path,
        timeout: float,
        eval_file: Path,
        expectations: str = "material",
    ) -> None:
        self.executable = executable
        self.timeout = timeout
        self.expectations = expectations
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
        self.send(f"setoption name EvalFile value {eval_file}")
        self.send("setoption name Use NNUE value true")
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
        command = f"go depth {test.depth}"
        if test.force_searchmove:
            command += f" searchmoves {test.searchmove}"
        self.send(command)
        return self.read_until(lambda line: line.startswith("bestmove "))

    def close(self) -> None:
        if self.process.poll() is not None:
            return
        try:
            self.send("quit")
            self.process.wait(timeout=self.timeout)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait()
        except (BrokenPipeError, OSError, ValueError):
            if self.process.poll() is None:
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
    mate = int(score_match.group(1)) if score_match is not None else None
    cp_match = CP_RE.search(final_info)
    cp = int(cp_match.group(1)) if cp_match is not None else None
    if test.expected_cp is not None:
        if mate is not None or cp != test.expected_cp:
            rendered = f"mate {mate}" if mate is not None else f"cp {cp}"
            raise AssertionError(
                f"{test.name}: expected cp {test.expected_cp}, got {rendered}: {final_info}"
            )
    elif test.expected_mate is None:
        if mate is not None:
            raise AssertionError(
                f"{test.name}: expected a searched quiet evasion, got mate {mate}: {final_info}"
            )
    elif mate != test.expected_mate:
        rendered = "non-mate" if mate is None else f"mate {mate}"
        raise AssertionError(
            f"{test.name}: expected mate {test.expected_mate}, got {rendered}: {final_info}"
        )

    reference = engine.expectations == "reference"
    expected_pv = (
        test.reference_pv
        if reference and test.reference_pv is not None
        else test.expected_pv
    )
    expected_bestmove = (
        test.reference_bestmove
        if reference and test.reference_bestmove is not None
        else test.searchmove
    )

    pv_match = PV_RE.search(final_info)
    pv = tuple(pv_match.group(1).split()) if pv_match and pv_match.group(1) else ()
    if pv[: len(expected_pv)] != expected_pv:
        raise AssertionError(
            f"{test.name}: expected PV prefix {' '.join(expected_pv)}, "
            f"got {' '.join(pv)}: {final_info}"
        )

    bestmove = output[-1].split()
    if len(bestmove) < 2 or bestmove[1] != expected_bestmove:
        raise AssertionError(
            f"{test.name}: expected bestmove {expected_bestmove}, got: {output[-1]}"
        )

    score = f"mate {mate}" if mate is not None else f"cp {cp}"
    print(f"PASS {test.name}: {score}, pv {' '.join(pv)}")


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
        help=(
            "NNUE to search with (resolved to an absolute path); by default the "
            "material-only AtomicNNUEV2 test network is built in a temporary directory"
        ),
    )
    parser.add_argument(
        "--expectations",
        choices=("material", "reference"),
        default="material",
        help=(
            "material: the expectations recorded with the material-count evaluation; "
            "reference: those recorded with the trained reference network"
        ),
    )
    args = parser.parse_args()

    candidate = args.candidate.resolve()
    if not candidate.is_file():
        parser.error(f"candidate does not exist: {candidate}")

    eval_file = args.eval_file.resolve() if args.eval_file is not None else None
    if eval_file is not None and not eval_file.is_file():
        parser.error(f"eval file does not exist: {eval_file}")
    if args.expectations == "reference" and eval_file is None:
        parser.error("--eval-file is required with --expectations reference")

    with tempfile.TemporaryDirectory(prefix="atomic-search-") as scratch:
        if eval_file is None:
            eval_file = create_material_network(Path(scratch))
        with UciProcess(candidate, args.timeout, eval_file, args.expectations) as engine:
            for test in SEARCH_CASES:
                check_case(engine, test)

    print(f"Atomic search regressions passed: {len(SEARCH_CASES)}/{len(SEARCH_CASES)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
