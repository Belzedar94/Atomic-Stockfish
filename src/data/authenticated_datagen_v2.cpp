/*
  Atomic-Stockfish authenticated teacher/Syzygy datagen contract V2
  Copyright (C) 2026 The Atomic-Stockfish developers

  Atomic-Stockfish is free software: you can redistribute it and/or modify
  it under the terms of the GNU General Public License as published by
  the Free Software Foundation, either version 3 of the License, or
  (at your option) any later version.
*/

#include "authenticated_datagen_v2.h"

#include <algorithm>
#include <array>
#include <cerrno>
#include <cstddef>
#include <cstdint>
#include <filesystem>
#include <limits>
#include <string>
#include <string_view>
#include <system_error>
#include <tuple>
#include <type_traits>
#include <utility>
#include <vector>

#include "atomic_bin_v2.h"
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

constexpr std::array<unsigned char, 8> BundleMagic = {'A', 'T', 'O', 'B', 'N', 'D', 'L', '2'};
constexpr u16   BundleVersion      = 2;
constexpr u16   BundleHeaderBytes  = 384;
constexpr u32   BundleEndianMarker = 0x01020304U;
constexpr u32   BundleEntryCount   = 3;
constexpr u64   BundleAlignment    = 64;
constexpr usize CopyBufferSize     = 1024 * 1024;
constexpr int   MaximumGeneratedPly = 4096;

static_assert(BundleHeaderBytes % BundleAlignment == 0);

DataResult invalid(std::string message) {
    return DataResult::failure(DataError::INVALID_MANIFEST, std::move(message));
}

bool is_lower_hex(std::string_view text, std::size_t width) {
    return text.size() == width && std::all_of(text.begin(), text.end(), [](unsigned char c) {
               return (c >= '0' && c <= '9') || (c >= 'a' && c <= 'f');
           });
}

bool ends_with(std::string_view text, std::string_view suffix) {
    return text.size() >= suffix.size() && text.substr(text.size() - suffix.size()) == suffix;
}

bool valid_utf8(std::string_view text) {
    const auto continuation = [](unsigned char byte) { return byte >= 0x80 && byte <= 0xBF; };
    for (std::size_t index = 0; index < text.size();)
    {
        const unsigned char first = text[index++];
        if (first <= 0x7F)
            continue;
        if (first >= 0xC2 && first <= 0xDF)
        {
            if (index == text.size() || !continuation(text[index++]))
                return false;
            continue;
        }
        if (first >= 0xE0 && first <= 0xEF)
        {
            if (index + 1 >= text.size())
                return false;
            const unsigned char second = text[index++];
            const unsigned char third  = text[index++];
            if (!continuation(third)
                || (first == 0xE0   ? second < 0xA0 || second > 0xBF
                    : first == 0xED ? second < 0x80 || second > 0x9F
                                    : !continuation(second)))
                return false;
            continue;
        }
        if (first >= 0xF0 && first <= 0xF4)
        {
            if (index + 2 >= text.size())
                return false;
            const unsigned char second = text[index++];
            const unsigned char third  = text[index++];
            const unsigned char fourth = text[index++];
            if (!continuation(third) || !continuation(fourth)
                || (first == 0xF0   ? second < 0x90 || second > 0xBF
                    : first == 0xF4 ? second < 0x80 || second > 0x8F
                                    : !continuation(second)))
                return false;
            continue;
        }
        return false;
    }
    return true;
}

std::string basename(const std::filesystem::path& path) {
#ifdef _WIN32
    const std::wstring wide = path.filename().wstring();
    if (wide.empty())
        return {};
    const int needed = ::WideCharToMultiByte(CP_UTF8, WC_ERR_INVALID_CHARS, wide.data(), int(wide.size()),
                                             nullptr, 0, nullptr, nullptr);
    if (needed <= 0)
        return {};
    std::string value(std::size_t(needed), '\0');
    if (::WideCharToMultiByte(CP_UTF8, WC_ERR_INVALID_CHARS, wide.data(), int(wide.size()),
                              value.data(), needed, nullptr, nullptr)
        != needed)
        return {};
    return value;
#else
    return path.filename().string();
#endif
}

