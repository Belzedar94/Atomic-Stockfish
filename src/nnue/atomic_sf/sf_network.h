/*
  Atomic-Stockfish, a specialized Atomic Chess engine derived from Stockfish
  Copyright (C) 2004-2026 The Stockfish developers (see AUTHORS file)

  Atomic-Stockfish is free software: you can redistribute it and/or modify
  it under the terms of the GNU General Public License as published by
  the Free Software Foundation, either version 3 of the License, or
  (at your option) any later version.
*/

// Stockfish-master network family (SF eca43a97 file format, Version
// 0x6A448AFA) as an Atomic backend:
//   N0 = HalfKAv2_hm, L1 1024 pairwise, SFNNv10+ tail 1024 -> 32 -> 32 -> 1
//   N1 = Full_Threats + HalfKAv2_hm, same tail (SF master as is)
// The tail (AtomicV2::NetworkArchitecture) is shared with AtomicNNUEV2 and is
// identical to upstream, hash included.

#ifndef ATOMIC_SF_NETWORK_H_INCLUDED
#define ATOMIC_SF_NETWORK_H_INCLUDED

#include <array>
#include <cassert>
#include <filesystem>
#include <iosfwd>
#include <optional>
#include <string>
#include <string_view>
#include <tuple>
#include <type_traits>

#include "../../types.h"
#include "../atomic_v2/architecture.h"
#include "../atomic_v2/nnz_helper.h"
#include "../nnue_common.h"
#include "sf_features.h"

namespace Stockfish {
class Position;
}

namespace Stockfish::Eval::NNUE {
struct NnueEvalTrace;
}

namespace Stockfish::Eval::NNUE::AtomicSF {

inline constexpr u32       FileVersion = 0x6A448AFAu;
inline constexpr IndexType L1          = 1024;
inline constexpr IndexType PSQTBuckets = 8;
inline constexpr IndexType LayerStacks = 8;
inline constexpr int       FtMaxVal    = 255;

using NetworkArchitecture = AtomicV2::NetworkArchitecture;
using NetworkOutput       = std::tuple<Value, Value>;
using RawNetworkOutput    = std::tuple<i32, i32>;

static_assert(NetworkArchitecture::TransformedFeatureDimensions == L1);

constexpr u32 combine_feature_hash(bool threats) {
    // nnue-pytorch ComposedFeatureTransformer: h = rotl(h, 1) ^ component.
    if (!threats)
        return HalfKAv2Hm::HashValue;
    const u32 h = FullThreats::HashValue;
    return ((h << 1) | (h >> 31)) ^ HalfKAv2Hm::HashValue;
}

template<bool Threats>
struct Shape {
    static constexpr IndexType PsqDimensions    = HalfKAv2Hm::Dimensions;
    static constexpr IndexType ThreatDimensions = Threats ? FullThreats::Dimensions : 0;
    static constexpr u32       FeatureHash      = combine_feature_hash(Threats);
    static constexpr u32       FeatureTransformerHash = FeatureHash ^ (L1 * 2);
    static constexpr u32       ArchitectureHash       = NetworkArchitecture::get_hash_value();
    static constexpr u32       NetworkHash = FeatureTransformerHash ^ ArchitectureHash;
};

// ---------------------------------------------------------------------------
// Accumulator

struct alignas(CacheLineSize) Accumulator {
    std::array<std::array<i16, L1>, COLOR_NB>          accumulation;
    std::array<std::array<i32, PSQTBuckets>, COLOR_NB> psqtAccumulation;
    std::array<bool, COLOR_NB>                         computed{};
};

struct AccumulatorState: public Accumulator {
    DirtyPiece   dirtyPiece;
    DirtyThreats dirtyThreats;
};

// Finny tables over the HalfKAv2_hm part only; threats are re-added on top.
struct AccumulatorCaches {
    struct alignas(CacheLineSize) Entry {
        std::array<BiasType, L1>                accumulation;
        std::array<PSQTWeightType, PSQTBuckets> psqtAccumulation;
        std::array<Piece, SQUARE_NB>            pieces;
        Bitboard                                pieceBB = 0;

        void clear(const std::array<BiasType, L1>& biases) noexcept {
            accumulation = biases;
            psqtAccumulation.fill(0);
            pieces.fill(NO_PIECE);
            pieceBB = 0;
        }
    };

    template<typename Network>
    explicit AccumulatorCaches(const Network& network) {
        clear(network);
    }

    template<typename Network>
    void clear(const Network& network) {
        for (auto& squareEntries : entries)
            for (auto& entry : squareEntries)
                entry.clear(network.feature_transformer().biases);
    }

    std::array<Entry, COLOR_NB>& operator[](Square square) noexcept { return entries[square]; }

    std::array<std::array<Entry, COLOR_NB>, SQUARE_NB> entries;
};

template<bool Threats>
class FeatureTransformer;

template<bool Threats>
class AccumulatorStack {
   public:
    static constexpr usize MaxSize = MAX_PLY + 1;

    [[nodiscard]] const AccumulatorState& latest() const noexcept { return accumulators[size - 1]; }

