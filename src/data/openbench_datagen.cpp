/*
  Atomic-Stockfish, a specialized Atomic Chess engine derived from Stockfish
  Copyright (C) 2004-2026 The Atomic-Stockfish developers

  Atomic-Stockfish is free software: you can redistribute it and/or modify
  it under the terms of the GNU General Public License as published by
  the Free Software Foundation, either version 3 of the License, or
  (at your option) any later version.
*/

#include "openbench_datagen.h"

#include <cerrno>
#include <cstdint>
#include <filesystem>
#include <iostream>
#include <limits>
#include <memory>
#include <set>
#include <sstream>
#include <string>
#include <string_view>
#include <system_error>
#include <utility>
#include <vector>

#include "atomic_bin_v2_manifest.h"
#include "authenticated_datagen_v2.h"
#include "engine.h"
#include "misc.h"
#include "openbench_bundle.h"
#include "sha256.h"
#include "training_data_generator.h"
#include "syzygy/tbprobe.h"

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

struct BridgeParams {
    int         threads = 0;
    int         hashMb  = 0;
    std::string network;
    std::string networkSha256;
    std::string count;
    std::string seed;
    std::string book;
    std::string bookSha256;
    bool        bookSha256Seen = false;
    std::string output;
    std::string producerSha256;
    bool        producerSha256Seen = false;
    std::string teacherMode;
    bool        teacherModeSeen = false;
    std::string syzygyPath;
    bool        syzygyPathSeen = false;
    std::string syzygyManifestSha256;
    bool        syzygyManifestSha256Seen = false;
    int         syzygyMax = 0;
    bool        syzygyMaxSeen = false;
    bool        contractV2 = false;
    std::string generatorOptions;
};

bool read_token(std::istream&    input,
                std::string&     value,
                std::string_view name,
                std::string&     error) {
    if (input >> value)
        return true;
    error = "Missing value for openbench_generate_training_data option " + std::string(name);
    return false;
}

bool read_quoted_path(std::istream&    input,
                      std::string&     value,
                      std::string_view name,
                      std::string&     error) {
    input >> std::ws;
    if (input.peek() != '"')
        return read_token(input, value, name, error);

    input.get();
    if (std::getline(input, value, '"') && !input.eof())
        return true;
    error = "Missing closing quote for openbench_generate_training_data option "
          + std::string(name);
    return false;
}

bool safe_serialized_token(std::string_view value) {
    if (value.empty())
        return false;
    for (const unsigned char c : value)
        if (c <= 0x20 || c == 0x7f || c == '"')
            return false;
    return true;
}

bool safe_quoted_path(std::string_view value) {
    if (value.empty() || value.front() == ' ' || value.back() == ' ')
        return false;
    for (const unsigned char c : value)
        if (c < 0x20 || c == 0x7f || c == '"')
            return false;
    return true;
}

bool read_positive_int(std::istream& input, int& value, std::string_view name, std::string& error) {
    std::string text;
    if (!read_token(input, text, name, error))
        return false;
    unsigned int parsed = 0;
    for (unsigned char c : text)
    {
        if (c < '0' || c > '9')
        {
            error = "Option " + std::string(name) + " must be a positive decimal integer";
            return false;
        }
        const unsigned int digit = c - '0';
        if (parsed > (unsigned(std::numeric_limits<int>::max()) - digit) / 10)
        {
            error = "Option " + std::string(name) + " must be a positive decimal integer";
            return false;
        }
        parsed = parsed * 10 + digit;
    }
    if (parsed == 0)
    {
        error = "Option " + std::string(name) + " must be a positive decimal integer";
        return false;
    }
    value = int(parsed);
    return true;
}

bool decimal_u64(std::string_view value) {
    if (value.empty())
        return false;
    u64 parsed = 0;
    for (unsigned char c : value)
    {
        if (c < '0' || c > '9')
            return false;
        const u64 digit = c - '0';
        if (parsed > (std::numeric_limits<u64>::max() - digit) / 10)
            return false;
        parsed = parsed * 10 + digit;
    }
    return parsed != 0;
}

