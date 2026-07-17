/*
  Atomic-Stockfish, a UCI chess playing engine derived from Stockfish
  Copyright (C) 2004-2026 The Stockfish developers (see AUTHORS file)

  Atomic-Stockfish is free software: you can redistribute it and/or modify
  it under the terms of the GNU General Public License as published by
  the Free Software Foundation, either version 3 of the License, or
  (at your option) any later version.
*/

#include "full_refresh.h"

#include <algorithm>
#include <array>
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

template<typename Emission>
CapturePairError fail_projection(Emission& result, CapturePairError error) {
    result = {};
    return error;
}

// The public relation projectors intentionally remain defensive scalar
// oracles. Full refresh has a stronger local proof: HM and CapturePair were
// emitted successfully above from this exact immutable snapshot. These two
// compact projectors consume that proof and visit only indices that were
// actually touched, instead of clearing and scanning the 2,304/10,240-row
// physical domains for every perspective.

template<std::size_t Capacity>
bool append_touched(std::array<IndexType, Capacity>& indices, IndexType& size, IndexType index) {
    if (size >= Capacity)
        return false;
    indices[size++] = index;
    return true;
}

KingBlastEpError project_king_blast_ep_full_refresh_trusted(const HmEmission&          hm,
                                                            Color                      perspective,
                                                            const CapturePairEmission& capturePairs,
                                                            KingBlastEpEmission&       result) {
    result             = {};
    result.orientation = capturePairs.orientation;

    std::array<Square, COLOR_NB> kingSquares{SQ_NONE, SQ_NONE};
    for (IndexType featureIndex = 0; featureIndex < hm.size; ++featureIndex)
    {
        const HmFeature& feature = hm.features[featureIndex];
        if (type_of(feature.piece) == KING)
            kingSquares[color_of(feature.piece)] = feature.boardSquare;
    }

    constexpr IndexType                   MaximumTouched = CapturePairMaximumActiveFeatures * 3;
    std::array<IndexType, MaximumTouched> touched;
    IndexType                             touchedSize = 0;
    auto activate = [&](Square orientedCenter, CapturePairActorRelation actorRelation,
                        KingBlastEpRelationClass relationClass) {
        IndexType localIndex = KingBlastEpPhysicalDimensions;
        return king_blast_ep_index(orientedCenter, actorRelation, relationClass, localIndex)
            && append_touched(touched, touchedSize, localIndex);
    };

    for (IndexType featureIndex = 0; featureIndex < capturePairs.size; ++featureIndex)
    {
        const CapturePairFeature& capturePair = capturePairs.features[featureIndex];
        const Color               actorColor =
          capturePair.actorRelation == CapturePairActorRelation::Own ? perspective : ~perspective;
        const Square enemyKing = result.orientation.orient(kingSquares[~actorColor]);
        const Square ownKing   = result.orientation.orient(kingSquares[actorColor]);

        KingBlastEpRelationClass relationClass{};
        if (!capturePair.enPassant && capturePair.targetClass == CapturePairTargetClass::King
            && capturePair.orientedCenter == enemyKing)
        {
            if (!activate(capturePair.orientedCenter, capturePair.actorRelation,
                          KingBlastEpRelationClass::EnemyKingCenter))
                return fail_projection(result, CapturePairError::NonCanonicalOrder);
        }
        else if (king_blast_ep_relation_class(capturePair.orientedCenter, enemyKing, true,
                                              relationClass)
                 && relationClass != KingBlastEpRelationClass::EnemyKingCenter
                 && !activate(capturePair.orientedCenter, capturePair.actorRelation, relationClass))
            return fail_projection(result, CapturePairError::NonCanonicalOrder);

        if (king_blast_ep_relation_class(capturePair.orientedCenter, ownKing, false, relationClass)
            && !activate(capturePair.orientedCenter, capturePair.actorRelation, relationClass))
            return fail_projection(result, CapturePairError::NonCanonicalOrder);

        if (capturePair.enPassant
            && !activate(capturePair.orientedCenter, capturePair.actorRelation,
                         KingBlastEpRelationClass::EnPassantMarker))
            return fail_projection(result, CapturePairError::NonCanonicalOrder);
    }

    auto touchedEnd = touched.begin() + touchedSize;
    std::sort(touched.begin(), touchedEnd);
    touchedEnd                 = std::unique(touched.begin(), touchedEnd);
    const IndexType uniqueSize = IndexType(touchedEnd - touched.begin());
    if (uniqueSize > KingBlastEpMaximumActiveFeatures)
        return fail_projection(result, CapturePairError::TooManyFeatures);

    for (auto current = touched.begin(); current != touchedEnd; ++current)
    {
        const IndexType localIndex         = *current;
        const IndexType relationClassIndex = localIndex % KingBlastEpRelationClasses;
        const IndexType centerAndRelation  = localIndex / KingBlastEpRelationClasses;
        const auto      actorRelation =
          CapturePairActorRelation(centerAndRelation % KingBlastEpActorRelations);
        const Square orientedCenter = Square(centerAndRelation / KingBlastEpActorRelations);
        const auto   relationClass  = KingBlastEpRelationClass(relationClassIndex);
        const Color  actorColor =
          actorRelation == CapturePairActorRelation::Own ? perspective : ~perspective;

        Square rawRelatedKing = SQ_NONE;
        if (relationClassIndex <= IndexType(KingBlastEpRelationClass::EnemyKingNW))
            rawRelatedKing = kingSquares[~actorColor];
        else if (relationClassIndex <= IndexType(KingBlastEpRelationClass::OwnKingNW))
            rawRelatedKing = kingSquares[actorColor];

        KingBlastEpFeature& feature = result.features[result.size++];
        feature.rawCenter           = result.orientation.orient(orientedCenter);
        feature.orientedCenter      = orientedCenter;
        feature.rawRelatedKing      = rawRelatedKing;
        feature.orientedRelatedKing = result.orientation.orient(rawRelatedKing);
        feature.actorRelation       = actorRelation;
        feature.relationClass       = relationClass;
        feature.localIndex          = localIndex;
        feature.physicalIndex       = KingBlastEpPhysicalOffset + localIndex;
        feature.enPassantMarker     = relationClass == KingBlastEpRelationClass::EnPassantMarker;
    }

    return CapturePairError::None;
}

