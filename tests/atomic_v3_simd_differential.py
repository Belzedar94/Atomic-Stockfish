#!/usr/bin/env python3
"""Independent fail-closed differential for the private AtomicNNUEV3 SIMD path.

Python authenticates and decodes the frozen H9.3g network, reconstructs the
H9.3f/H9.3h corpus without calling C++, and compares every published scalar
and SIMD diagnostic cell with the independent Python reference.  The C++
runner is deliberately only an observed implementation: it receives complete
FEN records and must report which ISA actually executed, exact accounting and
one final success sentinel.

H9.3j-a vectorizes only the feature-transformer row additions.  PSQT,
transform and the dense tail remain scalar, so the two signed-widening probes
exercise the i16 and i8 lane boundaries directly instead of pretending that
the dense head is SIMD at this milestone.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import re
import subprocess
import sys
from typing import Dict, Iterable, List, Mapping, Optional, Sequence, Tuple


ROOT = Path(__file__).resolve().parents[1]
PYTHON_DIR = ROOT / "tests" / "python"
sys.path.insert(0, str(PYTHON_DIR))
sys.path.insert(0, str(ROOT / "tests"))

import atomic_v3_capture_pair_reference as cp  # noqa: E402
import atomic_v3_full_refresh_differential as h93f  # noqa: E402
import atomic_v3_full_refresh_reference as full_refresh  # noqa: E402
import atomic_v3_hm_reference as hm  # noqa: E402
import atomic_v3_scalar_differential as h93h  # noqa: E402
import atomic_v3_scalar_reference as reference  # noqa: E402


FROZEN_NET_BYTES = 77_349_879
FROZEN_NET_SHA256 = (
    "00e46223822d06d7927e884eec10739ba19ef8dd82a6e262f627d361658080c2"
)
FROZEN_CORPUS_SHA256 = h93h.FROZEN_CORPUS_SHA256
FROZEN_BATCH_SHA256 = "6111fa15261596a452204aa99fa200b3fdcf764575c207e3c131216d6158045c"
FROZEN_CORPUS_FINGERPRINT = "0x4FBDB31B354FC080"
FROZEN_POSITIONS = 102
SUPPLEMENTAL_POSITIONS = 7
TOTAL_POSITIONS = FROZEN_POSITIONS + SUPPLEMENTAL_POSITIONS
WIDENING_PROBES = ("i16_signed", "i8_signed")
TAIL_PROBE_COUNTS = (0, 1, 3, 4, 7, 8, 15, 16, 17)
WIDENING_BEFORE = (
    1000,
    -1000,
    17,
    -17,
    40000,
    -40000,
    7,
    -7,
    123456,
    -123456,
    1,
    -1,
    99,
    -99,
    2048,
    -2048,
)
WIDENING_INPUTS = {
    "i16_signed": (
        -32768,
        -32767,
        -257,
        -1,
        0,
        1,
        255,
        256,
        32767,
        -8192,
        8192,
        -2,
        2,
        -128,
        127,
        42,
    ),
    "i8_signed": (
        -128,
        -127,
        -65,
        -1,
        0,
        1,
        63,
        64,
        127,
        -2,
        2,
        -32,
        32,
        -100,
        100,
        42,
    ),
}
REQUIRED_ISAS = ("scalar", "sse41", "avx2")
FROZEN_ROW_COUNTS = (218, 3_190, 2_564, 504, 2_992)


SCALAR_ARRAY_KEYS = frozenset(
    {
        "transformed",
        "fc0",
        "fc0_squared",
        "fc0_clipped",
        "fc1",
        "fc1_squared",
        "fc1_clipped",
        "fc2",
        "white.hm.rows",
        "white.capture_pair.rows",
        "white.king_blast_ep.rows",
        "white.blast_ring.rows",
        "white.accumulator",
        "white.psqt",
        "black.hm.rows",
        "black.capture_pair.rows",
        "black.king_blast_ep.rows",
        "black.blast_ring.rows",
        "black.accumulator",
        "black.psqt",
    }
)
SCALAR_INTEGER_KEYS = frozenset(
    {
        "side_to_move",
        "network_bucket",
        "psqt_difference",
        "psqt_value",
        "raw_output",
        "scaled_output",
        "positional_value",
    }
    | {
        f"{perspective}.perspective"
        for perspective in ("white", "black")
    }
    | {
        f"{perspective}.{slice_name}.size"
        for perspective in ("white", "black")
        for slice_name in ("hm", "capture_pair", "king_blast_ep", "blast_ring")
    }
    | {
        f"{perspective}.{slice_name}.orientation.{field}"
        for perspective in ("white", "black")
        for slice_name in ("hm", "capture_pair", "king_blast_ep", "blast_ring")
        for field in (
            "perspective",
            "own_king",
            "oriented_own_king",
            "vertical_xor",
            "horizontal_xor",
            "king_bucket",
        )
    }
    | {"white.hm.network_bucket", "black.hm.network_bucket"}
)
SCALAR_DIAGNOSTIC_KEYS = SCALAR_ARRAY_KEYS | SCALAR_INTEGER_KEYS

COUNTER_KEYS = (
    "bias_i16_rows",
    "hm_i16_rows",
    "capture_pair_i8_rows",
    "king_blast_ep_i16_rows",
    "blast_ring_i8_rows",
    "i16_rows",
    "i8_rows",
    "i16_lanes",
    "i8_lanes",
    "scalar_kernel_calls",
    "sse41_kernel_calls",
    "avx2_kernel_calls",
    "kernel_calls",
    "fallback_calls",
)


FINAL_SENTINEL_RE = re.compile(
    r"^AtomicNNUEV3 SIMD gate passed: "
    r"requested=(?P<requested>scalar|sse41|avx2) "
    r"executed=(?P<executed>scalar|sse41|avx2) "
    r"cases=(?P<cases>\d+) "
    r"comparisons=(?P<comparisons>\d+) "
    r"errors=(?P<errors>\d+) "
    r"error_probes=(?P<error_probes>\d+) "
    r"widening_probes=(?P<widening_probes>\d+) "
    r"i16_rows=(?P<i16_rows>\d+) "
    r"i8_rows=(?P<i8_rows>\d+) "
    r"kernel_calls=(?P<kernel_calls>\d+) "
    r"fallback_calls=(?P<fallback_calls>\d+) "
    r"fingerprint=0x(?P<fingerprint>[0-9A-F]{16})$"
)


class FixtureAuthenticationError(ValueError):
    """The supplied file is not the frozen H9.3g AtomicNNUEV3 fixture."""


class DifferentialFailure(RuntimeError):
    """The observed runner did not prove exact scalar/SIMD/Python identity."""


@dataclass(frozen=True)
class InputCase:
    index: int
    position: cp.CapturePosition
    fen: str
    chess960: bool


@dataclass(frozen=True)
class CaseRecord:
    index: int
    fields: Mapping[str, str]


@dataclass(frozen=True)
class ProbeRecord:
    index: int
    fields: Mapping[str, str]


@dataclass(frozen=True)
class ErrorProbeRecord:
    index: int
    fields: Mapping[str, str]


@dataclass(frozen=True)
class GateOutput:
    identity: Mapping[str, str]
    cases: Tuple[CaseRecord, ...]
    probes: Tuple[ProbeRecord, ...]
    error_probes: Tuple[ErrorProbeRecord, ...]
    summary: Mapping[str, str]
    sentinel: Mapping[str, str]


@dataclass(frozen=True)
class RowAccounting:
    cases: int
    bias_rows: int
    hm_rows: int
    capture_pair_rows: int
    king_blast_ep_rows: int
    blast_ring_rows: int

    @property
    def i16_rows(self) -> int:
        return self.bias_rows + self.hm_rows + self.king_blast_ep_rows

    @property
    def i8_rows(self) -> int:
        return self.capture_pair_rows + self.blast_ring_rows

    @property
    def kernel_calls(self) -> int:
        return self.i16_rows + self.i8_rows


def _stat_identity(value: os.stat_result) -> Tuple[int, int, int, int]:
    modified_ns = getattr(value, "st_mtime_ns", int(value.st_mtime * 1_000_000_000))
    return value.st_dev, value.st_ino, value.st_size, modified_ns


def authenticate_fixture(path: Path) -> str:
    """Authenticate size and SHA-256 from one stable, already-open handle."""

    digest = hashlib.sha256()
    try:
        with path.open("rb") as stream:
            before = os.fstat(stream.fileno())
            if before.st_size != FROZEN_NET_BYTES:
                raise FixtureAuthenticationError(
                    "AtomicNNUEV3 fixture size mismatch: "
                    f"expected={FROZEN_NET_BYTES} actual={before.st_size} file={path}"
                )
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
            after = os.fstat(stream.fileno())
    except OSError as error:
        raise FixtureAuthenticationError(
            f"could not read AtomicNNUEV3 fixture {path}: {error}"
        ) from error

    if _stat_identity(before) != _stat_identity(after):
        raise FixtureAuthenticationError(
            f"AtomicNNUEV3 fixture changed while it was authenticated: {path}"
        )
    actual = digest.hexdigest()
    if actual != FROZEN_NET_SHA256:
        raise FixtureAuthenticationError(
            "AtomicNNUEV3 fixture SHA-256 mismatch: "
            f"expected={FROZEN_NET_SHA256.upper()} actual={actual.upper()} file={path}"
        )
    return actual


def _fen(position: cp.CapturePosition) -> str:
    active = "w" if position.side_to_move == cp.WHITE else "b"
    ep = "-" if position.ep_square is None else cp.square_name(position.ep_square)
    rights = position.castling_rights or "-"
    return f"{h93f._placement(position)} {active} {rights} {ep} 0 1"


def corpus() -> Tuple[InputCase, ...]:
    """Return the frozen 102-position corpus plus seven boundary probes."""

    frozen = h93f.corpus()
    digest = h93f.corpus_digest(frozen)
    if len(frozen) != FROZEN_POSITIONS or digest != FROZEN_CORPUS_SHA256:
        raise DifferentialFailure(
            "H9.3f corpus identity differs: "
            f"positions={len(frozen)} digest={digest}"
        )
    row_targets = h93h._row_target_positions()
    bucket_probes = h93h._bucket_probe_positions()
    if len(row_targets) != 4 or len(bucket_probes) != 3:
        raise DifferentialFailure("H9.3h supplemental corpus inventory changed")
    values = tuple(frozen) + tuple(row_targets) + tuple(bucket_probes)
    if len(values) != TOTAL_POSITIONS:
        raise DifferentialFailure("SIMD corpus count changed")
    return tuple(
        InputCase(index, position, _fen(position), bool(position.atomic960))
        for index, position in enumerate(values)
    )


def _coverage_gate(
    network: reference.SparseNetwork, cases: Sequence[InputCase]
) -> None:
    frozen = tuple(case.position for case in cases[:FROZEN_POSITIONS])
    row_targets = tuple(
        case.position
        for case in cases[
            FROZEN_POSITIONS : FROZEN_POSITIONS + 4
        ]
    )
    bucket_probes = tuple(case.position for case in cases[-3:])
    h93h._check_supplemental(network, row_targets, bucket_probes, frozen)

    if not any(case.chess960 for case in cases):
        raise DifferentialFailure("SIMD corpus no longer includes Atomic960")
    if not any(case.position.ep_square is not None for case in cases):
        raise DifferentialFailure("SIMD corpus no longer includes EP metadata")

    horizontal = set()
    buckets = set()
    for case in cases:
        diagnostic = reference.evaluate(network, case.position)
        buckets.add(int(diagnostic["network_bucket"]))
        for perspective in cp.COLORS:
            emission = full_refresh.enumerate_full_refresh(case.position, perspective)
            horizontal.add(emission.orientation.horizontal_xor)
    if horizontal != {0, 7}:
        raise DifferentialFailure(
            f"SIMD corpus misses a horizontal mirror branch: {horizontal}"
        )
    if buckets != set(range(8)):
        raise DifferentialFailure(f"SIMD corpus misses material buckets: {buckets}")


def expected_diagnostic(
    network: reference.SparseNetwork, position: cp.CapturePosition
) -> Mapping[str, object]:
    """Expand the independent H9.3h result to the runner's complete schema."""

    base = dict(reference.evaluate(network, position))
    result: Dict[str, object] = {
        key: value
        for key, value in base.items()
        if key not in {
            f"{perspective}.{slice_name}"
            for perspective in ("white", "black")
            for slice_name in ("hm", "capture_pair", "king_blast_ep", "blast_ring")
        }
    }
    for perspective, prefix, color_index in (
        (cp.WHITE, "white", 0),
        (cp.BLACK, "black", 1),
    ):
        emission = full_refresh.enumerate_full_refresh(position, perspective)
        orientation = emission.orientation
        own_king = next(
            piece.square
            for piece in position.pieces
            if piece.color == perspective and piece.kind == cp.KING
        )
        hm_orientation = hm.orientation_for(perspective, own_king)
        if (
            orientation.vertical_xor != hm_orientation.vertical_xor
            or orientation.horizontal_xor != hm_orientation.horizontal_xor
            or orientation.oriented_own_king != hm_orientation.oriented_own_king
        ):
            raise DifferentialFailure("Python slice orientations disagree")
        result[f"{prefix}.perspective"] = color_index
        rows_by_slice = {
            "hm": tuple(row.physical_index for row in emission.hm),
            "capture_pair": tuple(
                row.physical_index for row in emission.capture_pairs
            ),
            "king_blast_ep": tuple(
                row.physical_index for row in emission.king_blast_ep
            ),
            "blast_ring": tuple(row.physical_index for row in emission.blast_ring),
        }
        for slice_name, rows in rows_by_slice.items():
            slice_prefix = f"{prefix}.{slice_name}"
            result[f"{slice_prefix}.size"] = len(rows)
            result[f"{slice_prefix}.rows"] = rows
            result[f"{slice_prefix}.orientation.perspective"] = color_index
            result[f"{slice_prefix}.orientation.own_king"] = own_king
            result[f"{slice_prefix}.orientation.oriented_own_king"] = (
                orientation.oriented_own_king
            )
            result[f"{slice_prefix}.orientation.vertical_xor"] = (
                orientation.vertical_xor
            )
            result[f"{slice_prefix}.orientation.horizontal_xor"] = (
                orientation.horizontal_xor
            )
            result[f"{slice_prefix}.orientation.king_bucket"] = (
                hm_orientation.king_bucket
            )
        result[f"{prefix}.hm.network_bucket"] = emission.network_bucket
    if set(result) != set(SCALAR_DIAGNOSTIC_KEYS):
        raise DifferentialFailure(
            "expanded Python diagnostic inventory differs: "
            f"missing={sorted(SCALAR_DIAGNOSTIC_KEYS - set(result))} "
            f"extra={sorted(set(result) - SCALAR_DIAGNOSTIC_KEYS)}"
        )
    return result


