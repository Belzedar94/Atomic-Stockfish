/*
  Stockfish, a UCI chess playing engine derived from Glaurung 2.1
  Copyright (C) 2004-2026 The Stockfish developers (see AUTHORS file)

  Stockfish is free software: you can redistribute it and/or modify
  it under the terms of the GNU General Public License as published by
  the Free Software Foundation, either version 3 of the License, or
  (at your option) any later version.

  Stockfish is distributed in the hope that it will be useful,
  but WITHOUT ANY WARRANTY; without even the implied warranty of
  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
  GNU General Public License for more details.

  You should have received a copy of the GNU General Public License
  along with this program.  If not, see <http://www.gnu.org/licenses/>.
*/

#include "engine.h"

#include <algorithm>
#include <cassert>
#include <filesystem>
#include <deque>
#include <iosfwd>
#include <memory>
#include <ostream>
#include <sstream>
#include <string_view>
#include <utility>
#include <vector>

#include "evaluate.h"
#include "misc.h"
#include "movegen.h"
#include "nnue/nnue_dispatcher.h"
#include "nnue/nnue_common.h"
#include "numa.h"
#include "perft.h"
#include "position.h"
#include "search.h"
#include "shm.h"
#include "syzygy/tbprobe.h"
#include "types.h"
#include "uci_move.h"
#include "ucioption.h"

