#!/usr/bin/env python3
"""Exhaustive, golden and metamorphic gates for the V3 BlastRing oracle."""

from __future__ import annotations

import ast
from dataclasses import replace
from pathlib import Path
import random
import sys

import pytest


ROOT = Path(__file__).resolve().parents[2]
PYTHON_TESTS = ROOT / "tests" / "python"
if str(PYTHON_TESTS) not in sys.path:
    sys.path.insert(0, str(PYTHON_TESTS))

import atomic_v3_blast_ring_reference as ring  # noqa: E402
import atomic_v3_capture_pair_reference as cp  # noqa: E402


def piece(color: str, kind: str, name: str) -> cp.Piece:
    return cp.Piece(color, kind, cp.square(name))


def position(
    *pieces: cp.Piece,
    side_to_move: str = cp.WHITE,
    ep_square: object = None,
    atomic960: bool = False,
    castling_rights: str = "-",
) -> cp.CapturePosition:
    if isinstance(ep_square, str):
        ep_square = cp.square(ep_square)
    return cp.CapturePosition(
        tuple(pieces),
        side_to_move=side_to_move,
        ep_square=ep_square,  # type: ignore[arg-type]
        atomic960=atomic960,
        castling_rights=castling_rights,
    )


def rows_at(
    value: cp.CapturePosition,
    perspective: str,
    center: str,
    actor_rel: object = None,
) -> tuple[ring.BlastRingActivation, ...]:
    raw_center = cp.square(center)
    return tuple(
        row
        for row in ring.enumerate_blast_ring(value, perspective)
        if row.raw_center == raw_center
        and (actor_rel is None or row.actor_rel == actor_rel)
    )


def compact_signature(
    value: cp.CapturePosition, perspective: str
) -> tuple[tuple[object, ...], ...]:
    return tuple(
        (
            row.index,
            row.oriented_center,
            row.actor_rel,
            row.oriented_collateral,
            row.collateral_rel,
            row.offset,
            row.collateral_class,
        )
        for row in ring.enumerate_blast_ring(value, perspective)
    )


def horizontal_mirror(value: cp.CapturePosition) -> cp.CapturePosition:
    return cp.CapturePosition(
        tuple(cp.Piece(item.color, item.kind, item.square ^ 7) for item in value.pieces),
        side_to_move=value.side_to_move,
        ep_square=None if value.ep_square is None else value.ep_square ^ 7,
        atomic960=value.atomic960,
        castling_rights=value.castling_rights,
    )


def color_vertical_mirror(value: cp.CapturePosition) -> cp.CapturePosition:
    return cp.CapturePosition(
        tuple(
            cp.Piece(
                cp.BLACK if item.color == cp.WHITE else cp.WHITE,
                item.kind,
                item.square ^ 56,
            )
            for item in value.pieces
        ),
        side_to_move=cp.BLACK if value.side_to_move == cp.WHITE else cp.WHITE,
        ep_square=None if value.ep_square is None else value.ep_square ^ 56,
        atomic960=value.atomic960,
        castling_rights=value.castling_rights,
    )


def test_dimensions_orders_and_offsets_are_frozen() -> None:
    assert ring.PHYSICAL_OFFSET == 64_844
    assert ring.CENTER_DIMENSIONS == 64
    assert ring.ACTOR_RELATIONS == (ring.OWN, ring.OPP)
    assert ring.COLLATERAL_RELATIONS == (ring.OWN, ring.OPP)
    assert ring.DIRECTION_ORDER == ("N", "NE", "E", "SE", "S", "SW", "W", "NW")
    assert ring.DIRECTION_DELTAS == {
        "N": 8,
        "NE": 9,
        "E": 1,
        "SE": -7,
        "S": -8,
        "SW": -9,
        "W": -1,
        "NW": 7,
    }
    assert ring.CLASS_ORDER == (
        ring.KNIGHT,
        ring.BISHOP,
        ring.ROOK,
        ring.QUEEN,
        ring.ADJACENT_PAWN_SURVIVES,
    )
    assert ring.PHYSICAL_DIMENSIONS == ring.TRAINING_DIMENSIONS == 10_240
    assert ring.MAX_ACTIVE_FEATURES == 240


