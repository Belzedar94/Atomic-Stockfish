/*
  Stockfish, a UCI chess playing engine derived from Glaurung 2.1
  Copyright (C) 2004-2026 The Stockfish developers (see AUTHORS file)

  Stockfish is free software: you can redistribute it and/or modify
  it under the terms of the GNU General Public License as published by
  the Free Software Foundation, either version 3 of the License, or
  (at your option) any later version.

  Stockfish is distributed in the hope that it will be useful,
  but WITHOUT ANY WARRANTY; without even the implied warranty of
  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
  GNU General Public License for more details.

  You should have received a copy of the GNU General Public License
  along with this program.  If not, see <http://www.gnu.org/licenses/>.
*/

#include "half_ka_v2_atomic.h"

#include <algorithm>

#include "../../bitboard.h"
#include "../../position.h"

namespace Stockfish::Eval::NNUE::Features {

namespace {

enum PieceSquareOffset : IndexType {
    PS_NONE       = 0,
    PS_OWN_PAWN   = 0 * SQUARE_NB,
    PS_OPP_PAWN   = 1 * SQUARE_NB,
    PS_OWN_KNIGHT = 2 * SQUARE_NB,
    PS_OPP_KNIGHT = 3 * SQUARE_NB,
    PS_OWN_BISHOP = 4 * SQUARE_NB,
    PS_OPP_BISHOP = 5 * SQUARE_NB,
    PS_OWN_ROOK   = 6 * SQUARE_NB,
    PS_OPP_ROOK   = 7 * SQUARE_NB,
    PS_OWN_QUEEN  = 8 * SQUARE_NB,
    PS_OPP_QUEEN  = 9 * SQUARE_NB,
    PS_KING       = 10 * SQUARE_NB
};

// Convention: OWN is the feature perspective and OPP is its opponent.
// KING deliberately occupies the old COMMONER plane for both colors.
constexpr IndexType PieceSquareIndex[COLOR_NB][PIECE_NB] = {
  {PS_NONE, PS_OWN_PAWN, PS_OWN_KNIGHT, PS_OWN_BISHOP, PS_OWN_ROOK, PS_OWN_QUEEN, PS_KING, PS_NONE,
   PS_NONE, PS_OPP_PAWN, PS_OPP_KNIGHT, PS_OPP_BISHOP, PS_OPP_ROOK, PS_OPP_QUEEN, PS_KING, PS_NONE},
  {PS_NONE, PS_OPP_PAWN, PS_OPP_KNIGHT, PS_OPP_BISHOP, PS_OPP_ROOK, PS_OPP_QUEEN, PS_KING, PS_NONE,
   PS_NONE, PS_OWN_PAWN, PS_OWN_KNIGHT, PS_OWN_BISHOP, PS_OWN_ROOK, PS_OWN_QUEEN, PS_KING,
   PS_NONE}};

}  // namespace

Square HalfKAv2Atomic::orient(Color perspective, Square s) {
    // Fairy's legacy variants feature maps a missing royal piece to feature
    // anchor zero for both perspectives.
    if (s == SQ_NONE)
        return SQ_A1;
    return perspective == WHITE ? s : flip_rank(s);
}

IndexType HalfKAv2Atomic::make_index(Color perspective, Square s, Piece pc, Square ksq) {
    assert(s != SQ_NONE);
    assert(pc != NO_PIECE);

    return IndexType(orient(perspective, s)) + PieceSquareIndex[perspective][pc]
         + IndexType(orient(perspective, ksq)) * PieceSquareDimensions;
}

void HalfKAv2Atomic::append_active_indices(const Position& pos,
                                           Color           perspective,
                                           IndexList&      active) {
    const Square ksq = pos.has_king(perspective) ? pos.square<KING>(perspective) : SQ_NONE;

    Bitboard occupied = pos.pieces();
    while (occupied)
    {
        const Square s = pop_lsb(occupied);
        active.push_back(make_index(perspective, s, pos.piece_on(s), ksq));
    }
}

void HalfKAv2Atomic::append_changed_indices(
  Color perspective, Square ksq, const DiffType& diff, IndexList& removed, IndexList& added) {
    assert(ksq != SQ_NONE);

    removed.push_back(make_index(perspective, diff.from, diff.pc, ksq));
    if (diff.to != SQ_NONE)
        added.push_back(make_index(perspective, diff.to, diff.pc, ksq));

    if (diff.remove_sq != SQ_NONE)
        removed.push_back(make_index(perspective, diff.remove_sq, diff.remove_pc, ksq));

    if (diff.add_sq != SQ_NONE)
        added.push_back(make_index(perspective, diff.add_sq, diff.add_pc, ksq));

    for (const auto& blasted : diff.atomicBlast)
        removed.push_back(make_index(perspective, blasted.square, blasted.pc, ksq));
}

bool HalfKAv2Atomic::requires_refresh(const DiffType& diff, Color perspective) {
    const Piece perspectiveKing = make_piece(perspective, KING);
    if (diff.requiresRefresh || diff.pc == perspectiveKing
        || (diff.remove_sq != SQ_NONE && diff.remove_pc == perspectiveKing))
        return true;

    return std::any_of(diff.atomicBlast.begin(), diff.atomicBlast.end(),
                       [perspectiveKing](const DirtyPiece::AtomicBlastPiece& blasted) {
                           return blasted.pc == perspectiveKing;
                       });
}

}  // namespace Stockfish::Eval::NNUE::Features
