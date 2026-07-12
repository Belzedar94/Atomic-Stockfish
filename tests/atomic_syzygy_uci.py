#!/usr/bin/env python3
"""Black-box UCI tests for native Atomic Syzygy integration.

Unlike ``atomic_syzygy.py``, this suite talks only to the production engine.
It exercises the public UCI contract, root DTZ ranking, an interior WDL probe,
Atomic960, castling eligibility, terminal positions, and recoverable table
loading failures using genuine ``.atbw/.atbz`` fixtures.

Examples::

    python tests/atomic_syzygy_uci.py
    python tests/atomic_syzygy_uci.py \
        --eval-file ../atomic_run3b_e202_l05.nnue
"""

from __future__ import annotations

import argparse
import queue
import re
import shutil
import subprocess
import tempfile
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Callable


REPO_ROOT = Path(__file__).resolve().parents[1]
WORKSPACE_ROOT = REPO_ROOT.parent
DEFAULT_TABLES = (
    WORKSPACE_ROOT
    / "research"
    / "shakmaty"
    / "shakmaty-syzygy"
    / "tables"
    / "atomic"
)

KBBBVK_FEN = "5BBB/8/8/8/8/8/6k1/7K w - - 0 1"
KBBBVK_SLOW_MOVE = "f8a3"
KBBBVK_FAST_MOVE = "f8h6"

# Seven pieces at the root make a root probe impossible (Atomic tables stop at
# six). After f5xe4 explodes both the capturing bishop and pawn, the resulting
# five-piece position is the genuine KBBBvK position below, with White to move.
INTERIOR_FEN = "7k/8/8/5b2/4P3/8/B1BB4/K7 b - - 0 1"
INTERIOR_MOVE = "f5e4"

CASTLING_FEN = "4k3/8/8/8/8/8/8/4K2R b K - 0 1"
NO_CASTLING_FEN = "4k3/8/8/8/8/8/8/4K2R b - - 0 1"
MISSING_BLACK_KING_FEN = "8/8/8/8/8/8/2K5/8 b - - 0 1"

TB_HITS_RE = re.compile(r"(?:^|\s)tbhits\s+(\d+)(?:\s|$)")
SCORE_RE = re.compile(r"(?:^|\s)score\s+(cp|mate)\s+(-?\d+)(?:\s|$)")
BESTMOVE_RE = re.compile(r"^bestmove\s+(\S+)")


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


@dataclass(frozen=True)
class SearchResult:
    output: list[str]
    bestmove: str
    tb_hits: int
    score_kind: str | None
    score_value: int | None


class UciProcess:
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

    def _read_output(self) -> None:
        assert self.process.stdout is not None
        for line in self.process.stdout:
            self.lines.put(line.rstrip("\r\n"))
        self.lines.put(None)

    def send(self, command: str) -> None:
        if self.process.poll() is not None:
            raise RuntimeError(
                f"{self.executable} exited with {self.process.returncode}"
            )
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
                    f"timed out waiting for {self.executable}; "
                    f"last output: {output[-15:]}"
                ) from exc
            if line is None:
                raise RuntimeError(
                    f"{self.executable} exited with {self.process.poll()}; "
                    f"last output: {output[-15:]}"
                )
            output.append(line)
            if predicate(line):
                return output

    def uci(self) -> list[str]:
        self.send("uci")
        return self.read_until(lambda line: line == "uciok")

    def ready(self) -> list[str]:
        self.send("isready")
        return self.read_until(lambda line: line == "readyok")

    def setoption(self, name: str, value: str | None = None) -> list[str]:
        suffix = "" if value is None else f" value {value}"
        self.send(f"setoption name {name}{suffix}")
        return self.ready()

    def search(self, fen: str, go: str) -> SearchResult:
        self.send(f"position fen {fen}")
        self.send(go)
        output = self.read_until(lambda line: BESTMOVE_RE.match(line) is not None)
        bestmove_match = BESTMOVE_RE.match(output[-1])
        assert bestmove_match is not None

        hits = [int(match.group(1)) for line in output if (match := TB_HITS_RE.search(line))]
        scores = [match.groups() for line in output if (match := SCORE_RE.search(line))]
        score_kind, score_text = scores[-1] if scores else (None, None)
        result = SearchResult(
            output=output,
            bestmove=bestmove_match.group(1),
            tb_hits=max(hits, default=0),
            score_kind=score_kind,
            score_value=int(score_text) if score_text is not None else None,
        )

        # A post-search barrier proves that terminal and failed-probe paths did
        # not merely emit a bestmove immediately before killing the process.
        self.ready()
        return result

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


