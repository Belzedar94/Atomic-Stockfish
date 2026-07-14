#!/usr/bin/env python3
"""Golden and metamorphic gates for the independent V3 HM oracle."""

from __future__ import annotations

from collections import Counter
import json
from pathlib import Path
import sys

import pytest


ROOT = Path(__file__).resolve().parents[2]
PYTHON_TESTS = ROOT / "tests" / "python"
if str(PYTHON_TESTS) not in sys.path:
    sys.path.insert(0, str(PYTHON_TESTS))

import atomic_v3_hm_reference as hm  # noqa: E402


FIXTURE_PATH = ROOT / "tests" / "fixtures" / "atomic-nnue-v3" / "hm-oracle-v1.json"
SCHEMA_PATH = ROOT / "schemas" / "atomic-nnue-v3.json"


def _strict_json(path: Path) -> dict[str, object]:
    raw = path.read_bytes()
    assert not raw.startswith(b"\xef\xbb\xbf")
    assert b"\r" not in raw
    assert raw.endswith(b"\n")

    def reject_duplicates(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise AssertionError(f"duplicate JSON key {key!r} in {path}")
            result[key] = value
        return result

    value = json.loads(raw, object_pairs_hook=reject_duplicates)
    assert isinstance(value, dict)
    return value


@pytest.fixture(scope="module")
def golden() -> dict[str, object]:
    fixture = _strict_json(FIXTURE_PATH)
    assert fixture["schema"] == "atomic-nnue-v3-hm-oracle-v1"
    return fixture


def _position_cases(golden: dict[str, object]) -> dict[str, dict[str, object]]:
    values = golden["positions"]
    assert isinstance(values, list)
    cases = {item["id"]: item for item in values}  # type: ignore[index]
    assert len(cases) == len(values)
    return cases


def _compact_snapshot(position: hm.HMPosition, perspective: str) -> dict[str, object]:
    full = hm.hm_snapshot(position, perspective)
    keys = (
        "orientation",
        "piece_count",
        "network_bucket",
        "activation_sha256",
        "outputs_i32le_sha256",
        "output_sentinels",
        "selected_psqt",
    )
    return {key: full[key] for key in keys}


def _oriented_king_for_bucket(bucket: int) -> int:
    assert 0 <= bucket < 32
    rank_index = 7 - bucket // 4
    file_index = 7 - bucket % 4
    return rank_index * 8 + file_index


def _ray_is_clear(start: int, target: int, occupied: set[int]) -> bool:
    start_file, start_rank = start % 8, start // 8
    target_file, target_rank = target % 8, target // 8
    file_delta = target_file - start_file
    rank_delta = target_rank - start_rank
    file_step = (file_delta > 0) - (file_delta < 0)
    rank_step = (rank_delta > 0) - (rank_delta < 0)
    square_ = start + rank_step * 8 + file_step
    while square_ != target:
        if square_ in occupied:
            return False
        square_ += rank_step * 8 + file_step
    return True


def _pseudo_attacks(piece: hm.Piece, target: int, occupied: set[int]) -> bool:
    file_delta = target % 8 - piece.square % 8
    rank_delta = target // 8 - piece.square // 8
    absolute_file = abs(file_delta)
    absolute_rank = abs(rank_delta)
    if piece.kind == "PAWN":
        direction = 1 if piece.color == hm.WHITE else -1
        return absolute_file == 1 and rank_delta == direction
    if piece.kind == "KNIGHT":
        return (absolute_file, absolute_rank) in {(1, 2), (2, 1)}
    if piece.kind == "KING":
        return max(absolute_file, absolute_rank) == 1
    diagonal = absolute_file == absolute_rank and absolute_file != 0
    orthogonal = (file_delta == 0) != (rank_delta == 0)
    if piece.kind == "BISHOP":
        return diagonal and _ray_is_clear(piece.square, target, occupied)
    if piece.kind == "ROOK":
        return orthogonal and _ray_is_clear(piece.square, target, occupied)
    if piece.kind == "QUEEN":
        return (diagonal or orthogonal) and _ray_is_clear(
            piece.square, target, occupied
        )
    raise AssertionError(f"unhandled piece kind {piece.kind}")


def _assert_quiet_atomic_fixture(position: hm.HMPosition) -> None:
    """Prove the curated cases contain no direct or adjacent-blast king threat."""

    occupied = {piece.square for piece in position.pieces}
    for defender in hm.COLORS:
        king = next(
            piece
            for piece in position.pieces
            if piece.color == defender and piece.kind == "KING"
        )
        king_file, king_rank = king.square % 8, king.square // 8
        adjacent_friendly = {
            piece.square
            for piece in position.pieces
            if piece.color == defender
            and piece.square != king.square
            and max(
                abs(piece.square % 8 - king_file),
                abs(piece.square // 8 - king_rank),
            )
            == 1
        }
        # With no friendly capture target in the blast ring, an opposing
        # capture can explode this king only by capturing the king itself.
        assert not adjacent_friendly
        assert not any(
            _pseudo_attacks(piece, king.square, occupied)
            for piece in position.pieces
            if piece.color != defender
        )


def test_reference_constants_match_provisional_machine_contract() -> None:
    contract = _strict_json(SCHEMA_PATH)
    slices = contract["feature_slices"]
    assert isinstance(slices, list)
    slice_ = next(item for item in slices if item["id"] == "half-ka-v2-atomic-hm")
    assert slice_["training_plane_order"] == list(hm.TRAINING_PLANES)
    assert slice_["physical_plane_order"] == list(hm.PHYSICAL_PLANES)
    assert slice_["training_dimensions"] == hm.TRAINING_DIMENSIONS == 24576
    assert slice_["physical_dimensions"] == hm.PHYSICAL_DIMENSIONS == 22528
    assert slice_["virtual_factor_dimensions"] == hm.VIRTUAL_DIMENSIONS == 768
    assert slice_["king_buckets"] == hm.KING_BUCKETS == 32
    assert hm.OUTPUTS == 1032
    assert hm.ACCUMULATOR_OUTPUTS == 1024
    assert hm.PSQT_OUTPUTS == 8

    orientation = contract["orientation"]
    assert orientation["black_perspective_xor"] == 56
    assert orientation["horizontal_mirror_xor"] == 7
    assert orientation["mirror_when_oriented_own_king_file_is"] == [
        "a",
        "b",
        "c",
        "d",
    ]


def test_all_64_raw_king_squares_for_both_perspectives_are_golden(
    golden: dict[str, object],
) -> None:
    tables = golden["king_orientation"]
    assert isinstance(tables, dict)
    for perspective in hm.COLORS:
        table = tables[perspective]
        assert isinstance(table, dict)
        xors = table["orientation_xor_by_raw_square"]
        squares = table["oriented_square_by_raw_square"]
        buckets = table["bucket_by_raw_square"]
        assert len(xors) == len(squares) == len(buckets) == 64

        actual = [hm.orientation_for(perspective, raw) for raw in range(64)]
        assert [item.square_xor for item in actual] == xors
        assert [item.oriented_own_king for item in actual] == squares
        assert [item.king_bucket for item in actual] == buckets
        assert all(item.oriented_own_king % 8 >= 4 for item in actual)
        assert Counter(item.king_bucket for item in actual) == Counter(
            {bucket: 2 for bucket in range(32)}
        )

        for raw, item in enumerate(actual):
            vertical = 56 if perspective == hm.BLACK else 0
            pre_horizontal = raw ^ vertical
            expected_horizontal = 7 if pre_horizontal % 8 < 4 else 0
            assert item.vertical_xor == vertical
            assert item.horizontal_xor == expected_horizontal
            assert item.oriented_own_king == raw ^ vertical ^ expected_horizontal
            assert item.king_bucket == (7 - item.oriented_own_king // 8) * 4 + (
                7 - item.oriented_own_king % 8
            )

    # A black raw square is the same pre-horizontal geometry as the vertically
    # flipped white raw square.  This freezes the perspective transform without
    # making the two perspectives share their horizontal branch.
    for raw in range(64):
        black = hm.orientation_for(hm.BLACK, raw)
        white = hm.orientation_for(hm.WHITE, raw ^ 56)
        assert black.oriented_own_king == white.oriented_own_king
        assert black.king_bucket == white.king_bucket


def test_fixture_covers_every_requested_hm_scenario(golden: dict[str, object]) -> None:
    cases = _position_cases(golden)
    assert set(cases) == {
        "opposite-mirror-all-classes",
        "opposite-mirror-all-classes-horizontal",
        "atomic960-ep-metadata",
        "same-material-metadata-mutated",
        "thirty-two-piece-boundary",
    }
    assert len(golden["export_mapping_cases"]) == 4  # type: ignore[arg-type]
    assert golden["metamorphic_pairs"] == [
        {
            "kind": "horizontal-mirror",
            "left": "opposite-mirror-all-classes",
            "right": "opposite-mirror-all-classes-horizontal",
        },
        {
            "kind": "stm-ep-castling-atomic960-neutral",
            "left": "atomic960-ep-metadata",
            "right": "same-material-metadata-mutated",
        },
    ]


def test_curated_atomic_positions_have_no_direct_or_blast_ring_king_threat(
    golden: dict[str, object],
) -> None:
    cases = _position_cases(golden)
    for case_id in (
        "opposite-mirror-all-classes",
        "opposite-mirror-all-classes-horizontal",
        "atomic960-ep-metadata",
        "same-material-metadata-mutated",
    ):
        _assert_quiet_atomic_fixture(hm.HMPosition.from_wire(cases[case_id]["position"]))

    atomic960 = hm.HMPosition.from_wire(cases["atomic960-ep-metadata"]["position"])
    assert {
        (piece.color, piece.kind, hm.square_name(piece.square))
        for piece in atomic960.pieces
    } == {
        (hm.WHITE, "KING", "c1"),
        (hm.WHITE, "ROOK", "a1"),
        (hm.WHITE, "ROOK", "h1"),
        (hm.WHITE, "PAWN", "e5"),
        (hm.BLACK, "KING", "c8"),
        (hm.BLACK, "ROOK", "a8"),
        (hm.BLACK, "ROOK", "h8"),
        (hm.BLACK, "PAWN", "d5"),
    }


def test_every_golden_position_replays_byte_exact(golden: dict[str, object]) -> None:
    for case in _position_cases(golden).values():
        position = hm.HMPosition.from_wire(case["position"])
        expected = case["expected"]
        for perspective in hm.COLORS:
            assert _compact_snapshot(position, perspective) == expected[perspective]


def test_all_piece_classes_relations_and_king_merge_are_active(
    golden: dict[str, object],
) -> None:
    case = _position_cases(golden)["opposite-mirror-all-classes"]
    position = hm.HMPosition.from_wire(case["position"])
    assert {piece.kind for piece in position.pieces} == set(hm.PIECE_KINDS)

    for perspective in hm.COLORS:
        active = hm.enumerate_hm(position, perspective)
        assert len(active) == 12
        assert {item.training_plane for item in active} == set(range(12))
        assert {item.physical_plane for item in active} == set(range(11))

        own_king = next(
            piece
            for piece in position.pieces
            if piece.color == perspective and piece.kind == "KING"
        )
        orient = hm.orientation_for(perspective, own_king.square)
        for piece, activation in zip(
            sorted(position.pieces, key=lambda item: item.square), active
        ):
            assert activation.oriented_square == orient.orient(piece.square)
            assert activation.training_plane == hm.training_plane(piece, perspective)
            assert activation.physical_plane == hm.physical_plane(
                activation.training_plane
            )
            assert 0 <= activation.training_index < hm.TRAINING_DIMENSIONS
            assert 0 <= activation.virtual_index < hm.VIRTUAL_DIMENSIONS
            assert 0 <= activation.physical_index < hm.PHYSICAL_DIMENSIONS
            source = hm.export_source(
                activation.physical_index, orient.oriented_own_king
            )
            assert source.training_index == activation.training_index
            assert source.virtual_index == activation.virtual_index

        king_rows = [item for item in active if item.training_plane >= 10]
        assert {item.training_plane for item in king_rows} == {10, 11}
        assert all(item.physical_plane == 10 for item in king_rows)


@pytest.mark.parametrize("perspective", hm.COLORS)
@pytest.mark.parametrize("piece_kind", hm.PIECE_KINDS)
@pytest.mark.parametrize("same_color", (True, False))
def test_training_plane_order_is_own_opponent_for_every_piece_class(
    perspective: str, piece_kind: str, same_color: bool
) -> None:
    color = perspective if same_color else (hm.BLACK if perspective == hm.WHITE else hm.WHITE)
    piece = hm.Piece(color, piece_kind, 0)
    expected = hm.PIECE_KINDS.index(piece_kind) * 2 + int(not same_color)
    assert hm.training_plane(piece, perspective) == expected
    assert hm.TRAINING_PLANES[expected] == (
        ("OWN_" if same_color else "OPP_") + piece_kind
    )


def test_12_to_11_source_mapping_for_every_physical_row() -> None:
    seen_training_planes: Counter[int] = Counter()
    for bucket in range(32):
        own_king = _oriented_king_for_bucket(bucket)
        for physical_plane in range(11):
            for oriented_square in range(64):
                physical_index = (
                    bucket * hm.PHYSICAL_BUCKET_WIDTH
                    + physical_plane * 64
                    + oriented_square
                )
                source = hm.export_source(physical_index, own_king)
                expected_training_plane = physical_plane
                if physical_plane == 10:
                    expected_training_plane = 10 if oriented_square == own_king else 11
                assert source.training_plane == expected_training_plane
                assert source.training_index == (
                    bucket * hm.TRAINING_BUCKET_WIDTH
                    + expected_training_plane * 64
                    + oriented_square
                )
                assert source.virtual_index == expected_training_plane * 64 + oriented_square
                seen_training_planes[source.training_plane] += 1
    assert set(seen_training_planes) == set(range(12))
    assert seen_training_planes[10] == 32
    assert seen_training_planes[11] == 32 * 63


def test_numeric_export_goldens_cover_all_1032_outputs_after_coalescing(
    golden: dict[str, object],
) -> None:
    cases = golden["export_mapping_cases"]
    assert isinstance(cases, list)
    seen_king_training_planes: set[int] = set()
    for case in cases:
        physical_index = case["physical_index"]
        own_king = hm.square(case["oriented_own_king"])
        source = hm.export_source(physical_index, own_king)
        actual_source = {
            "king_bucket": source.king_bucket,
            "physical_plane": source.physical_plane,
            "oriented_square": source.oriented_square,
            "training_plane": source.training_plane,
            "training_index": source.training_index,
            "virtual_index": source.virtual_index,
        }
        assert actual_source == case["expected_source"]
        if source.physical_plane == 10:
            seen_king_training_planes.add(source.training_plane)

        values = hm.coalesced_row(physical_index, own_king)
        assert len(values) == 1032
        for output, value in enumerate(values):
            assert value == hm.synthetic_bucket_weight(source.training_index, output) + (
                hm.synthetic_virtual_weight(source.virtual_index, output)
            )
            assert -16000 <= value <= 16000
        assert hm.i32le_sha256(values) == case["outputs_i32le_sha256"]
        assert {
            index: values[int(index)] for index in case["output_sentinels"]
        } == case["output_sentinels"]
        assert set(case["output_sentinels"]) == {
            "0",
            "1023",
            "1024",
            "1025",
            "1026",
            "1027",
            "1028",
            "1029",
            "1030",
            "1031",
        }
    assert seen_king_training_planes == {10, 11}


def test_white_and_black_choose_opposite_horizontal_branches_and_mirror_exactly(
    golden: dict[str, object],
) -> None:
    cases = _position_cases(golden)
    original = hm.HMPosition.from_wire(cases["opposite-mirror-all-classes"]["position"])
    mirrored = hm.HMPosition.from_wire(
        cases["opposite-mirror-all-classes-horizontal"]["position"]
    )
    assert hm.horizontally_mirrored(original) == mirrored

    original_branches = {}
    mirrored_branches = {}
    for perspective in hm.COLORS:
        original_king = next(
            p for p in original.pieces if p.color == perspective and p.kind == "KING"
        )
        mirrored_king = next(
            p for p in mirrored.pieces if p.color == perspective and p.kind == "KING"
        )
        original_orient = hm.orientation_for(perspective, original_king.square)
        mirrored_orient = hm.orientation_for(perspective, mirrored_king.square)
        original_branches[perspective] = original_orient.horizontal_xor
        mirrored_branches[perspective] = mirrored_orient.horizontal_xor
        assert original_orient.horizontal_xor ^ mirrored_orient.horizontal_xor == 7

        original_set = {
            (item.training_index, item.virtual_index, item.physical_index)
            for item in hm.enumerate_hm(original, perspective)
        }
        mirrored_set = {
            (item.training_index, item.virtual_index, item.physical_index)
            for item in hm.enumerate_hm(mirrored, perspective)
        }
        assert original_set == mirrored_set
        assert hm.accumulated_outputs(original, perspective) == hm.accumulated_outputs(
            mirrored, perspective
        )
    assert original_branches == {hm.WHITE: 7, hm.BLACK: 0}
    assert mirrored_branches == {hm.WHITE: 0, hm.BLACK: 7}


def test_atomic960_side_to_move_ep_and_castling_metadata_are_hm_neutral(
    golden: dict[str, object],
) -> None:
    cases = _position_cases(golden)
    atomic960 = hm.HMPosition.from_wire(cases["atomic960-ep-metadata"]["position"])
    changed = hm.HMPosition.from_wire(cases["same-material-metadata-mutated"]["position"])
    assert atomic960.atomic960 is True
    assert atomic960.ep_square == hm.square("d6")
    assert atomic960.castling_rights == "AHah"
    assert atomic960.side_to_move == hm.WHITE
    assert changed.atomic960 is False
    assert changed.ep_square is None
    assert changed.castling_rights == "-"
    assert changed.side_to_move == hm.BLACK
    assert atomic960.pieces == changed.pieces
    white_pawn = next(
        piece
        for piece in atomic960.pieces
        if piece.color == hm.WHITE and piece.kind == "PAWN"
    )
    assert white_pawn.square == hm.square("e5")
    assert atomic960.ep_square in (white_pawn.square + 7, white_pawn.square + 9)

    for perspective in hm.COLORS:
        assert hm.enumerate_hm(atomic960, perspective) == hm.enumerate_hm(
            changed, perspective
        )
        assert hm.accumulated_outputs(atomic960, perspective) == hm.accumulated_outputs(
            changed, perspective
        )


def test_thirty_two_piece_boundary_and_network_bucket(golden: dict[str, object]) -> None:
    case = _position_cases(golden)["thirty-two-piece-boundary"]
    position = hm.HMPosition.from_wire(case["position"])
    assert len(position.pieces) == 32
    assert Counter(piece.color for piece in position.pieces) == Counter(
        {hm.WHITE: 16, hm.BLACK: 16}
    )
    assert hm.network_bucket(32) == 7
    for perspective in hm.COLORS:
        assert len(hm.enumerate_hm(position, perspective)) == 32
        assert _compact_snapshot(position, perspective) == case["expected"][perspective]

    with pytest.raises(hm.HMContractError, match=r"2\.\.32"):
        hm.HMPosition(position.pieces + (hm.Piece(hm.WHITE, "QUEEN", hm.square("a3")),))

    recolored = list(position.pieces)
    pawn_index = next(
        index
        for index, piece in enumerate(recolored)
        if piece.color == hm.BLACK and piece.kind == "PAWN"
    )
    pawn = recolored[pawn_index]
    recolored[pawn_index] = hm.Piece(hm.WHITE, pawn.kind, pawn.square)
    with pytest.raises(hm.HMContractError, match="at most 16 pieces per color"):
        hm.HMPosition(tuple(recolored))

    for piece_count, expected in ((2, 0), (4, 0), (5, 1), (8, 1), (29, 7), (32, 7)):
        assert hm.network_bucket(piece_count) == expected
    for invalid in (1, 33):
        with pytest.raises(hm.HMContractError, match=r"2\.\.32"):
            hm.network_bucket(invalid)


def test_king_absent_or_duplicate_is_rejected_before_hm_enumeration(
    golden: dict[str, object],
) -> None:
    case = _position_cases(golden)["opposite-mirror-all-classes"]
    valid = hm.HMPosition.from_wire(case["position"])
    without_black_king = tuple(
        piece
        for piece in valid.pieces
        if not (piece.color == hm.BLACK and piece.kind == "KING")
    )
    with pytest.raises(hm.HMContractError, match="exactly one king per color"):
        hm.HMPosition(without_black_king)

    with pytest.raises(hm.HMContractError, match="exactly one king per color"):
        hm.HMPosition(
            valid.pieces + (hm.Piece(hm.WHITE, "KING", hm.square("h1")),)
        )


def test_material_order_does_not_change_feature_set_or_numeric_refresh(
    golden: dict[str, object],
) -> None:
    case = _position_cases(golden)["opposite-mirror-all-classes"]
    position = hm.HMPosition.from_wire(case["position"])
    reversed_position = hm.HMPosition(
        tuple(reversed(position.pieces)),
        side_to_move=position.side_to_move,
        ep_square=position.ep_square,
        atomic960=position.atomic960,
        castling_rights=position.castling_rights,
    )
    for perspective in hm.COLORS:
        assert hm.enumerate_hm(position, perspective) == hm.enumerate_hm(
            reversed_position, perspective
        )
        assert hm.hm_snapshot(position, perspective) == hm.hm_snapshot(
            reversed_position, perspective
        )


def test_reference_rejects_mismatched_export_bucket_and_malformed_inputs() -> None:
    own_king = _oriented_king_for_bucket(29)
    wrong_bucket_row = 28 * hm.PHYSICAL_BUCKET_WIDTH
    with pytest.raises(hm.HMContractError, match="bucket does not match"):
        hm.export_source(wrong_bucket_row, own_king)
    with pytest.raises(hm.HMContractError, match="files e-h"):
        hm.export_source(0, hm.square("a8"))
    with pytest.raises(hm.HMContractError, match=r"0\.\.63"):
        hm.Piece(hm.WHITE, "PAWN", 64)
    with pytest.raises(hm.HMContractError, match="unknown piece kind"):
        hm.Piece(hm.WHITE, "COMMONER", 0)
    with pytest.raises(hm.HMContractError, match="WHITE or BLACK"):
        hm.orientation_for("RED", 0)


@pytest.mark.parametrize("invalid", (True, False, 0.0, 1.0, "0", None, object()))
def test_scalar_index_helpers_reject_bool_and_non_integer_inputs(invalid: object) -> None:
    with pytest.raises(hm.HMContractError, match="training plane"):
        hm.physical_plane(invalid)  # type: ignore[arg-type]
    with pytest.raises(hm.HMContractError, match="physical index"):
        hm.export_source(invalid, hm.square("h8"))  # type: ignore[arg-type]
    with pytest.raises(hm.HMContractError, match="training index"):
        hm.synthetic_bucket_weight(invalid, 0)  # type: ignore[arg-type]
    with pytest.raises(hm.HMContractError, match="output"):
        hm.synthetic_bucket_weight(0, invalid)  # type: ignore[arg-type]
    with pytest.raises(hm.HMContractError, match="virtual index"):
        hm.synthetic_virtual_weight(invalid, 0)  # type: ignore[arg-type]
    with pytest.raises(hm.HMContractError, match="output"):
        hm.synthetic_virtual_weight(0, invalid)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("function", "arguments", "message"),
    (
        (hm.physical_plane, (-1,), "training plane"),
        (hm.physical_plane, (hm.TRAINING_PLANE_COUNT,), "training plane"),
        (hm.export_source, (-1, hm.square("h8")), "physical index"),
        (
            hm.export_source,
            (hm.PHYSICAL_DIMENSIONS, hm.square("h8")),
            "physical index",
        ),
        (hm.synthetic_bucket_weight, (-1, 0), "training index"),
        (
            hm.synthetic_bucket_weight,
            (hm.TRAINING_DIMENSIONS, 0),
            "training index",
        ),
        (hm.synthetic_bucket_weight, (0, -1), "output"),
        (hm.synthetic_bucket_weight, (0, hm.OUTPUTS), "output"),
        (hm.synthetic_virtual_weight, (-1, 0), "virtual index"),
        (
            hm.synthetic_virtual_weight,
            (hm.VIRTUAL_DIMENSIONS, 0),
            "virtual index",
        ),
        (hm.synthetic_virtual_weight, (0, -1), "output"),
        (hm.synthetic_virtual_weight, (0, hm.OUTPUTS), "output"),
    ),
)
def test_scalar_index_helpers_reject_out_of_range_inputs(
    function: object, arguments: tuple[int, ...], message: str
) -> None:
    with pytest.raises(hm.HMContractError, match=message):
        function(*arguments)  # type: ignore[operator]
