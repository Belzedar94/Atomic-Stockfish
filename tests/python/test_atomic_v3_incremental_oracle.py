#!/usr/bin/env python3
"""Fixture-free gates for the independent AtomicNNUEV3 HM delta oracle."""

from __future__ import annotations

import ast
from dataclasses import replace
from pathlib import Path
import sys
from typing import Iterable, Optional

import pytest


PYTHON_DIR = Path(__file__).resolve().parent
if str(PYTHON_DIR) not in sys.path:
    sys.path.insert(0, str(PYTHON_DIR))

import atomic_v3_blast_ring_reference as ring  # noqa: E402
import atomic_v3_capture_pair_reference as cp  # noqa: E402
import atomic_v3_full_refresh_reference as full  # noqa: E402
import atomic_v3_incremental_reference as incremental  # noqa: E402
import atomic_v3_king_blast_ep_reference as king  # noqa: E402
import atomic_v3_scalar_reference as scalar  # noqa: E402


DENSE_KEYS = (
    "fc0",
    "fc0_squared",
    "fc0_clipped",
    "fc1",
    "fc1_squared",
    "fc1_clipped",
    "fc2",
    "raw_output",
    "scaled_output",
    "positional_value",
)


def _piece(color: str, kind: str, square: str) -> cp.Piece:
    return cp.Piece(color, kind, cp.square(square))


def _position(
    pieces: Iterable[cp.Piece],
    *,
    side_to_move: str = cp.WHITE,
    ep_square: Optional[str] = None,
    atomic960: bool = False,
    castling_rights: str = "-",
) -> cp.CapturePosition:
    return cp.CapturePosition(
        tuple(pieces),
        side_to_move=side_to_move,
        ep_square=None if ep_square is None else cp.square(ep_square),
        atomic960=atomic960,
        castling_rights=castling_rights,
    )


BASE = _position(
    (
        _piece(cp.WHITE, cp.KING, "h1"),
        _piece(cp.BLACK, cp.KING, "h8"),
        _piece(cp.WHITE, cp.PAWN, "a2"),
        _piece(cp.BLACK, cp.PAWN, "g7"),
    )
)
BASE_METADATA_ONLY = _position(
    BASE.pieces,
    atomic960=True,
    castling_rights="HAha",
)
QUIET_A3 = _position(
    tuple(
        cp.Piece(piece.color, piece.kind, cp.square("a3"))
        if piece.color == cp.WHITE and piece.kind == cp.PAWN
        else piece
        for piece in BASE.pieces
    ),
    side_to_move=cp.BLACK,
)
QUIET_A4 = _position(
    tuple(
        cp.Piece(piece.color, piece.kind, cp.square("a4"))
        if piece.color == cp.WHITE and piece.kind == cp.PAWN
        else piece
        for piece in BASE.pieces
    )
)
QUIET_B5 = _position(
    tuple(
        cp.Piece(piece.color, piece.kind, cp.square("b5"))
        if piece.color == cp.WHITE and piece.kind == cp.PAWN
        else piece
        for piece in BASE.pieces
    ),
    side_to_move=cp.BLACK,
)
EP_PIECES = (
    _piece(cp.WHITE, cp.KING, "a1"),
    _piece(cp.BLACK, cp.KING, "h8"),
    _piece(cp.WHITE, cp.PAWN, "e5"),
    _piece(cp.BLACK, cp.PAWN, "d5"),
    _piece(cp.WHITE, cp.ROOK, "c5"),
)
EP_OFF = _position(EP_PIECES)
EP_PARENT = _position(EP_PIECES, ep_square="d6")
EP_NULL_CHILD = _position(EP_PIECES, side_to_move=cp.BLACK)
KING_D1 = _position(
    (
        _piece(cp.WHITE, cp.KING, "d1"),
        _piece(cp.BLACK, cp.KING, "h8"),
        _piece(cp.WHITE, cp.PAWN, "a2"),
        _piece(cp.BLACK, cp.BISHOP, "c6"),
    )
)
KING_E1 = _position(
    tuple(
        cp.Piece(piece.color, piece.kind, cp.square("e1"))
        if piece.color == cp.WHITE and piece.kind == cp.KING
        else piece
        for piece in KING_D1.pieces
    )
)
STM_WHITE = _position(
    (
        _piece(cp.WHITE, cp.KING, "h1"),
        _piece(cp.BLACK, cp.KING, "a8"),
        _piece(cp.WHITE, cp.QUEEN, "b3"),
        _piece(cp.BLACK, cp.KNIGHT, "f6"),
    )
)
STM_BLACK = _position(STM_WHITE.pieces, side_to_move=cp.BLACK)
BUCKET_ZERO = BASE
BUCKET_ONE = _position(BASE.pieces + (_piece(cp.WHITE, cp.KNIGHT, "b1"),))