def check_uci_contract(uci: list[str]) -> None:
    expected_limit = (
        "option name SyzygyProbeLimit type spin default 6 min 0 max 6"
    )
    require(expected_limit in uci, f"missing exact max-six Syzygy option:\n{uci}")
    require(
        "option name SyzygyPath type string default <empty>" in uci,
        f"missing SyzygyPath option:\n{uci}",
    )
    require(
        "option name UCI_Variant type combo default atomic var atomic" in uci,
        f"engine is not Atomic-only:\n{uci}",
    )
    require(
        "option name Use NNUE type combo default true var false var true var pure"
        in uci,
        f"missing tri-state NNUE option:\n{uci}",
    )


def assert_real_tables_loaded(output: list[str], tables: Path) -> None:
    found = [line for line in output if "tablebase files" in line and "Found" in line]
    require(found, f"SyzygyPath did not report loaded tables from {tables}:\n{output}")
    match = re.search(r"Found\s+(\d+)\s+WDL\s+and\s+(\d+)\s+DTZ", found[-1])
    require(match is not None, f"unrecognized table load report: {found[-1]}")
    require(int(match.group(1)) > 0, f"no Atomic WDL tables loaded: {found[-1]}")
    require(int(match.group(2)) > 0, f"no Atomic DTZ tables loaded: {found[-1]}")


def assert_root_probe(engine: UciProcess, *, atomic960: bool = False) -> SearchResult:
    engine.setoption("UCI_Chess960", "true" if atomic960 else "false")
    engine.setoption("Clear Hash")
    result = engine.search(
        KBBBVK_FEN,
        f"go depth 1 searchmoves {KBBBVK_SLOW_MOVE} {KBBBVK_FAST_MOVE}",
    )
    require(
        result.tb_hits >= 2,
        f"KBBBvK was not root-probed (Atomic960={atomic960}):\n{result.output}",
    )
    require(
        result.bestmove == KBBBVK_FAST_MOVE,
        "KBBBvK root DTZ ranking did not prefer f8h6 over f8a3 "
        f"(Atomic960={atomic960}):\n{result.output}",
    )
    require(
        result.score_value is not None and result.score_value > 0,
        f"KBBBvK root probe did not report a win:\n{result.output}",
    )
    return result


def test_interior_wdl(engine: UciProcess) -> None:
    engine.setoption("UCI_Chess960", "false")
    engine.setoption("Clear Hash")
    result = engine.search(
        INTERIOR_FEN,
        f"go depth 3 searchmoves {INTERIOR_MOVE}",
    )
    require(
        result.bestmove == INTERIOR_MOVE,
        f"interior WDL fixture did not search its forced move:\n{result.output}",
    )
    require(
        result.tb_hits > 0,
        "seven-piece root produced no observable interior KBBBvK WDL hit:\n"
        f"{result.output}",
    )
    require(
        result.score_value is not None and result.score_value < 0,
        f"interior KBBBvK WDL loss was not reflected in the root score:\n{result.output}",
    )


def test_terminal_without_king(engine: UciProcess) -> None:
    result = engine.search(MISSING_BLACK_KING_FEN, "go depth 1")
    require(
        result.bestmove in {"(none)", "0000"},
        f"terminal missing-king position returned a move:\n{result.output}",
    )
    require(
        result.tb_hits == 0,
        f"terminal missing-king position reached Syzygy:\n{result.output}",
    )


def test_castling_eligibility(engine: UciProcess) -> None:
    engine.setoption("Clear Hash")
    with_rights = engine.search(CASTLING_FEN, "go depth 1")
    require(
        with_rights.tb_hits == 0,
        f"position carrying castling rights entered Atomic Syzygy:\n{with_rights.output}",
    )

    engine.setoption("Clear Hash")
    without_rights = engine.search(NO_CASTLING_FEN, "go depth 1")
    require(
        without_rights.tb_hits > 0,
        "no-rights KRvK control position did not enter Atomic Syzygy:\n"
        f"{without_rights.output}",
    )