def test_all_10_240_indices_are_dense_unique_and_formula_exact() -> None:
    indices = []
    for center in range(64):
        for actor_index, actor_rel in enumerate(ring.ACTOR_RELATIONS):
            for collateral_index, collateral_rel in enumerate(
                ring.COLLATERAL_RELATIONS
            ):
                for offset_index, offset in enumerate(ring.DIRECTION_ORDER):
                    for class_index, collateral_class in enumerate(ring.CLASS_ORDER):
                        expected = (
                            (
                                (
                                    (center * 2 + actor_index) * 2
                                    + collateral_index
                                )
                                * 8
                                + offset_index
                            )
                            * 5
                            + class_index
                        )
                        actual = ring.blast_ring_index(
                            center,
                            actor_rel,
                            collateral_rel,
                            offset,
                            collateral_class,
                        )
                        assert actual == expected
                        indices.append(actual)
                        activation = ring.BlastRingActivation(
                            actual,
                            center,
                            center,
                            actor_rel,
                            center,
                            center,
                            collateral_rel,
                            offset,
                            collateral_class,
                        )
                        assert activation.local_index == expected
                        assert activation.physical_index == 64_844 + expected
                        assert activation.pawn_survives == (
                            collateral_class == ring.ADJACENT_PAWN_SURVIVES
                        )
    assert indices == list(range(10_240))
    assert ring.blast_ring_index(0, ring.OWN, ring.OWN, "N", ring.KNIGHT) == 0
    assert (
        ring.blast_ring_index(
            63,
            ring.OPP,
            ring.OPP,
            "NW",
            ring.ADJACENT_PAWN_SURVIVES,
        )
        == 10_239
    )


@pytest.mark.parametrize(
    ("offset", "expected_name"),
    (
        ("N", "d5"),
        ("NE", "e5"),
        ("E", "e4"),
        ("SE", "e3"),
        ("S", "d3"),
        ("SW", "c3"),
        ("W", "c4"),
        ("NW", "c5"),
    ),
)
def test_joint_frame_direction_helpers_have_exact_d4_goldens(
    offset: str, expected_name: str
) -> None:
    center = cp.square("d4")
    collateral = ring.directional_square(center, offset)
    assert collateral == cp.square(expected_name)
    assert collateral == center + ring.DIRECTION_DELTAS[offset]
    assert ring.adjacent_direction(center, collateral) == offset


def test_direction_helpers_reject_wrap_nonadjacency_and_offboard_offsets() -> None:
    assert ring.directional_square(cp.square("a1"), "W") is None
    assert ring.directional_square(cp.square("a1"), "SW") is None
    assert ring.directional_square(cp.square("h8"), "NE") is None
    assert ring.adjacent_direction(cp.square("a1"), cp.square("h1")) is None
    assert ring.adjacent_direction(cp.square("d4"), cp.square("d6")) is None
    assert ring.adjacent_direction(cp.square("d4"), cp.square("d4")) is None


@pytest.mark.parametrize(
    ("collateral_kind", "collateral_class"),
    (
        (cp.KNIGHT, ring.KNIGHT),
        (cp.BISHOP, ring.BISHOP),
        (cp.ROOK, ring.ROOK),
        (cp.QUEEN, ring.QUEEN),
        (cp.PAWN, ring.ADJACENT_PAWN_SURVIVES),
    ),
)
def test_all_collateral_classes_have_exact_d4_d5_numeric_goldens(
    collateral_kind: str, collateral_class: str
) -> None:
    value = position(
        piece(cp.WHITE, cp.KING, "h1"),
        piece(cp.BLACK, cp.KING, "h8"),
        piece(cp.WHITE, cp.KNIGHT, "b3"),
        piece(cp.BLACK, cp.PAWN, "d4"),
        piece(cp.BLACK, collateral_kind, "d5"),
    )
    selected = [
        row
        for row in rows_at(value, cp.WHITE, "d4", ring.OWN)
        if row.raw_collateral == cp.square("d5")
    ]
    assert len(selected) == 1
    expected = ((((cp.square("d4") * 2) * 2 + 1) * 8) * 5) + ring.CLASS_ORDER.index(
        collateral_class
    )
    assert selected[0].index == expected
    assert selected[0].physical_index == ring.PHYSICAL_OFFSET + expected
    assert selected[0].collateral_rel == ring.OPP
    assert selected[0].offset == "N"
    assert selected[0].collateral_class == collateral_class


@pytest.mark.parametrize("offset", ring.DIRECTION_ORDER)
def test_all_eight_offsets_emit_exact_d4_numeric_goldens(offset: str) -> None:
    collateral_square = ring.directional_square(cp.square("d4"), offset)
    assert collateral_square is not None
    value = position(
        piece(cp.WHITE, cp.KING, "h1"),
        piece(cp.BLACK, cp.KING, "h8"),
        piece(cp.WHITE, cp.KNIGHT, "b3"),
        piece(cp.BLACK, cp.PAWN, "d4"),
        cp.Piece(cp.BLACK, cp.BISHOP, collateral_square),
    )
    selected = [
        row
        for row in rows_at(value, cp.WHITE, "d4", ring.OWN)
        if row.raw_collateral == collateral_square
    ]
    assert len(selected) == 1
    expected = ring.blast_ring_index(
        cp.square("d4"), ring.OWN, ring.OPP, offset, ring.BISHOP
    )
    assert selected[0].local_index == expected
    assert selected[0].offset == offset


