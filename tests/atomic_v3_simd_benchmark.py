#!/usr/bin/env python3
"""Fail-closed local promotion audit for the private AtomicNNUEV3 SIMD kernels.

The C++ runner times one deterministic synthetic 1,024-lane i16 row and one
deterministic synthetic 1,024-lane i8 row.  It does not time search, a full V3
evaluation, fixture parameter rows, or a distribution of active Atomic rows.
This wrapper authenticates the fixture needed to load the private runner,
validates every benchmark record, and makes the noisy speed threshold opt-in.
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
EXPECTED_WARMUPS = 1
EXPECTED_TRIALS = 5
EXPECTED_REPETITIONS = 8192

IDENTITY_KEYS = (
    "network.version",
    "network.hash",
    "network.description",
    "wire.policy",
    "wire.simd_permuted",
    "isa.requested",
    "isa.maximum",
)
BENCHMARK_KEYS = (
    "isa.requested",
    "isa.executed",
    "warmups",
    "trials",
    "repetitions",
    "promotion_gate",
    "scalar_ns",
    "required_ns",
    "scalar_median_ns",
    "required_median_ns",
    "throughput_ratio",
    "sink",
)

FINAL_SENTINEL_RE = re.compile(
    r"^AtomicNNUEV3 SIMD benchmark passed: "
    r"requested=(?P<requested>scalar|sse41|avx2) "
    r"executed=(?P<executed>scalar|sse41|avx2) "
    r"warmups=(?P<warmups>\d+) "
    r"trials=(?P<trials>\d+) "
    r"ratio=(?P<ratio>\d+\.\d{6}) "
    r"promotion_gate=(?P<promotion_gate>[01])$"
)


class BenchmarkFailure(RuntimeError):
    """The observed output does not prove the requested local benchmark."""


@dataclass(frozen=True)
class BenchmarkRecord:
    identity: Mapping[str, str]
    fields: Mapping[str, str]
    scalar_ns: Tuple[int, ...]
    required_ns: Tuple[int, ...]
    scalar_median_ns: int
    required_median_ns: int
    throughput_ratio: Decimal
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


def _parse_positive_integer(value: str, field: str) -> int:
    if not re.fullmatch(r"[1-9]\d*", value):
        raise BenchmarkFailure(f"{field} is not a positive canonical integer: {value!r}")
    return int(value)


def _parse_samples(value: str, field: str, expected_count: int) -> Tuple[int, ...]:
    values = tuple(
        _parse_positive_integer(item, field) for item in value.split(",")
    )
    if len(values) != expected_count:
        raise BenchmarkFailure(
            f"{field} contains {len(values)} samples; expected {expected_count}"
        )
    return values


def _median(values: Sequence[int]) -> int:
    return sorted(values)[len(values) // 2]


def parse_benchmark_output(
    output: str, *, required_isa: str, promotion_gate: bool
) -> BenchmarkRecord:
    """Parse one complete C++ benchmark transcript without accepting extras."""

    if required_isa not in REQUIRED_ISAS:
        raise ValueError(f"unknown required ISA: {required_isa}")
    if "\x00" in output:
        raise BenchmarkFailure("benchmark output contains a NUL byte")
    lines = output.splitlines()
    if not lines:
        raise BenchmarkFailure("benchmark runner emitted no output")

    identity, offset = _parse_record(
        lines, 0, "simd_identity", IDENTITY_KEYS, "end_identity=1"
    )
    fields, offset = _parse_record(
        lines, offset, "simd_benchmark", BENCHMARK_KEYS, "end_benchmark=1"
    )
    if offset >= len(lines):
        raise BenchmarkFailure("benchmark success sentinel is missing")
    sentinel_match = FINAL_SENTINEL_RE.fullmatch(lines[offset])
    if sentinel_match is None:
        raise BenchmarkFailure("final benchmark success sentinel is not exact")
    if offset + 1 != len(lines):
        raise BenchmarkFailure("benchmark runner emitted output after its success sentinel")
    sentinel = sentinel_match.groupdict()

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

    expected_gate = "1" if promotion_gate else "0"
    exact = {
        "isa.requested": required_isa,
        "isa.executed": required_isa,
        "warmups": str(EXPECTED_WARMUPS),
        "trials": str(EXPECTED_TRIALS),
        "repetitions": str(EXPECTED_REPETITIONS),
        "promotion_gate": expected_gate,
    }
    for key, expected in exact.items():
        if fields[key] != expected:
            raise BenchmarkFailure(
                f"benchmark {key} differs: expected={expected!r} actual={fields[key]!r}"
            )

    scalar_ns = _parse_samples(fields["scalar_ns"], "scalar_ns", EXPECTED_TRIALS)
    required_ns = _parse_samples(
        fields["required_ns"], "required_ns", EXPECTED_TRIALS
    )
    scalar_median = _parse_positive_integer(
        fields["scalar_median_ns"], "scalar_median_ns"
    )
    required_median = _parse_positive_integer(
        fields["required_median_ns"], "required_median_ns"
    )
    if scalar_median != _median(scalar_ns):
        raise BenchmarkFailure("reported scalar median does not match raw samples")
    if required_median != _median(required_ns):
        raise BenchmarkFailure("reported required-ISA median does not match raw samples")

    if not re.fullmatch(r"\d+\.\d{6}", fields["throughput_ratio"]):
        raise BenchmarkFailure("throughput_ratio is not one fixed six-decimal value")
    try:
        ratio = Decimal(fields["throughput_ratio"])
    except InvalidOperation as error:
        raise BenchmarkFailure("throughput_ratio is not finite decimal data") from error
    recomputed = Decimal(scalar_median) / Decimal(required_median)
    if abs(ratio - recomputed) > Decimal("0.000001"):
        raise BenchmarkFailure("throughput_ratio does not match the two medians")
    if not re.fullmatch(r"0x[0-9A-F]+", fields["sink"]):
        raise BenchmarkFailure("benchmark sink is not canonical uppercase hexadecimal")

    sentinel_expected = {
        "requested": required_isa,
        "executed": required_isa,
        "warmups": str(EXPECTED_WARMUPS),
        "trials": str(EXPECTED_TRIALS),
        "promotion_gate": expected_gate,
        "ratio": fields["throughput_ratio"],
    }
    if sentinel != sentinel_expected:
        raise BenchmarkFailure("success sentinel disagrees with benchmark fields")

    if promotion_gate:
        if required_isa == "scalar":
            raise BenchmarkFailure("the local promotion gate cannot target scalar")
        if ratio <= Decimal(1):
            raise BenchmarkFailure("required SIMD kernel did not beat forced scalar")

    return BenchmarkRecord(
        identity=identity,
        fields=fields,
        scalar_ns=scalar_ns,
        required_ns=required_ns,
        scalar_median_ns=scalar_median,
        required_median_ns=required_median,
        throughput_ratio=ratio,
        sentinel=sentinel,
    )


def _stat_identity(path: Path) -> Tuple[int, int, int, int]:
    value = path.stat()
    modified_ns = getattr(value, "st_mtime_ns", int(value.st_mtime * 1_000_000_000))
    return value.st_dev, value.st_ino, value.st_size, modified_ns


def run_benchmark(
    runner: Path,
    net: Path,
    required_isa: str,
    *,
    promotion_gate: bool,
    timeout: float,
) -> Tuple[BenchmarkRecord, str, str]:
    """Authenticate, execute and parse one local benchmark transcript."""

    if required_isa not in REQUIRED_ISAS:
        raise ValueError(f"unknown required ISA: {required_isa}")
    if timeout <= 0:
        raise ValueError("benchmark timeout must be positive")
    if not runner.is_file():
        raise BenchmarkFailure(f"SIMD runner does not exist: {runner}")

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
        "--benchmark",
    ]
    if promotion_gate:
        command.append("--promotion-gate")
    try:
        completed = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise BenchmarkFailure(f"could not execute SIMD benchmark runner: {error}") from error

    after_sha256 = differential.authenticate_fixture(net)
    after = _stat_identity(net)
    if before != after or fixture_sha256 != after_sha256:
        raise BenchmarkFailure("AtomicNNUEV3 fixture changed during the benchmark")
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout).strip()
        raise BenchmarkFailure(
            f"SIMD benchmark runner exited {completed.returncode}: {detail}"
        )
    if completed.stderr:
        raise BenchmarkFailure("successful SIMD benchmark emitted stderr")
    parsed = parse_benchmark_output(
        completed.stdout,
        required_isa=required_isa,
        promotion_gate=promotion_gate,
    )
    return parsed, fixture_sha256, completed.stdout


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runner", type=Path, required=True)
    parser.add_argument("--net", type=Path, required=True)
    parser.add_argument("--require-isa", choices=REQUIRED_ISAS, required=True)
    parser.add_argument(
        "--promotion-gate",
        action="store_true",
        help="locally require median SIMD/scalar ratio > 1; never use in CI",
    )
    parser.add_argument("--timeout", type=float, default=300.0)
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _parser().parse_args(argv)
    try:
        record, fixture_sha256, transcript = run_benchmark(
            args.runner,
            args.net,
            args.require_isa,
            promotion_gate=args.promotion_gate,
            timeout=args.timeout,
        )
    except (BenchmarkFailure, differential.FixtureAuthenticationError, ValueError) as error:
        print(f"AtomicNNUEV3 SIMD benchmark audit FAILED: {error}", file=sys.stderr)
        return 1

    print(transcript, end="" if transcript.endswith("\n") else "\n")
    print(
        "AtomicNNUEV3 SIMD benchmark audit passed: "
        f"requested={args.require_isa} executed={record.fields['isa.executed']} "
        f"promotion_gate={int(args.promotion_gate)} trials={EXPECTED_TRIALS} "
        f"ratio={record.fields['throughput_ratio']} "
        f"fixture_sha256={fixture_sha256.upper()}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
