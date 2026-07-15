/*
  Atomic-Stockfish, a UCI chess playing engine derived from Stockfish
  Copyright (C) 2004-2026 The Stockfish developers (see AUTHORS file)

  Atomic-Stockfish is free software: you can redistribute it and/or modify
  it under the terms of the GNU General Public License as published by
  the Free Software Foundation, either version 3 of the License, or
  (at your option) any later version.
*/

#include "simd_isa.h"

namespace Stockfish::Eval::NNUE::AtomicV3 {

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

}  // namespace Stockfish::Eval::NNUE::AtomicV3
