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
#include <cstdlib>
#include <filesystem>
#include <iostream>
#include <memory>
#include <sstream>
#include <string>
#include <string_view>
#include <type_traits>
#include <utility>

#include "../../../bitboard.h"
#include "../../../position.h"
#include "../../../uci_move.h"
#include "../incremental_backend.h"

namespace Stockfish::Eval::NNUE::AtomicV3 {
namespace {

constexpr std::string_view StartFEN = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1";

bool     dumpSequence   = false;
bool     requireIsaMode = false;
unsigned eventIndex     = 0;
SimdIsa  requiredIsa    = SimdIsa::Scalar;

[[noreturn]] void die(std::string_view label, std::string_view fen, const std::string& detail) {
    std::cerr << "AtomicNNUEV3 incremental gate FAILED\nlabel=" << label << "\nFEN=" << fen << "\n"
              << detail << '\n';
    std::exit(EXIT_FAILURE);
}

void require(bool               condition,
             std::string_view   label,
             std::string_view   fen,
             const std::string& detail) {
    if (!condition)
        die(label, fen, detail);
}

void pass(std::string_view label) {
    if (!dumpSequence)
        std::cout << "PASS " << label << '\n';
}

bool same_orientation(const JointOrientation& lhs, const JointOrientation& rhs) {
    return lhs.perspective == rhs.perspective && lhs.ownKing == rhs.ownKing
        && lhs.orientedOwnKing == rhs.orientedOwnKing && lhs.verticalXor == rhs.verticalXor
        && lhs.horizontalXor == rhs.horizontalXor && lhs.kingBucket == rhs.kingBucket;
}

template<typename Emission>
bool same_physical_rows(const Emission& lhs, const Emission& rhs) {
    if (lhs.size != rhs.size || !same_orientation(lhs.orientation, rhs.orientation))
        return false;
    for (IndexType index = 0; index < lhs.size; ++index)
        if (lhs.features[index].physicalIndex != rhs.features[index].physicalIndex)
            return false;
    return true;
}

template<typename Range>
std::string range_difference(std::string_view label, const Range& lhs, const Range& rhs) {
    if (lhs.size() != rhs.size())
        return std::string(label) + " size mismatch";
    for (std::size_t index = 0; index < lhs.size(); ++index)
        if (lhs[index] != rhs[index])
        {
            std::ostringstream detail;
            detail << label << '[' << index
                   << "] mismatch: incremental=" << static_cast<long long>(lhs[index])
                   << " fresh=" << static_cast<long long>(rhs[index]);
            return detail.str();
        }
    return {};
}

std::string emission_difference(std::string_view           label,
                                const FullRefreshEmission& incremental,
                                const FullRefreshEmission& fresh) {
    if (incremental.hm.networkBucket != fresh.hm.networkBucket)
        return std::string(label) + ".hm.network_bucket mismatch";

    const auto compare = [&](std::string_view suffix, const auto& lhs, const auto& rhs) {
        if (!same_orientation(lhs.orientation, rhs.orientation))
            return std::string(label) + '.' + std::string(suffix) + ".orientation mismatch";
        if (lhs.size != rhs.size)
            return std::string(label) + '.' + std::string(suffix) + ".size mismatch";
        for (IndexType index = 0; index < lhs.size; ++index)
            if (lhs.features[index].physicalIndex != rhs.features[index].physicalIndex)
            {
                std::ostringstream detail;
                detail << label << '.' << suffix << ".rows[" << index
                       << "] mismatch: incremental=" << lhs.features[index].physicalIndex
                       << " fresh=" << rhs.features[index].physicalIndex;
                return detail.str();
            }
        return std::string{};
    };

    std::string detail = compare("hm", incremental.hm, fresh.hm);
    if (detail.empty())
        detail = compare("capture_pair", incremental.capturePairs, fresh.capturePairs);
    if (detail.empty())
        detail = compare("king_blast_ep", incremental.kingBlastEp, fresh.kingBlastEp);
    if (detail.empty())
        detail = compare("blast_ring", incremental.blastRing, fresh.blastRing);
    return detail;
}

std::string scalar_difference(const ScalarDiagnostic& incremental, const ScalarDiagnostic& fresh) {
    if (incremental.sideToMove != fresh.sideToMove)
        return "side_to_move mismatch";
    if (incremental.networkBucket != fresh.networkBucket)
        return "network_bucket mismatch";

    for (const Color perspective : {WHITE, BLACK})
    {
        const std::size_t index  = static_cast<std::size_t>(perspective);
        const auto&       lhs    = incremental.perspectives[index];
        const auto&       rhs    = fresh.perspectives[index];
        const std::string prefix = perspective == WHITE ? "white" : "black";
        if (lhs.perspective != rhs.perspective)
            return prefix + ".perspective mismatch";
        if (const std::string detail =
              emission_difference(prefix + ".emission", lhs.emission, rhs.emission);
            !detail.empty())
            return detail;
        if (const std::string detail =
              range_difference(prefix + ".accumulator", lhs.accumulator, rhs.accumulator);
            !detail.empty())
            return detail;
        if (const std::string detail = range_difference(prefix + ".psqt", lhs.psqt, rhs.psqt);
            !detail.empty())
            return detail;
    }

    if (const std::string detail =
          range_difference("transformed", incremental.transformed, fresh.transformed);
        !detail.empty())
        return detail;
    if (incremental.psqtDifference != fresh.psqtDifference)
        return "psqt_difference mismatch";
    if (incremental.psqtValue != fresh.psqtValue)
        return "psqt_value mismatch";
    if (const std::string detail = range_difference("fc0", incremental.dense.fc0, fresh.dense.fc0);
        !detail.empty())
        return detail;
    if (const std::string detail =
          range_difference("fc0_squared", incremental.dense.fc0Squared, fresh.dense.fc0Squared);
        !detail.empty())
        return detail;
    if (const std::string detail =
          range_difference("fc0_clipped", incremental.dense.fc0Clipped, fresh.dense.fc0Clipped);
        !detail.empty())
        return detail;
    if (const std::string detail = range_difference("fc1", incremental.dense.fc1, fresh.dense.fc1);
        !detail.empty())
        return detail;
    if (const std::string detail =
          range_difference("fc1_squared", incremental.dense.fc1Squared, fresh.dense.fc1Squared);
        !detail.empty())
        return detail;
    if (const std::string detail =
          range_difference("fc1_clipped", incremental.dense.fc1Clipped, fresh.dense.fc1Clipped);
        !detail.empty())
        return detail;
    if (const std::string detail = range_difference("fc2", incremental.dense.fc2, fresh.dense.fc2);
        !detail.empty())
        return detail;
    if (incremental.rawOutput != fresh.rawOutput)
        return "raw_output mismatch";
    if (incremental.scaledOutput != fresh.scaledOutput)
        return "scaled_output mismatch";
    if (incremental.positionalValue != fresh.positionalValue)
        return "positional_value mismatch";
    return {};
}

std::string hm_difference(const ScalarHmPerspective& incremental,
                          const ScalarHmPerspective& fresh,
                          std::string_view           prefix) {
    if (const std::string detail = range_difference(std::string(prefix) + ".hm_only.accumulator",
                                                    incremental.accumulator, fresh.accumulator);
        !detail.empty())
        return detail;
    return range_difference(std::string(prefix) + ".hm_only.psqt", incremental.psqt, fresh.psqt);
}

bool same_scalar_status(const ScalarStatus& lhs, const ScalarStatus& rhs) {
    return lhs.code == rhs.code && lhs.featureError == rhs.featureError
        && lhs.numericError == rhs.numericError;
}

bool counters_equal(const IncrementalCounters& lhs, const IncrementalCounters& rhs) {
    return lhs.hmRefreshes == rhs.hmRefreshes && lhs.hmDeltas == rhs.hmDeltas
        && lhs.hmReuses == rhs.hmReuses && lhs.relationRefreshes == rhs.relationRefreshes
        && lhs.relationAccumulatorRefreshes == rhs.relationAccumulatorRefreshes
        && lhs.relationAccumulatorDeltas == rhs.relationAccumulatorDeltas
        && lhs.relationAccumulatorReuses == rhs.relationAccumulatorReuses
        && lhs.snapshotMismatches == rhs.snapshotMismatches
        && lhs.epSquareMismatches == rhs.epSquareMismatches;
}

bool hm_delta_counters_equal(const HmDeltaCounters& lhs, const HmDeltaCounters& rhs) {
    return lhs.removedRows == rhs.removedRows && lhs.addedRows == rhs.addedRows
        && lhs.i16Lanes == rhs.i16Lanes && lhs.sourcePermutationLanes == rhs.sourcePermutationLanes
        && lhs.publishPermutationLanes == rhs.publishPermutationLanes
        && lhs.scalarKernelCalls == rhs.scalarKernelCalls
        && lhs.sse41KernelCalls == rhs.sse41KernelCalls
        && lhs.avx2KernelCalls == rhs.avx2KernelCalls;
}

bool hm_delta_counters_advanced_by(const HmDeltaCounters& before,
                                   const HmDeltaCounters& event,
                                   const HmDeltaCounters& after) {
    HmDeltaCounters expected = before;
    expected.removedRows += event.removedRows;
    expected.addedRows += event.addedRows;
    expected.i16Lanes += event.i16Lanes;
    expected.sourcePermutationLanes += event.sourcePermutationLanes;
    expected.publishPermutationLanes += event.publishPermutationLanes;
    expected.scalarKernelCalls += event.scalarKernelCalls;
    expected.sse41KernelCalls += event.sse41KernelCalls;
    expected.avx2KernelCalls += event.avx2KernelCalls;
    return hm_delta_counters_equal(expected, after);
}

bool diagnostic_is_clear(const IncrementalDiagnostic& value) {
    const ScalarDiagnostic zeroScalar{};
    if (!scalar_difference(value.scalar, zeroScalar).empty())
        return false;

    for (const Color perspective : {WHITE, BLACK})
    {
        const std::size_t index = static_cast<std::size_t>(perspective);
        if (!std::all_of(value.hmOnly[index].accumulator.begin(),
                         value.hmOnly[index].accumulator.end(), [](i32 cell) { return cell == 0; })
            || !std::all_of(value.hmOnly[index].psqt.begin(), value.hmOnly[index].psqt.end(),
                            [](i64 cell) { return cell == 0; }))
            return false;
        const auto& update = value.hmUpdates[index];
        if (update.source != HmSourceKind::None || update.sourcePly != 0
            || update.sourceDistance != 0 || update.removedRows != 0 || update.addedRows != 0)
            return false;
        const auto& relation = value.relationUpdates[index];
        if (relation.source != RelationSourceKind::None || relation.sourcePly != 0
            || relation.sourceDistance != 0 || relation.removedRows != 0 || relation.addedRows != 0)
            return false;
    }

    const IncrementalCounters zeroCounters{};
    const HmDeltaCounters     zeroHmDeltaCounters{};
    return counters_equal(value.eventCounters, zeroCounters) && !value.hmDelta.enabled
        && value.hmDelta.requestedIsa == SimdIsa::Scalar
        && value.hmDelta.executedIsa == SimdIsa::Scalar
        && hm_delta_counters_equal(value.hmDelta.counters, zeroHmDeltaCounters) && value.ply == 0
        && !value.sameFrameSnapshotMismatch && !value.epSquareMismatch
        && value.previousEpSquare == SQ_NONE && value.currentEpSquare == SQ_NONE
        && value.previousSideToMove == WHITE && value.currentSideToMove == WHITE;
}

const char* source_name(HmSourceKind source) {
    switch (source)
    {
    case HmSourceKind::None :
        return "none";
    case HmSourceKind::SameFrameReuse :
        return "same_frame_reuse";
    case HmSourceKind::StackDelta :
        return "stack_delta";
    case HmSourceKind::FullRefresh :
        return "full_refresh";
    }
    return "unknown";
}

bool parse_isa(std::string_view value, SimdIsa& result) {
    if (value == "scalar")
        result = SimdIsa::Scalar;
    else if (value == "sse41")
        result = SimdIsa::Sse41;
    else if (value == "avx2")
        result = SimdIsa::Avx2;
    else
        return false;
    return true;
}

std::string square_name(Square square) { return square == SQ_NONE ? "-" : UCI::square(square); }

template<typename Range>
void print_csv(std::string_view key, const Range& values) {
    std::cout << key << '=';
    for (std::size_t index = 0; index < values.size(); ++index)
    {
        if (index)
            std::cout << ',';
        std::cout << static_cast<long long>(values[index]);
    }
    std::cout << '\n';
}

void print_orientation(std::string_view key, const JointOrientation& orientation) {
    std::cout << key << ".perspective=" << int(orientation.perspective) << '\n'
              << key << ".own_king=" << int(orientation.ownKing) << '\n'
              << key << ".oriented_own_king=" << int(orientation.orientedOwnKing) << '\n'
              << key << ".vertical_xor=" << int(orientation.verticalXor) << '\n'
              << key << ".horizontal_xor=" << int(orientation.horizontalXor) << '\n'
              << key << ".king_bucket=" << orientation.kingBucket << '\n';
}

template<typename Emission>
void print_rows(std::string_view key, const Emission& emission) {
    print_orientation(std::string(key) + ".orientation", emission.orientation);
    std::cout << key << ".size=" << emission.size << '\n' << key << ".rows=";
    for (IndexType index = 0; index < emission.size; ++index)
    {
        if (index)
            std::cout << ',';
        std::cout << emission.features[index].physicalIndex;
    }
    std::cout << '\n';
}

void print_scalar(std::string_view prefix, const ScalarDiagnostic& value) {
    std::cout << prefix << ".side_to_move=" << int(value.sideToMove) << '\n'
              << prefix << ".network_bucket=" << value.networkBucket << '\n';
    for (const Color perspective : {WHITE, BLACK})
    {
        const std::size_t index = static_cast<std::size_t>(perspective);
        const auto&       item  = value.perspectives[index];
        const std::string side  = perspective == WHITE ? ".white" : ".black";
        const std::string base  = std::string(prefix) + side;
        std::cout << base << ".perspective=" << int(item.perspective) << '\n';
        print_rows(base + ".hm", item.emission.hm);
        std::cout << base << ".hm.network_bucket=" << item.emission.hm.networkBucket << '\n';
        print_rows(base + ".capture_pair", item.emission.capturePairs);
        print_rows(base + ".king_blast_ep", item.emission.kingBlastEp);
        print_rows(base + ".blast_ring", item.emission.blastRing);
        print_csv(base + ".accumulator", item.accumulator);
        print_csv(base + ".psqt", item.psqt);
    }
    print_csv(std::string(prefix) + ".transformed", value.transformed);
    print_csv(std::string(prefix) + ".fc0", value.dense.fc0);
    print_csv(std::string(prefix) + ".fc0_squared", value.dense.fc0Squared);
    print_csv(std::string(prefix) + ".fc0_clipped", value.dense.fc0Clipped);
    print_csv(std::string(prefix) + ".fc1", value.dense.fc1);
    print_csv(std::string(prefix) + ".fc1_squared", value.dense.fc1Squared);
    print_csv(std::string(prefix) + ".fc1_clipped", value.dense.fc1Clipped);
    print_csv(std::string(prefix) + ".fc2", value.dense.fc2);
    std::cout << prefix << ".psqt_difference=" << value.psqtDifference << '\n'
              << prefix << ".psqt_value=" << value.psqtValue << '\n'
              << prefix << ".raw_output=" << value.rawOutput << '\n'
              << prefix << ".scaled_output=" << value.scaledOutput << '\n'
              << prefix << ".positional_value=" << value.positionalValue << '\n';
}

void print_hm_delta(const HmDeltaDiagnostic& value) {
    if (!requireIsaMode)
        return;

    const auto& counters = value.counters;
    std::cout << "incremental.hm_delta.enabled=" << int(value.enabled) << '\n'
              << "incremental.hm_delta.requested_isa=" << simd_isa_name(value.requestedIsa) << '\n'
              << "incremental.hm_delta.executed_isa=" << simd_isa_name(value.executedIsa) << '\n'
              << "incremental.hm_delta.counters.removed_rows=" << counters.removedRows << '\n'
              << "incremental.hm_delta.counters.added_rows=" << counters.addedRows << '\n'
              << "incremental.hm_delta.counters.i16_lanes=" << counters.i16Lanes << '\n'
              << "incremental.hm_delta.counters.source_permutation_lanes="
              << counters.sourcePermutationLanes << '\n'
              << "incremental.hm_delta.counters.publish_permutation_lanes="
              << counters.publishPermutationLanes << '\n'
              << "incremental.hm_delta.counters.scalar_kernel_calls=" << counters.scalarKernelCalls
              << '\n'
              << "incremental.hm_delta.counters.sse41_kernel_calls=" << counters.sse41KernelCalls
              << '\n'
              << "incremental.hm_delta.counters.avx2_kernel_calls=" << counters.avx2KernelCalls
              << '\n'
              << "incremental.hm_delta.counters.kernel_calls=" << counters.kernel_calls() << '\n'
              << "incremental.hm_delta.counters.fallback_calls="
              << counters.fallback_calls(value.requestedIsa) << '\n';
}

struct DirtyRecord {
    bool   present         = false;
    Piece  pc              = NO_PIECE;
    Square from            = SQ_NONE;
    Square to              = SQ_NONE;
    bool   requiresRefresh = false;
    Square removeSquare    = SQ_NONE;
    Square addSquare       = SQ_NONE;
    Piece  removePiece     = NO_PIECE;
    Piece  addPiece        = NO_PIECE;
    std::array<DirtyPiece::AtomicBlastPiece, DirtyPiece::MAX_ATOMIC_BLAST_PIECES> blast{};
    usize                                                                         blastSize = 0;
};

DirtyRecord record_dirty(const DirtyPiece& dirty) {
    DirtyRecord result{};
    result.present         = true;
    result.pc              = dirty.pc;
    result.from            = dirty.from;
    result.to              = dirty.to;
    result.requiresRefresh = dirty.requiresRefresh;
    result.removeSquare    = dirty.remove_sq;
    result.addSquare       = dirty.add_sq;
    result.removePiece     = dirty.remove_sq == SQ_NONE ? NO_PIECE : dirty.remove_pc;
    result.addPiece        = dirty.add_sq == SQ_NONE ? NO_PIECE : dirty.add_pc;
    result.blastSize       = dirty.atomicBlast.size();
    for (usize index = 0; index < result.blastSize; ++index)
        result.blast[index] = dirty.atomicBlast[int(index)];
    return result;
}

struct EventDescription {
    std::string label;
    std::string action;
    std::string move;
    std::string fen;
    bool        chess960 = false;
    DirtyRecord dirty{};
};

void dump_event(const EventDescription&      event,
                const CapturePairSnapshot&   snapshot,
                const ScalarStatus&          freshStatus,
                const IncrementalStatus&     status,
                const IncrementalDiagnostic& diagnostic,
                bool                         exact) {
    if (!dumpSequence)
        return;

    const unsigned current = eventIndex++;
    std::cout << "record=incremental_event\n"
              << "event=" << current << '\n'
              << "label=" << event.label << '\n'
              << "action=" << event.action << '\n'
              << "move=" << event.move << '\n'
              << "fen=" << event.fen << '\n'
              << "chess960=" << int(event.chess960) << '\n'
              << "snapshot.side_to_move=" << int(snapshot.sideToMove) << '\n'
              << "snapshot.ep_square=" << int(snapshot.epSquare) << '\n'
              << "snapshot.ep_name=" << square_name(snapshot.epSquare) << '\n';
    print_csv("snapshot.board", snapshot.board);

    std::cout << "fresh.code=" << int(freshStatus.code) << '\n'
              << "fresh.feature_error=" << int(freshStatus.featureError) << '\n'
              << "fresh.numeric_error=" << int(freshStatus.numericError) << '\n'
              << "incremental.error=" << int(status.error) << '\n'
              << "incremental.error_text=" << incremental_error_message(status.error) << '\n'
              << "incremental.feature_error=" << int(status.featureError) << '\n'
              << "incremental.scalar_code=" << int(status.scalarStatus.code) << '\n'
              << "incremental.scalar_feature_error=" << int(status.scalarStatus.featureError)
              << '\n'
              << "incremental.scalar_numeric_error=" << int(status.scalarStatus.numericError)
              << '\n'
              << "comparison.exact=" << int(exact) << '\n'
              << "incremental.ply=" << diagnostic.ply << '\n'
              << "incremental.same_frame_snapshot_mismatch="
              << int(diagnostic.sameFrameSnapshotMismatch) << '\n'
              << "incremental.ep_square_mismatch=" << int(diagnostic.epSquareMismatch) << '\n'
              << "incremental.previous_ep_square=" << int(diagnostic.previousEpSquare) << '\n'
              << "incremental.previous_ep_name=" << square_name(diagnostic.previousEpSquare) << '\n'
              << "incremental.current_ep_square=" << int(diagnostic.currentEpSquare) << '\n'
              << "incremental.current_ep_name=" << square_name(diagnostic.currentEpSquare) << '\n'
              << "incremental.previous_side_to_move=" << int(diagnostic.previousSideToMove) << '\n'
              << "incremental.current_side_to_move=" << int(diagnostic.currentSideToMove) << '\n';

    for (const Color perspective : {WHITE, BLACK})
    {
        const std::size_t index  = static_cast<std::size_t>(perspective);
        const std::string side   = perspective == WHITE ? "white" : "black";
        const auto&       update = diagnostic.hmUpdates[index];
        std::cout << "incremental." << side << ".hm_update.source=" << source_name(update.source)
                  << '\n'
                  << "incremental." << side << ".hm_update.source_ply=" << update.sourcePly << '\n'
                  << "incremental." << side
                  << ".hm_update.source_distance=" << update.sourceDistance << '\n'
                  << "incremental." << side << ".hm_update.removed_rows=" << update.removedRows
                  << '\n'
                  << "incremental." << side << ".hm_update.added_rows=" << update.addedRows << '\n';
        print_csv("incremental." + side + ".hm_only.accumulator",
                  diagnostic.hmOnly[index].accumulator);
        print_csv("incremental." + side + ".hm_only.psqt", diagnostic.hmOnly[index].psqt);
    }

    const auto& counters = diagnostic.eventCounters;
    std::cout << "incremental.counters.hm_refreshes=" << counters.hmRefreshes << '\n'
              << "incremental.counters.hm_deltas=" << counters.hmDeltas << '\n'
              << "incremental.counters.hm_reuses=" << counters.hmReuses << '\n'
              << "incremental.counters.relation_refreshes=" << counters.relationRefreshes << '\n'
              << "incremental.counters.snapshot_mismatches=" << counters.snapshotMismatches << '\n'
              << "incremental.counters.ep_square_mismatches=" << counters.epSquareMismatches << '\n'
              << "dirty.present=" << int(event.dirty.present) << '\n'
              << "dirty.pc=" << int(event.dirty.pc) << '\n'
              << "dirty.from=" << int(event.dirty.from) << '\n'
              << "dirty.from_name=" << square_name(event.dirty.from) << '\n'
              << "dirty.to=" << int(event.dirty.to) << '\n'
              << "dirty.to_name=" << square_name(event.dirty.to) << '\n'
              << "dirty.requires_refresh=" << int(event.dirty.requiresRefresh) << '\n'
              << "dirty.remove_square=" << int(event.dirty.removeSquare) << '\n'
              << "dirty.remove_piece=" << int(event.dirty.removePiece) << '\n'
              << "dirty.add_square=" << int(event.dirty.addSquare) << '\n'
              << "dirty.add_piece=" << int(event.dirty.addPiece) << '\n'
              << "dirty.atomic_blast_size=" << event.dirty.blastSize << '\n'
              << "dirty.atomic_blast_pieces=";
    for (usize index = 0; index < event.dirty.blastSize; ++index)
    {
        if (index)
            std::cout << ',';
        std::cout << int(event.dirty.blast[index].pc);
    }
    std::cout << '\n' << "dirty.atomic_blast_squares=";
    for (usize index = 0; index < event.dirty.blastSize; ++index)
    {
        if (index)
            std::cout << ',';
        std::cout << int(event.dirty.blast[index].square);
    }
    std::cout << '\n';
    print_hm_delta(diagnostic.hmDelta);
    print_scalar("incremental.scalar", diagnostic.scalar);
    std::cout << "end_event=" << current << '\n';
}

void set_position(Position&        position,
                  StateInfo&       state,
                  std::string_view fen,
                  bool             chess960,
                  std::string_view label) {
    if (auto error = position.set(std::string(fen), chess960, &state))
        die(label, fen, "invalid fixture: " + std::string(error->what()));
}

Move require_move(const Position& position, std::string_view move, std::string_view label) {
    const Move parsed = UCI::to_move(position, std::string(move));
    if (!parsed)
        die(label, position.fen(), "fixture move is not legal: " + std::string(move));
    return parsed;
}

void validate_success_event(const IncrementalStack&      stack,
                            const Network&               network,
                            const EventDescription&      event,
                            const CapturePairSnapshot&   snapshot,
                            const ScalarStatus&          freshStatus,
                            const ScalarDiagnostic&      fresh,
                            const IncrementalStatus&     status,
                            const IncrementalDiagnostic& incremental,
                            const HmDeltaCounters&       hmDeltaBefore) {
    require(bool(freshStatus), event.label, event.fen,
            "fresh scalar oracle failed: " + std::string(scalar_error_message(freshStatus.code)));
    require(bool(status), event.label, event.fen,
            "incremental evaluation failed: "
              + std::string(incremental_error_message(status.error)));
    require(status.featureError == FullRefreshError::None
              && same_scalar_status(status.scalarStatus, ScalarStatus{}),
            event.label, event.fen, "successful incremental status retained an error domain");

    const std::string detail = scalar_difference(incremental.scalar, fresh);
    require(detail.empty(), event.label, event.fen, detail);

    for (const Color perspective : {WHITE, BLACK})
    {
        const std::size_t   index = static_cast<std::size_t>(perspective);
        ScalarHmPerspective expected{};
        const ScalarError   error =
          accumulate_hm_scalar(network, fresh.perspectives[index].emission.hm, expected);
        require(error == ScalarError::None, event.label, event.fen,
                "fresh HM-only accumulation failed for perspective="
                  + std::to_string(int(perspective)));
        const std::string hmDetail = hm_difference(incremental.hmOnly[index], expected,
                                                   perspective == WHITE ? "white" : "black");
        require(hmDetail.empty(), event.label, event.fen, hmDetail);
    }

    const auto& counters = incremental.eventCounters;
    require(incremental.ply == stack.ply(), event.label, event.fen, "diagnostic ply mismatch");
    require(counters.relationRefreshes == COLOR_NB, event.label, event.fen,
            "relations were not refreshed once per perspective");
    require(counters.relationAccumulatorRefreshes + counters.relationAccumulatorDeltas
                + counters.relationAccumulatorReuses
              == COLOR_NB,
            event.label, event.fen,
            "relation accumulator source counters do not cover both perspectives");
    require(counters.hmRefreshes + counters.hmDeltas + counters.hmReuses == COLOR_NB, event.label,
            event.fen, "HM source counters do not cover both perspectives");
    require(stack.ep_square_when_computed() == snapshot.epSquare, event.label, event.fen,
            "committed frame retained the wrong EP square");

    require(stack.hm_delta_execution_enabled() == requireIsaMode, event.label, event.fen,
            "incremental stack has the wrong HM-delta execution mode");
    require(stack.requested_isa() == (requireIsaMode ? requiredIsa : SimdIsa::Scalar), event.label,
            event.fen, "incremental stack retained the wrong requested ISA");

    HmDeltaCounters expected{};
    u64             removedRows       = 0;
    u64             addedRows         = 0;
    usize           deltaPerspectives = 0;
    for (const Color perspective : {WHITE, BLACK})
    {
        require(incremental.relationUpdates[static_cast<std::size_t>(perspective)].source
                  != RelationSourceKind::None,
                event.label, event.fen, "relation accumulator omitted its source");
        const auto& update = incremental.hmUpdates[static_cast<std::size_t>(perspective)];
        if (update.source != HmSourceKind::StackDelta)
            continue;
        removedRows += update.removedRows;
        addedRows += update.addedRows;
        ++deltaPerspectives;
    }
    const u64 rowOperations = removedRows + addedRows;
    if (requireIsaMode)
    {
        expected.removedRows             = removedRows;
        expected.addedRows               = addedRows;
        expected.i16Lanes                = rowOperations * AccumulatorDimensions;
        expected.sourcePermutationLanes  = deltaPerspectives * AccumulatorDimensions;
        expected.publishPermutationLanes = deltaPerspectives * AccumulatorDimensions;
        switch (requiredIsa)
        {
        case SimdIsa::Scalar :
            expected.scalarKernelCalls = rowOperations;
            break;
        case SimdIsa::Sse41 :
            expected.sse41KernelCalls = rowOperations;
            break;
        case SimdIsa::Avx2 :
            expected.avx2KernelCalls = rowOperations;
            break;
        }
    }

    require(incremental.hmDelta.enabled == requireIsaMode, event.label, event.fen,
            "HM-delta diagnostic has the wrong enabled state");
    require(incremental.hmDelta.requestedIsa == (requireIsaMode ? requiredIsa : SimdIsa::Scalar),
            event.label, event.fen, "HM-delta diagnostic has the wrong requested ISA");
    require(incremental.hmDelta.executedIsa == (requireIsaMode ? requiredIsa : SimdIsa::Scalar),
            event.label, event.fen, "HM-delta diagnostic has the wrong executed ISA");
    require(hm_delta_counters_equal(incremental.hmDelta.counters, expected), event.label, event.fen,
            "per-event HM-delta accounting differs from the semantic updates");
    const u64 expectedKernelCalls = requireIsaMode ? rowOperations : 0;
    require(incremental.hmDelta.counters.kernel_calls() == expectedKernelCalls, event.label,
            event.fen,
            requireIsaMode ? "HM-delta kernel call total differs from row operations"
                           : "legacy scalar path unexpectedly published HM-delta kernel calls");
    require(incremental.hmDelta.counters.fallback_calls(incremental.hmDelta.requestedIsa) == 0,
            event.label, event.fen, "HM-delta execution used a scalar fallback");
    require(hm_delta_counters_advanced_by(hmDeltaBefore, incremental.hmDelta.counters,
                                          stack.hm_delta_counters()),
            event.label, event.fen,
            "cumulative HM-delta accounting did not advance transactionally");
}

std::unique_ptr<IncrementalDiagnostic> evaluate_success(IncrementalStack& stack,
                                                        const Network&    network,
                                                        const Position&   position,
                                                        EventDescription  event) {
    event.fen                               = position.fen();
    const CapturePairSnapshot snapshot      = make_capture_pair_snapshot(position);
    auto                      fresh         = std::make_unique<ScalarDiagnostic>();
    auto                      incremental   = std::make_unique<IncrementalDiagnostic>();
    const HmDeltaCounters     hmDeltaBefore = stack.hm_delta_counters();
    const ScalarStatus        freshStatus   = evaluate_scalar(network, snapshot, *fresh);
    const IncrementalStatus   status        = stack.evaluate(network, snapshot, *incremental);
    validate_success_event(stack, network, event, snapshot, freshStatus, *fresh, status,
                           *incremental, hmDeltaBefore);
    dump_event(event, snapshot, freshStatus, status, *incremental, true);
    return incremental;
}

std::unique_ptr<IncrementalStack> make_incremental_stack(const Network& network) {
    if (requireIsaMode)
        return std::make_unique<IncrementalStack>(network, requiredIsa);
    return std::make_unique<IncrementalStack>(network);
}

void require_source(const IncrementalDiagnostic& diagnostic,
                    Color                        perspective,
                    HmSourceKind                 expected,
                    std::string_view             label,
                    std::string_view             fen) {
    const HmSourceKind actual = diagnostic.hmUpdates[static_cast<std::size_t>(perspective)].source;
    require(actual == expected, label, fen,
            "unexpected HM source for perspective=" + std::to_string(int(perspective))
              + ": expected=" + source_name(expected) + " actual=" + source_name(actual));
}

void run_quiet_roundtrip(const Network& network) {
    Position                 position;
    std::array<StateInfo, 2> states{};
    set_position(position, states[0], StartFEN, false, "quiet-root");
    auto stack = make_incremental_stack(network);

    auto root =
      evaluate_success(*stack, network, position, {"quiet-root", "reset_root", "-", "", false, {}});
    require_source(*root, WHITE, HmSourceKind::FullRefresh, "quiet-root", position.fen());
    require_source(*root, BLACK, HmSourceKind::FullRefresh, "quiet-root", position.fen());

    const Move  move  = require_move(position, "e2e4", "quiet-push");
    DirtyPiece& dirty = stack->push();
    position.do_move(move, states[1], position.gives_check(move), dirty, nullptr, nullptr);
    const DirtyRecord dirtyRecord = record_dirty(dirty);
    auto              child       = evaluate_success(*stack, network, position,
                                                     {"quiet-child", "push_eval", "e2e4", "", false, dirtyRecord});
    require_source(*child, WHITE, HmSourceKind::StackDelta, "quiet-child", position.fen());
    require_source(*child, BLACK, HmSourceKind::StackDelta, "quiet-child", position.fen());

    position.undo_move(move);
    stack->pop();
    auto restored = evaluate_success(*stack, network, position,
                                     {"quiet-restored", "pop_eval", "e2e4", "", false, {}});
    require_source(*restored, WHITE, HmSourceKind::SameFrameReuse, "quiet-restored",
                   position.fen());
    require_source(*restored, BLACK, HmSourceKind::SameFrameReuse, "quiet-restored",
                   position.fen());
    const std::string detail = scalar_difference(restored->scalar, root->scalar);
    require(detail.empty(), "quiet-restored", position.fen(), "root restoration: " + detail);
    pass("AtomicNNUEV3 reset/quiet push-eval-pop");
}

void run_lazy_branch(const Network& network) {
    Position                 position;
    std::array<StateInfo, 6> states{};
    set_position(position, states[0], StartFEN, false, "lazy-root");
    auto stack = make_incremental_stack(network);

    auto root =
      evaluate_success(*stack, network, position, {"lazy-root", "reset_root", "-", "", false, {}});
    constexpr std::array<std::string_view, 4> Line{{"e2e4", "e7e5", "g1f3", "b8c6"}};
    std::array<Move, Line.size()>             moves{};
    std::array<DirtyRecord, Line.size()>      dirties{};
    for (std::size_t ply = 0; ply < Line.size(); ++ply)
    {
        moves[ply]        = require_move(position, Line[ply], "lazy-push");
        DirtyPiece& dirty = stack->push();
        position.do_move(moves[ply], states[ply + 1], position.gives_check(moves[ply]), dirty,
                         nullptr, nullptr);
        dirties[ply] = record_dirty(dirty);
    }

    auto leaf = evaluate_success(
      *stack, network, position,
      {"lazy-leaf", "lazy_push_eval", "e2e4,e7e5,g1f3,b8c6", "", false, dirties.back()});
    for (const Color perspective : {WHITE, BLACK})
    {
        require_source(*leaf, perspective, HmSourceKind::StackDelta, "lazy-leaf", position.fen());
        require(leaf->hmUpdates[static_cast<std::size_t>(perspective)].sourceDistance == 4,
                "lazy-leaf", position.fen(), "lazy leaf did not delta from evaluated root");
    }

    position.undo_move(moves[3]);
    stack->pop();
    auto ancestor = evaluate_success(*stack, network, position,
                                     {"lazy-undo", "undo_eval", "b8c6", "", false, {}});
    for (const Color perspective : {WHITE, BLACK})
    {
        require_source(*ancestor, perspective, HmSourceKind::StackDelta, "lazy-undo",
                       position.fen());
        require(ancestor->hmUpdates[static_cast<std::size_t>(perspective)].sourceDistance == 3,
                "lazy-undo", position.fen(), "unevaluated ancestor did not delta from root");
    }

    const Move  branchMove  = require_move(position, "g8f6", "lazy-branch");
    DirtyPiece& branchDirty = stack->push();
    position.do_move(branchMove, states[4], position.gives_check(branchMove), branchDirty, nullptr,
                     nullptr);
    auto branch = evaluate_success(
      *stack, network, position,
      {"lazy-branch", "branch_eval", "g8f6", "", false, record_dirty(branchDirty)});
    for (const Color perspective : {WHITE, BLACK})
    {
        require_source(*branch, perspective, HmSourceKind::StackDelta, "lazy-branch",
                       position.fen());
        require(branch->hmUpdates[static_cast<std::size_t>(perspective)].sourceDistance == 1,
                "lazy-branch", position.fen(), "branch did not delta from evaluated ancestor");
    }

    position.undo_move(branchMove);
    stack->pop();
    auto ancestorRestored = evaluate_success(
      *stack, network, position, {"lazy-branch-undo", "branch_pop_eval", "g8f6", "", false, {}});
    require_source(*ancestorRestored, WHITE, HmSourceKind::SameFrameReuse, "lazy-branch-undo",
                   position.fen());
    require_source(*ancestorRestored, BLACK, HmSourceKind::SameFrameReuse, "lazy-branch-undo",
                   position.fen());
    require(scalar_difference(ancestorRestored->scalar, ancestor->scalar).empty(),
            "lazy-branch-undo", position.fen(), "evaluated ancestor was not restored exactly");

    for (std::size_t ply = 3; ply-- > 0;)
    {
        position.undo_move(moves[ply]);
        stack->pop();
    }
    auto rootRestored = evaluate_success(
      *stack, network, position, {"lazy-root-restored", "multi_pop_eval", "-", "", false, {}});
    require(scalar_difference(rootRestored->scalar, root->scalar).empty(), "lazy-root-restored",
            position.fen(), "multi-ply undo did not restore root exactly");
    pass("AtomicNNUEV3 lazy multi-ply/undo/branch");
}

void run_relation_blocker(const Network& network) {
    constexpr std::string_view Fen = "r6k/8/8/8/8/8/N7/R6K w - - 0 1";
    Position                   position;
    std::array<StateInfo, 2>   states{};
    set_position(position, states[0], Fen, false, "relation-blocker-root");
    auto stack = make_incremental_stack(network);
    auto root  = evaluate_success(*stack, network, position,
                                  {"relation-blocker-root", "reset_root", "-", "", false, {}});

    const Move  move  = require_move(position, "a2b4", "relation-unblock");
    DirtyPiece& dirty = stack->push();
    position.do_move(move, states[1], position.gives_check(move), dirty, nullptr, nullptr);
    auto unblocked =
      evaluate_success(*stack, network, position,
                       {"relation-unblocked", "push_eval", "a2b4", "", false, record_dirty(dirty)});
    bool relationChanged = false;
    for (const Color perspective : {WHITE, BLACK})
    {
        const auto& before =
          root->scalar.perspectives[static_cast<std::size_t>(perspective)].emission;
        const auto& after =
          unblocked->scalar.perspectives[static_cast<std::size_t>(perspective)].emission;
        relationChanged |= !same_physical_rows(before.capturePairs, after.capturePairs)
                        || !same_physical_rows(before.kingBlastEp, after.kingBlastEp)
                        || !same_physical_rows(before.blastRing, after.blastRing);
    }
    require(relationChanged, "relation-unblocked", position.fen(),
            "quiet unblocker changed no relation row");
    require(unblocked->eventCounters.relationRefreshes == COLOR_NB, "relation-unblocked",
            position.fen(), "relation slices were not full-refreshed");

    position.undo_move(move);
    stack->pop();
    auto reblocked = evaluate_success(*stack, network, position,
                                      {"relation-reblocked", "pop_eval", "a2b4", "", false, {}});
    require(scalar_difference(reblocked->scalar, root->scalar).empty(), "relation-reblocked",
            position.fen(), "blocker undo did not restore relation result");
    pass("AtomicNNUEV3 relation blocker/unblocker refresh");
}

void run_king_orientation(const Network& network) {
    constexpr std::string_view Fen = "7k/8/8/8/8/8/8/3K4 w - - 0 1";
    Position                   position;
    std::array<StateInfo, 2>   states{};
    set_position(position, states[0], Fen, false, "king-mirror-root");
    auto stack = make_incremental_stack(network);
    auto root  = evaluate_success(*stack, network, position,
                                  {"king-mirror-root", "reset_root", "-", "", false, {}});
    require(root->scalar.perspectives[WHITE].emission.hm.orientation.horizontalXor == 7,
            "king-mirror-root", position.fen(), "left-half king did not select mirrored branch");

    const Move  move  = require_move(position, "d1e1", "king-mirror-cross");
    DirtyPiece& dirty = stack->push();
    position.do_move(move, states[1], position.gives_check(move), dirty, nullptr, nullptr);
    auto crossed =
      evaluate_success(*stack, network, position,
                       {"king-mirror-cross", "push_eval", "d1e1", "", false, record_dirty(dirty)});
    require(crossed->scalar.perspectives[WHITE].emission.hm.orientation.horizontalXor == 0,
            "king-mirror-cross", position.fen(), "right-half king did not select identity branch");
    require_source(*crossed, WHITE, HmSourceKind::FullRefresh, "king-mirror-cross", position.fen());
    require_source(*crossed, BLACK, HmSourceKind::StackDelta, "king-mirror-cross", position.fen());

    position.undo_move(move);
    stack->pop();
    auto restored = evaluate_success(*stack, network, position,
                                     {"king-mirror-restored", "pop_eval", "d1e1", "", false, {}});
    require(scalar_difference(restored->scalar, root->scalar).empty(), "king-mirror-restored",
            position.fen(), "orientation undo did not restore root");
    pass("AtomicNNUEV3 king mirror/orientation refresh");
}

void run_material_bucket(const Network& network) {
    constexpr std::string_view Fen = "7k/p7/8/8/8/8/R7/K6N w - - 0 1";
    Position                   position;
    std::array<StateInfo, 2>   states{};
    set_position(position, states[0], Fen, false, "bucket-root");
    auto stack = make_incremental_stack(network);
    auto root  = evaluate_success(*stack, network, position,
                                  {"bucket-root", "reset_root", "-", "", false, {}});
    require(root->scalar.networkBucket == 1, "bucket-root", position.fen(),
            "five-piece fixture did not select bucket 1");

    const Move  move  = require_move(position, "a2a7", "bucket-transition");
    DirtyPiece& dirty = stack->push();
    position.do_move(move, states[1], position.gives_check(move), dirty, nullptr, nullptr);
    auto child =
      evaluate_success(*stack, network, position,
                       {"bucket-transition", "push_eval", "a2a7", "", false, record_dirty(dirty)});
    require(child->scalar.networkBucket == 0, "bucket-transition", position.fen(),
            "three-piece capture did not select bucket 0");
    require_source(*child, WHITE, HmSourceKind::StackDelta, "bucket-transition", position.fen());
    require_source(*child, BLACK, HmSourceKind::StackDelta, "bucket-transition", position.fen());

    position.undo_move(move);
    stack->pop();
    auto restored = evaluate_success(*stack, network, position,
                                     {"bucket-restored", "pop_eval", "a2a7", "", false, {}});
    require(scalar_difference(restored->scalar, root->scalar).empty(), "bucket-restored",
            position.fen(), "bucket undo did not restore root");
    pass("AtomicNNUEV3 material bucket transition");
}

template<typename Validator>
void run_special_roundtrip(const Network&   network,
                           std::string_view label,
                           std::string_view fen,
                           std::string_view uci,
                           Validator&&      validator) {
    Position                 position;
    std::array<StateInfo, 2> states{};
    set_position(position, states[0], fen, false, label);
    auto stack = make_incremental_stack(network);
    auto root  = evaluate_success(*stack, network, position,
                                  {std::string(label) + "-root", "reset_root", "-", "", false, {}});

    const Move  move  = require_move(position, uci, label);
    DirtyPiece& dirty = stack->push();
    position.do_move(move, states[1], position.gives_check(move), dirty, nullptr, nullptr);
    const DirtyRecord record = record_dirty(dirty);
    auto              child  = evaluate_success(
      *stack, network, position,
      {std::string(label) + "-child", "push_eval", std::string(uci), "", false, record});
    validator(position, record, *root, *child);

    position.undo_move(move);
    stack->pop();
    auto restored = evaluate_success(
      *stack, network, position,
      {std::string(label) + "-restored", "pop_eval", std::string(uci), "", false, {}});
    const std::string detail = scalar_difference(restored->scalar, root->scalar);
    require(detail.empty(), std::string(label) + "-restored", position.fen(),
            "special-move undo: " + detail);
    pass(std::string("AtomicNNUEV3 ") + std::string(label));
}

void run_special_moves(const Network& network) {
    run_special_roundtrip(network, "en-passant", "7k/8/2N1b3/2ppP3/8/8/8/K7 w - d6 0 2", "e5d6",
                          [](const Position& position, const DirtyRecord& dirty,
                             const IncrementalDiagnostic&, const IncrementalDiagnostic&) {
                              require(position.ep_square() == SQ_NONE, "en-passant-child",
                                      position.fen(), "EP square survived the move");
                              require(dirty.present && dirty.blastSize != 0, "en-passant-child",
                                      position.fen(), "Atomic EP produced no blast delta");
                          });

    run_special_roundtrip(
      network, "promotion", "7k/P7/8/8/8/8/8/K7 w - - 0 1", "a7a8q",
      [](const Position& position, const DirtyRecord& dirty, const IncrementalDiagnostic&,
         const IncrementalDiagnostic&) {
          require(position.piece_on(SQ_A8) == W_QUEEN, "promotion-child", position.fen(),
                  "promotion did not create the requested queen");
          require(dirty.addSquare == SQ_A8 && dirty.addPiece == W_QUEEN, "promotion-child",
                  position.fen(), "promotion DirtyPiece omitted the promoted queen");
      });

    run_special_roundtrip(
      network, "atomic-explosion", "7k/8/8/2pBn3/3r4/2PQN3/8/K7 w - - 0 1", "d3d4",
      [](const Position& position, const DirtyRecord& dirty, const IncrementalDiagnostic&,
         const IncrementalDiagnostic&) {
          require(dirty.blastSize >= 3, "atomic-explosion-child", position.fen(),
                  "multi-piece Atomic explosion was not represented in DirtyPiece");
          require(position.has_king(WHITE) && position.has_king(BLACK), "atomic-explosion-child",
                  position.fen(), "non-terminal explosion unexpectedly removed a king");
      });
}

void run_null_ep(const Network& network) {
    constexpr std::string_view Fen = "7k/8/2N1b3/2ppP3/8/8/8/K7 w - d6 0 2";
    Position                   position;
    std::array<StateInfo, 2>   states{};
    set_position(position, states[0], Fen, false, "null-ep-parent");
    auto stack  = make_incremental_stack(network);
    auto parent = evaluate_success(*stack, network, position,
                                   {"null-ep-parent", "reset_root", "-", "", false, {}});
    require(position.ep_square() == SQ_D6, "null-ep-parent", position.fen(),
            "parent fixture did not retain valid EP metadata");
    const usize sizeBefore = stack->size();

    position.do_null_move(states[1]);
    require(stack->size() == sizeBefore, "null-ep-child", position.fen(),
            "test accidentally pushed the incremental stack for null move");
    auto nullChild = evaluate_success(
      *stack, network, position, {"null-ep-child", "do_null_eval_no_push", "null", "", false, {}});
    require(stack->size() == sizeBefore, "null-ep-child", position.fen(),
            "incremental evaluation changed stack depth on null move");
    require(nullChild->sameFrameSnapshotMismatch && nullChild->epSquareMismatch, "null-ep-child",
            position.fen(), "null move did not diagnose same-frame EP change");
    require(nullChild->previousEpSquare == SQ_D6 && nullChild->currentEpSquare == SQ_NONE,
            "null-ep-child", position.fen(), "null move EP diagnostic is incorrect");
    require(nullChild->previousSideToMove == WHITE && nullChild->currentSideToMove == BLACK,
            "null-ep-child", position.fen(), "null move side-to-move diagnostic is incorrect");
    require_source(*nullChild, WHITE, HmSourceKind::SameFrameReuse, "null-ep-child",
                   position.fen());
    require_source(*nullChild, BLACK, HmSourceKind::SameFrameReuse, "null-ep-child",
                   position.fen());

    position.undo_null_move();
    require(stack->size() == sizeBefore, "null-ep-restored", position.fen(),
            "test accidentally popped the incremental stack for null undo");
    auto restored =
      evaluate_success(*stack, network, position,
                       {"null-ep-restored", "undo_null_eval_no_pop", "null", "", false, {}});
    require(restored->sameFrameSnapshotMismatch && restored->epSquareMismatch, "null-ep-restored",
            position.fen(), "null undo did not diagnose restored same-frame EP state");
    require(restored->previousEpSquare == SQ_NONE && restored->currentEpSquare == SQ_D6,
            "null-ep-restored", position.fen(), "null undo EP diagnostic is incorrect");
    const std::string detail = scalar_difference(restored->scalar, parent->scalar);
    require(detail.empty(), "null-ep-restored", position.fen(),
            "null undo did not restore exact parent scalar output: " + detail);
    pass("AtomicNNUEV3 EP null move without stack push/pop");
}

void evaluate_expected_failure(IncrementalStack&          stack,
                               const Network&             network,
                               const CapturePairSnapshot& snapshot,
                               EventDescription           event,
                               IncrementalError           expectedError,
                               bool                       freshShouldSucceed,
                               FullRefreshError expectedFeatureError = FullRefreshError::None) {
    auto fresh                              = std::make_unique<ScalarDiagnostic>();
    auto incremental                        = std::make_unique<IncrementalDiagnostic>();
    incremental->scalar.rawOutput           = 0x12345678;
    incremental->scalar.positionalValue     = -321;
    incremental->ply                        = 99;
    incremental->sameFrameSnapshotMismatch  = true;
    const IncrementalCounters before        = stack.counters();
    const HmDeltaCounters     hmDeltaBefore = stack.hm_delta_counters();
    const ScalarStatus        freshStatus   = evaluate_scalar(network, snapshot, *fresh);
    const IncrementalStatus   status        = stack.evaluate(network, snapshot, *incremental);

    require(bool(freshStatus) == freshShouldSucceed, event.label, event.fen,
            "fresh oracle success/failure did not match the fixture contract");
    require(status.error == expectedError, event.label, event.fen,
            "unexpected incremental failure: expected="
              + std::string(incremental_error_message(expectedError))
              + " actual=" + incremental_error_message(status.error));
    if (expectedError == IncrementalError::FeatureOracleError)
    {
        require(status.featureError == expectedFeatureError, event.label, event.fen,
                "incremental feature failure lost the exact oracle error");
        require(freshStatus.featureError == expectedFeatureError, event.label, event.fen,
                "fresh and incremental feature error domains diverged");
    }
    require(diagnostic_is_clear(*incremental), event.label, event.fen,
            "failed evaluation published a partial diagnostic");
    require(counters_equal(stack.counters(), before), event.label, event.fen,
            "failed evaluation mutated cumulative counters");
    require(hm_delta_counters_equal(stack.hm_delta_counters(), hmDeltaBefore), event.label,
            event.fen, "failed evaluation mutated cumulative HM-delta counters");
    dump_event(event, snapshot, freshStatus, status, *incremental, true);
}

void run_failure_transactionality(const Network& network, const std::filesystem::path& netPath) {
    Position  position;
    StateInfo state{};
    set_position(position, state, StartFEN, false, "failure-root");
    auto                      stack    = make_incremental_stack(network);
    auto                      baseline = evaluate_success(*stack, network, position,
                                                          {"failure-root", "reset_root", "-", "", false, {}});
    const CapturePairSnapshot valid    = make_capture_pair_snapshot(position);

    for (const auto [fault, label] :
         {std::pair{IncrementalFaultPoint::AfterFirstPerspective, "fault-after-white"},
          std::pair{IncrementalFaultPoint::BeforeComposition, "fault-before-composition"}})
    {
        stack->set_test_fault(fault);
        evaluate_expected_failure(*stack, network, valid,
                                  {label, "injected_failure", "-", position.fen(), false, {}},
                                  IncrementalError::InjectedFailure, true);
        stack->set_test_fault(IncrementalFaultPoint::None);
        auto restored = evaluate_success(
          *stack, network, position,
          {std::string(label) + "-restored", "post_failure_eval", "-", "", false, {}});
        require(scalar_difference(restored->scalar, baseline->scalar).empty(), label,
                position.fen(), "injected failure mutated the committed frame");
        require_source(*restored, WHITE, HmSourceKind::SameFrameReuse, label, position.fen());
        require_source(*restored, BLACK, HmSourceKind::SameFrameReuse, label, position.fen());
    }

    CapturePairSnapshot missingKing{};
    missingKing.board[SQ_A1] = W_KING;
    missingKing.sideToMove   = WHITE;
    missingKing.epSquare     = SQ_NONE;
    evaluate_expected_failure(
      *stack, network, missingKing,
      {"failure-missing-black-king", "feature_failure", "-", "snapshot", false, {}},
      IncrementalError::FeatureOracleError, false, FullRefreshError::MissingBlackKing);
    auto afterFeatureFailure =
      evaluate_success(*stack, network, position,
                       {"failure-feature-restored", "post_failure_eval", "-", "", false, {}});
    require(scalar_difference(afterFeatureFailure->scalar, baseline->scalar).empty(),
            "failure-feature-restored", position.fen(),
            "feature failure mutated the committed frame");

    LoadResult second = load_candidate(netPath);
    require(bool(second), "failure-network-identity", position.fen(),
            "could not authenticate a second fixture instance: " + second.error);
    evaluate_expected_failure(
      *stack, *second.network, valid,
      {"failure-network-identity", "network_mismatch", "-", position.fen(), false, {}},
      IncrementalError::NetworkMismatch, true);
    auto afterNetworkFailure =
      evaluate_success(*stack, network, position,
                       {"failure-network-restored", "post_failure_eval", "-", "", false, {}});
    require(scalar_difference(afterNetworkFailure->scalar, baseline->scalar).empty(),
            "failure-network-restored", position.fen(),
            "network mismatch mutated the committed frame");
    pass("AtomicNNUEV3 failure transactionality");
}

void run_unsupported_isa_transactionality(const Network& network) {
    Position  position;
    StateInfo state{};
    set_position(position, state, StartFEN, false, "unsupported-isa");

    std::array<SimdIsa, 3> unsupported{};
    usize                  unsupportedCount = 0;
    for (const SimdIsa isa : {SimdIsa::Sse41, SimdIsa::Avx2})
        if (!simd_isa_available(isa))
            unsupported[unsupportedCount++] = isa;
    unsupported[unsupportedCount++] = static_cast<SimdIsa>(255);

    for (usize isaIndex = 0; isaIndex < unsupportedCount; ++isaIndex)
    {
        auto         stack = std::make_unique<IncrementalStack>(network, unsupported[isaIndex]);
        const usize  sizeBefore                  = stack->size();
        const Square epBefore                    = stack->ep_square_when_computed();
        const IncrementalCounters countersBefore = stack->counters();
        const HmDeltaCounters     hmDeltaBefore  = stack->hm_delta_counters();
        const CapturePairSnapshot snapshot       = make_capture_pair_snapshot(position);

        for (unsigned probe = 0; probe < 2; ++probe)
        {
            auto diagnostic                    = std::make_unique<IncrementalDiagnostic>();
            diagnostic->scalar.rawOutput       = 0x12345678;
            diagnostic->scalar.positionalValue = -321;
            diagnostic->hmDelta.enabled        = true;
            diagnostic->hmDelta.requestedIsa   = SimdIsa::Avx2;
            diagnostic->hmDelta.executedIsa    = SimdIsa::Avx2;
            diagnostic->hmDelta.counters.avx2KernelCalls = 99;
            diagnostic->ply                              = 99;
            diagnostic->sameFrameSnapshotMismatch        = true;

            const IncrementalStatus status = stack->evaluate(network, snapshot, *diagnostic);
            require(status.error == IncrementalError::UnsupportedIsa, "unsupported-isa",
                    position.fen(),
                    "unavailable exact ISA request did not fail with UnsupportedIsa");
            require(diagnostic_is_clear(*diagnostic), "unsupported-isa", position.fen(),
                    "unavailable exact ISA request published a partial diagnostic");
            require(stack->size() == sizeBefore && stack->ep_square_when_computed() == epBefore,
                    "unsupported-isa", position.fen(),
                    "unavailable exact ISA request mutated incremental frame state");
            require(counters_equal(stack->counters(), countersBefore), "unsupported-isa",
                    position.fen(), "unavailable exact ISA request mutated cumulative counters");
            require(hm_delta_counters_equal(stack->hm_delta_counters(), hmDeltaBefore),
                    "unsupported-isa", position.fen(),
                    "unavailable exact ISA request mutated cumulative HM-delta counters");
        }
    }
    pass("AtomicNNUEV3 named and invalid unsupported ISA transactionality");
}

void run_reset_mode_contract(const Network& network) {
    auto stack = std::make_unique<IncrementalStack>(network, SimdIsa::Avx2);
    require(stack->hm_delta_execution_enabled() && stack->requested_isa() == SimdIsa::Avx2,
            "reset-mode-simd", StartFEN, "exact-ISA constructor did not retain its execution mode");

    stack->push();
    stack->reset(network);
    require(!stack->hm_delta_execution_enabled() && stack->requested_isa() == SimdIsa::Scalar,
            "reset-mode-legacy", StartFEN, "legacy reset inherited an earlier SIMD execution mode");
    require(stack->size() == 1 && counters_equal(stack->counters(), {})
              && hm_delta_counters_equal(stack->hm_delta_counters(), {}),
            "reset-mode-legacy", StartFEN, "legacy reset did not clear stack state and counters");

    stack->reset(network, SimdIsa::Sse41);
    require(stack->hm_delta_execution_enabled() && stack->requested_isa() == SimdIsa::Sse41,
            "reset-mode-simd-restored", StartFEN,
            "exact-ISA reset was overwritten by the common reset path");
    require(stack->size() == 1 && counters_equal(stack->counters(), {})
              && hm_delta_counters_equal(stack->hm_delta_counters(), {}),
            "reset-mode-simd-restored", StartFEN,
            "exact-ISA reset did not clear stack state and counters");

    pass("AtomicNNUEV3 reset execution-mode contract");
}

void run_runtime_identity(const Network& network) {
    constexpr std::array<std::string_view, 7> Fens{StartFEN,
                                                   "r6k/8/8/8/8/8/N7/R6K w - - 0 1",
                                                   "7k/8/8/8/8/8/8/3K4 w - - 0 1",
                                                   "7k/p7/8/8/8/8/R7/K6N w - - 0 1",
                                                   "7k/8/2N1b3/2ppP3/8/8/8/K7 w - d6 0 2",
                                                   "7k/P7/8/8/8/8/8/K7 w - - 0 1",
                                                   "7k/8/8/2pBn3/3r4/2PQN3/8/K7 w - - 0 1"};

    auto diagnosticStack = make_incremental_stack(network);
    auto runtimeStack    = make_incremental_stack(network);
    for (std::size_t index = 0; index < Fens.size(); ++index)
    {
        Position          position;
        StateInfo         state{};
        const std::string label = "runtime-identity-" + std::to_string(index);
        set_position(position, state, Fens[index], false, label);

        auto                    diagnostic = std::make_unique<IncrementalDiagnostic>();
        RuntimeOutput           runtime{};
        const IncrementalStatus diagnosticStatus =
          diagnosticStack->evaluate(network, position, *diagnostic);
        const IncrementalStatus runtimeStatus =
          runtimeStack->evaluate_runtime(network, position, runtime);
        require(bool(diagnosticStatus) && bool(runtimeStatus), label, position.fen(),
                "diagnostic or production evaluation failed");
        require(runtime.psqtDifference == diagnostic->scalar.psqtDifference, label, position.fen(),
                "production PSQT differs from the exact diagnostic path");
        require(runtime.rawOutput == diagnostic->scalar.rawOutput, label, position.fen(),
                "production raw dense output differs from the exact diagnostic path");
        require(runtime.scaledOutput == diagnostic->scalar.scaledOutput, label, position.fen(),
                "production scaled dense output differs from the exact diagnostic path");
    }

    pass("AtomicNNUEV3 production/diagnostic identity corpus");
}

u64 relation_accumulator_events(const IncrementalCounters& counters) {
    return counters.relationAccumulatorRefreshes + counters.relationAccumulatorDeltas
         + counters.relationAccumulatorReuses;
}

void require_runtime_full_refresh_identity(IncrementalStack& stack,
                                           const Network&    network,
                                           const Position&   position,
                                           std::string_view  label) {
    auto               fresh       = std::make_unique<ScalarDiagnostic>();
    const ScalarStatus freshStatus = evaluate_scalar(network, position, *fresh);
    require(bool(freshStatus), label, position.fen(),
            "fresh scalar oracle failed before production comparison");

    RuntimeOutput           runtime{};
    const IncrementalStatus status = stack.evaluate_runtime(network, position, runtime);
    require(bool(status), label, position.fen(),
            "production relation-cache evaluation failed: "
              + std::string(incremental_error_message(status.error)));
    require(runtime.psqtDifference == fresh->psqtDifference, label, position.fen(),
            "cached production PSQT differs from full refresh");
    require(runtime.rawOutput == fresh->rawOutput, label, position.fen(),
            "cached production raw output differs from full refresh");
    require(runtime.scaledOutput == fresh->scaledOutput, label, position.fen(),
            "cached production scaled output differs from full refresh");
}

void run_runtime_relation_roundtrips(const Network& network) {
    struct Fixture {
        std::string_view label;
        std::string_view fen;
        std::string_view uci;
        bool             chess960;
    };
    constexpr std::array<Fixture, 7> Fixtures{{
      {"runtime-quiet", StartFEN, "e2e4", false},
      {"runtime-relation-stable", "rnbq1bnr/pppp1kpp/4p3/5p2/3P4/6P1/PPPQPPBP/RNB1K1NR b KQ - 2 4",
       "g8e7", false},
      {"runtime-en-passant", "7k/8/2N1b3/2ppP3/8/8/8/K7 w - d6 0 2", "e5d6", false},
      {"runtime-promotion", "7k/P7/8/8/8/8/8/K7 w - - 0 1", "a7a8q", false},
      {"runtime-explosion", "7k/8/8/2pBn3/3r4/2PQN3/8/K7 w - - 0 1", "d3d4", false},
      {"runtime-castling", "4k3/8/8/8/8/8/8/R3K2R w KQ - 0 1", "e1g1", false},
      {"runtime-atomic960-castling", "7k/8/8/8/8/8/8/1RK5 w Q - 0 1", "c1b1", true},
    }};

    bool sawDelta = false;
    for (std::size_t fixtureIndex = 0; fixtureIndex < Fixtures.size(); ++fixtureIndex)
    {
        const auto&              fixture = Fixtures[fixtureIndex];
        Position                 position;
        std::array<StateInfo, 2> states{};
        set_position(position, states[0], fixture.fen, fixture.chess960, fixture.label);
        const std::string rootFen = position.fen();
        const Key         rootKey = position.key();
        auto              stack   = make_incremental_stack(network);

        const IncrementalCounters beforeRoot = stack->counters();
        require_runtime_full_refresh_identity(*stack, network, position,
                                              std::string(fixture.label) + "-root");
        const IncrementalCounters afterRoot = stack->counters();
        require(afterRoot.relationAccumulatorRefreshes
                  == beforeRoot.relationAccumulatorRefreshes + COLOR_NB,
                fixture.label, position.fen(),
                "root did not full-refresh both relation accumulators");

        const Move  move  = require_move(position, fixture.uci, fixture.label);
        DirtyPiece& dirty = stack->push();
        position.do_move(move, states[1], position.gives_check(move), dirty, nullptr, nullptr);

        if (fixtureIndex == 0)
            for (const auto fault : {IncrementalFaultPoint::AfterFirstPerspective,
                                     IncrementalFaultPoint::BeforeComposition,
                                     IncrementalFaultPoint::AfterCompositionBeforeCommit})
            {
                const IncrementalCounters beforeFault = stack->counters();
                RuntimeOutput             failed{17, 19, 23};
                stack->set_test_fault(fault);
                const IncrementalStatus status = stack->evaluate_runtime(network, position, failed);
                require(status.error == IncrementalError::InjectedFailure, fixture.label,
                        position.fen(), "runtime relation fault returned the wrong error");
                require(failed.psqtDifference == 0 && failed.rawOutput == 0
                          && failed.scaledOutput == 0,
                        fixture.label, position.fen(),
                        "runtime relation fault published a partial output");
                require(counters_equal(stack->counters(), beforeFault), fixture.label,
                        position.fen(), "runtime relation fault committed counters/state");
            }
        stack->set_test_fault(IncrementalFaultPoint::None);

        const IncrementalCounters beforeChild = stack->counters();
        require_runtime_full_refresh_identity(*stack, network, position,
                                              std::string(fixture.label) + "-child");
        const IncrementalCounters afterChild = stack->counters();
        require(relation_accumulator_events(afterChild)
                  == relation_accumulator_events(beforeChild) + COLOR_NB,
                fixture.label, position.fen(),
                "child relation accumulator sources do not cover both perspectives");
        sawDelta |= afterChild.relationAccumulatorDeltas > beforeChild.relationAccumulatorDeltas;

        position.undo_move(move);
        stack->pop();
        const IncrementalCounters beforeUndo = stack->counters();
        require_runtime_full_refresh_identity(*stack, network, position,
                                              std::string(fixture.label) + "-undo");
        const IncrementalCounters afterUndo = stack->counters();
        require(position.fen() == rootFen && position.key() == rootKey, fixture.label,
                position.fen(), "runtime relation roundtrip did not restore FEN/key");
        require(afterUndo.relationAccumulatorReuses
                  == beforeUndo.relationAccumulatorReuses + COLOR_NB,
                fixture.label, position.fen(),
                "undo did not reuse both committed parent relation accumulators");
    }
    require(sawDelta, "runtime-relation-delta", StartFEN,
            "directed runtime corpus never exercised a relation-row delta");
    pass("AtomicNNUEV3 runtime relation cache special-move/full-refresh/undo/fault identity");
}

}  // namespace
}  // namespace Stockfish::Eval::NNUE::AtomicV3

