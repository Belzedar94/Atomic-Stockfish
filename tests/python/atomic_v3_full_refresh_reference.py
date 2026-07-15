#!/usr/bin/env python3
"""Independent scalar composition oracle for one AtomicNNUEV3 perspective.

The coordinator intentionally performs one HM enumeration and one CapturePair
enumeration. The exact immutable CapturePair tuple is then supplied to both
relation projectors. This module models composition and ownership only; each
slice retains its separately tested semantic oracle.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Tuple

import atomic_v3_blast_ring_reference as blast_ring
import atomic_v3_capture_pair_reference as capture_pair
import atomic_v3_hm_reference as hm
import atomic_v3_king_blast_ep_reference as king_blast_ep


MAX_ACTIVE_FEATURES = (
    32
    + capture_pair.MAX_ACTIVE_FEATURES
    + king_blast_ep.MAX_ACTIVE_FEATURES
    + blast_ring.MAX_ACTIVE_FEATURES
)
PHYSICAL_DIMENSIONS = blast_ring.PHYSICAL_OFFSET + blast_ring.PHYSICAL_DIMENSIONS


class FullRefreshContractError(ValueError):
    """Raised when independently composed slices disagree on their frame."""


@dataclass(frozen=True)
class FullRefreshEmission:
    hm: Tuple[hm.HMActivation, ...]
    capture_pairs: Tuple[capture_pair.CapturePairActivation, ...]
    king_blast_ep: Tuple[king_blast_ep.KingBlastEPActivation, ...]
    blast_ring: Tuple[blast_ring.BlastRingActivation, ...]
    orientation: capture_pair.Orientation
    network_bucket: int

    @property
    def active_feature_count(self) -> int:
        return (
            len(self.hm)
            + len(self.capture_pairs)
            + len(self.king_blast_ep)
            + len(self.blast_ring)
        )

    def physical_indices(self) -> dict[str, Tuple[int, ...]]:
        return {
            "hm": tuple(row.physical_index for row in self.hm),
            "capture_pair": tuple(row.physical_index for row in self.capture_pairs),
            "king_blast_ep": tuple(row.physical_index for row in self.king_blast_ep),
            "blast_ring": tuple(row.physical_index for row in self.blast_ring),
        }


def as_hm_position(position: capture_pair.CapturePosition) -> hm.HMPosition:
    return hm.HMPosition(
        tuple(hm.Piece(piece.color, piece.kind, piece.square) for piece in position.pieces),
        side_to_move=position.side_to_move,
        ep_square=position.ep_square,
        atomic960=position.atomic960,
        castling_rights=position.castling_rights,
    )


def enumerate_full_refresh(
    position: capture_pair.CapturePosition, perspective: str
) -> FullRefreshEmission:
    """Compose all four slices with exact HM-once and CapturePair-once flow."""

    if not isinstance(position, capture_pair.CapturePosition):
        raise FullRefreshContractError("position must be a CapturePosition")
    if perspective not in capture_pair.COLORS:
        raise FullRefreshContractError("perspective must be WHITE or BLACK")

    hm_position = as_hm_position(position)
    hm_rows = hm.enumerate_hm(hm_position, perspective)
    capture_rows = capture_pair.enumerate_capture_pairs(position, perspective)
    king_rows = king_blast_ep.project_king_blast_ep(
        position, perspective, capture_rows
    )
    ring_rows = blast_ring.project_blast_ring(position, perspective, capture_rows)

    cp_orientation = capture_pair.orientation_for(position, perspective)
    own_king = next(
        piece
        for piece in hm_position.pieces
        if piece.color == perspective and piece.kind == "KING"
    )
    hm_orientation = hm.orientation_for(perspective, own_king.square)
    if (
        cp_orientation.perspective != hm_orientation.perspective
        or cp_orientation.vertical_xor != hm_orientation.vertical_xor
        or cp_orientation.horizontal_xor != hm_orientation.horizontal_xor
        or cp_orientation.oriented_own_king != hm_orientation.oriented_own_king
    ):
        raise FullRefreshContractError("HM and relation slices mixed orientations")

    piece_count = len(position.pieces)
    network_bucket = (piece_count - 1) // 4
    if not 0 <= network_bucket < 8:
        raise FullRefreshContractError("piece count escaped the eight network buckets")

    result = FullRefreshEmission(
        hm_rows,
        capture_rows,
        king_rows,
        ring_rows,
        cp_orientation,
        network_bucket,
    )
    if result.active_feature_count > MAX_ACTIVE_FEATURES:
        raise FullRefreshContractError("full refresh exceeded its 547-row bound")

    indices = result.physical_indices()
    if any(not 0 <= row < PHYSICAL_DIMENSIONS for rows in indices.values() for row in rows):
        raise AssertionError("full refresh emitted a physical row outside V3")
    return result


if MAX_ACTIVE_FEATURES != 547:
    raise AssertionError("full-refresh active bound drifted")
if PHYSICAL_DIMENSIONS != 75_084:
    raise AssertionError("full-refresh physical dimensions drifted")
