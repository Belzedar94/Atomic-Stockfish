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

#include <algorithm>
#include <cstdint>
#include <iosfwd>

namespace Stockfish {

class Engine;

namespace Data {

enum class TrainingResolutionSource {
    NONE,
    OUTCOME,
    MAX_PLY
};

inline TrainingResolutionSource training_resolution_source(bool terminal,
                                                           bool insufficient,
                                                           bool adjudicateInsufficient,
                                                           bool maxPlyReached) noexcept {
    if (terminal && (adjudicateInsufficient || !insufficient))
        return TrainingResolutionSource::OUTCOME;
    if (maxPlyReached)
        return TrainingResolutionSource::MAX_PLY;
    return TrainingResolutionSource::NONE;
}

// Decide whether every draw record from one completed game can be retained
// without exceeding the requested record-level draw fraction. Only records
// that still fit before targetCount are projected.
inline bool legacy_atomic_v1_draw_game_fits(std::uint64_t draws,
                                            std::uint64_t written,
                                            std::uint64_t buffered,
                                            std::uint64_t targetCount,
                                            double        keepDraws) noexcept {
    if (buffered == 0 || written >= targetCount)
        return false;
    const std::uint64_t accepted = std::min(buffered, targetCount - written);
    return (static_cast<long double>(draws) + accepted)
           / (static_cast<long double>(written) + accepted)
        <= keepDraws;
}

// Parse and execute the Atomic-only PV self-play generator command. This
// implementation is linked only by the isolated `data-generator` target.
// Returns false after emitting a diagnostic when the command cannot complete.
// The isolated UCI target translates that result to a non-zero process status,
// matching the historical tools command contract.
bool generate_training_data(Engine& engine, std::istream& input);

}  // namespace Data
}  // namespace Stockfish

#endif  // DATA_TRAINING_DATA_GENERATOR_H_INCLUDED
