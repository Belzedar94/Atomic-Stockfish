#!/usr/bin/env python3
"""Compare Atomic-Stockfish with the frozen Fairy-Stockfish speed baseline.

The two engines search the same fixed Atomic/Atomic960 FEN corpus through UCI.
This deliberately avoids each engine's private ``bench`` implementation.
"""

from __future__ import annotations

import argparse
import hashlib
import queue
import statistics
import subprocess
import threading
from contextlib import ExitStack
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import psutil


EXPECTED_NET_SHA256 = "99dc67eabf26a64faeeca3a88b4c38597a840b8d4a874b9f2cf658c6f92a04a6"
MEASURED_REPETITIONS = 5


@dataclass(frozen=True)
class CorpusPosition:
    name: str
    fen: str
    chess960: bool = False


# Rich middlegames dominate the corpus so every search consumes its complete
# node budget. The three Atomic960 roots also exercise specialized castling
# state without letting a different built-in bench corpus bias either engine.
CORPUS = (
    CorpusPosition(
        "atomic-start",
        "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1",
    ),
    CorpusPosition(
        "atomic-open-1",
        "rnbqkbnr/pppp1ppp/8/4p3/4P3/8/PPPP1PPP/RNBQKBNR w KQkq e6 0 2",
    ),
    CorpusPosition(
        "atomic-open-2",
        "rnbqkbnr/pppp1ppp/8/4p3/4P3/5N2/PPPP1PPP/RNBQKB1R b KQkq - 1 2",
    ),
    CorpusPosition(
        "atomic-mid-1",
        "rnbqkbn1/2pppp2/7r/6pp/pPP3PP/P4P2/3PP3/RNB1KBNR w KQq - 1 10",
    ),
    CorpusPosition(
        "atomic-mid-2",
        "rn1qkb1r/p5pp/2p5/3p4/N3P3/5P2/PPP4P/R1BQK3 w Qkq - 0 1",
    ),
    CorpusPosition(
        "atomic-mid-3",
        "r4b1r/2kb1N2/p2Bpnp1/8/2Pp3p/1P1PPP2/P5PP/R3K2R b KQ - 0 1",
    ),
    CorpusPosition(
        "atomic-mid-4",
        "Rn6/1rbq1bk1/2p2n1p/2Bp1p2/3Pp1pP/1N2P1P1/2Q1NPB1/6K1 w - - 2 26",
    ),
    CorpusPosition(
        "atomic-mid-5",
        "rnbqkb1r/ppp1pp2/5n1p/3p2p1/P2PP3/5P2/1PP3PP/RNBQKBNR w KQkq - 0 3",
    ),
    CorpusPosition(
        "atomic-mid-6",
        "rn1qk1n1/2p1pp1r/p3b1pp/1p1p4/PP1b2PP/R3NN2/2PPPP2/2BQKB1R w Kq - 1 10",
    ),
    CorpusPosition(
        "atomic-mid-7",
        "r4rk1/1b2ppbp/pq4pn/2pp1PB1/1p2P3/1P1P1NN1/1PP3PP/R2Q1RK1 w - - 0 13",
    ),
    CorpusPosition(
        "atomic960-start-1",
        "bbqnnrkr/pppppppp/8/8/8/8/PPPPPPPP/BBQNNRKR w HFhf - 0 1",
        True,
    ),
    CorpusPosition(
        "atomic960-start-2",
        "nrqbbkrn/pppppppp/8/8/8/8/PPPPPPPP/NRQBBKRN w GBgb - 0 1",
        True,
    ),
    CorpusPosition(
        "atomic960-open",
        "bbqnnrkr/pppp1ppp/8/4p3/4P3/5N2/PPPP1PPP/BBQN1RKR b HFhf - 1 2",
        True,
    ),
)


@dataclass(frozen=True)
class SearchResult:
    nodes: int
    elapsed_ms: int


@dataclass(frozen=True)
class Measurement:
    nodes: int
    elapsed_ms: int

    @property
    def nps(self) -> float:
        return 1000.0 * self.nodes / self.elapsed_ms


