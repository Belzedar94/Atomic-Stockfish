#!/usr/bin/env python3
"""Black-box CECP/XBoard contract for the Atomic-only engine.

The test deliberately uses ``ping`` as a protocol barrier instead of sleeps.
It covers feature negotiation, state editing, Atomic960, ``go``, the
historically missing ``playother`` command, clocks, terminal results, invalid
NNUE rejection, valid AtomicNNUEV2 search, Atomic Syzygy path wiring, and the
analysis lifecycle.
"""

from __future__ import annotations

import argparse
import queue
import re
import subprocess
import threading
from pathlib import Path
from typing import Callable


REPO_ROOT = Path(__file__).resolve().parents[1]
MOVE_RE = re.compile(r"^move ([a-h][1-8][a-h][1-8][qrbn]?)$")
HINT_RE = re.compile(r"^Hint: ([a-h][1-8][a-h][1-8][qrbn]?)$")
FEN_RE = re.compile(r"^Fen:\s+(.+)$")
ANALYSIS_RE = re.compile(r"^\d+\s+-?\d+\s+\d+\s+\d+(?:\s+.*)?$")
RESULT_RE = re.compile(r"^(?:1-0|0-1|1/2-1/2) \{.+\}$")


class XBoardProcess:
    def __init__(self, executable: Path, timeout: float) -> None:
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
        self.ping_id = 100

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
                    f"timed out waiting for {self.executable}; last output: {output[-10:]}"
                ) from exc
            if line is None:
                raise RuntimeError(
                    f"{self.executable} exited with {self.process.poll()}; "
                    f"last output: {output[-10:]}"
                )
            output.append(line)
            if predicate(line):
                return output

    def barrier(self) -> list[str]:
        self.ping_id += 1
        marker = f"pong {self.ping_id}"
        self.send(f"ping {self.ping_id}")
        return self.read_until(lambda line: line == marker)

    def fen(self) -> str:
        self.send("d")
        output = self.barrier()
        fens = [match.group(1) for line in output if (match := FEN_RE.match(line))]
        if not fens:
            raise AssertionError(f"XBoard diagnostic emitted no FEN: {output}")
        return fens[-1]

    def expect_move(self) -> tuple[str, list[str]]:
        output = self.read_until(lambda line: MOVE_RE.match(line) is not None)
        match = MOVE_RE.match(output[-1])
        assert match is not None
        return match.group(1), output

    def expect_hint(self) -> tuple[str, list[str]]:
        output = self.read_until(lambda line: HINT_RE.match(line) is not None)
        match = HINT_RE.match(output[-1])
        assert match is not None
        return match.group(1), output

    def expect_result(self, expected: str) -> list[str]:
        output = self.read_until(lambda line: RESULT_RE.match(line) is not None)
        if output[-1] != expected:
            raise AssertionError(f"expected result {expected!r}, got output {output}")
        return output

    def close(self) -> None:
        if self.process.poll() is not None:
            return
        try:
            self.send("quit")
            self.process.wait(timeout=self.timeout)
        except (BrokenPipeError, subprocess.TimeoutExpired):
            self.process.kill()
            self.process.wait()

    def __enter__(self) -> "XBoardProcess":
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()


def expect_fen(actual: str, expected: str, action: str) -> None:
    if actual != expected:
        raise AssertionError(f"{action}: expected FEN {expected!r}, got {actual!r}")


def expect_no_move(output: list[str], action: str) -> None:
    moves = [line for line in output if MOVE_RE.match(line)]
    if moves:
        raise AssertionError(f"{action}: discarded search leaked moves: {moves}")


