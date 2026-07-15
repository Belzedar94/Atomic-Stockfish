/*
  Atomic-Stockfish, a UCI chess playing engine derived from Stockfish
  Copyright (C) 2004-2026 The Stockfish developers (see AUTHORS file)

  Atomic-Stockfish is free software: you can redistribute it and/or modify
  it under the terms of the GNU General Public License as published by
  the Free Software Foundation, either version 3 of the License, or
  (at your option) any later version.
*/

#ifndef ATOMIC_V3_FULL_REFRESH_H_INCLUDED
#define ATOMIC_V3_FULL_REFRESH_H_INCLUDED

#include "blast_ring.h"
#include "capture_pair.h"
#include "hm_oracle.h"
#include "king_blast_ep.h"

namespace Stockfish {
class Position;
}

namespace Stockfish::Eval::NNUE::AtomicV3 {

inline constexpr IndexType FullRefreshMaximumActiveFeatures =
  HmMaximumActiveDimensions + CapturePairMaximumActiveFeatures + KingBlastEpMaximumActiveFeatures
  + BlastRingMaximumActiveFeatures;

// CapturePair's error domain is a strict superset of HM's and is already
// shared losslessly by both relation projectors.
using FullRefreshError = CapturePairError;

// Scalar correctness oracle for one accumulator perspective. Every member is
// caller-owned and self-contained; no Position or scratch reference survives
// the call. The four slice emissions deliberately retain their typed records
// so differentials can diagnose the first semantic layer that diverged.
struct FullRefreshEmission {
    HmEmission          hm{};
    CapturePairEmission capturePairs{};
    KingBlastEpEmission kingBlastEp{};
    BlastRingEmission   blastRing{};

    constexpr IndexType active_feature_count() const noexcept {
        return hm.size + capturePairs.size + kingBlastEp.size + blastRing.size;
    }
};

// Enumerates HM exactly once, enumerates CapturePair exactly once from that
// exact HM orientation, then feeds the same immutable CapturePair emission to
// KingBlastEP and BlastRing. Any error clears the entire result; malformed EP
// metadata is a successful no-EP projection, matching CapturePair semantics.
FullRefreshError emit_full_refresh(const CapturePairSnapshot& snapshot,
                                   Color                      perspective,
                                   FullRefreshEmission&       result);

// Takes exactly one board/side-to-move/EP snapshot before entering the scalar
// composition path. Callers must adjudicate king-absent Atomic terminals before
// NNUE; this adapter returns the mapped missing-king error instead of inventing
// an orientation.
FullRefreshError
emit_full_refresh(const Position& position, Color perspective, FullRefreshEmission& result);

const char* full_refresh_error_message(FullRefreshError error);

static_assert(FullRefreshMaximumActiveFeatures == 547);
static_assert(BlastRingPhysicalOffset + BlastRingPhysicalDimensions == 75084);

}  // namespace Stockfish::Eval::NNUE::AtomicV3

#endif  // ATOMIC_V3_FULL_REFRESH_H_INCLUDED
