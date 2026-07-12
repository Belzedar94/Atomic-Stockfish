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

#ifndef NNUE_ARCHITECTURE_H_INCLUDED
#define NNUE_ARCHITECTURE_H_INCLUDED

#include <cstdint>
#include <functional>
#include <iosfwd>

#include "features/half_ka_v2_atomic.h"
#include "layers/affine_transform.h"
#include "layers/clipped_relu.h"
#include "nnue_common.h"

namespace Stockfish::Eval::NNUE {

using FeatureSet = Features::HalfKAv2Atomic;

// Legacy Atomic V1 has 512 accumulator values per perspective. The two
// perspectives are concatenated into the 1024 inputs consumed by the net.
constexpr IndexType TransformedFeatureDimensions = 512;
constexpr IndexType NetworkInputDimensions       = TransformedFeatureDimensions * 2;

constexpr IndexType PSQTBuckets = 8;
constexpr IndexType LayerStacks = 8;

struct NetworkArchitecture {
    static constexpr IndexType TransformedFeatureDimensions = NetworkInputDimensions;
    static constexpr int       FC_0_OUTPUTS                 = 16;
    static constexpr int       FC_1_OUTPUTS                 = 32;

    Layers::AffineTransform<NetworkInputDimensions, FC_0_OUTPUTS> fc_0;
    Layers::ClippedReLU<FC_0_OUTPUTS>                             ac_0;
    Layers::AffineTransform<FC_0_OUTPUTS, FC_1_OUTPUTS>           fc_1;
    Layers::ClippedReLU<FC_1_OUTPUTS>                             ac_1;
    Layers::AffineTransform<FC_1_OUTPUTS, 1>                      fc_2;

    static constexpr u32 get_hash_value() {
        u32 hashValue = 0xEC42E90Du ^ NetworkInputDimensions;
        hashValue     = decltype(fc_0)::get_hash_value(hashValue);
        hashValue     = decltype(ac_0)::get_hash_value(hashValue);
        hashValue     = decltype(fc_1)::get_hash_value(hashValue);
        hashValue     = decltype(ac_1)::get_hash_value(hashValue);
        hashValue     = decltype(fc_2)::get_hash_value(hashValue);
        return hashValue;
    }

    bool read_parameters(std::istream& stream) {
        return fc_0.read_parameters(stream) && ac_0.read_parameters(stream)
            && fc_1.read_parameters(stream) && ac_1.read_parameters(stream)
            && fc_2.read_parameters(stream);
    }

    bool write_parameters(std::ostream& stream) const {
        return fc_0.write_parameters(stream) && ac_0.write_parameters(stream)
            && fc_1.write_parameters(stream) && ac_1.write_parameters(stream)
            && fc_2.write_parameters(stream);
    }

    i32 propagate(const TransformedFeatureType* transformedFeatures) const {
        alignas(CacheLineSize) typename decltype(fc_0)::OutputBuffer fc0Output{};
        alignas(CacheLineSize) typename decltype(ac_0)::OutputBuffer ac0Output{};
        alignas(CacheLineSize) typename decltype(fc_1)::OutputBuffer fc1Output{};
        alignas(CacheLineSize) typename decltype(ac_1)::OutputBuffer ac1Output{};
        alignas(CacheLineSize) typename decltype(fc_2)::OutputBuffer fc2Output{};

        fc_0.propagate(transformedFeatures, fc0Output);
        ac_0.propagate(fc0Output, ac0Output);
        fc_1.propagate(ac0Output, fc1Output);
        ac_1.propagate(fc1Output, ac1Output);
        fc_2.propagate(ac1Output, fc2Output);
        return fc2Output[0];
    }

    usize get_content_hash() const {
        usize h = 0;
        hash_combine(h, fc_0.get_content_hash());
        hash_combine(h, ac_0.get_content_hash());
        hash_combine(h, fc_1.get_content_hash());
        hash_combine(h, ac_1.get_content_hash());
        hash_combine(h, fc_2.get_content_hash());
        hash_combine(h, get_hash_value());
        return h;
    }
};

static_assert(NetworkArchitecture::get_hash_value() == 0x633376CAu);

}  // namespace Stockfish::Eval::NNUE

template<>
struct std::hash<Stockfish::Eval::NNUE::NetworkArchitecture> {
    Stockfish::usize
    operator()(const Stockfish::Eval::NNUE::NetworkArchitecture& architecture) const noexcept {
        return architecture.get_content_hash();
    }
};

#endif  // NNUE_ARCHITECTURE_H_INCLUDED
