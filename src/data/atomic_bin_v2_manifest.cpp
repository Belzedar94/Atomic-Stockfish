/*
  Atomic-Stockfish, a UCI chess variant playing engine derived from Stockfish
  Copyright (C) 2004-2026 The Stockfish developers (see AUTHORS file)

  Atomic-Stockfish is free software: you can redistribute it and/or modify
  it under the terms of the GNU General Public License as published by
  the Free Software Foundation, either version 3 of the License, or
  (at your option) any later version.
*/

#if defined(__linux__) && !defined(_GNU_SOURCE)
    #define _GNU_SOURCE
#endif

#include "atomic_bin_v2_manifest.h"

#include <algorithm>
#include <atomic>
#include <cerrno>
#include <cstddef>
#include <cstring>
#include <limits>
#include <optional>
#include <string>
#include <system_error>
#include <utility>

#include "atomic_bin_v2_wire.h"
#include "misc.h"
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
    #include <sys/stat.h>
#else
    #include <fcntl.h>
    #include <sys/stat.h>
    #include <unistd.h>
#endif

namespace Stockfish::Data {
namespace {

constexpr std::string_view DataSchemaSha256      = AtomicBinV2SchemaSha256Hex;
constexpr u64              MaximumManifestShards = 100000;
constexpr int              MaximumGeneratedPly   = 4096;

DataResult invalid_manifest(std::string message) {
    return DataResult::failure(DataError::INVALID_MANIFEST, std::move(message));
}

#ifndef _WIN32
std::string system_error_message(int error) { return std::generic_category().message(error); }
#endif

#ifdef _WIN32
std::string windows_error_message(unsigned long error) {
    return std::system_category().message(int(error));
}
#endif

bool ends_with(std::string_view text, std::string_view suffix) {
    return text.size() >= suffix.size() && text.substr(text.size() - suffix.size()) == suffix;
}

bool is_lower_hex(std::string_view text, std::size_t size) {
    return text.size() == size && std::all_of(text.begin(), text.end(), [](unsigned char value) {
               return (value >= '0' && value <= '9') || (value >= 'a' && value <= 'f');
           });
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

bool valid_decimal(std::string_view text) {
    if (text == "0" || text == "1")
        return true;
    return text.size() >= 3 && text[0] == '0' && text[1] == '.' && text.back() >= '1'
        && text.back() <= '9' && std::all_of(text.begin() + 2, text.end(), [](unsigned char value) {
               return value >= '0' && value <= '9';
           });
}

std::string path_filename_utf8(const std::filesystem::path& path) {
#ifdef _WIN32
    const std::wstring filename = path.filename().native();
    if (filename.empty() || filename.size() > std::size_t(std::numeric_limits<int>::max()))
        return {};

    const int inputSize  = int(filename.size());
    const int outputSize = ::WideCharToMultiByte(CP_UTF8, WC_ERR_INVALID_CHARS, filename.data(),
                                                 inputSize, nullptr, 0, nullptr, nullptr);
    if (outputSize <= 0)
        return {};

    std::string output(std::size_t(outputSize), '\0');
    if (::WideCharToMultiByte(CP_UTF8, WC_ERR_INVALID_CHARS, filename.data(), inputSize,
                              output.data(), outputSize, nullptr, nullptr)
        != outputSize)
        return {};
    return output;
#else
    return path.filename().string();
#endif
}

bool valid_filename(const std::filesystem::path& path, std::string_view suffix = {}) {
    const std::string          filename  = path_filename_utf8(path);
    constexpr std::string_view Forbidden = "/\\:<>\"|?*";
    return !filename.empty() && valid_utf8(filename) && filename != "." && filename != ".."
        && filename.find('\0') == std::string::npos
        && filename.find_first_of(Forbidden) == std::string::npos
        && (suffix.empty() || (filename.size() > suffix.size() && ends_with(filename, suffix)));
}

void append_quoted(std::string& output, std::string_view value) {
    constexpr char Hex[] = "0123456789abcdef";
    output.push_back('"');
    for (unsigned char byte : value)
    {
        switch (byte)
        {
        case '"' :
            output += "\\\"";
            break;
        case '\\' :
            output += "\\\\";
            break;
        case '\b' :
            output += "\\b";
            break;
        case '\f' :
            output += "\\f";
            break;
        case '\n' :
            output += "\\n";
            break;
        case '\r' :
            output += "\\r";
            break;
        case '\t' :
            output += "\\t";
            break;
        default :
            if (byte < 0x20)
            {
                output += "\\u00";
                output.push_back(Hex[byte >> 4]);
                output.push_back(Hex[byte & 0x0F]);
            }
            else
                output.push_back(char(byte));
        }
    }
    output.push_back('"');
}

void append_string_field(std::string&     output,
                         std::string_view name,
                         std::string_view value,
                         std::string_view separator = ",") {
    append_quoted(output, name);
    output.push_back(':');
    append_quoted(output, value);
    output += separator;
}

void append_integer_field(std::string&     output,
                          std::string_view name,
                          long long        value,
                          std::string_view separator = ",") {
    append_quoted(output, name);
    output.push_back(':');
    output += std::to_string(value);
    output += separator;
}

void append_bool_field(std::string&     output,
                       std::string_view name,
                       bool             value,
                       std::string_view separator = ",") {
    append_quoted(output, name);
    output.push_back(':');
    output += value ? "true" : "false";
    output += separator;
}

DataResult validate_manifest(const AtomicBinV2Manifest& manifest) {
    if (!is_lower_hex(AtomicBinV2ManifestSchemaSha256Hex, 64))
        return invalid_manifest("Atomic BIN V2 manifest schema SHA-256 is not frozen");
    if (!valid_filename(manifest.manifestPath, ".manifest.json"))
        return invalid_manifest("Atomic BIN V2 manifest path must end in .manifest.json");
    if (manifest.engineCommit != "unknown" && !is_lower_hex(manifest.engineCommit, 40))
        return invalid_manifest("Atomic BIN V2 engine commit must be lowercase SHA-1 or unknown");
    if (manifest.engineVersion.empty() || !valid_utf8(manifest.engineVersion))
        return invalid_manifest("Atomic BIN V2 engine version must be nonempty UTF-8");
    if (!valid_filename(manifest.networkPath) || !is_lower_hex(manifest.networkSha256, 64))
        return invalid_manifest("Atomic BIN V2 network metadata is invalid");
    if (manifest.bookIsFile)
    {
        if (!valid_filename(manifest.bookPath) || !is_lower_hex(manifest.bookSha256, 64))
            return invalid_manifest("Atomic BIN V2 book file metadata is invalid");
    }
    else if (!manifest.bookPath.empty() || !manifest.bookSha256.empty())
        return invalid_manifest("Atomic BIN V2 built-in book metadata must use null fields");
    if (manifest.threads == 0 || manifest.hashMb == 0)
        return invalid_manifest("Atomic BIN V2 UCI options are outside their domain");
    if (!valid_decimal(manifest.options.keepDraws))
        return invalid_manifest("Atomic BIN V2 keep_draws is not a canonical decimal string");
    const auto& options = manifest.options;
    if (options.searchDepthMin <= 0 || options.searchDepthMax < options.searchDepthMin
        || options.searchDepthMax >= MAX_PLY || options.evalLimit <= 0
        || options.evalLimit > std::numeric_limits<i16>::max() || options.evalDiffLimit < 0
        || options.writeMinPly < 0 || options.writeMaxPly <= options.writeMinPly
        || options.writeMaxPly > MaximumGeneratedPly || options.randomMoveMinPly < -1
        || options.randomMoveMaxPly < 0 || options.randomMoveMaxPly > MaximumGeneratedPly
        || (options.randomMoveMinPly != -1 && options.randomMoveMaxPly < options.randomMoveMinPly)
        || options.randomMoveCount < 0 || options.randomMoveCount > MaximumGeneratedPly
        || options.randomMoveLikeApery < 0 || options.randomMultiPv < 0
        || options.randomMultiPv > MAX_MOVES || options.randomMultiPvDiff < 0
        || options.randomMultiPvDepth < options.searchDepthMax
        || options.randomMultiPvDepth >= MAX_PLY)
        return invalid_manifest("Atomic BIN V2 generation options are outside producer domains");
    if (manifest.records == 0 || manifest.draws > manifest.records || manifest.shards.empty())
        return invalid_manifest("Atomic BIN V2 manifest statistics are inconsistent");
    if (manifest.options.requestedRecords != manifest.records
        || manifest.options.recordsPerShard == 0)
        return invalid_manifest("Atomic BIN V2 requested record metadata is inconsistent");

    if (manifest.shards.size() > MaximumManifestShards)
        return invalid_manifest("Atomic BIN V2 manifest exceeds the producer shard limit");

    u64 summedRecords = 0;
    for (std::size_t index = 0; index < manifest.shards.size(); ++index)
    {
        const auto& shard = manifest.shards[index];
        if (shard.index != index || shard.index > std::numeric_limits<u32>::max()
            || shard.records == 0 || !valid_filename(shard.path, ".atbin")
            || !is_lower_hex(shard.sha256, 64))
            return invalid_manifest("Atomic BIN V2 shard metadata is invalid");
        if (shard.path.parent_path().lexically_normal()
            != manifest.manifestPath.parent_path().lexically_normal())
            return invalid_manifest("Atomic BIN V2 shard and manifest directories differ");
        u64 expectedBytes = 0;
        if (DataResult size = atomic_bin_v2_file_size(shard.records, expectedBytes); !size)
            return size;
        if (shard.bytes != expectedBytes)
            return invalid_manifest("Atomic BIN V2 shard byte size is inconsistent");
        if (summedRecords > std::numeric_limits<u64>::max() - shard.records)
            return invalid_manifest("Atomic BIN V2 shard record sum overflows");
        summedRecords += shard.records;
        if (index + 1 < manifest.shards.size() && shard.records != manifest.options.recordsPerShard)
            return invalid_manifest("Atomic BIN V2 non-final shard is not full");
        if (index + 1 == manifest.shards.size() && shard.records > manifest.options.recordsPerShard)
            return invalid_manifest("Atomic BIN V2 final shard exceeds its configured size");
    }
    if (summedRecords != manifest.records)
        return invalid_manifest("Atomic BIN V2 shard records do not match statistics");
    if (manifest.manifestPath != atomic_bin_v2_manifest_path(manifest.shards.front().path))
        return invalid_manifest("Atomic BIN V2 sidecar path does not match its first shard");
    return DataResult::success();
}

#ifdef _WIN32
DataResult write_all(HANDLE handle, const std::string& bytes) {
    std::size_t written = 0;
    while (written < bytes.size())
    {
        const DWORD chunk =
          DWORD(std::min<std::size_t>(bytes.size() - written, std::numeric_limits<DWORD>::max()));
        DWORD count = 0;
        if (!::WriteFile(handle, bytes.data() + written, chunk, &count, nullptr) || count == 0)
        {
            const unsigned long error = count == 0 ? ERROR_WRITE_FAULT : ::GetLastError();
            return DataResult::failure(DataError::WRITE_FAILED,
                                       "Cannot write Atomic BIN V2 manifest: "
                                         + windows_error_message(error));
        }
        written += count;
    }
    return DataResult::success();
}

void remove_created_handle(HANDLE handle) noexcept {
    FILE_DISPOSITION_INFO disposition{TRUE};
    ::SetFileInformationByHandle(handle, FileDispositionInfo, &disposition, sizeof(disposition));
}
#else
DataResult write_all(int descriptor, const std::string& bytes) {
    std::size_t written = 0;
    while (written < bytes.size())
    {
        errno               = 0;
        const ssize_t count = ::write(descriptor, bytes.data() + written, bytes.size() - written);
        if (count > 0)
        {
            written += std::size_t(count);
            continue;
        }
        if (count < 0 && errno == EINTR)
            continue;
        const int error = count == 0 ? EIO : errno;
        return DataResult::failure(DataError::WRITE_FAILED, "Cannot write Atomic BIN V2 manifest: "
                                                              + system_error_message(error));
    }
    return DataResult::success();
}
#endif

}  // namespace

std::filesystem::path atomic_bin_v2_manifest_path(const std::filesystem::path& firstShard) {
    std::filesystem::path path = firstShard;
    path += ".manifest.json";
    return path;
}

DataResult preflight_atomic_bin_v2_manifest_publication(const std::filesystem::path& manifestPath) {
#ifdef _WIN32
    // CreateFileW(CREATE_NEW) plus deletion through the exact owned handle is
    // available on every supported Windows target. Parent existence,
    // writability and destination nonexistence are checked by the generator's
    // ordinary output-path preflight and again by the final publication.
    (void) manifestPath;
    return DataResult::success();
#elif defined(__linux__) && defined(O_TMPFILE) && defined(AT_EMPTY_PATH) \
  && defined(AT_SYMLINK_FOLLOW)
    std::filesystem::path directory = manifestPath.parent_path();
    if (directory.empty())
        directory = ".";

    int directoryFlags = O_RDONLY;
    #ifdef O_DIRECTORY
    directoryFlags |= O_DIRECTORY;
    #endif
    #ifdef O_CLOEXEC
    directoryFlags |= O_CLOEXEC;
    #endif
    const int directoryDescriptor = ::open(directory.c_str(), directoryFlags);
    if (directoryDescriptor == -1)
    {
        const int error = errno;
        return DataResult::failure(DataError::OPEN_FAILED,
                                   "Cannot preflight the Atomic BIN V2 manifest directory: "
                                     + system_error_message(error));
    }

    int temporaryFlags = O_WRONLY | O_TMPFILE;
    #ifdef O_CLOEXEC
    temporaryFlags |= O_CLOEXEC;
    #endif
    const int descriptor = ::openat(directoryDescriptor, ".", temporaryFlags, 0666);
    if (descriptor == -1)
    {
        const int error = errno;
        ::close(directoryDescriptor);
        return DataResult::failure(
          DataError::OPEN_FAILED,
          "Cannot preflight race-free Atomic BIN V2 manifest publication; the filesystem "
          "must support O_TMPFILE: "
            + system_error_message(error));
    }

    // For ordinary unprivileged processes the final linkat() fallback follows
    // this procfs descriptor link. Confirm that it resolves to this exact
    // anonymous inode, then exercise the same no-replace link operation inside
    // a private probe directory. This catches filesystems and security policies
    // that allow O_TMPFILE but reject publication, before a long generation.
    struct stat       anonymousStatus{};
    struct stat       procStatus{};
    const std::string descriptorPath = "/proc/self/fd/" + std::to_string(descriptor);
    const bool        procDescriptorMatches =
      ::fstat(descriptor, &anonymousStatus) == 0 && ::stat(descriptorPath.c_str(), &procStatus) == 0
      && anonymousStatus.st_dev == procStatus.st_dev && anonymousStatus.st_ino == procStatus.st_ino;
    if (!procDescriptorMatches)
    {
        ::close(descriptor);
        ::close(directoryDescriptor);
        return DataResult::failure(
          DataError::OPEN_FAILED,
          "Cannot preflight race-free Atomic BIN V2 manifest publication: /proc/self/fd does "
          "not resolve the anonymous staging inode");
    }

    static std::atomic<u64> probeSequence{0};
    std::string             probeDirectoryName;
    bool                    probeDirectoryCreated = false;
    for (unsigned int attempt = 0; attempt < 256; ++attempt)
    {
        probeDirectoryName = ".atomic-bin-v2-manifest-preflight-" + std::to_string(::getpid()) + "-"
                           + std::to_string(probeSequence.fetch_add(1));
        if (::mkdirat(directoryDescriptor, probeDirectoryName.c_str(), 0700) == 0)
        {
            probeDirectoryCreated = true;
            break;
        }
        if (errno != EEXIST)
            break;
    }
    if (!probeDirectoryCreated)
    {
        const int error = errno;
        ::close(descriptor);
        ::close(directoryDescriptor);
        return DataResult::failure(
          DataError::OPEN_FAILED,
          "Cannot create the private Atomic BIN V2 manifest publication probe: "
            + system_error_message(error));
    }

    int probeDirectoryFlags = O_RDONLY;
    #ifdef O_DIRECTORY
    probeDirectoryFlags |= O_DIRECTORY;
    #endif
    #ifdef O_CLOEXEC
    probeDirectoryFlags |= O_CLOEXEC;
    #endif
    #ifdef O_NOFOLLOW
    probeDirectoryFlags |= O_NOFOLLOW;
    #endif
    const int probeDirectoryDescriptor =
      ::openat(directoryDescriptor, probeDirectoryName.c_str(), probeDirectoryFlags);
    if (probeDirectoryDescriptor == -1)
    {
        const int openError = errno;
        errno               = 0;
        const int cleanupResult =
          ::unlinkat(directoryDescriptor, probeDirectoryName.c_str(), AT_REMOVEDIR);
        const int cleanupError = cleanupResult == 0 ? 0 : errno;
        ::close(descriptor);
        ::close(directoryDescriptor);
        std::string message = "Cannot open the private Atomic BIN V2 publication probe: "
                            + system_error_message(openError);
        if (cleanupError != 0)
            message += "; probe cleanup failed: " + system_error_message(cleanupError);
        return DataResult::failure(DataError::OPEN_FAILED, std::move(message));
    }

    auto probeLinkWithoutReplace = [&](int sourceDescriptor, const char* source, int sourceFlags) {
        int result;
        do
        {
            errno = 0;
            result =
              ::linkat(sourceDescriptor, source, probeDirectoryDescriptor, "probe", sourceFlags);
        } while (result != 0 && errno == EINTR);
        return result;
    };

    int probeLinkResult = probeLinkWithoutReplace(descriptor, "", AT_EMPTY_PATH);
    int probeLinkError  = probeLinkResult == 0 ? 0 : errno;
    if (probeLinkResult != 0
        && (probeLinkError == ENOENT || probeLinkError == EPERM || probeLinkError == EACCES))
    {
        probeLinkResult =
          probeLinkWithoutReplace(AT_FDCWD, descriptorPath.c_str(), AT_SYMLINK_FOLLOW);
        probeLinkError = probeLinkResult == 0 ? 0 : errno;
    }

    int cleanupError = 0;
    if (probeLinkResult == 0 && ::unlinkat(probeDirectoryDescriptor, "probe", 0) != 0)
        cleanupError = errno;
    ::close(probeDirectoryDescriptor);
    if (::unlinkat(directoryDescriptor, probeDirectoryName.c_str(), AT_REMOVEDIR) != 0
        && cleanupError == 0)
        cleanupError = errno;
    ::close(descriptor);
    ::close(directoryDescriptor);

    if (probeLinkResult != 0)
        return DataResult::failure(DataError::OPEN_FAILED,
                                   "Cannot preflight the Atomic BIN V2 manifest no-replace link: "
                                     + system_error_message(probeLinkError));
    if (cleanupError != 0)
        return DataResult::failure(
          DataError::ABORT_FAILED,
          "Cannot clean the private Atomic BIN V2 manifest publication probe: "
            + system_error_message(cleanupError));
    return DataResult::success();
#else
    (void) manifestPath;
    return DataResult::failure(
      DataError::OPEN_FAILED,
      "Race-free Atomic BIN V2 manifest publication requires Linux O_TMPFILE or Windows");
#endif
}

namespace {

class CanonicalManifestParser {
   public:
    explicit CanonicalManifestParser(std::string_view input) :
        bytes(input) {}

