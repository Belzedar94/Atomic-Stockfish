/*
  Atomic-Stockfish, a UCI chess playing engine derived from Stockfish
  Copyright (C) 2004-2026 The Stockfish developers (see AUTHORS file)

  Atomic-Stockfish is free software: you can redistribute it and/or modify
  it under the terms of the GNU General Public License as published by
  the Free Software Foundation, either version 3 of the License, or
  (at your option) any later version.
*/

#include "king_blast_ep.h"

#include "../../position.h"

namespace Stockfish::Eval::NNUE::AtomicV3 {
namespace {

bool activate(std::array<bool, KingBlastEpPhysicalDimensions>& active,
              bool&                                            anyActive,
              Square                                           orientedCenter,
              CapturePairActorRelation                         actorRelation,
              KingBlastEpRelationClass                         relationClass) {
    IndexType localIndex = KingBlastEpPhysicalDimensions;
    if (!king_blast_ep_index(orientedCenter, actorRelation, relationClass, localIndex))
        return false;
    active[localIndex] = true;
    anyActive          = true;
    return true;
}

}  // namespace

namespace Detail {

KingBlastEpError project_king_blast_ep_trusted(const CapturePairSnapshot& snapshot,
                                               Color                      perspective,
                                               const CapturePairEmission& capturePairs,
                                               KingBlastEpEmission&       result) {
    result             = {};
    result.orientation = capturePairs.orientation;

    std::array<Square, COLOR_NB> kingSquares{SQ_NONE, SQ_NONE};
    for (int squareIndex = 0; squareIndex < SQUARE_NB; ++squareIndex)
    {
        const Piece piece = snapshot.board[squareIndex];
        if (piece != NO_PIECE && type_of(piece) == KING)
            kingSquares[color_of(piece)] = Square(squareIndex);
    }

    std::array<bool, KingBlastEpPhysicalDimensions> active{};
    bool                                            anyActive = false;
    for (IndexType featureIndex = 0; featureIndex < capturePairs.size; ++featureIndex)
    {
        const CapturePairFeature& capturePair = capturePairs.features[featureIndex];
        if (!valid_capture_pair_relation(capturePair.actorRelation)
            || !is_ok(capturePair.orientedCenter))
        {
            result = {};
            return CapturePairError::NonCanonicalOrder;
        }

        const Color actorColor =
          capturePair.actorRelation == CapturePairActorRelation::Own ? perspective : ~perspective;
        const Square enemyKing = result.orientation.orient(kingSquares[~actorColor]);
        const Square ownKing   = result.orientation.orient(kingSquares[actorColor]);

        KingBlastEpRelationClass relationClass{};
        // Class zero authenticates the occupied KING-target relation, not an
        // interpretation of move legality or check status.
        if (!capturePair.enPassant && capturePair.targetClass == CapturePairTargetClass::King
            && capturePair.orientedCenter == enemyKing)
        {
            if (!activate(active, anyActive, capturePair.orientedCenter, capturePair.actorRelation,
                          KingBlastEpRelationClass::EnemyKingCenter))
            {
                result = {};
                return CapturePairError::NonCanonicalOrder;
            }
        }
        else if (king_blast_ep_relation_class(capturePair.orientedCenter, enemyKing, true,
                                              relationClass))
        {
            // The center case was handled only through a CP target KING, so
            // this branch can add directional enemy-king blast relations only.
            if (relationClass != KingBlastEpRelationClass::EnemyKingCenter
                && !activate(active, anyActive, capturePair.orientedCenter,
                             capturePair.actorRelation, relationClass))
            {
                result = {};
                return CapturePairError::NonCanonicalOrder;
            }
        }

        if (king_blast_ep_relation_class(capturePair.orientedCenter, ownKing, false, relationClass)
            && !activate(active, anyActive, capturePair.orientedCenter, capturePair.actorRelation,
                         relationClass))
        {
            result = {};
            return CapturePairError::NonCanonicalOrder;
        }

        if (capturePair.enPassant
            && !activate(active, anyActive, capturePair.orientedCenter, capturePair.actorRelation,
                         KingBlastEpRelationClass::EnPassantMarker))
        {
            result = {};
            return CapturePairError::NonCanonicalOrder;
        }
    }

    if (!anyActive)
        return CapturePairError::None;

    // The full 64x2x18 rectangle is scanned in local-index order, making the
    // output unique and canonical independently of CapturePair traversal.
    for (IndexType localIndex = 0; localIndex < KingBlastEpPhysicalDimensions; ++localIndex)
    {
        if (!active[localIndex])
            continue;
        if (result.size >= KingBlastEpMaximumActiveFeatures)
        {
            result = {};
            return CapturePairError::TooManyFeatures;
        }

        const IndexType relationClassIndex = localIndex % KingBlastEpRelationClasses;
        const IndexType centerAndRelation  = localIndex / KingBlastEpRelationClasses;
        const auto      actorRelation =
          CapturePairActorRelation(centerAndRelation % KingBlastEpActorRelations);
        const Square orientedCenter = Square(centerAndRelation / KingBlastEpActorRelations);
        const auto   relationClass  = KingBlastEpRelationClass(relationClassIndex);

        IndexType canonicalIndex = KingBlastEpPhysicalDimensions;
        if (!king_blast_ep_index(orientedCenter, actorRelation, relationClass, canonicalIndex)
            || canonicalIndex != localIndex)
        {
            result = {};
            return CapturePairError::NonCanonicalOrder;
        }

        const Color actorColor =
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

KingBlastEpError project_king_blast_ep(const CapturePairSnapshot& snapshot,
                                       Color                      perspective,
                                       const CapturePairEmission& capturePairs,
                                       KingBlastEpEmission&       result) {
    result = {};
    if (!well_formed_capture_pair_emission(snapshot, perspective, capturePairs))
        return CapturePairError::NonCanonicalOrder;
    return project_king_blast_ep_trusted(snapshot, perspective, capturePairs, result);
}

}  // namespace Detail

KingBlastEpError emit_king_blast_ep(const CapturePairSnapshot& snapshot,
                                    Color                      perspective,
                                    KingBlastEpEmission&       result) {
    result = {};

    // CapturePair is the sole source of candidate centers, actor relations and
    // validated geometric EP. Do not reconstruct any capture or EP relation
    // independently here. Composition callers can reuse this exact CP output
    // through project_king_blast_ep().
    CapturePairEmission    capturePairs{};
    const CapturePairError capturePairError =
      emit_capture_pairs(snapshot, perspective, capturePairs);
    if (capturePairError != CapturePairError::None)
        return capturePairError;
    return Detail::project_king_blast_ep_trusted(snapshot, perspective, capturePairs, result);
}

KingBlastEpError
emit_king_blast_ep(const Position& position, Color perspective, KingBlastEpEmission& result) {
    CapturePairSnapshot snapshot{};
    snapshot.sideToMove = position.side_to_move();
    snapshot.epSquare   = position.ep_square();
    for (int squareIndex = 0; squareIndex < SQUARE_NB; ++squareIndex)
        snapshot.board[squareIndex] = position.piece_on(Square(squareIndex));
    return emit_king_blast_ep(snapshot, perspective, result);
}

const char* king_blast_ep_error_message(KingBlastEpError error) {
    return capture_pair_error_message(error);
}

}  // namespace Stockfish::Eval::NNUE::AtomicV3
