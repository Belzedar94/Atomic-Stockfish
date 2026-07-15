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
#include <sstream>
#include <string>
#include <string_view>
#include <system_error>
#include <utility>

#include "atomic_bin_v2_manifest.h"
#include "engine.h"
#include "misc.h"
#include "openbench_bundle.h"
#include "sha256.h"
#include "training_data_generator.h"

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
    std::string output;
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
    while (input >> token)
    {
        if (token == "threads")
        {
            if (!read_positive_int(input, params.threads, token, error))
                return false;
        }
        else if (token == "hash")
        {
            if (!read_positive_int(input, params.hashMb, token, error))
                return false;
        }
        else if (token == "network")
        {
            if (!read_token(input, params.network, token, error))
                return false;
        }
        else if (token == "network_sha256")
        {
            if (!read_token(input, params.networkSha256, token, error))
                return false;
        }
        else if (token == "count")
        {
            if (!read_token(input, params.count, token, error))
                return false;
        }
        else if (token == "seed")
        {
            if (!read_token(input, params.seed, token, error))
                return false;
        }
        else if (token == "book")
        {
            if (!read_token(input, params.book, token, error))
                return false;
        }
        else if (token == "book_sha256")
        {
            if (!read_token(input, params.bookSha256, token, error))
                return false;
        }
        else if (token == "out")
        {
            if (!read_token(input, params.output, token, error))
                return false;
        }
        else if (token == "set_recommended_uci_options")
            forwarded << ' ' << token;
        else if (generator_option_takes_value(token))
        {
            std::string value;
            if (!read_token(input, value, token, error))
                return false;
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
    if (!params.bookSha256.empty() && !normalize_sha256(params.bookSha256))
    {
        error = "book_sha256 must contain exactly 64 hexadecimal characters";
        return false;
    }
    if (params.book == "NONE" && !params.bookSha256.empty())
    {
        error = "book_sha256 cannot be supplied with book NONE";
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
                              std::string&                 error) {
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

    bool preflight(std::string& error) {
        std::error_code ec;
        for (const auto* sidecar : {&shard, &manifest})
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

    bool take_ownership(std::string& error) {
        return shard.capture(error) && manifest.capture(error);
    }

    bool cleanup(std::string& error) {
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
        return report(manifest, manifestStatus) && report(shard, shardStatus);
    }

   private:
    OwnedSidecar shard;
    OwnedSidecar manifest;
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

    const auto        shard    = std::filesystem::path(output.string() + ".atbin");
    const auto        manifest = atomic_bin_v2_manifest_path(shard);
    GeneratedSidecars sidecars(shard, manifest);
    if (!sidecars.preflight(error))
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
    set_option(engine, "SyzygyPath", "<empty>");
    set_option(engine, "EvalFile", params.network);
    set_option(engine, "Use NNUE", "pure");

    if (int(engine.get_options()["Threads"]) != params.threads
        || int(engine.get_options()["Hash"]) != params.hashMb
        || int(engine.get_options()["UCI_Chess960"]) != 0
        || !std::string(engine.get_options()["SyzygyPath"]).empty()
        || std::string(engine.get_options()["EvalFile"]) != params.network
        || engine.get_options()["Use NNUE"] != "pure")
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
    if (!generate_training_data(engine, generatorInput))
        return false;
    if (!sidecars.take_ownership(error))
    {
        print_error(error);
        return false;
    }

    AtomicBinV2Manifest authenticatedManifest;
    if (DataResult loaded = load_atomic_bin_v2_manifest(manifest, authenticatedManifest); !loaded)
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
    if (!sidecars.cleanup(error))
    {
        print_error(error);
        return false;
    }

    std::cout << "INFO: openbench_bundle = " << output.string() << '\n'
              << "INFO: openbench_generate_training_data finished." << std::endl;
    return true;
}

}  // namespace Stockfish::Data
