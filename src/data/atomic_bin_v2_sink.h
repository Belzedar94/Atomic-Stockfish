/*
  Atomic-Stockfish, a UCI chess variant playing engine derived from Stockfish
  Copyright (C) 2004-2026 The Stockfish developers (see AUTHORS file)

  Atomic-Stockfish is free software: you can redistribute it and/or modify
  it under the terms of the GNU General Public License as published by
  the Free Software Foundation, either version 3 of the License, or
  (at your option) any later version.
*/

#ifndef ATOMIC_BIN_V2_SINK_H_INCLUDED
#define ATOMIC_BIN_V2_SINK_H_INCLUDED

#include <cstddef>
#include <filesystem>
#include <string>

#include "atomic_bin_v2.h"

namespace Stockfish::Data {

// Exclusive, non-appending Atomic BIN V2 writer. The destination is not opened
// until the first sample has passed the semantic Position/move adapter. While
// owned but unfinished its header intentionally carries record_count == 0.
class AtomicBinV2Sink final: public DatasetSink {
   public:
    explicit AtomicBinV2Sink(std::filesystem::path path);
    ~AtomicBinV2Sink() override;

    AtomicBinV2Sink(const AtomicBinV2Sink&)            = delete;
    AtomicBinV2Sink& operator=(const AtomicBinV2Sink&) = delete;
    AtomicBinV2Sink(AtomicBinV2Sink&&)                 = delete;
    AtomicBinV2Sink& operator=(AtomicBinV2Sink&&)      = delete;

    DataResult append(const TrainingDataSample& sample) override;
    DataResult finalize() override;
    DataResult abort() override;

    // Transactional OutputSeries rollback for a shard that was already closed
    // successfully. The retained identity token detects replacement before
    // removal. POSIX callers must keep the output directory free of concurrent
    // writers because there is no portable unlink-if-inode primitive. Idempotent
    // after successful removal.
    DataResult remove_finalized_owned();

    const std::filesystem::path& output_path() const noexcept { return outputPath; }
    u64                          records_written() const noexcept { return recordsWritten; }

    // These remain zero/empty until finalize() succeeds.
    u64                finalized_size() const noexcept { return finalizedSize; }
    const std::string& sha256_hex() const noexcept { return finalizedSha256; }

   private:
    enum class OwnedPathState {
        MATCHES,
        MISSING,
        DIFFERENT,
        INSPECTION_ERROR
    };

    DataResult     open_exclusively();
    DataResult     capture_identity();
    OwnedPathState inspect_owned_path(int& error) const noexcept;
    DataResult     write_bytes(const u8* data, std::size_t size, const char* description);
    DataResult     seek_to_start();
    DataResult     inspect_size(u64& size) const;
    DataResult     synchronize();
    DataResult     close_output();
    void           cleanup_partial(int& closeError, int& removeError) noexcept;

    std::filesystem::path outputPath;
    u64                   recordsWritten = 0;
    u64                   finalizedSize  = 0;
    std::string           finalizedSha256;
    int                   fileDescriptor = -1;
    u64                   identityDomain = 0;
    u64                   identityFile   = 0;
    bool                  created        = false;
    bool                  identityKnown  = false;
    bool                  accepting      = true;
    bool                  finalized      = false;
    bool                  aborted        = false;
};

}  // namespace Stockfish::Data

#endif  // #ifndef ATOMIC_BIN_V2_SINK_H_INCLUDED
