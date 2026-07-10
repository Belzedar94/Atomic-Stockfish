#!/usr/bin/env python3
"""Focused differential contract for the core Atomic chess rules.

The constants in this file were recorded from the frozen Atomic-only Fairy
reference based on fairy-stockfish/Fairy-Stockfish@fb78cb561aa01708338e35b3dc3b65a42149a3c4.
The Windows release binary used to record them has SHA-256
0D3E8D511A2395E6372EEFF32DDBC06C0CA4CC1668EECBECA4B7483B04AEED56.

By default the script first proves that the constants still match that
reference, then applies the same contract to Atomic-Stockfish.  Every failure
includes the fixture FEN and a precise root-move or transition difference.

Examples:
    python tests/atomic_rules.py
    python tests/atomic_rules.py --candidate src/atomic-stockfish-rules.exe
    python tests/atomic_rules.py --reference-only
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Mapping, Sequence


REPO_ROOT = Path(__file__).resolve().parents[1]
WORKSPACE_ROOT = REPO_ROOT.parent


def root_counts(specification: str) -> dict[str, int]:
    """Turn a compact, reviewable ``move=count`` list into a mapping."""

    result: dict[str, int] = {}
    for item in specification.split():
        move, count = item.split("=", maxsplit=1)
        result[move] = int(count)
    return result


@dataclass(frozen=True)
class PerftFixture:
    name: str
    rule: str
    fen: str
    roots: Mapping[str, int]
    chess960: bool = False
    forbidden: tuple[str, ...] = ()

    @property
    def depth(self) -> int:
        return 2

    @property
    def nodes(self) -> int:
        return sum(self.roots.values())


@dataclass(frozen=True)
class TransitionFixture:
    name: str
    rule: str
    fen: str
    move: str
    expected_fen: str
    chess960: bool = False


@dataclass(frozen=True)
class TerminalFixture:
    name: str
    rule: str
    fen: str
    score_kind: str


PERFT_FIXTURES: tuple[PerftFixture, ...] = (
    PerftFixture(
        name="explosion_bycatch_and_pawn_immunity",
        rule=(
            "Qxd4 explodes the capturer, captured rook, and adjacent non-pawns; "
            "the pawns on c3 and c5 survive collateral damage"
        ),
        fen="7k/8/8/2pBn3/3r4/2PQN3/8/K7 w - - 0 1",
        roots=root_counts(
            """
            c3c4=16 c3d4=4 e3d1=19 e3f1=19 e3c2=19 e3g2=19
            e3c4=16 e3g4=18 e3f5=19 d5h1=23 d5a2=22 d5g2=23
            d5b3=22 d5f3=23 d5c4=19 d5e4=20 d5c6=23 d5e6=22
            d5b7=23 d5f7=22 d5a8=23 d5g8=22 d3b1=21 d3d1=22
            d3f1=22 d3c2=21 d3d2=21 d3e2=22 d3c4=19 d3d4=4
            d3e4=18 d3b5=22 d3f5=21 d3a6=22 d3g6=20 d3h7=0
            a1b1=19 a1a2=19 a1b2=19
            """
        ),
    ),
    PerftFixture(
        name="self_explosion_is_illegal",
        rule="Rxd2 is illegal because the blast on d2 would remove the white king on c1",
        fen="7k/8/8/8/8/8/3r4/2KR4 w - - 0 1",
        roots=root_counts("d1e1=17 d1f1=17 d1g1=15 d1h1=3 c1b1=17"),
        forbidden=("d1d2",),
    ),
    PerftFixture(
        name="adjacent_kings_are_legal",
        rule="adjacent kings do not attack by capture because either capture self-explodes",
        fen="8/8/8/8/8/8/4k3/4K3 w - - 0 1",
        roots=root_counts("e1d1=7 e1f1=7 e1d2=7 e1f2=7"),
        forbidden=("e1e2",),
    ),
    PerftFixture(
        name="atomic_en_passant",
        rule=(
            "e5xd6 e.p. removes both pawns and explodes the adjacent knights/bishops, "
            "while the collateral pawn on c5 survives"
        ),
        fen="7k/8/2N1b3/2ppP3/8/8/8/K7 w - d6 0 2",
        roots=root_counts(
            "e5d6=4 c6b4=13 c6d4=12 c6a5=12 c6a7=12 c6e7=11 "
            "c6b8=12 c6d8=12 a1b1=12 a1a2=12 a1b2=12"
        ),
    ),
    PerftFixture(
        name="explosion_clears_castling_right",
        rule="...Rxb1 explodes the a1 rook by collateral damage and clears white Q-side rights",
        fen="6k1/8/8/8/8/8/1r6/RN2K2R b KQ - 0 1",
        roots=root_counts(
            """
            b2b1=15 b2a2=16 b2c2=22 b2d2=20 b2e2=2 b2f2=20
            b2g2=21 b2h2=16 b2b3=25 b2b4=25 b2b5=25 b2b6=25
            b2b7=25 b2b8=25 g8f7=22 g8g7=22 g8f8=22
            """
        ),
    ),
    PerftFixture(
        name="capture_promotion_explodes",
        rule=(
            "all four g7xh8 promotion encodings are legal and the promoted capturer "
            "immediately explodes together with the h8 rook and g8 bishop"
        ),
        fen="k5br/6P1/8/8/8/8/8/K7 w - - 0 1",
        roots=root_counts(
            "g7h8q=3 g7h8r=3 g7h8b=3 g7h8n=3 a1b1=17 a1b2=17"
        ),
    ),
    PerftFixture(
        name="king_exploded_is_terminal",
        rule="Rxg8 explodes the adjacent black king on h8 and has no reply subtree",
        fen="6rk/8/8/8/8/8/8/K5R1 w - - 0 1",
        roots=root_counts(
            """
            g1b1=15 g1c1=15 g1d1=15 g1e1=15 g1f1=15 g1h1=1
            g1g2=13 g1g3=12 g1g4=11 g1g5=10 g1g6=9 g1g7=6
            g1g8=0 a1b1=14 a1a2=14 a1b2=14
            """
        ),
    ),
    PerftFixture(
        name="atomic_checkmate_has_no_moves",
        rule="the canonical Fairy Atomic checkmate position has no legal moves",
        fen="BQ6/Rk6/8/8/8/8/8/4K3 b - - 0 1",
        roots={},
    ),
    PerftFixture(
        name="atomic_stalemate_has_no_moves",
        rule="the canonical Fairy Atomic stalemate position has no legal moves",
        fen="KQ6/Rk6/2B5/8/8/8/8/8 b - - 0 1",
        roots={},
    ),
    PerftFixture(
        name="atomic960_castle_is_legal",
        rule="c1b1 is the legal Chess960 queen-side castling encoding",
        fen="7k/8/8/8/8/8/8/1RK5 w Q - 0 1",
        chess960=True,
        roots=root_counts(
            """
            b1a1=3 b1b2=3 b1b3=3 b1b4=3 b1b5=3 b1b6=3 b1b7=1
            b1b8=2 c1d1=3 c1b2=3 c1c2=3 c1d2=3 c1b1=3
            """
        ),
    ),
    PerftFixture(
        name="atomic960_castle_through_atomic_check_is_illegal",
        rule=(
            "c1b1 is forbidden when the h1 queen creates the Atomic anti-castling "
            "constraint; the ordinary king move c1b2 remains legal"
        ),
        fen="7k/8/8/8/8/8/2PP4/1RK4q w Q - 0 1",
        chess960=True,
        roots=root_counts("c1b2=22"),
        forbidden=("c1b1",),
    ),
)


TRANSITION_FIXTURES: tuple[TransitionFixture, ...] = (
    TransitionFixture(
        name="explosion_bycatch_and_pawn_immunity_after_qxd4",
        rule="only the two collateral-immune pawns and both kings remain",
        fen="7k/8/8/2pBn3/3r4/2PQN3/8/K7 w - - 0 1",
        move="d3d4",
        expected_fen="7k/8/8/2p5/8/2P5/8/K7 b - - 0 1",
    ),
    TransitionFixture(
        name="atomic_en_passant_after_e5d6",
        rule="the e.p. capturer, captured pawn, and adjacent non-pawns are all absent",
        fen="7k/8/2N1b3/2ppP3/8/8/8/K7 w - d6 0 2",
        move="e5d6",
        expected_fen="7k/8/8/2p5/8/8/8/K7 b - - 0 2",
    ),
    TransitionFixture(
        name="castling_right_after_rook_bycatch",
        rule="Q-side right disappears with a1 rook; K-side right remains",
        fen="6k1/8/8/8/8/8/1r6/RN2K2R b KQ - 0 1",
        move="b2b1",
        expected_fen="6k1/8/8/8/8/8/8/4K2R w K - 0 2",
    ),
    TransitionFixture(
        name="capture_promotion_after_g7h8q",
        rule="promotion choice does not survive an Atomic capture blast",
        fen="k5br/6P1/8/8/8/8/8/K7 w - - 0 1",
        move="g7h8q",
        expected_fen="k7/8/8/8/8/8/8/K7 b - - 0 1",
    ),
    TransitionFixture(
        name="king_explosion_after_g1g8",
        rule="terminal Atomic FEN is allowed to contain no black king",
        fen="6rk/8/8/8/8/8/8/K5R1 w - - 0 1",
        move="g1g8",
        expected_fen="8/8/8/8/8/8/8/K7 b - - 0 1",
    ),
    TransitionFixture(
        name="direct_king_capture_after_h7h8",
        rule=(
            "capturing a KING directly uses the same terminal explosion path and "
            "must not trigger orthodox captured-king assertions"
        ),
        fen="7k/7R/8/8/8/8/8/K7 w - - 0 1",
        move="h7h8",
        expected_fen="8/8/8/8/8/8/8/K7 b - - 0 1",
    ),
)


TERMINAL_FIXTURES: tuple[TerminalFixture, ...] = (
    TerminalFixture(
        name="atomic_checkmate_result",
        rule="no legal moves while in Atomic check is a loss",
        fen="BQ6/Rk6/8/8/8/8/8/4K3 b - - 0 1",
        score_kind="mate",
    ),
    TerminalFixture(
        name="atomic_stalemate_result",
        rule="no legal moves without Atomic check is a draw",
        fen="KQ6/Rk6/2B5/8/8/8/8/8 b - - 0 1",
        score_kind="cp",
    ),
)


ROOT_LINE = re.compile(r"^\s*([a-h][1-8][a-h][1-8][qrbn]?)\s*:\s*(\d+)\s*$")
NODE_LINE = re.compile(r"^\s*Nodes searched:\s*(\d+)\s*$")
FEN_LINE = re.compile(r"^Fen:\s*(.+?)\s*$")
SCORE_LINE = re.compile(r"\bscore\s+(mate|cp)\s+(-?\d+)\b")


class EngineFailure(RuntimeError):
    pass


def run_engine(engine: Path, commands: Sequence[str], timeout: float) -> str:
    command_stream = "\n".join(("uci", *commands, "quit", ""))
    startup: dict[str, object] = {}
    if os.name == "nt":
        startup["creationflags"] = subprocess.CREATE_NO_WINDOW

    try:
        completed = subprocess.run(
            [str(engine)],
            input=command_stream,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
            **startup,
        )
    except subprocess.TimeoutExpired as error:
        partial = error.stdout or ""
        raise EngineFailure(
            f"engine timed out after {timeout:g}s\n{partial}"
        ) from error
    except OSError as error:
        raise EngineFailure(f"could not start engine: {error}") from error

    if completed.returncode != 0:
        raise EngineFailure(
            f"engine exited with code {completed.returncode}\n{completed.stdout}"
        )
    return completed.stdout


def option_commands(chess960: bool) -> tuple[str, ...]:
    return (
        "setoption name UCI_Variant value atomic",
        "setoption name Use NNUE value false",
        f"setoption name UCI_Chess960 value {'true' if chess960 else 'false'}",
        "isready",
    )


def parse_perft(output: str) -> tuple[dict[str, int], int]:
    roots: dict[str, int] = {}
    nodes: int | None = None
    for line in output.splitlines():
        if match := ROOT_LINE.match(line):
            roots[match.group(1)] = int(match.group(2))
        elif match := NODE_LINE.match(line):
            nodes = int(match.group(1))
    if nodes is None:
        raise EngineFailure(f"perft output has no node total\n{output}")
    return roots, nodes


def parse_fen(output: str) -> str:
    fens = [match.group(1) for line in output.splitlines() if (match := FEN_LINE.match(line))]
    if not fens:
        raise EngineFailure(f"diagnostic output has no Fen line\n{output}")
    return fens[-1]


def compare_mapping(expected: Mapping[str, int], actual: Mapping[str, int]) -> list[str]:
    errors: list[str] = []
    missing = sorted(set(expected) - set(actual))
    extra = sorted(set(actual) - set(expected))
    changed = sorted(
        move for move in set(expected) & set(actual) if expected[move] != actual[move]
    )
    if missing:
        errors.append(f"missing root moves: {' '.join(missing)}")
    if extra:
        errors.append(f"extra root moves: {' '.join(extra)}")
    if changed:
        details = " ".join(
            f"{move}={actual[move]} (expected {expected[move]})" for move in changed
        )
        errors.append(f"wrong root counts: {details}")
    return errors


def check_perft(engine: Path, fixture: PerftFixture, timeout: float) -> list[str]:
    output = run_engine(
        engine,
        (
            *option_commands(fixture.chess960),
            f"position fen {fixture.fen}",
            f"go perft {fixture.depth}",
        ),
        timeout,
    )
    roots, nodes = parse_perft(output)
    errors = compare_mapping(fixture.roots, roots)
    if nodes != fixture.nodes:
        errors.append(f"nodes={nodes} (expected {fixture.nodes})")
    for move in fixture.forbidden:
        if move in roots:
            errors.append(f"forbidden move is legal: {move}")
    return errors


def check_transition(engine: Path, fixture: TransitionFixture, timeout: float) -> list[str]:
    output = run_engine(
        engine,
        (
            *option_commands(fixture.chess960),
            f"position fen {fixture.fen} moves {fixture.move}",
            "d",
        ),
        timeout,
    )
    actual = parse_fen(output)
    if actual == fixture.expected_fen:
        return []
    return [f"after {fixture.move}: {actual} (expected {fixture.expected_fen})"]


def check_terminal(engine: Path, fixture: TerminalFixture, timeout: float) -> list[str]:
    output = run_engine(
        engine,
        (
            *option_commands(False),
            f"position fen {fixture.fen}",
            "go depth 1",
        ),
        timeout,
    )
    errors: list[str] = []
    if not re.search(r"^bestmove\s+\(none\)\s*$", output, re.MULTILINE):
        errors.append("terminal position did not return bestmove (none)")
    scores = SCORE_LINE.findall(output)
    if not scores:
        errors.append("terminal search emitted no UCI score")
    else:
        kind, value = scores[-1]
        if kind != fixture.score_kind or int(value) != 0:
            errors.append(
                f"terminal score={kind} {value} (expected {fixture.score_kind} 0)"
            )
    return errors


def report_failure(kind: str, name: str, rule: str, fen: str, errors: Iterable[str]) -> None:
    print(f"  FAIL [{kind}] {name}")
    print(f"       rule: {rule}")
    print(f"       FEN:  {fen}")
    for error in errors:
        print(f"       {error}")


def run_contract(label: str, engine: Path, timeout: float) -> int:
    print(f"\n{label}: {engine}")
    failures = 0

    checks: tuple[tuple[str, object], ...] = (
        *(("perft", fixture) for fixture in PERFT_FIXTURES),
        *(("transition", fixture) for fixture in TRANSITION_FIXTURES),
        *(("terminal", fixture) for fixture in TERMINAL_FIXTURES),
    )
    for kind, fixture in checks:
        try:
            if kind == "perft":
                errors = check_perft(engine, fixture, timeout)  # type: ignore[arg-type]
            elif kind == "transition":
                errors = check_transition(engine, fixture, timeout)  # type: ignore[arg-type]
            else:
                errors = check_terminal(engine, fixture, timeout)  # type: ignore[arg-type]
        except EngineFailure as error:
            errors = [str(error)]

        if errors:
            failures += 1
            report_failure(kind, fixture.name, fixture.rule, fixture.fen, errors)
        else:
            print(f"  PASS [{kind}] {fixture.name}")

    total = len(checks)
    print(f"{label}: {total - failures}/{total} checks passed")
    return failures


def existing_default(candidates: Sequence[Path]) -> Path:
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return candidates[0]


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    suffix = ".exe" if os.name == "nt" else ""
    default_reference = existing_default(
        (
            WORKSPACE_ROOT
            / "Fairy-Stockfish-atomic-reference"
            / "src"
            / f"Fairy-Atomic-Reference{suffix}",
            WORKSPACE_ROOT
            / "Fairy-Stockfish-atomic-reference"
            / "src"
            / f"Fairy-Atomic-Reference-debug{suffix}",
        )
    )
    default_candidate = existing_default(
        (
            REPO_ROOT / "src" / f"atomic-stockfish{suffix}",
            REPO_ROOT / "src" / f"atomic-stockfish-rules-debug{suffix}",
            REPO_ROOT / "src" / f"atomic-stockfish-rules{suffix}",
            REPO_ROOT / "src" / f"stockfish{suffix}",
        )
    )

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reference", type=Path, default=default_reference)
    parser.add_argument("--candidate", type=Path, default=default_candidate)
    parser.add_argument("--reference-only", action="store_true")
    parser.add_argument("--candidate-only", action="store_true")
    parser.add_argument("--timeout", type=float, default=30.0)
    args = parser.parse_args(argv)
    if args.reference_only and args.candidate_only:
        parser.error("--reference-only and --candidate-only are mutually exclusive")
    if args.timeout <= 0:
        parser.error("--timeout must be positive")
    return args


def validate_engine_path(parser_name: str, path: Path) -> Path:
    resolved = path.expanduser().resolve()
    if not resolved.is_file():
        raise SystemExit(f"{parser_name} engine does not exist: {resolved}")
    return resolved


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    failures = 0

    if not args.candidate_only:
        reference = validate_engine_path("reference", args.reference)
        failures += run_contract("Frozen Fairy reference", reference, args.timeout)
        if failures:
            print("\nReference drift detected; candidate results are not trustworthy.")
            return 1

    if not args.reference_only:
        candidate = validate_engine_path("candidate", args.candidate)
        failures += run_contract("Atomic-Stockfish candidate", candidate, args.timeout)

    if failures:
        print(f"\nAtomic rules contract FAILED ({failures} fixture failures).")
        return 1
    print("\nAtomic rules contract passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
