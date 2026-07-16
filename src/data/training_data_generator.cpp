/*
  Atomic-Stockfish training-data generator
  Copyright (C) 2026 The Atomic-Stockfish developers

  Atomic-Stockfish is free software: you can redistribute it and/or modify
  it under the terms of the GNU General Public License as published by
  the Free Software Foundation, either version 3 of the License, or
  (at your option) any later version.
*/

#include "training_data_generator.h"

#include <algorithm>
#include <array>
#include <atomic>
#include <cerrno>
#include <chrono>
#include <condition_variable>
#include <cmath>
#include <cctype>
#include <cstdio>
#include <cstdint>
#include <filesystem>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <limits>
#include <map>
#include <memory>
#include <mutex>
#include <new>
#include <optional>
#include <sstream>
#include <string>
#include <string_view>
#include <system_error>
#include <utility>
#include <vector>

#ifdef _WIN32
    #ifndef NOMINMAX
        #define NOMINMAX
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

#include "api/atomic_outcome.h"
#include "atomic_bin_v2.h"
#include "atomic_bin_v2_manifest.h"
#include "atomic_bin_v2_sink.h"
#include "atomic_v3_trajectory.h"
#include "engine.h"
#include "legacy_atomic_v1.h"
#include "misc.h"
#include "movegen.h"
#include "position.h"
#include "random_seed.h"
#include "search.h"
#include "sha256.h"
#include "thread.h"
#include "tt.h"

namespace Stockfish::Data {
namespace {

constexpr std::string_view AtomicStartFen =
  "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1";
constexpr u64              ReportEvery         = 5000;
constexpr u64              MinimumShardRecords = 200000;
constexpr u64              MaximumShardFiles   = 100000;
constexpr int              MaximumGeneratedPly = 4096;
constexpr int              LegacyKnownWin      = 10000;
constexpr std::string_view LegacyAtomicV1SchemaSha256 =
  "acca0f551f1c012c31a6c727dedccaebb7b5ebbc46810edb87e31bb208d5abe1";
// Preserve the historical direct-mapped 64M-key deduplication table. Reducing
// it changes collision/false-positive rates and therefore dataset selection.
constexpr usize DedupeTableSize = usize(64) * 1024 * 1024;

enum class DatasetFormat {
    LEGACY_ATOMIC_V1,
    ATOMIC_BIN_V2
};

struct GeneratorParams {
    int searchDepthMin = 3;
    int searchDepthMax = -1;
    u64 nodes          = 0;
    u64 count          = 100000000ULL;

    int evalLimit     = 3000;
    int evalDiffLimit = 64000;

    int randomMoveMinPly    = 1;
    int randomMoveMaxPly    = 24;
    int randomMoveCount     = 5;
    int randomMoveLikeApery = 0;
    int randomMultiPv       = 5;
    int randomMultiPvDiff   = 100;
    int randomMultiPvDepth  = -1;

    int writeMinPly = 5;
    int writeMaxPly = 400;
    u64 saveEvery   = std::numeric_limits<u64>::max();

    std::string outputFile = "training_data";
    std::string seedText;
    std::string book;

    DatasetFormat dataFormat = DatasetFormat::LEGACY_ATOMIC_V1;
    bool          atomic960  = false;

    double      keepDraws                    = 1.0;
    std::string keepDrawsManifest            = "1";
    bool        adjudicateDrawsByScore       = true;
    bool        adjudicateInsufficient       = true;
    bool        filterCaptures               = true;
    bool        filterChecks                 = false;
    bool        filterPromotions             = false;
    bool        randomFileName               = false;
    bool        setRecommendedUciOptionsSeen = false;
};

struct GameResolution {
    bool                 terminal = false;
    std::optional<Color> winner;
};

template<typename T>
bool read_value(std::istream& input, T& value, std::string_view name, std::string& error) {
    if (input >> value)
        return true;
    error = "Missing or invalid value for generate_training_data option " + std::string(name);
    return false;
}

bool read_bool(std::istream& input, bool& value, std::string_view name, std::string& error) {
    std::string token;
    if (!(input >> token))
    {
        error = "Missing value for generate_training_data option " + std::string(name);
        return false;
    }

    if (token == "1" || token == "true")
        value = true;
    else if (token == "0" || token == "false")
        value = false;
    else
    {
        error = "Boolean option " + std::string(name) + " must be 0, 1, false, or true";
        return false;
    }
    return true;
}

bool read_u64_value(std::istream& input, u64& value, std::string_view name, std::string& error) {
    std::string token;
    if (!(input >> token) || token.empty())
    {
        error = "Missing or invalid value for generate_training_data option " + std::string(name);
        return false;
    }

    u64 parsed = 0;
    for (unsigned char c : token)
    {
        if (c < '0' || c > '9')
        {
            error = "Unsigned option " + std::string(name) + " must contain decimal digits only";
            return false;
        }
        const u64 digit = c - '0';
        if (parsed > (std::numeric_limits<u64>::max() - digit) / 10)
        {
            error = "Unsigned option " + std::string(name) + " is out of range";
            return false;
        }
        parsed = parsed * 10 + digit;
    }

    value = parsed;
    return true;
}

bool read_keep_draws(std::istream& input,
                     double&       value,
                     std::string&  canonical,
                     std::string&  error) {
    value = 0.0;
    canonical.clear();

    std::string token;
    if (!(input >> token))
    {
        error = "Missing or invalid value for generate_training_data option keep_draws";
        return false;
    }
    if (DataResult normalized = normalize_atomic_keep_draws(token, value, canonical); !normalized)
    {
        error = normalized.message;
        return false;
    }
    return true;
}

bool parse_params(std::istream& input, GeneratorParams& params, std::string& error) {
    std::string token;
    bool        searchDepthMaxSeen = false;
    while (input >> token)
    {
        if (token == "depth")
        {
            if (!read_value(input, params.searchDepthMin, token, error))
                return false;
            params.searchDepthMax = params.searchDepthMin;
            searchDepthMaxSeen    = true;
        }
        else if (token == "min_depth")
        {
            if (!read_value(input, params.searchDepthMin, token, error))
                return false;
        }
        else if (token == "max_depth")
        {
            if (!read_value(input, params.searchDepthMax, token, error))
                return false;
            searchDepthMaxSeen = true;
        }
        else if (token == "nodes")
        {
            if (!read_u64_value(input, params.nodes, token, error))
                return false;
        }
        else if (token == "count")
        {
            if (!read_u64_value(input, params.count, token, error))
                return false;
        }
        else if (token == "output_file_name")
        {
            if (!read_value(input, params.outputFile, token, error))
                return false;
        }
        else if (token == "eval_limit")
        {
            if (!read_value(input, params.evalLimit, token, error))
                return false;
        }
        else if (token == "eval_diff_limit")
        {
            if (!read_value(input, params.evalDiffLimit, token, error))
                return false;
        }
        else if (token == "random_move_min_ply")
        {
            if (!read_value(input, params.randomMoveMinPly, token, error))
                return false;
        }
        else if (token == "random_move_max_ply")
        {
            if (!read_value(input, params.randomMoveMaxPly, token, error))
                return false;
        }
        else if (token == "random_move_count")
        {
            if (!read_value(input, params.randomMoveCount, token, error))
                return false;
        }
        else if (token == "random_move_like_apery")
        {
            if (!read_value(input, params.randomMoveLikeApery, token, error))
                return false;
        }
        else if (token == "random_multi_pv")
        {
            if (!read_value(input, params.randomMultiPv, token, error))
                return false;
        }
        else if (token == "random_multi_pv_diff")
        {
            if (!read_value(input, params.randomMultiPvDiff, token, error))
                return false;
        }
        else if (token == "random_multi_pv_depth")
        {
            if (!read_value(input, params.randomMultiPvDepth, token, error))
                return false;
        }
        else if (token == "write_min_ply")
        {
            if (!read_value(input, params.writeMinPly, token, error))
                return false;
        }
        else if (token == "write_max_ply")
        {
            if (!read_value(input, params.writeMaxPly, token, error))
                return false;
        }
        else if (token == "save_every")
        {
            if (!read_u64_value(input, params.saveEvery, token, error))
                return false;
        }
        else if (token == "book")
        {
            if (!read_value(input, params.book, token, error))
                return false;
        }
        else if (token == "random_file_name")
        {
            if (!read_bool(input, params.randomFileName, token, error))
                return false;
        }
        else if (token == "keep_draws")
        {
            if (!read_keep_draws(input, params.keepDraws, params.keepDrawsManifest, error))
                return false;
        }
        else if (token == "adjudicate_draws_by_score")
        {
            if (!read_bool(input, params.adjudicateDrawsByScore, token, error))
                return false;
        }
        else if (token == "adjudicate_draws_by_insufficient_material")
        {
            if (!read_bool(input, params.adjudicateInsufficient, token, error))
                return false;
        }
        else if (token == "filter_captures")
        {
            if (!read_bool(input, params.filterCaptures, token, error))
                return false;
        }
        else if (token == "filter_checks")
        {
            if (!read_bool(input, params.filterChecks, token, error))
                return false;
        }
        else if (token == "filter_promotions")
        {
            if (!read_bool(input, params.filterPromotions, token, error))
                return false;
        }
        else if (token == "data_format")
        {
            std::string value;
            if (!read_value(input, value, token, error))
                return false;
            if (value == "bin")
                params.dataFormat = DatasetFormat::LEGACY_ATOMIC_V1;
            else if (value == "atomic-bin-v2")
                params.dataFormat = DatasetFormat::ATOMIC_BIN_V2;
            else
            {
                error = "Unknown generate_training_data data_format " + value;
                return false;
            }
        }
        else if (token == "seed")
        {
            if (!read_value(input, params.seedText, token, error))
                return false;
        }
        else if (token == "set_recommended_uci_options")
            params.setRecommendedUciOptionsSeen = true;
        else
        {
            error = "Unknown generate_training_data option " + token;
            return false;
        }
    }

    if (!searchDepthMaxSeen)
        params.searchDepthMax = params.searchDepthMin;
    params.randomMultiPvDepth = std::max(params.searchDepthMax, params.randomMultiPvDepth);
    if (params.dataFormat == DatasetFormat::LEGACY_ATOMIC_V1
        && params.saveEvery != std::numeric_limits<u64>::max())
        params.saveEvery = std::max(params.saveEvery, MinimumShardRecords);

    const bool invalid =
      params.count == 0 || params.searchDepthMin <= 0
      || params.searchDepthMax < params.searchDepthMin || params.evalLimit <= 0
      || params.searchDepthMax >= MAX_PLY || params.randomMultiPvDepth >= MAX_PLY
      || params.evalLimit > std::numeric_limits<i16>::max() || params.evalDiffLimit < 0
      || params.writeMinPly < 0 || params.writeMaxPly <= params.writeMinPly
      || params.writeMaxPly > MaximumGeneratedPly || params.randomMoveMinPly < -1
      || params.randomMoveMaxPly < 0 || params.randomMoveMaxPly > MaximumGeneratedPly
      || (params.randomMoveMinPly != -1 && params.randomMoveMaxPly < params.randomMoveMinPly)
      || params.randomMoveCount < 0 || params.randomMoveCount > MaximumGeneratedPly
      || params.randomMoveLikeApery < 0 || params.randomMultiPv < 0
      || params.randomMultiPv > int(MAX_MOVES) || params.randomMultiPvDiff < 0
      || params.randomMultiPvDepth < 0 || !std::isfinite(params.keepDraws) || params.keepDraws < 0.0
      || params.keepDraws > 1.0 || params.outputFile.empty()
      || (params.dataFormat == DatasetFormat::ATOMIC_BIN_V2 && params.saveEvery == 0);

    if (invalid)
    {
        error = "Invalid generate_training_data parameter range";
        return false;
    }
    return true;
}

bool ends_with(std::string_view text, std::string_view suffix) {
    return text.size() >= suffix.size() && text.substr(text.size() - suffix.size()) == suffix;
}

std::filesystem::path shard_path(const std::string& rawName, u64 shard, DatasetFormat dataFormat) {
    std::string name = rawName;
    if (shard)
        name += "_" + std::to_string(shard);
    const std::string_view extension =
      dataFormat == DatasetFormat::LEGACY_ATOMIC_V1 ? ".bin" : ".atbin";
    if (!ends_with(name, extension))
        name += extension;
    return std::filesystem::path(name);
}

std::optional<std::vector<std::filesystem::path>> output_paths(const GeneratorParams& params,
                                                               std::string&           error) {
    const u64 recordsPerFile =
      params.saveEvery == std::numeric_limits<u64>::max() ? params.count : params.saveEvery;
    const u64 shards = 1 + (params.count - 1) / recordsPerFile;
    if (shards > MaximumShardFiles || shards > u64(std::numeric_limits<usize>::max()))
    {
        error = "Training-data request would create too many output shards";
        return std::nullopt;
    }

    std::vector<std::filesystem::path> paths;
    paths.reserve(usize(shards));
    for (u64 shard = 0; shard < shards; ++shard)
        paths.push_back(shard_path(params.outputFile, shard, params.dataFormat));
    return paths;
}

bool preflight_output_paths(const std::vector<std::filesystem::path>& paths, std::string& error) {
    for (const auto& path : paths)
    {
        std::error_code ec;
        const bool      exists = std::filesystem::exists(path, ec);
        if (ec)
        {
            error = "Cannot inspect output path " + path.string() + ": " + ec.message();
            return false;
        }
        if (exists)
        {
            error = "Training-data output already exists: " + path.string();
            return false;
        }
    }
    return true;
}

struct AuthenticatedFile {
    std::filesystem::path path;
    std::string           sha256;
    u64                   bytes = 0;
    std::string           contents;
};

bool load_book(std::istream*             snapshot,
               std::string_view          source,
               bool                      atomic960,
               std::vector<std::string>& positions,
               std::string&              error);

std::string path_to_utf8(const std::filesystem::path& path) {
#ifdef _WIN32
    return utf8_from_wstring(path.native());
#else
    return path.string();
#endif
}

std::optional<std::filesystem::path> absolute_file_candidate(const std::filesystem::path& path) {
    std::error_code ec;
    auto            absolute = std::filesystem::absolute(path, ec);
    if (ec)
        return std::nullopt;
    return absolute.lexically_normal();
}

std::FILE* open_private_exclusive(const std::filesystem::path& path) {
#ifdef _WIN32
    const int descriptor = ::_wopen(path.c_str(), _O_CREAT | _O_EXCL | _O_WRONLY | _O_BINARY,
                                    _S_IREAD | _S_IWRITE);
#else
    const int descriptor = ::open(path.c_str(), O_CREAT | O_EXCL | O_WRONLY | O_CLOEXEC, 0600);
#endif
    if (descriptor < 0)
        return nullptr;
    std::FILE* file = ::fdopen(descriptor, "wb");
    if (!file)
    {
        const int code = errno;
#ifdef _WIN32
        ::_close(descriptor);
#else
        ::close(descriptor);
#endif
        errno = code;
    }
    return file;
}

DataResult sync_private_file(std::FILE* file, std::string_view label) {
    if (!file || std::fflush(file) != 0)
        return DataResult::failure(DataError::WRITE_FAILED,
                                   "Cannot flush " + std::string(label) + ": "
                                     + std::generic_category().message(errno));
#ifdef _WIN32
    const int result = ::_commit(::_fileno(file));
#else
    const int result = ::fsync(::fileno(file));
#endif
    return result == 0
           ? DataResult::success()
           : DataResult::failure(DataError::WRITE_FAILED,
                                 "Cannot synchronize " + std::string(label) + ": "
                                   + std::generic_category().message(errno));
}

u64 read_u64_le_bytes(const u8* input) noexcept {
    u64 value = 0;
    for (unsigned i = 0; i < 8; ++i)
        value |= u64(input[i]) << (8 * i);
    return value;
}

bool read_authenticated_regular_file(const std::filesystem::path& path,
                                     AuthenticatedFile&           authenticated,
                                     std::string&                 error) {
    authenticated = {};
    Sha256 hash;

#ifdef _WIN32
    HANDLE handle = ::CreateFileW(path.c_str(), GENERIC_READ, FILE_SHARE_READ, nullptr,
                                  OPEN_EXISTING,
                                  FILE_ATTRIBUTE_NORMAL | FILE_FLAG_OPEN_REPARSE_POINT
                                    | FILE_FLAG_SEQUENTIAL_SCAN,
                                  nullptr);
    if (handle == INVALID_HANDLE_VALUE)
    {
        error = "Cannot open authenticated input " + path_to_utf8(path) + ": "
              + std::system_category().message(int(::GetLastError()));
        return false;
    }
    const auto close = [&]() { ::CloseHandle(handle); };

    FILE_ATTRIBUTE_TAG_INFO attributes{};
    BY_HANDLE_FILE_INFORMATION before{};
    LARGE_INTEGER size{};
    if (!::GetFileInformationByHandleEx(handle, FileAttributeTagInfo, &attributes,
                                        sizeof(attributes))
        || (attributes.FileAttributes & FILE_ATTRIBUTE_REPARSE_POINT)
        || !::GetFileInformationByHandle(handle, &before) || before.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY
        || !::GetFileSizeEx(handle, &size) || size.QuadPart < 0
        || u64(size.QuadPart) > u64(std::numeric_limits<std::size_t>::max())
        || u64(size.QuadPart) > Sha256MaxByteCount)
    {
        const DWORD code = ::GetLastError();
        close();
        error = "Authenticated input is not a stable regular non-reparse file: "
              + path_to_utf8(path);
        if (code != ERROR_SUCCESS)
            error += ": " + std::system_category().message(int(code));
        return false;
    }

    authenticated.contents.resize(std::size_t(size.QuadPart));
    std::size_t offset = 0;
    while (offset < authenticated.contents.size())
    {
        const DWORD requested = DWORD(std::min<std::size_t>(authenticated.contents.size() - offset,
                                                           1U << 20));
        DWORD read = 0;
        if (!::ReadFile(handle, authenticated.contents.data() + offset, requested, &read, nullptr)
            || read == 0)
        {
            const DWORD code = ::GetLastError();
            close();
            error = "Cannot read authenticated input " + path_to_utf8(path) + ": "
                  + std::system_category().message(int(code));
            return false;
        }
        hash.update(authenticated.contents.data() + offset, read);
        offset += read;
    }
    char  trailing = 0;
    DWORD trailingRead = 0;
    BY_HANDLE_FILE_INFORMATION after{};
    const bool stable = ::ReadFile(handle, &trailing, 1, &trailingRead, nullptr)
                     && trailingRead == 0 && ::GetFileInformationByHandle(handle, &after)
                     && before.dwVolumeSerialNumber == after.dwVolumeSerialNumber
                     && before.nFileIndexHigh == after.nFileIndexHigh
                     && before.nFileIndexLow == after.nFileIndexLow
                     && before.nFileSizeHigh == after.nFileSizeHigh
                     && before.nFileSizeLow == after.nFileSizeLow
                     && before.ftLastWriteTime.dwHighDateTime == after.ftLastWriteTime.dwHighDateTime
                     && before.ftLastWriteTime.dwLowDateTime == after.ftLastWriteTime.dwLowDateTime;
    close();
    if (!stable)
    {
        error = "Authenticated input changed while its byte snapshot was captured: "
              + path_to_utf8(path);
        return false;
    }
#else
    const int descriptor = ::open(path.c_str(), O_RDONLY | O_CLOEXEC | O_NOFOLLOW);
    if (descriptor < 0)
    {
        error = "Cannot open authenticated input " + path.string() + ": "
              + std::generic_category().message(errno);
        return false;
    }
    const auto close = [&]() { ::close(descriptor); };
    struct stat before{};
    if (::fstat(descriptor, &before) != 0 || !S_ISREG(before.st_mode) || before.st_size < 0
        || u64(before.st_size) > u64(std::numeric_limits<std::size_t>::max())
        || u64(before.st_size) > Sha256MaxByteCount)
    {
        close();
        error = "Authenticated input is not a stable regular non-symlink file: " + path.string();
        return false;
    }
    authenticated.contents.resize(std::size_t(before.st_size));
    std::size_t offset = 0;
    while (offset < authenticated.contents.size())
    {
        const ssize_t count = ::read(descriptor, authenticated.contents.data() + offset,
                                     authenticated.contents.size() - offset);
        if (count < 0 && errno == EINTR)
            continue;
        if (count <= 0)
        {
            const int code = errno;
            close();
            error = "Cannot read authenticated input " + path.string() + ": "
                  + std::generic_category().message(code ? code : EIO);
            return false;
        }
        hash.update(authenticated.contents.data() + offset, std::size_t(count));
        offset += std::size_t(count);
    }
    char trailing = 0;
    ssize_t trailingRead;
    do
        trailingRead = ::read(descriptor, &trailing, 1);
    while (trailingRead < 0 && errno == EINTR);
    struct stat after{};
    const auto timestampsMatch = [&]() {
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
    };
    const bool stable = trailingRead == 0 && ::fstat(descriptor, &after) == 0
                     && before.st_dev == after.st_dev && before.st_ino == after.st_ino
                     && before.st_size == after.st_size && timestampsMatch();
    close();
    if (!stable)
    {
        error = "Authenticated input changed while its byte snapshot was captured: "
              + path.string();
        return false;
    }
#endif

    authenticated.path   = path;
    authenticated.bytes  = u64(authenticated.contents.size());
    authenticated.sha256 = hash.hex_digest();
    return true;
}

bool authenticate_network(Engine&                      engine,
                          const std::filesystem::path& binaryDirectory,
                          std::string_view             requiredSha256,
                          AuthenticatedFile&           authenticated,
                          std::string&                 error) {
    const auto requested = path_from_utf8(std::string(engine.get_options()["EvalFile"]));
    if (requested.empty())
    {
        error = "Atomic BIN V2 requires an external EvalFile that can be authenticated";
        return false;
    }

    std::vector<std::filesystem::path> candidates;
    const auto                         add_candidate = [&](const std::filesystem::path& candidate) {
        const auto absolute = absolute_file_candidate(candidate);
        if (absolute
            && std::find(candidates.begin(), candidates.end(), *absolute) == candidates.end())
            candidates.push_back(*absolute);
    };
    add_candidate(requested);
    add_candidate(binaryDirectory / requested);
#ifdef DEFAULT_NNUE_DIRECTORY
    add_candidate(std::filesystem::path(stringify(DEFAULT_NNUE_DIRECTORY)) / requested);
#endif

    for (const auto& candidate : candidates)
    {
        AuthenticatedFile snapshot;
        if (!read_authenticated_regular_file(candidate, snapshot, error))
            continue;
        if (!requiredSha256.empty() && snapshot.sha256 != requiredSha256)
        {
            error = "Authenticated EvalFile SHA-256 does not match the required pin";
            return false;
        }
        std::istringstream stream(snapshot.contents, std::ios::binary);
        if (!engine.load_authenticated_network(stream, requested) || !engine.verify_network())
            continue;
        authenticated = std::move(snapshot);
        return true;
    }

    error = "Atomic BIN V2 could not authenticate and load the selected compatible EvalFile";
    return false;
}

bool authenticate_book_and_load(const std::string&        requested,
                                bool                      atomic960,
                                std::string_view          requiredSha256,
                                std::vector<std::string>& positions,
                                AuthenticatedFile&        authenticated,
                                std::string&              error) {
    const auto path = absolute_file_candidate(path_from_utf8(requested));
    if (!path)
    {
        error = "Cannot authenticate training-data opening book: " + requested;
        return false;
    }

    AuthenticatedFile snapshot;
    if (!read_authenticated_regular_file(*path, snapshot, error))
        return false;
    if (!requiredSha256.empty() && snapshot.sha256 != requiredSha256)
    {
        error = "Authenticated opening-book SHA-256 does not match the required pin";
        return false;
    }
    std::istringstream stream(snapshot.contents);
    if (!load_book(&stream, path_to_utf8(*path), atomic960, positions, error))
        return false;
    authenticated = std::move(snapshot);
    return true;
}

bool dataset_rule50_fits(DatasetFormat dataFormat, int rule50) {
    if (dataFormat == DatasetFormat::LEGACY_ATOMIC_V1)
        return legacy_atomic_v1_rule50_fits(rule50);
    return rule50 >= 0 && u64(rule50) <= AtomicBinV2MaxRule50;
}

bool dataset_position_clocks_fit(DatasetFormat dataFormat, const Position& position) {
    return dataset_rule50_fits(dataFormat, position.rule50_count())
        && (dataFormat != DatasetFormat::ATOMIC_BIN_V2
            || atomic_bin_v2_fullmove_fits_game_ply(position.game_ply()));
}

std::string trim(std::string text) {
    const auto first = text.find_first_not_of(" \t\r\n");
    if (first == std::string::npos)
        return {};
    const auto last = text.find_last_not_of(" \t\r\n");
    return text.substr(first, last - first + 1);
}

std::optional<std::string> normalize_book_line(std::string line) {
    line = trim(std::move(line));
    if (line.empty() || line[0] == '#')
        return std::nullopt;

    std::istringstream       stream(line);
    std::vector<std::string> fields;
    std::string              field;
    while (stream >> field)
        fields.push_back(field);
    if (fields.size() < 4)
        return std::nullopt;

    std::ostringstream fen;
    for (usize i = 0; i < 4; ++i)
        fen << (i ? " " : "") << fields[i];

    const auto decimal = [](const std::string& value) {
        return !value.empty() && std::all_of(value.begin(), value.end(), [](unsigned char c) {
            return std::isdigit(c) != 0;
        });
    };
    bool hasClocks = fields.size() >= 6 && decimal(fields[4]) && decimal(fields[5]);
    fen << (hasClocks ? " " + fields[4] + " " + fields[5] : " 0 1");
    return fen.str();
}

bool load_book(std::istream*             snapshot,
               std::string_view          source,
               bool                      atomic960,
               std::vector<std::string>& positions,
               std::string&              error) {
    const auto append_position = [&](const std::string& candidate) {
        Position  position;
        StateInfo state{};
        if (position.set(candidate, atomic960, &state) || !position.has_king(WHITE)
            || !position.has_king(BLACK) || Atomic::outcome(position, true, 0).terminal())
        {
            error = "Opening book contains an invalid or unsupported Atomic FEN: " + candidate;
            return false;
        }
        positions.push_back(position.fen());
        return true;
    };

    if (!snapshot)
        return append_position(std::string(AtomicStartFen));

    std::string line;
    while (std::getline(*snapshot, line))
    {
        auto candidate = normalize_book_line(std::move(line));
        if (!candidate)
            continue;

        if (!append_position(*candidate))
            return false;
    }

    if (snapshot->bad())
    {
        error = "Cannot read authenticated training-data opening book: " + std::string(source);
        return false;
    }

    if (positions.empty())
    {
        error = "Training-data opening book contains no usable positions";
        return false;
    }
    return true;
}

Color side_to_move_from_fen(const std::string& fen) {
    const auto separator = fen.find(' ');
    assert(separator != std::string::npos && separator + 1 < fen.size());
    return fen[separator + 1] == 'b' ? BLACK : WHITE;
}

struct AtomicBinV2ShardMetadata {
    std::filesystem::path path;
    u64                   index   = 0;
    u64                   records = 0;
    u64                   bytes   = 0;
    std::string           sha256;
};

class OutputSeries {
   public:
    OutputSeries(std::vector<std::filesystem::path> paths_,
                 u64                                recordsPerFile_,
                 DatasetFormat                      dataFormat_) :
        paths(std::move(paths_)),
        recordsPerFile(recordsPerFile_),
        dataFormat(dataFormat_) {}

