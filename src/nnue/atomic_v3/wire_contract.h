/*
  Atomic-Stockfish, a UCI chess playing engine derived from Stockfish
  Copyright (C) 2004-2026 The Stockfish developers (see AUTHORS file)

  Atomic-Stockfish is free software: you can redistribute it and/or modify
  it under the terms of the GNU General Public License as published by
  the Free Software Foundation, either version 3 of the License, or
  (at your option) any later version.
*/

#ifndef ATOMIC_V3_WIRE_CONTRACT_H_INCLUDED
#define ATOMIC_V3_WIRE_CONTRACT_H_INCLUDED

#include <array>
#include <cstddef>
#include <string_view>

#include "blast_ring.h"
#include "capture_pair.h"
#include "hm_oracle.h"
#include "king_blast_ep.h"

namespace Stockfish::Eval::NNUE::AtomicV3 {

inline constexpr u32 FnvOffsetBasis = 0x811C9DC5u;
inline constexpr u32 FnvPrime       = 0x01000193u;

constexpr u32 fnv1a_32(std::string_view bytes) noexcept {
    u32 hash = FnvOffsetBasis;
    for (const char byte : bytes)
    {
        hash ^= static_cast<u8>(byte);
        hash *= FnvPrime;
    }
    return hash;
}

constexpr bool is_ascii(std::string_view bytes) noexcept {
    for (const char byte : bytes)
        if (static_cast<unsigned char>(byte) > 0x7Fu)
            return false;
    return true;
}

constexpr u32 rotate_left_one(u32 value) noexcept { return (value << 1) | (value >> 31); }

inline constexpr std::string_view HmDescriptor =
  R"(HalfKAv2Atomic_hm|v1|square=A1_0_rank_major|offset=0|axes=king_bucket_h8_to_e1:32,piece_plane:OWN_P,OPP_P,OWN_N,OPP_N,OWN_B,OPP_B,OWN_R,OPP_R,OWN_Q,OPP_Q,MERGED_K,square:64|physical=22528|training_planes=OWN_P,OPP_P,OWN_N,OPP_N,OWN_B,OPP_B,OWN_R,OPP_R,OWN_Q,OPP_Q,OWN_K,OPP_K|training=24576|virtual=768|factor=bucket_plus_virtual_all_1032_then_export|king_merge=opp_then_own_king_square_all_1032|orientation=per_perspective_black_xor56_mirror_if_pre_h_king_file_lt4_shared_all_slices|royal=KING|dtype=i16|wire=i16_sleb_feature_major_output1024_contiguous_canonical_unpermuted_permute16|psqt=hm_only_i32_sleb_feature_major_bucket8_contiguous_after_relations)";

inline constexpr std::string_view CapturePairDescriptor =
  R"(AtomicCapturePair|v2-compact|square=A1_0_rank_major|offset=22528|axes=normal:actor_rel_accumulator:OWN,OPP;edge:PAWN84@0,KNIGHT336@84,BISHOP560@420,ROOK896@980,QUEEN1456@1876;target_enemy_of_actor:PAWN,KNIGHT,BISHOP,ROOK,QUEEN,KING;normal_local=((actor_rel*3332+edge)*6+target)@offset0_count39984;ep_tail=(actor_rel*14+ep_ordinal)@offset39984_count28;ep_edges=OWN_rank5_to6_OPP_rank4_to3_oriented_from_then_center_asc|physical=40012|physical_index=22528+local|orientation=per_perspective_black_xor56_mirror_if_pre_h_king_file_lt4_shared_all_slices|actor_rel=color_only|pawn_edge=OWN_north_OPP_south_no_extra_flip|occupancy=stop_first_occupied_emit_enemy|pins_checks_self_blast=unfiltered|promotion=one_pawn_relation_no_choice_expansion_current_piece_types|ep=validated_stm_geometric_cold_tail_fail_closed_source_for_kbr_ring|impossible_ep_rows=eliminated_no_holes|order=local_asc_unique|ownership=caller_owned|thread=pure_reentrant_immutable_position|king_actor=excluded|pawn_push=excluded|dtype=i8|wire=i8_raw_signed_twos_feature_major_output1024_contiguous_canonical_unpermuted_permute8_after_hm|psqt=none)";

inline constexpr std::string_view KingBlastEpDescriptor =
  R"(AtomicKingBlastEP|v1|offset=62540|axes=center:64;actor_rel_accumulator:OWN,OPP;king_rel_actor:ENEMY_KING_CENTER,ENEMY_KING_N,ENEMY_KING_NE,ENEMY_KING_E,ENEMY_KING_SE,ENEMY_KING_S,ENEMY_KING_SW,ENEMY_KING_W,ENEMY_KING_NW,OWN_KING_N,OWN_KING_NE,OWN_KING_E,OWN_KING_SE,OWN_KING_S,OWN_KING_SW,OWN_KING_W,OWN_KING_NW;class:EN_PASSANT_MARKER|local=((center*2+actor_rel)*18+class)@0..2303|physical=2304@62540..64843|orientation=per_perspective_black_xor56_mirror_if_pre_h_king_file_lt4_shared_all_slices|source=single_exact_unfiltered_cp_emission_including_validated_geometric_ep|offset=related_king_minus_center_in_joint_frame_exact_dfdr|activation=boolean_sorted_unique_capture_center_set|ep=landing_center_dedup_offcenter_pawn_excluded_fail_closed|rectangle=full_no_holes|error=cp_mapped_empty_no_partial|ownership=caller_owned|thread=pure_reentrant_immutable_position|max=17x2_plus1_eq35|dtype=i16|wire=i16_sleb_feature_major_output1024_contiguous_canonical_unpermuted_permute16_after_capture_pair|psqt=none)";

inline constexpr std::string_view BlastRingDescriptor =
  R"(AtomicBlastRing|v1|offset=64844|axes=center:64;actor_rel_accumulator:OWN,OPP;collateral_rel_accumulator:OWN,OPP;offset:N,NE,E,SE,S,SW,W,NW;class:KNIGHT,BISHOP,ROOK,QUEEN,ADJACENT_PAWN_SURVIVES|local=((((center*2+actor_rel)*2+collateral_rel)*8+offset)*5+class)@0..10239|physical=10240@64844..75083|orientation=per_perspective_black_xor56_mirror_if_pre_h_king_file_lt4_shared_all_slices|source=single_exact_unfiltered_cp_emission_including_validated_geometric_ep|group=center_actor_rel_distinct_origins|offset=collateral_minus_center_in_joint_frame_exact_dfdr|activation=boolean_sorted_unique_capture_center_union|origin=exclude_only_single_distinct_origin_group_retain_all_origins_if_multi|nonpawn=current_NBRQ_explodes|pawn=adjacent_survives_except_single_origin_or_ep_captured|ep=landing_center_malformed_omitted_normal_preserved|ep_captured_pawn=oriented_center_minus_own8_or_opp_minus8_always_excluded_even_multi|kings=separate|rectangle=full_no_holes|error=cp_mapped_empty_no_partial|ownership=caller_owned|thread=pure_reentrant_immutable_position|max=30x8_eq240|dtype=i8|wire=i8_raw_signed_twos_feature_major_output1024_contiguous_canonical_unpermuted_permute8_after_king_blast|psqt=none)";

inline constexpr u32 HmHash          = 0xA34A8666u;
inline constexpr u32 CapturePairHash = 0x9AEDB186u;
inline constexpr u32 KingBlastEpHash = 0xF5172BC0u;
inline constexpr u32 BlastRingHash   = 0x38377946u;

inline constexpr std::array<u32, 4> SliceHashes{
  {HmHash, CapturePairHash, KingBlastEpHash, BlastRingHash}};

constexpr u32 fold_slice_hashes(const std::array<u32, 4>& hashes) noexcept {
    u32 folded = 0;
    for (const u32 hash : hashes)
        folded = rotate_left_one(folded) ^ hash;
    return folded;
}

inline constexpr u32 FeatureHash = 0xA3FBDBE8u;

inline constexpr std::string_view TransformerDescriptor =
  R"(AtomicNNUEV3Transformer|v1|wire=biases:i16_sleb[1024],hm:i16_sleb[22528x1024],cp:i8_raw[40012x1024],kbr:i16_sleb[2304x1024],ring:i8_raw[10240x1024],hm_psqt:i32_sleb[22528x8],dense:8x(architecture_hash_u32=0x63337116,sfnnv15)|layout=each_feature_slice_feature_major_output1024_contiguous;hm_psqt_feature_major_bucket8_contiguous|sleb=COMPRESSED_LEB128_then_u32_le_byte_count_canonical_signed|file=canonical_unpermuted|raw_i8=signed_twos_complement|load_permute=biases,hm,kbr:i16_block16;cp,ring:i8_block8;hm_psqt:none|permute_order=avx512[0,2,4,6,1,3,5,7],avx2_lasx[0,2,1,3,4,6,5,7],other[0,1,2,3,4,5,6,7]|save=unpermute_copy_inverse_order_no_live_mutation|psqt=hm_only_same_virtual_factor_coalesce_and_12to11_export|dense_tail=byte_identical_atomic_v2_sfnnv15_architecture_0x63337116|strict_eof=true)";

inline constexpr u32 FileVersion               = 0xA70C0003u;
inline constexpr u32 TransformerDescriptorHash = 0xCC31067Au;
inline constexpr u32 FeatureTransformerHash    = 0x6FCAD592u;
inline constexpr u32 ArchitectureHash          = 0x63337116u;
inline constexpr u32 NetworkHash               = 0x0CF9A484u;

inline constexpr IndexType   AccumulatorDimensions = HmAccumulatorOutputs;
inline constexpr IndexType   PsqtBuckets           = HmPsqtOutputs;
inline constexpr IndexType   LayerStacks           = 8;
inline constexpr std::size_t ParameterAlignment    = 64;

inline constexpr std::size_t BiasCount = AccumulatorDimensions;
inline constexpr std::size_t HmWeightCount =
  std::size_t(HmPhysicalDimensions) * AccumulatorDimensions;
inline constexpr std::size_t CapturePairWeightCount =
  std::size_t(CapturePairPhysicalDimensions) * AccumulatorDimensions;
inline constexpr std::size_t KingBlastEpWeightCount =
  std::size_t(KingBlastEpPhysicalDimensions) * AccumulatorDimensions;
inline constexpr std::size_t BlastRingWeightCount =
  std::size_t(BlastRingPhysicalDimensions) * AccumulatorDimensions;
inline constexpr std::size_t HmPsqtWeightCount = std::size_t(HmPhysicalDimensions) * PsqtBuckets;

inline constexpr IndexType Fc0Inputs  = 1024;
inline constexpr IndexType Fc0Outputs = 32;
inline constexpr IndexType Fc1Inputs  = 64;
inline constexpr IndexType Fc1Outputs = 32;
inline constexpr IndexType Fc2Inputs  = 128;
inline constexpr IndexType Fc2Outputs = 1;

inline constexpr std::size_t Fc0WeightCount = std::size_t(Fc0Inputs) * Fc0Outputs;
inline constexpr std::size_t Fc1WeightCount = std::size_t(Fc1Inputs) * Fc1Outputs;
inline constexpr std::size_t Fc2WeightCount = std::size_t(Fc2Inputs) * Fc2Outputs;

inline constexpr std::size_t FeatureParameterBytes =
  BiasCount * sizeof(i16) + HmWeightCount * sizeof(i16) + CapturePairWeightCount * sizeof(i8)
  + KingBlastEpWeightCount * sizeof(i16) + BlastRingWeightCount * sizeof(i8)
  + HmPsqtWeightCount * sizeof(i32);

inline constexpr std::size_t DenseStackWireBytes =
  Fc0Outputs * sizeof(i32) + Fc0WeightCount * sizeof(i8) + Fc1Outputs * sizeof(i32)
  + Fc1WeightCount * sizeof(i8) + Fc2Outputs * sizeof(i32) + Fc2WeightCount * sizeof(i8);

struct HeaderIdentity {
    u32 version;
    u32 featureHash;
    u32 transformerDescriptorHash;
    u32 featureTransformerHash;
    u32 architectureHash;
    u32 networkHash;
};

inline constexpr HeaderIdentity Header{
  FileVersion,      FeatureHash, TransformerDescriptorHash, FeatureTransformerHash,
  ArchitectureHash, NetworkHash};

static_assert(HmDescriptor.size() == 656 && fnv1a_32(HmDescriptor) == HmHash);
static_assert(CapturePairDescriptor.size() == 1107
              && fnv1a_32(CapturePairDescriptor) == CapturePairHash);
static_assert(KingBlastEpDescriptor.size() == 1004
              && fnv1a_32(KingBlastEpDescriptor) == KingBlastEpHash);
static_assert(BlastRingDescriptor.size() == 1192 && fnv1a_32(BlastRingDescriptor) == BlastRingHash);
static_assert(is_ascii(HmDescriptor) && is_ascii(CapturePairDescriptor)
              && is_ascii(KingBlastEpDescriptor) && is_ascii(BlastRingDescriptor));
static_assert(fold_slice_hashes(SliceHashes) == FeatureHash);

static_assert(TransformerDescriptor.size() == 799);
static_assert(is_ascii(TransformerDescriptor));
static_assert(fnv1a_32(TransformerDescriptor) == TransformerDescriptorHash);
static_assert((FeatureHash ^ (AccumulatorDimensions * 2) ^ TransformerDescriptorHash)
              == FeatureTransformerHash);
static_assert((FeatureTransformerHash ^ ArchitectureHash) == NetworkHash);

static_assert(HmPhysicalDimensions == 22528 && CapturePairPhysicalDimensions == 40012);
static_assert(KingBlastEpPhysicalDimensions == 2304 && BlastRingPhysicalDimensions == 10240);
static_assert(BlastRingPhysicalOffset + BlastRingPhysicalDimensions == 75084);
static_assert(AccumulatorDimensions == 1024 && PsqtBuckets == 8 && LayerStacks == 8);
static_assert(ParameterAlignment == 64);
static_assert(Fc0Inputs == 1024 && Fc0Outputs == 32);
static_assert(Fc1Inputs == 64 && Fc1Outputs == 32);
static_assert(Fc2Inputs == 128 && Fc2Outputs == 1);
static_assert(FeatureParameterBytes == 103036928);
static_assert(DenseStackWireBytes == 35204);

}  // namespace Stockfish::Eval::NNUE::AtomicV3

#endif  // ATOMIC_V3_WIRE_CONTRACT_H_INCLUDED