bool normalize_sha256(std::string& value) {
    if (value.size() != 64)
        return false;
    for (char& c : value)
    {
        const unsigned char raw = static_cast<unsigned char>(c);
        if (raw >= '0' && raw <= '9')
            continue;
        if (raw >= 'A' && raw <= 'F')
            c = char(raw - 'A' + 'a');
        else if (raw < 'a' || raw > 'f')
            return false;
    }
    return true;
}

bool generator_option_takes_value(std::string_view name) {
    static constexpr std::string_view Names[] = {"depth",
                                                 "min_depth",
                                                 "max_depth",
                                                 "nodes",
                                                 "eval_limit",
                                                 "eval_diff_limit",
                                                 "random_move_min_ply",
                                                 "random_move_max_ply",
                                                 "random_move_count",
                                                 "random_move_like_apery",
                                                 "random_multi_pv",
                                                 "random_multi_pv_diff",
                                                 "random_multi_pv_depth",
                                                 "write_min_ply",
                                                 "write_max_ply",
                                                 "keep_draws",
                                                 "adjudicate_draws_by_score",
                                                 "adjudicate_draws_by_insufficient_material",
                                                 "filter_captures",
                                                 "filter_checks",
                                                 "filter_promotions"};
    for (const auto candidate : Names)
        if (candidate == name)
            return true;
    return false;
}

