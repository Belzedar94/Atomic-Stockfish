/*
  Atomic-Stockfish, a UCI chess playing engine derived from Stockfish
  Copyright (C) 2004-2026 The Stockfish developers (see AUTHORS file)

  Atomic-Stockfish is free software: you can redistribute it and/or modify
  it under the terms of the GNU General Public License as published by
  the Free Software Foundation, either version 3 of the License, or
  (at your option) any later version.
*/

#ifndef ATOMIC_V3_SIMD_BACKEND_H_INCLUDED
#define ATOMIC_V3_SIMD_BACKEND_H_INCLUDED

#include <array>
#include <cstddef>

#include "scalar_backend.h"

namespace Stockfish {
class Position;
}

namespace Stockfish::Eval::NNUE::AtomicV3 {

// H9.3j-a vectorizes only signed feature-row widening into canonical i32
// accumulators. PSQT, the feature transform and the dense stack deliberately
// remain on the frozen H9.3h scalar path.
enum class SimdIsa : u8 {
    Scalar,
    Sse41,
    Avx2
};

enum class SimdError : u8 {
    None,
    UnsupportedIsa,
    FeatureOracleError,
    InvalidFeatureIndex,
    FeatureAccumulatorOutOfRange,
    PsqtAccumulatorOutOfRange,
    ScalarCompositionError
};

struct SimdStatus {
    SimdError    error = SimdError::None;
    ScalarStatus scalarStatus{};

    explicit constexpr operator bool() const noexcept { return error == SimdError::None; }
};

struct SimdCounters {
    u64 biasI16Rows        = 0;
    u64 hmI16Rows          = 0;
    u64 capturePairI8Rows  = 0;
    u64 kingBlastEpI16Rows = 0;
    u64 blastRingI8Rows    = 0;
    u64 i16Lanes           = 0;
    u64 i8Lanes            = 0;
    u64 scalarKernelCalls  = 0;
    u64 sse41KernelCalls   = 0;
    u64 avx2KernelCalls    = 0;

    [[nodiscard]] constexpr u64 i16_rows() const noexcept {
        return biasI16Rows + hmI16Rows + kingBlastEpI16Rows;
    }
    [[nodiscard]] constexpr u64 i8_rows() const noexcept {
        return capturePairI8Rows + blastRingI8Rows;
    }
    [[nodiscard]] constexpr u64 kernel_calls() const noexcept {
        return scalarKernelCalls + sse41KernelCalls + avx2KernelCalls;
    }
    [[nodiscard]] constexpr u64 fallback_calls(SimdIsa requestedIsa) const noexcept {
        return requestedIsa == SimdIsa::Scalar ? 0 : scalarKernelCalls;
    }
};

// One scratch object belongs to one caller/thread. It is never retained by the
// immutable Network and its contents are unspecified after evaluate_simd().
struct alignas(64) SimdScratch {
    std::array<i32, AccumulatorDimensions> internalAccumulator{};
};

struct SimdDiagnostic {
    ScalarDiagnostic scalar{};
    SimdIsa          requestedIsa = SimdIsa::Scalar;
    SimdIsa          executedIsa  = SimdIsa::Scalar;
    SimdCounters     counters{};
};

[[nodiscard]] bool    simd_isa_available(SimdIsa isa) noexcept;
[[nodiscard]] SimdIsa maximum_simd_isa() noexcept;
const char*           simd_isa_name(SimdIsa isa) noexcept;
const char*           simd_error_message(SimdError error) noexcept;

// Both entry points execute exactly requestedIsa or fail closed. Result is
// reset on every failure; no partial accumulator or counter state is exposed.
[[nodiscard]] SimdStatus evaluate_simd(const Network&             network,
                                       const CapturePairSnapshot& snapshot,
                                       SimdIsa                    requestedIsa,
                                       SimdScratch&               scratch,
                                       SimdDiagnostic&            result);

[[nodiscard]] SimdStatus evaluate_simd(const Network&  network,
                                       const Position& position,
                                       SimdIsa         requestedIsa,
                                       SimdScratch&    scratch,
                                       SimdDiagnostic& result);

// Stable, non-inlined symbols used by the H9.3j objdump gate. Each function
// widens signed source lanes and adds them to i32 destination lanes.
#if defined(USE_SSE41) || defined(USE_AVX2) || defined(USE_AVX512)
extern "C" void
atomic_v3_add_i16_sse41_kernel(i32* destination, const i16* source, std::size_t count) noexcept;
extern "C" void
atomic_v3_add_i8_sse41_kernel(i32* destination, const i8* source, std::size_t count) noexcept;
#endif

#if defined(USE_AVX2) || defined(USE_AVX512)
extern "C" void
atomic_v3_add_i16_avx2_kernel(i32* destination, const i16* source, std::size_t count) noexcept;
extern "C" void
atomic_v3_add_i8_avx2_kernel(i32* destination, const i8* source, std::size_t count) noexcept;
#endif

static_assert(AccumulatorDimensions % 16 == 0);

}  // namespace Stockfish::Eval::NNUE::AtomicV3

#endif  // ATOMIC_V3_SIMD_BACKEND_H_INCLUDED
