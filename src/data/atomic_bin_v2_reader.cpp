/*
  Atomic-Stockfish, a UCI chess variant playing engine derived from Stockfish
  Copyright (C) 2004-2026 The Stockfish developers (see AUTHORS file)

  Atomic-Stockfish is free software: you can redistribute it and/or modify
  it under the terms of the GNU General Public License as published by
  the Free Software Foundation, either version 3 of the License, or
  (at your option) any later version.
*/

#include "atomic_bin_v2_reader.h"

#include <algorithm>
#include <cerrno>
#include <cstdint>
#include <limits>
#include <set>
#include <string>
#include <system_error>
#include <utility>

#include "sha256.h"

#ifdef _WIN32
    #ifndef NOMINMAX
        #define NOMINMAX
    #endif
    #ifndef WIN32_LEAN_AND_MEAN
        #define WIN32_LEAN_AND_MEAN
    #endif
    #include <windows.h>
    #include <fcntl.h>
    #include <io.h>
#else
    #include <fcntl.h>
    #include <sys/stat.h>
    #include <unistd.h>
#endif

namespace Stockfish::Data {
namespace {

std::string system_message(int error) {
    return std::generic_category().message(error ? error : EIO);
}

DataResult indexed(DataError error, std::string message, u64 shard, u64 local, u64 global) {
    return DataResult::failure(error, "Atomic BIN V2 shard=" + std::to_string(shard)
                                        + " local=" + std::to_string(local) + " global="
                                        + std::to_string(global) + ": " + std::move(message));
}

#ifdef _WIN32
struct FileIdentity {
    DWORD volume = 0;
    DWORD high   = 0;
    DWORD low    = 0;

    bool operator==(const FileIdentity& other) const {
        return volume == other.volume && high == other.high && low == other.low;
    }
    bool operator!=(const FileIdentity& other) const { return !(*this == other); }
    bool operator<(const FileIdentity& other) const {
        if (volume != other.volume)
            return volume < other.volume;
        return high != other.high ? high < other.high : low < other.low;
    }
};

struct ChangeToken {
    FILETIME creation{};
    FILETIME write{};
    u64      size = 0;

    bool operator==(const ChangeToken& other) const {
        return creation.dwLowDateTime == other.creation.dwLowDateTime
            && creation.dwHighDateTime == other.creation.dwHighDateTime
            && write.dwLowDateTime == other.write.dwLowDateTime
            && write.dwHighDateTime == other.write.dwHighDateTime && size == other.size;
    }
    bool operator!=(const ChangeToken& other) const { return !(*this == other); }
};

bool inspect_handle(HANDLE handle, FileIdentity& identity, ChangeToken& token) {
    FILE_ATTRIBUTE_TAG_INFO    tag{};
    BY_HANDLE_FILE_INFORMATION info{};
    if (!::GetFileInformationByHandleEx(handle, FileAttributeTagInfo, &tag, sizeof(tag))
        || (tag.FileAttributes & (FILE_ATTRIBUTE_REPARSE_POINT | FILE_ATTRIBUTE_DIRECTORY))
        || !::GetFileInformationByHandle(handle, &info))
        return false;
    identity       = {info.dwVolumeSerialNumber, info.nFileIndexHigh, info.nFileIndexLow};
    token.creation = info.ftCreationTime;
    token.write    = info.ftLastWriteTime;
    token.size     = (u64(info.nFileSizeHigh) << 32) | info.nFileSizeLow;
    return true;
}

int open_regular(const std::filesystem::path& path, FileIdentity& identity, ChangeToken& token) {
    const HANDLE handle =
      ::CreateFileW(path.c_str(), GENERIC_READ, FILE_SHARE_READ | FILE_SHARE_DELETE, nullptr,
                    OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL | FILE_FLAG_OPEN_REPARSE_POINT, nullptr);
    if (handle == INVALID_HANDLE_VALUE)
        return -1;
    if (!inspect_handle(handle, identity, token))
    {
        ::CloseHandle(handle);
        ::SetLastError(ERROR_CANT_ACCESS_FILE);
        errno = EACCES;
        return -1;
    }
    const int descriptor =
      ::_open_osfhandle(reinterpret_cast<std::intptr_t>(handle), _O_RDONLY | _O_BINARY);
    if (descriptor == -1)
        ::CloseHandle(handle);
    return descriptor;
}

bool inspect_descriptor(int descriptor, FileIdentity& identity, ChangeToken& token) {
    const std::intptr_t native = ::_get_osfhandle(descriptor);
    return native != -1 && inspect_handle(reinterpret_cast<HANDLE>(native), identity, token);
}

void close_descriptor(int descriptor) { ::_close(descriptor); }

bool seek_absolute(int descriptor, u64 offset) {
    return offset <= u64(std::numeric_limits<__int64>::max())
        && ::_lseeki64(descriptor, __int64(offset), SEEK_SET) == __int64(offset);
}

int read_descriptor(int descriptor, void* bytes, std::size_t size) {
    return ::_read(descriptor, bytes, unsigned(std::min<std::size_t>(size, unsigned(-1) >> 1)));
}
#else
struct FileIdentity {
    dev_t device = 0;
    ino_t inode  = 0;

