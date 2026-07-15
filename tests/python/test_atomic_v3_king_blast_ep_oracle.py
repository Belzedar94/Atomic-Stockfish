#!/usr/bin/env python3
"""Exhaustive, golden and metamorphic gates for the V3 KingBlastEP oracle."""

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

import atomic_v3_capture_pair_reference as cp  # noqa: E402
import atomic_v3_king_blast_ep_reference as kb  # noqa: E402


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
) -> tuple[kb.KingBlastEPActivation, ...]:
    raw_center = cp.square(center)
    return tuple(
        row
        for row in kb.enumerate_king_blast_ep(value, perspective)
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
            row.relation_class,
        )
        for row in kb.enumerate_king_blast_ep(value, perspective)
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


def test_dimensions_class_order_and_offsets_are_frozen() -> None:
    assert kb.PHYSICAL_OFFSET == 62_540
    assert kb.CENTER_DIMENSIONS == 64
    assert kb.ACTOR_RELATIONS == (kb.OWN, kb.OPP)
    assert kb.DIRECTION_ORDER == ("N", "NE", "E", "SE", "S", "SW", "W", "NW")
    assert kb.DIRECTION_DELTAS == {
        "N": 8,
        "NE": 9,
        "E": 1,
        "SE": -7,
        "S": -8,
        "SW": -9,
        "W": -1,
        "NW": 7,
    }
    assert kb.CLASS_ORDER == (
        (kb.ENEMY_KING_CENTER,)
        + tuple(f"ENEMY_KING_{direction}" for direction in kb.DIRECTION_ORDER)
        + tuple(f"OWN_KING_{direction}" for direction in kb.DIRECTION_ORDER)
        + (kb.EN_PASSANT_MARKER,)
    )
    assert kb.CLASS_DIMENSIONS == 18
    assert kb.PHYSICAL_DIMENSIONS == kb.TRAINING_DIMENSIONS == 2_304
    assert kb.MAX_ACTIVE_FEATURES == 35


def test_all_2_304_indices_are_dense_unique_and_formula_exact() -> None:
    indices = []
    for center in range(64):
        for relation_index, actor_rel in enumerate(kb.ACTOR_RELATIONS):
            for class_index, relation_class in enumerate(kb.CLASS_ORDER):
                expected = ((center * 2 + relation_index) * 18 + class_index)
                actual = kb.king_blast_ep_index(center, actor_rel, relation_class)
                assert actual == expected
                indices.append(actual)
                activation = kb.KingBlastEPActivation(
                    actual,
                    center,
                    center,
                    actor_rel,
                    relation_class,
                )
                assert activation.local_index == actual
                assert activation.physical_index == kb.PHYSICAL_OFFSET + actual
                assert activation.en_passant == (
                    relation_class == kb.EN_PASSANT_MARKER
                )
    assert indices == list(range(kb.PHYSICAL_DIMENSIONS))


@pytest.mark.parametrize(
    ("direction", "expected_name"),
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
    direction: str, expected_name: str
) -> None:
    center = cp.square("d4")
    related = kb.directional_square(center, direction)
    assert related == cp.square(expected_name)
    assert related == center + kb.DIRECTION_DELTAS[direction]
    assert kb.adjacent_direction(center, related) == direction


def test_direction_helpers_reject_file_wrap_and_non_adjacent_squares() -> None:
    assert kb.directional_square(cp.square("a1"), "W") is None
    assert kb.directional_square(cp.square("a1"), "SW") is None
    assert kb.directional_square(cp.square("h8"), "NE") is None
    assert kb.adjacent_direction(cp.square("a1"), cp.square("h1")) is None
    assert kb.adjacent_direction(cp.square("d4"), cp.square("d6")) is None
    assert kb.adjacent_direction(cp.square("d4"), cp.square("d4")) is None


def _direction_position(direction: str, enemy: bool) -> cp.CapturePosition:
    related = kb.directional_square(cp.square("d4"), direction)
    assert related is not None
    related_name = cp.square_name(related)
    if enemy:
        return position(
            piece(cp.WHITE, cp.KING, "h1"),
            piece(cp.BLACK, cp.KING, related_name),
            piece(cp.WHITE, cp.KNIGHT, "b3"),
            piece(cp.BLACK, cp.PAWN, "d4"),
        )
    return position(
        piece(cp.WHITE, cp.KING, "h1"),
        piece(cp.BLACK, cp.KING, related_name),
        piece(cp.BLACK, cp.KNIGHT, "b3"),
        piece(cp.WHITE, cp.PAWN, "d4"),
    )


