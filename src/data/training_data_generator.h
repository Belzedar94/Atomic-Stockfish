/*
  Atomic-Stockfish training-data generator
  Copyright (C) 2026 The Atomic-Stockfish developers

  Atomic-Stockfish is free software: you can redistribute it and/or modify
  it under the terms of the GNU General Public License as published by
  the Free Software Foundation, either version 3 of the License, or
  (at your option) any later version.
*/

#ifndef DATA_TRAINING_DATA_GENERATOR_H_INCLUDED
#define DATA_TRAINING_DATA_GENERATOR_H_INCLUDED

#include <iosfwd>

namespace Stockfish {

class Engine;

namespace Data {

// Parse and execute the Atomic-only PV self-play generator command. This
// implementation is linked only by the isolated `data-generator` target.
// Returns false after emitting a diagnostic when the command cannot complete.
// The isolated UCI target translates that result to a non-zero process status,
// matching the historical tools command contract.
bool generate_training_data(Engine& engine, std::istream& input);

}  // namespace Data
}  // namespace Stockfish

#endif  // DATA_TRAINING_DATA_GENERATOR_H_INCLUDED
