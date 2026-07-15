/*
  Atomic-Stockfish, a UCI chess playing engine derived from Stockfish
  Copyright (C) 2004-2026 The Stockfish developers (see AUTHORS file)

  Atomic-Stockfish is free software: you can redistribute it and/or modify
  it under the terms of the GNU General Public License as published by
  the Free Software Foundation, either version 3 of the License, or
  (at your option) any later version.
*/

#ifndef ATOMIC_V3_WIRE_IO_H_INCLUDED
#define ATOMIC_V3_WIRE_IO_H_INCLUDED

#include <algorithm>
#include <array>
#include <cstddef>
#include <cstring>
#include <istream>
#include <limits>
#include <ostream>
#include <type_traits>

#include "../../types.h"

namespace Stockfish::Eval::NNUE::AtomicV3::WireIO {

#if (defined(ATOMIC_V3_WIRE_TEST_FORCE_IDENTITY) + defined(ATOMIC_V3_WIRE_TEST_FORCE_AVX2_LASX) \
     + defined(ATOMIC_V3_WIRE_TEST_FORCE_AVX512)) \
  > 1
    #error "AtomicNNUEV3 wire test permutation overrides are mutually exclusive"
#endif

inline constexpr char        Leb128Magic[]     = "COMPRESSED_LEB128";
inline constexpr std::size_t Leb128MagicLength = sizeof(Leb128Magic) - 1;
inline constexpr std::size_t StreamChunkBytes  = 1U << 20;

inline bool read_exact(std::istream& stream, void* destination, std::size_t byteCount) {
    auto* output = static_cast<char*>(destination);
    while (byteCount != 0)
    {
        const std::size_t chunk = std::min(byteCount, StreamChunkBytes);
        stream.read(output, static_cast<std::streamsize>(chunk));
        if (!stream || std::size_t(stream.gcount()) != chunk)
            return false;
        output += chunk;
        byteCount -= chunk;
    }
    return true;
}

inline bool write_exact(std::ostream& stream, const void* source, std::size_t byteCount) {
    const auto* input = static_cast<const char*>(source);
    while (byteCount != 0)
    {
        const std::size_t chunk = std::min(byteCount, StreamChunkBytes);
        stream.write(input, static_cast<std::streamsize>(chunk));
        if (!stream)
            return false;
        input += chunk;
        byteCount -= chunk;
    }
    return true;
}

template<typename IntType>
bool read_little_endian(std::istream& stream, IntType& value) {
    static_assert(std::is_integral_v<IntType>);
    using Unsigned = std::make_unsigned_t<IntType>;

    std::array<u8, sizeof(IntType)> bytes{};
    if (!read_exact(stream, bytes.data(), bytes.size()))
        return false;

    Unsigned bits = 0;
    for (std::size_t index = 0; index < bytes.size(); ++index)
        bits |= Unsigned(bytes[index]) << (index * 8);
    std::memcpy(&value, &bits, sizeof(value));
    return true;
}

template<typename IntType>
bool write_little_endian(std::ostream& stream, IntType value) {
    static_assert(std::is_integral_v<IntType>);
    using Unsigned = std::make_unsigned_t<IntType>;

    Unsigned bits = 0;
    std::memcpy(&bits, &value, sizeof(bits));

    std::array<u8, sizeof(IntType)> bytes{};
    for (std::size_t index = 0; index < bytes.size(); ++index)
        bytes[index] = static_cast<u8>(bits >> (index * 8));
    return write_exact(stream, bytes.data(), bytes.size());
}

inline std::size_t signed_leb_size(i64 value) noexcept {
    std::size_t size = 0;
    bool        more;
    do
    {
        const u8 byte = static_cast<u8>(value) & 0x7Fu;
        value >>= 7;
        more = !(((byte & 0x40u) == 0 && value == 0) || ((byte & 0x40u) != 0 && value == -1));
        ++size;
    } while (more);
    return size;
}

class LimitedByteReader {
   public:
    LimitedByteReader(std::istream& stream, u32 remaining) noexcept :
        stream_(stream),
        remaining_(remaining) {}

    bool read(u8& value) {
        if (remaining_ == 0)
            return false;

        if (position_ == available_)
        {
            const std::size_t requested = std::min<std::size_t>(remaining_, buffer_.size());
            stream_.read(reinterpret_cast<char*>(buffer_.data()),
                         static_cast<std::streamsize>(requested));
            available_ = std::size_t(stream_.gcount());
            position_  = 0;
            if (available_ != requested)
                return false;
        }

        value = buffer_[position_++];
        --remaining_;
        return true;
    }

