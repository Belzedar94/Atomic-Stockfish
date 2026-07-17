#!/usr/bin/env python3
"""Exhaustive and metamorphic gates for the compact V3 CapturePair oracle."""

from __future__ import annotations

import ast
from collections import Counter
from pathlib import Path
import random
import sys

import pytest


ROOT = Path(__file__).resolve().parents[2]
PYTHON_TESTS = ROOT / "tests" / "python"
if str(PYTHON_TESTS) not in sys.path:
    sys.path.insert(0, str(PYTHON_TESTS))

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


def activations_from(
    value: cp.CapturePosition, perspective: str, origin: str
) -> tuple[cp.CapturePairActivation, ...]:
    raw_origin = cp.square(origin)
    return tuple(
        activation
        for activation in cp.enumerate_capture_pairs(value, perspective)
        if activation.raw_from == raw_origin
    )


def horizontal_mirror(value: cp.CapturePosition) -> cp.CapturePosition:
    return cp.CapturePosition(
        tuple(
            cp.Piece(item.color, item.kind, item.square ^ 7)
            for item in value.pieces
        ),
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
        side_to_move=(
            cp.BLACK if value.side_to_move == cp.WHITE else cp.WHITE
        ),
        ep_square=None if value.ep_square is None else value.ep_square ^ 56,
        atomic960=value.atomic960,
        castling_rights=value.castling_rights,
    )


def compact_signature(
    value: cp.CapturePosition, perspective: str
) -> tuple[tuple[object, ...], ...]:
    return tuple(
        (
            activation.index,
            activation.actor_rel,
            activation.actor_kind,
            activation.oriented_from,
            activation.oriented_to,
            activation.oriented_captured,
            activation.edge_ordinal,
            activation.target_class,
            activation.en_passant,
        )
        for activation in cp.enumerate_capture_pairs(value, perspective)
    )


def test_compact_dimensions_and_segment_constants_are_frozen() -> None:
    assert cp.PHYSICAL_OFFSET == 22_528
    assert cp.GEOMETRY_DIMENSIONS == 3_332
    assert cp.GEOMETRY_SEGMENT_BASES == {
        cp.PAWN: 0,
        cp.KNIGHT: 84,
        cp.BISHOP: 420,
        cp.ROOK: 980,
        cp.QUEEN: 1_876,
    }
    assert cp.GEOMETRY_COUNTS == {
        cp.PAWN: 84,
        cp.KNIGHT: 336,
        cp.BISHOP: 560,
        cp.ROOK: 896,
        cp.QUEEN: 1_456,
    }
    assert cp.TARGET_CLASSES == (
        cp.PAWN,
        cp.KNIGHT,
        cp.BISHOP,
        cp.ROOK,
        cp.QUEEN,
        cp.KING,
    )
    assert cp.NORMAL_DIMENSIONS == 39_984
    assert cp.EP_DIMENSIONS == 28
    assert cp.PHYSICAL_DIMENSIONS == 40_012
    assert cp.MAX_ACTIVE_FEATURES == 240


def test_geometry_lookup_has_one_non_constexpr_runtime_definition() -> None:
    header = (ROOT / "src/nnue/atomic_v3/capture_pair.h").read_text(
        encoding="utf-8"
    )
    source = (ROOT / "src/nnue/atomic_v3/capture_pair.cpp").read_text(
        encoding="utf-8"
    )

    assert (
        "extern const CapturePairGeometryLookup CapturePairGeometry;" in header
    )
    assert "inline constexpr CapturePairGeometryLookup" not in header
    assert "make_capture_pair_geometry_lookup" not in header
    assert (
        source.count(
            "const CapturePairGeometryLookup CapturePairGeometry = "
            "make_capture_pair_geometry_lookup();"
        )
        == 1
    )
    builder = source.split(
        "CapturePairGeometryLookup make_capture_pair_geometry_lookup() noexcept",
        1,
    )
    assert len(builder) == 2
    assert not builder[0].rstrip().endswith("constexpr")


