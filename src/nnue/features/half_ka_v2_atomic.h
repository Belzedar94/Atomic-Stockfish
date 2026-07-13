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

#ifndef NNUE_FEATURES_HALF_KA_V2_ATOMIC_H_INCLUDED
#define NNUE_FEATURES_HALF_KA_V2_ATOMIC_H_INCLUDED

#include "../../types.h"
#include "../nnue_common.h"

namespace Stockfish {
class Position;
}

namespace Stockfish::Eval::NNUE::Features {

// Legacy Fairy-Stockfish Atomic HalfKAv2 feature set.
//
// Atomic historically represented the royal piece as COMMONER. The compact
// Atomic engine represents it as KING, but the serialized feature plane is
// intentionally unchanged: both kings share the final (offset 640) plane.
class HalfKAv2Atomic {
   public:
    static constexpr u32 HashValue = 0x5F234CB8u;

    static constexpr IndexType PieceSquareDimensions = 11 * SQUARE_NB;
    static constexpr IndexType Dimensions            = SQUARE_NB * PieceSquareDimensions;

    static constexpr IndexType MaxActiveDimensions = 32;
    using IndexList                                = ValueList<IndexType, MaxActiveDimensions>;
    using DiffType                                 = DirtyPiece;

    static IndexType make_index(Color perspective, Square s, Piece pc, Square ksq);

    static void append_active_indices(const Position& pos, Color perspective, IndexList& active);

    static void append_changed_indices(
      Color perspective, Square ksq, const DiffType& diff, IndexList& removed, IndexList& added);

    static bool requires_refresh(const DiffType& diff, Color perspective);

   private:
    static Square orient(Color perspective, Square s);
};

static_assert(HalfKAv2Atomic::Dimensions == 45056);

}  // namespace Stockfish::Eval::NNUE::Features

#endif  // NNUE_FEATURES_HALF_KA_V2_ATOMIC_H_INCLUDED
