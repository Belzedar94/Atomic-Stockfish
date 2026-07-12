/*
  Atomic-Stockfish, a UCI chess variant playing engine derived from Stockfish
  Copyright (C) 2004-2026 The Stockfish developers (see AUTHORS file)

  Atomic-Stockfish is free software: you can redistribute it and/or modify
  it under the terms of the GNU General Public License as published by
  the Free Software Foundation, either version 3 of the License, or
  (at your option) any later version.

  Atomic-Stockfish is distributed in the hope that it will be useful,
  but WITHOUT ANY WARRANTY; without even the implied warranty of
  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
  GNU General Public License for more details.

  You should have received a copy of the GNU General Public License
  along with Atomic-Stockfish.  If not, see <http://www.gnu.org/licenses/>.
*/

#ifndef LEGACY_ATOMIC_V1_H_INCLUDED
#define LEGACY_ATOMIC_V1_H_INCLUDED

#include <array>
#include <cstddef>
#include <filesystem>
#include <string_view>

#include "training_data.h"

namespace Stockfish::Data {

inline constexpr std::size_t LegacyAtomicV1PackedPositionSize = 64;
inline constexpr std::size_t LegacyAtomicV1RecordSize         = 72;
inline constexpr int         LegacyAtomicV1MaxRule50          = 127;

inline constexpr bool legacy_atomic_v1_rule50_fits(int rule50) noexcept {
    return rule50 >= 0 && rule50 <= LegacyAtomicV1MaxRule50;
}

static_assert(sizeof(Move) == sizeof(u16), "Legacy Atomic V1 requires the native 16-bit Move wire");

using LegacyAtomicV1Record = std::array<u8, LegacyAtomicV1RecordSize>;

// Machine-readable capability handshake for the schema frozen in
// schemas/atomic-schema.json. This codec is intentionally write-only.
std::string_view atomic_data_schema_json() noexcept;

// Encode one historical headerless record. The output is zero-filled even on
// failure, so callers cannot accidentally consume a partially encoded record.
DataResult encode_legacy_atomic_v1(const TrainingDataSample& sample, LegacyAtomicV1Record& record);

// The legacy format forbids append and overwrite. The destination is therefore
// opened atomically with exclusive-create on the first successful write. A sink
// that receives no records never creates an invalid empty dataset.
class LegacyAtomicV1Sink final: public DatasetSink {
   public:
    explicit LegacyAtomicV1Sink(std::filesystem::path path);
    ~LegacyAtomicV1Sink() override;

    LegacyAtomicV1Sink(const LegacyAtomicV1Sink&)            = delete;
    LegacyAtomicV1Sink& operator=(const LegacyAtomicV1Sink&) = delete;
    LegacyAtomicV1Sink(LegacyAtomicV1Sink&&)                 = delete;
    LegacyAtomicV1Sink& operator=(LegacyAtomicV1Sink&&)      = delete;

    DataResult append(const TrainingDataSample& sample) override;
    DataResult finalize() override;
    DataResult abort() override;

    std::size_t                  records_written() const noexcept { return recordsWritten; }
    const std::filesystem::path& output_path() const noexcept { return outputPath; }

   private:
    DataResult open_exclusively();
    DataResult write_record(const LegacyAtomicV1Record& record);
    void       cleanup_partial(int& closeError, int& removeError) noexcept;

    std::filesystem::path outputPath;
    std::size_t           recordsWritten = 0;
    int                   fileDescriptor = -1;
    bool                  created        = false;
    bool                  accepting      = true;
    bool                  finalized      = false;
    bool                  aborted        = false;
};

}  // namespace Stockfish::Data

#endif  // #ifndef LEGACY_ATOMIC_V1_H_INCLUDED