struct FullRefreshCaptureCenterGroup {
    Bitboard  origins;
    Bitboard  epCapturedPawns;
    IndexType groupIndex;
};

bool full_refresh_collateral_class(Piece piece, BlastRingCollateralClass& result) {
    switch (type_of(piece))
    {
    case PAWN :
        result = BlastRingCollateralClass::AdjacentPawnSurvives;
        return true;
    case KNIGHT :
        result = BlastRingCollateralClass::Knight;
        return true;
    case BISHOP :
        result = BlastRingCollateralClass::Bishop;
        return true;
    case ROOK :
        result = BlastRingCollateralClass::Rook;
        return true;
    case QUEEN :
        result = BlastRingCollateralClass::Queen;
        return true;
    default :
        return false;
    }
}

BlastRingError project_blast_ring_full_refresh_trusted(const CapturePairSnapshot& snapshot,
                                                       Color                      perspective,
                                                       const CapturePairEmission& capturePairs,
                                                       BlastRingEmission&         result) {
    result             = {};
    result.orientation = capturePairs.orientation;

    constexpr IndexType GroupDimensions = BlastRingCenterDimensions * BlastRingActorRelations;
    std::array<FullRefreshCaptureCenterGroup, GroupDimensions> groups;
    std::array<IndexType, GroupDimensions>                     groupSlots;
    groupSlots.fill(GroupDimensions);
    IndexType groupSize = 0;
    for (IndexType featureIndex = 0; featureIndex < capturePairs.size; ++featureIndex)
    {
        const CapturePairFeature& capturePair = capturePairs.features[featureIndex];
        const IndexType groupIndex = IndexType(capturePair.orientedCenter) * BlastRingActorRelations
                                   + IndexType(capturePair.actorRelation);
        IndexType& groupSlot = groupSlots[groupIndex];
        if (groupSlot == GroupDimensions)
        {
            groupSlot         = groupSize++;
            groups[groupSlot] = {0, 0, groupIndex};
        }
        FullRefreshCaptureCenterGroup& group = groups[groupSlot];
        group.origins |= Bitboard(1) << int(capturePair.rawFrom);
        if (capturePair.enPassant)
            group.epCapturedPawns |= Bitboard(1) << int(capturePair.rawCaptured);
    }

    constexpr IndexType                   MaximumTouched = GroupDimensions * BlastRingDirections;
    std::array<IndexType, MaximumTouched> touched;
    IndexType                             touchedSize = 0;
    for (IndexType groupSlot = 0; groupSlot < groupSize; ++groupSlot)
    {
        const FullRefreshCaptureCenterGroup& group = groups[groupSlot];
        const auto                           actorRelation =
          CapturePairActorRelation(group.groupIndex % BlastRingActorRelations);
        const Square orientedCenter = Square(group.groupIndex / BlastRingActorRelations);

        Square soleOrigin = SQ_NONE;
        if (capture_pair_popcount(group.origins) == 1)
            for (int squareIndex = 0; squareIndex < SQUARE_NB; ++squareIndex)
                if (group.origins & (Bitboard(1) << squareIndex))
                {
                    soleOrigin = Square(squareIndex);
                    break;
                }

        for (IndexType directionIndex = 0; directionIndex < BlastRingDirections; ++directionIndex)
        {
            const auto direction          = BlastRingDirection(directionIndex);
            Square     orientedCollateral = SQ_NONE;
            if (!blast_ring_directional_square(orientedCenter, direction, orientedCollateral))
                continue;

            const Square rawCollateral = result.orientation.orient(orientedCollateral);
            const Piece  collateral    = snapshot.board[rawCollateral];
            if (collateral == NO_PIECE || type_of(collateral) == KING || rawCollateral == soleOrigin
                || (group.epCapturedPawns & (Bitboard(1) << int(rawCollateral))))
                continue;

            BlastRingCollateralClass collateralClass{};
            if (!full_refresh_collateral_class(collateral, collateralClass))
                return fail_projection(result, CapturePairError::NonCanonicalOrder);
            const BlastRingCollateralRelation collateralRelation =
              color_of(collateral) == perspective ? BlastRingCollateralRelation::Own
                                                  : BlastRingCollateralRelation::Opp;
            IndexType localIndex = BlastRingPhysicalDimensions;
            if (!blast_ring_index(orientedCenter, actorRelation, collateralRelation, direction,
                                  collateralClass, localIndex)
                || !append_touched(touched, touchedSize, localIndex))
                return fail_projection(result, CapturePairError::NonCanonicalOrder);
        }
    }

    auto touchedEnd = touched.begin() + touchedSize;
    std::sort(touched.begin(), touchedEnd);
    touchedEnd                 = std::unique(touched.begin(), touchedEnd);
    const IndexType uniqueSize = IndexType(touchedEnd - touched.begin());
    if (uniqueSize > BlastRingMaximumActiveFeatures)
        return fail_projection(result, CapturePairError::TooManyFeatures);

    for (auto current = touched.begin(); current != touchedEnd; ++current)
    {
        const IndexType localIndex = *current;
        IndexType       coordinate = localIndex;
        const auto      collateralClass =
          BlastRingCollateralClass(coordinate % BlastRingCollateralClasses);
        coordinate /= BlastRingCollateralClasses;
        const auto direction = BlastRingDirection(coordinate % BlastRingDirections);
        coordinate /= BlastRingDirections;
        const auto collateralRelation =
          BlastRingCollateralRelation(coordinate % BlastRingCollateralRelations);
        coordinate /= BlastRingCollateralRelations;
        const auto   actorRelation = CapturePairActorRelation(coordinate % BlastRingActorRelations);
        const Square orientedCenter     = Square(coordinate / BlastRingActorRelations);
        Square       orientedCollateral = SQ_NONE;
        if (!blast_ring_directional_square(orientedCenter, direction, orientedCollateral))
            return fail_projection(result, CapturePairError::NonCanonicalOrder);

        const Square      rawCenter     = result.orientation.orient(orientedCenter);
        const Square      rawCollateral = result.orientation.orient(orientedCollateral);
        const Piece       collateral    = snapshot.board[rawCollateral];
        BlastRingFeature& feature       = result.features[result.size++];
        feature.rawCenter               = rawCenter;
        feature.orientedCenter          = orientedCenter;
        feature.rawCollateral           = rawCollateral;
        feature.orientedCollateral      = orientedCollateral;
        feature.collateral              = collateral;
        feature.actorRelation           = actorRelation;
        feature.collateralRelation      = collateralRelation;
        feature.direction               = direction;
        feature.collateralClass         = collateralClass;
        feature.localIndex              = localIndex;
        feature.physicalIndex           = BlastRingPhysicalOffset + localIndex;
        feature.adjacentPawnSurvives =
          collateralClass == BlastRingCollateralClass::AdjacentPawnSurvives;
    }

    return CapturePairError::None;
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
    const KingBlastEpError kingBlastEpError = project_king_blast_ep_full_refresh_trusted(
      candidate.hm, perspective, candidate.capturePairs, candidate.kingBlastEp);
    if (kingBlastEpError != CapturePairError::None)
        return kingBlastEpError;

    const BlastRingError blastRingError = project_blast_ring_full_refresh_trusted(
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