def encode_batch(cases: Sequence[InputCase]) -> str:
    records: List[str] = []
    for case in cases:
        records.extend(
            (
                "record=simd_input",
                f"case={case.index}",
                f"chess960={int(case.chess960)}",
                f"fen={case.fen}",
                f"end_input={case.index}",
            )
        )
    records.append(f"batch_cases={len(cases)}")
    return "\n".join(records) + "\n"


def _parse_integer(value: str, *, context: str) -> int:
    try:
        parsed = int(value)
    except ValueError as error:
        raise DifferentialFailure(f"{context} is not an integer: {value!r}") from error
    if str(parsed) != value:
        raise DifferentialFailure(f"{context} is noncanonical: {value!r}")
    return parsed


def _parse_csv(value: str, *, context: str) -> Tuple[int, ...]:
    if not value:
        return ()
    return tuple(
        _parse_integer(item, context=f"{context}[{index}]")
        for index, item in enumerate(value.split(","))
    )


def _read_fields(
    lines: Sequence[str], cursor: int, end_marker: str, *, context: str
) -> Tuple[Dict[str, str], int]:
    fields: Dict[str, str] = {}
    while cursor < len(lines) and lines[cursor] != end_marker:
        key, separator, value = lines[cursor].partition("=")
        if not separator or not key or key in fields:
            raise DifferentialFailure(
                f"{context} emitted malformed/duplicate field: {lines[cursor]!r}"
            )
        fields[key] = value
        cursor += 1
    if cursor >= len(lines):
        raise DifferentialFailure(f"{context} is truncated before {end_marker!r}")
    return fields, cursor + 1


