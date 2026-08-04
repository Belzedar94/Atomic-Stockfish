/*
  Atomic-Stockfish authenticated teacher/Syzygy datagen contract V2
  Copyright (C) 2026 The Atomic-Stockfish developers

  Atomic-Stockfish is free software: you can redistribute it and/or modify
  it under the terms of the GNU General Public License as published by
  the Free Software Foundation, either version 3 of the License, or
  (at your option) any later version.
*/

#ifndef ATOMIC_DATA_AUTHENTICATED_DATAGEN_V2_H_INCLUDED
#define ATOMIC_DATA_AUTHENTICATED_DATAGEN_V2_H_INCLUDED

#include <filesystem>
#include <string>
#include <string_view>

#include "atomic_bin_v2_manifest.h"
#include "training_data.h"

namespace Stockfish::Data {

// These identities describe new byte contracts. They deliberately do not
// alias or reinterpret the frozen Atomic BIN V2 manifest V1 or ATOBNDL1 wire.
inline constexpr std::string_view AtomicBinV2TeacherManifestSchemaSha256Hex =
  "a99e2fccaf9e01bdd391d1d16b432597ae7b6cdbbb02b4fc9e077dbeb3643b31";
inline constexpr std::string_view AtomicDatagenAttestationSchemaSha256Hex =
  "38937506e50988317e3bf4cdd2c964e4934386123abf7dfd502af77ede6189d7";
inline constexpr std::string_view AtomicOpenBenchBundleV2SchemaSha256Hex =
  "fac3b8fa1c31e543a6483c59f8f2a2d895ceb067e789daef580b8439849e6aca";

inline constexpr std::string_view AtomicPureTeacherMode          = "pure";
inline constexpr std::string_view AtomicTrueTeacherMode          = "true";
inline constexpr int              AtomicTeacherSyzygyCardinality = 6;
inline constexpr int              AtomicTeacherSyzygyProbeLimit  = 6;
inline constexpr int              AtomicTeacherSyzygyProbeDepth  = 1;
inline constexpr std::string_view AtomicTeacherSyzygyInventorySha256Hex =
  "3d4b7fd0ab387f4f60da2078f612c9e8890e6026f551aebe8631efc157788f23";

struct AtomicDatagenV2Manifest {
    std::filesystem::path      manifestPath;
    std::string                engineCommit;
    std::string                engineVersion;
    std::string                producerSha256;
    std::filesystem::path      networkPath;
    std::string                networkSha256;
    bool                       bookIsFile = false;
    std::filesystem::path      bookPath;
    std::string                bookSha256;
    u64                        resolvedSeed = 0;
    u32                        threads      = 0;
    u64                        hashMb       = 0;
    std::string                teacherMode;
    std::string                useNnue;
    AtomicBinV2ManifestOptions options;
    std::string                inventorySha256;
    u64                        records  = 0;
    u64                        draws    = 0;
    u64                        tbProbes = 0;
    u64                        tbHits   = 0;
    AtomicBinV2ManifestShard   shard;
};

struct AtomicDatagenV2Attestation {
    std::filesystem::path attestationPath;
    std::filesystem::path manifestPath;
    u64                   manifestBytes = 0;
    std::string           manifestSha256;
    std::filesystem::path shardPath;
    u64                   shardBytes = 0;
    std::string           shardSha256;
    std::string           inventorySha256;
    std::string           producerSha256;
    std::string           teacherMode;
    std::string           useNnue;
    u64                   records  = 0;
    u64                   tbProbes = 0;
    u64                   tbHits   = 0;
};

std::filesystem::path
atomic_datagen_v2_manifest_path(const std::filesystem::path& firstShard);
std::filesystem::path
atomic_datagen_v2_attestation_path(const std::filesystem::path& bundlePath);

// Cheap destination check used before self-play. Final writers repeat this
// with an OS exclusive-create primitive, so a race can never replace output.
DataResult preflight_authenticated_datagen_v2_output(const std::filesystem::path& path);

DataResult render_atomic_datagen_v2_manifest(const AtomicDatagenV2Manifest& manifest,
                                             std::string&                    json);
DataResult write_atomic_datagen_v2_manifest(const AtomicDatagenV2Manifest& manifest);

DataResult render_atomic_datagen_v2_attestation(const AtomicDatagenV2Attestation& attestation,
                                                std::string&                       json);
DataResult write_atomic_datagen_v2_attestation(const AtomicDatagenV2Attestation& attestation);

// ATOBNDL2 contains manifest V2, attestation V1, and one Atomic BIN V2 shard.
// All sources are re-hashed while copying and the output uses exclusive create.
DataResult write_openbench_datagen_bundle_v2(const std::filesystem::path& output,
                                             const AtomicDatagenV2Attestation& attestation);

}  // namespace Stockfish::Data

#endif  // ATOMIC_DATA_AUTHENTICATED_DATAGEN_V2_H_INCLUDED
