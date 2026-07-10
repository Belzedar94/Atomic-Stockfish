/*
  Atomic-Stockfish, a specialized Atomic Chess engine derived from Stockfish
  Copyright (C) 2004-2026 The Stockfish developers (see AUTHORS file)
*/

#ifndef ATOMIC_FEN_H_INCLUDED
#define ATOMIC_FEN_H_INCLUDED

#include <string_view>

namespace Stockfish::Atomic {

// Stable compatibility values exposed by pyffish and ffish.js. Atomic does
// not use pockets, promoted-piece markers, counting rules or check counters,
// but their historical numeric slots remain reserved for ABI compatibility.
enum FenValidation : int {
    FEN_INVALID_COUNTING_RULE     = -14,
    FEN_INVALID_CHECK_COUNT       = -13,
    FEN_INVALID_PROMOTED_PIECE    = -12,
    FEN_INVALID_NB_PARTS          = -11,
    FEN_INVALID_CHAR              = -10,
    FEN_TOUCHING_KINGS            = -9,
    FEN_INVALID_BOARD_GEOMETRY    = -8,
    FEN_INVALID_POCKET_INFO       = -7,
    FEN_INVALID_SIDE_TO_MOVE      = -6,
    FEN_INVALID_CASTLING_INFO     = -5,
    FEN_INVALID_EN_PASSANT_SQ     = -4,
    FEN_INVALID_NUMBER_OF_KINGS   = -3,
    FEN_INVALID_HALF_MOVE_COUNTER = -2,
    FEN_INVALID_MOVE_COUNTER      = -1,
    FEN_EMPTY                     = 0,
    FEN_OK                        = 1
};

int validate_fen(std::string_view fen, bool chess960 = false);

}  // namespace Stockfish::Atomic

#endif  // ATOMIC_FEN_H_INCLUDED
