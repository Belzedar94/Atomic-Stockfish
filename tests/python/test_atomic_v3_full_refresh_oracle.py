#!/usr/bin/env python3
"""Composition, ownership and source-contract gates for V3 full refresh."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import ast
from pathlib import Path
import re
import sys
from typing import Optional

import pytest


ROOT = Path(__file__).resolve().parents[2]
PYTHON_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(PYTHON_DIR))

import atomic_v3_blast_ring_reference as ring  # noqa: E402
import atomic_v3_capture_pair_reference as cp  # noqa: E402
import atomic_v3_full_refresh_reference as full  # noqa: E402
import atomic_v3_hm_reference as hm  # noqa: E402
import atomic_v3_king_blast_ep_reference as kb  # noqa: E402


def _piece(color: str, kind: str, square: str) -> cp.Piece:
    return cp.Piece(color, kind, cp.square(square))


def _position(
    pieces: tuple[cp.Piece, ...],
    *,
    side_to_move: str = cp.WHITE,
    ep_square: Optional[str] = None,
    atomic960: bool = False,
    castling_rights: str = "-",
) -> cp.CapturePosition:
    return cp.CapturePosition(
        pieces,
        side_to_move=side_to_move,
        ep_square=None if ep_square is None else cp.square(ep_square),
        atomic960=atomic960,
        castling_rights=castling_rights,
    )


def _corpus() -> tuple[cp.CapturePosition, ...]:
    return (
        _position(
            (
                _piece(cp.WHITE, cp.KING, "c1"),
                _piece(cp.BLACK, cp.KING, "c8"),
                _piece(cp.WHITE, cp.QUEEN, "d4"),
                _piece(cp.BLACK, cp.ROOK, "d5"),
                _piece(cp.BLACK, cp.KNIGHT, "e5"),
                _piece(cp.WHITE, cp.PAWN, "e4"),
                _piece(cp.WHITE, cp.BISHOP, "c5"),
                _piece(cp.BLACK, cp.PAWN, "c4"),
            )
        ),
        _position(
            (
                _piece(cp.WHITE, cp.KING, "a1"),
                _piece(cp.BLACK, cp.KING, "h8"),
                _piece(cp.WHITE, cp.PAWN, "e5"),
                _piece(cp.BLACK, cp.PAWN, "d5"),
                _piece(cp.WHITE, cp.ROOK, "c5"),
                _piece(cp.BLACK, cp.BISHOP, "d7"),
            ),
            ep_square="d6",
        ),
        _position(
            (
                _piece(cp.WHITE, cp.KING, "a1"),
                _piece(cp.BLACK, cp.KING, "h8"),
                _piece(cp.WHITE, cp.PAWN, "e5"),
                _piece(cp.WHITE, cp.ROOK, "c5"),
                _piece(cp.BLACK, cp.BISHOP, "d7"),
            ),
            ep_square="d6",  # malformed metadata: normal projections survive
        ),
        _position(
            (
                _piece(cp.WHITE, cp.KING, "d4"),
                _piece(cp.BLACK, cp.KING, "e4"),
                _piece(cp.WHITE, cp.ROOK, "d2"),
                _piece(cp.BLACK, cp.QUEEN, "e2"),
                _piece(cp.WHITE, cp.KNIGHT, "c3"),
            )
        ),
        _position(
            (
                _piece(cp.WHITE, cp.KING, "c1"),
                _piece(cp.BLACK, cp.KING, "c8"),
                _piece(cp.WHITE, cp.ROOK, "a1"),
                _piece(cp.WHITE, cp.ROOK, "h1"),
                _piece(cp.BLACK, cp.ROOK, "a8"),
                _piece(cp.BLACK, cp.ROOK, "h8"),
                _piece(cp.WHITE, cp.QUEEN, "d4"),
                _piece(cp.BLACK, cp.BISHOP, "e4"),
            ),
            atomic960=True,
            castling_rights="AHah",
        ),
    )


def test_dimensions_offsets_and_aggregate_bound_are_exact() -> None:
    assert full.MAX_ACTIVE_FEATURES == 547
    assert full.PHYSICAL_DIMENSIONS == 75_084
    assert hm.PHYSICAL_DIMENSIONS == cp.PHYSICAL_OFFSET == 22_528
    assert cp.PHYSICAL_OFFSET + cp.PHYSICAL_DIMENSIONS == kb.PHYSICAL_OFFSET
    assert kb.PHYSICAL_OFFSET + kb.PHYSICAL_DIMENSIONS == ring.PHYSICAL_OFFSET
    assert ring.PHYSICAL_OFFSET + ring.PHYSICAL_DIMENSIONS == 75_084


@pytest.mark.parametrize("position", _corpus())
@pytest.mark.parametrize("perspective", cp.COLORS)
def test_combined_slices_equal_the_standalone_oracles(
    position: cp.CapturePosition, perspective: str
) -> None:
    combined = full.enumerate_full_refresh(position, perspective)
    hm_position = full.as_hm_position(position)
    assert combined.hm == hm.enumerate_hm(hm_position, perspective)
    assert combined.capture_pairs == cp.enumerate_capture_pairs(position, perspective)
    assert combined.king_blast_ep == kb.enumerate_king_blast_ep(position, perspective)
    assert combined.blast_ring == ring.enumerate_blast_ring(position, perspective)
    assert combined.active_feature_count <= full.MAX_ACTIVE_FEATURES


def test_hm_and_capture_pair_are_called_once_and_same_tuple_reaches_both_projectors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    position = _corpus()[1]
    counts = {"hm": 0, "cp": 0, "king": 0, "ring": 0}
    original_hm = hm.enumerate_hm
    original_cp = cp.enumerate_capture_pairs
    original_king = kb.project_king_blast_ep
    original_ring = ring.project_blast_ring
    shared: list[int] = []

    def counted_hm(*args: object, **kwargs: object) -> object:
        counts["hm"] += 1
        return original_hm(*args, **kwargs)  # type: ignore[arg-type]

    def counted_cp(*args: object, **kwargs: object) -> object:
        counts["cp"] += 1
        return original_cp(*args, **kwargs)  # type: ignore[arg-type]

    def counted_king(
        value: cp.CapturePosition,
        perspective: str,
        candidates: tuple[cp.CapturePairActivation, ...],
    ) -> object:
        counts["king"] += 1
        shared.append(id(candidates))
        return original_king(value, perspective, candidates)

    def counted_ring(
        value: cp.CapturePosition,
        perspective: str,
        candidates: tuple[cp.CapturePairActivation, ...],
    ) -> object:
        counts["ring"] += 1
        shared.append(id(candidates))
        return original_ring(value, perspective, candidates)

    monkeypatch.setattr(hm, "enumerate_hm", counted_hm)
    monkeypatch.setattr(cp, "enumerate_capture_pairs", counted_cp)
    monkeypatch.setattr(kb, "project_king_blast_ep", counted_king)
    monkeypatch.setattr(ring, "project_blast_ring", counted_ring)
    result = full.enumerate_full_refresh(position, cp.WHITE)
    assert result.active_feature_count > 0
    assert counts == {"hm": 1, "cp": 1, "king": 1, "ring": 1}
    assert len(shared) == 2 and shared[0] == shared[1]


def test_malformed_ep_is_success_and_preserves_normal_composition() -> None:
    malformed = _corpus()[2]
    baseline = cp.CapturePosition(
        malformed.pieces,
        side_to_move=malformed.side_to_move,
        ep_square=None,
    )
    for perspective in cp.COLORS:
        actual = full.enumerate_full_refresh(malformed, perspective)
        expected = full.enumerate_full_refresh(baseline, perspective)
        assert actual == expected


def test_orientation_is_joint_for_both_independent_perspectives() -> None:
    position = _corpus()[0]
    for perspective in cp.COLORS:
        result = full.enumerate_full_refresh(position, perspective)
        own_king = next(
            piece
            for piece in position.pieces
            if piece.color == perspective and piece.kind == cp.KING
        )
        expected = hm.orientation_for(perspective, own_king.square)
        assert result.orientation.vertical_xor == expected.vertical_xor
        assert result.orientation.horizontal_xor == expected.horizontal_xor
        assert result.orientation.oriented_own_king == expected.oriented_own_king


def test_concurrent_immutable_composition_is_deterministic() -> None:
    jobs = tuple(
        (position, perspective)
        for position in _corpus()
        for perspective in cp.COLORS
        for _ in range(16)
    )
    expected = {
        (position, perspective): full.enumerate_full_refresh(position, perspective)
        for position in _corpus()
        for perspective in cp.COLORS
    }

    def run(job: tuple[cp.CapturePosition, str]) -> full.FullRefreshEmission:
        return full.enumerate_full_refresh(*job)

    for workers in (1, 2, 4, 8):
        with ThreadPoolExecutor(max_workers=workers) as pool:
            results = tuple(pool.map(run, jobs))
        assert all(result == expected[job] for job, result in zip(jobs, results))


def test_python_39_grammar() -> None:
    for name in (
        "atomic_v3_full_refresh_reference.py",
        "test_atomic_v3_full_refresh_oracle.py",
        "../atomic_v3_full_refresh_differential.py",
    ):
        source = (PYTHON_DIR / name).read_text(encoding="utf-8")
        tree = ast.parse(source, filename=name, feature_version=9)
        assert tree.body


def test_cpp_source_contract_proves_single_enumeration_and_shared_cp() -> None:
    source = (ROOT / "src/nnue/atomic_v3/full_refresh.cpp").read_text(encoding="utf-8")
    capture_source = (ROOT / "src/nnue/atomic_v3/capture_pair.cpp").read_text(
        encoding="utf-8"
    )
    scalar_source = (ROOT / "src/nnue/atomic_v3/scalar_backend.cpp").read_text(
        encoding="utf-8"
    )
    assert len(re.findall(r"\bemit_hm_features\s*\(", source)) == 1
    assert len(re.findall(r"\bemit_capture_pairs_from_hm\s*\(", source)) == 1
    assert not re.search(r"(?<!from_hm)\bemit_capture_pairs\s*\(", source)
    assert not re.search(r"\bemit_king_blast_ep\s*\(", source)
    assert not re.search(r"\bemit_blast_ring\s*\(", source)

    king_call = re.search(
        r"project_king_blast_ep\s*\([^;]*candidate\.capturePairs[^;]*\)",
        source,
        re.DOTALL,
    )
    ring_call = re.search(
        r"project_blast_ring\s*\([^;]*candidate\.capturePairs[^;]*\)",
        source,
        re.DOTALL,
    )
    assert king_call is not None and ring_call is not None
    assert source.count("make_capture_pair_snapshot(position)") == 1
    assert scalar_source.count("make_capture_pair_snapshot(position)") == 1
    assert capture_source.count("position.piece_on(Square(squareIndex))") == 1
    assert "nnue_dispatcher" not in source
    assert "load_candidate" not in source


def test_runtime_dispatcher_remains_intentionally_untouched() -> None:
    dispatcher = (ROOT / "src/nnue/nnue_dispatcher.h").read_text(encoding="utf-8")
    dispatcher += (ROOT / "src/nnue/nnue_dispatcher.cpp").read_text(encoding="utf-8")
    assert "AtomicNNUEV3" not in dispatcher
    assert "0xA70C0003" not in dispatcher
