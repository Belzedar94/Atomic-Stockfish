/*
  Atomic-Stockfish, a specialized Atomic Chess engine derived from Stockfish
  Copyright (C) 2004-2026 The Stockfish developers (see AUTHORS file)

  Atomic-Stockfish is free software: you can redistribute it and/or modify
  it under the terms of the GNU General Public License as published by
  the Free Software Foundation, either version 3 of the License, or
  (at your option) any later version.
*/

#include <algorithm>
#include <array>
#include <cstdint>
#include <cstdlib>
#include <filesystem>
#include <iomanip>
#include <iostream>
#include <limits>
#include <memory>
#include <sstream>
#include <string>
#include <string_view>
#include <tuple>
#include <vector>

#include "attacks.h"
#include "bitboard.h"
#include "evaluate.h"
#include "misc.h"
#include "movegen.h"
#include "nnue/network.h"
#include "nnue/nnue_accumulator.h"
#include "nnue/nnue_misc.h"
#include "position.h"
#include "uci.h"
#include "uci_move.h"

namespace Stockfish {
namespace {

namespace NNUE = Eval::NNUE;

constexpr std::string_view StartFEN =
  "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1";

struct Context {
    u64         seed;
    std::string fen;
    std::string move;
    std::string phase;
};

[[noreturn]] void fail(const Context& context, const std::string& detail) {
    std::ostringstream out;
    out << detail << "\nseed=0x" << std::hex << std::uppercase << context.seed << std::dec
        << "\nphase=" << context.phase << "\nFEN=" << context.fen
        << "\nmove=" << (context.move.empty() ? "(none)" : context.move);
    std::cerr << "LegacyAtomicV1 incremental gate FAILED\n" << out.str() << '\n';
    std::exit(EXIT_FAILURE);
}

[[noreturn]] void die(const std::string& detail) {
    std::cerr << "LegacyAtomicV1 incremental gate FAILED\n" << detail << '\n';
    std::exit(EXIT_FAILURE);
}

struct Snapshot {
    NNUE::RawNetworkOutput raw;
    Value                  trueValue;
    Value                  pureValue;
    NNUE::AccumulatorState accumulator;
    std::string            fen;
    Key                    key;
};

Snapshot take_snapshot(const Position&       pos,
                       const NNUE::Network&  network,
                       NNUE::AccumulatorStack& stack,
                       NNUE::AccumulatorCaches& caches) {
    const auto raw = network.evaluate_raw(pos, stack, caches);
    const auto trueValue =
      Eval::evaluate(network, pos, stack, caches, VALUE_ZERO, Eval::UseNNUEMode::True);
    const auto pureValue =
      Eval::evaluate(network, pos, stack, caches, VALUE_ZERO, Eval::UseNNUEMode::Pure);

    return {raw, trueValue, pureValue, stack.latest(), pos.fen(), pos.key()};
}

std::string raw_string(const NNUE::RawNetworkOutput& raw) {
    const auto [psqt, positional] = raw;
    return "(" + std::to_string(psqt) + ", " + std::to_string(positional) + ")";
}

void compare_snapshots(const Snapshot& expected, const Snapshot& actual, const Context& context) {
    if (actual.fen != expected.fen)
        fail(context, "FEN mismatch: expected " + expected.fen + ", got " + actual.fen);

    if (actual.key != expected.key)
    {
        std::ostringstream detail;
        detail << "key mismatch: expected 0x" << std::hex << expected.key << ", got 0x"
               << actual.key;
        fail(context, detail.str());
    }

    if (actual.raw != expected.raw)
        fail(context, "raw NNUE mismatch: expected " + raw_string(expected.raw) + ", got "
                        + raw_string(actual.raw));

    if (actual.trueValue != expected.trueValue || actual.pureValue != expected.pureValue)
        fail(context,
             "scaled NNUE mismatch: expected true=" + std::to_string(expected.trueValue)
               + " pure=" + std::to_string(expected.pureValue) + ", got true="
               + std::to_string(actual.trueValue) + " pure=" + std::to_string(actual.pureValue));

    for (Color perspective : {WHITE, BLACK})
    {
        if (!actual.accumulator.computed[perspective]
            || !expected.accumulator.computed[perspective])
            fail(context, "accumulator perspective was not computed");

        for (usize i = 0; i < NNUE::TransformedFeatureDimensions; ++i)
            if (actual.accumulator.accumulation[perspective][i]
                != expected.accumulator.accumulation[perspective][i])
                fail(context,
                     "feature-transformer mismatch for perspective="
                       + std::to_string(int(perspective)) + " index=" + std::to_string(i)
                       + ": expected="
                       + std::to_string(expected.accumulator.accumulation[perspective][i])
                       + " got="
                       + std::to_string(actual.accumulator.accumulation[perspective][i]));

        for (usize bucket = 0; bucket < NNUE::PSQTBuckets; ++bucket)
            if (actual.accumulator.psqtAccumulation[perspective][bucket]
                != expected.accumulator.psqtAccumulation[perspective][bucket])
                fail(context,
                     "PSQT accumulator mismatch for perspective="
                       + std::to_string(int(perspective)) + " bucket=" + std::to_string(bucket)
                       + ": expected="
                       + std::to_string(expected.accumulator.psqtAccumulation[perspective][bucket])
                       + " got="
                       + std::to_string(actual.accumulator.psqtAccumulation[perspective][bucket]));
    }
}

Snapshot full_refresh_snapshot(const Position& pos, const NNUE::Network& network) {
    NNUE::AccumulatorStack  freshStack;
    NNUE::AccumulatorCaches freshCaches(network);
    freshStack.reset();
    return take_snapshot(pos, network, freshStack, freshCaches);
}

Value evaluate_fen(std::string_view fen,
                   const NNUE::Network& network,
                   Eval::UseNNUEMode mode) {
    Position  pos;
    StateInfo state{};
    if (auto error = pos.set(std::string(fen), false, &state))
        die("invalid rule50 evaluation fixture: " + std::string(error->what()));

    auto stack  = std::make_unique<NNUE::AccumulatorStack>();
    auto caches = std::make_unique<NNUE::AccumulatorCaches>(network);
    stack->reset();
    return Eval::evaluate(network, pos, *stack, *caches, VALUE_ZERO, mode);
}

void run_rule50_damping(const NNUE::Network& network) {
    constexpr std::string_view At99 = "7k/8/8/8/8/8/Q7/K7 w - - 99 1";
    constexpr std::string_view At100 = "7k/8/8/8/8/8/Q7/K7 w - - 100 1";
    constexpr std::string_view Beyond = "7k/8/8/8/8/8/Q7/K7 w - - 150 1";

    const Value classical99 = evaluate_fen(At99, network, Eval::UseNNUEMode::False);
    if (classical99 <= VALUE_ZERO)
        die("rule50 damping erased or reversed a positive evaluation before the boundary");

    for (const auto mode : {Eval::UseNNUEMode::False, Eval::UseNNUEMode::True})
    {
        if (evaluate_fen(At100, network, mode) != VALUE_ZERO)
            die("rule50 damping did not neutralize evaluation at 100 reversible plies");
        if (evaluate_fen(Beyond, network, mode) != VALUE_ZERO)
            die("rule50 damping reversed evaluation beyond 100 reversible plies");
    }

    std::cout << "PASS rule50 evaluation damping at and beyond draw boundary\n";
}

struct Fixture {
    std::string_view name;
    std::string_view fen;
    std::string_view move;
    bool             chess960;
    bool             requiresAtomicRefresh;
};

constexpr std::array FixedFixtures = {
  Fixture{"quiet", StartFEN, "e2e4", false, false},
  Fixture{"capture", "7k/8/8/8/3p4/2B5/8/K7 w - - 0 1", "c3d4", false, true},
  Fixture{"explosion-with-bycatch", "7k/8/8/2pBn3/3r4/2PQN3/8/K7 w - - 0 1",
          "d3d4", false, true},
  Fixture{"en-passant", "7k/8/2N1b3/2ppP3/8/8/8/K7 w - d6 0 2", "e5d6", false,
          true},
  Fixture{"promotion", "7k/P7/8/8/8/8/8/K7 w - - 0 1", "a7a8q", false, false},
  Fixture{"capture-promotion", "k5br/6P1/8/8/8/8/8/K7 w - - 0 1", "g7h8q", false,
          true},
  Fixture{"castling", "4k3/8/8/8/8/8/8/R3K2R w KQ - 0 1", "e1g1", false, false},
  Fixture{"atomic960-castling", "7k/8/8/8/8/8/8/1RK5 w Q - 0 1", "c1b1", true,
          false},
};

void run_fixed_fixture(const Fixture& fixture, const NNUE::Network& network) {
    Position  pos;
    StateInfo rootState{};
    StateInfo childState{};

    if (auto error = pos.set(std::string(fixture.fen), fixture.chess960, &rootState))
        die(std::string("invalid fixed fixture ") + std::string(fixture.name) + ": "
            + error->what());

    const Move move = UCI::to_move(pos, std::string(fixture.move));
    Context context{0, pos.fen(), std::string(fixture.move), std::string(fixture.name)};
    if (!move)
        fail(context, "fixture move is not legal");

    NNUE::AccumulatorStack  incremental;
    NNUE::AccumulatorCaches caches(network);
    incremental.reset();

    const Snapshot before = take_snapshot(pos, network, incremental, caches);
    compare_snapshots(full_refresh_snapshot(pos, network), before, context);

    auto [dirtyPiece, dirtyThreats] = incremental.push();
    pos.do_move(move, childState, pos.gives_check(move), dirtyPiece, dirtyThreats, nullptr, nullptr);

    if (dirtyPiece.requiresRefresh != fixture.requiresAtomicRefresh)
        fail(context,
             "unexpected Atomic full-refresh flag: expected="
               + std::to_string(fixture.requiresAtomicRefresh) + " got="
               + std::to_string(dirtyPiece.requiresRefresh));

    context.phase = std::string(fixture.name) + ":after-move";
    const Snapshot after = take_snapshot(pos, network, incremental, caches);
    compare_snapshots(full_refresh_snapshot(pos, network), after, context);

    pos.undo_move(move);
    incremental.pop();
    context.phase = std::string(fixture.name) + ":after-undo";
    context.fen   = pos.fen();
    const Snapshot undone = take_snapshot(pos, network, incremental, caches);
    compare_snapshots(before, undone, context);
    compare_snapshots(full_refresh_snapshot(pos, network), undone, context);

    std::cout << "PASS fixed " << fixture.name << '\n';
}

struct RandomSequence {
    u64              seed;
    std::string_view fen;
    bool             chess960;
};

constexpr std::array RandomSequences = {
  RandomSequence{0x243F6A8885A308D3ULL, StartFEN, false},
  RandomSequence{0x13198A2E03707344ULL,
                 "7k/8/8/2pBn3/3r4/2PQN3/8/K7 w - - 0 1", false},
  RandomSequence{0xA4093822299F31D0ULL,
                 "r3k2r/ppp2ppp/2npbn2/3q4/3P4/2N1PN2/PPP2PPP/R2QK2R w KQkq - 0 1",
                 false},
  RandomSequence{0x082EFA98EC4E6C89ULL, StartFEN, true},
};

struct RandomStats {
    u64 operations{};
    u64 makes{};
    u64 undos{};
    u64 fullRefreshComparisons{};
};

RandomStats run_random_sequence(const RandomSequence& sequence,
                                const NNUE::Network&  network,
                                u64                    operations,
                                u64                    fullRefreshInterval) {
    constexpr usize MaxDepth = 96;

    Position                              pos;
    std::array<StateInfo, MaxDepth + 1>   states{};
    std::vector<Move>                     path;
    std::vector<Snapshot>                 frames;
    NNUE::AccumulatorStack                incremental;
    NNUE::AccumulatorCaches               caches(network);
    PRNG                                  rng(sequence.seed);
    RandomStats                           stats;

    if (auto error = pos.set(std::string(sequence.fen), sequence.chess960, &states[0]))
        die("invalid random-sequence fixture: " + std::string(error->what()));

    incremental.reset();
    frames.push_back(take_snapshot(pos, network, incremental, caches));
    Context context{sequence.seed, pos.fen(), "", "random-root"};
    compare_snapshots(full_refresh_snapshot(pos, network), frames.back(), context);
    ++stats.fullRefreshComparisons;

    for (u64 operation = 0; operation < operations; ++operation)
    {
        const usize           depth = path.size();
        const MoveList<LEGAL> legal(pos);
        const u64             remaining = operations - operation;
        const bool shouldUndo = depth > 0
                             && (remaining <= depth || depth >= MaxDepth || legal.size() == 0
                                 || rng.rand<u32>() % 100 < 38);

        if (shouldUndo)
        {
            const Move        move        = path.back();
            const std::string moveText    = UCI::move(move, pos.is_chess960());
            const Snapshot    expected    = frames[frames.size() - 2];
            const std::string beforeUndo  = pos.fen();

            pos.undo_move(move);
            incremental.pop();
            path.pop_back();
            frames.pop_back();

            context = {sequence.seed, beforeUndo, moveText,
                       "random-undo-" + std::to_string(operation)};
            const Snapshot undone = take_snapshot(pos, network, incremental, caches);
            compare_snapshots(expected, undone, context);

            if (fullRefreshInterval && operation % fullRefreshInterval == 0)
            {
                compare_snapshots(full_refresh_snapshot(pos, network), undone, context);
                ++stats.fullRefreshComparisons;
            }

            ++stats.undos;
        }
        else if (legal.size())
        {
            const Move move = legal.begin()[rng.rand<usize>() % legal.size()];
            context = {sequence.seed, pos.fen(), UCI::move(move, pos.is_chess960()),
                       "random-make-" + std::to_string(operation)};

            auto [dirtyPiece, dirtyThreats] = incremental.push();
            pos.do_move(move, states[depth + 1], pos.gives_check(move), dirtyPiece, dirtyThreats,
                        nullptr, nullptr);

            const Snapshot after = take_snapshot(pos, network, incremental, caches);
            const bool compareFull = fullRefreshInterval == 1
                                  || (fullRefreshInterval
                                      && operation % fullRefreshInterval == 0)
                                  || dirtyPiece.requiresRefresh;
            if (compareFull)
            {
                compare_snapshots(full_refresh_snapshot(pos, network), after, context);
                ++stats.fullRefreshComparisons;
            }

            path.push_back(move);
            frames.push_back(after);
            ++stats.makes;
        }
        else
            fail({sequence.seed, pos.fen(), "", "random-terminal-root"},
                 "random root has no legal move and cannot be undone");

        ++stats.operations;
    }

    if (!path.empty())
        fail({sequence.seed, pos.fen(), UCI::move(path.back(), pos.is_chess960()),
              "random-final-depth"},
             "deterministic sequence did not return to its root");

    context = {sequence.seed, pos.fen(), "", "random-final-root"};
    compare_snapshots(full_refresh_snapshot(pos, network), frames.front(), context);
    ++stats.fullRefreshComparisons;

    return stats;
}

struct Options {
    std::filesystem::path net;
    std::string           mode = "smoke";
    u64                   operations{};
    u64                   fullRefreshInterval{};
};

u64 parse_u64(const std::string& value, std::string_view option) {
    if (value.empty())
        die("invalid value for " + std::string(option) + ": " + value);

    u64 result = 0;
    for (const char c : value)
    {
        if (c < '0' || c > '9')
            die("invalid value for " + std::string(option) + ": " + value);
        const u64 digit = u64(c - '0');
        if (result > (std::numeric_limits<u64>::max() - digit) / 10)
            die("value out of range for " + std::string(option) + ": " + value);
        result = result * 10 + digit;
    }
    return result;
}

Options parse_options(int argc, char* argv[]) {
    Options options;
    for (int i = 1; i < argc; ++i)
    {
        const std::string argument = argv[i];
        auto requireValue = [&](std::string_view option) -> std::string {
            if (++i >= argc)
                die("missing value for " + std::string(option));
            return argv[i];
        };

        if (argument == "--net")
            options.net = requireValue(argument);
        else if (argument == "--mode")
            options.mode = requireValue(argument);
        else if (argument == "--operations")
            options.operations = parse_u64(requireValue(argument), argument);
        else if (argument == "--full-refresh-interval")
            options.fullRefreshInterval = parse_u64(requireValue(argument), argument);
        else if (argument == "--help" || argument == "-h")
        {
            std::cout
              << "Usage: atomic-nnue-incremental-tests --net FILE [--mode smoke|release] "
                 "[--operations N] [--full-refresh-interval N]\n";
            std::exit(0);
        }
        else
            die("unknown argument: " + argument);
    }

    if (options.net.empty())
        die("--net is required");
    if (options.mode != "smoke" && options.mode != "release")
        die("--mode must be smoke or release");

    if (!options.operations)
        options.operations = options.mode == "release" ? 1'000'000 : 4'096;
    if (!options.fullRefreshInterval)
        options.fullRefreshInterval = options.mode == "release" ? 1'024 : 1;
    if (options.operations % (2 * RandomSequences.size()) != 0)
        die("--operations must be a multiple of "
            + std::to_string(2 * RandomSequences.size())
            + " so every deterministic sequence can return to its root");

    return options;
}

}  // namespace
}  // namespace Stockfish