    DataResult append(const TrainingDataSample& sample) {
        if (shard >= paths.size())
            return DataResult::failure(DataError::WRITE_FAILED,
                                       "Training-data output shard capacity was exhausted");

        if (!sink)
            sink = make_sink(paths[usize(shard)]);

        if (recordsInShard == recordsPerFile)
        {
            if (DataResult result = finalize_current(); !result)
                return result;
            ++shard;
            recordsInShard = 0;
            if (shard >= paths.size())
                return DataResult::failure(DataError::WRITE_FAILED,
                                           "Training-data output shard capacity was exhausted");
            sink = make_sink(paths[usize(shard)]);
        }

        if (DataResult result = sink->append(sample); !result)
            return result;
        ++recordsInShard;
        ++totalRecords;
        return DataResult::success();
    }

    DataResult finalize() {
        if (!sink)
            return DataResult::failure(DataError::EMPTY_DATASET,
                                       "Cannot finalize an empty training dataset");
        return finalize_current();
    }

    DataResult abort() {
        DataResult first = DataResult::success();
        if (sink)
        {
            if (DataResult result = sink->abort(); !result)
                first = result;
            sink.reset();
        }

        if (dataFormat == DatasetFormat::ATOMIC_BIN_V2)
        {
            for (auto& owner : finalizedV2Owners)
                if (DataResult result = owner->remove_finalized_owned(); !result && first)
                    first = result;
        }
        else
        {
            for (u64 i = 0; i < finalizedShards; ++i)
            {
                const auto&     path = paths[usize(i)];
                std::error_code ec;
                std::filesystem::remove(path, ec);
                if (ec && first)
                    first = DataResult::failure(DataError::ABORT_FAILED,
                                                "Cannot remove partial output " + path.string()
                                                  + ": " + ec.message());
            }
        }
        if (first)
        {
            finalizedShards = 0;
            finalizedV2Shards.clear();
            finalizedV2Owners.clear();
        }
        return first;
    }

    u64                                          records_written() const { return totalRecords; }
    const std::vector<AtomicBinV2ShardMetadata>& atomic_bin_v2_shards() const noexcept {
        return finalizedV2Shards;
    }

   private:
    std::unique_ptr<DatasetSink> make_sink(const std::filesystem::path& path) const {
        if (dataFormat == DatasetFormat::LEGACY_ATOMIC_V1)
            return std::make_unique<LegacyAtomicV1Sink>(path);
        return std::make_unique<AtomicBinV2Sink>(path);
    }

    DataResult finalize_current() {
        assert(sink);
        if (DataResult result = sink->finalize(); !result)
            return result;

        if (dataFormat == DatasetFormat::ATOMIC_BIN_V2)
        {
            std::unique_ptr<AtomicBinV2Sink> v2(static_cast<AtomicBinV2Sink*>(sink.release()));
            finalizedV2Shards.push_back({v2->output_path(), shard, v2->records_written(),
                                         v2->finalized_size(), v2->sha256_hex()});
            finalizedV2Owners.push_back(std::move(v2));
        }
        else
            sink.reset();
        ++finalizedShards;
        return DataResult::success();
    }

    std::vector<std::filesystem::path> paths;
    u64                                recordsPerFile;
    DatasetFormat                      dataFormat;
    u64                                shard           = 0;
    u64                                recordsInShard  = 0;
    u64                                totalRecords    = 0;
    u64                                finalizedShards = 0;
    std::unique_ptr<DatasetSink>       sink;

    std::vector<AtomicBinV2ShardMetadata>         finalizedV2Shards;
    std::vector<std::unique_ptr<AtomicBinV2Sink>> finalizedV2Owners;
};

class SeenPositions {
   public:
    SeenPositions() :
        slots(std::make_unique<std::atomic<Key>[]>(DedupeTableSize)) {
        for (usize i = 0; i < DedupeTableSize; ++i)
            slots[i].store(0, std::memory_order_relaxed);
    }

    bool already_seen(Key key) {
        const usize index = usize(key) & (DedupeTableSize - 1);
        return slots[index].exchange(key, std::memory_order_relaxed) == key;
    }

   private:
    std::unique_ptr<std::atomic<Key>[]> slots;
};

class Generator {
   public:
    Generator(const GeneratorParams&             params_,
              ThreadPool&                        threads_,
              TranspositionTable&                tt_,
              std::vector<std::filesystem::path> paths_,
              std::vector<std::string>           openingPositions_,
              u64                                resolvedSeed) :
        params(params_),
        threads(threads_),
        tt(tt_),
        output(std::move(paths_),
               params_.saveEvery == std::numeric_limits<u64>::max() ? params_.count
                                                                    : params_.saveEvery,
               params_.dataFormat),
        openingPositions(std::move(openingPositions_)) {
        ReplayablePRNG source(resolvedSeed);
        workerSeeds.reserve(threads.size());
        for (usize i = 0; i < threads.size(); ++i)
            workerSeeds.push_back(i == 0 ? source.seed() : source.next_seed());

        // The historical EPD book is shuffled exactly once with worker 0's
        // stream, then shared round-robin across workers. With no book, the
        // singleton start position consumes no random number.
        if (!params.book.empty())
        {
            ReplayablePRNG bookRng(workerSeeds[0]);
            for (usize i = 0; i < openingPositions.size(); ++i)
                std::swap(openingPositions[i],
                          openingPositions[i + bookRng.rand(openingPositions.size() - i)]);
            workerSeeds[0] = bookRng.seed();
        }
    }