def _parse_sentinel(line: str) -> Mapping[str, str]:
    match = FINAL_SENTINEL_RE.fullmatch(line)
    if match is None:
        raise DifferentialFailure(
            "final non-empty line is not the exact AtomicNNUEV3 SIMD sentinel: "
            f"{line!r}"
        )
    return match.groupdict()


def parse_gate_output(
    output: str, *, expected_cases: int, required_isa: str
) -> GateOutput:
    """Parse the runner wire strictly; no unframed or duplicate output passes."""

    lines = output.splitlines()
    while lines and not lines[-1]:
        lines.pop()
    if not lines:
        raise DifferentialFailure("SIMD runner emitted no output")
    sentinel = _parse_sentinel(lines[-1])
    lines = lines[:-1]
    if required_isa not in REQUIRED_ISAS:
        raise ValueError(f"unsupported required ISA: {required_isa}")

    cursor = 0
    if cursor >= len(lines) or lines[cursor] != "record=simd_identity":
        raise DifferentialFailure("SIMD runner identity record is missing or out of order")
    identity, cursor = _read_fields(
        lines, cursor + 1, "end_identity=1", context="SIMD identity"
    )

    probes: List[ProbeRecord] = []
    for index in range(len(WIDENING_PROBES)):
        if cursor >= len(lines) or lines[cursor] != "record=simd_widening_probe":
            raise DifferentialFailure(
                f"SIMD widening probe {index} is missing or out of order"
            )
        fields, cursor = _read_fields(
            lines,
            cursor + 1,
            f"end_probe={index}",
            context=f"SIMD widening probe {index}",
        )
        probes.append(ProbeRecord(index, fields))

    error_probes: List[ErrorProbeRecord] = []
    for index in range(4):
        if cursor >= len(lines) or lines[cursor] != "record=simd_error_probe":
            raise DifferentialFailure(
                f"SIMD error probe {index} is missing or out of order"
            )
        fields, cursor = _read_fields(
            lines,
            cursor + 1,
            f"end_error_probe={index}",
            context=f"SIMD error probe {index}",
        )
        error_probes.append(ErrorProbeRecord(index, fields))

    cases: List[CaseRecord] = []
    for index in range(expected_cases):
        if cursor >= len(lines) or lines[cursor] != "record=simd_case":
            raise DifferentialFailure(f"SIMD case {index} is missing or out of order")
        fields, cursor = _read_fields(
            lines,
            cursor + 1,
            f"end_case={index}",
            context=f"SIMD case {index}",
        )
        cases.append(CaseRecord(index, fields))

    if cursor >= len(lines) or lines[cursor] != "record=simd_summary":
        raise DifferentialFailure("SIMD runner summary is missing or out of order")
    summary, cursor = _read_fields(
        lines,
        cursor + 1,
        "end_summary=1",
        context="SIMD summary",
    )
    if cursor != len(lines):
        raise DifferentialFailure("SIMD runner emitted output after its summary")

    if len(cases) != expected_cases:
        raise DifferentialFailure(
            f"SIMD runner case count differs: {len(cases)} != {expected_cases}"
        )
    if sentinel["requested"] != required_isa or sentinel["executed"] != required_isa:
        raise DifferentialFailure(
            "SIMD runner did not execute the required ISA exactly: "
            f"requested={sentinel['requested']} executed={sentinel['executed']}"
        )
    return GateOutput(
        identity,
        tuple(cases),
        tuple(probes),
        tuple(error_probes),
        summary,
        sentinel,
    )