@pytest.mark.parametrize("actor_rel", ring.ACTOR_RELATIONS)
@pytest.mark.parametrize("collateral_rel", ring.COLLATERAL_RELATIONS)
def test_actor_and_collateral_relations_are_independent_color_labels(
    actor_rel: str, collateral_rel: str
) -> None:
    actor_color = cp.WHITE if actor_rel == ring.OWN else cp.BLACK
    target_color = cp.BLACK if actor_color == cp.WHITE else cp.WHITE
    collateral_color = cp.WHITE if collateral_rel == ring.OWN else cp.BLACK
    value = position(
        piece(cp.WHITE, cp.KING, "h1"),
        piece(cp.BLACK, cp.KING, "h8"),
        piece(actor_color, cp.KNIGHT, "b3"),
        piece(target_color, cp.PAWN, "d4"),
        piece(collateral_color, cp.BISHOP, "d5"),
    )
    selected = [
        row
        for row in rows_at(value, cp.WHITE, "d4", actor_rel)
        if row.raw_collateral == cp.square("d5")
    ]
    assert len(selected) == 1
    assert selected[0].collateral_rel == collateral_rel
    assert selected[0].oriented_center == cp.square("d4")
    assert selected[0].oriented_collateral == cp.square("d5")
    expected = ring.blast_ring_index(
        cp.square("d4"), actor_rel, collateral_rel, "N", ring.BISHOP
    )
    assert selected[0].index == expected


def test_board_edge_emits_only_real_a1_neighbors_without_file_wrap() -> None:
    value = position(
        piece(cp.WHITE, cp.KING, "h1"),
        piece(cp.BLACK, cp.KING, "h8"),
        piece(cp.WHITE, cp.KNIGHT, "c2"),
        piece(cp.BLACK, cp.PAWN, "a1"),
        piece(cp.BLACK, cp.KNIGHT, "a2"),
        piece(cp.BLACK, cp.BISHOP, "b2"),
        piece(cp.BLACK, cp.ROOK, "b1"),
    )
    selected = rows_at(value, cp.WHITE, "a1", ring.OWN)
    assert {row.raw_collateral for row in selected} == {
        cp.square("a2"),
        cp.square("b2"),
        cp.square("b1"),
    }
    assert {row.offset for row in selected} == {"N", "NE", "E"}


def test_sole_adjacent_capture_origin_is_excluded() -> None:
    value = position(
        piece(cp.WHITE, cp.KING, "h1"),
        piece(cp.BLACK, cp.KING, "h8"),
        piece(cp.WHITE, cp.ROOK, "d3"),
        piece(cp.BLACK, cp.PAWN, "d4"),
        piece(cp.BLACK, cp.KNIGHT, "e4"),
    )
    selected = rows_at(value, cp.WHITE, "d4", ring.OWN)
    assert not any(row.raw_collateral == cp.square("d3") for row in selected)
    assert any(row.raw_collateral == cp.square("e4") for row in selected)


def test_sole_pawn_capture_origin_is_not_mislabeled_as_surviving_collateral() -> None:
    value = position(
        piece(cp.WHITE, cp.KING, "h1"),
        piece(cp.BLACK, cp.KING, "a8"),
        piece(cp.WHITE, cp.PAWN, "d5"),
        piece(cp.BLACK, cp.ROOK, "e6"),
        piece(cp.WHITE, cp.PAWN, "f6"),
    )
    selected = rows_at(value, cp.WHITE, "e6", ring.OWN)
    assert not any(row.raw_collateral == cp.square("d5") for row in selected)
    assert any(
        row.raw_collateral == cp.square("f6")
        and row.collateral_class == ring.ADJACENT_PAWN_SURVIVES
        for row in selected
    )


def test_multiple_origins_retain_each_adjacent_origin_as_possible_collateral() -> None:
    value = position(
        piece(cp.WHITE, cp.KING, "h1"),
        piece(cp.BLACK, cp.KING, "h8"),
        piece(cp.WHITE, cp.ROOK, "d3"),
        piece(cp.WHITE, cp.ROOK, "c4"),
        piece(cp.BLACK, cp.PAWN, "d4"),
    )
    candidates = [
        row
        for row in cp.enumerate_capture_pairs(value, cp.WHITE)
        if row.raw_to == cp.square("d4") and row.actor_rel == ring.OWN
    ]
    assert {row.raw_from for row in candidates} == {cp.square("d3"), cp.square("c4")}
    selected = rows_at(value, cp.WHITE, "d4", ring.OWN)
    retained = {
        row.raw_collateral: row
        for row in selected
        if row.raw_collateral in {cp.square("d3"), cp.square("c4")}
    }
    assert set(retained) == {cp.square("d3"), cp.square("c4")}
    assert {row.offset for row in retained.values()} == {"S", "W"}
    assert all(row.collateral_class == ring.ROOK for row in retained.values())