    DataResult parse(const std::filesystem::path& manifestPath, AtomicBinV2Manifest& manifest) {
        manifest              = {};
        manifest.manifestPath = manifestPath.lexically_normal();

        if (!valid_utf8(bytes))
            return failure("is not valid UTF-8");
        if (bytes.size() > 64U * 1024U * 1024U)
            return failure("exceeds the 64 MiB manifest limit");

        u64         manifestVersion = 0;
        std::string manifestSchema;
        std::string dataSchema;
        std::string format;
        std::string useNnue;

        if (!character('{') || !key("manifest_version") || !unsigned_number(manifestVersion)
            || !comma() || !key("manifest_schema_sha256") || !string(manifestSchema) || !comma()
            || !key("data_schema_sha256") || !string(dataSchema) || !comma() || !key("format")
            || !string(format) || !comma() || !key("engine") || !character('{') || !key("commit")
            || !string(manifest.engineCommit) || !comma() || !key("version")
            || !string(manifest.engineVersion) || !character('}') || !comma() || !key("network")
            || !character('{') || !key("file"))
            return failure("does not match the frozen field order");

        std::string filename;
        if (!string(filename))
            return failure("contains an invalid network filename");
        manifest.networkPath = resolve(manifest.manifestPath, filename);
        if (!comma() || !key("sha256") || !string(manifest.networkSha256) || !character('}')
            || !comma() || !key("book") || !character('{') || !key("kind"))
            return failure("does not match the frozen network/book layout");

        std::string bookKind;
        if (!string(bookKind) || !comma() || !key("file"))
            return failure("contains invalid book metadata");
        if (bookKind == "file")
        {
            if (!string(filename))
                return failure("contains an invalid book filename");
            manifest.bookIsFile = true;
            manifest.bookPath   = resolve(manifest.manifestPath, filename);
            if (!comma() || !key("sha256") || !string(manifest.bookSha256))
                return failure("contains invalid book authentication metadata");
        }
        else if (bookKind == "builtin-startpos")
        {
            if (!literal("null") || !comma() || !key("sha256") || !literal("null"))
                return failure("must use null file/hash for the built-in book");
        }
        else
            return failure("contains an unsupported book kind");

        if (!character('}') || !comma() || !key("generation") || !character('{')
            || !key("resolved_seed") || !unsigned_string(manifest.resolvedSeed) || !comma()
            || !key("atomic960") || !boolean(manifest.atomic960) || !comma() || !key("threads")
            || !unsigned32(manifest.threads) || !comma() || !key("hash_mb")
            || !unsigned_string(manifest.hashMb) || !comma() || !key("use_nnue") || !string(useNnue)
            || !comma() || !key("options") || !character('{'))
            return failure("does not match the frozen generation layout");

        auto& options = manifest.options;
        if (!key("search_depth_min") || !signed32(options.searchDepthMin) || !comma()
            || !key("search_depth_max") || !signed32(options.searchDepthMax) || !comma()
            || !key("nodes") || !unsigned_string(options.nodes) || !comma()
            || !key("requested_records") || !unsigned_string(options.requestedRecords) || !comma()
            || !key("records_per_shard") || !unsigned_string(options.recordsPerShard) || !comma()
            || !key("eval_limit") || !signed32(options.evalLimit) || !comma()
            || !key("eval_diff_limit") || !signed32(options.evalDiffLimit) || !comma()
            || !key("random_move_min_ply") || !signed32(options.randomMoveMinPly) || !comma()
            || !key("random_move_max_ply") || !signed32(options.randomMoveMaxPly) || !comma()
            || !key("random_move_count") || !signed32(options.randomMoveCount) || !comma()
            || !key("random_move_like_apery") || !signed32(options.randomMoveLikeApery) || !comma()
            || !key("random_multi_pv") || !signed32(options.randomMultiPv) || !comma()
            || !key("random_multi_pv_diff") || !signed32(options.randomMultiPvDiff) || !comma()
            || !key("random_multi_pv_depth") || !signed32(options.randomMultiPvDepth) || !comma()
            || !key("write_min_ply") || !signed32(options.writeMinPly) || !comma()
            || !key("write_max_ply") || !signed32(options.writeMaxPly) || !comma()
            || !key("keep_draws") || !string(options.keepDraws) || !comma()
            || !key("adjudicate_draws_by_score") || !boolean(options.adjudicateDrawsByScore)
            || !comma() || !key("adjudicate_insufficient")
            || !boolean(options.adjudicateInsufficient) || !comma() || !key("filter_captures")
            || !boolean(options.filterCaptures) || !comma() || !key("filter_checks")
            || !boolean(options.filterChecks) || !comma() || !key("filter_promotions")
            || !boolean(options.filterPromotions) || !comma() || !key("random_file_name")
            || !boolean(options.randomFileName) || !comma()
            || !key("set_recommended_uci_options_seen")
            || !boolean(options.setRecommendedUciOptionsSeen) || !character('}') || !character('}')
            || !comma() || !key("statistics") || !character('{') || !key("records")
            || !unsigned_string(manifest.records) || !comma() || !key("draws")
            || !unsigned_string(manifest.draws) || !character('}') || !comma() || !key("shards")
            || !character('['))
            return failure("does not match the frozen options/statistics layout");

        if (peek() == ']')
            return failure("contains no shards");
        for (u64 expected = 0;; ++expected)
        {
            if (expected > std::numeric_limits<u32>::max() || expected >= MaximumManifestShards)
                return failure("contains too many shards");
            AtomicBinV2ManifestShard shard;
            u32                      parsedIndex = 0;
            if (!character('{') || !key("index") || !unsigned32(parsedIndex) || !comma()
                || !key("file") || !string(filename) || !comma() || !key("records")
                || !unsigned_string(shard.records) || !comma() || !key("bytes")
                || !unsigned_string(shard.bytes) || !comma() || !key("sha256")
                || !string(shard.sha256) || !character('}'))
                return failure("contains invalid shard metadata");
            shard.index = parsedIndex;
            shard.path  = resolve(manifest.manifestPath, filename);
            manifest.shards.push_back(std::move(shard));
            if (peek() != ',')
                break;
            ++cursor;
        }
        if (!character(']') || !character('}') || !character('\n') || cursor != bytes.size())
            return failure("contains trailing or noncanonical bytes");

        if (manifestVersion != 1 || manifestSchema != AtomicBinV2ManifestSchemaSha256Hex
            || dataSchema != DataSchemaSha256 || format != "atomic-bin-v2" || useNnue != "pure")
            return DataResult::failure(DataError::SCHEMA_MISMATCH,
                                       "Atomic BIN V2 manifest schema/capability constants differ");

        std::string rendered;
        if (DataResult valid = render_atomic_bin_v2_manifest(manifest, rendered); !valid)
            return valid;
        if (rendered != bytes)
            return failure("is not byte-exact canonical JSON");
        return DataResult::success();
    }