def _first_difference(actual: object, expected: object) -> str:
    if isinstance(actual, tuple) and isinstance(expected, tuple):
        if len(actual) != len(expected):
            return f"length {len(actual)} != {len(expected)}"
        for index, (left, right) in enumerate(zip(actual, expected)):
            if left != right:
                return f"index {index}: {left!r} != {right!r}"
    return f"{actual!r} != {expected!r}"


def _fingerprint_integer(state: int, value: int, width: int) -> int:
    bits = value & ((1 << (width * 8)) - 1)
    for byte in range(width):
        state ^= (bits >> (byte * 8)) & 0xFF
        state = (state * 1_099_511_628_211) & 0xFFFFFFFFFFFFFFFF
    return state


def _fingerprint_range(
    state: int, values: Sequence[int], width: int
) -> int:
    for value in values:
        state = _fingerprint_integer(state, value, width)
    return state


def diagnostic_fingerprint(diagnostic: Mapping[str, object]) -> str:
    """Reproduce the frozen H9.3h diagnostic FNV wire independently."""

    def array(key: str) -> Tuple[int, ...]:
        value = diagnostic[key]
        if not isinstance(value, tuple):
            raise DifferentialFailure(f"Python diagnostic {key} is not an array")
        return value

    state = 1_469_598_103_934_665_603
    state = _fingerprint_integer(state, int(diagnostic["side_to_move"]), 1)
    state = _fingerprint_integer(state, int(diagnostic["network_bucket"]), 4)
    for perspective, prefix in ((0, "white"), (1, "black")):
        state = _fingerprint_integer(state, perspective, 1)
        for slice_name in ("hm", "capture_pair", "king_blast_ep", "blast_ring"):
            base = f"{prefix}.{slice_name}"
            rows = array(f"{base}.rows")
            state = _fingerprint_integer(state, int(diagnostic[f"{base}.size"]), 4)
            state = _fingerprint_range(state, rows, 4)
        state = _fingerprint_range(state, array(f"{prefix}.accumulator"), 4)
        state = _fingerprint_range(state, array(f"{prefix}.psqt"), 8)
    state = _fingerprint_range(state, array("transformed"), 1)
    state = _fingerprint_integer(state, int(diagnostic["psqt_difference"]), 4)
    state = _fingerprint_integer(state, int(diagnostic["psqt_value"]), 4)
    for key, width in (
        ("fc0", 4),
        ("fc0_squared", 1),
        ("fc0_clipped", 1),
        ("fc1", 4),
        ("fc1_squared", 1),
        ("fc1_clipped", 1),
        ("fc2", 4),
    ):
        state = _fingerprint_range(state, array(key), width)
    state = _fingerprint_integer(state, int(diagnostic["raw_output"]), 8)
    state = _fingerprint_integer(state, int(diagnostic["scaled_output"]), 4)
    state = _fingerprint_integer(state, int(diagnostic["positional_value"]), 4)
    return f"0x{state:016X}"