    bool run(std::string& error) {
        threads.wait_for_search_finished();
        threads.clear();
        tt.clear(threads);
        threads.stop = false;
        tt.new_search();

        std::vector<Thread*> workers;
        workers.reserve(threads.size());
        for (auto& thread : threads)
            workers.push_back(thread.get());

        for (usize i = 0; i < workers.size(); ++i)
        {
            Search::Worker* worker = workers[i]->worker.get();
            threads.run_on_thread(i, [this, worker, i]() { run_worker(*worker, i); });
        }

        for (usize i = 0; i < workers.size(); ++i)
            threads.wait_on_thread(i);
        threads.stop = false;

        std::lock_guard lock(outputMutex);
        if (!failure.empty())
        {
            const DataResult cleanup = output.abort();
            error                    = failure;
            if (!cleanup)
                error += "; cleanup failed: " + cleanup.message;
            return false;
        }

        if (DataResult result = output.finalize(); !result)
        {
            const DataResult cleanup = output.abort();
            error                    = result.message;
            if (!cleanup)
                error += "; cleanup failed: " + cleanup.message;
            return false;
        }
        return true;
    }

    u64 records_written() const { return written.load(std::memory_order_relaxed); }
    u64 draws_written() const { return draws.load(std::memory_order_relaxed); }
    const std::vector<AtomicBinV2ShardMetadata>& atomic_bin_v2_shards() const noexcept {
        return output.atomic_bin_v2_shards();
    }
    DataResult abort_output() {
        std::lock_guard lock(outputMutex);
        return output.abort();
    }

   private:
    const GeneratorParams&   params;
    ThreadPool&              threads;
    TranspositionTable&      tt;
    OutputSeries             output;
    std::vector<std::string> openingPositions;
    std::vector<u64>         workerSeeds;
    SeenPositions            seen;

    std::atomic<u64>                            written{0};
    std::atomic<u64>                            draws{0};
    std::atomic<bool>                           finished{false};
    std::mutex                                  outputMutex;
    std::mutex                                  openingMutex;
    usize                                       openingIndex = 0;
    std::string                                 failure;
    const std::chrono::steady_clock::time_point started = std::chrono::steady_clock::now();

    void fail(std::string message) {
        {
            std::lock_guard lock(outputMutex);
            if (failure.empty())
                failure = std::move(message);
        }
        finished.store(true, std::memory_order_relaxed);
        threads.stop = true;
    }

    const std::string& next_opening() {
        std::lock_guard    lock(openingMutex);
        const std::string& fen = openingPositions[openingIndex];
        if (++openingIndex == openingPositions.size())
            openingIndex = 0;
        return fen;
    }

    std::vector<u8> random_move_flags(ReplayablePRNG& rng) const {
        std::vector<u8> flags(usize(params.randomMoveMaxPly + params.randomMoveCount), 0);

        std::vector<int> candidates;
        for (int ply = std::max(params.randomMoveMinPly - 1, 0); ply < params.randomMoveMaxPly;
             ++ply)
            candidates.push_back(ply);

        const int count = std::min(params.randomMoveCount, int(candidates.size()));
        for (int i = 0; i < count; ++i)
        {
            const usize selected = usize(i) + usize(rng.rand(candidates.size() - usize(i)));
            std::swap(candidates[usize(i)], candidates[selected]);
            flags[usize(candidates[usize(i)])] = 1;
        }
        return flags;
    }

    std::optional<Move> choose_random_move(Search::Worker&  worker,
                                           Position&        position,
                                           ReplayablePRNG&  rng,
                                           std::vector<u8>& flags,
                                           int              ply,
                                           int&             randomMovesMade) const {
        const bool selected =
          (params.randomMoveMinPly != -1 && usize(ply) < flags.size() && flags[usize(ply)])
          || (params.randomMoveMinPly == -1 && randomMovesMade < params.randomMoveCount);
        if (!selected)
            return std::nullopt;

        ++randomMovesMade;
        if (params.randomMultiPv == 0)
        {
            const MoveList<LEGAL> moves(position);
            if (moves.size() == 0)
                return std::nullopt;

            if (params.randomMoveLikeApery == 0 || rng.rand(u64(params.randomMoveLikeApery)) != 0)
                return *(moves.begin() + rng.rand(moves.size()));

            std::vector<Move> kingMoves;
            for (Move move : moves)
                if (type_of(position.moved_piece(move)) == KING)
                    kingMoves.push_back(move);

            if (kingMoves.empty())
                return *(moves.begin() + rng.rand(moves.size()));

            const Move kingMove = kingMoves[rng.rand(kingMoves.size())];
            if (rng.rand(2) == 0)
            {
                assert(usize(ply + 1) <= flags.size());
                flags.insert(flags.begin() + ply + 1, 1);
            }
            return kingMove;
        }

        Search::TrainingSearchRequest request;
        request.mode      = Search::TrainingSearchMode::FixedDepth;
        request.depth     = params.randomMultiPvDepth;
        request.multiPV   = usize(params.randomMultiPv);
        const auto result = worker.training_search(position, request);
        if (result.lines.empty())
            return std::nullopt;

        usize candidates = result.lines.size();
        for (usize i = 1; i < candidates; ++i)
            if (std::int64_t(result.lines.front().value)
                > std::int64_t(result.lines[i].value) + std::int64_t(params.randomMultiPvDiff))
            {
                candidates = i;
                break;
            }

        const auto& line = result.lines[rng.rand(candidates)];
        return line.pv.empty() ? std::optional<Move>{} : std::optional<Move>{line.pv[0]};
    }

    GameResolution
    current_resolution(const Position& position, int ply, const std::vector<int>& scores) const {
        const auto outcome          = Atomic::outcome(position, true, 0);
        const auto resolutionSource = training_resolution_source(
          outcome.terminal(), outcome.termination == Atomic::Termination::InsufficientMaterial,
          params.adjudicateInsufficient, ply >= params.writeMaxPly);
        if (resolutionSource == TrainingResolutionSource::OUTCOME)
            return {true, outcome.winner};
        if (resolutionSource == TrainingResolutionSource::MAX_PLY)
            return {true, std::nullopt};

        if (params.adjudicateDrawsByScore && ply >= 80 && scores.size() >= 8)
        {
            const bool allZero =
              std::all_of(scores.end() - 8, scores.end(), [](int score) { return score == 0; });
            if (allZero)
                return {true, std::nullopt};
        }
        return {};
    }

    bool commit_game(std::vector<TrainingDataSample>& samples, std::optional<Color> winner) {
        if (samples.empty())
            return false;

        std::lock_guard lock(outputMutex);
        if (!failure.empty() || finished.load(std::memory_order_relaxed))
            return true;

        const bool draw = !winner.has_value();
        if (draw
            && !legacy_atomic_v1_draw_game_fits(
              draws.load(std::memory_order_relaxed), written.load(std::memory_order_relaxed),
              u64(samples.size()), params.count, params.keepDraws))
            return false;

        for (auto& sample : samples)
        {
            const u64 index = written.load(std::memory_order_relaxed);
            if (index >= params.count)
            {
                finished.store(true, std::memory_order_relaxed);
                threads.stop = true;
                return true;
            }

            sample.result = winner ? (side_to_move_from_fen(sample.fen) == *winner ? 1 : -1) : 0;
            if (DataResult result = output.append(sample); !result)
            {
                failure = result.message;
                finished.store(true, std::memory_order_relaxed);
                threads.stop = true;
                return true;
            }

            const u64 done = written.fetch_add(1, std::memory_order_relaxed) + 1;
            if (draw)
                draws.fetch_add(1, std::memory_order_relaxed);

            if (done % ReportEvery == 0 || done == params.count)
            {
                const auto elapsed = std::max(
                  1.0, std::chrono::duration<double>(std::chrono::steady_clock::now() - started)
                         .count());
                std::cout << "info string training data " << done << "/" << params.count
                          << " records, " << u64(double(done) / elapsed) << " records/s"
                          << std::endl;
            }

            if (done >= params.count)
            {
                finished.store(true, std::memory_order_relaxed);
                threads.stop = true;
                return true;
            }
        }
        return false;
    }

    void run_worker(Search::Worker& worker, usize workerIndex) {
        ReplayablePRNG         rng(workerSeeds[workerIndex]);
        std::vector<StateInfo> states(usize(params.writeMaxPly) + 1);

        while (!finished.load(std::memory_order_relaxed))
        {
            StateInfo          rootState{};
            Position           position;
            const std::string& initialFen = next_opening();
            if (const auto setError = position.set(initialFen, params.atomic960, &rootState))
            {
                fail(std::string("Cannot set opening FEN: ") + setError->what());
                return;
            }

            int        resignCounter = 0;
            const bool shouldResign  = rng.rand(10) > 1;

            std::vector<TrainingDataSample> samples;
            samples.reserve(usize(params.writeMaxPly));
            std::vector<int> scores;
            scores.reserve(usize(params.writeMaxPly));
            std::vector<u8> flags           = random_move_flags(rng);
            int             randomMovesMade = 0;

            for (int ply = 0; !finished.load(std::memory_order_relaxed); ++ply)
            {
                const int depth =
                  params.searchDepthMin
                  + int(rng.rand(u64(params.searchDepthMax - params.searchDepthMin + 1)));

                Search::TrainingSearchRequest evalRequest;
                evalRequest.mode = Search::TrainingSearchMode::Evaluate;
                if (!position.has_king(WHITE) || !position.has_king(BLACK)
                    || position.atomic_in_check(position.side_to_move())
                    || Atomic::outcome(position, true, 0).terminal())
                    evalRequest.mode = Search::TrainingSearchMode::Quiescence;
                const auto evalResult = worker.training_search(position, evalRequest);

                Search::TrainingSearchRequest qRequest;
                qRequest.mode      = Search::TrainingSearchMode::Quiescence;
                const auto qResult = worker.training_search(position, qRequest);

                Search::TrainingSearchRequest searchRequest;
                searchRequest.mode      = Search::TrainingSearchMode::FixedDepth;
                searchRequest.depth     = depth;
                searchRequest.nodes     = params.nodes;
                searchRequest.multiPV   = 1;
                const auto searchResult = worker.training_search(position, searchRequest);

                if (finished.load(std::memory_order_relaxed))
                    break;
                if (const auto resolution = current_resolution(position, ply, scores);
                    resolution.terminal)
                {
                    commit_game(samples, resolution.winner);
                    break;
                }
                if (searchResult.value == VALUE_NONE || qResult.value == VALUE_NONE
                    || evalResult.value == VALUE_NONE)
                {
                    fail("Synchronous training search returned VALUE_NONE");
                    return;
                }
                if (searchResult.pv.empty())
                    break;

                const int score = searchResult.value;
                if (std::abs(score) >= params.evalLimit)
                {
                    ++resignCounter;
                    if ((shouldResign && resignCounter >= 4) || std::abs(score) >= LegacyKnownWin)
                    {
                        const Color winner =
                          score > 0 ? position.side_to_move() : ~position.side_to_move();
                        commit_game(samples, winner);
                        break;
                    }
                }
                else
                    resignCounter = 0;

                scores.push_back(score);
                const Move bestMove = searchResult.pv[0];
                const bool stableTarget =
                  std::abs(int(qResult.value) - int(evalResult.value)) <= params.evalDiffLimit;

                if (ply >= params.writeMinPly
                    && dataset_position_clocks_fit(params.dataFormat, position)
                    && !seen.already_seen(position.key()) && position.has_king(WHITE)
                    && position.has_king(BLACK)
                    && !position.atomic_in_check(position.side_to_move()) && stableTarget
                    && !(params.filterCaptures && position.capture(bestMove))
                    && !(params.filterChecks && position.gives_check(bestMove))
                    && !(params.filterPromotions && bestMove.type_of() == PROMOTION))
                {
                    samples.push_back(
                      {position.fen(), score, bestMove, ply, 0,
                       params.atomic960 ? TRAINING_DATA_CHESS960 : NO_TRAINING_DATA_FLAGS});
                }

                const auto randomMove =
                  choose_random_move(worker, position, rng, flags, ply, randomMovesMade);
                const Move nextMove = randomMove.value_or(bestMove);
                if (!MoveList<LEGAL>(position).contains(nextMove))
                {
                    fail("Training generator selected an illegal move");
                    return;
                }

                position.do_move(nextMove, states[usize(ply)], &tt);
            }
        }
    }
};

struct AtomicV3GeneratorParams {
    GeneratorParams generation;
    u64             trainCount          = 0;
    u64             validationCount     = 0;
    u64             splitSeed           = 0;
    u64             validationThreshold = 0;
    u64             maximumGames        = 1000000000ULL;
    std::string     outputPrefix;
    std::string     networkSha256;
    std::string     bookSha256;
    bool            adjudicateResignations = false;

