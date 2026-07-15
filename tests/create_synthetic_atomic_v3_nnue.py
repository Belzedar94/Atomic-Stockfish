#!/usr/bin/env python3
"""Create the deterministic AtomicNNUEV3 mixed-wire test network.

The large feature tensors are emitted sparsely and in bounded chunks.  The
fixture covers signed/canonical SLEB boundaries, raw i8 signs, SIMD block
boundaries, the exact safe PSQT top-32 limit, and eight distinguishable safe
SFNNv15 stacks.  Existing output files are never overwritten.
"""

from __future__ import annotations

import argparse
import hashlib
from pathlib import Path
import struct
import sys
from typing import BinaryIO, Mapping, Optional, Sequence, Union


ROOT = Path(__file__).resolve().parents[1]
PYTHON_DIR = ROOT / "tests" / "python"
sys.path.insert(0, str(PYTHON_DIR))

import atomic_v3_wire_reference as wire  # noqa: E402


FILE_VERSION = 0xA70C0003
NETWORK_HASH = 0x0CF9A484
FEATURE_TRANSFORMER_HASH = 0x6FCAD592
ARCHITECTURE_HASH = 0x63337116
DESCRIPTION = b"Atomic-Stockfish AtomicNNUEV3 controlled mixed-wire CI source"
LEB128_MAGIC = b"COMPRESSED_LEB128"

ACCUMULATOR_DIMENSIONS = 1024
HM_DIMENSIONS = 22528
CAPTURE_PAIR_DIMENSIONS = 40012
KING_BLAST_EP_DIMENSIONS = 2304
BLAST_RING_DIMENSIONS = 10240
PSQT_BUCKETS = 8
LAYER_STACKS = 8
FC0_INPUTS = 1024
FC0_OUTPUTS = 32
FC1_INPUTS = 64
FC1_OUTPUTS = 32
FC2_INPUTS = 128
INT32_MAX = (1 << 31) - 1

# Frozen from the first controlled, timestamp-free generation.  Normal mode
# authenticates both values; only the explicit --measure workflow bypasses the
# pins so a deliberate fixture revision can establish a new identity.
EXPECTED_SIZE: Optional[int] = 77_349_879
EXPECTED_SHA256 = "00E46223822D06D7927E884EEC10739BA19EF8DD82A6E262F627D361658080C2"
ZERO_CHUNK = bytes(1 << 20)

BIAS_SENTINELS = {
    0: 64,
    1: -65,
    7: 32767,
    8: -32768,
    15: -1,
    16: 1,
    31: 63,
    32: -64,
    511: 127,
    512: -128,
    1023: 42,
}
HM_SENTINELS = {
    0: 32767,
    1: -32768,
    7: 64,
    8: -65,
    15: -1,
    16: 1,
    1023: 63,
    1024: -64,
    HM_DIMENSIONS * ACCUMULATOR_DIMENSIONS - 1: 1234,
}
CAPTURE_PAIR_SENTINELS = {
    0: -128,
    1: -1,
    2: 1,
    7: 127,
    8: -127,
    15: 126,
    16: -2,
    1023: 17,
    1024: -17,
    CAPTURE_PAIR_DIMENSIONS * ACCUMULATOR_DIMENSIONS - 1: 91,
}
KING_BLAST_EP_SENTINELS = {
    0: -32768,
    1: 32767,
    7: -65,
    8: 64,
    15: -1,
    16: 1,
    1023: -64,
    1024: 63,
    KING_BLAST_EP_DIMENSIONS * ACCUMULATOR_DIMENSIONS - 1: -1234,
}
BLAST_RING_SENTINELS = {
    0: 127,
    1: -128,
    7: -1,
    8: 1,
    15: -127,
    16: 126,
    1023: -19,
    1024: 19,
    BLAST_RING_DIMENSIONS * ACCUMULATOR_DIMENSIONS - 1: -91,
}


