/*
  Atomic-Stockfish, a UCI chess variant playing engine derived from Stockfish
  Copyright (C) 2004-2026 The Stockfish developers (see AUTHORS file)

  Atomic-Stockfish is free software: you can redistribute it and/or modify
  it under the terms of the GNU General Public License as published by
  the Free Software Foundation, either version 3 of the License, or
  (at your option) any later version.
*/

#ifndef ATOMIC_BIN_V2_H_INCLUDED
#define ATOMIC_BIN_V2_H_INCLUDED

#include "atomic_bin_v2_wire.h"

namespace Stockfish {

class Position;

}

namespace Stockfish::Data {

// Convert between the format-neutral generator sample and the frozen V2 wire.
// Both directions fail closed through Position's Atomic parser and legal move
// generator. Output is reset before any validation so failed calls cannot leak
// partially encoded or decoded data.
DataResult encode_atomic_bin_v2(const TrainingDataSample& sample, AtomicBinV2Record& record);
DataResult decode_atomic_bin_v2(const AtomicBinV2Record& record, TrainingDataSample& sample);

// Canonical V2 primitives used by the trajectory ledger. These are additive
// views of the exact same encoders used by encode_atomic_bin_v2(); exposing
// them avoids reparsing a FEN for every played move and, crucially, prevents a
// second move-wire convention from drifting away from the frozen 64-byte
// record.
DataResult encode_atomic_bin_v2_position(const Position& position, AtomicBinV2Position& wire);
DataResult encode_atomic_bin_v2_move(Move move, u32& wire);
DataResult decode_atomic_bin_v2_move(u32 wire, Move& move);

// Position::game_ply() is the authoritative generator clock. Keep the V2
// sample eligibility gate in the format adapter so generation cannot buffer a
// position that the wire encoder must later reject.
bool atomic_bin_v2_fullmove_fits_game_ply(int gamePly) noexcept;

}  // namespace Stockfish::Data

#endif  // #ifndef ATOMIC_BIN_V2_H_INCLUDED
