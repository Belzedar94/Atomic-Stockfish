/*
  Atomic-Stockfish, a specialized Atomic Chess engine derived from Stockfish
  Copyright (C) 2004-2026 The Stockfish developers (see AUTHORS file)

  Atomic-Stockfish is free software: you can redistribute it and/or modify
  it under the terms of the GNU General Public License as published by
  the Free Software Foundation, either version 3 of the License, or
  (at your option) any later version.
*/

#include "sf_network.h"

#include <algorithm>
#include <cstring>
#include <istream>
#include <limits>
#include <memory>
#include <ostream>

#include "../../bitboard.h"
#include "../../evaluate.h"
#include "../../position.h"
#include "../atomic_v2/feature_transformer.h"
#include "../atomic_v2/io.h"
#include "../nnue_misc.h"
#include "../simd.h"

namespace Stockfish::Eval::NNUE::AtomicSF {

using namespace SIMD;
namespace IO = AtomicV2::IO;

namespace {

constexpr u32 MaxDescriptionSize = 1U << 20;

constexpr auto PackusEpi16Order        = AtomicV2::FeatureTransformer::PackusEpi16Order;
constexpr auto InversePackusEpi16Order = AtomicV2::FeatureTransformer::InversePackusEpi16Order;

using PsqActiveList  = HalfKAv2Hm::ActiveIndexList;
using ThreatList     = FullThreats::IndexList;

// Add/subtract feature rows of both blocks onto an accumulator. Mirrors
// upstream apply_combined()/update_accumulator_refresh_cache().
template<bool Threats, typename PsqRemoved, typename PsqAdded>
void apply_delta(const FeatureTransformer<Threats>&  ft,
                 const std::array<i16, L1>&          sourceAccumulation,
                 const std::array<i32, PSQTBuckets>& sourcePsqt,
                 std::array<i16, L1>&                targetAccumulation,
                 std::array<i32, PSQTBuckets>&       targetPsqt,
                 const PsqRemoved&                   psqRemoved,
                 const PsqAdded&                     psqAdded,
                 const ThreatList*                   thrRemoved,
                 const ThreatList*                   thrAdded) noexcept {
    constexpr IndexType Dimensions = L1;

#ifdef VECTOR
    using Tiling = SIMDTiling<Dimensions, Dimensions, PSQTBuckets>;

    vec_t      acc[Tiling::NumRegs];
    psqt_vec_t psqt[Tiling::NumPsqtRegs];

    const auto* psqWeights = &ft.weights[0];

    for (IndexType j = 0; j < Dimensions / Tiling::TileHeight; ++j)
    {
        const usize tileOff  = j * Tiling::TileHeight;
        auto*       fromTile = reinterpret_cast<const vec_t*>(&sourceAccumulation[tileOff]);
        auto*       toTile   = reinterpret_cast<vec_t*>(&targetAccumulation[tileOff]);

        for (IndexType k = 0; k < Tiling::NumRegs; ++k)
            acc[k] = fromTile[k];

        for (const IndexType index : psqRemoved)
        {
            auto* row = reinterpret_cast<const vec_t*>(&psqWeights[index * Dimensions + tileOff]);
            for (IndexType k = 0; k < Tiling::NumRegs; ++k)
                acc[k] = vec_sub_16(acc[k], row[k]);
        }

        for (const IndexType index : psqAdded)
        {
            auto* row = reinterpret_cast<const vec_t*>(&psqWeights[index * Dimensions + tileOff]);
            for (IndexType k = 0; k < Tiling::NumRegs; ++k)
                acc[k] = vec_add_16(acc[k], row[k]);
        }

        if constexpr (Threats)
        {
            const auto* threatWeights = &ft.threatWeights[0];

            if (thrRemoved)
                for (const IndexType index : *thrRemoved)
                {
                    auto* column = reinterpret_cast<const vec_i8_t*>(
                      &threatWeights[index * Dimensions + tileOff]);
    #ifdef USE_NEON
                    for (IndexType k = 0; k < Tiling::NumRegs; k += 2)
                    {
                        acc[k]     = vsubw_s8(acc[k], vget_low_s8(column[k / 2]));
                        acc[k + 1] = vsubw_high_s8(acc[k + 1], column[k / 2]);
                    }
    #else
                    for (IndexType k = 0; k < Tiling::NumRegs; ++k)
                        acc[k] = vec_sub_16(acc[k], vec_convert_8_16(column[k]));
    #endif
                }

            if (thrAdded)
                for (const IndexType index : *thrAdded)
                {
                    auto* column = reinterpret_cast<const vec_i8_t*>(
                      &threatWeights[index * Dimensions + tileOff]);
    #ifdef USE_NEON
                    for (IndexType k = 0; k < Tiling::NumRegs; k += 2)
                    {
                        acc[k]     = vaddw_s8(acc[k], vget_low_s8(column[k / 2]));
                        acc[k + 1] = vaddw_high_s8(acc[k + 1], column[k / 2]);
                    }
    #else
                    for (IndexType k = 0; k < Tiling::NumRegs; ++k)
                        acc[k] = vec_add_16(acc[k], vec_convert_8_16(column[k]));
    #endif
                }
        }

        for (IndexType k = 0; k < Tiling::NumRegs; k++)
            vec_store(&toTile[k], acc[k]);
    }

    for (IndexType j = 0; j < PSQTBuckets / Tiling::PsqtTileHeight; ++j)
    {
        const usize psqtTileOff  = j * Tiling::PsqtTileHeight;
        auto*       fromTilePsqt = reinterpret_cast<const psqt_vec_t*>(&sourcePsqt[psqtTileOff]);
        auto*       toTilePsqt   = reinterpret_cast<psqt_vec_t*>(&targetPsqt[psqtTileOff]);

        for (IndexType k = 0; k < Tiling::NumPsqtRegs; ++k)
            psqt[k] = fromTilePsqt[k];

        for (const IndexType index : psqRemoved)
        {
            auto* col = reinterpret_cast<const psqt_vec_t*>(
              &ft.psqtWeights[index * PSQTBuckets + psqtTileOff]);
            for (IndexType k = 0; k < Tiling::NumPsqtRegs; ++k)
                psqt[k] = vec_sub_psqt_32(psqt[k], col[k]);
        }

        for (const IndexType index : psqAdded)
        {
            auto* col = reinterpret_cast<const psqt_vec_t*>(
              &ft.psqtWeights[index * PSQTBuckets + psqtTileOff]);
            for (IndexType k = 0; k < Tiling::NumPsqtRegs; ++k)
                psqt[k] = vec_add_psqt_32(psqt[k], col[k]);
        }

        if constexpr (Threats)
        {
            if (thrRemoved)
                for (const IndexType index : *thrRemoved)
                {
                    auto* col = reinterpret_cast<const psqt_vec_t*>(
                      &ft.threatPsqtWeights[index * PSQTBuckets + psqtTileOff]);
                    for (IndexType k = 0; k < Tiling::NumPsqtRegs; ++k)
                        psqt[k] = vec_sub_psqt_32(psqt[k], col[k]);
                }

            if (thrAdded)
                for (const IndexType index : *thrAdded)
                {
                    auto* col = reinterpret_cast<const psqt_vec_t*>(
                      &ft.threatPsqtWeights[index * PSQTBuckets + psqtTileOff]);
                    for (IndexType k = 0; k < Tiling::NumPsqtRegs; ++k)
                        psqt[k] = vec_add_psqt_32(psqt[k], col[k]);
                }
        }

        for (IndexType k = 0; k < Tiling::NumPsqtRegs; ++k)
            vec_store_psqt(&toTilePsqt[k], psqt[k]);
    }

#else

    if (&targetAccumulation != &sourceAccumulation)
        targetAccumulation = sourceAccumulation;
    if (&targetPsqt != &sourcePsqt)
        targetPsqt = sourcePsqt;

    auto apply16 = [&](IndexType index, int sign) {
        const IndexType offset = Dimensions * index;
        for (IndexType j = 0; j < Dimensions; ++j)
            targetAccumulation[j] =
              static_cast<i16>(targetAccumulation[j] + sign * ft.weights[offset + j]);
        for (usize k = 0; k < PSQTBuckets; ++k)
            targetPsqt[k] += sign * ft.psqtWeights[index * PSQTBuckets + k];
    };

    for (const auto index : psqRemoved)
        apply16(index, -1);
    for (const auto index : psqAdded)
        apply16(index, 1);

    if constexpr (Threats)
    {
        auto apply8 = [&](IndexType index, int sign) {
            const IndexType offset = Dimensions * index;
            for (IndexType j = 0; j < Dimensions; ++j)
                targetAccumulation[j] =
                  static_cast<i16>(targetAccumulation[j] + sign * ft.threatWeights[offset + j]);
            for (usize k = 0; k < PSQTBuckets; ++k)
                targetPsqt[k] += sign * ft.threatPsqtWeights[index * PSQTBuckets + k];
        };

        if (thrRemoved)
            for (const auto index : *thrRemoved)
                apply8(index, -1);
        if (thrAdded)
            for (const auto index : *thrAdded)
                apply8(index, 1);
    }
#endif
}

Bitboard changed_pieces(const std::array<Piece, SQUARE_NB>& oldPieces,
                        const std::array<Piece, SQUARE_NB>& newPieces) {
#if defined(USE_AVX2)
    static_assert(sizeof(Piece) == 1);
    Bitboard sameBB = 0;
    for (int i = 0; i < 64; i += 32)
    {
        const __m256i old_v = _mm256_loadu_si256(reinterpret_cast<const __m256i*>(&oldPieces[i]));
        const __m256i new_v = _mm256_loadu_si256(reinterpret_cast<const __m256i*>(&newPieces[i]));
        const __m256i cmpEqual  = _mm256_cmpeq_epi8(old_v, new_v);
        const u32     equalMask = _mm256_movemask_epi8(cmpEqual);
        sameBB |= static_cast<Bitboard>(equalMask) << i;
    }
    return ~sameBB;
#elif defined(USE_SSE2)
    Bitboard sameBB = 0;
    for (int i = 0; i < 64; i += 16)
    {
        const __m128i old_v = _mm_loadu_si128(reinterpret_cast<const __m128i*>(&oldPieces[i]));
        const __m128i new_v = _mm_loadu_si128(reinterpret_cast<const __m128i*>(&newPieces[i]));
        const __m128i same  = _mm_cmpeq_epi8(old_v, new_v);
        sameBB |= static_cast<Bitboard>(u16(_mm_movemask_epi8(same))) << i;
    }
    return ~sameBB;
#else
    Bitboard changed = 0;
    for (Square sq = SQ_A1; sq <= SQ_H8; ++sq)
        changed |= static_cast<Bitboard>(oldPieces[sq] != newPieces[sq]) << sq;
    return changed;
#endif
}

}  // namespace

// ---------------------------------------------------------------------------
// AccumulatorStack

template<bool Threats>
void AccumulatorStack<Threats>::reset() noexcept {
    accumulators[0].dirtyPiece   = {};
    accumulators[0].dirtyThreats = {};
    accumulators[0].computed.fill(false);
    size = 1;
}

template<bool Threats>
DirtyPiece& AccumulatorStack<Threats>::push() noexcept {
    assert(size < MaxSize);
    auto& target = accumulators[size++];
    target.computed.fill(false);
    if constexpr (Threats)
        target.dirtyThreats.refresh = true;  // until after_move() fills it
    return target.dirtyPiece;
}

template<bool Threats>
void AccumulatorStack<Threats>::pop() noexcept {
    assert(size > 1);
    --size;
}

template<bool Threats>
void AccumulatorStack<Threats>::after_move(const Position& pos) noexcept {
    if constexpr (Threats)
    {
        auto& st = mut_latest();
        compute_dirty_threats(pos, st.dirtyPiece, st.dirtyThreats);
    }
    else
        (void) pos;
}

template<bool Threats>
bool AccumulatorStack<Threats>::requires_refresh(const AccumulatorState& state,
                                                 Color                   perspective) noexcept {
    if constexpr (Threats)
        if (state.dirtyThreats.refresh)
            return true;
    return HalfKAv2Hm::requires_refresh(state.dirtyPiece, perspective);
}

template<bool Threats>
void AccumulatorStack<Threats>::evaluate(const Position&                    pos,
                                         const FeatureTransformer<Threats>& featureTransformer,
                                         AccumulatorCaches&                 cache) noexcept {
    evaluate_side(WHITE, pos, featureTransformer, cache);
    evaluate_side(BLACK, pos, featureTransformer, cache);
}

template<bool Threats>
void AccumulatorStack<Threats>::evaluate_side(Color                              perspective,
                                              const Position&                    pos,
                                              const FeatureTransformer<Threats>& featureTransformer,
                                              AccumulatorCaches&                 cache) noexcept {
    if (mut_latest().computed[perspective])
        return;

    usize base = size - 1;
    while (base > 0 && !accumulators[base].computed[perspective]
           && !requires_refresh(accumulators[base], perspective))
        --base;

    const Square kingSquare = feature_king_square(pos, perspective);

    if (!accumulators[base].computed[perspective])
    {
        refresh(perspective, pos, featureTransformer, mut_latest(), cache);

        // Walk back down to base: no move after base needs a refresh for this
        // perspective, so later evaluations below can start from there.
        for (usize current = size - 1; current > base; --current)
            update<false>(perspective, kingSquare, featureTransformer, accumulators[current],
                          accumulators[current - 1]);
        return;
    }

    for (usize current = base + 1; current < size; ++current)
        update<true>(perspective, kingSquare, featureTransformer, accumulators[current - 1],
                     accumulators[current]);
}

template<bool Threats>
void AccumulatorStack<Threats>::refresh(Color                              perspective,
                                        const Position&                    pos,
                                        const FeatureTransformer<Threats>& featureTransformer,
                                        AccumulatorState&                  target,
                                        AccumulatorCaches&                 cache) noexcept {
    const Square kingSquare = feature_king_square(pos, perspective);
    auto&        entry      = cache[kingSquare][perspective];

    PsqActiveList removed, added;

    const Bitboard changedBB = changed_pieces(entry.pieces, pos.piece_array());
    Bitboard       removedBB = changedBB & entry.pieceBB;
    Bitboard       addedBB   = changedBB & pos.pieces();

    while (removedBB)
    {
        const Square sq = pop_lsb(removedBB);
        removed.push_back(HalfKAv2Hm::make_index(perspective, sq, entry.pieces[sq], kingSquare));
    }
    while (addedBB)
    {
        const Square sq = pop_lsb(addedBB);
        added.push_back(HalfKAv2Hm::make_index(perspective, sq, pos.piece_on(sq), kingSquare));
    }

    // Update the Finny entry (HalfKAv2_hm only) in place.
    apply_delta<Threats>(featureTransformer, entry.accumulation, entry.psqtAccumulation,
                         entry.accumulation, entry.psqtAccumulation, removed, added, nullptr,
                         nullptr);
    entry.pieceBB = pos.pieces();
    entry.pieces  = pos.piece_array();

    if constexpr (Threats)
    {
        ThreatList active;
        FullThreats::append_active_indices(perspective, pos, active);
        const PsqActiveList none{};
        apply_delta<Threats>(featureTransformer, entry.accumulation, entry.psqtAccumulation,
                             target.accumulation[perspective],
                             target.psqtAccumulation[perspective], none, none, nullptr,
                             &active);
    }
    else
    {
        target.accumulation[perspective]     = entry.accumulation;
        target.psqtAccumulation[perspective] = entry.psqtAccumulation;
    }

    target.computed[perspective] = true;
}

// Forward: target.dirty* is the move source -> target. Backward: source.dirty*
// is the move target -> source, applied in reverse.
template<bool Threats>
template<bool Forward>
void AccumulatorStack<Threats>::update(Color                              perspective,
                                       Square                             kingSquare,
                                       const FeatureTransformer<Threats>& featureTransformer,
                                       const AccumulatorState&            source,
                                       AccumulatorState&                  target) noexcept {
    assert(source.computed[perspective] && !target.computed[perspective]);

    const AccumulatorState& moveState = Forward ? target : source;

    HalfKAv2Hm::RemovedIndexList psqRemoved;
    HalfKAv2Hm::AddedIndexList   psqAdded;
    HalfKAv2Hm::append_changed_indices(perspective, kingSquare, moveState.dirtyPiece, psqRemoved,
                                       psqAdded);

    if constexpr (Threats)
    {
        assert(!moveState.dirtyThreats.refresh);
        ThreatList thrRemoved, thrAdded;
        FullThreats::append_changed_indices(perspective, kingSquare, moveState.dirtyThreats,
                                            thrRemoved, thrAdded);
        if constexpr (Forward)
            apply_delta<Threats>(featureTransformer, source.accumulation[perspective],
                                 source.psqtAccumulation[perspective],
                                 target.accumulation[perspective],
                                 target.psqtAccumulation[perspective], psqRemoved, psqAdded,
                                 &thrRemoved, &thrAdded);
        else
            apply_delta<Threats>(featureTransformer, source.accumulation[perspective],
                                 source.psqtAccumulation[perspective],
                                 target.accumulation[perspective],
                                 target.psqtAccumulation[perspective], psqAdded, psqRemoved,
                                 &thrAdded, &thrRemoved);
    }
    else
    {
        if constexpr (Forward)
            apply_delta<Threats>(featureTransformer, source.accumulation[perspective],
                                 source.psqtAccumulation[perspective],
                                 target.accumulation[perspective],
                                 target.psqtAccumulation[perspective], psqRemoved, psqAdded,
                                 nullptr, nullptr);
        else
            apply_delta<Threats>(featureTransformer, source.accumulation[perspective],
                                 source.psqtAccumulation[perspective],
                                 target.accumulation[perspective],
                                 target.psqtAccumulation[perspective], psqAdded, psqRemoved,
                                 nullptr, nullptr);
    }

    target.computed[perspective] = true;
}

// ---------------------------------------------------------------------------
// FeatureTransformer

template<bool Threats>
void FeatureTransformer<Threats>::permute_weights() {
    AtomicV2::permute<16>(biases, PackusEpi16Order);
    AtomicV2::permute<16>(weights, PackusEpi16Order);
    if constexpr (Threats)
        AtomicV2::permute<8>(threatWeights, PackusEpi16Order);
}

template<bool Threats>
void FeatureTransformer<Threats>::unpermute_weights() {
    AtomicV2::permute<16>(biases, InversePackusEpi16Order);
    AtomicV2::permute<16>(weights, InversePackusEpi16Order);
    if constexpr (Threats)
        AtomicV2::permute<8>(threatWeights, InversePackusEpi16Order);
}

template<bool Threats>
bool FeatureTransformer<Threats>::read_parameters(std::istream& stream) {
    if (!IO::read_signed_leb(stream, biases))
        return false;

    if constexpr (Threats)
    {
        stream.read(reinterpret_cast<char*>(threatWeights.data()),
                    std::streamsize(threatWeights.size()));
        if (!stream || !IO::read_signed_leb(stream, threatPsqtWeights))
            return false;
    }

    if (!IO::read_signed_leb(stream, weights) || !IO::read_signed_leb(stream, psqtWeights))
        return false;

    permute_weights();
    return bool(stream);
}

template<bool Threats>
bool FeatureTransformer<Threats>::write_parameters(std::ostream& stream) const {
    auto copy = std::make_unique<FeatureTransformer>(*this);
    copy->unpermute_weights();

    if (!IO::write_signed_leb(stream, copy->biases))
        return false;

    if constexpr (Threats)
    {
        stream.write(reinterpret_cast<const char*>(copy->threatWeights.data()),
                     std::streamsize(copy->threatWeights.size()));
        if (!stream || !IO::write_signed_leb(stream, copy->threatPsqtWeights))
            return false;
    }

    return IO::write_signed_leb(stream, copy->weights)
        && IO::write_signed_leb(stream, copy->psqtWeights);
}

template<bool Threats>
usize FeatureTransformer<Threats>::get_content_hash() const {
    usize h = 0;
    hash_combine(h, get_raw_data_hash(biases));
    hash_combine(h, get_raw_data_hash(weights));
    hash_combine(h, get_raw_data_hash(psqtWeights));
    if constexpr (Threats)
    {
        hash_combine(h, get_raw_data_hash(threatWeights));
        hash_combine(h, get_raw_data_hash(threatPsqtWeights));
    }
    hash_combine(h, get_hash_value());
    return h;
}

template<bool Threats>
i32 FeatureTransformer<Threats>::transform(const Position&            pos,
                                           AccumulatorStack<Threats>& accumulatorStack,
                                           AccumulatorCaches&         cache,
                                           OutputType*                output,
                                           int                        bucket,
                                           AtomicV2::NNZInfo<L1>&     nnzInfo) const {
    accumulatorStack.evaluate(pos, *this, cache);
    const auto& accumulatorState = accumulatorStack.latest();

    const Color perspectives[2]  = {pos.side_to_move(), ~pos.side_to_move()};
    const auto& psqtAccumulation = accumulatorState.psqtAccumulation;
    const auto  psqt =
      (psqtAccumulation[perspectives[0]][bucket] - psqtAccumulation[perspectives[1]][bucket]) / 2;

    const auto& accumulation = accumulatorState.accumulation;

    for (IndexType p = 0; p < 2; ++p)
    {
        const IndexType offset = (HalfDimensions / 2) * p;

        [[maybe_unused]] auto cursor = nnzInfo.make_cursor(p);

#if defined(VECTOR)

        constexpr IndexType OutputChunkSize = MaxChunkSize;
        static_assert((HalfDimensions / 2) % OutputChunkSize == 0);
        constexpr IndexType NumOutputChunks = HalfDimensions / 2 / OutputChunkSize;

        [[maybe_unused]] const vec_t   Zero  = vec_zero();
        [[maybe_unused]] const vec_t   FtMax = vec_set_16(FtMaxVal);
        [[maybe_unused]] constexpr int shift = 7;

        const vec_t* in0 = reinterpret_cast<const vec_t*>(&(accumulation[perspectives[p]][0]));
        const vec_t* in1 =
          reinterpret_cast<const vec_t*>(&(accumulation[perspectives[p]][HalfDimensions / 2]));
        vec_t* out = reinterpret_cast<vec_t*>(output + offset);

        for (IndexType j = 0; j < NumOutputChunks; j += 2)
        {
            vec_t packed[2];
            for (IndexType k = 0; k < 2; ++k)
            {
                const IndexType i = (j + k) * 2;

                vec_t acc0a = in0[i + 0];
                vec_t acc0b = in0[i + 1];
                vec_t acc1a = in1[i + 0];
                vec_t acc1b = in1[i + 1];

                static_assert(FtMaxVal == 255);

    #if defined(USE_NEON)
                uint16x8_t mul0 = vmull_u8(vqmovun_s16(acc0a), vqmovun_s16(acc1a));
                uint16x8_t mul1 = vmull_u8(vqmovun_s16(acc0b), vqmovun_s16(acc1b));

                uint8x16x2_t uzp = vuzpq_u8(vreinterpretq_u8_u16(mul0), vreinterpretq_u8_u16(mul1));
                uint8x16_t   pab = vshrq_n_u8(uzp.val[1], 1);
                vec_t        result = reinterpret_cast<vec_t>(pab);
    #elif defined(USE_LSX) || defined(USE_LASX)
                vec_t pa = vec_packus_16(acc0a, acc0b);
                vec_t pb = vec_packus_16(acc1a, acc1b);

                vec_t hi     = vec_mulhi_8(pa, pb);
                vec_t result = vec_srli_8(hi, 1);
    #elif defined(__wasm__)
                vec_t mul0 = vec_packus_16(acc0a, acc0b);
                vec_t mul1 = vec_packus_16(acc1a, acc1b);

                vec_t low = wasm_u16x8_extmul_low_u8x16(mul0, mul1);
                vec_t hi  = wasm_u16x8_extmul_high_u8x16(mul0, mul1);

                vec_t merged = wasm_i8x16_shuffle(low, hi, 1, 3, 5, 7, 9, 11, 13, 15, 17, 19, 21,
                                                  23, 25, 27, 29, 31);
                vec_t result = wasm_u8x16_shr(merged, 1);
    #else
                vec_t sum0a = vec_slli_16(vec_max_16(vec_min_16(acc0a, FtMax), Zero), shift);
                vec_t sum0b = vec_slli_16(vec_max_16(vec_min_16(acc0b, FtMax), Zero), shift);
                vec_t sum1a = vec_min_16(acc1a, FtMax);
                vec_t sum1b = vec_min_16(acc1b, FtMax);

                vec_t pa = vec_mulhi_16(sum0a, sum1a);
                vec_t pb = vec_mulhi_16(sum0b, sum1b);

                vec_t result = vec_packus_16(pa, pb);
    #endif

                packed[k] = out[j + k] = result;
            }

            cursor.record2(packed[0], packed[1]);
        }

#else

        for (IndexType j = 0; j < HalfDimensions / 2; ++j)
        {
            BiasType sum0 = accumulation[static_cast<int>(perspectives[p])][j + 0];
            BiasType sum1 = accumulation[static_cast<int>(perspectives[p])][j + HalfDimensions / 2];

            sum0 = std::clamp<BiasType>(sum0, 0, FtMaxVal);
            sum1 = std::clamp<BiasType>(sum1, 0, FtMaxVal);

            output[offset + j] = static_cast<OutputType>(unsigned(sum0 * sum1) / 512);
        }

#endif
    }

    return psqt;
}

// ---------------------------------------------------------------------------
// Network

namespace {

int bucket_of(const Position& pos) {
    return std::clamp((pos.count<ALL_PIECES>() - 1) / 4, 0, int(LayerStacks) - 1);
}

}  // namespace

template<bool Threats>
RawNetworkOutput Network<Threats>::evaluate_raw(const Position&            pos,
                                                AccumulatorStack<Threats>& accumulatorStack,
                                                AccumulatorCaches&         cache) const {
    assert(initialized_);

    alignas(CacheLineSize)
      TransformedFeatureType transformed[FeatureTransformer<Threats>::BufferSize];

    AtomicV2::NNZInfo<L1> nnzInfo{};
    const int             bucket = bucket_of(pos);
    const i32             psqt =
      featureTransformer_.transform(pos, accumulatorStack, cache, transformed, bucket, nnzInfo);
    const i32 positional = networks_[bucket].propagate(transformed, nnzInfo);
    return {psqt, positional};
}

template<bool Threats>
NetworkOutput Network<Threats>::evaluate(const Position&            pos,
                                         AccumulatorStack<Threats>& accumulatorStack,
                                         AccumulatorCaches&         cache) const {
    const auto [psqt, positional] = evaluate_raw(pos, accumulatorStack, cache);
    return {static_cast<Value>(psqt / OutputScale), static_cast<Value>(positional / OutputScale)};
}

template<bool Threats>
NnueEvalTrace Network<Threats>::trace_evaluate(const Position&            pos,
                                               AccumulatorStack<Threats>& accumulatorStack,
                                               AccumulatorCaches&         cache) const {
    assert(initialized_);

    alignas(CacheLineSize)
      TransformedFeatureType transformed[FeatureTransformer<Threats>::BufferSize];

    NnueEvalTrace trace{};
    trace.correctBucket = bucket_of(pos);
    for (IndexType bucket = 0; bucket < LayerStacks; ++bucket)
    {
        AtomicV2::NNZInfo<L1> nnzInfo{};
        const i32             psqt =
          featureTransformer_.transform(pos, accumulatorStack, cache, transformed, bucket, nnzInfo);
        const i32 positional = networks_[bucket].propagate(transformed, nnzInfo);

        trace.psqt[bucket]       = static_cast<Value>(psqt / OutputScale);
        trace.positional[bucket] = static_cast<Value>(positional / OutputScale);
        trace.total[bucket]      = Eval::Detail::atomic_nnue_value_from_raw(psqt, positional);
    }
    return trace;
}

template<bool Threats>
usize Network<Threats>::get_content_hash() const noexcept {
    if (!initialized_)
        return 0;

    usize hash = 0;
    hash_combine(hash, static_cast<usize>(FileVersion));
    hash_combine(hash, static_cast<usize>(S::NetworkHash));
    hash_combine(hash, featureTransformer_.get_content_hash());
    for (const auto& architecture : networks_)
        hash_combine(hash, architecture.get_content_hash());
    return hash;
}

template<bool Threats>
bool Network<Threats>::load(std::istream& stream, std::string& description, std::string& error) {
    initialized_ = false;

    u32 version = 0, hash = 0, size = 0;
    if (!IO::read_little_endian(stream, version) || !IO::read_little_endian(stream, hash)
        || !IO::read_little_endian(stream, size))
    {
        error = "truncated header";
        return false;
    }
    if (version != FileVersion || hash != S::NetworkHash)
    {
        error = "incompatible version or network hash";
        return false;
    }
    if (size > MaxDescriptionSize)
    {
        error = "description too large";
        return false;
    }

    description.resize(size);
    if (size)
        stream.read(description.data(), std::streamsize(size));

    u32 ftHash = 0;
    if (!stream || !IO::read_little_endian(stream, ftHash) || ftHash != S::FeatureTransformerHash)
    {
        error = "incompatible feature transformer hash";
        return false;
    }
    if (!featureTransformer_.read_parameters(stream))
    {
        error = "invalid or truncated feature transformer";
        return false;
    }

    for (usize bucket = 0; bucket < LayerStacks; ++bucket)
    {
        u32 archHash = 0;
        if (!IO::read_little_endian(stream, archHash) || archHash != S::ArchitectureHash
            || !networks_[bucket].read_parameters(stream))
        {
            error = "invalid layer stack " + std::to_string(bucket);
            return false;
        }
    }

    char trailing;
    if (stream.get(trailing))
    {
        error = "trailing bytes";
        return false;
    }

    initialized_ = true;
    return true;
}

template<bool Threats>
bool Network<Threats>::save(std::ostream& stream, std::string_view description) const {
    if (!initialized_ || description.size() > MaxDescriptionSize)
        return false;

    if (!IO::write_little_endian(stream, FileVersion)
        || !IO::write_little_endian(stream, S::NetworkHash)
        || !IO::write_little_endian(stream, static_cast<u32>(description.size())))
        return false;
    if (!description.empty())
        stream.write(description.data(), std::streamsize(description.size()));

    if (!stream || !IO::write_little_endian(stream, S::FeatureTransformerHash)
        || !featureTransformer_.write_parameters(stream))
        return false;

    for (const auto& architecture : networks_)
        if (!IO::write_little_endian(stream, S::ArchitectureHash)
            || !architecture.write_parameters(stream))
            return false;

    return bool(stream);
}

std::optional<bool> probe_header(std::istream& stream) {
    const auto start = stream.tellg();
    u32        version = 0, hash = 0;
    const bool ok = IO::read_little_endian(stream, version) && IO::read_little_endian(stream, hash);
    stream.clear();
    stream.seekg(start);
    if (!ok || version != FileVersion)
        return std::nullopt;
    if (hash == Shape<true>::NetworkHash)
        return true;
    if (hash == Shape<false>::NetworkHash)
        return false;
    return std::nullopt;
}

template class AccumulatorStack<false>;
template class AccumulatorStack<true>;
template class FeatureTransformer<false>;
template class FeatureTransformer<true>;
template class Network<false>;
template class Network<true>;

}  // namespace Stockfish::Eval::NNUE::AtomicSF
