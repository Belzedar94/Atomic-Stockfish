/*
  Atomic-Stockfish, a specialized Atomic Chess engine derived from Stockfish
  Copyright (C) 2004-2026 The Atomic-Stockfish developers

  Atomic-Stockfish is free software: you can redistribute it and/or modify
  it under the terms of the GNU General Public License as published by
  the Free Software Foundation, either version 3 of the License, or
  (at your option) any later version.
*/

#include "openbench_bundle.h"

#include <algorithm>
#include <array>
#include <cerrno>
#include <cstdint>
#include <cstring>
#include <filesystem>
#include <fstream>
#include <limits>
#include <string>
#include <string_view>
#include <system_error>
#include <type_traits>

#include "atomic_bin_v2.h"
#include "atomic_bin_v2_manifest.h"
#include "sha256.h"

#ifdef _WIN32
    #ifndef NOMINMAX
        #define NOMINMAX
    #endif
    #ifndef WIN32_LEAN_AND_MEAN
        #define WIN32_LEAN_AND_MEAN
    #endif
    #include <windows.h>
#else
    #include <fcntl.h>
    #include <sys/stat.h>
    #include <unistd.h>
#endif

namespace Stockfish::Data {
namespace {

constexpr std::array<unsigned char, 8> BundleMagic       = {'A', 'T', 'O', 'B', 'N', 'D', 'L', '1'};
constexpr u16                          BundleVersion     = 1;
constexpr u16                          BundleHeaderBytes = 256;
constexpr u32                          BundleEndianMarker = 0x01020304U;
constexpr u32                          BundleEntryCount   = 2;
constexpr u64                          BundleAlignment    = 64;
constexpr usize                        CopyBufferSize     = 1024 * 1024;

static_assert(BundleHeaderBytes % BundleAlignment == 0);

#ifndef _WIN32
std::string system_message(int error) {
    return std::generic_category().message(error ? error : EIO);
}
#endif

template<typename UInt>
void store_little_endian(std::array<unsigned char, BundleHeaderBytes>& header,
                         std::size_t                                   offset,
                         UInt                                          value) {
    static_assert(std::is_unsigned_v<UInt>);
    for (std::size_t i = 0; i < sizeof(UInt); ++i)
        header[offset + i] = static_cast<unsigned char>(value >> (8 * i));
}

bool decode_sha256(std::string_view hex, unsigned char* output) {
    if (hex.size() != 64)
        return false;
    auto nibble = [](unsigned char c) -> int {
        if (c >= '0' && c <= '9')
            return c - '0';
        if (c >= 'a' && c <= 'f')
            return 10 + c - 'a';
        if (c >= 'A' && c <= 'F')
            return 10 + c - 'A';
        return -1;
    };
    for (std::size_t i = 0; i < 32; ++i)
    {
        const int high = nibble(static_cast<unsigned char>(hex[2 * i]));
        const int low  = nibble(static_cast<unsigned char>(hex[2 * i + 1]));
        if (high < 0 || low < 0)
            return false;
        output[i] = static_cast<unsigned char>((high << 4) | low);
    }
    return true;
}

class ExclusiveOutput {
   public:
    explicit ExclusiveOutput(std::filesystem::path output) :
        path(std::move(output)) {}
    ExclusiveOutput(const ExclusiveOutput&)            = delete;
    ExclusiveOutput& operator=(const ExclusiveOutput&) = delete;
    ~ExclusiveOutput() { abort(); }

    DataResult open() {
#ifdef _WIN32
        handle = ::CreateFileW(path.c_str(), GENERIC_WRITE | DELETE, FILE_SHARE_READ, nullptr,
                               CREATE_NEW, FILE_ATTRIBUTE_NORMAL, nullptr);
        if (handle == INVALID_HANDLE_VALUE)
        {
            const auto error = ::GetLastError();
            return DataResult::failure(error == ERROR_FILE_EXISTS || error == ERROR_ALREADY_EXISTS
                                         ? DataError::OUTPUT_EXISTS
                                         : DataError::OPEN_FAILED,
                                       "Cannot create OpenBench bundle exclusively: "
                                         + std::system_category().message(int(error)));
        }
#else
        int flags = O_WRONLY | O_CREAT | O_EXCL;
    #ifdef O_CLOEXEC
        flags |= O_CLOEXEC;
    #endif
    #ifdef O_NOFOLLOW
        flags |= O_NOFOLLOW;
    #endif
        descriptor = ::open(path.c_str(), flags, 0600);
        if (descriptor == -1)
            return DataResult::failure(
              errno == EEXIST ? DataError::OUTPUT_EXISTS : DataError::OPEN_FAILED,
              "Cannot create OpenBench bundle exclusively: " + system_message(errno));
        active = true;
        if (::fstat(descriptor, &identity) != 0)
        {
            const int error = errno;
            abort();
            return DataResult::failure(DataError::OPEN_FAILED,
                                       "Cannot capture OpenBench bundle identity: "
                                         + system_message(error));
        }
#endif
#ifdef _WIN32
        active = true;
#endif
        return DataResult::success();
    }

