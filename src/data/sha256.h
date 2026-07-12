/*
  Atomic-Stockfish, a UCI chess variant playing engine derived from Stockfish
  Copyright (C) 2004-2026 The Stockfish developers (see AUTHORS file)

  Atomic-Stockfish is free software: you can redistribute it and/or modify
  it under the terms of the GNU General Public License as published by
  the Free Software Foundation, either version 3 of the License, or
  (at your option) any later version.
*/

#ifndef ATOMIC_DATA_SHA256_H_INCLUDED
#define ATOMIC_DATA_SHA256_H_INCLUDED

#include <array>
#include <cstddef>
#include <filesystem>
#include <limits>
#include <string>
#include <string_view>

#include "training_data.h"

namespace Stockfish::Data {

inline constexpr u64 Sha256MaxByteCount = std::numeric_limits<u64>::max() / 8;

// Small dependency-free SHA-256 implementation used to authenticate datasets,
// manifests, networks and opening books. digest() finalizes a copy, so callers
// may inspect a prefix and continue feeding the original object. As required by
// SHA-256, one object must receive no more than Sha256MaxByteCount bytes; the
// file helpers below enforce that bound before reading.
class Sha256 {
   public:
    using Digest = std::array<u8, 32>;

    Sha256() = default;

    void update(const void* data, std::size_t size) noexcept;
    void update(std::string_view text) noexcept { update(text.data(), text.size()); }

    Digest      digest() const noexcept;
    std::string hex_digest() const;

   private:
    void transform(const u8* block) noexcept;

    std::array<u32, 8> state = {0x6A09E667U, 0xBB67AE85U, 0x3C6EF372U, 0xA54FF53AU,
                                0x510E527FU, 0x9B05688CU, 0x1F83D9ABU, 0x5BE0CD19U};
    std::array<u8, 64> buffer{};
    u64                totalBytes = 0;
    std::size_t        buffered   = 0;
};

// Hash exactly byteCount bytes starting at offset zero. The descriptor remains
// open and is left positioned immediately after the hashed range. This lets the
// Atomic BIN V2 sink patch and authenticate the very same owned descriptor.
DataResult sha256_file_descriptor(int descriptor, u64 byteCount, std::string& lowerHex);

// Open and hash the complete file. size receives the authenticated byte count.
DataResult sha256_file(const std::filesystem::path& path, std::string& lowerHex, u64& size);

}  // namespace Stockfish::Data

#endif  // #ifndef ATOMIC_DATA_SHA256_H_INCLUDED
