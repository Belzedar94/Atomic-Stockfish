/*
  Atomic-Stockfish, a specialized Atomic Chess engine derived from Stockfish
  Copyright (C) 2004-2026 The Stockfish developers (see AUTHORS file)

  Atomic-Stockfish is free software: you can redistribute it and/or modify
  it under the terms of the GNU General Public License as published by
  the Free Software Foundation, either version 3 of the License, or
  (at your option) any later version.
*/

#include "atomic_init.h"

#include <mutex>

#include "attacks.h"
#include "bitboard.h"
#include "position.h"

namespace Stockfish {

void initialize_atomic_core() {
    static std::once_flag initialized;
    std::call_once(initialized, [] {
        Bitboards::init();
        Attacks::init();
        Position::init();
    });
}

}  // namespace Stockfish
