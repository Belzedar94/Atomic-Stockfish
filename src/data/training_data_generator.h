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
#include <atomic>
#include <condition_variable>
#include <cstdint>
#include <iosfwd>

namespace Stockfish {

class Engine;

namespace Data {

struct AtomicDatagenV2Manifest;

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

// Advance the ordered-commit cursor and wake a producer that is allowed to
// bypass a full pending buffer because it owns the newly expected game_id.
// Keeping the store and notification inseparable prevents the coordinator and
// that producer from sleeping forever while each waits for the other.
inline void advance_atomic_v3_commit_game_id(std::atomic<std::uint64_t>& cursor,
                                             std::condition_variable&    pendingCv,
                                             std::uint64_t               nextGameId) noexcept {
    cursor.store(nextGameId, std::memory_order_relaxed);
    pendingCv.notify_all();
}

// Parse and execute the Atomic-only PV self-play generator command. This
// implementation is linked only by the isolated `data-generator` target.
// Returns false after emitting a diagnostic when the command cannot complete.
// The isolated UCI target translates that result to a non-zero process status,
// matching the historical tools command contract.
bool generate_training_data(Engine& engine, std::istream& input);

// Explicit authenticated teacher path used only by the OpenBench bridge V2.
// The caller must pre-fill manifestPath, inventory identity and an explicit
// teacherMode/useNnue pair. The generator fills the remaining authenticated
// metadata and publishes only the new manifest V2 contract.
bool generate_authenticated_training_data_v2(Engine&                  engine,
                                              std::istream&            input,
                                              AtomicDatagenV2Manifest& manifest);

// Additive AtomicNNUEV3 producer. Unlike the historical command it buffers a
// complete game, partitions by a label-free trajectory hash, and publishes a
// role-specific Atomic BIN V2 dataset plus its replay ledger.
bool generate_atomic_v3_chunk(Engine& engine, std::istream& input);

}  // namespace Data
}  // namespace Stockfish

#endif  // DATA_TRAINING_DATA_GENERATOR_H_INCLUDED
