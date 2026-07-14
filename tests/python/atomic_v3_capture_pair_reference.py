#!/usr/bin/env python3
"""Independent scalar oracle for the compact AtomicNNUEV3 CapturePair slice.

The oracle intentionally has no dependency on engine code.  It implements the
provisional H9.3c contract with rank-major ``A1=0`` squares, the shared HM
orientation, occupancy pseudocaptures, and the compact 40,012-row index space.
It is a test reference, not a move-legality implementation: pins, check
evasion, king adjacency, and atomic self-blast are deliberately ignored.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Iterable, Mapping, Optional, Sequence, Tuple


WHITE = "WHITE"
BLACK = "BLACK"
COLORS = (WHITE, BLACK)

OWN = "OWN"
OPP = "OPP"
ACTOR_RELATIONS = (OWN, OPP)

PAWN = "PAWN"
KNIGHT = "KNIGHT"
BISHOP = "BISHOP"
ROOK = "ROOK"
QUEEN = "QUEEN"
KING = "KING"
PIECE_KINDS = (PAWN, KNIGHT, BISHOP, ROOK, QUEEN, KING)
ACTOR_KINDS = PIECE_KINDS[:-1]
TARGET_CLASSES = PIECE_KINDS

SQUARES = 64
PHYSICAL_OFFSET = 22_528
GEOMETRY_DIMENSIONS = 3_332
GEOMETRY_SEGMENT_BASES = {
    PAWN: 0,
    KNIGHT: 84,
    BISHOP: 420,
    ROOK: 980,
    QUEEN: 1_876,
}
GEOMETRY_COUNTS = {
    PAWN: 84,
    KNIGHT: 336,
    BISHOP: 560,
    ROOK: 896,
    QUEEN: 1_456,
}
NORMAL_TARGET_CLASSES = 6
NORMAL_DIMENSIONS = 2 * GEOMETRY_DIMENSIONS * NORMAL_TARGET_CLASSES
EP_EDGES_PER_RELATION = 14
EP_DIMENSIONS = 2 * EP_EDGES_PER_RELATION
PHYSICAL_DIMENSIONS = NORMAL_DIMENSIONS + EP_DIMENSIONS
MAX_ACTIVE_FEATURES = 240

_KNIGHT_OFFSETS = (
    (-2, -1),
    (-2, 1),
    (-1, -2),
    (-1, 2),
    (1, -2),
    (1, 2),
    (2, -1),
    (2, 1),
)
_BISHOP_DIRECTIONS = ((-1, -1), (-1, 1), (1, -1), (1, 1))
_ROOK_DIRECTIONS = ((-1, 0), (0, -1), (0, 1), (1, 0))
_QUEEN_DIRECTIONS = _BISHOP_DIRECTIONS + _ROOK_DIRECTIONS
_SLIDER_DIRECTIONS = {
    BISHOP: _BISHOP_DIRECTIONS,
    ROOK: _ROOK_DIRECTIONS,
    QUEEN: _QUEEN_DIRECTIONS,
}


class CapturePairContractError(ValueError):
    """Raised when an input is outside the evaluable CapturePair contract."""


def _require_plain_int(value: object, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise CapturePairContractError(f"{label} must be an integer")
    return value


def _require_square(value: object, label: str = "square") -> int:
    result = _require_plain_int(value, label)
    if not 0 <= result < SQUARES:
        raise CapturePairContractError(f"{label} must be in 0..63")
    return result


def square(name: str) -> int:
    """Convert ``a1`` through ``h8`` to an A1=0 rank-major ordinal."""

    if not isinstance(name, str) or len(name) != 2:
        raise CapturePairContractError("square name must have the form a1..h8")
    file_index = ord(name[0]) - ord("a")
    rank_index = ord(name[1]) - ord("1")
    if not 0 <= file_index < 8 or not 0 <= rank_index < 8:
        raise CapturePairContractError("square name must have the form a1..h8")
    return rank_index * 8 + file_index


def square_name(value: int) -> str:
    value = _require_square(value)
    return chr(ord("a") + value % 8) + chr(ord("1") + value // 8)


def _square_at(file_index: int, rank_index: int) -> int:
    return rank_index * 8 + file_index


def _file_rank(value: int) -> Tuple[int, int]:
    value = _require_square(value)
    return value % 8, value // 8


def _opposite(color: str) -> str:
    if color == WHITE:
        return BLACK
    if color == BLACK:
        return WHITE
    raise CapturePairContractError(f"unknown color: {color!r}")


@dataclass(frozen=True)
class Piece:
    color: str
    kind: str
    square: int

    def __post_init__(self) -> None:
        if self.color not in COLORS:
            raise CapturePairContractError(f"unknown piece color: {self.color!r}")
        if self.kind not in PIECE_KINDS:
            raise CapturePairContractError(f"unknown piece kind: {self.kind!r}")
        _require_square(self.square, "piece square")

    @classmethod
    def from_wire(cls, value: Sequence[object]) -> "Piece":
        if (
            not isinstance(value, Sequence)
            or isinstance(value, (str, bytes))
            or len(value) != 3
        ):
            raise CapturePairContractError(
                "piece wire value must be [color, kind, square]"
            )
        color, kind, piece_square = value
        if not isinstance(color, str) or not isinstance(kind, str):
            raise CapturePairContractError("piece color and kind must be strings")
        if isinstance(piece_square, str):
            piece_square = square(piece_square)
        return cls(color, kind, piece_square)  # type: ignore[arg-type]


def validate_material(pieces: Iterable[Piece]) -> Tuple[Piece, ...]:
    material = tuple(pieces)
    if not 2 <= len(material) <= 32:
        raise CapturePairContractError(
            "evaluable CapturePair material must contain 2..32 pieces"
        )

    occupied = set()
    color_counts = {WHITE: 0, BLACK: 0}
    king_counts = {WHITE: 0, BLACK: 0}
    for piece in material:
        if not isinstance(piece, Piece):
            raise CapturePairContractError("material entries must be Piece instances")
        if piece.square in occupied:
            raise CapturePairContractError(
                f"two pieces occupy {square_name(piece.square)}"
            )
        occupied.add(piece.square)
        color_counts[piece.color] += 1
        if piece.kind == KING:
            king_counts[piece.color] += 1

    if color_counts[WHITE] > 16 or color_counts[BLACK] > 16:
        raise CapturePairContractError(
            "evaluable CapturePair material allows at most 16 pieces per color"
        )
    if king_counts != {WHITE: 1, BLACK: 1}:
        raise CapturePairContractError(
            "evaluable CapturePair material requires exactly one king per color"
        )
    return material


@dataclass(frozen=True)
class CapturePosition:
    pieces: Tuple[Piece, ...]
    side_to_move: str = WHITE
    ep_square: Optional[int] = None
    atomic960: bool = False
    castling_rights: str = "-"

    def __post_init__(self) -> None:
        if self.side_to_move not in COLORS:
            raise CapturePairContractError("side_to_move must be WHITE or BLACK")
        if self.ep_square is not None:
            _require_square(self.ep_square, "ep_square")
        if not isinstance(self.atomic960, bool):
            raise CapturePairContractError("atomic960 must be boolean")
        if not isinstance(self.castling_rights, str):
            raise CapturePairContractError("castling_rights must be a string")
        validate_material(self.pieces)

    @classmethod
    def from_wire(cls, value: Mapping[str, object]) -> "CapturePosition":
        if not isinstance(value, Mapping):
            raise CapturePairContractError("position wire value must be an object")
        raw_pieces = value.get("pieces")
        if not isinstance(raw_pieces, list):
            raise CapturePairContractError("position.pieces must be a list")
        ep_value = value.get("ep_square")
        if ep_value == "-" or ep_value is None:
            ep_square = None
        elif isinstance(ep_value, str):
            ep_square = square(ep_value)
        else:
            ep_square = ep_value
        return cls(
            tuple(Piece.from_wire(item) for item in raw_pieces),  # type: ignore[arg-type]
            side_to_move=value.get("side_to_move", WHITE),  # type: ignore[arg-type]
            ep_square=ep_square,  # type: ignore[arg-type]
            atomic960=value.get("atomic960", False),  # type: ignore[arg-type]
            castling_rights=value.get("castling_rights", "-"),  # type: ignore[arg-type]
        )


@dataclass(frozen=True)
class Orientation:
    perspective: str
    vertical_xor: int
    horizontal_xor: int
    oriented_own_king: int

    @property
    def square_xor(self) -> int:
        return self.vertical_xor ^ self.horizontal_xor

    def orient(self, raw_square: int) -> int:
        return _require_square(raw_square) ^ self.square_xor


def orientation_for(position: CapturePosition, perspective: str) -> Orientation:
    if perspective not in COLORS:
        raise CapturePairContractError("perspective must be WHITE or BLACK")
    own_king = next(
        piece
        for piece in position.pieces
        if piece.color == perspective and piece.kind == KING
    )
    vertical = 56 if perspective == BLACK else 0
    pre_horizontal_king = own_king.square ^ vertical
    horizontal = 7 if pre_horizontal_king % 8 < 4 else 0
    oriented_king = pre_horizontal_king ^ horizontal
    if oriented_king % 8 < 4:
        raise AssertionError("oriented own king did not normalize to files e-h")
    return Orientation(perspective, vertical, horizontal, oriented_king)


def actor_relation(actor_color: str, perspective: str) -> str:
    if actor_color not in COLORS or perspective not in COLORS:
        raise CapturePairContractError("actor color and perspective must be colors")
    return OWN if actor_color == perspective else OPP


def _leaper_edges(offsets: Sequence[Tuple[int, int]]) -> Tuple[Tuple[int, int], ...]:
    result = []
    for from_square in range(SQUARES):
        from_file, from_rank = _file_rank(from_square)
        destinations = []
        for file_delta, rank_delta in offsets:
            to_file = from_file + file_delta
            to_rank = from_rank + rank_delta
            if 0 <= to_file < 8 and 0 <= to_rank < 8:
                destinations.append(_square_at(to_file, to_rank))
        for to_square in sorted(destinations):
            result.append((from_square, to_square))
    return tuple(result)


def _slider_edges(
    directions: Sequence[Tuple[int, int]],
) -> Tuple[Tuple[int, int], ...]:
    result = []
    for from_square in range(SQUARES):
        from_file, from_rank = _file_rank(from_square)
        destinations = []
        for file_delta, rank_delta in directions:
            to_file = from_file + file_delta
            to_rank = from_rank + rank_delta
            while 0 <= to_file < 8 and 0 <= to_rank < 8:
                destinations.append(_square_at(to_file, to_rank))
                to_file += file_delta
                to_rank += rank_delta
        for to_square in sorted(destinations):
            result.append((from_square, to_square))
    return tuple(result)


def _pawn_edges(actor_rel: str) -> Tuple[Tuple[int, int], ...]:
    if actor_rel not in ACTOR_RELATIONS:
        raise CapturePairContractError("actor_rel must be OWN or OPP")
    rank_delta = 1 if actor_rel == OWN else -1
    result = []
    # Evaluable pawns may originate only on ranks 2..7.  Excluding ranks 1
    # and 8 is what makes each actor-relative table exactly 84 edges.
    for from_rank in range(1, 7):
        for from_file in range(8):
            from_square = _square_at(from_file, from_rank)
            destinations = []
            for file_delta in (-1, 1):
                to_file = from_file + file_delta
                to_rank = from_rank + rank_delta
                if 0 <= to_file < 8 and 0 <= to_rank < 8:
                    destinations.append(_square_at(to_file, to_rank))
            for to_square in sorted(destinations):
                result.append((from_square, to_square))
    return tuple(result)


_COMMON_EDGE_TABLES = {
    KNIGHT: _leaper_edges(_KNIGHT_OFFSETS),
    BISHOP: _slider_edges(_BISHOP_DIRECTIONS),
    ROOK: _slider_edges(_ROOK_DIRECTIONS),
    QUEEN: _slider_edges(_QUEEN_DIRECTIONS),
}
_PAWN_EDGE_TABLES = {OWN: _pawn_edges(OWN), OPP: _pawn_edges(OPP)}


def geometry_edges(actor_kind: str, actor_rel: str) -> Tuple[Tuple[int, int], ...]:
    """Return one lexicographically ordered segment-local edge table."""

    if actor_kind not in ACTOR_KINDS:
        raise CapturePairContractError("CapturePair actor must be a non-king piece")
    if actor_rel not in ACTOR_RELATIONS:
        raise CapturePairContractError("actor_rel must be OWN or OPP")
    if actor_kind == PAWN:
        return _PAWN_EDGE_TABLES[actor_rel]
    return _COMMON_EDGE_TABLES[actor_kind]


_GEOMETRY_ORDINALS = {
    (actor_kind, actor_rel): {
        edge: GEOMETRY_SEGMENT_BASES[actor_kind] + local_ordinal
        for local_ordinal, edge in enumerate(geometry_edges(actor_kind, actor_rel))
    }
    for actor_kind in ACTOR_KINDS
    for actor_rel in ACTOR_RELATIONS
}


def edge_ordinal(
    actor_kind: str, actor_rel: str, oriented_from: int, oriented_to: int
) -> int:
    if actor_kind not in ACTOR_KINDS:
        raise CapturePairContractError("CapturePair actor must be a non-king piece")
    if actor_rel not in ACTOR_RELATIONS:
        raise CapturePairContractError("actor_rel must be OWN or OPP")
    oriented_from = _require_square(oriented_from, "oriented_from")
    oriented_to = _require_square(oriented_to, "oriented_to")
    try:
        return _GEOMETRY_ORDINALS[(actor_kind, actor_rel)][
            (oriented_from, oriented_to)
        ]
    except KeyError as error:
        raise CapturePairContractError(
            "from/to pair is not in the actor geometry segment"
        ) from error


def ep_edges(actor_rel: str) -> Tuple[Tuple[int, int], ...]:
    """Return the 14 strict EP origin/center pairs for one actor relation."""

    if actor_rel not in ACTOR_RELATIONS:
        raise CapturePairContractError("actor_rel must be OWN or OPP")
    center_rank = 5 if actor_rel == OWN else 2
    result = tuple(
        edge
        for edge in geometry_edges(PAWN, actor_rel)
        if edge[1] // 8 == center_rank
    )
    if len(result) != EP_EDGES_PER_RELATION:
        raise AssertionError("EP edge table does not contain exactly 14 edges")
    return result


_EP_ORDINALS = {
    actor_rel: {edge: ordinal for ordinal, edge in enumerate(ep_edges(actor_rel))}
    for actor_rel in ACTOR_RELATIONS
}


def normal_index(
    actor_rel: str,
    actor_kind: str,
    oriented_from: int,
    oriented_to: int,
    target_class: str,
) -> int:
    """Map one occupied-target pseudocapture into rows 0..39,983."""

    if actor_rel not in ACTOR_RELATIONS:
        raise CapturePairContractError("actor_rel must be OWN or OPP")
    if target_class not in TARGET_CLASSES:
        raise CapturePairContractError("unknown normal CapturePair target class")
    relation_index = ACTOR_RELATIONS.index(actor_rel)
    target_index = TARGET_CLASSES.index(target_class)
    geometry_ordinal = edge_ordinal(
        actor_kind, actor_rel, oriented_from, oriented_to
    )
    result = (
        (relation_index * GEOMETRY_DIMENSIONS + geometry_ordinal)
        * NORMAL_TARGET_CLASSES
        + target_index
    )
    if not 0 <= result < NORMAL_DIMENSIONS:
        raise AssertionError("normal CapturePair index escaped its compact prefix")
    return result


def en_passant_index(
    actor_rel: str, oriented_from: int, oriented_center: int
) -> int:
    """Map one strict EP pseudocapture into the 28-row compact tail."""

    if actor_rel not in ACTOR_RELATIONS:
        raise CapturePairContractError("actor_rel must be OWN or OPP")
    oriented_from = _require_square(oriented_from, "oriented_from")
    oriented_center = _require_square(oriented_center, "oriented_center")
    try:
        ordinal = _EP_ORDINALS[actor_rel][(oriented_from, oriented_center)]
    except KeyError as error:
        raise CapturePairContractError(
            "from/center pair is not a strict EP edge"
        ) from error
    result = (
        NORMAL_DIMENSIONS
        + ACTOR_RELATIONS.index(actor_rel) * EP_EDGES_PER_RELATION
        + ordinal
    )
    if not NORMAL_DIMENSIONS <= result < PHYSICAL_DIMENSIONS:
        raise AssertionError("EP CapturePair index escaped its compact tail")
    return result


@dataclass(frozen=True)
class CapturePairActivation:
    index: int
    actor_rel: str
    actor_kind: str
    raw_from: int
    raw_to: int
    raw_captured: int
    oriented_from: int
    oriented_to: int
    oriented_captured: int
    edge_ordinal: int
    target_class: str
    en_passant: bool

    @property
    def local_index(self) -> int:
        return self.index

    @property
    def physical_index(self) -> int:
        return PHYSICAL_OFFSET + self.index


def _strict_ep_center(
    position: CapturePosition, occupied: Mapping[int, Piece]
) -> Optional[int]:
    """Authenticate EP metadata; malformed metadata contributes no EP row."""

    center = position.ep_square
    if center is None:
        return None
    if center in occupied:
        return None

    actor_color = position.side_to_move
    actor_push = 8 if actor_color == WHITE else -8
    expected_rank = 5 if actor_color == WHITE else 2
    if center // 8 != expected_rank:
        return None

    captured_square = center - actor_push
    if not 0 <= captured_square < SQUARES:
        return None
    captured = occupied.get(captured_square)
    if (
        captured is None
        or captured.color != _opposite(actor_color)
        or captured.kind != PAWN
    ):
        return None
    return center


def _normal_activation(
    actor: Piece,
    target: Piece,
    actor_rel: str,
    orientation: Orientation,
) -> CapturePairActivation:
    oriented_from = orientation.orient(actor.square)
    oriented_to = orientation.orient(target.square)
    ordinal = edge_ordinal(actor.kind, actor_rel, oriented_from, oriented_to)
    return CapturePairActivation(
        normal_index(
            actor_rel,
            actor.kind,
            oriented_from,
            oriented_to,
            target.kind,
        ),
        actor_rel,
        actor.kind,
        actor.square,
        target.square,
        target.square,
        oriented_from,
        oriented_to,
        oriented_to,
        ordinal,
        target.kind,
        False,
    )


def _ep_activation(
    actor: Piece,
    center: int,
    actor_rel: str,
    orientation: Orientation,
) -> CapturePairActivation:
    oriented_from = orientation.orient(actor.square)
    oriented_center = orientation.orient(center)
    actor_push = 8 if actor.color == WHITE else -8
    raw_captured = center - actor_push
    oriented_captured = orientation.orient(raw_captured)
    ordinal = edge_ordinal(PAWN, actor_rel, oriented_from, oriented_center)
    return CapturePairActivation(
        en_passant_index(actor_rel, oriented_from, oriented_center),
        actor_rel,
        PAWN,
        actor.square,
        center,
        raw_captured,
        oriented_from,
        oriented_center,
        oriented_captured,
        ordinal,
        "EN_PASSANT",
        True,
    )


def _pawn_candidates(
    actor: Piece,
    actor_rel: str,
    orientation: Orientation,
    occupied: Mapping[int, Piece],
    strict_ep_center: Optional[int],
    side_to_move: str,
) -> Tuple[CapturePairActivation, ...]:
    oriented_from = orientation.orient(actor.square)
    result = []
    for _, oriented_to in geometry_edges(PAWN, actor_rel):
        # Select just the two table entries for this oriented origin.
        if _ != oriented_from:
            continue
        raw_to = orientation.orient(oriented_to)
        target = occupied.get(raw_to)
        if target is not None:
            if target.color != actor.color:
                result.append(
                    _normal_activation(actor, target, actor_rel, orientation)
                )
        elif strict_ep_center == raw_to and actor.color == side_to_move:
            result.append(_ep_activation(actor, raw_to, actor_rel, orientation))
    return tuple(result)


def _knight_candidates(
    actor: Piece,
    actor_rel: str,
    orientation: Orientation,
    occupied: Mapping[int, Piece],
) -> Tuple[CapturePairActivation, ...]:
    oriented_from = orientation.orient(actor.square)
    result = []
    for edge_from, oriented_to in geometry_edges(KNIGHT, actor_rel):
        if edge_from != oriented_from:
            continue
        raw_to = orientation.orient(oriented_to)
        target = occupied.get(raw_to)
        if target is not None and target.color != actor.color:
            result.append(_normal_activation(actor, target, actor_rel, orientation))
    return tuple(result)


def _slider_candidates(
    actor: Piece,
    actor_rel: str,
    orientation: Orientation,
    occupied: Mapping[int, Piece],
) -> Tuple[CapturePairActivation, ...]:
    oriented_from = orientation.orient(actor.square)
    from_file, from_rank = _file_rank(oriented_from)
    result = []
    for file_delta, rank_delta in _SLIDER_DIRECTIONS[actor.kind]:
        to_file = from_file + file_delta
        to_rank = from_rank + rank_delta
        while 0 <= to_file < 8 and 0 <= to_rank < 8:
            oriented_to = _square_at(to_file, to_rank)
            raw_to = orientation.orient(oriented_to)
            target = occupied.get(raw_to)
            if target is not None:
                if target.color != actor.color:
                    result.append(
                        _normal_activation(actor, target, actor_rel, orientation)
                    )
                break
            to_file += file_delta
            to_rank += rank_delta
    return tuple(result)


def enumerate_capture_pairs(
    position: CapturePosition, perspective: str
) -> Tuple[CapturePairActivation, ...]:
    """Enumerate deterministic compact CapturePair activations for a perspective."""

    if not isinstance(position, CapturePosition):
        raise CapturePairContractError("position must be a CapturePosition")
    if perspective not in COLORS:
        raise CapturePairContractError("perspective must be WHITE or BLACK")

    orientation = orientation_for(position, perspective)
    occupied: Dict[int, Piece] = {piece.square: piece for piece in position.pieces}
    strict_ep_center = _strict_ep_center(position, occupied)
    activations = []
    for actor in sorted(position.pieces, key=lambda piece: piece.square):
        if actor.kind == KING:
            continue
        relation = actor_relation(actor.color, perspective)
        if actor.kind == PAWN:
            actor_activations = _pawn_candidates(
                actor,
                relation,
                orientation,
                occupied,
                strict_ep_center,
                position.side_to_move,
            )
        elif actor.kind == KNIGHT:
            actor_activations = _knight_candidates(
                actor, relation, orientation, occupied
            )
        else:
            actor_activations = _slider_candidates(
                actor, relation, orientation, occupied
            )
        if len(actor_activations) > 8:
            raise AssertionError("one CapturePair actor exceeded eight candidates")
        activations.extend(actor_activations)

    activations.sort(key=lambda activation: activation.index)
    indices = [activation.index for activation in activations]
    if len(indices) != len(set(indices)):
        raise AssertionError("CapturePair emitted a duplicate physical index")
    if len(activations) > MAX_ACTIVE_FEATURES:
        raise AssertionError("CapturePair exceeded its 240-row accumulator bound")
    if any(not 0 <= index < PHYSICAL_DIMENSIONS for index in indices):
        raise AssertionError("CapturePair emitted an out-of-range physical index")
    return tuple(activations)


for _kind, _count in GEOMETRY_COUNTS.items():
    for _relation in ACTOR_RELATIONS:
        if len(geometry_edges(_kind, _relation)) != _count:
            raise AssertionError(f"{_kind} geometry count does not match the contract")

if NORMAL_DIMENSIONS != 39_984 or PHYSICAL_DIMENSIONS != 40_012:
    raise AssertionError("compact CapturePair dimensions do not match the contract")