@pytest.mark.parametrize("direction", kb.DIRECTION_ORDER)
def test_all_enemy_direction_classes_have_exact_d4_numeric_goldens(
    direction: str,
) -> None:
    value = _direction_position(direction, enemy=True)
    selected = rows_at(value, cp.WHITE, "d4", kb.OWN)
    relation_class = f"ENEMY_KING_{direction}"
    assert [row.relation_class for row in selected] == [relation_class]
    expected = ((cp.square("d4") * 2 + 0) * 18) + kb.CLASS_ORDER.index(
        relation_class
    )
    assert selected[0].index == expected
    assert selected[0].physical_index == kb.PHYSICAL_OFFSET + expected


@pytest.mark.parametrize("direction", kb.DIRECTION_ORDER)
def test_all_own_direction_classes_are_actor_relative_and_have_d4_goldens(
    direction: str,
) -> None:
    value = _direction_position(direction, enemy=False)
    selected = rows_at(value, cp.WHITE, "d4", kb.OPP)
    relation_class = f"OWN_KING_{direction}"
    assert [row.relation_class for row in selected] == [relation_class]
    expected = ((cp.square("d4") * 2 + 1) * 18) + kb.CLASS_ORDER.index(
        relation_class
    )
    assert selected[0].index == expected
    assert selected[0].physical_index == kb.PHYSICAL_OFFSET + expected


def test_direct_king_target_uses_enemy_center_class_and_exact_index() -> None:
    value = position(
        piece(cp.WHITE, cp.KING, "h1"),
        piece(cp.BLACK, cp.KING, "d4"),
        piece(cp.WHITE, cp.KNIGHT, "b3"),
    )
    selected = rows_at(value, cp.WHITE, "d4", kb.OWN)
    assert [row.relation_class for row in selected] == [kb.ENEMY_KING_CENTER]
    expected = (cp.square("d4") * 2) * 18
    assert selected[0].index == expected == 972
    assert selected[0].physical_index == 63_512


def test_direct_king_target_can_coexist_with_adjacent_own_king_row() -> None:
    value = position(
        piece(cp.WHITE, cp.KING, "e4"),
        piece(cp.BLACK, cp.KING, "d4"),
        piece(cp.WHITE, cp.KNIGHT, "b3"),
    )
    selected = rows_at(value, cp.WHITE, "d4", kb.OWN)
    assert [row.relation_class for row in selected] == [
        kb.ENEMY_KING_CENTER,
        kb.OWN_KING_E,
    ]


def test_two_attackers_deduplicate_one_boolean_center_relation_class() -> None:
    value = position(
        piece(cp.WHITE, cp.KING, "h1"),
        piece(cp.BLACK, cp.KING, "d5"),
        piece(cp.WHITE, cp.KNIGHT, "b3"),
        piece(cp.WHITE, cp.KNIGHT, "f3"),
        piece(cp.BLACK, cp.PAWN, "d4"),
    )
    cp_candidates = [
        row
        for row in cp.enumerate_capture_pairs(value, cp.WHITE)
        if row.raw_to == cp.square("d4") and row.actor_rel == cp.OWN
    ]
    assert len(cp_candidates) == 2
    selected = rows_at(value, cp.WHITE, "d4", kb.OWN)
    assert [row.relation_class for row in selected] == ["ENEMY_KING_N"]


def test_simultaneous_enemy_and_own_blast_rows_are_both_retained() -> None:
    value = position(
        piece(cp.WHITE, cp.KING, "e4"),
        piece(cp.BLACK, cp.KING, "d5"),
        piece(cp.BLACK, cp.KNIGHT, "b3"),
        piece(cp.WHITE, cp.PAWN, "d4"),
    )
    selected = rows_at(value, cp.WHITE, "d4", kb.OPP)
    assert [row.relation_class for row in selected] == [
        "ENEMY_KING_E",
        "OWN_KING_N",
    ]


def test_touching_kings_are_accepted_but_never_become_capture_actors() -> None:
    value = position(
        piece(cp.WHITE, cp.KING, "e4"),
        piece(cp.BLACK, cp.KING, "e5"),
    )
    assert kb.enumerate_king_blast_ep(value, cp.WHITE) == ()
    assert kb.enumerate_king_blast_ep(value, cp.BLACK) == ()


