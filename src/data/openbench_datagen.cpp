/*
  Atomic-Stockfish, a specialized Atomic Chess engine derived from Stockfish
  Copyright (C) 2004-2026 The Atomic-Stockfish developers

  Atomic-Stockfish is free software: you can redistribute it and/or modify
  it under the terms of the GNU General Public License as published by
  the Free Software Foundation, either version 3 of the License, or
  (at your option) any later version.
*/

#include "openbench_datagen.h"

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
#include "openbench_bundle.h"
#include "training_data_generator.h"

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

class GeneratedSidecars {
   public:
    GeneratedSidecars(std::filesystem::path shardPath, std::filesystem::path manifestPath) :
        shard(std::move(shardPath)),
        manifest(std::move(manifestPath)) {}

    bool preflight(std::string& error) {
        std::error_code ec;
        for (const auto& path : {shard, manifest})
        {
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

    ~GeneratedSidecars() { cleanup(); }

    void take_ownership() noexcept { owned = true; }

    void cleanup() noexcept {
        if (!owned)
            return;
        std::error_code ignored;
        std::filesystem::remove(manifest, ignored);
        std::filesystem::remove(shard, ignored);
        owned = false;
    }

   private:
    std::filesystem::path shard;
    std::filesystem::path manifest;
    bool                  owned = false;
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

    const auto output = std::filesystem::path(params.output).lexically_normal();
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
    sidecars.take_ownership();

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

    std::cout << "INFO: openbench_bundle = " << output.string() << '\n'
              << "INFO: openbench_generate_training_data finished." << std::endl;
    return true;
}

}  // namespace Stockfish::Data
