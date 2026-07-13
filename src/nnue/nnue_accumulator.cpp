/*
  Stockfish, a UCI chess playing engine derived from Glaurung 2.1
  Copyright (C) 2004-2026 The Stockfish developers (see AUTHORS file)

  Stockfish is free software: you can redistribute it and/or modify
  it under the terms of the GNU General Public License as published by
  the Free Software Foundation, either version 3 of the License, or
  (at your option) any later version.

  Stockfish is distributed in the hope that it will be useful,
  but WITHOUT ANY WARRANTY; without even the implied warranty of
  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
  GNU General Public License for more details.

  You should have received a copy of the GNU General Public License
  along with this program.  If not, see <http://www.gnu.org/licenses/>.
*/

#include "nnue_accumulator.h"

#include <algorithm>

#include "../position.h"
#include "features/half_ka_v2_atomic.h"
#include "nnue_feature_transformer.h"
#include "simd.h"

namespace Stockfish::Eval::NNUE {

using namespace SIMD;

#ifdef VECTOR
using LegacyAtomicAccumulatorTiling =
  SIMDTiling<TransformedFeatureDimensions, TransformedFeatureDimensions, PSQTBuckets>;

static_assert(CacheLineSize % alignof(vec_t) == 0);
static_assert(CacheLineSize % alignof(psqt_vec_t) == 0);
static_assert((TransformedFeatureDimensions * sizeof(i16)) % alignof(vec_t) == 0);
static_assert((PSQTBuckets * sizeof(i32)) % alignof(psqt_vec_t) == 0);
#endif

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
                                AccumulatorCaches&) noexcept {
    evaluate_side(WHITE, pos, featureTransformer);
    evaluate_side(BLACK, pos, featureTransformer);
}

void AccumulatorStack::evaluate_side(Color                     perspective,
                                     const Position&           pos,
                                     const FeatureTransformer& featureTransformer) noexcept {
    if (mut_latest().computed[perspective])
        return;

    usize base = size - 1;
    while (base > 0 && !accumulators[base].computed[perspective]
           && !FeatureSet::requires_refresh(accumulators[base].dirtyPiece, perspective))
        --base;

    if (!accumulators[base].computed[perspective])
    {
        refresh(perspective, pos, featureTransformer, mut_latest());
        return;
    }

    const Square ksq = pos.has_king(perspective) ? pos.square<KING>(perspective) : SQ_NONE;
    for (usize current = base + 1; current < size; ++current)
    {
        if (FeatureSet::requires_refresh(accumulators[current].dirtyPiece, perspective))
        {
            refresh(perspective, pos, featureTransformer, mut_latest());
            return;
        }

        update(perspective, ksq, featureTransformer, accumulators[current - 1],
               accumulators[current]);
    }
}

void AccumulatorStack::refresh(Color                     perspective,
                               const Position&           pos,
                               const FeatureTransformer& featureTransformer,
                               AccumulatorState&         target) noexcept {
    target.accumulation[perspective] = featureTransformer.biases;
    target.psqtAccumulation[perspective].fill(0);

    FeatureSet::IndexList active;
    FeatureSet::append_active_indices(pos, perspective, active);

#ifdef VECTOR
    using Tiling = LegacyAtomicAccumulatorTiling;

    vec_t      acc[Tiling::NumRegs];
    psqt_vec_t psqt[Tiling::NumPsqtRegs];

    for (IndexType tile = 0; tile < TransformedFeatureDimensions / Tiling::TileHeight; ++tile)
    {
        const IndexType tileOffset = tile * Tiling::TileHeight;
        const auto*     biasTile =
          reinterpret_cast<const vec_t*>(&featureTransformer.biases[tileOffset]);
        auto* targetTile = reinterpret_cast<vec_t*>(&target.accumulation[perspective][tileOffset]);

        for (IndexType reg = 0; reg < Tiling::NumRegs; ++reg)
            acc[reg] = vec_load(&biasTile[reg]);

        for (const IndexType index : active)
        {
            assert(index < FeatureTransformer::InputDimensions);
            const auto* weightTile = reinterpret_cast<const vec_t*>(
              &featureTransformer.weights[index * TransformedFeatureDimensions + tileOffset]);
            for (IndexType reg = 0; reg < Tiling::NumRegs; ++reg)
                acc[reg] = vec_add_16(acc[reg], weightTile[reg]);
        }

        for (IndexType reg = 0; reg < Tiling::NumRegs; ++reg)
            vec_store(&targetTile[reg], acc[reg]);
    }

    for (IndexType tile = 0; tile < PSQTBuckets / Tiling::PsqtTileHeight; ++tile)
    {
        const IndexType tileOffset = tile * Tiling::PsqtTileHeight;
        auto*           targetTile =
          reinterpret_cast<psqt_vec_t*>(&target.psqtAccumulation[perspective][tileOffset]);

        for (IndexType reg = 0; reg < Tiling::NumPsqtRegs; ++reg)
            psqt[reg] = vec_zero_psqt();

        for (const IndexType index : active)
        {
            const auto* weightTile = reinterpret_cast<const psqt_vec_t*>(
              &featureTransformer.psqtWeights[index * PSQTBuckets + tileOffset]);
            for (IndexType reg = 0; reg < Tiling::NumPsqtRegs; ++reg)
                psqt[reg] = vec_add_psqt_32(psqt[reg], weightTile[reg]);
        }

        for (IndexType reg = 0; reg < Tiling::NumPsqtRegs; ++reg)
            vec_store_psqt(&targetTile[reg], psqt[reg]);
    }
#else
    for (const IndexType index : active)
    {
        assert(index < FeatureTransformer::InputDimensions);
        const IndexType offset = index * TransformedFeatureDimensions;

        for (IndexType i = 0; i < TransformedFeatureDimensions; ++i)
            target.accumulation[perspective][i] = static_cast<i16>(
              target.accumulation[perspective][i] + featureTransformer.weights[offset + i]);

        for (IndexType bucket = 0; bucket < PSQTBuckets; ++bucket)
            target.psqtAccumulation[perspective][bucket] +=
              featureTransformer.psqtWeights[index * PSQTBuckets + bucket];
    }
#endif

    target.computed[perspective] = true;
}

