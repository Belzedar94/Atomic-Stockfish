/*
  Stockfish, a UCI chess playing engine derived from Glaurung 2.1
  Copyright (C) 2004-2026 The Stockfish developers (see AUTHORS file)

  Stockfish is free software: you can redistribute it and/or modify
  it under the terms of the GNU General Public License as published by
  the Free Software Foundation, either version 3 of the License, or
  (at your option) any later version.
*/

#ifndef ATOMIC_V2_CONSTANTS_H_INCLUDED
#define ATOMIC_V2_CONSTANTS_H_INCLUDED

#include <string_view>

#include "../../types.h"
#include "../features/half_ka_v2_atomic.h"

namespace Stockfish::Eval::NNUE::AtomicV2 {

using FeatureSet = ::Stockfish::Eval::NNUE::Features::HalfKAv2Atomic;

inline constexpr u32 FileVersion            = 0xA70C0002u;
inline constexpr u32 FeatureHash            = 0x5F234CB8u;
inline constexpr u32 FeatureTransformerHash = 0x5F2344B8u;
inline constexpr u32 ArchitectureHash       = 0x63337116u;
inline constexpr u32 NetworkHash            = 0x3C1035AEu;

inline constexpr IndexType L1          = 1024;
inline constexpr int       L2          = 32;
inline constexpr int       L3          = 32;
inline constexpr IndexType PSQTBuckets = 8;
inline constexpr IndexType LayerStacks = 8;

inline constexpr int FtMaxVal     = 255;
inline constexpr int HiddenOneVal = 128;

inline constexpr std::string_view BackendName        = "atomic-v2";
inline constexpr std::string_view BackendDisplayName = "AtomicNNUEV2 SFNNv15";

struct HeaderIdentity {
    u32 version;
    u32 featureHash;
    u32 featureTransformerHash;
    u32 architectureHash;
    u32 networkHash;
};

struct ShapeMetadata {
    IndexType featureDimensions;
    IndexType accumulatorDimensionsPerPerspective;
    IndexType transformedDimensions;
    int       fc0Outputs;
    int       fc1Outputs;
    int       outputs;
    IndexType psqtBuckets;
    IndexType layerStacks;
};

inline constexpr HeaderIdentity Header{FileVersion, FeatureHash, FeatureTransformerHash,
                                       ArchitectureHash, NetworkHash};

inline constexpr ShapeMetadata Shape{
  FeatureSet::Dimensions, L1, L1, L2, L3, 1, PSQTBuckets, LayerStacks};

static_assert(FeatureSet::HashValue == FeatureHash);
static_assert(FeatureSet::Dimensions == 45056);
static_assert((FeatureHash ^ (L1 * 2)) == FeatureTransformerHash);
static_assert((FeatureTransformerHash ^ ArchitectureHash) == NetworkHash);

}  // namespace Stockfish::Eval::NNUE::AtomicV2

#endif  // ATOMIC_V2_CONSTANTS_H_INCLUDED
