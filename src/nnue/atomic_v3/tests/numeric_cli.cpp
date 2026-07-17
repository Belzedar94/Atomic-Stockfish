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
#include <iostream>
#include <limits>
#include <string_view>
#include <vector>

#include "../numeric.h"

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

constexpr std::size_t PsqtWeightCount = std::size_t(HmPhysicalDimensions) * HmPsqtBuckets;

std::size_t psqt_index(std::size_t row, IndexType bucket) { return row * HmPsqtBuckets + bucket; }

bool all_psqt_sums(const HmPsqtBounds& bounds, i64 expected) {
    return std::all_of(bounds.maximumAbsoluteSums.begin(), bounds.maximumAbsoluteSums.end(),
                       [expected](i64 value) { return value == expected; });
}

void test_feature_transformer_envelope() {
    expect(
      FeatureTransformerAccumulatorMinimum == -2289664
        && FeatureTransformerAccumulatorMaximum == 2289116
        && feature_transformer_accumulator_in_range(FeatureTransformerAccumulatorMinimum)
        && feature_transformer_accumulator_in_range(FeatureTransformerAccumulatorMaximum)
        && !feature_transformer_accumulator_in_range(i64(FeatureTransformerAccumulatorMinimum) - 1)
        && !feature_transformer_accumulator_in_range(i64(FeatureTransformerAccumulatorMaximum) + 1),
      "feature-transformer i32 envelope has exact inclusive boundaries");
}

void test_psqt_exact_boundary_all_buckets() {
    std::vector<i32> weights(PsqtWeightCount, 0);
    for (IndexType bucket = 0; bucket < HmPsqtBuckets; ++bucket)
        weights[psqt_index(0, bucket)] = std::numeric_limits<i32>::max();

    HmPsqtBounds bounds{};
    const auto   error = validate_hm_psqt_weights(weights.data(), weights.size(), bounds);
    expect(error == NumericError::None && all_psqt_sums(bounds, std::numeric_limits<i32>::max()),
           "all eight HM PSQT buckets accept the exact INT32_MAX top-32 boundary");

    weights[psqt_index(1, 3)] = 1;
    bounds.maximumAbsoluteSums.fill(17);
    expect(validate_hm_psqt_weights(weights.data(), weights.size(), bounds)
               == NumericError::PsqtAccumulatorOverflow
             && all_psqt_sums(bounds, 0),
           "one bucket at exact boundary plus one fails and clears all PSQT bounds");
}

void test_psqt_promoted_magnitude_and_top_33() {
    std::vector<i32> weights(PsqtWeightCount, 0);
    for (IndexType bucket = 0; bucket < HmPsqtBuckets; ++bucket)
        for (std::size_t row = 0; row < 33; ++row)
            weights[psqt_index(row, bucket)] = i32(row + 1);

    HmPsqtBounds bounds{};
    expect(validate_hm_psqt_weights(weights.data(), weights.size(), bounds) == NumericError::None
             && all_psqt_sums(bounds, 560),
           "top-32 selection drops the smallest of 33 weights in every PSQT bucket");

    std::fill(weights.begin(), weights.end(), 0);
    weights[psqt_index(0, 6)] = -std::numeric_limits<i32>::max();
    expect(validate_hm_psqt_weights(weights.data(), weights.size(), bounds) == NumericError::None
             && bounds.maximumAbsoluteSums[6] == std::numeric_limits<i32>::max(),
           "negative PSQT weights use promoted i64 magnitude without signed wraparound");

    weights[psqt_index(0, 6)] = std::numeric_limits<i32>::min();
    bounds.maximumAbsoluteSums.fill(17);
    expect(validate_hm_psqt_weights(weights.data(), weights.size(), bounds)
               == NumericError::PsqtWeightMagnitudeOverflow
             && all_psqt_sums(bounds, 0),
           "INT32_MIN PSQT weight is rejected explicitly and clears the result");
}

void test_psqt_failure_paths() {
    HmPsqtBounds bounds{};
    expect(validate_hm_psqt_weights(nullptr, PsqtWeightCount, bounds) == NumericError::NullData,
           "PSQT validation rejects null tensor data");

    std::vector<i32> weights(PsqtWeightCount, 0);
    expect(validate_hm_psqt_weights(weights.data(), weights.size() - 1, bounds)
             == NumericError::InvalidDimensions,
           "PSQT validation rejects non-canonical tensor dimensions");
}

void test_psqt_perspective_difference() {
    constexpr i64 IntMax = std::numeric_limits<i32>::max();
    i32           value  = 17;
    expect(psqt_perspective_difference(IntMax, -IntMax, value) == NumericError::None
             && value == std::numeric_limits<i32>::max(),
           "PSQT perspective difference accepts the exact positive boundary after /2");
    expect(psqt_perspective_difference(-IntMax, IntMax, value) == NumericError::None
             && value == -std::numeric_limits<i32>::max(),
           "PSQT perspective difference accepts the exact negative boundary after /2");
    expect(psqt_perspective_difference(3, 0, value) == NumericError::None && value == 1
             && psqt_perspective_difference(-3, 0, value) == NumericError::None && value == -1,
           "PSQT perspective /2 truncates odd signed differences toward zero");
    value = 17;
    expect(psqt_perspective_difference(std::numeric_limits<i32>::min(), 0, value)
               == NumericError::PsqtAccumulatorOverflow
             && value == 0,
           "PSQT perspective difference rejects a state outside the loader envelope");
}

