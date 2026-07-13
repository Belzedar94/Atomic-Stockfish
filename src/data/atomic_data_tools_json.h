/*
  Atomic-Stockfish, a specialized Atomic Chess engine derived from Stockfish
  Copyright (C) 2004-2026 The Stockfish developers (see AUTHORS file)

  Atomic-Stockfish is free software: you can redistribute it and/or modify
  it under the terms of the GNU General Public License as published by
  the Free Software Foundation, either version 3 of the License, or
  (at your option) any later version.
*/

#ifndef ATOMIC_DATA_TOOLS_JSON_H_INCLUDED
#define ATOMIC_DATA_TOOLS_JSON_H_INCLUDED

#include <string>

#include "types.h"

namespace Stockfish::Data {

struct AtomicDataToolsValidationStats {
    u32 shards               = 0;
    u64 records              = 0;
    u64 sideToMoveWins       = 0;
    u64 draws                = 0;
    u64 sideToMoveLosses     = 0;
    u64 atomic960RecordCount = 0;
};

// Render the complete canonical success response, including its single LF.
// Record-derived counters are decimal strings so their uint64 domain remains
// exact in JSON consumers backed by IEEE-754 numbers. The manifest-bounded
// shard count remains a number.
std::string render_atomic_data_tools_validation_json(const AtomicDataToolsValidationStats& stats);

}  // namespace Stockfish::Data

#endif  // #ifndef ATOMIC_DATA_TOOLS_JSON_H_INCLUDED
