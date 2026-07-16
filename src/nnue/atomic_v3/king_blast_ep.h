/*
  Atomic-Stockfish, a UCI chess playing engine derived from Stockfish
  Copyright (C) 2004-2026 The Stockfish developers (see AUTHORS file)

  Atomic-Stockfish is free software: you can redistribute it and/or modify
  it under the terms of the GNU General Public License as published by
  the Free Software Foundation, either version 3 of the License, or
  (at your option) any later version.
*/

#ifndef ATOMIC_V3_KING_BLAST_EP_H_INCLUDED
#define ATOMIC_V3_KING_BLAST_EP_H_INCLUDED

#include <array>

#include "../../types.h"
#include "../nnue_common.h"
#include "capture_pair.h"
#include "orientation.h"

namespace Stockfish {
class Position;
}

namespace Stockfish::Eval::NNUE::AtomicV3 {

inline constexpr IndexType KingBlastEpCenterDimensions   = 64;
inline constexpr IndexType KingBlastEpActorRelations     = 2;
inline constexpr IndexType KingBlastEpRelationClasses    = 18;
inline constexpr IndexType KingBlastEpPhysicalDimensions = 2304;
inline constexpr IndexType KingBlastEpPhysicalOffset =
  CapturePairPhysicalOffset + CapturePairPhysicalDimensions;
inline constexpr IndexType KingBlastEpMaximumActiveFeatures = 35;

// Enemy/own is relative to the capture actor. Direction names are measured
// from the jointly oriented capture center, never from a second actor-relative
// board transform.
enum class KingBlastEpRelationClass : u8 {
    EnemyKingCenter,
    EnemyKingN,
    EnemyKingNE,
    EnemyKingE,
    EnemyKingSE,
    EnemyKingS,
    EnemyKingSW,
    EnemyKingW,
    EnemyKingNW,
    OwnKingN,
    OwnKingNE,
    OwnKingE,
    OwnKingSE,
    OwnKingS,
    OwnKingSW,
    OwnKingW,
    OwnKingNW,
    EnPassantMarker
};

// KingBlastEP is a strict projection of CapturePair. Reusing the error domain
// makes propagation lossless; in particular malformed optional EP metadata is
// still a successful CP/KBR emission with no EP marker.
using KingBlastEpError = CapturePairError;

struct KingBlastEpFeature {
    Square                   rawCenter           = SQ_NONE;
    Square                   orientedCenter      = SQ_NONE;
    Square                   rawRelatedKing      = SQ_NONE;
    Square                   orientedRelatedKing = SQ_NONE;
    CapturePairActorRelation actorRelation       = CapturePairActorRelation::Own;
    KingBlastEpRelationClass relationClass       = KingBlastEpRelationClass::EnemyKingCenter;
    IndexType                localIndex          = 0;
    IndexType                physicalIndex       = 0;
    bool                     enPassantMarker     = false;
};

struct KingBlastEpEmission {
    JointOrientation                                                 orientation{};
    std::array<KingBlastEpFeature, KingBlastEpMaximumActiveFeatures> features{};
    IndexType                                                        size = 0;
};

constexpr bool valid_king_blast_ep_class(KingBlastEpRelationClass relationClass) {
    return IndexType(relationClass) < KingBlastEpRelationClasses;
}

constexpr bool king_blast_ep_index(Square                   orientedCenter,
                                   CapturePairActorRelation actorRelation,
                                   KingBlastEpRelationClass relationClass,
                                   IndexType&               result) {
    if (!is_ok(orientedCenter) || !valid_capture_pair_relation(actorRelation)
        || !valid_king_blast_ep_class(relationClass))
        return false;

    result = (IndexType(orientedCenter) * KingBlastEpActorRelations + IndexType(actorRelation))
             * KingBlastEpRelationClasses
           + IndexType(relationClass);
    return true;
}

// Maps an adjacent related king to one of the eight directional classes. A
// king on the center is accepted only for enemyOfActor and maps to class zero.
// Explicit file/rank deltas make edge wrapping impossible.
constexpr bool king_blast_ep_relation_class(Square                    orientedCenter,
                                            Square                    orientedRelatedKing,
                                            bool                      enemyOfActor,
                                            KingBlastEpRelationClass& result) {
    if (!is_ok(orientedCenter) || !is_ok(orientedRelatedKing))
        return false;

    const int fileDelta = int(file_of(orientedRelatedKing)) - int(file_of(orientedCenter));
    const int rankDelta = int(rank_of(orientedRelatedKing)) - int(rank_of(orientedCenter));

    if (enemyOfActor && fileDelta == 0 && rankDelta == 0)
    {
        result = KingBlastEpRelationClass::EnemyKingCenter;
        return true;
    }
    if (fileDelta < -1 || fileDelta > 1 || rankDelta < -1 || rankDelta > 1
        || (fileDelta == 0 && rankDelta == 0))
        return false;

    IndexType direction = 0;
    if (fileDelta == 0 && rankDelta == 1)
        direction = 0;  // N
    else if (fileDelta == 1 && rankDelta == 1)
        direction = 1;  // NE
    else if (fileDelta == 1 && rankDelta == 0)
        direction = 2;  // E
    else if (fileDelta == 1 && rankDelta == -1)
        direction = 3;  // SE
    else if (fileDelta == 0 && rankDelta == -1)
        direction = 4;  // S
    else if (fileDelta == -1 && rankDelta == -1)
        direction = 5;  // SW
    else if (fileDelta == -1 && rankDelta == 0)
        direction = 6;  // W
    else if (fileDelta == -1 && rankDelta == 1)
        direction = 7;  // NW
    else
        return false;

    result = KingBlastEpRelationClass((enemyOfActor ? 1 : 9) + direction);
    return true;
}

namespace Detail {

// Internal trusted composition seam. Precondition: capturePairs is the exact
// successful output of emit_capture_pairs(snapshot, perspective), not an
// arbitrary caller-assembled subset. The defensive checks below prove its
// canonical representation but cannot prove completeness without enumerating
// CP again. This lets the combined V3 refresh path and BlastRing reuse CP once.
// The projector uses only caller-owned scratch and retains no input reference.
KingBlastEpError project_king_blast_ep(const CapturePairSnapshot& snapshot,
                                       Color                      perspective,
                                       const CapturePairEmission& capturePairs,
                                       KingBlastEpEmission&       result);

// Faster internal seam for a CapturePair emission produced in the same call
// chain. The caller owns the proof that capturePairs is exact and canonical.
KingBlastEpError project_king_blast_ep_trusted(const CapturePairSnapshot& snapshot,
                                               Color                      perspective,
                                               const CapturePairEmission& capturePairs,
                                               KingBlastEpEmission&       result);

}  // namespace Detail

KingBlastEpError emit_king_blast_ep(const CapturePairSnapshot& snapshot,
                                    Color                      perspective,
                                    KingBlastEpEmission&       result);

KingBlastEpError
emit_king_blast_ep(const Position& position, Color perspective, KingBlastEpEmission& result);

const char* king_blast_ep_error_message(KingBlastEpError error);

static_assert(KingBlastEpPhysicalOffset == 62540);
static_assert(KingBlastEpPhysicalDimensions
              == KingBlastEpCenterDimensions * KingBlastEpActorRelations
                   * KingBlastEpRelationClasses);
static_assert(KingBlastEpPhysicalOffset + KingBlastEpPhysicalDimensions == 64844);

}  // namespace Stockfish::Eval::NNUE::AtomicV3

#endif  // ATOMIC_V3_KING_BLAST_EP_H_INCLUDED