void test_affine_boundaries() {
    const std::array<i8, 1> negativeMinimum{{std::numeric_limits<i8>::min()}};
    AffineOutputBounds      bounds{};
    expect(affine_output_bounds(0, negativeMinimum.data(), negativeMinimum.size(), bounds)
               == NumericError::None
             && bounds.canonicalMagnitude == 16256 && bounds.signedRange.lower == -16256
             && bounds.signedRange.upper == 0,
           "affine helper promotes i8=-128 and emits its exact signed interval");

    const std::array<i8, 1> one{{1}};
    const i32               exactBias = std::numeric_limits<i32>::max() - i32(DenseInputMaximum);
    expect(affine_output_bounds(exactBias, one.data(), one.size(), bounds) == NumericError::None
             && bounds.canonicalMagnitude == std::numeric_limits<i32>::max()
             && bounds.signedRange.lower == exactBias
             && bounds.signedRange.upper == std::numeric_limits<i32>::max(),
           "affine canonical magnitude accepts exact INT32_MAX boundary");

    bounds = {{17, 17}, 17};
    expect(affine_output_bounds(exactBias + 1, one.data(), one.size(), bounds)
               == NumericError::AffineAccumulatorOverflow
             && bounds.signedRange.lower == 0 && bounds.signedRange.upper == 0
             && bounds.canonicalMagnitude == 0,
           "affine canonical magnitude rejects boundary plus one and clears output");

    bounds = {};
    expect(affine_output_bounds(-23, nullptr, 0, bounds) == NumericError::None
             && bounds.signedRange.lower == -23 && bounds.signedRange.upper == -23
             && bounds.canonicalMagnitude == 23,
           "zero-input affine output has the exact bias interval");
    expect(affine_output_bounds(-std::numeric_limits<i32>::max(), nullptr, 0, bounds)
               == NumericError::None
             && bounds.signedRange.lower == -std::numeric_limits<i32>::max()
             && bounds.signedRange.upper == -std::numeric_limits<i32>::max()
             && bounds.canonicalMagnitude == std::numeric_limits<i32>::max(),
           "affine canonical magnitude accepts the exact negative INT32_MAX boundary");
    bounds = {{17, 17}, 17};
    expect(affine_output_bounds(std::numeric_limits<i32>::min(), nullptr, 0, bounds)
               == NumericError::AffineAccumulatorOverflow
             && bounds.signedRange.lower == 0 && bounds.signedRange.upper == 0
             && bounds.canonicalMagnitude == 0,
           "affine canonical magnitude rejects INT32_MIN before abs arithmetic");
    expect(affine_output_bounds(0, nullptr, 1, bounds) == NumericError::NullData,
           "non-empty affine weights reject null data");
}

void test_forward_interval_gate() {
    constexpr i64  Tail = 1517555112LL;
    SignedInterval raw{};
    expect(
      validate_forward_interval({std::numeric_limits<i32>::min(), std::numeric_limits<i32>::max()},
                                {0, Tail}, {0, Tail}, raw)
          == NumericError::None
        && raw.lower == RawOutputMinimum && raw.upper == RawOutputMaximum,
      "global forward gate accepts both exact scalable raw boundaries");

    raw = {17, 17};
    expect(
      validate_forward_interval({0, std::numeric_limits<i32>::max()}, {0, Tail + 1}, {0, 0}, raw)
          == NumericError::RawOutputOverflow
        && raw.lower == 0 && raw.upper == 0,
      "global forward gate rejects upper raw boundary plus one");
    expect(
      validate_forward_interval({std::numeric_limits<i32>::min(), 0}, {0, 0}, {0, Tail + 1}, raw)
          == NumericError::RawOutputOverflow
        && raw.lower == 0 && raw.upper == 0,
      "global forward gate rejects lower raw boundary minus one");

    constexpr i64 IntMax = std::numeric_limits<i32>::max();
    expect(
      validate_forward_interval({-IntMax, IntMax}, {-IntMax, IntMax}, {-IntMax, IntMax}, raw)
        == NumericError::RawOutputOverflow,
      "three individually valid affine ranges expose and reject the 3*INT32_MAX counterexample");

    expect(validate_forward_interval({1, -1}, {0, 0}, {0, 0}, raw) == NumericError::InvalidInterval,
           "global forward gate rejects inverted intervals");
    expect(validate_forward_interval(
             {std::numeric_limits<i32>::min(), i64(std::numeric_limits<i32>::max()) + 1}, {0, 0},
             {0, 0}, raw)
             == NumericError::AffineAccumulatorOverflow,
           "global forward gate rejects affine endpoints outside i32");
}