NETWORK_POSITIONS = (
    BASE,
    BASE_METADATA_ONLY,
    QUIET_A3,
    QUIET_A4,
    QUIET_B5,
    EP_OFF,
    EP_PARENT,
    EP_NULL_CHILD,
    KING_D1,
    KING_E1,
    STM_WHITE,
    STM_BLACK,
    BUCKET_ONE,
)


def _feature_weights(local_row: int, salt: int) -> tuple[tuple[int, int], ...]:
    """Small signed weights derived only from the physical row ordinal."""

    return (
        (0, (local_row * 3 + salt) % 9 - 4),
        (1, (local_row * 5 + salt + 2) % 11 - 5),
        (511, (local_row * 7 + salt + 1) % 7 - 3),
        (512, (local_row * 11 + salt + 3) % 9 - 4),
        (513, (local_row * 13 + salt + 4) % 11 - 5),
        (1023, (local_row * 17 + salt + 5) % 7 - 3),
    )


def _dense_stack(bucket: int) -> scalar.SparseDenseStack:
    fc0_biases = tuple(1_100 + bucket * 41 + output * 7 for output in range(32))
    fc1_biases = tuple(700 + bucket * 29 - output * 3 for output in range(32))
    fc2_biases = (3_000 + bucket * 137,)
    return scalar.SparseDenseStack(
        fc0_biases,
        {
            0: ((0, 7), (1, -5), (511, 3), (512, 11), (1023, -2)),
            7: ((0, -13), (512, 17), (513, 5), (1023, 3)),
            30: ((0, 37), (1, 9), (512, 19), (513, -7)),
            31: ((0, -11), (1, 5), (512, 23), (513, 13)),
        },
        fc1_biases,
        {
            0: ((0, 5), (7, -3), (31, 7), (32, -2), (63, 11)),
            15: ((0, -7), (30, 3), (31, 13), (32, 5), (63, -2)),
            31: ((1, 11), (30, -5), (33, 7), (62, 2)),
        },
        fc2_biases,
        {
            0: (
                (0, 3),
                (15, -5),
                (31, 7),
                (32, 11),
                (63, -13),
                (64, 17),
                (95, -19),
                (96, 23),
                (127, -29),
            )
        },
    )


def _build_network() -> scalar.SparseNetwork:
    physical = {
        "hm": set(),
        "capture_pair": set(),
        "king_blast_ep": set(),
        "blast_ring": set(),
    }
    for position in NETWORK_POSITIONS:
        for perspective in cp.COLORS:
            indices = full.enumerate_full_refresh(position, perspective).physical_indices()
            for name in physical:
                physical[name].update(indices[name])

    def rows(name: str, offset: int, salt: int) -> scalar.SparseRows:
        return {
            physical_row - offset: _feature_weights(physical_row - offset, salt)
            for physical_row in sorted(physical[name])
        }

    hm_rows = rows("hm", scalar.HM_PHYSICAL_OFFSET, 1)
    return scalar.SparseNetwork(
        Path("<fixture-free-algebraic-network>"),
        b"AtomicNNUEV3 fixture-free incremental unit network",
        "IN-MEMORY",
        tuple(144 + (output * 7) % 37 for output in range(1024)),
        hm_rows,
        rows("capture_pair", scalar.CAPTURE_PAIR_PHYSICAL_OFFSET, 3),
        rows("king_blast_ep", scalar.KING_BLAST_EP_PHYSICAL_OFFSET, 5),
        rows("blast_ring", scalar.BLAST_RING_PHYSICAL_OFFSET, 7),
        {
            local_row: tuple(
                (
                    bucket,
                    ((local_row + 17) * (bucket + 3) + bucket * 101) % 20_003
                    - 10_001,
                )
                for bucket in range(scalar.PSQT_BUCKETS)
            )
            for local_row in hm_rows
        },
        tuple(_dense_stack(bucket) for bucket in range(scalar.LAYER_STACKS)),
    )