bool parse_bridge_params(std::istream& input, BridgeParams& params, std::string& error) {
    std::ostringstream forwarded;
    std::string        token;
    std::set<std::string> seenSerializedOptions;
    std::string           duplicateSerializedOption;
    std::string           unsafeSerializedOption;
    const auto note_serialized_option = [&](std::string_view name) {
        if (!seenSerializedOptions.emplace(name).second && duplicateSerializedOption.empty())
            duplicateSerializedOption = name;
    };
    const auto note_serialized_value = [&](std::string_view name, std::string_view value) {
        if (!safe_serialized_token(value) && unsafeSerializedOption.empty())
            unsafeSerializedOption = name;
    };
    while (input >> token)
    {
        if (token == "threads")
        {
            note_serialized_option(token);
            if (!read_positive_int(input, params.threads, token, error))
                return false;
        }
        else if (token == "hash")
        {
            note_serialized_option(token);
            if (!read_positive_int(input, params.hashMb, token, error))
                return false;
        }
        else if (token == "network")
        {
            note_serialized_option(token);
            if (!read_token(input, params.network, token, error))
                return false;
            note_serialized_value(token, params.network);
        }
        else if (token == "network_sha256")
        {
            note_serialized_option(token);
            if (!read_token(input, params.networkSha256, token, error))
                return false;
        }
        else if (token == "count")
        {
            note_serialized_option(token);
            if (!read_token(input, params.count, token, error))
                return false;
        }
        else if (token == "seed")
        {
            note_serialized_option(token);
            if (!read_token(input, params.seed, token, error))
                return false;
            note_serialized_value(token, params.seed);
        }
        else if (token == "book")
        {
            note_serialized_option(token);
            if (!read_token(input, params.book, token, error))
                return false;
            note_serialized_value(token, params.book);
        }
        else if (token == "book_sha256")
        {
            note_serialized_option(token);
            if (!read_token(input, params.bookSha256, token, error))
                return false;
            params.bookSha256Seen = true;
        }
        else if (token == "out")
        {
            note_serialized_option(token);
            if (!read_token(input, params.output, token, error))
                return false;
            note_serialized_value(token, params.output);
        }
        else if (token == "teacher_mode")
        {
            if (params.teacherModeSeen)
            {
                error = "Duplicate openbench_generate_training_data option teacher_mode";
                return false;
            }
            if (!read_token(input, params.teacherMode, token, error))
                return false;
            params.teacherModeSeen = true;
        }
        else if (token == "syzygy")
        {
            if (params.syzygyPathSeen)
            {
                error = "Duplicate openbench_generate_training_data option syzygy";
                return false;
            }
            if (!read_quoted_path(input, params.syzygyPath, token, error))
                return false;
            params.syzygyPathSeen = true;
        }
        else if (token == "syzygy_manifest_sha256")
        {
            if (params.syzygyManifestSha256Seen)
            {
                error = "Duplicate openbench_generate_training_data option "
                        "syzygy_manifest_sha256";
                return false;
            }
            if (!read_token(input, params.syzygyManifestSha256, token, error))
                return false;
            params.syzygyManifestSha256Seen = true;
        }
        else if (token == "syzygy_max")
        {
            if (params.syzygyMaxSeen)
            {
                error = "Duplicate openbench_generate_training_data option syzygy_max";
                return false;
            }
            if (!read_positive_int(input, params.syzygyMax, token, error))
                return false;
            params.syzygyMaxSeen = true;
        }
        else if (token == "producer_sha256")
        {
            if (params.producerSha256Seen)
            {
                error = "Duplicate openbench_generate_training_data option producer_sha256";
                return false;
            }
            if (!read_token(input, params.producerSha256, token, error))
                return false;
            params.producerSha256Seen = true;
        }
        else if (token == "set_recommended_uci_options")
        {
            note_serialized_option(token);
            forwarded << ' ' << token;
        }
        else if (generator_option_takes_value(token))
        {
            note_serialized_option(token);
            std::string value;
            if (!read_token(input, value, token, error))
                return false;
            note_serialized_value(token, value);
            forwarded << ' ' << token << ' ' << value;
        }
        else
        {
            error = "Unknown or reserved openbench_generate_training_data option " + token;
            return false;
        }
    }

    if (params.threads <= 0 || params.hashMb <= 0 || params.network.empty()
        || !normalize_sha256(params.networkSha256) || !decimal_u64(params.count)
        || params.seed.empty() || params.book.empty() || params.output.empty())
    {
        error = "openbench_generate_training_data requires threads, hash, network, "
                "network_sha256, count, seed, book, and out";
        return false;
    }
    const bool anyV2Field = params.teacherModeSeen || params.syzygyPathSeen
                         || params.syzygyManifestSha256Seen || params.syzygyMaxSeen;
    const bool allV2Fields = params.teacherModeSeen && params.syzygyPathSeen
                          && params.syzygyManifestSha256Seen && params.syzygyMaxSeen;
    if (anyV2Field && !allV2Fields)
    {
        error = "Authenticated teacher/Syzygy V2 requires syzygy, syzygy_manifest_sha256, "
                "syzygy_max, and teacher_mode together";
        return false;
    }
    params.contractV2 = allV2Fields;
    if (params.contractV2)
    {
        if (!duplicateSerializedOption.empty())
        {
            error = "Authenticated teacher/Syzygy V2 rejects duplicate option "
                  + duplicateSerializedOption;
            return false;
        }
        if (!unsafeSerializedOption.empty())
        {
            error = "Authenticated teacher/Syzygy V2 requires option " + unsafeSerializedOption
                  + " to be one unquoted token without whitespace or control characters";
            return false;
        }
        if (!safe_quoted_path(params.syzygyPath))
        {
            error = "Authenticated teacher/Syzygy V2 syzygy path contains an unsafe control, "
                    "quote, or boundary space";
            return false;
        }
        if (!params.bookSha256Seen)
        {
            error = params.book == "NONE"
                    ? "Authenticated teacher/Syzygy V2 requires exact sentinel book_sha256 "
                      "NONE with book NONE"
                    : "Authenticated teacher/Syzygy V2 requires book_sha256 for a file book";
            return false;
        }
        if (params.book == "NONE")
        {
            if (params.bookSha256 != "NONE")
            {
                error = "Authenticated teacher/Syzygy V2 requires exact pair book NONE "
                        "book_sha256 NONE";
                return false;
            }
        }
        else if (params.bookSha256 == "NONE" || !normalize_sha256(params.bookSha256))
        {
            error = "Authenticated teacher/Syzygy V2 requires a 64-hex book_sha256 for a file "
                    "book";
            return false;
        }
        if ((params.teacherMode != AtomicPureTeacherMode
             && params.teacherMode != AtomicTrueTeacherMode)
            || params.syzygyPath.empty() || params.syzygyPath == "NONE"
            || !normalize_sha256(params.syzygyManifestSha256)
            || params.syzygyManifestSha256 != AtomicTeacherSyzygyInventorySha256Hex
            || params.syzygyMax != AtomicTeacherSyzygyCardinality)
        {
            error = "Authenticated teacher/Syzygy V2 requires teacher_mode pure|true, the "
                    "pinned Atomic inventory SHA-256, and syzygy_max 6";
            return false;
        }
    }
    else
    {
        if (params.bookSha256Seen && !normalize_sha256(params.bookSha256))
        {
            error = "book_sha256 must contain exactly 64 hexadecimal characters";
            return false;
        }
        if (params.book == "NONE" && params.bookSha256Seen)
        {
            error = "book_sha256 cannot be supplied with book NONE";
            return false;
        }
    }
    if (params.producerSha256Seen
        && (!params.contractV2 || !normalize_sha256(params.producerSha256)))
    {
        error = "producer_sha256 requires the authenticated V2 field group and exactly 64 "
                "hexadecimal characters";
        return false;
    }
    params.generatorOptions = std::move(forwarded).str();
    return true;
}

