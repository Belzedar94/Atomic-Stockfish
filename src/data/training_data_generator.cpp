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
#include <atomic>
#include <chrono>
#include <cmath>
#include <cctype>
#include <cstdint>
#include <filesystem>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <limits>
#include <locale>
#include <memory>
#include <mutex>
#include <optional>
#include <sstream>
#include <string>
#include <string_view>
#include <system_error>
#include <utility>
#include <vector>

#include "api/atomic_outcome.h"
#include "atomic_bin_v2_manifest.h"
#include "atomic_bin_v2_sink.h"
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
    std::string token;
    if (!(input >> token) || token.empty() || token.size() > 4096)
    {
        error = "Missing or invalid value for generate_training_data option keep_draws";
        return false;
    }

    std::istringstream numeric(token);
    numeric.imbue(std::locale::classic());
    double parsed = 0.0;
    if (!(numeric >> parsed) || !std::isfinite(parsed) || parsed < 0.0 || parsed > 1.0)
    {
        error = "keep_draws must be a finite decimal value between 0 and 1";
        return false;
    }
    char trailing = 0;
    if (numeric >> trailing)
    {
        error = "keep_draws must be a finite decimal value between 0 and 1";
        return false;
    }
    if (parsed == 0.0)
    {
        value     = 0.0;
        canonical = "0";
        return true;
    }

    std::size_t cursor   = 0;
    bool        negative = false;
    if (token[cursor] == '+' || token[cursor] == '-')
    {
        negative = token[cursor] == '-';
        ++cursor;
    }
    if (negative || cursor == token.size())
    {
        error = "keep_draws must be a non-negative decimal value";
        return false;
    }

    std::string digits;
    bool        sawDigit       = false;
    bool        sawPoint       = false;
    long long   fractionalSize = 0;
    while (cursor < token.size() && token[cursor] != 'e' && token[cursor] != 'E')
    {
        const unsigned char byte = token[cursor++];
        if (byte == '.')
        {
            if (sawPoint)
            {
                error = "keep_draws contains more than one decimal point";
                return false;
            }
            sawPoint = true;
            continue;
        }
        if (byte < '0' || byte > '9')
        {
            error = "keep_draws must use decimal notation";
            return false;
        }
        sawDigit = true;
        digits.push_back(char(byte));
        if (sawPoint)
            ++fractionalSize;
    }
    if (!sawDigit)
    {
        error = "keep_draws must contain decimal digits";
        return false;
    }

    long long exponent = 0;
    if (cursor < token.size())
    {
        ++cursor;
        bool exponentNegative = false;
        if (cursor < token.size() && (token[cursor] == '+' || token[cursor] == '-'))
        {
            exponentNegative = token[cursor] == '-';
            ++cursor;
        }
        if (cursor == token.size())
        {
            error = "keep_draws exponent is missing decimal digits";
            return false;
        }
        for (; cursor < token.size(); ++cursor)
        {
            const unsigned char byte = token[cursor];
            if (byte < '0' || byte > '9')
            {
                error = "keep_draws exponent must contain decimal digits only";
                return false;
            }
            const int digit = byte - '0';
            exponent        = std::min(1000000LL, exponent * 10 + digit);
        }
        if (exponentNegative)
            exponent = -exponent;
    }

    const auto firstNonZero = digits.find_first_not_of('0');
    if (firstNonZero == std::string::npos)
    {
        value     = 0.0;
        canonical = "0";
        return true;
    }
    digits.erase(0, firstNonZero);
    long long trailingZeros = 0;
    while (digits.size() > 1 && digits.back() == '0')
    {
        digits.pop_back();
        ++trailingZeros;
    }

    const long long point =
      static_cast<long long>(digits.size()) + exponent - fractionalSize + trailingZeros;
    if (point > 1 || (point == 1 && digits != "1"))
    {
        error = "keep_draws exact decimal value exceeds 1";
        return false;
    }

    if (point == 1)
        canonical = "1";
    else
    {
        canonical = "0.";
        if (point < 0)
            canonical.append(std::size_t(-point), '0');
        canonical += digits;
    }
    value = parsed;
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
};