@pytest.mark.parametrize("actor_rel", cp.ACTOR_RELATIONS)
def test_every_geometry_edge_is_lexicographic_and_has_one_ordinal(
    actor_rel: str,
) -> None:
    all_ordinals: list[int] = []
    for actor_kind in cp.ACTOR_KINDS:
        edges = cp.geometry_edges(actor_kind, actor_rel)
        assert edges == tuple(sorted(edges))
        assert len(edges) == len(set(edges)) == cp.GEOMETRY_COUNTS[actor_kind]
        base = cp.GEOMETRY_SEGMENT_BASES[actor_kind]
        ordinals = tuple(
            cp.edge_ordinal(actor_kind, actor_rel, from_square, to_square)
            for from_square, to_square in edges
        )
        assert ordinals == tuple(range(base, base + len(edges)))
        all_ordinals.extend(ordinals)
    assert all_ordinals == list(range(cp.GEOMETRY_DIMENSIONS))


def test_all_39_984_normal_indices_are_dense_unique_and_formula_exact() -> None:
    indices: list[int] = []
    for relation_index, actor_rel in enumerate(cp.ACTOR_RELATIONS):
        for actor_kind in cp.ACTOR_KINDS:
            for from_square, to_square in cp.geometry_edges(actor_kind, actor_rel):
                ordinal = cp.edge_ordinal(
                    actor_kind, actor_rel, from_square, to_square
                )
                for target_index, target_class in enumerate(cp.TARGET_CLASSES):
                    expected = (
                        (relation_index * cp.GEOMETRY_DIMENSIONS + ordinal) * 6
                        + target_index
                    )
                    actual = cp.normal_index(
                        actor_rel,
                        actor_kind,
                        from_square,
                        to_square,
                        target_class,
                    )
                    assert actual == expected
                    indices.append(actual)
    assert indices == list(range(cp.NORMAL_DIMENSIONS))


def test_ep_tables_and_all_28_tail_indices_are_exact() -> None:
    own_names = (
        "a5-b6",
        "b5-a6",
        "b5-c6",
        "c5-b6",
        "c5-d6",
        "d5-c6",
        "d5-e6",
        "e5-d6",
        "e5-f6",
        "f5-e6",
        "f5-g6",
        "g5-f6",
        "g5-h6",
        "h5-g6",
    )
    opp_names = tuple(name.translate(str.maketrans("56", "43")) for name in own_names)
    expected_by_relation = {cp.OWN: own_names, cp.OPP: opp_names}

    all_indices = []
    for relation_index, actor_rel in enumerate(cp.ACTOR_RELATIONS):
        edges = cp.ep_edges(actor_rel)
        assert tuple(
            f"{cp.square_name(from_square)}-{cp.square_name(to_square)}"
            for from_square, to_square in edges
        ) == expected_by_relation[actor_rel]
        for ordinal, (from_square, center) in enumerate(edges):
            expected = cp.NORMAL_DIMENSIONS + relation_index * 14 + ordinal
            assert cp.en_passant_index(actor_rel, from_square, center) == expected
            all_indices.append(expected)
    assert all_indices == list(range(cp.NORMAL_DIMENSIONS, cp.PHYSICAL_DIMENSIONS))


def test_horizontal_mirror_keeps_joint_oriented_indices() -> None:
    original = position(
        piece(cp.WHITE, cp.KING, "c1"),
        piece(cp.BLACK, cp.KING, "a8"),
        piece(cp.WHITE, cp.ROOK, "a2"),
        piece(cp.BLACK, cp.KNIGHT, "a7"),
        piece(cp.WHITE, cp.PAWN, "d5"),
        piece(cp.BLACK, cp.PAWN, "e5"),
        ep_square="e6",
    )
    mirrored = horizontal_mirror(original)
    assert compact_signature(original, cp.WHITE) == compact_signature(
        mirrored, cp.WHITE
    )


def test_color_vertical_mirror_keeps_joint_oriented_indices() -> None:
    original = position(
        piece(cp.WHITE, cp.KING, "f1"),
        piece(cp.BLACK, cp.KING, "b8"),
        piece(cp.WHITE, cp.BISHOP, "c3"),
        piece(cp.BLACK, cp.QUEEN, "f6"),
        piece(cp.WHITE, cp.PAWN, "d5"),
        piece(cp.BLACK, cp.PAWN, "e5"),
        ep_square="e6",
    )
    mirrored = color_vertical_mirror(original)
    assert compact_signature(original, cp.WHITE) == compact_signature(
        mirrored, cp.BLACK
    )


