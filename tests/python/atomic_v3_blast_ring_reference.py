#!/usr/bin/env python3
"""Independent scalar oracle for the AtomicNNUEV3 BlastRing slice.

CapturePair is the sole source of capturable centers, actor relations and
authenticated en-passant candidates.  This module projects the boolean union
of adjacent collateral outcomes.  It deliberately performs no independent
move generation, attack-map construction, legality filtering or EP inference.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Optional, Set, Tuple

import atomic_v3_capture_pair_reference as capture_pair


WHITE = capture_pair.WHITE
BLACK = capture_pair.BLACK
COLORS = capture_pair.COLORS

OWN = capture_pair.OWN
OPP = capture_pair.OPP
ACTOR_RELATIONS = capture_pair.ACTOR_RELATIONS
COLLATERAL_RELATIONS = (OWN, OPP)

DIRECTION_ORDER = ("N", "NE", "E", "SE", "S", "SW", "W", "NW")
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

KNIGHT = capture_pair.KNIGHT
BISHOP = capture_pair.BISHOP
ROOK = capture_pair.ROOK
QUEEN = capture_pair.QUEEN
ADJACENT_PAWN_SURVIVES = "ADJACENT_PAWN_SURVIVES"
CLASS_ORDER = (KNIGHT, BISHOP, ROOK, QUEEN, ADJACENT_PAWN_SURVIVES)

CENTER_DIMENSIONS = 64
ACTOR_RELATION_DIMENSIONS = 2
COLLATERAL_RELATION_DIMENSIONS = 2
OFFSET_DIMENSIONS = 8
CLASS_DIMENSIONS = 5
PHYSICAL_OFFSET = 64_844
PHYSICAL_DIMENSIONS = (
    CENTER_DIMENSIONS
    * ACTOR_RELATION_DIMENSIONS
    * COLLATERAL_RELATION_DIMENSIONS
    * OFFSET_DIMENSIONS
    * CLASS_DIMENSIONS
)
TRAINING_DIMENSIONS = PHYSICAL_DIMENSIONS
MAX_ACTIVE_FEATURES = 240


class BlastRingContractError(ValueError):
    """Raised when an input or upstream candidate violates the contract."""


def _require_plain_int(value: object, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise BlastRingContractError(f"{label} must be an integer")
    return value


def _require_square(value: object, label: str = "square") -> int:
    result = _require_plain_int(value, label)
    if not 0 <= result < CENTER_DIMENSIONS:
        raise BlastRingContractError(f"{label} must be in 0..63")
    return result


def blast_ring_index(
    oriented_center: int,
    actor_rel: str,
    collateral_rel: str,
    offset: str,
    collateral_class: str,
) -> int:
    """Return the exact compact row for one validated tensor coordinate."""

    center = _require_square(oriented_center, "oriented_center")
    if actor_rel not in ACTOR_RELATIONS:
        raise BlastRingContractError("actor_rel must be OWN or OPP")
    if collateral_rel not in COLLATERAL_RELATIONS:
        raise BlastRingContractError("collateral_rel must be OWN or OPP")
    if offset not in DIRECTION_ORDER:
        raise BlastRingContractError("unknown BlastRing offset")
    if collateral_class not in CLASS_ORDER:
        raise BlastRingContractError("unknown BlastRing collateral_class")

    result = (
        (
            (
                (
                    center * ACTOR_RELATION_DIMENSIONS
                    + ACTOR_RELATIONS.index(actor_rel)
                )
                * COLLATERAL_RELATION_DIMENSIONS
                + COLLATERAL_RELATIONS.index(collateral_rel)
            )
            * OFFSET_DIMENSIONS
            + DIRECTION_ORDER.index(offset)
        )
        * CLASS_DIMENSIONS
        + CLASS_ORDER.index(collateral_class)
    )
    if not 0 <= result < PHYSICAL_DIMENSIONS:
        raise AssertionError("BlastRing index escaped its compact tensor")
    return result


def adjacent_direction(
    oriented_center: int, oriented_collateral: int
) -> Optional[str]:
    """Return the compass offset for an exactly adjacent oriented square."""

    center = _require_square(oriented_center, "oriented_center")
    collateral = _require_square(oriented_collateral, "oriented_collateral")
    center_file, center_rank = center % 8, center // 8
    collateral_file, collateral_rank = collateral % 8, collateral // 8
    return _VECTOR_TO_DIRECTION.get(
        (collateral_file - center_file, collateral_rank - center_rank)
    )


def directional_square(oriented_center: int, offset: str) -> Optional[int]:
    """Return one adjacent square without permitting file wrapping."""

    center = _require_square(oriented_center, "oriented_center")
    if offset not in DIRECTION_ORDER:
        raise BlastRingContractError("unknown BlastRing offset")
    file_delta, rank_delta = DIRECTION_VECTORS[offset]
    file_index = center % 8 + file_delta
    rank_index = center // 8 + rank_delta
    if not 0 <= file_index < 8 or not 0 <= rank_index < 8:
        return None
    result = rank_index * 8 + file_index
    if result != center + DIRECTION_DELTAS[offset]:
        raise AssertionError("direction vector and delta disagree")
    return result


@dataclass(frozen=True)
class BlastRingActivation:
    index: int
    raw_center: int
    oriented_center: int
    actor_rel: str
    raw_collateral: int
    oriented_collateral: int
    collateral_rel: str
    offset: str
    collateral_class: str

    @property
    def local_index(self) -> int:
        return self.index

    @property
    def physical_index(self) -> int:
        return PHYSICAL_OFFSET + self.index

    @property
    def pawn_survives(self) -> bool:
        return self.collateral_class == ADJACENT_PAWN_SURVIVES


@dataclass
class _CaptureCenter:
    raw_center: int
    oriented_center: int
    actor_rel: str
    raw_origins: Set[int]
    ep_captured_pawns: Set[int]


def _validate_position_domain(
    position: capture_pair.CapturePosition, perspective: str
) -> None:
    if not isinstance(position, capture_pair.CapturePosition):
        raise BlastRingContractError("position must be a CapturePosition")
    if perspective not in COLORS:
        raise BlastRingContractError("perspective must be WHITE or BLACK")
    for color in COLORS:
        kings = tuple(
            piece
            for piece in position.pieces
            if piece.color == color and piece.kind == capture_pair.KING
        )
        if len(kings) != 1:
            raise BlastRingContractError(
                f"position must contain exactly one {color.lower()} king"
            )


def _validate_candidate(
    position: capture_pair.CapturePosition,
    perspective: str,
    candidate: capture_pair.CapturePairActivation,
    occupied: Dict[int, capture_pair.Piece],
    orientation: capture_pair.Orientation,
) -> capture_pair.Piece:
    if not isinstance(candidate, capture_pair.CapturePairActivation):
        raise BlastRingContractError(
            "CapturePair candidate source returned a non-activation"
        )

    actor = occupied.get(candidate.raw_from)
    captured = occupied.get(candidate.raw_captured)
    if actor is None or actor.kind == capture_pair.KING:
        raise BlastRingContractError("CapturePair candidate has no non-king actor")
    if captured is None:
        raise BlastRingContractError("CapturePair candidate has no captured piece")
    if actor.kind != candidate.actor_kind:
        raise BlastRingContractError("CapturePair candidate actor kind is inconsistent")
    if capture_pair.actor_relation(actor.color, perspective) != candidate.actor_rel:
        raise BlastRingContractError("CapturePair candidate actor relation is inconsistent")
    if orientation.orient(candidate.raw_from) != candidate.oriented_from:
        raise BlastRingContractError("CapturePair candidate origin orientation is inconsistent")
    if orientation.orient(candidate.raw_to) != candidate.oriented_to:
        raise BlastRingContractError("CapturePair candidate center orientation is inconsistent")
    if orientation.orient(candidate.raw_captured) != candidate.oriented_captured:
        raise BlastRingContractError("CapturePair captured-square orientation is inconsistent")

    try:
        expected_edge = capture_pair.edge_ordinal(
            actor.kind,
            candidate.actor_rel,
            candidate.oriented_from,
            candidate.oriented_to,
        )
    except capture_pair.CapturePairContractError as error:
        raise BlastRingContractError(
            "CapturePair candidate geometry is inconsistent"
        ) from error
    if candidate.edge_ordinal != expected_edge:
        raise BlastRingContractError("CapturePair candidate edge ordinal is inconsistent")

    if candidate.en_passant:
        actor_push = 8 if actor.color == WHITE else -8
        if (
            candidate.target_class != "EN_PASSANT"
            or actor.kind != capture_pair.PAWN
            or actor.color != position.side_to_move
            or position.ep_square != candidate.raw_to
            or candidate.raw_to in occupied
            or candidate.raw_captured != candidate.raw_to - actor_push
            or captured.kind != capture_pair.PAWN
            or captured.color == actor.color
        ):
            raise BlastRingContractError("CapturePair EP candidate is inconsistent")
        try:
            expected_index = capture_pair.en_passant_index(
                candidate.actor_rel,
                candidate.oriented_from,
                candidate.oriented_to,
            )
        except capture_pair.CapturePairContractError as error:
            raise BlastRingContractError(
                "CapturePair EP index is inconsistent"
            ) from error
    else:
        target = occupied.get(candidate.raw_to)
        if (
            target is None
            or target.color == actor.color
            or target.kind != candidate.target_class
            or candidate.raw_captured != candidate.raw_to
        ):
            raise BlastRingContractError("CapturePair normal target is inconsistent")
        try:
            expected_index = capture_pair.normal_index(
                candidate.actor_rel,
                actor.kind,
                candidate.oriented_from,
                candidate.oriented_to,
                target.kind,
            )
        except capture_pair.CapturePairContractError as error:
            raise BlastRingContractError(
                "CapturePair normal index is inconsistent"
            ) from error
    if candidate.local_index != expected_index:
        raise BlastRingContractError("CapturePair candidate local index is inconsistent")
    return actor


def _collateral_class(piece: capture_pair.Piece) -> Optional[str]:
    if piece.kind == capture_pair.KING:
        return None
    if piece.kind == capture_pair.PAWN:
        return ADJACENT_PAWN_SURVIVES
    if piece.kind not in CLASS_ORDER:
        raise BlastRingContractError("unsupported BlastRing collateral piece")
    return piece.kind


def project_blast_ring(
    position: capture_pair.CapturePosition,
    perspective: str,
    candidates: Tuple[capture_pair.CapturePairActivation, ...],
) -> Tuple[BlastRingActivation, ...]:
    """Project one exact trusted CapturePair emission without re-enumerating it."""

    _validate_position_domain(position, perspective)
    orientation = capture_pair.orientation_for(position, perspective)
    occupied: Dict[int, capture_pair.Piece] = {
        piece.square: piece for piece in position.pieces
    }

    # The grouped set of distinct origins is part of the BlastRing semantics
    # and must not be reconstructed from attacks or legal moves.
    groups: Dict[Tuple[int, str], _CaptureCenter] = {}
    previous_candidate_index = -1
    for candidate in candidates:
        actor = _validate_candidate(
            position, perspective, candidate, occupied, orientation
        )
        if candidate.local_index <= previous_candidate_index:
            raise BlastRingContractError(
                "CapturePair candidates must use strict canonical order"
            )
        previous_candidate_index = candidate.local_index
        key = (candidate.oriented_to, candidate.actor_rel)
        group = groups.setdefault(
            key,
            _CaptureCenter(
                candidate.raw_to,
                candidate.oriented_to,
                candidate.actor_rel,
                set(),
                set(),
            ),
        )
        if (
            group.raw_center != candidate.raw_to
            or group.oriented_center != candidate.oriented_to
            or group.actor_rel != candidate.actor_rel
        ):
            raise BlastRingContractError("CapturePair center grouping is inconsistent")
        group.raw_origins.add(actor.square)
        if candidate.en_passant:
            group.ep_captured_pawns.add(candidate.raw_captured)

    rows: Dict[int, BlastRingActivation] = {}
    for group in groups.values():
        sole_origin = (
            next(iter(group.raw_origins)) if len(group.raw_origins) == 1 else None
        )
        for offset in DIRECTION_ORDER:
            oriented_collateral = directional_square(group.oriented_center, offset)
            if oriented_collateral is None:
                continue
            raw_collateral = orientation.orient(oriented_collateral)
            collateral = occupied.get(raw_collateral)
            if collateral is None:
                continue
            collateral_class = _collateral_class(collateral)
            if collateral_class is None:
                continue
            if raw_collateral == sole_origin:
                continue
            if raw_collateral in group.ep_captured_pawns:
                continue

            collateral_rel = capture_pair.actor_relation(
                collateral.color, perspective
            )
            index = blast_ring_index(
                group.oriented_center,
                group.actor_rel,
                collateral_rel,
                offset,
                collateral_class,
            )
            activation = BlastRingActivation(
                index,
                group.raw_center,
                group.oriented_center,
                group.actor_rel,
                raw_collateral,
                oriented_collateral,
                collateral_rel,
                offset,
                collateral_class,
            )
            previous = rows.setdefault(index, activation)
            if previous != activation:
                raise BlastRingContractError(
                    "two BlastRing coordinates alias one compact row"
                )

    result = tuple(rows[index] for index in sorted(rows))
    indices = [activation.local_index for activation in result]
    if indices != sorted(set(indices)):
        raise AssertionError("BlastRing emitted duplicate or unordered rows")
    if len(result) > MAX_ACTIVE_FEATURES:
        raise BlastRingContractError(
            "BlastRing exceeded its 240-row accumulator bound"
        )
    if any(not 0 <= index < PHYSICAL_DIMENSIONS for index in indices):
        raise AssertionError("BlastRing emitted an out-of-range local row")
    return result


def enumerate_blast_ring(
    position: capture_pair.CapturePosition, perspective: str
) -> Tuple[BlastRingActivation, ...]:
    """Enumerate CapturePair once, then project its exact collateral rows."""

    _validate_position_domain(position, perspective)
    candidates = capture_pair.enumerate_capture_pairs(position, perspective)
    return project_blast_ring(position, perspective, candidates)


if len(DIRECTION_ORDER) != OFFSET_DIMENSIONS or len(set(DIRECTION_ORDER)) != 8:
    raise AssertionError("BlastRing offset order must contain eight unique offsets")
if len(CLASS_ORDER) != CLASS_DIMENSIONS or len(set(CLASS_ORDER)) != 5:
    raise AssertionError("BlastRing class order must contain five unique classes")
if PHYSICAL_DIMENSIONS != 10_240:
    raise AssertionError("BlastRing dimensions do not match the frozen contract")
