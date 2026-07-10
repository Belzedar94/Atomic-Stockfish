/*
  Atomic-Stockfish, a specialized Atomic Chess engine derived from Stockfish
  Copyright (C) 2004-2026 The Stockfish developers (see AUTHORS file)

  Atomic-Stockfish is free software: you can redistribute it and/or modify
  it under the terms of the GNU General Public License as published by
  the Free Software Foundation, either version 3 of the License, or
  (at your option) any later version.
*/

#ifndef ATOMIC_NOTATION_H_INCLUDED
#define ATOMIC_NOTATION_H_INCLUDED

#include <string>
#include <string_view>

#include "../types.h"

namespace Stockfish {

class Position;

namespace Atomic {

// Format and parse legal Atomic moves. Position is non-const because check and
// mate suffixes are determined by a reversible make/undo of the candidate.
std::string to_san(Position& pos, Move move);
std::string to_lan(Position& pos, Move move);
Move        parse_san(Position& pos, std::string_view notation);
Move        parse_lan(Position& pos, std::string_view notation);

}  // namespace Atomic
}  // namespace Stockfish

#endif  // ATOMIC_NOTATION_H_INCLUDED
