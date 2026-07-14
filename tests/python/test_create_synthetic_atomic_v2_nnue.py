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

        assert struct.unpack("<I", stream.read(4))[0] == module.ARCHITECTURE_HASH
        biases = struct.unpack(f"<{module.FC0_OUTPUTS}i", stream.read(module.FC0_OUTPUTS * 4))
        assert biases[:-2] == (0,) * (module.FC0_OUTPUTS - 2)
        assert biases[-2:] == (module.FC0_SKIP_BIAS, 0)


def test_controlled_score_proves_v2_scaling_contract(generated) -> None:
    module, _output, _size, _digest = generated
    multiplier = 600 * 16
    denominator = 128 * (1 << 6) * 2
    assert module.FC0_SKIP_BIAS * multiplier // denominator == module.EXPECTED_RAW_POSITIONAL
    assert module.EXPECTED_RAW_POSITIONAL // 16 == module.EXPECTED_ENGINE_VALUE