void test_scale_and_checked_narrow() {
    i32 value = 17;
    expect(scale_raw_output(RawOutputMinimum, value) == NumericError::None
             && value == std::numeric_limits<i32>::min(),
           "minimum raw output scales exactly to INT32_MIN");
    expect(scale_raw_output(RawOutputMaximum, value) == NumericError::None
             && value == std::numeric_limits<i32>::max(),
           "maximum raw output scales exactly to INT32_MAX");
    expect(scale_raw_output(-1, value) == NumericError::None && value == 0,
           "negative fractional scaling truncates toward zero");
    expect(scale_raw_output(1, value) == NumericError::None && value == 0,
           "positive fractional scaling truncates toward zero");
    expect(scale_raw_output(-OutputScaleDenominator, value) == NumericError::None
             && value == -OutputScaleNumerator,
           "negative integral scaling preserves sign and exact quotient");

    i64 raw    = 1;
    i32 scaled = 1;
    expect(compose_dense_output(std::numeric_limits<i32>::max(), std::numeric_limits<i32>::max(),
                                629928535, raw, scaled)
               == NumericError::None
             && raw == RawOutputMaximum && scaled == std::numeric_limits<i32>::max(),
           "i64 dense composition accepts the positive V3 boundary above INT32_MAX");
    expect(compose_dense_output(std::numeric_limits<i32>::min(), std::numeric_limits<i32>::min(),
                                -629928536, raw, scaled)
               == NumericError::None
             && raw == RawOutputMinimum && scaled == std::numeric_limits<i32>::min(),
           "i64 dense composition accepts the negative V3 boundary below INT32_MIN");
    expect(compose_dense_output(std::numeric_limits<i32>::max(), std::numeric_limits<i32>::max(),
                                629928534, raw, scaled)
               == NumericError::RawOutputOverflow
             && raw == 0 && scaled == 0,
           "i64 dense composition rejects boundary plus one transactionally");

    value = 17;
    expect(scale_raw_output(RawOutputMinimum - 1, value) == NumericError::RawOutputOverflow
             && value == 0,
           "scale rejects raw value below the proven envelope and clears output");
    expect(scale_raw_output(RawOutputMaximum + 1, value) == NumericError::RawOutputOverflow
             && value == 0,
           "scale rejects raw value above the proven envelope and clears output");

    expect(checked_narrow_i32(std::numeric_limits<i32>::min(), value) == NumericError::None
             && value == std::numeric_limits<i32>::min(),
           "checked i32 narrow accepts INT32_MIN");
    expect(checked_narrow_i32(std::numeric_limits<i32>::max(), value) == NumericError::None
             && value == std::numeric_limits<i32>::max(),
           "checked i32 narrow accepts INT32_MAX");
    expect(checked_narrow_i32(i64(std::numeric_limits<i32>::min()) - 1, value)
               == NumericError::I32NarrowingOverflow
             && value == 0,
           "checked i32 narrow rejects INT32_MIN minus one");
    expect(checked_narrow_i32(i64(std::numeric_limits<i32>::max()) + 1, value)
               == NumericError::I32NarrowingOverflow
             && value == 0,
           "checked i32 narrow rejects INT32_MAX plus one");
}

void test_error_messages() {
    constexpr std::array<NumericError, 9> Errors{{
      NumericError::None,
      NumericError::NullData,
      NumericError::InvalidDimensions,
      NumericError::PsqtWeightMagnitudeOverflow,
      NumericError::PsqtAccumulatorOverflow,
      NumericError::AffineAccumulatorOverflow,
      NumericError::InvalidInterval,
      NumericError::RawOutputOverflow,
      NumericError::I32NarrowingOverflow,
    }};
    expect(std::all_of(Errors.begin(), Errors.end(),
                       [](NumericError error) {
                           return std::string_view(numeric_error_message(error))
                               != "unknown numeric error";
                       }),
           "every numeric error has a stable diagnostic");
}

}  // namespace
}  // namespace Stockfish::Eval::NNUE::AtomicV3

int main() {
    using namespace Stockfish::Eval::NNUE::AtomicV3;

    test_feature_transformer_envelope();
    test_psqt_exact_boundary_all_buckets();
    test_psqt_promoted_magnitude_and_top_33();
    test_psqt_failure_paths();
    test_psqt_perspective_difference();
    test_affine_boundaries();
    test_forward_interval_gate();
    test_scale_and_checked_narrow();
    test_error_messages();

    if (failures != 0)
    {
        std::cerr << failures << " AtomicNNUEV3 numeric self-test(s) failed\n";
        return EXIT_FAILURE;
    }

    std::cout << "All AtomicNNUEV3 numeric self-tests passed\n";
    return EXIT_SUCCESS;
}
