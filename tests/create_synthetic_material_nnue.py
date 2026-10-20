#!/usr/bin/env python3
"""Create the deterministic material-only AtomicNNUEV2 test network.

Atomic-Stockfish evaluates with NNUE only. The classical evaluator it used to
carry was a plain material count, and network-free tests used it as a
deterministic search oracle. This network reproduces that count exactly, so
those tests keep their expectations.

The feature transformer and every dense layer are zero, so the positional
output is zero. Each PSQT weight is the value of the piece its feature encodes,
scaled by the network output scale: positive for the perspective's own pawns,
knights, bishops, rooks and queens, negative for the opponent's, and zero for
kings. Every bucket carries the same weights. The AtomicNNUEV2 path halves the
difference of the two perspectives and divides by the output scale, so with
``Use NNUE=true`` it returns the side-to-move material balance with the
fifty-move damping applied: the removed classical evaluation, value for value.
The one difference is the Chess960 cornered-bishop correction, which the NNUE
path still adds to Atomic960 positions.
"""

from __future__ import annotations

import argparse
import hashlib
from pathlib import Path
import struct
from typing import BinaryIO, Sequence


VERSION = 0xA70C0002
NETWORK_HASH = 0x3C1035AE
FEATURE_TRANSFORMER_HASH = 0x5F2344B8
ARCHITECTURE_HASH = 0x63337116
DESCRIPTION = b"Atomic-Stockfish AtomicNNUEV2 material-only synthetic CI source"

FEATURE_DIMENSIONS = 45056
ACCUMULATOR_DIMENSIONS = 1024
PSQT_BUCKETS = 8
LAYER_STACKS = 8
FC0_INPUTS = 1024
FC0_OUTPUTS = 32
FC1_INPUTS = 64
FC1_OUTPUTS = 32
FC2_INPUTS = 128

OUTPUT_SCALE = 16
SQUARES = 64
PIECE_SQUARE_DIMENSIONS = 11 * SQUARES

# src/types.h piece values.
PAWN, KNIGHT, BISHOP, ROOK, QUEEN = 208, 781, 825, 1276, 2538

# HalfKAv2Atomic planes, in feature order: own and opponent pawn, knight,
# bishop, rook and queen, then the shared king plane.
PLANE_VALUES = (PAWN, -PAWN, KNIGHT, -KNIGHT, BISHOP, -BISHOP, ROOK, -ROOK, QUEEN, -QUEEN, 0)

LEB128_MAGIC = b"COMPRESSED_LEB128"
EXPECTED_SHA256 = "1639E5D50FAE9927739B4AF00FB335FC5FD46D10E07BE0AB5A33A5797EAB2159"
ZERO_CHUNK = bytes(1024 * 1024)


def signed_leb128(value: int) -> bytes:
    """Encode one value as canonical (shortest) signed LEB128."""
    encoded = bytearray()
    while True:
        byte = value & 0x7F
        value >>= 7
        if (value == 0 and not byte & 0x40) or (value == -1 and byte & 0x40):
            encoded.append(byte)
            return bytes(encoded)
        encoded.append(byte | 0x80)


class HashedWriter:
    def __init__(self, output: BinaryIO):
        self.output = output
        self.digest = hashlib.sha256()
        self.size = 0

    def write(self, data: bytes) -> None:
        self.output.write(data)
        self.digest.update(data)
        self.size += len(data)

    def uint32(self, value: int) -> None:
        self.write(struct.pack("<I", value))

    def int32(self, value: int) -> None:
        self.write(struct.pack("<i", value))

    def zeros(self, size: int) -> None:
        while size:
            chunk_size = min(size, len(ZERO_CHUNK))
            self.write(ZERO_CHUNK[:chunk_size])
            size -= chunk_size

    def zero_sleb128_array(self, count: int) -> None:
        # Canonical signed LEB128 encodes zero as one 0x00 byte.
        self.write(LEB128_MAGIC)
        self.uint32(count)
        self.zeros(count)

    def material_psqt_weights(self) -> None:
        plane_blocks = [signed_leb128(value * OUTPUT_SCALE) * PSQT_BUCKETS for value in PLANE_VALUES]
        payload = b"".join(
            plane_blocks[feature % PIECE_SQUARE_DIMENSIONS // SQUARES]
            for feature in range(FEATURE_DIMENSIONS)
        )
        self.write(LEB128_MAGIC)
        self.uint32(len(payload))
        self.write(payload)


def create_network(path: Path, *, verify_hash: bool = True) -> tuple[int, str]:
    path = path.expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)

    with path.open("xb") as output:
        writer = HashedWriter(output)
        writer.uint32(VERSION)
        writer.uint32(NETWORK_HASH)
        writer.uint32(len(DESCRIPTION))
        writer.write(DESCRIPTION)

        writer.uint32(FEATURE_TRANSFORMER_HASH)
        writer.zero_sleb128_array(ACCUMULATOR_DIMENSIONS)
        writer.zero_sleb128_array(FEATURE_DIMENSIONS * ACCUMULATOR_DIMENSIONS)
        writer.material_psqt_weights()

        for _bucket in range(LAYER_STACKS):
            writer.uint32(ARCHITECTURE_HASH)
            for _ in range(FC0_OUTPUTS):
                writer.int32(0)
            writer.zeros(FC0_OUTPUTS * FC0_INPUTS)
            for _ in range(FC1_OUTPUTS):
                writer.int32(0)
            writer.zeros(FC1_OUTPUTS * FC1_INPUTS)
            writer.int32(0)
            writer.zeros(FC2_INPUTS)

        size = writer.size
        digest = writer.digest.hexdigest().upper()

    if verify_hash and digest != EXPECTED_SHA256:
        path.unlink(missing_ok=True)
        raise AssertionError(
            f"synthetic material AtomicNNUEV2 hash mismatch: expected {EXPECTED_SHA256}, "
            f"got {digest}"
        )
    return size, digest


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--measure",
        action="store_true",
        help="print the canonical hash without comparing EXPECTED_SHA256",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    size, digest = create_network(args.output, verify_hash=not args.measure)
    print(f"Synthetic material AtomicNNUEV2 network created size={size} sha256={digest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