    AtomicV3GeneratorParams() {
        generation.dataFormat              = DatasetFormat::ATOMIC_BIN_V2;
        generation.adjudicateDrawsByScore  = false;
        generation.filterPromotions        = true;
        generation.keepDraws               = 1.0;
        generation.keepDrawsManifest       = "1";
        generation.randomFileName          = false;
        generation.saveEvery               = std::numeric_limits<u64>::max();
    }
};

bool normalize_sha256_pin(std::string& value) {
    if (value.size() != 64)
        return false;
    for (char& character : value)
    {
        const unsigned char c = static_cast<unsigned char>(character);
        if (!std::isxdigit(c))
            return false;
        character = char(std::tolower(c));
    }
    return true;
}

#ifdef ATOMIC_DATA_GENERATOR_GIT_SHA
bool is_canonical_git_commit(std::string_view value) {
    return value.size() == 40
        && std::all_of(value.begin(), value.end(), [](unsigned char character) {
               return (character >= '0' && character <= '9')
                   || (character >= 'a' && character <= 'f');
           });
}
#endif

bool parse_atomic_v3_params(std::istream&            input,
                            AtomicV3GeneratorParams& params,
                            std::string&             error) {
    auto&       common = params.generation;
    std::string token;
    bool        depthMaxSeen = false;
    bool        generationSeedSeen = false;
    bool        splitSeedSeen      = false;
    bool        thresholdSeen      = false;
    bool        networkPinSeen     = false;
    bool        bookPinSeen        = false;
    while (input >> token)
    {
        if (token == "depth")
        {
            if (!read_value(input, common.searchDepthMin, token, error))
                return false;
            common.searchDepthMax = common.searchDepthMin;
            depthMaxSeen          = true;
        }
        else if (token == "min_depth")
        {
            if (!read_value(input, common.searchDepthMin, token, error))
                return false;
        }
        else if (token == "max_depth")
        {
            if (!read_value(input, common.searchDepthMax, token, error))
                return false;
            depthMaxSeen = true;
        }
        else if (token == "nodes")
        {
            if (!read_u64_value(input, common.nodes, token, error))
                return false;
        }
        else if (token == "train_count")
        {
            if (!read_u64_value(input, params.trainCount, token, error))
                return false;
        }
        else if (token == "validation_count")
        {
            if (!read_u64_value(input, params.validationCount, token, error))
                return false;
        }
        else if (token == "output_prefix" || token == "out")
        {
            if (!read_value(input, params.outputPrefix, token, error))
                return false;
        }
        else if (token == "eval_limit")
        {
            if (!read_value(input, common.evalLimit, token, error))
                return false;
        }
        else if (token == "eval_diff_limit")
        {
            if (!read_value(input, common.evalDiffLimit, token, error))
                return false;
        }
        else if (token == "random_move_min_ply")
        {
            if (!read_value(input, common.randomMoveMinPly, token, error))
                return false;
        }
        else if (token == "random_move_max_ply")
        {
            if (!read_value(input, common.randomMoveMaxPly, token, error))
                return false;
        }
        else if (token == "random_move_count")
        {
            if (!read_value(input, common.randomMoveCount, token, error))
                return false;
        }
        else if (token == "random_move_like_apery")
        {
            if (!read_value(input, common.randomMoveLikeApery, token, error))
                return false;
        }
        else if (token == "random_multi_pv")
        {
            if (!read_value(input, common.randomMultiPv, token, error))
                return false;
        }
        else if (token == "random_multi_pv_diff")
        {
            if (!read_value(input, common.randomMultiPvDiff, token, error))
                return false;
        }
        else if (token == "random_multi_pv_depth")
        {
            if (!read_value(input, common.randomMultiPvDepth, token, error))
                return false;
        }
        else if (token == "write_min_ply")
        {
            if (!read_value(input, common.writeMinPly, token, error))
                return false;
        }
        else if (token == "write_max_ply")
        {
            if (!read_value(input, common.writeMaxPly, token, error))
                return false;
        }
        else if (token == "book")
        {
            if (!read_value(input, common.book, token, error))
                return false;
        }
        else if (token == "keep_draws")
        {
            if (!read_keep_draws(input, common.keepDraws, common.keepDrawsManifest, error))
                return false;
        }
        else if (token == "adjudicate_draws_by_score")
        {
            if (!read_bool(input, common.adjudicateDrawsByScore, token, error))
                return false;
        }
        else if (token == "adjudicate_draws_by_insufficient_material")
        {
            if (!read_bool(input, common.adjudicateInsufficient, token, error))
                return false;
        }
        else if (token == "adjudicate_resignations")
        {
            if (!read_bool(input, params.adjudicateResignations, token, error))
                return false;
        }
        else if (token == "filter_captures")
        {
            if (!read_bool(input, common.filterCaptures, token, error))
                return false;
        }
        else if (token == "filter_checks")
        {
            if (!read_bool(input, common.filterChecks, token, error))
                return false;
        }
        else if (token == "filter_promotions")
        {
            if (!read_bool(input, common.filterPromotions, token, error))
                return false;
        }
        else if (token == "seed" || token == "generation_seed")
        {
            u64 seed = 0;
            if (!read_u64_value(input, seed, token, error) || seed == 0)
            {
                if (error.empty())
                    error = "Atomic V3 generation seed must be a nonzero decimal uint64";
                return false;
            }
            common.seedText     = std::to_string(seed);
            generationSeedSeen = true;
        }
        else if (token == "split_seed")
        {
            if (!read_u64_value(input, params.splitSeed, token, error))
                return false;
            splitSeedSeen = true;
        }
        else if (token == "validation_threshold_u64")
        {
            if (!read_u64_value(input, params.validationThreshold, token, error))
                return false;
            thresholdSeen = true;
        }
        else if (token == "network_sha256")
        {
            if (!read_value(input, params.networkSha256, token, error))
                return false;
            networkPinSeen = true;
        }
        else if (token == "book_sha256")
        {
            if (!read_value(input, params.bookSha256, token, error))
                return false;
            bookPinSeen = true;
        }
        else if (token == "max_games")
        {
            if (!read_u64_value(input, params.maximumGames, token, error))
                return false;
        }
        else if (token == "set_recommended_uci_options")
            common.setRecommendedUciOptionsSeen = true;
        else
        {
            error = "Unknown generate_atomic_v3_chunk option " + token;
            return false;
        }
    }

    if (!depthMaxSeen)
        common.searchDepthMax = common.searchDepthMin;
    common.randomMultiPvDepth = std::max(common.searchDepthMax, common.randomMultiPvDepth);
    if (params.trainCount <= std::numeric_limits<u64>::max() - params.validationCount)
        common.count = params.trainCount + params.validationCount;
    else
        common.count = 0;

    const bool badPin = !networkPinSeen || !normalize_sha256_pin(params.networkSha256)
                     || !bookPinSeen
                     || (!common.book.empty() && !normalize_sha256_pin(params.bookSha256))
                     || (common.book.empty() && params.bookSha256 != "none");
    const bool invalid =
      params.trainCount == 0 || params.validationCount == 0 || common.count == 0
      || params.outputPrefix.empty() || !generationSeedSeen || !splitSeedSeen || !thresholdSeen
      || params.validationThreshold == 0
      || params.validationThreshold == std::numeric_limits<u64>::max() || badPin
      || params.maximumGames == 0 || common.searchDepthMin <= 0
      || common.searchDepthMax < common.searchDepthMin || common.searchDepthMax >= MAX_PLY
      || common.randomMultiPvDepth < 0 || common.randomMultiPvDepth >= MAX_PLY
      || common.evalLimit <= 0 || common.evalLimit > std::numeric_limits<i16>::max()
      || common.evalDiffLimit < 0 || common.writeMinPly < 0
      || common.writeMaxPly <= common.writeMinPly || common.writeMaxPly > MaximumGeneratedPly
      || common.randomMoveMinPly < -1 || common.randomMoveMaxPly < 0
      || common.randomMoveMaxPly > MaximumGeneratedPly
      || (common.randomMoveMinPly != -1
          && common.randomMoveMaxPly < common.randomMoveMinPly)
      || common.randomMoveCount < 0 || common.randomMoveCount > MaximumGeneratedPly
      || common.randomMoveLikeApery < 0 || common.randomMultiPv < 0
      || common.randomMultiPv > int(MAX_MOVES) || common.randomMultiPvDiff < 0
      || common.keepDraws != 1.0 || common.keepDrawsManifest != "1"
      || common.adjudicateDrawsByScore || params.adjudicateResignations;
    if (invalid)
    {
        error = "Invalid generate_atomic_v3_chunk parameter or release-policy value";
        return false;
    }
    return true;
}

std::optional<AtomicV3StopReason> atomic_v3_stop_reason(Atomic::Termination termination) {
    switch (termination)
    {
    case Atomic::Termination::AtomicExplosion :
        return AtomicV3StopReason::ATOMIC_EXPLOSION;
    case Atomic::Termination::Checkmate :
        return AtomicV3StopReason::CHECKMATE;
    case Atomic::Termination::Stalemate :
        return AtomicV3StopReason::STALEMATE;
    case Atomic::Termination::InsufficientMaterial :
        return AtomicV3StopReason::INSUFFICIENT_MATERIAL;
    case Atomic::Termination::FiftyMoveRule :
        return AtomicV3StopReason::FIFTY_MOVE_RULE;
    case Atomic::Termination::ThreefoldRepetition :
        return AtomicV3StopReason::THREEFOLD_REPETITION;
    case Atomic::Termination::Ongoing :
        return std::nullopt;
    }
    return std::nullopt;
}

class AtomicV3FeatureAuditIndex {
   public:
    AtomicV3FeatureAuditIndex(const std::filesystem::path& directory,
                              std::string_view             role,
                              bool                         membershipEnabled_) :
        rawPath(directory / (std::string(role) + "-feature-input-keys.raw.partial")),
        sortedPath(directory / (std::string(role) + "-feature-input-keys.sorted.partial")),
        label(role),
        membershipEnabled(membershipEnabled_) {}

    ~AtomicV3FeatureAuditIndex() { (void) abort(); }

    DataResult append(const AtomicV3FeatureInputKey& key) {
        if (finalized)
            return DataResult::failure(DataError::SINK_CLOSED,
                                       "Cannot append a finalized Atomic V3 " + label
                                         + " feature index");
        if (!rawFile)
        {
            rawFile = open_private_exclusive(rawPath);
            if (!rawFile)
                return DataResult::failure(errno == EEXIST ? DataError::OUTPUT_EXISTS
                                                           : DataError::OPEN_FAILED,
                                           "Cannot create private Atomic V3 " + label
                                             + " feature index");
        }
        if (std::fwrite(key.data(), 1, key.size(), rawFile) != key.size())
            return DataResult::failure(DataError::WRITE_FAILED,
                                       "Cannot write private Atomic V3 " + label
                                         + " feature index");
        ++observations;
        return DataResult::success();
    }

    DataResult finalize() {
        if (finalized)
            return DataResult::success();
        if (!rawFile || observations == 0)
            return DataResult::failure(DataError::EMPTY_DATASET,
                                       "Atomic V3 " + label + " feature index cannot be empty");
        if (DataResult synced =
              sync_private_file(rawFile, "Atomic V3 " + label + " feature index");
            !synced)
            return synced;
        if (std::fclose(rawFile) != 0)
        {
            rawFile = nullptr;
            return DataResult::failure(DataError::CLOSE_FAILED,
                                       "Cannot close private Atomic V3 " + label
                                         + " feature index");
        }
        rawFile = nullptr;

        if (DataResult sorted = sort_unique_atomic_v3_keys(
              rawPath, sortedPath, observations, false, uniqueRecords);
            !sorted)
            return sorted;

        if (!membershipEnabled)
        {
            finalized = true;
            return DataResult::success();
        }

        constexpr u64 MaximumBloomBytes = u64(512) * 1024 * 1024;
        const u64 desiredBits = uniqueRecords > std::numeric_limits<u64>::max() / 10
                                ? std::numeric_limits<u64>::max()
                                : uniqueRecords * 10;
        bloomBits = std::max<u64>(8, std::min<u64>(MaximumBloomBytes * 8, desiredBits));
        bloom.assign(std::size_t((bloomBits + 7) / 8), 0);
        sparse.reserve(std::size_t(uniqueRecords / SparseStride + 1));

        std::ifstream input(sortedPath, std::ios::binary);
        if (!input)
            return DataResult::failure(DataError::OPEN_FAILED,
                                       "Cannot open sorted Atomic V3 " + label
                                         + " feature index");
        for (u64 ordinal = 0; ordinal < uniqueRecords; ++ordinal)
        {
            AtomicV3FeatureInputKey key{};
            input.read(reinterpret_cast<char*>(key.data()), std::streamsize(key.size()));
            if (input.gcount() != std::streamsize(key.size()))
                return DataResult::failure(DataError::READ_FAILED,
                                           "Cannot scan sorted Atomic V3 " + label
                                             + " feature index");
            if (ordinal % SparseStride == 0)
                sparse.push_back({key, ordinal});
            bloom_add(key);
        }
        char trailing = 0;
        if (input.read(&trailing, 1) || !input.eof())
            return DataResult::failure(DataError::FILE_SIZE_MISMATCH,
                                       "Sorted Atomic V3 " + label
                                         + " feature index has trailing bytes");

        sortedStream.open(sortedPath, std::ios::binary);
        if (!sortedStream)
            return DataResult::failure(DataError::OPEN_FAILED,
                                       "Cannot retain sorted Atomic V3 " + label
                                         + " feature index");
        finalized = true;
        return DataResult::success();
    }

    DataResult contains(const AtomicV3FeatureInputKey& key, bool& present) {
        present = false;
        if (!finalized)
            return DataResult::failure(DataError::SINK_CLOSED,
                                        "Atomic V3 feature index was queried before finalization");
        if (!membershipEnabled)
            return DataResult::failure(DataError::SINK_CLOSED,
                                       "Atomic V3 audit-only feature index cannot be queried");
        if (!bloom_maybe_contains(key) || sparse.empty())
            return DataResult::success();

        auto it = std::upper_bound(
          sparse.begin(), sparse.end(), key,
          [](const AtomicV3FeatureInputKey& value, const SparseEntry& entry) {
              return value < entry.first;
          });
        if (it == sparse.begin())
            return DataResult::success();
        --it;
        const u64 count = std::min<u64>(SparseStride, uniqueRecords - it->ordinal);
        std::vector<AtomicV3FeatureInputKey> block(static_cast<std::size_t>(count));
        sortedStream.clear();
        sortedStream.seekg(std::streamoff(it->ordinal * 32), std::ios::beg);
        sortedStream.read(reinterpret_cast<char*>(block.data()), std::streamsize(count * 32));
        if (sortedStream.gcount() != std::streamsize(count * 32))
            return DataResult::failure(DataError::READ_FAILED,
                                       "Cannot query sorted Atomic V3 " + label
                                         + " feature index");
        present = std::binary_search(block.begin(), block.end(), key);
        return DataResult::success();
    }

    DataResult abort() {
        DataResult first = DataResult::success();
        sortedStream.close();
        if (rawFile)
        {
            if (std::fclose(rawFile) != 0)
                first = DataResult::failure(DataError::CLOSE_FAILED,
                                            "Cannot close Atomic V3 " + label
                                              + " feature staging file");
            rawFile = nullptr;
        }
        for (const auto& path : {rawPath, sortedPath})
        {
            std::error_code ec;
            std::filesystem::remove(path, ec);
            if (ec && first)
                first = DataResult::failure(DataError::ABORT_FAILED,
                                            "Cannot remove Atomic V3 " + label
                                              + " feature staging file: "
                                              + ec.message());
        }
        finalized = false;
        return first;
    }

    const std::filesystem::path& sorted_path() const noexcept { return sortedPath; }
    u64 unique_records() const noexcept { return uniqueRecords; }

   private:
    static constexpr u64 SparseStride = 4096;
    struct SparseEntry {
        AtomicV3FeatureInputKey first{};
        u64                     ordinal = 0;
    };

    std::pair<u64, u64> bloom_hashes(const AtomicV3FeatureInputKey& key) const noexcept {
        u64 first  = read_u64_le_bytes(key.data());
        u64 second = read_u64_le_bytes(key.data() + 8) | 1ULL;
        return {first, second};
    }

    void bloom_add(const AtomicV3FeatureInputKey& key) noexcept {
        const auto [first, second] = bloom_hashes(key);
        for (u64 i = 0; i < 6; ++i)
        {
            const u64 bit = (first + i * second) % bloomBits;
            bloom[std::size_t(bit >> 3)] |= u8(1U << (bit & 7));
        }
    }

    bool bloom_maybe_contains(const AtomicV3FeatureInputKey& key) const noexcept {
        const auto [first, second] = bloom_hashes(key);
        for (u64 i = 0; i < 6; ++i)
        {
            const u64 bit = (first + i * second) % bloomBits;
            if (!(bloom[std::size_t(bit >> 3)] & u8(1U << (bit & 7))))
                return false;
        }
        return true;
    }

    std::filesystem::path       rawPath;
    std::filesystem::path       sortedPath;
    std::string                 label;
    std::FILE*                  rawFile = nullptr;
    std::ifstream               sortedStream;
    std::vector<u8>             bloom;
    std::vector<SparseEntry>    sparse;
    u64                         bloomBits    = 0;
    u64                         observations = 0;
    u64                         uniqueRecords = 0;
    bool                        finalized = false;
    bool                        membershipEnabled = false;
};

struct AtomicV3RolePaths {
    std::filesystem::path stagedDataset;
    std::filesystem::path stagedManifest;
    std::filesystem::path stagedLedger;
    std::filesystem::path finalDataset;
    std::filesystem::path finalManifest;
    std::filesystem::path finalLedger;
};

class AtomicV3RoleOutput {
   public:
    AtomicV3RoleOutput(AtomicV3RolePaths paths_,
                       AtomicV3DatasetRole role_,
                       u64 splitSeed,
        u64 validationThreshold,
        u32 expectedMaximumPly) :
        paths(std::move(paths_)),
        dataset(paths.stagedDataset),
        ledger(paths.stagedLedger.parent_path(), paths.stagedLedger.filename().string(), role_,
               splitSeed, validationThreshold, expectedMaximumPly),
        maximumPly(expectedMaximumPly) {}

    ~AtomicV3RoleOutput() { (void) abort(); }

    DataResult append(const AtomicV3Trajectory& trajectory) {
        if (DataResult valid = validate_atomic_v3_trajectory(trajectory, maximumPly); !valid)
            return valid;
        const u64 firstRecord = dataset.records_written();
        for (const auto& sample : trajectory.samples)
            if (DataResult written = dataset.append(sample); !written)
                return written;
        if (DataResult written = ledger.append(trajectory, firstRecord); !written)
            return written;
        if (trajectory.terminalResult == 0)
            drawRecords += u64(trajectory.samples.size());
        return DataResult::success();
    }