bool valid_basename(const std::filesystem::path& path, std::string_view suffix = {}) {
    const std::string name = basename(path);
    constexpr std::string_view Forbidden = "/\\:<>\"|?*";
    return !name.empty() && name != "." && name != ".." && valid_utf8(name)
        && name.find('\0') == std::string::npos
        && name.find_first_of(Forbidden) == std::string::npos
        && (suffix.empty() || (name.size() > suffix.size() && ends_with(name, suffix)));
}

void quote(std::string& output, std::string_view value) {
    constexpr char Hex[] = "0123456789abcdef";
    output.push_back('"');
    for (unsigned char c : value)
        switch (c)
        {
        case '"' : output += "\\\""; break;
        case '\\' : output += "\\\\"; break;
        case '\b' : output += "\\b"; break;
        case '\f' : output += "\\f"; break;
        case '\n' : output += "\\n"; break;
        case '\r' : output += "\\r"; break;
        case '\t' : output += "\\t"; break;
        default :
            if (c < 0x20)
            {
                output += "\\u00";
                output.push_back(Hex[c >> 4]);
                output.push_back(Hex[c & 0x0F]);
            }
            else
                output.push_back(char(c));
        }
    output.push_back('"');
}

void string_field(std::string& output, std::string_view name, std::string_view value,
                  std::string_view separator = ",") {
    quote(output, name);
    output.push_back(':');
    quote(output, value);
    output += separator;
}

void integer_field(std::string& output, std::string_view name, long long value,
                   std::string_view separator = ",") {
    quote(output, name);
    output.push_back(':');
    output += std::to_string(value);
    output += separator;
}

void uint_string_field(std::string& output, std::string_view name, u64 value,
                       std::string_view separator = ",") {
    quote(output, name);
    output += ":\"" + std::to_string(value) + "\"";
    output += separator;
}

void bool_field(std::string& output, std::string_view name, bool value,
                std::string_view separator = ",") {
    quote(output, name);
    output.push_back(':');
    output += value ? "true" : "false";
    output += separator;
}

void append_generation_options(std::string& output, const AtomicBinV2ManifestOptions& options) {
    output.push_back('{');
    integer_field(output, "search_depth_min", options.searchDepthMin);
    integer_field(output, "search_depth_max", options.searchDepthMax);
    uint_string_field(output, "nodes", options.nodes);
    uint_string_field(output, "requested_records", options.requestedRecords);
    uint_string_field(output, "records_per_shard", options.recordsPerShard);
    integer_field(output, "eval_limit", options.evalLimit);
    integer_field(output, "eval_diff_limit", options.evalDiffLimit);
    integer_field(output, "random_move_min_ply", options.randomMoveMinPly);
    integer_field(output, "random_move_max_ply", options.randomMoveMaxPly);
    integer_field(output, "random_move_count", options.randomMoveCount);
    integer_field(output, "random_move_like_apery", options.randomMoveLikeApery);
    integer_field(output, "random_multi_pv", options.randomMultiPv);
    integer_field(output, "random_multi_pv_diff", options.randomMultiPvDiff);
    integer_field(output, "random_multi_pv_depth", options.randomMultiPvDepth);
    integer_field(output, "write_min_ply", options.writeMinPly);
    integer_field(output, "write_max_ply", options.writeMaxPly);
    string_field(output, "keep_draws", options.keepDraws);
    bool_field(output, "adjudicate_draws_by_score", options.adjudicateDrawsByScore);
    bool_field(output, "adjudicate_insufficient", options.adjudicateInsufficient);
    bool_field(output, "filter_captures", options.filterCaptures);
    bool_field(output, "filter_checks", options.filterChecks);
    bool_field(output, "filter_promotions", options.filterPromotions);
    bool_field(output, "random_file_name", options.randomFileName);
    bool_field(output, "set_recommended_uci_options_seen", options.setRecommendedUciOptionsSeen,
               "");
    output.push_back('}');
}