def run(engine: Path, timeout: float, eval_file: Path | None = None) -> None:
    start = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"

    with XBoardProcess(engine, timeout) as xb:
        xb.send("xboard")
        xb.send("protover 2")
        features = xb.read_until(lambda line: line == "feature done=1")
        joined = "\n".join(features)
        for required in (
            "setboard=1",
            "usermove=1",
            "ping=1",
            "analyze=1",
            "san=0",
            'egt="syzygy"',
            'variants="atomic"',
            'myname="Atomic-Stockfish',
            'option="Use NNUE -combo true /// false /// pure"',
            'option="Ponder -check 0"',
            'option="SyzygyPath -path <empty>"',
        ):
            if required not in joined:
                raise AssertionError(f"missing XBoard feature {required!r}:\n{joined}")
        if 'variants="chess' in joined or ",chess" in joined:
            raise AssertionError(f"non-Atomic variant advertised:\n{joined}")

        xb.send("option Use NNUE=false")
        xb.send("variant atomic")
        xb.barrier()

        # CECP's standard egtpath command and the engine-defined option share
        # the one native Atomic Syzygy adapter. Re-advertising features exposes
        # the current value, so this checks mapping without requiring tables.
        syzygy_path = "xboard-empty-atomic-tables path"
        xb.send(f"egtpath SyZyGy {syzygy_path}")
        xb.send("protover 2")
        syzygy_features = xb.read_until(lambda line: line == "feature done=1")
        expected_path_feature = f'option="SyzygyPath -path {syzygy_path}"'
        if not any(expected_path_feature in line for line in syzygy_features):
            raise AssertionError(
                f"egtpath did not update the shared SyzygyPath: {syzygy_features}"
            )

        xb.send("egtpath gaviota should-not-replace-syzygy")
        xb.send("egtpath syzygy")
        xb.send("egtpath")
        invalid_egtpath = xb.barrier()
        for expected_error in (
            "Error (unsupported tablebase type): gaviota",
            "Error (invalid egtpath): missing Syzygy path",
            "Error (invalid egtpath): expected TYPE PATH",
        ):
            if expected_error not in invalid_egtpath:
                raise AssertionError(
                    f"missing egtpath error {expected_error!r}: {invalid_egtpath}"
                )

        xb.send("protover 2")
        unchanged_syzygy = xb.read_until(lambda line: line == "feature done=1")
        if not any(expected_path_feature in line for line in unchanged_syzygy):
            raise AssertionError(
                f"invalid egtpath changed SyzygyPath: {unchanged_syzygy}"
            )

        # Force mode and reversible state editing.
        xb.send("new")
        xb.send("force")
        expect_fen(xb.fen(), start, "new")

        xb.send("usermove e2e4")
        expect_fen(
            xb.fen(),
            "rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq - 0 1",
            "usermove",
        )
        xb.send("undo")
        expect_fen(xb.fen(), start, "undo")

        xb.send("usermove e2e4")
        xb.send("usermove e7e5")
        xb.send("remove")
        expect_fen(xb.fen(), start, "remove")

        # A partial remove has no two-ply unit to retract and must not underflow.
        xb.send("usermove e2e4")
        before_partial_remove = xb.fen()
        xb.send("remove")
        expect_fen(xb.fen(), before_partial_remove, "partial remove")
        xb.send("undo")

        fixture = "7k/7R/8/8/8/8/8/K7 w - - 0 1"
        xb.send(f"setboard {fixture}")
        expect_fen(xb.fen(), fixture, "setboard")

        # Atomic960 uses the standard UCI_Chess960 king-to-rook encoding. Here
        # c1b1 leaves the king on c1 and moves the b1 rook to d1. The option is
        # protocol configuration and therefore must survive ``new``.
        xb.send("option UCI_Chess960=1")
        xb.send("new")
        xb.send("force")
        xb.send("setboard 7k/8/8/8/8/8/8/1RK5 w Q - 0 1")
        xb.send("usermove c1b1")
        expect_fen(
            xb.fen(),
            "7k/8/8/8/8/8/8/2KR4 b - - 1 1",
            "Atomic960 castling",
        )
        xb.send("option UCI_Chess960=0")
        xb.send(f"setboard {fixture}")

        # Bad input is a protocol error, not an exception that kills the engine.
        xb.send("setboard")
        xb.send("level nope 0:10 0")
        xb.send("level 0 0:99 0")
        xb.send("st -1")
        xb.send("sd nope")
        xb.send("time overflow")
        xb.send("otim -1")
        xb.send("cores nope")
        xb.send("memory -1")
        xb.send("option Hash=not-a-number")
        xb.send("perft -1")
        malformed = xb.barrier()
        errors = [line for line in malformed if line.startswith("Error (invalid")]
        if len(errors) < 10:
            raise AssertionError(f"malformed commands were not rejected safely: {malformed}")
        expect_fen(xb.fen(), fixture, "malformed commands preserve position")

        xb.send("variant chess")
        unsupported = xb.barrier()
        if "Error (unsupported variant): chess" not in unsupported:
            raise AssertionError(f"unsupported variant was not rejected: {unsupported}")
        expect_fen(xb.fen(), fixture, "unsupported variant preserves position")

        # A requested but incompatible/missing NNUE blocks go without killing
        # CECP. This covers the bool Engine::go rejection path used by XBoard.
        xb.send("option EvalFile=missing-xboard-contract-test.nnue")
        xb.send("option Use NNUE=true")
        xb.send("new")

        # Perft uses only rules and move generation. It must therefore remain
        # available with the default NNUE-enabled mode even when EvalFile did
        # not load. This also catches holding the synchronized-output lock
        # around engine.perft(), whose root divide writes synchronized lines.
        xb.send("perft 1")
        networkless_perft = xb.read_until(lambda line: line == "Nodes searched: 20")
        if any(line.startswith("Error") for line in networkless_perft):
            raise AssertionError(
                f"networkless XBoard perft produced an error: {networkless_perft}"
            )
        xb.barrier()

        xb.send("sd 1")
        xb.send("go")
        rejected_go = xb.barrier()
        if any(MOVE_RE.match(line) for line in rejected_go):
            raise AssertionError(f"go ran with an invalid NNUE: {rejected_go}")
        if not any("NNUE" in line or "network" in line.lower() for line in rejected_go):
            raise AssertionError(f"invalid NNUE rejection was not diagnosed: {rejected_go}")
        xb.send("force")
        xb.send("option Use NNUE=false")
        xb.barrier()

        if eval_file is not None:
            xb.send(f"option EvalFile={eval_file}")
            xb.send("option Use NNUE=true")
            xb.send("new")
            xb.send("sd 1")
            xb.send("go")
            _, nnue_output = xb.expect_move()
            if not any(
                "NNUE evaluation using Legacy Atomic V1" in line
                or "NNUE evaluation using AtomicNNUEV2" in line
                for line in nnue_output
            ):
                raise AssertionError(
                    "XBoard did not search with the requested supported Atomic NNUE: "
                    f"{nnue_output}"
                )
            xb.send("force")
            xb.send("option Use NNUE=false")
            xb.barrier()

        # Normal go: the engine plays the side to move and applies its move.
        xb.send("new")
        xb.send("sd 1")
        xb.send("level 0 0:10 0")
        xb.send("time 1000")
        xb.send("otim 1000")
        xb.send("go")
        _, go_output = xb.expect_move()
        if any(line.startswith("Error") for line in go_output):
            raise AssertionError(f"go produced a protocol error: {go_output}")
        if xb.fen().split()[1] != "b":
            raise AssertionError("go did not apply the engine's white move")

        # ``hard`` must start pondering autonomously from the PV returned by the
        # completed search. A thinking line after ``move`` and before any new
        # board command proves this is a real background ponder, not a restart
        # deferred until usermove. ``hint`` exposes that exact predicted move.
        xb.send("new")
        xb.send("sd 3")
        xb.send("hard")
        xb.send("go")
        xb.expect_move()
        ponder_thinking = xb.read_until(lambda line: ANALYSIS_RE.match(line) is not None)
        if not any(ANALYSIS_RE.match(line) for line in ponder_thinking):
            raise AssertionError(f"hard did not start autonomous ponder: {ponder_thinking}")
        xb.send("hint")
        predicted, hint_output = xb.expect_hint()
        if any(line.startswith("Error") for line in hint_output):
            raise AssertionError(f"hint failed during ponder: {hint_output}")

        # Matching the prediction is a ponderhit: the already-running search
        # becomes the real timed search and returns the engine's next move.
        xb.send(f"usermove {predicted}")
        _, hit_output = xb.expect_move()
        if any("Illegal move" in line for line in hit_output):
            raise AssertionError(f"ponderhit rejected its predicted move: {hit_output}")
        xb.send("easy")
        easy_output = xb.barrier()
        expect_no_move(easy_output, "easy stops next ponder")
        if xb.fen().split()[1] != "b":
            raise AssertionError("ponderhit sequence did not commit exactly three plies")

        def start_live_ponder() -> str:
            xb.send("new")
            xb.send("hard")
            xb.send("sd 2")
            xb.send("usermove e2e4")
            _, engine_output = xb.expect_move()
            if any("Illegal move" in line for line in engine_output):
                raise AssertionError(f"could not start ponder fixture: {engine_output}")
            xb.read_until(lambda line: ANALYSIS_RE.match(line) is not None)
            xb.send("hint")
            hint, _ = xb.expect_hint()
            return hint

        # A different legal move is a ponder miss. The hypothetical search is
        # discarded, the actual move is applied once, and a fresh search answers.
        missed_prediction = start_live_ponder()
        actual = "a2a3" if missed_prediction != "a2a3" else "h2h3"
        xb.send(f"usermove {actual}")
        _, miss_output = xb.expect_move()
        if any("Illegal move" in line for line in miss_output):
            raise AssertionError(f"ponder miss did not restart safely: {miss_output}")
        xb.send("easy")
        expect_no_move(xb.barrier(), "easy after ponder miss")
        if xb.fen().split()[1] != "w":
            raise AssertionError("ponder miss sequence did not commit exactly four plies")

        # Every state-editing/stop command must cancel an active ponder without
        # leaking its hypothetical bestmove or leaving a worker behind.
        start_live_ponder()
        xb.send("?")
        stopped = xb.barrier()
        expect_no_move(stopped, "? during ponder")
        if xb.fen().split()[1] != "w":
            raise AssertionError("? did not restore the actual two-ply position")

        start_live_ponder()
        xb.send("force")
        forced = xb.barrier()
        expect_no_move(forced, "force during ponder")
        if xb.fen().split()[1] != "w":
            raise AssertionError("force did not restore the actual two-ply position")

        start_live_ponder()
        xb.send("undo")
        undone = xb.barrier()
        expect_no_move(undone, "undo during ponder")
        if xb.fen().split()[1] != "b":
            raise AssertionError("undo did not retract the engine move after cancelling ponder")

        start_live_ponder()
        xb.send("remove")
        removed = xb.barrier()
        expect_no_move(removed, "remove during ponder")
        expect_fen(xb.fen(), start, "remove after cancelling ponder")
        xb.send("easy")
        xb.barrier()

        # playother leaves the opponent on move; after e2e4 the engine must
        # answer for black. This was missing in the Fairy baseline.
        xb.send("new")
        xb.send("sd 1")
        xb.send("playother")
        xb.send("usermove e2e4")
        xb.expect_move()
        if xb.fen().split()[1] != "w":
            raise AssertionError("playother did not make the engine answer as black")

        # Analyze must remain interruptible and leave the engine responsive.
        xb.send("new")
        xb.send("post")
        xb.send("analyze")
        analysis = xb.read_until(lambda line: ANALYSIS_RE.match(line) is not None)
        if not any(ANALYSIS_RE.match(line) for line in analysis):
            raise AssertionError(f"analyze produced no CECP thinking line: {analysis}")
        xb.send("exit")
        xb.barrier()

        # CECP needs game results, not a blanket resign, for all Atomic terminal
        # classes. These also guard the missing-king path against orthodox code.
        terminals = (
            (
                "8/8/8/8/8/8/8/K7 b - - 0 1",
                "1-0 {White wins by atomic explosion}",
            ),
            (
                "7k/8/8/8/8/8/8/8 w - - 0 1",
                "0-1 {Black wins by atomic explosion}",
            ),
            (
                "BQ6/Rk6/8/8/8/8/8/4K3 b - - 0 1",
                "1-0 {White mates}",
            ),
            (
                "KQ6/Rk6/2B5/8/8/8/8/8 b - - 0 1",
                "1/2-1/2 {Stalemate}",
            ),
        )
        for fen, expected in terminals:
            xb.send("force")
            xb.send(f"setboard {fen}")
            xb.send("go")
            terminal_output = xb.expect_result(expected)
            if "resign" in terminal_output:
                raise AssertionError(f"terminal {fen} was reported as resign: {terminal_output}")
            xb.barrier()

    print("XBoard Atomic protocol passed")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--candidate",
        type=Path,
        default=REPO_ROOT / "src" / "atomic-stockfish.exe",
    )
    parser.add_argument("--timeout", type=float, default=15.0)
    parser.add_argument(
        "--eval-file",
        type=Path,
        help="optional authenticated Legacy Atomic V1 or AtomicNNUEV2 fixture used for a CECP search",
    )
    args = parser.parse_args()
    if args.timeout <= 0:
        parser.error("timeout must be positive")
    candidate = args.candidate.resolve()
    if not candidate.is_file():
        parser.error(f"candidate does not exist: {candidate}")
    eval_file = args.eval_file.resolve() if args.eval_file is not None else None
    if eval_file is not None and not eval_file.is_file():
        parser.error(f"evaluation file does not exist: {eval_file}")
    run(candidate, args.timeout, eval_file)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