    bool operator==(const FileIdentity& other) const {
        return device == other.device && inode == other.inode;
    }
    bool operator!=(const FileIdentity& other) const { return !(*this == other); }
    bool operator<(const FileIdentity& other) const {
        return device != other.device ? device < other.device : inode < other.inode;
    }
};

struct ChangeToken {
    std::int64_t modifiedSeconds     = 0;
    long         modifiedNanoseconds = 0;
    std::int64_t changedSeconds      = 0;
    long         changedNanoseconds  = 0;
    u64          size                = 0;

    bool operator==(const ChangeToken& other) const {
        return modifiedSeconds == other.modifiedSeconds
            && modifiedNanoseconds == other.modifiedNanoseconds
            && changedSeconds == other.changedSeconds
            && changedNanoseconds == other.changedNanoseconds && size == other.size;
    }
    bool operator!=(const ChangeToken& other) const { return !(*this == other); }
};

bool inspect_status(const struct stat& status, FileIdentity& identity, ChangeToken& token) {
    if (!S_ISREG(status.st_mode) || status.st_size < 0)
        return false;
    identity   = {status.st_dev, status.st_ino};
    token.size = u64(status.st_size);
    #if defined(__APPLE__)
    token.modifiedSeconds     = status.st_mtimespec.tv_sec;
    token.modifiedNanoseconds = status.st_mtimespec.tv_nsec;
    token.changedSeconds      = status.st_ctimespec.tv_sec;
    token.changedNanoseconds  = status.st_ctimespec.tv_nsec;
    #else
    token.modifiedSeconds     = status.st_mtim.tv_sec;
    token.modifiedNanoseconds = status.st_mtim.tv_nsec;
    token.changedSeconds      = status.st_ctim.tv_sec;
    token.changedNanoseconds  = status.st_ctim.tv_nsec;
    #endif
    return true;
}

int open_regular(const std::filesystem::path& path, FileIdentity& identity, ChangeToken& token) {
    int flags = O_RDONLY;
    #ifdef O_CLOEXEC
    flags |= O_CLOEXEC;
    #endif
    #ifdef O_NOFOLLOW
    flags |= O_NOFOLLOW;
    #endif
    const int descriptor = ::open(path.c_str(), flags);
    if (descriptor == -1)
        return -1;
    struct stat status{};
    if (::fstat(descriptor, &status) != 0 || !inspect_status(status, identity, token))
    {
        ::close(descriptor);
        errno = EINVAL;
        return -1;
    }
    return descriptor;
}

bool inspect_descriptor(int descriptor, FileIdentity& identity, ChangeToken& token) {
    struct stat status{};
    return ::fstat(descriptor, &status) == 0 && inspect_status(status, identity, token);
}

void close_descriptor(int descriptor) { ::close(descriptor); }

bool seek_absolute(int descriptor, u64 offset) {
    if constexpr (sizeof(off_t) <= sizeof(u64))
        if (offset > u64(std::numeric_limits<off_t>::max()))
            return false;
    return ::lseek(descriptor, off_t(offset), SEEK_SET) == off_t(offset);
}

ssize_t read_descriptor(int descriptor, void* bytes, std::size_t size) {
    return ::read(descriptor, bytes, size);
}
#endif

DataResult read_exact(int descriptor, void* output, std::size_t size) {
    auto*       bytes  = static_cast<u8*>(output);
    std::size_t offset = 0;
    while (offset < size)
    {
        errno            = 0;
        const auto count = read_descriptor(descriptor, bytes + offset, size - offset);
        if (count > 0)
        {
            offset += std::size_t(count);
            continue;
        }
        if (count < 0 && errno == EINTR)
            continue;
        return DataResult::failure(
          count == 0 ? DataError::FILE_SIZE_MISMATCH : DataError::READ_FAILED,
          count == 0 ? "unexpected end of shard" : "shard read failed: " + system_message(errno));
    }
    return DataResult::success();
}

}  // namespace

struct AtomicBinV2DatasetReader::Shard {
    std::filesystem::path path;
    int                   descriptor = -1;
    FileIdentity          identity;
    ChangeToken           token;