@pytest.fixture(scope="module")
def network() -> scalar.SparseNetwork:
    return _build_network()


def _assert_full_frame(
    network: scalar.SparseNetwork, frame: incremental.IncrementalFrame
) -> None:
    expected = scalar.evaluate(network, frame.position)
    assert frame.result == expected
    assert len(frame.result["transformed"]) == scalar.FC0_INPUTS
    assert all(key in frame.result for key in DENSE_KEYS)
    for perspective, prefix in ((cp.WHITE, "white"), (cp.BLACK, "black")):
        state = frame.transition(perspective).state
        assert state.rows == tuple(sorted(set(state.rows)))
        assert state.rows == tuple(sorted(expected[f"{prefix}.hm"]))
        hm_only = list(network.biases)
        for physical_row in state.rows:
            local_row = physical_row - scalar.HM_PHYSICAL_OFFSET
            for output, value in network.hm.get(local_row, ()):
                hm_only[output] += value
        assert state.accumulator == tuple(hm_only)
        assert state.psqt == expected[f"{prefix}.psqt"]
        assert len(state.accumulator) == scalar.ACCUMULATOR_DIMENSIONS
        assert len(state.psqt) == scalar.PSQT_BUCKETS
        assert frame.result[f"{prefix}.accumulator"] == expected[
            f"{prefix}.accumulator"
        ]
        for relation in ("capture_pair", "king_blast_ep", "blast_ring"):
            rows = frame.result[f"{prefix}.{relation}"]
            assert len(rows) == len(set(rows))


def _manual_delta_accumulator(
    network: scalar.SparseNetwork,
    previous: incremental.HmPerspectiveState,
    transition: incremental.HmTransition,
) -> tuple[tuple[int, ...], tuple[int, ...]]:
    accumulator = list(previous.accumulator)
    psqt = list(previous.psqt)
    for sign, physical_rows in ((-1, transition.removed), (1, transition.added)):
        for physical_row in physical_rows:
            local_row = physical_row - scalar.HM_PHYSICAL_OFFSET
            for output, value in network.hm.get(local_row, ()):
                accumulator[output] += sign * value
            for bucket, value in network.hm_psqt.get(local_row, ()):
                psqt[bucket] += sign * value
    return tuple(accumulator), tuple(psqt)


def _unchecked_position(pieces: tuple[cp.Piece, ...]) -> cp.CapturePosition:
    """Construct a malformed frozen snapshot solely for transaction tests."""

    value = object.__new__(cp.CapturePosition)
    object.__setattr__(value, "pieces", pieces)
    object.__setattr__(value, "side_to_move", cp.WHITE)
    object.__setattr__(value, "ep_square", None)
    object.__setattr__(value, "atomic960", False)
    object.__setattr__(value, "castling_rights", "-")
    return value


def test_same_row_reuse_preserves_canonical_state_and_full_execution(
    network: scalar.SparseNetwork,
) -> None:
    oracle = incremental.IncrementalOracle(network)
    first, second = oracle.advance_many((BASE, BASE_METADATA_ONLY))
    _assert_full_frame(network, first)
    _assert_full_frame(network, second)

    for perspective in cp.COLORS:
        before = first.transition(perspective)
        after = second.transition(perspective)
        assert after.reused
        assert after.removed == after.added == ()
        assert after.retained == before.state.rows
        assert after.state.accumulator == before.state.accumulator
        assert after.state.psqt == before.state.psqt


def test_quiet_piece_change_is_exact_old_minus_removed_plus_added(
    network: scalar.SparseNetwork,
) -> None:
    oracle = incremental.IncrementalOracle(network)
    first, second = oracle.advance_many((BASE, QUIET_A3))
    _assert_full_frame(network, first)
    _assert_full_frame(network, second)

    for perspective in cp.COLORS:
        transition = second.transition(perspective)
        assert not transition.rebuilt
        assert transition.removed
        assert transition.added
        accumulator, psqt = _manual_delta_accumulator(
            network, first.transition(perspective).state, transition
        )
        assert transition.state.accumulator == accumulator
        assert transition.state.psqt == psqt