def _psqt_sentinels() -> dict[int, int]:
    values: dict[int, int] = {}
    for row in range(32):
        magnitude = 67_108_863 if row == 31 else 67_108_864
        for bucket in range(PSQT_BUCKETS):
            sign = -1 if (row + bucket) & 1 else 1
            values[row * PSQT_BUCKETS + bucket] = sign * magnitude
    return values


PSQT_SENTINELS = _psqt_sentinels()

FC0_WEIGHT_SENTINELS = {
    FC0_INPUTS + 0: -128,
    FC0_INPUTS + 7: -1,
    FC0_INPUTS + 8: 1,
    FC0_INPUTS + 15: 127,
    2 * FC0_INPUTS + 16: -127,
    2 * FC0_INPUTS + 31: 126,
}
FC1_WEIGHT_SENTINELS = {
    FC1_INPUTS + 0: 127,
    FC1_INPUTS + 7: 1,
    FC1_INPUTS + 8: -1,
    FC1_INPUTS + 15: -128,
    2 * FC1_INPUTS + 16: 126,
    2 * FC1_INPUTS + 31: -127,
}


def sleb_payload_element_offset(
    sentinels: Mapping[int, int], index: int
) -> int:
    """Return the deterministic byte offset of one sparse SLEB element."""

    if index < 0:
        raise ValueError("negative SLEB element index")
    return index + sum(
        len(wire.encode_sleb128(value)) - 1
        for candidate, value in sentinels.items()
        if candidate < index
    )


def dense_parameters(bucket: int) -> tuple[
    tuple[int, ...],
    Mapping[int, int],
    tuple[int, ...],
    Mapping[int, int],
    int,
    Mapping[int, int],
]:
    if not 0 <= bucket < LAYER_STACKS:
        raise ValueError(f"layer-stack bucket out of range: {bucket}")
    fc0_biases = [0] * FC0_OUTPUTS
    fc0_biases[0] = INT32_MAX  # exact accepted affine boundary, zero row
    fc0_biases[29] = bucket + 1
    fc1_biases = [0] * FC1_OUTPUTS
    fc1_biases[0] = -INT32_MAX  # exact accepted affine boundary, zero row
    fc1_biases[31] = -(bucket + 1)
    fc2_bias = INT32_MAX - bucket
    return (
        tuple(fc0_biases),
        FC0_WEIGHT_SENTINELS,
        tuple(fc1_biases),
        FC1_WEIGHT_SENTINELS,
        fc2_bias,
        {},
    )


def selected_indices() -> dict[str, tuple[int, ...]]:
    selected = {
        "biases": tuple(BIAS_SENTINELS),
        "hm": tuple(HM_SENTINELS),
        "capture_pair": tuple(CAPTURE_PAIR_SENTINELS),
        "king_blast_ep": tuple(KING_BLAST_EP_SENTINELS),
        "blast_ring": tuple(BLAST_RING_SENTINELS),
        "hm_psqt": tuple(sorted(PSQT_SENTINELS)[:16])
        + tuple(sorted(PSQT_SENTINELS)[-16:]),
    }
    for bucket in range(LAYER_STACKS):
        prefix = f"dense.{bucket}"
        selected[f"{prefix}.fc0_biases"] = (0, 29, 30, 31)
        selected[f"{prefix}.fc0_weights"] = tuple(FC0_WEIGHT_SENTINELS)
        selected[f"{prefix}.fc1_biases"] = (0, 31)
        selected[f"{prefix}.fc1_weights"] = tuple(FC1_WEIGHT_SENTINELS)
        selected[f"{prefix}.fc2_bias"] = (0,)
        selected[f"{prefix}.fc2_weights"] = (0, FC2_INPUTS - 1)
    return selected


SELECTED_INDICES = selected_indices()


