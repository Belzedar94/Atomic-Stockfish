/*
  Atomic-Stockfish, a specialized Atomic Chess engine derived from Stockfish
  Copyright (C) 2004-2026 The Atomic-Stockfish developers

  Atomic-Stockfish is free software: you can redistribute it and/or modify
  it under the terms of the GNU General Public License as published by
  the Free Software Foundation, either version 3 of the License, or
  (at your option) any later version.
*/

#ifndef ATOMIC_DATA_OPENBENCH_BUNDLE_H_INCLUDED
#define ATOMIC_DATA_OPENBENCH_BUNDLE_H_INCLUDED

#include <filesystem>
#include <string_view>

#include "training_data.h"

namespace Stockfish::Data {

inline constexpr std::string_view AtomicOpenBenchBundleSchemaSha256Hex =
  "f8155e881b6d1de53341d5084a0e253c91318383bceea2c235e667893284b9dc";

// Publish the one-file artifact expected by OpenBench. The bundle contains
// exactly one .atbin shard and its mandatory canonical manifest. The manifest
// precedes its 64-byte-aligned payload in the frozen ATOBNDL1 wire. The fixed
// header authenticates all three schemas and both entries.
DataResult write_openbench_datagen_bundle(const std::filesystem::path& output,
                                          const std::filesystem::path& shard,
                                          const std::filesystem::path& manifest);

}  // namespace Stockfish::Data

#endif  // ATOMIC_DATA_OPENBENCH_BUNDLE_H_INCLUDED