def test_valid_white_ep_projects_marker_and_adjacent_enemy_king() -> None:
    value = position(
        piece(cp.WHITE, cp.KING, "h1"),
        piece(cp.BLACK, cp.KING, "e7"),
        piece(cp.WHITE, cp.PAWN, "d5"),
        piece(cp.BLACK, cp.PAWN, "e5"),
        side_to_move=cp.WHITE,
        ep_square="e6",
    )
    own = rows_at(value, cp.WHITE, "e6", kb.OWN)
    assert [row.relation_class for row in own] == [
        "ENEMY_KING_N",
        kb.EN_PASSANT_MARKER,
    ]
    marker_expected = (
        (cp.square("e6") * 2) * 18 + kb.CLASS_ORDER.index(kb.EN_PASSANT_MARKER)
    )
    assert own[-1].index == marker_expected

    # The capture actor is still WHITE from BLACK's accumulator perspective:
    # actor_rel becomes OPP, while enemy remains the BLACK king.  The shared
    # BLACK orientation maps that king south of the center.
    opp = rows_at(value, cp.BLACK, "e6", kb.OPP)
    assert [row.relation_class for row in opp] == [
        "ENEMY_KING_S",
        kb.EN_PASSANT_MARKER,
    ]


def test_valid_black_ep_projects_marker_in_both_perspectives() -> None:
    value = position(
        piece(cp.WHITE, cp.KING, "e2"),
        piece(cp.BLACK, cp.KING, "h8"),
        piece(cp.BLACK, cp.PAWN, "d4"),
        piece(cp.WHITE, cp.PAWN, "e4"),
        side_to_move=cp.BLACK,
        ep_square="e3",
    )
    black_rows = rows_at(value, cp.BLACK, "e3", kb.OWN)
    assert [row.relation_class for row in black_rows] == [
        "ENEMY_KING_N",
        kb.EN_PASSANT_MARKER,
    ]
    white_rows = rows_at(value, cp.WHITE, "e3", kb.OPP)
    assert [row.relation_class for row in white_rows] == [
        "ENEMY_KING_S",
        kb.EN_PASSANT_MARKER,
    ]


def test_two_ep_origins_deduplicate_to_one_landing_square_marker() -> None:
    value = position(
        piece(cp.WHITE, cp.KING, "h1"),
        piece(cp.BLACK, cp.KING, "e7"),
        piece(cp.WHITE, cp.PAWN, "d5"),
        piece(cp.WHITE, cp.PAWN, "f5"),
        piece(cp.BLACK, cp.PAWN, "e5"),
        side_to_move=cp.WHITE,
        ep_square="e6",
    )
    candidates = [
        row
        for row in cp.enumerate_capture_pairs(value, cp.WHITE)
        if row.en_passant
    ]
    assert len(candidates) == 2
    assert {row.raw_from for row in candidates} == {cp.square("d5"), cp.square("f5")}
    assert {row.raw_to for row in candidates} == {cp.square("e6")}
    assert {row.raw_captured for row in candidates} == {cp.square("e5")}

    for perspective in cp.COLORS:
        markers = [
            row
            for row in kb.enumerate_king_blast_ep(value, perspective)
            if row.relation_class == kb.EN_PASSANT_MARKER
        ]
        assert len(markers) == 1
        assert markers[0].raw_center == cp.square("e6")
        assert markers[0].raw_center != cp.square("e5")


def test_pinned_and_atomic_illegal_self_blast_candidates_are_retained() -> None:
    pinned = position(
        piece(cp.WHITE, cp.KING, "e1"),
        piece(cp.BLACK, cp.KING, "g5"),
        piece(cp.WHITE, cp.KNIGHT, "e2"),
        piece(cp.BLACK, cp.ROOK, "e8"),
        piece(cp.BLACK, cp.PAWN, "f4"),
    )
    assert [
        row.relation_class for row in rows_at(pinned, cp.WHITE, "f4", kb.OWN)
    ] == [kb.ENEMY_KING_NE]

    self_blast = position(
        piece(cp.WHITE, cp.KING, "f3"),
        piece(cp.BLACK, cp.KING, "h8"),
        piece(cp.WHITE, cp.QUEEN, "d4"),
        piece(cp.BLACK, cp.PAWN, "e4"),
    )
    assert [
        row.relation_class
        for row in rows_at(self_blast, cp.WHITE, "e4", kb.OWN)
    ] == [kb.OWN_KING_SE]