class UciEngine:
    def __init__(
        self,
        label: str,
        executable: Path,
        net: Path,
        hash_mb: int,
        affinity: int,
        timeout: float,
    ) -> None:
        self.label = label
        self.timeout = timeout
        self.chess960: bool | None = None
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
        psutil.Process(self.process.pid).cpu_affinity([affinity])
        assert self.process.stdin is not None and self.process.stdout is not None
        self.lines: queue.Queue[str | None] = queue.Queue()
        threading.Thread(target=self._reader, daemon=True).start()

        self.send("uci")
        uci_output = self.read_until(lambda line: line == "uciok")
        options = {
            line[len("option name ") : line.index(" type ")].casefold()
            for line in uci_output
            if line.startswith("option name ") and " type " in line
        }
        required = {
            "threads",
            "hash",
            "clear hash",
            "uci_chess960",
            "uci_variant",
            "use nnue",
            "evalfile",
        }
        missing = sorted(required - options)
        if missing:
            raise RuntimeError(f"{label} is missing UCI options: {', '.join(missing)}")

        self.send("setoption name UCI_Variant value atomic")
        self.send("setoption name Threads value 1")
        self.send(f"setoption name Hash value {hash_mb}")
        self.send("setoption name Ponder value false")
        self.send("setoption name MultiPV value 1")
        self.send("setoption name Use NNUE value true")
        self.send(f"setoption name EvalFile value {net}")
        self.set_chess960(False)
        self.ready()

    def __enter__(self) -> UciEngine:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def _reader(self) -> None:
        assert self.process.stdout is not None
        for line in self.process.stdout:
            self.lines.put(line.rstrip("\r\n"))
        self.lines.put(None)

    def send(self, command: str) -> None:
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
                    f"{self.label} timed out; last output={output[-10:]}"
                ) from exc
            if line is None:
                raise RuntimeError(
                    f"{self.label} exited with {self.process.poll()}; "
                    f"last output={output[-10:]}"
                )
            output.append(line)
            if predicate(line):
                return output

    def ready(self) -> None:
        self.send("isready")
        self.read_until(lambda line: line == "readyok")

    def set_chess960(self, enabled: bool) -> None:
        if self.chess960 != enabled:
            self.send(
                f"setoption name UCI_Chess960 value {'true' if enabled else 'false'}"
            )
            self.chess960 = enabled

    def reset(self) -> None:
        self.send("ucinewgame")
        self.send("setoption name Clear Hash")
        self.ready()

    def search(self, position: CorpusPosition, node_budget: int) -> SearchResult:
        self.set_chess960(position.chess960)
        self.send(f"position fen {position.fen}")
        self.send(f"go nodes {node_budget}")
        output = self.read_until(lambda line: line.startswith("bestmove "))

        for line in reversed(output):
            if not line.startswith("info "):
                continue
            fields = line.split()
            try:
                nodes = int(fields[fields.index("nodes") + 1])
                elapsed_ms = int(fields[fields.index("time") + 1])
            except (ValueError, IndexError):
                continue
            if nodes < node_budget:
                raise AssertionError(
                    f"{self.label}/{position.name} stopped at {nodes} nodes before "
                    f"the {node_budget}-node budget; the corpus is not comparable"
                )
            if elapsed_ms <= 0:
                raise AssertionError(
                    f"{self.label}/{position.name} reported {elapsed_ms} ms; "
                    "increase --nodes"
                )
            return SearchResult(nodes, elapsed_ms)

        raise AssertionError(
            f"{self.label}/{position.name} produced no final nodes/time info: "
            f"{output[-10:]}"
        )

    def run_corpus(self, node_budget: int) -> Measurement:
        self.reset()
        results = [self.search(position, node_budget) for position in CORPUS]
        return Measurement(
            sum(result.nodes for result in results),
            sum(result.elapsed_ms for result in results),
        )

    def close(self) -> None:
        if self.process.poll() is None:
            try:
                self.send("quit")
                self.process.wait(timeout=self.timeout)
            except (BrokenPipeError, subprocess.TimeoutExpired):
                self.process.kill()
                self.process.wait()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def corpus_sha256() -> str:
    digest = hashlib.sha256()
    for position in CORPUS:
        digest.update(
            f"{position.name}|{int(position.chess960)}|{position.fen}\n".encode()
        )
    return digest.hexdigest()


