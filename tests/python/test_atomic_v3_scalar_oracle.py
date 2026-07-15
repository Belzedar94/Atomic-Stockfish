#!/usr/bin/env python3
"""Focused unit gates for the independent AtomicNNUEV3 scalar oracle."""

from __future__ import annotations

from pathlib import Path

import atomic_v3_scalar_reference as scalar
import atomic_v3_wire_reference as wire


def test_fixture_identity_is_hard_pinned_and_generator_independent() -> None:
    source = Path(scalar.__file__).read_text(encoding="utf-8")
    assert scalar.FROZEN_FIXTURE_SIZE == 77_349_879
    assert (
        scalar.FROZEN_FIXTURE_SHA256
        == "00E46223822D06D7927E884EEC10739BA19EF8DD82A6E262F627D361658080C2"
    )
    assert "create_synthetic_atomic_v3_nnue" not in source
    assert "SENTINELS" not in source


def test_signed_division_truncates_toward_zero() -> None:
    assert scalar.trunc_div(7, 2) == 3
    assert scalar.trunc_div(-7, 2) == -3
    assert scalar.trunc_div(-33_554_431, 16) == -2_097_151
    assert scalar.trunc_div(-75_267, 16) == -4_704


def test_sparse_sleb_second_pass_retains_signed_boundaries(tmp_path: Path) -> None:
    values = (0, 63, 64, -64, -65, 32_767, -32_768, 0)
    payload = b"".join(wire.encode_sleb128(value) for value in values)
    path = tmp_path / "sleb.bin"
    path.write_bytes(payload)
    span = wire.WireSpan(0, 0, len(payload), len(values))
    with path.open("rb") as stream:
        actual = scalar._scan_sparse_sleb(stream, span, 16, "fixture")
    assert actual == {
        1: 63,
        2: 64,
        3: -64,
        4: -65,
        5: 32_767,
        6: -32_768,
    }


def test_sparse_i8_second_pass_preserves_twos_complement(tmp_path: Path) -> None:
    payload = bytes((0, 1, 127, 128, 255, 0))
    path = tmp_path / "raw-i8.bin"
    path.write_bytes(payload)
    span = wire.WireSpan(0, 0, len(payload), len(payload))
    with path.open("rb") as stream:
        actual = scalar._scan_sparse_i8(stream, span, "fixture")
    assert actual == {1: 1, 2: 127, 3: -128, 4: -1}


def test_adversarial_dense_vectors_lock_every_signed_scale() -> None:
    expected = {
        False: (51_544, 30_201, 1_887),
        True: (-128_456, -75_267, -4_704),
    }
    for negative, outputs in expected.items():
        transformed, stack = scalar.adversarial_dense_vector(negative)
        result = scalar.propagate_dense(transformed, stack)
        assert len(result["fc0"]) == 32
        assert len(result["fc0_squared"]) == 32
        assert len(result["fc0_clipped"]) == 32
        assert len(result["fc1"]) == 32
        assert len(result["fc1_squared"]) == 32
        assert len(result["fc1_clipped"]) == 32
        assert len(result["fc2"]) == 1
        assert (
            result["raw_output"],
            result["scaled_output"],
            result["positional_value"],
        ) == outputs
