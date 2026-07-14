/*
  Atomic-Stockfish, a specialized Atomic Chess engine derived from Stockfish
  Copyright (C) 2004-2026 The Stockfish developers (see AUTHORS file)

  Atomic-Stockfish is free software: you can redistribute it and/or modify
  it under the terms of the GNU General Public License as published by
  the Free Software Foundation, either version 3 of the License, or
  (at your option) any later version.
*/

#include <array>
#include <chrono>
#include <cstdlib>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <limits>
#include <memory>
#include <string>
#include <string_view>
#include <tuple>
#include <vector>

#include "attacks.h"
#include "bitboard.h"
#include "misc.h"
#include "movegen.h"
#include "nnue/atomic_v2/io.h"
#include "nnue/atomic_v2/network.h"
#include "nnue/nnue_dispatcher.h"
#include "position.h"
#include "uci.h"
#include "uci_move.h"

namespace Stockfish {
namespace {

namespace V2 = Eval::NNUE::AtomicV2;

constexpr std::string_view StartFEN = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1";

[[noreturn]] void fail(std::string_view label, const Position& pos, const std::string& detail) {
    std::cerr << "AtomicNNUEV2 incremental gate FAILED\nlabel=" << label << "\nFEN=" << pos.fen()
              << "\n"
              << detail << '\n';
    std::exit(EXIT_FAILURE);
}

[[noreturn]] void die(const std::string& detail) {
    std::cerr << "AtomicNNUEV2 incremental gate FAILED\n" << detail << '\n';
    std::exit(EXIT_FAILURE);
}

struct Snapshot {
    V2::RawNetworkOutput raw;
    V2::AccumulatorState accumulator;
};

void compare_snapshots(const Snapshot&  expected,
                       const Snapshot&  actual,
                       const Position&  pos,
                       std::string_view label);

Snapshot take_snapshot(const Position&        pos,
                       const V2::Network&     network,
                       V2::AccumulatorStack&  stack,
                       V2::AccumulatorCaches& caches) {
    const auto raw = network.evaluate_raw(pos, stack, caches);
    return {raw, stack.latest()};
}

Snapshot full_refresh_snapshot(const Position& pos, const V2::Network& network) {
    auto stack  = std::make_unique<V2::AccumulatorStack>();
    auto caches = std::make_unique<V2::AccumulatorCaches>(network);
    stack->reset();
    return take_snapshot(pos, network, *stack, *caches);
}

Snapshot take_dispatch_snapshot(const Position&               pos,
                                const Eval::NNUE::AnyNetwork& network,
                                Eval::NNUE::AnyAccumulator&   accumulator) {
    const auto raw = network.evaluate_raw(pos, accumulator);
    return {raw, accumulator.v2_latest()};
}

struct TemporaryFile {
    std::filesystem::path path;
    std::filesystem::path directory;

    TemporaryFile(std::filesystem::path value, std::filesystem::path owner) :
        path(std::move(value)),
        directory(std::move(owner)) {}

    TemporaryFile(const TemporaryFile&)            = delete;
    TemporaryFile& operator=(const TemporaryFile&) = delete;

    TemporaryFile(TemporaryFile&& other) noexcept :
        path(std::move(other.path)),
        directory(std::move(other.directory)) {
        other.path.clear();
        other.directory.clear();
    }

    TemporaryFile& operator=(TemporaryFile&&) = delete;