def test_remote_relation_only_change_keeps_hm_stable_but_changes_full_output(
    network: scalar.SparseNetwork,
) -> None:
    oracle = incremental.IncrementalOracle(network)
    without_ep, with_ep = oracle.advance_many((EP_OFF, EP_PARENT))
    _assert_full_frame(network, without_ep)
    _assert_full_frame(network, with_ep)

    for perspective in cp.COLORS:
        assert with_ep.transition(perspective).reused
        assert (
            with_ep.transition(perspective).state
            == without_ep.transition(perspective).state
        )
    changed_relation = any(
        without_ep.result[f"{prefix}.{name}"] != with_ep.result[f"{prefix}.{name}"]
        for prefix in ("white", "black")
        for name in ("capture_pair", "king_blast_ep", "blast_ring")
    )
    assert changed_relation
    assert without_ep.result["transformed"] != with_ep.result["transformed"]
    assert without_ep.result["raw_output"] != with_ep.result["raw_output"]


def test_king_mirror_boundary_forces_orientation_rebuild(
    network: scalar.SparseNetwork,
) -> None:
    oracle = incremental.IncrementalOracle(network)
    before, after = oracle.advance_many((KING_D1, KING_E1))
    _assert_full_frame(network, before)
    _assert_full_frame(network, after)

    white = after.white
    assert before.white.state.orientation.oriented_own_king == cp.square("e1")
    assert after.white.state.orientation.oriented_own_king == cp.square("e1")
    assert before.white.state.orientation.horizontal_xor == 7
    assert after.white.state.orientation.horizontal_xor == 0
    assert white.rebuilt
    assert white.removed == before.white.state.rows
    assert white.added == after.white.state.rows
    assert not after.black.rebuilt


def test_side_to_move_only_null_reuses_hm_and_swaps_execution_perspective(
    network: scalar.SparseNetwork,
) -> None:
    oracle = incremental.IncrementalOracle(network)
    before, after = oracle.advance_many((STM_WHITE, STM_BLACK))
    _assert_full_frame(network, before)
    _assert_full_frame(network, after)
    assert before.result["side_to_move"] == 0
    assert after.result["side_to_move"] == 1
    for perspective in cp.COLORS:
        assert after.transition(perspective).reused
        assert after.transition(perspective).state == before.transition(perspective).state
    assert before.result["transformed"] != after.result["transformed"]


def test_valid_ep_parent_null_clear_and_undo_restore_parent_exactly(
    network: scalar.SparseNetwork,
) -> None:
    oracle = incremental.IncrementalOracle(network)
    parent, null_child, restored = oracle.advance_many(
        (EP_PARENT, EP_NULL_CHILD, EP_PARENT)
    )
    for frame in (parent, null_child, restored):
        _assert_full_frame(network, frame)
    assert parent.result != null_child.result
    assert restored.result == parent.result
    for perspective in cp.COLORS:
        assert null_child.transition(perspective).reused
        assert restored.transition(perspective).reused
        assert restored.transition(perspective).state == parent.transition(perspective).state


def test_lazy_source_can_skip_captured_frames_without_changing_final_snapshot(
    network: scalar.SparseNetwork,
) -> None:
    direct = incremental.IncrementalOracle(network)
    direct.advance(BASE)
    direct_final = direct.advance(QUIET_B5)

    captured = incremental.IncrementalOracle(network)
    frames = captured.advance_many((BASE, QUIET_A3, QUIET_A4, QUIET_B5))
    for frame in frames:
        _assert_full_frame(network, frame)
    _assert_full_frame(network, direct_final)
    assert direct_final.result == frames[-1].result
    assert direct.states == captured.states
    assert direct.accepted_snapshots == 2
    assert captured.accepted_snapshots == 4


def test_piece_count_bucket_change_selects_new_dense_stack(
    network: scalar.SparseNetwork,
) -> None:
    oracle = incremental.IncrementalOracle(network)
    before, after = oracle.advance_many((BUCKET_ZERO, BUCKET_ONE))
    _assert_full_frame(network, before)
    _assert_full_frame(network, after)
    assert before.result["network_bucket"] == 0
    assert after.result["network_bucket"] == 1
    assert before.result["raw_output"] != after.result["raw_output"]
    assert any(after.transition(perspective).added for perspective in cp.COLORS)


