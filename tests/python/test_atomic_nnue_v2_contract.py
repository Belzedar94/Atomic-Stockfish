from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
CONTRACT_FILE = ROOT / "schemas" / "atomic-nnue-v2.json"


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise AssertionError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _load_contract() -> dict[str, Any]:
    payload = CONTRACT_FILE.read_bytes()
    assert b"\r" not in payload, "NNUE contract must use LF line endings"
    assert payload.endswith(b"\n") and not payload.endswith(b"\n\n")
    return json.loads(
        payload.decode("utf-8"), object_pairs_hook=_reject_duplicate_keys
    )


def _u32(value: object) -> int:
    assert isinstance(value, str)
    assert re.fullmatch(r"0x[0-9A-F]{8}", value)
    return int(value, 16)


def _affine_hash(previous: int, output_dimensions: int) -> int:
    value = (0xCC03DAE4 + output_dimensions) & 0xFFFFFFFF
    return (value ^ (previous >> 1) ^ ((previous << 31) & 0xFFFFFFFF)) & 0xFFFFFFFF


def _clipped_relu_hash(previous: int) -> int:
    return (0x538D24C7 + previous) & 0xFFFFFFFF


def _architecture_hash(network_input: int, outputs: tuple[int, int, int]) -> int:
    value = 0xEC42E90D ^ (network_input * 2)
    value = _affine_hash(value, outputs[0])
    value = _clipped_relu_hash(value)
    value = _affine_hash(value, outputs[1])
    value = _clipped_relu_hash(value)
    return _affine_hash(value, outputs[2])


def _compact(source: str) -> str:
    return re.sub(r"\s+", " ", source)


def test_contract_has_exact_dual_backend_identity() -> None:
    contract = _load_contract()
    assert set(contract) == {
        "schema_version",
        "schema_id",
        "variant",
        "byte_order",
        "backends",
    }
    assert contract["schema_version"] == 1
    assert contract["schema_id"] == "atomic-nnue-dual-backend-v1"
    assert contract["variant"] == "atomic"
    assert contract["byte_order"] == "little-endian"

    backends = contract["backends"]
    assert set(backends) == {"legacy-atomic-v1", "atomic-nnue-v2"}
    versions = {_u32(backend["file_version"]) for backend in backends.values()}
    assert versions == {0x7AF32F20, 0xA70C0002}
    assert 0x6A448AFA not in versions  # Official Stockfish SFNNv15 remains distinct.


def test_legacy_v1_contract_is_frozen_against_cpp() -> None:
    legacy = _load_contract()["backends"]["legacy-atomic-v1"]
    common = _compact((ROOT / "src/nnue/nnue_common.h").read_text(encoding="utf-8"))
    features = _compact(
        (ROOT / "src/nnue/features/half_ka_v2_atomic.h").read_text(encoding="utf-8")
    )
    transformer = _compact(
        (ROOT / "src/nnue/nnue_feature_transformer.h").read_text(encoding="utf-8")
    )
    architecture = _compact(
        (ROOT / "src/nnue/nnue_architecture.h").read_text(encoding="utf-8")
    )
    network = _compact((ROOT / "src/nnue/network.h").read_text(encoding="utf-8"))

    assert "constexpr u32 Version = 0x7AF32F20u;" in common
    assert "static constexpr u32 HashValue = 0x5F234CB8u;" in features
    assert "constexpr IndexType TransformedFeatureDimensions = 512;" in architecture
    assert "constexpr IndexType NetworkInputDimensions = TransformedFeatureDimensions * 2;" in architecture
    assert "constexpr IndexType PSQTBuckets = 8;" in architecture
    assert "constexpr IndexType LayerStacks = 8;" in architecture
    assert "static_assert(FeatureTransformer::InputDimensions == 45056);" in transformer
    assert "static_assert(FeatureTransformer::OutputDimensions == 1024);" in transformer
    assert "static_assert(FeatureTransformer::get_hash_value() == 0x5F2348B8u);" in transformer
    assert "static constexpr int FC_0_OUTPUTS = 16;" in architecture
    assert "static constexpr int FC_1_OUTPUTS = 32;" in architecture
    assert "Layers::AffineTransform<FC_1_OUTPUTS, 1> fc_2;" in architecture
    assert "static_assert(NetworkArchitecture::get_hash_value() == 0x633376CAu);" in architecture
    assert "static_assert(hash == 0x3C103E72u);" in network

    assert _u32(legacy["file_version"]) == 0x7AF32F20
    assert legacy["feature_dimensions"] == 45056
    assert legacy["accumulator_dimensions_per_perspective"] == 512
    assert legacy["feature_transformer_output_dimensions"] == 1024
    assert legacy["psqt_buckets"] == legacy["layer_stacks"] == 8
    assert _u32(legacy["feature_set_hash"]) == 0x5F234CB8
    assert _u32(legacy["feature_transformer_hash"]) == (
        _u32(legacy["feature_set_hash"]) ^ legacy["feature_transformer_hash_xor"]
    ) == 0x5F2348B8
    topology = legacy["topology"]
    assert topology["fc0"] == {"input_dimensions": 1024, "output_dimensions": 16}
    assert topology["fc1"] == {"input_dimensions": 16, "output_dimensions": 32}
    assert topology["fc2"] == {"input_dimensions": 32, "output_dimensions": 1}
    assert _u32(legacy["architecture_hash"]) == 0x633376CA
    assert _u32(legacy["network_hash"]) == (
        _u32(legacy["feature_transformer_hash"]) ^ _u32(legacy["architecture_hash"])
    ) == 0x3C103E72


