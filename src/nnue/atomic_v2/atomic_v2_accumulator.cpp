/*
  Stockfish, a UCI chess playing engine derived from Glaurung 2.1
  Copyright (C) 2004-2026 The Stockfish developers (see AUTHORS file)

  Stockfish is free software: you can redistribute it and/or modify
  it under the terms of the GNU General Public License as published by
  the Free Software Foundation, either version 3 of the License, or
  (at your option) any later version.
*/

#include "accumulator.h"

#include <type_traits>

#include "../../bitboard.h"
#include "../../position.h"
#include "../simd.h"
#include "feature_transformer.h"

namespace Stockfish::Eval::NNUE::AtomicV2 {

using namespace SIMD;

static_assert(std::is_same_v<FeatureSet::DiffType, DirtyPiece>);

namespace {

#ifdef VECTOR
using AtomicV2AccumulatorTiling = SIMDTiling<L1, L1, PSQTBuckets>;

static_assert(CacheLineSize % alignof(vec_t) == 0);
static_assert(CacheLineSize % alignof(psqt_vec_t) == 0);
static_assert((L1 * sizeof(i16)) % alignof(vec_t) == 0);
static_assert((PSQTBuckets * sizeof(i32)) % alignof(psqt_vec_t) == 0);
#endif

template<typename RemovedList, typename AddedList>
void apply_delta(Color                               perspective,
                 const FeatureTransformer&           featureTransformer,
                 const std::array<i16, L1>&          sourceAccumulation,
                 const std::array<i32, PSQTBuckets>& sourcePsqt,
                 std::array<i16, L1>&                targetAccumulation,
                 std::array<i32, PSQTBuckets>&       targetPsqt,
                 const RemovedList&                  removed,
                 const AddedList&                    added) noexcept {
    (void) perspective;

#ifdef VECTOR
    using Tiling = AtomicV2AccumulatorTiling;

    vec_t      acc[Tiling::NumRegs];
    psqt_vec_t psqt[Tiling::NumPsqtRegs];

    for (IndexType tile = 0; tile < L1 / Tiling::TileHeight; ++tile)
    {
        const IndexType tileOffset = tile * Tiling::TileHeight;
        const auto* sourceTile = reinterpret_cast<const vec_t*>(&sourceAccumulation[tileOffset]);
        auto*       targetTile = reinterpret_cast<vec_t*>(&targetAccumulation[tileOffset]);

        for (IndexType reg = 0; reg < Tiling::NumRegs; ++reg)
            acc[reg] = vec_load(&sourceTile[reg]);

        for (const IndexType index : removed)
        {
            assert(index < FeatureTransformer::InputDimensions);
            const auto* weights =
              reinterpret_cast<const vec_t*>(&featureTransformer.weights[index * L1 + tileOffset]);
            for (IndexType reg = 0; reg < Tiling::NumRegs; ++reg)
                acc[reg] = vec_sub_16(acc[reg], weights[reg]);
        }

        for (const IndexType index : added)
        {
            assert(index < FeatureTransformer::InputDimensions);
            const auto* weights =
              reinterpret_cast<const vec_t*>(&featureTransformer.weights[index * L1 + tileOffset]);
            for (IndexType reg = 0; reg < Tiling::NumRegs; ++reg)
                acc[reg] = vec_add_16(acc[reg], weights[reg]);
        }

        for (IndexType reg = 0; reg < Tiling::NumRegs; ++reg)
            vec_store(&targetTile[reg], acc[reg]);
    }

    for (IndexType tile = 0; tile < PSQTBuckets / Tiling::PsqtTileHeight; ++tile)
    {
        const IndexType tileOffset = tile * Tiling::PsqtTileHeight;
        const auto*     sourceTile = reinterpret_cast<const psqt_vec_t*>(&sourcePsqt[tileOffset]);
        auto*           targetTile = reinterpret_cast<psqt_vec_t*>(&targetPsqt[tileOffset]);

        for (IndexType reg = 0; reg < Tiling::NumPsqtRegs; ++reg)
            psqt[reg] = vec_load_psqt(&sourceTile[reg]);

        for (const IndexType index : removed)
        {
            const auto* weights = reinterpret_cast<const psqt_vec_t*>(
              &featureTransformer.psqtWeights[index * PSQTBuckets + tileOffset]);
            for (IndexType reg = 0; reg < Tiling::NumPsqtRegs; ++reg)
                psqt[reg] = vec_sub_psqt_32(psqt[reg], weights[reg]);
        }

        for (const IndexType index : added)
        {
            const auto* weights = reinterpret_cast<const psqt_vec_t*>(
              &featureTransformer.psqtWeights[index * PSQTBuckets + tileOffset]);
            for (IndexType reg = 0; reg < Tiling::NumPsqtRegs; ++reg)
                psqt[reg] = vec_add_psqt_32(psqt[reg], weights[reg]);
        }

        for (IndexType reg = 0; reg < Tiling::NumPsqtRegs; ++reg)
            vec_store_psqt(&targetTile[reg], psqt[reg]);
    }
#else
    targetAccumulation = sourceAccumulation;
    targetPsqt         = sourcePsqt;

    auto apply = [&](IndexType index, int sign) {
        assert(index < FeatureTransformer::InputDimensions);
        const IndexType offset = index * L1;
        for (IndexType dimension = 0; dimension < L1; ++dimension)
            targetAccumulation[dimension] =
              static_cast<i16>(targetAccumulation[dimension]
                               + sign * featureTransformer.weights[offset + dimension]);
        for (IndexType bucket = 0; bucket < PSQTBuckets; ++bucket)
            targetPsqt[bucket] +=
              sign * featureTransformer.psqtWeights[index * PSQTBuckets + bucket];
    };

    for (const IndexType index : removed)
        apply(index, -1);
    for (const IndexType index : added)
        apply(index, 1);
#endif
}

}  // namespace

const AccumulatorState& AccumulatorStack::latest() const noexcept { return accumulators[size - 1]; }

AccumulatorState& AccumulatorStack::mut_latest() noexcept { return accumulators[size - 1]; }

void AccumulatorStack::reset() noexcept {
    accumulators[0].dirtyPiece = {};
    accumulators[0].computed.fill(false);
    size = 1;
}

DirtyPiece& AccumulatorStack::push() noexcept {
    assert(size < MaxSize);
    auto& target      = accumulators[size++];
    target.dirtyPiece = {};
    target.computed.fill(false);
    return target.dirtyPiece;
}

void AccumulatorStack::pop() noexcept {
    assert(size > 1);
    --size;
}

void AccumulatorStack::evaluate(const Position&           pos,
                                const FeatureTransformer& featureTransformer,
                                AccumulatorCaches&        cache) noexcept {
    evaluate_side(WHITE, pos, featureTransformer, cache);
    evaluate_side(BLACK, pos, featureTransformer, cache);
}

void AccumulatorStack::evaluate_side(Color                     perspective,
                                     const Position&           pos,
                                     const FeatureTransformer& featureTransformer,
                                     AccumulatorCaches&        cache) noexcept {
    if (mut_latest().computed[perspective])
        return;

    if (!pos.has_king(perspective))
    {
        refresh(perspective, pos, featureTransformer, mut_latest(), cache);
        return;
    }

    usize base = size - 1;
    while (base > 0 && !accumulators[base].computed[perspective]
           && !FeatureSet::requires_refresh(accumulators[base].dirtyPiece, perspective))
        --base;

    if (!accumulators[base].computed[perspective])
    {
        refresh(perspective, pos, featureTransformer, mut_latest(), cache);
        return;
    }

    const Square kingSquare = pos.square<KING>(perspective);
    for (usize current = base + 1; current < size; ++current)
    {
        if (FeatureSet::requires_refresh(accumulators[current].dirtyPiece, perspective))
        {
            refresh(perspective, pos, featureTransformer, mut_latest(), cache);
            return;
        }

        update(perspective, kingSquare, featureTransformer, accumulators[current - 1],
               accumulators[current]);
    }
}

void AccumulatorStack::refresh(Color                     perspective,
                               const Position&           pos,
                               const FeatureTransformer& featureTransformer,
                               AccumulatorState&         target,
                               AccumulatorCaches&        cache) noexcept {
    FeatureSet::ActiveIndexList removed;
    FeatureSet::ActiveIndexList added;

    if (!pos.has_king(perspective))
    {
        target.accumulation[perspective] = featureTransformer.biases;
        target.psqtAccumulation[perspective].fill(0);
        FeatureSet::append_active_indices(pos, perspective, added);
        apply_delta(perspective, featureTransformer, target.accumulation[perspective],
                    target.psqtAccumulation[perspective], target.accumulation[perspective],
                    target.psqtAccumulation[perspective], removed, added);
        target.computed[perspective] = true;
        return;
    }

    const Square kingSquare = pos.square<KING>(perspective);
    auto&        entry      = cache[kingSquare][perspective];

    Bitboard changed = 0;
    for (Square square = SQ_A1; square <= SQ_H8; ++square)
        if (entry.pieces[square] != pos.piece_on(square))
            changed |= square_bb(square);

    Bitboard removedBB = changed & entry.pieceBB;
    Bitboard addedBB   = changed & pos.pieces();
    while (removedBB)
    {
        const Square square = pop_lsb(removedBB);
        removed.push_back(
          FeatureSet::make_index(perspective, square, entry.pieces[square], kingSquare));
    }
    while (addedBB)
    {
        const Square square = pop_lsb(addedBB);
        added.push_back(
          FeatureSet::make_index(perspective, square, pos.piece_on(square), kingSquare));
    }

    apply_delta(perspective, featureTransformer, entry.accumulation, entry.psqtAccumulation,
                target.accumulation[perspective], target.psqtAccumulation[perspective], removed,
                added);

    entry.accumulation     = target.accumulation[perspective];
    entry.psqtAccumulation = target.psqtAccumulation[perspective];
    entry.pieceBB          = pos.pieces();
    for (Square square = SQ_A1; square <= SQ_H8; ++square)
        entry.pieces[square] = pos.piece_on(square);

    target.computed[perspective] = true;
}

void AccumulatorStack::update(Color                     perspective,
                              Square                    kingSquare,
                              const FeatureTransformer& featureTransformer,
                              const AccumulatorState&   source,
                              AccumulatorState&         target) noexcept {
    FeatureSet::RemovedIndexList removed;
    FeatureSet::AddedIndexList   added;
    FeatureSet::append_changed_indices(perspective, kingSquare, target.dirtyPiece, removed, added);

    apply_delta(perspective, featureTransformer, source.accumulation[perspective],
                source.psqtAccumulation[perspective], target.accumulation[perspective],
                target.psqtAccumulation[perspective], removed, added);
    target.computed[perspective] = true;
}

}  // namespace Stockfish::Eval::NNUE::AtomicV2
