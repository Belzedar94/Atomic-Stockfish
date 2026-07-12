/*
  Atomic-Stockfish, a UCI chess variant playing engine derived from Stockfish
  Copyright (C) 2004-2026 The Stockfish developers (see AUTHORS file)

  Atomic-Stockfish is free software: you can redistribute it and/or modify
  it under the terms of the GNU General Public License as published by
  the Free Software Foundation, either version 3 of the License, or
  (at your option) any later version.
*/

#include "sha256.h"

#include <algorithm>
#include <cerrno>
#include <cstring>
#include <limits>
#include <string>
#include <system_error>

#ifdef _WIN32
    #include <fcntl.h>
    #include <io.h>
    #include <sys/stat.h>
#else
    #include <fcntl.h>
    #include <sys/stat.h>
    #include <unistd.h>
#endif

namespace Stockfish::Data {
namespace {

constexpr std::array<u32, 64> RoundConstants = {
  0x428A2F98U, 0x71374491U, 0xB5C0FBCFU, 0xE9B5DBA5U, 0x3956C25BU, 0x59F111F1U, 0x923F82A4U,
  0xAB1C5ED5U, 0xD807AA98U, 0x12835B01U, 0x243185BEU, 0x550C7DC3U, 0x72BE5D74U, 0x80DEB1FEU,
  0x9BDC06A7U, 0xC19BF174U, 0xE49B69C1U, 0xEFBE4786U, 0x0FC19DC6U, 0x240CA1CCU, 0x2DE92C6FU,
  0x4A7484AAU, 0x5CB0A9DCU, 0x76F988DAU, 0x983E5152U, 0xA831C66DU, 0xB00327C8U, 0xBF597FC7U,
  0xC6E00BF3U, 0xD5A79147U, 0x06CA6351U, 0x14292967U, 0x27B70A85U, 0x2E1B2138U, 0x4D2C6DFCU,
  0x53380D13U, 0x650A7354U, 0x766A0ABBU, 0x81C2C92EU, 0x92722C85U, 0xA2BFE8A1U, 0xA81A664BU,
  0xC24B8B70U, 0xC76C51A3U, 0xD192E819U, 0xD6990624U, 0xF40E3585U, 0x106AA070U, 0x19A4C116U,
  0x1E376C08U, 0x2748774CU, 0x34B0BCB5U, 0x391C0CB3U, 0x4ED8AA4AU, 0x5B9CCA4FU, 0x682E6FF3U,
  0x748F82EEU, 0x78A5636FU, 0x84C87814U, 0x8CC70208U, 0x90BEFFFAU, 0xA4506CEBU, 0xBEF9A3F7U,
  0xC67178F2U};

constexpr u32 rotate_right(u32 value, unsigned shift) noexcept {
    return (value >> shift) | (value << (32 - shift));
}

std::string system_error_message(int error) {
    return std::generic_category().message(error ? error : EIO);
}

DataResult seek_to_start(int descriptor) {
    errno = 0;
#ifdef _WIN32
    if (::_lseeki64(descriptor, 0, SEEK_SET) == 0)
#else
    if (::lseek(descriptor, 0, SEEK_SET) == 0)
#endif
        return DataResult::success();

    const int error = errno;
    return DataResult::failure(DataError::OPEN_FAILED,
                               "Cannot seek file for SHA-256: " + system_error_message(error));
}

DataResult descriptor_size(int descriptor, u64& size) {
    errno = 0;
#ifdef _WIN32
    struct _stati64 status{};
    const int       result = ::_fstati64(descriptor, &status);
#else
    struct stat status{};
    const int   result = ::fstat(descriptor, &status);
#endif
    if (result != 0)
    {
        const int error = errno;
        return DataResult::failure(DataError::OPEN_FAILED, "Cannot inspect file for SHA-256: "
                                                             + system_error_message(error));
    }
    if (status.st_size < 0)
        return DataResult::failure(DataError::FILE_SIZE_MISMATCH,
                                   "Cannot hash a file with a negative size");

    using FileSize = decltype(status.st_size);
    if constexpr (sizeof(FileSize) > sizeof(u64))
        if (status.st_size > FileSize(std::numeric_limits<u64>::max()))
            return DataResult::failure(DataError::FILE_SIZE_MISMATCH,
                                       "File is too large for the SHA-256 helper");

    size = u64(status.st_size);
    return DataResult::success();
}

}  // namespace

void Sha256::transform(const u8* block) noexcept {
    std::array<u32, 64> words{};
    for (std::size_t index = 0; index < 16; ++index)
    {
        const std::size_t offset = index * 4;
        words[index]             = (u32(block[offset]) << 24) | (u32(block[offset + 1]) << 16)
                     | (u32(block[offset + 2]) << 8) | u32(block[offset + 3]);
    }
    for (std::size_t index = 16; index < words.size(); ++index)
    {
        const u32 s0 = rotate_right(words[index - 15], 7) ^ rotate_right(words[index - 15], 18)
                     ^ (words[index - 15] >> 3);
        const u32 s1 = rotate_right(words[index - 2], 17) ^ rotate_right(words[index - 2], 19)
                     ^ (words[index - 2] >> 10);
        words[index] = words[index - 16] + s0 + words[index - 7] + s1;
    }

    u32 a = state[0];
    u32 b = state[1];
    u32 c = state[2];
    u32 d = state[3];
    u32 e = state[4];
    u32 f = state[5];
    u32 g = state[6];
    u32 h = state[7];

    for (std::size_t index = 0; index < words.size(); ++index)
    {
        const u32 sum1       = rotate_right(e, 6) ^ rotate_right(e, 11) ^ rotate_right(e, 25);
        const u32 choose     = (e & f) ^ (~e & g);
        const u32 temporary1 = h + sum1 + choose + RoundConstants[index] + words[index];
        const u32 sum0       = rotate_right(a, 2) ^ rotate_right(a, 13) ^ rotate_right(a, 22);
        const u32 majority   = (a & b) ^ (a & c) ^ (b & c);
        const u32 temporary2 = sum0 + majority;

        h = g;
        g = f;
        f = e;
        e = d + temporary1;
        d = c;
        c = b;
        b = a;
        a = temporary1 + temporary2;
    }

    state[0] += a;
    state[1] += b;
    state[2] += c;
    state[3] += d;
    state[4] += e;
    state[5] += f;
    state[6] += g;
    state[7] += h;
}

void Sha256::update(const void* rawData, std::size_t size) noexcept {
    if (size == 0)
        return;

    const auto* data = static_cast<const u8*>(rawData);
    totalBytes += u64(size);

    if (buffered != 0)
    {
        const std::size_t copied = std::min(size, buffer.size() - buffered);
        std::memcpy(buffer.data() + buffered, data, copied);
        buffered += copied;
        data += copied;
        size -= copied;
        if (buffered == buffer.size())
        {
            transform(buffer.data());
            buffered = 0;
        }
    }

    while (size >= buffer.size())
    {
        transform(data);
        data += buffer.size();
        size -= buffer.size();
    }

    if (size != 0)
    {
        std::memcpy(buffer.data(), data, size);
        buffered = size;
    }
}

Sha256::Digest Sha256::digest() const noexcept {
    Sha256 final = *this;

    final.buffer[final.buffered++] = 0x80;
    if (final.buffered > 56)
    {
        std::fill(final.buffer.begin() + final.buffered, final.buffer.end(), u8(0));
        final.transform(final.buffer.data());
        final.buffered = 0;
    }

    std::fill(final.buffer.begin() + final.buffered, final.buffer.begin() + 56, u8(0));
    const u64 bitLength = totalBytes * 8;
    for (unsigned byte = 0; byte < 8; ++byte)
        final.buffer[63 - byte] = u8(bitLength >> (byte * 8));
    final.transform(final.buffer.data());

    Digest result{};
    for (std::size_t index = 0; index < final.state.size(); ++index)
        for (unsigned byte = 0; byte < 4; ++byte)
            result[index * 4 + byte] = u8(final.state[index] >> (24 - byte * 8));
    return result;
}

std::string Sha256::hex_digest() const {
    static constexpr char Hex[] = "0123456789abcdef";
    const Digest          bytes = digest();
    std::string           result(bytes.size() * 2, '0');
    for (std::size_t index = 0; index < bytes.size(); ++index)
    {
        result[index * 2]     = Hex[bytes[index] >> 4];
        result[index * 2 + 1] = Hex[bytes[index] & 0x0F];
    }
    return result;
}

DataResult sha256_file_descriptor(int descriptor, u64 byteCount, std::string& lowerHex) {
    lowerHex.clear();
    if (descriptor < 0)
        return DataResult::failure(DataError::OPEN_FAILED,
                                   "Cannot hash an invalid file descriptor");
    if (byteCount > Sha256MaxByteCount)
        return DataResult::failure(DataError::FILE_SIZE_MISMATCH,
                                   "File exceeds the SHA-256 message-length domain");
    if (DataResult seek = seek_to_start(descriptor); !seek)
        return seek;

    Sha256                hash;
    std::array<u8, 65536> chunk{};
    u64                   remaining = byteCount;
    while (remaining != 0)
    {
        const std::size_t requested = std::size_t(std::min<u64>(remaining, u64(chunk.size())));
        errno                       = 0;
#ifdef _WIN32
        const int count = ::_read(descriptor, chunk.data(), unsigned(requested));
#else
        const ssize_t count = ::read(descriptor, chunk.data(), requested);
#endif
        if (count > 0)
        {
            hash.update(chunk.data(), std::size_t(count));
            remaining -= u64(count);
            continue;
        }
        if (count < 0 && errno == EINTR)
            continue;
        if (count == 0)
            return DataResult::failure(DataError::FILE_SIZE_MISMATCH,
                                       "File ended before the requested SHA-256 byte count");

        const int error = errno;
        return DataResult::failure(DataError::OPEN_FAILED,
                                   "Cannot read file for SHA-256: " + system_error_message(error));
    }

    lowerHex = hash.hex_digest();
    return DataResult::success();
}

DataResult sha256_file(const std::filesystem::path& path, std::string& lowerHex, u64& size) {
    lowerHex.clear();
    size = 0;

    errno = 0;
#ifdef _WIN32
    const int descriptor = ::_wopen(path.c_str(), _O_BINARY | _O_RDONLY | _O_NOINHERIT);
#else
    int flags = O_RDONLY;
    #ifdef O_CLOEXEC
    flags |= O_CLOEXEC;
    #endif
    const int descriptor = ::open(path.c_str(), flags);
#endif
    if (descriptor < 0)
    {
        const int error = errno;
        return DataResult::failure(DataError::OPEN_FAILED,
                                   "Cannot open file for SHA-256: " + system_error_message(error));
    }

    DataResult result = descriptor_size(descriptor, size);
    if (result)
        result = sha256_file_descriptor(descriptor, size, lowerHex);
    if (result)
    {
        u64 finalSize = 0;
        result        = descriptor_size(descriptor, finalSize);
        if (result && finalSize != size)
            result = DataResult::failure(DataError::FILE_SIZE_MISMATCH,
                                         "File size changed while computing SHA-256");
    }

    errno = 0;
#ifdef _WIN32
    const int closeResult = ::_close(descriptor);
#else
    const int closeResult = ::close(descriptor);
#endif
    if (closeResult != 0)
    {
        const int closeError = errno;
        if (result)
            result =
              DataResult::failure(DataError::CLOSE_FAILED, "Cannot close SHA-256 input: "
                                                             + system_error_message(closeError));
        else
            result.message += "; close failed: " + system_error_message(closeError);
    }

    if (!result)
    {
        lowerHex.clear();
        size = 0;
    }
    return result;
}

}  // namespace Stockfish::Data