def _malformed_ep_position(actor_color: str, case: str) -> tuple[cp.CapturePosition, str]:
    if actor_color == cp.WHITE:
        items = [
            piece(cp.WHITE, cp.KING, "h1"),
            piece(cp.BLACK, cp.KING, "a6"),
            piece(cp.WHITE, cp.KNIGHT, "c3"),
            piece(cp.BLACK, cp.ROOK, "b5"),
            piece(
                cp.WHITE,
                cp.ROOK if case == "origin-replaced" else cp.PAWN,
                "d5",
            ),
        ]
        if case != "missing-off-center":
            off_kind = cp.ROOK if case == "off-center-not-pawn" else cp.PAWN
            off_color = cp.WHITE if case == "off-center-friendly" else cp.BLACK
            items.append(piece(off_color, off_kind, "e5"))
        if case == "occupied-center":
            items.append(piece(cp.BLACK, cp.BISHOP, "e6"))
        ep_square = "e3" if case == "wrong-rank" else "e6"
        return (
            position(*items, side_to_move=cp.WHITE, ep_square=ep_square),
            "b5",
        )

    items = [
        piece(cp.WHITE, cp.KING, "a3"),
        piece(cp.BLACK, cp.KING, "h8"),
        piece(cp.BLACK, cp.KNIGHT, "c6"),
        piece(cp.WHITE, cp.ROOK, "b4"),
        piece(
            cp.BLACK,
            cp.ROOK if case == "origin-replaced" else cp.PAWN,
            "d4",
        ),
    ]
    if case != "missing-off-center":
        off_kind = cp.ROOK if case == "off-center-not-pawn" else cp.PAWN
        off_color = cp.BLACK if case == "off-center-friendly" else cp.WHITE
        items.append(piece(off_color, off_kind, "e4"))
    if case == "occupied-center":
        items.append(piece(cp.WHITE, cp.BISHOP, "e3"))
    ep_square = "e6" if case == "wrong-rank" else "e3"
    return position(*items, side_to_move=cp.BLACK, ep_square=ep_square), "b4"


@pytest.mark.parametrize("actor_color", cp.COLORS)
@pytest.mark.parametrize(
    "case",
    (
        "wrong-rank",
        "occupied-center",
        "missing-off-center",
        "off-center-not-pawn",
        "off-center-friendly",
        "origin-replaced",
    ),
)
def test_malformed_ep_fails_closed_but_preserves_normal_projection(
    actor_color: str, case: str
) -> None:
    value, normal_center = _malformed_ep_position(actor_color, case)
    baseline = cp.CapturePosition(
        value.pieces,
        side_to_move=value.side_to_move,
        ep_square=None,
    )
    for perspective in cp.COLORS:
        rows = kb.enumerate_king_blast_ep(value, perspective)
        assert not any(row.relation_class == kb.EN_PASSANT_MARKER for row in rows)
        assert any(row.raw_center == cp.square(normal_center) for row in rows)
        assert compact_signature(value, perspective) == compact_signature(
            baseline, perspective
        )


def test_ep_marker_comes_only_from_capture_pair_validated_tail(monkeypatch: object) -> None:
    value = position(
        piece(cp.WHITE, cp.KING, "h1"),
        piece(cp.BLACK, cp.KING, "e7"),
        piece(cp.WHITE, cp.PAWN, "d5"),
        piece(cp.BLACK, cp.PAWN, "e5"),
        side_to_move=cp.WHITE,
        ep_square="e6",
    )
    original = cp.enumerate_capture_pairs(value, cp.WHITE)
    without_ep = tuple(candidate for candidate in original if not candidate.en_passant)
    monkeypatch.setattr(cp, "enumerate_capture_pairs", lambda *_: without_ep)  # type: ignore[attr-defined]
    rows = kb.enumerate_king_blast_ep(value, cp.WHITE)
    assert not any(row.relation_class == kb.EN_PASSANT_MARKER for row in rows)


def test_capture_pair_is_the_sole_candidate_source(monkeypatch: object) -> None:
    value = _direction_position("N", enemy=True)
    assert rows_at(value, cp.WHITE, "d4", kb.OWN)
    calls = []

    def empty_source(*arguments: object) -> tuple[object, ...]:
        calls.append(arguments)
        return ()

    monkeypatch.setattr(cp, "enumerate_capture_pairs", empty_source)  # type: ignore[attr-defined]
    assert kb.enumerate_king_blast_ep(value, cp.WHITE) == ()
    assert calls == [(value, cp.WHITE)]