   private:
    DataResult failure(std::string message) const {
        return invalid_manifest("Atomic BIN V2 manifest " + std::move(message) + " at byte "
                                + std::to_string(cursor));
    }

    static std::filesystem::path resolve(const std::filesystem::path& manifestPath,
                                         const std::string&           filename) {
#ifdef _WIN32
        if (filename.empty() || filename.size() > std::size_t(std::numeric_limits<int>::max()))
            return {};
        const int length = ::MultiByteToWideChar(CP_UTF8, MB_ERR_INVALID_CHARS, filename.data(),
                                                 int(filename.size()), nullptr, 0);
        if (length <= 0)
            return {};
        std::wstring wide(std::size_t(length), L'\0');
        if (::MultiByteToWideChar(CP_UTF8, MB_ERR_INVALID_CHARS, filename.data(),
                                  int(filename.size()), wide.data(), length)
            != length)
            return {};
        return (manifestPath.parent_path() / std::filesystem::path(wide)).lexically_normal();
#else
        return (manifestPath.parent_path() / std::filesystem::path(filename)).lexically_normal();
#endif
    }

    char peek() const { return cursor < bytes.size() ? bytes[cursor] : '\0'; }
    bool character(char expected) {
        if (peek() != expected)
            return false;
        ++cursor;
        return true;
    }
    bool comma() { return character(','); }
    bool literal(std::string_view expected) {
        if (bytes.substr(cursor, expected.size()) != expected)
            return false;
        cursor += expected.size();
        return true;
    }
    bool key(std::string_view expected) {
        return character('"') && literal(expected) && character('"') && character(':');
    }
    bool boolean(bool& value) {
        if (literal("true"))
        {
            value = true;
            return true;
        }
        if (literal("false"))
        {
            value = false;
            return true;
        }
        return false;
    }
    static bool hex_digit(char input, u32& value) {
        if (input >= '0' && input <= '9')
            value = u32(input - '0');
        else if (input >= 'a' && input <= 'f')
            value = u32(input - 'a' + 10);
        else if (input >= 'A' && input <= 'F')
            value = u32(input - 'A' + 10);
        else
            return false;
        return true;
    }
    bool unicode_escape(u32& codepoint) {
        codepoint = 0;
        for (unsigned count = 0; count < 4; ++count)
        {
            u32 digit = 0;
            if (cursor == bytes.size() || !hex_digit(bytes[cursor++], digit))
                return false;
            codepoint = codepoint * 16 + digit;
        }
        return true;
    }
    static void append_utf8(std::string& output, u32 codepoint) {
        if (codepoint <= 0x7F)
            output.push_back(char(codepoint));
        else if (codepoint <= 0x7FF)
        {
            output.push_back(char(0xC0 | (codepoint >> 6)));
            output.push_back(char(0x80 | (codepoint & 0x3F)));
        }
        else if (codepoint <= 0xFFFF)
        {
            output.push_back(char(0xE0 | (codepoint >> 12)));
            output.push_back(char(0x80 | ((codepoint >> 6) & 0x3F)));
            output.push_back(char(0x80 | (codepoint & 0x3F)));
        }
        else
        {
            output.push_back(char(0xF0 | (codepoint >> 18)));
            output.push_back(char(0x80 | ((codepoint >> 12) & 0x3F)));
            output.push_back(char(0x80 | ((codepoint >> 6) & 0x3F)));
            output.push_back(char(0x80 | (codepoint & 0x3F)));
        }
    }
    bool string(std::string& output) {
        output.clear();
        if (!character('"'))
            return false;
        while (cursor < bytes.size())
        {
            const unsigned char current = bytes[cursor++];
            if (current == '"')
                return true;
            if (current < 0x20)
                return false;
            if (current != '\\')
            {
                output.push_back(char(current));
                continue;
            }
            if (cursor == bytes.size())
                return false;
            switch (bytes[cursor++])
            {
            case '"' :
                output.push_back('"');
                break;
            case '\\' :
                output.push_back('\\');
                break;
            case '/' :
                output.push_back('/');
                break;
            case 'b' :
                output.push_back('\b');
                break;
            case 'f' :
                output.push_back('\f');
                break;
            case 'n' :
                output.push_back('\n');
                break;
            case 'r' :
                output.push_back('\r');
                break;
            case 't' :
                output.push_back('\t');
                break;
            case 'u' : {
                u32 codepoint = 0;
                if (!unicode_escape(codepoint))
                    return false;
                if (codepoint >= 0xD800 && codepoint <= 0xDBFF)
                {
                    if (!literal("\\u"))
                        return false;
                    u32 low = 0;
                    if (!unicode_escape(low) || low < 0xDC00 || low > 0xDFFF)
                        return false;
                    codepoint = 0x10000 + ((codepoint - 0xD800) << 10) + (low - 0xDC00);
                }
                else if (codepoint >= 0xDC00 && codepoint <= 0xDFFF)
                    return false;
                append_utf8(output, codepoint);
                break;
            }
            default :
                return false;
            }
        }
        return false;
    }
    bool unsigned_number(u64& value) {
        const std::size_t start = cursor;
        if (peek() < '0' || peek() > '9')
            return false;
        value = 0;
        do
        {
            const u64 digit = u64(bytes[cursor++] - '0');
            if (value > (std::numeric_limits<u64>::max() - digit) / 10)
                return false;
            value = value * 10 + digit;
        } while (peek() >= '0' && peek() <= '9');
        return cursor - start == 1 || bytes[start] != '0';
    }
    bool unsigned_string(u64& value) {
        std::string text;
        if (!string(text) || text.empty() || (text.size() > 1 && text[0] == '0'))
            return false;
        value = 0;
        for (char digit : text)
        {
            if (digit < '0' || digit > '9'
                || value > (std::numeric_limits<u64>::max() - u64(digit - '0')) / 10)
                return false;
            value = value * 10 + u64(digit - '0');
        }
        return true;
    }
    bool unsigned32(u32& value) {
        u64 parsed = 0;
        if (!unsigned_number(parsed) || parsed > std::numeric_limits<u32>::max())
            return false;
        value = u32(parsed);
        return true;
    }
    bool signed32(int& value) {
        const bool negative = peek() == '-';
        if (negative)
            ++cursor;
        u64 parsed = 0;
        if (!unsigned_number(parsed))
            return false;
        const u64 maximum = negative ? u64(std::numeric_limits<i32>::max()) + 1
                                     : u64(std::numeric_limits<i32>::max());
        if (parsed > maximum || (negative && parsed == 0))
            return false;
        value = negative ? int(-std::int64_t(parsed)) : int(parsed);
        return true;
    }