def _diagnostic(fields: Mapping[str, str], prefix: str) -> Mapping[str, object]:
    expected_fields = {prefix + key for key in SCALAR_DIAGNOSTIC_KEYS}
    actual_fields = {
        key
        for key in fields
        if key.startswith(prefix)
        and key != prefix + "fingerprint"
        and not key.startswith(prefix + "counters.")
    }
    if actual_fields != expected_fields:
        raise DifferentialFailure(
            f"{prefix[:-1]} diagnostic inventory differs: "
            f"missing={sorted(expected_fields - actual_fields)} "
            f"extra={sorted(actual_fields - expected_fields)}"
        )
    result: Dict[str, object] = {}
    for key in SCALAR_INTEGER_KEYS:
        result[key] = _parse_integer(fields[prefix + key], context=prefix + key)
    for key in SCALAR_ARRAY_KEYS:
        result[key] = _parse_csv(fields[prefix + key], context=prefix + key)
    return result


def _compare_mapping(
    context: str, actual: Mapping[str, object], expected: Mapping[str, object]
) -> None:
    if set(actual) != set(expected):
        raise DifferentialFailure(
            f"{context} keys differ: actual={sorted(actual)} expected={sorted(expected)}"
        )
    for key, expected_value in expected.items():
        actual_value = actual[key]
        if actual_value != expected_value:
            raise DifferentialFailure(
                f"{context} {key} differs at "
                f"{_first_difference(actual_value, expected_value)}"
            )


def _unsigned_counter(fields: Mapping[str, str], key: str, *, context: str) -> int:
    if key not in fields:
        raise DifferentialFailure(f"{context} is missing {key}")
    value = _parse_integer(fields[key], context=f"{context}.{key}")
    if value < 0:
        raise DifferentialFailure(f"{context}.{key} is negative")
    return value


def _verify_case(
    record: CaseRecord,
    case: InputCase,
    expected: Mapping[str, object],
    required_isa: str,
) -> None:
    fields = record.fields
    fixed = {
        "case": str(case.index),
        "fen": case.fen,
        "chess960": str(int(case.chess960)),
        "isa.requested": required_isa,
        "isa.executed": required_isa,
        "status": "ok",
        "comparison.exact": "1",
    }
    for key, value in fixed.items():
        if fields.get(key) != value:
            raise DifferentialFailure(
                f"case {case.index} {key} differs: {fields.get(key)!r} != {value!r}"
            )
    scalar = _diagnostic(fields, "scalar.")
    simd = _diagnostic(fields, "simd.")
    _compare_mapping(f"case {case.index} scalar/Python", scalar, expected)
    _compare_mapping(f"case {case.index} SIMD/Python", simd, expected)
    _compare_mapping(f"case {case.index} scalar/SIMD", scalar, simd)

    for key in ("scalar.fingerprint", "simd.fingerprint"):
        value = fields.get(key, "")
        if re.fullmatch(r"0x[0-9A-F]{16}", value) is None:
            raise DifferentialFailure(f"case {case.index} has invalid {key}: {value!r}")
    if fields["scalar.fingerprint"] != fields["simd.fingerprint"]:
        raise DifferentialFailure(f"case {case.index} diagnostic fingerprints differ")
    expected_fingerprint = diagnostic_fingerprint(expected)
    if fields["scalar.fingerprint"] != expected_fingerprint:
        raise DifferentialFailure(
            f"case {case.index} fingerprint differs from Python: "
            f"{fields['scalar.fingerprint']} != {expected_fingerprint}"
        )
    per_case = expected_accounting((expected,))
    _verify_counters(
        fields,
        "simd.counters.",
        required_isa,
        per_case,
        context=f"case {case.index} counters",
    )
    expected_fields = set(fixed)
    expected_fields.update("scalar." + key for key in SCALAR_DIAGNOSTIC_KEYS)
    expected_fields.update("simd." + key for key in SCALAR_DIAGNOSTIC_KEYS)
    expected_fields.update(
        {"scalar.fingerprint", "simd.fingerprint"}
        | {"simd.counters." + key for key in COUNTER_KEYS}
    )
    if set(fields) != expected_fields:
        raise DifferentialFailure(
            f"case {case.index} field inventory differs: "
            f"missing={sorted(expected_fields - set(fields))} "
            f"extra={sorted(set(fields) - expected_fields)}"
        )


