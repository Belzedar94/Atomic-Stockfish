/*
  Atomic-Stockfish, a specialized Atomic Chess engine derived from Stockfish
  Copyright (C) 2004-2026 The Stockfish developers (see AUTHORS file)

  Atomic-Stockfish is free software: you can redistribute it and/or modify
  it under the terms of the GNU General Public License as published by
  the Free Software Foundation, either version 3 of the License, or
  (at your option) any later version.
*/

#ifndef ATOMIC_OUTCOME_H_INCLUDED
#define ATOMIC_OUTCOME_H_INCLUDED

#include <optional>
#include <string>

#include "../types.h"

namespace Stockfish {

class Position;

namespace Atomic {

enum class Termination {
    Ongoing,
    AtomicExplosion,
    Checkmate,
    Stalemate,
    InsufficientMaterial,
    FiftyMoveRule,
    ThreefoldRepetition
};

struct Outcome {
    Termination          termination = Termination::Ongoing;
    Value                value       = VALUE_ZERO;  // Relative to side to move.
    std::optional<Color> winner;

    bool        terminal() const { return termination != Termination::Ongoing; }
    std::string result() const;
};

Outcome  outcome(const Position& pos, bool claimDraw = false, int repetitionPly = 0);
bool     has_insufficient_material(Color color, const Position& pos);
Bitboard checked_squares(const Position& pos);

}  // namespace Atomic
}  // namespace Stockfish

#endif  // ATOMIC_OUTCOME_H_INCLUDED