def test_reordered_and_duplicated_capture_pair_source_stays_sorted_unique(
    monkeypatch: object,
) -> None:
    value = position(
        piece(cp.WHITE, cp.KING, "e4"),
        piece(cp.BLACK, cp.KING, "d5"),
        piece(cp.BLACK, cp.KNIGHT, "b3"),
        piece(cp.WHITE, cp.PAWN, "d4"),
        piece(cp.WHITE, cp.ROOK, "a4"),
        piece(cp.BLACK, cp.BISHOP, "a6"),
    )
    baseline = kb.enumerate_king_blast_ep(value, cp.WHITE)
    candidates = cp.enumerate_capture_pairs(value, cp.WHITE)
    reordered = tuple(reversed(candidates)) + candidates + tuple(reversed(candidates))
    monkeypatch.setattr(cp, "enumerate_capture_pairs", lambda *_: reordered)  # type: ignore[attr-defined]
    actual = kb.enumerate_king_blast_ep(value, cp.WHITE)
    assert actual == baseline
    indices = [row.local_index for row in actual]
    assert indices == sorted(set(indices))


def test_promotion_candidate_is_not_multiplied_and_current_type_is_history_free() -> None:
    pawn_position = position(
        piece(cp.WHITE, cp.KING, "h1"),
        piece(cp.BLACK, cp.KING, "g8"),
        piece(cp.WHITE, cp.PAWN, "g7"),
        piece(cp.BLACK, cp.ROOK, "h8"),
    )
    promoted_position = position(
        piece(cp.WHITE, cp.KING, "h1"),
        piece(cp.BLACK, cp.KING, "g8"),
        piece(cp.WHITE, cp.QUEEN, "g7"),
        piece(cp.BLACK, cp.ROOK, "h8"),
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
    assert rows_at(pawn_position, cp.WHITE, "h8", kb.OWN) == rows_at(
        promoted_position, cp.WHITE, "h8", kb.OWN
    )


def test_horizontal_mirror_preserves_joint_oriented_signature() -> None:
    original = position(
        piece(cp.WHITE, cp.KING, "c1"),
        piece(cp.BLACK, cp.KING, "b6"),
        piece(cp.WHITE, cp.ROOK, "a2"),
        piece(cp.BLACK, cp.PAWN, "a5"),
    )
    mirrored = horizontal_mirror(original)
    for perspective in cp.COLORS:
        assert compact_signature(original, perspective) == compact_signature(
            mirrored, perspective
        )


def test_color_vertical_mirror_and_perspective_swap_preserve_signature() -> None:
    original = position(
        piece(cp.WHITE, cp.KING, "f1"),
        piece(cp.BLACK, cp.KING, "e5"),
        piece(cp.WHITE, cp.KNIGHT, "b3"),
        piece(cp.BLACK, cp.PAWN, "d4"),
    )
    mirrored = color_vertical_mirror(original)
    assert compact_signature(original, cp.WHITE) == compact_signature(
        mirrored, cp.BLACK
    )


def test_actor_relative_king_semantics_survive_accumulator_perspective_change() -> None:
    value = position(
        piece(cp.WHITE, cp.KING, "h1"),
        piece(cp.BLACK, cp.KING, "e6"),
        piece(cp.WHITE, cp.KNIGHT, "c3"),
        piece(cp.BLACK, cp.PAWN, "d5"),
    )
    white_rows = rows_at(value, cp.WHITE, "d5", kb.OWN)
    black_rows = rows_at(value, cp.BLACK, "d5", kb.OPP)
    assert [row.relation_class for row in white_rows] == ["ENEMY_KING_NE"]
    assert [row.relation_class for row in black_rows] == ["ENEMY_KING_SE"]


def test_atomic960_and_castling_metadata_are_projection_neutral() -> None:
    pieces = (
        piece(cp.WHITE, cp.KING, "b1"),
        piece(cp.BLACK, cp.KING, "e6"),
        piece(cp.WHITE, cp.QUEEN, "d4"),
        piece(cp.BLACK, cp.ROOK, "d5"),
    )
    standard = position(*pieces, atomic960=False, castling_rights="KQkq")
    atomic960 = position(*pieces, atomic960=True, castling_rights="BGbg")
    for perspective in cp.COLORS:
        assert compact_signature(standard, perspective) == compact_signature(
            atomic960, perspective
        )


def test_output_is_boolean_sorted_unique_and_within_the_35_row_bound() -> None:
    rng = random.Random(0xA70C_3D35)
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
                cp.Piece(
                    cp.WHITE,
                    rng.choice(cp.ACTOR_KINDS),
                    squares[cursor],
                )
            )
            cursor += 1
        for _ in range(black_count):
            pieces.append(
                cp.Piece(
                    cp.BLACK,
                    rng.choice(cp.ACTOR_KINDS),
                    squares[cursor],
                )
            )
            cursor += 1
        value = cp.CapturePosition(tuple(pieces), side_to_move=rng.choice(cp.COLORS))
        for perspective in cp.COLORS:
            rows = kb.enumerate_king_blast_ep(value, perspective)
            indices = [row.index for row in rows]
            observed_max = max(observed_max, len(rows))
            assert indices == sorted(indices)
            assert len(indices) == len(set(indices)) <= kb.MAX_ACTIVE_FEATURES
            assert all(0 <= index < kb.PHYSICAL_DIMENSIONS for index in indices)
            assert all(
                row.physical_index == kb.PHYSICAL_OFFSET + row.local_index
                for row in rows
            )
    assert observed_max > 0


