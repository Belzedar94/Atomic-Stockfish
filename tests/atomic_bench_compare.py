#!/usr/bin/env python3
"""Compare Atomic-Stockfish with the frozen Fairy-Stockfish speed baseline.

The two engines search the same fixed Atomic/Atomic960 FEN corpus through UCI.
This deliberately avoids each engine's private ``bench`` implementation.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import queue
import statistics
import subprocess
import sys
import threading
from contextlib import ExitStack
from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP, localcontext
from pathlib import Path
from typing import Any, Callable, Mapping, Optional, Sequence

import psutil

TESTS_DIR = Path(__file__).resolve().parent
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))

from atomic_compiler_preflight import (
    CompilerPreflightError,
    FileFingerprint,
    NORMATIVE_BASELINE_SHA256,
    fingerprint_files,
    require_matching_compilation_settings,
    require_sha256,
    verify_file_fingerprints,
)
from atomic_los_gate import (
    EXPECTED_PSUTIL_VERSION,
    GateConfigurationError,
    normative_psutil_fingerprints,
)


EXPECTED_NET_SHA256 = "99DC67EABF26A64FAEECA3A88B4C38597A840B8D4A874B9F2CF658C6F92A04A6"
MEASURED_REPETITIONS = 5
NORMATIVE_NODES_PER_FEN = 100_000
NORMATIVE_HASH_MB = 64
NORMATIVE_SEARCH_TIMEOUT_SECONDS = 60
CANDIDATE_NNUE_ARCHITECTURE_MARKER = "(45MiB, (45056, 1024, 16, 32, 1))"
BENCHMARK_JSON_SCHEMA_VERSION = 1
BENCHMARK_GATE = "bmi2-vs-fairy"
BENCHMARK_METRIC = "median-nps"
BENCHMARK_DECIMAL_QUANTUM = Decimal("0.000001")


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


def require_nnue_output(label: str, net: Path, output: list[str]) -> None:
    if any("ERROR:" in line.upper() for line in output):
        raise RuntimeError(f"{label} reported an NNUE/protocol error: {output[-10:]}")
    selected_net = str(net)
    expected_prefixes: tuple[str, ...]
    if label in {"candidate", "control"}:
        expected_prefixes = (
            f"NNUE evaluation using Legacy Atomic V1 {selected_net} ",
            f"NNUE evaluation using AtomicNNUEV2 {selected_net} ",
        )
    else:
        expected_prefixes = (f"NNUE evaluation using {selected_net} ",)
    net_lines = [
        line
        for line in output
        if any(expected_prefix in line for expected_prefix in expected_prefixes)
    ]
    marker_ok = (
        any(CANDIDATE_NNUE_ARCHITECTURE_MARKER in line for line in net_lines)
        if label in {"candidate", "control"}
        else any("enabled" in line for line in net_lines)
    )
    if not marker_ok:
        raise RuntimeError(
            f"{label} did not confirm selected NNUE {selected_net}: {output[-10:]}"
        )


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
        self.chess960: Optional[bool] = None
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
        try:
            process_info = psutil.Process(self.process.pid)
            process_info.cpu_affinity([affinity])
            observed_affinity = process_info.cpu_affinity()
            if observed_affinity != [affinity]:
                raise RuntimeError(
                    f"{label} affinity readback mismatch: requested "
                    f"[{affinity}], got {observed_affinity}"
                )
            assert self.process.stdin is not None and self.process.stdout is not None
            self.lines: queue.Queue[Optional[str]] = queue.Queue()
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
                raise RuntimeError(
                    f"{label} is missing UCI options: {', '.join(missing)}"
                )

            self.send("setoption name UCI_Variant value atomic")
            self.send("setoption name Threads value 1")
            self.send(f"setoption name Hash value {hash_mb}")
            self.send("setoption name Ponder value false")
            self.send("setoption name MultiPV value 1")
            self.send("setoption name Use NNUE value true")
            self.send(f"setoption name EvalFile value {net}")
            self.set_chess960(False)
            self.verify_nnue(net)
        except BaseException:
            self.close()
            raise

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

    def ready(self) -> list[str]:
        self.send("isready")
        return self.read_until(lambda line: line == "readyok")

    def verify_nnue(self, net: Path) -> None:
        # Atomic-Stockfish reports network verification when `go` starts,
        # while Fairy may report it during `isready`. Keep the smoke search
        # outside every timed measurement and accept the marker from either
        # phase only when the one-node search also reaches bestmove.
        output = self.ready()
        self.send("position startpos")
        self.send("go nodes 1")
        output.extend(self.read_until(lambda line: line.startswith("bestmove ")))
        require_nnue_output(self.label, net, output)

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
                try:
                    self.send("quit")
                except (BrokenPipeError, OSError, ValueError):
                    pass
                try:
                    self.process.wait(timeout=self.timeout)
                except (subprocess.TimeoutExpired, OSError, ValueError):
                    pass
            finally:
                if self.process.poll() is None:
                    try:
                        self.process.kill()
                    except (OSError, ValueError):
                        pass
                    try:
                        self.process.wait(timeout=self.timeout)
                    except (subprocess.TimeoutExpired, OSError, ValueError):
                        pass


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


def measurement_nps(measurement: Measurement) -> Decimal:
    """Return an exact decimal NPS value from the recorded integer evidence."""

    if measurement.nodes <= 0 or measurement.elapsed_ms <= 0:
        raise ValueError("benchmark samples require positive nodes and elapsed time")
    with localcontext() as context:
        context.prec = 50
        return Decimal(measurement.nodes) * Decimal(1000) / Decimal(
            measurement.elapsed_ms
        )


def median_nps(measurements: Sequence[Measurement]) -> Decimal:
    if len(measurements) != MEASURED_REPETITIONS:
        raise ValueError(
            f"benchmark requires exactly {MEASURED_REPETITIONS} measured repetitions"
        )
    return statistics.median(measurement_nps(item) for item in measurements)


def fixed_six(value: Decimal) -> str:
    if not value.is_finite() or value <= 0:
        raise ValueError("benchmark decimal values must be finite and positive")
    with localcontext() as context:
        context.prec = 50
        return format(
            value.quantize(BENCHMARK_DECIMAL_QUANTUM, rounding=ROUND_HALF_UP),
            ".6f",
        )


def benchmark_document(
    *,
    candidate_fingerprint: FileFingerprint,
    baseline_fingerprint: FileFingerprint,
    eval_fingerprint: FileFingerprint,
    candidate_samples: Sequence[Measurement],
    baseline_samples: Sequence[Measurement],
    affinity: int,
) -> Mapping[str, Any]:
    """Build the one canonical, independently recomputable release-gate record."""

    candidate_median = median_nps(candidate_samples)
    baseline_median = median_nps(baseline_samples)
    with localcontext() as context:
        context.prec = 50
        ratio = candidate_median / baseline_median
    passed = candidate_median > baseline_median

    def artifact(fingerprint: FileFingerprint) -> Mapping[str, Any]:
        return {
            "bytes": fingerprint.size,
            "fileName": fingerprint.path.name,
            "sha256": fingerprint.sha256.lower(),
        }

    def samples(values: Sequence[Measurement]) -> list[Mapping[str, int]]:
        return [
            {"nodes": value.nodes, "timeMillis": value.elapsed_ms} for value in values
        ]

    return {
        "baseline": artifact(baseline_fingerprint),
        "baselineMedianNps": fixed_six(baseline_median),
        "baselineSamples": samples(baseline_samples),
        "candidate": artifact(candidate_fingerprint),
        "candidateMedianNps": fixed_six(candidate_median),
        "candidateSamples": samples(candidate_samples),
        "corpusSha256": corpus_sha256(),
        "cpuAffinity": affinity,
        "evalFileSha256": eval_fingerprint.sha256.lower(),
        "gate": BENCHMARK_GATE,
        "hashMb": NORMATIVE_HASH_MB,
        "metric": BENCHMARK_METRIC,
        "nodesPerFen": NORMATIVE_NODES_PER_FEN,
        "pass": passed,
        "positions": len(CORPUS),
        "ratio": fixed_six(ratio),
        "repetitions": MEASURED_REPETITIONS,
        "schemaVersion": BENCHMARK_JSON_SCHEMA_VERSION,
        "searchTimeoutSeconds": NORMATIVE_SEARCH_TIMEOUT_SECONDS,
        "threads": 1,
        "warmups": 1,
    }


def write_benchmark_json(path: Path, document: Mapping[str, Any]) -> None:
    """Write canonical JSON exactly once; a failed write leaves no false evidence."""

    payload = (
        json.dumps(
            document,
            ensure_ascii=True,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("ascii")
    descriptor: Optional[int] = None
    created = False
    try:
        descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        created = True
        with os.fdopen(descriptor, "wb") as output:
            descriptor = None
            output.write(payload)
            output.flush()
            os.fsync(output.fileno())
    except BaseException:
        if descriptor is not None:
            os.close(descriptor)
        if created:
            path.unlink(missing_ok=True)
        raise


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Strict Atomic-Stockfish versus Fairy-Stockfish speed gate"
    )
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--eval-file", type=Path, required=True)
    parser.add_argument(
        "--nodes",
        type=int,
        default=NORMATIVE_NODES_PER_FEN,
        help="normative value is 100000 nodes per FEN",
    )
    parser.add_argument(
        "--hash", type=int, default=NORMATIVE_HASH_MB, dest="hash_mb"
    )
    parser.add_argument(
        "--affinity",
        type=int,
        required=True,
        help="explicit logical CPU used for every serialized search",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=float(NORMATIVE_SEARCH_TIMEOUT_SECONDS),
        help="normative per-search timeout is 60 seconds",
    )
    parser.add_argument(
        "--json-output",
        type=Path,
        help="exclusive canonical JSON evidence written after the real postflight",
    )
    args = parser.parse_args(argv)

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
    if args.nodes != NORMATIVE_NODES_PER_FEN:
        parser.error(
            f"--nodes must be exactly {NORMATIVE_NODES_PER_FEN} for the "
            "normative performance gate"
        )
    if args.hash_mb != NORMATIVE_HASH_MB:
        parser.error(
            f"--hash must be exactly {NORMATIVE_HASH_MB} for the normative "
            "performance gate"
        )
    if args.timeout != float(NORMATIVE_SEARCH_TIMEOUT_SECONDS):
        parser.error(
            f"--timeout must be exactly {NORMATIVE_SEARCH_TIMEOUT_SECONDS} for "
            "the normative performance gate"
        )
    if args.json_output is not None:
        output = args.json_output.absolute()
        if os.path.lexists(output):
            parser.error(f"--json-output already exists: {output}")
        if not output.parent.is_dir():
            parser.error(f"--json-output parent is not a directory: {output.parent}")

    try:
        artifacts = normative_psutil_fingerprints() + fingerprint_files(
            (
                ("candidate", candidate),
                ("baseline", baseline),
                ("eval_file", net),
            )
        )
        artifact_map = {artifact.label: artifact for artifact in artifacts}
        require_sha256(
            artifact_map["baseline"],
            NORMATIVE_BASELINE_SHA256,
            description="frozen Fairy-Stockfish BMI2 baseline",
        )
        require_sha256(
            artifact_map["eval_file"],
            EXPECTED_NET_SHA256,
            description="Legacy Atomic V1 network",
        )
        require_matching_compilation_settings(
            candidate,
            baseline,
            expected_fingerprints=artifact_map,
            expected_baseline_sha256=NORMATIVE_BASELINE_SHA256,
        )
    except (CompilerPreflightError, GateConfigurationError) as exc:
        parser.error(str(exc))

    net_sha = artifact_map["eval_file"].sha256

    allowed = psutil.Process().cpu_affinity()
    affinity = args.affinity
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
        f"cpu={affinity} psutil={EXPECTED_PSUTIL_VERSION} "
        f"nodes_per_fen={args.nodes} warmups=1 "
        f"repetitions={MEASURED_REPETITIONS}"
    )

    samples: dict[str, list[Measurement]] = {"candidate": [], "baseline": []}
    try:
        try:
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

                # All searches are serialized on the same logical CPU.
                # Alternating the order reduces heat/frequency bias.
                print_measurement(
                    "Warm-up candidate", engines["candidate"].run_corpus(args.nodes)
                )
                print_measurement(
                    "Warm-up baseline", engines["baseline"].run_corpus(args.nodes)
                )
                for index in range(MEASURED_REPETITIONS):
                    order = (
                        ("candidate", "baseline")
                        if index % 2 == 0
                        else ("baseline", "candidate")
                    )
                    for label in order:
                        measurement = engines[label].run_corpus(args.nodes)
                        samples[label].append(measurement)
                        print_measurement(
                            f"Run {index + 1}/{MEASURED_REPETITIONS} {label}",
                            measurement,
                        )
        finally:
            verify_file_fingerprints(
                artifacts,
                emit=print,
                pass_label="Benchmark artifact postflight",
            )
    except CompilerPreflightError as exc:
        parser.error(str(exc))
    except Exception as exc:
        parser.error(f"benchmark infrastructure failure: {exc}")

    candidate_median = median_nps(samples["candidate"])
    baseline_median = median_nps(samples["baseline"])
    ratio = candidate_median / baseline_median
    candidate_size = artifact_map["candidate"].size
    baseline_size = artifact_map["baseline"].size
    print(
        f"Candidate: median_nps={candidate_median:.0f} binary_bytes={candidate_size}"
    )
    print(f"Baseline: median_nps={baseline_median:.0f} binary_bytes={baseline_size}")
    print(
        f"Comparison: nps_ratio={ratio:.4f} "
        f"speed_delta_pct={(ratio - Decimal(1)) * Decimal(100):.2f} "
        f"size_ratio={candidate_size / baseline_size:.4f} "
        f"size_delta_bytes={candidate_size - baseline_size:+d}"
    )

    document = benchmark_document(
        candidate_fingerprint=artifact_map["candidate"],
        baseline_fingerprint=artifact_map["baseline"],
        eval_fingerprint=artifact_map["eval_file"],
        candidate_samples=samples["candidate"],
        baseline_samples=samples["baseline"],
        affinity=affinity,
    )
    if args.json_output is not None:
        try:
            write_benchmark_json(args.json_output.absolute(), document)
        except OSError as exc:
            parser.error(f"cannot write --json-output exclusively: {exc}")

    if candidate_median <= baseline_median:
        print("PERFORMANCE GATE: FAIL (candidate median NPS is not strictly higher)")
        return 1

    print("PERFORMANCE GATE: PASS (candidate median NPS is strictly higher)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