DataResult validate_manifest(const AtomicDatagenV2Manifest& manifest) {
    if (!is_lower_hex(AtomicBinV2TeacherManifestSchemaSha256Hex, 64)
        || !is_lower_hex(AtomicDatagenAttestationSchemaSha256Hex, 64)
        || !is_lower_hex(AtomicOpenBenchBundleV2SchemaSha256Hex, 64))
        return invalid("Authenticated datagen V2 schema identities are not frozen");
    if (!valid_basename(manifest.manifestPath, ".manifest-v2.json"))
        return invalid("Authenticated datagen V2 manifest path is not portable");
    if (!is_lower_hex(manifest.engineCommit, 40) || manifest.engineVersion.empty()
        || !valid_utf8(manifest.engineVersion)
        || (!manifest.producerSha256.empty() && !is_lower_hex(manifest.producerSha256, 64)))
        return invalid("Authenticated datagen V2 engine identity is invalid");
    if (!valid_basename(manifest.networkPath) || !is_lower_hex(manifest.networkSha256, 64))
        return invalid("Authenticated datagen V2 network identity is invalid");
    if (manifest.bookIsFile)
    {
        if (!valid_basename(manifest.bookPath) || !is_lower_hex(manifest.bookSha256, 64))
            return invalid("Authenticated datagen V2 book identity is invalid");
    }
    else if (!manifest.bookPath.empty() || !manifest.bookSha256.empty())
        return invalid("Authenticated datagen V2 built-in book identity is invalid");
    if (manifest.inventorySha256 != AtomicTeacherSyzygyInventorySha256Hex)
        return invalid("Authenticated datagen V2 Syzygy inventory identity is invalid");
    if (manifest.threads == 0 || manifest.hashMb == 0 || manifest.records == 0
        || manifest.draws > manifest.records || manifest.tbHits > manifest.tbProbes)
        return invalid("Authenticated datagen V2 counters are outside their domains");
    if (!((manifest.teacherMode == AtomicPureTeacherMode && manifest.useNnue == "pure")
          || (manifest.teacherMode == AtomicTrueTeacherMode && manifest.useNnue == "true")))
        return invalid("Authenticated datagen V2 teacher mode and Use NNUE do not match");
    if (!valid_basename(manifest.shard.path, ".atbin") || manifest.shard.index != 0
        || manifest.shard.records != manifest.records || manifest.shard.bytes == 0
        || !is_lower_hex(manifest.shard.sha256, 64))
        return invalid("Authenticated datagen V2 shard descriptor is invalid");
    double      keepDrawsEffective = 0.0;
    std::string keepDrawsCanonical;
    if (DataResult keepDraws = normalize_atomic_keep_draws(manifest.options.keepDraws,
                                                           keepDrawsEffective,
                                                           keepDrawsCanonical);
        !keepDraws || keepDrawsCanonical != manifest.options.keepDraws)
        return invalid("Authenticated datagen V2 keep_draws is not canonical");
    const auto& options = manifest.options;
    if (options.searchDepthMin <= 0 || options.searchDepthMax < options.searchDepthMin
        || options.searchDepthMax >= MAX_PLY || options.evalLimit <= 0
        || options.evalLimit > std::numeric_limits<i16>::max() || options.evalDiffLimit < 0
        || options.writeMinPly < 0 || options.writeMaxPly <= options.writeMinPly
        || options.writeMaxPly > MaximumGeneratedPly || options.randomMoveMinPly < -1
        || options.randomMoveMaxPly < 0 || options.randomMoveMaxPly > MaximumGeneratedPly
        || (options.randomMoveMinPly != -1
            && options.randomMoveMaxPly < options.randomMoveMinPly)
        || options.randomMoveCount < 0 || options.randomMoveCount > MaximumGeneratedPly
        || options.randomMoveLikeApery < 0 || options.randomMultiPv < 0
        || options.randomMultiPv > MAX_MOVES || options.randomMultiPvDiff < 0
        || options.randomMultiPvDepth < options.searchDepthMax
        || options.randomMultiPvDepth >= MAX_PLY)
        return invalid("Authenticated datagen V2 generation options are outside producer domains");
    if (options.requestedRecords != manifest.records
        || options.recordsPerShard != manifest.records || options.randomFileName)
        return invalid("Authenticated datagen V2 generator options are not canonical");
    u64 expectedShardBytes = 0;
    if (DataResult size = atomic_bin_v2_file_size(manifest.shard.records, expectedShardBytes);
        !size)
        return size;
    if (manifest.shard.bytes != expectedShardBytes
        || manifest.shard.path.parent_path().lexically_normal()
             != manifest.manifestPath.parent_path().lexically_normal()
        || manifest.manifestPath != atomic_datagen_v2_manifest_path(manifest.shard.path))
        return invalid("Authenticated datagen V2 shard/manifest layout is inconsistent");
    return DataResult::success();
}