def test_sliders_stop_at_first_enemy_and_never_emit_xrays() -> None:
    value = position(
        piece(cp.WHITE, cp.KING, "g1"),
        piece(cp.BLACK, cp.KING, "g8"),
        piece(cp.WHITE, cp.ROOK, "a1"),
        piece(cp.BLACK, cp.BISHOP, "a4"),
        piece(cp.BLACK, cp.QUEEN, "a6"),
    )
    rook_rows = activations_from(value, cp.WHITE, "a1")
    assert [(cp.square_name(row.raw_to), row.target_class) for row in rook_rows] == [
        ("a4", cp.BISHOP)
    ]


def test_friendly_slider_blocker_stops_ray_without_emission() -> None:
    value = position(
        piece(cp.WHITE, cp.KING, "g1"),
        piece(cp.BLACK, cp.KING, "g8"),
        piece(cp.WHITE, cp.BISHOP, "b1"),
        piece(cp.WHITE, cp.PAWN, "d3"),
        piece(cp.BLACK, cp.QUEEN, "f5"),
    )
    assert activations_from(value, cp.WHITE, "b1") == ()


@pytest.mark.parametrize("target_class", cp.TARGET_CLASSES)
def test_all_six_occupied_target_classes_are_encoded(target_class: str) -> None:
    black_target = piece(cp.BLACK, target_class, "d5")
    black_king = (
        black_target if target_class == cp.KING else piece(cp.BLACK, cp.KING, "h8")
    )
    extras = () if target_class == cp.KING else (black_target,)
    value = position(
        piece(cp.WHITE, cp.KING, "h1"),
        black_king,
        piece(cp.WHITE, cp.KNIGHT, "c3"),
        *extras,
    )
    rows = activations_from(value, cp.WHITE, "c3")
    selected = [row for row in rows if row.raw_to == cp.square("d5")]
    assert len(selected) == 1
    assert selected[0].target_class == target_class
    assert selected[0].index % 6 == cp.TARGET_CLASSES.index(target_class)


def test_enemy_on_pawn_push_square_is_not_a_capture_pair() -> None:
    value = position(
        piece(cp.WHITE, cp.KING, "a1"),
        piece(cp.BLACK, cp.KING, "h8"),
        piece(cp.WHITE, cp.PAWN, "e2"),
        piece(cp.BLACK, cp.ROOK, "e3"),
    )
    assert activations_from(value, cp.WHITE, "e2") == ()


def test_promotion_captures_emit_once_each_as_pawn_not_four_promotions() -> None:
    value = position(
        piece(cp.WHITE, cp.KING, "a1"),
        piece(cp.BLACK, cp.KING, "a8"),
        piece(cp.WHITE, cp.PAWN, "g7"),
        piece(cp.BLACK, cp.BISHOP, "f8"),
        piece(cp.BLACK, cp.ROOK, "h8"),
    )
    rows = activations_from(value, cp.WHITE, "g7")
    assert len(rows) == 2
    assert {cp.square_name(row.raw_to) for row in rows} == {"f8", "h8"}
    assert {row.actor_kind for row in rows} == {cp.PAWN}
    assert {row.target_class for row in rows} == {cp.BISHOP, cp.ROOK}
    assert not any(row.en_passant for row in rows)


def test_valid_ep_emits_two_distinct_origin_rows() -> None:
    value = position(
        piece(cp.WHITE, cp.KING, "h1"),
        piece(cp.BLACK, cp.KING, "h8"),
        piece(cp.WHITE, cp.PAWN, "d5"),
        piece(cp.WHITE, cp.PAWN, "f5"),
        piece(cp.BLACK, cp.PAWN, "e5"),
        side_to_move=cp.WHITE,
        ep_square="e6",
    )
    rows = [
        row for row in cp.enumerate_capture_pairs(value, cp.WHITE) if row.en_passant
    ]
    assert len(rows) == 2
    assert {cp.square_name(row.raw_from) for row in rows} == {"d5", "f5"}
    assert {cp.square_name(row.raw_to) for row in rows} == {"e6"}
    assert {cp.square_name(row.raw_captured) for row in rows} == {"e5"}
    assert len({row.index for row in rows}) == 2
    assert all(cp.NORMAL_DIMENSIONS <= row.index < cp.PHYSICAL_DIMENSIONS for row in rows)
    assert all(row.local_index == row.index for row in rows)
    assert all(row.physical_index == cp.PHYSICAL_OFFSET + row.index for row in rows)


