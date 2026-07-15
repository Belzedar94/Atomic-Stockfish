/*
  Atomic-Stockfish, a UCI chess playing engine derived from Stockfish
  Copyright (C) 2004-2026 The Stockfish developers (see AUTHORS file)

  Atomic-Stockfish is free software: you can redistribute it and/or modify
  it under the terms of the GNU General Public License as published by
  the Free Software Foundation, either version 3 of the License, or
  (at your option) any later version.
*/

#include "numeric.h"

#include <algorithm>
#include <limits>

namespace Stockfish::Eval::NNUE::AtomicV3 {
namespace {

constexpr i64 I32Minimum = std::numeric_limits<i32>::min();
constexpr i64 I32Maximum = std::numeric_limits<i32>::max();

constexpr bool interval_fits_i32(const SignedInterval& interval) {
    return interval.valid() && interval.lower >= I32Minimum && interval.upper <= I32Maximum;
}

constexpr i64 promoted_magnitude(i32 value) { return value < 0 ? -i64(value) : i64(value); }

constexpr i64 promoted_magnitude(i8 value) { return value < 0 ? -i64(value) : i64(value); }

}  // namespace

NumericError
validate_hm_psqt_weights(const i32* weights, std::size_t weightCount, HmPsqtBounds& result) {
    result = {};

    constexpr std::size_t ExpectedWeightCount = std::size_t(HmPhysicalDimensions) * HmPsqtBuckets;
    if (weightCount != ExpectedWeightCount)
        return NumericError::InvalidDimensions;
    if (!weights)
        return NumericError::NullData;

    std::array<std::array<i64, HmPsqtMaximumActiveFeatures>, HmPsqtBuckets> largest{};

    for (std::size_t index = 0; index < weightCount; ++index)
    {
        const i32 weight = weights[index];
        if (weight == std::numeric_limits<i32>::min())
            return NumericError::PsqtWeightMagnitudeOverflow;

        const i64 magnitude = promoted_magnitude(weight);
        auto&     bucket    = largest[index % HmPsqtBuckets];
        if (magnitude <= bucket.front())
            continue;

        bucket.front() = magnitude;
        for (std::size_t position = 1;
             position < bucket.size() && bucket[position - 1] > bucket[position]; ++position)
            std::swap(bucket[position - 1], bucket[position]);
    }

    HmPsqtBounds candidate{};
    for (IndexType bucket = 0; bucket < HmPsqtBuckets; ++bucket)
    {
        i64 sum = 0;
        for (const i64 magnitude : largest[bucket])
        {
            if (magnitude > I32Maximum - sum)
                return NumericError::PsqtAccumulatorOverflow;
            sum += magnitude;
        }
        candidate.maximumAbsoluteSums[bucket] = sum;
    }

    result = candidate;
    return NumericError::None;
}

NumericError psqt_perspective_difference(HmPsqtAccumulatorType first,
                                         HmPsqtAccumulatorType second,
                                         i32&                  result) {
    result = 0;
    if (first < -I32Maximum || first > I32Maximum || second < -I32Maximum || second > I32Maximum)
        return NumericError::PsqtAccumulatorOverflow;

    return checked_narrow_i32((first - second) / PsqtPerspectiveDivisor, result);
}

NumericError affine_output_bounds(i32                 bias,
                                  const i8*           weights,
                                  std::size_t         weightCount,
                                  AffineOutputBounds& result) {
    result = {};
    if (!weights && weightCount != 0)
        return NumericError::NullData;

    const i64 biasMagnitude = promoted_magnitude(bias);
    if (biasMagnitude > I32Maximum)
        return NumericError::AffineAccumulatorOverflow;

    i64 lower              = bias;
    i64 upper              = bias;
    i64 canonicalMagnitude = biasMagnitude;

    for (std::size_t index = 0; index < weightCount; ++index)
    {
        const i64 weight    = weights[index];
        const i64 magnitude = promoted_magnitude(weights[index]);
        const i64 term      = DenseInputMaximum * magnitude;
        if (term > I32Maximum - canonicalMagnitude)
            return NumericError::AffineAccumulatorOverflow;

        canonicalMagnitude += term;
        if (weight < 0)
            lower += DenseInputMaximum * weight;
        else
            upper += DenseInputMaximum * weight;
    }

    const AffineOutputBounds candidate{{lower, upper}, canonicalMagnitude};
    if (!interval_fits_i32(candidate.signedRange))
        return NumericError::AffineAccumulatorOverflow;

    result = candidate;
    return NumericError::None;
}

NumericError validate_forward_interval(const SignedInterval& fc2,
                                       const SignedInterval& fc0_30,
                                       const SignedInterval& fc0_31,
                                       SignedInterval&       result) {
    result = {};
    if (!fc2.valid() || !fc0_30.valid() || !fc0_31.valid())
        return NumericError::InvalidInterval;
    if (!interval_fits_i32(fc2) || !interval_fits_i32(fc0_30) || !interval_fits_i32(fc0_31))
        return NumericError::AffineAccumulatorOverflow;

    const SignedInterval candidate{fc2.lower + fc0_30.lower - fc0_31.upper,
                                   fc2.upper + fc0_30.upper - fc0_31.lower};
    if (candidate.lower < RawOutputMinimum || candidate.upper > RawOutputMaximum)
        return NumericError::RawOutputOverflow;

    result = candidate;
    return NumericError::None;
}

NumericError checked_narrow_i32(i64 value, i32& result) {
    result = 0;
    if (value < I32Minimum || value > I32Maximum)
        return NumericError::I32NarrowingOverflow;
    result = i32(value);
    return NumericError::None;
}

NumericError scale_raw_output(i64 rawOutput, i32& result) {
    result = 0;
    if (rawOutput < RawOutputMinimum || rawOutput > RawOutputMaximum)
        return NumericError::RawOutputOverflow;

    const i64 scaled = (rawOutput * OutputScaleNumerator) / OutputScaleDenominator;
    return checked_narrow_i32(scaled, result);
}

const char* numeric_error_message(NumericError error) {
    switch (error)
    {
    case NumericError::None :
        return "none";
    case NumericError::NullData :
        return "null data";
    case NumericError::InvalidDimensions :
        return "invalid dimensions";
    case NumericError::PsqtWeightMagnitudeOverflow :
        return "PSQT weight magnitude exceeds INT32_MAX";
    case NumericError::PsqtAccumulatorOverflow :
        return "PSQT active-weight envelope exceeds INT32_MAX";
    case NumericError::AffineAccumulatorOverflow :
        return "affine output envelope exceeds INT32";
    case NumericError::InvalidInterval :
        return "invalid signed interval";
    case NumericError::RawOutputOverflow :
        return "raw forward interval exceeds the scalable envelope";
    case NumericError::I32NarrowingOverflow :
        return "value cannot be represented as INT32";
    }
    return "unknown numeric error";
}

}  // namespace Stockfish::Eval::NNUE::AtomicV3
