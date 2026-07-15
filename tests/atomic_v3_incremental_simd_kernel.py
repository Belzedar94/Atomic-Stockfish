#!/usr/bin/env python3
"""Fail-closed wrapper for the private AtomicNNUEV3 incremental SIMD kernel gate."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import math
from pathlib import Path
import re
import subprocess
import sys
from typing import Dict, List, Mapping, Sequence, Tuple


REQUIRED_ISAS = ("scalar", "sse41", "avx2")
ISA_RANK = {name: rank for rank, name in enumerate(REQUIRED_ISAS)}
TAIL_COUNTS = (0, 1, 3, 4, 7, 8, 9, 15, 16, 17, 1023, 1024, 1025)
DISPATCHER_CALLS = len(TAIL_COUNTS) * 4 + 2
STABLE_CALLS = len(TAIL_COUNTS) * 4 + 2
CANARY_CHECKS = len(TAIL_COUNTS) * 6 * 4
NONZERO_BASE_LANES = sum(TAIL_COUNTS)

FNV_OFFSET = 1_469_598_103_934_665_603
FNV_PRIME = 1_099_511_628_211

IDENTITY_FIELDS = ("isa.requested", "isa.maximum", "tail_counts")
TAIL_FIELDS = (
    "case",
    "count",
    "isa.executed",
    "dispatcher.add.exact",
    "dispatcher.remove.exact",
    "dispatcher.restore.exact",
    "stable.add.exact",
    "stable.remove.exact",
    "stable.restore.exact",
    "destination.canaries",
    "source.canaries",
    "source.immutable",
    "source.minimum_covered",
    "source.maximum_covered",
    "nonzero_bases",
    "comparison.exact",
)
SUMMARY_FIELDS = (
    "isa.requested",
    "isa.executed",
    "tail_cases",
    "dispatcher_calls",
    "stable_calls",
    "null_pointer_probes",
    "unavailable_probes",
    "fallback_calls",
    "canary_checks",
    "source.minimum_covered",
    "source.maximum_covered",
    "nonzero_base_lanes",
    "fingerprint",
)

FINAL_SENTINEL_RE = re.compile(
    r"^AtomicNNUEV3 incremental SIMD kernel gate passed: "
    r"requested=(?P<requested>scalar|sse41|avx2) "
    r"executed=(?P<executed>scalar|sse41|avx2) "
    r"tail-cases=(?P<tail_cases>[0-9]+) "
    r"dispatcher-calls=(?P<dispatcher_calls>[0-9]+) "
    r"stable-calls=(?P<stable_calls>[0-9]+) "
    r"null-pointer-probes=(?P<null_pointer_probes>[0-9]+) "
    r"unavailable-probes=(?P<unavailable_probes>[0-9]+) "
    r"fallback-calls=(?P<fallback_calls>[0-9]+) "
    r"canary-checks=(?P<canary_checks>[0-9]+) "
    r"nonzero-base-lanes=(?P<nonzero_base_lanes>[0-9]+) "
    r"fingerprint=(?P<fingerprint>0x[0-9A-F]{16})$"
)


class KernelGateError(ValueError):
    """The runner did not prove the complete incremental SIMD kernel contract."""


@dataclass(frozen=True)
class GateOutput:
    identity: Mapping[str, str]
    tails: Tuple[Mapping[str, str], ...]
    summary: Mapping[str, str]
    sentinel: Mapping[str, str]


def _source_value(index: int) -> int:
    if index == 0:
        return -32768
    if index == 1:
        return 32767
    return ((index * 40503 + 97) & 0xFFFF) - 32768


def _base_value(index: int) -> int:
    magnitude = 0x100000000 + index * 1_000_003 + (index % 17) * 65_537
    return magnitude if index % 2 == 0 else -magnitude


def _fingerprint_integer(state: int, value: int, bits: int) -> int:
    unsigned = value & ((1 << bits) - 1)
    for byte in range(bits // 8):
        state ^= (unsigned >> (byte * 8)) & 0xFF
        state = (state * FNV_PRIME) & 0xFFFFFFFFFFFFFFFF
    return state


def expected_fingerprint() -> str:
    state = FNV_OFFSET
    for count in TAIL_COUNTS:
        state = _fingerprint_integer(state, count, 64)
        for index in range(count):
            source = _source_value(index)
            base = _base_value(index)
            state = _fingerprint_integer(state, source, 16)
            state = _fingerprint_integer(state, base, 64)
            state = _fingerprint_integer(state, base + source, 64)
            state = _fingerprint_integer(state, base - source, 64)
    return f"0x{state:016X}"


FROZEN_FINGERPRINT = "0x21E9FF9A77F881F2"


def _split_field(line: str) -> Tuple[str, str]:
    key, separator, value = line.partition("=")
    if not separator or not key or not value:
        raise KernelGateError(f"malformed key=value line: {line!r}")
    return key, value


def _consume_record(
    lines: Sequence[str],
    offset: int,
    *,
    kind: str,
    field_names: Sequence[str],
    end_key: str,
    end_value: str,
) -> Tuple[Dict[str, str], int]:
    if offset >= len(lines) or lines[offset] != f"record={kind}":
        actual = "end of output" if offset >= len(lines) else repr(lines[offset])
        raise KernelGateError(f"expected record={kind}, got {actual}")
    offset += 1
    fields: Dict[str, str] = {}
    for expected_key in field_names:
        if offset >= len(lines):
            raise KernelGateError(f"record={kind} was truncated before {expected_key}")
        key, value = _split_field(lines[offset])
        if key != expected_key:
            raise KernelGateError(
                f"record={kind} ordered field mismatch: expected={expected_key} actual={key}"
            )
        if key in fields:
            raise KernelGateError(f"record={kind} duplicated field {key}")
        fields[key] = value
        offset += 1
    if offset >= len(lines):
        raise KernelGateError(f"record={kind} was truncated before {end_key}")
    key, value = _split_field(lines[offset])
    if key != end_key or value != end_value:
        raise KernelGateError(
            f"record={kind} end marker mismatch: expected={end_key}={end_value} "
            f"actual={key}={value}"
        )
    return fields, offset + 1


def parse_gate_output(output: str) -> GateOutput:
    lines = output.splitlines()
    if not lines:
        raise KernelGateError("child emitted no output")
    for line in lines:
        if not line or line != line.strip():
            raise KernelGateError("child output contains a blank or padded line")

    offset = 0
    identity, offset = _consume_record(
        lines,
        offset,
        kind="incremental_simd_kernel_identity",
        field_names=IDENTITY_FIELDS,
        end_key="end_identity",
        end_value="1",
    )
    tails: List[Mapping[str, str]] = []
    for case_index in range(len(TAIL_COUNTS)):
        fields, offset = _consume_record(
            lines,
            offset,
            kind="incremental_simd_kernel_tail",
            field_names=TAIL_FIELDS,
            end_key="end_case",
            end_value=str(case_index),
        )
        tails.append(fields)
    summary, offset = _consume_record(
        lines,
        offset,
        kind="incremental_simd_kernel_summary",
        field_names=SUMMARY_FIELDS,
        end_key="end_summary",
        end_value="1",
    )
    if offset != len(lines) - 1:
        raise KernelGateError("success sentinel is not the sole final output line")
    match = FINAL_SENTINEL_RE.fullmatch(lines[offset])
    if match is None:
        raise KernelGateError(
            "final non-empty line is not the incremental SIMD kernel success sentinel"
        )
    return GateOutput(identity, tuple(tails), summary, match.groupdict())


def _expect(fields: Mapping[str, str], key: str, expected: object, context: str) -> None:
    actual = fields.get(key)
    encoded = str(expected)
    if actual != encoded:
        raise KernelGateError(
            f"{context} {key} differs: expected={encoded!r} actual={actual!r}"
        )


def _unavailable_probes(maximum_isa: str) -> int:
    # Two operations for each named ISA above the compile-time maximum, plus
    # Add and Remove for the invalid enum value used to prove no fallback.
    return 2 * ((len(REQUIRED_ISAS) - 1 - ISA_RANK[maximum_isa]) + 1)


def validate_gate_output(output: str, required_isa: str) -> str:
    if required_isa not in REQUIRED_ISAS:
        raise ValueError("required ISA must be scalar, sse41 or avx2")
    parsed = parse_gate_output(output)

    _expect(parsed.identity, "isa.requested", required_isa, "identity")
    maximum_isa = parsed.identity.get("isa.maximum", "")
    if maximum_isa not in REQUIRED_ISAS:
        raise KernelGateError(f"identity reported unknown maximum ISA {maximum_isa!r}")
    if ISA_RANK[maximum_isa] < ISA_RANK[required_isa]:
        raise KernelGateError(
            f"required ISA {required_isa} exceeds compiled maximum {maximum_isa}"
        )
    _expect(
        parsed.identity,
        "tail_counts",
        ",".join(str(value) for value in TAIL_COUNTS),
        "identity",
    )

    for case_index, (fields, count) in enumerate(zip(parsed.tails, TAIL_COUNTS)):
        context = f"tail[{case_index}]"
        _expect(fields, "case", case_index, context)
        _expect(fields, "count", count, context)
        _expect(fields, "isa.executed", required_isa, context)
        for key in (
            "dispatcher.add.exact",
            "dispatcher.remove.exact",
            "dispatcher.restore.exact",
            "stable.add.exact",
            "stable.remove.exact",
            "stable.restore.exact",
            "source.immutable",
            "comparison.exact",
        ):
            _expect(fields, key, 1, context)
        _expect(fields, "destination.canaries", 2, context)
        _expect(fields, "source.canaries", 2, context)
        _expect(fields, "source.minimum_covered", int(count >= 1), context)
        _expect(fields, "source.maximum_covered", int(count >= 2), context)
        _expect(fields, "nonzero_bases", count, context)

    expected_summary = {
        "isa.requested": required_isa,
        "isa.executed": required_isa,
        "tail_cases": len(TAIL_COUNTS),
        "dispatcher_calls": DISPATCHER_CALLS,
        "stable_calls": STABLE_CALLS,
        "null_pointer_probes": 3,
        "unavailable_probes": _unavailable_probes(maximum_isa),
        "fallback_calls": 0,
        "canary_checks": CANARY_CHECKS,
        "source.minimum_covered": 1,
        "source.maximum_covered": 1,
        "nonzero_base_lanes": NONZERO_BASE_LANES,
        "fingerprint": FROZEN_FINGERPRINT,
    }
    for key, value in expected_summary.items():
        _expect(parsed.summary, key, value, "summary")

    sentinel_to_summary = {
        "requested": "isa.requested",
        "executed": "isa.executed",
        "tail_cases": "tail_cases",
        "dispatcher_calls": "dispatcher_calls",
        "stable_calls": "stable_calls",
        "null_pointer_probes": "null_pointer_probes",
        "unavailable_probes": "unavailable_probes",
        "fallback_calls": "fallback_calls",
        "canary_checks": "canary_checks",
        "nonzero_base_lanes": "nonzero_base_lanes",
        "fingerprint": "fingerprint",
    }
    for sentinel_key, summary_key in sentinel_to_summary.items():
        if parsed.sentinel[sentinel_key] != parsed.summary[summary_key]:
            raise KernelGateError(
                f"sentinel {sentinel_key} differs from summary {summary_key}"
            )
    return FROZEN_FINGERPRINT


def run(runner: Path, required_isa: str, timeout: float) -> str:
    if required_isa not in REQUIRED_ISAS:
        raise ValueError("required ISA must be scalar, sse41 or avx2")
    if not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("timeout must be finite and positive")
    runner = runner.resolve()
    if not runner.is_file():
        raise KernelGateError(f"incremental SIMD kernel runner does not exist: {runner}")
    try:
        completed = subprocess.run(
            [str(runner), "--require-isa", required_isa],
            text=True,
            capture_output=True,
            check=False,
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise KernelGateError(f"could not execute incremental SIMD kernel runner: {error}") from error
    if completed.returncode != 0:
        raise KernelGateError(
            f"incremental SIMD kernel runner failed ({completed.returncode}):\n"
            f"stdout:\n{completed.stdout}\nstderr:\n{completed.stderr}"
        )
    return validate_gate_output(completed.stdout, required_isa)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runner", type=Path, required=True)
    parser.add_argument("--require-isa", choices=REQUIRED_ISAS, required=True)
    parser.add_argument("--timeout", type=float, default=60.0)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        fingerprint = run(args.runner, args.require_isa, args.timeout)
    except (ValueError, KernelGateError) as error:
        print(f"Atomic V3 incremental SIMD kernel gate failed: {error}", file=sys.stderr)
        return 1
    print(
        "Atomic V3 incremental SIMD kernel Python gate passed: "
        f"isa={args.require_isa} tails={len(TAIL_COUNTS)} "
        f"fingerprint={fingerprint}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