int main(int argc, char* argv[]) {
    using namespace Stockfish;

    const Options options = parse_options(argc, argv);

    Bitboards::init();
    Attacks::init();
    Position::init();

    auto                 network = std::make_unique<Eval::NNUE::Network>();
    Eval::NNUE::EvalFile evalFile;
    network->load({}, options.net, evalFile);
    if (!evalFile.current || network->get_content_hash() == 0)
        die("failed to load a compatible Legacy Atomic V1 network: " + options.net.string());

    run_rule50_damping(*network);

    for (const auto& fixture : FixedFixtures)
        run_fixed_fixture(fixture, *network);

    RandomStats totals;
    for (usize index = 0; index < RandomSequences.size(); ++index)
    {
        const u64 base = options.operations / RandomSequences.size();
        const u64 extra = index < options.operations % RandomSequences.size() ? 1 : 0;
        const auto stats = run_random_sequence(RandomSequences[index], *network, base + extra,
                                               options.fullRefreshInterval);
        totals.operations += stats.operations;
        totals.makes += stats.makes;
        totals.undos += stats.undos;
        totals.fullRefreshComparisons += stats.fullRefreshComparisons;

        std::cout << "PASS random seed=0x" << std::hex << std::uppercase
                  << RandomSequences[index].seed << std::dec << " requested=" << base + extra
                  << " operations=" << stats.operations << " makes=" << stats.makes
                  << " undos=" << stats.undos
                  << " full-refresh=" << stats.fullRefreshComparisons << '\n';
    }

    std::cout << "LegacyAtomicV1 incremental gate passed: mode=" << options.mode
              << " requested-random-operations=" << options.operations
              << " actual-random-operations=" << totals.operations
              << " makes=" << totals.makes << " undos=" << totals.undos
              << " full-refresh-comparisons=" << totals.fullRefreshComparisons << '\n';
    return 0;
}
