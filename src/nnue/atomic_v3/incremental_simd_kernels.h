/*
  Atomic-Stockfish, a UCI chess playing engine derived from Stockfish
  Copyright (C) 2004-2026 The Stockfish developers (see AUTHORS file)

  Atomic-Stockfish is free software: you can redistribute it and/or modify
  it under the terms of the GNU General Public License as published by
  the Free Software Foundation, either version 3 of the License, or
  (at your option) any later version.
*/

#ifndef ATOMIC_V3_INCREMENTAL_SIMD_KERNELS_H_INCLUDED
#define ATOMIC_V3_INCREMENTAL_SIMD_KERNELS_H_INCLUDED

#include <cstddef>

#include "simd_isa.h"

namespace Stockfish::Eval::NNUE::AtomicV3 {

enum class HmDeltaOperation : u8 {
    Add,
    Remove
};

struct HmDeltaKernelResult {
    bool    applied     = false;
    SimdIsa executedIsa = SimdIsa::Scalar;

    explicit constexpr operator bool() const noexcept { return applied; }
};

// Apply exactly the requested execution policy to signed i16 weights and an
// i64 accumulator. A rejected result means invalid input or an ISA unavailable
// in this binary. The successful result is reported by the kernel that actually
// ran, so callers never infer execution identity from the request.
[[nodiscard]] HmDeltaKernelResult apply_hm_delta_kernel(SimdIsa          isa,
                                                        HmDeltaOperation operation,
                                                        i64*             destination,
                                                        const i16*       source,
                                                        std::size_t      count) noexcept;

#if defined(USE_SSE41) || defined(USE_AVX2) || defined(USE_AVX512)
extern "C" SimdIsa
atomic_v3_add_i16_i64_sse41_kernel(i64* destination, const i16* source, std::size_t count) noexcept;
extern "C" SimdIsa
atomic_v3_sub_i16_i64_sse41_kernel(i64* destination, const i16* source, std::size_t count) noexcept;
#endif

#if defined(USE_AVX2) || defined(USE_AVX512)
extern "C" SimdIsa
atomic_v3_add_i16_i64_avx2_kernel(i64* destination, const i16* source, std::size_t count) noexcept;
extern "C" SimdIsa
atomic_v3_sub_i16_i64_avx2_kernel(i64* destination, const i16* source, std::size_t count) noexcept;
#endif

}  // namespace Stockfish::Eval::NNUE::AtomicV3

#endif  // ATOMIC_V3_INCREMENTAL_SIMD_KERNELS_H_INCLUDED
