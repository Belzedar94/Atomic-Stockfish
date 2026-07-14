/*
  Stockfish, a UCI chess playing engine derived from Glaurung 2.1
  Copyright (C) 2004-2026 The Stockfish developers (see AUTHORS file)

  Stockfish is free software: you can redistribute it and/or modify
  it under the terms of the GNU General Public License as published by
  the Free Software Foundation, either version 3 of the License, or
  (at your option) any later version.
*/

#ifndef ATOMIC_V2_NNZ_HELPER_H_INCLUDED
#define ATOMIC_V2_NNZ_HELPER_H_INCLUDED

#include <array>
#include <cstring>

#include "../nnue_common.h"
#include "../simd.h"

namespace Stockfish::Eval::NNUE::AtomicV2 {

// Tracks non-zero groups of four transformed bytes for the sparse first
// affine layer. The layout is the SFNNv15 layout; the generic record path is
// deliberately independent of the vec_nnz helpers removed from Legacy V1.
template<usize Dimensions>
struct NNZInfo {
#if defined(USE_AVX512)
    unsigned count = 0;
    u16      nnz[Dimensions / 4]{};

    #ifdef USE_AVX512ICL
    alignas(64) static constexpr auto Indices = []() {
        std::array<std::array<u16, 32>, 2> indices{};
        for (int p = 0; p < 2; ++p)
        {
            indices[p] = {0, 1, 2,  3,  16, 17, 18, 19, 4,  5,  6,  7,  20, 21, 22, 23,
                          8, 9, 10, 11, 24, 25, 26, 27, 12, 13, 14, 15, 28, 29, 30, 31};
            for (u16& index : indices[p])
                index += p * Dimensions / 8;
        }
        return indices;
    }();
    #else
    alignas(64) static constexpr auto Indices = []() {
        std::array<std::array<u32, 16>, 2> indices{};
        for (int p = 0; p < 2; ++p)
        {
            indices[p] = {0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15};
            for (u32& index : indices[p])
                index += p * Dimensions / 8;
        }
        return indices;
    }();
    #endif

    struct Cursor {
        NNZInfo& info;
        __m512i  indices;
        unsigned count;

        Cursor(NNZInfo& target, bool perspective) :
            info(target),
            indices(_mm512_load_si512(&Indices[perspective])),
            count(target.count) {}

        void record2(SIMD::vec_t first, SIMD::vec_t second) {
    #ifdef USE_AVX512ICL
            const __m512i   packed = _mm512_packs_epi32(first, second);
            const __mmask32 mask   = _mm512_test_epi16_mask(packed, packed);
            const __m512i   found  = _mm512_maskz_compress_epi16(mask, indices);
            _mm512_storeu_si512(info.nnz + count, found);
            count += popcount(mask);
            indices = _mm512_add_epi16(indices, _mm512_set1_epi16(32));
    #else
            for (const auto neurons : {first, second})
            {
                const __mmask16 mask  = _mm512_test_epi32_mask(neurons, neurons);
                const __m512i   found = _mm512_maskz_compress_epi32(mask, indices);
                _mm512_mask_cvtepi32_storeu_epi16(info.nnz + count, 0xFFFF, found);
                count += popcount(mask);
                indices = _mm512_add_epi32(indices, _mm512_set1_epi32(16));
            }
    #endif
        }

        ~Cursor() { info.count = count; }
    };

    Cursor make_cursor(bool perspective) { return {*this, perspective}; }
#else
    alignas(8) u8 bitset[(Dimensions + 31) / 32]{};

    struct Cursor {
        u8* out;

        Cursor(NNZInfo& info, bool perspective) :
            out(info.bitset + perspective * Dimensions / 64) {}

    #if defined(VECTOR)
        void record2(SIMD::vec_t first, SIMD::vec_t second) {
            constexpr usize VectorBytes = sizeof(SIMD::vec_t);
            constexpr usize Groups      = VectorBytes * 2 / 4;
            constexpr usize MaskBytes   = (Groups + 7) / 8;

            alignas(MaxSimdWidth) std::array<u8, VectorBytes * 2> bytes{};
            std::memcpy(bytes.data(), &first, VectorBytes);
            std::memcpy(bytes.data() + VectorBytes, &second, VectorBytes);

            std::array<u8, MaskBytes> mask{};
            for (usize group = 0; group < Groups; ++group)
            {
                bool nonzero = false;
                for (usize byte = 0; byte < 4; ++byte)
                    nonzero |= bytes[group * 4 + byte] != 0;
                mask[group / 8] |= static_cast<u8>(nonzero) << (group % 8);
            }

            std::memcpy(out, mask.data(), mask.size());
            out += mask.size();
        }
    #endif
    };

    Cursor make_cursor(bool perspective) { return {*this, perspective}; }
#endif
};

}  // namespace Stockfish::Eval::NNUE::AtomicV2

#endif  // ATOMIC_V2_NNZ_HELPER_H_INCLUDED
