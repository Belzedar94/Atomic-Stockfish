#!/usr/bin/env python3
"""Fixed-node mate battery for the production network.

Every position in the fixture is a proven forced win (closed by AtomicDB with a
minimax proof, not by an engine score). The engine searches each one for a
fixed number of nodes from a fresh game with one thread, so the result is
deterministic for a given build and network.

Two checks:

* the engine must report a mate of the correct sign (side to move mates, or
  side to move is mated) in its final score for at least --min-found of the
  positions. Search changes may legitimately move a few positions across the
  node budget, hence the threshold;
* the engine must NEVER report a mate of the wrong sign, in any info line.
  A proven win scored as a loss (or the reverse) is a correctness bug, not a
  strength question.

Fixture format (TSV with a header): fen, winner (white|black), mate_plies,
key (the AtomicDB position key, for provenance).
"""

from __future__ import annotations

import argparse
import concurrent.futures
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_FIXTURE = REPO_ROOT / "tests" / "fixtures" / "atomic-mate-battery.tsv"
SCORE_RE = re.compile(r"\bscore (cp|mate) (-?\d+)\b")


@dataclass(frozen=True)
class Case:
    fen: str
    winner: str
    mate_plies: int
    key: str

    @property
    def expected_sign(self) -> int:
        side_to_move = "white" if self.fen.split()[1] == "w" else "black"
        return 1 if side_to_move == self.winner else -1


@dataclass(frozen=True)
class Outcome:
    case: Case
    final_kind: str | None
    final_value: int | None
    wrong_sign_lines: tuple[str, ...]

    @property
    def found(self) -> bool:
        return (
            self.final_kind == "mate"
            and self.final_value is not None
            and self.final_value != 0
            and (self.final_value > 0) == (self.case.expected_sign > 0)
        )


def load_fixture(path: Path) -> list[Case]:
    lines = path.read_text(encoding="utf-8").splitlines()
    header = lines[0].split("\t")
    if header != ["fen", "winner", "mate_plies", "key"]:
        raise SystemExit(f"unexpected fixture header in {path}: {header}")
    cases = []
    for number, line in enumerate(lines[1:], start=2):
        if not line.strip():
            continue
        fen, winner, mate_plies, key = line.split("\t")
        if winner not in ("white", "black"):
            raise SystemExit(f"{path}:{number}: winner must be white or black")
        case = Case(fen, winner, int(mate_plies), key)
        # A side that is to move and wins mates in an odd number of plies.
        if (case.mate_plies % 2 == 1) != (case.expected_sign > 0):
            raise SystemExit(f"{path}:{number}: mate_plies parity contradicts the winner")
        cases.append(case)
    return cases


class Engine:
    def __init__(self, path: Path, hash_mb: int) -> None:
        self.process = subprocess.Popen(
            [str(path)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
        )
        self.send("uci")
        self.wait_for("uciok")
        self.send("setoption name Threads value 1")
        self.send(f"setoption name Hash value {hash_mb}")
        self.sync()

    def send(self, command: str) -> None:
        assert self.process.stdin is not None
        self.process.stdin.write(command + "\n")
        self.process.stdin.flush()

    def lines(self):
        assert self.process.stdout is not None
        for line in self.process.stdout:
            yield line.rstrip("\n")
        raise RuntimeError("engine exited unexpectedly")

    def wait_for(self, token: str) -> list[str]:
        seen = []
        for line in self.lines():
            seen.append(line)
            if line == token or line.startswith(token + " "):
                return seen
        return seen

    def sync(self) -> None:
        self.send("isready")
        self.wait_for("readyok")

    def search(self, case: Case, nodes: int) -> Outcome:
        self.send("ucinewgame")
        self.sync()
        self.send(f"position fen {case.fen}")
        self.send(f"go nodes {nodes}")
        final_kind = final_value = None
        wrong = []
        for line in self.wait_for("bestmove"):
            if not line.startswith("info ") or " score " not in line:
                continue
            match = SCORE_RE.search(line)
            if match is None:
                continue
            kind, value = match.group(1), int(match.group(2))
            if kind == "mate" and value != 0 and (value > 0) != (case.expected_sign > 0):
                wrong.append(line)
            if " multipv " in line and " multipv 1 " not in line:
                continue
            if " lowerbound" in line or " upperbound" in line:
                continue
            final_kind, final_value = kind, value
        return Outcome(case, final_kind, final_value, tuple(wrong))

    def close(self) -> None:
        try:
            self.send("quit")
            self.process.wait(timeout=10)
        except Exception:
            self.process.kill()


def run_chunk(engine_path: Path, cases: list[Case], nodes: int, hash_mb: int) -> list[Outcome]:
    engine = Engine(engine_path, hash_mb)
    try:
        return [engine.search(case, nodes) for case in cases]
    finally:
        engine.close()


def describe(outcome: Outcome) -> str:
    score = (
        "none"
        if outcome.final_kind is None
        else f"{outcome.final_kind} {outcome.final_value}"
    )
    want = "mates" if outcome.case.expected_sign > 0 else "is mated"
    return (
        f"{'FOUND' if outcome.found else 'miss '} {score:>10}  "
        f"(side to move {want}, proven in {outcome.case.mate_plies} plies)  {outcome.case.fen}"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    parser.add_argument("--engine", type=Path, required=True)
    parser.add_argument("--fixture", type=Path, default=DEFAULT_FIXTURE)
    parser.add_argument("--nodes", type=int, default=500_000)
    parser.add_argument("--hash", type=int, default=16)
    parser.add_argument("--jobs", type=int, default=2)
    parser.add_argument(
        "--min-found",
        type=float,
        default=0.9,
        help="minimum fraction of positions with a correct-sign final mate score",
    )
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    cases = load_fixture(args.fixture)
    jobs = max(1, min(args.jobs, len(cases)))
    chunks = [cases[index::jobs] for index in range(jobs)]
    outcomes: dict[Case, Outcome] = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=jobs) as pool:
        futures = [
            pool.submit(run_chunk, args.engine, chunk, args.nodes, args.hash)
            for chunk in chunks
        ]
        for future in futures:
            for outcome in future.result():
                outcomes[outcome.case] = outcome

    ordered = [outcomes[case] for case in cases]
    found = sum(outcome.found for outcome in ordered)
    wrong = [outcome for outcome in ordered if outcome.wrong_sign_lines]
    for outcome in ordered:
        if args.verbose or not outcome.found or outcome.wrong_sign_lines:
            print(describe(outcome))
        for line in outcome.wrong_sign_lines:
            print(f"  WRONG-SIGN MATE: {line}")

    required = args.min_found * len(cases)
    print(
        f"mate battery: {found}/{len(cases)} proven wins scored as mate of the correct sign "
        f"at {args.nodes} nodes (need {required:.1f}); wrong-sign mates in "
        f"{len(wrong)} position(s)"
    )
    if wrong:
        print("FAIL: the engine reported a mate of the wrong sign in a proven position")
        return 1
    if found < required:
        print("FAIL: too few proven mates found within the node budget")
        return 1
    print("mate battery passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