void set_option(Engine& engine, std::string_view name, const std::string& value) {
    std::istringstream command("name " + std::string(name) + " value " + value);
    engine.get_options().setoption(command);
}

bool authenticate_sha256_gate(const std::filesystem::path& path,
                              std::string_view             expectedSha256,
                              std::string_view             label,
                              std::string&                 error,
                              u64*                         authenticatedBytes = nullptr) {
    std::string actualSha256;
    u64         bytes = 0;
    if (DataResult hashed = sha256_file(path, actualSha256, bytes); !hashed)
    {
        error = "Cannot authenticate OpenBench " + std::string(label)
              + " before generation: " + hashed.message;
        return false;
    }
    if (actualSha256 != expectedSha256)
    {
        error = "OpenBench " + std::string(label)
              + " SHA-256 differs from the supplied pre-generation gate";
        return false;
    }
    if (authenticatedBytes)
        *authenticatedBytes = bytes;
    return true;
}

class OwnedSidecar {
   public:
    enum class CleanupResult {
        SUCCESS,
        PATH_REPLACED,
        INSPECTION_FAILED,
        REMOVE_FAILED,
        CLOSE_FAILED
    };

    struct CleanupStatus {
        CleanupResult result = CleanupResult::SUCCESS;
        int           error  = 0;
    };

    explicit OwnedSidecar(std::filesystem::path sidecarPath) :
        path(std::move(sidecarPath)) {}

    OwnedSidecar(const OwnedSidecar&)            = delete;
    OwnedSidecar& operator=(const OwnedSidecar&) = delete;

    ~OwnedSidecar() { cleanup(); }

    const std::filesystem::path& output_path() const noexcept { return path; }

