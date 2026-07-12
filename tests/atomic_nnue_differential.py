#!/usr/bin/env python3
"""Differential Legacy Atomic V1 evaluation test against frozen Fairy.

The default run builds a deterministic 10,000-position corpus by playing legal
moves reported by the Fairy oracle. Games rotate through Atomic startpos and
focused Atomic/Atomic960 fixtures, and the seeded random choice is made from a
sorted legal-move list so the corpus is reproducible across machines.

Three contracts are checked for every position:

* ``Use NNUE=true``: candidate final evaluation versus Fairy final evaluation.
* ``Use NNUE=pure``: candidate raw Legacy Atomic V1 result versus Fairy's
  unadjusted ``NNUE evaluation`` trace line.
* At or beyond Atomic's 100-ply rule-50 boundary, the candidate's normal
  playing trace must be neutral. The companion C++ gate proves the internal
  ``Value`` is exactly zero. Fairy's historical evaluator can reverse sign
  beyond that boundary, so its final value is diagnostic there; the raw-network
  comparison remains active.

The frozen Fairy oracle exposes ``Use NNUE`` only as a boolean. It does not
offer a reliable ``pure`` protocol mode or exact internal raw integers. Its
trace does expose the unadjusted network result, but rounded to 0.01 pawn.
Accordingly, the pure differential is explicitly a trace-precision contract;
the script also checks candidate raw internal units are identical in ``true``
and ``pure`` and that the candidate pure final output is the same raw result.
An instrumented Fairy oracle would still be required for bit-exact raw-output
comparison. This limitation is reported at the end of every successful run.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import queue
import random
import re
import subprocess
import sys
import threading
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Sequence


REPO_ROOT = Path(__file__).resolve().parents[1]
WORKSPACE_ROOT = REPO_ROOT.parent
LEGACY_NET_SHA256 = "99dc67eabf26a64faeeca3a88b4c38597a840b8d4a874b9f2cf658c6f92a04a6"
LEGACY_PAWN_VALUE = 208

MOVE_RE = re.compile(r"^\s*([a-h][1-8][a-h][1-8][qrbn]?)\s*:\s*\d+\s*$")
NODES_RE = re.compile(r"^\s*Nodes searched:\s*\d+\s*$")
FEN_RE = re.compile(r"^Fen:\s*(.+?)\s*$")
OPTION_RE = re.compile(r"^option name (.+?) type (\S+)(?:\s|$)")
NUMBER = r"([+-]?(?:\d+(?:\.\d*)?|\.\d+))"
FINAL_RE = re.compile(rf"^Final evaluation\s+{NUMBER}\s+\(white side\)(.*)$")
RAW_WHITE_RE = re.compile(rf"^NNUE evaluation\s+{NUMBER}\s+\(white side\)\s*$")
RAW_INTERNAL_RE = re.compile(
    r"^NNUE evaluation\s+([+-]?\d+)\s+\(side to move, internal units\)\s*$"
)
MODE_RE = re.compile(r"\[Use NNUE=(false|true|pure)\]")


@dataclass(frozen=True)
class StartPosition:
    name: str
    specification: str
    chess960: bool = False


START_POSITIONS: tuple[StartPosition, ...] = (
    StartPosition("atomic_startpos", "startpos"),
    StartPosition(
        "explosion_bycatch",
        "fen 7k/8/8/2pBn3/3r4/2PQN3/8/K7 w - - 0 1",
    ),
    StartPosition(
        "atomic_en_passant",
        "fen 7k/8/2N1b3/2ppP3/8/8/8/K7 w - d6 0 2",
    ),
    StartPosition(
        "capture_promotion",
        "fen k5br/6P1/8/8/8/8/8/K7 w - - 0 1",
    ),
    StartPosition(
        "adjacent_kings",
        "fen 8/8/8/8/8/8/4k3/4K3 w - - 0 1",
    ),
    StartPosition(
        "atomic960_castling",
        "fen 7k/8/8/8/8/8/8/1RK5 w Q - 0 1",
        chess960=True,
    ),
    StartPosition(
        "atomic960_castling_constraint",
        "fen 7k/8/8/8/8/8/2PP4/1RK4q w Q - 0 1",
        chess960=True,
    ),
)


@dataclass(frozen=True)
class CorpusPosition:
    fen: str
    chess960: bool
    source: str


@dataclass(frozen=True)
class EvalResult:
    final_white: float
    raw_white: float | None
    raw_internal_stm: int | None
    mode: str | None


class EngineFailure(RuntimeError):
    pass


class UciProcess:
    def __init__(self, executable: Path, timeout: float, label: str) -> None:
        self.executable = executable
        self.timeout = timeout
        self.label = label
        self.current_chess960: bool | None = None

        startup: dict[str, object] = {}
        if os.name == "nt":
            startup["creationflags"] = subprocess.CREATE_NO_WINDOW

        try:
            self.process = subprocess.Popen(
                [str(executable)],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                **startup,
            )
        except OSError as exc:
            raise EngineFailure(f"could not start {label} ({executable}): {exc}") from exc

        assert self.process.stdin is not None
        assert self.process.stdout is not None
        self.lines: queue.Queue[str | None] = queue.Queue()
        self.reader = threading.Thread(target=self._read_output, daemon=True)
        self.reader.start()

        self.send("uci")
        uci_output = self.read_until(lambda line: line == "uciok")
        self.options = self._parse_options(uci_output)
        self._require_option("UCI_Variant")
        self._require_option("UCI_Chess960")

    def _read_output(self) -> None:
        assert self.process.stdout is not None
        for line in self.process.stdout:
            self.lines.put(line.rstrip("\r\n"))
        self.lines.put(None)

    @staticmethod
    def _parse_options(lines: Sequence[str]) -> dict[str, tuple[str, str]]:
        options: dict[str, tuple[str, str]] = {}
        for line in lines:
            match = OPTION_RE.match(line)
            if match:
                options[match.group(1).lower()] = (match.group(1), match.group(2))
        return options

    def _require_option(self, name: str) -> tuple[str, str]:
        try:
            return self.options[name.lower()]
        except KeyError as exc:
            raise EngineFailure(
                f"{self.label} does not expose required UCI option {name!r}"
            ) from exc

    def send(self, command: str) -> None:
        if self.process.poll() is not None:
            raise EngineFailure(
                f"{self.label} exited with code {self.process.returncode} before {command!r}"
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
                raise EngineFailure(
                    f"timed out after {self.timeout:g}s waiting for {self.label}; "
                    f"last output: {output[-12:]}"
                ) from exc
            if line is None:
                raise EngineFailure(
                    f"{self.label} exited with code {self.process.poll()}; "
                    f"last output: {output[-12:]}"
                )
            output.append(line)
            if predicate(line):
                return output

    def ready(self) -> list[str]:
        self.send("isready")
        return self.read_until(lambda line: line == "readyok")

    def set_option(self, name: str, value: str) -> None:
        self._require_option(name)
        self.send(f"setoption name {name} value {value}")

    def configure_atomic(self, *, net: Path | None, mode: str | None) -> None:
        self.set_option("UCI_Variant", "atomic")
        if net is not None:
            self._require_option("EvalFile")
            self.set_option("EvalFile", str(net))
        if mode is not None:
            _, option_type = self._require_option("Use NNUE")
            if mode == "pure" and option_type != "combo":
                raise EngineFailure(
                    f"{self.label} exposes Use NNUE as {option_type}, not a pure-capable combo"
                )
            self.set_option("Use NNUE", mode)
        self.ready()

    def set_chess960(self, enabled: bool) -> None:
        if self.current_chess960 == enabled:
            return
        self.set_option("UCI_Chess960", "true" if enabled else "false")
        self.current_chess960 = enabled
        self.ready()

    def new_game(self, chess960: bool) -> None:
        self.set_chess960(chess960)
        self.send("ucinewgame")
        self.ready()

    def describe(self, position_command: str) -> tuple[str, list[str]]:
        self.send(position_command)
        self.send("d")
        self.send("go perft 1")
        output = self.read_until(lambda line: bool(NODES_RE.match(line)))

        fen: str | None = None
        moves: list[str] = []
        for line in output:
            if match := FEN_RE.match(line):
                fen = match.group(1)
            elif match := MOVE_RE.match(line):
                moves.append(match.group(1))
        if fen is None:
            raise EngineFailure(
                f"{self.label} did not report a FEN for {position_command!r}: {output[-20:]}"
            )
        return fen, sorted(moves)

    def evaluate(self, position: CorpusPosition) -> EvalResult:
        self.set_chess960(position.chess960)
        self.send(f"position fen {position.fen}")
        self.send("eval")
        output = self.ready()

        final_white: float | None = None
        raw_white: float | None = None
        raw_internal: int | None = None
        mode: str | None = None
        for line in output:
            stripped = line.strip()
            if match := FINAL_RE.match(stripped):
                final_white = float(match.group(1))
                if mode_match := MODE_RE.search(match.group(2)):
                    mode = mode_match.group(1)
            elif match := RAW_WHITE_RE.match(stripped):
                raw_white = float(match.group(1))
            elif match := RAW_INTERNAL_RE.match(stripped):
                raw_internal = int(match.group(1))

        if final_white is None:
            excerpt = "\n".join(output[-30:])
            raise EngineFailure(
                f"{self.label} produced no numeric final evaluation for {position.fen}:\n{excerpt}"
            )
        return EvalResult(final_white, raw_white, raw_internal, mode)

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


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def has_both_kings(fen: str) -> bool:
    board = fen.split(maxsplit=1)[0]
    return board.count("K") == 1 and board.count("k") == 1


def build_corpus(
    generator: UciProcess,
    *,
    target: int,
    seed: int,
    max_plies: int,
    max_games: int,
) -> tuple[list[CorpusPosition], Counter[str]]:
    rng = random.Random(seed)
    corpus: list[CorpusPosition] = []
    seen: set[tuple[bool, str]] = set()
    source_counts: Counter[str] = Counter()

    for game_index in range(max_games):
        if len(corpus) >= target:
            break

        start = START_POSITIONS[game_index % len(START_POSITIONS)]
        generator.new_game(start.chess960)
        command = f"position {start.specification}"

        for _ply in range(max_plies + 1):
            fen, legal_moves = generator.describe(command)

            if not has_both_kings(fen):
                break

            key = (start.chess960, fen)
            if key not in seen:
                seen.add(key)
                corpus.append(CorpusPosition(fen, start.chess960, start.name))
                source_counts[start.name] += 1
                if len(corpus) >= target:
                    break

            if not legal_moves:
                break

            move = rng.choice(legal_moves)
            command = f"position fen {fen} moves {move}"

    if len(corpus) != target:
        raise EngineFailure(
            f"generated only {len(corpus)} unique non-terminal positions, expected {target}; "
            f"increase --max-games or --max-plies"
        )
    return corpus, source_counts


def corpus_sha256(corpus: Sequence[CorpusPosition]) -> str:
    digest = hashlib.sha256()
    for position in corpus:
        digest.update(("960" if position.chess960 else "atomic").encode("ascii"))
        digest.update(b"\0")
        digest.update(position.fen.encode("ascii"))
        digest.update(b"\n")
    return digest.hexdigest()


def halfmove_clock(position: CorpusPosition) -> int:
    fields = position.fen.split()
    if len(fields) != 6:
        raise EngineFailure(
            f"corpus position has {len(fields)} FEN fields instead of 6: {position.fen}"
        )
    try:
        value = int(fields[4])
    except ValueError as exc:
        raise EngineFailure(
            f"corpus position has a non-numeric halfmove clock: {position.fen}"
        ) from exc
    if value < 0:
        raise EngineFailure(f"corpus position has a negative halfmove clock: {position.fen}")
    return value


def compare_corpus(
    corpus: Sequence[CorpusPosition],
    *,
    candidate_true: UciProcess,
    candidate_pure: UciProcess,
    oracle: UciProcess,
    true_tolerance: float,
    pure_tolerance: float,
    max_errors: int,
    progress: int,
) -> tuple[float, float, float, int]:
    errors: list[str] = []
    max_true_error = 0.0
    max_rule50_oracle_delta = 0.0
    max_pure_error = 0.0
    rule50_damped = 0

    with ThreadPoolExecutor(max_workers=3, thread_name_prefix="atomic-nnue-diff") as executor:
        for index, position in enumerate(corpus, start=1):
            oracle_future = executor.submit(oracle.evaluate, position)
            true_future = executor.submit(candidate_true.evaluate, position)
            pure_future = executor.submit(candidate_pure.evaluate, position)
            oracle_eval = oracle_future.result()
            true_eval = true_future.result()
            pure_eval = pure_future.result()

            position_errors: list[str] = []
            if true_eval.mode != "true":
                position_errors.append(f"candidate true trace reported mode={true_eval.mode!r}")
            if pure_eval.mode != "pure":
                position_errors.append(f"candidate pure trace reported mode={pure_eval.mode!r}")
            if oracle_eval.raw_white is None:
                position_errors.append("Fairy trace exposed no unadjusted NNUE evaluation")
            if true_eval.raw_internal_stm is None or pure_eval.raw_internal_stm is None:
                position_errors.append("candidate trace exposed no raw internal NNUE value")
            elif true_eval.raw_internal_stm != pure_eval.raw_internal_stm:
                position_errors.append(
                    "candidate raw internal value differs by mode: "
                    f"true={true_eval.raw_internal_stm}, pure={pure_eval.raw_internal_stm}"
                )

            clock = halfmove_clock(position)
            at_rule50_boundary = clock >= 100
            true_error = abs(true_eval.final_white - oracle_eval.final_white)
            if at_rule50_boundary:
                rule50_damped += 1
                max_rule50_oracle_delta = max(max_rule50_oracle_delta, true_error)
                if true_eval.final_white != 0.0:
                    position_errors.append(
                        "candidate true trace is not neutral at the Atomic rule-50 boundary: "
                        f"candidate={true_eval.final_white:+.6f}, "
                        f"halfmove={clock}"
                    )
            else:
                max_true_error = max(max_true_error, true_error)
                if true_error > true_tolerance:
                    position_errors.append(
                        f"true final differs by {true_error:.6f}: "
                        f"candidate={true_eval.final_white:+.6f}, "
                        f"oracle={oracle_eval.final_white:+.6f}"
                    )

            if pure_eval.raw_internal_stm is not None and oracle_eval.raw_white is not None:
                side_sign = 1 if position.fen.split()[1] == "w" else -1
                candidate_raw_white = (
                    side_sign * pure_eval.raw_internal_stm / LEGACY_PAWN_VALUE
                )
                pure_error = abs(candidate_raw_white - oracle_eval.raw_white)
                max_pure_error = max(max_pure_error, pure_error)
                if pure_error > pure_tolerance:
                    position_errors.append(
                        f"pure raw trace differs by {pure_error:.6f}: "
                        f"candidate={candidate_raw_white:+.6f}, "
                        f"oracle={oracle_eval.raw_white:+.6f}"
                    )

                # The trace formats the final value to two decimal places. Do
                # not reconstruct iostream's floating-point rounding with C++
                # integer truncation: that produced false failures around the
                # half-centipawn boundary. The unrounded raw value must instead
                # agree with the displayed pure result to trace precision.
                pure_display_error = abs(pure_eval.final_white - candidate_raw_white)
                if pure_display_error > 0.0051:
                    position_errors.append(
                        "candidate pure final is inconsistent with its raw internal result: "
                        f"final={pure_eval.final_white:+.6f}, "
                        f"raw={candidate_raw_white:+.6f}, "
                        f"display_delta={pure_display_error:.6f}"
                    )

            if position_errors:
                errors.append(
                    f"#{index} source={position.source} chess960={position.chess960}\n"
                    f"FEN: {position.fen}\n  " + "\n  ".join(position_errors)
                )
                if len(errors) >= max_errors:
                    break

            if progress and (index % progress == 0 or index == len(corpus)):
                print(
                    f"Checked {index}/{len(corpus)} positions; "
                    f"max true delta={max_true_error:.6f}, "
                    f"max pure-trace delta={max_pure_error:.6f}",
                    flush=True,
                )

    if errors:
        raise AssertionError(
            f"Legacy Atomic V1 differential failed ({len(errors)} shown, "
            f"limit {max_errors}):\n\n" + "\n\n".join(errors)
        )
    return max_true_error, max_rule50_oracle_delta, max_pure_error, rule50_damped


def existing_file(parser: argparse.ArgumentParser, value: Path, label: str) -> Path:
    path = value.resolve()
    if not path.is_file():
        parser.error(f"{label} does not exist: {path}")
    return path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("candidate", type=Path)
    parser.add_argument("oracle", type=Path)
    parser.add_argument(
        "--net",
        type=Path,
        default=WORKSPACE_ROOT / "atomic_run3b_e202_l05.nnue",
    )
    parser.add_argument("--positions", type=int, default=10_000)
    parser.add_argument("--seed", type=int, default=20260710)
    parser.add_argument("--max-plies", type=int, default=120)
    parser.add_argument("--max-games", type=int, default=50_000)
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument(
        "--true-tolerance",
        type=float,
        default=0.011,
        help="maximum pawn-unit delta after the engines' two-decimal UCI formatting",
    )
    parser.add_argument(
        "--pure-tolerance",
        type=float,
        default=0.0051,
        help="maximum pawn-unit delta against Fairy's two-decimal raw trace",
    )
    parser.add_argument("--max-errors", type=int, default=20)
    parser.add_argument("--progress", type=int, default=250)
    args = parser.parse_args()

    for name in ("positions", "max_plies", "max_games", "max_errors"):
        if getattr(args, name) <= 0:
            parser.error(f"--{name.replace('_', '-')} must be positive")
    if args.timeout <= 0 or args.true_tolerance < 0 or args.pure_tolerance < 0:
        parser.error("timeout must be positive and tolerances must be non-negative")
    if args.progress < 0:
        parser.error("--progress must be non-negative")

    candidate_path = existing_file(parser, args.candidate, "candidate")
    oracle_path = existing_file(parser, args.oracle, "oracle")
    net_path = existing_file(parser, args.net, "network")

    net_sha = sha256_file(net_path)
    if net_sha.lower() != LEGACY_NET_SHA256:
        parser.error(
            f"network SHA-256 mismatch: expected {LEGACY_NET_SHA256.upper()}, "
            f"got {net_sha.upper()} ({net_path})"
        )

    print(f"Candidate: {candidate_path}")
    print(f"Fairy oracle: {oracle_path}")
    print(f"Legacy net: {net_path}")
    print(f"Legacy net SHA-256: {net_sha.upper()}")
    print(
        f"Corpus: positions={args.positions}, seed={args.seed}, "
        f"max_plies={args.max_plies}"
    )

    try:
        with ExitStack() as stack:
            generator = stack.enter_context(
                UciProcess(oracle_path, args.timeout, "Fairy generator")
            )
            generator.configure_atomic(net=None, mode="false")

            candidate_true = stack.enter_context(
                UciProcess(candidate_path, args.timeout, "Atomic candidate true")
            )
            candidate_true.configure_atomic(net=net_path, mode="true")

            candidate_pure = stack.enter_context(
                UciProcess(candidate_path, args.timeout, "Atomic candidate pure")
            )
            candidate_pure.configure_atomic(net=net_path, mode="pure")

            oracle = stack.enter_context(UciProcess(oracle_path, args.timeout, "Fairy oracle"))
            oracle.configure_atomic(net=net_path, mode="true")

            corpus, source_counts = build_corpus(
                generator,
                target=args.positions,
                seed=args.seed,
                max_plies=args.max_plies,
                max_games=args.max_games,
            )
            print(
                "Corpus sources: "
                + ", ".join(f"{name}={source_counts[name]}" for name in sorted(source_counts))
            )
            print(f"Corpus SHA-256: {corpus_sha256(corpus).upper()}")

            (
                max_true_error,
                max_rule50_oracle_delta,
                max_pure_error,
                rule50_damped,
            ) = compare_corpus(
                corpus,
                candidate_true=candidate_true,
                candidate_pure=candidate_pure,
                oracle=oracle,
                true_tolerance=args.true_tolerance,
                pure_tolerance=args.pure_tolerance,
                max_errors=args.max_errors,
                progress=args.progress,
            )
    except (EngineFailure, AssertionError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    print(
        f"Legacy Atomic V1 differential passed: {len(corpus)}/{len(corpus)} positions; "
        f"max true delta={max_true_error:.6f}; "
        f"max rule50 oracle delta={max_rule50_oracle_delta:.6f}; "
        f"max pure-trace delta={max_pure_error:.6f}; "
        f"rule50-damped={rule50_damped}"
    )
    print(
        "Pure limitation: frozen Fairy exposes only a two-decimal unadjusted NNUE trace; "
        "pure equivalence is trace-precision plus candidate internal-consistency, not a "
        "bit-exact raw-oracle comparison."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
