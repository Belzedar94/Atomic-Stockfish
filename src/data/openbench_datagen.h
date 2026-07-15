/*
  Atomic-Stockfish, a specialized Atomic Chess engine derived from Stockfish
  Copyright (C) 2004-2026 The Atomic-Stockfish developers

  Atomic-Stockfish is free software: you can redistribute it and/or modify
  it under the terms of the GNU General Public License as published by
  the Free Software Foundation, either version 3 of the License, or
  (at your option) any later version.
*/

#ifndef ATOMIC_DATA_OPENBENCH_DATAGEN_H_INCLUDED
#define ATOMIC_DATA_OPENBENCH_DATAGEN_H_INCLUDED

#include <iosfwd>

namespace Stockfish {

class Engine;

namespace Data {

// Configure the isolated generator from one OpenBench command, generate one
// Atomic BIN V2 shard and publish the shard plus mandatory manifest as exactly
// one bundle path.
bool openbench_generate_training_data(Engine& engine, std::istream& input);

}  // namespace Data
}  // namespace Stockfish

#endif  // ATOMIC_DATA_OPENBENCH_DATAGEN_H_INCLUDED
