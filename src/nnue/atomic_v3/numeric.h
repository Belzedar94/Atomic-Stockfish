/*
  Atomic-Stockfish, a UCI chess playing engine derived from Stockfish
  Copyright (C) 2004-2026 The Stockfish developers (see AUTHORS file)

  Atomic-Stockfish is free software: you can redistribute it and/or modify
  it under the terms of the GNU General Public License as published by
  the Free Software Foundation, either version 3 of the License, or
  (at your option) any later version.
*/

#ifndef ATOMIC_V3_NUMERIC_H_INCLUDED
#define ATOMIC_V3_NUMERIC_H_INCLUDED

#include <array>
#include <cstddef>
#include <limits>

#include "full_refresh.h"

namespace Stockfish::Eval::NNUE::AtomicV3 {

// Conservative scalar feature-transformer envelope proved from the four V3
// slice dtypes and their individual maximum-active bounds. This does not claim
// that one legal position simultaneously reaches every slice maximum.
inline constexpr i64 FeatureTransformerI16Contributors =
  1 + HmMaximumActiveDimensions + KingBlastEpMaximumActiveFeatures;
inline constexpr i64 FeatureTransformerI8Contributors =
  CapturePairMaximumActiveFeatures + BlastRingMaximumActiveFeatures;
inline constexpr i64 FeatureTransformerAccumulatorMinimumWide =
  FeatureTransformerI16Contributors * std::numeric_limits<i16>::min()
  + FeatureTransformerI8Contributors * std::numeric_limits<i8>::min();
inline constexpr i64 FeatureTransformerAccumulatorMaximumWide =
  FeatureTransformerI16Contributors * std::numeric_limits<i16>::max()
  + FeatureTransformerI8Contributors * std::numeric_limits<i8>::max();
static_assert(FeatureTransformerAccumulatorMinimumWide >= std::numeric_limits<i32>::min());
static_assert(FeatureTransformerAccumulatorMaximumWide <= std::numeric_limits<i32>::max());
inline constexpr i32 FeatureTransformerAccumulatorMinimum =
  static_cast<i32>(FeatureTransformerAccumulatorMinimumWide);
inline constexpr i32 FeatureTransformerAccumulatorMaximum =
  static_cast<i32>(FeatureTransformerAccumulatorMaximumWide);

inline constexpr IndexType HmPsqtBuckets               = HmPsqtOutputs;
inline constexpr IndexType HmPsqtMaximumActiveFeatures = HmMaximumActiveDimensions;
inline constexpr i64       PsqtPerspectiveDivisor      = 2;
inline constexpr i64       DenseInputMaximum           = 127;

// Runtime PSQT state remains i64 even though the loader envelope guarantees
// every valid fully refreshed bucket is representable as i32. This also keeps
// incremental add/subtract intermediates free of signed overflow.
using HmPsqtAccumulatorType = i64;

// The largest raw SFNNv15 forward interval that remains representable after
// the exact 9600 / 16384 scale and truncation toward zero.
inline constexpr i64 RawOutputMinimum       = -3665038760LL;
inline constexpr i64 RawOutputMaximum       = 3665038759LL;
inline constexpr i64 OutputScaleNumerator   = 9600;
inline constexpr i64 OutputScaleDenominator = 16384;

enum class NumericError : u8 {
    None,
    NullData,
    InvalidDimensions,
    PsqtWeightMagnitudeOverflow,
    PsqtAccumulatorOverflow,
    AffineAccumulatorOverflow,
    InvalidInterval,
    RawOutputOverflow,
    I32NarrowingOverflow
};

struct SignedInterval {
    i64 lower = 0;
    i64 upper = 0;

