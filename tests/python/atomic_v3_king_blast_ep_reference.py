#!/usr/bin/env python3
"""Independent scalar oracle for the AtomicNNUEV3 KingBlastEP slice.

CapturePair is the only candidate source.  This module merely projects those
unfiltered occupied-target and authenticated en-passant candidates onto the
boolean ``center x actor relation x king/EP class`` tensor.  It intentionally
does not generate moves, reconstruct EP history, or apply legality filters.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Optional, Tuple

import atomic_v3_capture_pair_reference as capture_pair


WHITE = capture_pair.WHITE
BLACK = capture_pair.BLACK
COLORS = capture_pair.COLORS

OWN = capture_pair.OWN
OPP = capture_pair.OPP
ACTOR_RELATIONS = capture_pair.ACTOR_RELATIONS

ENEMY_KING_CENTER = "ENEMY_KING_CENTER"
DIRECTION_ORDER = ("N", "NE", "E", "SE", "S", "SW", "W", "NW")
ENEMY_KING_N = "ENEMY_KING_N"
ENEMY_KING_NE = "ENEMY_KING_NE"
ENEMY_KING_E = "ENEMY_KING_E"
ENEMY_KING_SE = "ENEMY_KING_SE"
ENEMY_KING_S = "ENEMY_KING_S"
ENEMY_KING_SW = "ENEMY_KING_SW"
ENEMY_KING_W = "ENEMY_KING_W"
ENEMY_KING_NW = "ENEMY_KING_NW"
OWN_KING_N = "OWN_KING_N"
OWN_KING_NE = "OWN_KING_NE"
OWN_KING_E = "OWN_KING_E"
OWN_KING_SE = "OWN_KING_SE"
OWN_KING_S = "OWN_KING_S"
OWN_KING_SW = "OWN_KING_SW"
OWN_KING_W = "OWN_KING_W"
OWN_KING_NW = "OWN_KING_NW"
ENEMY_DIRECTION_CLASSES = (
    ENEMY_KING_N,
    ENEMY_KING_NE,
    ENEMY_KING_E,
    ENEMY_KING_SE,
    ENEMY_KING_S,
    ENEMY_KING_SW,
    ENEMY_KING_W,
    ENEMY_KING_NW,
)
OWN_DIRECTION_CLASSES = (
    OWN_KING_N,
    OWN_KING_NE,
    OWN_KING_E,
    OWN_KING_SE,
    OWN_KING_S,
    OWN_KING_SW,
    OWN_KING_W,
    OWN_KING_NW,
)
EN_PASSANT_MARKER = "EN_PASSANT_MARKER"
CLASS_ORDER = (
    (ENEMY_KING_CENTER,)
    + ENEMY_DIRECTION_CLASSES
    + OWN_DIRECTION_CLASSES
    + (EN_PASSANT_MARKER,)
)

# Deltas and vectors are expressed after the one shared perspective
# orientation has already been applied.  Vectors, rather than integer deltas
# alone, make file wrapping impossible at A/H boundaries.
DIRECTION_DELTAS = {
    "N": 8,
    "NE": 9,
    "E": 1,
    "SE": -7,
    "S": -8,
    "SW": -9,
    "W": -1,
    "NW": 7,
}
DIRECTION_VECTORS = {
    "N": (0, 1),
    "NE": (1, 1),
    "E": (1, 0),
    "SE": (1, -1),
    "S": (0, -1),
    "SW": (-1, -1),
    "W": (-1, 0),
    "NW": (-1, 1),
}
_VECTOR_TO_DIRECTION = {
    vector: direction for direction, vector in DIRECTION_VECTORS.items()
}

CENTER_DIMENSIONS = 64
ACTOR_RELATION_DIMENSIONS = 2
CLASS_DIMENSIONS = 18
PHYSICAL_OFFSET = 62_540
PHYSICAL_DIMENSIONS = CENTER_DIMENSIONS * ACTOR_RELATION_DIMENSIONS * CLASS_DIMENSIONS
TRAINING_DIMENSIONS = PHYSICAL_DIMENSIONS
MAX_ACTIVE_FEATURES = 35


class KingBlastEPContractError(ValueError):
    """Raised when an input is outside the KingBlastEP contract."""


def _require_plain_int(value: object, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise KingBlastEPContractError(f"{label} must be an integer")
    return value


def _require_square(value: object, label: str = "square") -> int:
    result = _require_plain_int(value, label)
    if not 0 <= result < CENTER_DIMENSIONS:
        raise KingBlastEPContractError(f"{label} must be in 0..63")
    return result


def _opposite(color: str) -> str:
    if color == WHITE:
        return BLACK
    if color == BLACK:
        return WHITE
    raise KingBlastEPContractError(f"unknown color: {color!r}")


def king_blast_ep_index(
    oriented_center: int, actor_rel: str, relation_class: str
) -> int:
    """Return the compact local row for one validated tensor coordinate."""

    center = _require_square(oriented_center, "oriented_center")
    if actor_rel not in ACTOR_RELATIONS:
        raise KingBlastEPContractError("actor_rel must be OWN or OPP")
    if relation_class not in CLASS_ORDER:
        raise KingBlastEPContractError("unknown KingBlastEP relation_class")
    result = (
        (center * ACTOR_RELATION_DIMENSIONS + ACTOR_RELATIONS.index(actor_rel))
        * CLASS_DIMENSIONS
        + CLASS_ORDER.index(relation_class)
    )
    if not 0 <= result < PHYSICAL_DIMENSIONS:
        raise AssertionError("KingBlastEP index escaped its compact tensor")
    return result


def adjacent_direction(oriented_center: int, oriented_related: int) -> Optional[str]:
    """Return the exact compass suffix for adjacent oriented squares."""

    center = _require_square(oriented_center, "oriented_center")
    related = _require_square(oriented_related, "oriented_related")
    center_file, center_rank = center % 8, center // 8
    related_file, related_rank = related % 8, related // 8
    return _VECTOR_TO_DIRECTION.get(
        (related_file - center_file, related_rank - center_rank)
    )


def directional_square(oriented_center: int, direction: str) -> Optional[int]:
    """Return an adjacent square without allowing A/H-file wrapping."""

    center = _require_square(oriented_center, "oriented_center")
    if direction not in DIRECTION_ORDER:
        raise KingBlastEPContractError("unknown KingBlastEP direction")
    file_delta, rank_delta = DIRECTION_VECTORS[direction]
    file_index = center % 8 + file_delta
    rank_index = center // 8 + rank_delta
    if not 0 <= file_index < 8 or not 0 <= rank_index < 8:
        return None
    result = rank_index * 8 + file_index
    if result != center + DIRECTION_DELTAS[direction]:
        raise AssertionError("direction vector and delta disagree")
    return result


@dataclass(frozen=True)
class KingBlastEPActivation:
    index: int
    raw_center: int
    oriented_center: int
    actor_rel: str
    relation_class: str

    @property
    def local_index(self) -> int:
        return self.index

    @property
    def physical_index(self) -> int:
        return PHYSICAL_OFFSET + self.index

    @property
    def en_passant(self) -> bool:
        return self.relation_class == EN_PASSANT_MARKER


def _validate_candidate(
    position: capture_pair.CapturePosition,
    perspective: str,
    candidate: capture_pair.CapturePairActivation,
    occupied: Dict[int, capture_pair.Piece],
    orientation: capture_pair.Orientation,
) -> capture_pair.Piece:
    """Reject an inconsistent upstream record before projecting any rows."""

    if not isinstance(candidate, capture_pair.CapturePairActivation):
        raise KingBlastEPContractError(
            "CapturePair candidate source returned a non-activation"
        )
    actor = occupied.get(candidate.raw_from)
    captured = occupied.get(candidate.raw_captured)
    if actor is None or actor.kind == capture_pair.KING:
        raise KingBlastEPContractError("CapturePair candidate has no non-king actor")
    if captured is None:
        raise KingBlastEPContractError("CapturePair candidate has no captured piece")
    if actor.kind != candidate.actor_kind:
        raise KingBlastEPContractError("CapturePair candidate actor kind is inconsistent")
    if capture_pair.actor_relation(actor.color, perspective) != candidate.actor_rel:
        raise KingBlastEPContractError("CapturePair candidate actor relation is inconsistent")
    if orientation.orient(candidate.raw_from) != candidate.oriented_from:
        raise KingBlastEPContractError("CapturePair candidate origin orientation is inconsistent")
    if orientation.orient(candidate.raw_to) != candidate.oriented_to:
        raise KingBlastEPContractError("CapturePair candidate center orientation is inconsistent")
    if orientation.orient(candidate.raw_captured) != candidate.oriented_captured:
        raise KingBlastEPContractError("CapturePair captured-square orientation is inconsistent")
    if candidate.en_passant:
        if candidate.target_class != "EN_PASSANT":
            raise KingBlastEPContractError("CapturePair EP candidate has a non-EP target class")
        if captured.kind != capture_pair.PAWN or captured.color == actor.color:
            raise KingBlastEPContractError("CapturePair EP captured pawn is inconsistent")
    else:
        target = occupied.get(candidate.raw_to)
        if (
            target is None
            or target.color == actor.color
            or target.kind != candidate.target_class
            or candidate.raw_captured != candidate.raw_to
        ):
            raise KingBlastEPContractError("CapturePair normal target is inconsistent")
    return actor


def enumerate_king_blast_ep(
    position: capture_pair.CapturePosition, perspective: str
) -> Tuple[KingBlastEPActivation, ...]:
    """Project the exact CapturePair candidate set into boolean KBR/EP rows."""

    if not isinstance(position, capture_pair.CapturePosition):
        raise KingBlastEPContractError("position must be a CapturePosition")
    if perspective not in COLORS:
        raise KingBlastEPContractError("perspective must be WHITE or BLACK")

    occupied: Dict[int, capture_pair.Piece] = {
        piece.square: piece for piece in position.pieces
    }
    kings = {}
    for color in COLORS:
        matches = tuple(
            piece
            for piece in position.pieces
            if piece.color == color and piece.kind == capture_pair.KING
        )
        if len(matches) != 1:
            raise KingBlastEPContractError(
                f"position must contain exactly one {color.lower()} king"
            )
        kings[color] = matches[0]

    orientation = capture_pair.orientation_for(position, perspective)
    oriented_kings = {
        color: orientation.orient(piece.square) for color, piece in kings.items()
    }

    # A dictionary keyed by local row implements the schema's boolean set and
    # makes repeated attackers to the same center/relation/class idempotent.
    rows: Dict[int, KingBlastEPActivation] = {}

    def activate(
        raw_center: int,
        oriented_center: int,
        actor_rel: str,
        relation_class: str,
    ) -> None:
        index = king_blast_ep_index(oriented_center, actor_rel, relation_class)
        activation = KingBlastEPActivation(
            index,
            raw_center,
            oriented_center,
            actor_rel,
            relation_class,
        )
        previous = rows.setdefault(index, activation)
        if previous != activation:
            raise KingBlastEPContractError(
                "two KingBlastEP coordinates alias one compact row"
            )

    # No board geometry below creates a candidate.  Every iteration originates
    # in the independent CapturePair oracle, including the validated EP tail.
    candidates = capture_pair.enumerate_capture_pairs(position, perspective)
    for candidate in candidates:
        actor = _validate_candidate(
            position, perspective, candidate, occupied, orientation
        )
        actor_color = actor.color
        enemy_color = _opposite(actor_color)
        raw_center = candidate.raw_to
        oriented_center = candidate.oriented_to

        enemy_king = oriented_kings[enemy_color]
        if enemy_king == oriented_center:
            if candidate.en_passant or candidate.target_class != capture_pair.KING:
                raise KingBlastEPContractError(
                    "only a normal KING target may occupy the enemy-king center"
                )
            activate(
                raw_center,
                oriented_center,
                candidate.actor_rel,
                ENEMY_KING_CENTER,
            )
        else:
            direction = adjacent_direction(oriented_center, enemy_king)
            if direction is not None:
                activate(
                    raw_center,
                    oriented_center,
                    candidate.actor_rel,
                    f"ENEMY_KING_{direction}",
                )

        own_king = oriented_kings[actor_color]
        if own_king == oriented_center:
            raise KingBlastEPContractError(
                "CapturePair candidate cannot target its actor's own king"
            )
        own_direction = adjacent_direction(oriented_center, own_king)
        if own_direction is not None:
            activate(
                raw_center,
                oriented_center,
                candidate.actor_rel,
                f"OWN_KING_{own_direction}",
            )

        if candidate.en_passant:
            activate(
                raw_center,
                oriented_center,
                candidate.actor_rel,
                EN_PASSANT_MARKER,
            )

    result = tuple(rows[index] for index in sorted(rows))
    indices = [activation.index for activation in result]
    if len(indices) != len(set(indices)):
        raise AssertionError("KingBlastEP boolean projection emitted a duplicate row")
    if len(result) > MAX_ACTIVE_FEATURES:
        raise KingBlastEPContractError(
            "KingBlastEP exceeded its 35-row accumulator bound"
        )
    if any(not 0 <= index < PHYSICAL_DIMENSIONS for index in indices):
        raise AssertionError("KingBlastEP emitted an out-of-range local row")
    return result


if len(CLASS_ORDER) != CLASS_DIMENSIONS or len(set(CLASS_ORDER)) != CLASS_DIMENSIONS:
    raise AssertionError("KingBlastEP class order must contain 18 unique classes")
if PHYSICAL_DIMENSIONS != 2_304:
    raise AssertionError("KingBlastEP dimensions do not match the frozen contract")
