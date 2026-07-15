/*
  Atomic-Stockfish, a UCI chess playing engine derived from Stockfish
  Copyright (C) 2004-2026 The Stockfish developers (see AUTHORS file)

  Atomic-Stockfish is free software: you can redistribute it and/or modify
  it under the terms of the GNU General Public License as published by
  the Free Software Foundation, either version 3 of the License, or
  (at your option) any later version.
*/

#ifndef ATOMIC_V3_BLAST_RING_H_INCLUDED
#define ATOMIC_V3_BLAST_RING_H_INCLUDED

#include <array>

#include "../../types.h"
#include "../nnue_common.h"
#include "capture_pair.h"
#include "king_blast_ep.h"
#include "orientation.h"

namespace Stockfish {
class Position;
}

namespace Stockfish::Eval::NNUE::AtomicV3 {

inline constexpr IndexType BlastRingCenterDimensions    = 64;
inline constexpr IndexType BlastRingActorRelations      = 2;
inline constexpr IndexType BlastRingCollateralRelations = 2;
inline constexpr IndexType BlastRingDirections          = 8;
inline constexpr IndexType BlastRingCollateralClasses   = 5;
inline constexpr IndexType BlastRingPhysicalDimensions  = 10240;
inline constexpr IndexType BlastRingPhysicalOffset =
  KingBlastEpPhysicalOffset + KingBlastEpPhysicalDimensions;
inline constexpr IndexType BlastRingMaximumActiveFeatures = 240;

// Collateral ownership is relative to the accumulator perspective, not to the
// capture actor. It deliberately has a distinct type from ActorRelation so the
// two tensor coordinates cannot be swapped accidentally.
enum class BlastRingCollateralRelation : u8 {
    Own,
    Opp
};

// Directions are measured once in the joint perspective orientation. No
// actor-relative transform is applied after CapturePair has supplied a center.
enum class BlastRingDirection : u8 {
    N,
    NE,
    E,
    SE,
    S,
    SW,
    W,
    NW
};

// Non-pawns adjacent to an atomic capture center explode. Adjacent pawns are
// immune to the blast and therefore use an explicit survival class.
enum class BlastRingCollateralClass : u8 {
    Knight,
    Bishop,
    Rook,
    Queen,
    AdjacentPawnSurvives
};

// BlastRing is a strict projection of CapturePair, so its public path
// propagates the CapturePair error domain losslessly.
using BlastRingError = CapturePairError;

struct BlastRingFeature {
    Square                      rawCenter            = SQ_NONE;
    Square                      orientedCenter       = SQ_NONE;
    Square                      rawCollateral        = SQ_NONE;
    Square                      orientedCollateral   = SQ_NONE;
    Piece                       collateral           = NO_PIECE;
    CapturePairActorRelation    actorRelation        = CapturePairActorRelation::Own;
    BlastRingCollateralRelation collateralRelation   = BlastRingCollateralRelation::Own;
    BlastRingDirection          direction            = BlastRingDirection::N;
    BlastRingCollateralClass    collateralClass      = BlastRingCollateralClass::Knight;
    IndexType                   localIndex           = 0;
    IndexType                   physicalIndex        = 0;
    bool                        adjacentPawnSurvives = false;
};

struct BlastRingEmission {
    JointOrientation                                             orientation{};
    std::array<BlastRingFeature, BlastRingMaximumActiveFeatures> features{};
    IndexType                                                    size = 0;
};

constexpr bool valid_blast_ring_collateral_relation(BlastRingCollateralRelation relation) {
    return relation == BlastRingCollateralRelation::Own
        || relation == BlastRingCollateralRelation::Opp;
}

constexpr bool valid_blast_ring_direction(BlastRingDirection direction) {
    return IndexType(direction) < BlastRingDirections;
}

constexpr bool valid_blast_ring_class(BlastRingCollateralClass collateralClass) {
    return IndexType(collateralClass) < BlastRingCollateralClasses;
}

constexpr bool blast_ring_index(Square                      orientedCenter,
                                CapturePairActorRelation    actorRelation,
                                BlastRingCollateralRelation collateralRelation,
                                BlastRingDirection          direction,
                                BlastRingCollateralClass    collateralClass,
                                IndexType&                  result) {
    if (!is_ok(orientedCenter) || !valid_capture_pair_relation(actorRelation)
        || !valid_blast_ring_collateral_relation(collateralRelation)
        || !valid_blast_ring_direction(direction) || !valid_blast_ring_class(collateralClass))
        return false;

    result = ((((IndexType(orientedCenter) * BlastRingActorRelations + IndexType(actorRelation))
                  * BlastRingCollateralRelations
                + IndexType(collateralRelation))
                 * BlastRingDirections
               + IndexType(direction))
              * BlastRingCollateralClasses)
           + IndexType(collateralClass);
    return true;
}

// Resolves one compass direction without permitting file wrapping.
constexpr bool
blast_ring_directional_square(Square orientedCenter, BlastRingDirection direction, Square& result) {
    if (!is_ok(orientedCenter) || !valid_blast_ring_direction(direction))
        return false;

    constexpr std::array<int, BlastRingDirections> FileDeltas{0, 1, 1, 1, 0, -1, -1, -1};
    constexpr std::array<int, BlastRingDirections> RankDeltas{1, 1, 0, -1, -1, -1, 0, 1};
    const IndexType                                directionIndex = IndexType(direction);
    const int file = int(file_of(orientedCenter)) + FileDeltas[directionIndex];
    const int rank = int(rank_of(orientedCenter)) + RankDeltas[directionIndex];
    if (file < int(FILE_A) || file > int(FILE_H) || rank < int(RANK_1) || rank > int(RANK_8))
        return false;

    result = make_square(File(file), Rank(rank));
    return true;
}

constexpr bool
blast_ring_direction(Square orientedCenter, Square orientedCollateral, BlastRingDirection& result) {
    if (!is_ok(orientedCenter) || !is_ok(orientedCollateral))
        return false;
    for (IndexType direction = 0; direction < BlastRingDirections; ++direction)
    {
        Square candidate = SQ_NONE;
        if (blast_ring_directional_square(orientedCenter, BlastRingDirection(direction), candidate)
            && candidate == orientedCollateral)
        {
            result = BlastRingDirection(direction);
            return true;
        }
    }
    return false;
}

namespace Detail {

// Internal trusted composition seam. Precondition: capturePairs is the exact
// successful output of emit_capture_pairs(snapshot, perspective), not an
// arbitrary caller-assembled subset. Defensive validation proves shape,
// orientation, indexing and order, but intentionally does not enumerate CP a
// second time and therefore cannot authenticate subset completeness. This is
// the seam used by a combined V3 refresh to share one CapturePair emission.
BlastRingError project_blast_ring(const CapturePairSnapshot& snapshot,
                                  Color                      perspective,
                                  const CapturePairEmission& capturePairs,
                                  BlastRingEmission&         result);

}  // namespace Detail

BlastRingError
emit_blast_ring(const CapturePairSnapshot& snapshot, Color perspective, BlastRingEmission& result);

BlastRingError
emit_blast_ring(const Position& position, Color perspective, BlastRingEmission& result);

const char* blast_ring_error_message(BlastRingError error);

static_assert(BlastRingPhysicalOffset == 64844);
static_assert(BlastRingPhysicalDimensions
              == BlastRingCenterDimensions * BlastRingActorRelations * BlastRingCollateralRelations
                   * BlastRingDirections * BlastRingCollateralClasses);
static_assert(BlastRingPhysicalOffset + BlastRingPhysicalDimensions == 75084);

}  // namespace Stockfish::Eval::NNUE::AtomicV3

#endif  // ATOMIC_V3_BLAST_RING_H_INCLUDED