    [[nodiscard]] u32 remaining() const noexcept { return remaining_; }

   private:
    std::istream&        stream_;
    u32                  remaining_ = 0;
    std::array<u8, 8192> buffer_{};
    std::size_t          position_  = 0;
    std::size_t          available_ = 0;
};

template<typename IntType>
bool read_signed_leb_value(LimitedByteReader& reader, IntType& result) {
    static_assert(std::is_signed_v<IntType>);
    static_assert(sizeof(IntType) <= sizeof(i32));

    constexpr std::size_t MaxBytes = (sizeof(IntType) * 8 + 6) / 7;
    i64                   decoded  = 0;
    unsigned              shift    = 0;

    for (std::size_t used = 1; used <= MaxBytes; ++used)
    {
        u8 byte = 0;
        if (!reader.read(byte))
            return false;

        decoded |= i64(byte & 0x7Fu) << shift;
        shift += 7;

        if ((byte & 0x80u) != 0)
            continue;

        if ((byte & 0x40u) != 0)
            decoded |= -(i64(1) << shift);

        if (decoded < std::numeric_limits<IntType>::min()
            || decoded > std::numeric_limits<IntType>::max() || signed_leb_size(decoded) != used)
            return false;

        result = static_cast<IntType>(decoded);
        return true;
    }

    return false;
}

template<typename IntType>
bool read_signed_leb(std::istream& stream, IntType* values, std::size_t count) {
    static_assert(std::is_signed_v<IntType>);
    static_assert(sizeof(IntType) <= sizeof(i32));

    if (!values && count != 0)
        return false;

    std::array<char, Leb128MagicLength> magic{};
    if (!read_exact(stream, magic.data(), magic.size())
        || std::memcmp(magic.data(), Leb128Magic, magic.size()) != 0)
        return false;

    u32 byteCount = 0;
    if (!read_little_endian(stream, byteCount))
        return false;

    constexpr std::size_t MaxBytesPerValue = (sizeof(IntType) * 8 + 6) / 7;
    if (count > std::numeric_limits<u32>::max() || byteCount < count
        || u64(byteCount) > u64(count) * MaxBytesPerValue)
        return false;

    LimitedByteReader reader(stream, byteCount);
    for (std::size_t index = 0; index < count; ++index)
        if (!read_signed_leb_value(reader, values[index]))
            return false;

    return reader.remaining() == 0;
}

class BufferedByteWriter {
   public:
    explicit BufferedByteWriter(std::ostream& stream) noexcept :
        stream_(stream) {}

    bool put(u8 value) {
        buffer_[position_++] = value;
        return position_ != buffer_.size() || flush();
    }

    bool flush() {
        if (position_ == 0)
            return bool(stream_);
        stream_.write(reinterpret_cast<const char*>(buffer_.data()),
                      static_cast<std::streamsize>(position_));
        position_ = 0;
        return bool(stream_);
    }

   private:
    std::ostream&        stream_;
    std::array<u8, 8192> buffer_{};
    std::size_t          position_ = 0;
};

template<typename IntType>
bool write_signed_leb_value(BufferedByteWriter& writer, IntType input) {
    static_assert(std::is_signed_v<IntType>);

    i64 value = input;
    while (true)
    {
        u8 byte = static_cast<u8>(value) & 0x7Fu;
        value >>= 7;
        const bool done =
          ((byte & 0x40u) == 0 && value == 0) || ((byte & 0x40u) != 0 && value == -1);
        if (!done)
            byte |= 0x80u;
        if (!writer.put(byte))
            return false;
        if (done)
            return true;
    }
}

template<typename IntType>
bool write_signed_leb(std::ostream& stream, const IntType* values, std::size_t count) {
    static_assert(std::is_signed_v<IntType>);
    static_assert(sizeof(IntType) <= sizeof(i32));

    if (!values && count != 0)
        return false;

    u64 byteCount = 0;
    for (std::size_t index = 0; index < count; ++index)
    {
        byteCount += signed_leb_size(values[index]);
        if (byteCount > std::numeric_limits<u32>::max())
            return false;
    }

    if (!write_exact(stream, Leb128Magic, Leb128MagicLength)
        || !write_little_endian(stream, static_cast<u32>(byteCount)))
        return false;

    BufferedByteWriter writer(stream);
    for (std::size_t index = 0; index < count; ++index)
        if (!write_signed_leb_value(writer, values[index]))
            return false;
    return writer.flush();
}