def test_hm_psqt_state_accepts_wide_scratch_within_publishable_i32(
    network: scalar.SparseNetwork,
) -> None:
    emission = full.enumerate_full_refresh(BASE, cp.WHITE)
    physical_row = emission.hm[0].physical_index
    wide_network = replace(
        network,
        hm_psqt={
            physical_row - scalar.HM_PHYSICAL_OFFSET: ((0, 1_000_000),)
        },
    )
    transition = incremental.transition_hm(
        wide_network, emission, cp.WHITE, previous=None
    )
    assert transition.state.psqt[0] == 1_000_000
    assert transition.state.psqt[1:] == (0,) * (scalar.PSQT_BUCKETS - 1)


def test_hm_psqt_i64_scratch_rejects_out_of_i32_publication_transactionally(
    network: scalar.SparseNetwork,
) -> None:
    base_rows = {
        activation.physical_index
        for perspective in cp.COLORS
        for activation in full.enumerate_full_refresh(BASE, perspective).hm
    }
    child_rows = {
        activation.physical_index
        for perspective in cp.COLORS
        for activation in full.enumerate_full_refresh(QUIET_A3, perspective).hm
    }
    added_row = min(child_rows - base_rows)
    overflow_network = replace(
        network,
        hm_psqt={
            added_row - scalar.HM_PHYSICAL_OFFSET: ((0, 3_000_000_000),)
        },
    )
    oracle = incremental.IncrementalOracle(overflow_network)
    published = oracle.advance(BASE)
    states = oracle.states
    accepted = oracle.accepted_snapshots

    with pytest.raises(
        incremental.IncrementalOracleError, match="publishable i32 envelope"
    ):
        oracle.advance(QUIET_A3)
    assert oracle.states is states
    assert oracle.last_frame is published
    assert oracle.accepted_snapshots == accepted


def test_invalid_missing_or_multiple_king_snapshots_are_transactional(
    network: scalar.SparseNetwork,
) -> None:
    missing_king = _unchecked_position(
        (
            _piece(cp.WHITE, cp.KING, "a1"),
            _piece(cp.BLACK, cp.PAWN, "h7"),
        )
    )
    multiple_kings = _unchecked_position(
        (
            _piece(cp.WHITE, cp.KING, "a1"),
            _piece(cp.WHITE, cp.KING, "b1"),
            _piece(cp.BLACK, cp.KING, "h8"),
        )
    )
    oracle = incremental.IncrementalOracle(network)
    published = oracle.advance(BASE)
    states = oracle.states
    accepted = oracle.accepted_snapshots

    for invalid in (object(), missing_king, multiple_kings):
        with pytest.raises(incremental.IncrementalOracleError):
            oracle.advance(invalid)  # type: ignore[arg-type]
        assert oracle.states is states
        assert oracle.last_frame is published
        assert oracle.accepted_snapshots == accepted

    with pytest.raises(incremental.IncrementalOracleError):
        oracle.advance_many((QUIET_A3, multiple_kings))
    assert oracle.states is states
    assert oracle.last_frame is published
    assert oracle.accepted_snapshots == accepted

    recovered = oracle.advance(QUIET_A3)
    _assert_full_frame(network, recovered)
    assert oracle.accepted_snapshots == accepted + 1


def test_merge_contract_rejects_noncanonical_or_out_of_slice_rows() -> None:
    assert incremental.merge_row_difference((1, 3, 7), (1, 2, 7, 9)) == (
        (3,),
        (2, 9),
        (1, 7),
    )
    with pytest.raises(incremental.IncrementalOracleError, match="duplicate"):
        incremental.merge_row_difference((1, 1), (1,))
    with pytest.raises(incremental.IncrementalOracleError, match="out-of-range"):
        incremental.merge_row_difference((-1,), ())
    with pytest.raises(incremental.IncrementalOracleError, match="out-of-range"):
        incremental.merge_row_difference((), (cp.PHYSICAL_OFFSET,))


def test_oracle_source_is_python39_and_independent_of_engine_delta_helpers() -> None:
    paths = (Path(incremental.__file__), Path(__file__))
    for path in paths:
        ast.parse(path.read_text(encoding="utf-8"), filename=str(path), feature_version=9)

    source = Path(incremental.__file__).read_text(encoding="utf-8")
    for forbidden in (
        "incremental_backend",
        "DirtyPiece",
        "do_move",
        "legal_moves",
        "create_synthetic_atomic_v3_nnue",
        "SENTINELS",
    ):
        assert forbidden not in source