    DataResult finalize(const AtomicV3GeneratorParams& params,
                        const AuthenticatedFile&       network,
                        bool                           bookIsFile,
                        const AuthenticatedFile&       book,
                        u32                            threads,
                        u64                            hashMb,
                        std::string_view               engineVersion,
                        u64                            requestedRecords) {
        if (dataset.records_written() != requestedRecords || ledger.records() != requestedRecords)
            return DataResult::failure(DataError::RECORD_COUNT_OUT_OF_RANGE,
                                       "Atomic V3 role did not reach its requested record count");
        if (DataResult result = dataset.finalize(); !result)
            return result;
        datasetFinalized = true;

        AtomicBinV2Manifest manifest;
        manifest.manifestPath = paths.stagedManifest;
#ifdef ATOMIC_DATA_GENERATOR_GIT_SHA
        manifest.engineCommit = stringify(ATOMIC_DATA_GENERATOR_GIT_SHA);
#else
        manifest.engineCommit = "unknown";
#endif
        manifest.engineVersion = std::string(engineVersion);
        manifest.networkPath   = network.path;
        manifest.networkSha256 = network.sha256;
        manifest.bookIsFile    = bookIsFile;
        if (bookIsFile)
        {
            manifest.bookPath   = book.path;
            manifest.bookSha256 = book.sha256;
        }
        manifest.resolvedSeed = ReplayablePRNG::resolve(params.generation.seedText);
        manifest.atomic960    = params.generation.atomic960;
        manifest.threads      = threads;
        manifest.hashMb       = hashMb;

        const auto& source = params.generation;
        auto&       options = manifest.options;
        options.searchDepthMin                 = source.searchDepthMin;
        options.searchDepthMax                 = source.searchDepthMax;
        options.nodes                          = source.nodes;
        options.requestedRecords               = requestedRecords;
        options.recordsPerShard                = requestedRecords;
        options.evalLimit                      = source.evalLimit;
        options.evalDiffLimit                  = source.evalDiffLimit;
        options.randomMoveMinPly               = source.randomMoveMinPly;
        options.randomMoveMaxPly               = source.randomMoveMaxPly;
        options.randomMoveCount                = source.randomMoveCount;
        options.randomMoveLikeApery            = source.randomMoveLikeApery;
        options.randomMultiPv                   = source.randomMultiPv;
        options.randomMultiPvDiff               = source.randomMultiPvDiff;
        options.randomMultiPvDepth              = source.randomMultiPvDepth;
        options.writeMinPly                     = source.writeMinPly;
        options.writeMaxPly                     = source.writeMaxPly;
        options.keepDraws                       = source.keepDrawsManifest;
        options.adjudicateDrawsByScore          = false;
        options.adjudicateInsufficient          = source.adjudicateInsufficient;
        options.filterCaptures                  = source.filterCaptures;
        options.filterChecks                    = source.filterChecks;
        options.filterPromotions                = source.filterPromotions;
        options.randomFileName                  = false;
        options.setRecommendedUciOptionsSeen    = source.setRecommendedUciOptionsSeen;
        manifest.records                        = dataset.records_written();
        manifest.draws                          = drawRecords;
        manifest.shards.push_back({dataset.output_path(), 0, dataset.records_written(),
                                   dataset.finalized_size(), dataset.sha256_hex()});

        if (DataResult result = write_atomic_bin_v2_manifest(manifest); !result)
            return result;
        manifestWritten = true;
        if (DataResult result =
              sha256_file(paths.stagedManifest, manifestSha256, manifestBytes);
            !result)
            return result;

        if (DataResult result =
              ledger.finalize(paths.stagedLedger, manifestSha256, ledgerMetadata);
            !result)
            return result;
        return DataResult::success();
    }

    DataResult abort() {
        if (aborted)
            return DataResult::success();
        DataResult first = ledger.abort();
        if (manifestWritten)
        {
            std::error_code ec;
            std::filesystem::remove(paths.stagedManifest, ec);
            if (ec && first)
                first = DataResult::failure(DataError::ABORT_FAILED,
                                            "Cannot remove staged Atomic V3 manifest: "
                                              + ec.message());
        }
        DataResult datasetResult = datasetFinalized ? dataset.remove_finalized_owned()
                                                    : dataset.abort();
        if (!datasetResult && first)
            first = datasetResult;
        aborted = true;
        return first;
    }

    const AtomicV3RolePaths& output_paths() const noexcept { return paths; }
    const std::string& manifest_sha256() const noexcept { return manifestSha256; }
    const AtomicV3LedgerMetadata& ledger_metadata() const noexcept { return ledgerMetadata; }
    const AtomicBinV2Sink& dataset_sink() const noexcept { return dataset; }
    u64 manifest_size() const noexcept { return manifestBytes; }

   private:
    AtomicV3RolePaths               paths;
    AtomicBinV2Sink                 dataset;
    AtomicV3TrajectoryLedgerStager  ledger;
    AtomicV3LedgerMetadata          ledgerMetadata;
    u64                             drawRecords = 0;
    u64                             manifestBytes = 0;
    std::string                     manifestSha256;
    bool                            datasetFinalized = false;
    bool                            manifestWritten  = false;
    bool                            aborted          = false;
    u32                             maximumPly       = 0;
};

class AtomicV3Generator {
   public:
    AtomicV3Generator(const AtomicV3GeneratorParams& params_,
                       ThreadPool&                    threads_,
                       AtomicV3RoleOutput&             train_,
                       AtomicV3RoleOutput&             validation_,
                       std::vector<std::string>        openings,
                       u64                             resolvedSeed,
                       const std::filesystem::path&    privateDirectory,
                       u64                             hashMb_) :
        params(params_),
        threads(threads_),
        train(train_),
        validation(validation_),
        openingPositions(std::move(openings)),
        generationSeed(resolvedSeed),
        trainFeatureIndex(privateDirectory, "train", true),
        validationFeatureIndex(privateDirectory, "validation", false),
        hashMb(hashMb_) {
        if (!params.generation.book.empty())
        {
            ReplayablePRNG shuffle(resolvedSeed ^ 0xA70C03B00C5EEDULL);
            for (usize i = 0; i < openingPositions.size(); ++i)
                std::swap(openingPositions[i],
                          openingPositions[i + shuffle.rand(openingPositions.size() - i)]);
        }
    }

    bool run(std::string& error) {
        threads.wait_for_search_finished();
        threads.clear();
        threads.stop = false;

        const usize workerCount = threads.size();
        const usize perWorkerMb = usize(std::max<u64>(1, hashMb / workerCount));
        privateTts.reserve(workerCount);
        privateHistories.reserve(workerCount);
        workerDone.assign(workerCount, false);
        pendingLimit = std::max<usize>(2, workerCount * 2);
        for (usize i = 0; i < workerCount; ++i)
        {
            privateTts.push_back(std::make_unique<TranspositionTable>());
            privateTts.back()->resize(perWorkerMb, threads);
            privateHistories.push_back(std::make_unique<SharedHistories>(1));
        }

        for (usize i = 0; i < workerCount; ++i)
        {
            Search::Worker* worker = (threads.begin() + std::ptrdiff_t(i))->get()->worker.get();
            threads.run_on_thread(i, [this, i, worker, workerCount]() {
                produce_games(i, workerCount, *worker, *privateTts[i], *privateHistories[i]);
            });
        }

        u64 nextGameId = 0;
        while (!cancelled.load(std::memory_order_relaxed))
        {
            CompletedGame game;
            {
                std::unique_lock<std::mutex> lock(pendingMutex);
                pendingCv.wait(lock, [&]() {
                    return pendingGames.find(nextGameId) != pendingGames.end()
                        || std::all_of(workerDone.begin(), workerDone.end(), [](bool done) {
                               return done;
                           });
                });
                auto found = pendingGames.find(nextGameId);
                if (found == pendingGames.end())
                {
                    failure = "Atomic V3 generator exhausted max_games before both role targets";
                    break;
                }
                game = std::move(found->second);
                pendingGames.erase(found);
            }
            pendingCv.notify_all();
            gamesGenerated = nextGameId + 1;
            if (!game.error.empty())
            {
                failure = std::move(game.error);
                break;
            }
            if (!game.trajectory.samples.empty())
                commit_trajectory(game.trajectory);
            if (!failure.empty() || (trainWritten == params.trainCount
                                     && validationWritten == params.validationCount))
                break;
            if (++nextGameId >= params.maximumGames)
            {
                failure = "Atomic V3 generator exhausted max_games before both role targets";
                break;
            }
            advance_atomic_v3_commit_game_id(nextCommitGameId, pendingCv, nextGameId);
        }

        cancelled.store(true, std::memory_order_relaxed);
        threads.stop = true;
        pendingCv.notify_all();
        for (usize i = 0; i < workerCount; ++i)
            threads.wait_on_thread(i);
        threads.stop = false;
        privateTts.clear();
        privateHistories.clear();
        if (!failure.empty())
        {
            error = failure;
            return false;
        }
        if (trainWritten != params.trainCount || validationWritten != params.validationCount)
        {
            error = "Atomic V3 generator stopped before both role targets were complete";
            return false;
        }
        if (DataResult finalized = validationFeatureIndex.finalize(); !finalized)
        {
            error = finalized.message;
            return false;
        }
        if (DataResult disjoint = prove_feature_sets_disjoint(); !disjoint)
        {
            error = disjoint.message;
            return false;
        }
        return true;
    }

    u64 train_records() const noexcept { return trainWritten; }
    u64 validation_records() const noexcept { return validationWritten; }
    u64 games_generated() const noexcept { return gamesGenerated; }

    DataResult abort_audits() {
        DataResult first = trainFeatureIndex.abort();
        DataResult second = validationFeatureIndex.abort();
        return !first ? first : second;
    }

   private:
    const AtomicV3GeneratorParams& params;
    ThreadPool&                    threads;
    AtomicV3RoleOutput&             train;
    AtomicV3RoleOutput&             validation;
    std::vector<std::string>        openingPositions;
    u64                             generationSeed;
    AtomicV3FeatureAuditIndex       trainFeatureIndex;
    AtomicV3FeatureAuditIndex       validationFeatureIndex;
    u64                             hashMb;
    u64                             trainWritten      = 0;
    u64                             validationWritten = 0;
    u64                             gamesGenerated    = 0;
    std::string                     failure;
    struct CompletedGame {
        u64                gameId = 0;
        AtomicV3Trajectory trajectory;
        std::string        error;
    };
    std::vector<std::unique_ptr<TranspositionTable>> privateTts;
    std::vector<std::unique_ptr<SharedHistories>>     privateHistories;
    std::map<u64, CompletedGame>                     pendingGames;
    std::vector<bool>                                workerDone;
    std::mutex                                       pendingMutex;
    std::condition_variable                          pendingCv;
    std::atomic_bool                                 cancelled{false};
    std::atomic<u64>                                 nextCommitGameId{0};
    usize                                            pendingLimit = 0;
    const std::chrono::steady_clock::time_point started = std::chrono::steady_clock::now();

    void fail(std::string message) {
        if (failure.empty())
            failure = std::move(message);
        cancelled.store(true, std::memory_order_relaxed);
    }

    DataResult prove_feature_sets_disjoint() const {
        std::ifstream trainInput(trainFeatureIndex.sorted_path(), std::ios::binary);
        std::ifstream validationInput(validationFeatureIndex.sorted_path(), std::ios::binary);
        if (!trainInput || !validationInput)
            return DataResult::failure(DataError::OPEN_FAILED,
                                       "Cannot open final Atomic V3 feature split audit");

        AtomicV3FeatureInputKey trainKey{};
        AtomicV3FeatureInputKey validationKey{};
        u64 trainRead = 0;
        u64 validationRead = 0;
        const auto readKey = [](std::ifstream& input,
                                AtomicV3FeatureInputKey& key,
                                u64&                     count) {
            input.read(reinterpret_cast<char*>(key.data()), std::streamsize(key.size()));
            if (input.gcount() == 0 && input.eof())
                return 0;
            if (input.gcount() != std::streamsize(key.size()))
                return -1;
            ++count;
            return 1;
        };

        int trainStatus = readKey(trainInput, trainKey, trainRead);
        int validationStatus = readKey(validationInput, validationKey, validationRead);
        while (trainStatus > 0 && validationStatus > 0)
        {
            if (trainKey == validationKey)
                return DataResult::failure(
                  DataError::FILE_IDENTITY_MISMATCH,
                  "Atomic V3 train/validation feature-input sets overlap after final sorting");
            if (trainKey < validationKey)
                trainStatus = readKey(trainInput, trainKey, trainRead);
            else
                validationStatus = readKey(validationInput, validationKey, validationRead);
        }
        while (trainStatus > 0)
            trainStatus = readKey(trainInput, trainKey, trainRead);
        while (validationStatus > 0)
            validationStatus = readKey(validationInput, validationKey, validationRead);
        if (trainStatus < 0 || validationStatus < 0
            || trainRead != trainFeatureIndex.unique_records()
            || validationRead != validationFeatureIndex.unique_records())
            return DataResult::failure(DataError::READ_FAILED,
                                       "Final Atomic V3 feature split audit is truncated");
        return DataResult::success();
    }

    u64 game_seed(u64 gameId) const noexcept {
        constexpr char Domain[] = "atomic-v3-game-seed-v1\0";
        Sha256 hash;
        hash.update(Domain, sizeof(Domain) - 1);
        std::array<u8, 16> wire{};
        for (unsigned i = 0; i < 8; ++i)
        {
            wire[i]     = u8(generationSeed >> (8 * i));
            wire[8 + i] = u8(gameId >> (8 * i));
        }
        hash.update(wire.data(), wire.size());
        return read_u64_le_bytes(hash.digest().data());
    }

    const std::string& opening_for(u64 gameId) const {
        return openingPositions[usize(gameId % openingPositions.size())];
    }

    std::vector<u8> random_move_flags(ReplayablePRNG& rng) const {
        const auto& source = params.generation;
        std::vector<u8> flags(usize(source.randomMoveMaxPly + source.randomMoveCount), 0);
        std::vector<int> candidates;
        for (int ply = std::max(source.randomMoveMinPly - 1, 0);
             ply < source.randomMoveMaxPly; ++ply)
            candidates.push_back(ply);
        const int count = std::min(source.randomMoveCount, int(candidates.size()));
        for (int i = 0; i < count; ++i)
        {
            const usize selected = usize(i) + usize(rng.rand(candidates.size() - usize(i)));
            std::swap(candidates[usize(i)], candidates[selected]);
            flags[usize(candidates[usize(i)])] = 1;
        }
        return flags;
    }

    std::optional<Move> choose_random_move(Search::Worker& worker,
                                           Position&       position,
                                           std::vector<u8>& flags,
                                           int             ply,
                                           int&            randomMovesMade,
                                           ReplayablePRNG&  rng,
                                           TranspositionTable& privateTt,
                                           SharedHistories&    privateHistory) {
        const auto& source = params.generation;
        const bool selected =
          (source.randomMoveMinPly != -1 && usize(ply) < flags.size() && flags[usize(ply)])
          || (source.randomMoveMinPly == -1 && randomMovesMade < source.randomMoveCount);
        if (!selected)
            return std::nullopt;

        ++randomMovesMade;
        if (source.randomMultiPv == 0)
        {
            const MoveList<LEGAL> moves(position);
            if (moves.size() == 0)
                return std::nullopt;
            if (source.randomMoveLikeApery == 0
                || rng.rand(u64(source.randomMoveLikeApery)) != 0)
                return *(moves.begin() + rng.rand(moves.size()));

            std::vector<Move> kingMoves;
            for (Move move : moves)
                if (type_of(position.moved_piece(move)) == KING)
                    kingMoves.push_back(move);
            if (kingMoves.empty())
                return *(moves.begin() + rng.rand(moves.size()));
            const Move kingMove = kingMoves[rng.rand(kingMoves.size())];
            if (rng.rand(2) == 0)
                flags.insert(flags.begin() + ply + 1, 1);
            return kingMove;
        }

        Search::TrainingSearchRequest request;
        request.mode    = Search::TrainingSearchMode::FixedDepth;
        request.depth   = source.randomMultiPvDepth;
        request.multiPV = usize(source.randomMultiPv);
        request.transpositionTable = &privateTt;
        request.sharedHistories     = &privateHistory;
        const auto result = worker.training_search(position, request);
        if (result.lines.empty())
            return std::nullopt;
        usize candidates = result.lines.size();
        for (usize i = 1; i < candidates; ++i)
            if (std::int64_t(result.lines.front().value)
                > std::int64_t(result.lines[i].value)
                    + std::int64_t(source.randomMultiPvDiff))
            {
                candidates = i;
                break;
            }
        const auto& line = result.lines[rng.rand(candidates)];
        return line.pv.empty() ? std::optional<Move>{} : std::optional<Move>{line.pv[0]};
    }