    std::string_view bytes;
    std::size_t      cursor = 0;
};

DataResult read_manifest_file(const std::filesystem::path& path, std::string& bytes) {
    bytes.clear();
#ifdef _WIN32
    const HANDLE handle =
      ::CreateFileW(path.c_str(), GENERIC_READ, FILE_SHARE_READ, nullptr, OPEN_EXISTING,
                    FILE_ATTRIBUTE_NORMAL | FILE_FLAG_OPEN_REPARSE_POINT, nullptr);
    if (handle == INVALID_HANDLE_VALUE)
        return DataResult::failure(DataError::OPEN_FAILED,
                                   "Cannot open Atomic BIN V2 manifest: "
                                     + windows_error_message(::GetLastError()));
    FILE_ATTRIBUTE_TAG_INFO tag{};
    LARGE_INTEGER           size{};
    if (!::GetFileInformationByHandleEx(handle, FileAttributeTagInfo, &tag, sizeof(tag))
        || (tag.FileAttributes & FILE_ATTRIBUTE_REPARSE_POINT)
        || (tag.FileAttributes & FILE_ATTRIBUTE_DIRECTORY) || !::GetFileSizeEx(handle, &size)
        || size.QuadPart < 0 || u64(size.QuadPart) > 64U * 1024U * 1024U)
    {
        ::CloseHandle(handle);
        return DataResult::failure(
          DataError::OPEN_FAILED,
          "Atomic BIN V2 manifest must be a small regular non-reparse file");
    }
    bytes.resize(std::size_t(size.QuadPart));
    std::size_t offset = 0;
    while (offset < bytes.size())
    {
        const DWORD request = DWORD(std::min<std::size_t>(bytes.size() - offset, 1U << 20));
        DWORD       count   = 0;
        if (!::ReadFile(handle, bytes.data() + offset, request, &count, nullptr) || count == 0)
        {
            ::CloseHandle(handle);
            bytes.clear();
            return DataResult::failure(DataError::READ_FAILED,
                                       "Cannot read complete Atomic BIN V2 manifest");
        }
        offset += count;
    }
    LARGE_INTEGER finalSize{};
    if (!::GetFileSizeEx(handle, &finalSize) || finalSize.QuadPart != size.QuadPart)
    {
        ::CloseHandle(handle);
        bytes.clear();
        return DataResult::failure(DataError::FILE_IDENTITY_MISMATCH,
                                   "Atomic BIN V2 manifest changed while being read");
    }
    ::CloseHandle(handle);
#else
    int flags = O_RDONLY;
    #ifdef O_CLOEXEC
    flags |= O_CLOEXEC;
    #endif
    #ifdef O_NOFOLLOW
    flags |= O_NOFOLLOW;
    #endif
    const int descriptor = ::open(path.c_str(), flags);
    if (descriptor == -1)
        return DataResult::failure(DataError::OPEN_FAILED, "Cannot open Atomic BIN V2 manifest: "
                                                             + system_error_message(errno));
    struct stat before{};
    if (::fstat(descriptor, &before) != 0 || !S_ISREG(before.st_mode) || before.st_size < 0
        || u64(before.st_size) > 64U * 1024U * 1024U)
    {
        ::close(descriptor);
        return DataResult::failure(
          DataError::OPEN_FAILED,
          "Atomic BIN V2 manifest must be a small regular non-symlink file");
    }
    bytes.resize(std::size_t(before.st_size));
    std::size_t offset = 0;
    while (offset < bytes.size())
    {
        const ssize_t count = ::read(descriptor, bytes.data() + offset, bytes.size() - offset);
        if (count < 0 && errno == EINTR)
            continue;
        if (count <= 0)
        {
            ::close(descriptor);
            bytes.clear();
            return DataResult::failure(DataError::READ_FAILED,
                                       "Cannot read complete Atomic BIN V2 manifest");
        }
        offset += std::size_t(count);
    }
    struct stat after{};
    const bool  inspected       = ::fstat(descriptor, &after) == 0;
    const bool  timestampsMatch = [&] {
    #if defined(__APPLE__)
        return before.st_mtimespec.tv_sec == after.st_mtimespec.tv_sec
            && before.st_mtimespec.tv_nsec == after.st_mtimespec.tv_nsec
            && before.st_ctimespec.tv_sec == after.st_ctimespec.tv_sec
            && before.st_ctimespec.tv_nsec == after.st_ctimespec.tv_nsec;
    #else
        return before.st_mtim.tv_sec == after.st_mtim.tv_sec
            && before.st_mtim.tv_nsec == after.st_mtim.tv_nsec
            && before.st_ctim.tv_sec == after.st_ctim.tv_sec
            && before.st_ctim.tv_nsec == after.st_ctim.tv_nsec;
    #endif
    }();
    if (!inspected || before.st_dev != after.st_dev || before.st_ino != after.st_ino
        || before.st_size != after.st_size || !timestampsMatch)
    {
        ::close(descriptor);
        bytes.clear();
        return DataResult::failure(DataError::FILE_IDENTITY_MISMATCH,
                                   "Atomic BIN V2 manifest changed while being read");
    }
    ::close(descriptor);
#endif
    return DataResult::success();
}

}  // namespace

DataResult parse_atomic_bin_v2_manifest(std::string_view             bytes,
                                        const std::filesystem::path& manifestPath,
                                        AtomicBinV2Manifest&         output) {
    output = {};
    if (manifestPath.extension() == ".atbin"
        || !ends_with(path_filename_utf8(manifestPath), ".atbin.manifest.json"))
        return invalid_manifest(
          "Atomic BIN V2 datasets must be opened through an .atbin.manifest.json sidecar");
    CanonicalManifestParser parser(bytes);
    AtomicBinV2Manifest     parsed;
    if (DataResult result = parser.parse(manifestPath, parsed); !result)
        return result;
    output = std::move(parsed);
    return DataResult::success();
}

DataResult load_atomic_bin_v2_manifest(const std::filesystem::path& manifestPath,
                                       AtomicBinV2Manifest&         output) {
    output = {};
    if (manifestPath.extension() == ".atbin"
        || !ends_with(path_filename_utf8(manifestPath), ".atbin.manifest.json"))
        return invalid_manifest(
          "Atomic BIN V2 datasets must be opened through an .atbin.manifest.json sidecar");
    std::string bytes;
    if (DataResult read = read_manifest_file(manifestPath, bytes); !read)
        return read;
    return parse_atomic_bin_v2_manifest(bytes, manifestPath, output);
}

DataResult render_atomic_bin_v2_manifest(const AtomicBinV2Manifest& manifest, std::string& json) {
    json.clear();
    if (DataResult valid = validate_manifest(manifest); !valid)
        return valid;

    json.reserve(2048 + manifest.shards.size() * 160);
    json += '{';
    append_integer_field(json, "manifest_version", 1);
    append_string_field(json, "manifest_schema_sha256", AtomicBinV2ManifestSchemaSha256Hex);
    append_string_field(json, "data_schema_sha256", DataSchemaSha256);
    append_string_field(json, "format", "atomic-bin-v2");

    json += "\"engine\":{";
    append_string_field(json, "commit", manifest.engineCommit);
    append_string_field(json, "version", manifest.engineVersion, "},");

    json += "\"network\":{";
    append_string_field(json, "file", path_filename_utf8(manifest.networkPath));
    append_string_field(json, "sha256", manifest.networkSha256, "},");

    json += "\"book\":{";
    append_string_field(json, "kind", manifest.bookIsFile ? "file" : "builtin-startpos");
    if (manifest.bookIsFile)
    {
        append_string_field(json, "file", path_filename_utf8(manifest.bookPath));
        append_string_field(json, "sha256", manifest.bookSha256, "},");
    }
    else
        json += "\"file\":null,\"sha256\":null},";

    json += "\"generation\":{";
    append_string_field(json, "resolved_seed", std::to_string(manifest.resolvedSeed));
    append_bool_field(json, "atomic960", manifest.atomic960);
    append_integer_field(json, "threads", manifest.threads);
    append_string_field(json, "hash_mb", std::to_string(manifest.hashMb));
    append_string_field(json, "use_nnue", "pure");
    json += "\"options\":{";
    const auto& options = manifest.options;
    append_integer_field(json, "search_depth_min", options.searchDepthMin);
    append_integer_field(json, "search_depth_max", options.searchDepthMax);
    append_string_field(json, "nodes", std::to_string(options.nodes));
    append_string_field(json, "requested_records", std::to_string(options.requestedRecords));
    append_string_field(json, "records_per_shard", std::to_string(options.recordsPerShard));
    append_integer_field(json, "eval_limit", options.evalLimit);
    append_integer_field(json, "eval_diff_limit", options.evalDiffLimit);
    append_integer_field(json, "random_move_min_ply", options.randomMoveMinPly);
    append_integer_field(json, "random_move_max_ply", options.randomMoveMaxPly);
    append_integer_field(json, "random_move_count", options.randomMoveCount);
    append_integer_field(json, "random_move_like_apery", options.randomMoveLikeApery);
    append_integer_field(json, "random_multi_pv", options.randomMultiPv);
    append_integer_field(json, "random_multi_pv_diff", options.randomMultiPvDiff);
    append_integer_field(json, "random_multi_pv_depth", options.randomMultiPvDepth);
    append_integer_field(json, "write_min_ply", options.writeMinPly);
    append_integer_field(json, "write_max_ply", options.writeMaxPly);
    append_string_field(json, "keep_draws", options.keepDraws);
    append_bool_field(json, "adjudicate_draws_by_score", options.adjudicateDrawsByScore);
    append_bool_field(json, "adjudicate_insufficient", options.adjudicateInsufficient);
    append_bool_field(json, "filter_captures", options.filterCaptures);
    append_bool_field(json, "filter_checks", options.filterChecks);
    append_bool_field(json, "filter_promotions", options.filterPromotions);
    append_bool_field(json, "random_file_name", options.randomFileName);
    append_bool_field(json, "set_recommended_uci_options_seen",
                      options.setRecommendedUciOptionsSeen, "}},");

    json += "\"statistics\":{";
    append_string_field(json, "records", std::to_string(manifest.records));
    append_string_field(json, "draws", std::to_string(manifest.draws), "},");

    json += "\"shards\":[";
    for (std::size_t index = 0; index < manifest.shards.size(); ++index)
    {
        if (index)
            json.push_back(',');
        const auto& shard = manifest.shards[index];
        json.push_back('{');
        append_integer_field(json, "index", static_cast<long long>(shard.index));
        append_string_field(json, "file", path_filename_utf8(shard.path));
        append_string_field(json, "records", std::to_string(shard.records));
        append_string_field(json, "bytes", std::to_string(shard.bytes));
        append_string_field(json, "sha256", shard.sha256, "}");
    }
    json += "]}\n";
    return DataResult::success();
}

DataResult write_atomic_bin_v2_manifest(const AtomicBinV2Manifest& manifest) {
    std::string json;
    if (DataResult rendered = render_atomic_bin_v2_manifest(manifest, json); !rendered)
        return rendered;

    // Re-authenticate each finalized path immediately before publishing its
    // sidecar. This catches path replacement or mutation after sink close.
    for (const auto& shard : manifest.shards)
    {
        std::string actualSha;
        u64         actualSize = 0;
        if (DataResult hashed = sha256_file(shard.path, actualSha, actualSize); !hashed)
            return hashed;
        if (actualSha != shard.sha256 || actualSize != shard.bytes)
            return invalid_manifest("Atomic BIN V2 shard changed before manifest publication");
    }

#ifdef _WIN32
    const HANDLE handle =
      ::CreateFileW(manifest.manifestPath.c_str(), GENERIC_WRITE | DELETE, FILE_SHARE_READ, nullptr,
                    CREATE_NEW, FILE_ATTRIBUTE_NORMAL, nullptr);
    if (handle == INVALID_HANDLE_VALUE)
    {
        const unsigned long error = ::GetLastError();
        return DataResult::failure(
          error == ERROR_FILE_EXISTS || error == ERROR_ALREADY_EXISTS ? DataError::OUTPUT_EXISTS
                                                                      : DataError::OPEN_FAILED,
          "Cannot create Atomic BIN V2 manifest exclusively: " + windows_error_message(error));
    }

    if (DataResult written = write_all(handle, json); !written)
    {
        remove_created_handle(handle);
        ::CloseHandle(handle);
        return written;
    }
    if (!::FlushFileBuffers(handle))
    {
        const unsigned long error = ::GetLastError();
        remove_created_handle(handle);
        ::CloseHandle(handle);
        return DataResult::failure(DataError::WRITE_FAILED,
                                   "Cannot synchronize Atomic BIN V2 manifest: "
                                     + windows_error_message(error));
    }
    if (!::CloseHandle(handle))
    {
        const unsigned long error = ::GetLastError();
        // CloseHandle normally either succeeds or leaves this exact handle
        // valid. If it remains valid, mark only that owned file for deletion.
        remove_created_handle(handle);
        ::CloseHandle(handle);
        return DataResult::failure(DataError::CLOSE_FAILED, "Cannot close Atomic BIN V2 manifest: "
                                                              + windows_error_message(error));
    }
#else
    #if defined(__linux__) && defined(O_TMPFILE) && defined(AT_EMPTY_PATH) \
      && defined(AT_SYMLINK_FOLLOW)
    std::filesystem::path directory = manifest.manifestPath.parent_path();
    if (directory.empty())
        directory = ".";

    int directoryFlags = O_RDONLY;
        #ifdef O_DIRECTORY
    directoryFlags |= O_DIRECTORY;
        #endif
        #ifdef O_CLOEXEC
    directoryFlags |= O_CLOEXEC;
        #endif
    const int directoryDescriptor = ::open(directory.c_str(), directoryFlags);
    if (directoryDescriptor == -1)
    {
        const int error = errno;
        return DataResult::failure(DataError::OPEN_FAILED,
                                   "Cannot open the Atomic BIN V2 manifest directory: "
                                     + system_error_message(error));
    }

    int temporaryFlags = O_WRONLY | O_TMPFILE;
        #ifdef O_CLOEXEC
    temporaryFlags |= O_CLOEXEC;
        #endif
    const int descriptor = ::openat(directoryDescriptor, ".", temporaryFlags, 0666);
    if (descriptor == -1)
    {
        const int error = errno;
        ::close(directoryDescriptor);
        return DataResult::failure(
          DataError::OPEN_FAILED,
          "Cannot create the anonymous Atomic BIN V2 manifest staging file; the filesystem "
          "must support O_TMPFILE for race-free publication: "
            + system_error_message(error));
    }

    if (DataResult written = write_all(descriptor, json); !written)
    {
        ::close(descriptor);
        ::close(directoryDescriptor);
        return written;
    }

    errno = 0;
    int syncResult;
    do
    {
        syncResult = ::fsync(descriptor);
    } while (syncResult != 0 && errno == EINTR);
    if (syncResult != 0)
    {
        const int error = errno;
        ::close(descriptor);
        ::close(directoryDescriptor);
        return DataResult::failure(DataError::WRITE_FAILED,
                                   "Cannot synchronize Atomic BIN V2 manifest staging: "
                                     + system_error_message(error));
    }

    const std::string filename = manifest.manifestPath.filename().string();
    auto linkWithoutReplace    = [&](int sourceDescriptor, const char* source, int sourceFlags) {
        int result;
        do
        {
            errno  = 0;
            result = ::linkat(sourceDescriptor, source, directoryDescriptor, filename.c_str(),
                                 sourceFlags);
        } while (result != 0 && errno == EINTR);
        return result;
    };

    int linkResult = linkWithoutReplace(descriptor, "", AT_EMPTY_PATH);
    int linkError  = linkResult == 0 ? 0 : errno;
    if (linkResult != 0 && (linkError == ENOENT || linkError == EPERM || linkError == EACCES))
    {
        const std::string descriptorPath = "/proc/self/fd/" + std::to_string(descriptor);
        linkResult = linkWithoutReplace(AT_FDCWD, descriptorPath.c_str(), AT_SYMLINK_FOLLOW);
        linkError  = linkResult == 0 ? 0 : errno;
    }
    if (linkResult != 0)
    {
        ::close(descriptor);
        ::close(directoryDescriptor);
        if (linkError == EEXIST)
            return DataResult::failure(DataError::OUTPUT_EXISTS,
                                       "Cannot publish Atomic BIN V2 manifest exclusively: output "
                                       "already exists");
        return DataResult::failure(DataError::WRITE_FAILED,
                                   "Cannot publish Atomic BIN V2 manifest exclusively: "
                                     + system_error_message(linkError));
    }

    // linkat() is the publication commit point. The destination now names the
    // fully written inode and must never be removed or treated as a rollback
    // candidate because another process can replace its pathname at any time.
    // Best-effort directory synchronization and closes cannot turn a committed
    // publication into an ordinary failure: the caller would then remove the
    // finalized dataset shards referenced by this manifest.
    ::fsync(directoryDescriptor);
    ::close(descriptor);
    ::close(directoryDescriptor);
    #else
    return DataResult::failure(
      DataError::OPEN_FAILED,
      "Race-free Atomic BIN V2 manifest publication requires Linux O_TMPFILE or Windows");
    #endif
#endif
    return DataResult::success();
}

}  // namespace Stockfish::Data
