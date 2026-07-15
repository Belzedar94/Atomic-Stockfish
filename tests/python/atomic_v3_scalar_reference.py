#!/usr/bin/env python3
"""Independent sparse-fixture and scalar executor for AtomicNNUEV3.

The frozen 77 MiB network is first validated by the independent wire oracle.
Only after its exact size and SHA-256 are authenticated is it scanned again to
retain every non-zero canonical parameter.  This module deliberately does not
import the fixture producer or any of its sentinel maps.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
from pathlib import Path
import struct
from typing import BinaryIO, Dict, Iterable, Mapping, Sequence, Tuple

import atomic_v3_blast_ring_reference as blast_ring
import atomic_v3_capture_pair_reference as capture_pair
import atomic_v3_full_refresh_reference as full_refresh
import atomic_v3_king_blast_ep_reference as king_blast_ep
import atomic_v3_wire_reference as wire


FROZEN_FIXTURE_SIZE = 77_349_879
FROZEN_FIXTURE_SHA256 = (
    "00E46223822D06D7927E884EEC10739BA19EF8DD82A6E262F627D361658080C2"
)

ACCUMULATOR_DIMENSIONS = 1024
PSQT_BUCKETS = 8
LAYER_STACKS = 8
FC0_INPUTS = 1024
FC0_OUTPUTS = 32
FC1_INPUTS = 64
FC1_OUTPUTS = 32
FC2_INPUTS = 128
FC2_OUTPUTS = 1

HM_PHYSICAL_OFFSET = 0
CAPTURE_PAIR_PHYSICAL_OFFSET = 22_528
KING_BLAST_EP_PHYSICAL_OFFSET = 62_540
BLAST_RING_PHYSICAL_OFFSET = 64_844

FEATURE_ACCUMULATOR_MINIMUM = -2_289_664
FEATURE_ACCUMULATOR_MAXIMUM = 2_289_116
INT32_MIN = -(1 << 31)
INT32_MAX = (1 << 31) - 1
RAW_OUTPUT_MINIMUM = -3_665_038_760
RAW_OUTPUT_MAXIMUM = 3_665_038_759
OUTPUT_SCALE_NUMERATOR = 9_600
OUTPUT_SCALE_DENOMINATOR = 16_384
OUTPUT_SCALE = 16
STREAM_CHUNK = 1 << 20

SparseRows = Mapping[int, Tuple[Tuple[int, int], ...]]


class ScalarOracleError(ValueError):
    """The frozen fixture or one scalar intermediate violated its contract."""


@dataclass(frozen=True)
class SparseDenseStack:
    fc0_biases: Tuple[int, ...]
    fc0_weights: SparseRows
    fc1_biases: Tuple[int, ...]
    fc1_weights: SparseRows
    fc2_biases: Tuple[int, ...]
    fc2_weights: SparseRows


@dataclass(frozen=True)
class SparseNetwork:
    path: Path
    description: bytes
    sha256: str
    biases: Tuple[int, ...]
    hm: SparseRows
    capture_pair: SparseRows
    king_blast_ep: SparseRows
    blast_ring: SparseRows
    hm_psqt: SparseRows
    dense: Tuple[SparseDenseStack, ...]


def _assert_contract() -> None:
    actual = (
        wire.ACCUMULATOR_DIMENSIONS,
        wire.PSQT_BUCKETS,
        wire.LAYER_STACKS,
        wire.FC0_INPUTS,
        wire.FC0_OUTPUTS,
        wire.FC1_INPUTS,
        wire.FC1_OUTPUTS,
        wire.FC2_INPUTS,
        capture_pair.PHYSICAL_OFFSET,
        king_blast_ep.PHYSICAL_OFFSET,
        blast_ring.PHYSICAL_OFFSET,
    )
    expected = (
        ACCUMULATOR_DIMENSIONS,
        PSQT_BUCKETS,
        LAYER_STACKS,
        FC0_INPUTS,
        FC0_OUTPUTS,
        FC1_INPUTS,
        FC1_OUTPUTS,
        FC2_INPUTS,
        CAPTURE_PAIR_PHYSICAL_OFFSET,
        KING_BLAST_EP_PHYSICAL_OFFSET,
        BLAST_RING_PHYSICAL_OFFSET,
    )
    if actual != expected:
        raise AssertionError(
            "AtomicNNUEV3 scalar Python constants drifted: "
            f"actual={actual!r} expected={expected!r}"
        )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while True:
            chunk = stream.read(STREAM_CHUNK)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest().upper()


def _read_exact(stream: BinaryIO, size: int, context: str) -> bytes:
    chunks = []
    remaining = size
    while remaining:
        chunk = stream.read(remaining)
        if not chunk:
            raise ScalarOracleError(f"truncated {context}")
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def _scan_sparse_sleb(
    stream: BinaryIO, span: wire.WireSpan, bits: int, name: str
) -> Dict[int, int]:
    """Decode one already validated SLEB payload and retain non-zero values."""

    stream.seek(span.payload_offset)
    remaining = span.payload_size
    element = 0
    pending = bytearray()
    value = 0
    shift = 0
    maximum_bytes = (bits + 6) // 7
    minimum = -(1 << (bits - 1))
    maximum = (1 << (bits - 1)) - 1
    sparse: Dict[int, int] = {}

    while remaining:
        chunk = _read_exact(stream, min(remaining, STREAM_CHUNK), name)
        remaining -= len(chunk)

        # Canonical zero is one 0x00 byte. Most of the controlled fixture can
        # therefore advance in C-speed without allocating Python integers.
        if not pending and chunk.count(0) == len(chunk):
            element += len(chunk)
            if element > span.elements:
                raise ScalarOracleError(f"{name} decoded too many elements")
            continue

        for byte in chunk:
            pending.append(byte)
            value |= (byte & 0x7F) << shift
            shift += 7
            if len(pending) > maximum_bytes:
                raise ScalarOracleError(f"{name} contains an overlong SLEB value")
            if byte & 0x80:
                continue
            if byte & 0x40:
                value -= 1 << shift
            if not minimum <= value <= maximum:
                raise ScalarOracleError(f"{name} contains a value outside i{bits}")
            if element >= span.elements:
                raise ScalarOracleError(f"{name} decoded too many elements")
            if value:
                sparse[element] = value
            element += 1
            pending.clear()
            value = 0
            shift = 0

    if pending or element != span.elements:
        raise ScalarOracleError(
            f"{name} decoded {element} elements, expected {span.elements}"
        )
    return sparse


def _scan_sparse_i8(
    stream: BinaryIO, span: wire.WireSpan, name: str
) -> Dict[int, int]:
    if span.payload_size != span.elements:
        raise ScalarOracleError(f"{name} raw-i8 span has inconsistent dimensions")
    stream.seek(span.payload_offset)
    consumed = 0
    sparse: Dict[int, int] = {}
    while consumed < span.elements:
        chunk = _read_exact(
            stream, min(STREAM_CHUNK, span.elements - consumed), name
        )
        if chunk.count(0) != len(chunk):
            for relative, byte in enumerate(chunk):
                if byte:
                    sparse[consumed + relative] = byte if byte < 128 else byte - 256
        consumed += len(chunk)
    return sparse


def _read_i32_vector(
    stream: BinaryIO, span: wire.WireSpan, count: int, name: str
) -> Tuple[int, ...]:
    if span.payload_size != count * 4 or span.elements != count:
        raise ScalarOracleError(f"{name} i32 span has inconsistent dimensions")
    stream.seek(span.payload_offset)
    return tuple(struct.unpack(f"<{count}i", _read_exact(stream, count * 4, name)))


def _as_rows(flat: Mapping[int, int], width: int, name: str) -> SparseRows:
    rows: Dict[int, list[Tuple[int, int]]] = {}
    for index, value in flat.items():
        row, column = divmod(index, width)
        rows.setdefault(row, []).append((column, value))
    result = {
        row: tuple(sorted(values)) for row, values in sorted(rows.items())
    }
    if sum(len(values) for values in result.values()) != len(flat):
        raise ScalarOracleError(f"{name} sparse row conversion lost a value")
    return result


def _nonzero_count(rows: SparseRows) -> int:
    return sum(len(values) for values in rows.values())


def _require_sparse_count(name: str, actual: int, expected: int) -> None:
    if actual != expected:
        raise ScalarOracleError(
            f"{name} non-zero count differs: {actual} != {expected}"
        )


def load_frozen_fixture(path: Path) -> SparseNetwork:
    """Authenticate and sparsely decode the exact mixed-wire H9.3g fixture."""

    _assert_contract()
    path = path.expanduser().resolve()
    if path.stat().st_size != FROZEN_FIXTURE_SIZE:
        raise ScalarOracleError(
            "AtomicNNUEV3 scalar fixture size differs from the frozen identity"
        )

    try:
        parsed = wire.parse_network(path)
    except (OSError, wire.WireError, ValueError) as error:
        raise ScalarOracleError(f"AtomicNNUEV3 fixture wire validation failed: {error}") from error
    if parsed.size != FROZEN_FIXTURE_SIZE or parsed.sha256 != FROZEN_FIXTURE_SHA256:
        raise ScalarOracleError(
            "AtomicNNUEV3 scalar fixture SHA-256 differs from the frozen identity"
        )

    required_spans = {
        "biases",
        "hm",
        "capture_pair",
        "king_blast_ep",
        "blast_ring",
        "hm_psqt",
    }
    for bucket in range(LAYER_STACKS):
        prefix = f"dense.{bucket}"
        required_spans.update(
            {
                f"{prefix}.fc0_biases",
                f"{prefix}.fc0_weights",
                f"{prefix}.fc1_biases",
                f"{prefix}.fc1_weights",
                f"{prefix}.fc2_bias",
                f"{prefix}.fc2_weights",
            }
        )
    if set(parsed.spans) != required_spans:
        raise ScalarOracleError("AtomicNNUEV3 fixture span inventory differs")

    with path.open("rb") as stream:
        bias_sparse = _scan_sparse_sleb(
            stream, parsed.spans["biases"], 16, "biases"
        )
        hm_flat = _scan_sparse_sleb(stream, parsed.spans["hm"], 16, "hm")
        capture_flat = _scan_sparse_i8(
            stream, parsed.spans["capture_pair"], "capture_pair"
        )
        king_flat = _scan_sparse_sleb(
            stream, parsed.spans["king_blast_ep"], 16, "king_blast_ep"
        )
        ring_flat = _scan_sparse_i8(
            stream, parsed.spans["blast_ring"], "blast_ring"
        )
        psqt_flat = _scan_sparse_sleb(
            stream, parsed.spans["hm_psqt"], 32, "hm_psqt"
        )

        dense = []
        for bucket in range(LAYER_STACKS):
            prefix = f"dense.{bucket}"
            dense.append(
                SparseDenseStack(
                    _read_i32_vector(
                        stream,
                        parsed.spans[f"{prefix}.fc0_biases"],
                        FC0_OUTPUTS,
                        f"{prefix}.fc0_biases",
                    ),
                    _as_rows(
                        _scan_sparse_i8(
                            stream,
                            parsed.spans[f"{prefix}.fc0_weights"],
                            f"{prefix}.fc0_weights",
                        ),
                        FC0_INPUTS,
                        f"{prefix}.fc0_weights",
                    ),
                    _read_i32_vector(
                        stream,
                        parsed.spans[f"{prefix}.fc1_biases"],
                        FC1_OUTPUTS,
                        f"{prefix}.fc1_biases",
                    ),
                    _as_rows(
                        _scan_sparse_i8(
                            stream,
                            parsed.spans[f"{prefix}.fc1_weights"],
                            f"{prefix}.fc1_weights",
                        ),
                        FC1_INPUTS,
                        f"{prefix}.fc1_weights",
                    ),
                    _read_i32_vector(
                        stream,
                        parsed.spans[f"{prefix}.fc2_bias"],
                        FC2_OUTPUTS,
                        f"{prefix}.fc2_bias",
                    ),
                    _as_rows(
                        _scan_sparse_i8(
                            stream,
                            parsed.spans[f"{prefix}.fc2_weights"],
                            f"{prefix}.fc2_weights",
                        ),
                        FC2_INPUTS,
                        f"{prefix}.fc2_weights",
                    ),
                )
            )

    _require_sparse_count("biases", len(bias_sparse), 11)
    hm = _as_rows(hm_flat, ACCUMULATOR_DIMENSIONS, "hm")
    capture = _as_rows(
        capture_flat, ACCUMULATOR_DIMENSIONS, "capture_pair"
    )
    king = _as_rows(king_flat, ACCUMULATOR_DIMENSIONS, "king_blast_ep")
    ring = _as_rows(ring_flat, ACCUMULATOR_DIMENSIONS, "blast_ring")
    psqt = _as_rows(psqt_flat, PSQT_BUCKETS, "hm_psqt")
    _require_sparse_count("hm", _nonzero_count(hm), 9)
    _require_sparse_count("capture_pair", _nonzero_count(capture), 10)
    _require_sparse_count("king_blast_ep", _nonzero_count(king), 9)
    _require_sparse_count("blast_ring", _nonzero_count(ring), 9)
    _require_sparse_count("hm_psqt", _nonzero_count(psqt), 256)
    for bucket, stack in enumerate(dense):
        _require_sparse_count(
            f"dense.{bucket}.fc0_biases",
            sum(value != 0 for value in stack.fc0_biases),
            2,
        )
        _require_sparse_count(
            f"dense.{bucket}.fc0_weights", _nonzero_count(stack.fc0_weights), 6
        )
        _require_sparse_count(
            f"dense.{bucket}.fc1_biases",
            sum(value != 0 for value in stack.fc1_biases),
            2,
        )
        _require_sparse_count(
            f"dense.{bucket}.fc1_weights", _nonzero_count(stack.fc1_weights), 6
        )
        _require_sparse_count(
            f"dense.{bucket}.fc2_biases",
            sum(value != 0 for value in stack.fc2_biases),
            1,
        )
        _require_sparse_count(
            f"dense.{bucket}.fc2_weights", _nonzero_count(stack.fc2_weights), 0
        )

    # The second pass must still describe the same authenticated pathname.
    if path.stat().st_size != FROZEN_FIXTURE_SIZE or _sha256(path) != FROZEN_FIXTURE_SHA256:
        raise ScalarOracleError("AtomicNNUEV3 fixture changed during sparse decoding")

    biases = [0] * ACCUMULATOR_DIMENSIONS
    for output, value in bias_sparse.items():
        if not 0 <= output < ACCUMULATOR_DIMENSIONS:
            raise ScalarOracleError("bias index escaped the accumulator")
        biases[output] = value
    return SparseNetwork(
        path,
        parsed.description,
        parsed.sha256,
        tuple(biases),
        hm,
        capture,
        king,
        ring,
        psqt,
        tuple(dense),
    )


def trunc_div(value: int, divisor: int) -> int:
    """C++ signed integer division for a positive divisor."""

    if divisor <= 0:
        raise ValueError("divisor must be positive")
    return value // divisor if value >= 0 else -((-value) // divisor)


def _checked_i32(value: int, context: str) -> int:
    if not INT32_MIN <= value <= INT32_MAX:
        raise ScalarOracleError(f"{context} escaped i32: {value}")
    return value


def _add_rows(
    accumulation: list[int],
    physical_rows: Iterable[int],
    physical_offset: int,
    weights: SparseRows,
    name: str,
) -> None:
    for physical in physical_rows:
        local = physical - physical_offset
        if local < 0:
            raise ScalarOracleError(f"{name} physical row precedes its slice")
        for output, value in weights.get(local, ()):
            accumulation[output] += value


def _perspective(
    network: SparseNetwork,
    position: capture_pair.CapturePosition,
    perspective: str,
) -> Tuple[full_refresh.FullRefreshEmission, Tuple[int, ...], Tuple[int, ...]]:
    emission = full_refresh.enumerate_full_refresh(position, perspective)
    indices = emission.physical_indices()
    accumulator = list(network.biases)
    _add_rows(accumulator, indices["hm"], HM_PHYSICAL_OFFSET, network.hm, "hm")
    _add_rows(
        accumulator,
        indices["capture_pair"],
        CAPTURE_PAIR_PHYSICAL_OFFSET,
        network.capture_pair,
        "capture_pair",
    )
    _add_rows(
        accumulator,
        indices["king_blast_ep"],
        KING_BLAST_EP_PHYSICAL_OFFSET,
        network.king_blast_ep,
        "king_blast_ep",
    )
    _add_rows(
        accumulator,
        indices["blast_ring"],
        BLAST_RING_PHYSICAL_OFFSET,
        network.blast_ring,
        "blast_ring",
    )
    for value in accumulator:
        if not FEATURE_ACCUMULATOR_MINIMUM <= value <= FEATURE_ACCUMULATOR_MAXIMUM:
            raise ScalarOracleError("feature accumulator escaped the proved envelope")

    psqt = [0] * PSQT_BUCKETS
    for row in indices["hm"]:
        for bucket, value in network.hm_psqt.get(row, ()):
            psqt[bucket] += value
    for value in psqt:
        _checked_i32(value, "PSQT accumulator")
    return emission, tuple(accumulator), tuple(psqt)


def _affine(
    inputs: Sequence[int],
    biases: Sequence[int],
    weights: SparseRows,
    context: str,
) -> Tuple[int, ...]:
    outputs = list(biases)
    for output, row in weights.items():
        if not 0 <= output < len(outputs):
            raise ScalarOracleError(f"{context} output index escaped its layer")
        for input_index, weight in row:
            if not 0 <= input_index < len(inputs):
                raise ScalarOracleError(f"{context} input index escaped its layer")
            outputs[output] += weight * inputs[input_index]
    return tuple(_checked_i32(value, context) for value in outputs)


def _squared(values: Sequence[int], shift: int) -> Tuple[int, ...]:
    return tuple(min(127, (value * value) >> shift) for value in values)


def _clipped(values: Sequence[int], shift: int) -> Tuple[int, ...]:
    return tuple(0 if value <= 0 else min(127, value >> shift) for value in values)


def propagate_dense(
    transformed: Sequence[int], stack: SparseDenseStack
) -> Mapping[str, object]:
    if len(transformed) != FC0_INPUTS or any(
        not 0 <= value <= 127 for value in transformed
    ):
        raise ScalarOracleError("dense input must contain 1,024 values in [0, 127]")
    fc0 = _affine(transformed, stack.fc0_biases, stack.fc0_weights, "fc0")
    fc0_squared = _squared(fc0, 21)
    fc0_clipped = _clipped(fc0, 7)
    fc1_input = fc0_squared + fc0_clipped
    fc1 = _affine(fc1_input, stack.fc1_biases, stack.fc1_weights, "fc1")
    fc1_squared = _squared(fc1, 19)
    fc1_clipped = _clipped(fc1, 6)
    fc2_input = fc1_input + fc1_squared + fc1_clipped
    fc2 = _affine(fc2_input, stack.fc2_biases, stack.fc2_weights, "fc2")
    raw = fc2[0] + fc0[-2] - fc0[-1]
    if not RAW_OUTPUT_MINIMUM <= raw <= RAW_OUTPUT_MAXIMUM:
        raise ScalarOracleError("raw dense output escaped the scalable envelope")
    scaled = _checked_i32(
        trunc_div(raw * OUTPUT_SCALE_NUMERATOR, OUTPUT_SCALE_DENOMINATOR),
        "scaled output",
    )
    return {
        "fc0": fc0,
        "fc0_squared": fc0_squared,
        "fc0_clipped": fc0_clipped,
        "fc1": fc1,
        "fc1_squared": fc1_squared,
        "fc1_clipped": fc1_clipped,
        "fc2": fc2,
        "raw_output": raw,
        "scaled_output": scaled,
        "positional_value": trunc_div(scaled, OUTPUT_SCALE),
    }


def evaluate(
    network: SparseNetwork, position: capture_pair.CapturePosition
) -> Mapping[str, object]:
    by_color = {
        perspective: _perspective(network, position, perspective)
        for perspective in capture_pair.COLORS
    }
    white = by_color[capture_pair.WHITE]
    black = by_color[capture_pair.BLACK]
    if white[0].network_bucket != black[0].network_bucket:
        raise ScalarOracleError("perspectives selected different dense buckets")
    bucket = white[0].network_bucket
    if not 0 <= bucket < LAYER_STACKS:
        raise ScalarOracleError("position selected an invalid dense bucket")

    opponent_color = (
        capture_pair.BLACK
        if position.side_to_move == capture_pair.WHITE
        else capture_pair.WHITE
    )
    stm = by_color[position.side_to_move]
    opponent = by_color[opponent_color]

    transformed = tuple(
        min(255, max(0, stm[1][index]))
        * min(255, max(0, stm[1][index + 512]))
        // 512
        for index in range(512)
    ) + tuple(
        min(255, max(0, opponent[1][index]))
        * min(255, max(0, opponent[1][index + 512]))
        // 512
        for index in range(512)
    )

    psqt_difference = _checked_i32(
        trunc_div(stm[2][bucket] - opponent[2][bucket], 2),
        "PSQT perspective difference",
    )
    result: Dict[str, object] = {
        "side_to_move": 0 if position.side_to_move == capture_pair.WHITE else 1,
        "network_bucket": bucket,
        "transformed": transformed,
        "psqt_difference": psqt_difference,
        "psqt_value": trunc_div(psqt_difference, OUTPUT_SCALE),
    }
    result.update(propagate_dense(transformed, network.dense[bucket]))

    for perspective, prefix in (
        (capture_pair.WHITE, "white"),
        (capture_pair.BLACK, "black"),
    ):
        emission, accumulator, psqt = by_color[perspective]
        rows = emission.physical_indices()
        result[f"{prefix}.hm"] = rows["hm"]
        result[f"{prefix}.capture_pair"] = rows["capture_pair"]
        result[f"{prefix}.king_blast_ep"] = rows["king_blast_ep"]
        result[f"{prefix}.blast_ring"] = rows["blast_ring"]
        result[f"{prefix}.accumulator"] = accumulator
        result[f"{prefix}.psqt"] = psqt
    return result


def adversarial_dense_vector(
    negative: bool,
) -> Tuple[Tuple[int, ...], SparseDenseStack]:
    """Independent reconstruction of the C++ signed dense boundary vector."""

    transformed = tuple((index * 29 + 3) % 128 for index in range(FC0_INPUTS))

    fc0_biases = tuple((output - 16) * 97 for output in range(FC0_OUTPUTS))
    mutable_fc0_biases = list(fc0_biases)
    mutable_fc0_biases[0] = 22_000
    mutable_fc0_biases[30] = -14_000
    mutable_fc0_biases[31] = 18_000
    fc0_flat: Dict[int, int] = {}
    fc0_boundaries = (0, 31, 32, 511, 512, 1023)
    fc0_values = (3, -5, 7, -11, 13, -17)
    for output in (0, 5, 30, 31):
        for input_index, weight in zip(fc0_boundaries, fc0_values):
            fc0_flat[output * FC0_INPUTS + input_index] = (
                weight if output % 2 == 0 else -weight
            )

    fc1_biases = tuple((output - 12) * 53 for output in range(FC1_OUTPUTS))
    mutable_fc1_biases = list(fc1_biases)
    mutable_fc1_biases[0] = 15_000
    mutable_fc1_biases[31] = 9_000
    fc1_flat: Dict[int, int] = {}
    fc1_boundaries = (0, 31, 32, 63)
    fc1_values = (-9, 11, -13, 15)
    for output in (0, 7, 31):
        for input_index, weight in zip(fc1_boundaries, fc1_values):
            fc1_flat[output * FC1_INPUTS + input_index] = (
                weight if output % 2 == 0 else -weight
            )

    fc2_flat = {
        index: weight
        for index, weight in zip(
            (0, 63, 64, 95, 96, 127), (19, -23, 29, -31, 37, -41)
        )
    }
    return transformed, SparseDenseStack(
        tuple(mutable_fc0_biases),
        _as_rows(fc0_flat, FC0_INPUTS, "adversarial.fc0"),
        tuple(mutable_fc1_biases),
        _as_rows(fc1_flat, FC1_INPUTS, "adversarial.fc1"),
        (-90_000 if negative else 90_000,),
        _as_rows(fc2_flat, FC2_INPUTS, "adversarial.fc2"),
    )


_assert_contract()
