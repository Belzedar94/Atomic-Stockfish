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
import json
import os
import queue
import re
import shutil
import subprocess
import tempfile
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Sequence


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
DEFAULT_ANALYSIS_BOOK = (
    REPO_ROOT / "tests" / "fixtures" / "atomic-syzygy" / "six-man-endgames.epd"
)
DEFAULT_ANALYSIS_MANIFEST = (
    REPO_ROOT / "tests" / "fixtures" / "atomic-syzygy" / "six-man-fixtures.json"
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

# A six-man position whose legal root moves preserve KPPPPvK material. This
# lets the public UCI suite prove that SyzygyProbeLimit=5 suppresses the probe
# while limit 6 reaches the real WDL/DTZ pair, without depending on lower-man
# tables for captures or promotions.
KPPPPVK_FEN = "8/8/8/8/8/PPPP4/8/K6k w - - 0 1"
KPPPPVK_WDL = "KPPPPvK.atbw"
KPPPPVK_DTZ = "KPPPPvK.atbz"
KPPPPVK_WDL_VALUE = 2
KPPPPVK_DTZ_VALUE = 1
KPPPPVK_ORACLE_MOVES = frozenset(
    {"a3a4", "b3b4", "c3c4", "d3d4", "a1b1", "a1a2", "a1b2"}
)

TB_HITS_RE = re.compile(r"(?:^|\s)tbhits\s+(\d+)(?:\s|$)")
SCORE_RE = re.compile(r"(?:^|\s)score\s+(cp|mate)\s+(-?\d+)(?:\s|$)")
BESTMOVE_RE = re.compile(r"^bestmove\s+(\S+)")
DRIVER_PROBE_RE = re.compile(
    r"^probe wdl=(-?\d+) wdl_state=(-?\d+) dtz=(-?\d+) dtz_state=(-?\d+)$",
    re.MULTILINE,
)


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


def normalize_table_dirs(table_dirs: Sequence[Path]) -> tuple[Path, ...]:
    """Resolve a non-empty, duplicate-free Atomic Syzygy search path."""

    resolved = tuple(path.expanduser().resolve() for path in table_dirs)
    require(bool(resolved), "at least one Atomic table directory is required")
    require(
        len(resolved) == len(set(resolved)),
        f"duplicate Atomic table directories are not allowed: {resolved}",
    )
    return resolved


def syzygy_path_value(table_dirs: Sequence[Path]) -> str:
    """Render the platform-native multi-directory SyzygyPath value."""

    normalized = normalize_table_dirs(table_dirs)
    return os.pathsep.join(str(path) for path in normalized)


def find_table_file(table_dirs: Sequence[Path], name: str) -> Path | None:
    matches = [path / name for path in table_dirs if (path / name).is_file()]
    require(
        len(matches) <= 1,
        f"Atomic table fixture {name} is ambiguous across directories: {matches}",
    )
    return matches[0] if matches else None


def assert_real_tables_loaded(
    output: list[str], tables: Sequence[Path]
) -> tuple[int, int]:
    found = [line for line in output if "tablebase files" in line and "Found" in line]
    require(found, f"SyzygyPath did not report loaded tables from {tables}:\n{output}")
    match = re.search(r"Found\s+(\d+)\s+WDL\s+and\s+(\d+)\s+DTZ", found[-1])
    require(match is not None, f"unrecognized table load report: {found[-1]}")
    counts = (int(match.group(1)), int(match.group(2)))
    require(counts[0] > 0, f"no Atomic WDL tables loaded: {found[-1]}")
    require(counts[1] > 0, f"no Atomic DTZ tables loaded: {found[-1]}")
    return counts


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


def test_six_man_probe_limit(engine: UciProcess) -> None:
    """Prove the public limit boundary with a genuine six-man WDL/DTZ pair."""

    engine.setoption("UCI_Chess960", "false")
    engine.setoption("SyzygyProbeLimit", "5")
    engine.setoption("Clear Hash")
    limited = engine.search(KPPPPVK_FEN, "go depth 1")
    require(
        limited.tb_hits == 0,
        "KPPPPvK was probed despite SyzygyProbeLimit=5:\n" f"{limited.output}",
    )

    engine.setoption("SyzygyProbeLimit", "6")
    engine.setoption("Clear Hash")
    enabled = engine.search(KPPPPVK_FEN, "go depth 1")
    require(
        enabled.tb_hits > 0,
        "KPPPPvK produced no tablebase hit with SyzygyProbeLimit=6:\n"
        f"{enabled.output}",
    )
    require(
        enabled.bestmove in KPPPPVK_ORACLE_MOVES,
        f"KPPPPvK returned non-oracle move {enabled.bestmove}:\n{enabled.output}",
    )
    require(
        enabled.score_value is not None and enabled.score_value > 0,
        f"KPPPPvK six-man probe did not preserve the oracle win:\n{enabled.output}",
    )


def validate_kpppp_driver_output(output: str) -> None:
    probe = DRIVER_PROBE_RE.search(output)
    require(probe is not None, f"six-man driver emitted no probe record:\n{output}")
    actual = tuple(int(value) for value in probe.groups())
    # A pawn push is a zeroing best move, so the successful DTZ probe reports
    # ZEROING_BEST_MOVE (2) rather than the generic OK (1) state.
    expected = (KPPPPVK_WDL_VALUE, 1, KPPPPVK_DTZ_VALUE, 2)
    require(
        actual == expected,
        f"KPPPPvK direct probe is {actual}; expected {expected}:\n{output}",
    )
    for label in ("root_no_rule50", "root_rule50", "root_wdl"):
        require(
            re.search(rf"^{label} ok=1(?:\s|$)", output, re.MULTILINE) is not None,
            f"KPPPPvK direct probe did not complete {label}:\n{output}",
        )
    require(
        re.search(
            # A successfully DTZ-ranked root intentionally disables further
            # interior probing, leaving Config.cardinality at zero.
            r"^rank_root root_in_tb=1 cardinality=0 "
            r"use_rule50=1 probe_depth=1$",
            output,
            re.MULTILINE,
        )
        is not None,
        f"KPPPPvK direct root ranking contract failed:\n{output}",
    )


def test_six_man_driver(
    driver: Path, table_dirs: Sequence[Path], timeout: float
) -> None:
    # The direct driver needs exactly one authenticated WDL/DTZ pair. Pointing
    # it at the production 1,020-file corpus makes its one-time directory scan
    # dominate this proof (and can exceed a minute on the archive volume).
    # Stage the two small fixtures in isolation; the UCI half of this script
    # still loads and verifies the complete corpus afterwards.
    fixture_paths = {
        name: find_table_file(table_dirs, name)
        for name in (KPPPPVK_WDL, KPPPPVK_DTZ)
    }
    require(
        all(path is not None for path in fixture_paths.values()),
        "six-man driver requires the KPPPPvK WDL/DTZ pair",
    )
    with tempfile.TemporaryDirectory(prefix="atomic-syzygy-driver-") as temporary:
        isolated = Path(temporary)
        for name, source in fixture_paths.items():
            assert source is not None
            shutil.copy2(source, isolated / name)

        try:
            completed = subprocess.run(
                [str(driver), str(isolated), KPPPPVK_FEN],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise AssertionError(f"could not run six-man Syzygy driver: {exc}") from exc
    require(
        completed.returncode == 0,
        f"six-man Syzygy driver exited {completed.returncode}:\n{completed.stdout}",
    )
    validate_kpppp_driver_output(completed.stdout)
    print(
        "Atomic Syzygy KPPPPvK direct probe: PASS "
        f"wdl={KPPPPVK_WDL_VALUE} dtz={KPPPPVK_DTZ_VALUE} "
        "pieces=6 root_cardinality=0"
    )


def load_analysis_expectations(manifest: Path) -> dict[str, dict[str, object]]:
    try:
        payload = json.loads(manifest.read_text(encoding="utf-8"))
        positions = payload["challenge_positions"]["positions"]
    except (OSError, json.JSONDecodeError, KeyError, TypeError) as exc:
        raise AssertionError(f"invalid six-man analysis manifest {manifest}: {exc}") from exc
    require(isinstance(positions, list), "analysis manifest positions must be a list")
    require(
        all(
            isinstance(position, dict) and isinstance(position.get("fen"), str)
            for position in positions
        ),
        "analysis manifest positions must contain string FENs",
    )
    expectations = {str(position["fen"]): position for position in positions}
    require(
        len(expectations) == len(positions),
        "analysis manifest contains duplicate FENs",
    )
    return expectations


def analyze_six_man_book(
    engine: UciProcess,
    book: Path,
    expectations: dict[str, dict[str, object]] | None = None,
) -> list[SearchResult]:
    """Require observable on/off table use for every frozen endgame position."""

    fens = [line.strip() for line in book.read_text(encoding="utf-8").splitlines()]
    require(bool(fens), f"six-man analysis book is empty: {book}")
    require(
        all(len(fen.split()) == 6 for fen in fens),
        f"six-man analysis book contains a non-FEN line: {book}",
    )
    if expectations is not None:
        require(
            set(fens) == set(expectations),
            "six-man analysis book and oracle manifest FENs differ",
        )

    engine.setoption("UCI_Chess960", "false")
    enabled_results: list[SearchResult] = []
    for index, fen in enumerate(fens, 1):
        engine.setoption("SyzygyProbeLimit", "6")
        engine.setoption("Clear Hash")
        enabled = engine.search(fen, "go depth 1")
        require(
            enabled.tb_hits > 0,
            f"analysis position {index} produced no six-man tbhits:\n"
            f"fen={fen}\n{enabled.output}",
        )
        require(
            enabled.bestmove not in {"(none)", "0000"}
            and enabled.score_value is not None,
            f"analysis position {index} returned no move/score:\n{enabled.output}",
        )
        expected = expectations.get(fen) if expectations is not None else None
        if expected is not None:
            if expected.get("unique_winning_move") is True:
                require(
                    enabled.bestmove == expected.get("best_move"),
                    f"analysis position {index} selected {enabled.bestmove}; "
                    f"oracle unique win is {expected.get('best_move')}",
                )
            require(
                expected.get("category") == "win"
                and enabled.score_value is not None
                and enabled.score_value > 0,
                f"analysis position {index} did not preserve the oracle win category",
            )

        engine.setoption("SyzygyProbeLimit", "0")
        engine.setoption("Clear Hash")
        disabled = engine.search(fen, "go depth 1")
        require(
            disabled.tb_hits == 0,
            f"analysis position {index} still probed with limit 0:\n{disabled.output}",
        )
        require(
            disabled.bestmove not in {"(none)", "0000"},
            f"analysis position {index} failed without tables:\n{disabled.output}",
        )
        enabled_results.append(enabled)
        print(
            "Atomic Syzygy analysis "
            f"position={index}/{len(fens)} tbhits={enabled.tb_hits} "
            f"bestmove={enabled.bestmove} score={enabled.score_kind}:{enabled.score_value}"
            + (
                f" oracle_category={expected.get('category')} oracle_dtz={expected.get('dtz')}"
                if expected is not None
                else ""
            )
        )

    engine.setoption("SyzygyProbeLimit", "6")
    return enabled_results


def test_recoverable_paths(
    engine: UciProcess,
    tables: Sequence[Path],
    *,
    kbbb_wdl: Path,
    kbbb_dtz: Path,
) -> None:
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
        wdl = bytearray(kbbb_wdl.read_bytes())
        wdl[:4] = b"BAD!"
        (corrupt / "KBBBvK.atbw").write_bytes(wdl)
        shutil.copy2(kbbb_dtz, corrupt / "KBBBvK.atbz")

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

        restored = engine.setoption("SyzygyPath", syzygy_path_value(tables))
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
    for table_dir in args.tables:
        if not table_dir.is_dir():
            parser.error(f"Atomic table directory does not exist: {table_dir}")
    for name in ("KBBBvK.atbw", "KBBBvK.atbz", "KRvK.atbw"):
        if find_table_file(args.tables, name) is None:
            parser.error(
                f"required real Atomic fixture is missing from all --tables: {name}"
            )
    six_man = tuple(
        find_table_file(args.tables, name) for name in (KPPPPVK_WDL, KPPPPVK_DTZ)
    )
    if any(path is None for path in six_man) and any(path is not None for path in six_man):
        parser.error("KPPPPvK six-man fixture must provide both .atbw and .atbz")
    if args.require_six_man and any(path is None for path in six_man):
        parser.error(
            "--require-six-man needs KPPPPvK.atbw and KPPPPvK.atbz across --tables"
        )
    args.has_six_man = all(path is not None for path in six_man)
    if args.require_six_man and (
        args.syzygy_driver is None or not args.syzygy_driver.is_file()
    ):
        parser.error("--require-six-man requires an existing --syzygy-driver")
    if args.eval_file is not None and not args.eval_file.is_file():
        parser.error(f"network does not exist: {args.eval_file}")
    if args.analysis_book is not None and not args.analysis_book.is_file():
        parser.error(f"six-man analysis book does not exist: {args.analysis_book}")
    if args.analysis_book is not None and not args.require_six_man:
        parser.error("--analysis-book requires --require-six-man")
    if args.analysis_book is not None and not args.analysis_manifest.is_file():
        parser.error(
            f"six-man analysis manifest does not exist: {args.analysis_manifest}"
        )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--engine",
        type=Path,
        default=REPO_ROOT / "src" / "atomic-stockfish.exe",
    )
    parser.add_argument(
        "--tables",
        type=Path,
        action="append",
        help=(
            "Atomic table directory; repeat for split 3-4-5, 6-wdl and 6-dtz "
            "trees (default: the historical local fixture directory)"
        ),
    )
    parser.add_argument(
        "--require-six-man",
        action="store_true",
        help="require and exercise the real KPPPPvK six-man WDL/DTZ fixture",
    )
    parser.add_argument(
        "--syzygy-driver",
        type=Path,
        help=(
            "same-checkout atomic-syzygy-driver used to freeze KPPPPvK "
            "WDL=+2 and DTZ=1"
        ),
    )
    parser.add_argument(
        "--analysis-book",
        type=Path,
        nargs="?",
        const=DEFAULT_ANALYSIS_BOOK,
        help=(
            "also compare limit-6 and limit-0 tbhits for each FEN in a six-man "
            "book; without a value, use the repository's five-position fixture"
        ),
    )
    parser.add_argument(
        "--analysis-manifest",
        type=Path,
        default=DEFAULT_ANALYSIS_MANIFEST,
        help="oracle metadata for --analysis-book",
    )
    parser.add_argument(
        "--eval-file",
        type=Path,
        help="also repeat root/interior probes with Use NNUE=true",
    )
    parser.add_argument("--timeout", type=float, default=30.0)
    args = parser.parse_args()
    args.engine = args.engine.resolve()
    args.tables = normalize_table_dirs(args.tables or (DEFAULT_TABLES,))
    if args.eval_file is not None:
        args.eval_file = args.eval_file.resolve()
    if args.syzygy_driver is not None:
        args.syzygy_driver = args.syzygy_driver.expanduser().resolve()
    if args.analysis_book is not None:
        args.analysis_book = args.analysis_book.expanduser().resolve()
    args.analysis_manifest = args.analysis_manifest.expanduser().resolve()
    validate_inputs(parser, args)

    if args.require_six_man:
        assert args.syzygy_driver is not None
        test_six_man_driver(args.syzygy_driver, args.tables, args.timeout)

    with UciProcess(args.engine, args.timeout) as engine:
        check_uci_contract(engine.uci())
        engine.setoption("Threads", "1")
        engine.setoption("Hash", "16")
        engine.setoption("SyzygyProbeLimit", "6")
        engine.setoption("SyzygyProbeDepth", "1")
        engine.setoption("Syzygy50MoveRule", "true")
        engine.setoption("Use NNUE", "false")

        loaded = engine.setoption("SyzygyPath", syzygy_path_value(args.tables))
        assert_real_tables_loaded(loaded, args.tables)

        assert_root_probe(engine)
        test_interior_wdl(engine)
        test_terminal_without_king(engine)
        test_castling_eligibility(engine)
        assert_root_probe(engine, atomic960=True)
        if args.require_six_man:
            test_six_man_probe_limit(engine)
        if args.analysis_book is not None:
            analyze_six_man_book(
                engine,
                args.analysis_book,
                load_analysis_expectations(args.analysis_manifest),
            )

        if args.eval_file is not None:
            engine.setoption("EvalFile", str(args.eval_file))
            engine.setoption("Use NNUE", "true")
            nnue_root = assert_root_probe(engine)
            require(
                not any("ERROR:" in line for line in nnue_root.output),
                f"Use NNUE=true rejected the supplied network:\n{nnue_root.output}",
            )
            test_interior_wdl(engine)
            if args.require_six_man:
                test_six_man_probe_limit(engine)
            if args.analysis_book is not None:
                analyze_six_man_book(
                    engine,
                    args.analysis_book,
                    load_analysis_expectations(args.analysis_manifest),
                )
            engine.setoption("Use NNUE", "false")

        kbbb_wdl = find_table_file(args.tables, "KBBBvK.atbw")
        kbbb_dtz = find_table_file(args.tables, "KBBBvK.atbz")
        assert kbbb_wdl is not None and kbbb_dtz is not None
        test_recoverable_paths(
            engine,
            args.tables,
            kbbb_wdl=kbbb_wdl,
            kbbb_dtz=kbbb_dtz,
        )

    modes = "false/true" if args.eval_file is not None else "false"
    print(
        "Atomic Syzygy UCI tests passed: max6, load, root ranking, interior WDL, "
        "terminal, castling, Atomic960, recoverable paths, "
        f"six-man-limit={args.require_six_man}, analysis={args.analysis_book is not None}, "
        f"NNUE={modes}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
