/*
  Atomic-Stockfish, a UCI chess variant playing engine derived from Stockfish
  Copyright (C) 2004-2026 The Stockfish developers (see AUTHORS file)

  Atomic-Stockfish is free software: you can redistribute it and/or modify
  it under the terms of the GNU General Public License as published by
  the Free Software Foundation, either version 3 of the License, or
  (at your option) any later version.
*/

#ifndef ATOMIC_BIN_V2_READER_H_INCLUDED
#define ATOMIC_BIN_V2_READER_H_INCLUDED

#include <filesystem>
#include <memory>
#include <vector>

#include "atomic_bin_v2.h"
#include "atomic_bin_v2_manifest.h"

namespace Stockfish::Data {

struct AtomicBinV2DecodedRecord {
    AtomicBinV2RecordFields fields;
    TrainingDataSample      sample;
    u64                     shardIndex  = 0;
    u64                     localIndex  = 0;
    u64                     globalIndex = 0;
};

// A manifest-authoritative streaming reader. open() authenticates every shard,
// validates every record through the Atomic rules engine, verifies byte-exact
// re-encoding and checks aggregate statistics before returning the reader.
// next() then streams from the already authenticated descriptors without
// loading the dataset into memory.
class AtomicBinV2DatasetReader {
   public:
    ~AtomicBinV2DatasetReader();

    AtomicBinV2DatasetReader(const AtomicBinV2DatasetReader&)            = delete;
    AtomicBinV2DatasetReader& operator=(const AtomicBinV2DatasetReader&) = delete;

    static DataResult open(const std::filesystem::path&               manifestPath,
                           std::unique_ptr<AtomicBinV2DatasetReader>& output);

    DataResult next(AtomicBinV2DecodedRecord& output, bool& hasRecord);
    DataResult rewind();

    const AtomicBinV2Manifest& manifest() const noexcept { return metadata; }

   private:
    struct Shard;

    AtomicBinV2DatasetReader() = default;
    DataResult open_shard(std::size_t index, bool establishIdentity);
    DataResult verify_shard(std::size_t index, u64 local, u64 global, bool verifyPath);
    DataResult decode_record(const AtomicBinV2Record&  wire,
                             std::size_t               shard,
                             u64                       local,
                             u64                       global,
                             AtomicBinV2DecodedRecord& output) const;

    AtomicBinV2Manifest                 metadata;
    std::vector<std::unique_ptr<Shard>> shards;
    std::size_t                         currentShard  = 0;
    u64                                 currentLocal  = 0;
    u64                                 currentGlobal = 0;
    bool                                failed        = false;
};

}  // namespace Stockfish::Data

#endif  // #ifndef ATOMIC_BIN_V2_READER_H_INCLUDED