    void seal_trajectory(AtomicV3Trajectory& trajectory,
                         const Atomic::Outcome& outcome,
                         AtomicV3StopReason reason) const {
        trajectory.stopReason = reason;
        trajectory.terminalResult = !outcome.winner ? 0 : (*outcome.winner == WHITE ? 1 : -1);
        for (auto& sample : trajectory.samples)
            sample.result = trajectory.terminalResult == 0
                            ? 0
                            : (side_to_move_from_fen(sample.fen) == WHITE
                                 ? trajectory.terminalResult
                                 : -trajectory.terminalResult);
    }

    bool commit_trajectory(AtomicV3Trajectory& trajectory) {
        if (trajectory.samples.empty() || trajectory.playedMoves.empty())
            return false;

        const auto group = atomic_v3_split_group_id(trajectory.rootPosition,
                                                     trajectory.atomic960,
                                                     trajectory.playedMoves);
        const auto role = atomic_v3_partition_role(params.splitSeed,
                                                    params.validationThreshold, group);
        // Freeze train first. Validation is not retained until the exact train
        // feature-input set has been externally sorted; this makes train win
        // every collision without unbounded candidate buffering.
        if (trainWritten < params.trainCount && role != AtomicV3DatasetRole::TRAIN)
            return false;
        if (trainWritten == params.trainCount && role != AtomicV3DatasetRole::VALIDATION)
            return false;

        u64* written = role == AtomicV3DatasetRole::TRAIN ? &trainWritten : &validationWritten;
        const u64 target = role == AtomicV3DatasetRole::TRAIN ? params.trainCount
                                                               : params.validationCount;
        if (*written >= target)
            return false;
        const u64 remaining = target - *written;
        std::vector<AtomicV3FeatureInputKey> retainedValidationKeys;
        if (role == AtomicV3DatasetRole::VALIDATION)
        {
            std::vector<TrainingDataSample> decontaminated;
            decontaminated.reserve(trajectory.samples.size());
            retainedValidationKeys.reserve(trajectory.samples.size());
            for (auto& sample : trajectory.samples)
            {
                AtomicV3FeatureInputKey key{};
                if (DataResult keyed = atomic_v3_feature_input_key(sample, key); !keyed)
                {
                    fail(keyed.message);
                    return true;
                }
                bool overlapsTrain = false;
                if (DataResult queried = trainFeatureIndex.contains(key, overlapsTrain); !queried)
                {
                    fail(queried.message);
                    return true;
                }
                if (!overlapsTrain)
                {
                    decontaminated.push_back(std::move(sample));
                    retainedValidationKeys.push_back(key);
                }
            }
            trajectory.samples = std::move(decontaminated);
            if (trajectory.samples.empty())
                return false;
        }
        if (u64(trajectory.samples.size()) > remaining)
        {
            trajectory.samples.resize(usize(remaining));
            retainedValidationKeys.resize(usize(remaining));
        }

        if (role == AtomicV3DatasetRole::TRAIN)
            for (const auto& sample : trajectory.samples)
            {
                AtomicV3FeatureInputKey key{};
                if (DataResult keyed = atomic_v3_feature_input_key(sample, key); !keyed)
                {
                    fail(keyed.message);
                    return true;
                }
                if (DataResult indexed = trainFeatureIndex.append(key); !indexed)
                {
                    fail(indexed.message);
                    return true;
                }
            }

        if (role == AtomicV3DatasetRole::VALIDATION)
            for (const auto& key : retainedValidationKeys)
                if (DataResult audited = validationFeatureIndex.append(key); !audited)
                {
                    fail(audited.message);
                    return true;
                }

        AtomicV3RoleOutput& output = role == AtomicV3DatasetRole::TRAIN ? train : validation;
        if (DataResult result = output.append(trajectory); !result)
        {
            fail(result.message);
            return true;
        }
        *written += u64(trajectory.samples.size());
        if (role == AtomicV3DatasetRole::TRAIN && trainWritten == params.trainCount)
        {
            if (DataResult indexed = trainFeatureIndex.finalize(); !indexed)
            {
                fail(indexed.message);
                return true;
            }
        }

        const u64 done = trainWritten + validationWritten;
        if (done % ReportEvery == 0 || (trainWritten == params.trainCount
                                        && validationWritten == params.validationCount))
        {
            const auto elapsed = std::max(
              1.0, std::chrono::duration<double>(std::chrono::steady_clock::now() - started)
                     .count());
            std::cout << "info string Atomic V3 data " << done << "/"
                      << params.generation.count << " records, "
                      << u64(double(done) / elapsed) << " records/s" << std::endl;
        }
        return true;
    }

    CompletedGame generate_game(u64                 gameId,
                                Search::Worker&     worker,
                                TranspositionTable& privateTt,
                                SharedHistories&    privateHistory) {
        CompletedGame completed;
        completed.gameId = gameId;
        const auto& source = params.generation;
        std::vector<StateInfo> states(usize(source.writeMaxPly));
        privateTt.new_search();
        ReplayablePRNG rng(game_seed(gameId));

        StateInfo rootState{};
        Position  position;
        if (const auto setError = position.set(opening_for(gameId), source.atomic960, &rootState))
        {
            completed.error = std::string("Cannot set Atomic V3 opening FEN: ") + setError->what();
            return completed;
        }

        auto& trajectory = completed.trajectory;
        trajectory.rootFen                = position.fen();
        trajectory.atomic960              = source.atomic960;
        trajectory.adjudicateInsufficient = source.adjudicateInsufficient;
        if (DataResult encoded = encode_atomic_bin_v2_position(position, trajectory.rootPosition);
            !encoded)
        {
            completed.error = encoded.message;
            return completed;
        }

        std::vector<u8> flags = random_move_flags(rng);
        int randomMovesMade   = 0;
        for (int ply = 0; !cancelled.load(std::memory_order_relaxed); ++ply)
        {
            const auto outcome = Atomic::outcome(position, true, 0);
            const bool ignoredInsufficient =
              outcome.termination == Atomic::Termination::InsufficientMaterial
              && !source.adjudicateInsufficient;
            if (outcome.terminal() && !ignoredInsufficient)
            {
                const auto reason = atomic_v3_stop_reason(outcome.termination);
                if (!reason)
                {
                    completed.error = "Atomic V3 terminal outcome has no frozen stop reason";
                    return completed;
                }
                seal_trajectory(trajectory, outcome, *reason);
                return completed;
            }
            if (ply >= source.writeMaxPly)
            {
                seal_trajectory(trajectory, Atomic::Outcome{},
                                AtomicV3StopReason::MAXIMUM_PLY_DRAW);
                return completed;
            }

            const int depth = source.searchDepthMin
                            + int(rng.rand(u64(source.searchDepthMax - source.searchDepthMin + 1)));
            Search::TrainingSearchRequest evalRequest;
            evalRequest.mode = position.atomic_in_check(position.side_to_move())
                                 ? Search::TrainingSearchMode::Quiescence
                                 : Search::TrainingSearchMode::Evaluate;
            evalRequest.transpositionTable = &privateTt;
            evalRequest.sharedHistories     = &privateHistory;
            const auto evalResult = worker.training_search(position, evalRequest);
            Search::TrainingSearchRequest qRequest;
            qRequest.mode               = Search::TrainingSearchMode::Quiescence;
            qRequest.transpositionTable = &privateTt;
            qRequest.sharedHistories     = &privateHistory;
            const auto qResult = worker.training_search(position, qRequest);
            Search::TrainingSearchRequest searchRequest;
            searchRequest.mode               = Search::TrainingSearchMode::FixedDepth;
            searchRequest.depth              = depth;
            searchRequest.nodes              = source.nodes;
            searchRequest.multiPV            = 1;
            searchRequest.transpositionTable = &privateTt;
            searchRequest.sharedHistories     = &privateHistory;
            const auto searchResult = worker.training_search(position, searchRequest);
            if (cancelled.load(std::memory_order_relaxed))
                return completed;
            if (searchResult.value == VALUE_NONE || qResult.value == VALUE_NONE
                || evalResult.value == VALUE_NONE || searchResult.pv.empty())
            {
                completed.error = "Atomic V3 synchronous training search returned no result";
                return completed;
            }

            const int  score      = searchResult.value;
            const Move bestMove   = searchResult.pv[0];
            const bool stableTarget =
              std::abs(int(qResult.value) - int(evalResult.value)) <= source.evalDiffLimit;
            if (ply >= source.writeMinPly && dataset_position_clocks_fit(source.dataFormat, position)
                && std::abs(score) < source.evalLimit && stableTarget
                && position.has_king(WHITE) && position.has_king(BLACK)
                && !position.atomic_in_check(position.side_to_move())
                && !(source.filterCaptures && position.capture(bestMove))
                && !(source.filterChecks && position.gives_check(bestMove))
                && !(source.filterPromotions && bestMove.type_of() == PROMOTION))
                trajectory.samples.push_back(
                  {position.fen(), score, bestMove, ply, 0,
                   source.atomic960 ? TRAINING_DATA_CHESS960 : NO_TRAINING_DATA_FLAGS});

            const auto randomMove = choose_random_move(worker, position, flags, ply,
                                                       randomMovesMade, rng, privateTt,
                                                       privateHistory);
            const Move playedMove = randomMove.value_or(bestMove);
            if (!MoveList<LEGAL>(position).contains(playedMove))
            {
                completed.error = "Atomic V3 generator selected an illegal played move";
                return completed;
            }
            u32 playedWire = 0;
            if (DataResult encoded = encode_atomic_bin_v2_move(playedMove, playedWire); !encoded)
            {
                completed.error = encoded.message;
                return completed;
            }
            trajectory.playedMoves.push_back(playedWire);
            position.do_move(playedMove, states[usize(ply)], &privateTt);
        }
        return completed;
    }

