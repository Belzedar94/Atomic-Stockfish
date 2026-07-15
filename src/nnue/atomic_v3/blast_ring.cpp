/*
  Atomic-Stockfish, a UCI chess playing engine derived from Stockfish
  Copyright (C) 2004-2026 The Stockfish developers (see AUTHORS file)

  Atomic-Stockfish is free software: you can redistribute it and/or modify
  it under the terms of the GNU General Public License as published by
  the Free Software Foundation, either version 3 of the License, or
  (at your option) any later version.
*/

#include "blast_ring.h"

#include "../../position.h"

namespace Stockfish::Eval::NNUE::AtomicV3 {
namespace {

struct CaptureCenterGroup {
    bool                        active          = false;
    Square                      rawCenter       = SQ_NONE;
    IndexType                   distinctOrigins = 0;
    std::array<bool, SQUARE_NB> origins{};
    std::array<bool, SQUARE_NB> epCapturedPawns{};
};

// Keep the local projection defensive even after the shared trusted-emission
// validator has authenticated the board. Numeric validation must precede
// type_of()/color_of() so enum holes never reach those helpers.
bool valid_nonempty_piece(Piece piece) {
    const int code     = int(piece);
    const int typeCode = code & 7;
    return code > int(NO_PIECE) && code < PIECE_NB && (code >> 3) < COLOR_NB
        && typeCode >= int(PAWN) && typeCode <= int(KING);
}

bool collateral_class(Piece piece, BlastRingCollateralClass& result) {
    if (!valid_nonempty_piece(piece))
        return false;
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

BlastRingCollateralRelation collateral_relation(Piece piece, Color perspective) {
    return color_of(piece) == perspective ? BlastRingCollateralRelation::Own
                                          : BlastRingCollateralRelation::Opp;
}

}  // namespace

namespace Detail {

BlastRingError project_blast_ring(const CapturePairSnapshot& snapshot,
                                  Color                      perspective,
                                  const CapturePairEmission& capturePairs,
                                  BlastRingEmission&         result) {
    result = {};
    if (!well_formed_capture_pair_emission(snapshot, perspective, capturePairs))
        return CapturePairError::NonCanonicalOrder;

    result.orientation = capturePairs.orientation;

    std::array<CaptureCenterGroup, BlastRingCenterDimensions * BlastRingActorRelations> groups{};
    for (IndexType featureIndex = 0; featureIndex < capturePairs.size; ++featureIndex)
    {
        const CapturePairFeature& capturePair = capturePairs.features[featureIndex];
        const IndexType groupIndex = IndexType(capturePair.orientedCenter) * BlastRingActorRelations
                                   + IndexType(capturePair.actorRelation);
        CaptureCenterGroup& group = groups[groupIndex];
        if (!group.active)
        {
            group.active    = true;
            group.rawCenter = capturePair.rawCenter;
        }
        else if (group.rawCenter != capturePair.rawCenter)
        {
            result = {};
            return CapturePairError::NonCanonicalOrder;
        }

        if (!group.origins[capturePair.rawFrom])
        {
            group.origins[capturePair.rawFrom] = true;
            ++group.distinctOrigins;
        }
        if (capturePair.enPassant)
            group.epCapturedPawns[capturePair.rawCaptured] = true;
    }

    std::array<bool, BlastRingPhysicalDimensions> active{};
    for (IndexType groupIndex = 0; groupIndex < groups.size(); ++groupIndex)
    {
        const CaptureCenterGroup& group = groups[groupIndex];
        if (!group.active)
            continue;
        if (group.distinctOrigins == 0)
        {
            result = {};
            return CapturePairError::NonCanonicalOrder;
        }

        const auto   actorRelation = CapturePairActorRelation(groupIndex % BlastRingActorRelations);
        const Square orientedCenter = Square(groupIndex / BlastRingActorRelations);
        if (!valid_capture_pair_relation(actorRelation) || !is_ok(orientedCenter)
            || result.orientation.orient(group.rawCenter) != orientedCenter)
        {
            result = {};
            return CapturePairError::NonCanonicalOrder;
        }

        Square soleOrigin = SQ_NONE;
        if (group.distinctOrigins == 1)
            for (int squareIndex = 0; squareIndex < SQUARE_NB; ++squareIndex)
                if (group.origins[squareIndex])
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
            if (collateral == NO_PIECE)
                continue;
            if (!valid_nonempty_piece(collateral))
            {
                result = {};
                return CapturePairError::NonCanonicalOrder;
            }
            if (type_of(collateral) == KING || rawCollateral == soleOrigin
                || group.epCapturedPawns[rawCollateral])
                continue;

            BlastRingCollateralClass collateralClass{};
            if (!collateral_class(collateral, collateralClass))
            {
                result = {};
                return CapturePairError::NonCanonicalOrder;
            }
            const BlastRingCollateralRelation collateralRelation =
              collateral_relation(collateral, perspective);

            IndexType localIndex = BlastRingPhysicalDimensions;
            if (!blast_ring_index(orientedCenter, actorRelation, collateralRelation, direction,
                                  collateralClass, localIndex))
            {
                result = {};
                return CapturePairError::NonCanonicalOrder;
            }
            active[localIndex] = true;
        }
    }

