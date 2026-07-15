/*
  Atomic-Stockfish, a UCI chess playing engine derived from Stockfish
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
#include <fstream>
#include <iomanip>
#include <iostream>
#include <limits>
#include <memory>
#include <sstream>
#include <string>
#include <string_view>
#include <streambuf>
#include <thread>
#include <utility>
#include <vector>

#include "attacks.h"
#include "bitboard.h"
#include "misc.h"
#include "movegen.h"
#include "position.h"
#include "uci_move.h"

#include "data/sha256.h"

#include "../incremental_backend.h"
#include "../scalar_backend.h"
#include "../wire_network.h"

namespace Stockfish::Eval::NNUE::AtomicV3 {
namespace {

constexpr std::string_view StartFEN = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1";
constexpr u64              SignatureOffset              = 14695981039346656037ULL;
constexpr u64              SignaturePrime               = 1099511628211ULL;
constexpr usize            RootCount                    = 8;
constexpr usize            MaxDepth                     = 96;
constexpr usize            DirectedMoveCount            = 32;
constexpr usize            DirectedCaptureCount         = 23;
constexpr usize            DirectedTerminalFailureCount = 8;
constexpr usize            DirectedPromotionCount       = 19;
constexpr usize            DirectedEnPassantCount       = 6;
constexpr usize            DirectedStandardCastleCount  = 4;
constexpr usize            DirectedAtomic960CastleCount = 11;
constexpr usize            DirectedMaximumBlast         = 9;
constexpr u64              FrozenNetBytes               = 77'349'879;
constexpr std::string_view FrozenNetSha256 =
  "00e46223822d06d7927e884eec10739ba19ef8dd82a6e262f627d361658080c2";

[[noreturn]] void die(const std::string& detail);

class MemoryInputBuffer final: public std::streambuf {
   public:
    explicit MemoryInputBuffer(std::string& bytes) {
        char* begin = bytes.data();
        setg(begin, begin, begin + bytes.size());
    }
};

std::string read_authenticated_fixture(const std::filesystem::path& path) {
    std::ifstream input(path, std::ios::binary | std::ios::ate);
    if (!input)
        die("could not open AtomicNNUEV3 fixture: " + path.string());
    const std::streampos end = input.tellg();
    if (end < 0 || static_cast<u64>(end) != FrozenNetBytes)
        die("AtomicNNUEV3 fixture size mismatch: expected=" + std::to_string(FrozenNetBytes)
            + " actual="
            + (end < 0 ? std::string("unavailable") : std::to_string(static_cast<u64>(end))));

    std::string bytes(static_cast<usize>(FrozenNetBytes), '\0');
    input.seekg(0, std::ios::beg);
    input.read(bytes.data(), static_cast<std::streamsize>(bytes.size()));
    if (!input || static_cast<u64>(input.gcount()) != FrozenNetBytes)
        die("AtomicNNUEV3 fixture changed or became unreadable during authentication");
    char extra{};
    input.read(&extra, 1);
    if (input.gcount() != 0)
        die("AtomicNNUEV3 fixture grew during authentication");

    Data::Sha256 digest;
    digest.update(bytes.data(), bytes.size());
    const std::string actualSha = digest.hex_digest();
    if (actualSha != FrozenNetSha256)
        die("AtomicNNUEV3 fixture SHA-256 mismatch: expected=" + std::string(FrozenNetSha256)
            + " actual=" + actualSha);
    return bytes;
}

LoadResult load_authenticated_fixture(std::string& bytes) {
    MemoryInputBuffer buffer(bytes);
    std::istream      input(&buffer);
    return load_candidate(input);
}

struct Context {
    u64         seed = 0;
    std::string phase;
    std::string fen;
    std::string move;
};

[[noreturn]] void fail(const Context& context, const std::string& detail) {
    std::cerr << "AtomicNNUEV3 incremental stress gate FAILED\n"
              << detail << "\nseed=0x" << std::hex << std::uppercase << context.seed << std::dec
              << "\nphase=" << context.phase << "\nFEN=" << context.fen
              << "\nmove=" << (context.move.empty() ? "(none)" : context.move) << '\n';
    std::exit(EXIT_FAILURE);
}

[[noreturn]] void die(const std::string& detail) { fail({}, detail); }

void require(bool condition, const Context& context, const std::string& detail) {
    if (!condition)
        fail(context, detail);
}

void append_bytes(u64& signature, u64 value, usize bytes) {
    for (usize index = 0; index < bytes; ++index)
    {
        signature ^= value & 0xFF;
        signature *= SignaturePrime;
        value >>= 8;
    }
}

template<typename Range>
void append_range(u64& signature, const Range& values) {
    for (const auto value : values)
        append_bytes(signature, static_cast<u64>(value), sizeof(value));
}

void append_string(u64& signature, std::string_view value) {
    append_bytes(signature, value.size(), sizeof(usize));
    for (const unsigned char byte : value)
        append_bytes(signature, byte, 1);
}

void append_scalar_signature(u64& signature, const ScalarDiagnostic& value) {
    append_bytes(signature, value.sideToMove, 1);
    append_bytes(signature, value.networkBucket, sizeof(value.networkBucket));
    for (const auto& perspective : value.perspectives)
    {
        append_bytes(signature, perspective.perspective, 1);
        append_range(signature, perspective.accumulator);
        append_range(signature, perspective.psqt);
    }
    append_range(signature, value.transformed);
    append_bytes(signature, static_cast<u32>(value.psqtDifference), 4);
    append_bytes(signature, static_cast<u32>(value.psqtValue), 4);
    append_range(signature, value.dense.fc0);
    append_range(signature, value.dense.fc0Squared);
    append_range(signature, value.dense.fc0Clipped);
    append_range(signature, value.dense.fc1);
    append_range(signature, value.dense.fc1Squared);
    append_range(signature, value.dense.fc1Clipped);
    append_range(signature, value.dense.fc2);
    append_bytes(signature, static_cast<u64>(value.rawOutput), 8);
    append_bytes(signature, static_cast<u32>(value.scaledOutput), 4);
    append_bytes(signature, static_cast<u32>(value.positionalValue), 4);
}

void append_counters_signature(u64& signature, const IncrementalCounters& value) {
    append_bytes(signature, value.hmRefreshes, 8);
    append_bytes(signature, value.hmDeltas, 8);
    append_bytes(signature, value.hmReuses, 8);
    append_bytes(signature, value.relationRefreshes, 8);
    append_bytes(signature, value.snapshotMismatches, 8);
    append_bytes(signature, value.epSquareMismatches, 8);
}

void append_incremental_signature(u64& signature, const IncrementalDiagnostic& value) {
    for (const Color color : {WHITE, BLACK})
    {
        const usize index = static_cast<usize>(color);
        append_range(signature, value.hmOnly[index].accumulator);
        append_range(signature, value.hmOnly[index].psqt);
        const auto& update = value.hmUpdates[index];
        append_bytes(signature, static_cast<u8>(update.source), 1);
        append_bytes(signature, update.sourcePly, sizeof(update.sourcePly));
        append_bytes(signature, update.sourceDistance, sizeof(update.sourceDistance));
        append_bytes(signature, update.removedRows, sizeof(update.removedRows));
        append_bytes(signature, update.addedRows, sizeof(update.addedRows));
    }
    append_counters_signature(signature, value.eventCounters);
    append_bytes(signature, value.ply, sizeof(value.ply));
    append_bytes(signature, value.sameFrameSnapshotMismatch, 1);
    append_bytes(signature, value.epSquareMismatch, 1);
    append_bytes(signature, static_cast<u8>(value.previousEpSquare), 1);
    append_bytes(signature, static_cast<u8>(value.currentEpSquare), 1);
    append_bytes(signature, static_cast<u8>(value.previousSideToMove), 1);
    append_bytes(signature, static_cast<u8>(value.currentSideToMove), 1);
}

void append_snapshot_signature(u64& signature, const CapturePairSnapshot& value) {
    for (const Piece piece : value.board)
        append_bytes(signature, static_cast<u8>(piece), 1);
    append_bytes(signature, static_cast<u8>(value.sideToMove), 1);
    append_bytes(signature, static_cast<u8>(value.epSquare), 1);
}

bool same_orientation(const JointOrientation& lhs, const JointOrientation& rhs) {
    return lhs.perspective == rhs.perspective && lhs.ownKing == rhs.ownKing
        && lhs.orientedOwnKing == rhs.orientedOwnKing && lhs.verticalXor == rhs.verticalXor
        && lhs.horizontalXor == rhs.horizontalXor && lhs.kingBucket == rhs.kingBucket;
}

template<typename Range>
std::string range_difference(std::string_view label, const Range& lhs, const Range& rhs) {
    if (lhs.size() != rhs.size())
        return std::string(label) + " size mismatch";
    for (std::size_t index = 0; index < lhs.size(); ++index)
        if (lhs[index] != rhs[index])
        {
            std::ostringstream out;
            out << label << '[' << index
                << "] mismatch: incremental=" << static_cast<long long>(lhs[index])
                << " fresh=" << static_cast<long long>(rhs[index]);
            return out.str();
        }
    return {};
}

std::string emission_difference(std::string_view           label,
                                const FullRefreshEmission& lhs,
                                const FullRefreshEmission& rhs) {
    if (lhs.hm.networkBucket != rhs.hm.networkBucket)
        return std::string(label) + ".hm.network_bucket mismatch";
    const auto compare = [&](std::string_view suffix, const auto& a, const auto& b) {
        if (!same_orientation(a.orientation, b.orientation))
            return std::string(label) + '.' + std::string(suffix) + ".orientation mismatch";
        if (a.size != b.size)
            return std::string(label) + '.' + std::string(suffix) + ".size mismatch";
        for (IndexType index = 0; index < a.size; ++index)
            if (a.features[index].physicalIndex != b.features[index].physicalIndex)
                return std::string(label) + '.' + std::string(suffix) + ".row mismatch";
        return std::string{};
    };

    std::string detail = compare("hm", lhs.hm, rhs.hm);
    if (detail.empty())
        detail = compare("capture_pair", lhs.capturePairs, rhs.capturePairs);
    if (detail.empty())
        detail = compare("king_blast_ep", lhs.kingBlastEp, rhs.kingBlastEp);
    if (detail.empty())
        detail = compare("blast_ring", lhs.blastRing, rhs.blastRing);
    return detail;
}

std::string scalar_difference(const ScalarDiagnostic& lhs, const ScalarDiagnostic& rhs) {
    if (lhs.sideToMove != rhs.sideToMove)
        return "side_to_move mismatch";
    if (lhs.networkBucket != rhs.networkBucket)
        return "network_bucket mismatch";
    for (const Color color : {WHITE, BLACK})
    {
        const usize       index  = static_cast<usize>(color);
        const auto&       a      = lhs.perspectives[index];
        const auto&       b      = rhs.perspectives[index];
        const std::string prefix = color == WHITE ? "white" : "black";
        if (a.perspective != b.perspective)
            return prefix + ".perspective mismatch";
        if (const auto detail = emission_difference(prefix + ".emission", a.emission, b.emission);
            !detail.empty())
            return detail;
        if (const auto detail =
              range_difference(prefix + ".accumulator", a.accumulator, b.accumulator);
            !detail.empty())
            return detail;
        if (const auto detail = range_difference(prefix + ".psqt", a.psqt, b.psqt); !detail.empty())
            return detail;
    }
    if (const auto detail = range_difference("transformed", lhs.transformed, rhs.transformed);
        !detail.empty())
        return detail;
    if (lhs.psqtDifference != rhs.psqtDifference || lhs.psqtValue != rhs.psqtValue)
        return "PSQT scalar mismatch";
    if (const auto detail = range_difference("fc0", lhs.dense.fc0, rhs.dense.fc0); !detail.empty())
        return detail;
    if (const auto detail =
          range_difference("fc0_squared", lhs.dense.fc0Squared, rhs.dense.fc0Squared);
        !detail.empty())
        return detail;
    if (const auto detail =
          range_difference("fc0_clipped", lhs.dense.fc0Clipped, rhs.dense.fc0Clipped);
        !detail.empty())
        return detail;
    if (const auto detail = range_difference("fc1", lhs.dense.fc1, rhs.dense.fc1); !detail.empty())
        return detail;
    if (const auto detail =
          range_difference("fc1_squared", lhs.dense.fc1Squared, rhs.dense.fc1Squared);
        !detail.empty())
        return detail;
    if (const auto detail =
          range_difference("fc1_clipped", lhs.dense.fc1Clipped, rhs.dense.fc1Clipped);
        !detail.empty())
        return detail;
    if (const auto detail = range_difference("fc2", lhs.dense.fc2, rhs.dense.fc2); !detail.empty())
        return detail;
    if (lhs.rawOutput != rhs.rawOutput || lhs.scaledOutput != rhs.scaledOutput
        || lhs.positionalValue != rhs.positionalValue)
        return "dense output mismatch";
    return {};
}

bool diagnostic_is_clear(const IncrementalDiagnostic& value) {
    static const ScalarDiagnostic zero{};
    if (!scalar_difference(value.scalar, zero).empty())
        return false;
    for (const Color color : {WHITE, BLACK})
    {
        const usize index = static_cast<usize>(color);
        if (!std::all_of(value.hmOnly[index].accumulator.begin(),
                         value.hmOnly[index].accumulator.end(), [](i32 cell) { return cell == 0; })
            || !std::all_of(value.hmOnly[index].psqt.begin(), value.hmOnly[index].psqt.end(),
                            [](i64 cell) { return cell == 0; }))
            return false;
        const auto& update = value.hmUpdates[index];
        if (update.source != HmSourceKind::None || update.sourcePly || update.sourceDistance
            || update.removedRows || update.addedRows)
            return false;
    }
    const auto& counters = value.eventCounters;
    return !counters.hmRefreshes && !counters.hmDeltas && !counters.hmReuses
        && !counters.relationRefreshes && !counters.snapshotMismatches
        && !counters.epSquareMismatches && !value.ply && !value.sameFrameSnapshotMismatch
        && !value.epSquareMismatch && value.previousEpSquare == SQ_NONE
        && value.currentEpSquare == SQ_NONE && value.previousSideToMove == WHITE
        && value.currentSideToMove == WHITE;
}

bool counters_equal(const IncrementalCounters& lhs, const IncrementalCounters& rhs) {
    return lhs.hmRefreshes == rhs.hmRefreshes && lhs.hmDeltas == rhs.hmDeltas
        && lhs.hmReuses == rhs.hmReuses && lhs.relationRefreshes == rhs.relationRefreshes
        && lhs.snapshotMismatches == rhs.snapshotMismatches
        && lhs.epSquareMismatches == rhs.epSquareMismatches;
}

bool counters_advanced_by(const IncrementalCounters& after,
                          const IncrementalCounters& before,
                          const IncrementalCounters& delta) {
    return after.hmRefreshes == before.hmRefreshes + delta.hmRefreshes
        && after.hmDeltas == before.hmDeltas + delta.hmDeltas
        && after.hmReuses == before.hmReuses + delta.hmReuses
        && after.relationRefreshes == before.relationRefreshes + delta.relationRefreshes
        && after.snapshotMismatches == before.snapshotMismatches + delta.snapshotMismatches
        && after.epSquareMismatches == before.epSquareMismatches + delta.epSquareMismatches;
}

struct Stats {
    u64   operations{};
    u64   makes{};
    u64   undos{};
    u64   evaluations{};
    u64   fullRefreshComparisons{};
    u64   captures{};
    u64   terminalFailures{};
    u64   standardCastles{};
    u64   atomic960Castles{};
    u64   directedMoves{};
    u64   directedCaptures{};
    u64   directedTerminalFailures{};
    u64   directedPromotions{};
    u64   directedEnPassants{};
    u64   directedStandardCastles{};
    u64   directedAtomic960Castles{};
    usize directedMaxBlast{};
    u64   hmRefreshes{};
    u64   hmDeltas{};
    u64   hmReuses{};
    u64   relationRefreshes{};
    u64   snapshotMismatches{};
    u64   epSquareMismatches{};
    usize maxSourceDistance{};
    u64   signature = SignatureOffset;
};

void record_incremental_coverage(Stats& stats, const IncrementalDiagnostic& value) {
    stats.hmRefreshes += value.eventCounters.hmRefreshes;
    stats.hmDeltas += value.eventCounters.hmDeltas;
    stats.hmReuses += value.eventCounters.hmReuses;
    stats.relationRefreshes += value.eventCounters.relationRefreshes;
    stats.snapshotMismatches += value.eventCounters.snapshotMismatches;
    stats.epSquareMismatches += value.eventCounters.epSquareMismatches;
    for (const auto& update : value.hmUpdates)
        stats.maxSourceDistance = std::max(stats.maxSourceDistance, update.sourceDistance);
}

void merge_coverage(Stats& destination, const Stats& source) {
    destination.hmRefreshes += source.hmRefreshes;
    destination.hmDeltas += source.hmDeltas;
    destination.hmReuses += source.hmReuses;
    destination.relationRefreshes += source.relationRefreshes;
    destination.snapshotMismatches += source.snapshotMismatches;
    destination.epSquareMismatches += source.epSquareMismatches;
    destination.maxSourceDistance =
      std::max(destination.maxSourceDistance, source.maxSourceDistance);
}

void evaluate_state(IncrementalStack&      stack,
                    const Network&         network,
                    const Position&        position,
                    bool                   compareFresh,
                    Stats&                 stats,
                    const Context&         context,
                    IncrementalDiagnostic* observed = nullptr) {
    std::unique_ptr<IncrementalDiagnostic> owned;
    if (!observed)
    {
        owned    = std::make_unique<IncrementalDiagnostic>();
        observed = owned.get();
    }
    const IncrementalCounters before   = stack.counters();
    const IncrementalStatus   status   = stack.evaluate(network, position, *observed);
    const bool                hasWhite = position.has_king(WHITE);
    const bool                hasBlack = position.has_king(BLACK);

    append_bytes(stats.signature, static_cast<u8>(status.error), 1);
    append_bytes(stats.signature, static_cast<u8>(status.featureError), 1);
    append_bytes(stats.signature, position.key(), 8);
    append_string(stats.signature, position.fen());

    if (hasWhite && hasBlack)
    {
        require(bool(status), context,
                "nonterminal incremental evaluation failed: "
                  + std::string(incremental_error_message(status.error)));
        require(counters_advanced_by(stack.counters(), before, observed->eventCounters), context,
                "successful evaluation counters do not equal the published event delta");
        const auto& event = observed->eventCounters;
        require(event.hmRefreshes + event.hmDeltas + event.hmReuses == COLOR_NB
                  && event.relationRefreshes == COLOR_NB,
                context, "successful evaluation published impossible HM/relation accounting");
        require(event.snapshotMismatches == observed->sameFrameSnapshotMismatch
                  && event.epSquareMismatches == observed->epSquareMismatch,
                context, "successful evaluation mismatch flags/counters diverged");
        require(observed->hmUpdates[WHITE].source != HmSourceKind::None
                  && observed->hmUpdates[BLACK].source != HmSourceKind::None,
                context, "successful evaluation omitted an HM source");
        if (compareFresh)
        {
            auto               fresh       = std::make_unique<ScalarDiagnostic>();
            const ScalarStatus freshStatus = evaluate_scalar(network, position, *fresh);
            require(bool(freshStatus), context,
                    "nonterminal full refresh failed: "
                      + std::string(scalar_error_message(freshStatus.code)));
            const std::string detail = scalar_difference(observed->scalar, *fresh);
            require(detail.empty(), context, "incremental/full-refresh divergence: " + detail);
            ++stats.fullRefreshComparisons;
        }
        append_scalar_signature(stats.signature, observed->scalar);
        append_incremental_signature(stats.signature, *observed);
        append_bytes(stats.signature, stack.size(), sizeof(usize));
        record_incremental_coverage(stats, *observed);
    }
    else
    {
        require(hasWhite != hasBlack, context, "stress position lost both kings");
        const FullRefreshError expected =
          hasWhite ? FullRefreshError::MissingBlackKing : FullRefreshError::MissingWhiteKing;
        require(status.error == IncrementalError::FeatureOracleError
                  && status.featureError == expected,
                context, "terminal evaluation did not fail with the exact missing-king domain");
        require(diagnostic_is_clear(*observed), context,
                "terminal failure published a partial incremental diagnostic");
        require(counters_equal(stack.counters(), before), context,
                "terminal failure mutated cumulative incremental counters");
        if (compareFresh)
        {
            auto               fresh       = std::make_unique<ScalarDiagnostic>();
            const ScalarStatus freshStatus = evaluate_scalar(network, position, *fresh);
            require(!freshStatus && freshStatus.code == ScalarError::FeatureOracleError
                      && freshStatus.featureError == expected,
                    context, "terminal full refresh returned the wrong error domain");
            static const ScalarDiagnostic zero{};
            require(scalar_difference(*fresh, zero).empty(), context,
                    "terminal full refresh published a partial diagnostic");
            ++stats.fullRefreshComparisons;
        }
        append_incremental_signature(stats.signature, *observed);
        append_bytes(stats.signature, stack.size(), sizeof(usize));
        ++stats.terminalFailures;
    }
    ++stats.evaluations;
}

void set_position(Position&        position,
                  StateInfo&       state,
                  std::string_view fen,
                  bool             chess960,
                  std::string_view label) {
    if (auto error = position.set(std::string(fen), chess960, &state))
        die("invalid " + std::string(label) + " FEN: " + error->what());
}

Move require_move(const Position& position, std::string_view uci, std::string_view label) {
    const Move move = UCI::to_move(position, std::string(uci));
    if (!move)
        die("illegal " + std::string(label) + " move " + std::string(uci) + " in "
            + position.fen());
    return move;
}

void append_stats_signature(Stats& destination, const Stats& source) {
    append_bytes(destination.signature, source.signature, 8);
}

void require_source(const IncrementalDiagnostic& diagnostic,
                    Color                        perspective,
                    HmSourceKind                 expected,
                    const Context&               context,
                    std::string_view             detail) {
    const auto& update = diagnostic.hmUpdates[static_cast<usize>(perspective)];
    require(update.source == expected, context,
            std::string(detail) + ": unexpected HM source for "
              + (perspective == WHITE ? "WHITE" : "BLACK"));
}

struct DirectedMoveFixture {
    std::string_view label;
    std::string_view fen;
    std::string_view uci;
    bool             chess960;
    MoveType         moveType;
    bool             capture;
    int              blastSize;
    FullRefreshError terminalError    = FullRefreshError::None;
    HmSourceKind     childWhiteSource = HmSourceKind::None;
    HmSourceKind     childBlackSource = HmSourceKind::None;
    int              rootBucket       = -1;
    int              childBucket      = -1;
    int              rootWhiteMirror  = -1;
    int              childWhiteMirror = -1;
};

void run_move_fixture(const Network& network, const DirectedMoveFixture& fixture, Stats& totals) {
    Position                 position;
    std::array<StateInfo, 2> states{};
    set_position(position, states[0], fixture.fen, fixture.chess960, fixture.label);
    const std::string rootFen = position.fen();
    const Key         rootKey = position.key();
    auto              stack   = std::make_unique<IncrementalStack>(network);
    Stats             local;
    auto              root = std::make_unique<IncrementalDiagnostic>();
    evaluate_state(
      *stack, network, position, true, local,
      {0, std::string(fixture.label) + "-root", position.fen(), std::string(fixture.uci)},
      root.get());
    require_source(
      *root, WHITE, HmSourceKind::FullRefresh,
      {0, std::string(fixture.label) + "-root", position.fen(), std::string(fixture.uci)},
      fixture.label);
    require_source(
      *root, BLACK, HmSourceKind::FullRefresh,
      {0, std::string(fixture.label) + "-root", position.fen(), std::string(fixture.uci)},
      fixture.label);
    require(fixture.rootBucket < 0
              || root->scalar.networkBucket == static_cast<IndexType>(fixture.rootBucket),
            {0, std::string(fixture.label) + "-root", position.fen(), std::string(fixture.uci)},
            "directed fixture root network bucket mismatch");
    require(fixture.rootWhiteMirror < 0
              || root->scalar.perspectives[WHITE].emission.hm.orientation.horizontalXor
                   == fixture.rootWhiteMirror,
            {0, std::string(fixture.label) + "-root", position.fen(), std::string(fixture.uci)},
            "directed fixture root WHITE mirror branch mismatch");

    const Move move = require_move(position, fixture.uci, fixture.label);
    require(move.type_of() == fixture.moveType,
            {0, std::string(fixture.label) + "-move", position.fen(), std::string(fixture.uci)},
            "directed fixture encoded the wrong move type");
    if (fixture.moveType == PROMOTION)
    {
        const char      suffix   = fixture.uci.back();
        const PieceType expected = suffix == 'q' ? QUEEN
                                 : suffix == 'r' ? ROOK
                                 : suffix == 'b' ? BISHOP
                                 : suffix == 'n' ? KNIGHT
                                                 : NO_PIECE_TYPE;
        require(expected != NO_PIECE_TYPE && move.promotion_type() == expected,
                {0, std::string(fixture.label) + "-move", position.fen(), std::string(fixture.uci)},
                "directed fixture encoded the wrong promotion subtype");
    }
    require(position.capture(move) == fixture.capture,
            {0, std::string(fixture.label) + "-move", position.fen(), std::string(fixture.uci)},
            "directed fixture capture classification mismatch");

    DirtyPiece& dirty = stack->push();
    position.do_move(move, states[1], position.gives_check(move), dirty, nullptr, nullptr);
    require(
      fixture.blastSize < 0 || static_cast<int>(dirty.atomicBlast.size()) == fixture.blastSize,
      {0, std::string(fixture.label) + "-child", position.fen(), std::string(fixture.uci)},
      "directed fixture Atomic blast size mismatch: expected=" + std::to_string(fixture.blastSize)
        + " actual=" + std::to_string(dirty.atomicBlast.size()));

    auto child = std::make_unique<IncrementalDiagnostic>();
    evaluate_state(
      *stack, network, position, true, local,
      {0, std::string(fixture.label) + "-child", position.fen(), std::string(fixture.uci)},
      child.get());
    if (fixture.terminalError == FullRefreshError::None)
    {
        require(
          position.has_king(WHITE) && position.has_king(BLACK),
          {0, std::string(fixture.label) + "-child", position.fen(), std::string(fixture.uci)},
          "nonterminal directed fixture removed a king");
        if (fixture.childWhiteSource != HmSourceKind::None)
            require_source(
              *child, WHITE, fixture.childWhiteSource,
              {0, std::string(fixture.label) + "-child", position.fen(), std::string(fixture.uci)},
              fixture.label);
        if (fixture.childBlackSource != HmSourceKind::None)
            require_source(
              *child, BLACK, fixture.childBlackSource,
              {0, std::string(fixture.label) + "-child", position.fen(), std::string(fixture.uci)},
              fixture.label);
        require(
          fixture.childBucket < 0
            || child->scalar.networkBucket == static_cast<IndexType>(fixture.childBucket),
          {0, std::string(fixture.label) + "-child", position.fen(), std::string(fixture.uci)},
          "directed fixture child network bucket mismatch");
        require(
          fixture.childWhiteMirror < 0
            || child->scalar.perspectives[WHITE].emission.hm.orientation.horizontalXor
                 == fixture.childWhiteMirror,
          {0, std::string(fixture.label) + "-child", position.fen(), std::string(fixture.uci)},
          "directed fixture child WHITE mirror branch mismatch");
    }
    else
    {
        const bool expectedMissingWhite =
          fixture.terminalError == FullRefreshError::MissingWhiteKing;
        require(
          (!position.has_king(WHITE)) == expectedMissingWhite
            && (!position.has_king(BLACK)) == !expectedMissingWhite,
          {0, std::string(fixture.label) + "-child", position.fen(), std::string(fixture.uci)},
          "terminal directed fixture removed the wrong king");
        require(
          local.terminalFailures == 1,
          {0, std::string(fixture.label) + "-child", position.fen(), std::string(fixture.uci)},
          "terminal directed fixture did not fail exactly once");
    }

    position.undo_move(move);
    stack->pop();
    auto restored = std::make_unique<IncrementalDiagnostic>();
    evaluate_state(
      *stack, network, position, true, local,
      {0, std::string(fixture.label) + "-undo", position.fen(), std::string(fixture.uci)},
      restored.get());
    require(position.fen() == rootFen && position.key() == rootKey,
            {0, std::string(fixture.label) + "-undo", position.fen(), std::string(fixture.uci)},
            "directed fixture did not restore exact FEN/key");
    require(scalar_difference(restored->scalar, root->scalar).empty(),
            {0, std::string(fixture.label) + "-undo", position.fen(), std::string(fixture.uci)},
            "directed fixture did not restore exact scalar diagnostic");
    require_source(
      *restored, WHITE, HmSourceKind::SameFrameReuse,
      {0, std::string(fixture.label) + "-undo", position.fen(), std::string(fixture.uci)},
      fixture.label);
    require_source(
      *restored, BLACK, HmSourceKind::SameFrameReuse,
      {0, std::string(fixture.label) + "-undo", position.fen(), std::string(fixture.uci)},
      fixture.label);

    ++totals.directedMoves;
    totals.directedCaptures += fixture.capture;
    totals.directedTerminalFailures += fixture.terminalError != FullRefreshError::None;
    totals.directedPromotions += fixture.moveType == PROMOTION;
    totals.directedEnPassants += fixture.moveType == EN_PASSANT;
    if (fixture.blastSize >= 0)
        totals.directedMaxBlast =
          std::max(totals.directedMaxBlast, static_cast<usize>(fixture.blastSize));
    merge_coverage(totals, local);
    append_stats_signature(totals, local);
    std::cout << "PASS directed " << fixture.label << '\n';
}

void run_castling_fixture(const Network&   network,
                          bool             chess960,
                          std::string_view fen,
                          std::string_view uci,
                          Stats&           totals) {
    Position                 position;
    std::array<StateInfo, 2> states{};
    set_position(position, states[0], fen, chess960, chess960 ? "Atomic960 castling" : "castling");
    const std::string rootFen = position.fen();
    const Key         rootKey = position.key();
    auto              stack   = std::make_unique<IncrementalStack>(network);
    Stats             local;
    auto              root = std::make_unique<IncrementalDiagnostic>();
    evaluate_state(*stack, network, position, true, local,
                   {0, "directed-castle-root", position.fen(), std::string(uci)}, root.get());
    require_source(*root, WHITE, HmSourceKind::FullRefresh,
                   {0, "directed-castle-root", position.fen(), std::string(uci)}, "castling");
    require_source(*root, BLACK, HmSourceKind::FullRefresh,
                   {0, "directed-castle-root", position.fen(), std::string(uci)}, "castling");
    const Move move = require_move(position, uci, "castling");
    require(move.type_of() == CASTLING, {0, "directed-castle", position.fen(), std::string(uci)},
            "fixture move is not encoded as castling");
    DirtyPiece& dirty = stack->push();
    position.do_move(move, states[1], position.gives_check(move), dirty, nullptr, nullptr);
    auto child = std::make_unique<IncrementalDiagnostic>();
    evaluate_state(*stack, network, position, true, local,
                   {0, "directed-castle-child", position.fen(), std::string(uci)}, child.get());
    for (const Color perspective : {WHITE, BLACK})
    {
        const usize index = static_cast<usize>(perspective);
        const bool  unchangedOrientation =
          same_orientation(root->scalar.perspectives[index].emission.hm.orientation,
                           child->scalar.perspectives[index].emission.hm.orientation);
        require_source(*child, perspective,
                       unchangedOrientation ? HmSourceKind::StackDelta : HmSourceKind::FullRefresh,
                       {0, "directed-castle-child", position.fen(), std::string(uci)}, "castling");
        if (unchangedOrientation)
            require(child->hmUpdates[index].sourceDistance == 1,
                    {0, "directed-castle-child", position.fen(), std::string(uci)},
                    "castling child did not delta from its direct parent");
    }
    position.undo_move(move);
    stack->pop();
    auto restored = std::make_unique<IncrementalDiagnostic>();
    evaluate_state(*stack, network, position, true, local,
                   {0, "directed-castle-undo", position.fen(), std::string(uci)}, restored.get());
    require(position.fen() == rootFen && position.key() == rootKey,
            {0, "directed-castle-undo", position.fen(), std::string(uci)},
            "castling undo did not restore exact FEN/key");
    require(scalar_difference(restored->scalar, root->scalar).empty(),
            {0, "directed-castle-undo", position.fen(), std::string(uci)},
            "castling undo did not restore exact scalar diagnostic");
    require_source(*restored, WHITE, HmSourceKind::SameFrameReuse,
                   {0, "directed-castle-undo", position.fen(), std::string(uci)}, "castling");
    require_source(*restored, BLACK, HmSourceKind::SameFrameReuse,
                   {0, "directed-castle-undo", position.fen(), std::string(uci)}, "castling");
    ++(chess960 ? totals.directedAtomic960Castles : totals.directedStandardCastles);
    merge_coverage(totals, local);
    append_stats_signature(totals, local);
    std::cout << "PASS directed " << (chess960 ? "Atomic960" : "standard") << " castling\n";
}

void run_directed_move_fixtures(const Network& network, Stats& totals) {
    constexpr FullRefreshError NoTerminal = FullRefreshError::None;
    constexpr HmSourceKind     Delta      = HmSourceKind::StackDelta;
    constexpr std::array<DirectedMoveFixture, DirectedMoveCount> Fixtures{{
      {"white-en-passant", "7k/8/2N1b3/2ppP3/8/8/8/K7 w - d6 0 2", "e5d6", false, EN_PASSANT, true,
       3, NoTerminal, Delta, Delta},
      {"black-en-passant", "k7/8/8/8/2PPp3/2n1B3/8/7K b - d3 0 2", "e4d3", false, EN_PASSANT, true,
       3, NoTerminal, Delta, Delta},
      {"white-max-en-passant-blast", "7k/2n1n3/2n1n3/2npP3/8/8/8/K7 w - d6 0 2", "e5d6", false,
       EN_PASSANT, true, 6, NoTerminal, Delta, Delta},
      {"black-max-en-passant-blast", "k7/8/8/8/2NPp3/2N1N3/2N1N3/7K b - d3 0 2", "e4d3", false,
       EN_PASSANT, true, 6, NoTerminal, Delta, Delta},
      {"white-quiet-promotion-q", "7k/P7/8/8/8/8/8/K7 w - - 0 1", "a7a8q", false, PROMOTION, false,
       0, NoTerminal, Delta, Delta},
      {"white-quiet-promotion-r", "7k/P7/8/8/8/8/8/K7 w - - 0 1", "a7a8r", false, PROMOTION, false,
       0, NoTerminal, Delta, Delta},
      {"white-quiet-promotion-b", "7k/P7/8/8/8/8/8/K7 w - - 0 1", "a7a8b", false, PROMOTION, false,
       0, NoTerminal, Delta, Delta},
      {"white-quiet-promotion-n", "7k/P7/8/8/8/8/8/K7 w - - 0 1", "a7a8n", false, PROMOTION, false,
       0, NoTerminal, Delta, Delta},
      {"black-quiet-promotion-q", "k7/8/8/8/8/8/p7/7K b - - 0 1", "a2a1q", false, PROMOTION, false,
       0, NoTerminal, Delta, Delta},
      {"black-quiet-promotion-r", "k7/8/8/8/8/8/p7/7K b - - 0 1", "a2a1r", false, PROMOTION, false,
       0, NoTerminal, Delta, Delta},
      {"black-quiet-promotion-b", "k7/8/8/8/8/8/p7/7K b - - 0 1", "a2a1b", false, PROMOTION, false,
       0, NoTerminal, Delta, Delta},
      {"black-quiet-promotion-n", "k7/8/8/8/8/8/p7/7K b - - 0 1", "a2a1n", false, PROMOTION, false,
       0, NoTerminal, Delta, Delta},
      {"capture-promotion-q", "k5br/6P1/8/8/8/8/8/K7 w - - 0 1", "g7h8q", false, PROMOTION, true, 2,
       NoTerminal, Delta, Delta},
      {"capture-promotion-r", "k5br/6P1/8/8/8/8/8/K7 w - - 0 1", "g7h8r", false, PROMOTION, true, 2,
       NoTerminal, Delta, Delta},
      {"capture-promotion-b", "k5br/6P1/8/8/8/8/8/K7 w - - 0 1", "g7h8b", false, PROMOTION, true, 2,
       NoTerminal, Delta, Delta},
      {"capture-promotion-n", "k5br/6P1/8/8/8/8/8/K7 w - - 0 1", "g7h8n", false, PROMOTION, true, 2,
       NoTerminal, Delta, Delta},
      {"black-capture-promotion-q", "k7/8/8/8/8/8/6p1/K5BR b - - 0 1", "g2h1q", false, PROMOTION,
       true, 2, NoTerminal, Delta, Delta},
      {"black-capture-promotion-r", "k7/8/8/8/8/8/6p1/K5BR b - - 0 1", "g2h1r", false, PROMOTION,
       true, 2, NoTerminal, Delta, Delta},
      {"black-capture-promotion-b", "k7/8/8/8/8/8/6p1/K5BR b - - 0 1", "g2h1b", false, PROMOTION,
       true, 2, NoTerminal, Delta, Delta},
      {"black-capture-promotion-n", "k7/8/8/8/8/8/6p1/K5BR b - - 0 1", "g2h1n", false, PROMOTION,
       true, 2, NoTerminal, Delta, Delta},
      {"max-capture-promotion-blast", "2nrn2k/2nnP3/8/8/8/8/8/K7 w - - 0 1", "e7d8q", false,
       PROMOTION, true, 5, NoTerminal, Delta, Delta},
      {"max-nine-piece-blast", "7k/8/8/2nnn3/2nrn3/2nnnN2/8/K7 w - - 0 1", "f3d4", false, NORMAL,
       true, 9, NoTerminal, Delta, Delta},
      {"white-king-mirror-crossing", "7k/8/8/8/8/8/8/3K4 w - - 0 1", "d1e1", false, NORMAL, false,
       0, NoTerminal, HmSourceKind::FullRefresh, Delta, -1, -1, 7, 0},
      {"material-bucket-crossing", "7k/p7/8/8/8/8/R7/K6N w - - 0 1", "a2a7", false, NORMAL, true, 1,
       NoTerminal, Delta, Delta, 1, 0},
      {"direct-missing-black", "7k/7R/8/8/8/8/8/K7 w - - 0 1", "h7h8", false, NORMAL, true, 1,
       FullRefreshError::MissingBlackKing},
      {"direct-missing-white", "k7/8/8/8/8/8/r7/K7 b - - 0 1", "a2a1", false, NORMAL, true, 1,
       FullRefreshError::MissingWhiteKing},
      {"adjacent-blast-missing-black", "7k/6p1/8/8/8/8/8/K5R1 w - - 0 1", "g1g7", false, NORMAL,
       true, 2, FullRefreshError::MissingBlackKing},
      {"adjacent-blast-missing-white", "k5r1/8/8/8/8/8/6P1/7K b - - 0 1", "g8g2", false, NORMAL,
       true, 2, FullRefreshError::MissingWhiteKing},
      {"en-passant-missing-black", "8/2k5/8/3pP3/8/8/8/K7 w - d6 0 1", "e5d6", false, EN_PASSANT,
       true, 2, FullRefreshError::MissingBlackKing},
      {"en-passant-missing-white", "k7/8/8/8/3Pp3/8/2K5/8 b - d3 0 1", "e4d3", false, EN_PASSANT,
       true, 2, FullRefreshError::MissingWhiteKing},
      {"capture-promotion-missing-black", "6kr/6P1/8/8/8/8/8/K7 w - - 0 1", "g7h8q", false,
       PROMOTION, true, 2, FullRefreshError::MissingBlackKing},
      {"capture-promotion-missing-white", "k7/8/8/8/8/8/6p1/6KR b - - 0 1", "g2h1q", false,
       PROMOTION, true, 2, FullRefreshError::MissingWhiteKing},
    }};

    for (const auto& fixture : Fixtures)
        run_move_fixture(network, fixture, totals);
}

void run_null_ep_fixture(const Network& network, Stats& totals) {
    constexpr std::string_view Fen = "7k/8/2N1b3/2ppP3/8/8/8/K7 w - d6 0 2";
    Position                   position;
    std::array<StateInfo, 2>   states{};
    set_position(position, states[0], Fen, false, "null EP");
    auto  stack = std::make_unique<IncrementalStack>(network);
    Stats local;

    auto root = std::make_unique<IncrementalDiagnostic>();
    evaluate_state(*stack, network, position, true, local,
                   {0, "directed-null-ep-root", position.fen(), "null"}, root.get());
    const usize sizeBefore = stack->size();
    position.do_null_move(states[1]);
    require(stack->size() == sizeBefore, {0, "directed-null-ep-child", position.fen(), "null"},
            "null move unexpectedly pushed the incremental stack");

    auto child = std::make_unique<IncrementalDiagnostic>();
    evaluate_state(*stack, network, position, true, local,
                   {0, "directed-null-ep-child", position.fen(), "null"}, child.get());
    require(child->sameFrameSnapshotMismatch && child->epSquareMismatch,
            {0, "directed-null-ep-child", position.fen(), "null"},
            "null move did not diagnose same-frame EP invalidation");
    require(child->previousEpSquare == SQ_D6 && child->currentEpSquare == SQ_NONE,
            {0, "directed-null-ep-child", position.fen(), "null"},
            "null move published the wrong EP transition");
    require_source(*child, WHITE, HmSourceKind::SameFrameReuse,
                   {0, "directed-null-ep-child", position.fen(), "null"}, "null EP");
    require_source(*child, BLACK, HmSourceKind::SameFrameReuse,
                   {0, "directed-null-ep-child", position.fen(), "null"}, "null EP");

    position.undo_null_move();
    require(stack->size() == sizeBefore, {0, "directed-null-ep-restored", position.fen(), "null"},
            "null undo unexpectedly popped the incremental stack");
    auto restored = std::make_unique<IncrementalDiagnostic>();
    evaluate_state(*stack, network, position, true, local,
                   {0, "directed-null-ep-restored", position.fen(), "null"}, restored.get());
    require(restored->sameFrameSnapshotMismatch && restored->epSquareMismatch,
            {0, "directed-null-ep-restored", position.fen(), "null"},
            "null undo did not diagnose restored EP metadata");
    require(restored->previousEpSquare == SQ_NONE && restored->currentEpSquare == SQ_D6,
            {0, "directed-null-ep-restored", position.fen(), "null"},
            "null undo published the wrong EP transition");
    require(scalar_difference(restored->scalar, root->scalar).empty(),
            {0, "directed-null-ep-restored", position.fen(), "null"},
            "null undo did not restore exact scalar diagnostic");

    merge_coverage(totals, local);
    append_stats_signature(totals, local);
    std::cout << "PASS directed null EP same-frame invalidation/restoration\n";
}

void run_deep_stack_fixture(const Network& network, Stats& totals) {
    constexpr usize Depth = IncrementalStack::MaxSize - 1;
    Position        position;
    auto            states = std::make_unique<std::array<StateInfo, IncrementalStack::MaxSize>>();
    auto            moves  = std::make_unique<std::array<Move, Depth>>();
    set_position(position, (*states)[0], StartFEN, false, "deep stack");
    const std::string rootFen = position.fen();
    const Key         rootKey = position.key();
    auto              stack   = std::make_unique<IncrementalStack>(network);
    Stats             local;

    auto root = std::make_unique<IncrementalDiagnostic>();
    evaluate_state(*stack, network, position, true, local,
                   {0, "directed-deep-root", position.fen(), {}}, root.get());
    constexpr std::array<std::string_view, 4> Repetition{{"g1f3", "g8f6", "f3g1", "f6g8"}};
    for (usize ply = 0; ply < Depth; ++ply)
    {
        (*moves)[ply] = require_move(position, Repetition[ply % Repetition.size()], "deep stack");
        DirtyPiece& dirty = stack->push();
        position.do_move((*moves)[ply], (*states)[ply + 1], position.gives_check((*moves)[ply]),
                         dirty, nullptr, nullptr);
    }
    require(stack->size() == IncrementalStack::MaxSize,
            {0, "directed-deep-leaf", position.fen(), {}},
            "deep stack did not reach its exact MAX_PLY + 1 capacity");

    auto leaf = std::make_unique<IncrementalDiagnostic>();
    evaluate_state(*stack, network, position, true, local,
                   {0, "directed-deep-leaf", position.fen(), {}}, leaf.get());
    require(leaf->ply == Depth, {0, "directed-deep-leaf", position.fen(), {}},
            "deep stack diagnostic published the wrong ply");
    for (const Color perspective : {WHITE, BLACK})
    {
        require_source(*leaf, perspective, HmSourceKind::StackDelta,
                       {0, "directed-deep-leaf", position.fen(), {}}, "deep stack");
        require(leaf->hmUpdates[static_cast<usize>(perspective)].sourcePly == 0
                  && leaf->hmUpdates[static_cast<usize>(perspective)].sourceDistance == Depth,
                {0, "directed-deep-leaf", position.fen(), {}},
                "deep stack did not delta from the evaluated root across MAX_PLY frames");
    }

    for (usize ply = Depth; ply-- > 0;)
    {
        position.undo_move((*moves)[ply]);
        stack->pop();
    }
    require(stack->size() == 1 && position.fen() == rootFen && position.key() == rootKey,
            {0, "directed-deep-restored", position.fen(), {}},
            "deep stack unwind did not restore exact root/depth");
    auto restored = std::make_unique<IncrementalDiagnostic>();
    evaluate_state(*stack, network, position, true, local,
                   {0, "directed-deep-restored", position.fen(), {}}, restored.get());
    require(scalar_difference(restored->scalar, root->scalar).empty(),
            {0, "directed-deep-restored", position.fen(), {}},
            "deep stack unwind did not restore exact scalar diagnostic");
    require_source(*restored, WHITE, HmSourceKind::SameFrameReuse,
                   {0, "directed-deep-restored", position.fen(), {}}, "deep stack");
    require_source(*restored, BLACK, HmSourceKind::SameFrameReuse,
                   {0, "directed-deep-restored", position.fen(), {}}, "deep stack");

    merge_coverage(totals, local);
    append_stats_signature(totals, local);
    std::cout << "PASS directed MAX_PLY lazy stack/restoration\n";
}

void expect_failure(IncrementalStack&          stack,
                    const Network&             network,
                    const CapturePairSnapshot& snapshot,
                    IncrementalError           expectedError,
                    FullRefreshError           expectedFeatureError,
                    Stats&                     stats,
                    const Context&             context) {
    auto diagnostic                       = std::make_unique<IncrementalDiagnostic>();
    diagnostic->ply                       = 99;
    diagnostic->scalar.rawOutput          = 0x12345678;
    diagnostic->sameFrameSnapshotMismatch = true;
    const IncrementalCounters before      = stack.counters();
    const usize               sizeBefore  = stack.size();
    const IncrementalStatus   status      = stack.evaluate(network, snapshot, *diagnostic);

    require(status.error == expectedError && status.featureError == expectedFeatureError, context,
            "expected failure returned the wrong error domain");
    require(diagnostic_is_clear(*diagnostic), context,
            "expected failure published a partial incremental diagnostic");
    require(counters_equal(stack.counters(), before) && stack.size() == sizeBefore, context,
            "expected failure mutated committed counters or stack depth");
    append_bytes(stats.signature, static_cast<u8>(status.error), 1);
    append_bytes(stats.signature, static_cast<u8>(status.featureError), 1);
    append_snapshot_signature(stats.signature, snapshot);
    append_incremental_signature(stats.signature, *diagnostic);
    append_bytes(stats.signature, stack.size(), sizeof(usize));
}

void run_failure_recovery_fixture(const Network& network,
                                  const Network& secondNetwork,
                                  Stats&         totals) {
    Position  position;
    StateInfo state{};
    set_position(position, state, StartFEN, false, "failure recovery");
    auto  stack = std::make_unique<IncrementalStack>(network);
    Stats local;
    auto  baseline = std::make_unique<IncrementalDiagnostic>();
    evaluate_state(*stack, network, position, true, local,
                   {0, "directed-failure-root", position.fen(), {}}, baseline.get());
    const CapturePairSnapshot valid = make_capture_pair_snapshot(position);

    for (const auto [fault, label] :
         {std::pair{IncrementalFaultPoint::AfterFirstPerspective, "fault-after-first-perspective"},
          std::pair{IncrementalFaultPoint::BeforeComposition, "fault-before-composition"},
          std::pair{IncrementalFaultPoint::AfterCompositionBeforeCommit,
                    "fault-after-composition-before-commit"}})
    {
        stack->set_test_fault(fault);
        expect_failure(*stack, network, valid, IncrementalError::InjectedFailure,
                       FullRefreshError::None, local,
                       {0, std::string("directed-") + label, position.fen(), {}});
        stack->set_test_fault(IncrementalFaultPoint::None);
        auto recovered = std::make_unique<IncrementalDiagnostic>();
        evaluate_state(*stack, network, position, true, local,
                       {0, std::string("directed-") + label + "-recovered", position.fen(), {}},
                       recovered.get());
        require(scalar_difference(recovered->scalar, baseline->scalar).empty(),
                {0, std::string("directed-") + label + "-recovered", position.fen(), {}},
                "injected failure changed the committed frame");
        require_source(*recovered, WHITE, HmSourceKind::SameFrameReuse,
                       {0, std::string("directed-") + label + "-recovered", position.fen(), {}},
                       label);
        require_source(*recovered, BLACK, HmSourceKind::SameFrameReuse,
                       {0, std::string("directed-") + label + "-recovered", position.fen(), {}},
                       label);
    }

    expect_failure(*stack, secondNetwork, valid, IncrementalError::NetworkMismatch,
                   FullRefreshError::None, local,
                   {0, "directed-network-mismatch", position.fen(), {}});
    auto afterMismatch = std::make_unique<IncrementalDiagnostic>();
    evaluate_state(*stack, network, position, true, local,
                   {0, "directed-network-mismatch-recovered", position.fen(), {}},
                   afterMismatch.get());
    require(scalar_difference(afterMismatch->scalar, baseline->scalar).empty(),
            {0, "directed-network-mismatch-recovered", position.fen(), {}},
            "network mismatch changed the committed frame");
    require_source(*afterMismatch, WHITE, HmSourceKind::SameFrameReuse,
                   {0, "directed-network-mismatch-recovered", position.fen(), {}},
                   "network mismatch recovery");
    require_source(*afterMismatch, BLACK, HmSourceKind::SameFrameReuse,
                   {0, "directed-network-mismatch-recovered", position.fen(), {}},
                   "network mismatch recovery");

    const auto malformed = [&](std::string_view label, CapturePairSnapshot snapshot,
                               FullRefreshError expected) {
        expect_failure(*stack, network, snapshot, IncrementalError::FeatureOracleError, expected,
                       local, {0, std::string(label), "snapshot", {}});
        auto recovered = std::make_unique<IncrementalDiagnostic>();
        evaluate_state(*stack, network, position, true, local,
                       {0, std::string(label) + "-recovered", position.fen(), {}}, recovered.get());
        require(scalar_difference(recovered->scalar, baseline->scalar).empty(),
                {0, std::string(label) + "-recovered", position.fen(), {}},
                "malformed snapshot changed the committed frame");
        require_source(*recovered, WHITE, HmSourceKind::SameFrameReuse,
                       {0, std::string(label) + "-recovered", position.fen(), {}}, label);
        require_source(*recovered, BLACK, HmSourceKind::SameFrameReuse,
                       {0, std::string(label) + "-recovered", position.fen(), {}}, label);
    };

    CapturePairSnapshot missingBlack{};
    missingBlack.board[SQ_A1] = W_KING;
    missingBlack.sideToMove   = WHITE;
    malformed("directed-missing-black", missingBlack, FullRefreshError::MissingBlackKing);

    CapturePairSnapshot missingWhite{};
    missingWhite.board[SQ_H8] = B_KING;
    missingWhite.sideToMove   = BLACK;
    malformed("directed-missing-white", missingWhite, FullRefreshError::MissingWhiteKing);

    CapturePairSnapshot multipleWhite = valid;
    multipleWhite.board[SQ_A3]        = W_KING;
    malformed("directed-multiple-white", multipleWhite, FullRefreshError::MultipleWhiteKings);

    CapturePairSnapshot multipleBlack = valid;
    multipleBlack.board[SQ_A6]        = B_KING;
    malformed("directed-multiple-black", multipleBlack, FullRefreshError::MultipleBlackKings);

    CapturePairSnapshot invalidSide = valid;
    invalidSide.sideToMove          = Color(COLOR_NB);
    malformed("directed-invalid-side", invalidSide, FullRefreshError::InvalidSideToMove);

    CapturePairSnapshot invalidPiece = valid;
    invalidPiece.board[SQ_A3]        = Piece(7);
    malformed("directed-invalid-piece", invalidPiece, FullRefreshError::InvalidPiece);

    CapturePairSnapshot tooManyWhite = valid;
    tooManyWhite.board[SQ_A3]        = W_PAWN;
    malformed("directed-too-many-white-pieces", tooManyWhite,
              FullRefreshError::TooManyPiecesPerColor);

    CapturePairSnapshot tooManyBlack = valid;
    tooManyBlack.board[SQ_A6]        = B_PAWN;
    malformed("directed-too-many-black-pieces", tooManyBlack,
              FullRefreshError::TooManyPiecesPerColor);

    stack->reset(secondNetwork);
    auto resetSecond = std::make_unique<IncrementalDiagnostic>();
    evaluate_state(*stack, secondNetwork, position, true, local,
                   {0, "directed-reset-second-network", position.fen(), {}}, resetSecond.get());
    require(scalar_difference(resetSecond->scalar, baseline->scalar).empty(),
            {0, "directed-reset-second-network", position.fen(), {}},
            "reset to an equivalent authenticated network changed evaluation");
    require_source(*resetSecond, WHITE, HmSourceKind::FullRefresh,
                   {0, "directed-reset-second-network", position.fen(), {}}, "network reset");
    require_source(*resetSecond, BLACK, HmSourceKind::FullRefresh,
                   {0, "directed-reset-second-network", position.fen(), {}}, "network reset");
    stack->reset(network);
    auto resetOriginal = std::make_unique<IncrementalDiagnostic>();
    evaluate_state(*stack, network, position, true, local,
                   {0, "directed-reset-original-network", position.fen(), {}}, resetOriginal.get());
    require(scalar_difference(resetOriginal->scalar, baseline->scalar).empty(),
            {0, "directed-reset-original-network", position.fen(), {}},
            "reset back to the original network changed evaluation");
    require_source(*resetOriginal, WHITE, HmSourceKind::FullRefresh,
                   {0, "directed-reset-original-network", position.fen(), {}}, "network reset");
    require_source(*resetOriginal, BLACK, HmSourceKind::FullRefresh,
                   {0, "directed-reset-original-network", position.fen(), {}}, "network reset");

    merge_coverage(totals, local);
    append_stats_signature(totals, local);
    std::cout << "PASS directed transactional failures/network reset/recovery\n";
}

struct RandomSequence {
    u64              seed;
    std::string_view fen;
    bool             chess960;
};

constexpr std::array<RandomSequence, RootCount> RandomSequences{{
  {0x243F6A8885A308D3ULL, StartFEN, false},
  {0x13198A2E03707344ULL, "7k/8/8/2pBn3/3r4/2PQN3/8/K7 w - - 0 1", false},
  {0xA4093822299F31D0ULL, "r3k2r/ppp2ppp/2npbn2/3q4/3P4/2N1PN2/PPP2PPP/R2QK2R w KQkq - 0 1", false},
  {0x082EFA98EC4E6C89ULL, "rnbqkbn1/2pppp2/7r/6pp/pPP3PP/P4P2/3PP3/RNB1KBNR w KQq - 1 10", false},
  {0x452821E638D01377ULL, "bbqnnrkr/pppppppp/8/8/8/8/PPPPPPPP/BBQNNRKR w HFhf - 0 1", true},
  {0xBE5466CF34E90C6CULL, "nrqbbkrn/pppppppp/8/8/8/8/PPPPPPPP/NRQBBKRN w GBgb - 0 1", true},
  {0xC0AC29B7C97C50DDULL, "7k/8/2N1b3/2ppP3/8/8/8/K7 w - d6 0 2", false},
  {0x3F84D5B5B5470917ULL, "k5br/6P1/6N1/8/8/8/8/K7 w - - 0 1", false},
}};

Stats run_random_sequence(const RandomSequence& sequence,
                          const Network&        network,
                          u64                   operations,
                          u64                   fullRefreshInterval) {
    Position          position;
    auto              states = std::make_unique<std::array<StateInfo, MaxDepth + 1>>();
    std::vector<Move> path;
    auto              stack = std::make_unique<IncrementalStack>(network);
    PRNG              rng(sequence.seed);
    Stats             stats;

    set_position(position, (*states)[0], sequence.fen, sequence.chess960, "random root");
    const std::string rootFen = position.fen();
    const Key         rootKey = position.key();
    evaluate_state(*stack, network, position, true, stats,
                   {sequence.seed, "random-root", position.fen(), {}});

    for (u64 operation = 0; operation < operations; ++operation)
    {
        const usize       depth     = path.size();
        const u64         remaining = operations - operation;
        const bool        terminal  = !position.has_king(WHITE) || !position.has_king(BLACK);
        std::vector<Move> legalMoves;
        if (!terminal)
            for (const Move move : MoveList<LEGAL>(position))
                legalMoves.push_back(move);

        const bool shouldUndo = depth > 0
                             && (terminal || remaining <= depth || depth >= MaxDepth
                                 || legalMoves.empty() || rng.rand<u32>() % 100 < 38);
        std::string moveText;
        std::string action;
        if (shouldUndo)
        {
            const Move move          = path.back();
            moveText                 = UCI::move(move, position.is_chess960());
            const std::string before = position.fen();
            position.undo_move(move);
            stack->pop();
            path.pop_back();
            action = "undo";
            ++stats.undos;
            (void) before;
        }
        else if (!legalMoves.empty())
        {
            const Move move     = legalMoves[rng.rand<usize>() % legalMoves.size()];
            moveText            = UCI::move(move, position.is_chess960());
            const bool  capture = position.capture(move);
            const bool  castle  = move.type_of() == CASTLING;
            const bool  c960    = position.is_chess960();
            DirtyPiece& dirty   = stack->push();
            position.do_move(move, (*states)[depth + 1], position.gives_check(move), dirty, nullptr,
                             nullptr);
            path.push_back(move);
            action = "make";
            ++stats.makes;
            if (capture)
                ++stats.captures;
            if (castle)
                ++(c960 ? stats.atomic960Castles : stats.standardCastles);
        }
        else
            fail({sequence.seed, "random-terminal-root", position.fen(), {}},
                 "root has no legal move and cannot be unwound");

        append_bytes(stats.signature, operation, 8);
        append_bytes(stats.signature, action == "make" ? 1 : 2, 1);
        append_string(stats.signature, moveText);
        const bool compareFresh = operation % fullRefreshInterval == 0;
        evaluate_state(*stack, network, position, compareFresh, stats,
                       {sequence.seed, "random-" + action + '-' + std::to_string(operation),
                        position.fen(), moveText});
        ++stats.operations;
    }

    require(path.empty(), {sequence.seed, "random-final-depth", position.fen(), {}},
            "deterministic sequence did not unwind to its root");
    require(position.fen() == rootFen && position.key() == rootKey,
            {sequence.seed, "random-final-root", position.fen(), {}},
            "deterministic sequence did not restore the exact root");
    require(stats.makes == stats.undos, {sequence.seed, "random-accounting", position.fen(), {}},
            "random sequence make/undo accounting diverged");
    const auto& counters = stack->counters();
    require(stats.hmRefreshes == counters.hmRefreshes && stats.hmDeltas == counters.hmDeltas
              && stats.hmReuses == counters.hmReuses
              && stats.relationRefreshes == counters.relationRefreshes
              && stats.snapshotMismatches == counters.snapshotMismatches
              && stats.epSquareMismatches == counters.epSquareMismatches,
            {sequence.seed, "random-counter-accounting", position.fen(), {}},
            "random sequence Stats diverged from the stack cumulative counters");
    return stats;
}

struct Options {
    std::filesystem::path net;
    std::string           mode = "smoke";
    u64                   operations{};
    u64                   fullRefreshInterval{};
    u64                   threads{};
    bool                  operationsSpecified{};
    bool                  fullRefreshIntervalSpecified{};
    bool                  threadsSpecified{};
};

u64 parse_u64(std::string_view text, std::string_view option) {
    if (text.empty())
        die("missing numeric value for " + std::string(option));
    u64 value = 0;
    for (const char c : text)
    {
        if (c < '0' || c > '9')
            die("invalid numeric value for " + std::string(option) + ": " + std::string(text));
        const u64 digit = static_cast<u64>(c - '0');
        if (value > (std::numeric_limits<u64>::max() - digit) / 10)
            die("numeric value out of range for " + std::string(option));
        value = value * 10 + digit;
    }
    return value;
}

Options parse_options(int argc, char* argv[]) {
    Options options;
    for (int index = 1; index < argc; ++index)
    {
        const std::string argument     = argv[index];
        const auto        requireValue = [&](std::string_view name) {
            if (++index >= argc)
                die("missing value for " + std::string(name));
            return std::string(argv[index]);
        };
        if (argument == "--net")
            options.net = requireValue(argument);
        else if (argument == "--mode")
            options.mode = requireValue(argument);
        else if (argument == "--operations")
        {
            options.operations          = parse_u64(requireValue(argument), argument);
            options.operationsSpecified = true;
        }
        else if (argument == "--full-refresh-interval")
        {
            options.fullRefreshInterval          = parse_u64(requireValue(argument), argument);
            options.fullRefreshIntervalSpecified = true;
        }
        else if (argument == "--threads")
        {
            options.threads          = parse_u64(requireValue(argument), argument);
            options.threadsSpecified = true;
        }
        else if (argument == "--help" || argument == "-h")
        {
            std::cout << "Usage: atomic-v3-incremental-stress-tests --net FILE "
                         "[--mode smoke|release|soak] [--operations N] "
                         "[--full-refresh-interval N] [--threads 1|2|4|8]\n";
            std::exit(EXIT_SUCCESS);
        }
        else
            die("unknown argument: " + argument);
    }

    if (options.net.empty())
        die("--net is required");
    if (options.mode != "smoke" && options.mode != "release" && options.mode != "soak")
        die("--mode must be smoke, release or soak");
    if (!options.operationsSpecified)
        options.operations = options.mode == "smoke"   ? 4'096
                           : options.mode == "release" ? 65'536
                                                       : 1'048'576;
    else if (!options.operations)
        die("--operations must be positive");
    if (!options.fullRefreshIntervalSpecified)
        options.fullRefreshInterval = 1;
    else if (!options.fullRefreshInterval)
        die("--full-refresh-interval must be positive");
    if (!options.threadsSpecified)
        options.threads = options.mode == "smoke" ? 1 : 8;
    else if (!options.threads)
        die("--threads must be one of 1, 2, 4 or 8");
    if (options.operations % 16 != 0)
        die("--operations must be a multiple of 16");
    if (options.fullRefreshInterval > options.operations)
        die("--full-refresh-interval cannot exceed --operations");
    if (options.threads != 1 && options.threads != 2 && options.threads != 4
        && options.threads != 8)
        die("--threads must be one of 1, 2, 4 or 8");
    return options;
}

}  // namespace
}  // namespace Stockfish::Eval::NNUE::AtomicV3

int main(int argc, char* argv[]) {
    using namespace Stockfish;
    using namespace Stockfish::Eval::NNUE::AtomicV3;

    const Options options = parse_options(argc, argv);
    Bitboards::init();
    Attacks::init();
    Position::init();

    std::string fixtureBytes = read_authenticated_fixture(options.net);
    LoadResult  loaded       = load_authenticated_fixture(fixtureBytes);
    if (!loaded)
        die("failed to load AtomicNNUEV3 fixture: " + loaded.error);
    const Network& network = *loaded.network;
    LoadResult     second  = load_authenticated_fixture(fixtureBytes);
    if (!second)
        die("failed to load second AtomicNNUEV3 fixture instance: " + second.error);

    Stats totals;
    run_castling_fixture(network, false, "4k3/8/8/8/8/8/8/R3K2R w KQ - 0 1", "e1g1", totals);
    run_castling_fixture(network, false, "4k3/8/8/8/8/8/8/R3K2R w KQ - 0 1", "e1c1", totals);
    run_castling_fixture(network, false, "r3k2r/8/8/8/8/8/8/4K3 b kq - 0 1", "e8g8", totals);
    run_castling_fixture(network, false, "r3k2r/8/8/8/8/8/8/4K3 b kq - 0 1", "e8c8", totals);

    run_castling_fixture(network, true, "7k/8/8/8/8/8/8/1RK5 w B - 0 1", "c1b1", totals);
    run_castling_fixture(network, true, "7k/8/8/8/8/8/8/3RK3 w D - 0 1", "e1d1", totals);
    run_castling_fixture(network, true, "7k/8/8/8/8/8/8/2RK4 w C - 0 1", "d1c1", totals);
    run_castling_fixture(network, true, "k7/8/8/8/8/8/8/6KR w H - 0 1", "g1h1", totals);
    run_castling_fixture(network, true, "k7/8/8/8/8/8/8/4KR2 w F - 0 1", "e1f1", totals);
    run_castling_fixture(network, true, "k7/8/8/8/8/8/8/5K1R w H - 0 1", "f1h1", totals);
    run_castling_fixture(network, true, "k7/8/8/8/8/8/8/4K1R1 w G - 0 1", "e1g1", totals);
    run_castling_fixture(network, true, "k7/8/8/8/8/8/8/5KR1 w G - 0 1", "f1g1", totals);
    run_castling_fixture(network, true, "1rk5/8/8/8/8/8/8/7K b b - 0 1", "c8b8", totals);
    run_castling_fixture(network, true, "3rk3/8/8/8/8/8/8/7K b d - 0 1", "e8d8", totals);
    run_castling_fixture(network, true, "2rk4/8/8/8/8/8/8/7K b c - 0 1", "d8c8", totals);

    run_directed_move_fixtures(network, totals);
    run_null_ep_fixture(network, totals);
    run_deep_stack_fixture(network, totals);
    run_failure_recovery_fixture(network, *second.network, totals);

    const u64                    perSequence = options.operations / RootCount;
    std::array<Stats, RootCount> randomStats{};
    std::vector<std::thread>     workers;
    workers.reserve(options.threads);
    for (u64 worker = 0; worker < options.threads; ++worker)
        workers.emplace_back([&, worker] {
            for (usize index = worker; index < RootCount; index += options.threads)
                randomStats[index] = run_random_sequence(RandomSequences[index], network,
                                                         perSequence, options.fullRefreshInterval);
        });
    for (auto& worker : workers)
        worker.join();

    for (usize index = 0; index < RootCount; ++index)
    {
        const auto& sequence = RandomSequences[index];
        const auto& stats    = randomStats[index];
        totals.operations += stats.operations;
        totals.makes += stats.makes;
        totals.undos += stats.undos;
        totals.evaluations += stats.evaluations;
        totals.fullRefreshComparisons += stats.fullRefreshComparisons;
        totals.captures += stats.captures;
        totals.terminalFailures += stats.terminalFailures;
        totals.standardCastles += stats.standardCastles;
        totals.atomic960Castles += stats.atomic960Castles;
        merge_coverage(totals, stats);
        append_stats_signature(totals, stats);
        std::cout << "PASS random seed=0x" << std::hex << std::uppercase << sequence.seed
                  << std::dec << " operations=" << stats.operations << " makes=" << stats.makes
                  << " undos=" << stats.undos << " terminal-failures=" << stats.terminalFailures
                  << " signature=0x" << std::hex << std::uppercase << std::setw(16)
                  << std::setfill('0') << stats.signature << std::setfill(' ') << std::dec << '\n';
    }

    require(totals.operations == options.operations, {}, "aggregate operation count mismatch");
    require(totals.makes == totals.undos, {}, "aggregate make/undo count mismatch");
    require(totals.evaluations == totals.operations + RootCount, {},
            "aggregate evaluation count mismatch");
    const u64 sampledPerSequence =
      (perSequence + options.fullRefreshInterval - 1) / options.fullRefreshInterval;
    require(totals.fullRefreshComparisons == RootCount + RootCount * sampledPerSequence, {},
            "aggregate full-refresh count mismatch");
    require(totals.captures <= totals.makes && totals.terminalFailures <= totals.captures
              && totals.standardCastles + totals.atomic960Castles <= totals.makes,
            {}, "random special-move accounting was impossible");
    require(totals.directedMoves == DirectedMoveCount
              && totals.directedCaptures == DirectedCaptureCount
              && totals.directedTerminalFailures == DirectedTerminalFailureCount
              && totals.directedPromotions == DirectedPromotionCount
              && totals.directedEnPassants == DirectedEnPassantCount
              && totals.directedStandardCastles == DirectedStandardCastleCount
              && totals.directedAtomic960Castles == DirectedAtomic960CastleCount
              && totals.directedMaxBlast == DirectedMaximumBlast,
            {}, "directed Atomic fixture accounting was incomplete");
    require(totals.hmRefreshes > 0 && totals.hmDeltas > 0 && totals.hmReuses > 0
              && totals.relationRefreshes > 0,
            {}, "incremental HM/relation source coverage was incomplete");
    require(totals.snapshotMismatches >= 2 && totals.epSquareMismatches >= 2, {},
            "same-frame null/EP invalidation coverage was incomplete");
    require(totals.maxSourceDistance == IncrementalStack::MaxSize - 1, {},
            "MAX_PLY lazy source-distance coverage was not observed exactly");

    std::cout << "AtomicNNUEV3 incremental stress gate passed: mode=" << options.mode
              << " requested-operations=" << options.operations
              << " actual-operations=" << totals.operations << " makes=" << totals.makes
              << " undos=" << totals.undos << " evaluations=" << totals.evaluations
              << " full-refresh-comparisons=" << totals.fullRefreshComparisons
              << " random-captures=" << totals.captures
              << " random-terminal-failures=" << totals.terminalFailures
              << " random-standard-castles=" << totals.standardCastles
              << " random-atomic960-castles=" << totals.atomic960Castles
              << " directed-moves=" << totals.directedMoves
              << " directed-captures=" << totals.directedCaptures
              << " directed-terminal-failures=" << totals.directedTerminalFailures
              << " directed-promotions=" << totals.directedPromotions
              << " directed-en-passants=" << totals.directedEnPassants
              << " directed-standard-castles=" << totals.directedStandardCastles
              << " directed-atomic960-castles=" << totals.directedAtomic960Castles
              << " directed-max-blast=" << totals.directedMaxBlast << " threads=" << options.threads
              << " state-signature=0x" << std::hex << std::uppercase << std::setw(16)
              << std::setfill('0') << totals.signature << std::setfill(' ') << std::dec << '\n';
    return EXIT_SUCCESS;
}