    constexpr bool valid() const noexcept { return lower <= upper; }
};

struct HmPsqtBounds {
    std::array<HmPsqtAccumulatorType, HmPsqtBuckets> maximumAbsoluteSums{};
};

struct AffineOutputBounds {
    SignedInterval signedRange{};
    i64            canonicalMagnitude = 0;
};

constexpr bool feature_transformer_accumulator_in_range(i64 value) noexcept {
    return value >= FeatureTransformerAccumulatorMinimum
        && value <= FeatureTransformerAccumulatorMaximum;
}

// Validates the complete feature-major [HmPhysicalDimensions][8] HM-only PSQT
// tensor. For each bucket, the sum of the 32 largest promoted magnitudes must
// fit i32. INT32_MIN is rejected explicitly: its promoted magnitude is
// INT32_MAX + 1 and can never be a legal active contribution.
NumericError
validate_hm_psqt_weights(const i32* weights, std::size_t weightCount, HmPsqtBounds& result);

// Reproduces the inherited Stockfish/AtomicNNUEV2 PSQT convention exactly:
// subtract the two bounded perspective totals in i64 and divide by two with
// signed truncation toward zero before the checked i32 result is exposed.
NumericError
psqt_perspective_difference(HmPsqtAccumulatorType first, HmPsqtAccumulatorType second, i32& result);

// Computes both the exact signed output interval for inputs in [0, 127] and
// the canonical symmetric loader bound
//   abs64(bias) + 127 * sum(abs64(weight)).
// The canonical bound, not merely the tighter signed interval, must fit i32.
NumericError affine_output_bounds(i32                 bias,
                                  const i8*           weights,
                                  std::size_t         weightCount,
                                  AffineOutputBounds& result);

// Applies the final SFNNv15 composition exactly:
//   fc2 + fc0[30] - fc0[31]
//   [L2 + L30 - U31, U2 + U30 - L31].
// Every affine input interval must itself fit i32, and the composed interval
// must remain inside [RawOutputMinimum, RawOutputMaximum].
NumericError validate_forward_interval(const SignedInterval& fc2,
                                       const SignedInterval& fc0_30,
                                       const SignedInterval& fc0_31,
                                       SignedInterval&       result);

NumericError checked_narrow_i32(i64 value, i32& result);

// Composes the three authenticated i32 tail components in i64 before scaling.
// This is required because a valid V3 raw output may exceed INT32 even though
// each affine component is individually representable as i32.
NumericError compose_dense_output(
  i32 fc2, i32 fc0SkipAdd, i32 fc0SkipSubtract, i64& rawOutput, i32& scaledOutput);

// Scales in i64 and relies on C++ signed integer division for truncation toward
// zero, then performs an explicit checked i32 narrowing.
NumericError scale_raw_output(i64 rawOutput, i32& result);

const char* numeric_error_message(NumericError error);

static_assert(FeatureTransformerAccumulatorMinimum == -2289664);
static_assert(FeatureTransformerAccumulatorMaximum == 2289116);
static_assert(FeatureTransformerI16Contributors == 68);
static_assert(FeatureTransformerI8Contributors == 480);
static_assert(feature_transformer_accumulator_in_range(FeatureTransformerAccumulatorMinimum));
static_assert(feature_transformer_accumulator_in_range(FeatureTransformerAccumulatorMaximum));
static_assert(!feature_transformer_accumulator_in_range(i64(FeatureTransformerAccumulatorMinimum)
                                                        - 1));
static_assert(!feature_transformer_accumulator_in_range(i64(FeatureTransformerAccumulatorMaximum)
                                                        + 1));
static_assert(HmPsqtBuckets == 8);
static_assert(HmPsqtMaximumActiveFeatures == 32);
static_assert(PsqtPerspectiveDivisor == 2);
static_assert(std::numeric_limits<HmPsqtAccumulatorType>::is_signed
              && std::numeric_limits<HmPsqtAccumulatorType>::digits >= 63);
static_assert(OutputScaleNumerator == 9600 && OutputScaleDenominator == 16384);
static_assert((RawOutputMinimum * OutputScaleNumerator) / OutputScaleDenominator
              == std::numeric_limits<i32>::min());
static_assert((RawOutputMaximum * OutputScaleNumerator) / OutputScaleDenominator
              == std::numeric_limits<i32>::max());

}  // namespace Stockfish::Eval::NNUE::AtomicV3

#endif  // ATOMIC_V3_NUMERIC_H_INCLUDED