def print_measurement(prefix: str, measurement: Measurement) -> None:
    print(
        f"{prefix}: nodes={measurement.nodes} time_ms={measurement.elapsed_ms} "
        f"nps={measurement.nps:.0f}"
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Strict Atomic-Stockfish versus Fairy-Stockfish speed gate"
    )
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--eval-file", type=Path, required=True)
    parser.add_argument("--nodes", type=int, default=100_000, help="nodes per FEN")
    parser.add_argument("--hash", type=int, default=64, dest="hash_mb")
    parser.add_argument("--affinity", type=int)
    parser.add_argument("--timeout", type=float, default=60.0, help="per-search seconds")
    args = parser.parse_args()

    candidate = args.candidate.resolve()
    baseline = args.baseline.resolve()
    net = args.eval_file.resolve()
    for label, path in (
        ("candidate", candidate),
        ("baseline", baseline),
        ("eval file", net),
    ):
        if not path.is_file():
            parser.error(f"{label} does not exist: {path}")
    if candidate == baseline:
        parser.error("candidate and baseline must be different executables")
    if args.nodes <= 0 or args.hash_mb <= 0 or args.timeout <= 0:
        parser.error("--nodes, --hash, and --timeout must be positive")

    net_sha = sha256_file(net)
    if net_sha != EXPECTED_NET_SHA256:
        parser.error(
            f"unexpected Legacy Atomic V1 network SHA-256: {net_sha}; "
            f"expected {EXPECTED_NET_SHA256}"
        )

    allowed = psutil.Process().cpu_affinity()
    affinity = args.affinity if args.affinity is not None else allowed[0]
    if affinity not in allowed:
        parser.error(f"CPU {affinity} is outside this process affinity: {allowed}")

    atomic_count = sum(not position.chess960 for position in CORPUS)
    atomic960_count = len(CORPUS) - atomic_count
    print(
        f"Corpus: positions={len(CORPUS)} atomic={atomic_count} "
        f"atomic960={atomic960_count} sha256={corpus_sha256()}"
    )
    print(
        f"Configuration: net_sha256={net_sha} threads=1 hash_mb={args.hash_mb} "
        f"cpu={affinity} nodes_per_fen={args.nodes} warmups=1 repetitions=5"
    )

    samples: dict[str, list[Measurement]] = {"candidate": [], "baseline": []}
    with ExitStack() as stack:
        engines = {
            "candidate": stack.enter_context(
                UciEngine(
                    "candidate",
                    candidate,
                    net,
                    args.hash_mb,
                    affinity,
                    args.timeout,
                )
            ),
            "baseline": stack.enter_context(
                UciEngine(
                    "baseline",
                    baseline,
                    net,
                    args.hash_mb,
                    affinity,
                    args.timeout,
                )
            ),
        }

        # All searches are serialized on the same logical CPU. Alternating the
        # measured order reduces systematic heat/frequency bias.
        print_measurement("Warm-up candidate", engines["candidate"].run_corpus(args.nodes))
        print_measurement("Warm-up baseline", engines["baseline"].run_corpus(args.nodes))
        for index in range(MEASURED_REPETITIONS):
            order = ("candidate", "baseline") if index % 2 == 0 else (
                "baseline",
                "candidate",
            )
            for label in order:
                measurement = engines[label].run_corpus(args.nodes)
                samples[label].append(measurement)
                print_measurement(
                    f"Run {index + 1}/{MEASURED_REPETITIONS} {label}", measurement
                )

    candidate_median = statistics.median(sample.nps for sample in samples["candidate"])
    baseline_median = statistics.median(sample.nps for sample in samples["baseline"])
    ratio = candidate_median / baseline_median
    candidate_size = candidate.stat().st_size
    baseline_size = baseline.stat().st_size
    print(
        f"Candidate: median_nps={candidate_median:.0f} binary_bytes={candidate_size}"
    )
    print(f"Baseline: median_nps={baseline_median:.0f} binary_bytes={baseline_size}")
    print(
        f"Comparison: nps_ratio={ratio:.4f} speed_delta_pct={(ratio - 1.0) * 100:.2f} "
        f"size_ratio={candidate_size / baseline_size:.4f} "
        f"size_delta_bytes={candidate_size - baseline_size:+d}"
    )

    if candidate_median <= baseline_median:
        print("PERFORMANCE GATE: FAIL (candidate median NPS is not strictly higher)")
        return 1

    print("PERFORMANCE GATE: PASS (candidate median NPS is strictly higher)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