def test_adjacent_pawn_survives_when_it_is_not_the_sole_origin() -> None:
    value = position(
        piece(cp.WHITE, cp.KING, "h1"),
        piece(cp.BLACK, cp.KING, "h8"),
        piece(cp.WHITE, cp.KNIGHT, "b3"),
        piece(cp.BLACK, cp.ROOK, "d4"),
        piece(cp.WHITE, cp.PAWN, "d5"),
    )
    selected = [
        row
        for row in rows_at(value, cp.WHITE, "d4", ring.OWN)
        if row.raw_collateral == cp.square("d5")
    ]
    assert len(selected) == 1
    assert selected[0].collateral_class == ring.ADJACENT_PAWN_SURVIVES
    assert selected[0].pawn_survives


def test_adjacent_kings_are_always_owned_by_the_separate_king_slice() -> None:
    value = position(
        piece(cp.WHITE, cp.KING, "e4"),
        piece(cp.BLACK, cp.KING, "d5"),
        piece(cp.WHITE, cp.KNIGHT, "b3"),
        piece(cp.BLACK, cp.PAWN, "d4"),
        piece(cp.BLACK, cp.BISHOP, "e5"),
    )
    selected = rows_at(value, cp.WHITE, "d4", ring.OWN)
    assert not any(
        row.raw_collateral in {cp.square("e4"), cp.square("d5")}
        for row in selected
    )
    assert any(row.raw_collateral == cp.square("e5") for row in selected)


def test_direct_king_target_can_still_project_nonking_collateral() -> None:
    value = position(
        piece(cp.WHITE, cp.KING, "h1"),
        piece(cp.BLACK, cp.KING, "d4"),
        piece(cp.WHITE, cp.KNIGHT, "b3"),
        piece(cp.BLACK, cp.ROOK, "d5"),
    )
    selected = rows_at(value, cp.WHITE, "d4", ring.OWN)
    assert [(row.raw_collateral, row.collateral_class) for row in selected] == [
        (cp.square("d5"), ring.ROOK)
    ]


def test_valid_white_ep_excludes_origin_and_offcenter_captured_pawn() -> None:
    value = position(
        piece(cp.WHITE, cp.KING, "h1"),
        piece(cp.BLACK, cp.KING, "a8"),
        piece(cp.WHITE, cp.PAWN, "d5"),
        piece(cp.BLACK, cp.PAWN, "e5"),
        piece(cp.WHITE, cp.PAWN, "f6"),
        piece(cp.BLACK, cp.KNIGHT, "e7"),
        side_to_move=cp.WHITE,
        ep_square="e6",
    )
    for perspective in cp.COLORS:
        actor_rel = ring.OWN if perspective == cp.WHITE else ring.OPP
        selected = rows_at(value, perspective, "e6", actor_rel)
        assert not any(row.raw_collateral == cp.square("d5") for row in selected)
        assert not any(row.raw_collateral == cp.square("e5") for row in selected)
        assert any(
            row.raw_collateral == cp.square("f6")
            and row.collateral_class == ring.ADJACENT_PAWN_SURVIVES
            for row in selected
        )
        assert any(
            row.raw_collateral == cp.square("e7")
            and row.collateral_class == ring.KNIGHT
            for row in selected
        )


def test_two_white_ep_origins_are_retained_but_captured_pawn_is_always_excluded() -> None:
    value = position(
        piece(cp.WHITE, cp.KING, "h1"),
        piece(cp.BLACK, cp.KING, "a8"),
        piece(cp.WHITE, cp.PAWN, "d5"),
        piece(cp.WHITE, cp.PAWN, "f5"),
        piece(cp.BLACK, cp.PAWN, "e5"),
        side_to_move=cp.WHITE,
        ep_square="e6",
    )
    for perspective in cp.COLORS:
        actor_rel = ring.OWN if perspective == cp.WHITE else ring.OPP
        candidates = [
            row
            for row in cp.enumerate_capture_pairs(value, perspective)
            if row.en_passant
        ]
        assert {row.raw_from for row in candidates} == {
            cp.square("d5"),
            cp.square("f5"),
        }
        selected = rows_at(value, perspective, "e6", actor_rel)
        retained = {
            row.raw_collateral: row
            for row in selected
            if row.raw_collateral in {cp.square("d5"), cp.square("f5")}
        }
        assert set(retained) == {cp.square("d5"), cp.square("f5")}
        assert all(
            row.collateral_class == ring.ADJACENT_PAWN_SURVIVES
            for row in retained.values()
        )
        assert not any(row.raw_collateral == cp.square("e5") for row in selected)


