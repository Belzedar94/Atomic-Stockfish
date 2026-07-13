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
#include <array>
#include <cerrno>
#include <cstdlib>
#include <cstdint>
#include <cstring>
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
    if (offset > u64(std::numeric_limits<__int64>::max()))
        return false;
    const auto target = static_cast<__int64>(offset);
    return ::_lseeki64(descriptor, target, SEEK_SET) == target;
}

int read_descriptor(int descriptor, void* bytes, std::size_t size) {
    return ::_read(descriptor, bytes, unsigned(std::min<std::size_t>(size, unsigned(-1) >> 1)));
}

int write_descriptor(int descriptor, const void* bytes, std::size_t size) {
    return ::_write(descriptor, bytes, unsigned(std::min<std::size_t>(size, unsigned(-1) >> 1)));
}

using BCryptGenRandomFunction = LONG(WINAPI*)(void*, unsigned char*, ULONG, ULONG);

struct SystemRandomProvider {
    HMODULE                 module    = nullptr;
    BCryptGenRandomFunction generate  = nullptr;
    DWORD                   loadError = ERROR_SUCCESS;

    SystemRandomProvider() {
        module = ::LoadLibraryW(L"bcrypt.dll");
        if (!module)
        {
            loadError = ::GetLastError();
            return;
        }
        const FARPROC address = ::GetProcAddress(module, "BCryptGenRandom");
        if (!address)
        {
            loadError = ::GetLastError();
            return;
        }
        static_assert(sizeof(generate) == sizeof(address));
        std::memcpy(&generate, &address, sizeof(generate));
    }

    ~SystemRandomProvider() {
        if (module)
            ::FreeLibrary(module);
    }

    SystemRandomProvider(const SystemRandomProvider&)            = delete;
    SystemRandomProvider& operator=(const SystemRandomProvider&) = delete;
};

const SystemRandomProvider& system_random_provider() {
    static const SystemRandomProvider provider;
    return provider;
}

