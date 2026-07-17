from __future__ import annotations

import hashlib
import importlib.util
from pathlib import Path
import struct

import pytest


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "tests" / "nnue_v3_modes.py"
SPEC = importlib.util.spec_from_file_location("nnue_v3_modes", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
MODES = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODES)


def test_frozen_fixture_identity_matches_the_canonical_generator() -> None:
    generator = (ROOT / "tests/create_synthetic_atomic_v3_nnue.py").read_text(
        encoding="utf-8"
    )
    assert MODES.V3_SIZE == 77_349_879
    assert MODES.V3_SHA256 in generator
    assert MODES.V3_VERSION == 0xA70C0003
    assert MODES.V3_NETWORK_HASH == 0x0CF9A484
    assert MODES.V2_SHA256 == (
        "4DEB05CFF79B5D5EBA51C560F64ED24224671C188B6C5DB27521033E587C87C6"
    )
    assert MODES.THREAD_COUNTS == (1, 2, 4, 8)
    assert MODES.WIDE_TRACE_INTERNAL == 31_506
    assert MODES.WIDE_TRACE_WHITE_PAWNS == 151.47
    assert MODES.WIDE_TRACE_TABLE_COMPONENT == "+378092.30"
    assert MODES.WIDE_TRACE_TABLE_TOTAL == "+151.47"


def test_authentication_is_fail_closed_for_size_hash_and_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    payload = struct.pack(
        "<III", MODES.V3_VERSION, MODES.V3_NETWORK_HASH, 0
    ) + b"fixture"
    fixture = tmp_path / "fixture.nnue"
    fixture.write_bytes(payload)
    expected = hashlib.sha256(payload).hexdigest().upper()
    monkeypatch.setattr(MODES, "V3_SIZE", len(payload))

    MODES.authenticate_v3(fixture, expected)
    with pytest.raises(AssertionError, match="SHA-256 mismatch"):
        MODES.authenticate_v3(fixture, "0" * 64)

    wrong_header = bytearray(payload)
    wrong_header[0] ^= 1
    fixture.write_bytes(wrong_header)
    with pytest.raises(AssertionError, match="identity mismatch"):
        MODES.authenticate_v3(
            fixture, hashlib.sha256(wrong_header).hexdigest().upper()
        )

    fixture.write_bytes(payload + b"x")
    with pytest.raises(AssertionError, match="size mismatch"):
        MODES.authenticate_v3(
            fixture, hashlib.sha256(payload + b"x").hexdigest().upper()
        )


def test_search_markers_accept_only_real_v3_and_classical_paths() -> None:
    MODES.require_v3_search(
        ["NNUE evaluation using AtomicNNUEV3 fixture", "bestmove e2e4"],
        mode="true",
        threads=8,
    )
    with pytest.raises(AssertionError, match="did not report AtomicNNUEV3"):
        MODES.require_v3_search(
            ["NNUE evaluation using AtomicNNUEV2 fixture", "bestmove e2e4"],
            mode="pure",
            threads=1,
        )
    with pytest.raises(AssertionError, match="did not search"):
        MODES.require_v3_search(
            ["NNUE evaluation using AtomicNNUEV3 fixture", "bestmove (none)"],
            mode="true",
            threads=1,
        )

    MODES.require_classical_search(["bestmove e2e4"])
    with pytest.raises(AssertionError, match="activated an NNUE backend"):
        MODES.require_classical_search(
            ["NNUE evaluation using AtomicNNUEV3 fixture", "bestmove e2e4"]
        )


def test_relative_path_collision_accepts_only_cwd_v2_before_root_v3() -> None:
    MODES.require_relative_v2_selection(
        ["NNUE evaluation using AtomicNNUEV2 fixture", "bestmove e2e4"]
    )
    with pytest.raises(AssertionError, match="did not select cwd AtomicNNUEV2"):
        MODES.require_relative_v2_selection(
            ["NNUE evaluation using AtomicNNUEV3 fixture", "bestmove e2e4"]
        )
    with pytest.raises(AssertionError, match="did not search"):
        MODES.require_relative_v2_selection(
            ["NNUE evaluation using AtomicNNUEV2 fixture", "bestmove (none)"]
        )


def test_wide_v3_trace_must_saturate_and_match_search_evaluation() -> None:
    valid = [
        "NNUE evaluation          +31506 (side to move, internal units)",
        "Final evaluation      +151.47 (white side) [Use NNUE=true]",
    ]
    MODES.require_wide_v3_trace(valid)

    with pytest.raises(AssertionError, match="without wrapping"):
        MODES.require_wide_v3_trace(
            [
                "NNUE evaluation          -2 (side to move, internal units)",
                valid[1],
            ]
        )

    with pytest.raises(AssertionError, match="trace and search evaluation disagree"):
        MODES.require_wide_v3_trace(
            [
                valid[0],
                "Final evaluation      -0.01 (white side) [Use NNUE=true]",
            ]
        )


def test_wide_v3_contribution_table_uses_overflow_safe_integer_normalization() -> None:
    valid = [
        (
            f"|  {bucket}         |     0.00   |  +378092.30   |  "
            f"+151.47   |"
        )
        for bucket in range(8)
    ]
    MODES.require_wide_v3_trace_table(valid)

    wrapped = valid.copy()
    wrapped[0] = "|  0         |     0.00   |  +34885.32   |  +34885.32   |"
    with pytest.raises(AssertionError, match="contribution table overflowed"):
        MODES.require_wide_v3_trace_table(wrapped)