def test_v2_hashes_are_derived_from_the_declared_dimensions() -> None:
    v2 = _load_contract()["backends"]["atomic-nnue-v2"]
    topology = v2["topology"]

    assert _u32(v2["file_version"]) == 0xA70C0002
    assert v2["feature_set"] == "HalfKAv2Atomic"
    assert v2["feature_dimensions"] == 45056
    assert v2["accumulator_dimensions_per_perspective"] == 1024
    assert v2["feature_transformer_output_dimensions"] == 1024
    assert v2["psqt_buckets"] == v2["layer_stacks"] == 8
    assert _u32(v2["feature_set_hash"]) == 0x5F234CB8
    assert v2["feature_transformer_hash_xor"] == 1024 * 2
    assert _u32(v2["feature_transformer_hash"]) == (
        _u32(v2["feature_set_hash"]) ^ v2["feature_transformer_hash_xor"]
    ) == 0x5F2344B8

    outputs = tuple(topology[f"fc{index}"]["output_dimensions"] for index in range(3))
    assert outputs == (32, 32, 1)
    assert _u32(v2["architecture_hash"]) == _architecture_hash(
        v2["feature_transformer_output_dimensions"], outputs
    ) == 0x63337116
    assert _u32(v2["network_hash"]) == (
        _u32(v2["feature_transformer_hash"]) ^ _u32(v2["architecture_hash"])
    ) == 0x3C1035AE


def test_v2_contract_freezes_the_physical_sfnnv15_topology() -> None:
    v2 = _load_contract()["backends"]["atomic-nnue-v2"]
    pairwise = v2["pairwise_multiply"]
    topology = v2["topology"]

    assert pairwise == {
        "input_dimensions_per_perspective": 1024,
        "half_dimensions": 512,
        "output_dimensions_per_perspective": 512,
        "concatenated_output_dimensions": 1024,
    }
    assert pairwise["half_dimensions"] * 2 == pairwise["input_dimensions_per_perspective"]
    assert pairwise["output_dimensions_per_perspective"] * 2 == topology["fc0"]["input_dimensions"]
    assert topology["fc0"] == {"input_dimensions": 1024, "output_dimensions": 32}
    assert topology["fc1"] == {"input_dimensions": 64, "output_dimensions": 32}
    assert topology["fc2"] == {"input_dimensions": 128, "output_dimensions": 1}
    assert topology["activation_paths"] == {
        "after_fc0": ["squared-clipped-relu", "clipped-relu"],
        "after_fc1": ["squared-clipped-relu", "clipped-relu"],
    }
    assert topology["fc0_skip_indices"] == [30, 31]
    assert topology["fc0_skip_coefficients"] == [1, -1]

    scaling = topology["output_scaling"]
    assert scaling == {
        "hidden_one": 128,
        "weight_scale_bits": 6,
        "output_scale": 16,
        "network_unit_scale": 600,
        "multiplier": 9600,
        "denominator": 16384,
    }
    assert scaling["multiplier"] == scaling["network_unit_scale"] * scaling["output_scale"]
    assert scaling["denominator"] == (
        scaling["hidden_one"] * (1 << scaling["weight_scale_bits"]) * 2
    )
