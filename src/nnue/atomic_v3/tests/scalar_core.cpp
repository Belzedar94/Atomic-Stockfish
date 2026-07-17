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
#include <atomic>
#include <chrono>
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
#include <thread>
#include <type_traits>
#include <utility>
#include <vector>

#include "../../../bitboard.h"
#include "../../../position.h"
#include "../scalar_backend.h"
#include "../wire_io.h"

namespace Stockfish::Eval::NNUE::AtomicV3 {
namespace {

int failures = 0;

void expect(bool condition, std::string_view label) {
    if (condition)
        std::cout << "PASS " << label << '\n';
    else
    {
        std::cerr << "FAIL " << label << '\n';
        ++failures;
    }
}

template<typename Range>
bool all_zero(const Range& range) {
    return std::all_of(range.begin(), range.end(), [](const auto value) { return value == 0; });
}

bool diagnostic_is_clear(const ScalarDiagnostic& value) {
    if (value.sideToMove != WHITE || value.networkBucket != 0 || value.psqtDifference != 0
        || value.psqtValue != 0 || value.rawOutput != 0 || value.scaledOutput != 0
        || value.positionalValue != 0 || !all_zero(value.transformed) || !all_zero(value.dense.fc0)
        || !all_zero(value.dense.fc0Squared) || !all_zero(value.dense.fc0Clipped)
        || !all_zero(value.dense.fc1) || !all_zero(value.dense.fc1Squared)
        || !all_zero(value.dense.fc1Clipped) || !all_zero(value.dense.fc2))
        return false;

    for (const auto& perspective : value.perspectives)
        if (!all_zero(perspective.accumulator) || !all_zero(perspective.psqt)
            || perspective.emission.active_feature_count() != 0)
            return false;
    return true;
}

template<typename IntType, std::size_t BlockBytes>
i64 canonical_parameter(const IntType* values, std::size_t canonicalIndex) {
    return values[WireIO::internal_index_from_canonical<IntType, BlockBytes>(canonicalIndex)];
}

i64 expected_accumulator_cell(const Network&             network,
                              const FullRefreshEmission& emission,
                              std::size_t                output) {
    i64 expected = canonical_parameter<i16, 16>(network.biases(), output);
    for (IndexType index = 0; index < emission.hm.size; ++index)
        expected += canonical_parameter<i16, 16>(
          network.hm_weights(),
          emission.hm.features[index].physicalIndex * AccumulatorDimensions + output);
    for (IndexType index = 0; index < emission.capturePairs.size; ++index)
        expected += canonical_parameter<i8, 8>(
          network.capture_pair_weights(),
          (emission.capturePairs.features[index].physicalIndex - CapturePairPhysicalOffset)
              * AccumulatorDimensions
            + output);
    for (IndexType index = 0; index < emission.kingBlastEp.size; ++index)
        expected += canonical_parameter<i16, 16>(
          network.king_blast_ep_weights(),
          (emission.kingBlastEp.features[index].physicalIndex - KingBlastEpPhysicalOffset)
              * AccumulatorDimensions
            + output);
    for (IndexType index = 0; index < emission.blastRing.size; ++index)
        expected += canonical_parameter<i8, 8>(
          network.blast_ring_weights(),
          (emission.blastRing.features[index].physicalIndex - BlastRingPhysicalOffset)
              * AccumulatorDimensions
            + output);
    return expected;
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

bool same_full_refresh(const FullRefreshEmission& lhs, const FullRefreshEmission& rhs) {
    return lhs.hm.networkBucket == rhs.hm.networkBucket && same_physical_rows(lhs.hm, rhs.hm)
        && same_physical_rows(lhs.capturePairs, rhs.capturePairs)
        && same_physical_rows(lhs.kingBlastEp, rhs.kingBlastEp)
        && same_physical_rows(lhs.blastRing, rhs.blastRing);
}

bool set_position(Position&        position,
                  StateInfo&       state,
                  std::string_view fen,
                  bool             chess960 = false) {
    return !position.set(std::string(fen), chess960, &state);
}

bool verify_diagnostic(const Network&             network,
                       const CapturePairSnapshot& snapshot,
                       const ScalarDiagnostic&    diagnostic) {
    for (const Color perspective : {WHITE, BLACK})
    {
        const auto&         actual = diagnostic.perspectives[static_cast<std::size_t>(perspective)];
        FullRefreshEmission expectedEmission{};
        if (emit_full_refresh(snapshot, perspective, expectedEmission) != FullRefreshError::None
            || !same_full_refresh(actual.emission, expectedEmission))
            return false;

        for (std::size_t output = 0; output < AccumulatorDimensions; ++output)
            if (actual.accumulator[output]
                != expected_accumulator_cell(network, expectedEmission, output))
                return false;

        std::array<i64, PsqtBuckets> expectedPsqt{};
        for (IndexType index = 0; index < expectedEmission.hm.size; ++index)
            for (IndexType bucket = 0; bucket < PsqtBuckets; ++bucket)
                expectedPsqt[bucket] +=
                  network.hm_psqt_weights()[expectedEmission.hm.features[index].physicalIndex
                                              * PsqtBuckets
                                            + bucket];
        if (actual.psqt != expectedPsqt)
            return false;
    }

    const auto& stm = diagnostic.perspectives[static_cast<std::size_t>(snapshot.sideToMove)];
    const auto& opp = diagnostic.perspectives[static_cast<std::size_t>(~snapshot.sideToMove)];
    for (std::size_t index = 0; index < AccumulatorDimensions / 2; ++index)
    {
        const auto expected = [](i32 first, i32 second) {
            const i32 a = std::clamp(first, 0, 255);
            const i32 b = std::clamp(second, 0, 255);
            return static_cast<u8>((u32(a) * u32(b)) / 512U);
        };
        if (diagnostic.transformed[index]
              != expected(stm.accumulator[index], stm.accumulator[index + 512])
            || diagnostic.transformed[index + 512]
                 != expected(opp.accumulator[index], opp.accumulator[index + 512]))
            return false;
    }

    i32 expectedPsqtDifference = 0;
    if (psqt_perspective_difference(stm.psqt[diagnostic.networkBucket],
                                    opp.psqt[diagnostic.networkBucket], expectedPsqtDifference)
          != NumericError::None
        || diagnostic.psqtDifference != expectedPsqtDifference)
        return false;

    // The controlled wire-v1 fixture deliberately has no fc2 weights and no
    // FC0 skip biases. Each bucket therefore publishes a distinct, exact raw
    // value independent of its transformed input.
    const i64 expectedRaw    = std::numeric_limits<i32>::max() - diagnostic.networkBucket;
    i32       expectedScaled = 0;
    return diagnostic.dense.fc0[0] == std::numeric_limits<i32>::max()
        && diagnostic.dense.fc0[29] == i32(diagnostic.networkBucket + 1)
        && diagnostic.dense.fc0[30] == 0 && diagnostic.dense.fc0[31] == 0
        && diagnostic.dense.fc0Squared[0] == 127 && diagnostic.dense.fc0Clipped[0] == 127
        && diagnostic.dense.fc2[0] == expectedRaw && diagnostic.rawOutput == expectedRaw
        && scale_raw_output(expectedRaw, expectedScaled) == NumericError::None
        && diagnostic.scaledOutput == expectedScaled
        && diagnostic.psqtValue == diagnostic.psqtDifference / OutputScale
        && diagnostic.positionalValue == diagnostic.scaledOutput / OutputScale;
}

bool verify_success(const Network&    network,
                    const Position&   position,
                    ScalarDiagnostic& diagnostic) {
    return evaluate_scalar(network, position, diagnostic)
        && verify_diagnostic(network, make_capture_pair_snapshot(position), diagnostic);
}

bool verify_success(const Network&             network,
                    const CapturePairSnapshot& snapshot,
                    ScalarDiagnostic&          diagnostic) {
    return evaluate_scalar(network, snapshot, diagnostic)
        && verify_diagnostic(network, snapshot, diagnostic);
}

template<typename Integer>
void fingerprint_integer(u64& hash, Integer value) {
    using Unsigned = std::make_unsigned_t<Integer>;
    Unsigned bits  = static_cast<Unsigned>(value);
    for (std::size_t byte = 0; byte < sizeof(Integer); ++byte)
    {
        hash ^= static_cast<u8>(bits >> (byte * 8));
        hash *= 1099511628211ULL;
    }
}

template<typename Range>
void fingerprint_range(u64& hash, const Range& values) {
    for (const auto value : values)
        fingerprint_integer(hash, value);
}

template<typename Emission>
void fingerprint_emission(u64& hash, const Emission& emission) {
    fingerprint_integer(hash, emission.size);
    for (IndexType index = 0; index < emission.size; ++index)
        fingerprint_integer(hash, emission.features[index].physicalIndex);
}

u64 fingerprint(const ScalarDiagnostic& value) {
    u64 hash = 1469598103934665603ULL;
    fingerprint_integer(hash, value.sideToMove);
    fingerprint_integer(hash, value.networkBucket);
    for (const auto& perspective : value.perspectives)
    {
        fingerprint_integer(hash, perspective.perspective);
        fingerprint_emission(hash, perspective.emission.hm);
        fingerprint_emission(hash, perspective.emission.capturePairs);
        fingerprint_emission(hash, perspective.emission.kingBlastEp);
        fingerprint_emission(hash, perspective.emission.blastRing);
        fingerprint_range(hash, perspective.accumulator);
        fingerprint_range(hash, perspective.psqt);
    }
    fingerprint_range(hash, value.transformed);
    fingerprint_integer(hash, value.psqtDifference);
    fingerprint_integer(hash, value.psqtValue);
    fingerprint_range(hash, value.dense.fc0);
    fingerprint_range(hash, value.dense.fc0Squared);
    fingerprint_range(hash, value.dense.fc0Clipped);
    fingerprint_range(hash, value.dense.fc1);
    fingerprint_range(hash, value.dense.fc1Squared);
    fingerprint_range(hash, value.dense.fc1Clipped);
    fingerprint_range(hash, value.dense.fc2);
    fingerprint_integer(hash, value.rawOutput);
    fingerprint_integer(hash, value.scaledOutput);
    fingerprint_integer(hash, value.positionalValue);
    return hash;
}

struct DenseVector {
    DenseStackParameters      stack{};
    std::array<u8, Fc0Inputs> transformed{};
};

DenseVector make_adversarial_dense_vector(bool negative) {
    DenseVector vector{};
    for (std::size_t index = 0; index < vector.transformed.size(); ++index)
        vector.transformed[index] = static_cast<u8>((index * 29 + 3) % 128);

    constexpr std::array<std::size_t, 6> Fc0Boundaries{{0, 31, 32, 511, 512, 1023}};
    constexpr std::array<i8, 6>          Fc0Weights{{3, -5, 7, -11, 13, -17}};
    constexpr std::array<std::size_t, 4> Fc0Rows{{0, 5, 30, 31}};
    for (std::size_t output = 0; output < Fc0Outputs; ++output)
        vector.stack.fc0Biases[output] = (i32(output) - 16) * 97;
    vector.stack.fc0Biases[0]  = 22000;
    vector.stack.fc0Biases[30] = -14000;
    vector.stack.fc0Biases[31] = 18000;
    for (const std::size_t output : Fc0Rows)
        for (std::size_t index = 0; index < Fc0Boundaries.size(); ++index)
            vector.stack.fc0Weights[output * Fc0Inputs + Fc0Boundaries[index]] =
              static_cast<i8>((output & 1) == 0 ? Fc0Weights[index] : -Fc0Weights[index]);

    constexpr std::array<std::size_t, 4> Fc1Boundaries{{0, 31, 32, 63}};
    constexpr std::array<i8, 4>          Fc1Weights{{-9, 11, -13, 15}};
    constexpr std::array<std::size_t, 3> Fc1Rows{{0, 7, 31}};
    for (std::size_t output = 0; output < Fc1Outputs; ++output)
        vector.stack.fc1Biases[output] = (i32(output) - 12) * 53;
    vector.stack.fc1Biases[0]  = 15000;
    vector.stack.fc1Biases[31] = 9000;
    for (const std::size_t output : Fc1Rows)
        for (std::size_t index = 0; index < Fc1Boundaries.size(); ++index)
            vector.stack.fc1Weights[output * Fc1Inputs + Fc1Boundaries[index]] =
              static_cast<i8>((output & 1) == 0 ? Fc1Weights[index] : -Fc1Weights[index]);

    constexpr std::array<std::size_t, 6> Fc2Boundaries{{0, 63, 64, 95, 96, 127}};
    constexpr std::array<i8, 6>          Fc2Weights{{19, -23, 29, -31, 37, -41}};
    vector.stack.fc2Biases[0] = negative ? -90000 : 90000;
    for (std::size_t index = 0; index < Fc2Boundaries.size(); ++index)
        vector.stack.fc2Weights[Fc2Boundaries[index]] = Fc2Weights[index];
    return vector;
}

u64 fingerprint_dense(const ScalarDenseResult& value) {
    u64 hash = 1469598103934665603ULL;
    fingerprint_range(hash, value.layers.fc0);
    fingerprint_range(hash, value.layers.fc0Squared);
    fingerprint_range(hash, value.layers.fc0Clipped);
    fingerprint_range(hash, value.layers.fc1);
    fingerprint_range(hash, value.layers.fc1Squared);
    fingerprint_range(hash, value.layers.fc1Clipped);
    fingerprint_range(hash, value.layers.fc2);
    fingerprint_integer(hash, value.rawOutput);
    fingerprint_integer(hash, value.scaledOutput);
    fingerprint_integer(hash, value.positionalValue);
    return hash;
}

bool dense_result_is_clear(const ScalarDenseResult& value) {
    return all_zero(value.layers.fc0) && all_zero(value.layers.fc0Squared)
        && all_zero(value.layers.fc0Clipped) && all_zero(value.layers.fc1)
        && all_zero(value.layers.fc1Squared) && all_zero(value.layers.fc1Clipped)
        && all_zero(value.layers.fc2) && value.rawOutput == 0 && value.scaledOutput == 0
        && value.positionalValue == 0;
}

void test_adversarial_dense(u64& corpusFingerprint) {
    bool exact = true;
    for (const bool negative : {false, true})
    {
        const DenseVector  vector = make_adversarial_dense_vector(negative);
        ScalarDenseResult  result{};
        const NumericError error = propagate_dense_scalar(vector.stack, vector.transformed, result);
        exact                    = exact && error == NumericError::None
             && (negative ? result.rawOutput < 0 : result.rawOutput > 0)
             && result.scaledOutput / OutputScale == result.positionalValue;
        fingerprint_integer(corpusFingerprint, fingerprint_dense(result));
    }
    expect(exact,
           "adversarial dense vectors exercise signed FC0/FC1/FC2, skip and both scale signs");

    DenseVector overflow        = make_adversarial_dense_vector(false);
    overflow.stack.fc2Biases[0] = std::numeric_limits<i32>::max();
    overflow.stack.fc2Weights.fill(127);
    overflow.transformed.fill(127);
    ScalarDenseResult rejected{};
    rejected.rawOutput = 1;
    const NumericError error =
      propagate_dense_scalar(overflow.stack, overflow.transformed, rejected);
    expect(error == NumericError::I32NarrowingOverflow && dense_result_is_clear(rejected),
           "adversarial dense affine overflow rejects transactionally with clear output");

    DenseVector composition{};
    composition.stack.fc2Biases[0]              = std::numeric_limits<i32>::max();
    composition.stack.fc0Biases[Fc0Outputs - 2] = std::numeric_limits<i32>::max();
    rejected                                    = {};
    rejected.rawOutput                          = 1;
    const NumericError compositionError =
      propagate_dense_scalar(composition.stack, composition.transformed, rejected);
    expect(compositionError == NumericError::RawOutputOverflow && dense_result_is_clear(rejected),
           "adversarial dense composition overflow rejects transactionally with clear output");
}

void test_runtime_dense_identity(const Network& network) {
    std::array<std::array<u8, Fc0Inputs>, 5> inputs{};
    inputs[1][0]    = 1;
    inputs[1][255]  = 127;
    inputs[1][256]  = 2;
    inputs[1][511]  = 126;
    inputs[1][512]  = 3;
    inputs[1][767]  = 125;
    inputs[1][768]  = 4;
    inputs[1][1023] = 124;
    for (std::size_t index = 0; index < Fc0Inputs; ++index)
    {
        inputs[2][index] = static_cast<u8>((index * 17 + 3) % 128);
        inputs[3][index] = index % 4 == 0 ? 127 : 0;
        inputs[4][index] = index % 257 == 0 ? static_cast<u8>(index % 128) : 0;
    }

    bool exact = network.dense_runtime_ready();
    for (IndexType bucket = 0; bucket < LayerStacks; ++bucket)
        for (const auto& input : inputs)
        {
            ScalarDenseResult  scalar{};
            const NumericError scalarStatus =
              propagate_dense_scalar(network.dense_stacks()[bucket], input, scalar);
            AtomicV2::NNZInfo<AtomicV2::L1> nnz{};
            nnz.reset_from(input.data());
            const auto runtime =
              network.dense_runtime_stacks()[bucket].propagate_components(input.data(), nnz);
            const i64 raw =
              i64(runtime.fc2Output) + i64(runtime.fc0SkipAdd) - i64(runtime.fc0SkipSubtract);
            exact = exact && scalarStatus == NumericError::None
                 && runtime.fc2Output == scalar.layers.fc2[0]
                 && runtime.fc0SkipAdd == scalar.layers.fc0[Fc0Outputs - 2]
                 && runtime.fc0SkipSubtract == scalar.layers.fc0[Fc0Outputs - 1]
                 && raw == scalar.rawOutput;
        }
    expect(exact,
           "all runtime dense buckets and sparse NNZ boundary patterns equal the scalar oracle");
}

void test_snapshot_adapter() {
    Position   position;
    StateInfo  state{};
    const bool valid = set_position(position, state, "7k/8/8/8/3pP3/8/8/K7 b - e3 17 42");
    const CapturePairSnapshot snapshot =
      valid ? make_capture_pair_snapshot(position) : CapturePairSnapshot{};
    bool exact = valid && snapshot.sideToMove == BLACK && snapshot.epSquare == SQ_E3;
    for (int square = 0; square < SQUARE_NB && exact; ++square)
        exact = snapshot.board[square] == position.piece_on(Square(square));
    expect(exact, "shared Position snapshot copies board, side-to-move and EP exactly once");
}

void test_parameter_mapping(const Network& network) {
    constexpr std::array<std::pair<std::size_t, i16>, 11> Biases{{
      {0, 64},
      {1, -65},
      {7, 32767},
      {8, -32768},
      {15, -1},
      {16, 1},
      {31, 63},
      {32, -64},
      {511, 127},
      {512, -128},
      {1023, 42},
    }};
    bool                                                  exact = true;
    for (const auto [index, expected] : Biases)
    {
        const std::size_t i16Internal = WireIO::internal_index_from_canonical<i16, 16>(index);
        const std::size_t i8Internal  = WireIO::internal_index_from_canonical<i8, 8>(index);
        exact = exact && i16Internal == i8Internal && network.biases()[i16Internal] == expected;
    }
    expect(exact,
           "i16/i8 mixed tensors share one logical-to-internal output map under this policy");
}

std::array<std::string_view, LayerStacks> bucket_fens() {
    return {{
      "7k/8/8/8/8/8/P7/K7 w - - 0 1",
      "7k/8/8/8/8/8/1PPP4/K7 w - - 0 1",
      "7k/8/8/8/8/8/1PPPPPPP/K7 w - - 0 1",
      "7k/1pppp3/8/8/8/8/1PPPPPPP/K7 w - - 0 1",
      "7k/pppppppp/8/8/8/8/1PPPPPPP/K7 w - - 0 1",
      "7k/pppppppp/8/8/8/8/1PPPPPPP/KNBRQ3 w - - 0 1",
      "1nbrq2k/pppppppp/8/8/8/8/1PPPPPPP/KNBRQ3 w - - 0 1",
      "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1",
    }};
}

u64 test_success_corpus(const Network& network) {
    u64        corpusFingerprint = 1469598103934665603ULL;
    const auto buckets           = bucket_fens();
    for (IndexType bucket = 0; bucket < LayerStacks; ++bucket)
    {
        Position         position;
        StateInfo        state{};
        ScalarDiagnostic diagnostic{};
        const bool       exact = set_position(position, state, buckets[bucket])
                        && verify_success(network, position, diagnostic)
                        && diagnostic.networkBucket == bucket;
        expect(exact, "scalar full refresh selects and executes bucket " + std::to_string(bucket));
        if (exact)
            fingerprint_integer(corpusFingerprint, fingerprint(diagnostic));
    }

    struct Fixture {
        std::string_view fen;
        bool             chess960;
        std::string_view label;
    };
    constexpr std::array<Fixture, 10> Fixtures{{
      {"rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR b KQkq - 0 1", false,
       "start position with BLACK to move"},
      {"7k/3b4/8/2RPp3/8/8/8/K7 w - d6 0 1", false, "validated en-passant capture geometry"},
      {"7k/3b4/8/2R1P3/8/8/8/K7 w - d6 0 1", false,
       "malformed EP metadata omitted without rejecting normal captures"},
      {"r3k2r/8/8/3Qb3/8/8/8/R3K2R w KQkq - 0 1", false, "castling-related orthodox state"},
      {"r1k4r/8/8/3Qb3/8/8/8/R1K4R w AHah - 0 1", true, "Atomic960 castling-related state"},
      {"5brk/6P1/6N1/8/8/8/8/K7 w - - 0 1", false, "capture and promotion-ready tactical state"},
      {"6Qk/8/6N1/8/8/8/8/K7 b - - 0 1", false, "post-promotion piece state"},
      {"8/8/8/8/3Kk3/8/8/8 w - - 0 1", false, "Atomic touching-kings state"},
      {"7k/8/8/8/3K4/8/8/q7 w - - 0 1", false, "left-half king horizontal-mirror branch"},
      {"k7/8/8/8/4K3/8/8/7q w - - 0 1", false, "right-half king identity branch"},
    }};
    for (const Fixture& fixture : Fixtures)
    {
        Position         position;
        StateInfo        state{};
        ScalarDiagnostic diagnostic{};
        const bool       exact = set_position(position, state, fixture.fen, fixture.chess960)
                        && verify_success(network, position, diagnostic);
        expect(exact, fixture.label);
        if (exact)
            fingerprint_integer(corpusFingerprint, fingerprint(diagnostic));
    }

    Position         withRights;
    Position         withoutRights;
    StateInfo        rightsState{};
    StateInfo        noRightsState{};
    ScalarDiagnostic rightsDiagnostic{};
    ScalarDiagnostic noRightsDiagnostic{};
    const bool       castlingIndependent =
      set_position(withRights, rightsState, "r3k2r/8/8/3Qb3/8/8/8/R3K2R w KQkq - 0 1")
      && set_position(withoutRights, noRightsState, "r3k2r/8/8/3Qb3/8/8/8/R3K2R w - - 0 1")
      && evaluate_scalar(network, withRights, rightsDiagnostic)
      && evaluate_scalar(network, withoutRights, noRightsDiagnostic)
      && fingerprint(rightsDiagnostic) == fingerprint(noRightsDiagnostic);
    expect(castlingIndependent,
           "castling rights do not enter the frozen V3 feature or execution contract");

    Position         mirrored;
    Position         identity;
    StateInfo        mirroredState{};
    StateInfo        identityState{};
    ScalarDiagnostic mirroredDiagnostic{};
    ScalarDiagnostic identityDiagnostic{};
    const bool       mirrorBranches =
      set_position(mirrored, mirroredState, "7k/8/8/8/3K4/8/8/q7 w - - 0 1")
      && set_position(identity, identityState, "k7/8/8/8/4K3/8/8/7q w - - 0 1")
      && evaluate_scalar(network, mirrored, mirroredDiagnostic)
      && evaluate_scalar(network, identity, identityDiagnostic)
      && mirroredDiagnostic.perspectives[WHITE].emission.hm.orientation.horizontalXor == 7
      && identityDiagnostic.perspectives[WHITE].emission.hm.orientation.horizontalXor == 0;
    expect(mirrorBranches,
           "scalar refresh executes both horizontal-orientation branches explicitly");

    auto has_row = [](const auto& emission, IndexType physical) {
        for (IndexType index = 0; index < emission.size; ++index)
            if (emission.features[index].physicalIndex == physical)
                return true;
        return false;
    };

    CapturePairSnapshot hmSentinel{};
    hmSentinel.sideToMove   = WHITE;
    hmSentinel.board[SQ_H8] = W_KING;
    hmSentinel.board[SQ_H1] = B_KING;
    hmSentinel.board[SQ_A1] = W_PAWN;
    ScalarDiagnostic hmDiagnostic{};
    const bool       hmExact = verify_success(network, hmSentinel, hmDiagnostic)
                      && has_row(hmDiagnostic.perspectives[WHITE].emission.hm, 0);
    expect(hmExact, "targeted snapshot activates nonzero HM/PSQT fixture row zero");
    if (hmExact)
        fingerprint_integer(corpusFingerprint, fingerprint(hmDiagnostic));

    CapturePairSnapshot oddPsqt{};
    oddPsqt.sideToMove   = WHITE;
    oddPsqt.board[SQ_H8] = W_KING;
    oddPsqt.board[SQ_H1] = B_KING;
    oddPsqt.board[SQ_H4] = W_PAWN;
    ScalarDiagnostic oddPsqtDiagnostic{};
    const bool       oddPsqtExact = verify_success(network, oddPsqt, oddPsqtDiagnostic)
                           && has_row(oddPsqtDiagnostic.perspectives[WHITE].emission.hm, 31)
                           && oddPsqtDiagnostic.psqtDifference == -33554431
                           && oddPsqtDiagnostic.psqtValue == -2097151;
    expect(oddPsqtExact,
           "negative odd PSQT difference and inherited /16 both truncate toward zero");
    if (oddPsqtExact)
        fingerprint_integer(corpusFingerprint, fingerprint(oddPsqtDiagnostic));

    CapturePairSnapshot cpSentinel{};
    cpSentinel.sideToMove   = WHITE;
    cpSentinel.board[SQ_H1] = W_KING;
    cpSentinel.board[SQ_H8] = B_KING;
    cpSentinel.board[SQ_A2] = W_PAWN;
    cpSentinel.board[SQ_B3] = B_PAWN;
    ScalarDiagnostic cpDiagnostic{};
    const bool       cpExact =
      verify_success(network, cpSentinel, cpDiagnostic)
      && has_row(cpDiagnostic.perspectives[WHITE].emission.capturePairs, CapturePairPhysicalOffset);
    expect(cpExact, "targeted snapshot activates nonzero CapturePair fixture row zero");
    if (cpExact)
        fingerprint_integer(corpusFingerprint, fingerprint(cpDiagnostic));

    CapturePairSnapshot relationSentinel{};
    relationSentinel.sideToMove   = WHITE;
    relationSentinel.board[SQ_H8] = W_KING;
    relationSentinel.board[SQ_A1] = B_KING;
    relationSentinel.board[SQ_B3] = W_KNIGHT;
    relationSentinel.board[SQ_A2] = W_KNIGHT;
    ScalarDiagnostic relationDiagnostic{};
    const bool       relationExact = verify_success(network, relationSentinel, relationDiagnostic)
                            && has_row(relationDiagnostic.perspectives[WHITE].emission.kingBlastEp,
                                       KingBlastEpPhysicalOffset)
                            && has_row(relationDiagnostic.perspectives[WHITE].emission.blastRing,
                                       BlastRingPhysicalOffset);
    expect(relationExact,
           "targeted snapshot activates nonzero KingBlastEP and BlastRing fixture rows zero");
    if (relationExact)
        fingerprint_integer(corpusFingerprint, fingerprint(relationDiagnostic));
    return corpusFingerprint;
}

void test_rejections(const Network& network) {
    CapturePairSnapshot missing{};
    missing.sideToMove   = WHITE;
    missing.board[SQ_A1] = W_KING;

    ScalarDiagnostic output{};
    output.sideToMove    = BLACK;
    output.networkBucket = 7;
    output.rawOutput     = 99;
    output.transformed.fill(7);
    ScalarStatus status = evaluate_scalar(network, missing, output);
    expect(!status && status.code == ScalarError::FeatureOracleError
             && status.featureError == FullRefreshError::MissingBlackKing
             && diagnostic_is_clear(output),
           "missing king fails closed and clears every published output");

    CapturePairSnapshot multiple{};
    multiple.sideToMove   = BLACK;
    multiple.board[SQ_A1] = W_KING;
    multiple.board[SQ_B1] = W_KING;
    multiple.board[SQ_H8] = B_KING;
    output.rawOutput      = 77;
    status                = evaluate_scalar(network, multiple, output);
    expect(!status && status.code == ScalarError::FeatureOracleError
             && status.featureError == FullRefreshError::MultipleWhiteKings
             && diagnostic_is_clear(output),
           "multiple king snapshot fails closed with exact feature error");

    CapturePairSnapshot invalidSide{};
    invalidSide.sideToMove   = static_cast<Color>(COLOR_NB);
    invalidSide.board[SQ_A1] = W_KING;
    invalidSide.board[SQ_H8] = B_KING;
    output.scaledOutput      = 42;
    status                   = evaluate_scalar(network, invalidSide, output);
    expect(!status && status.code == ScalarError::FeatureOracleError
             && status.featureError == FullRefreshError::InvalidSideToMove
             && diagnostic_is_clear(output),
           "invalid side-to-move fails before transform indexing and clears outputs");
}

void test_composition_rejections(const Network& network) {
    CapturePairSnapshot snapshot{};
    snapshot.sideToMove   = WHITE;
    snapshot.board[SQ_A1] = W_KING;
    snapshot.board[SQ_H8] = B_KING;
    snapshot.board[SQ_C3] = W_KNIGHT;
    snapshot.board[SQ_F6] = B_KNIGHT;

    std::array<FullRefreshEmission, COLOR_NB> emissions{};
    std::array<ScalarHmPerspective, COLOR_NB> hmStates{};
    bool                                      prepared = true;
    for (const Color perspective : {WHITE, BLACK})
    {
        const std::size_t index = static_cast<std::size_t>(perspective);
        prepared =
          prepared
          && emit_full_refresh(snapshot, perspective, emissions[index]) == FullRefreshError::None
          && accumulate_hm_scalar(network, emissions[index].hm, hmStates[index])
               == ScalarError::None;
    }
    expect(prepared, "composition rejection fixture prepares authenticated emissions");
    if (!prepared)
        return;

    const auto rejects_transactionally = [&](const auto& forged, std::string_view label) {
        ScalarDiagnostic output{};
        output.sideToMove    = BLACK;
        output.networkBucket = 7;
        output.rawOutput     = 91;
        output.transformed.fill(13);
        output.perspectives[BLACK].accumulator.fill(9);
        const ScalarStatus status =
          compose_scalar_diagnostic(network, snapshot, forged, hmStates, output);
        expect(!status && status.code == ScalarError::InvalidFeatureIndex
                 && diagnostic_is_clear(output),
               label);
    };

    {
        auto forged           = emissions;
        forged[WHITE].hm.size = HmMaximumActiveDimensions + 1;
        rejects_transactionally(forged,
                                "composition rejects oversized HM emission transactionally");
    }
    {
        auto forged                     = emissions;
        forged[WHITE].capturePairs.size = CapturePairMaximumActiveFeatures + 1;
        rejects_transactionally(
          forged, "composition rejects oversized CapturePair emission transactionally");
    }
    {
        auto forged                    = emissions;
        forged[WHITE].kingBlastEp.size = KingBlastEpMaximumActiveFeatures + 1;
        rejects_transactionally(
          forged, "composition rejects oversized KingBlastEP emission transactionally");
    }
    {
        auto forged                  = emissions;
        forged[WHITE].blastRing.size = BlastRingMaximumActiveFeatures + 1;
        rejects_transactionally(forged,
                                "composition rejects oversized BlastRing emission transactionally");
    }

    const JointOrientation crossed = emissions[BLACK].hm.orientation;
    {
        auto forged                            = emissions;
        forged[WHITE].capturePairs.orientation = crossed;
        rejects_transactionally(
          forged, "composition rejects crossed CapturePair orientation transactionally");
    }
    {
        auto forged                           = emissions;
        forged[WHITE].kingBlastEp.orientation = crossed;
        rejects_transactionally(
          forged, "composition rejects crossed KingBlastEP orientation transactionally");
    }
    {
        auto forged                         = emissions;
        forged[WHITE].blastRing.orientation = crossed;
        rejects_transactionally(
          forged, "composition rejects crossed BlastRing orientation transactionally");
    }
}

CapturePairSnapshot concurrency_snapshot() {
    CapturePairSnapshot snapshot{};
    snapshot.sideToMove   = WHITE;
    snapshot.epSquare     = SQ_D6;
    snapshot.board[SQ_A1] = W_KING;
    snapshot.board[SQ_H8] = B_KING;
    snapshot.board[SQ_E5] = W_PAWN;
    snapshot.board[SQ_D5] = B_PAWN;
    snapshot.board[SQ_C5] = W_ROOK;
    snapshot.board[SQ_D7] = B_BISHOP;
    return snapshot;
}

void test_concurrent_reads(const Network& network) {
    const CapturePairSnapshot snapshot = concurrency_snapshot();
    ScalarDiagnostic          baseline{};
    const bool                baselineOk          = verify_success(network, snapshot, baseline);
    const u64                 baselineFingerprint = baselineOk ? fingerprint(baseline) : 0;

    bool exact = baselineOk;
    for (const unsigned threadCount : {1U, 2U, 4U, 8U})
    {
        std::atomic<bool>        matches{true};
        std::vector<std::thread> threads;
        for (unsigned threadIndex = 0; threadIndex < threadCount; ++threadIndex)
            threads.emplace_back([&]() {
                for (unsigned iteration = 0; iteration < 32 && matches.load(); ++iteration)
                {
                    ScalarDiagnostic actual{};
                    if (!evaluate_scalar(network, snapshot, actual)
                        || fingerprint(actual) != baselineFingerprint)
                        matches.store(false);
                }
            });
        for (std::thread& thread : threads)
            thread.join();
        exact = exact && matches.load();
    }
    expect(exact, "one immutable V3 Network is bit-exact with 1/2/4/8 concurrent readers");
}

bool files_equal(const std::filesystem::path& first, const std::filesystem::path& second) {
    std::error_code error;
    if (std::filesystem::file_size(first, error) != std::filesystem::file_size(second, error)
        || error)
        return false;

    std::ifstream              lhs(first, std::ios::binary);
    std::ifstream              rhs(second, std::ios::binary);
    std::array<char, 1U << 16> lhsBytes{};
    std::array<char, 1U << 16> rhsBytes{};
    while (lhs && rhs)
    {
        lhs.read(lhsBytes.data(), lhsBytes.size());
        rhs.read(rhsBytes.data(), rhsBytes.size());
        const auto lhsCount = lhs.gcount();
        const auto rhsCount = rhs.gcount();
        if (lhsCount != rhsCount
            || !std::equal(lhsBytes.begin(), lhsBytes.begin() + lhsCount, rhsBytes.begin()))
            return false;
    }
    return lhs.eof() && rhs.eof() && !lhs.bad() && !rhs.bad();
}

void test_evaluate_then_save(const Network& network, const std::filesystem::path& source) {
    const CapturePairSnapshot snapshot = concurrency_snapshot();
    ScalarDiagnostic          before{};
    ScalarDiagnostic          after{};
    const bool                evaluated = bool(evaluate_scalar(network, snapshot, before));

    const auto                  nonce = std::chrono::steady_clock::now().time_since_epoch().count();
    const std::filesystem::path output =
      std::filesystem::temp_directory_path()
      / ("atomic-v3-scalar-after-evaluate-" + std::to_string(nonce) + ".nnue");
    bool saved = false;
    {
        std::ofstream stream(output, std::ios::binary | std::ios::trunc);
        saved = stream && network.save(stream) && bool(stream);
    }
    const bool exact = evaluated && saved && files_equal(source, output)
                    && evaluate_scalar(network, snapshot, after)
                    && fingerprint(before) == fingerprint(after);
    std::error_code ignored;
    std::filesystem::remove(output, ignored);
    expect(exact,
           "evaluate then save is byte-exact and does not mutate live V3 parameters or output");
}

template<typename Range>
void print_csv(std::string_view name, const Range& values) {
    std::cout << name << '=';
    bool first = true;
    for (const auto value : values)
    {
        if (!first)
            std::cout << ',';
        first = false;
        std::cout << static_cast<long long>(value);
    }
    std::cout << '\n';
}

template<typename Emission>
void print_rows(std::string_view name, const Emission& emission) {
    std::cout << name << '=';
    for (IndexType index = 0; index < emission.size; ++index)
    {
        if (index != 0)
            std::cout << ',';
        std::cout << emission.features[index].physicalIndex;
    }
    std::cout << '\n';
}

Piece piece_from_fen(char token) {
    switch (token)
    {
    case 'P' :
        return W_PAWN;
    case 'N' :
        return W_KNIGHT;
    case 'B' :
        return W_BISHOP;
    case 'R' :
        return W_ROOK;
    case 'Q' :
        return W_QUEEN;
    case 'K' :
        return W_KING;
    case 'p' :
        return B_PAWN;
    case 'n' :
        return B_KNIGHT;
    case 'b' :
        return B_BISHOP;
    case 'r' :
        return B_ROOK;
    case 'q' :
        return B_QUEEN;
    case 'k' :
        return B_KING;
    default :
        return NO_PIECE;
    }
}

bool parse_board_placement(std::string_view placement, HmBoard& board) {
    board    = {};
    int rank = 7;
    int file = 0;
    for (const char token : placement)
    {
        if (token == '/')
        {
            if (file != 8 || rank == 0)
                return false;
            --rank;
            file = 0;
            continue;
        }
        if (token >= '1' && token <= '8')
        {
            file += token - '0';
            if (file > 8)
                return false;
            continue;
        }
        const Piece piece = piece_from_fen(token);
        if (piece == NO_PIECE || file >= 8)
            return false;
        board[rank * 8 + file] = piece;
        ++file;
    }
    return rank == 0 && file == 8;
}

bool parse_color(std::string_view text, Color& color) {
    if (text == "white" || text == "w")
    {
        color = WHITE;
        return true;
    }
    if (text == "black" || text == "b")
    {
        color = BLACK;
        return true;
    }
    return false;
}

bool parse_square(std::string_view text, Square& square) {
    if (text == "-")
    {
        square = SQ_NONE;
        return true;
    }
    if (text.size() != 2 || text[0] < 'a' || text[0] > 'h' || text[1] < '1' || text[1] > '8')
        return false;
    square = make_square(File(text[0] - 'a'), Rank(text[1] - '1'));
    return true;
}

int dump_diagnostic(const ScalarStatus& status, const ScalarDiagnostic& diagnostic) {
    if (!status)
    {
        std::cerr << "error=" << scalar_error_message(status.code)
                  << " feature_error=" << full_refresh_error_message(status.featureError)
                  << " numeric_error=" << numeric_error_message(status.numericError) << '\n';
        return EXIT_FAILURE;
    }

    std::cout << "side_to_move=" << int(diagnostic.sideToMove) << '\n'
              << "network_bucket=" << diagnostic.networkBucket << '\n';
    for (const Color perspective : {WHITE, BLACK})
    {
        const auto&       value  = diagnostic.perspectives[static_cast<std::size_t>(perspective)];
        const std::string prefix = perspective == WHITE ? "white." : "black.";
        print_rows(prefix + "hm", value.emission.hm);
        print_rows(prefix + "capture_pair", value.emission.capturePairs);
        print_rows(prefix + "king_blast_ep", value.emission.kingBlastEp);
        print_rows(prefix + "blast_ring", value.emission.blastRing);
        print_csv(prefix + "accumulator", value.accumulator);
        print_csv(prefix + "psqt", value.psqt);
    }
    print_csv("transformed", diagnostic.transformed);
    print_csv("fc0", diagnostic.dense.fc0);
    print_csv("fc0_squared", diagnostic.dense.fc0Squared);
    print_csv("fc0_clipped", diagnostic.dense.fc0Clipped);
    print_csv("fc1", diagnostic.dense.fc1);
    print_csv("fc1_squared", diagnostic.dense.fc1Squared);
    print_csv("fc1_clipped", diagnostic.dense.fc1Clipped);
    print_csv("fc2", diagnostic.dense.fc2);
    std::cout << "psqt_difference=" << diagnostic.psqtDifference << '\n'
              << "psqt_value=" << diagnostic.psqtValue << '\n'
              << "raw_output=" << diagnostic.rawOutput << '\n'
              << "scaled_output=" << diagnostic.scaledOutput << '\n'
              << "positional_value=" << diagnostic.positionalValue << '\n'
              << "fingerprint=0x" << std::hex << std::uppercase << fingerprint(diagnostic)
              << std::dec << '\n';
    return EXIT_SUCCESS;
}

int dump_dense_vector(std::string_view name) {
    if (name != "positive" && name != "negative")
    {
        std::cerr << "error=unknown dense vector\n";
        return EXIT_FAILURE;
    }
    const DenseVector  vector = make_adversarial_dense_vector(name == "negative");
    ScalarDenseResult  result{};
    const NumericError error = propagate_dense_scalar(vector.stack, vector.transformed, result);
    if (error != NumericError::None)
    {
        std::cerr << "error=" << numeric_error_message(error) << '\n';
        return EXIT_FAILURE;
    }
    print_csv("dense_input", vector.transformed);
    print_csv("fc0", result.layers.fc0);
    print_csv("fc0_squared", result.layers.fc0Squared);
    print_csv("fc0_clipped", result.layers.fc0Clipped);
    print_csv("fc1", result.layers.fc1);
    print_csv("fc1_squared", result.layers.fc1Squared);
    print_csv("fc1_clipped", result.layers.fc1Clipped);
    print_csv("fc2", result.layers.fc2);
    std::cout << "raw_output=" << result.rawOutput << '\n'
              << "scaled_output=" << result.scaledOutput << '\n'
              << "positional_value=" << result.positionalValue << '\n'
              << "dense_fingerprint=0x" << std::hex << std::uppercase << fingerprint_dense(result)
              << std::dec << '\n';
    return EXIT_SUCCESS;
}

int dump_fen(const Network& network, std::string_view fen, bool chess960) {
    Position  position;
    StateInfo state{};
    if (!set_position(position, state, fen, chess960))
    {
        std::cerr << "error=invalid FEN\n";
        return EXIT_FAILURE;
    }
    ScalarDiagnostic   diagnostic{};
    const ScalarStatus status = evaluate_scalar(network, position, diagnostic);
    return dump_diagnostic(status, diagnostic);
}

int dump_snapshot(const Network&   network,
                  std::string_view sideToMove,
                  std::string_view epSquare,
                  std::string_view placement) {
    CapturePairSnapshot snapshot{};
    if (!parse_color(sideToMove, snapshot.sideToMove) || !parse_square(epSquare, snapshot.epSquare)
        || !parse_board_placement(placement, snapshot.board))
    {
        std::cerr << "error=invalid snapshot\n";
        return EXIT_FAILURE;
    }
    ScalarDiagnostic   diagnostic{};
    const ScalarStatus status = evaluate_scalar(network, snapshot, diagnostic);
    return dump_diagnostic(status, diagnostic);
}

int dump_batch(const Network& network) {
    std::string line;
    unsigned    caseIndex = 0;
    while (std::getline(std::cin, line))
    {
        if (line.empty())
            continue;
        std::istringstream input(line);
        std::string        sideToMove;
        std::string        epSquare;
        std::string        placement;
        std::string        trailing;
        if (!(input >> sideToMove >> epSquare >> placement) || (input >> trailing))
        {
            std::cerr << "error=invalid batch record " << caseIndex << '\n';
            return EXIT_FAILURE;
        }
        std::cout << "case=" << caseIndex << '\n';
        if (dump_snapshot(network, sideToMove, epSquare, placement) != EXIT_SUCCESS)
            return EXIT_FAILURE;
        std::cout << "end_case=" << caseIndex << '\n';
        ++caseIndex;
    }
    if (!std::cin.eof() || std::cin.bad())
    {
        std::cerr << "error=batch input I/O failure\n";
        return EXIT_FAILURE;
    }
    std::cout << "batch_cases=" << caseIndex << '\n';
    return EXIT_SUCCESS;
}

}  // namespace
}  // namespace Stockfish::Eval::NNUE::AtomicV3

int main(int argc, char* argv[]) {
    using namespace Stockfish;
    using namespace Stockfish::Eval::NNUE::AtomicV3;

    Bitboards::init();
    Attacks::init();
    Position::init();

    if (argc != 2 && argc != 3 && argc != 4 && argc != 6)
    {
        std::cerr << "usage: atomic-v3-scalar-tests NET [--batch | --dense-vector NAME | "
                     "--fen FEN | --chess960-fen FEN | --snapshot STM EP PLACEMENT]\n";
        return EXIT_FAILURE;
    }

    LoadResult loaded = load_candidate(std::filesystem::path(argv[1]));
    if (!loaded)
    {
        std::cerr << "error=" << loaded.error << '\n';
        return EXIT_FAILURE;
    }

    if (argc == 3)
    {
        if (std::string_view(argv[2]) != "--batch")
        {
            std::cerr << "usage: atomic-v3-scalar-tests NET --batch\n";
            return EXIT_FAILURE;
        }
        return dump_batch(*loaded.network);
    }

    if (argc == 4)
    {
        const std::string_view mode = argv[2];
        if (mode == "--dense-vector")
            return dump_dense_vector(argv[3]);
        if (mode != "--fen" && mode != "--chess960-fen")
        {
            std::cerr << "usage: atomic-v3-scalar-tests NET [--batch | --dense-vector NAME | "
                         "--fen FEN | --chess960-fen FEN | --snapshot STM EP PLACEMENT]\n";
            return EXIT_FAILURE;
        }
        return dump_fen(*loaded.network, argv[3], mode == "--chess960-fen");
    }
    if (argc == 6)
    {
        if (std::string_view(argv[2]) != "--snapshot")
        {
            std::cerr << "usage: atomic-v3-scalar-tests NET --snapshot STM EP PLACEMENT\n";
            return EXIT_FAILURE;
        }
        return dump_snapshot(*loaded.network, argv[3], argv[4], argv[5]);
    }

    test_snapshot_adapter();
    test_parameter_mapping(*loaded.network);
    u64 corpusFingerprint = test_success_corpus(*loaded.network);
    test_adversarial_dense(corpusFingerprint);
    test_runtime_dense_identity(*loaded.network);
    test_rejections(*loaded.network);
    test_composition_rejections(*loaded.network);
    test_concurrent_reads(*loaded.network);
    test_evaluate_then_save(*loaded.network, loaded.resolvedPath);

    // Frozen only after the final corpus included every semantic, concurrency,
    // sentinel-row and orientation case. All forced parameter layouts must
    // publish this same complete canonical diagnostic identity.
    constexpr u64 ExpectedCorpusFingerprint = 0x46F68EAB20FF9D50ULL;
    std::cout << "corpus_fingerprint=0x" << std::hex << std::uppercase << corpusFingerprint
              << std::dec << '\n';
    expect(corpusFingerprint == ExpectedCorpusFingerprint,
           "complete scalar diagnostic corpus has the frozen cross-policy fingerprint");

    if (failures != 0)
    {
        std::cerr << "Atomic V3 scalar tests failed: " << failures << '\n';
        return EXIT_FAILURE;
    }
    std::cout << "Atomic V3 scalar tests passed\n";
    return EXIT_SUCCESS;
}
