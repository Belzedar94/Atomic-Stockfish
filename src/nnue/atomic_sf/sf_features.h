/*
  Atomic-Stockfish, a specialized Atomic Chess engine derived from Stockfish
  Copyright (C) 2004-2026 The Stockfish developers (see AUTHORS file)

  Atomic-Stockfish is free software: you can redistribute it and/or modify
  it under the terms of the GNU General Public License as published by
  the Free Software Foundation, either version 3 of the License, or
  (at your option) any later version.
*/

// Input features of the Stockfish-master network family (HalfKAv2_hm and
// Full_Threats, SF eca43a97 / nnue-pytorch b851229), adapted to Atomic.
//
// Feature indexing is byte-for-byte the upstream one, so the official trainer
// output loads unchanged. The Atomic differences are confined to the deltas:
//  - HalfKAv2_hm: a capture removes the capturer, the victim and the adjacent
//    non-pawns (DirtyPiece::atomicBlast), handled as a bounded delta.
//  - Full_Threats: a capture can change hundreds of threat features, so it is
//    never updated incrementally; the accumulator is refreshed instead.
//    Non-capture moves use the upstream incremental threat update, replayed on
//    a copy of the board after the move has been made.
// Kings neither attack nor are attacked in Full_Threats, as upstream.

#ifndef ATOMIC_SF_FEATURES_H_INCLUDED
#define ATOMIC_SF_FEATURES_H_INCLUDED

#include "../../misc.h"
#include "../../types.h"
#include "../nnue_common.h"

namespace Stockfish {
class Position;
}

namespace Stockfish::Eval::NNUE::AtomicSF {

using ThreatWeightType = i8;

// Keep track of what threats change on the board (upstream layout).
struct DirtyThreat {
    static constexpr int PcSqOffset         = 0;
    static constexpr int ThreatenedSqOffset = 8;
    static constexpr int ThreatenedPcOffset = 16;
    static constexpr int PcOffset           = 20;

    DirtyThreat() = default;
    DirtyThreat(Piece pc, Piece threatened_pc, Square pc_sq, Square threatened_sq, bool add) {
        data = (u32(add) << 31) | (u32(pc) << PcOffset) | (u32(threatened_pc) << ThreatenedPcOffset)
             | (u32(threatened_sq) << ThreatenedSqOffset) | (u32(pc_sq) << PcSqOffset);
    }

    Piece  pc() const { return static_cast<Piece>(data >> PcOffset & 0xf); }
    Piece  threatened_pc() const { return static_cast<Piece>(data >> ThreatenedPcOffset & 0xf); }
    Square threatened_sq() const { return static_cast<Square>(data >> ThreatenedSqOffset & 0xff); }
    Square pc_sq() const { return static_cast<Square>(data >> PcSqOffset & 0xff); }
    bool   add() const { return data >> 31; }

   private:
    u32 data;
};

// Upstream bound: a non-castling move changes at most 80 features, castling
// at most 36. Atomic captures are bounded by CaptureThreatBudget below.
// One put/remove changes at most 8 outgoing + 16 incoming + 8 discovered
// threats (upstream bound).
inline constexpr usize MaxThreatsPerPieceUpdate = 32;
inline constexpr usize DirtyThreatCapacity      = 96;
#ifndef ATOMIC_SF_CAPTURE_BUDGET
    #define ATOMIC_SF_CAPTURE_BUDGET 96
#endif
// Above this many changed threat features a capture refreshes instead.
inline constexpr usize CaptureThreatBudget = ATOMIC_SF_CAPTURE_BUDGET;
static_assert(CaptureThreatBudget <= DirtyThreatCapacity);

struct DirtyThreats {
    ValueList<DirtyThreat, DirtyThreatCapacity> list;
    bool                       refresh = false;
};

class HalfKAv2Hm {
    enum {
        PS_NONE     = 0,
        PS_W_PAWN   = 0,
        PS_B_PAWN   = 1 * SQUARE_NB,
        PS_W_KNIGHT = 2 * SQUARE_NB,
        PS_B_KNIGHT = 3 * SQUARE_NB,
        PS_W_BISHOP = 4 * SQUARE_NB,
        PS_B_BISHOP = 5 * SQUARE_NB,
        PS_W_ROOK   = 6 * SQUARE_NB,
        PS_B_ROOK   = 7 * SQUARE_NB,
        PS_W_QUEEN  = 8 * SQUARE_NB,
        PS_B_QUEEN  = 9 * SQUARE_NB,
        PS_KING     = 10 * SQUARE_NB,
        PS_NB       = 11 * SQUARE_NB
    };