void AccumulatorStack::update(Color                     perspective,
                              Square                    ksq,
                              const FeatureTransformer& featureTransformer,
                              const AccumulatorState&   source,
                              AccumulatorState&         target) noexcept {
    FeatureSet::IndexList removed;
    FeatureSet::IndexList added;
    FeatureSet::append_changed_indices(perspective, ksq, target.dirtyPiece, removed, added);

#ifdef VECTOR
    using Tiling = LegacyAtomicAccumulatorTiling;

    vec_t      acc[Tiling::NumRegs];
    psqt_vec_t psqt[Tiling::NumPsqtRegs];

    for (IndexType tile = 0; tile < TransformedFeatureDimensions / Tiling::TileHeight; ++tile)
    {
        const IndexType tileOffset = tile * Tiling::TileHeight;
        const auto*     sourceTile =
          reinterpret_cast<const vec_t*>(&source.accumulation[perspective][tileOffset]);
        auto* targetTile = reinterpret_cast<vec_t*>(&target.accumulation[perspective][tileOffset]);

        for (IndexType reg = 0; reg < Tiling::NumRegs; ++reg)
            acc[reg] = vec_load(&sourceTile[reg]);

        for (const IndexType index : removed)
        {
            assert(index < FeatureTransformer::InputDimensions);
            const auto* weightTile = reinterpret_cast<const vec_t*>(
              &featureTransformer.weights[index * TransformedFeatureDimensions + tileOffset]);
            for (IndexType reg = 0; reg < Tiling::NumRegs; ++reg)
                acc[reg] = vec_sub_16(acc[reg], weightTile[reg]);
        }

        for (const IndexType index : added)
        {
            assert(index < FeatureTransformer::InputDimensions);
            const auto* weightTile = reinterpret_cast<const vec_t*>(
              &featureTransformer.weights[index * TransformedFeatureDimensions + tileOffset]);
            for (IndexType reg = 0; reg < Tiling::NumRegs; ++reg)
                acc[reg] = vec_add_16(acc[reg], weightTile[reg]);
        }

        for (IndexType reg = 0; reg < Tiling::NumRegs; ++reg)
            vec_store(&targetTile[reg], acc[reg]);
    }

    for (IndexType tile = 0; tile < PSQTBuckets / Tiling::PsqtTileHeight; ++tile)
    {
        const IndexType tileOffset = tile * Tiling::PsqtTileHeight;
        const auto*     sourceTile =
          reinterpret_cast<const psqt_vec_t*>(&source.psqtAccumulation[perspective][tileOffset]);
        auto* targetTile =
          reinterpret_cast<psqt_vec_t*>(&target.psqtAccumulation[perspective][tileOffset]);

        for (IndexType reg = 0; reg < Tiling::NumPsqtRegs; ++reg)
            psqt[reg] = vec_load_psqt(&sourceTile[reg]);

        for (const IndexType index : removed)
        {
            const auto* weightTile = reinterpret_cast<const psqt_vec_t*>(
              &featureTransformer.psqtWeights[index * PSQTBuckets + tileOffset]);
            for (IndexType reg = 0; reg < Tiling::NumPsqtRegs; ++reg)
                psqt[reg] = vec_sub_psqt_32(psqt[reg], weightTile[reg]);
        }

        for (const IndexType index : added)
        {
            const auto* weightTile = reinterpret_cast<const psqt_vec_t*>(
              &featureTransformer.psqtWeights[index * PSQTBuckets + tileOffset]);
            for (IndexType reg = 0; reg < Tiling::NumPsqtRegs; ++reg)
                psqt[reg] = vec_add_psqt_32(psqt[reg], weightTile[reg]);
        }

        for (IndexType reg = 0; reg < Tiling::NumPsqtRegs; ++reg)
            vec_store_psqt(&targetTile[reg], psqt[reg]);
    }
#else
    target.accumulation[perspective]     = source.accumulation[perspective];
    target.psqtAccumulation[perspective] = source.psqtAccumulation[perspective];

    auto apply = [&](IndexType index, int sign) {
        assert(index < FeatureTransformer::InputDimensions);
        const IndexType offset = index * TransformedFeatureDimensions;

        for (IndexType i = 0; i < TransformedFeatureDimensions; ++i)
            target.accumulation[perspective][i] = static_cast<i16>(
              target.accumulation[perspective][i] + sign * featureTransformer.weights[offset + i]);

        for (IndexType bucket = 0; bucket < PSQTBuckets; ++bucket)
            target.psqtAccumulation[perspective][bucket] +=
              sign * featureTransformer.psqtWeights[index * PSQTBuckets + bucket];
    };

    for (const IndexType index : removed)
        apply(index, -1);
    for (const IndexType index : added)
        apply(index, 1);
#endif

    target.computed[perspective] = true;
}

}  // namespace Stockfish::Eval::NNUE