def _verify_probes(probes: Sequence[ProbeRecord], required_isa: str) -> None:
    field_order = (
        "probe",
        "kind",
        "isa.executed",
        "before",
        "input",
        "expected",
        "actual",
        "tail_counts",
        "kernel_calls",
        "fallback_calls",
        "tail_cases",
        "tail_canaries",
        "tails.exact",
        "comparison.exact",
    )
    for index, (record, name) in enumerate(zip(probes, WIDENING_PROBES)):
        fields = record.fields
        if tuple(fields) != field_order:
            raise DifferentialFailure(
                f"SIMD probe {index} ordered field inventory differs: "
                f"actual={tuple(fields)!r} expected={field_order!r}"
            )
        expected_text = {
            "probe": str(index),
            "kind": name,
            "isa.executed": required_isa,
            "kernel_calls": str(len(TAIL_PROBE_COUNTS) + 1),
            "fallback_calls": "0",
            "tail_cases": str(len(TAIL_PROBE_COUNTS)),
            "tail_canaries": str(len(TAIL_PROBE_COUNTS) * 2),
            "tails.exact": "1",
            "comparison.exact": "1",
        }
        for key, value in expected_text.items():
            if fields[key] != value:
                raise DifferentialFailure(
                    f"SIMD probe {index} {key} differs: {fields[key]!r} != {value!r}"
                )
        before = _parse_csv(fields["before"], context=f"probe {index}.before")
        weights = _parse_csv(fields["input"], context=f"probe {index}.input")
        expected = _parse_csv(fields["expected"], context=f"probe {index}.expected")
        actual = _parse_csv(fields["actual"], context=f"probe {index}.actual")
        tail_counts = _parse_csv(
            fields["tail_counts"], context=f"probe {index}.tail_counts"
        )
        if before != WIDENING_BEFORE or weights != WIDENING_INPUTS[name]:
            raise DifferentialFailure(
                f"SIMD probe {index} frozen signed widening vector differs"
            )
        if len(before) != len(weights) or expected != actual:
            raise DifferentialFailure(f"SIMD probe {index} has incoherent lane accounting")
        independent = tuple(left + right for left, right in zip(before, weights))
        if expected != independent:
            raise DifferentialFailure(f"SIMD probe {index} signed widening differs from Python")
        minimum, maximum = (-32768, 32767) if name == "i16_signed" else (-128, 127)
        if min(weights) != minimum or max(weights) != maximum:
            raise DifferentialFailure(
                f"SIMD probe {index} no longer covers both signed boundaries"
            )
        if tail_counts != TAIL_PROBE_COUNTS:
            raise DifferentialFailure(
                f"SIMD probe {index} tail boundary list differs: "
                f"{tail_counts!r} != {TAIL_PROBE_COUNTS!r}"
            )
        if (
            _parse_integer(fields["kernel_calls"], context=f"probe {index}.kernel_calls")
            != len(tail_counts) + 1
            or _parse_integer(fields["tail_cases"], context=f"probe {index}.tail_cases")
            != len(tail_counts)
            or _parse_integer(
                fields["tail_canaries"], context=f"probe {index}.tail_canaries"
            )
            != len(tail_counts) * 2
        ):
            raise DifferentialFailure(
                f"SIMD probe {index} tail call/case/canary accounting differs"
            )


def _verify_identity(
    identity: Mapping[str, str], network: reference.SparseNetwork, required_isa: str
) -> None:
    expected_fields = {
        "network.version",
        "network.hash",
        "network.description",
        "wire.policy",
        "wire.simd_permuted",
        "isa.requested",
        "isa.maximum",
    }
    if set(identity) != expected_fields:
        raise DifferentialFailure(
            "SIMD identity field inventory differs: "
            f"missing={sorted(expected_fields - set(identity))} "
            f"extra={sorted(set(identity) - expected_fields)}"
        )
    try:
        description = network.description.decode("utf-8")
    except UnicodeDecodeError as error:
        raise DifferentialFailure("Python fixture description is not UTF-8") from error
    exact = {
        "network.version": "0xA70C0003",
        "network.hash": "0xCF9A484",
        "network.description": description,
        "wire.simd_permuted": "1",
        "isa.requested": required_isa,
    }
    for key, value in exact.items():
        if identity[key] != value:
            raise DifferentialFailure(
                f"SIMD identity {key} differs: {identity[key]!r} != {value!r}"
            )
    if identity["wire.policy"] not in {"identity", "avx2_lasx", "avx512"}:
        raise DifferentialFailure("SIMD identity reported an unknown wire policy")
    maximum = identity["isa.maximum"]
    if maximum not in REQUIRED_ISAS or REQUIRED_ISAS.index(maximum) < REQUIRED_ISAS.index(
        required_isa
    ):
        raise DifferentialFailure("SIMD identity maximum ISA cannot satisfy the request")