    static constexpr IndexType PieceSquareIndex[COLOR_NB][PIECE_NB] = {
      // Convention: W - us, B - them. Both kings share one plane.
      {PS_NONE, PS_W_PAWN, PS_W_KNIGHT, PS_W_BISHOP, PS_W_ROOK, PS_W_QUEEN, PS_KING, PS_NONE,
       PS_NONE, PS_B_PAWN, PS_B_KNIGHT, PS_B_BISHOP, PS_B_ROOK, PS_B_QUEEN, PS_KING, PS_NONE},
      {PS_NONE, PS_B_PAWN, PS_B_KNIGHT, PS_B_BISHOP, PS_B_ROOK, PS_B_QUEEN, PS_KING, PS_NONE,
       PS_NONE, PS_W_PAWN, PS_W_KNIGHT, PS_W_BISHOP, PS_W_ROOK, PS_W_QUEEN, PS_KING, PS_NONE}};

#define B(v) (v * PS_NB)
    // clang-format off
    static constexpr IndexType KingBuckets[SQUARE_NB] = {
        B(28), B(29), B(30), B(31), B(31), B(30), B(29), B(28),
        B(24), B(25), B(26), B(27), B(27), B(26), B(25), B(24),
        B(20), B(21), B(22), B(23), B(23), B(22), B(21), B(20),
        B(16), B(17), B(18), B(19), B(19), B(18), B(17), B(16),
        B(12), B(13), B(14), B(15), B(15), B(14), B(13), B(12),
        B( 8), B( 9), B(10), B(11), B(11), B(10), B( 9), B( 8),
        B( 4), B( 5), B( 6), B( 7), B( 7), B( 6), B( 5), B( 4),
        B( 0), B( 1), B( 2), B( 3), B( 3), B( 2), B( 1), B( 0),
    };
    // clang-format on
#undef B

    // clang-format off
    static constexpr IndexType OrientTBL[SQUARE_NB] = {
        SQ_H1, SQ_H1, SQ_H1, SQ_H1, SQ_A1, SQ_A1, SQ_A1, SQ_A1,
        SQ_H1, SQ_H1, SQ_H1, SQ_H1, SQ_A1, SQ_A1, SQ_A1, SQ_A1,
        SQ_H1, SQ_H1, SQ_H1, SQ_H1, SQ_A1, SQ_A1, SQ_A1, SQ_A1,
        SQ_H1, SQ_H1, SQ_H1, SQ_H1, SQ_A1, SQ_A1, SQ_A1, SQ_A1,
        SQ_H1, SQ_H1, SQ_H1, SQ_H1, SQ_A1, SQ_A1, SQ_A1, SQ_A1,
        SQ_H1, SQ_H1, SQ_H1, SQ_H1, SQ_A1, SQ_A1, SQ_A1, SQ_A1,
        SQ_H1, SQ_H1, SQ_H1, SQ_H1, SQ_A1, SQ_A1, SQ_A1, SQ_A1,
        SQ_H1, SQ_H1, SQ_H1, SQ_H1, SQ_A1, SQ_A1, SQ_A1, SQ_A1,
    };
    // clang-format on

   public:
    static constexpr u32       HashValue  = 0x7f234cb8u;
    static constexpr IndexType Dimensions = IndexType(SQUARE_NB) * IndexType(PS_NB) / 2;

