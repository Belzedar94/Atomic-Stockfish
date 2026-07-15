/*
  Atomic-Stockfish, a UCI chess playing engine derived from Stockfish
  Copyright (C) 2004-2026 The Stockfish developers (see AUTHORS file)

  Atomic-Stockfish is free software: you can redistribute it and/or modify
  it under the terms of the GNU General Public License as published by
  the Free Software Foundation, either version 3 of the License, or
  (at your option) any later version.
*/

#include "simd_backend.h"

#include <algorithm>
#include <limits>
#include <utility>

#if defined(USE_AVX2) || defined(USE_AVX512)
    #include <immintrin.h>
#elif defined(USE_SSE41)
    #include <smmintrin.h>
#endif

#include "../../position.h"
#include "wire_io.h"

#if defined(_MSC_VER)
    #define ATOMIC_V3_NOINLINE __declspec(noinline)
#elif defined(__GNUC__) || defined(__clang__)
    #define ATOMIC_V3_NOINLINE __attribute__((noinline, used))
#else
    #define ATOMIC_V3_NOINLINE
#endif

namespace Stockfish::Eval::NNUE::AtomicV3 {
namespace {

constexpr std::size_t color_index(Color color) noexcept { return static_cast<std::size_t>(color); }

constexpr ScalarStatus scalar_failure(ScalarError      error,
                                      FullRefreshError feature = FullRefreshError::None) noexcept {
    return {error, feature, NumericError::None};
}

SimdStatus fail(SimdDiagnostic& result, SimdError error, ScalarStatus scalarStatus = {}) noexcept {
    result = {};
    return {error, scalarStatus};
}

void add_i16_scalar(i32* destination, const i16* source, std::size_t count) noexcept {
    volatile i32* cells = destination;
    for (std::size_t index = 0; index < count; ++index)
        cells[index] = cells[index] + static_cast<i32>(source[index]);
}

void add_i8_scalar(i32* destination, const i8* source, std::size_t count) noexcept {
    volatile i32* cells = destination;
    for (std::size_t index = 0; index < count; ++index)
        cells[index] = cells[index] + static_cast<i32>(source[index]);
}

void add_i16(SimdIsa       isa,
             i32*          destination,
             const i16*    source,
             std::size_t   count,
             SimdCounters& counters) noexcept {
    switch (isa)
    {
    case SimdIsa::Scalar :
        add_i16_scalar(destination, source, count);
        ++counters.scalarKernelCalls;
        break;
    case SimdIsa::Sse41 :
#if defined(USE_SSE41) || defined(USE_AVX2) || defined(USE_AVX512)
        atomic_v3_add_i16_sse41_kernel(destination, source, count);
        ++counters.sse41KernelCalls;
#else
        add_i16_scalar(destination, source, count);
        ++counters.scalarKernelCalls;
#endif
        break;
    case SimdIsa::Avx2 :
#if defined(USE_AVX2) || defined(USE_AVX512)
        atomic_v3_add_i16_avx2_kernel(destination, source, count);
        ++counters.avx2KernelCalls;
#else
        add_i16_scalar(destination, source, count);
        ++counters.scalarKernelCalls;
#endif
        break;
    }
    counters.i16Lanes += count;
}

void add_i8(SimdIsa       isa,
            i32*          destination,
            const i8*     source,
            std::size_t   count,
            SimdCounters& counters) noexcept {
    switch (isa)
    {
    case SimdIsa::Scalar :
        add_i8_scalar(destination, source, count);
        ++counters.scalarKernelCalls;
        break;
    case SimdIsa::Sse41 :
#if defined(USE_SSE41) || defined(USE_AVX2) || defined(USE_AVX512)
        atomic_v3_add_i8_sse41_kernel(destination, source, count);
        ++counters.sse41KernelCalls;
#else
        add_i8_scalar(destination, source, count);
        ++counters.scalarKernelCalls;
#endif
        break;
    case SimdIsa::Avx2 :
#if defined(USE_AVX2) || defined(USE_AVX512)
        atomic_v3_add_i8_avx2_kernel(destination, source, count);
        ++counters.avx2KernelCalls;
#else
        add_i8_scalar(destination, source, count);
        ++counters.scalarKernelCalls;
#endif
        break;
    }
    counters.i8Lanes += count;
}

SimdStatus evaluate_impl(const Network&             network,
                         const CapturePairSnapshot& snapshot,
                         SimdIsa                    requestedIsa,
                         SimdScratch&               scratch,
                         SimdDiagnostic&            result) {
    result = {};
    if (!simd_isa_available(requestedIsa))
        return fail(result, SimdError::UnsupportedIsa);

    SimdDiagnostic candidate{};
    candidate.requestedIsa = requestedIsa;
    candidate.executedIsa  = requestedIsa;

    if (!network.biases() || !network.hm_weights() || !network.capture_pair_weights()
        || !network.king_blast_ep_weights() || !network.blast_ring_weights()
        || !network.hm_psqt_weights())
        return fail(result, SimdError::InvalidFeatureIndex,
                    scalar_failure(ScalarError::InvalidFeatureIndex));

    std::array<FullRefreshEmission, COLOR_NB> emissions{};
    std::array<ScalarHmPerspective, COLOR_NB> completeStates{};

    for (const Color perspective : {WHITE, BLACK})
    {
        const std::size_t index        = color_index(perspective);
        const auto        featureError = emit_full_refresh(snapshot, perspective, emissions[index]);
        if (featureError != FullRefreshError::None)
            return fail(result, SimdError::FeatureOracleError,
                        scalar_failure(ScalarError::FeatureOracleError, featureError));

        const auto& emission = emissions[index];
        auto&       complete = completeStates[index];
        auto&       internal = scratch.internalAccumulator;
        internal.fill(0);

        add_i16(requestedIsa, internal.data(), network.biases(), internal.size(),
                candidate.counters);
        ++candidate.counters.biasI16Rows;

        for (IndexType feature = 0; feature < emission.hm.size; ++feature)
        {
            const std::size_t row = emission.hm.features[feature].physicalIndex;
            if (row >= HmPhysicalDimensions)
                return fail(result, SimdError::InvalidFeatureIndex,
                            scalar_failure(ScalarError::InvalidFeatureIndex));

            add_i16(requestedIsa, internal.data(),
                    network.hm_weights() + row * AccumulatorDimensions, internal.size(),
                    candidate.counters);
            ++candidate.counters.hmI16Rows;
            for (IndexType bucket = 0; bucket < PsqtBuckets; ++bucket)
                complete.psqt[bucket] += network.hm_psqt_weights()[row * PsqtBuckets + bucket];
        }

        for (IndexType feature = 0; feature < emission.capturePairs.size; ++feature)
        {
            const std::size_t physical = emission.capturePairs.features[feature].physicalIndex;
            if (physical < CapturePairPhysicalOffset
                || physical - CapturePairPhysicalOffset >= CapturePairPhysicalDimensions)
                return fail(result, SimdError::InvalidFeatureIndex,
                            scalar_failure(ScalarError::InvalidFeatureIndex));
            const std::size_t row = physical - CapturePairPhysicalOffset;
            add_i8(requestedIsa, internal.data(),
                   network.capture_pair_weights() + row * AccumulatorDimensions, internal.size(),
                   candidate.counters);
            ++candidate.counters.capturePairI8Rows;
        }

        for (IndexType feature = 0; feature < emission.kingBlastEp.size; ++feature)
        {
            const std::size_t physical = emission.kingBlastEp.features[feature].physicalIndex;
            if (physical < KingBlastEpPhysicalOffset
                || physical - KingBlastEpPhysicalOffset >= KingBlastEpPhysicalDimensions)
                return fail(result, SimdError::InvalidFeatureIndex,
                            scalar_failure(ScalarError::InvalidFeatureIndex));
            const std::size_t row = physical - KingBlastEpPhysicalOffset;
            add_i16(requestedIsa, internal.data(),
                    network.king_blast_ep_weights() + row * AccumulatorDimensions, internal.size(),
                    candidate.counters);
            ++candidate.counters.kingBlastEpI16Rows;
        }

        for (IndexType feature = 0; feature < emission.blastRing.size; ++feature)
        {
            const std::size_t physical = emission.blastRing.features[feature].physicalIndex;
            if (physical < BlastRingPhysicalOffset
                || physical - BlastRingPhysicalOffset >= BlastRingPhysicalDimensions)
                return fail(result, SimdError::InvalidFeatureIndex,
                            scalar_failure(ScalarError::InvalidFeatureIndex));
            const std::size_t row = physical - BlastRingPhysicalOffset;
            add_i8(requestedIsa, internal.data(),
                   network.blast_ring_weights() + row * AccumulatorDimensions, internal.size(),
                   candidate.counters);
            ++candidate.counters.blastRingI8Rows;
        }

        for (std::size_t canonical = 0; canonical < AccumulatorDimensions; ++canonical)
        {
            const std::size_t internalIndex =
              WireIO::internal_index_from_canonical<i16, 16>(canonical);
            const i32 value = internal[internalIndex];
            if (!feature_transformer_accumulator_in_range(value))
                return fail(result, SimdError::FeatureAccumulatorOutOfRange,
                            scalar_failure(ScalarError::FeatureAccumulatorOutOfRange));
            complete.accumulator[canonical] = value;
        }

        for (const HmPsqtAccumulatorType value : complete.psqt)
            if (value < std::numeric_limits<i32>::min() || value > std::numeric_limits<i32>::max())
                return fail(result, SimdError::PsqtAccumulatorOutOfRange,
                            scalar_failure(ScalarError::PsqtAccumulatorOutOfRange));
    }

    ScalarDiagnostic   scalar{};
    const ScalarStatus scalarStatus =
      finalize_scalar_diagnostic(network, snapshot, emissions, completeStates, scalar);
    if (!scalarStatus)
        return fail(result, SimdError::ScalarCompositionError, scalarStatus);

    candidate.scalar = std::move(scalar);
    result           = std::move(candidate);
    return {};
}

}  // namespace

#if defined(USE_SSE41) || defined(USE_AVX2) || defined(USE_AVX512)

extern "C" ATOMIC_V3_NOINLINE void
atomic_v3_add_i16_sse41_kernel(i32* destination, const i16* source, std::size_t count) noexcept {
    if (!destination || !source)
        return;

    std::size_t index = 0;
    for (; index + 8 <= count; index += 8)
    {
        const __m128i packed = _mm_loadu_si128(reinterpret_cast<const __m128i*>(source + index));
        const __m128i low    = _mm_cvtepi16_epi32(packed);
        const __m128i high   = _mm_cvtepi16_epi32(_mm_srli_si128(packed, 8));
        _mm_storeu_si128(
          reinterpret_cast<__m128i*>(destination + index),
          _mm_add_epi32(_mm_loadu_si128(reinterpret_cast<const __m128i*>(destination + index)),
                        low));
        _mm_storeu_si128(
          reinterpret_cast<__m128i*>(destination + index + 4),
          _mm_add_epi32(_mm_loadu_si128(reinterpret_cast<const __m128i*>(destination + index + 4)),
                        high));
    }
    add_i16_scalar(destination + index, source + index, count - index);
}

extern "C" ATOMIC_V3_NOINLINE void
atomic_v3_add_i8_sse41_kernel(i32* destination, const i8* source, std::size_t count) noexcept {
    if (!destination || !source)
        return;

    std::size_t index = 0;
    for (; index + 16 <= count; index += 16)
    {
        const __m128i packed   = _mm_loadu_si128(reinterpret_cast<const __m128i*>(source + index));
        const __m128i widened0 = _mm_cvtepi8_epi32(packed);
        const __m128i widened1 = _mm_cvtepi8_epi32(_mm_srli_si128(packed, 4));
        const __m128i widened2 = _mm_cvtepi8_epi32(_mm_srli_si128(packed, 8));
        const __m128i widened3 = _mm_cvtepi8_epi32(_mm_srli_si128(packed, 12));
        _mm_storeu_si128(
          reinterpret_cast<__m128i*>(destination + index),
          _mm_add_epi32(_mm_loadu_si128(reinterpret_cast<const __m128i*>(destination + index)),
                        widened0));
        _mm_storeu_si128(
          reinterpret_cast<__m128i*>(destination + index + 4),
          _mm_add_epi32(_mm_loadu_si128(reinterpret_cast<const __m128i*>(destination + index + 4)),
                        widened1));
        _mm_storeu_si128(
          reinterpret_cast<__m128i*>(destination + index + 8),
          _mm_add_epi32(_mm_loadu_si128(reinterpret_cast<const __m128i*>(destination + index + 8)),
                        widened2));
        _mm_storeu_si128(
          reinterpret_cast<__m128i*>(destination + index + 12),
          _mm_add_epi32(_mm_loadu_si128(reinterpret_cast<const __m128i*>(destination + index + 12)),
                        widened3));
    }
    add_i8_scalar(destination + index, source + index, count - index);
}

#endif

#if defined(USE_AVX2) || defined(USE_AVX512)

extern "C" ATOMIC_V3_NOINLINE void
atomic_v3_add_i16_avx2_kernel(i32* destination, const i16* source, std::size_t count) noexcept {
    if (!destination || !source)
        return;

    std::size_t index = 0;
    for (; index + 8 <= count; index += 8)
    {
        const __m256i widened =
          _mm256_cvtepi16_epi32(_mm_loadu_si128(reinterpret_cast<const __m128i*>(source + index)));
        _mm256_storeu_si256(
          reinterpret_cast<__m256i*>(destination + index),
          _mm256_add_epi32(
            _mm256_loadu_si256(reinterpret_cast<const __m256i*>(destination + index)), widened));
    }
    add_i16_scalar(destination + index, source + index, count - index);
}

extern "C" ATOMIC_V3_NOINLINE void
atomic_v3_add_i8_avx2_kernel(i32* destination, const i8* source, std::size_t count) noexcept {
    if (!destination || !source)
        return;

    std::size_t index = 0;
    for (; index + 8 <= count; index += 8)
    {
        const __m256i widened =
          _mm256_cvtepi8_epi32(_mm_loadl_epi64(reinterpret_cast<const __m128i*>(source + index)));
        _mm256_storeu_si256(
          reinterpret_cast<__m256i*>(destination + index),
          _mm256_add_epi32(
            _mm256_loadu_si256(reinterpret_cast<const __m256i*>(destination + index)), widened));
    }
    add_i8_scalar(destination + index, source + index, count - index);
}

#endif

bool simd_isa_available(SimdIsa isa) noexcept {
    switch (isa)
    {
    case SimdIsa::Scalar :
        return true;
    case SimdIsa::Sse41 :
#if defined(USE_SSE41) || defined(USE_AVX2) || defined(USE_AVX512)
        return true;
#else
        return false;
#endif
    case SimdIsa::Avx2 :
#if defined(USE_AVX2) || defined(USE_AVX512)
        return true;
#else
        return false;
#endif
    }
    return false;
}

SimdIsa maximum_simd_isa() noexcept {
#if defined(USE_AVX2) || defined(USE_AVX512)
    return SimdIsa::Avx2;
#elif defined(USE_SSE41)
    return SimdIsa::Sse41;
#else
    return SimdIsa::Scalar;
#endif
}

const char* simd_isa_name(SimdIsa isa) noexcept {
    switch (isa)
    {
    case SimdIsa::Scalar :
        return "scalar";
    case SimdIsa::Sse41 :
        return "sse41";
    case SimdIsa::Avx2 :
        return "avx2";
    }
    return "unknown";
}

const char* simd_error_message(SimdError error) noexcept {
    switch (error)
    {
    case SimdError::None :
        return "none";
    case SimdError::UnsupportedIsa :
        return "requested SIMD ISA is not compiled into this binary";
    case SimdError::FeatureOracleError :
        return "feature oracle rejected the position";
    case SimdError::InvalidFeatureIndex :
        return "feature emission escaped its parameter tensor";
    case SimdError::FeatureAccumulatorOutOfRange :
        return "feature accumulator escaped the proved i32 envelope";
    case SimdError::PsqtAccumulatorOutOfRange :
        return "HM PSQT accumulator escaped the proved i32 envelope";
    case SimdError::ScalarCompositionError :
        return "scalar transform, PSQT or dense completion failed";
    }
    return "unknown Atomic V3 SIMD error";
}

SimdStatus evaluate_simd(const Network&             network,
                         const CapturePairSnapshot& snapshot,
                         SimdIsa                    requestedIsa,
                         SimdScratch&               scratch,
                         SimdDiagnostic&            result) {
    return evaluate_impl(network, snapshot, requestedIsa, scratch, result);
}

SimdStatus evaluate_simd(const Network&  network,
                         const Position& position,
                         SimdIsa         requestedIsa,
                         SimdScratch&    scratch,
                         SimdDiagnostic& result) {
    return evaluate_impl(network, make_capture_pair_snapshot(position), requestedIsa, scratch,
                         result);
}

}  // namespace Stockfish::Eval::NNUE::AtomicV3

#undef ATOMIC_V3_NOINLINE
