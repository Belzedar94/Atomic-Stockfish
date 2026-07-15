/*
  Atomic-Stockfish, a UCI chess playing engine derived from Stockfish
  Copyright (C) 2004-2026 The Stockfish developers (see AUTHORS file)

  Atomic-Stockfish is free software: you can redistribute it and/or modify
  it under the terms of the GNU General Public License as published by
  the Free Software Foundation, either version 3 of the License, or
  (at your option) any later version.
*/

#include "scalar_backend.h"

#include <algorithm>
#include <cstddef>
#include <limits>
#include <utility>

#include "../../position.h"
#include "wire_io.h"

namespace Stockfish::Eval::NNUE::AtomicV3 {
namespace {

using InternalAccumulator = std::array<i64, AccumulatorDimensions>;

constexpr bool mixed_parameter_output_mappings_are_identical() noexcept {
    for (std::size_t output = 0; output < AccumulatorDimensions; ++output)
        if (WireIO::internal_index_from_canonical<i16, 16>(output)
            != WireIO::internal_index_from_canonical<i8, 8>(output))
            return false;
    return true;
}

static_assert(mixed_parameter_output_mappings_are_identical());
static_assert(AccumulatorDimensions % (8 * 8) == 0);

constexpr std::size_t color_index(Color color) noexcept { return static_cast<std::size_t>(color); }

constexpr bool same_orientation(const JointOrientation& lhs, const JointOrientation& rhs) noexcept {
    return lhs.perspective == rhs.perspective && lhs.ownKing == rhs.ownKing
        && lhs.orientedOwnKing == rhs.orientedOwnKing && lhs.verticalXor == rhs.verticalXor
        && lhs.horizontalXor == rhs.horizontalXor && lhs.kingBucket == rhs.kingBucket;
}

constexpr bool valid_composition_emission(const FullRefreshEmission& emission,
                                          Color                      perspective) noexcept {
    return emission.hm.size <= HmMaximumActiveDimensions
        && emission.capturePairs.size <= CapturePairMaximumActiveFeatures
        && emission.kingBlastEp.size <= KingBlastEpMaximumActiveFeatures
        && emission.blastRing.size <= BlastRingMaximumActiveFeatures
        && is_canonical_joint_orientation(emission.hm.orientation)
        && emission.hm.orientation.perspective == perspective
        && same_orientation(emission.hm.orientation, emission.capturePairs.orientation)
        && same_orientation(emission.hm.orientation, emission.kingBlastEp.orientation)
        && same_orientation(emission.hm.orientation, emission.blastRing.orientation);
}

ScalarStatus fail(ScalarDiagnostic& result,
                  ScalarError       error,
                  FullRefreshError  featureError = FullRefreshError::None,
                  NumericError      numericError = NumericError::None) {
    result = {};
    return {error, featureError, numericError};
}

template<typename WeightType>
bool add_internal_row(InternalAccumulator& accumulator,
                      const WeightType*    weights,
                      std::size_t          row,
                      std::size_t          rowCount) noexcept {
    if (!weights || row >= rowCount)
        return false;

    const WeightType* const source = weights + row * AccumulatorDimensions;
    for (std::size_t output = 0; output < AccumulatorDimensions; ++output)
        accumulator[output] += source[output];
    return true;
}

template<typename WeightType, std::size_t BlockBytes>
bool add_canonical_row(InternalAccumulator& accumulator,
                       const WeightType*    weights,
                       std::size_t          row,
                       std::size_t          rowCount) noexcept {
    if (!weights || row >= rowCount)
        return false;

    const WeightType* const source = weights + row * AccumulatorDimensions;
    for (std::size_t canonical = 0; canonical < AccumulatorDimensions; ++canonical)
        accumulator[canonical] +=
          source[WireIO::internal_index_from_canonical<WeightType, BlockBytes>(canonical)];
    return true;
}

ScalarError add_relation_rows(const Network&             network,
                              const FullRefreshEmission& emission,
                              InternalAccumulator&       accumulator) noexcept {
    for (IndexType index = 0; index < emission.capturePairs.size; ++index)
    {
        const std::size_t physical = emission.capturePairs.features[index].physicalIndex;
        if (physical < CapturePairPhysicalOffset
            || !add_canonical_row<i8, 8>(accumulator, network.capture_pair_weights(),
                                         physical - CapturePairPhysicalOffset,
                                         CapturePairPhysicalDimensions))
            return ScalarError::InvalidFeatureIndex;
    }

    for (IndexType index = 0; index < emission.kingBlastEp.size; ++index)
    {
        const std::size_t physical = emission.kingBlastEp.features[index].physicalIndex;
        if (physical < KingBlastEpPhysicalOffset
            || !add_canonical_row<i16, 16>(accumulator, network.king_blast_ep_weights(),
                                           physical - KingBlastEpPhysicalOffset,
                                           KingBlastEpPhysicalDimensions))
            return ScalarError::InvalidFeatureIndex;
    }

    for (IndexType index = 0; index < emission.blastRing.size; ++index)
    {
        const std::size_t physical = emission.blastRing.features[index].physicalIndex;
        if (physical < BlastRingPhysicalOffset
            || !add_canonical_row<i8, 8>(accumulator, network.blast_ring_weights(),
                                         physical - BlastRingPhysicalOffset,
                                         BlastRingPhysicalDimensions))
            return ScalarError::InvalidFeatureIndex;
    }

    return ScalarError::None;
}

u8 transform_value(i32 first, i32 second) noexcept {
    const i32 clippedFirst  = std::clamp(first, 0, 255);
    const i32 clippedSecond = std::clamp(second, 0, 255);
    return static_cast<u8>((static_cast<u32>(clippedFirst) * static_cast<u32>(clippedSecond))
                           / 512U);
}

void transform_perspective(const ScalarPerspectiveDiagnostic& perspective,
                           u8*                                destination) noexcept {
    for (std::size_t output = 0; output < AccumulatorDimensions / 2; ++output)
        destination[output] =
          transform_value(perspective.accumulator[output],
                          perspective.accumulator[output + AccumulatorDimensions / 2]);
}

template<std::size_t Inputs, std::size_t Outputs>
NumericError affine(const std::array<u8, Inputs>& input,
                    const i32*                    biases,
                    const i8*                     weights,
                    std::array<i32, Outputs>&     output) noexcept {
    if (!biases || !weights)
        return NumericError::NullData;

    for (std::size_t out = 0; out < Outputs; ++out)
    {
        i64 sum = biases[out];
        for (std::size_t in = 0; in < Inputs; ++in)
            sum += i64(weights[out * Inputs + in]) * input[in];
        const NumericError error = checked_narrow_i32(sum, output[out]);
        if (error != NumericError::None)
        {
            output = {};
            return error;
        }
    }
    return NumericError::None;
}

u8 squared_clipped_relu(i32 input, unsigned shift) noexcept {
    const i64 wide = input;
    return static_cast<u8>(std::min<i64>(127, (wide * wide) >> shift));
}

u8 clipped_relu(i32 input, unsigned shift) noexcept {
    if (input <= 0)
        return 0;
    return static_cast<u8>(std::min<i32>(127, input >> shift));
}

NumericError propagate_dense_layers(const DenseStackParameters&      stack,
                                    const std::array<u8, Fc0Inputs>& transformed,
                                    ScalarDenseDiagnostic&           result) noexcept {
    result = {};
    NumericError error =
      affine(transformed, stack.fc0Biases.data(), stack.fc0Weights.data(), result.fc0);
    if (error != NumericError::None)
        return error;

    std::array<u8, Fc1Inputs> fc1Input{};
    for (std::size_t index = 0; index < Fc0Outputs; ++index)
    {
        // SFNNv15: FC0 squared path uses WeightScaleBits + 1 = 7, hence
        // 2 * 7 + 7 = 21. The ordinary clipped path shifts by seven.
        result.fc0Squared[index]     = squared_clipped_relu(result.fc0[index], 21);
        result.fc0Clipped[index]     = clipped_relu(result.fc0[index], 7);
        fc1Input[index]              = result.fc0Squared[index];
        fc1Input[Fc0Outputs + index] = result.fc0Clipped[index];
    }

    error = affine(fc1Input, stack.fc1Biases.data(), stack.fc1Weights.data(), result.fc1);
    if (error != NumericError::None)
        return error;

    std::array<u8, Fc2Inputs> fc2Input{};
    std::copy(fc1Input.begin(), fc1Input.end(), fc2Input.begin());
    for (std::size_t index = 0; index < Fc1Outputs; ++index)
    {
        // FC1 uses WeightScaleBits = 6: 2 * 6 + 7 = 19.
        result.fc1Squared[index]                 = squared_clipped_relu(result.fc1[index], 19);
        result.fc1Clipped[index]                 = clipped_relu(result.fc1[index], 6);
        fc2Input[Fc1Inputs + index]              = result.fc1Squared[index];
        fc2Input[Fc1Inputs + Fc1Outputs + index] = result.fc1Clipped[index];
    }

    error = affine(fc2Input, stack.fc2Biases.data(), stack.fc2Weights.data(), result.fc2);
    if (error != NumericError::None)
        result = {};
    return error;
}

ScalarStatus evaluate_impl(const Network&             network,
                           const CapturePairSnapshot& snapshot,
                           ScalarDiagnostic&          result) {
    result = {};
    std::array<FullRefreshEmission, COLOR_NB> emissions{};
    std::array<ScalarHmPerspective, COLOR_NB> hmStates{};

    for (const Color perspective : {WHITE, BLACK})
    {
        const std::size_t index        = color_index(perspective);
        const auto        featureError = emit_full_refresh(snapshot, perspective, emissions[index]);
        if (featureError != FullRefreshError::None)
            return fail(result, ScalarError::FeatureOracleError, featureError);

        const ScalarError hmError =
          accumulate_hm_scalar(network, emissions[index].hm, hmStates[index]);
        if (hmError != ScalarError::None)
            return fail(result, hmError);
    }

    return compose_scalar_diagnostic(network, snapshot, emissions, hmStates, result);
}

}  // namespace

ScalarError accumulate_hm_scalar(const Network&       network,
                                 const HmEmission&    emission,
                                 ScalarHmPerspective& result) noexcept {
    result = {};
    ScalarHmPerspective candidate{};

    if (!network.biases() || !network.hm_weights() || !network.hm_psqt_weights()
        || emission.size > HmMaximumActiveDimensions
        || !is_canonical_joint_orientation(emission.orientation)
        || emission.networkBucket >= LayerStacks)
        return ScalarError::InvalidFeatureIndex;

    InternalAccumulator internal{};
    for (std::size_t output = 0; output < internal.size(); ++output)
        internal[output] = network.biases()[output];

    for (IndexType index = 0; index < emission.size; ++index)
    {
        const std::size_t row = emission.features[index].physicalIndex;
        if (!add_internal_row(internal, network.hm_weights(), row, HmPhysicalDimensions))
            return ScalarError::InvalidFeatureIndex;

        for (IndexType bucket = 0; bucket < PsqtBuckets; ++bucket)
            candidate.psqt[bucket] += network.hm_psqt_weights()[row * PsqtBuckets + bucket];
    }

    for (std::size_t canonical = 0; canonical < AccumulatorDimensions; ++canonical)
    {
        const std::size_t internalIndex = WireIO::internal_index_from_canonical<i16, 16>(canonical);
        if (!feature_transformer_accumulator_in_range(internal[internalIndex]))
            return ScalarError::FeatureAccumulatorOutOfRange;
        candidate.accumulator[canonical] = static_cast<i32>(internal[internalIndex]);
    }

    for (const HmPsqtAccumulatorType value : candidate.psqt)
        if (value < std::numeric_limits<i32>::min() || value > std::numeric_limits<i32>::max())
            return ScalarError::PsqtAccumulatorOutOfRange;

    result = candidate;
    return ScalarError::None;
}

ScalarStatus compose_scalar_diagnostic(const Network&                                   network,
                                       const CapturePairSnapshot&                       snapshot,
                                       const std::array<FullRefreshEmission, COLOR_NB>& emissions,
                                       const std::array<ScalarHmPerspective, COLOR_NB>& hmStates,
                                       ScalarDiagnostic&                                result) {
    result = {};
    if (snapshot.sideToMove != WHITE && snapshot.sideToMove != BLACK)
        return fail(result, ScalarError::FeatureOracleError, FullRefreshError::InvalidSideToMove);

    ScalarDiagnostic candidate{};
    candidate.sideToMove = snapshot.sideToMove;

    for (const Color perspective : {WHITE, BLACK})
    {
        const std::size_t index       = color_index(perspective);
        const auto&       emission    = emissions[index];
        const auto&       hm          = hmStates[index];
        auto&             destination = candidate.perspectives[index];

        if (!valid_composition_emission(emission, perspective)
            || emission.hm.networkBucket >= LayerStacks)
            return fail(result, ScalarError::InvalidFeatureIndex);

        destination.perspective = perspective;
        destination.emission    = emission;
        destination.psqt        = hm.psqt;

        InternalAccumulator combined{};
        for (std::size_t output = 0; output < AccumulatorDimensions; ++output)
            combined[output] = hm.accumulator[output];

        const ScalarError relationError = add_relation_rows(network, emission, combined);
        if (relationError != ScalarError::None)
            return fail(result, relationError);

        for (std::size_t output = 0; output < AccumulatorDimensions; ++output)
        {
            if (!feature_transformer_accumulator_in_range(combined[output]))
                return fail(result, ScalarError::FeatureAccumulatorOutOfRange);
            destination.accumulator[output] = static_cast<i32>(combined[output]);
        }

        for (const HmPsqtAccumulatorType value : destination.psqt)
            if (value < std::numeric_limits<i32>::min() || value > std::numeric_limits<i32>::max())
                return fail(result, ScalarError::PsqtAccumulatorOutOfRange);
    }

    const auto& white = candidate.perspectives[color_index(WHITE)];
    const auto& black = candidate.perspectives[color_index(BLACK)];
    if (white.emission.hm.networkBucket != black.emission.hm.networkBucket)
        return fail(result, ScalarError::InconsistentPerspectiveBucket);
    candidate.networkBucket = white.emission.hm.networkBucket;

    const auto& stm = candidate.perspectives[color_index(snapshot.sideToMove)];
    const auto& opp = candidate.perspectives[color_index(~snapshot.sideToMove)];
    transform_perspective(stm, candidate.transformed.data());
    transform_perspective(opp, candidate.transformed.data() + AccumulatorDimensions / 2);

    NumericError numeric =
      psqt_perspective_difference(stm.psqt[candidate.networkBucket],
                                  opp.psqt[candidate.networkBucket], candidate.psqtDifference);
    if (numeric != NumericError::None)
        return fail(result, ScalarError::NumericContractError, FullRefreshError::None, numeric);
    candidate.psqtValue = candidate.psqtDifference / OutputScale;

    ScalarDenseResult dense{};
    numeric = propagate_dense_scalar(network.dense_stacks()[candidate.networkBucket],
                                     candidate.transformed, dense);
    if (numeric != NumericError::None)
        return fail(result, ScalarError::NumericContractError, FullRefreshError::None, numeric);
    candidate.dense           = dense.layers;
    candidate.rawOutput       = dense.rawOutput;
    candidate.scaledOutput    = dense.scaledOutput;
    candidate.positionalValue = dense.positionalValue;

    result = std::move(candidate);
    return {};
}

NumericError propagate_dense_scalar(const DenseStackParameters&      stack,
                                    const std::array<u8, Fc0Inputs>& transformed,
                                    ScalarDenseResult&               result) noexcept {
    result = {};
    ScalarDenseResult candidate{};
    NumericError      error = propagate_dense_layers(stack, transformed, candidate.layers);
    if (error != NumericError::None)
        return error;

    candidate.rawOutput = i64(candidate.layers.fc2[0]) + candidate.layers.fc0[Fc0Outputs - 2]
                        - candidate.layers.fc0[Fc0Outputs - 1];
    if (candidate.rawOutput < RawOutputMinimum || candidate.rawOutput > RawOutputMaximum)
        return NumericError::RawOutputOverflow;
    error = scale_raw_output(candidate.rawOutput, candidate.scaledOutput);
    if (error != NumericError::None)
        return error;
    candidate.positionalValue = candidate.scaledOutput / OutputScale;
    result                    = candidate;
    return NumericError::None;
}

ScalarStatus evaluate_scalar(const Network&             network,
                             const CapturePairSnapshot& snapshot,
                             ScalarDiagnostic&          result) {
    return evaluate_impl(network, snapshot, result);
}

ScalarStatus
evaluate_scalar(const Network& network, const Position& position, ScalarDiagnostic& result) {
    return evaluate_impl(network, make_capture_pair_snapshot(position), result);
}

const char* scalar_error_message(ScalarError error) noexcept {
    switch (error)
    {
    case ScalarError::None :
        return "none";
    case ScalarError::FeatureOracleError :
        return "feature oracle rejected the position";
    case ScalarError::InvalidFeatureIndex :
        return "feature emission escaped its parameter tensor";
    case ScalarError::InconsistentPerspectiveBucket :
        return "perspectives selected different network buckets";
    case ScalarError::FeatureAccumulatorOutOfRange :
        return "feature accumulator escaped the proved i32 envelope";
    case ScalarError::PsqtAccumulatorOutOfRange :
        return "HM PSQT accumulator escaped the proved i32 envelope";
    case ScalarError::NumericContractError :
        return "dense or output numeric contract failed";
    }
    return "unknown Atomic V3 scalar error";
}

}  // namespace Stockfish::Eval::NNUE::AtomicV3
