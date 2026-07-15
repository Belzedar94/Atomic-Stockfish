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
              Square                                           orientedCenter,
              CapturePairActorRelation                         actorRelation,
              KingBlastEpRelationClass                         relationClass) {
    IndexType localIndex = KingBlastEpPhysicalDimensions;
    if (!king_blast_ep_index(orientedCenter, actorRelation, relationClass, localIndex))
        return false;
    active[localIndex] = true;
    return true;
}

bool valid_board_piece(Piece piece) {
    if (piece == NO_PIECE)
        return true;
    if (int(piece) >= PIECE_NB || (int(piece) >> 3) >= COLOR_NB)
        return false;
    const PieceType pieceType = type_of(piece);
    return pieceType >= PAWN && pieceType <= KING;
}

bool well_formed_capture_pair_emission(const CapturePairSnapshot& snapshot,
                                       Color                      perspective,
                                       const CapturePairEmission& capturePairs) {
    if ((perspective != WHITE && perspective != BLACK)
        || capturePairs.orientation.perspective != perspective
        || !is_canonical_joint_orientation(capturePairs.orientation)
        || capturePairs.size > CapturePairMaximumActiveFeatures)
        return false;

    std::array<IndexType, COLOR_NB> kingCounts{};
    std::array<Square, COLOR_NB>    kingSquares{SQ_NONE, SQ_NONE};
    for (int squareIndex = 0; squareIndex < SQUARE_NB; ++squareIndex)
    {
        const Piece piece = snapshot.board[squareIndex];
        if (!valid_board_piece(piece))
            return false;
        if (piece != NO_PIECE && type_of(piece) == KING)
        {
            ++kingCounts[color_of(piece)];
            kingSquares[color_of(piece)] = Square(squareIndex);
        }
    }
    if (kingCounts[WHITE] != 1 || kingCounts[BLACK] != 1
        || kingSquares[perspective] != capturePairs.orientation.ownKing)
        return false;

    for (IndexType featureIndex = 0; featureIndex < capturePairs.size; ++featureIndex)
    {
        const CapturePairFeature& feature = capturePairs.features[featureIndex];
        if (!valid_capture_pair_relation(feature.actorRelation) || !is_ok(feature.rawFrom)
            || !is_ok(feature.rawCenter) || !is_ok(feature.rawCaptured)
            || !is_ok(feature.orientedFrom) || !is_ok(feature.orientedCenter)
            || !is_ok(feature.orientedCaptured)
            || feature.physicalIndex != CapturePairPhysicalOffset + feature.localIndex
            || (featureIndex
                && capturePairs.features[featureIndex - 1].localIndex >= feature.localIndex)
            || feature.orientedFrom != capturePairs.orientation.orient(feature.rawFrom)
            || feature.orientedCenter != capturePairs.orientation.orient(feature.rawCenter)
            || feature.orientedCaptured != capturePairs.orientation.orient(feature.rawCaptured)
            || feature.actor == NO_PIECE || int(feature.actor) >= PIECE_NB
            || type_of(feature.actor) < PAWN || type_of(feature.actor) > QUEEN
            || feature.captured == NO_PIECE || int(feature.captured) >= PIECE_NB
            || type_of(feature.captured) < PAWN || type_of(feature.captured) > KING
            || snapshot.board[feature.rawFrom] != feature.actor
            || snapshot.board[feature.rawCaptured] != feature.captured
            || color_of(feature.captured) == color_of(feature.actor)
            || feature.actorRelation
                 != (color_of(feature.actor) == perspective ? CapturePairActorRelation::Own
                                                            : CapturePairActorRelation::Opp))
            return false;

        IndexType expectedEdge = CapturePairGeometryDimensions;
        if (!capture_pair_edge_ordinal(type_of(feature.actor), feature.actorRelation,
                                       feature.orientedFrom, feature.orientedCenter, expectedEdge)
            || feature.edgeOrdinal != expectedEdge)
            return false;

        IndexType expectedIndex = CapturePairPhysicalDimensions;
        if (feature.enPassant)
        {
            IndexType expectedEp = CapturePairEpEdgesPerRelation;
            if (feature.targetClass != CapturePairTargetClass::EnPassant
                || type_of(feature.actor) != PAWN || type_of(feature.captured) != PAWN
                || feature.rawCenter == feature.rawCaptured
                || snapshot.board[feature.rawCenter] != NO_PIECE
                || !capture_pair_ep_ordinal(feature.actorRelation, feature.orientedFrom,
                                            feature.orientedCenter, expectedEp)
                || feature.epOrdinal != expectedEp
                || !capture_pair_ep_index(feature.actorRelation, expectedEp, expectedIndex))
                return false;
        }
        else if (feature.rawCenter != feature.rawCaptured || feature.epOrdinal != 0
                 || IndexType(feature.targetClass) >= CapturePairNormalTargetClasses
                 || feature.targetClass
                      != CapturePairTargetClass(int(type_of(feature.captured)) - int(PAWN))
                 || !capture_pair_normal_index(feature.actorRelation, expectedEdge,
                                               feature.targetClass, expectedIndex))
            return false;

        if (feature.localIndex != expectedIndex)
            return false;
    }
    return true;
}

}  // namespace

namespace Detail {

KingBlastEpError project_king_blast_ep(const CapturePairSnapshot& snapshot,
                                       Color                      perspective,
                                       const CapturePairEmission& capturePairs,
                                       KingBlastEpEmission&       result) {
    result = {};
    if (!well_formed_capture_pair_emission(snapshot, perspective, capturePairs))
        return CapturePairError::NonCanonicalOrder;

    result.orientation = capturePairs.orientation;

    std::array<Square, COLOR_NB> kingSquares{SQ_NONE, SQ_NONE};
    for (int squareIndex = 0; squareIndex < SQUARE_NB; ++squareIndex)
    {
        const Piece piece = snapshot.board[squareIndex];
        if (piece != NO_PIECE && type_of(piece) == KING)
            kingSquares[color_of(piece)] = Square(squareIndex);
    }

    std::array<bool, KingBlastEpPhysicalDimensions> active{};
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
            if (!activate(active, capturePair.orientedCenter, capturePair.actorRelation,
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
                && !activate(active, capturePair.orientedCenter, capturePair.actorRelation,
                             relationClass))
            {
                result = {};
                return CapturePairError::NonCanonicalOrder;
            }
        }

        if (king_blast_ep_relation_class(capturePair.orientedCenter, ownKing, false, relationClass)
            && !activate(active, capturePair.orientedCenter, capturePair.actorRelation,
                         relationClass))
        {
            result = {};
            return CapturePairError::NonCanonicalOrder;
        }

        if (capturePair.enPassant
            && !activate(active, capturePair.orientedCenter, capturePair.actorRelation,
                         KingBlastEpRelationClass::EnPassantMarker))
        {
            result = {};
            return CapturePairError::NonCanonicalOrder;
        }
    }

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
    return Detail::project_king_blast_ep(snapshot, perspective, capturePairs, result);
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