def test_valid_black_ep_emits_two_distinct_origin_rows() -> None:
    value = position(
        piece(cp.WHITE, cp.KING, "a1"),
        piece(cp.BLACK, cp.KING, "h8"),
        piece(cp.BLACK, cp.PAWN, "c4"),
        piece(cp.BLACK, cp.PAWN, "e4"),
        piece(cp.WHITE, cp.PAWN, "d4"),
        side_to_move=cp.BLACK,
        ep_square="d3",
    )
    rows = [
        row for row in cp.enumerate_capture_pairs(value, cp.BLACK) if row.en_passant
    ]
    assert len(rows) == 2
    assert {cp.square_name(row.raw_from) for row in rows} == {"c4", "e4"}
    assert {cp.square_name(row.raw_to) for row in rows} == {"d3"}
    assert {cp.square_name(row.raw_captured) for row in rows} == {"d4"}
    assert all(row.actor_rel == cp.OWN for row in rows)


def test_ep_actor_relation_changes_with_perspective_not_with_stm() -> None:
    value = position(
        piece(cp.WHITE, cp.KING, "h1"),
        piece(cp.BLACK, cp.KING, "h8"),
        piece(cp.WHITE, cp.PAWN, "d5"),
        piece(cp.BLACK, cp.PAWN, "e5"),
        side_to_move=cp.WHITE,
        ep_square="e6",
    )
    own = [row for row in cp.enumerate_capture_pairs(value, cp.WHITE) if row.en_passant]
    opp = [row for row in cp.enumerate_capture_pairs(value, cp.BLACK) if row.en_passant]
    assert len(own) == len(opp) == 1
    assert own[0].actor_rel == cp.OWN
    assert opp[0].actor_rel == cp.OPP
    assert own[0].index < cp.NORMAL_DIMENSIONS + 14 <= opp[0].index


def test_opponent_pawn_attacking_ep_center_is_not_an_ep_actor() -> None:
    value = position(
        piece(cp.WHITE, cp.KING, "a1"),
        piece(cp.BLACK, cp.KING, "h8"),
        piece(cp.BLACK, cp.PAWN, "d7"),
        piece(cp.BLACK, cp.PAWN, "e5"),
        side_to_move=cp.WHITE,
        ep_square="e6",
    )
    assert not any(
        row.en_passant for row in cp.enumerate_capture_pairs(value, cp.WHITE)
    )


def _malformed_ep_position(case: str) -> cp.CapturePosition:
    items = [
        piece(cp.WHITE, cp.KING, "a1"),
        piece(cp.BLACK, cp.KING, "h8"),
        piece(
            cp.WHITE,
            cp.ROOK if case == "origin-replaced" else cp.PAWN,
            "d5",
        ),
        piece(cp.WHITE, cp.KNIGHT, "c3"),
        piece(cp.BLACK, cp.ROOK, "b4"),
    ]
    if case != "missing-off-center":
        off_kind = cp.ROOK if case == "off-center-not-pawn" else cp.PAWN
        off_color = cp.WHITE if case == "off-center-friendly" else cp.BLACK
        items.append(piece(off_color, off_kind, "e5"))
    if case == "occupied-center":
        items.append(piece(cp.BLACK, cp.BISHOP, "e6"))
    ep = "e3" if case == "wrong-rank" else "e6"
    return position(*items, side_to_move=cp.WHITE, ep_square=ep)


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
def test_malformed_ep_fails_closed_without_partial_ep_indices(case: str) -> None:
    value = _malformed_ep_position(case)
    rows = cp.enumerate_capture_pairs(value, cp.WHITE)
    assert not any(row.en_passant for row in rows)
    assert not any(row.index >= cp.NORMAL_DIMENSIONS for row in rows)


def test_malformed_ep_does_not_suppress_unrelated_normal_captures() -> None:
    value = position(
        piece(cp.WHITE, cp.KING, "a1"),
        piece(cp.BLACK, cp.KING, "h8"),
        piece(cp.WHITE, cp.KNIGHT, "c3"),
        piece(cp.BLACK, cp.ROOK, "d5"),
        side_to_move=cp.WHITE,
        ep_square="e6",
    )
    rows = cp.enumerate_capture_pairs(value, cp.WHITE)
    assert not any(row.en_passant for row in rows)
    assert any(
        row.raw_from == cp.square("c3")
        and row.raw_to == cp.square("d5")
        and row.target_class == cp.ROOK
        for row in rows
    )


