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
#include <string_view>

#include "atomic_bin_v2_manifest.h"
#include "atomic_bin_v2_reader.h"
#include "types.h"

namespace Stockfish::Data {

inline constexpr std::string_view AtomicDataToolsDecodeSchemaSha256Hex =
  "5e3f8d7c6db6ee955b71747ee063859e15609adb557a3754228a606f3df2caad";

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

// Render one complete canonical JSONL line, including its single LF. Decode
// callers must buffer these lines until the DatasetReader has reached a clean
// EOF; rendering a line is not permission to expose a partially validated
// dataset on stdout.
std::string
render_atomic_data_tools_decode_header(const AtomicBinV2Manifest& manifest, u64 offset, u32 limit);
std::string render_atomic_data_tools_decode_record(const AtomicBinV2DecodedRecord& record);
std::string render_atomic_data_tools_decode_footer(const AtomicDataToolsValidationStats& stats,
                                                   u64                                   offset,
                                                   u32                                   limit);

}  // namespace Stockfish::Data

#endif  // #ifndef ATOMIC_DATA_TOOLS_JSON_H_INCLUDED
