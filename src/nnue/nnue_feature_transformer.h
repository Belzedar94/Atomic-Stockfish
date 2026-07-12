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

#ifndef NNUE_FEATURE_TRANSFORMER_H_INCLUDED
#define NNUE_FEATURE_TRANSFORMER_H_INCLUDED

#include <algorithm>
#include <array>
#include <cstdint>
#include <functional>
#include <iosfwd>

#include "../position.h"
#include "../types.h"
#include "nnue_accumulator.h"
#include "nnue_architecture.h"
#include "nnue_common.h"

namespace Stockfish::Eval::NNUE {

class FeatureTransformer {
    static constexpr IndexType HalfDimensions = TransformedFeatureDimensions;

   public:
    using OutputType = TransformedFeatureType;

    static constexpr IndexType InputDimensions  = FeatureSet::Dimensions;
    static constexpr IndexType OutputDimensions = HalfDimensions * 2;
    static constexpr usize     BufferSize       = OutputDimensions * sizeof(OutputType);

    static constexpr u32 get_hash_value() { return FeatureSet::HashValue ^ OutputDimensions; }

    bool read_parameters(std::istream& stream) {
        read_little_endian<BiasType>(stream, biases.data(), biases.size());
        read_little_endian<WeightType>(stream, weights.data(), weights.size());
        read_little_endian<PSQTWeightType>(stream, psqtWeights.data(), psqtWeights.size());
        return !stream.fail();
    }

    bool write_parameters(std::ostream& stream) const {
        write_little_endian<BiasType>(stream, biases.data(), biases.size());
        write_little_endian<WeightType>(stream, weights.data(), weights.size());
        write_little_endian<PSQTWeightType>(stream, psqtWeights.data(), psqtWeights.size());
        return !stream.fail();
    }

    usize get_content_hash() const {
        usize h = 0;
        hash_combine(h, get_raw_data_hash(biases));
        hash_combine(h, get_raw_data_hash(weights));
        hash_combine(h, get_raw_data_hash(psqtWeights));
        hash_combine(h, get_hash_value());
        return h;
    }

    i32 transform(const Position&    pos,
                  AccumulatorStack&  accumulatorStack,
                  AccumulatorCaches& cache,
                  OutputType*        output,
                  int                bucket) const {
        assert(bucket >= 0 && bucket < int(PSQTBuckets));

        accumulatorStack.evaluate(pos, *this, cache);
        const auto& accumulator = accumulatorStack.latest();

        const Color perspectives[COLOR_NB] = {pos.side_to_move(), ~pos.side_to_move()};
        const i32   psqt                   = (accumulator.psqtAccumulation[perspectives[0]][bucket]
                          - accumulator.psqtAccumulation[perspectives[1]][bucket])
                       / 2;

        for (IndexType p = 0; p < COLOR_NB; ++p)
        {
            const auto& source = accumulator.accumulation[perspectives[p]];
            const auto  offset = p * HalfDimensions;
            for (IndexType i = 0; i < HalfDimensions; ++i)
                output[offset + i] = static_cast<OutputType>(std::clamp<int>(source[i], 0, 127));
        }

        return psqt;
    }

    alignas(CacheLineSize) std::array<BiasType, HalfDimensions> biases;
    alignas(CacheLineSize) std::array<WeightType, HalfDimensions * InputDimensions> weights;
    alignas(CacheLineSize) std::array<PSQTWeightType, InputDimensions * PSQTBuckets> psqtWeights;
};

static_assert(FeatureTransformer::InputDimensions == 45056);
static_assert(FeatureTransformer::OutputDimensions == 1024);
static_assert(FeatureTransformer::get_hash_value() == 0x5F2348B8u);

}  // namespace Stockfish::Eval::NNUE

template<>
struct std::hash<Stockfish::Eval::NNUE::FeatureTransformer> {
    Stockfish::usize
    operator()(const Stockfish::Eval::NNUE::FeatureTransformer& transformer) const noexcept {
        return transformer.get_content_hash();
    }
};

#endif  // NNUE_FEATURE_TRANSFORMER_H_INCLUDED