    DataResult write(const void* bytes, u64 byteCount) {
        const auto* cursor = static_cast<const unsigned char*>(bytes);
        while (byteCount)
        {
            const auto block = std::size_t(std::min<u64>(byteCount, CopyBufferSize));
#ifdef _WIN32
            DWORD written = 0;
            if (!::WriteFile(handle, cursor, DWORD(block), &written, nullptr) || written != block)
                return DataResult::failure(
                  DataError::WRITE_FAILED,
                  "Cannot write OpenBench bundle: "
                    + std::system_category().message(int(::GetLastError())));
#else
            ssize_t written;
            do
                written = ::write(descriptor, cursor, block);
            while (written < 0 && errno == EINTR);
            if (written <= 0)
                return DataResult::failure(DataError::WRITE_FAILED,
                                           "Cannot write OpenBench bundle: "
                                             + system_message(errno));
#endif
            cursor += std::size_t(written);
            byteCount -= u64(written);
        }
        return DataResult::success();
    }

    DataResult finish() {
#ifdef _WIN32
        if (!::FlushFileBuffers(handle))
            return DataResult::failure(DataError::WRITE_FAILED,
                                       "Cannot synchronize OpenBench bundle: "
                                         + std::system_category().message(int(::GetLastError())));
        if (!::CloseHandle(handle))
            return DataResult::failure(DataError::CLOSE_FAILED,
                                       "Cannot close OpenBench bundle: "
                                         + std::system_category().message(int(::GetLastError())));
        handle = INVALID_HANDLE_VALUE;
#else
        if (::fsync(descriptor) != 0)
            return DataResult::failure(DataError::WRITE_FAILED,
                                       "Cannot synchronize OpenBench bundle: "
                                         + system_message(errno));
        if (::close(descriptor) != 0)
            return DataResult::failure(DataError::CLOSE_FAILED,
                                       "Cannot close OpenBench bundle: " + system_message(errno));
        descriptor = -1;
#endif
        active = false;
        return DataResult::success();
    }

    void abort() noexcept {
        if (!active)
            return;
#ifdef _WIN32
        FILE_DISPOSITION_INFO disposition{};
        disposition.DeleteFile = TRUE;
        ::SetFileInformationByHandle(handle, FileDispositionInfo, &disposition,
                                     sizeof(disposition));
        ::CloseHandle(handle);
        handle = INVALID_HANDLE_VALUE;
#else
        struct stat current{};
        if (::lstat(path.c_str(), &current) == 0 && current.st_dev == identity.st_dev
            && current.st_ino == identity.st_ino)
            ::unlink(path.c_str());
        ::close(descriptor);
        descriptor = -1;
#endif
        active = false;
    }

