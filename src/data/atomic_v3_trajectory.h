/*
  Atomic-Stockfish AtomicNNUEV3 trajectory producer
  Copyright (C) 2026 The Atomic-Stockfish developers

  Atomic-Stockfish is free software: you can redistribute it and/or modify
  it under the terms of the GNU General Public License as published by
  the Free Software Foundation, either version 3 of the License, or
  (at your option) any later version.
*/

#ifndef ATOMIC_V3_TRAJECTORY_H_INCLUDED
#define ATOMIC_V3_TRAJECTORY_H_INCLUDED

#include <array>
#include <cstdio>
#include <filesystem>
#include <optional>
#include <string>
#include <string_view>
#include <vector>

#include "atomic_bin_v2.h"

namespace Stockfish::Data {

inline constexpr std::string_view AtomicV3TrajectorySchemaSha256Hex =
  "c2aaf1b2813b124a9daa2905a3dc277d635aabcd536b2677155933ef2bb18a3e";

enum class AtomicV3DatasetRole : u32 {
    TRAIN      = 0,
    VALIDATION = 1
};

enum class AtomicV3StopReason : u8 {
    ATOMIC_EXPLOSION        = 0,
    CHECKMATE               = 1,
    STALEMATE               = 2,
    INSUFFICIENT_MATERIAL   = 3,
    FIFTY_MOVE_RULE         = 4,
    THREEFOLD_REPETITION    = 5,
    MAXIMUM_PLY_DRAW        = 6,
    SCORE_DRAW_ADJUDICATION = 7,
    EVALUATION_RESIGNATION  = 8
};

struct AtomicV3Trajectory {
    std::string                     rootFen;
    AtomicBinV2Position             rootPosition{};
    std::vector<u32>                playedMoves;
    std::vector<TrainingDataSample> samples;
    i8                              terminalResult         = 0;  // White perspective.
    bool                            atomic960              = false;
    bool                            adjudicateInsufficient = true;
    AtomicV3StopReason              stopReason             = AtomicV3StopReason::MAXIMUM_PLY_DRAW;
};

using AtomicV3SplitGroupId    = std::array<u8, 32>;
using AtomicV3FeatureInputKey = std::array<u8, 32>;

inline constexpr std::string_view AtomicV3FeatureSchemaSha256Hex =
  "9d3c77a58e5e55ac1bc798dab41977451eb523fce1d6fd3ec3f7c1e574a78750";

AtomicV3SplitGroupId atomic_v3_split_group_id(const AtomicBinV2Position& root,
                                              bool                       atomic960,
                                              const std::vector<u32>&    playedMoves) noexcept;

u64 atomic_v3_partition_hash(u64 splitSeed, const AtomicV3SplitGroupId& group) noexcept;

AtomicV3DatasetRole atomic_v3_partition_role(u64                         splitSeed,
                                             u64                         validationThreshold,
                                             const AtomicV3SplitGroupId& group) noexcept;

// Replays the complete move stream, checks every retained pre-move sample and
// proves the result/stop reason before a byte reaches a role artifact.
DataResult validate_atomic_v3_trajectory(const AtomicV3Trajectory& trajectory,
                                         std::optional<u32> expectedMaximumPly = std::nullopt);

// Exact model-input identity used by the release split audit. Absolute color
// is intentionally absent; the two ordered perspectives are STM/opponent.
DataResult atomic_v3_feature_input_key(const TrainingDataSample& sample,
                                       AtomicV3FeatureInputKey&  key);

// Bounded-memory external sort for raw concatenated 32-byte identities. The
// output is canonical ascending unique order and is created exclusively.
DataResult sort_unique_atomic_v3_keys(const std::filesystem::path& input,
                                      const std::filesystem::path& output,
                                      u64                          expectedRecords,
                                      bool                         rejectDuplicates,
                                      u64&                         uniqueRecords);

#ifdef ATOMIC_V3_EXTERNAL_SORT_TEST_HOOKS
// Focused-test entrypoint for exercising release-scale merge topology without
// allocating multi-gigabyte fixtures. Production binaries expose only the
// fixed-memory, fixed-fan-in wrapper above.
DataResult sort_unique_atomic_v3_keys_with_limits_for_testing(const std::filesystem::path& input,
                                                              const std::filesystem::path& output,
                                                              u64   expectedRecords,
                                                              bool  rejectDuplicates,
                                                              u64&  uniqueRecords,
                                                              usize chunkRecords,
                                                              usize mergeFanIn);
#endif

struct AtomicV3LedgerMetadata {
    std::filesystem::path path;
    u64                   records      = 0;
    u64                   trajectories = 0;
    u64                   moves        = 0;
    u64                   bytes        = 0;
    std::string           sha256;
};

// Streams fixed-size entries and move wires to private staging files. The
// public .attraj is created only after the matching canonical manifest exists
// and can be authenticated into its header.
class AtomicV3TrajectoryLedgerStager {
   public:
    AtomicV3TrajectoryLedgerStager(std::filesystem::path privateDirectory,
                                   std::string           basename,
                                   AtomicV3DatasetRole   role,
                                   u64                   splitSeed,
                                   u64                   validationThreshold,
                                   u32                   expectedMaximumPly = 0);
    ~AtomicV3TrajectoryLedgerStager();

    AtomicV3TrajectoryLedgerStager(const AtomicV3TrajectoryLedgerStager&)            = delete;
    AtomicV3TrajectoryLedgerStager& operator=(const AtomicV3TrajectoryLedgerStager&) = delete;

    DataResult append(const AtomicV3Trajectory& trajectory, u64 expectedFirstRecord);
    DataResult finalize(const std::filesystem::path& finalPath,
                        std::string_view             manifestSha256,
                        AtomicV3LedgerMetadata&      metadata);
    DataResult abort();

    u64 records() const noexcept { return recordCount; }
    u64 trajectories() const noexcept { return trajectoryCount; }
    u64 moves() const noexcept { return moveCount; }

   private:
    DataResult open_staging();
    DataResult close_staging();

    std::filesystem::path privateDirectory;
    std::filesystem::path entriesPath;
    std::filesystem::path movesPath;
    std::filesystem::path groupsPath;
    std::filesystem::path sortedGroupsPath;
    std::filesystem::path finalizedPath;
    AtomicV3DatasetRole   role;
    u64                   splitSeed;
    u64                   validationThreshold;
    u32                   expectedMaximumPly;
    std::FILE*            entriesFile     = nullptr;
    std::FILE*            movesFile       = nullptr;
    std::FILE*            groupsFile      = nullptr;
    u64                   recordCount     = 0;
    u64                   trajectoryCount = 0;
    u64                   moveCount       = 0;
    bool                  finalized       = false;
    bool                  aborted         = false;
};

}  // namespace Stockfish::Data

#endif  // ATOMIC_V3_TRAJECTORY_H_INCLUDED
