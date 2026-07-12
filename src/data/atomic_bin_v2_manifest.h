/*
  Atomic-Stockfish, a UCI chess variant playing engine derived from Stockfish
  Copyright (C) 2004-2026 The Stockfish developers (see AUTHORS file)

  Atomic-Stockfish is free software: you can redistribute it and/or modify
  it under the terms of the GNU General Public License as published by
  the Free Software Foundation, either version 3 of the License, or
  (at your option) any later version.
*/

#ifndef ATOMIC_BIN_V2_MANIFEST_H_INCLUDED
#define ATOMIC_BIN_V2_MANIFEST_H_INCLUDED

#include <filesystem>
#include <string>
#include <string_view>
#include <vector>

#include "training_data.h"

namespace Stockfish::Data {

inline constexpr std::string_view AtomicBinV2ManifestSchemaSha256Hex =
  "83d63922df3ac4a0c81a21ec9d9fd9e180efe50f26efee62fe01710e09da5b42";

struct AtomicBinV2ManifestOptions {
    int         searchDepthMin      = 0;
    int         searchDepthMax      = 0;
    u64         nodes               = 0;
    u64         requestedRecords    = 0;
    u64         recordsPerShard     = 0;
    int         evalLimit           = 0;
    int         evalDiffLimit       = 0;
    int         randomMoveMinPly    = 0;
    int         randomMoveMaxPly    = 0;
    int         randomMoveCount     = 0;
    int         randomMoveLikeApery = 0;
    int         randomMultiPv       = 0;
    int         randomMultiPvDiff   = 0;
    int         randomMultiPvDepth  = 0;
    int         writeMinPly         = 0;
    int         writeMaxPly         = 0;
    std::string keepDraws;
    bool        adjudicateDrawsByScore       = false;
    bool        adjudicateInsufficient       = false;
    bool        filterCaptures               = false;
    bool        filterChecks                 = false;
    bool        filterPromotions             = false;
    bool        randomFileName               = false;
    bool        setRecommendedUciOptionsSeen = false;
};

struct AtomicBinV2ManifestShard {
    std::filesystem::path path;
    u64                   index   = 0;
    u64                   records = 0;
    u64                   bytes   = 0;
    std::string           sha256;
};

struct AtomicBinV2Manifest {
    std::filesystem::path                 manifestPath;
    std::string                           engineCommit;
    std::string                           engineVersion;
    std::filesystem::path                 networkPath;
    std::string                           networkSha256;
    bool                                  bookIsFile = false;
    std::filesystem::path                 bookPath;
    std::string                           bookSha256;
    u64                                   resolvedSeed = 0;
    bool                                  atomic960    = false;
    u32                                   threads      = 0;
    u64                                   hashMb       = 0;
    AtomicBinV2ManifestOptions            options;
    u64                                   records = 0;
    u64                                   draws   = 0;
    std::vector<AtomicBinV2ManifestShard> shards;
};

// The required sidecar is adjacent to the first shard and appends the suffix
// without replacing .atbin: dataset.atbin.manifest.json.
std::filesystem::path atomic_bin_v2_manifest_path(const std::filesystem::path& firstShard);

// Fail before dataset generation when the destination cannot provide the
// platform's race-free sidecar publication primitive. The transient private
// probe never reserves or modifies the final manifest path; orderly cleanup is
// checked and reported, while abrupt process termination can leave only the
// clearly prefixed private probe directory.
DataResult preflight_atomic_bin_v2_manifest_publication(const std::filesystem::path& manifestPath);

// Render canonical minified UTF-8 JSON with exactly one trailing LF. The
// manifest contains no timestamps or absolute paths.
DataResult render_atomic_bin_v2_manifest(const AtomicBinV2Manifest& manifest, std::string& json);

// Parse only the frozen canonical representation produced by
// render_atomic_bin_v2_manifest(). The caller supplies the sidecar path because
// shard basenames are resolved relative to it. Output is reset before parsing;
// a failure can never leave partially trusted metadata behind.
DataResult parse_atomic_bin_v2_manifest(std::string_view             bytes,
                                        const std::filesystem::path& manifestPath,
                                        AtomicBinV2Manifest&         output);

// Read and parse a regular, non-symlink sidecar through one authenticated file
// descriptor. Raw .atbin paths are intentionally not accepted as datasets.
DataResult load_atomic_bin_v2_manifest(const std::filesystem::path& manifestPath,
                                       AtomicBinV2Manifest&         output);

// Create the sidecar with exclusive-create semantics. An error never replaces
// an existing file and removes only a partial sidecar created by this call.
DataResult write_atomic_bin_v2_manifest(const AtomicBinV2Manifest& manifest);

}  // namespace Stockfish::Data

#endif  // #ifndef ATOMIC_BIN_V2_MANIFEST_H_INCLUDED