def test_two_black_ep_origins_use_the_same_raw_exclusions_in_both_perspectives() -> None:
    value = position(
        piece(cp.WHITE, cp.KING, "a1"),
        piece(cp.BLACK, cp.KING, "h8"),
        piece(cp.BLACK, cp.PAWN, "d4"),
        piece(cp.BLACK, cp.PAWN, "f4"),
        piece(cp.WHITE, cp.PAWN, "e4"),
        side_to_move=cp.BLACK,
        ep_square="e3",
    )
    for perspective in cp.COLORS:
        actor_rel = ring.OWN if perspective == cp.BLACK else ring.OPP
        selected = rows_at(value, perspective, "e3", actor_rel)
        origins = {
            row.raw_collateral
            for row in selected
            if row.raw_collateral in {cp.square("d4"), cp.square("f4")}
        }
        assert origins == {cp.square("d4"), cp.square("f4")}
        assert not any(row.raw_collateral == cp.square("e4") for row in selected)


def _malformed_ep_position(actor_color: str, case: str) -> cp.CapturePosition:
    if actor_color == cp.WHITE:
        items = [
            piece(cp.WHITE, cp.KING, "h1"),
            piece(cp.BLACK, cp.KING, "a8"),
            piece(cp.WHITE, cp.KNIGHT, "c3"),
            piece(cp.BLACK, cp.ROOK, "b5"),
            piece(cp.BLACK, cp.BISHOP, "b6"),
        ]
        if case != "no-attacker":
            items.append(piece(cp.WHITE, cp.PAWN, "d5"))
        if case != "missing-off-center":
            kind = cp.ROOK if case == "off-center-not-pawn" else cp.PAWN
            color = cp.WHITE if case == "off-center-friendly" else cp.BLACK
            items.append(piece(color, kind, "e5"))
        if case == "occupied-center":
            items.append(piece(cp.BLACK, cp.KNIGHT, "e6"))
        ep_square = "e3" if case == "wrong-rank" else "e6"
        return position(*items, side_to_move=cp.WHITE, ep_square=ep_square)

    items = [
        piece(cp.WHITE, cp.KING, "a1"),
        piece(cp.BLACK, cp.KING, "h8"),
        piece(cp.BLACK, cp.KNIGHT, "c6"),
        piece(cp.WHITE, cp.ROOK, "b4"),
        piece(cp.WHITE, cp.BISHOP, "b3"),
    ]
    if case != "no-attacker":
        items.append(piece(cp.BLACK, cp.PAWN, "d4"))
    if case != "missing-off-center":
        kind = cp.ROOK if case == "off-center-not-pawn" else cp.PAWN
        color = cp.BLACK if case == "off-center-friendly" else cp.WHITE
        items.append(piece(color, kind, "e4"))
    if case == "occupied-center":
        items.append(piece(cp.WHITE, cp.KNIGHT, "e3"))
    ep_square = "e6" if case == "wrong-rank" else "e3"
    return position(*items, side_to_move=cp.BLACK, ep_square=ep_square)


@pytest.mark.parametrize("actor_color", cp.COLORS)
@pytest.mark.parametrize(
    "case",
    (
        "wrong-rank",
        "occupied-center",
        "missing-off-center",
        "off-center-not-pawn",
        "off-center-friendly",
        "no-attacker",
    ),
)
def test_malformed_ep_omits_only_tail_and_preserves_normal_blast_ring(
    actor_color: str, case: str
) -> None:
    value = _malformed_ep_position(actor_color, case)
    baseline = cp.CapturePosition(
        value.pieces,
        side_to_move=value.side_to_move,
        ep_square=None,
    )
    for perspective in cp.COLORS:
        assert compact_signature(value, perspective) == compact_signature(
            baseline, perspective
        )
        assert ring.enumerate_blast_ring(value, perspective)


