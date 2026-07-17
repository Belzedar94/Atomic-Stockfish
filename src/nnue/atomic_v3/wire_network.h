/*
  Atomic-Stockfish, a UCI chess playing engine derived from Stockfish
  Copyright (C) 2004-2026 The Stockfish developers (see AUTHORS file)

  Atomic-Stockfish is free software: you can redistribute it and/or modify
  it under the terms of the GNU General Public License as published by
  the Free Software Foundation, either version 3 of the License, or
  (at your option) any later version.
*/

#ifndef ATOMIC_V3_WIRE_NETWORK_H_INCLUDED
#define ATOMIC_V3_WIRE_NETWORK_H_INCLUDED

#include <array>
#include <filesystem>
#include <iosfwd>
#include <memory>
#include <new>
#include <string>
#include <string_view>
#include <type_traits>

#include "../atomic_v2/architecture.h"
#include "wire_contract.h"

namespace Stockfish::Eval::NNUE {
class AnyNetwork;
}

namespace Stockfish::Eval::NNUE::AtomicV3 {

inline constexpr u32 MaximumDescriptionBytes = 1U << 20;

enum class WireError : u8 {
    None,
    TruncatedHeader,
    WrongFileVersion,
    WrongNetworkHash,
    DescriptionTooLarge,
    TruncatedDescription,
    WrongTransformerHash,
    AllocationFailure,
    InvalidBiases,
    InvalidHmWeights,
    TruncatedCapturePair,
    InvalidKingBlastEp,
    TruncatedBlastRing,
    InvalidPsqt,
    WrongArchitectureHash,
    TruncatedArchitecture,
    PsqtRangeExceeded,
    AffineRangeExceeded,
    RawOutputRangeExceeded,
    InvalidPermutationShape,
    InvalidDenseRuntimeLayout,
    TrailingBytes,
    IoError,
    NotInitialized,
    OutputDescriptionTooLarge,
    OutputError,
    FileNotFound
};

const char* wire_error_message(WireError error) noexcept;

struct DenseStackParameters {
    // H9.3g deliberately keeps the byte-identical SFNNv15 tail in canonical
    // output-major wire order. Only the six mixed feature tensors have an ISA
    // runtime permutation at this milestone; a later backend may materialize
    // its dense execution layout from these authenticated canonical values.
    std::array<i32, Fc0Outputs>    fc0Biases;
    std::array<i8, Fc0WeightCount> fc0Weights;
    std::array<i32, Fc1Outputs>    fc1Biases;
    std::array<i8, Fc1WeightCount> fc1Weights;
    std::array<i32, Fc2Outputs>    fc2Biases;
    std::array<i8, Fc2WeightCount> fc2Weights;
};

struct SaveResult {
    WireError   code = WireError::None;
    std::string error;

    explicit operator bool() const noexcept { return code == WireError::None; }
};

template<typename IntType, std::size_t Count>
struct alignas(ParameterAlignment) AlignedParameterArray {
    AlignedParameterArray() noexcept {}
    AlignedParameterArray(const AlignedParameterArray&)            = default;
    AlignedParameterArray& operator=(const AlignedParameterArray&) = default;
    AlignedParameterArray(AlignedParameterArray&&)                 = default;
    AlignedParameterArray& operator=(AlignedParameterArray&&)      = default;

    IntType values[Count];
};

using BiasParameterStorage        = AlignedParameterArray<i16, BiasCount>;
using HmParameterStorage          = AlignedParameterArray<i16, HmWeightCount>;
using CapturePairParameterStorage = AlignedParameterArray<i8, CapturePairWeightCount>;
using KingBlastEpParameterStorage = AlignedParameterArray<i16, KingBlastEpWeightCount>;
using BlastRingParameterStorage   = AlignedParameterArray<i8, BlastRingWeightCount>;
using HmPsqtParameterStorage      = AlignedParameterArray<i32, HmPsqtWeightCount>;

class Network;
struct LoadResult;

[[nodiscard]] LoadResult load_candidate(std::istream& stream);
[[nodiscard]] LoadResult load_candidate(const std::filesystem::path& path);

// A successful instance is only created after the complete canonical wire has
// passed strict EOF and every numeric gate. Runtime parameters remain inline
// and trivially copyable so AnyNetwork can publish them through Stockfish's
// system-wide shared-memory/NUMA layer without process-local pointers.
class Network final {
   public:
    ~Network() = default;

    Network(const Network&)            = default;
    Network& operator=(const Network&) = default;
    Network(Network&&)                 = default;
    Network& operator=(Network&&)      = default;

    static constexpr u32 version() noexcept { return FileVersion; }
    static constexpr u32 feature_hash() noexcept { return FeatureHash; }
    static constexpr u32 transformer_descriptor_hash() noexcept {
        return TransformerDescriptorHash;
    }
    static constexpr u32 feature_transformer_hash() noexcept { return FeatureTransformerHash; }
    static constexpr u32 architecture_hash() noexcept { return ArchitectureHash; }
    static constexpr u32 network_hash() noexcept { return NetworkHash; }
    static constexpr HeaderIdentity header_identity() noexcept { return Header; }

    [[nodiscard]] std::string_view description() const noexcept {
        return {description_.data(), descriptionSize_};
    }
    [[nodiscard]] bool  simd_permuted() const noexcept { return simdPermuted_; }
    [[nodiscard]] usize get_content_hash() const noexcept { return contentHash_; }