int main(int argc, char* argv[]) {
    using namespace Stockfish;
    using namespace Stockfish::Eval::NNUE::AtomicV3;

    constexpr std::string_view Usage = "usage: atomic-v3-incremental-tests NET [--dump-sequence] "
                                       "[--require-isa scalar|sse41|avx2]";
    if (argc < 2)
    {
        std::cerr << Usage << '\n';
        return EXIT_FAILURE;
    }

    bool haveDump = false;
    bool haveIsa  = false;
    for (int index = 2; index < argc; ++index)
    {
        const std::string_view argument = argv[index];
        if (argument == "--dump-sequence" && !haveDump)
        {
            dumpSequence = true;
            haveDump     = true;
        }
        else if (argument == "--require-isa" && index + 1 < argc && !haveIsa)
        {
            if (!parse_isa(argv[++index], requiredIsa))
            {
                std::cerr << Usage << '\n';
                return EXIT_FAILURE;
            }
            requireIsaMode = true;
            haveIsa        = true;
        }
        else
        {
            std::cerr << Usage << '\n';
            return EXIT_FAILURE;
        }
    }

    // An exact ISA request is a gate, never a dispatch hint. Reject it before
    // initializing engine state or constructing/mutating any incremental stack.
    if (requireIsaMode && !simd_isa_available(requiredIsa))
    {
        std::cerr << "required_isa=" << simd_isa_name(requiredIsa) << " available=0\n";
        return EXIT_FAILURE;
    }

    Bitboards::init();
    Attacks::init();
    Position::init();

    const std::filesystem::path netPath(argv[1]);
    LoadResult                  loaded = load_candidate(netPath);
    if (!loaded)
    {
        std::cerr << "error=" << loaded.error << '\n';
        return EXIT_FAILURE;
    }

    run_quiet_roundtrip(*loaded.network);
    run_lazy_branch(*loaded.network);
    run_relation_blocker(*loaded.network);
    run_king_orientation(*loaded.network);
    run_material_bucket(*loaded.network);
    run_special_moves(*loaded.network);
    run_null_ep(*loaded.network);
    run_runtime_identity(*loaded.network);
    run_runtime_relation_roundtrips(*loaded.network);
    run_failure_transactionality(*loaded.network, netPath);
    run_reset_mode_contract(*loaded.network);
    run_unsupported_isa_transactionality(*loaded.network);

    if (dumpSequence)
        std::cout << "sequence_events=" << eventIndex << '\n';
    else
        std::cout << "AtomicNNUEV3 incremental scalar gate passed\n";
    return EXIT_SUCCESS;
}