    bool capture(std::string& error) {
#ifdef _WIN32
        const HANDLE handle = ::CreateFileW(
          path.c_str(), FILE_READ_ATTRIBUTES,
          FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE, nullptr, OPEN_EXISTING,
          FILE_ATTRIBUTE_NORMAL | FILE_FLAG_OPEN_REPARSE_POINT, nullptr);
        if (handle == INVALID_HANDLE_VALUE)
        {
            const unsigned long nativeError = ::GetLastError();
            error = "Cannot take ownership of generated OpenBench sidecar " + path.string() + ": "
                  + std::system_category().message(int(nativeError));
            return false;
        }

        BY_HANDLE_FILE_INFORMATION information{};
        const bool inspected = bool(::GetFileInformationByHandle(handle, &information));
        if (!inspected
            || (information.dwFileAttributes
                & (FILE_ATTRIBUTE_DIRECTORY | FILE_ATTRIBUTE_REPARSE_POINT)))
        {
            const unsigned long nativeError = inspected ? ERROR_SUCCESS : ::GetLastError();
            ::CloseHandle(handle);
            error = "Generated OpenBench sidecar is not an owned regular file: " + path.string();
            if (nativeError != ERROR_SUCCESS)
                error += ": " + std::system_category().message(int(nativeError));
            return false;
        }
        identityDevice = std::uint64_t(information.dwVolumeSerialNumber);
        identityFile =
          (std::uint64_t(information.nFileIndexHigh) << 32) | information.nFileIndexLow;
        active = true;
        if (!::CloseHandle(handle))
        {
            const unsigned long nativeError = ::GetLastError();
            error = "Cannot close generated OpenBench sidecar identity handle " + path.string()
                  + ": " + std::system_category().message(int(nativeError));
            return false;
        }
#else
        int flags = O_RDONLY;
    #ifdef O_CLOEXEC
        flags |= O_CLOEXEC;
    #endif
    #ifdef O_NOFOLLOW
        flags |= O_NOFOLLOW;
    #endif
        descriptor = ::open(path.c_str(), flags);
        if (descriptor == -1)
        {
            const int nativeError = errno;
            error = "Cannot take ownership of generated OpenBench sidecar " + path.string() + ": "
                  + std::generic_category().message(nativeError ? nativeError : EIO);
            return false;
        }

        struct stat status{};
        if (::fstat(descriptor, &status) != 0 || !S_ISREG(status.st_mode))
        {
            const int nativeError = errno;
            ::close(descriptor);
            descriptor = -1;
            error = "Generated OpenBench sidecar is not an owned regular file: " + path.string();
            if (nativeError)
                error += ": " + std::generic_category().message(nativeError);
            return false;
        }
        identityDevice = std::uint64_t(status.st_dev);
        identityFile   = std::uint64_t(status.st_ino);
#endif
        active = true;
        return true;
    }

    CleanupStatus cleanup() noexcept {
        if (!active)
            return {};

        CleanupStatus status;
#ifdef _WIN32
        const HANDLE handle = ::CreateFileW(
          path.c_str(), FILE_READ_ATTRIBUTES | DELETE,
          FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE, nullptr, OPEN_EXISTING,
          FILE_ATTRIBUTE_NORMAL | FILE_FLAG_OPEN_REPARSE_POINT, nullptr);
        if (handle == INVALID_HANDLE_VALUE)
        {
            const unsigned long nativeError = ::GetLastError();
            if (nativeError != ERROR_FILE_NOT_FOUND && nativeError != ERROR_PATH_NOT_FOUND)
            {
                status.result = CleanupResult::INSPECTION_FAILED;
                status.error  = int(nativeError);
            }
            active = false;
            return status;
        }

        BY_HANDLE_FILE_INFORMATION information{};
        if (!::GetFileInformationByHandle(handle, &information))
        {
            status.result = CleanupResult::INSPECTION_FAILED;
            status.error  = int(::GetLastError());
        }
        else
        {
            const std::uint64_t device = information.dwVolumeSerialNumber;
            const std::uint64_t file =
              (std::uint64_t(information.nFileIndexHigh) << 32) | information.nFileIndexLow;
            if ((information.dwFileAttributes
                 & (FILE_ATTRIBUTE_DIRECTORY | FILE_ATTRIBUTE_REPARSE_POINT))
                || device != identityDevice || file != identityFile)
                status.result = CleanupResult::PATH_REPLACED;
            else
            {
                FILE_DISPOSITION_INFO disposition{TRUE};
                if (!::SetFileInformationByHandle(handle, FileDispositionInfo, &disposition,
                                                  sizeof(disposition)))
                {
                    status.result = CleanupResult::REMOVE_FAILED;
                    status.error  = int(::GetLastError());
                }
            }
        }

        if (!::CloseHandle(handle) && status.result == CleanupResult::SUCCESS)
        {
            status.result = CleanupResult::CLOSE_FAILED;
            status.error  = int(::GetLastError());
        }
        active = false;
#else
        struct stat current{};
        errno = 0;
        if (::lstat(path.c_str(), &current) != 0)
        {
            if (errno != ENOENT)
            {
                status.result = CleanupResult::INSPECTION_FAILED;
                status.error  = errno;
            }
        }
        else if (std::uint64_t(current.st_dev) != identityDevice
                 || std::uint64_t(current.st_ino) != identityFile)
            status.result = CleanupResult::PATH_REPLACED;
        else if (::unlink(path.c_str()) != 0 && errno != ENOENT)
        {
            status.result = CleanupResult::REMOVE_FAILED;
            status.error  = errno;
        }

        // POSIX has no portable unlink-if-inode primitive. Keeping the owned
        // descriptor open and revalidating with lstat immediately before
        // unlink prevents ordinary path replacement from deleting a foreign
        // file. A hostile writer with directory access is outside this bridge's
        // threat model, matching AtomicBinV2Sink's rollback contract.
        if (::close(descriptor) != 0 && status.result == CleanupResult::SUCCESS)
        {
            status.result = CleanupResult::CLOSE_FAILED;
            status.error  = errno;
        }
        descriptor = -1;
#endif
        active = false;
        return status;
    }

