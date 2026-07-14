#!/usr/bin/env python3
"""Create the deterministic controlled-output AtomicNNUEV2 test network.

The feature transformer and dense weights are zero.  Each layer stack sets
``fc0[30]`` to 16384 and ``fc0[31]`` to zero, so the SFNNv15 skip connection
produces a raw positional score of 9600 and therefore an undamped engine score
of exactly 600 after division by ``OutputScale``.  This catches accidental use
of the Legacy Atomic V1 material proxy or entertainment blend in the V2 path.
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
DESCRIPTION = b"Atomic-Stockfish AtomicNNUEV2 controlled synthetic CI source"

FEATURE_DIMENSIONS = 45056
ACCUMULATOR_DIMENSIONS = 1024
PSQT_BUCKETS = 8
LAYER_STACKS = 8
FC0_INPUTS = 1024
FC0_OUTPUTS = 32
FC1_INPUTS = 64
FC1_OUTPUTS = 32
FC2_INPUTS = 128

LEB128_MAGIC = b"COMPRESSED_LEB128"
FC0_SKIP_BIAS = 16384
EXPECTED_RAW_POSITIONAL = 9600
EXPECTED_ENGINE_VALUE = 600

# Filled from the canonical writer below.  Keeping this fixed makes an
# accidental wire-order or quantization change visible before engine loading.
EXPECTED_SHA256 = "A910EFADDC3450FC7D690C9BB1FC3EF70DDE02F96AD320304668F2B03D868053"
ZERO_CHUNK = bytes(1024 * 1024)


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
        writer.zero_sleb128_array(FEATURE_DIMENSIONS * PSQT_BUCKETS)

        for _ in range(LAYER_STACKS):
            writer.uint32(ARCHITECTURE_HASH)

            for index in range(FC0_OUTPUTS):
                writer.int32(FC0_SKIP_BIAS if index == FC0_OUTPUTS - 2 else 0)
            writer.zeros(FC0_OUTPUTS * FC0_INPUTS)  # int8 fc0 weights

            writer.zeros(FC1_OUTPUTS * 4)  # int32 fc1 biases
            writer.zeros(FC1_OUTPUTS * FC1_INPUTS)  # int8 fc1 weights

            writer.int32(0)  # fc2 bias
            writer.zeros(FC2_INPUTS)  # int8 fc2 weights

        size = writer.size
        digest = writer.digest.hexdigest().upper()

    if verify_hash and EXPECTED_SHA256 and digest != EXPECTED_SHA256:
        path.unlink(missing_ok=True)
        raise AssertionError(
            f"synthetic AtomicNNUEV2 hash mismatch: expected {EXPECTED_SHA256}, got {digest}"
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
    print(
        "Synthetic AtomicNNUEV2 network created "
        f"size={size} sha256={digest} raw={EXPECTED_RAW_POSITIONAL} "
        f"value={EXPECTED_ENGINE_VALUE}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