def expected_selected_values() -> dict[str, int]:
    tensors: dict[str, Mapping[int, int]] = {
        "biases": BIAS_SENTINELS,
        "hm": HM_SENTINELS,
        "capture_pair": CAPTURE_PAIR_SENTINELS,
        "king_blast_ep": KING_BLAST_EP_SENTINELS,
        "blast_ring": BLAST_RING_SENTINELS,
        "hm_psqt": PSQT_SENTINELS,
    }
    expected: dict[str, int] = {}
    for name, indices in SELECTED_INDICES.items():
        if not name.startswith("dense."):
            values = tensors[name]
            for index in indices:
                expected[f"{name}[{index}]"] = values.get(index, 0)
            continue
        _, bucket_text, field = name.split(".")
        bucket = int(bucket_text)
        fc0_biases, fc0_weights, fc1_biases, fc1_weights, fc2_bias, fc2_weights = (
            dense_parameters(bucket)
        )
        fields: dict[str, Union[Mapping[int, int], tuple[int, ...]]] = {
            "fc0_biases": fc0_biases,
            "fc0_weights": fc0_weights,
            "fc1_biases": fc1_biases,
            "fc1_weights": fc1_weights,
            "fc2_bias": (fc2_bias,),
            "fc2_weights": fc2_weights,
        }
        values = fields[field]
        for index in indices:
            if isinstance(values, Mapping):
                value = values.get(index, 0)
            else:
                value = values[index]
            expected[f"{name}[{index}]"] = value
    return expected


class HashedWriter:
    def __init__(self, output: BinaryIO):
        self.output = output
        self.digest = hashlib.sha256()
        self.size = 0

    def write(self, data: bytes) -> None:
        written = self.output.write(data)
        if written != len(data):
            raise OSError("short write while creating AtomicNNUEV3 fixture")
        self.digest.update(data)
        self.size += len(data)

    def uint32(self, value: int) -> None:
        self.write(struct.pack("<I", value))

    def int32(self, value: int) -> None:
        self.write(struct.pack("<i", value))

    def zeros(self, size: int) -> None:
        while size:
            count = min(size, len(ZERO_CHUNK))
            self.write(ZERO_CHUNK[:count])
            size -= count

    def sparse_sleb(
        self,
        elements: int,
        sentinels: Mapping[int, int],
        *,
        bits: int,
    ) -> None:
        ordered = sorted(sentinels.items())
        if ordered and (ordered[0][0] < 0 or ordered[-1][0] >= elements):
            raise ValueError("SLEB sentinel lies outside its tensor")
        minimum = -(1 << (bits - 1))
        maximum = (1 << (bits - 1)) - 1
        encoded: list[tuple[int, bytes]] = []
        for index, value in ordered:
            if not minimum <= value <= maximum:
                raise ValueError(f"SLEB sentinel {value} is outside i{bits}")
            encoded.append((index, wire.encode_sleb128(value)))
        payload_size = elements + sum(len(value) - 1 for _, value in encoded)
        self.write(LEB128_MAGIC)
        self.uint32(payload_size)
        cursor = 0
        for index, value in encoded:
            self.zeros(index - cursor)
            self.write(value)
            cursor = index + 1
        self.zeros(elements - cursor)

    def sparse_i8(self, elements: int, sentinels: Mapping[int, int]) -> None:
        ordered = sorted(sentinels.items())
        if ordered and (ordered[0][0] < 0 or ordered[-1][0] >= elements):
            raise ValueError("i8 sentinel lies outside its tensor")
        cursor = 0
        for index, value in ordered:
            if not -128 <= value <= 127:
                raise ValueError(f"raw sentinel {value} is outside i8")
            self.zeros(index - cursor)
            self.write(bytes((value & 0xFF,)))
            cursor = index + 1
        self.zeros(elements - cursor)

    def i32_vector(self, values: Sequence[int]) -> None:
        self.write(struct.pack(f"<{len(values)}i", *values))