   private:
    std::filesystem::path path;
    bool                  active = false;
#ifdef _WIN32
    std::uint64_t identityDevice = 0;
    std::uint64_t identityFile   = 0;
#else
    int           descriptor     = -1;
    std::uint64_t identityDevice = 0;
    std::uint64_t identityFile   = 0;
#endif
};

class GeneratedSidecars {
   public:
    GeneratedSidecars(std::filesystem::path shardPath, std::filesystem::path manifestPath) :
        shard(std::move(shardPath)),
        manifest(std::move(manifestPath)) {}

    GeneratedSidecars(std::filesystem::path shardPath,
                      std::filesystem::path manifestPath,
                      std::filesystem::path attestationPath) :
        shard(std::move(shardPath)),
        manifest(std::move(manifestPath)),
        attestation(std::make_unique<OwnedSidecar>(std::move(attestationPath))) {}

    bool preflight(std::string& error) {
        std::error_code ec;
        std::vector<const OwnedSidecar*> paths{&shard, &manifest};
        if (attestation)
            paths.push_back(attestation.get());
        for (const auto* sidecar : paths)
        {
            const auto& path = sidecar->output_path();
            ec.clear();
            const auto status = std::filesystem::symlink_status(path, ec);
            if (ec == std::errc::no_such_file_or_directory)
                ec.clear();
            if (ec)
            {
                error = "Cannot inspect OpenBench sidecar output: " + ec.message();
                return false;
            }
            if (std::filesystem::exists(status) || std::filesystem::is_symlink(status))
            {
                error = "OpenBench sidecar output already exists: " + path.string();
                return false;
            }
        }
        return true;
    }

    ~GeneratedSidecars() = default;

    bool take_primary_ownership(std::string& error) {
        return shard.capture(error) && manifest.capture(error);
    }

    bool take_attestation_ownership(std::string& error) {
        return !attestation || attestation->capture(error);
    }

    bool cleanup(std::string& error) {
        const auto attestationStatus =
          attestation ? attestation->cleanup() : OwnedSidecar::CleanupStatus{};
        const auto manifestStatus = manifest.cleanup();
        const auto shardStatus    = shard.cleanup();
        const auto report         = [&](const OwnedSidecar&         sidecar,
                                OwnedSidecar::CleanupStatus status) -> bool {
            using Result = OwnedSidecar::CleanupResult;
            if (status.result == Result::SUCCESS)
                return true;
            error = "Cannot remove generated OpenBench sidecar " + sidecar.output_path().string();
            if (status.result == Result::PATH_REPLACED)
                error += " because its path was replaced";
            else
            {
#ifdef _WIN32
                error += ": " + std::system_category().message(status.error);
#else
                error += ": " + std::generic_category().message(status.error ? status.error : EIO);
#endif
            }
            return false;
        };
        return (!attestation || report(*attestation, attestationStatus))
            && report(manifest, manifestStatus) && report(shard, shardStatus);
    }

   private:
    OwnedSidecar shard;
    OwnedSidecar manifest;
    std::unique_ptr<OwnedSidecar> attestation;
};

void print_error(std::string_view message) { std::cout << "ERROR: " << message << std::endl; }

}  // namespace