    ~Shard() {
        if (descriptor >= 0)
            close_descriptor(descriptor);
    }

    void close() {
        if (descriptor >= 0)
        {
            close_descriptor(descriptor);
            descriptor = -1;
        }
    }
};

AtomicBinV2DatasetReader::~AtomicBinV2DatasetReader() = default;

DataResult AtomicBinV2DatasetReader::open_shard(std::size_t index, bool establishIdentity) {
    auto&       shard    = *shards[index];
    const auto& expected = metadata.shards[index];
    shard.close();

    FileIdentity identity;
    ChangeToken  token;
    errno            = 0;
    shard.descriptor = open_regular(shard.path, identity, token);
    const u64 global = currentGlobal;
    if (shard.descriptor < 0)
        return indexed(DataError::OPEN_FAILED,
                       "cannot open regular non-link shard: " + system_message(errno), index, 0,
                       global);
    if (token.size != expected.bytes)
        return indexed(DataError::FILE_SIZE_MISMATCH, "shard size differs from manifest", index, 0,
                       global);
    if (establishIdentity)
    {
        shard.identity = identity;
        shard.token    = token;
    }
    else if (identity != shard.identity)
        return indexed(DataError::FILE_IDENTITY_MISMATCH,
                       "shard pathname was replaced after authentication", index, 0, global);

    std::string hash;
    if (DataResult hashed = sha256_file_descriptor(shard.descriptor, token.size, hash); !hashed)
        return indexed(hashed.error, hashed.message, index, 0, global);
    FileIdentity afterIdentity;
    ChangeToken  afterToken;
    if (!inspect_descriptor(shard.descriptor, afterIdentity, afterToken)
        || afterIdentity != identity || afterToken != token)
        return indexed(DataError::FILE_IDENTITY_MISMATCH, "shard changed while being authenticated",
                       index, 0, global);
    if (hash != expected.sha256)
        return indexed(DataError::SCHEMA_MISMATCH, "shard SHA-256 differs from manifest", index, 0,
                       global);
    if (!seek_absolute(shard.descriptor, 0))
        return indexed(DataError::READ_FAILED, "cannot seek to shard header", index, 0, global);
    AtomicBinV2Header header{};
    if (DataResult read = read_exact(shard.descriptor, header.data(), header.size()); !read)
        return indexed(read.error, read.message, index, 0, global);
    u64 headerCount = 0;
    if (DataResult decoded = decode_atomic_bin_v2_header(header, headerCount); !decoded)
        return indexed(decoded.error, decoded.message, index, 0, global);
    if (headerCount != expected.records)
        return indexed(DataError::RECORD_COUNT_OUT_OF_RANGE,
                       "header record count differs from manifest", index, 0, global);
    return verify_shard(index, 0, global, false);
}

DataResult
AtomicBinV2DatasetReader::verify_shard(std::size_t index, u64 local, u64 global, bool verifyPath) {
    auto&        shard = *shards[index];
    FileIdentity identity;
    ChangeToken  token;
    if (shard.descriptor < 0 || !inspect_descriptor(shard.descriptor, identity, token)
        || identity != shard.identity || token != shard.token)
        return indexed(DataError::FILE_IDENTITY_MISMATCH, "authenticated shard descriptor changed",
                       index, local, global);
    if (!verifyPath)
        return DataResult::success();

    FileIdentity pathIdentity;
    ChangeToken  pathToken;
    const int    check = open_regular(shard.path, pathIdentity, pathToken);
    if (check < 0)
        return indexed(DataError::FILE_IDENTITY_MISMATCH,
                       "shard pathname cannot be re-authenticated", index, local, global);
    close_descriptor(check);
    if (pathIdentity != shard.identity || pathToken != shard.token)
        return indexed(DataError::FILE_IDENTITY_MISMATCH, "shard pathname was replaced or changed",
                       index, local, global);
    return DataResult::success();
}

DataResult AtomicBinV2DatasetReader::decode_record(const AtomicBinV2Record&  wire,
                                                   std::size_t               shard,
                                                   u64                       local,
                                                   u64                       global,
                                                   AtomicBinV2DecodedRecord& output) const {
    output = {};
    AtomicBinV2DecodedRecord decoded;
    if (DataResult structural = decode_atomic_bin_v2_record_structural(wire, decoded.fields);
        !structural)
        return indexed(structural.error, structural.message, shard, local, global);
    if (bool(decoded.fields.flags & ATOMIC_BIN_V2_ATOMIC960) != metadata.atomic960)
        return indexed(DataError::INVALID_RECORD,
                       "record Atomic960 flag differs from manifest generation mode", shard, local,
                       global);
    if (DataResult semantic = decode_atomic_bin_v2(wire, decoded.sample); !semantic)
        return indexed(semantic.error, semantic.message, shard, local, global);
    AtomicBinV2Record roundTrip{};
    if (DataResult encoded = encode_atomic_bin_v2(decoded.sample, roundTrip); !encoded)
        return indexed(encoded.error, "decoded record cannot be re-encoded: " + encoded.message,
                       shard, local, global);
    if (roundTrip != wire)
        return indexed(DataError::INVALID_RECORD, "record is not byte-exact canonical", shard,
                       local, global);
    decoded.shardIndex  = shard;
    decoded.localIndex  = local;
    decoded.globalIndex = global;
    output              = std::move(decoded);
    return DataResult::success();
}

DataResult AtomicBinV2DatasetReader::open(const std::filesystem::path&               manifestPath,
                                          std::unique_ptr<AtomicBinV2DatasetReader>& output) {
    output.reset();
    auto reader = std::unique_ptr<AtomicBinV2DatasetReader>(new AtomicBinV2DatasetReader());
    if (DataResult loaded = load_atomic_bin_v2_manifest(manifestPath, reader->metadata); !loaded)
        return loaded;

    std::set<std::filesystem::path> namedPaths;
    std::set<FileIdentity>          identities;
    u64                             auditedDraws = 0;
    for (std::size_t index = 0; index < reader->metadata.shards.size(); ++index)
    {
        const auto& expected = reader->metadata.shards[index];
        if (!namedPaths.insert(expected.path.lexically_normal()).second)
            return indexed(DataError::INVALID_MANIFEST, "manifest repeats a shard pathname", index,
                           0, reader->currentGlobal);

        auto shard  = std::make_unique<Shard>();
        shard->path = expected.path;
        reader->shards.push_back(std::move(shard));
        if (DataResult opened = reader->open_shard(index, true); !opened)
            return opened;
        if (!identities.insert(reader->shards[index]->identity).second)
            return indexed(DataError::FILE_IDENTITY_MISMATCH,
                           "manifest repeats a shard file identity", index, 0,
                           reader->currentGlobal);

        for (u64 local = 0; local < expected.records; ++local)
        {
            if (DataResult stable =
                  reader->verify_shard(index, local, reader->currentGlobal, false);
                !stable)
                return stable;
            AtomicBinV2Record wire{};
            if (DataResult read =
                  read_exact(reader->shards[index]->descriptor, wire.data(), wire.size());
                !read)
                return indexed(read.error, read.message, index, local, reader->currentGlobal);
            if (DataResult stable =
                  reader->verify_shard(index, local, reader->currentGlobal, false);
                !stable)
                return stable;
            AtomicBinV2DecodedRecord decoded;
            if (DataResult valid =
                  reader->decode_record(wire, index, local, reader->currentGlobal, decoded);
                !valid)
                return valid;
            auditedDraws += decoded.sample.result == 0;
            ++reader->currentGlobal;
        }

        // Close the hash-to-audit race with a second same-descriptor digest.
        std::string finalHash;
        if (DataResult hashed =
              sha256_file_descriptor(reader->shards[index]->descriptor, expected.bytes, finalHash);
            !hashed)
            return indexed(hashed.error, hashed.message, index, expected.records,
                           reader->currentGlobal);
        if (finalHash != expected.sha256)
            return indexed(DataError::FILE_IDENTITY_MISMATCH, "shard changed during semantic audit",
                           index, expected.records, reader->currentGlobal);
        if (DataResult stable =
              reader->verify_shard(index, expected.records, reader->currentGlobal, true);
            !stable)
            return stable;
        reader->shards[index]->close();
    }
    if (reader->currentGlobal != reader->metadata.records || auditedDraws != reader->metadata.draws)
        return DataResult::failure(
          DataError::INVALID_MANIFEST,
          "Atomic BIN V2 audited record/draw statistics differ from manifest");
    reader->currentShard = 0;
    reader->currentLocal = reader->currentGlobal = 0;
    output                                       = std::move(reader);
    return DataResult::success();
}

DataResult AtomicBinV2DatasetReader::rewind() {
    if (failed)
        return DataResult::failure(DataError::READ_FAILED,
                                   "Atomic BIN V2 reader cannot rewind after a failed read");
    for (auto& shard : shards)
        shard->close();
    currentShard = 0;
    currentLocal = currentGlobal = 0;

    u64 global = 0;
    for (std::size_t index = 0; index < shards.size(); ++index)
    {
        FileIdentity identity;
        ChangeToken  token;
        const int    check = open_regular(shards[index]->path, identity, token);
        if (check < 0)
        {
            failed = true;
            return indexed(DataError::FILE_IDENTITY_MISMATCH,
                           "shard pathname cannot be re-authenticated", index, 0, global);
        }
        close_descriptor(check);
        if (identity != shards[index]->identity || token != shards[index]->token)
        {
            failed = true;
            return indexed(DataError::FILE_IDENTITY_MISMATCH,
                           "shard pathname was replaced or changed", index, 0, global);
        }
        global += metadata.shards[index].records;
    }
    return DataResult::success();
}

DataResult AtomicBinV2DatasetReader::next(AtomicBinV2DecodedRecord& output, bool& hasRecord) {
    output    = {};
    hasRecord = false;
    if (failed)
        return DataResult::failure(DataError::READ_FAILED,
                                   "Atomic BIN V2 reader cannot continue after a failed read");
    auto poison = [&](DataResult result) {
        failed = true;
        if (currentShard < shards.size())
            shards[currentShard]->close();
        return result;
    };
    if (currentGlobal == metadata.records)
        return DataResult::success();
    if (currentShard >= shards.size())
        return poison(indexed(DataError::RECORD_COUNT_OUT_OF_RANGE,
                              "reader exhausted shards before aggregate record count", currentShard,
                              currentLocal, currentGlobal));
    if (shards[currentShard]->descriptor < 0)
        if (DataResult opened = open_shard(currentShard, false); !opened)
            return poison(std::move(opened));
    if (DataResult stable = verify_shard(currentShard, currentLocal, currentGlobal, false); !stable)
        return poison(std::move(stable));

    AtomicBinV2Record wire{};
    if (DataResult read = read_exact(shards[currentShard]->descriptor, wire.data(), wire.size());
        !read)
        return poison(indexed(read.error, read.message, currentShard, currentLocal, currentGlobal));
    const bool finalRecord = currentLocal + 1 == metadata.shards[currentShard].records;
    if (DataResult stable = verify_shard(currentShard, currentLocal, currentGlobal, finalRecord);
        !stable)
        return poison(std::move(stable));
    if (DataResult decoded = decode_record(wire, currentShard, currentLocal, currentGlobal, output);
        !decoded)
        return poison(std::move(decoded));

    hasRecord = true;
    ++currentLocal;
    ++currentGlobal;
    if (currentLocal == metadata.shards[currentShard].records)
    {
        shards[currentShard]->close();
        ++currentShard;
        currentLocal = 0;
    }
    return DataResult::success();
}

}  // namespace Stockfish::Data