    void produce_games(usize workerId,
                       usize workerCount,
                       Search::Worker& worker,
                       TranspositionTable& privateTt,
                       SharedHistories&    privateHistory) {
        for (u64 gameId = workerId; gameId < params.maximumGames && !cancelled.load();
             gameId += workerCount)
        {
            CompletedGame completed = generate_game(gameId, worker, privateTt, privateHistory);
            const bool    fatal      = !completed.error.empty();
            {
                std::unique_lock<std::mutex> lock(pendingMutex);
                pendingCv.wait(lock, [&]() {
                    return cancelled.load(std::memory_order_relaxed)
                        || pendingGames.size() < pendingLimit
                        || gameId == nextCommitGameId.load(std::memory_order_relaxed);
                });
                if (cancelled.load(std::memory_order_relaxed))
                    break;
                pendingGames.emplace(gameId, std::move(completed));
            }
            pendingCv.notify_all();
            if (fatal)
                break;
        }
        {
            std::lock_guard<std::mutex> lock(pendingMutex);
            workerDone[workerId] = true;
        }
        pendingCv.notify_all();
    }
};

bool portable_atomic_v3_basename(std::string_view name) {
    if (name.empty() || name == "." || name == "..")
        return false;
    return std::none_of(name.begin(), name.end(), [](unsigned char c) {
        return c < 0x20 || c == 0x7F || c == '/' || c == '\\' || c == ':' || c == '*'
            || c == '?' || c == '"' || c == '<' || c == '>' || c == '|';
    });
}

std::optional<std::filesystem::path> create_atomic_v3_private_directory(
  const std::filesystem::path& parent,
  std::string_view             basename,
  u64                          seed,
  std::string&                 error) {
    ReplayablePRNG suffix(seed ^ 0xA70C0003A77A31ULL);
    for (unsigned attempt = 0; attempt < 100; ++attempt)
    {
        std::ostringstream name;
        name << '.' << basename << ".atomic-v3-private-" << std::hex << suffix.rand<u64>();
        const auto path = parent / name.str();
        std::error_code ec;
        if (std::filesystem::create_directory(path, ec))
            return path;
        if (ec && ec != std::errc::file_exists)
        {
            error = "Cannot create Atomic V3 private staging directory: " + ec.message();
            return std::nullopt;
        }
    }
    error = "Cannot allocate a unique Atomic V3 private staging directory";
    return std::nullopt;
}

struct AtomicV3PublishArtifact {
    std::filesystem::path staged;
    std::filesystem::path destination;
    std::string           sha256;
    u64                   bytes = 0;
};

DataResult hash_stable_regular_file(const std::filesystem::path& path,
                                    std::string&                 checksum,
                                    u64&                         bytes) {
    checksum.clear();
    bytes = 0;
#ifdef _WIN32
    const HANDLE handle = ::CreateFileW(
      path.c_str(), GENERIC_READ, FILE_SHARE_READ, nullptr, OPEN_EXISTING,
      FILE_ATTRIBUTE_NORMAL | FILE_FLAG_OPEN_REPARSE_POINT | FILE_FLAG_SEQUENTIAL_SCAN, nullptr);
    if (handle == INVALID_HANDLE_VALUE)
        return DataResult::failure(DataError::OPEN_FAILED,
                                   "Cannot open Atomic V3 artifact for authenticated audit: "
                                     + std::system_category().message(int(::GetLastError())));
    FILE_ATTRIBUTE_TAG_INFO attributes{};
    BY_HANDLE_FILE_INFORMATION before{};
    LARGE_INTEGER size{};
    if (!::GetFileInformationByHandleEx(handle, FileAttributeTagInfo, &attributes,
                                        sizeof(attributes))
        || (attributes.FileAttributes & (FILE_ATTRIBUTE_REPARSE_POINT | FILE_ATTRIBUTE_DIRECTORY))
        || !::GetFileInformationByHandle(handle, &before) || !::GetFileSizeEx(handle, &size)
        || size.QuadPart < 0 || u64(size.QuadPart) > Sha256MaxByteCount)
    {
        const DWORD code = ::GetLastError();
        ::CloseHandle(handle);
        return DataResult::failure(DataError::OPEN_FAILED,
                                   "Atomic V3 artifact is not a stable regular non-reparse file: "
                                     + std::system_category().message(int(code)));
    }
    Sha256                hash;
    std::array<u8, 65536> buffer{};
    u64                   remaining = u64(size.QuadPart);
    while (remaining != 0)
    {
        const DWORD requested = DWORD(std::min<u64>(remaining, buffer.size()));
        DWORD       count     = 0;
        if (!::ReadFile(handle, buffer.data(), requested, &count, nullptr) || count == 0)
        {
            const DWORD code = ::GetLastError();
            ::CloseHandle(handle);
            return DataResult::failure(DataError::READ_FAILED,
                                       "Cannot read complete Atomic V3 artifact audit: "
                                         + std::system_category().message(int(code)));
        }
        hash.update(buffer.data(), count);
        remaining -= count;
    }
    char trailing = 0;
    DWORD trailingRead = 0;
    BY_HANDLE_FILE_INFORMATION after{};
    const bool stable = ::ReadFile(handle, &trailing, 1, &trailingRead, nullptr)
                     && trailingRead == 0 && ::GetFileInformationByHandle(handle, &after)
                     && before.dwVolumeSerialNumber == after.dwVolumeSerialNumber
                     && before.nFileIndexHigh == after.nFileIndexHigh
                     && before.nFileIndexLow == after.nFileIndexLow
                     && before.nFileSizeHigh == after.nFileSizeHigh
                     && before.nFileSizeLow == after.nFileSizeLow
                     && before.ftLastWriteTime.dwHighDateTime == after.ftLastWriteTime.dwHighDateTime
                     && before.ftLastWriteTime.dwLowDateTime == after.ftLastWriteTime.dwLowDateTime;
    ::CloseHandle(handle);
    if (!stable)
        return DataResult::failure(DataError::FILE_IDENTITY_MISMATCH,
                                   "Atomic V3 artifact changed during authenticated audit");
    bytes    = u64(size.QuadPart);
    checksum = hash.hex_digest();
#else
    int flags = O_RDONLY | O_CLOEXEC;
    #ifdef O_NOFOLLOW
    flags |= O_NOFOLLOW;
    #endif
    const int descriptor = ::open(path.c_str(), flags);
    if (descriptor < 0)
        return DataResult::failure(DataError::OPEN_FAILED,
                                   "Cannot open Atomic V3 artifact for authenticated audit: "
                                     + std::generic_category().message(errno));
    struct stat before{};
    if (::fstat(descriptor, &before) != 0 || !S_ISREG(before.st_mode) || before.st_size < 0
        || u64(before.st_size) > Sha256MaxByteCount)
    {
        const int code = errno;
        ::close(descriptor);
        return DataResult::failure(DataError::OPEN_FAILED,
                                   "Atomic V3 artifact is not a stable regular non-symlink file: "
                                     + std::generic_category().message(code ? code : EINVAL));
    }
    DataResult result = sha256_file_descriptor(descriptor, u64(before.st_size), checksum);
    struct stat after{};
    const auto timestampsMatch = [&]() {
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
    };
    if (result
        && (::fstat(descriptor, &after) != 0 || before.st_dev != after.st_dev
            || before.st_ino != after.st_ino || before.st_size != after.st_size
            || !timestampsMatch()))
        result = DataResult::failure(DataError::FILE_IDENTITY_MISMATCH,
                                     "Atomic V3 artifact changed during authenticated audit");
    const int closeResult = ::close(descriptor);
    if (closeResult != 0 && result)
        result = DataResult::failure(DataError::CLOSE_FAILED,
                                     "Cannot close Atomic V3 artifact audit: "
                                       + std::generic_category().message(errno));
    if (!result)
    {
        checksum.clear();
        return result;
    }
    bytes = u64(before.st_size);
#endif
    return DataResult::success();
}

DataResult synchronize_publication_directory(const std::filesystem::path& directory) {
#ifdef _WIN32
    // Windows has no portable directory-fsync primitive. Every journal write
    // is committed with FlushFileBuffers and every final artifact was already
    // committed before its private audit; this handle flush is the strongest
    // available metadata barrier and is allowed to report "unsupported".
    const HANDLE handle = ::CreateFileW(directory.c_str(), GENERIC_READ,
                                        FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE,
                                        nullptr, OPEN_EXISTING, FILE_FLAG_BACKUP_SEMANTICS, nullptr);
    if (handle == INVALID_HANDLE_VALUE)
        return DataResult::failure(DataError::OPEN_FAILED,
                                   "Cannot open Atomic V3 publication directory: "
                                     + std::system_category().message(int(::GetLastError())));
    const bool  synced = ::FlushFileBuffers(handle) != 0;
    const DWORD code   = synced ? ERROR_SUCCESS : ::GetLastError();
    ::CloseHandle(handle);
    if (!synced && code != ERROR_INVALID_HANDLE && code != ERROR_ACCESS_DENIED)
        return DataResult::failure(DataError::WRITE_FAILED,
                                   "Cannot synchronize Atomic V3 publication directory: "
                                     + std::system_category().message(int(code)));
#else
    int flags = O_RDONLY | O_CLOEXEC;
    #ifdef O_DIRECTORY
    flags |= O_DIRECTORY;
    #endif
    const int descriptor = ::open(directory.c_str(), flags);
    if (descriptor < 0)
        return DataResult::failure(DataError::OPEN_FAILED,
                                   "Cannot open Atomic V3 publication directory: "
                                     + std::generic_category().message(errno));
    int result;
    do
        result = ::fsync(descriptor);
    while (result != 0 && errno == EINTR);
    const int code = errno;
    const int closeResult = ::close(descriptor);
    if (result != 0 || closeResult != 0)
        return DataResult::failure(DataError::WRITE_FAILED,
                                   "Cannot synchronize Atomic V3 publication directory: "
                                     + std::generic_category().message(result != 0 ? code : errno));
#endif
    return DataResult::success();
}

DataResult same_regular_file_identity(const std::filesystem::path& first,
                                      const std::filesystem::path& second,
                                      bool&                        same) {
    same = false;
#ifdef _WIN32
    const auto openIdentity = [](const std::filesystem::path& path) {
        return ::CreateFileW(path.c_str(), FILE_READ_ATTRIBUTES,
                             FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE, nullptr,
                             OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL | FILE_FLAG_OPEN_REPARSE_POINT,
                             nullptr);
    };
    const HANDLE firstHandle  = openIdentity(first);
    const HANDLE secondHandle = openIdentity(second);
    if (firstHandle == INVALID_HANDLE_VALUE || secondHandle == INVALID_HANDLE_VALUE)
    {
        if (firstHandle != INVALID_HANDLE_VALUE)
            ::CloseHandle(firstHandle);
        if (secondHandle != INVALID_HANDLE_VALUE)
            ::CloseHandle(secondHandle);
        return DataResult::failure(DataError::OPEN_FAILED,
                                   "Cannot identify Atomic V3 publication during rollback");
    }
    FILE_ATTRIBUTE_TAG_INFO firstTag{}, secondTag{};
    BY_HANDLE_FILE_INFORMATION firstInfo{}, secondInfo{};
    const bool inspected =
      ::GetFileInformationByHandleEx(firstHandle, FileAttributeTagInfo, &firstTag, sizeof(firstTag))
      && ::GetFileInformationByHandleEx(secondHandle, FileAttributeTagInfo, &secondTag,
                                        sizeof(secondTag))
      && !(firstTag.FileAttributes & (FILE_ATTRIBUTE_REPARSE_POINT | FILE_ATTRIBUTE_DIRECTORY))
      && !(secondTag.FileAttributes & (FILE_ATTRIBUTE_REPARSE_POINT | FILE_ATTRIBUTE_DIRECTORY))
      && ::GetFileInformationByHandle(firstHandle, &firstInfo)
      && ::GetFileInformationByHandle(secondHandle, &secondInfo);
    ::CloseHandle(firstHandle);
    ::CloseHandle(secondHandle);
    if (!inspected)
        return DataResult::failure(DataError::FILE_IDENTITY_MISMATCH,
                                   "Cannot authenticate Atomic V3 publication rollback identity");
    same = firstInfo.dwVolumeSerialNumber == secondInfo.dwVolumeSerialNumber
        && firstInfo.nFileIndexHigh == secondInfo.nFileIndexHigh
        && firstInfo.nFileIndexLow == secondInfo.nFileIndexLow;
#else
    struct stat firstInfo{}, secondInfo{};
    if (::lstat(first.c_str(), &firstInfo) != 0 || ::lstat(second.c_str(), &secondInfo) != 0
        || !S_ISREG(firstInfo.st_mode) || !S_ISREG(secondInfo.st_mode))
        return DataResult::failure(DataError::OPEN_FAILED,
                                   "Cannot identify Atomic V3 publication during rollback");
    same = firstInfo.st_dev == secondInfo.st_dev && firstInfo.st_ino == secondInfo.st_ino;
#endif
    return DataResult::success();
}

DataResult remove_owned_publication(const AtomicV3PublishArtifact& artifact) {
    bool same = false;
    if (DataResult identified =
          same_regular_file_identity(artifact.staged, artifact.destination, same);
        !identified)
        return identified;
    if (!same)
        return DataResult::failure(DataError::ABORT_FAILED,
                                   "Refusing to remove a replaced Atomic V3 publication");
    std::error_code ec;
    if (!std::filesystem::remove(artifact.destination, ec) || ec)
        return DataResult::failure(DataError::ABORT_FAILED,
                                   "Cannot roll back owned Atomic V3 publication: "
                                     + (ec ? ec.message() : std::string("path was not removed")));
    return DataResult::success();
}

DataResult publish_atomic_v3_artifacts(const std::vector<AtomicV3PublishArtifact>& artifacts) {
    if (artifacts.empty())
        return DataResult::failure(DataError::EMPTY_DATASET,
                                   "Atomic V3 publication transaction has no artifacts");
    const auto directory = artifacts.front().destination.parent_path();
    for (const auto& artifact : artifacts)
    {
        if (artifact.destination.parent_path() != directory)
            return DataResult::failure(DataError::INVALID_MANIFEST,
                                       "Atomic V3 transaction artifacts must share one directory");
        std::string checksum;
        u64         bytes = 0;
        if (DataResult audited = hash_stable_regular_file(artifact.staged, checksum, bytes);
            !audited)
            return audited;
        if (checksum != artifact.sha256 || bytes != artifact.bytes)
            return DataResult::failure(DataError::FILE_IDENTITY_MISMATCH,
                                       "Staged Atomic V3 artifact differs before publication");
    }

    const auto journal = directory
                       / ("." + artifacts.front().destination.filename().string()
                          + ".atomic-v3-publish.journal");
    std::FILE* journalFile = open_private_exclusive(journal);
    if (!journalFile)
        return DataResult::failure(errno == EEXIST ? DataError::OUTPUT_EXISTS
                                                   : DataError::OPEN_FAILED,
                                   "Cannot create Atomic V3 publication journal exclusively");
    std::ostringstream journalContents;
    journalContents << "atomic-v3-publication-v1\nphase=prepared\n";
    for (const auto& artifact : artifacts)
        journalContents << artifact.destination.filename().string() << '\t' << artifact.bytes
                        << '\t' << artifact.sha256 << '\n';
    const std::string journalBytes = journalContents.str();
    DataResult journalResult =
      std::fwrite(journalBytes.data(), 1, journalBytes.size(), journalFile) == journalBytes.size()
        ? sync_private_file(journalFile, "Atomic V3 publication journal")
        : DataResult::failure(DataError::WRITE_FAILED,
                              "Cannot write Atomic V3 publication journal");
    if (std::fclose(journalFile) != 0 && journalResult)
        journalResult = DataResult::failure(DataError::CLOSE_FAILED,
                                            "Cannot close Atomic V3 publication journal");
    if (!journalResult)
    {
        std::error_code removeError;
        std::filesystem::remove(journal, removeError);
        return journalResult;
    }
    if (DataResult synced = synchronize_publication_directory(directory); !synced)
    {
        std::error_code removeError;
        std::filesystem::remove(journal, removeError);
        return synced;
    }

    std::vector<std::size_t> published;
    DataResult failure = DataResult::success();
    for (std::size_t index = 0; index < artifacts.size(); ++index)
    {
        const auto& artifact = artifacts[index];
        std::error_code ec;
        std::filesystem::create_hard_link(artifact.staged, artifact.destination, ec);
        if (ec)
        {
            failure = DataResult::failure(
              ec == std::errc::file_exists ? DataError::OUTPUT_EXISTS : DataError::WRITE_FAILED,
              "Cannot transactionally publish Atomic V3 artifact "
                + artifact.destination.string() + ": " + ec.message());
            break;
        }
        published.push_back(index);
    }

    if (failure)
        for (const auto& artifact : artifacts)
        {
            std::string checksum;
            u64         bytes = 0;
            if (DataResult audited =
                  hash_stable_regular_file(artifact.destination, checksum, bytes);
                !audited || checksum != artifact.sha256 || bytes != artifact.bytes)
            {
                failure = !audited
                          ? audited
                          : DataResult::failure(DataError::FILE_IDENTITY_MISMATCH,
                                                "Published Atomic V3 artifact hash mismatch");
                break;
            }
        }
    if (failure)
        failure = synchronize_publication_directory(directory);

    if (!failure)
    {
        DataResult rollback = DataResult::success();
        for (auto it = published.rbegin(); it != published.rend(); ++it)
            if (DataResult removed = remove_owned_publication(artifacts[*it]); !removed && rollback)
                rollback = removed;
        if (!rollback)
            failure.message += "; rollback failed and journal retained: " + rollback.message;
        else
        {
            std::error_code journalError;
            if (!std::filesystem::remove(journal, journalError) || journalError)
                failure.message += "; rollback succeeded but journal cleanup failed: "
                                 + (journalError ? journalError.message()
                                                 : std::string("path was not removed"));
            (void) synchronize_publication_directory(directory);
        }
        return failure;
    }

    std::error_code journalError;
    if (!std::filesystem::remove(journal, journalError) || journalError)
    {
        DataResult failureResult = DataResult::failure(
          DataError::ABORT_FAILED,
          "Atomic V3 artifacts passed global audit but publication journal could not be removed: "
            + (journalError ? journalError.message() : std::string("path was not removed")));
        DataResult rollback = DataResult::success();
        for (auto it = published.rbegin(); it != published.rend(); ++it)
            if (DataResult removed = remove_owned_publication(artifacts[*it]); !removed && rollback)
                rollback = removed;
        if (!rollback)
            failureResult.message += "; rollback failed and journal retained: " + rollback.message;
        return failureResult;
    }
    // The strict directory sync above is the transaction's durability boundary. Once the
    // journal has been removed the publication is committed, so an unsupported or transient
    // second directory sync must not report failure while leaving the complete public set in
    // place. The journal removal itself was already flushed by the preceding file and directory
    // protocol on platforms that support it.
    (void) synchronize_publication_directory(directory);
    return DataResult::success();
}

std::string random_suffix(u64 seed) {
    ReplayablePRNG rng(seed);
    for (int i = 0; i < 10; ++i)
        rng.rand(1);
    std::ostringstream suffix;
    suffix << '_' << std::hex << rng.rand<u64>() << rng.rand<u64>();
    return suffix.str();
}

void print_error(std::string_view message) { sync_cout << "ERROR: " << message << sync_endl; }

}  // namespace

bool generate_training_data(Engine& engine, std::istream& input) {
    GeneratorParams params;
    std::string     error;
    if (!parse_params(input, params, error))
    {
        print_error(error);
        return false;
    }

    engine.wait_for_search_finished();

    if (params.setRecommendedUciOptionsSeen)
    {
        std::istringstream skill("name Skill Level value 20");
        engine.options.setoption(skill);
        if (params.dataFormat == DatasetFormat::LEGACY_ATOMIC_V1)
        {
            std::istringstream chess960("name UCI_Chess960 value false");
            engine.options.setoption(chess960);
        }
    }

    if (engine.options["Use NNUE"] != "pure")
    {
        print_error("generate_training_data requires Use NNUE=pure");
        return false;
    }
    params.atomic960 = int(engine.options["UCI_Chess960"]) != 0;
    if (params.dataFormat == DatasetFormat::LEGACY_ATOMIC_V1 && params.atomic960)
    {
        print_error("Legacy Atomic V1 cannot encode Atomic960; set UCI_Chess960=false");
        return false;
    }
    if (params.dataFormat == DatasetFormat::ATOMIC_BIN_V2
        && engine.threads.size() > std::numeric_limits<u32>::max())
    {
        print_error("Atomic BIN V2 thread count exceeds the manifest domain");
        return false;
    }
    if (params.dataFormat == DatasetFormat::ATOMIC_BIN_V2
        && !std::string(engine.options["SyzygyPath"]).empty())
    {
        print_error("Atomic BIN V2 generation requires an empty SyzygyPath");
        return false;
    }

    AuthenticatedFile networkMetadata;
    if (params.dataFormat == DatasetFormat::ATOMIC_BIN_V2)
    {
        if (!authenticate_network(engine, engine.binaryDirectory, {},
                                  networkMetadata, error))
        {
            print_error(error);
            return false;
        }
    }
    else if (!engine.verify_network())
    {
        print_error("generate_training_data requires a valid compatible Atomic NNUE network");
        return false;
    }

    const u64 resolvedSeed = ReplayablePRNG::resolve(params.seedText);
    if (params.randomFileName)
        params.outputFile += random_suffix(resolvedSeed);

    std::vector<std::string> openings;
    AuthenticatedFile        bookMetadata;
    const bool               bookIsFile = !params.book.empty();
    const bool bookLoaded = bookIsFile
                            ? authenticate_book_and_load(params.book, params.atomic960, {}, openings,
                                                         bookMetadata, error)
                            : load_book(nullptr, {}, params.atomic960, openings, error);
    if (!bookLoaded)
    {
        print_error(error);
        return false;
    }
    std::string{}.swap(networkMetadata.contents);
    std::string{}.swap(bookMetadata.contents);

    auto paths = output_paths(params, error);
    if (!paths)
    {
        print_error(error);
        return false;
    }
    std::vector<std::filesystem::path> preflightPaths = *paths;
    std::filesystem::path              manifestPath;
    if (params.dataFormat == DatasetFormat::ATOMIC_BIN_V2)
    {
        manifestPath = atomic_bin_v2_manifest_path(paths->front());
        preflightPaths.push_back(manifestPath);
    }
    if (!preflight_output_paths(preflightPaths, error))
    {
        print_error(error);
        return false;
    }
    if (params.dataFormat == DatasetFormat::ATOMIC_BIN_V2)
    {
        const DataResult publicationPreflight =
          preflight_atomic_bin_v2_manifest_publication(manifestPath);
        if (!publicationPreflight)
        {
            print_error(publicationPreflight.message);
            return false;
        }
    }

    std::cout << "INFO: Executing generate_training_data command\n"
              << "INFO: schema_sha256 = "
              << (params.dataFormat == DatasetFormat::LEGACY_ATOMIC_V1 ? LegacyAtomicV1SchemaSha256
                                                                       : AtomicBinV2SchemaSha256Hex)
              << '\n'
              << "PRNG::initial_seed = " << resolvedSeed << '\n'
              << "INFO: threads = " << engine.threads.size() << '\n'
              << "INFO: depth = " << params.searchDepthMin << ".." << params.searchDepthMax << '\n'
              << "INFO: count = " << params.count << '\n'
              << "INFO: output = " << paths->front().string() << std::endl;

    Generator generator(params, engine.threads, engine.tt, std::move(*paths), std::move(openings),
                        resolvedSeed);
    if (!generator.run(error))
    {
        print_error(error);
        return false;
    }

    if (params.dataFormat == DatasetFormat::ATOMIC_BIN_V2)
    {
        AtomicBinV2Manifest manifest;
        manifest.manifestPath = manifestPath;
#ifdef ATOMIC_DATA_GENERATOR_GIT_SHA
        manifest.engineCommit = stringify(ATOMIC_DATA_GENERATOR_GIT_SHA);
#else
        manifest.engineCommit = "unknown";
#endif
        manifest.engineVersion = engine_version_info();
        manifest.networkPath   = networkMetadata.path;
        manifest.networkSha256 = networkMetadata.sha256;
        manifest.bookIsFile    = bookIsFile;
        if (bookIsFile)
        {
            manifest.bookPath   = bookMetadata.path;
            manifest.bookSha256 = bookMetadata.sha256;
        }
        manifest.resolvedSeed = resolvedSeed;
        manifest.atomic960    = params.atomic960;
        manifest.threads      = u32(engine.threads.size());
        manifest.hashMb       = u64(int(engine.options["Hash"]));

        auto& options            = manifest.options;
        options.searchDepthMin   = params.searchDepthMin;
        options.searchDepthMax   = params.searchDepthMax;
        options.nodes            = params.nodes;
        options.requestedRecords = params.count;
        options.recordsPerShard =
          params.saveEvery == std::numeric_limits<u64>::max() ? params.count : params.saveEvery;
        options.evalLimit                    = params.evalLimit;
        options.evalDiffLimit                = params.evalDiffLimit;
        options.randomMoveMinPly             = params.randomMoveMinPly;
        options.randomMoveMaxPly             = params.randomMoveMaxPly;
        options.randomMoveCount              = params.randomMoveCount;
        options.randomMoveLikeApery          = params.randomMoveLikeApery;
        options.randomMultiPv                = params.randomMultiPv;
        options.randomMultiPvDiff            = params.randomMultiPvDiff;
        options.randomMultiPvDepth           = params.randomMultiPvDepth;
        options.writeMinPly                  = params.writeMinPly;
        options.writeMaxPly                  = params.writeMaxPly;
        options.keepDraws                    = params.keepDrawsManifest;
        options.adjudicateDrawsByScore       = params.adjudicateDrawsByScore;
        options.adjudicateInsufficient       = params.adjudicateInsufficient;
        options.filterCaptures               = params.filterCaptures;
        options.filterChecks                 = params.filterChecks;
        options.filterPromotions             = params.filterPromotions;
        options.randomFileName               = params.randomFileName;
        options.setRecommendedUciOptionsSeen = params.setRecommendedUciOptionsSeen;

        manifest.records = generator.records_written();
        manifest.draws   = generator.draws_written();
        for (const auto& shard : generator.atomic_bin_v2_shards())
            manifest.shards.push_back(
              {shard.path, shard.index, shard.records, shard.bytes, shard.sha256});

        if (DataResult published = write_atomic_bin_v2_manifest(manifest); !published)
        {
            error                    = published.message;
            const DataResult cleanup = generator.abort_output();
            if (!cleanup)
                error += "; cleanup failed: " + cleanup.message;
            print_error(error);
            return false;
        }
        std::cout << "INFO: manifest = " << manifestPath.string() << '\n'
                  << "INFO: manifest_schema_sha256 = " << AtomicBinV2ManifestSchemaSha256Hex
                  << '\n';
    }

    std::cout << "INFO: generate_training_data finished.\n"
              << "INFO: records=" << generator.records_written()
              << " draws=" << generator.draws_written() << std::endl;
    return true;
}

bool generate_atomic_v3_chunk(Engine& engine, std::istream& input) {
#ifndef ATOMIC_DATA_GENERATOR_GIT_SHA
    print_error(
      "generate_atomic_v3_chunk requires a clean Git build with a pinned 40-hex commit");
    return false;
#endif
#ifdef ATOMIC_DATA_GENERATOR_GIT_SHA
    if (!is_canonical_git_commit(stringify(ATOMIC_DATA_GENERATOR_GIT_SHA)))
    {
        print_error("generate_atomic_v3_chunk producer commit pin is not canonical 40-hex");
        return false;
    }
#endif
    AtomicV3GeneratorParams params;
    std::string             error;
    if (!parse_atomic_v3_params(input, params, error))
    {
        print_error(error);
        return false;
    }

    engine.wait_for_search_finished();
    if (params.generation.setRecommendedUciOptionsSeen)
    {
        std::istringstream skill("name Skill Level value 20");
        engine.options.setoption(skill);
    }
    if (engine.options["Use NNUE"] != "pure")
    {
        print_error("generate_atomic_v3_chunk requires Use NNUE=pure");
        return false;
    }
    if (!std::string(engine.options["SyzygyPath"]).empty())
    {
        print_error("generate_atomic_v3_chunk requires an empty SyzygyPath");
        return false;
    }
    if (engine.threads.size() == 0
        || engine.threads.size() > std::numeric_limits<u32>::max())
    {
        print_error("generate_atomic_v3_chunk thread count is outside uint32");
        return false;
    }
    params.generation.atomic960 = int(engine.options["UCI_Chess960"]) != 0;

    AuthenticatedFile networkMetadata;
    if (!authenticate_network(engine, engine.binaryDirectory, params.networkSha256,
                              networkMetadata, error))
    {
        print_error(error);
        return false;
    }
    if (networkMetadata.sha256 != params.networkSha256)
    {
        print_error("generate_atomic_v3_chunk EvalFile SHA-256 does not match network_sha256");
        return false;
    }

    std::vector<std::string> openings;
    AuthenticatedFile        bookMetadata;
    const bool               bookIsFile = !params.generation.book.empty();
    const bool bookLoaded = bookIsFile
                            ? authenticate_book_and_load(
                                params.generation.book, params.generation.atomic960,
                                params.bookSha256, openings, bookMetadata, error)
                            : load_book(nullptr, {}, params.generation.atomic960, openings, error);
    if (!bookLoaded)
    {
        print_error(error);
        return false;
    }
    if (bookIsFile && bookMetadata.sha256 != params.bookSha256)
    {
        print_error("generate_atomic_v3_chunk book SHA-256 does not match book_sha256");
        return false;
    }
    // Authentication and parsing consumed the same immutable byte snapshots;
    // only the pins/path metadata are needed for the long generation phase.
    std::string{}.swap(networkMetadata.contents);
    std::string{}.swap(bookMetadata.contents);

    std::error_code ec;
    auto prefix = std::filesystem::absolute(path_from_utf8(params.outputPrefix), ec);
    if (ec)
    {
        print_error("Cannot resolve Atomic V3 output prefix: " + ec.message());
        return false;
    }
    prefix = prefix.lexically_normal();
    const auto parent = prefix.parent_path();
    const auto base   = prefix.filename().string();
    if (!portable_atomic_v3_basename(base) || !std::filesystem::is_directory(parent, ec) || ec)
    {
        print_error("Atomic V3 output prefix must have a portable basename in an existing directory");
        return false;
    }

    const auto trainDataset       = parent / (base + ".train.atbin");
    const auto validationDataset  = parent / (base + ".validation.atbin");
    const auto trainManifest      = atomic_bin_v2_manifest_path(trainDataset);
    const auto validationManifest = atomic_bin_v2_manifest_path(validationDataset);
    const auto trainLedger        = parent / (base + ".train.attraj");
    const auto validationLedger   = parent / (base + ".validation.attraj");
    const std::vector<std::filesystem::path> finalPaths = {
      trainDataset, validationDataset, trainManifest,
      validationManifest, trainLedger, validationLedger};
    if (!preflight_output_paths(finalPaths, error))
    {
        print_error(error);
        return false;
    }

    const u64 resolvedSeed = ReplayablePRNG::resolve(params.generation.seedText);
    auto privateDirectory =
      create_atomic_v3_private_directory(parent, base, resolvedSeed, error);
    if (!privateDirectory)
    {
        print_error(error);
        return false;
    }
    const auto cleanupPrivate = [&]() {
        std::error_code cleanupError;
        std::filesystem::remove_all(*privateDirectory, cleanupError);
    };

    AtomicV3RolePaths trainPaths{
      *privateDirectory / trainDataset.filename(),
      *privateDirectory / trainManifest.filename(),
      *privateDirectory / trainLedger.filename(),
      trainDataset,
      trainManifest,
      trainLedger};
    AtomicV3RolePaths validationPaths{
      *privateDirectory / validationDataset.filename(),
      *privateDirectory / validationManifest.filename(),
      *privateDirectory / validationLedger.filename(),
      validationDataset,
      validationManifest,
      validationLedger};

    if (DataResult preflight =
          preflight_atomic_bin_v2_manifest_publication(trainPaths.stagedManifest);
        !preflight)
    {
        cleanupPrivate();
        print_error(preflight.message);
        return false;
    }
    if (DataResult preflight =
          preflight_atomic_bin_v2_manifest_publication(validationPaths.stagedManifest);
        !preflight)
    {
        cleanupPrivate();
        print_error(preflight.message);
        return false;
    }

    AtomicV3RoleOutput trainOutput(trainPaths, AtomicV3DatasetRole::TRAIN,
                                    params.splitSeed, params.validationThreshold,
                                    u32(params.generation.writeMaxPly));
    AtomicV3RoleOutput validationOutput(validationPaths, AtomicV3DatasetRole::VALIDATION,
                                         params.splitSeed, params.validationThreshold,
                                         u32(params.generation.writeMaxPly));

    const u64 hashMb = u64(int(engine.options["Hash"]));
    std::cout << "INFO: Executing generate_atomic_v3_chunk command\n"
              << "INFO: data_schema_sha256 = " << AtomicBinV2SchemaSha256Hex << '\n'
              << "INFO: trajectory_schema_sha256 = "
              << AtomicV3TrajectorySchemaSha256Hex << '\n'
              << "INFO: generation_seed = " << resolvedSeed << '\n'
              << "INFO: split_seed = " << params.splitSeed << '\n'
              << "INFO: validation_threshold_u64 = " << params.validationThreshold << '\n'
              << "INFO: configured_threads = " << engine.threads.size() << '\n'
              << "INFO: deterministic_producer_workers = " << engine.threads.size() << '\n'
              << "INFO: deterministic_coordinator_threads = 1\n"
              << "INFO: train_count = " << params.trainCount << '\n'
              << "INFO: validation_count = " << params.validationCount << std::endl;

    AtomicV3Generator generator(params, engine.threads, trainOutput, validationOutput,
                                 std::move(openings), resolvedSeed, *privateDirectory, hashMb);
    if (!generator.run(error))
    {
        DataResult auditCleanup      = generator.abort_audits();
        DataResult trainCleanup      = trainOutput.abort();
        DataResult validationCleanup = validationOutput.abort();
        if (!auditCleanup)
            error += "; feature-audit cleanup failed: " + auditCleanup.message;
        if (!trainCleanup)
            error += "; train cleanup failed: " + trainCleanup.message;
        if (!validationCleanup)
            error += "; validation cleanup failed: " + validationCleanup.message;
        cleanupPrivate();
        print_error(error);
        return false;
    }
    if (DataResult auditCleanup = generator.abort_audits(); !auditCleanup)
    {
        error = auditCleanup.message;
        (void) trainOutput.abort();
        (void) validationOutput.abort();
        cleanupPrivate();
        print_error(error);
        return false;
    }

    const u32 threads = u32(engine.threads.size());
    const std::string version = engine_version_info();
    if (DataResult result = trainOutput.finalize(
          params, networkMetadata, bookIsFile, bookMetadata, threads, hashMb, version,
          params.trainCount);
        !result)
    {
        error = result.message;
        (void) trainOutput.abort();
        (void) validationOutput.abort();
        cleanupPrivate();
        print_error(error);
        return false;
    }
    if (DataResult result = validationOutput.finalize(
          params, networkMetadata, bookIsFile, bookMetadata, threads, hashMb, version,
          params.validationCount);
        !result)
    {
        error = result.message;
        (void) trainOutput.abort();
        (void) validationOutput.abort();
        cleanupPrivate();
        print_error(error);
        return false;
    }

    // Repeat the public-name preflight immediately before exclusive hard-link
    // publication. A concurrent creator still loses safely at create_hard_link.
    if (!preflight_output_paths(finalPaths, error))
    {
        (void) trainOutput.abort();
        (void) validationOutput.abort();
        cleanupPrivate();
        print_error(error);
        return false;
    }

    const std::vector<AtomicV3PublishArtifact> artifacts = {
      {trainPaths.stagedDataset, trainPaths.finalDataset,
       trainOutput.dataset_sink().sha256_hex(), trainOutput.dataset_sink().finalized_size()},
      {validationPaths.stagedDataset, validationPaths.finalDataset,
       validationOutput.dataset_sink().sha256_hex(),
       validationOutput.dataset_sink().finalized_size()},
      {trainPaths.stagedManifest, trainPaths.finalManifest,
       trainOutput.manifest_sha256(), trainOutput.manifest_size()},
      {validationPaths.stagedManifest, validationPaths.finalManifest,
       validationOutput.manifest_sha256(), validationOutput.manifest_size()},
      {trainPaths.stagedLedger, trainPaths.finalLedger,
       trainOutput.ledger_metadata().sha256, trainOutput.ledger_metadata().bytes},
      {validationPaths.stagedLedger, validationPaths.finalLedger,
       validationOutput.ledger_metadata().sha256, validationOutput.ledger_metadata().bytes},
    };
    if (DataResult published = publish_atomic_v3_artifacts(artifacts); !published)
    {
        error = published.message;
        (void) trainOutput.abort();
        (void) validationOutput.abort();
        cleanupPrivate();
        print_error(error);
        return false;
    }

    cleanupPrivate();
    std::cout << "INFO: generate_atomic_v3_chunk finished.\n"
              << "INFO: train_records=" << generator.train_records()
              << " validation_records=" << generator.validation_records()
              << " trajectories="
              << trainOutput.ledger_metadata().trajectories
                   + validationOutput.ledger_metadata().trajectories
              << " games=" << generator.games_generated() << '\n'
              << "INFO: committed_game_id_range = 0.."
              << (generator.games_generated() - 1) << " (diagnostic-only)\n"
              << "INFO: train_manifest = " << trainManifest.string() << '\n'
              << "INFO: validation_manifest = " << validationManifest.string() << '\n'
              << "INFO: train_trajectory_ledger = " << trainLedger.string() << '\n'
              << "INFO: validation_trajectory_ledger = " << validationLedger.string()
              << std::endl;
    return true;
}

}  // namespace Stockfish::Data
