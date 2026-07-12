#!/usr/bin/env python3
"""Create the frozen zero-weight Legacy Atomic V1 network without PyTorch."""

from __future__ import annotations

import argparse
import hashlib
from pathlib import Path
import struct
from typing import BinaryIO, Sequence


VERSION = 0x7AF32F20
NETWORK_HASH = 0x3C103E72
DESCRIPTION = b"Atomic-Stockfish LegacyAtomicV1 synthetic CI source"
NUM_FEATURES = 45056
L1 = 512
L2 = 16
L3 = 32
PSQT_BUCKETS = 8
LAYER_STACKS = 8
EXPECTED_SHA256 = "9CF054CA00B82AB53A34473DE52D1104AEDDAA19B2E7B24091B5E613AF485985"
ZERO_CHUNK = bytes(1024 * 1024)


def fc_hash() -> int:
    previous = 0xEC42E90D ^ (L1 * 2)
    for output_size in (L2, L3, 1):
        layer_hash = (0xCC03DAE4 + output_size) & 0xFFFFFFFF
        layer_hash ^= previous >> 1
        layer_hash ^= (previous << 31) & 0xFFFFFFFF
        if output_size != 1:
            layer_hash = (layer_hash + 0x538D24C7) & 0xFFFFFFFF
        previous = layer_hash
    return previous


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

    def zeros(self, size: int) -> None:
        while size:
            chunk_size = min(size, len(ZERO_CHUNK))
            self.write(ZERO_CHUNK[:chunk_size])
            size -= chunk_size


def create_network(path: Path) -> tuple[int, str]:
    path = path.expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as output:
        writer = HashedWriter(output)
        layers_hash = fc_hash()
        writer.uint32(VERSION)
        writer.uint32(NETWORK_HASH)
        writer.uint32(len(DESCRIPTION))
        writer.write(DESCRIPTION)

        # feature_hash ^ (L1 * 2) == network_hash ^ fc_hash
        writer.uint32(NETWORK_HASH ^ layers_hash)
        writer.zeros(L1 * 2)  # int16 feature-transformer biases
        writer.zeros(NUM_FEATURES * L1 * 2)  # int16 feature weights
        writer.zeros(NUM_FEATURES * PSQT_BUCKETS * 4)  # int32 PSQT weights

        for _ in range(LAYER_STACKS):
            writer.uint32(layers_hash)
            writer.zeros(L2 * 4)  # int32 L1 biases
            writer.zeros(L2 * (2 * L1))  # int8 L1 weights
            writer.zeros(L3 * 4)  # int32 L2 biases
            writer.zeros(L3 * 32)  # int8 L2 weights, input padded to 32
            writer.zeros(4)  # int32 output bias
            writer.zeros(32)  # int8 output weights

        size = writer.size
        digest = writer.digest.hexdigest().upper()

    if digest != EXPECTED_SHA256:
        path.unlink(missing_ok=True)
        raise AssertionError(
            f"synthetic network hash mismatch: expected {EXPECTED_SHA256}, got {digest}"
        )
    return size, digest


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    size, digest = create_network(args.output)
    print(f"Synthetic Legacy Atomic V1 network created size={size} sha256={digest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