    // Scan the complete compact rectangle so output remains a sorted boolean
    // union even if upstream CapturePair traversal changes.
    for (IndexType localIndex = 0; localIndex < BlastRingPhysicalDimensions; ++localIndex)
    {
        if (!active[localIndex])
            continue;
        if (result.size >= BlastRingMaximumActiveFeatures)
        {
            result = {};
            return CapturePairError::TooManyFeatures;
        }

        IndexType  coordinate = localIndex;
        const auto collateralClass =
          BlastRingCollateralClass(coordinate % BlastRingCollateralClasses);
        coordinate /= BlastRingCollateralClasses;
        const auto direction = BlastRingDirection(coordinate % BlastRingDirections);
        coordinate /= BlastRingDirections;
        const auto collateralRelation =
          BlastRingCollateralRelation(coordinate % BlastRingCollateralRelations);
        coordinate /= BlastRingCollateralRelations;
        const auto   actorRelation = CapturePairActorRelation(coordinate % BlastRingActorRelations);
        const Square orientedCenter = Square(coordinate / BlastRingActorRelations);

        IndexType canonicalIndex     = BlastRingPhysicalDimensions;
        Square    orientedCollateral = SQ_NONE;
        if (!blast_ring_index(orientedCenter, actorRelation, collateralRelation, direction,
                              collateralClass, canonicalIndex)
            || canonicalIndex != localIndex
            || !blast_ring_directional_square(orientedCenter, direction, orientedCollateral))
        {
            result = {};
            return CapturePairError::NonCanonicalOrder;
        }

        const Square             rawCenter     = result.orientation.orient(orientedCenter);
        const Square             rawCollateral = result.orientation.orient(orientedCollateral);
        const Piece              collateral    = snapshot.board[rawCollateral];
        BlastRingCollateralClass expectedClass{};
        if (!collateral_class(collateral, expectedClass) || expectedClass != collateralClass
            || collateral_relation(collateral, perspective) != collateralRelation)
        {
            result = {};
            return CapturePairError::NonCanonicalOrder;
        }

        BlastRingFeature& feature  = result.features[result.size++];
        feature.rawCenter          = rawCenter;
        feature.orientedCenter     = orientedCenter;
        feature.rawCollateral      = rawCollateral;
        feature.orientedCollateral = orientedCollateral;
        feature.collateral         = collateral;
        feature.actorRelation      = actorRelation;
        feature.collateralRelation = collateralRelation;
        feature.direction          = direction;
        feature.collateralClass    = collateralClass;
        feature.localIndex         = localIndex;
        feature.physicalIndex      = BlastRingPhysicalOffset + localIndex;
        feature.adjacentPawnSurvives =
          collateralClass == BlastRingCollateralClass::AdjacentPawnSurvives;
    }

    return CapturePairError::None;
}

}  // namespace Detail

BlastRingError
emit_blast_ring(const CapturePairSnapshot& snapshot, Color perspective, BlastRingEmission& result) {
    result = {};

    // CapturePair is emitted exactly once and is the sole authority for
    // candidate centers, actor relations, distinct origins and validated EP.
    CapturePairEmission    capturePairs{};
    const CapturePairError capturePairError =
      emit_capture_pairs(snapshot, perspective, capturePairs);
    if (capturePairError != CapturePairError::None)
        return capturePairError;
    return Detail::project_blast_ring(snapshot, perspective, capturePairs, result);
}

BlastRingError
emit_blast_ring(const Position& position, Color perspective, BlastRingEmission& result) {
    CapturePairSnapshot snapshot{};
    snapshot.sideToMove = position.side_to_move();
    snapshot.epSquare   = position.ep_square();
    for (int squareIndex = 0; squareIndex < SQUARE_NB; ++squareIndex)
        snapshot.board[squareIndex] = position.piece_on(Square(squareIndex));
    return emit_blast_ring(snapshot, perspective, result);
}

const char* blast_ring_error_message(BlastRingError error) {
    return capture_pair_error_message(error);
}

}  // namespace Stockfish::Eval::NNUE::AtomicV3
