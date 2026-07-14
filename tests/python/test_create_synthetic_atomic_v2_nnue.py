from __future__ import annotations

import importlib.util
from pathlib import Path
import struct

import pytest


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "tests" / "create_synthetic_atomic_v2_nnue.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("create_atomic_v2", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def generated(tmp_path_factory: pytest.TempPathFactory):
    module = _load_module()
    output = tmp_path_factory.mktemp("atomic-v2-net") / "controlled.nnue"
    size, digest = module.create_network(output)
    return module, output, size, digest


def test_controlled_v2_network_is_canonical_and_exclusive(generated) -> None:
    module, output, size, digest = generated
    assert digest == module.EXPECTED_SHA256
    assert size == output.stat().st_size == 46_780_619

    with pytest.raises(FileExistsError):
        module.create_network(output)


def test_controlled_v2_network_has_exact_wire_order(generated) -> None:
    module, output, _size, _digest = generated

    with output.open("rb") as stream:
        version, network_hash, description_size = struct.unpack("<III", stream.read(12))
        assert version == module.VERSION
        assert network_hash == module.NETWORK_HASH
        assert stream.read(description_size) == module.DESCRIPTION
        assert struct.unpack("<I", stream.read(4))[0] == module.FEATURE_TRANSFORMER_HASH

        for count in (
            module.ACCUMULATOR_DIMENSIONS,
            module.FEATURE_DIMENSIONS * module.ACCUMULATOR_DIMENSIONS,
            module.FEATURE_DIMENSIONS * module.PSQT_BUCKETS,
        ):
            assert stream.read(len(module.LEB128_MAGIC)) == module.LEB128_MAGIC
            assert struct.unpack("<I", stream.read(4))[0] == count
            stream.seek(count, 1)

        for bucket in range(module.LAYER_STACKS):
            expected = module.dense_parameters(bucket)
            assert struct.unpack("<I", stream.read(4))[0] == module.ARCHITECTURE_HASH
            fc0_biases = struct.unpack(
                f"<{module.FC0_OUTPUTS}i", stream.read(module.FC0_OUTPUTS * 4)
            )
            fc0_weights = stream.read(module.FC0_OUTPUTS * module.FC0_INPUTS)
            fc1_biases = struct.unpack(
                f"<{module.FC1_OUTPUTS}i", stream.read(module.FC1_OUTPUTS * 4)
            )
            fc1_weights = stream.read(module.FC1_OUTPUTS * module.FC1_INPUTS)
            fc2_bias = struct.unpack("<i", stream.read(4))[0]
            fc2_weights = stream.read(module.FC2_INPUTS)
            assert (
                list(fc0_biases),
                fc0_weights,
                list(fc1_biases),
                fc1_weights,
                fc2_bias,
                fc2_weights,
            ) == (
                expected[0],
                bytes(expected[1]),
                expected[2],
                bytes(expected[3]),
                expected[4],
                bytes(expected[5]),
            )
        assert stream.read(1) == b""


def test_controlled_score_proves_v2_scaling_contract(generated) -> None:
    module, _output, _size, _digest = generated
    traces = tuple(
        module.reference_dense_trace(bucket) for bucket in range(module.LAYER_STACKS)
    )
    assert traces[-1] == {
        "transformed": (2, 1, 3, 4, 5, 6, 7, 1, 2, 3),
        "fc0": (8216, 4144),
        "sqr0": (32, 8),
        "crelu0": (64, 32),
        "fc1": (4600, 3096),
        "sqr1": (40, 18),
        "crelu1": (71, 48),
        "fc2": 2581,
        "fwd": 18965,
        "raw": module.EXPECTED_RAW_POSITIONAL,
    }
    assert tuple(trace["raw"] for trace in traces) == (
        module.EXPECTED_RAW_POSITIONAL_BY_BUCKET
    )
    assert tuple(trace["raw"] // 16 for trace in traces) == (
        module.EXPECTED_ENGINE_VALUE_BY_BUCKET
    )
    assert module.EXPECTED_RAW_POSITIONAL // 16 == module.EXPECTED_ENGINE_VALUE


def test_fixture_has_nonzero_feature_identity_diagnostics(generated) -> None:
    module, output, _size, _digest = generated
    with output.open("rb") as stream:
        _version, _network_hash, description_size = struct.unpack(
            "<III", stream.read(12)
        )
        stream.seek(description_size + 4, 1)  # description + transformer hash

        assert stream.read(len(module.LEB128_MAGIC)) == module.LEB128_MAGIC
        bias_bytes = struct.unpack("<I", stream.read(4))[0]
        assert bias_bytes == module.ACCUMULATOR_DIMENSIONS
        feature_biases = stream.read(bias_bytes)

        assert stream.read(len(module.LEB128_MAGIC)) == module.LEB128_MAGIC
        feature_weight_bytes = module.FEATURE_DIMENSIONS * module.ACCUMULATOR_DIMENSIONS
        assert struct.unpack("<I", stream.read(4))[0] == feature_weight_bytes
        first = stream.read(module.ACCUMULATOR_DIMENSIONS)
        second = stream.read(module.ACCUMULATOR_DIMENSIONS)
        stream.seek(feature_weight_bytes - len(first) - len(second), 1)

        assert stream.read(len(module.LEB128_MAGIC)) == module.LEB128_MAGIC
        assert struct.unpack("<I", stream.read(4))[0] == (
            module.FEATURE_DIMENSIONS * module.PSQT_BUCKETS
        )
        psqt_first = stream.read(module.PSQT_BUCKETS)
        psqt_second = stream.read(module.PSQT_BUCKETS)

    expected_biases = bytearray(module.ACCUMULATOR_DIMENSIONS)
    for index, first_bias, second_bias in module.FT_PAIR_BIASES:
        expected_biases[index] = first_bias
        expected_biases[module.ACCUMULATOR_DIMENSIONS // 2 + index] = second_bias
    assert feature_biases == bytes(expected_biases)

    assert first[:2] == bytes((1, (-1) & 0x7F))
    assert second[:2] == bytes((2, (-18) & 0x7F))
    assert not any(first[2:])
    assert not any(second[2:])

    def expected_psqt(feature: int) -> bytes:
        values = [
            (feature * (2 * bucket + 1) + 17 * bucket) % 127 - 63
            for bucket in range(module.PSQT_BUCKETS - 1)
        ]
        return bytes([(value & 0x7F) for value in values] + [0])

    assert psqt_first == expected_psqt(0)
    assert psqt_second == expected_psqt(1)
    assert any(psqt_first[:-1])
    assert psqt_first[-1] == psqt_second[-1] == 0


def test_sparse_diagnostics_cover_both_halves_and_simd_boundaries(generated) -> None:
    module, _output, _size, _digest = generated
    assert module.EXPECTED_NNZ_GROUPS == (
        0,
        3,
        4,
        7,
        8,
        15,
        16,
        63,
        64,
        127,
        128,
        131,
        132,
        135,
        136,
        143,
        144,
        191,
        192,
        255,
    )

    connected = {
        input_index
        for _output, input_index, _value in module.FC0_CONNECTIONS
    }
    assert connected == {
        input_index
        for index in module.FT_PAIR_INDICES
        for input_index in (index, module.FC0_INPUTS // 2 + index)
    }