def test_promotion_candidate_is_not_multiplied_and_actor_type_is_history_free() -> None:
    pawn_position = position(
        piece(cp.WHITE, cp.KING, "h1"),
        piece(cp.BLACK, cp.KING, "g8"),
        piece(cp.WHITE, cp.PAWN, "g7"),
        piece(cp.BLACK, cp.ROOK, "h8"),
        piece(cp.WHITE, cp.KNIGHT, "h7"),
    )
    promoted_position = position(
        piece(cp.WHITE, cp.KING, "h1"),
        piece(cp.BLACK, cp.KING, "g8"),
        piece(cp.WHITE, cp.QUEEN, "g7"),
        piece(cp.BLACK, cp.ROOK, "h8"),
        piece(cp.WHITE, cp.KNIGHT, "h7"),
    )
    pawn_cp = [
        row
        for row in cp.enumerate_capture_pairs(pawn_position, cp.WHITE)
        if row.raw_from == cp.square("g7") and row.raw_to == cp.square("h8")
    ]
    queen_cp = [
        row
        for row in cp.enumerate_capture_pairs(promoted_position, cp.WHITE)
        if row.raw_from == cp.square("g7") and row.raw_to == cp.square("h8")
    ]
    assert len(pawn_cp) == len(queen_cp) == 1
    assert pawn_cp[0].actor_kind == cp.PAWN
    assert queen_cp[0].actor_kind == cp.QUEEN
    for perspective in cp.COLORS:
        pawn_rows = tuple(
            (
                row.raw_collateral,
                row.collateral_rel,
                row.offset,
                row.collateral_class,
            )
            for row in rows_at(pawn_position, perspective, "h8")
        )
        queen_rows = tuple(
            (
                row.raw_collateral,
                row.collateral_rel,
                row.offset,
                row.collateral_class,
            )
            for row in rows_at(promoted_position, perspective, "h8")
        )
        assert pawn_rows == queen_rows
        assert any(row[-1] == ring.KNIGHT for row in pawn_rows)


def test_already_promoted_collateral_uses_current_piece_class_only() -> None:
    value = position(
        piece(cp.WHITE, cp.KING, "h1"),
        piece(cp.BLACK, cp.KING, "h8"),
        piece(cp.WHITE, cp.KNIGHT, "b3"),
        piece(cp.BLACK, cp.PAWN, "d4"),
        piece(cp.BLACK, cp.QUEEN, "d5"),
    )
    selected = [
        row
        for row in rows_at(value, cp.WHITE, "d4", ring.OWN)
        if row.raw_collateral == cp.square("d5")
    ]
    assert len(selected) == 1
    assert selected[0].collateral_class == ring.QUEEN


def test_horizontal_mirror_preserves_joint_oriented_signature() -> None:
    original = position(
        piece(cp.WHITE, cp.KING, "c1"),
        piece(cp.BLACK, cp.KING, "b7"),
        piece(cp.WHITE, cp.ROOK, "a2"),
        piece(cp.BLACK, cp.PAWN, "a5"),
        piece(cp.BLACK, cp.BISHOP, "b5"),
        piece(cp.WHITE, cp.PAWN, "a6"),
    )
    mirrored = horizontal_mirror(original)
    for perspective in cp.COLORS:
        assert compact_signature(original, perspective) == compact_signature(
            mirrored, perspective
        )


def test_color_vertical_mirror_and_perspective_swap_preserve_signature() -> None:
    original = position(
        piece(cp.WHITE, cp.KING, "f1"),
        piece(cp.BLACK, cp.KING, "e6"),
        piece(cp.WHITE, cp.KNIGHT, "b3"),
        piece(cp.BLACK, cp.ROOK, "d4"),
        piece(cp.WHITE, cp.BISHOP, "d5"),
    )
    mirrored = color_vertical_mirror(original)
    assert compact_signature(original, cp.WHITE) == compact_signature(
        mirrored, cp.BLACK
    )


def test_perspective_change_relabels_both_relations_without_second_transform() -> None:
    value = position(
        piece(cp.WHITE, cp.KING, "h1"),
        piece(cp.BLACK, cp.KING, "h8"),
        piece(cp.WHITE, cp.KNIGHT, "b3"),
        piece(cp.BLACK, cp.PAWN, "d4"),
        piece(cp.WHITE, cp.BISHOP, "d5"),
    )
    white = [
        row
        for row in rows_at(value, cp.WHITE, "d4", ring.OWN)
        if row.raw_collateral == cp.square("d5")
    ]
    black = [
        row
        for row in rows_at(value, cp.BLACK, "d4", ring.OPP)
        if row.raw_collateral == cp.square("d5")
    ]
    assert len(white) == len(black) == 1
    assert white[0].collateral_rel == ring.OWN
    assert black[0].collateral_rel == ring.OPP
    assert white[0].offset == "N"
    assert black[0].offset == "S"
    assert white[0].oriented_center == cp.square("d4")
    assert black[0].oriented_center == cp.square("d5")


def test_atomic960_and_castling_metadata_are_projection_neutral() -> None:
    pieces = (
        piece(cp.WHITE, cp.KING, "b1"),
        piece(cp.BLACK, cp.KING, "e6"),
        piece(cp.WHITE, cp.QUEEN, "d4"),
        piece(cp.BLACK, cp.ROOK, "d5"),
        piece(cp.BLACK, cp.BISHOP, "e5"),
    )
    standard = position(*pieces, atomic960=False, castling_rights="KQkq")
    atomic960 = position(*pieces, atomic960=True, castling_rights="BGbg")
    for perspective in cp.COLORS:
        assert compact_signature(standard, perspective) == compact_signature(
            atomic960, perspective
        )


