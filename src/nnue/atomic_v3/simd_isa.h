/*
  Atomic-Stockfish, a UCI chess playing engine derived from Stockfish
  Copyright (C) 2004-2026 The Stockfish developers (see AUTHORS file)

  Atomic-Stockfish is free software: you can redistribute it and/or modify
  it under the terms of the GNU General Public License as published by
  the Free Software Foundation, either version 3 of the License, or
  (at your option) any later version.
*/

#ifndef ATOMIC_V3_SIMD_ISA_H_INCLUDED
#define ATOMIC_V3_SIMD_ISA_H_INCLUDED

#include "../../types.h"

namespace Stockfish::Eval::NNUE::AtomicV3 {

// Private execution policy shared by the isolated full-refresh and
// incremental SIMD gates. An unavailable exact request always fails closed;
// production dispatch is deliberately outside this layer.
enum class SimdIsa : u8 {
    Scalar,
    Sse41,
    Avx2
};

[[nodiscard]] bool    simd_isa_available(SimdIsa isa) noexcept;
[[nodiscard]] SimdIsa maximum_simd_isa() noexcept;
const char*           simd_isa_name(SimdIsa isa) noexcept;

}  // namespace Stockfish::Eval::NNUE::AtomicV3

#endif  // ATOMIC_V3_SIMD_ISA_H_INCLUDED