@pytest.mark.parametrize(
    ("function", "arguments", "message"),
    (
        (kb.king_blast_ep_index, (-1, kb.OWN, kb.ENEMY_KING_CENTER), "0..63"),
        (kb.king_blast_ep_index, (True, kb.OWN, kb.ENEMY_KING_CENTER), "integer"),
        (kb.king_blast_ep_index, (0, "SIDE", kb.ENEMY_KING_CENTER), "actor_rel"),
        (kb.king_blast_ep_index, (0, kb.OWN, "KING"), "relation_class"),
        (kb.directional_square, (0, "UP"), "direction"),
        (kb.adjacent_direction, (0, 64), "0..63"),
    ),
)
def test_index_and_direction_helpers_fail_closed(
    function: object, arguments: tuple[object, ...], message: str
) -> None:
    with pytest.raises(kb.KingBlastEPContractError, match=message):
        function(*arguments)  # type: ignore[operator]


def test_projection_domain_and_inconsistent_candidate_fail_closed(
    monkeypatch: object,
) -> None:
    with pytest.raises(kb.KingBlastEPContractError, match="CapturePosition"):
        kb.enumerate_king_blast_ep(object(), cp.WHITE)  # type: ignore[arg-type]
    kings_only = position(
        piece(cp.WHITE, cp.KING, "h1"),
        piece(cp.BLACK, cp.KING, "h8"),
    )
    with pytest.raises(kb.KingBlastEPContractError, match="perspective"):
        kb.enumerate_king_blast_ep(kings_only, "SIDE")

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
    with pytest.raises(kb.KingBlastEPContractError, match="exactly one black king"):
        kb.enumerate_king_blast_ep(missing_black, cp.WHITE)

    value = _direction_position("N", enemy=True)
    candidates = cp.enumerate_capture_pairs(value, cp.WHITE)
    selected = next(row for row in candidates if row.raw_to == cp.square("d4"))
    forged = replace(selected, actor_rel=cp.OPP)
    monkeypatch.setattr(cp, "enumerate_capture_pairs", lambda *_: (forged,))  # type: ignore[attr-defined]
    with pytest.raises(kb.KingBlastEPContractError, match="actor relation"):
        kb.enumerate_king_blast_ep(value, cp.WHITE)


def test_bound_violation_fails_without_returning_a_partial_projection(
    monkeypatch: object,
) -> None:
    value = _direction_position("N", enemy=True)
    monkeypatch.setattr(kb, "MAX_ACTIVE_FEATURES", 0)  # type: ignore[attr-defined]
    with pytest.raises(kb.KingBlastEPContractError, match="35-row"):
        kb.enumerate_king_blast_ep(value, cp.WHITE)


@pytest.mark.parametrize(
    "filename",
    (
        "atomic_v3_king_blast_ep_reference.py",
        "test_atomic_v3_king_blast_ep_oracle.py",
    ),
)
def test_king_blast_ep_python_parses_with_python_3_9_grammar(filename: str) -> None:
    source = (PYTHON_TESTS / filename).read_text(encoding="utf-8")
    tree = ast.parse(source, feature_version=9)
    assert tree.body
