/*
  Atomic-Stockfish, a UCI chess playing engine derived from Stockfish
  Copyright (C) 2004-2026 The Stockfish developers (see AUTHORS file)

  Atomic-Stockfish is free software: you can redistribute it and/or modify
  it under the terms of the GNU General Public License as published by
  the Free Software Foundation, either version 3 of the License, or
  (at your option) any later version.
*/

#include "incremental_simd_kernels.h"

#include <cstring>

#if defined(USE_AVX2) || defined(USE_AVX512)
    #include <immintrin.h>
#elif defined(USE_SSE41)
    #include <smmintrin.h>
#endif

#if defined(_MSC_VER)
    #define ATOMIC_V3_INCREMENTAL_NOINLINE __declspec(noinline)
#elif defined(__GNUC__) || defined(__clang__)
    #define ATOMIC_V3_INCREMENTAL_NOINLINE __attribute__((noinline, used))
#else
    #define ATOMIC_V3_INCREMENTAL_NOINLINE
#endif

namespace Stockfish::Eval::NNUE::AtomicV3 {
namespace {

void store_wrapped_i64(i64& destination, u64 bits) noexcept {
    static_assert(sizeof(destination) == sizeof(bits));
    std::memcpy(&destination, &bits, sizeof(bits));
}

void add_i16_i64_scalar(i64* destination, const i16* source, std::size_t count) noexcept {
    for (std::size_t index = 0; index < count; ++index)
        store_wrapped_i64(destination[index],
                          static_cast<u64>(destination[index])
                            + static_cast<u64>(static_cast<i64>(source[index])));
}

void sub_i16_i64_scalar(i64* destination, const i16* source, std::size_t count) noexcept {
    for (std::size_t index = 0; index < count; ++index)
        store_wrapped_i64(destination[index],
                          static_cast<u64>(destination[index])
                            - static_cast<u64>(static_cast<i64>(source[index])));
}

}  // namespace

#if defined(USE_SSE41) || defined(USE_AVX2) || defined(USE_AVX512)

extern "C" ATOMIC_V3_INCREMENTAL_NOINLINE SimdIsa atomic_v3_add_i16_i64_sse41_kernel(
  i64* destination, const i16* source, std::size_t count) noexcept {
    std::size_t index = 0;
    for (; index + 4 <= count; index += 4)
    {
        const __m128i packed = _mm_loadl_epi64(reinterpret_cast<const __m128i*>(source + index));
        const __m128i wide32 = _mm_cvtepi16_epi32(packed);
        const __m128i low64  = _mm_cvtepi32_epi64(wide32);
        const __m128i high64 = _mm_cvtepi32_epi64(_mm_srli_si128(wide32, 8));
        _mm_storeu_si128(
          reinterpret_cast<__m128i*>(destination + index),
          _mm_add_epi64(_mm_loadu_si128(reinterpret_cast<const __m128i*>(destination + index)),
                        low64));
        _mm_storeu_si128(
          reinterpret_cast<__m128i*>(destination + index + 2),
          _mm_add_epi64(_mm_loadu_si128(reinterpret_cast<const __m128i*>(destination + index + 2)),
                        high64));
    }
    add_i16_i64_scalar(destination + index, source + index, count - index);
    return SimdIsa::Sse41;
}

extern "C" ATOMIC_V3_INCREMENTAL_NOINLINE SimdIsa atomic_v3_sub_i16_i64_sse41_kernel(
  i64* destination, const i16* source, std::size_t count) noexcept {
    std::size_t index = 0;
    for (; index + 4 <= count; index += 4)
    {
        const __m128i packed = _mm_loadl_epi64(reinterpret_cast<const __m128i*>(source + index));
        const __m128i wide32 = _mm_cvtepi16_epi32(packed);
        const __m128i low64  = _mm_cvtepi32_epi64(wide32);
        const __m128i high64 = _mm_cvtepi32_epi64(_mm_srli_si128(wide32, 8));
        _mm_storeu_si128(
          reinterpret_cast<__m128i*>(destination + index),
          _mm_sub_epi64(_mm_loadu_si128(reinterpret_cast<const __m128i*>(destination + index)),
                        low64));
        _mm_storeu_si128(
          reinterpret_cast<__m128i*>(destination + index + 2),
          _mm_sub_epi64(_mm_loadu_si128(reinterpret_cast<const __m128i*>(destination + index + 2)),
                        high64));
    }
    sub_i16_i64_scalar(destination + index, source + index, count - index);
    return SimdIsa::Sse41;
}

#endif

#if defined(USE_AVX2) || defined(USE_AVX512)

extern "C" ATOMIC_V3_INCREMENTAL_NOINLINE SimdIsa
atomic_v3_add_i16_i64_avx2_kernel(i64* destination, const i16* source, std::size_t count) noexcept {
    std::size_t index = 0;
    for (; index + 8 <= count; index += 8)
    {
        const __m256i wide32 =
          _mm256_cvtepi16_epi32(_mm_loadu_si128(reinterpret_cast<const __m128i*>(source + index)));
        const __m256i low64  = _mm256_cvtepi32_epi64(_mm256_castsi256_si128(wide32));
        const __m256i high64 = _mm256_cvtepi32_epi64(_mm256_extracti128_si256(wide32, 1));
        _mm256_storeu_si256(
          reinterpret_cast<__m256i*>(destination + index),
          _mm256_add_epi64(
            _mm256_loadu_si256(reinterpret_cast<const __m256i*>(destination + index)), low64));
        _mm256_storeu_si256(
          reinterpret_cast<__m256i*>(destination + index + 4),
          _mm256_add_epi64(
            _mm256_loadu_si256(reinterpret_cast<const __m256i*>(destination + index + 4)), high64));
    }
    add_i16_i64_scalar(destination + index, source + index, count - index);
    return SimdIsa::Avx2;
}

extern "C" ATOMIC_V3_INCREMENTAL_NOINLINE SimdIsa
atomic_v3_sub_i16_i64_avx2_kernel(i64* destination, const i16* source, std::size_t count) noexcept {
    std::size_t index = 0;
    for (; index + 8 <= count; index += 8)
    {
        const __m256i wide32 =
          _mm256_cvtepi16_epi32(_mm_loadu_si128(reinterpret_cast<const __m128i*>(source + index)));
        const __m256i low64  = _mm256_cvtepi32_epi64(_mm256_castsi256_si128(wide32));
        const __m256i high64 = _mm256_cvtepi32_epi64(_mm256_extracti128_si256(wide32, 1));
        _mm256_storeu_si256(
          reinterpret_cast<__m256i*>(destination + index),
          _mm256_sub_epi64(
            _mm256_loadu_si256(reinterpret_cast<const __m256i*>(destination + index)), low64));
        _mm256_storeu_si256(
          reinterpret_cast<__m256i*>(destination + index + 4),
          _mm256_sub_epi64(
            _mm256_loadu_si256(reinterpret_cast<const __m256i*>(destination + index + 4)), high64));
    }
    sub_i16_i64_scalar(destination + index, source + index, count - index);
    return SimdIsa::Avx2;
}

#endif

HmDeltaKernelResult apply_hm_delta_kernel(SimdIsa          isa,
                                          HmDeltaOperation operation,
                                          i64*             destination,
                                          const i16*       source,
                                          std::size_t      count) noexcept {
    if (!destination || !source || !simd_isa_available(isa)
        || (operation != HmDeltaOperation::Add && operation != HmDeltaOperation::Remove))
        return {};

    switch (isa)
    {
    case SimdIsa::Scalar :
        if (operation == HmDeltaOperation::Add)
            add_i16_i64_scalar(destination, source, count);
        else
            sub_i16_i64_scalar(destination, source, count);
        return {true, SimdIsa::Scalar};
    case SimdIsa::Sse41 :
#if defined(USE_SSE41) || defined(USE_AVX2) || defined(USE_AVX512)
        return {true, operation == HmDeltaOperation::Add
                        ? atomic_v3_add_i16_i64_sse41_kernel(destination, source, count)
                        : atomic_v3_sub_i16_i64_sse41_kernel(destination, source, count)};
#else
        return {};
#endif
    case SimdIsa::Avx2 :
#if defined(USE_AVX2) || defined(USE_AVX512)
        return {true, operation == HmDeltaOperation::Add
                        ? atomic_v3_add_i16_i64_avx2_kernel(destination, source, count)
                        : atomic_v3_sub_i16_i64_avx2_kernel(destination, source, count)};
#else
        return {};
#endif
    }
    return {};
}

}  // namespace Stockfish::Eval::NNUE::AtomicV3

#undef ATOMIC_V3_INCREMENTAL_NOINLINE
