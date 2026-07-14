/*
  Stockfish, a UCI chess playing engine derived from Glaurung 2.1
  Copyright (C) 2004-2026 The Stockfish developers (see AUTHORS file)

  Stockfish is free software: you can redistribute it and/or modify
  it under the terms of the GNU General Public License as published by
  the Free Software Foundation, either version 3 of the License, or
  (at your option) any later version.
*/

#ifndef ATOMIC_V2_IO_H_INCLUDED
#define ATOMIC_V2_IO_H_INCLUDED

#include <algorithm>
#include <array>
#include <cstdint>
#include <cstring>
#include <istream>
#include <limits>
#include <ostream>
#include <type_traits>

#include "../nnue_common.h"

namespace Stockfish::Eval::NNUE::AtomicV2::IO {

inline constexpr char  Leb128Magic[]     = "COMPRESSED_LEB128";
inline constexpr usize Leb128MagicLength = sizeof(Leb128Magic) - 1;

template<typename IntType>
bool read_little_endian(std::istream& stream, IntType& value) {
    value = ::Stockfish::Eval::NNUE::read_little_endian<IntType>(stream);
    return bool(stream);
}

template<typename IntType>
bool write_little_endian(std::ostream& stream, IntType value) {
    ::Stockfish::Eval::NNUE::write_little_endian<IntType>(stream, value);
    return bool(stream);
}

inline usize signed_leb_size(std::int64_t value) noexcept {
    usize size = 0;
    bool  more;
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
    LimitedByteReader(std::istream& stream, u32 remaining) :
        stream_(stream),
        remaining_(remaining) {}

    bool read(u8& value) {
        if (remaining_ == 0)
            return false;

        if (position_ == available_)
        {
            const usize requested = std::min<usize>(remaining_, buffer_.size());
            stream_.read(reinterpret_cast<char*>(buffer_.data()), std::streamsize(requested));
            available_ = usize(stream_.gcount());
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
    usize                position_  = 0;
    usize                available_ = 0;
};

template<typename IntType>
bool read_signed_leb_value(LimitedByteReader& reader, IntType& value) {
    static_assert(std::is_signed_v<IntType>);
    static_assert(sizeof(IntType) <= sizeof(i32));

    constexpr usize MaxBytes = (sizeof(IntType) * 8 + 6) / 7;
    std::int64_t    decoded  = 0;
    unsigned        shift    = 0;

    for (usize used = 1; used <= MaxBytes; ++used)
    {
        u8 byte;
        if (!reader.read(byte))
            return false;

        decoded |= std::int64_t(byte & 0x7Fu) << shift;
        shift += 7;

        if ((byte & 0x80u) != 0)
            continue;

        if ((byte & 0x40u) != 0)
            decoded |= -(std::int64_t(1) << shift);

        if (decoded < std::numeric_limits<IntType>::min()
            || decoded > std::numeric_limits<IntType>::max() || signed_leb_size(decoded) != used)
            return false;

        value = static_cast<IntType>(decoded);
        return true;
    }

    return false;
}

template<typename IntType, usize Count>
bool read_signed_leb(std::istream& stream, std::array<IntType, Count>& values) {
    static_assert(std::is_signed_v<IntType>);

    std::array<char, Leb128MagicLength> magic{};
    stream.read(magic.data(), std::streamsize(magic.size()));
    if (!stream || std::memcmp(magic.data(), Leb128Magic, magic.size()) != 0)
        return false;

    u32 byteCount = 0;
    if (!read_little_endian(stream, byteCount))
        return false;

    constexpr usize MaxBytesPerValue = (sizeof(IntType) * 8 + 6) / 7;
    if (byteCount < Count || std::uint64_t(byteCount) > std::uint64_t(Count) * MaxBytesPerValue)
        return false;

    LimitedByteReader reader(stream, byteCount);
    for (auto& value : values)
        if (!read_signed_leb_value(reader, value))
            return false;

    return reader.remaining() == 0;
}

template<typename IntType, usize Count>
bool write_signed_leb(std::ostream& stream, const std::array<IntType, Count>& values) {
    static_assert(std::is_signed_v<IntType>);

    std::uint64_t byteCount = 0;
    for (const IntType value : values)
        byteCount += signed_leb_size(value);
    if (byteCount > std::numeric_limits<u32>::max())
        return false;

    stream.write(Leb128Magic, std::streamsize(Leb128MagicLength));
    if (!write_little_endian(stream, static_cast<u32>(byteCount)))
        return false;

    std::array<u8, 8192> buffer{};
    usize                position = 0;
    auto                 flush    = [&]() {
        if (position == 0)
            return bool(stream);
        stream.write(reinterpret_cast<const char*>(buffer.data()), std::streamsize(position));
        position = 0;
        return bool(stream);
    };

    for (IntType input : values)
    {
        std::int64_t value = input;
        while (true)
        {
            u8 byte = static_cast<u8>(value) & 0x7Fu;
            value >>= 7;
            const bool done =
              ((byte & 0x40u) == 0 && value == 0) || ((byte & 0x40u) != 0 && value == -1);
            if (!done)
                byte |= 0x80u;

            buffer[position++] = byte;
            if (position == buffer.size() && !flush())
                return false;
            if (done)
                break;
        }
    }

    return flush();
}

}  // namespace Stockfish::Eval::NNUE::AtomicV2::IO

#endif  // ATOMIC_V2_IO_H_INCLUDED
