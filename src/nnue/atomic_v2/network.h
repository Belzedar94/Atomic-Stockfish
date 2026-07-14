/*
  Stockfish, a UCI chess playing engine derived from Glaurung 2.1
  Copyright (C) 2004-2026 The Stockfish developers (see AUTHORS file)

  Stockfish is free software: you can redistribute it and/or modify
  it under the terms of the GNU General Public License as published by
  the Free Software Foundation, either version 3 of the License, or
  (at your option) any later version.
*/

#ifndef ATOMIC_V2_NETWORK_H_INCLUDED
#define ATOMIC_V2_NETWORK_H_INCLUDED

#include <filesystem>
#include <functional>
#include <iosfwd>
#include <string>
#include <string_view>
#include <tuple>
#include <type_traits>

#include "../../types.h"
#include "../nnue_common.h"
#include "accumulator.h"
#include "architecture.h"
#include "constants.h"
#include "feature_transformer.h"

namespace Stockfish {
class Position;
}

namespace Stockfish::Eval::NNUE {
struct NnueEvalTrace;
}

namespace Stockfish::Eval::NNUE::AtomicV2 {

using NetworkOutput    = std::tuple<Value, Value>;
using RawNetworkOutput = std::tuple<i32, i32>;

class Network;

struct LoadResult {
    bool                  success = false;
    std::string           description;
    std::filesystem::path resolvedPath;
    std::string           error;

    explicit operator bool() const noexcept { return success; }
};

[[nodiscard]] LoadResult load_candidate(std::istream& stream, Network& destination);

[[nodiscard]] LoadResult load_candidate(const std::filesystem::path& rootDirectory,
                                        const std::filesystem::path& evalfilePath,
                                        Network&                     destination);

// Inline and trivially copyable so a NUMA replica can be copied into shared
// memory without pointer fixups. Candidate parsing is deliberately external:
// AnyNetwork owns the single heap allocation and passes its V2 union member to
// load_candidate(), avoiding a second ~90 MiB object and copy.
class Network {
   public:
    Network() = default;

    Network(const Network&)            = default;
    Network(Network&&)                 = default;
    Network& operator=(const Network&) = default;
    Network& operator=(Network&&)      = default;

    static constexpr u32 version() noexcept { return FileVersion; }
    static constexpr u32 feature_hash() noexcept { return FeatureHash; }
    static constexpr u32 feature_transformer_hash() noexcept { return FeatureTransformerHash; }
    static constexpr u32 architecture_hash() noexcept { return ArchitectureHash; }
    static constexpr u32 network_hash() noexcept { return NetworkHash; }
    static constexpr HeaderIdentity   header_identity() noexcept { return Header; }
    static constexpr ShapeMetadata    shape() noexcept { return Shape; }
    static constexpr std::string_view backend_name() noexcept { return BackendName; }
    static constexpr std::string_view display_name() noexcept { return BackendDisplayName; }

    [[nodiscard]] bool  initialized() const noexcept { return initialized_; }
    [[nodiscard]] usize get_content_hash() const noexcept;

    [[nodiscard]] const FeatureTransformer& feature_transformer() const noexcept {
        return featureTransformer_;
    }
    [[nodiscard]] FeatureTransformer& feature_transformer() noexcept { return featureTransformer_; }

    [[nodiscard]] NetworkOutput evaluate(const Position&    pos,
                                         AccumulatorStack&  accumulatorStack,
                                         AccumulatorCaches& cache) const;

    [[nodiscard]] RawNetworkOutput evaluate_raw(const Position&    pos,
                                                AccumulatorStack&  accumulatorStack,
                                                AccumulatorCaches& cache) const;

    [[nodiscard]] NnueEvalTrace trace_evaluate(const Position&    pos,
                                               AccumulatorStack&  accumulatorStack,
                                               AccumulatorCaches& cache) const;

    bool save(std::ostream& stream, std::string_view description) const;

   private:
    bool read_parameters(std::istream& stream, std::string& description, std::string& error);
    bool write_parameters(std::ostream& stream, std::string_view description) const;

    FeatureTransformer  featureTransformer_;
    NetworkArchitecture networks_[LayerStacks];
    bool                initialized_ = false;

    friend LoadResult load_candidate(std::istream&, Network&);
};

static_assert(Network::feature_hash() == FeatureSet::HashValue);
static_assert(Network::feature_transformer_hash() == FeatureTransformer::get_hash_value());
static_assert(Network::architecture_hash() == NetworkArchitecture::get_hash_value());
static_assert(Network::network_hash()
              == (FeatureTransformer::get_hash_value() ^ NetworkArchitecture::get_hash_value()));
static_assert(std::is_standard_layout_v<Network>);
static_assert(std::is_trivially_copyable_v<Network>);
static_assert(std::is_trivially_destructible_v<Network>);

}  // namespace Stockfish::Eval::NNUE::AtomicV2

template<>
struct std::hash<Stockfish::Eval::NNUE::AtomicV2::Network> {
    Stockfish::usize
    operator()(const Stockfish::Eval::NNUE::AtomicV2::Network& network) const noexcept {
        return network.get_content_hash();
    }
};

#endif  // ATOMIC_V2_NETWORK_H_INCLUDED