def _verify_error_probes(probes: Sequence[ErrorProbeRecord]) -> None:
    expected = (
        ("invalid_side", 2, 1, 2),
        ("missing_black_king", 2, 1, 4),
        ("multiple_white_kings", 2, 1, 5),
        ("unsupported_isa", 1, 0, 0),
    )
    expected_fields = {
        "error_probe",
        "name",
        "actual.error",
        "actual.scalar_error",
        "actual.feature_error",
        "transactional",
        "comparison.exact",
    }
    if len(probes) != len(expected):
        raise DifferentialFailure("SIMD error probe count differs")
    for index, (record, values) in enumerate(zip(probes, expected)):
        fields = record.fields
        if set(fields) != expected_fields:
            raise DifferentialFailure(f"SIMD error probe {index} field inventory differs")
        name, simd_error, scalar_error, feature_error = values
        exact = {
            "error_probe": str(index),
            "name": name,
            "actual.error": str(simd_error),
            "actual.scalar_error": str(scalar_error),
            "actual.feature_error": str(feature_error),
            "transactional": "1",
            "comparison.exact": "1",
        }
        if dict(fields) != exact:
            raise DifferentialFailure(
                f"SIMD error probe {index} differs: {dict(fields)!r} != {exact!r}"
            )


def expected_accounting(
    diagnostics: Sequence[Mapping[str, object]],
) -> RowAccounting:
    def rows(key: str) -> int:
        total = 0
        for diagnostic in diagnostics:
            value = diagnostic[key]
            if not isinstance(value, tuple):
                raise DifferentialFailure(f"Python diagnostic {key} is not a row tuple")
            total += len(value)
        return total

    return RowAccounting(
        len(diagnostics),
        len(diagnostics) * 2,
        rows("white.hm.rows") + rows("black.hm.rows"),
        rows("white.capture_pair.rows") + rows("black.capture_pair.rows"),
        rows("white.king_blast_ep.rows") + rows("black.king_blast_ep.rows"),
        rows("white.blast_ring.rows") + rows("black.blast_ring.rows"),
    )


def _verify_counters(
    fields: Mapping[str, str],
    prefix: str,
    required_isa: str,
    expected: RowAccounting,
    *,
    context: str,
) -> Mapping[str, int]:
    actual = {
        key: _unsigned_counter(fields, prefix + key, context=context)
        for key in COUNTER_KEYS
    }
    row_expected = {
        "bias_i16_rows": expected.bias_rows,
        "hm_i16_rows": expected.hm_rows,
        "capture_pair_i8_rows": expected.capture_pair_rows,
        "king_blast_ep_i16_rows": expected.king_blast_ep_rows,
        "blast_ring_i8_rows": expected.blast_ring_rows,
        "i16_rows": expected.i16_rows,
        "i8_rows": expected.i8_rows,
        "i16_lanes": expected.i16_rows * 1024,
        "i8_lanes": expected.i8_rows * 1024,
        "kernel_calls": expected.kernel_calls,
        "fallback_calls": 0,
    }
    for key, value in row_expected.items():
        if actual[key] != value:
            raise DifferentialFailure(
                f"{context}.{key} differs from Python: {actual[key]} != {value}"
            )
    for isa in REQUIRED_ISAS:
        expected_calls = expected.kernel_calls if isa == required_isa else 0
        if actual[f"{isa}_kernel_calls"] != expected_calls:
            raise DifferentialFailure(
                f"{context}.{isa}_kernel_calls differs: "
                f"{actual[f'{isa}_kernel_calls']} != {expected_calls}"
            )
    if actual["kernel_calls"] != sum(
        actual[f"{isa}_kernel_calls"] for isa in REQUIRED_ISAS
    ):
        raise DifferentialFailure(f"{context} kernel call accounting is incompatible")
    return actual


def corpus_fingerprint(diagnostics: Sequence[Mapping[str, object]]) -> str:
    state = 1_469_598_103_934_665_603
    for index, diagnostic in enumerate(diagnostics):
        state = _fingerprint_integer(state, index, 4)
        state = _fingerprint_integer(
            state, int(diagnostic_fingerprint(diagnostic), 16), 8
        )
    return f"0x{state:016X}"