bool load_book(const std::string&        path,
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

std::optional<std::filesystem::path> absolute_regular_file(const std::filesystem::path& path) {
    std::error_code ec;
    auto            absolute = std::filesystem::absolute(path, ec);
    if (ec)
        return std::nullopt;
    absolute = absolute.lexically_normal();
    if (!std::filesystem::is_regular_file(absolute, ec) || ec)
        return std::nullopt;
    return absolute;
}

bool authenticate_network(Engine&                      engine,
                          const std::filesystem::path& binaryDirectory,
                          Eval::NNUE::EvalFile&        networkFile,
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
        const auto absolute = absolute_regular_file(candidate);
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
        std::string beforeSha;
        u64         beforeSize = 0;
        if (DataResult before = sha256_file(candidate, beforeSha, beforeSize); !before)
        {
            error = before.message;
            return false;
        }

        std::istringstream option("name EvalFile value " + path_to_utf8(candidate));
        // Force a byte reload even when the resolved pathname equals the
        // already-selected EvalFile. Otherwise a file changed after the UCI
        // setoption could be hashed while stale in-memory weights remain live.
        networkFile.current.reset();
        engine.get_options().setoption(option);
        if (!engine.verify_network())
            continue;

        std::string afterSha;
        u64         afterSize = 0;
        if (DataResult after = sha256_file(candidate, afterSha, afterSize); !after)
        {
            error = after.message;
            return false;
        }
        if (beforeSha != afterSha || beforeSize != afterSize)
        {
            error = "Atomic BIN V2 EvalFile changed while it was being loaded";
            return false;
        }

        authenticated = {candidate, std::move(afterSha), afterSize};
        return true;
    }

    error = "Atomic BIN V2 could not authenticate and load the selected compatible EvalFile";
    return false;
}

bool authenticate_book_and_load(const std::string&        requested,
                                bool                      atomic960,
                                std::vector<std::string>& positions,
                                AuthenticatedFile&        authenticated,
                                std::string&              error) {
    const auto path = absolute_regular_file(path_from_utf8(requested));
    if (!path)
    {
        error = "Cannot authenticate training-data opening book: " + requested;
        return false;
    }

    std::string beforeSha;
    u64         beforeSize = 0;
    if (DataResult before = sha256_file(*path, beforeSha, beforeSize); !before)
    {
        error = before.message;
        return false;
    }
    if (!load_book(path_to_utf8(*path), atomic960, positions, error))
        return false;

    std::string afterSha;
    u64         afterSize = 0;
    if (DataResult after = sha256_file(*path, afterSha, afterSize); !after)
    {
        error = after.message;
        return false;
    }
    if (beforeSha != afterSha || beforeSize != afterSize)
    {
        error = "Atomic BIN V2 opening book changed while it was being loaded";
        return false;
    }

    authenticated = {*path, std::move(afterSha), afterSize};
    return true;
}

bool dataset_rule50_fits(DatasetFormat dataFormat, int rule50) {
    if (dataFormat == DatasetFormat::LEGACY_ATOMIC_V1)
        return legacy_atomic_v1_rule50_fits(rule50);
    return rule50 >= 0 && u64(rule50) <= AtomicBinV2MaxRule50;
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

bool load_book(const std::string&        path,
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

    if (path.empty())
        return append_position(std::string(AtomicStartFen));

    std::ifstream input(path);
    if (!input)
    {
        error = "Cannot open training-data opening book: " + path;
        return false;
    }

    std::string line;
    while (std::getline(input, line))
    {
        auto candidate = normalize_book_line(std::move(line));
        if (!candidate)
            continue;

        if (!append_position(*candidate))
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
                    && dataset_rule50_fits(params.dataFormat, position.rule50_count())
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
        if (!authenticate_network(engine, engine.binaryDirectory, engine.networkFile,
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
    const bool               bookLoaded =
      params.dataFormat == DatasetFormat::ATOMIC_BIN_V2 && bookIsFile
                      ? authenticate_book_and_load(params.book, params.atomic960, openings, bookMetadata, error)
                      : load_book(params.book, params.atomic960, openings, error);
    if (!bookLoaded)
    {
        print_error(error);
        return false;
    }

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

}  // namespace Stockfish::Data
