/*
  Atomic-Stockfish, a UCI chess playing engine derived from Stockfish
  Copyright (C) 2004-2026 The Stockfish developers (see AUTHORS file)

  Atomic-Stockfish is free software: you can redistribute it and/or modify
  it under the terms of the GNU General Public License as published by
  the Free Software Foundation, either version 3 of the License, or
  (at your option) any later version.
*/

#ifndef ATOMIC_V3_HM_ORACLE_H_INCLUDED
#define ATOMIC_V3_HM_ORACLE_H_INCLUDED

#include <array>

#include "../../types.h"
#include "../nnue_common.h"
#include "orientation.h"

namespace Stockfish {
class Position;
}

namespace Stockfish::Eval::NNUE::AtomicV3 {

inline constexpr IndexType HmKingBuckets             = 32;
inline constexpr IndexType HmSquareDimensions        = 64;
inline constexpr IndexType HmTrainingPlanes          = 12;
inline constexpr IndexType HmPhysicalPlanes          = 11;
inline constexpr IndexType HmTrainingRowsPerBucket   = HmTrainingPlanes * HmSquareDimensions;
inline constexpr IndexType HmPhysicalRowsPerBucket   = HmPhysicalPlanes * HmSquareDimensions;
inline constexpr IndexType HmTrainingDimensions      = HmKingBuckets * HmTrainingRowsPerBucket;
inline constexpr IndexType HmPhysicalDimensions      = HmKingBuckets * HmPhysicalRowsPerBucket;
inline constexpr IndexType HmVirtualDimensions       = HmTrainingRowsPerBucket;
inline constexpr IndexType HmAccumulatorOutputs      = 1024;
inline constexpr IndexType HmPsqtOutputs             = 8;
inline constexpr IndexType HmCoalescedOutputs        = HmAccumulatorOutputs + HmPsqtOutputs;
inline constexpr IndexType HmMaximumActiveDimensions = 32;
inline constexpr IndexType HmMaximumPiecesPerColor   = 16;

enum class HmTrainingPlane : u8 {
    OwnPawn,
    OppPawn,
    OwnKnight,
    OppKnight,
    OwnBishop,
    OppBishop,
    OwnRook,
    OppRook,
    OwnQueen,
    OppQueen,
    OwnKing,
    OppKing
};

enum class HmPhysicalPlane : u8 {
    OwnPawn,
    OppPawn,
    OwnKnight,
    OppKnight,
    OwnBishop,
    OppBishop,
    OwnRook,
    OppRook,
    OwnQueen,
    OppQueen,
    MergedKing
};

enum class HmOutputKind : u8 {
    Accumulator,
    Psqt
};

enum class HmOracleError : u8 {
    None,
    InvalidPerspective,
    MissingWhiteKing,
    MissingBlackKing,
    MultipleWhiteKings,
    MultipleBlackKings,
    TooManyPiecesPerColor,
    TooManyPieces,
    InvalidPiece
};

struct HmFeature {
    Square          boardSquare    = SQ_NONE;
    Square          orientedSquare = SQ_NONE;
    Piece           piece          = NO_PIECE;
    HmTrainingPlane trainingPlane  = HmTrainingPlane::OwnPawn;
    HmPhysicalPlane physicalPlane  = HmPhysicalPlane::OwnPawn;
    IndexType       trainingIndex  = 0;
    IndexType       virtualIndex   = 0;
    IndexType       physicalIndex  = 0;
    IndexType       psqtRow        = 0;
};

struct HmEmission {
    JointOrientation                                 orientation{};
    std::array<HmFeature, HmMaximumActiveDimensions> features{};
    IndexType                                        size          = 0;
    IndexType                                        networkBucket = 0;
};

using HmBoard = std::array<Piece, SQUARE_NB>;

// One scalar address mapping for the factorized 12-plane trainer export. The
// same bucket/virtual source and coalesced source output feed either one of the
// 1,024 accumulator columns or one of the eight HM-only PSQT columns.
struct HmExportSource {
    HmTrainingPlane trainingPlane     = HmTrainingPlane::OwnPawn;
    IndexType       physicalRow       = 0;
    IndexType       bucketWeightRow   = 0;
    IndexType       virtualWeightRow  = 0;
    IndexType       sourceOutput      = 0;
    HmOutputKind    destinationKind   = HmOutputKind::Accumulator;
    IndexType       destinationOutput = 0;
};

constexpr IndexType
hm_training_index(IndexType kingBucket, HmTrainingPlane plane, Square orientedSquare) {
    return kingBucket * HmTrainingRowsPerBucket + IndexType(plane) * HmSquareDimensions
         + IndexType(orientedSquare);
}

constexpr IndexType hm_virtual_index(HmTrainingPlane plane, Square orientedSquare) {
    return IndexType(plane) * HmSquareDimensions + IndexType(orientedSquare);
}

constexpr IndexType
hm_physical_index(IndexType kingBucket, HmPhysicalPlane plane, Square orientedSquare) {
    return kingBucket * HmPhysicalRowsPerBucket + IndexType(plane) * HmSquareDimensions
         + IndexType(orientedSquare);
}

// V3 shares one runtime bucket between HM PSQT and the SFNNv15 dense stack.
// The scalar oracle is fail-closed outside the evaluable two-king material
// domain instead of silently clamping malformed or overpopulated positions.
constexpr bool hm_network_bucket(IndexType pieceCount, IndexType& result) {
    if (pieceCount < 2 || pieceCount > HmMaximumActiveDimensions)
        return false;
    result = (pieceCount - 1) / 4;
    return true;
}

// Maps one physical export cell back to the two factorized training cells
// whose sum creates it. For MERGED_KING, OWN_KING is selected only at the
// oriented own-king square; every other square comes from OPP_KING. This rule
// is identical for all 1,032 outputs, including the eight PSQT outputs.
bool hm_export_source(const JointOrientation& orientation,
                      HmPhysicalPlane         physicalPlane,
                      Square                  orientedSquare,
                      IndexType               coalescedOutput,
                      HmExportSource&         result);

// Deterministic scalar full-board HM enumerator. Features are emitted in raw
// A1..H8 square order. It deliberately has no accumulator, dispatcher, loader,
// UCI or playing-engine dependency.
HmOracleError emit_hm_features(const HmBoard& board, Color perspective, HmEmission& result);

// Position is an adapter over the same validated board oracle, not a second
// implementation. In particular, it does not trust Position to supply the
// exactly-one-king and material-domain invariants.
HmOracleError emit_hm_features(const Position& position, Color perspective, HmEmission& result);

const char* hm_oracle_error_message(HmOracleError error);

static_assert(HmTrainingRowsPerBucket == 768);
static_assert(HmPhysicalRowsPerBucket == 704);
static_assert(HmTrainingDimensions == 24576);
static_assert(HmPhysicalDimensions == 22528);
static_assert(HmVirtualDimensions == 768);
static_assert(HmCoalescedOutputs == 1032);

}  // namespace Stockfish::Eval::NNUE::AtomicV3

#endif  // ATOMIC_V3_HM_ORACLE_H_INCLUDED
