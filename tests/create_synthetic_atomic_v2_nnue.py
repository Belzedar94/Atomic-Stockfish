#!/usr/bin/env python3
"""Create the deterministic controlled-output AtomicNNUEV2 test network.

Feature-transformer dimensions 0 and 1 and seven PSQT buckets carry
deterministic per-feature diagnostics. Ten independent pairwise dimensions
activate sparse groups on both halves of the 1024-byte transformed input,
including the SSE, AVX2, AVX-512/WASM, bitset-segment and final-input
boundaries. Every layer stack has a distinct final bias while retaining the
known bucket-seven score. The fixture therefore detects stack permutation or
fixed-bucket bugs as well as sparse offset errors.

Each stack also carries an independently modelled non-zero path through fc0,
both fc0 activations, fc1, both fc1 activations and fc2. The skip outputs remain
``fc0[30]=16384`` and ``fc0[31]=0``. This catches incremental feature errors as
well as accidental use of the Legacy Atomic V1 material proxy or entertainment
blend in the V2 path.
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
FC0_TARGETS = (8216, 4144)
FC2_BASE_BIAS = 1024
FC2_STACK_STEP = 128

# (output index, first accumulator bias, paired accumulator bias). All values
# fit in one canonical signed-LEB byte, keeping the fixture's transformer block
# directly patchable by the transactional reload tests. The products cover
# non-zero transformed values 1..7 and every important sparse-input boundary.
FT_PAIR_BIASES = (
    (2, 32, 32),
    (15, 16, 32),
    (16, 32, 48),
    (31, 48, 48),
    (32, 48, 56),
    (63, 56, 56),
    (64, 60, 60),
    (255, 20, 32),
    (256, 32, 40),
    (511, 32, 56),
)
FT_PAIR_INDICES = tuple(index for index, _first, _second in FT_PAIR_BIASES)
EXPECTED_NNZ_GROUPS = tuple(
    sorted(
        {
            input_index // 4
            for index in FT_PAIR_INDICES
            for input_index in (index, FC0_INPUTS // 2 + index)
        }
    )
)

# Keep the original four connections stable so the V2A->V2B reload fixtures
# can patch one well-known transformer pair and all its consumers. Additional
# connections force every activated sparse group to affect propagation.
FC0_CONNECTIONS = (
    (0, 2, 5),
    (0, FC0_INPUTS // 2 + 2, 7),
    (1, 2, 11),
    (1, FC0_INPUTS // 2 + 2, 13),
) + tuple(
    connection
    for ordinal, index in enumerate(FT_PAIR_INDICES[1:], start=1)
    for connection in (
        (0, index, 1 + ordinal % 5),
        (0, FC0_INPUTS // 2 + index, 6 + ordinal % 5),
        (1, index, 2 + ordinal % 4),
        (1, FC0_INPUTS // 2 + index, 7 + ordinal % 4),
    )
)

EXPECTED_RAW_POSITIONAL = 11112
EXPECTED_ENGINE_VALUE = 694
EXPECTED_RAW_POSITIONAL_BY_BUCKET = tuple(
    EXPECTED_RAW_POSITIONAL + (bucket - (LAYER_STACKS - 1)) * 75
    for bucket in range(LAYER_STACKS)
)
EXPECTED_ENGINE_VALUE_BY_BUCKET = tuple(
    raw // 16 for raw in EXPECTED_RAW_POSITIONAL_BY_BUCKET
)

# Filled from the canonical writer below.  Keeping this fixed makes an
# accidental wire-order or quantization change visible before engine loading.
# Updated together with the deterministic feature-index diagnostics and dense
# path below. The public output is independently modelled by
# ``reference_dense_trace``.
EXPECTED_SHA256 = "4DEB05CFF79B5D5EBA51C560F64ED24224671C188B6C5DB27521033E587C87C6"
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

    def diagnostic_feature_biases(self) -> None:
        values = bytearray(ACCUMULATOR_DIMENSIONS)
        for index, first, second in FT_PAIR_BIASES:
            values[index] = first
            values[ACCUMULATOR_DIMENSIONS // 2 + index] = second
        self.write(LEB128_MAGIC)
        self.uint32(len(values))
        self.write(values)

    def diagnostic_feature_weights(self) -> None:
        """Write one-byte canonical SLEB weights that identify every feature.

        Only dimensions 0 and 1 carry per-feature values. The dimensions in
        ``FT_PAIR_BIASES`` retain fixed transformer biases and feed the
        controlled dense path. Incremental-vs-refresh tests can therefore
        detect a missing, extra, or stale HalfKAv2 feature instead of comparing
        two all-zero accumulators.
        """

        count = FEATURE_DIMENSIONS * ACCUMULATOR_DIMENSIONS
        self.write(LEB128_MAGIC)
        self.uint32(count)
        block = bytearray(ACCUMULATOR_DIMENSIONS)
        for feature in range(FEATURE_DIMENSIONS):
            positive = feature % 63 + 1
            negative = -(feature * 17 % 64 + 1)
            block[0] = positive
            block[1] = negative & 0x7F
            self.write(block)

    def diagnostic_psqt_weights(self) -> None:
        """Write non-zero PSQT diagnostics without changing startpos output.

        Bucket seven is zero because the 32-piece start position selects it.
        The other seven buckets use canonical one-byte signed LEB values with
        period 127. Combined with the feature-transformer diagnostics' period
        4032, every one of the 45,056 feature rows has a distinct signature.
        """

        count = FEATURE_DIMENSIONS * PSQT_BUCKETS
        self.write(LEB128_MAGIC)
        self.uint32(count)
        block = bytearray(PSQT_BUCKETS)
        for feature in range(FEATURE_DIMENSIONS):
            for bucket in range(PSQT_BUCKETS - 1):
                value = (feature * (2 * bucket + 1) + 17 * bucket) % 127 - 63
                block[bucket] = value & 0x7F
            block[PSQT_BUCKETS - 1] = 0
            self.write(block)


def transformed_inputs() -> dict[int, int]:
    values: dict[int, int] = {}
    for index, first, second in FT_PAIR_BIASES:
        transformed = first * second // 512
        if transformed <= 0:
            raise AssertionError(f"pairwise diagnostic {index} must be non-zero")
        values[index] = transformed
        values[FC0_INPUTS // 2 + index] = transformed
    return values


def dense_parameters(
    bucket: int = LAYER_STACKS - 1,
) -> tuple[list[int], bytearray, list[int], bytearray, int, bytearray]:
    if not 0 <= bucket < LAYER_STACKS:
        raise ValueError(f"layer-stack bucket out of range: {bucket}")

    inputs = transformed_inputs()
    fc0_biases = [0] * FC0_OUTPUTS
    for output, target in enumerate(FC0_TARGETS):
        contribution = sum(
            value * inputs[input_index]
            for candidate, input_index, value in FC0_CONNECTIONS
            if candidate == output
        )
        fc0_biases[output] = target - contribution
    fc0_biases[FC0_OUTPUTS - 2] = FC0_SKIP_BIAS
    fc0_weights = bytearray(FC0_OUTPUTS * FC0_INPUTS)
    for output, input_index, value in FC0_CONNECTIONS:
        fc0_weights[output * FC0_INPUTS + input_index] = value

    fc1_biases = [0] * FC1_OUTPUTS
    fc1_biases[0] = 4096
    fc1_biases[1] = 2048
    fc1_weights = bytearray(FC1_OUTPUTS * FC1_INPUTS)
    for output, input_index, value in (
        (0, 0, 2),
        (0, 1, 3),
        (0, FC0_OUTPUTS, 4),
        (0, FC0_OUTPUTS + 1, 5),
        (1, 0, 6),
        (1, 1, 7),
        (1, FC0_OUTPUTS, 8),
        (1, FC0_OUTPUTS + 1, 9),
    ):
        fc1_weights[output * FC1_INPUTS + input_index] = value

    fc2_bias = FC2_BASE_BIAS + FC2_STACK_STEP * (bucket - (LAYER_STACKS - 1))
    fc2_weights = bytearray(FC2_INPUTS)
    for input_index, value in (
        (0, 1),
        (1, 2),
        (FC0_OUTPUTS, 3),
        (FC0_OUTPUTS + 1, 4),
        (FC0_OUTPUTS * 2, 5),
        (FC0_OUTPUTS * 2 + 1, 6),
        (FC0_OUTPUTS * 2 + FC1_OUTPUTS, 7),
        (FC0_OUTPUTS * 2 + FC1_OUTPUTS + 1, 8),
    ):
        fc2_weights[input_index] = value
    return fc0_biases, fc0_weights, fc1_biases, fc1_weights, fc2_bias, fc2_weights


def reference_dense_trace(
    bucket: int = LAYER_STACKS - 1,
) -> dict[str, tuple[int, ...] | int]:
    inputs = transformed_inputs()
    transformed = tuple(inputs[index] for index in FT_PAIR_INDICES)
    fc0_biases, _fc0_weights, _fc1_biases, _fc1_weights, fc2_bias, _fc2_weights = (
        dense_parameters(bucket)
    )
    fc0 = tuple(
        fc0_biases[output]
        + sum(
            value * inputs[input_index]
            for candidate, input_index, value in FC0_CONNECTIONS
            if candidate == output
        )
        for output in range(2)
    )
    sqr0 = tuple(value * value >> 21 for value in fc0)
    crelu0 = tuple(value >> 7 for value in fc0)
    fc1 = (
        4096 + 2 * sqr0[0] + 3 * sqr0[1] + 4 * crelu0[0] + 5 * crelu0[1],
        2048 + 6 * sqr0[0] + 7 * sqr0[1] + 8 * crelu0[0] + 9 * crelu0[1],
    )
    sqr1 = tuple(value * value >> 19 for value in fc1)
    crelu1 = tuple(value >> 6 for value in fc1)
    fc2 = (
        fc2_bias
        + sqr0[0]
        + 2 * sqr0[1]
        + 3 * crelu0[0]
        + 4 * crelu0[1]
        + 5 * sqr1[0]
        + 6 * sqr1[1]
        + 7 * crelu1[0]
        + 8 * crelu1[1]
    )
    fwd = fc2 + FC0_SKIP_BIAS
    raw = fwd * (600 * 16) // (128 * 64 * 2)
    return {
        "transformed": transformed,
        "fc0": fc0,
        "sqr0": sqr0,
        "crelu0": crelu0,
        "fc1": fc1,
        "sqr1": sqr1,
        "crelu1": crelu1,
        "fc2": fc2,
        "fwd": fwd,
        "raw": raw,
    }


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
        writer.diagnostic_feature_biases()
        writer.diagnostic_feature_weights()
        writer.diagnostic_psqt_weights()

        for bucket in range(LAYER_STACKS):
            fc0_biases, fc0_weights, fc1_biases, fc1_weights, fc2_bias, fc2_weights = (
                dense_parameters(bucket)
            )
            writer.uint32(ARCHITECTURE_HASH)

            for value in fc0_biases:
                writer.int32(value)
            writer.write(fc0_weights)

            for value in fc1_biases:
                writer.int32(value)
            writer.write(fc1_weights)

            writer.int32(fc2_bias)
            writer.write(fc2_weights)

        size = writer.size
        digest = writer.digest.hexdigest().upper()

    if verify_hash and EXPECTED_SHA256 and digest != EXPECTED_SHA256:
        path.unlink(missing_ok=True)
        raise AssertionError(
            f"synthetic AtomicNNUEV2 hash mismatch: expected {EXPECTED_SHA256}, got {digest}"
        )
    for bucket, expected in enumerate(EXPECTED_RAW_POSITIONAL_BY_BUCKET):
        trace = reference_dense_trace(bucket)
        if trace["raw"] != expected:
            raise AssertionError(
                f"dense reference mismatch for bucket {bucket}: expected {expected}, "
                f"got {trace['raw']}"
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