inline constexpr std::array<std::size_t, 8> ParameterBlockOrder = [] {
#if defined(ATOMIC_V3_WIRE_TEST_FORCE_IDENTITY)
    return std::array<std::size_t, 8>{{0, 1, 2, 3, 4, 5, 6, 7}};
#elif defined(ATOMIC_V3_WIRE_TEST_FORCE_AVX512) || defined(USE_AVX512)
    return std::array<std::size_t, 8>{{0, 2, 4, 6, 1, 3, 5, 7}};
#elif defined(ATOMIC_V3_WIRE_TEST_FORCE_AVX2_LASX) || defined(USE_AVX2) || defined(USE_LASX)
    return std::array<std::size_t, 8>{{0, 2, 1, 3, 4, 6, 5, 7}};
#else
    return std::array<std::size_t, 8>{{0, 1, 2, 3, 4, 5, 6, 7}};
#endif
}();

template<std::size_t Count>
constexpr std::array<std::size_t, Count>
inverse_permutation(const std::array<std::size_t, Count>& order) noexcept {
    std::array<std::size_t, Count> inverse{};
    for (std::size_t index = 0; index < Count; ++index)
        inverse[order[index]] = index;
    return inverse;
}

inline constexpr auto InverseParameterBlockOrder = inverse_permutation(ParameterBlockOrder);

constexpr bool parameter_block_order_matches(
  const std::array<std::size_t, ParameterBlockOrder.size()>& expected) noexcept {
    for (std::size_t index = 0; index < ParameterBlockOrder.size(); ++index)
        if (ParameterBlockOrder[index] != expected[index])
            return false;
    return true;
}

constexpr bool valid_parameter_block_order() noexcept {
    std::array<bool, ParameterBlockOrder.size()> seen{};
    for (const std::size_t value : ParameterBlockOrder)
    {
        if (value >= seen.size() || seen[value])
            return false;
        seen[value] = true;
    }
    return true;
}

constexpr bool inverse_parameter_block_order_is_exact() noexcept {
    const auto restored = inverse_permutation(InverseParameterBlockOrder);
    for (std::size_t index = 0; index < restored.size(); ++index)
        if (restored[index] != ParameterBlockOrder[index])
            return false;
    return true;
}

template<typename IntType, std::size_t BlockBytes>
constexpr std::size_t internal_index_from_canonical(std::size_t canonicalIndex) noexcept {
    static_assert(BlockBytes % sizeof(IntType) == 0);
    constexpr std::size_t ValuesPerBlock = BlockBytes / sizeof(IntType);
    constexpr std::size_t ValuesPerChunk = ValuesPerBlock * ParameterBlockOrder.size();

    const std::size_t chunkBase      = canonicalIndex / ValuesPerChunk * ValuesPerChunk;
    const std::size_t chunkOffset    = canonicalIndex % ValuesPerChunk;
    const std::size_t canonicalBlock = chunkOffset / ValuesPerBlock;
    const std::size_t blockOffset    = chunkOffset % ValuesPerBlock;
    return chunkBase + InverseParameterBlockOrder[canonicalBlock] * ValuesPerBlock + blockOffset;
}

template<typename IntType, std::size_t BlockBytes>
bool permute_parameters(IntType* values, std::size_t count) {
    static_assert(BlockBytes % sizeof(IntType) == 0);
    constexpr std::size_t ProcessBytes = BlockBytes * ParameterBlockOrder.size();

    if ((!values && count != 0) || count > std::numeric_limits<std::size_t>::max() / sizeof(IntType)
        || (count * sizeof(IntType)) % ProcessBytes != 0)
        return false;

    std::array<std::byte, ProcessBytes> buffer{};
    auto* const                         bytes = reinterpret_cast<std::byte*>(values);
    for (std::size_t offset = 0; offset < count * sizeof(IntType); offset += ProcessBytes)
    {
        for (std::size_t block = 0; block < ParameterBlockOrder.size(); ++block)
            std::memcpy(buffer.data() + block * BlockBytes,
                        bytes + offset + ParameterBlockOrder[block] * BlockBytes, BlockBytes);
        std::memcpy(bytes + offset, buffer.data(), buffer.size());
    }
    return true;
}