def test_recoverable_paths(engine: UciProcess, tables: Path) -> None:
    with tempfile.TemporaryDirectory(prefix="atomic-syzygy-uci-") as temporary:
        root = Path(temporary)
        missing = root / "does-not-exist"
        missing_report = engine.setoption("SyzygyPath", str(missing))
        require(
            any("Found 0 WDL and 0 DTZ" in line for line in missing_report),
            f"missing path was not handled as an empty table set:\n{missing_report}",
        )
        missing_search = engine.search(NO_CASTLING_FEN, "go depth 1")
        require(
            missing_search.bestmove not in {"(none)", "0000"},
            f"engine stopped searching after a missing SyzygyPath:\n{missing_search.output}",
        )
        require(
            missing_search.tb_hits == 0,
            f"missing SyzygyPath retained stale mapped tables:\n{missing_search.output}",
        )

        corrupt = root / "corrupt"
        corrupt.mkdir()
        wdl = bytearray((tables / "KBBBvK.atbw").read_bytes())
        wdl[:4] = b"BAD!"
        (corrupt / "KBBBvK.atbw").write_bytes(wdl)
        shutil.copy2(tables / "KBBBvK.atbz", corrupt / "KBBBvK.atbz")

        corrupt_report = engine.setoption("SyzygyPath", str(corrupt))
        require(
            any("Found 1 WDL and 1 DTZ" in line for line in corrupt_report),
            f"corrupt fixture directory was not indexed:\n{corrupt_report}",
        )
        corrupt_search = engine.search(KBBBVK_FEN, "go depth 1")
        require(
            any("corrupt" in line.lower() for line in corrupt_search.output),
            f"bad Atomic WDL magic was not diagnosed:\n{corrupt_search.output}",
        )
        require(
            corrupt_search.bestmove not in {"(none)", "0000"},
            f"corrupt Atomic table killed normal search:\n{corrupt_search.output}",
        )

        restored = engine.setoption("SyzygyPath", str(tables))
        assert_real_tables_loaded(restored, tables)
        engine.setoption("Clear Hash")
        recovered = engine.search(NO_CASTLING_FEN, "go depth 1")
        require(
            recovered.tb_hits > 0,
            f"engine did not recover after a corrupt table path:\n{recovered.output}",
        )


def validate_inputs(parser: argparse.ArgumentParser, args: argparse.Namespace) -> None:
    if not args.engine.is_file():
        parser.error(f"engine does not exist: {args.engine}")
    if not args.tables.is_dir():
        parser.error(f"Atomic table directory does not exist: {args.tables}")
    for name in ("KBBBvK.atbw", "KBBBvK.atbz", "KRvK.atbw"):
        if not (args.tables / name).is_file():
            parser.error(f"required real Atomic fixture is missing: {args.tables / name}")
    if args.eval_file is not None and not args.eval_file.is_file():
        parser.error(f"network does not exist: {args.eval_file}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--engine",
        type=Path,
        default=REPO_ROOT / "src" / "atomic-stockfish.exe",
    )
    parser.add_argument("--tables", type=Path, default=DEFAULT_TABLES)
    parser.add_argument(
        "--eval-file",
        type=Path,
        help="also repeat root/interior probes with Use NNUE=true",
    )
    parser.add_argument("--timeout", type=float, default=30.0)
    args = parser.parse_args()
    args.engine = args.engine.resolve()
    args.tables = args.tables.resolve()
    if args.eval_file is not None:
        args.eval_file = args.eval_file.resolve()
    validate_inputs(parser, args)

    with UciProcess(args.engine, args.timeout) as engine:
        check_uci_contract(engine.uci())
        engine.setoption("Threads", "1")
        engine.setoption("Hash", "16")
        engine.setoption("SyzygyProbeLimit", "6")
        engine.setoption("SyzygyProbeDepth", "1")
        engine.setoption("Syzygy50MoveRule", "true")
        engine.setoption("Use NNUE", "false")

        loaded = engine.setoption("SyzygyPath", str(args.tables))
        assert_real_tables_loaded(loaded, args.tables)

        assert_root_probe(engine)
        test_interior_wdl(engine)
        test_terminal_without_king(engine)
        test_castling_eligibility(engine)
        assert_root_probe(engine, atomic960=True)

        if args.eval_file is not None:
            engine.setoption("EvalFile", str(args.eval_file))
            engine.setoption("Use NNUE", "true")
            nnue_root = assert_root_probe(engine)
            require(
                not any("ERROR:" in line for line in nnue_root.output),
                f"Use NNUE=true rejected the supplied network:\n{nnue_root.output}",
            )
            test_interior_wdl(engine)
            engine.setoption("Use NNUE", "false")

        test_recoverable_paths(engine, args.tables)

    modes = "false/true" if args.eval_file is not None else "false"
    print(
        "Atomic Syzygy UCI tests passed: max6, load, root ranking, interior WDL, "
        f"terminal, castling, Atomic960, recoverable paths, NNUE={modes}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
