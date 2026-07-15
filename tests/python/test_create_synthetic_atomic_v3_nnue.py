from __future__ import annotations

import hashlib
import importlib.util
from pathlib import Path

import pytest

import atomic_v3_wire_reference as wire


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "tests" / "create_synthetic_atomic_v3_nnue.py"


def _load_generator():
    spec = importlib.util.spec_from_file_location("create_atomic_v3", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while True:
            chunk = stream.read(1 << 20)
            if not chunk:
                return digest.hexdigest().upper()
            digest.update(chunk)


@pytest.fixture(scope="module")
def generated(tmp_path_factory: pytest.TempPathFactory):
    generator = _load_generator()
    output = tmp_path_factory.mktemp("atomic-v3-net") / "controlled.nnue"
    size, digest = generator.create_network(output)
    parsed = wire.parse_network(
        output, selected_indices=generator.SELECTED_INDICES
    )
    return generator, output, size, digest, parsed


def test_normal_generation_requires_frozen_identity_before_creating_output(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    generator = _load_generator()
    monkeypatch.setattr(generator, "EXPECTED_SIZE", None)
    monkeypatch.setattr(generator, "EXPECTED_SHA256", "")
    output = tmp_path / "must-not-exist" / "fixture.nnue"
    with pytest.raises(RuntimeError, match="use --measure"):
        generator.create_network(output)
    assert not output.exists()
    assert not output.parent.exists()


def test_controlled_v3_network_has_frozen_identity_and_no_overwrite(generated) -> None:
    generator, output, size, digest, parsed = generated
    assert generator.EXPECTED_SIZE is not None, "measure and freeze V3 fixture size"
    assert generator.EXPECTED_SHA256, "measure and freeze V3 fixture SHA-256"
    assert size == output.stat().st_size == parsed.size == generator.EXPECTED_SIZE
    assert digest == parsed.sha256 == generator.EXPECTED_SHA256
    before = _sha256(output)
    with pytest.raises(FileExistsError):
        generator.create_network(output)
    assert output.stat().st_size == size
    assert _sha256(output) == before == digest
    assert wire.parse_network(output).sha256 == digest


def test_mixed_wire_selected_values_prove_order_signs_and_boundaries(generated) -> None:
    generator, _output, _size, _digest, parsed = generated
    assert parsed.description == generator.DESCRIPTION
    assert parsed.selected == generator.expected_selected_values()
    assert parsed.psqt_top32_sums == (wire.INT32_MAX,) * wire.PSQT_BUCKETS

    spans = parsed.spans
    assert spans["biases"].elements == wire.ACCUMULATOR_DIMENSIONS
    assert spans["hm"].elements == (
        wire.HM_DIMENSIONS * wire.ACCUMULATOR_DIMENSIONS
    )
    assert spans["capture_pair"].payload_size == (
        wire.CAPTURE_PAIR_DIMENSIONS * wire.ACCUMULATOR_DIMENSIONS
    )
    assert spans["king_blast_ep"].elements == (
        wire.KING_BLAST_EP_DIMENSIONS * wire.ACCUMULATOR_DIMENSIONS
    )
    assert spans["blast_ring"].payload_size == (
        wire.BLAST_RING_DIMENSIONS * wire.ACCUMULATOR_DIMENSIONS
    )
    assert spans["hm_psqt"].elements == wire.HM_DIMENSIONS * wire.PSQT_BUCKETS
    ordered = (
        spans["biases"].frame_offset,
        spans["hm"].frame_offset,
        spans["capture_pair"].frame_offset,
        spans["king_blast_ep"].frame_offset,
        spans["blast_ring"].frame_offset,
        spans["hm_psqt"].frame_offset,
        parsed.dense_stacks[0].architecture_offset,
    )
    assert ordered == tuple(sorted(ordered))


def test_all_dense_stacks_are_distinct_and_exactly_inside_numeric_gates(generated) -> None:
    _generator, _output, _size, _digest, parsed = generated
    assert len(parsed.dense_stacks) == wire.LAYER_STACKS
    assert tuple(stack.bucket for stack in parsed.dense_stacks) == tuple(
        range(wire.LAYER_STACKS)
    )
    assert tuple(stack.fc2.absolute for stack in parsed.dense_stacks) == tuple(
        wire.INT32_MAX - bucket for bucket in range(wire.LAYER_STACKS)
    )
    assert tuple(stack.fwd_lower for stack in parsed.dense_stacks) == tuple(
        wire.INT32_MAX - bucket for bucket in range(wire.LAYER_STACKS)
    )
    assert tuple(stack.fwd_upper for stack in parsed.dense_stacks) == tuple(
        wire.INT32_MAX - bucket for bucket in range(wire.LAYER_STACKS)
    )
    for stack in parsed.dense_stacks:
        assert stack.fc0[0].absolute == wire.INT32_MAX
        assert stack.fc1[0].absolute == wire.INT32_MAX
        assert wire.FWD_MIN <= stack.fwd_lower <= stack.fwd_upper <= wire.FWD_MAX


def test_corruption_overlays_reject_hash_sleb_psqt_dense_and_truncation(generated) -> None:
    generator, output, _size, _digest, parsed = generated

    with pytest.raises(wire.WireError, match="network hash mismatch"):
        wire.parse_network(output, patches={4: (wire.NETWORK_HASH & 0xFF) ^ 1})

    bias_payload = parsed.spans["biases"].payload_offset
    with pytest.raises(wire.WireError, match="non-canonical"):
        wire.parse_network(output, patches={bias_payload: 0x80, bias_payload + 1: 0x00})

    psqt_index = 31 * wire.PSQT_BUCKETS
    original = generator.PSQT_SENTINELS[psqt_index]
    replacement = -67_108_864
    assert original == -67_108_863
    encoded = wire.encode_sleb128(replacement)
    assert len(encoded) == len(wire.encode_sleb128(original))
    relative = generator.sleb_payload_element_offset(
        generator.PSQT_SENTINELS, psqt_index
    )
    psqt_offset = parsed.spans["hm_psqt"].payload_offset + relative
    psqt_patch = {psqt_offset + index: byte for index, byte in enumerate(encoded)}
    with pytest.raises(wire.WireError, match="top-32"):
        wire.parse_network(output, patches=psqt_patch)

    dense_weight = parsed.spans["dense.0.fc0_weights"].payload_offset
    with pytest.raises(wire.WireError, match="affine i32 envelope"):
        wire.parse_network(output, patches={dense_weight: 1})

    architecture = parsed.dense_stacks[0].architecture_offset
    with pytest.raises(wire.WireError, match="architecture hash mismatch"):
        wire.parse_network(output, patches={architecture: 0})

    with pytest.raises(wire.WireError, match="truncated"):
        wire.parse_network(output, limit=parsed.size - 1)
    with pytest.raises(ValueError, match="outside the visible input"):
        wire.parse_network(output, patches={parsed.size: 0})
    with pytest.raises(ValueError, match="outside the visible input"):
        wire.parse_network(
            output,
            patches={parsed.size - 1: 0},
            limit=parsed.size - 1,
        )
    with pytest.raises(ValueError, match="non-negative"):
        wire.parse_network(output, limit=-1)


def test_streaming_roundtrip_is_byte_exact_exclusive_and_strict_eof(
    generated, tmp_path: Path
) -> None:
    _generator, output, size, digest, source_parsed = generated
    roundtrip = tmp_path / "roundtrip.nnue"
    parsed = wire.roundtrip_network(output, roundtrip)
    assert parsed.size == roundtrip.stat().st_size == size
    assert parsed.sha256 == digest
    assert _sha256(roundtrip) == _sha256(output) == digest
    assert wire.parse_network(roundtrip).sha256 == digest

    preexisting = tmp_path / "preexisting.nnue"
    marker = b"preexisting target must survive FileExistsError byte-exactly"
    preexisting.write_bytes(marker)
    with pytest.raises(FileExistsError):
        wire.roundtrip_network(output, preexisting)
    assert preexisting.read_bytes() == marker

    with roundtrip.open("ab") as stream:
        stream.write(b"\x00")
    with pytest.raises(wire.WireError, match="trailing bytes"):
        wire.parse_network(roundtrip)
    dense_weight = source_parsed.spans["dense.0.fc0_weights"].payload_offset
    with pytest.raises(wire.WireError, match="trailing bytes"):
        wire.parse_network(roundtrip, patches={dense_weight: 1})
