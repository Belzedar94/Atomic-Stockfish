from __future__ import annotations

from io import BytesIO
from pathlib import Path
import struct

import pytest

import atomic_v3_wire_reference as wire


def _framed(values: tuple[int, ...]) -> bytes:
    payload = b"".join(wire.encode_sleb128(value) for value in values)
    return wire.LEB128_MAGIC + struct.pack("<I", len(payload)) + payload


def _decode_small(
    payload: bytes, *, count: int, bits: int, patches=None, limit=None
) -> dict[str, int]:
    reader = wire._Reader(BytesIO(payload), patches=patches, limit=limit)
    selected: dict[str, int] = {}
    wire._read_sleb_tensor(
        reader,
        "small",
        count,
        bits,
        {"small": tuple(range(count))},
        selected,
        {},
    )
    assert reader.read_some(1) == b""
    return selected


def test_independent_ascii_descriptors_derive_the_frozen_hash_contract() -> None:
    assert all(
        descriptor.isascii()
        for descriptor in (
            wire.HM_DESCRIPTOR,
            wire.CAPTURE_PAIR_DESCRIPTOR,
            wire.KING_BLAST_EP_DESCRIPTOR,
            wire.BLAST_RING_DESCRIPTOR,
            wire.TRANSFORMER_DESCRIPTOR,
        )
    )
    assert wire.SLICE_HASHES == (
        0xA34A8666,
        0x9AEDB186,
        0xF5172BC0,
        0x38377946,
    )
    assert wire.FEATURE_HASH == 0xA3FBDBE8
    assert len(wire.TRANSFORMER_DESCRIPTOR) == 799
    assert wire.TRANSFORMER_DESCRIPTOR_HASH == 0xCC31067A
    assert wire.FEATURE_TRANSFORMER_HASH == 0x6FCAD592
    assert wire.NETWORK_HASH == 0x0CF9A484
    wire.assert_hash_contract()


@pytest.mark.parametrize(
    "value",
    (
        -(1 << 31),
        -32768,
        -8193,
        -8192,
        -65,
        -64,
        -1,
        0,
        1,
        63,
        64,
        8191,
        8192,
        32767,
        (1 << 31) - 1,
    ),
)
def test_signed_leb_encoder_is_canonical_and_round_trips(value: int) -> None:
    bits = 16 if -32768 <= value <= 32767 else 32
    assert _decode_small(_framed((value,)), count=1, bits=bits) == {
        "small[0]": value
    }


@pytest.mark.parametrize(
    "values,bits",
    (
        ((64, -65, 32767, -32768), 16),
        (((1 << 31) - 1, -(1 << 31)), 32),
    ),
)
def test_signed_leb_multibyte_values_cross_stream_chunks(
    monkeypatch: pytest.MonkeyPatch, values: tuple[int, ...], bits: int
) -> None:
    monkeypatch.setattr(wire, "STREAM_CHUNK", 1)
    assert any(len(wire.encode_sleb128(value)) > 1 for value in values)
    assert _decode_small(_framed(values), count=len(values), bits=bits) == {
        f"small[{index}]": value for index, value in enumerate(values)
    }


@pytest.mark.parametrize(
    "payload,error",
    (
        (wire.LEB128_MAGIC + struct.pack("<I", 2) + b"\x80\x00", "non-canonical"),
        (wire.LEB128_MAGIC + struct.pack("<I", 1) + b"\x80", "unterminated"),
        (
            wire.LEB128_MAGIC
            + struct.pack("<I", len(wire.encode_sleb128(32768)))
            + wire.encode_sleb128(32768),
            "outside i16",
        ),
        (_framed((0, 0)), "too many values"),
        (_framed((0,)), "shorter than its element count"),
        (wire.LEB128_MAGIC + struct.pack("<I", 0), "shorter than"),
        (wire.LEB128_MAGIC + struct.pack("<I", 4), "canonical maximum"),
    ),
)
def test_signed_leb_reader_rejects_noncanonical_or_wrong_shape(
    payload: bytes, error: str
) -> None:
    count = 2 if "element count" in error else 1
    with pytest.raises(wire.WireError, match=error):
        _decode_small(payload, count=count, bits=16)


def test_read_only_patch_and_limit_overlays_do_not_mutate_the_source() -> None:
    payload = _framed((0, 1, -1))
    original = bytes(payload)
    payload_offset = len(wire.LEB128_MAGIC) + 4
    patched = _decode_small(
        payload,
        count=3,
        bits=16,
        patches={payload_offset + 1: 2},
    )
    assert patched == {"small[0]": 0, "small[1]": 2, "small[2]": -1}
    with pytest.raises(wire.WireError, match="truncated"):
        _decode_small(payload, count=3, bits=16, limit=len(payload) - 1)
    with pytest.raises(ValueError, match="non-negative"):
        wire._Reader(BytesIO(payload), limit=-1)
    with pytest.raises(ValueError, match="outside the read limit"):
        wire._Reader(BytesIO(payload), patches={len(payload) - 1: 0}, limit=len(payload) - 1)
    assert payload == original


def test_reference_is_schema_independent_and_has_no_large_tensor_container() -> None:
    source = Path(wire.__file__).read_text(encoding="utf-8")
    assert "import json" not in source
    assert "schemas/atomic-nnue-v3.json" not in source
    assert "[0] * (HM_DIMENSIONS" not in source
    assert wire.STREAM_CHUNK <= 1 << 20


def test_global_forward_interval_accepts_exact_asymmetric_limits_only() -> None:
    tail = wire.FWD_MAX - wire.INT32_MAX
    assert tail == wire.INT32_MIN - wire.FWD_MIN

    assert wire.validate_forward_interval(
        (0, wire.INT32_MAX),
        (0, tail),
        (0, 0),
        context="positive exact boundary",
    ) == (0, wire.FWD_MAX)
    with pytest.raises(wire.WireError, match="dense skip/output"):
        wire.validate_forward_interval(
            (0, wire.INT32_MAX),
            (0, tail + 1),
            (0, 0),
            context="positive boundary plus one",
        )

    assert wire.validate_forward_interval(
        (wire.INT32_MIN, 0),
        (0, 0),
        (0, tail),
        context="negative exact boundary",
    ) == (wire.FWD_MIN, 0)
    with pytest.raises(wire.WireError, match="dense skip/output"):
        wire.validate_forward_interval(
            (wire.INT32_MIN, 0),
            (0, 0),
            (0, tail + 1),
            context="negative boundary minus one",
        )
