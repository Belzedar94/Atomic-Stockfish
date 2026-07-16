/*
  Stockfish, a UCI chess playing engine derived from Glaurung 2.1
  Copyright (C) 2004-2026 The Stockfish developers (see AUTHORS file)

  Stockfish is free software: you can redistribute it and/or modify
  it under the terms of the GNU General Public License as published by
  the Free Software Foundation, either version 3 of the License, or
  (at your option) any later version.
*/

#ifndef ATOMIC_V2_ARCHITECTURE_H_INCLUDED
#define ATOMIC_V2_ARCHITECTURE_H_INCLUDED

#include <cstdint>
#include <functional>
#include <iosfwd>

#include "../layers/affine_transform.h"
#include "../layers/clipped_relu.h"
#include "../nnue_common.h"
#include "constants.h"
#include "layers/affine_transform_sparse_input.h"
#include "layers/sqr_clipped_relu.h"
#include "nnz_helper.h"

namespace Stockfish::Eval::NNUE::AtomicV2 {

static_assert(PSQTBuckets % 8 == 0);

struct NetworkArchitecture {
    static constexpr IndexType TransformedFeatureDimensions = L1;
    static constexpr int       FC_0_OUTPUTS                 = L2;
    static constexpr int       FC_1_OUTPUTS                 = L3;

    AtomicV2::Layers::AffineTransformSparseInput<TransformedFeatureDimensions, FC_0_OUTPUTS> fc_0;
    AtomicV2::Layers::SqrClippedReLU<FC_0_OUTPUTS, WeightScaleBits + 1>              ac_sqr_0;
    ::Stockfish::Eval::NNUE::Layers::ClippedReLU<FC_0_OUTPUTS, WeightScaleBits + 1>  ac_0;
    ::Stockfish::Eval::NNUE::Layers::AffineTransform<FC_0_OUTPUTS * 2, FC_1_OUTPUTS> fc_1;
    AtomicV2::Layers::SqrClippedReLU<FC_1_OUTPUTS, WeightScaleBits>                  ac_sqr_1;
    ::Stockfish::Eval::NNUE::Layers::ClippedReLU<FC_1_OUTPUTS, WeightScaleBits>      ac_1;
    ::Stockfish::Eval::NNUE::Layers::AffineTransform<FC_0_OUTPUTS * 2 + FC_1_OUTPUTS * 2, 1> fc_2;

    static constexpr u32 get_hash_value() {
        u32 hashValue = 0xEC42E90Du ^ (TransformedFeatureDimensions * 2);
        hashValue     = decltype(fc_0)::get_hash_value(hashValue);
        // The squared activations intentionally remain absent from the
        // SFNNv15 wire hash, matching the pinned trainer and official engine.
        hashValue = decltype(ac_0)::get_hash_value(hashValue);
        hashValue = decltype(fc_1)::get_hash_value(hashValue);
        hashValue = decltype(ac_1)::get_hash_value(hashValue);
        hashValue = decltype(fc_2)::get_hash_value(hashValue);
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

    struct PropagationComponents {
        i32 fc2Output       = 0;
        i32 fc0SkipAdd      = 0;
        i32 fc0SkipSubtract = 0;
    };

    PropagationComponents propagate_components(const TransformedFeatureType* transformedFeatures,
                                               const NNZInfo<L1>&            nnzInfo) const {
        struct alignas(CacheLineSize) Buffer {
            alignas(CacheLineSize) typename decltype(fc_0)::OutputBuffer fc_0_out;
            alignas(CacheLineSize) typename decltype(ac_sqr_0)::OutputType
              concat_buffer[ceil_to_multiple<IndexType>(FC_0_OUTPUTS * 2 + FC_1_OUTPUTS * 2, 32)];
            alignas(CacheLineSize) typename decltype(fc_1)::OutputBuffer fc_1_out;
            alignas(CacheLineSize) typename decltype(fc_2)::OutputBuffer fc_2_out;
        };

        Buffer buffer;

        fc_0.propagate(transformedFeatures, buffer.fc_0_out, nnzInfo);
        ac_sqr_0.propagate(buffer.fc_0_out, buffer.concat_buffer);
        ac_0.propagate(buffer.fc_0_out, buffer.concat_buffer + FC_0_OUTPUTS);

        fc_1.propagate(buffer.concat_buffer, buffer.fc_1_out);
        ac_sqr_1.propagate(buffer.fc_1_out, buffer.concat_buffer + FC_0_OUTPUTS * 2);
        ac_1.propagate(buffer.fc_1_out, buffer.concat_buffer + FC_0_OUTPUTS * 2 + FC_1_OUTPUTS);

        fc_2.propagate(buffer.concat_buffer, buffer.fc_2_out);

        static_assert(FC_0_OUTPUTS >= 2);
        return {buffer.fc_2_out[0], buffer.fc_0_out[FC_0_OUTPUTS - 2],
                buffer.fc_0_out[FC_0_OUTPUTS - 1]};
    }

    i32 propagate(const TransformedFeatureType* transformedFeatures,
                  const NNZInfo<L1>&            nnzInfo) const {
        const PropagationComponents components = propagate_components(transformedFeatures, nnzInfo);
        i32                         fwdOut     = components.fc2Output;
        fwdOut += components.fc0SkipAdd - components.fc0SkipSubtract;

        constexpr i64 Multiplier = 600 * OutputScale;
        constexpr i64 Denominator =
          static_cast<i64>(HiddenOneVal) * static_cast<i64>(1U << WeightScaleBits) * 2;

        return static_cast<i32>((static_cast<i64>(fwdOut) * Multiplier) / Denominator);
    }

    usize get_content_hash() const {
        usize hash = 0;
        hash_combine(hash, fc_0.get_content_hash());
        hash_combine(hash, ac_sqr_0.get_content_hash());
        hash_combine(hash, ac_0.get_content_hash());
        hash_combine(hash, fc_1.get_content_hash());
        hash_combine(hash, ac_1.get_content_hash());
        hash_combine(hash, fc_2.get_content_hash());
        hash_combine(hash, get_hash_value());
        return hash;
    }
};

static_assert(NetworkArchitecture::TransformedFeatureDimensions == 1024);
static_assert(NetworkArchitecture::FC_0_OUTPUTS == 32);
static_assert(NetworkArchitecture::FC_1_OUTPUTS == 32);
static_assert(NetworkArchitecture::get_hash_value() == ArchitectureHash);

}  // namespace Stockfish::Eval::NNUE::AtomicV2

template<>
struct std::hash<Stockfish::Eval::NNUE::AtomicV2::NetworkArchitecture> {
    Stockfish::usize operator()(
      const Stockfish::Eval::NNUE::AtomicV2::NetworkArchitecture& architecture) const noexcept {
        return architecture.get_content_hash();
    }
};

#endif  // ATOMIC_V2_ARCHITECTURE_H_INCLUDED