def test_capture_pair_is_called_once_and_is_the_sole_center_source(
    monkeypatch: object,
) -> None:
    value = position(
        piece(cp.WHITE, cp.KING, "h1"),
        piece(cp.BLACK, cp.KING, "h8"),
        piece(cp.WHITE, cp.KNIGHT, "b3"),
        piece(cp.BLACK, cp.PAWN, "d4"),
        piece(cp.BLACK, cp.BISHOP, "d5"),
    )
    assert ring.enumerate_blast_ring(value, cp.WHITE)
    calls = []

    def empty_source(*arguments: object) -> tuple[object, ...]:
        calls.append(arguments)
        return ()

    monkeypatch.setattr(cp, "enumerate_capture_pairs", empty_source)  # type: ignore[attr-defined]
    assert ring.enumerate_blast_ring(value, cp.WHITE) == ()
    assert calls == [(value, cp.WHITE)]


def test_reordered_and_duplicated_capture_pair_source_is_rejected_as_noncanonical(
    monkeypatch: object,
) -> None:
    value = position(
        piece(cp.WHITE, cp.KING, "h1"),
        piece(cp.BLACK, cp.KING, "h8"),
        piece(cp.WHITE, cp.ROOK, "d3"),
        piece(cp.WHITE, cp.ROOK, "c4"),
        piece(cp.BLACK, cp.PAWN, "d4"),
        piece(cp.BLACK, cp.BISHOP, "e4"),
    )
    candidates = cp.enumerate_capture_pairs(value, cp.WHITE)
    reordered = tuple(reversed(candidates)) + candidates + tuple(reversed(candidates))
    monkeypatch.setattr(cp, "enumerate_capture_pairs", lambda *_: reordered)  # type: ignore[attr-defined]
    with pytest.raises(ring.BlastRingContractError, match="canonical order"):
        ring.enumerate_blast_ring(value, cp.WHITE)


def test_output_is_boolean_sorted_unique_and_within_the_240_row_bound() -> None:
    rng = random.Random(0xA70C_3E40)
    observed_max = 0
    for _ in range(256):
        squares = rng.sample(range(64), 32)
        white_count = rng.randrange(0, 16)
        black_count = rng.randrange(0, 16)
        pieces = [
            cp.Piece(cp.WHITE, cp.KING, squares[0]),
            cp.Piece(cp.BLACK, cp.KING, squares[1]),
        ]
        cursor = 2
        for _ in range(white_count):
            pieces.append(
                cp.Piece(cp.WHITE, rng.choice(cp.ACTOR_KINDS), squares[cursor])
            )
            cursor += 1
        for _ in range(black_count):
            pieces.append(
                cp.Piece(cp.BLACK, rng.choice(cp.ACTOR_KINDS), squares[cursor])
            )
            cursor += 1
        value = cp.CapturePosition(tuple(pieces), side_to_move=rng.choice(cp.COLORS))
        for perspective in cp.COLORS:
            rows = ring.enumerate_blast_ring(value, perspective)
            indices = [row.local_index for row in rows]
            observed_max = max(observed_max, len(rows))
            assert indices == sorted(indices)
            assert len(indices) == len(set(indices)) <= ring.MAX_ACTIVE_FEATURES
            orientation = cp.orientation_for(value, perspective)
            for row in rows:
                assert 0 <= row.local_index < ring.PHYSICAL_DIMENSIONS
                assert row.physical_index == ring.PHYSICAL_OFFSET + row.local_index
                assert orientation.orient(row.raw_center) == row.oriented_center
                assert orientation.orient(row.raw_collateral) == row.oriented_collateral
                assert (
                    ring.adjacent_direction(
                        row.oriented_center, row.oriented_collateral
                    )
                    == row.offset
                )
                collateral = next(
                    item for item in value.pieces if item.square == row.raw_collateral
                )
                assert collateral.kind != cp.KING
                assert row.collateral_rel == cp.actor_relation(
                    collateral.color, perspective
                )
    assert observed_max > 0


@pytest.mark.parametrize(
    ("function", "arguments", "message"),
    (
        (
            ring.blast_ring_index,
            (-1, ring.OWN, ring.OWN, "N", ring.KNIGHT),
            "0..63",
        ),
        (
            ring.blast_ring_index,
            (True, ring.OWN, ring.OWN, "N", ring.KNIGHT),
            "integer",
        ),
        (
            ring.blast_ring_index,
            (0, "SIDE", ring.OWN, "N", ring.KNIGHT),
            "actor_rel",
        ),
        (
            ring.blast_ring_index,
            (0, ring.OWN, "SIDE", "N", ring.KNIGHT),
            "collateral_rel",
        ),
        (
            ring.blast_ring_index,
            (0, ring.OWN, ring.OWN, "UP", ring.KNIGHT),
            "offset",
        ),
        (
            ring.blast_ring_index,
            (0, ring.OWN, ring.OWN, "N", cp.KING),
            "collateral_class",
        ),
        (ring.directional_square, (0, "UP"), "offset"),
        (ring.adjacent_direction, (0, 64), "0..63"),
    ),
)
def test_index_and_direction_helpers_fail_closed(
    function: object, arguments: tuple[object, ...], message: str
) -> None:
    with pytest.raises(ring.BlastRingContractError, match=message):
        function(*arguments)  # type: ignore[operator]