    static constexpr IndexType MaxActiveDimensions  = 32;
    static constexpr IndexType MaxRemovedDimensions = 2 + DirtyPiece::MAX_ATOMIC_BLAST_PIECES;
    static constexpr IndexType MaxAddedDimensions   = 2;

    using ActiveIndexList  = ValueList<IndexType, MaxActiveDimensions>;
    using RemovedIndexList = ValueList<IndexType, MaxRemovedDimensions>;
    using AddedIndexList   = ValueList<IndexType, MaxAddedDimensions>;

    static IndexType make_index(Color perspective, Square s, Piece pc, Square ksq) {
        const IndexType flip = 56 * perspective;
        return (IndexType(s) ^ OrientTBL[ksq] ^ flip) + PieceSquareIndex[perspective][pc]
             + KingBuckets[int(ksq) ^ flip];
    }

    static void append_changed_indices(Color             perspective,
                                       Square            ksq,
                                       const DirtyPiece& diff,
                                       RemovedIndexList& removed,
                                       AddedIndexList&   added);

    static bool requires_refresh(const DirtyPiece& diff, Color perspective);
};

class FullThreats {
   public:
    static constexpr u32       HashValue           = 0x8f234cb8u;
    static constexpr IndexType Dimensions          = 60720;
    static constexpr IndexType MaxActiveDimensions = 128;

    // clang-format off
    static constexpr i8 OrientTBL[SQUARE_NB] = {
        SQ_A1, SQ_A1, SQ_A1, SQ_A1, SQ_H1, SQ_H1, SQ_H1, SQ_H1,
        SQ_A1, SQ_A1, SQ_A1, SQ_A1, SQ_H1, SQ_H1, SQ_H1, SQ_H1,
        SQ_A1, SQ_A1, SQ_A1, SQ_A1, SQ_H1, SQ_H1, SQ_H1, SQ_H1,
        SQ_A1, SQ_A1, SQ_A1, SQ_A1, SQ_H1, SQ_H1, SQ_H1, SQ_H1,
        SQ_A1, SQ_A1, SQ_A1, SQ_A1, SQ_H1, SQ_H1, SQ_H1, SQ_H1,
        SQ_A1, SQ_A1, SQ_A1, SQ_A1, SQ_H1, SQ_H1, SQ_H1, SQ_H1,
        SQ_A1, SQ_A1, SQ_A1, SQ_A1, SQ_H1, SQ_H1, SQ_H1, SQ_H1,
        SQ_A1, SQ_A1, SQ_A1, SQ_A1, SQ_H1, SQ_H1, SQ_H1, SQ_H1,
    };

    static constexpr int map[PIECE_TYPE_NB-2][PIECE_TYPE_NB-2] = {
      { 0,  1, -1,  2, -1, -1},
      { 0,  1,  2,  3,  4, -1},
      { 0,  1,  2,  3, -1, -1},
      { 0,  1,  2,  3, -1, -1},
      { 0,  1,  2,  3,  4, -1},
      {-1, -1, -1, -1, -1, -1}
    };
    // clang-format on

    using IndexList = ValueList<IndexType, MaxActiveDimensions>;

    static IndexType
    make_index(Color perspective, Piece attkr, Square from, Square to, Piece attkd, Square ksq);

    static void append_active_indices(Color perspective, const Position& pos, IndexList& active);

    static void append_changed_indices(Color               perspective,
                                       Square              ksq,
                                       const DirtyThreats& diff,
                                       IndexList&          removed,
                                       IndexList&          added);
};

// The perspective's king square used for feature orientation. Atomic search
// never evaluates a position without both kings; the fallback only keeps the
// indexing in range if an accumulator is ever refreshed for such a position.
Square feature_king_square(const Position& pos, Color perspective);

// Fill dts with the threat changes of the move that produced `after`, whose
// DirtyPiece is dp. Captures (every Atomic capture explodes) set dts.refresh.
void compute_dirty_threats(const Position& after, const DirtyPiece& dp, DirtyThreats& dts);

}  // namespace Stockfish::Eval::NNUE::AtomicSF

#endif  // ATOMIC_SF_FEATURES_H_INCLUDED
