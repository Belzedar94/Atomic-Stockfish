#!/usr/bin/env python3
"""Audit the representative AtomicNNUEV3 incremental SIMD benchmark.

The benchmark covers one quiet move, capture, promotion, en-passant capture,
and maximum nine-piece Atomic blast.  It authenticates exact scalar/SIMD
results before timing, performs one warm-up, then five order-alternating
trials.  Ratios are reporting data: this audit intentionally has no noisy
``ratio > 1`` CI threshold.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
import re
import subprocess
import sys
from typing import Mapping, Optional, Sequence, Tuple


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))

import atomic_v3_simd_differential as differential  # noqa: E402


REQUIRED_ISAS = ("scalar", "sse41", "avx2")
ISA_RANK = {name: rank for rank, name in enumerate(REQUIRED_ISAS)}
EXPECTED_VERSION = "0xA70C0003"
EXPECTED_NETWORK_HASH = "0xCF9A484"
EXPECTED_DESCRIPTION = "Atomic-Stockfish AtomicNNUEV3 controlled mixed-wire CI source"
EXPECTED_CASES = (
    (
        "quiet",
        "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1",
        "e2e4",
        0,
    ),
    ("capture", "7k/p7/8/8/8/8/R7/K6N w - - 0 1", "a2a7", None),
    ("promotion", "7k/P7/8/8/8/8/8/K7 w - - 0 1", "a7a8q", 0),
    (
        "en-passant",
        "7k/8/2N1b3/2ppP3/8/8/8/K7 w - d6 0 2",
        "e5d6",
        None,
    ),
    (
        "max-blast",
        "7k/8/8/2nnn3/2nrn3/2nnnN2/8/K7 w - - 0 1",
        "f3d4",
        9,
    ),
)
EXPECTED_WARMUPS = 1
EXPECTED_TRIALS = 5
EXPECTED_REPETITIONS = 128

IDENTITY_KEYS = (
    "network.version",
    "network.hash",
    "network.description",
    "wire.policy",
    "wire.simd_permuted",
    "isa.requested",
    "isa.maximum",
)
CASE_KEYS = (
    "case",
    "fen",
    "move",
    "blast_size",
    "removed_rows",
    "added_rows",
    "scalar_ns",
    "required_ns",
    "scalar_median_ns",
    "required_median_ns",
    "speed_ratio",
    "exactness",
    "fingerprint",
)
SUMMARY_KEYS = (
    "isa.requested",
    "isa.executed",
    "cases",
    "warmups",
    "trials",
    "repetitions_per_case",
    "alternating_trials",
    "exactness",
    "scalar_total_ns",
    "required_total_ns",
    "scalar_median_ns",
    "required_median_ns",
    "speed_ratio",
    "sink",
)

FINAL_SENTINEL_RE = re.compile(
    r"^AtomicNNUEV3 incremental SIMD benchmark passed: "
    r"requested=(?P<requested>scalar|sse41|avx2) "
    r"executed=(?P<executed>scalar|sse41|avx2) "
    r"cases=(?P<cases>\d+) "
    r"warmups=(?P<warmups>\d+) "
    r"trials=(?P<trials>\d+) "
    r"ratio=(?P<ratio>\d+\.\d{6}) "
    r"exactness=(?P<exactness>[01])$"
)


class BenchmarkFailure(RuntimeError):
    """The transcript does not prove the representative benchmark contract."""


@dataclass(frozen=True)
class CaseRecord:
    fields: Mapping[str, str]
    scalar_ns: Tuple[int, ...]
    required_ns: Tuple[int, ...]
    scalar_median_ns: int
    required_median_ns: int
    speed_ratio: Decimal


@dataclass(frozen=True)
class BenchmarkRecord:
    identity: Mapping[str, str]
    cases: Tuple[CaseRecord, ...]
    summary: Mapping[str, str]
    scalar_total_ns: Tuple[int, ...]
    required_total_ns: Tuple[int, ...]
    scalar_median_ns: int
    required_median_ns: int
    speed_ratio: Decimal
    sentinel: Mapping[str, str]


def _parse_record(
    lines: Sequence[str],
    offset: int,
    record_name: str,
    keys: Sequence[str],
    terminator: str,
) -> Tuple[Mapping[str, str], int]:
    if offset >= len(lines) or lines[offset] != f"record={record_name}":
        raise BenchmarkFailure(f"missing or out-of-order {record_name} record")
    offset += 1
    fields = {}
    for key in keys:
        if offset >= len(lines):
            raise BenchmarkFailure(f"{record_name} record is truncated before {key}")
        prefix = f"{key}="
        if not lines[offset].startswith(prefix):
            raise BenchmarkFailure(
                f"{record_name} expected {key!r}, observed {lines[offset]!r}"
            )
        value = lines[offset][len(prefix) :]
        if not value:
            raise BenchmarkFailure(f"{record_name} field {key!r} is empty")
        fields[key] = value
        offset += 1
    if offset >= len(lines) or lines[offset] != terminator:
        raise BenchmarkFailure(f"{record_name} record lacks exact {terminator!r}")
    return fields, offset + 1


def _parse_nonnegative(value: str, field: str) -> int:
    if not re.fullmatch(r"0|[1-9]\d*", value):
        raise BenchmarkFailure(f"{field} is not a canonical integer: {value!r}")
    return int(value)


def _parse_positive(value: str, field: str) -> int:
    parsed = _parse_nonnegative(value, field)
    if parsed == 0:
        raise BenchmarkFailure(f"{field} must be positive")
    return parsed


def _parse_samples(value: str, field: str) -> Tuple[int, ...]:
    samples = tuple(_parse_positive(item, field) for item in value.split(","))
    if len(samples) != EXPECTED_TRIALS:
        raise BenchmarkFailure(
            f"{field} contains {len(samples)} samples; expected {EXPECTED_TRIALS}"
        )
    return samples


def _median(values: Sequence[int]) -> int:
    return sorted(values)[len(values) // 2]


def _parse_ratio(value: str, scalar_median: int, required_median: int, field: str) -> Decimal:
    if not re.fullmatch(r"\d+\.\d{6}", value):
        raise BenchmarkFailure(f"{field} is not one fixed six-decimal value")
    try:
        ratio = Decimal(value)
    except InvalidOperation as error:
        raise BenchmarkFailure(f"{field} is not finite decimal data") from error
    recomputed = Decimal(scalar_median) / Decimal(required_median)
    if abs(ratio - recomputed) > Decimal("0.000001"):
        raise BenchmarkFailure(f"{field} does not match the two medians")
    return ratio


def parse_benchmark_output(output: str, *, required_isa: str) -> BenchmarkRecord:
    """Parse one complete transcript, rejecting missing, reordered, or extra data."""

    if required_isa not in REQUIRED_ISAS:
        raise ValueError(f"unknown required ISA: {required_isa}")
    if "\x00" in output:
        raise BenchmarkFailure("benchmark output contains a NUL byte")
    lines = output.splitlines()
    if not lines:
        raise BenchmarkFailure("benchmark runner emitted no output")

    identity, offset = _parse_record(
        lines,
        0,
        "incremental_simd_benchmark_identity",
        IDENTITY_KEYS,
        "end_identity=1",
    )
    if identity["network.version"] != EXPECTED_VERSION:
        raise BenchmarkFailure("AtomicNNUEV3 network version differs")
    if identity["network.hash"] != EXPECTED_NETWORK_HASH:
        raise BenchmarkFailure("AtomicNNUEV3 network hash differs")
    if identity["network.description"] != EXPECTED_DESCRIPTION:
        raise BenchmarkFailure("AtomicNNUEV3 network description differs")
    if identity["wire.policy"] not in ("identity", "avx2_lasx", "avx512"):
        raise BenchmarkFailure("runner reported an unknown wire policy")
    if identity["wire.simd_permuted"] != "1":
        raise BenchmarkFailure("authenticated network was not marked SIMD-permuted")
    if identity["isa.requested"] != required_isa:
        raise BenchmarkFailure("identity requested ISA differs")
    maximum = identity["isa.maximum"]
    if maximum not in REQUIRED_ISAS or ISA_RANK[maximum] < ISA_RANK[required_isa]:
        raise BenchmarkFailure("compiled maximum ISA cannot satisfy the request")

    case_records = []
    scalar_columns = []
    required_columns = []
    for expected_name, expected_fen, expected_move, expected_blast in EXPECTED_CASES:
        fields, offset = _parse_record(
            lines,
            offset,
            "transition_case",
            CASE_KEYS,
            f"end_case={expected_name}",
        )
        if fields["case"] != expected_name:
            raise BenchmarkFailure(f"transition case order differs at {expected_name}")
        if fields["fen"] != expected_fen or fields["move"] != expected_move:
            raise BenchmarkFailure(f"{expected_name} fixture identity differs")
        blast = _parse_nonnegative(fields["blast_size"], f"{expected_name}.blast_size")
        if expected_blast is not None and blast != expected_blast:
            raise BenchmarkFailure(f"{expected_name} blast size differs")
        if expected_name in ("capture", "en-passant") and not 1 <= blast <= 8:
            raise BenchmarkFailure(f"{expected_name} is not a representative non-max blast")
        removed = _parse_nonnegative(fields["removed_rows"], f"{expected_name}.removed_rows")
        added = _parse_nonnegative(fields["added_rows"], f"{expected_name}.added_rows")
        if removed + added == 0:
            raise BenchmarkFailure(f"{expected_name} executed no HM row operation")
        scalar_ns = _parse_samples(fields["scalar_ns"], f"{expected_name}.scalar_ns")
        required_ns = _parse_samples(
            fields["required_ns"], f"{expected_name}.required_ns"
        )
        scalar_median = _parse_positive(
            fields["scalar_median_ns"], f"{expected_name}.scalar_median_ns"
        )
        required_median = _parse_positive(
            fields["required_median_ns"], f"{expected_name}.required_median_ns"
        )
        if scalar_median != _median(scalar_ns):
            raise BenchmarkFailure(f"{expected_name} scalar median differs")
        if required_median != _median(required_ns):
            raise BenchmarkFailure(f"{expected_name} required median differs")
        ratio = _parse_ratio(
            fields["speed_ratio"],
            scalar_median,
            required_median,
            f"{expected_name}.speed_ratio",
        )
        if fields["exactness"] != "1":
            raise BenchmarkFailure(f"{expected_name} exactness was not proven")
        if not re.fullmatch(r"0x[0-9A-F]{16}", fields["fingerprint"]):
            raise BenchmarkFailure(f"{expected_name} fingerprint is not canonical")
        scalar_columns.append(scalar_ns)
        required_columns.append(required_ns)
        case_records.append(
            CaseRecord(
                fields=fields,
                scalar_ns=scalar_ns,
                required_ns=required_ns,
                scalar_median_ns=scalar_median,
                required_median_ns=required_median,
                speed_ratio=ratio,
            )
        )

    summary, offset = _parse_record(
        lines,
        offset,
        "incremental_simd_benchmark_summary",
        SUMMARY_KEYS,
        "end_summary=1",
    )
    expected_summary = {
        "isa.requested": required_isa,
        "isa.executed": required_isa,
        "cases": str(len(EXPECTED_CASES)),
        "warmups": str(EXPECTED_WARMUPS),
        "trials": str(EXPECTED_TRIALS),
        "repetitions_per_case": str(EXPECTED_REPETITIONS),
        "alternating_trials": "1",
        "exactness": "1",
    }
    for key, expected in expected_summary.items():
        if summary[key] != expected:
            raise BenchmarkFailure(
                f"summary {key} differs: expected={expected!r} actual={summary[key]!r}"
            )

    scalar_totals = _parse_samples(summary["scalar_total_ns"], "scalar_total_ns")
    required_totals = _parse_samples(summary["required_total_ns"], "required_total_ns")
    expected_scalar_totals = tuple(
        sum(column[trial] for column in scalar_columns)
        for trial in range(EXPECTED_TRIALS)
    )
    expected_required_totals = tuple(
        sum(column[trial] for column in required_columns)
        for trial in range(EXPECTED_TRIALS)
    )
    if scalar_totals != expected_scalar_totals:
        raise BenchmarkFailure("scalar totals do not equal the per-case samples")
    if required_totals != expected_required_totals:
        raise BenchmarkFailure("required totals do not equal the per-case samples")
    scalar_median = _parse_positive(summary["scalar_median_ns"], "scalar_median_ns")
    required_median = _parse_positive(
        summary["required_median_ns"], "required_median_ns"
    )
    if scalar_median != _median(scalar_totals):
        raise BenchmarkFailure("summary scalar median differs")
    if required_median != _median(required_totals):
        raise BenchmarkFailure("summary required median differs")
    ratio = _parse_ratio(
        summary["speed_ratio"], scalar_median, required_median, "summary.speed_ratio"
    )
    if not re.fullmatch(r"0x[0-9A-F]+", summary["sink"]):
        raise BenchmarkFailure("summary sink is not canonical hexadecimal")

    if offset >= len(lines):
        raise BenchmarkFailure("benchmark success sentinel is missing")
    match = FINAL_SENTINEL_RE.fullmatch(lines[offset])
    if match is None:
        raise BenchmarkFailure("final benchmark success sentinel is not exact")
    if offset + 1 != len(lines):
        raise BenchmarkFailure("benchmark runner emitted output after its success sentinel")
    sentinel = match.groupdict()
    expected_sentinel = {
        "requested": required_isa,
        "executed": required_isa,
        "cases": str(len(EXPECTED_CASES)),
        "warmups": str(EXPECTED_WARMUPS),
        "trials": str(EXPECTED_TRIALS),
        "ratio": summary["speed_ratio"],
        "exactness": "1",
    }
    if sentinel != expected_sentinel:
        raise BenchmarkFailure("success sentinel disagrees with the summary")

    return BenchmarkRecord(
        identity=identity,
        cases=tuple(case_records),
        summary=summary,
        scalar_total_ns=scalar_totals,
        required_total_ns=required_totals,
        scalar_median_ns=scalar_median,
        required_median_ns=required_median,
        speed_ratio=ratio,
        sentinel=sentinel,
    )


def _stat_identity(path: Path) -> Tuple[int, int, int, int]:
    value = path.stat()
    modified_ns = getattr(value, "st_mtime_ns", int(value.st_mtime * 1_000_000_000))
    return value.st_dev, value.st_ino, value.st_size, modified_ns


def run_benchmark(
    runner: Path, net: Path, required_isa: str, *, timeout: float
) -> Tuple[BenchmarkRecord, str, str]:
    """Authenticate, execute, and parse one benchmark transcript."""

    if required_isa not in REQUIRED_ISAS:
        raise ValueError(f"unknown required ISA: {required_isa}")
    if timeout <= 0:
        raise ValueError("benchmark timeout must be positive")
    if not runner.is_file():
        raise BenchmarkFailure(f"incremental SIMD benchmark runner does not exist: {runner}")
    try:
        before = _stat_identity(net)
    except OSError as error:
        raise BenchmarkFailure(f"AtomicNNUEV3 fixture does not exist: {net}") from error
    fixture_sha256 = differential.authenticate_fixture(net)
    command = [
        str(runner),
        "--net",
        str(net),
        "--require-isa",
        required_isa,
    ]
    try:
        completed = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise BenchmarkFailure(f"could not execute benchmark runner: {error}") from error
    after_sha256 = differential.authenticate_fixture(net)
    after = _stat_identity(net)
    if before != after or fixture_sha256 != after_sha256:
        raise BenchmarkFailure("AtomicNNUEV3 fixture changed during the benchmark")
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout).strip()
        raise BenchmarkFailure(f"benchmark runner exited {completed.returncode}: {detail}")
    if completed.stderr:
        raise BenchmarkFailure("successful benchmark emitted stderr")
    parsed = parse_benchmark_output(completed.stdout, required_isa=required_isa)
    return parsed, fixture_sha256, completed.stdout


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runner", type=Path, required=True)
    parser.add_argument("--net", type=Path, required=True)
    parser.add_argument("--require-isa", choices=REQUIRED_ISAS, required=True)
    parser.add_argument("--timeout", type=float, default=300.0)
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _parser().parse_args(argv)
    try:
        record, fixture_sha256, transcript = run_benchmark(
            args.runner, args.net, args.require_isa, timeout=args.timeout
        )
    except (BenchmarkFailure, differential.FixtureAuthenticationError, ValueError) as error:
        print(f"AtomicNNUEV3 incremental SIMD benchmark audit FAILED: {error}", file=sys.stderr)
        return 1

    print(transcript, end="" if transcript.endswith("\n") else "\n")
    print(
        "AtomicNNUEV3 incremental SIMD benchmark audit passed: "
        f"requested={args.require_isa} cases={len(record.cases)} "
        f"trials={EXPECTED_TRIALS} exactness=1 ratio={record.summary['speed_ratio']} "
        f"fixture_sha256={fixture_sha256.upper()}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
