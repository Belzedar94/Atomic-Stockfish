/*
  Atomic-Stockfish, a specialized Atomic Chess engine derived from Stockfish
  Copyright (C) 2004-2026 The Stockfish developers (see AUTHORS file)

  Atomic-Stockfish is free software: you can redistribute it and/or modify
  it under the terms of the GNU General Public License as published by
  the Free Software Foundation, either version 3 of the License, or
  (at your option) any later version.
*/

#ifndef UCI_MOVE_H_INCLUDED
#define UCI_MOVE_H_INCLUDED

#include <string>

#include "types.h"

namespace Stockfish {

class Position;

// Move notation helpers shared by UCI, XBoard, search output and bindings.
// This layer deliberately depends only on Position/move generation, never on
// Engine or Search, so future protocol and language APIs can reuse it directly.
namespace UCI {

std::string square(Square s);
std::string move(Move m, bool chess960 = false);
std::string to_lower(std::string str);
Move        to_move(const Position& pos, std::string str);

}  // namespace UCI
}  // namespace Stockfish

#endif  // UCI_MOVE_H_INCLUDED