def test_ep_does_not_reconstruct_the_previous_double_push_origin() -> None:
    value = position(
        piece(cp.WHITE, cp.KING, "a1"),
        piece(cp.BLACK, cp.KING, "h8"),
        piece(cp.WHITE, cp.PAWN, "d5"),
        piece(cp.BLACK, cp.PAWN, "e5"),
        piece(cp.BLACK, cp.BISHOP, "e7"),
        side_to_move=cp.WHITE,
        ep_square="e6",
    )
    ep_rows = [
        row for row in cp.enumerate_capture_pairs(value, cp.WHITE) if row.en_passant
    ]
    assert len(ep_rows) == 1


def test_pinned_and_self_blasting_ep_candidates_are_retained() -> None:
    pinned = position(
        piece(cp.WHITE, cp.KING, "d1"),
        piece(cp.BLACK, cp.KING, "h8"),
        piece(cp.WHITE, cp.PAWN, "d5"),
        piece(cp.BLACK, cp.PAWN, "e5"),
        piece(cp.BLACK, cp.ROOK, "d8"),
        side_to_move=cp.WHITE,
        ep_square="e6",
    )
    self_blasting = position(
        piece(cp.WHITE, cp.KING, "d6"),
        piece(cp.BLACK, cp.KING, "h8"),
        piece(cp.WHITE, cp.PAWN, "d5"),
        piece(cp.BLACK, cp.PAWN, "e5"),
        side_to_move=cp.WHITE,
        ep_square="e6",
    )
    assert len(
        [row for row in cp.enumerate_capture_pairs(pinned, cp.WHITE) if row.en_passant]
    ) == 1
    assert len(
        [
            row
            for row in cp.enumerate_capture_pairs(self_blasting, cp.WHITE)
            if row.en_passant
        ]
    ) == 1


def test_pinned_capture_is_deliberately_retained() -> None:
    value = position(
        piece(cp.WHITE, cp.KING, "e1"),
        piece(cp.BLACK, cp.KING, "a8"),
        piece(cp.WHITE, cp.ROOK, "e2"),
        piece(cp.BLACK, cp.ROOK, "e8"),
        piece(cp.BLACK, cp.PAWN, "h2"),
    )
    rows = activations_from(value, cp.WHITE, "e2")
    assert any(row.raw_to == cp.square("h2") for row in rows)


def test_atomic_self_blasting_capture_is_deliberately_retained() -> None:
    value = position(
        piece(cp.WHITE, cp.KING, "d4"),
        piece(cp.BLACK, cp.KING, "h8"),
        piece(cp.WHITE, cp.KNIGHT, "f3"),
        piece(cp.BLACK, cp.PAWN, "e5"),
    )
    rows = activations_from(value, cp.WHITE, "f3")
    assert any(row.raw_to == cp.square("e5") for row in rows)


def test_touching_kings_are_accepted_but_kings_never_act() -> None:
    value = position(
        piece(cp.WHITE, cp.KING, "e4"),
        piece(cp.BLACK, cp.KING, "e5"),
    )
    assert cp.enumerate_capture_pairs(value, cp.WHITE) == ()
    assert cp.enumerate_capture_pairs(value, cp.BLACK) == ()


def test_atomic960_and_castling_metadata_are_capture_pair_neutral() -> None:
    pieces = (
        piece(cp.WHITE, cp.KING, "b1"),
        piece(cp.BLACK, cp.KING, "g8"),
        piece(cp.WHITE, cp.QUEEN, "d4"),
        piece(cp.BLACK, cp.KNIGHT, "d7"),
    )
    standard = position(*pieces, atomic960=False, castling_rights="KQkq")
    atomic960 = position(*pieces, atomic960=True, castling_rights="BGbg")
    for perspective in cp.COLORS:
        assert compact_signature(standard, perspective) == compact_signature(
            atomic960, perspective
        )