   private:
    std::filesystem::path path;
    bool                  active = false;
#ifdef _WIN32
    HANDLE handle = INVALID_HANDLE_VALUE;
#else
    int         descriptor = -1;
    struct stat identity{};
#endif
};

DataResult copy_authenticated(ExclusiveOutput&             output,
                              const std::filesystem::path& source,
                              u64                          expectedBytes,
                              std::string_view             expectedSha256) {
    std::ifstream input(source, std::ios::binary);
    if (!input)
        return DataResult::failure(DataError::OPEN_FAILED,
                                   "Cannot open OpenBench bundle source " + source.string());

    Sha256                           digest;
    std::array<char, CopyBufferSize> buffer{};
    u64                              remaining = expectedBytes;
    while (remaining)
    {
        const auto block = std::size_t(std::min<u64>(remaining, buffer.size()));
        input.read(buffer.data(), std::streamsize(block));
        if (input.gcount() != std::streamsize(block))
            return DataResult::failure(DataError::READ_FAILED,
                                       "OpenBench bundle source changed while being copied");
        digest.update(buffer.data(), block);
        if (DataResult written = output.write(buffer.data(), block); !written)
            return written;
        remaining -= block;
    }
    char extra = 0;
    if (input.read(&extra, 1))
        return DataResult::failure(DataError::FILE_IDENTITY_MISMATCH,
                                   "OpenBench bundle source grew while being copied");
    if (digest.hex_digest() != expectedSha256)
        return DataResult::failure(DataError::FILE_IDENTITY_MISMATCH,
                                   "OpenBench bundle source changed after authentication");
    return DataResult::success();
}

DataResult write_zero_padding(ExclusiveOutput& output, u64 bytes) {
    constexpr std::array<unsigned char, BundleAlignment> Zeroes{};
    while (bytes)
    {
        const u64 block = std::min<u64>(bytes, Zeroes.size());
        if (DataResult written = output.write(Zeroes.data(), block); !written)
            return written;
        bytes -= block;
    }
    return DataResult::success();
}

}  // namespace

DataResult write_openbench_datagen_bundle(const std::filesystem::path& outputPath,
                                          const std::filesystem::path& shardPath,
                                          const std::filesystem::path& manifestPath) {
    AtomicBinV2Manifest manifest;
    if (DataResult loaded = load_atomic_bin_v2_manifest(manifestPath, manifest); !loaded)
        return loaded;
    if (manifest.shards.size() != 1 || manifest.shards[0].path.filename() != shardPath.filename())
        return DataResult::failure(DataError::INVALID_MANIFEST,
                                   "OpenBench bundle requires exactly one matching V2 shard");

    std::string manifestSha;
    std::string shardSha;
    u64         manifestBytes = 0;
    u64         shardBytes    = 0;
    if (DataResult hashed = sha256_file(manifestPath, manifestSha, manifestBytes); !hashed)
        return hashed;
    if (DataResult hashed = sha256_file(shardPath, shardSha, shardBytes); !hashed)
        return hashed;
    const auto& descriptor = manifest.shards[0];
    if (descriptor.sha256 != shardSha || descriptor.bytes != shardBytes
        || descriptor.records != manifest.records)
        return DataResult::failure(DataError::FILE_IDENTITY_MISMATCH,
                                   "OpenBench bundle shard differs from its canonical manifest");

    if (manifestBytes > std::numeric_limits<u64>::max() - BundleHeaderBytes - (BundleAlignment - 1))
        return DataResult::failure(DataError::RECORD_COUNT_OUT_OF_RANGE,
                                   "OpenBench bundle manifest offset overflows u64");
    const u64 manifestOffset = BundleHeaderBytes;
    const u64 payloadOffset =
      (manifestOffset + manifestBytes + BundleAlignment - 1) & ~(BundleAlignment - 1);
    if (shardBytes > std::numeric_limits<u64>::max() - payloadOffset)
        return DataResult::failure(DataError::RECORD_COUNT_OUT_OF_RANGE,
                                   "OpenBench bundle payload extent overflows u64");

    std::array<unsigned char, BundleHeaderBytes> header{};
    std::copy(BundleMagic.begin(), BundleMagic.end(), header.begin());
    store_little_endian(header, 8, BundleVersion);
    store_little_endian(header, 10, BundleHeaderBytes);
    store_little_endian(header, 12, BundleEndianMarker);
    store_little_endian(header, 16, u32(0));
    store_little_endian(header, 20, BundleEntryCount);
    if (!decode_sha256(AtomicOpenBenchBundleSchemaSha256Hex, header.data() + 24)
        || !decode_sha256(AtomicBinV2SchemaSha256Hex, header.data() + 56)
        || !decode_sha256(AtomicBinV2ManifestSchemaSha256Hex, header.data() + 88)
        || !decode_sha256(manifestSha, header.data() + 136)
        || !decode_sha256(shardSha, header.data() + 184))
        return DataResult::failure(DataError::SCHEMA_MISMATCH,
                                   "OpenBench bundle contains an invalid SHA-256 constant");
    store_little_endian(header, 120, manifestOffset);
    store_little_endian(header, 128, manifestBytes);
    store_little_endian(header, 168, payloadOffset);
    store_little_endian(header, 176, shardBytes);
    store_little_endian(header, 216, manifest.records);

    ExclusiveOutput output(outputPath);
    if (DataResult opened = output.open(); !opened)
        return opened;
    if (DataResult written = output.write(header.data(), header.size()); !written)
        return written;
    if (DataResult copied = copy_authenticated(output, manifestPath, manifestBytes, manifestSha);
        !copied)
        return copied;
    if (DataResult padded =
          write_zero_padding(output, payloadOffset - manifestOffset - manifestBytes);
        !padded)
        return padded;
    if (DataResult copied = copy_authenticated(output, shardPath, shardBytes, shardSha); !copied)
        return copied;
    return output.finish();
}

}  // namespace Stockfish::Data