def _verify_accounting(
    output: GateOutput,
    required_isa: str,
    expected: RowAccounting,
    expected_fingerprint: str,
) -> None:
    summary = output.summary
    allowed_summary = {
        "isa.requested",
        "isa.executed",
        "cases",
        "comparisons",
        "errors",
        "error_probes",
        "widening_probes",
        "corpus_fingerprint",
    }
    allowed_summary.update("totals." + key for key in COUNTER_KEYS)
    if set(summary) != allowed_summary:
        raise DifferentialFailure(
            "SIMD summary field inventory differs: "
            f"missing={sorted(allowed_summary - set(summary))} "
            f"extra={sorted(set(summary) - allowed_summary)}"
        )
    for key in ("isa.requested", "isa.executed"):
        if summary[key] != required_isa:
            raise DifferentialFailure(f"SIMD summary {key} differs")

    cases = _unsigned_counter(summary, "cases", context="summary")
    comparisons = _unsigned_counter(summary, "comparisons", context="summary")
    errors = _unsigned_counter(summary, "errors", context="summary")
    if cases != expected.cases or comparisons != expected.cases or errors != 0:
        raise DifferentialFailure(
            "SIMD summary has incompatible case accounting: "
            f"cases={cases} comparisons={comparisons} errors={errors}"
        )
    if summary["error_probes"] != "4" or summary["widening_probes"] != "2":
        raise DifferentialFailure("SIMD summary probe accounting differs")
    counters = _verify_counters(
        summary, "totals.", required_isa, expected, context="summary counters"
    )
    fingerprint = summary["corpus_fingerprint"]
    if fingerprint != expected_fingerprint:
        raise DifferentialFailure(
            f"SIMD corpus fingerprint differs: {fingerprint} != {expected_fingerprint}"
        )
    sentinel = output.sentinel
    exact = {
        "requested": required_isa,
        "executed": required_isa,
        "cases": str(cases),
        "comparisons": str(comparisons),
        "errors": str(errors),
        "error_probes": "4",
        "widening_probes": "2",
        "i16_rows": str(counters["i16_rows"]),
        "i8_rows": str(counters["i8_rows"]),
        "kernel_calls": str(counters["kernel_calls"]),
        "fallback_calls": str(counters["fallback_calls"]),
        "fingerprint": fingerprint[2:],
    }
    if dict(sentinel) != exact:
        raise DifferentialFailure("SIMD final sentinel disagrees with the summary")


def run(runner: Path, fixture: Path, required_isa: str, timeout: float) -> str:
    runner = runner.expanduser().resolve()
    fixture = fixture.expanduser().resolve()
    if required_isa not in REQUIRED_ISAS:
        raise ValueError(f"--require-isa must be one of {', '.join(REQUIRED_ISAS)}")
    if timeout <= 0:
        raise ValueError("--timeout must be positive")
    if not runner.is_file():
        raise ValueError(f"SIMD runner does not exist: {runner}")

    authenticate_fixture(fixture)
    network = reference.load_frozen_fixture(fixture)
    cases = corpus()
    _coverage_gate(network, cases)
    expected = tuple(expected_diagnostic(network, case.position) for case in cases)
    expected_fingerprint = corpus_fingerprint(expected)
    if expected_fingerprint != FROZEN_CORPUS_FINGERPRINT:
        raise DifferentialFailure(
            "SIMD Python corpus fingerprint differs: "
            f"{expected_fingerprint} != {FROZEN_CORPUS_FINGERPRINT}"
        )
    accounting = expected_accounting(expected)
    if (
        accounting.bias_rows,
        accounting.hm_rows,
        accounting.capture_pair_rows,
        accounting.king_blast_ep_rows,
        accounting.blast_ring_rows,
    ) != FROZEN_ROW_COUNTS:
        raise DifferentialFailure(f"SIMD frozen row accounting differs: {accounting}")
    batch = encode_batch(cases)
    batch_sha = hashlib.sha256(batch.encode("utf-8")).hexdigest()
    if batch_sha != FROZEN_BATCH_SHA256:
        raise DifferentialFailure(
            f"SIMD batch identity differs: {batch_sha} != {FROZEN_BATCH_SHA256}"
        )

    completed = subprocess.run(
        [
            str(runner),
            "--net",
            str(fixture),
            "--require-isa",
            required_isa,
            "--batch",
        ],
        input=batch,
        text=True,
        capture_output=True,
        check=False,
        timeout=timeout,
    )
    if completed.returncode != 0:
        raise DifferentialFailure(
            f"C++ SIMD runner failed ({completed.returncode}):\n"
            f"stdout:\n{completed.stdout}\nstderr:\n{completed.stderr}"
        )
    observed = parse_gate_output(
        completed.stdout, expected_cases=len(cases), required_isa=required_isa
    )
    _verify_identity(observed.identity, network, required_isa)
    _verify_error_probes(observed.error_probes)
    for record, case, diagnostic in zip(observed.cases, cases, expected):
        _verify_case(record, case, diagnostic, required_isa)
    _verify_probes(observed.probes, required_isa)
    _verify_accounting(
        observed, required_isa, accounting, expected_fingerprint
    )
    return observed.sentinel["fingerprint"]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runner", type=Path, required=True)
    parser.add_argument("--net", type=Path, required=True)
    parser.add_argument("--require-isa", choices=REQUIRED_ISAS, required=True)
    parser.add_argument("--timeout", type=float, default=300.0)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        fingerprint = run(args.runner, args.net, args.require_isa, args.timeout)
    except (ValueError, DifferentialFailure, FixtureAuthenticationError) as error:
        print(f"Atomic V3 SIMD differential failed: {error}", file=sys.stderr)
        return 1
    print(
        "Atomic V3 SIMD Python differential passed: "
        f"isa={args.require_isa} frozen_positions={FROZEN_POSITIONS} "
        f"supplemental_positions={SUPPLEMENTAL_POSITIONS} "
        f"widening_probes={len(WIDENING_PROBES)} fingerprint={fingerprint}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
