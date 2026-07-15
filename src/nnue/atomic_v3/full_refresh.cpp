/*
  Atomic-Stockfish, a UCI chess playing engine derived from Stockfish
  Copyright (C) 2004-2026 The Stockfish developers (see AUTHORS file)

  Atomic-Stockfish is free software: you can redistribute it and/or modify
  it under the terms of the GNU General Public License as published by
  the Free Software Foundation, either version 3 of the License, or
  (at your option) any later version.
*/

#include "full_refresh.h"

#include <utility>

#include "../../position.h"

namespace Stockfish::Eval::NNUE::AtomicV3 {
namespace {

bool same_orientation(const JointOrientation& lhs, const JointOrientation& rhs) {
    return lhs.perspective == rhs.perspective && lhs.ownKing == rhs.ownKing
        && lhs.orientedOwnKing == rhs.orientedOwnKing && lhs.verticalXor == rhs.verticalXor
        && lhs.horizontalXor == rhs.horizontalXor && lhs.kingBucket == rhs.kingBucket;
}

FullRefreshError fail(FullRefreshEmission& result, FullRefreshError error) {
    result = {};
    return error;
}

}  // namespace

FullRefreshError emit_full_refresh(const CapturePairSnapshot& snapshot,
                                   Color                      perspective,
                                   FullRefreshEmission&       result) {
    result = {};
    FullRefreshEmission candidate{};

    // This is the only HM enumeration in the combined path.
    const HmOracleError hmError = emit_hm_features(snapshot.board, perspective, candidate.hm);
    if (hmError != HmOracleError::None)
        return Detail::capture_pair_error_from_hm(hmError);

    // This is the only CapturePair enumeration. It consumes the exact HM
    // emission above instead of invoking the public CP wrapper, which would
    // enumerate HM again.
    const CapturePairError capturePairError = Detail::emit_capture_pairs_from_hm(
      snapshot, perspective, candidate.hm, candidate.capturePairs);
    if (capturePairError != CapturePairError::None)
        return capturePairError;

    // Both projectors receive the same immutable object. Neither reconstructs
    // attacks or EP metadata and neither owns or retains the input.
    const KingBlastEpError kingBlastEpError = Detail::project_king_blast_ep(
      snapshot, perspective, candidate.capturePairs, candidate.kingBlastEp);
    if (kingBlastEpError != CapturePairError::None)
        return kingBlastEpError;

    const BlastRingError blastRingError = Detail::project_blast_ring(
      snapshot, perspective, candidate.capturePairs, candidate.blastRing);
    if (blastRingError != CapturePairError::None)
        return blastRingError;

    const JointOrientation& orientation = candidate.hm.orientation;
    if (!same_orientation(orientation, candidate.capturePairs.orientation)
        || !same_orientation(orientation, candidate.kingBlastEp.orientation)
        || !same_orientation(orientation, candidate.blastRing.orientation))
        return fail(result, CapturePairError::NonCanonicalOrder);

    if (candidate.active_feature_count() > FullRefreshMaximumActiveFeatures)
        return fail(result, CapturePairError::TooManyFeatures);

    result = std::move(candidate);
    return CapturePairError::None;
}

FullRefreshError
emit_full_refresh(const Position& position, Color perspective, FullRefreshEmission& result) {
    return emit_full_refresh(make_capture_pair_snapshot(position), perspective, result);
}

const char* full_refresh_error_message(FullRefreshError error) {
    return capture_pair_error_message(error);
}

}  // namespace Stockfish::Eval::NNUE::AtomicV3