DataResult create_private_snapshot(int& descriptor) {
    descriptor = -1;
    std::error_code       pathError;
    std::filesystem::path directory = std::filesystem::temp_directory_path(pathError);
    if (pathError || directory.empty())
        return DataResult::failure(DataError::OPEN_FAILED,
                                   "Cannot locate the system temporary directory: "
                                     + pathError.message());

    const auto& provider = system_random_provider();
    if (!provider.generate)
        return DataResult::failure(
          DataError::OPEN_FAILED,
          "Cannot load the Windows system random provider: "
            + std::system_category().message(static_cast<int>(provider.loadError)));

    constexpr ULONG   UseSystemPreferredRng = 0x00000002UL;
    constexpr wchar_t Hex[]                 = L"0123456789abcdef";
    for (unsigned attempt = 0; attempt < 128; ++attempt)
    {
        std::array<unsigned char, 32> random{};
        const LONG                    status = provider.generate(
          nullptr, random.data(), static_cast<ULONG>(random.size()), UseSystemPreferredRng);
        if (status < 0)
            return DataResult::failure(DataError::OPEN_FAILED,
                                       "Windows system random generation failed while creating a "
                                       "private Atomic BIN V2 snapshot");

        std::wstring name = L"atomic-bin-v2-reader-";
        name.reserve(name.size() + random.size() * 2 + 4);
        for (const unsigned char byte : random)
        {
            name.push_back(Hex[byte >> 4]);
            name.push_back(Hex[byte & 0x0F]);
        }
        name += L".tmp";
        const std::filesystem::path path   = directory / name;
        const HANDLE                handle = ::CreateFileW(
          path.c_str(), GENERIC_READ | GENERIC_WRITE | DELETE, 0, nullptr, CREATE_NEW,
          FILE_ATTRIBUTE_TEMPORARY | FILE_FLAG_DELETE_ON_CLOSE | FILE_FLAG_SEQUENTIAL_SCAN,
          nullptr);
        if (handle == INVALID_HANDLE_VALUE)
        {
            const DWORD error = ::GetLastError();
            if (error == ERROR_FILE_EXISTS || error == ERROR_ALREADY_EXISTS)
                continue;
            return DataResult::failure(
              DataError::OPEN_FAILED,
              "Cannot create a private auto-deleting Atomic BIN V2 snapshot: "
                + std::system_category().message(static_cast<int>(error)));
        }

        descriptor = ::_open_osfhandle(reinterpret_cast<std::intptr_t>(handle),
                                       _O_RDWR | _O_BINARY | _O_NOINHERIT);
        if (descriptor < 0)
        {
            const int error = errno;
            ::CloseHandle(handle);
            return DataResult::failure(
              DataError::OPEN_FAILED,
              "Cannot attach a descriptor to the private Atomic BIN V2 snapshot: "
                + system_message(error));
        }
        return DataResult::success();
    }

    return DataResult::failure(DataError::OPEN_FAILED,
                               "Cannot allocate a unique private Atomic BIN V2 snapshot name");
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

ssize_t write_descriptor(int descriptor, const void* bytes, std::size_t size) {
    return ::write(descriptor, bytes, size);
}

DataResult create_private_snapshot(int& descriptor) {
    descriptor = -1;
    std::error_code       pathError;
    std::filesystem::path directory = std::filesystem::temp_directory_path(pathError);
    if (pathError || directory.empty())
        return DataResult::failure(DataError::OPEN_FAILED,
                                   "Cannot locate the system temporary directory: "
                                     + pathError.message());
    std::string pattern = (directory / "atomic-bin-v2-reader-XXXXXX").native();
    descriptor          = ::mkstemp(pattern.data());
    if (descriptor < 0)
        return DataResult::failure(DataError::OPEN_FAILED,
                                   "Cannot create a private Atomic BIN V2 snapshot: "
                                     + system_message(errno));

    const int unlinkResult = ::unlink(pattern.data());
    if (unlinkResult != 0)
    {
        const int error = errno;
        close_descriptor(descriptor);
        descriptor = -1;
        return DataResult::failure(DataError::OPEN_FAILED,
                                   "Cannot unlink the private Atomic BIN V2 snapshot: "
                                     + system_message(error));
    }
    const int flags = ::fcntl(descriptor, F_GETFD);
    if (flags < 0 || ::fcntl(descriptor, F_SETFD, flags | FD_CLOEXEC) != 0)
    {
        const int error = errno;
        close_descriptor(descriptor);
        descriptor = -1;
        return DataResult::failure(
          DataError::OPEN_FAILED, "Cannot make the private Atomic BIN V2 snapshot non-inheritable: "
                                    + system_message(error));
    }
    return DataResult::success();
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

DataResult write_exact(int descriptor, const void* input, std::size_t size) {
    const auto* bytes  = static_cast<const u8*>(input);
    std::size_t offset = 0;
    while (offset < size)
    {
        errno            = 0;
        const auto count = write_descriptor(descriptor, bytes + offset, size - offset);
        if (count > 0)
        {
            offset += std::size_t(count);
            continue;
        }
        if (count < 0 && errno == EINTR)
            continue;
        return DataResult::failure(DataError::WRITE_FAILED,
                                   "private shard snapshot write failed: " + system_message(errno));
    }
    return DataResult::success();
}

}  // namespace

struct AtomicBinV2DatasetReader::Shard {
    std::filesystem::path path;
    int                   descriptor = -1;
    FileIdentity          sourceIdentity;
    FileIdentity          snapshotIdentity;
    ChangeToken           snapshotToken;

    ~Shard() { close(); }

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

    FileIdentity sourceIdentity;
    ChangeToken  sourceToken;
    errno                      = 0;
    const int sourceDescriptor = open_regular(shard.path, sourceIdentity, sourceToken);
    const u64 global           = currentGlobal;
    if (sourceDescriptor < 0)
        return indexed(DataError::OPEN_FAILED,
                       "cannot open regular non-link shard: " + system_message(errno), index, 0,
                       global);
    if (sourceToken.size != expected.bytes)
    {
        close_descriptor(sourceDescriptor);
        return indexed(DataError::FILE_SIZE_MISMATCH, "shard size differs from manifest", index, 0,
                       global);
    }
    if (sourceToken.size > Sha256MaxByteCount)
    {
        close_descriptor(sourceDescriptor);
        return indexed(DataError::FILE_SIZE_MISMATCH,
                       "shard exceeds the SHA-256 message-length domain", index, 0, global);
    }
    if (!establishIdentity && sourceIdentity != shard.sourceIdentity)
    {
        close_descriptor(sourceDescriptor);
        return indexed(DataError::FILE_IDENTITY_MISMATCH,
                       "shard pathname was replaced after authentication", index, 0, global);
    }

    int snapshotDescriptor = -1;
    if (DataResult created = create_private_snapshot(snapshotDescriptor); !created)
    {
        close_descriptor(sourceDescriptor);
        return indexed(created.error, created.message, index, 0, global);
    }

    Sha256                hash;
    std::array<u8, 65536> chunk{};
    u64                   remaining = sourceToken.size;
    while (remaining)
    {
        const std::size_t count = std::size_t(std::min<u64>(remaining, chunk.size()));
        if (DataResult read = read_exact(sourceDescriptor, chunk.data(), count); !read)
        {
            close_descriptor(snapshotDescriptor);
            close_descriptor(sourceDescriptor);
            return indexed(read.error, read.message, index, 0, global);
        }
        if (DataResult written = write_exact(snapshotDescriptor, chunk.data(), count); !written)
        {
            close_descriptor(snapshotDescriptor);
            close_descriptor(sourceDescriptor);
            return indexed(written.error, written.message, index, 0, global);
        }
        hash.update(chunk.data(), count);
        remaining -= count;
    }

    FileIdentity afterIdentity;
    ChangeToken  afterToken;
    if (!inspect_descriptor(sourceDescriptor, afterIdentity, afterToken)
        || afterIdentity != sourceIdentity || afterToken != sourceToken)
    {
        close_descriptor(snapshotDescriptor);
        close_descriptor(sourceDescriptor);
        return indexed(DataError::FILE_IDENTITY_MISMATCH, "shard changed while being authenticated",
                       index, 0, global);
    }

    FileIdentity pathIdentity;
    ChangeToken  pathToken;
    const int    pathDescriptor = open_regular(shard.path, pathIdentity, pathToken);
    if (pathDescriptor < 0 || pathIdentity != sourceIdentity || pathToken != sourceToken)
    {
        if (pathDescriptor >= 0)
            close_descriptor(pathDescriptor);
        close_descriptor(snapshotDescriptor);
        close_descriptor(sourceDescriptor);
        return indexed(DataError::FILE_IDENTITY_MISMATCH,
                       "shard pathname changed while being staged", index, 0, global);
    }
    close_descriptor(pathDescriptor);
    close_descriptor(sourceDescriptor);

    if (hash.hex_digest() != expected.sha256)
    {
        close_descriptor(snapshotDescriptor);
        return indexed(DataError::SCHEMA_MISMATCH, "shard SHA-256 differs from manifest", index, 0,
                       global);
    }

    FileIdentity snapshotIdentity;
    ChangeToken  snapshotToken;
    if (!inspect_descriptor(snapshotDescriptor, snapshotIdentity, snapshotToken)
        || snapshotToken.size != expected.bytes)
    {
        close_descriptor(snapshotDescriptor);
        return indexed(DataError::FILE_IDENTITY_MISMATCH,
                       "private shard snapshot size or identity is invalid", index, 0, global);
    }
    std::string snapshotHash;
    if (DataResult hashed =
          sha256_file_descriptor(snapshotDescriptor, expected.bytes, snapshotHash);
        !hashed)
    {
        close_descriptor(snapshotDescriptor);
        return indexed(hashed.error, hashed.message, index, 0, global);
    }
    FileIdentity authenticatedIdentity;
    ChangeToken  authenticatedToken;
    if (snapshotHash != expected.sha256
        || !inspect_descriptor(snapshotDescriptor, authenticatedIdentity, authenticatedToken)
        || authenticatedIdentity != snapshotIdentity || authenticatedToken != snapshotToken)
    {
        close_descriptor(snapshotDescriptor);
        return indexed(DataError::FILE_IDENTITY_MISMATCH,
                       "private shard snapshot failed full SHA-256 authentication", index, 0,
                       global);
    }

    shard.descriptor       = snapshotDescriptor;
    shard.snapshotIdentity = snapshotIdentity;
    shard.snapshotToken    = snapshotToken;
    if (establishIdentity)
        shard.sourceIdentity = sourceIdentity;

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
    return verify_shard(index, 0, global);
}

DataResult AtomicBinV2DatasetReader::verify_shard(std::size_t index, u64 local, u64 global) {
    auto&        shard = *shards[index];
    FileIdentity identity;
    ChangeToken  token;
    if (shard.descriptor < 0 || !inspect_descriptor(shard.descriptor, identity, token)
        || identity != shard.snapshotIdentity || token != shard.snapshotToken)
        return indexed(DataError::FILE_IDENTITY_MISMATCH,
                       "private authenticated shard snapshot changed", index, local, global);
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
        if (!identities.insert(reader->shards[index]->sourceIdentity).second)
            return indexed(DataError::FILE_IDENTITY_MISMATCH,
                           "manifest repeats a shard file identity", index, 0,
                           reader->currentGlobal);

        for (u64 local = 0; local < expected.records; ++local)
        {
            if (DataResult stable = reader->verify_shard(index, local, reader->currentGlobal);
                !stable)
                return stable;
            AtomicBinV2Record wire{};
            if (DataResult read =
                  read_exact(reader->shards[index]->descriptor, wire.data(), wire.size());
                !read)
                return indexed(read.error, read.message, index, local, reader->currentGlobal);
            if (DataResult stable = reader->verify_shard(index, local, reader->currentGlobal);
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
              reader->verify_shard(index, expected.records, reader->currentGlobal);
            !stable)
            return stable;
        reader->shards[index]->close();
    }
    if (reader->currentGlobal != reader->metadata.records || auditedDraws != reader->metadata.draws)
    {
        const std::size_t last  = reader->metadata.shards.size() - 1;
        const u64         local = reader->metadata.shards[last].records;
        return indexed(DataError::INVALID_MANIFEST,
                       "EOF audited record/draw totals differ from manifest", last, local,
                       reader->currentGlobal);
    }
    reader->currentShard = 0;
    reader->currentLocal = reader->currentGlobal = reader->currentDraws = 0;
    output                                                              = std::move(reader);
    return DataResult::success();
}

DataResult AtomicBinV2DatasetReader::rewind() {
    if (failed)
        return DataResult::failure(DataError::READ_FAILED,
                                   "Atomic BIN V2 reader cannot rewind after a failed read");
    for (auto& shard : shards)
        shard->close();
    currentShard = 0;
    currentLocal = currentGlobal = currentDraws = 0;

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
        if (identity != shards[index]->sourceIdentity)
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
    {
        if (currentDraws != metadata.draws)
        {
            const std::size_t last  = metadata.shards.size() - 1;
            const u64         local = metadata.shards[last].records;
            return poison(indexed(DataError::INVALID_MANIFEST,
                                  "EOF streamed record/draw totals differ from manifest", last,
                                  local, currentGlobal));
        }
        return DataResult::success();
    }
    if (currentShard >= shards.size())
        return poison(indexed(DataError::RECORD_COUNT_OUT_OF_RANGE,
                              "reader exhausted shards before aggregate record count", currentShard,
                              currentLocal, currentGlobal));
    if (shards[currentShard]->descriptor < 0)
        if (DataResult opened = open_shard(currentShard, false); !opened)
            return poison(std::move(opened));
    if (DataResult stable = verify_shard(currentShard, currentLocal, currentGlobal); !stable)
        return poison(std::move(stable));

    AtomicBinV2Record wire{};
    if (DataResult read = read_exact(shards[currentShard]->descriptor, wire.data(), wire.size());
        !read)
        return poison(indexed(read.error, read.message, currentShard, currentLocal, currentGlobal));
    if (DataResult stable = verify_shard(currentShard, currentLocal, currentGlobal); !stable)
        return poison(std::move(stable));
    if (DataResult decoded = decode_record(wire, currentShard, currentLocal, currentGlobal, output);
        !decoded)
        return poison(std::move(decoded));

    hasRecord = true;
    currentDraws += output.sample.result == 0;
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