    [[nodiscard]] const i16* biases() const noexcept { return biases_.values; }
    [[nodiscard]] const i16* hm_weights() const noexcept { return hmWeights_.values; }
    [[nodiscard]] const i8*  capture_pair_weights() const noexcept {
        return capturePairWeights_.values;
    }
    [[nodiscard]] const i16* king_blast_ep_weights() const noexcept {
        return kingBlastEpWeights_.values;
    }
    [[nodiscard]] const i8* blast_ring_weights() const noexcept { return blastRingWeights_.values; }
    [[nodiscard]] const i32* hm_psqt_weights() const noexcept { return hmPsqtWeights_.values; }
    [[nodiscard]] const std::array<DenseStackParameters, LayerStacks>&
    dense_stacks() const noexcept {
        return denseStacks_;
    }
    [[nodiscard]] const std::array<AtomicV2::NetworkArchitecture, LayerStacks>&
    dense_runtime_stacks() const noexcept {
        return denseRuntimeStacks_;
    }
    [[nodiscard]] bool dense_runtime_ready() const noexcept { return denseRuntimeReady_; }

    // Save uses the original authenticated description unless a replacement is
    // explicitly supplied. Both paths serialize canonical bytes from small
    // inverse-permutation block copies without mutating this Network.
    [[nodiscard]] SaveResult save(std::ostream& stream) const;
    [[nodiscard]] SaveResult save(std::ostream& stream, std::string_view description) const;

   private:
    Network() = default;

    bool       allocate_parameters() noexcept;
    bool       read_feature_parameters(std::istream& stream, WireError& code, std::string& error);
    bool       read_dense_parameters(std::istream& stream, WireError& code, std::string& error);
    bool       validate_numeric(WireError& code, std::string& error) const;
    bool       permute_feature_parameters() noexcept;
    bool       materialize_dense_runtime() noexcept;
    void       set_description(std::string_view description) noexcept;
    void       compute_content_hash() noexcept;
    SaveResult write_parameters(std::ostream& stream, std::string_view description) const;

    BiasParameterStorage        biases_{};
    HmParameterStorage          hmWeights_{};
    CapturePairParameterStorage capturePairWeights_{};
    KingBlastEpParameterStorage kingBlastEpWeights_{};
    BlastRingParameterStorage   blastRingWeights_{};
    HmPsqtParameterStorage      hmPsqtWeights_{};

    std::array<DenseStackParameters, LayerStacks>          denseStacks_;
    std::array<AtomicV2::NetworkArchitecture, LayerStacks> denseRuntimeStacks_;
    std::array<char, MaximumDescriptionBytes>              description_{};
    u32                                                    descriptionSize_   = 0;
    usize                                                  contentHash_       = 0;
    bool                                                   simdPermuted_      = false;
    bool                                                   denseRuntimeReady_ = false;

    friend LoadResult load_candidate(std::istream&);
    friend class ::Stockfish::Eval::NNUE::AnyNetwork;
};

struct LoadResult {
    WireError                code = WireError::None;
    std::unique_ptr<Network> network;
    std::string              description;
    std::filesystem::path    resolvedPath;
    std::string              error;

    LoadResult()                                 = default;
    LoadResult(LoadResult&&) noexcept            = default;
    LoadResult& operator=(LoadResult&&) noexcept = default;
    LoadResult(const LoadResult&)                = delete;
    LoadResult& operator=(const LoadResult&)     = delete;

    explicit operator bool() const noexcept {
        return code == WireError::None && network != nullptr;
    }
};

static_assert(Network::version() == 0xA70C0003u);
static_assert(Network::feature_hash() == 0xA3FBDBE8u);
static_assert(Network::transformer_descriptor_hash() == 0xCC31067Au);
static_assert(Network::feature_transformer_hash() == 0x6FCAD592u);
static_assert(Network::architecture_hash() == 0x63337116u);
static_assert(Network::network_hash() == 0x0CF9A484u);
static_assert(sizeof(DenseStackParameters) == DenseStackWireBytes);
static_assert(AtomicV2::ArchitectureHash == ArchitectureHash);
static_assert(AtomicV2::NetworkArchitecture::TransformedFeatureDimensions == Fc0Inputs);
static_assert(AtomicV2::NetworkArchitecture::FC_0_OUTPUTS == Fc0Outputs);
static_assert(AtomicV2::NetworkArchitecture::FC_1_OUTPUTS == Fc1Outputs);
static_assert(AtomicV2::LayerStacks == LayerStacks);
static_assert(alignof(BiasParameterStorage) == ParameterAlignment);
static_assert(alignof(HmParameterStorage) == ParameterAlignment);
static_assert(alignof(CapturePairParameterStorage) == ParameterAlignment);
static_assert(alignof(KingBlastEpParameterStorage) == ParameterAlignment);
static_assert(alignof(BlastRingParameterStorage) == ParameterAlignment);
static_assert(alignof(HmPsqtParameterStorage) == ParameterAlignment);
static_assert(sizeof(BiasParameterStorage) == BiasCount * sizeof(i16));
static_assert(sizeof(HmParameterStorage) == HmWeightCount * sizeof(i16));
static_assert(sizeof(CapturePairParameterStorage) == CapturePairWeightCount * sizeof(i8));
static_assert(sizeof(KingBlastEpParameterStorage) == KingBlastEpWeightCount * sizeof(i16));
static_assert(sizeof(BlastRingParameterStorage) == BlastRingWeightCount * sizeof(i8));
static_assert(sizeof(HmPsqtParameterStorage) == HmPsqtWeightCount * sizeof(i32));
static_assert(std::is_trivially_copy_constructible_v<Network>);
static_assert(std::is_trivially_move_constructible_v<Network>);
static_assert(std::is_trivially_destructible_v<Network>);

}  // namespace Stockfish::Eval::NNUE::AtomicV3

#endif  // ATOMIC_V3_WIRE_NETWORK_H_INCLUDED