    ~TemporaryFile() {
        if (path.empty())
            return;
        std::error_code error;
        std::filesystem::remove_all(directory, error);
    }
};

u32 read_u32(std::istream& stream, std::string_view label) {
    u32 value = 0;
    if (!V2::IO::read_little_endian(stream, value))
        die("failed to read " + std::string(label));
    return value;
}

void replace_byte(std::fstream& stream, std::streamoff offset, u8 expected, u8 replacement) {
    stream.seekg(offset);
    char value = 0;
    stream.read(&value, 1);
    if (!stream || static_cast<u8>(value) != expected)
        die("unexpected byte while creating same-backend rebind fixture at offset "
            + std::to_string(offset));
    stream.seekp(offset);
    const char next = static_cast<char>(replacement);
    stream.write(&next, 1);
    if (!stream)
        die("failed to patch same-backend rebind fixture at offset " + std::to_string(offset));
}

TemporaryFile create_rebind_variant(const std::filesystem::path& source) {
    constexpr std::streamoff AccumulatorDimensions = 1024;
    constexpr std::streamoff Fc0Inputs             = 1024;
    constexpr std::streamoff Fc0Outputs            = 32;
    constexpr std::streamoff Fc1Inputs             = 64;
    constexpr std::streamoff Fc1Outputs            = 32;
    constexpr std::streamoff Fc2Inputs             = 128;
    constexpr std::streamoff StackSize             = 4 + Fc0Outputs * 4 + Fc0Outputs * Fc0Inputs
                                       + Fc1Outputs * 4 + Fc1Outputs * Fc1Inputs + 4 + Fc2Inputs;

    std::filesystem::path directory;
    const auto      nonce = std::chrono::high_resolution_clock::now().time_since_epoch().count();
    std::error_code error;
    for (int attempt = 0; attempt < 100 && directory.empty(); ++attempt)
    {
        const auto candidate =
          std::filesystem::temp_directory_path()
          / ("atomic-v2-rebind-" + std::to_string(nonce) + "-" + std::to_string(attempt));
        error.clear();
        if (std::filesystem::create_directory(candidate, error))
            directory = candidate;
    }
    if (directory.empty())
        die("failed to create private same-backend rebind directory");

    TemporaryFile variant{directory / "weighted-v2b.nnue", directory};
    if (!std::filesystem::copy_file(source, variant.path, std::filesystem::copy_options::none,
                                    error))
        die("failed to copy same-backend rebind fixture: " + error.message());

    std::fstream stream(variant.path, std::ios::binary | std::ios::in | std::ios::out);
    if (!stream)
        die("failed to open same-backend rebind fixture");

    if (read_u32(stream, "V2 version") != V2::FileVersion
        || read_u32(stream, "V2 network hash") != V2::NetworkHash)
        die("wrong identity in same-backend rebind fixture");
    const u32 descriptionSize = read_u32(stream, "V2 description size");
    stream.seekg(descriptionSize, std::ios::cur);
    if (read_u32(stream, "V2 feature-transformer hash") != V2::FeatureTransformerHash)
        die("wrong transformer identity in same-backend rebind fixture");

    std::streamoff featureBiasPayload = -1;
    for (int block = 0; block < 3; ++block)
    {
        std::array<char, V2::IO::Leb128MagicLength> magic{};
        stream.read(magic.data(), std::streamsize(magic.size()));
        if (!stream || !std::equal(magic.begin(), magic.end(), V2::IO::Leb128Magic))
            die("wrong LEB128 block in same-backend rebind fixture");
        const u32 byteCount = read_u32(stream, "V2 LEB128 byte count");
        if (block == 0)
        {
            if (byteCount != AccumulatorDimensions)
                die("unexpected V2 feature-bias payload size");
            featureBiasPayload = stream.tellg();
        }
        stream.seekg(byteCount, std::ios::cur);
    }
    const std::streamoff firstStack = stream.tellg();
    if (featureBiasPayload < 0 || read_u32(stream, "V2 first stack hash") != V2::ArchitectureHash)
        die("wrong first layer stack in same-backend rebind fixture");

    // Change feature-transformer state and all layer-stack consumers. A stale
    // Finny entry therefore produces a measurably different result from a
    // fresh accumulator bound to V2B.
    replace_byte(stream, featureBiasPayload + 2, 32, 63);
    replace_byte(stream, featureBiasPayload + AccumulatorDimensions / 2 + 2, 32, 63);
    constexpr std::array connections = {
      std::tuple{0, 2, 5},
      std::tuple{0, 514, 7},
      std::tuple{1, 2, 11},
      std::tuple{1, 514, 13},
    };
    for (std::streamoff stack = 0; stack < V2::LayerStacks; ++stack)
    {
        const std::streamoff weights = firstStack + stack * StackSize + 4 + Fc0Outputs * 4;
        for (const auto& [output, input, expected] : connections)
            replace_byte(stream, weights + output * Fc0Inputs + input, u8(expected), 127);
    }
    stream.flush();
    if (!stream)
        die("failed to flush same-backend rebind fixture");
    return variant;
}

void require_same_backend_rebind(const std::filesystem::path& fixture) {
    TemporaryFile        variant  = create_rebind_variant(fixture);
    auto                 networkA = std::make_unique<Eval::NNUE::AnyNetwork>();
    auto                 networkB = std::make_unique<Eval::NNUE::AnyNetwork>();
    Eval::NNUE::EvalFile metadataA;
    Eval::NNUE::EvalFile metadataB;
    if (!networkA->load({}, fixture, metadataA)
        || networkA->backend() != Eval::NNUE::NetworkBackend::AtomicNNUEV2)
        die("failed to load V2A through the dispatcher");
    if (!networkB->load({}, variant.path, metadataB)
        || networkB->backend() != Eval::NNUE::NetworkBackend::AtomicNNUEV2)
        die("failed to load V2B through the dispatcher");
    if (networkA->get_content_hash() == networkB->get_content_hash())
        die("same-backend rebind fixture did not change network content");

    Position  pos;
    StateInfo state{};
    if (auto error = pos.set(std::string(StartFEN), false, &state))
        die("invalid same-backend rebind position: " + std::string(error->what()));

    auto           hot    = std::make_unique<Eval::NNUE::AnyAccumulator>(*networkA);
    const Snapshot before = take_dispatch_snapshot(pos, *networkA, *hot);
    Snapshot       fresh;
    {
        auto accumulator = std::make_unique<Eval::NNUE::AnyAccumulator>(*networkB);
        fresh            = take_dispatch_snapshot(pos, *networkB, *accumulator);
    }
    if (before.raw == fresh.raw)
        die("V2B did not change the controlled dispatcher evaluation");

    hot->rebind(*networkB);
    const Snapshot rebound = take_dispatch_snapshot(pos, *networkB, *hot);
    compare_snapshots(fresh, rebound, pos, "same-backend-v2a-to-v2b-rebind");
    std::cout << "PASS AtomicNNUEV2 same-backend V2A->V2B rebind clears caches\n";
}

void require_nonzero_diagnostics(const V2::Network& network) {
    Position                   pos;
    StateInfo                  state{};
    constexpr std::string_view Fen = "7k/8/8/8/8/1P6/1P6/B6K w - - 0 1";
    if (auto error = pos.set(std::string(Fen), false, &state))
        die(std::string("invalid diagnostic fixture: ") + error->what());

    const Snapshot snapshot         = full_refresh_snapshot(pos, network);
    bool           hasFeatureSignal = false;
    bool           hasPsqtSignal    = false;
    for (Color perspective : {WHITE, BLACK})
    {
        for (const i16 value : snapshot.accumulator.accumulation[perspective])
            hasFeatureSignal |= value != 0;
        for (const i32 value : snapshot.accumulator.psqtAccumulation[perspective])
            hasPsqtSignal |= value != 0;
    }

    if (!hasFeatureSignal || !hasPsqtSignal)
        fail("diagnostic-network-signals", pos,
             "fixture must exercise non-zero feature-transformer and PSQT accumulators");
    std::cout << "PASS AtomicNNUEV2 non-zero FT and PSQT diagnostics\n";
}

void compare_snapshots(const Snapshot&  expected,
                       const Snapshot&  actual,
                       const Position&  pos,
                       std::string_view label) {
    if (actual.raw != expected.raw)
    {
        const auto [expectedPsqt, expectedPositional] = expected.raw;
        const auto [actualPsqt, actualPositional]     = actual.raw;
        fail(label, pos,
             "raw mismatch: expected=(" + std::to_string(expectedPsqt) + ","
               + std::to_string(expectedPositional) + ") actual=(" + std::to_string(actualPsqt)
               + "," + std::to_string(actualPositional) + ")");
    }

    for (Color perspective : {WHITE, BLACK})
    {
        if (!expected.accumulator.computed[perspective]
            || !actual.accumulator.computed[perspective])
            fail(label, pos, "an accumulator perspective was not computed");

        if (actual.accumulator.accumulation[perspective]
            != expected.accumulator.accumulation[perspective])
            fail(label, pos,
                 "feature-transformer accumulator mismatch for perspective="
                   + std::to_string(int(perspective)));

        if (actual.accumulator.psqtAccumulation[perspective]
            != expected.accumulator.psqtAccumulation[perspective])
            fail(label, pos,
                 "PSQT accumulator mismatch for perspective=" + std::to_string(int(perspective)));
    }
}

struct Fixture {
    std::string_view name;
    std::string_view fen;
    std::string_view move;
    bool             chess960;
};

constexpr std::array FixedFixtures = {
  Fixture{"quiet", StartFEN, "e2e4", false},
  Fixture{"capture-king-explosion", "7k/6p1/8/8/8/8/8/K5R1 w - - 0 1", "g1g7", false},
  Fixture{"direct-king-capture", "7k/7R/8/8/8/8/8/K7 w - - 0 1", "h7h8", false},
  Fixture{"explosion-with-bycatch", "7k/8/8/2pBn3/3r4/2PQN3/8/K7 w - - 0 1", "d3d4", false},
  Fixture{"maximum-nine-piece-blast", "7k/8/8/2nnn3/2nrn3/2nnnN2/8/K7 w - - 0 1", "f3d4", false},
  Fixture{"en-passant", "7k/8/2N1b3/2ppP3/8/8/8/K7 w - d6 0 2", "e5d6", false},
  Fixture{"maximum-en-passant-delta", "7k/2n1n3/2n1n3/2npP3/8/8/8/K7 w - d6 0 2", "e5d6", false},
  Fixture{"promotion", "7k/P7/8/8/8/8/8/K7 w - - 0 1", "a7a8q", false},
  Fixture{"capture-promotion", "k5br/6P1/8/8/8/8/8/K7 w - - 0 1", "g7h8q", false},
  Fixture{"maximum-capture-promotion-delta", "2nrn2k/2nnP3/8/8/8/8/8/K7 w - - 0 1", "e7d8q", false},
  Fixture{"castling", "4k3/8/8/8/8/8/8/R3K2R w KQ - 0 1", "e1g1", false},
  Fixture{"atomic960-castling", "7k/8/8/8/8/8/8/1RK5 w Q - 0 1", "c1b1", true},
};

void run_fixed_fixture(const Fixture& fixture, const V2::Network& network) {
    Position  pos;
    StateInfo rootState{};
    StateInfo childState{};
    if (auto error = pos.set(std::string(fixture.fen), fixture.chess960, &rootState))
        die("invalid fixed fixture " + std::string(fixture.name) + ": " + error->what());

    const Move move = UCI::to_move(pos, std::string(fixture.move));
    if (!move)
        fail(fixture.name, pos, "fixture move is not legal: " + std::string(fixture.move));

    auto stack  = std::make_unique<V2::AccumulatorStack>();
    auto caches = std::make_unique<V2::AccumulatorCaches>(network);
    stack->reset();

    const Snapshot before = take_snapshot(pos, network, *stack, *caches);
    compare_snapshots(full_refresh_snapshot(pos, network), before, pos,
                      std::string(fixture.name) + ":root");

    DirtyPiece& dirty = stack->push();
    pos.do_move(move, childState, pos.gives_check(move), dirty, nullptr, nullptr);
    const Snapshot after = take_snapshot(pos, network, *stack, *caches);
    compare_snapshots(full_refresh_snapshot(pos, network), after, pos,
                      std::string(fixture.name) + ":after-move");

    pos.undo_move(move);
    stack->pop();
    const Snapshot undone = take_snapshot(pos, network, *stack, *caches);
    compare_snapshots(before, undone, pos, std::string(fixture.name) + ":after-undo");
    compare_snapshots(full_refresh_snapshot(pos, network), undone, pos,
                      std::string(fixture.name) + ":refresh-after-undo");

    std::cout << "PASS AtomicNNUEV2 fixed " << fixture.name << '\n';
}

struct RandomSequence {
    u64              seed;
    std::string_view fen;
    bool             chess960;
};

constexpr std::array RandomSequences = {
  RandomSequence{0x243F6A8885A308D3ULL, StartFEN, false},
  RandomSequence{0x13198A2E03707344ULL, "7k/8/8/2pBn3/3r4/2PQN3/8/K7 w - - 0 1", false},
  RandomSequence{0xA4093822299F31D0ULL,
                 "r3k2r/ppp2ppp/2npbn2/3q4/3P4/2N1PN2/PPP2PPP/R2QK2R w KQkq - 0 1", false},
  RandomSequence{0x082EFA98EC4E6C89ULL, StartFEN, true},
};

struct RandomStats {
    u64 operations{};
    u64 makes{};
    u64 undos{};
    u64 fullRefreshComparisons{};
};

RandomStats run_random_sequence(const RandomSequence& sequence,
                                const V2::Network&    network,
                                u64                   operations,
                                u64                   fullRefreshInterval) {
    constexpr usize MaxDepth = 96;

    Position                            pos;
    std::array<StateInfo, MaxDepth + 1> states{};
    std::vector<Move>                   path;
    std::vector<Snapshot>               frames;
    auto                                stack  = std::make_unique<V2::AccumulatorStack>();
    auto                                caches = std::make_unique<V2::AccumulatorCaches>(network);
    PRNG                                rng(sequence.seed);
    RandomStats                         stats;

    if (auto error = pos.set(std::string(sequence.fen), sequence.chess960, &states[0]))
        die("invalid random fixture: " + std::string(error->what()));

    stack->reset();
    frames.push_back(take_snapshot(pos, network, *stack, *caches));
    compare_snapshots(full_refresh_snapshot(pos, network), frames.back(), pos, "random-root");
    ++stats.fullRefreshComparisons;

    for (u64 operation = 0; operation < operations; ++operation)
    {
        const usize           depth = path.size();
        const MoveList<LEGAL> legal(pos);
        const u64             remaining  = operations - operation;
        const bool            shouldUndo = depth > 0
                             && (remaining <= depth || depth >= MaxDepth || legal.size() == 0
                                 || rng.rand<u32>() % 100 < 38);

        if (shouldUndo)
        {
            const Move     move     = path.back();
            const Snapshot expected = frames[frames.size() - 2];
            pos.undo_move(move);
            stack->pop();
            path.pop_back();
            frames.pop_back();

            const Snapshot undone = take_snapshot(pos, network, *stack, *caches);
            compare_snapshots(expected, undone, pos, "random-undo");
            if (fullRefreshInterval && operation % fullRefreshInterval == 0)
            {
                compare_snapshots(full_refresh_snapshot(pos, network), undone, pos,
                                  "random-undo-refresh");
                ++stats.fullRefreshComparisons;
            }
            ++stats.undos;
        }
        else if (legal.size())
        {
            const Move  move  = legal.begin()[rng.rand<usize>() % legal.size()];
            DirtyPiece& dirty = stack->push();
            pos.do_move(move, states[depth + 1], pos.gives_check(move), dirty, nullptr, nullptr);

            const Snapshot after              = take_snapshot(pos, network, *stack, *caches);
            bool           perspectiveRefresh = false;
            for (Color perspective : {WHITE, BLACK})
                perspectiveRefresh |= V2::FeatureSet::requires_refresh(dirty, perspective);

            if (fullRefreshInterval == 1
                || (fullRefreshInterval && operation % fullRefreshInterval == 0)
                || perspectiveRefresh)
            {
                compare_snapshots(full_refresh_snapshot(pos, network), after, pos,
                                  "random-make-refresh");
                ++stats.fullRefreshComparisons;
            }

            path.push_back(move);
            frames.push_back(after);
            ++stats.makes;
        }
        else
            fail("random-terminal-root", pos, "root has no legal move and cannot be undone");

        ++stats.operations;
    }

    if (!path.empty())
        fail("random-final-depth", pos, "sequence did not return to its root");
    compare_snapshots(full_refresh_snapshot(pos, network), frames.front(), pos,
                      "random-final-root");
    ++stats.fullRefreshComparisons;
    return stats;
}

u64 parse_u64(const std::string& value, std::string_view option) {
    if (value.empty())
        die("empty value for " + std::string(option));
    u64 result = 0;
    for (const char character : value)
    {
        if (character < '0' || character > '9')
            die("invalid value for " + std::string(option) + ": " + value);
        const u64 digit = u64(character - '0');
        if (result > (std::numeric_limits<u64>::max() - digit) / 10)
            die("out-of-range value for " + std::string(option));
        result = result * 10 + digit;
    }
    return result;
}

struct Options {
    std::filesystem::path net;
    u64                   operations          = 4096;
    u64                   fullRefreshInterval = 1;
};

Options parse_options(int argc, char* argv[]) {
    Options options;
    for (int index = 1; index < argc; ++index)
    {
        const std::string argument      = argv[index];
        auto              require_value = [&](std::string_view option) {
            if (++index >= argc)
                die("missing value for " + std::string(option));
            return std::string(argv[index]);
        };

        if (argument == "--net")
            options.net = require_value(argument);
        else if (argument == "--operations")
            options.operations = parse_u64(require_value(argument), argument);
        else if (argument == "--full-refresh-interval")
            options.fullRefreshInterval = parse_u64(require_value(argument), argument);
        else
            die("unknown argument: " + argument);
    }

    if (options.net.empty())
        die("--net is required");
    if (!options.operations || options.operations % (2 * RandomSequences.size()) != 0)
        die("--operations must be a positive multiple of "
            + std::to_string(2 * RandomSequences.size()));
    if (!options.fullRefreshInterval)
        die("--full-refresh-interval must be positive");
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

    require_same_backend_rebind(options.net);

    auto       network = std::make_unique<V2::Network>();
    const auto loaded  = V2::load_candidate({}, options.net, *network);
    if (!loaded)
        die("failed to load AtomicNNUEV2 fixture: " + loaded.error);

    require_nonzero_diagnostics(*network);

    for (const Fixture& fixture : FixedFixtures)
        run_fixed_fixture(fixture, *network);

    RandomStats total;
    for (usize index = 0; index < RandomSequences.size(); ++index)
    {
        const u64  operations = options.operations / RandomSequences.size();
        const auto stats      = run_random_sequence(RandomSequences[index], *network, operations,
                                                    options.fullRefreshInterval);
        total.operations += stats.operations;
        total.makes += stats.makes;
        total.undos += stats.undos;
        total.fullRefreshComparisons += stats.fullRefreshComparisons;
        std::cout << "PASS AtomicNNUEV2 random seed=" << RandomSequences[index].seed
                  << " operations=" << stats.operations << " makes=" << stats.makes
                  << " undos=" << stats.undos
                  << " refresh-comparisons=" << stats.fullRefreshComparisons << '\n';
    }

    std::cout << "AtomicNNUEV2 incremental gate passed: operations=" << total.operations
              << " makes=" << total.makes << " undos=" << total.undos
              << " full-refresh-comparisons=" << total.fullRefreshComparisons << '\n';
    return EXIT_SUCCESS;
}