template<typename IntType, std::size_t BlockBytes>
void copy_canonical_chunk(const IntType*                                         internalValues,
                          std::array<IntType, BlockBytes * 8 / sizeof(IntType)>& canonicalValues) {
    static_assert(BlockBytes % sizeof(IntType) == 0);
    const auto* input  = reinterpret_cast<const std::byte*>(internalValues);
    auto*       output = reinterpret_cast<std::byte*>(canonicalValues.data());
    for (std::size_t canonicalBlock = 0; canonicalBlock < ParameterBlockOrder.size();
         ++canonicalBlock)
        std::memcpy(output + canonicalBlock * BlockBytes,
                    input + InverseParameterBlockOrder[canonicalBlock] * BlockBytes, BlockBytes);
}

template<typename IntType, std::size_t BlockBytes>
bool write_signed_leb_unpermuted(std::ostream&  stream,
                                 const IntType* internalValues,
                                 std::size_t    count) {
    static_assert(std::is_signed_v<IntType>);
    static_assert(sizeof(IntType) <= sizeof(i32));
    static_assert(BlockBytes % sizeof(IntType) == 0);
    constexpr std::size_t ValuesPerChunk = BlockBytes * 8 / sizeof(IntType);

    if ((!internalValues && count != 0) || count % ValuesPerChunk != 0)
        return false;

    u64 byteCount = 0;
    for (std::size_t index = 0; index < count; ++index)
    {
        byteCount += signed_leb_size(internalValues[index]);
        if (byteCount > std::numeric_limits<u32>::max())
            return false;
    }

    if (!write_exact(stream, Leb128Magic, Leb128MagicLength)
        || !write_little_endian(stream, static_cast<u32>(byteCount)))
        return false;

    BufferedByteWriter                  writer(stream);
    std::array<IntType, ValuesPerChunk> canonical{};
    for (std::size_t offset = 0; offset < count; offset += ValuesPerChunk)
    {
        copy_canonical_chunk<IntType, BlockBytes>(internalValues + offset, canonical);
        for (const IntType value : canonical)
            if (!write_signed_leb_value(writer, value))
                return false;
    }
    return writer.flush();
}

template<typename IntType, std::size_t BlockBytes>
bool write_raw_unpermuted(std::ostream& stream, const IntType* internalValues, std::size_t count) {
    static_assert(sizeof(IntType) == 1);
    static_assert(BlockBytes % sizeof(IntType) == 0);
    constexpr std::size_t ValuesPerChunk = BlockBytes * 8 / sizeof(IntType);

    if ((!internalValues && count != 0) || count % ValuesPerChunk != 0)
        return false;

    std::array<IntType, ValuesPerChunk> canonical{};
    std::array<IntType, 65536>          output{};
    std::size_t                         buffered = 0;
    auto                                flush    = [&]() {
        const bool success = write_exact(stream, output.data(), buffered);
        buffered = 0;
        return success;
    };

    for (std::size_t offset = 0; offset < count; offset += ValuesPerChunk)
    {
        copy_canonical_chunk<IntType, BlockBytes>(internalValues + offset, canonical);
        if (buffered + canonical.size() > output.size() && !flush())
            return false;
        std::copy(canonical.begin(), canonical.end(), output.begin() + buffered);
        buffered += canonical.size();
    }
    return flush();
}

static_assert(valid_parameter_block_order());
static_assert(inverse_parameter_block_order_is_exact());
#if defined(ATOMIC_V3_WIRE_TEST_FORCE_IDENTITY)
static_assert(parameter_block_order_matches(std::array<std::size_t, 8>{{0, 1, 2, 3, 4, 5, 6, 7}}));
#elif defined(ATOMIC_V3_WIRE_TEST_FORCE_AVX512) || defined(USE_AVX512)
static_assert(parameter_block_order_matches(std::array<std::size_t, 8>{{0, 2, 4, 6, 1, 3, 5, 7}}));
#elif defined(ATOMIC_V3_WIRE_TEST_FORCE_AVX2_LASX) || defined(USE_AVX2) || defined(USE_LASX)
static_assert(parameter_block_order_matches(std::array<std::size_t, 8>{{0, 2, 1, 3, 4, 6, 5, 7}}));
#else
static_assert(parameter_block_order_matches(std::array<std::size_t, 8>{{0, 1, 2, 3, 4, 5, 6, 7}}));
#endif
static_assert(sizeof(i8) == 1 && std::numeric_limits<i8>::min() == -128
              && std::numeric_limits<i8>::max() == 127);

}  // namespace Stockfish::Eval::NNUE::AtomicV3::WireIO

#endif  // ATOMIC_V3_WIRE_IO_H_INCLUDED