def test_output_is_sorted_unique_bounded_and_at_most_eight_per_actor() -> None:
    rng = random.Random(0xA70C_C0DE)
    non_king_kinds = cp.ACTOR_KINDS
    for _ in range(128):
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
                    rng.choice(non_king_kinds),
                    squares[cursor],
                )
            )
            cursor += 1
        for _ in range(black_count):
            pieces.append(
                cp.Piece(
                    cp.BLACK,
                    rng.choice(non_king_kinds),
                    squares[cursor],
                )
            )
            cursor += 1
        value = cp.CapturePosition(tuple(pieces), side_to_move=rng.choice(cp.COLORS))
        for perspective in cp.COLORS:
            rows = cp.enumerate_capture_pairs(value, perspective)
            indices = [row.index for row in rows]
            assert indices == sorted(indices)
            assert len(indices) == len(set(indices)) <= cp.MAX_ACTIVE_FEATURES
            assert all(0 <= index < cp.PHYSICAL_DIMENSIONS for index in indices)
            per_actor = Counter(row.raw_from for row in rows)
            assert all(count <= 8 for count in per_actor.values())


@pytest.mark.parametrize(
    ("function", "arguments", "message"),
    (
        (cp.geometry_edges, (cp.KING, cp.OWN), "non-king"),
        (cp.geometry_edges, (cp.ROOK, "SIDE"), "actor_rel"),
        (cp.edge_ordinal, (cp.ROOK, cp.OWN, 0, 9), "geometry"),
        (cp.edge_ordinal, (cp.ROOK, cp.OWN, True, 8), "integer"),
        (cp.normal_index, (cp.OWN, cp.KING, 0, 1, cp.PAWN), "non-king"),
        (cp.normal_index, (cp.OWN, cp.ROOK, 0, 8, "EP"), "target"),
        (cp.en_passant_index, (cp.OWN, cp.square("a5"), cp.square("a6")), "strict EP"),
        (cp.en_passant_index, ("SIDE", cp.square("a5"), cp.square("b6")), "actor_rel"),
    ),
)
def test_index_helpers_fail_closed(
    function: object, arguments: tuple[object, ...], message: str
) -> None:
    with pytest.raises(cp.CapturePairContractError, match=message):
        function(*arguments)  # type: ignore[operator]


def test_position_domain_validation_fails_closed() -> None:
    with pytest.raises(cp.CapturePairContractError, match="two pieces"):
        position(
            piece(cp.WHITE, cp.KING, "a1"),
            piece(cp.BLACK, cp.KING, "a1"),
        )
    with pytest.raises(cp.CapturePairContractError, match="exactly one king"):
        position(
            piece(cp.WHITE, cp.KING, "a1"),
            piece(cp.BLACK, cp.ROOK, "h8"),
        )
    with pytest.raises(cp.CapturePairContractError, match="ep_square"):
        position(
            piece(cp.WHITE, cp.KING, "a1"),
            piece(cp.BLACK, cp.KING, "h8"),
            ep_square=True,
        )
    with pytest.raises(cp.CapturePairContractError, match="perspective"):
        cp.enumerate_capture_pairs(
            position(
                piece(cp.WHITE, cp.KING, "a1"),
                piece(cp.BLACK, cp.KING, "h8"),
            ),
            "SIDE",
        )
    with pytest.raises(cp.CapturePairContractError, match="CapturePosition"):
        cp.enumerate_capture_pairs(object(), cp.WHITE)  # type: ignore[arg-type]


def test_per_color_and_total_material_limits_are_enforced() -> None:
    white_squares = tuple(range(17))
    pieces = [
        cp.Piece(cp.WHITE, cp.KING, white_squares[0]),
        cp.Piece(cp.BLACK, cp.KING, 63),
    ]
    pieces.extend(
        cp.Piece(cp.WHITE, cp.PAWN, square_index)
        for square_index in white_squares[1:]
    )
    with pytest.raises(cp.CapturePairContractError, match="at most 16"):
        cp.CapturePosition(tuple(pieces))


@pytest.mark.parametrize(
    "filename",
    (
        "atomic_v3_capture_pair_reference.py",
        "test_atomic_v3_capture_pair_oracle.py",
    ),
)
def test_capture_pair_python_parses_with_python_3_9_grammar(filename: str) -> None:
    source = (PYTHON_TESTS / filename).read_text(encoding="utf-8")
    tree = ast.parse(source, feature_version=9)
    assert tree.body