class ExclusiveFile {
   public:
    explicit ExclusiveFile(std::filesystem::path path_) : path(std::move(path_)) {}
    ExclusiveFile(const ExclusiveFile&)            = delete;
    ExclusiveFile& operator=(const ExclusiveFile&) = delete;
    ~ExclusiveFile() { abort(); }

    DataResult open() {
#ifdef _WIN32
        handle = ::CreateFileW(path.c_str(), GENERIC_WRITE | DELETE, FILE_SHARE_READ, nullptr,
                               CREATE_NEW, FILE_ATTRIBUTE_NORMAL, nullptr);
        if (handle == INVALID_HANDLE_VALUE)
        {
            const auto error = ::GetLastError();
            return DataResult::failure(
              error == ERROR_FILE_EXISTS || error == ERROR_ALREADY_EXISTS ? DataError::OUTPUT_EXISTS
                                                                          : DataError::OPEN_FAILED,
              "Cannot create authenticated datagen output exclusively: "
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
            return DataResult::failure(errno == EEXIST ? DataError::OUTPUT_EXISTS
                                                       : DataError::OPEN_FAILED,
                                       "Cannot create authenticated datagen output exclusively: "
                                         + std::generic_category().message(errno));
        if (::fstat(descriptor, &identity) != 0)
        {
            const int error = errno;
            ::close(descriptor);
            descriptor = -1;
            return DataResult::failure(DataError::OPEN_FAILED,
                                       "Cannot capture authenticated datagen output identity: "
                                         + std::generic_category().message(error));
        }
#endif
        active = true;
        return DataResult::success();
    }

    DataResult write(const void* bytes, u64 count) {
        const auto* cursor = static_cast<const unsigned char*>(bytes);
        while (count)
        {
            const auto block = std::size_t(std::min<u64>(count, CopyBufferSize));
#ifdef _WIN32
            DWORD written = 0;
            if (!::WriteFile(handle, cursor, DWORD(block), &written, nullptr) || written != block)
                return DataResult::failure(DataError::WRITE_FAILED,
                                           "Cannot write authenticated datagen output: "
                                             + std::system_category().message(
                                               int(::GetLastError())));
#else
            ssize_t written;
            do
                written = ::write(descriptor, cursor, block);
            while (written < 0 && errno == EINTR);
            if (written <= 0)
                return DataResult::failure(DataError::WRITE_FAILED,
                                           "Cannot write authenticated datagen output: "
                                             + std::generic_category().message(errno));
#endif
            cursor += std::size_t(written);
            count -= u64(written);
        }
        return DataResult::success();
    }

    DataResult finish() {
#ifdef _WIN32
        if (!::FlushFileBuffers(handle))
            return DataResult::failure(DataError::WRITE_FAILED,
                                       "Cannot synchronize authenticated datagen output: "
                                         + std::system_category().message(int(::GetLastError())));
        if (!::CloseHandle(handle))
            return DataResult::failure(DataError::CLOSE_FAILED,
                                       "Cannot close authenticated datagen output: "
                                         + std::system_category().message(int(::GetLastError())));
        handle = INVALID_HANDLE_VALUE;
#else
        if (::fsync(descriptor) != 0)
            return DataResult::failure(DataError::WRITE_FAILED,
                                       "Cannot synchronize authenticated datagen output: "
                                         + std::generic_category().message(errno));
        if (::close(descriptor) != 0)
            return DataResult::failure(DataError::CLOSE_FAILED,
                                       "Cannot close authenticated datagen output: "
                                         + std::generic_category().message(errno));
        descriptor = -1;
#endif
        active = false;
        return DataResult::success();
    }

    void abort() noexcept {
        if (!active)
            return;
#ifdef _WIN32
        FILE_DISPOSITION_INFO disposition{TRUE};
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

DataResult write_json_exclusive(const std::filesystem::path& path, std::string_view json) {
    ExclusiveFile output(path);
    if (DataResult opened = output.open(); !opened)
        return opened;
    if (DataResult written = output.write(json.data(), json.size()); !written)
        return written;
    return output.finish();
}

bool decode_sha256(std::string_view hex, unsigned char* output) {
    if (hex.size() != 64)
        return false;
    const auto nibble = [](unsigned char c) -> int {
        if (c >= '0' && c <= '9') return c - '0';
        if (c >= 'a' && c <= 'f') return 10 + c - 'a';
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

template<typename UInt>
void store_little_endian(std::array<unsigned char, BundleHeaderBytes>& header,
                         std::size_t offset, UInt value) {
    static_assert(std::is_unsigned_v<UInt>);
    for (std::size_t i = 0; i < sizeof(UInt); ++i)
        header[offset + i] = static_cast<unsigned char>(value >> (8 * i));
}

DataResult copy_authenticated(ExclusiveFile& output, const std::filesystem::path& source,
                              u64 expectedBytes, std::string_view expectedSha256) {
#ifdef _WIN32
    const HANDLE input = ::CreateFileW(source.c_str(), GENERIC_READ,
                                       FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE,
                                       nullptr, OPEN_EXISTING,
                                       FILE_ATTRIBUTE_NORMAL | FILE_FLAG_SEQUENTIAL_SCAN, nullptr);
    if (input == INVALID_HANDLE_VALUE)
        return DataResult::failure(DataError::OPEN_FAILED,
                                   "Cannot open authenticated bundle source: "
                                     + std::system_category().message(int(::GetLastError())));
#else
    int flags = O_RDONLY;
    #ifdef O_CLOEXEC
    flags |= O_CLOEXEC;
    #endif
    #ifdef O_NOFOLLOW
    flags |= O_NOFOLLOW;
    #endif
    const int input = ::open(source.c_str(), flags);
    if (input == -1)
        return DataResult::failure(DataError::OPEN_FAILED,
                                   "Cannot open authenticated bundle source: "
                                     + std::generic_category().message(errno));
#endif
    Sha256                     digest;
    std::vector<unsigned char> buffer(CopyBufferSize);
    u64                        remaining = expectedBytes;
    while (remaining)
    {
        const auto block = std::size_t(std::min<u64>(remaining, buffer.size()));
#ifdef _WIN32
        DWORD read = 0;
        if (!::ReadFile(input, buffer.data(), DWORD(block), &read, nullptr) || read != block)
        {
            ::CloseHandle(input);
            return DataResult::failure(DataError::READ_FAILED,
                                       "Authenticated bundle source changed while copying");
        }
#else
        ssize_t read;
        do
            read = ::read(input, buffer.data(), block);
        while (read < 0 && errno == EINTR);
        if (read != ssize_t(block))
        {
            ::close(input);
            return DataResult::failure(DataError::READ_FAILED,
                                       "Authenticated bundle source changed while copying");
        }
#endif
        digest.update(buffer.data(), block);
        if (DataResult written = output.write(buffer.data(), block); !written)
        {
#ifdef _WIN32
            ::CloseHandle(input);
#else
            ::close(input);
#endif
            return written;
        }
        remaining -= block;
    }
    unsigned char extra = 0;
#ifdef _WIN32
    DWORD extraRead = 0;
    const bool grew = ::ReadFile(input, &extra, 1, &extraRead, nullptr) && extraRead != 0;
    ::CloseHandle(input);
#else
    ssize_t extraRead;
    do
        extraRead = ::read(input, &extra, 1);
    while (extraRead < 0 && errno == EINTR);
    const bool grew = extraRead > 0;
    ::close(input);
#endif
    if (grew || digest.hex_digest() != expectedSha256)
        return DataResult::failure(DataError::FILE_IDENTITY_MISMATCH,
                                   "Authenticated bundle source identity changed while copying");
    return DataResult::success();
}

DataResult zero_padding(ExclusiveFile& output, u64 bytes) {
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

std::filesystem::path
atomic_datagen_v2_manifest_path(const std::filesystem::path& firstShard) {
    std::filesystem::path path = firstShard;
    path += ".manifest-v2.json";
    return path;
}

std::filesystem::path
atomic_datagen_v2_attestation_path(const std::filesystem::path& bundlePath) {
    std::filesystem::path path = bundlePath;
    path += ".attestation.json";
    return path;
}

DataResult preflight_authenticated_datagen_v2_output(const std::filesystem::path& path) {
    if (path.empty() || path.filename().empty())
        return DataResult::failure(DataError::OPEN_FAILED,
                                   "Authenticated datagen V2 output must name a file");
    std::error_code ec;
    const auto      status = std::filesystem::symlink_status(path, ec);
    if (ec == std::errc::no_such_file_or_directory)
        ec.clear();
    if (ec)
        return DataResult::failure(DataError::OPEN_FAILED,
                                   "Cannot inspect authenticated datagen V2 output: "
                                     + ec.message());
    if (std::filesystem::exists(status) || std::filesystem::is_symlink(status))
        return DataResult::failure(DataError::OUTPUT_EXISTS,
                                   "Authenticated datagen V2 output already exists: "
                                     + path.string());
    return DataResult::success();
}

DataResult render_atomic_datagen_v2_manifest(const AtomicDatagenV2Manifest& manifest,
                                             std::string& json) {
    json.clear();
    if (DataResult valid = validate_manifest(manifest); !valid)
        return valid;

    json += "{\"manifest_version\":2,";
    string_field(json, "manifest_schema_sha256", AtomicBinV2TeacherManifestSchemaSha256Hex);
    string_field(json, "data_schema_sha256", AtomicBinV2SchemaSha256Hex);
    string_field(json, "format", "atomic-bin-v2");
    json += "\"engine\":{";
    string_field(json, "commit", manifest.engineCommit);
    if (!manifest.producerSha256.empty())
    {
        string_field(json, "version", manifest.engineVersion);
        string_field(json, "producer_sha256", manifest.producerSha256, "");
    }
    else
        string_field(json, "version", manifest.engineVersion, "");
    json += "},\"network\":{";
    string_field(json, "file", basename(manifest.networkPath));
    string_field(json, "sha256", manifest.networkSha256, "");
    json += "},\"book\":{";
    if (manifest.bookIsFile)
    {
        string_field(json, "kind", "file");
        string_field(json, "file", basename(manifest.bookPath));
        string_field(json, "sha256", manifest.bookSha256, "");
    }
    else
    {
        string_field(json, "kind", "builtin-startpos");
        json += "\"file\":null,\"sha256\":null";
    }
    json += "},\"generation\":{";
    uint_string_field(json, "resolved_seed", manifest.resolvedSeed);
    bool_field(json, "atomic960", false);
    integer_field(json, "threads", manifest.threads);
    uint_string_field(json, "hash_mb", manifest.hashMb);
    string_field(json, "teacher_mode", manifest.teacherMode);
    string_field(json, "use_nnue", manifest.useNnue);
    json += "\"options\":";
    append_generation_options(json, manifest.options);
    json += ",\"syzygy\":{";
    string_field(json, "inventory_sha256", manifest.inventorySha256);
    integer_field(json, "cardinality", AtomicTeacherSyzygyCardinality);
    integer_field(json, "probe_limit", AtomicTeacherSyzygyProbeLimit);
    integer_field(json, "probe_depth", AtomicTeacherSyzygyProbeDepth);
    bool_field(json, "rule50", true);
    string_field(json, "wdl_suffix", ".atbw");
    string_field(json, "dtz_suffix", ".atbz", "");
    json += "}},\"statistics\":{";
    uint_string_field(json, "records", manifest.records);
    uint_string_field(json, "draws", manifest.draws);
    uint_string_field(json, "tb_probes", manifest.tbProbes);
    uint_string_field(json, "tb_hits", manifest.tbHits, "");
    json += "},\"shards\":[{";
    integer_field(json, "index", 0);
    string_field(json, "file", basename(manifest.shard.path));
    uint_string_field(json, "records", manifest.shard.records);
    uint_string_field(json, "bytes", manifest.shard.bytes);
    string_field(json, "sha256", manifest.shard.sha256, "");
    json += "}]}\n";
    return DataResult::success();
}

DataResult write_atomic_datagen_v2_manifest(const AtomicDatagenV2Manifest& manifest) {
    std::string json;
    if (DataResult rendered = render_atomic_datagen_v2_manifest(manifest, json); !rendered)
        return rendered;
    std::string sha;
    u64         bytes = 0;
    if (DataResult hashed = sha256_file(manifest.shard.path, sha, bytes); !hashed)
        return hashed;
    if (sha != manifest.shard.sha256 || bytes != manifest.shard.bytes)
        return invalid("Authenticated datagen V2 shard changed before manifest publication");
    return write_json_exclusive(manifest.manifestPath, json);
}

DataResult render_atomic_datagen_v2_attestation(const AtomicDatagenV2Attestation& value,
                                                std::string& json) {
    json.clear();
    if (!valid_basename(value.attestationPath, ".attestation.json")
        || !valid_basename(value.manifestPath, ".manifest-v2.json")
        || !valid_basename(value.shardPath, ".atbin") || value.manifestBytes == 0
        || value.shardBytes == 0
        || !is_lower_hex(value.manifestSha256, 64) || !is_lower_hex(value.shardSha256, 64)
        || value.inventorySha256 != AtomicTeacherSyzygyInventorySha256Hex
        || (!value.producerSha256.empty() && !is_lower_hex(value.producerSha256, 64))
        || value.records == 0
        || value.tbHits > value.tbProbes)
        return invalid("Authenticated datagen V2 attestation metadata is invalid");
    if (!((value.teacherMode == AtomicPureTeacherMode && value.useNnue == "pure")
          || (value.teacherMode == AtomicTrueTeacherMode && value.useNnue == "true")))
        return invalid("Authenticated datagen V2 attestation teacher mode is invalid");
    u64 expectedShardBytes = 0;
    if (DataResult size = atomic_bin_v2_file_size(value.records, expectedShardBytes); !size)
        return size;
    if (value.shardBytes != expectedShardBytes)
        return invalid("Authenticated datagen V2 attestation shard size is inconsistent");

    json += "{\"attestation_version\":1,";
    string_field(json, "attestation_schema_sha256", AtomicDatagenAttestationSchemaSha256Hex);
    string_field(json, "contract", "atomic-openbench-authenticated-teacher-syzygy-v2");
    if (!value.producerSha256.empty())
        string_field(json, "producer_sha256", value.producerSha256);
    const auto artifact = [&](std::string_view name, const std::filesystem::path& path, u64 bytes,
                              std::string_view sha, std::string_view schema, bool comma) {
        quote(json, name);
        json += ":{";
        string_field(json, "file", basename(path));
        uint_string_field(json, "bytes", bytes);
        string_field(json, "sha256", sha);
        string_field(json, "schema_sha256", schema, "");
        json += comma ? "}," : "}";
    };
    artifact("manifest", value.manifestPath, value.manifestBytes, value.manifestSha256,
             AtomicBinV2TeacherManifestSchemaSha256Hex, true);
    artifact("shard", value.shardPath, value.shardBytes, value.shardSha256,
             AtomicBinV2SchemaSha256Hex, true);
    json += "\"syzygy_inventory\":{";
    string_field(json, "sha256", value.inventorySha256);
    integer_field(json, "cardinality", AtomicTeacherSyzygyCardinality, "");
    json += "},\"teacher\":{";
    string_field(json, "mode", value.teacherMode);
    string_field(json, "use_nnue", value.useNnue);
    integer_field(json, "syzygy_probe_limit", AtomicTeacherSyzygyProbeLimit);
    integer_field(json, "syzygy_probe_depth", AtomicTeacherSyzygyProbeDepth);
    bool_field(json, "syzygy_50_move_rule", true, "");
    json += "},\"counters\":{";
    uint_string_field(json, "tb_probes", value.tbProbes);
    uint_string_field(json, "tb_hits", value.tbHits, "");
    json += "}}\n";
    return DataResult::success();
}

DataResult write_atomic_datagen_v2_attestation(const AtomicDatagenV2Attestation& value) {
    std::string json;
    if (DataResult rendered = render_atomic_datagen_v2_attestation(value, json); !rendered)
        return rendered;
    for (const auto& source : std::array{
           std::tuple{value.manifestPath, value.manifestBytes, std::string_view(value.manifestSha256)},
           std::tuple{value.shardPath, value.shardBytes, std::string_view(value.shardSha256)}})
    {
        std::string sha;
        u64         bytes = 0;
        if (DataResult hashed = sha256_file(std::get<0>(source), sha, bytes); !hashed)
            return hashed;
        if (bytes != std::get<1>(source) || sha != std::get<2>(source))
            return invalid("Authenticated datagen V2 source changed before attestation");
    }
    return write_json_exclusive(value.attestationPath, json);
}

DataResult write_openbench_datagen_bundle_v2(const std::filesystem::path& outputPath,
                                             const AtomicDatagenV2Attestation& value) {
    std::string attestationSha;
    u64         attestationBytes = 0;
    if (DataResult hashed = sha256_file(value.attestationPath, attestationSha, attestationBytes);
        !hashed)
        return hashed;
    std::string actualSha;
    u64         actualBytes = 0;
    if (DataResult hashed = sha256_file(value.manifestPath, actualSha, actualBytes); !hashed)
        return hashed;
    if (actualSha != value.manifestSha256 || actualBytes != value.manifestBytes)
        return invalid("Authenticated datagen V2 manifest changed before bundling");
    if (DataResult hashed = sha256_file(value.shardPath, actualSha, actualBytes); !hashed)
        return hashed;
    if (actualSha != value.shardSha256 || actualBytes != value.shardBytes)
        return invalid("Authenticated datagen V2 shard changed before bundling");
    if (value.shardBytes < AtomicBinV2HeaderSize
        || (value.shardBytes - AtomicBinV2HeaderSize) % AtomicBinV2RecordSize != 0
        || (value.shardBytes - AtomicBinV2HeaderSize) / AtomicBinV2RecordSize
             != value.records)
        return invalid("Authenticated datagen V2 shard size does not match its record count");

    const u64 manifestOffset = BundleHeaderBytes;
    const u64 attestationOffset =
      (manifestOffset + value.manifestBytes + BundleAlignment - 1) & ~(BundleAlignment - 1);
    const u64 payloadOffset =
      (attestationOffset + attestationBytes + BundleAlignment - 1) & ~(BundleAlignment - 1);
    if (payloadOffset < attestationOffset || payloadOffset > std::numeric_limits<u64>::max()
                                                       - value.shardBytes)
        return DataResult::failure(DataError::RECORD_COUNT_OUT_OF_RANGE,
                                   "Authenticated datagen V2 bundle extent overflows u64");

    std::array<unsigned char, BundleHeaderBytes> header{};
    std::copy(BundleMagic.begin(), BundleMagic.end(), header.begin());
    store_little_endian(header, 8, BundleVersion);
    store_little_endian(header, 10, BundleHeaderBytes);
    store_little_endian(header, 12, BundleEndianMarker);
    store_little_endian(header, 16, u32(0));
    store_little_endian(header, 20, BundleEntryCount);
    if (!decode_sha256(AtomicOpenBenchBundleV2SchemaSha256Hex, header.data() + 24)
        || !decode_sha256(AtomicBinV2SchemaSha256Hex, header.data() + 56)
        || !decode_sha256(AtomicBinV2TeacherManifestSchemaSha256Hex, header.data() + 88)
        || !decode_sha256(AtomicDatagenAttestationSchemaSha256Hex, header.data() + 120)
        || !decode_sha256(value.manifestSha256, header.data() + 168)
        || !decode_sha256(attestationSha, header.data() + 216)
        || !decode_sha256(value.shardSha256, header.data() + 264)
        || !decode_sha256(value.inventorySha256, header.data() + 320))
        return DataResult::failure(DataError::SCHEMA_MISMATCH,
                                   "Authenticated datagen V2 bundle has an invalid SHA identity");
    store_little_endian(header, 152, manifestOffset);
    store_little_endian(header, 160, value.manifestBytes);
    store_little_endian(header, 200, attestationOffset);
    store_little_endian(header, 208, attestationBytes);
    store_little_endian(header, 248, payloadOffset);
    store_little_endian(header, 256, value.shardBytes);
    store_little_endian(header, 296, value.records);
    store_little_endian(header, 304, value.tbProbes);
    store_little_endian(header, 312, value.tbHits);

    ExclusiveFile output(outputPath);
    if (DataResult opened = output.open(); !opened)
        return opened;
    if (DataResult written = output.write(header.data(), header.size()); !written)
        return written;
    if (DataResult copied = copy_authenticated(output, value.manifestPath, value.manifestBytes,
                                               value.manifestSha256);
        !copied)
        return copied;
    if (DataResult padded = zero_padding(
          output, attestationOffset - manifestOffset - value.manifestBytes);
        !padded)
        return padded;
    if (DataResult copied = copy_authenticated(output, value.attestationPath, attestationBytes,
                                               attestationSha);
        !copied)
        return copied;
    if (DataResult padded = zero_padding(
          output, payloadOffset - attestationOffset - attestationBytes);
        !padded)
        return padded;
    if (DataResult copied = copy_authenticated(output, value.shardPath, value.shardBytes,
                                               value.shardSha256);
        !copied)
        return copied;
    return output.finish();
}

}  // namespace Stockfish::Data