bool openbench_generate_training_data(Engine& engine, std::istream& input) {
    BridgeParams params;
    std::string  error;
    if (!parse_bridge_params(input, params, error))
    {
        print_error(error);
        return false;
    }

    const auto output = path_from_utf8(params.output).lexically_normal();
    if (output.empty() || output.filename().empty())
    {
        print_error("OpenBench bundle output must name a file");
        return false;
    }
    const bool contractV2 = params.contractV2;
    if (contractV2)
    {
        if (DataResult preflight = preflight_authenticated_datagen_v2_output(output); !preflight)
        {
            print_error(preflight.message);
            return false;
        }
    }
    else
    {
        std::error_code ec;
        if (std::filesystem::exists(output, ec))
        {
            print_error("OpenBench bundle output already exists: " + output.string());
            return false;
        }
        if (ec)
        {
            print_error("Cannot inspect OpenBench bundle output: " + ec.message());
            return false;
        }
    }

    const auto shard      = std::filesystem::path(output.string() + ".atbin");
    const auto manifest   = contractV2 ? atomic_datagen_v2_manifest_path(shard)
                                       : atomic_bin_v2_manifest_path(shard);
    const auto attestation = contractV2 ? atomic_datagen_v2_attestation_path(output)
                                        : std::filesystem::path{};
    std::unique_ptr<GeneratedSidecars> sidecars =
      contractV2 ? std::make_unique<GeneratedSidecars>(shard, manifest, attestation)
                 : std::make_unique<GeneratedSidecars>(shard, manifest);
    if (!sidecars->preflight(error))
    {
        print_error(error);
        return false;
    }

    // Fail cheap before starting a production-sized self-play chunk. The
    // generator authenticates the same files again while loading them and the
    // manifest checks below close the remaining change-after-check window.
    if (!authenticate_sha256_gate(path_from_utf8(params.network), params.networkSha256, "network",
                                  error)
        || (params.book != "NONE" && !params.bookSha256.empty()
            && !authenticate_sha256_gate(path_from_utf8(params.book), params.bookSha256, "book",
                                         error)))
    {
        print_error(error);
        return false;
    }

    engine.wait_for_search_finished();
    set_option(engine, "Threads", std::to_string(params.threads));
    set_option(engine, "Hash", std::to_string(params.hashMb));
    set_option(engine, "UCI_Chess960", "false");
    set_option(engine, "SyzygyPath", contractV2 ? params.syzygyPath : "<empty>");
    if (contractV2)
    {
        set_option(engine, "SyzygyProbeLimit", std::to_string(AtomicTeacherSyzygyProbeLimit));
        set_option(engine, "SyzygyProbeDepth", std::to_string(AtomicTeacherSyzygyProbeDepth));
        set_option(engine, "Syzygy50MoveRule", "true");
    }
    set_option(engine, "EvalFile", params.network);
    const std::string useNnue = params.teacherMode == AtomicTrueTeacherMode ? "true" : "pure";
    set_option(engine, "Use NNUE", contractV2 ? useNnue : "pure");

    if (int(engine.get_options()["Threads"]) != params.threads
        || int(engine.get_options()["Hash"]) != params.hashMb
        || int(engine.get_options()["UCI_Chess960"]) != 0
        || (contractV2 ? std::string(engine.get_options()["SyzygyPath"]) != params.syzygyPath
                       : !std::string(engine.get_options()["SyzygyPath"]).empty())
        || std::string(engine.get_options()["EvalFile"]) != params.network
        || std::string(engine.get_options()["Use NNUE"])
             != (contractV2 ? useNnue : std::string("pure"))
        || (contractV2
            && (int(engine.get_options()["SyzygyProbeLimit"]) != AtomicTeacherSyzygyProbeLimit
                || int(engine.get_options()["SyzygyProbeDepth"]) != AtomicTeacherSyzygyProbeDepth
                || !bool(engine.get_options()["Syzygy50MoveRule"])
                || Tablebases::MaxCardinality != params.syzygyMax)))
    {
        print_error("OpenBench UCI option configuration was rejected");
        return false;
    }

    std::ostringstream command;
    command << params.generatorOptions << " count " << params.count << " seed " << params.seed
            << " output_file_name " << shard.string() << " data_format atomic-bin-v2 save_every "
            << params.count << " random_file_name false";
    if (params.book != "NONE")
        command << " book " << params.book;
    std::istringstream generatorInput(command.str());
    AtomicDatagenV2Manifest authenticatedV2;
    if (contractV2)
    {
        authenticatedV2.manifestPath    = manifest;
        authenticatedV2.inventorySha256 = params.syzygyManifestSha256;
        authenticatedV2.producerSha256  = params.producerSha256;
        authenticatedV2.teacherMode     = params.teacherMode;
        authenticatedV2.useNnue         = useNnue;
    }
    const bool generated = contractV2
                           ? generate_authenticated_training_data_v2(engine, generatorInput,
                                                                     authenticatedV2)
                           : generate_training_data(engine, generatorInput);
    if (!generated)
        return false;
    if (!sidecars->take_primary_ownership(error))
    {
        print_error(error);
        return false;
    }

    if (!contractV2)
    {
        AtomicBinV2Manifest authenticatedManifest;
        if (DataResult loaded = load_atomic_bin_v2_manifest(manifest, authenticatedManifest);
            !loaded)
        {
            print_error(loaded.message);
            return false;
        }
        if (authenticatedManifest.networkSha256 != params.networkSha256)
        {
            print_error("Generated manifest network SHA-256 differs from network_sha256");
            return false;
        }
        if (!params.bookSha256.empty()
            && (!authenticatedManifest.bookIsFile
                || authenticatedManifest.bookSha256 != params.bookSha256))
        {
            print_error("Generated manifest book SHA-256 differs from book_sha256");
            return false;
        }
        if (DataResult bundled = write_openbench_datagen_bundle(output, shard, manifest); !bundled)
        {
            print_error(bundled.message);
            return false;
        }
    }
    else
    {
        if (authenticatedV2.networkSha256 != params.networkSha256
            || (params.book != "NONE"
                && (!authenticatedV2.bookIsFile
                    || authenticatedV2.bookSha256 != params.bookSha256)))
        {
            print_error("Generated manifest differs from a supplied asset SHA-256 gate");
            return false;
        }
        AtomicDatagenV2Attestation proof;
        proof.attestationPath = attestation;
        proof.manifestPath    = manifest;
        proof.shardPath       = shard;
        proof.inventorySha256 = authenticatedV2.inventorySha256;
        proof.producerSha256  = authenticatedV2.producerSha256;
        proof.teacherMode     = authenticatedV2.teacherMode;
        proof.useNnue         = authenticatedV2.useNnue;
        proof.records         = authenticatedV2.records;
        proof.tbProbes        = authenticatedV2.tbProbes;
        proof.tbHits          = authenticatedV2.tbHits;
        if (DataResult hashed = sha256_file(manifest, proof.manifestSha256,
                                            proof.manifestBytes);
            !hashed)
        {
            print_error(hashed.message);
            return false;
        }
        if (DataResult hashed = sha256_file(shard, proof.shardSha256, proof.shardBytes); !hashed)
        {
            print_error(hashed.message);
            return false;
        }
        if (proof.shardSha256 != authenticatedV2.shard.sha256
            || proof.shardBytes != authenticatedV2.shard.bytes)
        {
            print_error("Generated shard changed before attestation");
            return false;
        }
        if (DataResult published = write_atomic_datagen_v2_attestation(proof); !published)
        {
            print_error(published.message);
            return false;
        }
        if (!sidecars->take_attestation_ownership(error))
        {
            print_error(error);
            return false;
        }
        if (DataResult bundled = write_openbench_datagen_bundle_v2(output, proof); !bundled)
        {
            print_error(bundled.message);
            return false;
        }
    }
    if (!sidecars->cleanup(error))
    {
        print_error(error);
        return false;
    }

    std::cout << "INFO: openbench_bundle = " << output.string() << '\n'
              << "INFO: openbench_generate_training_data finished." << std::endl;
    return true;
}

}  // namespace Stockfish::Data
