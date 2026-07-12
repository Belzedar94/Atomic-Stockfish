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
#include "engine.h"
#include "legacy_atomic_v1.h"
#include "misc.h"
#include "movegen.h"
#include "position.h"
#include "random_seed.h"
#include "search.h"
#include "thread.h"
#include "tt.h"

namespace Stockfish::Data {
namespace {

constexpr std::string_view AtomicStartFen =
  "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1";
constexpr u64 ReportEvery         = 5000;
constexpr u64 MinimumShardRecords = 200000;
constexpr u64 MaximumShardFiles   = 100000;
constexpr int MaximumGeneratedPly = 4096;
constexpr int LegacyKnownWin      = 10000;
// Preserve the historical direct-mapped 64M-key deduplication table. Reducing
// it changes collision/false-positive rates and therefore dataset selection.
constexpr usize DedupeTableSize = usize(64) * 1024 * 1024;

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

    double keepDraws                    = 1.0;
    bool   adjudicateDrawsByScore       = true;
    bool   adjudicateInsufficient       = true;
    bool   filterCaptures               = true;
    bool   filterChecks                 = false;
    bool   filterPromotions             = false;
    bool   randomFileName               = false;
    bool   setRecommendedUciOptionsSeen = false;
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

bool parse_params(std::istream& input, GeneratorParams& params, std::string& error) {
    std::string dataFormat = "bin";
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
            if (!read_value(input, params.keepDraws, token, error))
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
            if (!read_value(input, dataFormat, token, error))
                return false;
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

    if (dataFormat != "bin")
    {
        error = "Legacy Atomic V1 data_format must be bin";
        return false;
    }

    if (!searchDepthMaxSeen)
        params.searchDepthMax = params.searchDepthMin;
    params.randomMultiPvDepth = std::max(params.searchDepthMax, params.randomMultiPvDepth);
    if (params.saveEvery != std::numeric_limits<u64>::max())
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
      || params.keepDraws > 1.0 || params.outputFile.empty();

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

std::filesystem::path shard_path(const std::string& rawName, u64 shard) {
    std::string name = rawName;
    if (shard)
        name += "_" + std::to_string(shard);
    if (!ends_with(name, ".bin"))
        name += ".bin";
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
        paths.push_back(shard_path(params.outputFile, shard));
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

bool load_book(const std::string& path, std::vector<std::string>& positions, std::string& error) {
    if (path.empty())
    {
        positions.emplace_back(AtomicStartFen);
        return true;
    }

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

        Position  position;
        StateInfo state{};
        if (position.set(*candidate, false, &state) || !position.has_king(WHITE)
            || !position.has_king(BLACK) || position.is_chess960()
            || Atomic::outcome(position, true, 0).terminal())
        {
            error = "Opening book contains an invalid or unsupported Atomic FEN: " + *candidate;
            return false;
        }
        positions.push_back(position.fen());
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

class OutputSeries {
   public:
    OutputSeries(std::vector<std::filesystem::path> paths_, u64 recordsPerFile_) :
        paths(std::move(paths_)),
        recordsPerFile(recordsPerFile_) {}

    DataResult append(const TrainingDataSample& sample) {
        if (shard >= paths.size())
            return DataResult::failure(DataError::WRITE_FAILED,
                                       "Training-data output shard capacity was exhausted");

        if (!sink)
            sink = std::make_unique<LegacyAtomicV1Sink>(paths[usize(shard)]);

        if (recordsInShard == recordsPerFile)
        {
            if (DataResult result = sink->finalize(); !result)
                return result;
            ++finalizedShards;
            sink.reset();
            ++shard;
            recordsInShard = 0;
            if (shard >= paths.size())
                return DataResult::failure(DataError::WRITE_FAILED,
                                           "Training-data output shard capacity was exhausted");
            sink = std::make_unique<LegacyAtomicV1Sink>(paths[usize(shard)]);
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
        return sink->finalize();
    }

    DataResult abort() {
        DataResult first = DataResult::success();
        if (sink)
        {
            if (DataResult result = sink->abort(); !result)
                first = result;
            sink.reset();
        }

        for (u64 i = 0; i < finalizedShards; ++i)
        {
            const auto&     path = paths[usize(i)];
            std::error_code ec;
            std::filesystem::remove(path, ec);
            if (ec && first)
                first = DataResult::failure(DataError::ABORT_FAILED, "Cannot remove partial output "
                                                                       + path.string() + ": "
                                                                       + ec.message());
        }
        return first;
    }

    u64 records_written() const { return totalRecords; }

   private:
    std::vector<std::filesystem::path>  paths;
    u64                                 recordsPerFile;
    u64                                 shard           = 0;
    u64                                 recordsInShard  = 0;
    u64                                 totalRecords    = 0;
    u64                                 finalizedShards = 0;
    std::unique_ptr<LegacyAtomicV1Sink> sink;
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
                                                                    : params_.saveEvery),
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
            if (const auto setError = position.set(initialFen, false, &rootState))
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
                    && legacy_atomic_v1_rule50_fits(position.rule50_count())
                    && !seen.already_seen(position.key()) && position.has_king(WHITE)
                    && position.has_king(BLACK)
                    && !position.atomic_in_check(position.side_to_move()) && stableTarget
                    && !(params.filterCaptures && position.capture(bestMove))
                    && !(params.filterChecks && position.gives_check(bestMove))
                    && !(params.filterPromotions && bestMove.type_of() == PROMOTION))
                {
                    samples.push_back(
                      {position.fen(), score, bestMove, ply, 0, NO_TRAINING_DATA_FLAGS});
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
        std::istringstream chess960("name UCI_Chess960 value false");
        engine.options.setoption(chess960);
    }

    if (engine.options["Use NNUE"] != "pure")
    {
        print_error("generate_training_data requires Use NNUE=pure");
        return false;
    }
    if (int(engine.options["UCI_Chess960"]) != 0)
    {
        print_error("Legacy Atomic V1 cannot encode Atomic960; set UCI_Chess960=false");
        return false;
    }
    if (!engine.verify_network())
    {
        print_error("generate_training_data requires a valid compatible Atomic NNUE network");
        return false;
    }

    const u64 resolvedSeed = ReplayablePRNG::resolve(params.seedText);
    if (params.randomFileName)
        params.outputFile += random_suffix(resolvedSeed);

    std::vector<std::string> openings;
    if (!load_book(params.book, openings, error))
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
    if (!preflight_output_paths(*paths, error))
    {
        print_error(error);
        return false;
    }

    std::cout << "INFO: Executing generate_training_data command\n"
              << "INFO: schema_sha256 = "
              << "acca0f551f1c012c31a6c727dedccaebb7b5ebbc46810edb87e31bb208d5abe1\n"
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

    std::cout << "INFO: generate_training_data finished.\n"
              << "INFO: records=" << generator.records_written()
              << " draws=" << generator.draws_written() << std::endl;
    return true;
}

}  // namespace Stockfish::Data
