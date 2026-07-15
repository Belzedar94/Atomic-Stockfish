/*
  Atomic-Stockfish, a UCI chess playing engine derived from Stockfish
  Copyright (C) 2004-2026 The Stockfish developers (see AUTHORS file)

  Atomic-Stockfish is free software: you can redistribute it and/or modify
  it under the terms of the GNU General Public License as published by
  the Free Software Foundation, either version 3 of the License, or
  (at your option) any later version.
*/

#ifndef ATOMIC_V3_CAPTURE_PAIR_H_INCLUDED
#define ATOMIC_V3_CAPTURE_PAIR_H_INCLUDED

#include <array>

#include "../../types.h"
#include "../nnue_common.h"
#include "hm_oracle.h"
#include "orientation.h"

namespace Stockfish {
class Position;
}

namespace Stockfish::Eval::NNUE::AtomicV3 {

inline constexpr IndexType CapturePairActorRelations        = 2;
inline constexpr IndexType CapturePairGeometryDimensions    = 3332;
inline constexpr IndexType CapturePairNormalTargetClasses   = 6;
inline constexpr IndexType CapturePairNormalDimensions      = 39984;
inline constexpr IndexType CapturePairEpEdgesPerRelation    = 14;
inline constexpr IndexType CapturePairEpDimensions          = 28;
inline constexpr IndexType CapturePairPhysicalDimensions    = 40012;
inline constexpr IndexType CapturePairPhysicalOffset        = HmPhysicalDimensions;
inline constexpr IndexType CapturePairMaximumActiveFeatures = 240;

enum class CapturePairActorRelation : u8 {
    Own,
    Opp
};

enum class CapturePairTargetClass : u8 {
    Pawn,
    Knight,
    Bishop,
    Rook,
    Queen,
    King,
    EnPassant
};

enum class CapturePairError : u8 {
    None,
    InvalidPerspective,
    InvalidSideToMove,
    MissingWhiteKing,
    MissingBlackKing,
    MultipleWhiteKings,
    MultipleBlackKings,
    TooManyPiecesPerColor,
    TooManyPieces,
    InvalidPiece,
    TooManyFeatures,
    NonCanonicalOrder
};

struct CapturePairSnapshot {
    HmBoard board{};
    Color   sideToMove = WHITE;
    Square  epSquare   = SQ_NONE;
};

struct CapturePairFeature {
    Square                   rawFrom          = SQ_NONE;
    Square                   rawCenter        = SQ_NONE;
    Square                   rawCaptured      = SQ_NONE;
    Square                   orientedFrom     = SQ_NONE;
    Square                   orientedCenter   = SQ_NONE;
    Square                   orientedCaptured = SQ_NONE;
    Piece                    actor            = NO_PIECE;
    Piece                    captured         = NO_PIECE;
    CapturePairActorRelation actorRelation    = CapturePairActorRelation::Own;
    CapturePairTargetClass   targetClass      = CapturePairTargetClass::Pawn;
    IndexType                edgeOrdinal      = 0;
    IndexType                epOrdinal        = 0;
    IndexType                localIndex       = 0;
    IndexType                physicalIndex    = 0;
    bool                     enPassant        = false;
};

struct CapturePairEmission {
    JointOrientation                                                 orientation{};
    std::array<CapturePairFeature, CapturePairMaximumActiveFeatures> features{};
    IndexType                                                        size = 0;
};

constexpr bool valid_capture_pair_relation(CapturePairActorRelation relation) {
    return relation == CapturePairActorRelation::Own || relation == CapturePairActorRelation::Opp;
}

constexpr IndexType capture_pair_geometry_base(PieceType pieceType) {
    return pieceType == PAWN   ? 0
         : pieceType == KNIGHT ? 84
         : pieceType == BISHOP ? 420
         : pieceType == ROOK   ? 980
         : pieceType == QUEEN  ? 1876
                               : CapturePairGeometryDimensions;
}

constexpr bool capture_pair_geometric_edge(PieceType                pieceType,
                                           CapturePairActorRelation relation,
                                           Square                   from,
                                           Square                   to) {
    if (!valid_capture_pair_relation(relation) || !is_ok(from) || !is_ok(to) || from == to
        || pieceType < PAWN || pieceType > QUEEN)
        return false;

    const int fileDelta = int(file_of(to)) - int(file_of(from));
    const int rankDelta = int(rank_of(to)) - int(rank_of(from));
    const int absFile   = fileDelta < 0 ? -fileDelta : fileDelta;
    const int absRank   = rankDelta < 0 ? -rankDelta : rankDelta;

    if (pieceType == PAWN)
    {
        if (rank_of(from) < RANK_2 || rank_of(from) > RANK_7 || absFile != 1)
            return false;
        return rankDelta == (relation == CapturePairActorRelation::Own ? 1 : -1);
    }
    if (pieceType == KNIGHT)
        return (absFile == 1 && absRank == 2) || (absFile == 2 && absRank == 1);
    if (pieceType == BISHOP)
        return absFile == absRank;
    if (pieceType == ROOK)
        return (fileDelta == 0) != (rankDelta == 0);
    return absFile == absRank || ((fileDelta == 0) != (rankDelta == 0));
}

constexpr IndexType capture_pair_geometry_count(PieceType                pieceType,
                                                CapturePairActorRelation relation) {
    IndexType count = 0;
    for (int from = 0; from < SQUARE_NB; ++from)
        for (int to = 0; to < SQUARE_NB; ++to)
            count += capture_pair_geometric_edge(pieceType, relation, Square(from), Square(to));
    return count;
}

constexpr bool capture_pair_edge_ordinal(PieceType                pieceType,
                                         CapturePairActorRelation relation,
                                         Square                   from,
                                         Square                   to,
                                         IndexType&               result) {
    if (!capture_pair_geometric_edge(pieceType, relation, from, to))
        return false;

    IndexType ordinal = capture_pair_geometry_base(pieceType);
    for (int candidateFrom = 0; candidateFrom < SQUARE_NB; ++candidateFrom)
        for (int candidateTo = 0; candidateTo < SQUARE_NB; ++candidateTo)
        {
            if (!capture_pair_geometric_edge(pieceType, relation, Square(candidateFrom),
                                             Square(candidateTo)))
                continue;
            if (candidateFrom == int(from) && candidateTo == int(to))
            {
                result = ordinal;
                return true;
            }
            ++ordinal;
        }
    return false;
}

constexpr bool capture_pair_normal_index(CapturePairActorRelation relation,
                                         IndexType                edgeOrdinal,
                                         CapturePairTargetClass   targetClass,
                                         IndexType&               result) {
    if (!valid_capture_pair_relation(relation) || edgeOrdinal >= CapturePairGeometryDimensions
        || IndexType(targetClass) >= CapturePairNormalTargetClasses)
        return false;
    result = (IndexType(relation) * CapturePairGeometryDimensions + edgeOrdinal)
             * CapturePairNormalTargetClasses
           + IndexType(targetClass);
    return true;
}

constexpr bool capture_pair_ep_edge(CapturePairActorRelation relation, Square from, Square center) {
    if (!capture_pair_geometric_edge(PAWN, relation, from, center))
        return false;
    return relation == CapturePairActorRelation::Own
           ? rank_of(from) == RANK_5 && rank_of(center) == RANK_6
           : rank_of(from) == RANK_4 && rank_of(center) == RANK_3;
}

constexpr bool capture_pair_ep_ordinal(CapturePairActorRelation relation,
                                       Square                   from,
                                       Square                   center,
                                       IndexType&               result) {
    if (!capture_pair_ep_edge(relation, from, center))
        return false;
    IndexType ordinal = 0;
    for (int candidateFrom = 0; candidateFrom < SQUARE_NB; ++candidateFrom)
        for (int candidateCenter = 0; candidateCenter < SQUARE_NB; ++candidateCenter)
        {
            if (!capture_pair_ep_edge(relation, Square(candidateFrom), Square(candidateCenter)))
                continue;
            if (candidateFrom == int(from) && candidateCenter == int(center))
            {
                result = ordinal;
                return true;
            }
            ++ordinal;
        }
    return false;
}

constexpr bool
capture_pair_ep_index(CapturePairActorRelation relation, IndexType epOrdinal, IndexType& result) {
    if (!valid_capture_pair_relation(relation) || epOrdinal >= CapturePairEpEdgesPerRelation)
        return false;
    result =
      CapturePairNormalDimensions + IndexType(relation) * CapturePairEpEdgesPerRelation + epOrdinal;
    return true;
}

namespace Detail {

// Shared defensive validator for trusted projection seams. Precondition:
// emission is the exact successful output of emit_capture_pairs(snapshot,
// perspective). These checks authenticate its canonical representation and
// snapshot domain, but deliberately do not re-enumerate CP and therefore
// cannot prove that an arbitrary caller-supplied subset is complete.
bool well_formed_capture_pair_emission(const CapturePairSnapshot& snapshot,
                                       Color                      perspective,
                                       const CapturePairEmission& emission);

}  // namespace Detail

CapturePairError emit_capture_pairs(const CapturePairSnapshot& snapshot,
                                    Color                      perspective,
                                    CapturePairEmission&       result);

CapturePairError
emit_capture_pairs(const Position& position, Color perspective, CapturePairEmission& result);

const char* capture_pair_error_message(CapturePairError error);

static_assert(capture_pair_geometry_count(PAWN, CapturePairActorRelation::Own) == 84);
static_assert(capture_pair_geometry_count(PAWN, CapturePairActorRelation::Opp) == 84);
static_assert(capture_pair_geometry_count(KNIGHT, CapturePairActorRelation::Own) == 336);
static_assert(capture_pair_geometry_count(BISHOP, CapturePairActorRelation::Own) == 560);
static_assert(capture_pair_geometry_count(ROOK, CapturePairActorRelation::Own) == 896);
static_assert(capture_pair_geometry_count(QUEEN, CapturePairActorRelation::Own) == 1456);
static_assert(CapturePairNormalDimensions
              == CapturePairActorRelations * CapturePairGeometryDimensions
                   * CapturePairNormalTargetClasses);
static_assert(CapturePairEpDimensions == CapturePairActorRelations * CapturePairEpEdgesPerRelation);
static_assert(CapturePairPhysicalDimensions
              == CapturePairNormalDimensions + CapturePairEpDimensions);

}  // namespace Stockfish::Eval::NNUE::AtomicV3

#endif  // ATOMIC_V3_CAPTURE_PAIR_H_INCLUDED
