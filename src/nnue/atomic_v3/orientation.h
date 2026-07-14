/*
  Atomic-Stockfish, a UCI chess playing engine derived from Stockfish
  Copyright (C) 2004-2026 The Stockfish developers (see AUTHORS file)

  Atomic-Stockfish is free software: you can redistribute it and/or modify
  it under the terms of the GNU General Public License as published by
  the Free Software Foundation, either version 3 of the License, or
  (at your option) any later version.
*/

#ifndef ATOMIC_V3_ORIENTATION_H_INCLUDED
#define ATOMIC_V3_ORIENTATION_H_INCLUDED

#include "../../types.h"
#include "../nnue_common.h"

namespace Stockfish::Eval::NNUE::AtomicV3 {

// One perspective owns one joint orientation. Every V3 slice must use the
// same square xor for that perspective; actor/collateral relations only
// relabel colors and never reorient a square.
struct JointOrientation {
    Color     perspective     = WHITE;
    Square    ownKing         = SQ_NONE;
    Square    orientedOwnKing = SQ_NONE;
    u8        verticalXor     = 0;
    u8        horizontalXor   = 0;
    IndexType kingBucket      = 0;

    constexpr Square orient(Square square) const {
        return square == SQ_NONE ? SQ_NONE : Square(int(square) ^ verticalXor ^ horizontalXor);
    }
};

// Returns false for a missing king (or an invalid perspective). V3 evaluation
// is forbidden after either king has exploded, so callers must never invent an
// anchor square for SQ_NONE.
constexpr bool make_joint_orientation(Color perspective, Square ownKing, JointOrientation& result) {
    if ((perspective != WHITE && perspective != BLACK) || !is_ok(ownKing))
        return false;

    const u8     vertical          = perspective == BLACK ? 56 : 0;
    const Square preHorizontalKing = Square(int(ownKing) ^ vertical);
    const u8     horizontal        = file_of(preHorizontalKing) < FILE_E ? 7 : 0;
    const Square orientedKing      = Square(int(preHorizontalKing) ^ horizontal);

    // The horizontal branch places the oriented own king on files e-h. The
    // bucket ordering is h8=0, g8=1, ..., e1=31.
    const IndexType bucket =
      IndexType(7 - int(rank_of(orientedKing))) * 4 + IndexType(7 - int(file_of(orientedKing)));

    result = {perspective, ownKing, orientedKing, vertical, horizontal, bucket};
    return true;
}

constexpr bool is_canonical_joint_orientation(const JointOrientation& orientation) {
    JointOrientation expected{};
    return make_joint_orientation(orientation.perspective, orientation.ownKing, expected)
        && orientation.perspective == expected.perspective
        && orientation.ownKing == expected.ownKing
        && orientation.orientedOwnKing == expected.orientedOwnKing
        && orientation.verticalXor == expected.verticalXor
        && orientation.horizontalXor == expected.horizontalXor
        && orientation.kingBucket == expected.kingBucket;
}

}  // namespace Stockfish::Eval::NNUE::AtomicV3

#endif  // ATOMIC_V3_ORIENTATION_H_INCLUDED