    void        reset() noexcept;
    DirtyPiece& push() noexcept;
    void        pop() noexcept;

    // Called once the move of the latest push() has been made on pos.
    void after_move(const Position& pos) noexcept;

    void evaluate(const Position&                    pos,
                  const FeatureTransformer<Threats>& featureTransformer,
                  AccumulatorCaches&                 cache) noexcept;

   private:
    [[nodiscard]] AccumulatorState& mut_latest() noexcept { return accumulators[size - 1]; }

    static bool requires_refresh(const AccumulatorState& state, Color perspective) noexcept;

    void evaluate_side(Color                              perspective,
                       const Position&                    pos,
                       const FeatureTransformer<Threats>& featureTransformer,
                       AccumulatorCaches&                 cache) noexcept;

    void refresh(Color                              perspective,
                 const Position&                    pos,
                 const FeatureTransformer<Threats>& featureTransformer,
                 AccumulatorState&                  target,
                 AccumulatorCaches&                 cache) noexcept;

    template<bool Forward>
    void update(Color                              perspective,
                Square                             kingSquare,
                const FeatureTransformer<Threats>& featureTransformer,
                const AccumulatorState&            source,
                AccumulatorState&                  target) noexcept;

    std::array<AccumulatorState, MaxSize> accumulators;
    usize                                 size = 1;
};

// ---------------------------------------------------------------------------
// Feature transformer

template<bool Threats>
class FeatureTransformer {
   public:
    using OutputType = TransformedFeatureType;
    using S          = Shape<Threats>;

    static constexpr IndexType HalfDimensions   = L1;
    static constexpr IndexType OutputDimensions = L1;
    static constexpr usize     BufferSize       = OutputDimensions * sizeof(OutputType);

    static constexpr u32 get_hash_value() { return S::FeatureTransformerHash; }

    bool read_parameters(std::istream& stream);
    bool write_parameters(std::ostream& stream) const;

    usize get_content_hash() const;

    i32 transform(const Position&             pos,
                  AccumulatorStack<Threats>&  accumulatorStack,
                  AccumulatorCaches&          cache,
                  OutputType*                 output,
                  int                         bucket,
                  AtomicV2::NNZInfo<L1>&      nnzInfo) const;

    void permute_weights();
    void unpermute_weights();

    alignas(CacheLineSize) std::array<BiasType, HalfDimensions> biases;
    alignas(CacheLineSize) std::array<WeightType, HalfDimensions * S::PsqDimensions> weights;
    alignas(CacheLineSize)
      std::array<PSQTWeightType, S::PsqDimensions * PSQTBuckets> psqtWeights;
    alignas(CacheLineSize)
      std::array<ThreatWeightType, HalfDimensions * S::ThreatDimensions> threatWeights;
    alignas(CacheLineSize)
      std::array<PSQTWeightType, S::ThreatDimensions * PSQTBuckets> threatPsqtWeights;
};

// ---------------------------------------------------------------------------
// Network

template<bool Threats>
class Network {
   public:
    using S = Shape<Threats>;

    Network() = default;

    [[nodiscard]] bool  initialized() const noexcept { return initialized_; }
    [[nodiscard]] usize get_content_hash() const noexcept;

    [[nodiscard]] const FeatureTransformer<Threats>& feature_transformer() const noexcept {
        return featureTransformer_;
    }

    [[nodiscard]] NetworkOutput evaluate(const Position&            pos,
                                         AccumulatorStack<Threats>& accumulatorStack,
                                         AccumulatorCaches&         cache) const;

    [[nodiscard]] RawNetworkOutput evaluate_raw(const Position&            pos,
                                                AccumulatorStack<Threats>& accumulatorStack,
                                                AccumulatorCaches&         cache) const;

    [[nodiscard]] NnueEvalTrace trace_evaluate(const Position&            pos,
                                               AccumulatorStack<Threats>& accumulatorStack,
                                               AccumulatorCaches&         cache) const;

    // Loads the whole stream; fails without trailing garbage tolerance.
    bool load(std::istream& stream, std::string& description, std::string& error);
    bool save(std::ostream& stream, std::string_view description) const;

    static constexpr std::string_view display_name() noexcept {
        return Threats ? "AtomicSF N1 (Full_Threats+HalfKAv2_hm)" : "AtomicSF N0 (HalfKAv2_hm)";
    }

   private:
    FeatureTransformer<Threats> featureTransformer_;
    NetworkArchitecture         networks_[LayerStacks];
    bool                        initialized_ = false;
};

// Reads only the 8-byte header (version, network hash) and rewinds.
// Returns the matching kind (false = N0, true = N1) or nullopt.
std::optional<bool> probe_header(std::istream& stream);

static_assert(std::is_trivially_copyable_v<Network<false>>);
static_assert(std::is_trivially_copyable_v<Network<true>>);
static_assert(std::is_trivially_destructible_v<Network<true>>);

}  // namespace Stockfish::Eval::NNUE::AtomicSF

#endif  // ATOMIC_SF_NETWORK_H_INCLUDED