def test_projection_domain_missing_king_and_inconsistent_candidate_fail_closed(
    monkeypatch: object,
) -> None:
    with pytest.raises(ring.BlastRingContractError, match="CapturePosition"):
        ring.enumerate_blast_ring(object(), cp.WHITE)  # type: ignore[arg-type]
    kings_only = position(
        piece(cp.WHITE, cp.KING, "h1"),
        piece(cp.BLACK, cp.KING, "h8"),
    )
    with pytest.raises(ring.BlastRingContractError, match="perspective"):
        ring.enumerate_blast_ring(kings_only, "SIDE")

    missing_black = object.__new__(cp.CapturePosition)
    object.__setattr__(
        missing_black,
        "pieces",
        (piece(cp.WHITE, cp.KING, "e1"),),
    )
    object.__setattr__(missing_black, "side_to_move", cp.WHITE)
    object.__setattr__(missing_black, "ep_square", None)
    object.__setattr__(missing_black, "atomic960", False)
    object.__setattr__(missing_black, "castling_rights", "-")
    with pytest.raises(ring.BlastRingContractError, match="exactly one black king"):
        ring.enumerate_blast_ring(missing_black, cp.WHITE)

    value = position(
        piece(cp.WHITE, cp.KING, "h1"),
        piece(cp.BLACK, cp.KING, "h8"),
        piece(cp.WHITE, cp.KNIGHT, "b3"),
        piece(cp.BLACK, cp.PAWN, "d4"),
        piece(cp.BLACK, cp.BISHOP, "d5"),
    )
    candidates = cp.enumerate_capture_pairs(value, cp.WHITE)
    selected = next(row for row in candidates if row.raw_to == cp.square("d4"))
    forged = replace(selected, actor_rel=cp.OPP)
    monkeypatch.setattr(cp, "enumerate_capture_pairs", lambda *_: (forged,))  # type: ignore[attr-defined]
    with pytest.raises(ring.BlastRingContractError, match="actor relation"):
        ring.enumerate_blast_ring(value, cp.WHITE)


def test_forged_ep_center_must_match_position_metadata(monkeypatch: object) -> None:
    value = position(
        piece(cp.WHITE, cp.KING, "h1"),
        piece(cp.BLACK, cp.KING, "a8"),
        piece(cp.WHITE, cp.PAWN, "d5"),
        piece(cp.BLACK, cp.PAWN, "e5"),
        side_to_move=cp.WHITE,
        ep_square="e6",
    )
    candidate = next(
        row for row in cp.enumerate_capture_pairs(value, cp.WHITE) if row.en_passant
    )
    no_ep = position(*value.pieces, side_to_move=cp.WHITE)
    monkeypatch.setattr(cp, "enumerate_capture_pairs", lambda *_: (candidate,))  # type: ignore[attr-defined]
    with pytest.raises(ring.BlastRingContractError, match="EP candidate"):
        ring.enumerate_blast_ring(no_ep, cp.WHITE)


def test_bound_violation_fails_without_returning_partial_projection(
    monkeypatch: object,
) -> None:
    value = position(
        piece(cp.WHITE, cp.KING, "h1"),
        piece(cp.BLACK, cp.KING, "h8"),
        piece(cp.WHITE, cp.KNIGHT, "b3"),
        piece(cp.BLACK, cp.PAWN, "d4"),
        piece(cp.BLACK, cp.BISHOP, "d5"),
    )
    monkeypatch.setattr(ring, "MAX_ACTIVE_FEATURES", 0)  # type: ignore[attr-defined]
    with pytest.raises(ring.BlastRingContractError, match="240-row"):
        ring.enumerate_blast_ring(value, cp.WHITE)


@pytest.mark.parametrize(
    "filename",
    (
        "atomic_v3_blast_ring_reference.py",
        "test_atomic_v3_blast_ring_oracle.py",
    ),
)
def test_blast_ring_python_parses_with_python_3_9_grammar(filename: str) -> None:
    source = (PYTHON_TESTS / filename).read_text(encoding="utf-8")
    ast.parse(source, filename=filename, feature_version=9)