namespace Stockfish {

namespace NN = Eval::NNUE;

constexpr int  MaxHashMB      = Is64Bit ? 33554432 : 2048;
int            MaxThreads     = std::max(1024, 4 * int(get_hardware_concurrency()));
constexpr auto AtomicStartFEN = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1";

// The default configuration will attempt to group L3 domains up to 32 threads.
// This size was found to be a good balance between the Elo gain of increased
// history sharing and the speed loss from more cross-cache accesses (see
// PR#6526). The user can always explicitly override this behavior.
constexpr NumaAutoPolicy DefaultNumaPolicy = BundledL3Policy{32};

Engine::Engine(std::optional<std::filesystem::path> path) :
    binaryDirectory(path ? CommandLine::get_binary_directory(*path) : std::filesystem::path{}),
    numaContext(NumaConfig::from_system(DefaultNumaPolicy)),
    states(new std::deque<StateInfo>(1)),
    threads(),
    networkFile{std::nullopt, ""},
    network(numaContext) {

    pos.set(AtomicStartFEN, false, &states->back());

    options.add(  //
      "Debug Log File", Option("", [](const Option& o) {
          start_logger(path_from_utf8(std::string(o)));
          return std::nullopt;
      }));

    options.add(  //
      "NumaPolicy", Option("auto", [this](const Option& o) {
          set_numa_config_from_option(o);
          return numa_config_information_as_string() + "\n"
               + thread_allocation_information_as_string();
      }));

    options.add(  //
      "Threads", Option(1, 1, MaxThreads, [this](const Option&) {
          resize_threads();
          return thread_allocation_information_as_string();
      }));

    options.add(  //
      "Hash", Option(16, 1, MaxHashMB, [this](const Option& o) {
          set_tt_size(o);
          return std::nullopt;
      }));

    options.add(  //
      "Clear Hash", Option([this](const Option&) {
          search_clear();
          return std::nullopt;
      }));

    options.add(  //
      "Ponder", Option(false));

    options.add(  //
      "MultiPV", Option(1, 1, MAX_MOVES));

    options.add("Skill Level", Option(20, 0, 20));

    options.add("Move Overhead", Option(10, 0, 5000));

    options.add("nodestime", Option(0, 0, 10000));

    options.add("UCI_Chess960", Option(false));

    // Atomic is a combo, even though it deliberately has a single value. This
    // keeps the public UCI contract compatible with chess-variant GUIs while
    // making it impossible to select rules this specialized engine does not
    // implement.
    options.add("UCI_Variant", Option("atomic var atomic", "atomic"));

    // Compatibility input used by variant-aware tournament runners. Atomic-
    // Stockfish has compiled-in rules, so the configured file is intentionally
    // ignored and UCI_Variant remains fixed to atomic.
    options.add("VariantPath", Option(""));

    options.add(  //
      "SyzygyPath", Option("", [](const Option& o) {
          Tablebases::init(o);
          return std::nullopt;
      }));

    options.add("SyzygyProbeDepth", Option(1, 1, 100));
    options.add("Syzygy50MoveRule", Option(true));
    options.add("SyzygyProbeLimit", Option(6, 0, 6));

    // NNUE is the only evaluation: the classical evaluator was removed, so a
    // network is required to search. `true` is the playing mode; `pure` is the
    // raw network result that the data generators require. `false` is no longer
    // a value: like any other value outside a combo, it is ignored.
    options.add("Use NNUE", Option("true var true var pure", "true"));

    options.add(  //
      "EvalFile", Option(EvalFileDefaultName, [this](const Option& o) {
          load_network(path_from_utf8(std::string(o)));
          return std::nullopt;
      }));

    network = get_default_network();
    threads.clear();
    threads.ensure_network_replicated();
    resize_threads();
}

u64 Engine::perft(const std::string& fen, Depth depth, bool isChess960) {
    return Benchmark::perft(fen, depth, isChess960);
}

bool Engine::go(Search::LimitsType& limits) {
    assert(limits.perft == 0);
    if (!verify_network())
        return false;

    threads.start_thinking(options, pos, states, limits);
    return true;
}
void Engine::stop() { threads.stop = true; }

void Engine::search_clear() {
    wait_for_search_finished();

    tt.clear(threads);
    threads.clear();

    // @TODO wont work with multiple instances
    Tablebases::init(options["SyzygyPath"]);  // Free and remap Atomic tables
}

void Engine::set_on_update_no_moves(std::function<void(const Engine::InfoShort&)>&& f) {
    updateContext.onUpdateNoMoves = std::move(f);
}

void Engine::set_on_update_full(std::function<void(const Engine::InfoFull&)>&& f) {
    updateContext.onUpdateFull = std::move(f);
}

void Engine::set_on_iter(std::function<void(const Engine::InfoIter&)>&& f) {
    updateContext.onIter = std::move(f);
}

void Engine::set_on_bestmove(std::function<void(std::string_view, std::string_view)>&& f) {
    updateContext.onBestmove = std::move(f);
}

void Engine::set_on_verify_network(std::function<void(std::string_view)>&& f) {
    onVerifyNetwork = std::move(f);
}

void Engine::wait_for_search_finished() { threads.main_thread()->wait_for_search_finished(); }

std::optional<PositionSetError> Engine::set_position(const std::string&              fen,
                                                     const std::vector<std::string>& moves) {
    // Drop the old state and create a new one
    states   = StateListPtr(new std::deque<StateInfo>(1));
    auto err = pos.set(fen, options["UCI_Chess960"], &states->back());
    if (err.has_value())
        return err;

    for (const auto& move : moves)
    {
        auto m = UCI::to_move(pos, move);

        if (m == Move::none())
            return PositionSetError("Illegal move: " + move);

        states->emplace_back();
        pos.do_move(m, states->back());
    }

    return std::nullopt;
}

// modifiers

void Engine::set_numa_config_from_option(const std::string& o) {
    if (o == "auto" || o == "system")
    {
        numaContext.set_numa_config(NumaConfig::from_system(DefaultNumaPolicy));
    }
    else if (o == "hardware")
    {
        // Don't respect affinity set in the system.
        numaContext.set_numa_config(NumaConfig::from_system(DefaultNumaPolicy, false));
    }
    else if (o == "none")
    {
        numaContext.set_numa_config(NumaConfig{});
    }
    else
    {
        numaContext.set_numa_config(NumaConfig::from_string(o));
    }

    // Force reallocation of threads in case affinities need to change.
    resize_threads();
    threads.ensure_network_replicated();
}

void Engine::resize_threads() {
    threads.wait_for_search_finished();
    threads.set(numaContext.get_numa_config(), {options, threads, tt, sharedHists, network},
                updateContext);

    // Reallocate the hash with the new threadpool size
    set_tt_size(options["Hash"]);
    threads.ensure_network_replicated();
}

void Engine::set_tt_size(usize mb) {
    wait_for_search_finished();
    tt.resize(mb, threads);
}

void Engine::set_ponderhit(bool b) { threads.main_manager()->ponder = b; }

// network related

Eval::UseNNUEMode Engine::nnue_mode() const {
    return options["Use NNUE"] == "pure" ? Eval::UseNNUEMode::Pure : Eval::UseNNUEMode::True;
}

bool Engine::verify_network() const {
    // There is no evaluation without a network, so a missing or incompatible
    // one stops `go`, `bench` and `eval` with an explicit error.
    const auto file = path_from_utf8(std::string(options["EvalFile"]));
    if (!network->verify(onVerifyNetwork, networkFile, file))
        return false;

    auto statuses = network.get_status_and_errors();
    for (usize i = 0; i < statuses.size(); ++i)
    {
        const auto [status, error] = statuses[i];
        std::string message        = "Network replica " + std::to_string(i + 1) + ": ";
        if (status == SystemWideSharedConstantAllocationStatus::NoAllocation)
        {
            message += "No allocation.";
        }
        else if (status == SystemWideSharedConstantAllocationStatus::LocalMemory)
        {
            message += "Local memory.";
        }
        else if (status == SystemWideSharedConstantAllocationStatus::SharedMemory)
        {
            message += "Shared memory.";
        }
        else
        {
            message += "Unknown status.";
        }

        if (error.has_value())
        {
            message += " " + *error;
        }

        if (onVerifyNetwork)
            onVerifyNetwork(message);
    }

    return true;
}

LargePagePtr<Eval::NNUE::AnyNetwork> Engine::get_default_network() {

    auto         network_ = make_unique_large_page<NN::AnyNetwork>();
    NN::EvalFile candidateFile{std::nullopt, ""};
    if (network_->load(binaryDirectory, std::filesystem::path{}, candidateFile))
        networkFile = std::move(candidateFile);

    return network_;
}

void Engine::load_network(const std::filesystem::path& file) {
    wait_for_search_finished();

    auto         candidate = make_unique_large_page<NN::AnyNetwork>();
    NN::EvalFile candidateFile{std::nullopt, ""};
    if (!candidate->load(binaryDirectory, file, candidateFile))
        return;

    network     = std::move(candidate);
    networkFile = std::move(candidateFile);
    threads.clear();
    threads.ensure_network_replicated();
}

bool Engine::load_authenticated_network(std::istream&                stream,
                                        const std::filesystem::path& logicalPath) {
    wait_for_search_finished();

    auto         candidate = make_unique_large_page<NN::AnyNetwork>();
    NN::EvalFile candidateFile{std::nullopt, ""};
    if (!candidate->load_authenticated(stream, logicalPath, candidateFile))
        return false;

    network     = std::move(candidate);
    networkFile = std::move(candidateFile);
    threads.clear();
    threads.ensure_network_replicated();
    return true;
}

void Engine::save_network(const std::optional<std::filesystem::path>& file) {
    wait_for_search_finished();
    network->save(networkFile, file);
}

// utility functions

void Engine::trace_eval() const {
    StateListPtr trace_states(new std::deque<StateInfo>(1));
    Position     p;
    p.set(pos.fen(), options["UCI_Chess960"], &trace_states->back());

    if (!verify_network())
        return;

    sync_cout << "\n" << Eval::trace(p, *network, nnue_mode()) << sync_endl;
}

// Plays random legal Atomic games (captures, explosions, castling, promotions,
// random take-backs) and compares, at random plies, the evaluation obtained from
// the incrementally updated accumulator stack with one computed from scratch
// (stack reset and Finny caches cleared). Any difference is a hard failure.
// Usage: nnuecheck [games] [maxPlies] [seed]
void Engine::nnue_check(std::istream& is) const {
    int  games = 200, maxPlies = 160;
    u64  seed  = 20261106;
    is >> games >> maxPlies >> seed;
    if (!seed)
        seed = 1;

    if (!verify_network())
        return;

    const Eval::NNUE::AnyNetwork& net = *network;

    auto incremental = std::make_unique<Eval::NNUE::AnyAccumulator>(net);
    auto fresh       = std::make_unique<Eval::NNUE::AnyAccumulator>(net);

    static const char* const StartFens[] = {
      "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1",
      "r3k2r/pppqbppp/2n1bn2/3pp3/3PP3/2N1BN2/PPPQBPPP/R3K2R w KQkq - 0 1",
      "r1bqkb1r/pppp1ppp/2n2n2/4p3/2B1P3/5N2/PPPP1PPP/RNBQK2R w KQkq - 4 4",
      "4k3/1P4P1/8/2n2b2/2N2B2/8/1p4p1/4K3 w - - 0 1",
      "r3k2r/1P4P1/8/3qQ3/8/8/1p4p1/R3K2R w KQkq - 0 1",
      "rnbqkbnr/ppp1p1pp/8/3pPp2/8/8/PPPP1PPP/RNBQKBNR w KQkq f6 0 3",
    };

    PRNG rng(seed);
    u64  checks = 0, mismatches = 0, moves = 0, captures = 0, undos = 0, evalsAfterBlast = 0;

    for (int g = 0; g < games && mismatches < 10; ++g)
    {
        StateListPtr gameStates(new std::deque<StateInfo>(1));
        Position     gamePos;
        const char*  fen = StartFens[g % (sizeof(StartFens) / sizeof(StartFens[0]))];
        gamePos.set(fen, false, &gameStates->back());
        incremental->reset();

        std::vector<Move> line;
        bool              lastWasBlast = false;

        for (int ply = 0; ply < maxPlies; ++ply)
        {
            const bool undo = !line.empty() && rng.rand<u64>() % 100 < 15;
            if (undo)
            {
                gamePos.undo_move(line.back());
                line.pop_back();
                incremental->pop();
                gameStates->pop_back();
                ++undos;
                lastWasBlast = false;
            }
            else
            {
                MoveList<LEGAL> legal(gamePos);
                if (!legal.size())
                    break;

                const Move m = *(legal.begin() + rng.rand<u64>() % legal.size());
                gameStates->emplace_back();
                DirtyPiece& dp = incremental->push();
                gamePos.do_move(m, gameStates->back(), gamePos.gives_check(m), dp, nullptr, nullptr);
                incremental->after_move(gamePos);
                line.push_back(m);
                ++moves;
                lastWasBlast = dp.atomicBlast.size() > 0;
                captures += lastWasBlast;

                if (gamePos.is_atomic_terminal())
                {
                    // A king exploded: take the move back and go on.
                    gamePos.undo_move(m);
                    line.pop_back();
                    incremental->pop();
                    gameStates->pop_back();
                    lastWasBlast = false;
                    continue;
                }
            }

            if (rng.rand<u64>() % 100 < 50)
                continue;

            const auto [ip, iq] = net.evaluate_raw(gamePos, *incremental);
            fresh->rebind(net);
            const auto [fp, fq] = net.evaluate_raw(gamePos, *fresh);
            ++checks;
            evalsAfterBlast += lastWasBlast;

            if (ip != fp || iq != fq
                || incremental->debug_latest_hash() != fresh->debug_latest_hash())
            {
                ++mismatches;
                std::ostringstream ss;
                ss << "nnuecheck MISMATCH game " << g << " ply " << ply << " fen " << gamePos.fen()
                   << " incremental (" << ip << ", " << iq << ") fresh (" << fp << ", " << fq
                   << ") line:";
                for (Move lm : line)
                    ss << ' ' << UCI::move(lm, false);
                sync_cout << ss.str() << sync_endl;
            }
        }
    }

    sync_cout << "nnuecheck backend " << net.backend_name() << " games " << games << " moves "
              << moves << " captures " << captures << " undos " << undos << " checks " << checks
              << " checks_right_after_blast " << evalsAfterBlast << " mismatches " << mismatches
              << (mismatches ? " FAIL" : " OK") << sync_endl;
}

const OptionsMap& Engine::get_options() const { return options; }
OptionsMap&       Engine::get_options() { return options; }

std::string Engine::fen() const { return pos.fen(); }

void Engine::flip() { pos.flip(); }

std::string Engine::visualize() const {
    std::stringstream ss;
    ss << pos;
    return ss.str();
}

int Engine::get_hashfull(int maxAge) const { return tt.hashfull(maxAge); }

std::vector<std::pair<usize, usize>> Engine::get_bound_thread_count_by_numa_node() const {
    auto                                 counts = threads.get_bound_thread_count_by_numa_node();
    const NumaConfig&                    cfg    = numaContext.get_numa_config();
    std::vector<std::pair<usize, usize>> ratios;
    NumaIndex                            n = 0;
    for (; n < counts.size(); ++n)
        ratios.emplace_back(counts[n], cfg.num_cpus_in_numa_node(n));
    if (!counts.empty())
        for (; n < cfg.num_numa_nodes(); ++n)
            ratios.emplace_back(0, cfg.num_cpus_in_numa_node(n));
    return ratios;
}

std::string Engine::get_numa_config_as_string() const {
    return numaContext.get_numa_config().to_string();
}

std::string Engine::numa_config_information_as_string() const {
    auto cfgStr = get_numa_config_as_string();
    return "Available processors: " + cfgStr;
}

std::string Engine::thread_binding_information_as_string() const {
    auto              boundThreadsByNode = get_bound_thread_count_by_numa_node();
    std::stringstream ss;
    if (boundThreadsByNode.empty())
        return ss.str();

    bool isFirst = true;

    for (auto&& [current, total] : boundThreadsByNode)
    {
        if (!isFirst)
            ss << ":";
        ss << current << "/" << total;
        isFirst = false;
    }

    return ss.str();
}

std::string Engine::thread_allocation_information_as_string() const {
    std::stringstream ss;

    usize threadsSize = threads.size();
    ss << "Using " << threadsSize << (threadsSize > 1 ? " threads" : " thread");

    auto boundThreadsByNodeStr = thread_binding_information_as_string();
    if (boundThreadsByNodeStr.empty())
        return ss.str();

    ss << " with NUMA node thread binding: ";
    ss << boundThreadsByNodeStr;

    return ss.str();
}
}