def _assert_hash_contract() -> None:
    wire.assert_hash_contract()
    actual = (
        FILE_VERSION,
        NETWORK_HASH,
        FEATURE_TRANSFORMER_HASH,
        ARCHITECTURE_HASH,
    )
    expected = (
        wire.FILE_VERSION,
        wire.EXPECTED_NETWORK_HASH,
        wire.EXPECTED_FEATURE_TRANSFORMER_HASH,
        wire.ARCHITECTURE_HASH,
    )
    if actual != expected:
        raise AssertionError(f"synthetic AtomicNNUEV3 hash constants drifted: {actual!r}")


def create_network(path: Path, *, verify_hash: bool = True) -> tuple[int, str]:
    _assert_hash_contract()
    if verify_hash and (EXPECTED_SIZE is None or not EXPECTED_SHA256):
        raise RuntimeError(
            "synthetic AtomicNNUEV3 fixture identity is not frozen; use --measure"
        )
    path = path.expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    created = False
    try:
        with path.open("xb") as output:
            created = True
            writer = HashedWriter(output)
            writer.uint32(FILE_VERSION)
            writer.uint32(NETWORK_HASH)
            writer.uint32(len(DESCRIPTION))
            writer.write(DESCRIPTION)
            writer.uint32(FEATURE_TRANSFORMER_HASH)

            writer.sparse_sleb(
                ACCUMULATOR_DIMENSIONS, BIAS_SENTINELS, bits=16
            )
            writer.sparse_sleb(
                HM_DIMENSIONS * ACCUMULATOR_DIMENSIONS, HM_SENTINELS, bits=16
            )
            writer.sparse_i8(
                CAPTURE_PAIR_DIMENSIONS * ACCUMULATOR_DIMENSIONS,
                CAPTURE_PAIR_SENTINELS,
            )
            writer.sparse_sleb(
                KING_BLAST_EP_DIMENSIONS * ACCUMULATOR_DIMENSIONS,
                KING_BLAST_EP_SENTINELS,
                bits=16,
            )
            writer.sparse_i8(
                BLAST_RING_DIMENSIONS * ACCUMULATOR_DIMENSIONS,
                BLAST_RING_SENTINELS,
            )
            writer.sparse_sleb(
                HM_DIMENSIONS * PSQT_BUCKETS, PSQT_SENTINELS, bits=32
            )

            for bucket in range(LAYER_STACKS):
                (
                    fc0_biases,
                    fc0_weights,
                    fc1_biases,
                    fc1_weights,
                    fc2_bias,
                    fc2_weights,
                ) = dense_parameters(bucket)
                writer.uint32(ARCHITECTURE_HASH)
                writer.i32_vector(fc0_biases)
                writer.sparse_i8(FC0_OUTPUTS * FC0_INPUTS, fc0_weights)
                writer.i32_vector(fc1_biases)
                writer.sparse_i8(FC1_OUTPUTS * FC1_INPUTS, fc1_weights)
                writer.int32(fc2_bias)
                writer.sparse_i8(FC2_INPUTS, fc2_weights)

            size = writer.size
            digest = writer.digest.hexdigest().upper()

        if verify_hash and EXPECTED_SIZE is not None and size != EXPECTED_SIZE:
            raise AssertionError(
                f"synthetic AtomicNNUEV3 size mismatch: {size} != {EXPECTED_SIZE}"
            )
        if verify_hash and EXPECTED_SHA256 and digest != EXPECTED_SHA256:
            raise AssertionError(
                "synthetic AtomicNNUEV3 hash mismatch: "
                f"{digest} != {EXPECTED_SHA256}"
            )
        return size, digest
    except BaseException:
        if created:
            path.unlink(missing_ok=True)
        raise


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--measure",
        action="store_true",
        help="print size/SHA without enforcing the frozen fixture identity",
    )
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    size, digest = create_network(args.output, verify_hash=not args.measure)
    print(
        "Synthetic AtomicNNUEV3 network created "
        f"size={size} sha256={digest} stacks={LAYER_STACKS}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
