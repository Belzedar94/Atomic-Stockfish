/*
  Atomic-Stockfish, a UCI chess playing engine derived from Stockfish
  Copyright (C) 2004-2026 The Stockfish developers (see AUTHORS file)

  Atomic-Stockfish is free software: you can redistribute it and/or modify
  it under the terms of the GNU General Public License as published by
  the Free Software Foundation, either version 3 of the License, or
  (at your option) any later version.
*/

#include "capture_pair.h"

#include "../../attacks.h"
#include "../../bitboard.h"
#include "../../position.h"

namespace Stockfish::Eval::NNUE::AtomicV3 {
namespace {

CapturePairError map_hm_error(HmOracleError error) {
    switch (error)
    {
    case HmOracleError::None :
        return CapturePairError::None;
    case HmOracleError::InvalidPerspective :
        return CapturePairError::InvalidPerspective;
    case HmOracleError::MissingWhiteKing :
        return CapturePairError::MissingWhiteKing;
    case HmOracleError::MissingBlackKing :
        return CapturePairError::MissingBlackKing;
    case HmOracleError::MultipleWhiteKings :
        return CapturePairError::MultipleWhiteKings;
    case HmOracleError::MultipleBlackKings :
        return CapturePairError::MultipleBlackKings;
    case HmOracleError::TooManyPiecesPerColor :
        return CapturePairError::TooManyPiecesPerColor;
    case HmOracleError::TooManyPieces :
        return CapturePairError::TooManyPieces;
    case HmOracleError::InvalidPiece :
        return CapturePairError::InvalidPiece;
    }
    return CapturePairError::InvalidPiece;
}

CapturePairTargetClass target_class(Piece piece) {
    return CapturePairTargetClass(int(type_of(piece)) - int(PAWN));
}

Bitboard geometric_targets(PieceType pieceType, CapturePairActorRelation relation, Square from) {
    Bitboard result = 0;
    for (int to = 0; to < SQUARE_NB; ++to)
        if (capture_pair_geometric_edge(pieceType, relation, from, Square(to)))
            result |= Square(to);
    return result;
}

bool valid_ep_metadata(const CapturePairSnapshot& snapshot) {
    if (snapshot.epSquare == SQ_NONE)
        return false;
    if (!is_ok(snapshot.epSquare))
        return false;

    const Rank requiredRank = snapshot.sideToMove == WHITE ? RANK_6 : RANK_3;
    if (rank_of(snapshot.epSquare) != requiredRank)
        return false;
    if (snapshot.board[snapshot.epSquare] != NO_PIECE)
        return false;

    const Square capturedSquare =
      Square(int(snapshot.epSquare) - int(pawn_push(snapshot.sideToMove)));
    if (!is_ok(capturedSquare)
        || snapshot.board[capturedSquare] != make_piece(~snapshot.sideToMove, PAWN))
        return false;

    bool hasAttacker = false;
    for (int from = 0; from < SQUARE_NB; ++from)
    {
        if (snapshot.board[from] != make_piece(snapshot.sideToMove, PAWN))
            continue;
        const int fileDelta = int(file_of(snapshot.epSquare)) - int(file_of(Square(from)));
        const int rankDelta = int(rank_of(snapshot.epSquare)) - int(rank_of(Square(from)));
        const int expected  = snapshot.sideToMove == WHITE ? 1 : -1;
        hasAttacker |= (fileDelta == 1 || fileDelta == -1) && rankDelta == expected;
    }
    return hasAttacker;
}

bool add_feature(CapturePairEmission& result, const CapturePairFeature& feature) {
    if (result.size >= CapturePairMaximumActiveFeatures)
        return false;
    if (result.size && result.features[result.size - 1].localIndex >= feature.localIndex)
        return false;
    result.features[result.size++] = feature;
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

bool compatible_hm_emission(const CapturePairSnapshot& snapshot,
                            Color                      perspective,
                            const HmEmission&          hm) {
    std::array<IndexType, COLOR_NB> pieceCounts{};
    std::array<IndexType, COLOR_NB> kingCounts{};
    std::array<Square, COLOR_NB>    kingSquares{SQ_NONE, SQ_NONE};
    IndexType                       totalPieces = 0;
    for (int squareIndex = 0; squareIndex < SQUARE_NB; ++squareIndex)
    {
        const Piece piece = snapshot.board[squareIndex];
        if (!valid_board_piece(piece))
            return false;
        if (piece == NO_PIECE)
            continue;

        const Color color = color_of(piece);
        ++pieceCounts[color];
        ++totalPieces;
        if (type_of(piece) == KING)
        {
            ++kingCounts[color];
            kingSquares[color] = Square(squareIndex);
        }
    }

    IndexType networkBucket = 0;
    return (perspective == WHITE || perspective == BLACK)
        && hm.orientation.perspective == perspective
        && is_canonical_joint_orientation(hm.orientation) && hm.size >= 2
        && hm.size <= HmMaximumActiveDimensions && hm.size <= hm.features.size()
        && totalPieces == hm.size && pieceCounts[WHITE] <= HmMaximumPiecesPerColor
        && pieceCounts[BLACK] <= HmMaximumPiecesPerColor && kingCounts[WHITE] == 1
        && kingCounts[BLACK] == 1 && kingSquares[perspective] == hm.orientation.ownKing
        && hm_network_bucket(hm.size, networkBucket) && hm.networkBucket == networkBucket
        && is_ok(hm.orientation.ownKing);
}

}  // namespace

namespace Detail {

CapturePairError capture_pair_error_from_hm(HmOracleError error) { return map_hm_error(error); }

bool well_formed_capture_pair_emission(const CapturePairSnapshot& snapshot,
                                       Color                      perspective,
                                       const CapturePairEmission& emission) {
    if ((perspective != WHITE && perspective != BLACK)
        || (snapshot.sideToMove != WHITE && snapshot.sideToMove != BLACK)
        || emission.orientation.perspective != perspective
        || !is_canonical_joint_orientation(emission.orientation)
        || emission.size > CapturePairMaximumActiveFeatures)
        return false;

    std::array<IndexType, COLOR_NB> pieceCounts{};
    std::array<IndexType, COLOR_NB> kingCounts{};
    std::array<Square, COLOR_NB>    kingSquares{SQ_NONE, SQ_NONE};
    IndexType                       totalPieces = 0;
    for (int squareIndex = 0; squareIndex < SQUARE_NB; ++squareIndex)
    {
        const Piece piece = snapshot.board[squareIndex];
        if (!valid_board_piece(piece))
            return false;
        if (piece == NO_PIECE)
            continue;

        const Color color = color_of(piece);
        ++pieceCounts[color];
        ++totalPieces;
        if (type_of(piece) == KING)
        {
            ++kingCounts[color];
            kingSquares[color] = Square(squareIndex);
        }
    }
    if (totalPieces < 2 || totalPieces > 32 || pieceCounts[WHITE] > 16 || pieceCounts[BLACK] > 16
        || kingCounts[WHITE] != 1 || kingCounts[BLACK] != 1
        || kingSquares[perspective] != emission.orientation.ownKing)
        return false;

    for (IndexType featureIndex = 0; featureIndex < emission.size; ++featureIndex)
    {
        const CapturePairFeature& feature = emission.features[featureIndex];
        if (!valid_capture_pair_relation(feature.actorRelation) || !is_ok(feature.rawFrom)
            || !is_ok(feature.rawCenter) || !is_ok(feature.rawCaptured)
            || !is_ok(feature.orientedFrom) || !is_ok(feature.orientedCenter)
            || !is_ok(feature.orientedCaptured)
            || feature.physicalIndex != CapturePairPhysicalOffset + feature.localIndex
            || (featureIndex
                && emission.features[featureIndex - 1].localIndex >= feature.localIndex)
            || feature.orientedFrom != emission.orientation.orient(feature.rawFrom)
            || feature.orientedCenter != emission.orientation.orient(feature.rawCenter)
            || feature.orientedCaptured != emission.orientation.orient(feature.rawCaptured)
            || !valid_board_piece(feature.actor) || feature.actor == NO_PIECE
            || type_of(feature.actor) > QUEEN || !valid_board_piece(feature.captured)
            || feature.captured == NO_PIECE || snapshot.board[feature.rawFrom] != feature.actor
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
            const Square expectedRawCaptured =
              Square(int(feature.rawCenter) - int(pawn_push(snapshot.sideToMove)));
            IndexType expectedEp = CapturePairEpEdgesPerRelation;
            if (feature.targetClass != CapturePairTargetClass::EnPassant
                || type_of(feature.actor) != PAWN || type_of(feature.captured) != PAWN
                || color_of(feature.actor) != snapshot.sideToMove
                || feature.rawCenter != snapshot.epSquare || !is_ok(expectedRawCaptured)
                || feature.rawCaptured != expectedRawCaptured
                || snapshot.board[feature.rawCenter] != NO_PIECE
                || !capture_pair_ep_ordinal(feature.actorRelation, feature.orientedFrom,
                                            feature.orientedCenter, expectedEp)
                || feature.epOrdinal != expectedEp
                || !capture_pair_ep_index(feature.actorRelation, expectedEp, expectedIndex))
                return false;
        }
        else if (feature.rawCenter != feature.rawCaptured || feature.epOrdinal != 0
                 || snapshot.board[feature.rawCenter] != feature.captured
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

}  // namespace Detail

CapturePairError Detail::emit_capture_pairs_from_hm(const CapturePairSnapshot& snapshot,
                                                    Color                      perspective,
                                                    const HmEmission&          hm,
                                                    CapturePairEmission&       result) {
    result = {};

    if (!compatible_hm_emission(snapshot, perspective, hm))
        return CapturePairError::NonCanonicalOrder;
    if (snapshot.sideToMove != WHITE && snapshot.sideToMove != BLACK)
        return CapturePairError::InvalidSideToMove;

    // EP is optional feature metadata, not a precondition for evaluating the
    // placement. A malformed center must fail closed for the EP tail while
    // leaving every occupancy pseudocapture intact.
    const bool epActive = valid_ep_metadata(snapshot);

    result.orientation = hm.orientation;

    HmBoard                        orientedBoard{};
    Bitboard                       occupied = 0;
    std::array<Bitboard, COLOR_NB> colorOccupied{};
    for (int rawSquare = 0; rawSquare < SQUARE_NB; ++rawSquare)
    {
        const Piece piece = snapshot.board[rawSquare];
        if (piece == NO_PIECE)
            continue;
        const Square orientedSquare   = result.orientation.orient(Square(rawSquare));
        orientedBoard[orientedSquare] = piece;
        occupied |= orientedSquare;
        colorOccupied[color_of(piece)] |= orientedSquare;
    }

    for (CapturePairActorRelation relation :
         {CapturePairActorRelation::Own, CapturePairActorRelation::Opp})
    {
        const Color actorColor =
          relation == CapturePairActorRelation::Own ? perspective : ~perspective;
        for (PieceType pieceType : {PAWN, KNIGHT, BISHOP, ROOK, QUEEN})
            for (int fromIndex = 0; fromIndex < SQUARE_NB; ++fromIndex)
            {
                const Square from  = Square(fromIndex);
                const Piece  actor = orientedBoard[from];
                if (actor != make_piece(actorColor, pieceType))
                    continue;

                Bitboard targets = pieceType == PAWN
                                   ? geometric_targets(pieceType, relation, from)
                                   : Attacks::attacks_bb(pieceType, from, occupied);
                targets &= colorOccupied[~actorColor];
                while (targets)
                {
                    const Square                 center   = pop_lsb(targets);
                    const Piece                  captured = orientedBoard[center];
                    IndexType                    edgeOrdinal;
                    IndexType                    localIndex;
                    const CapturePairTargetClass target = target_class(captured);
                    if (!capture_pair_edge_ordinal(pieceType, relation, from, center, edgeOrdinal)
                        || !capture_pair_normal_index(relation, edgeOrdinal, target, localIndex))
                    {
                        result = {};
                        return CapturePairError::InvalidPiece;
                    }

                    const Square             rawFrom   = result.orientation.orient(from);
                    const Square             rawCenter = result.orientation.orient(center);
                    const CapturePairFeature feature{
                      rawFrom,     rawCenter,
                      rawCenter,   from,
                      center,      center,
                      actor,       captured,
                      relation,    target,
                      edgeOrdinal, 0,
                      localIndex,  CapturePairPhysicalOffset + localIndex,
                      false};
                    if (!add_feature(result, feature))
                    {
                        const bool overflow = result.size >= CapturePairMaximumActiveFeatures;
                        result              = {};
                        return overflow ? CapturePairError::TooManyFeatures
                                        : CapturePairError::NonCanonicalOrder;
                    }
                }
            }
    }

    if (epActive)
    {
        const CapturePairActorRelation relation = snapshot.sideToMove == perspective
                                                  ? CapturePairActorRelation::Own
                                                  : CapturePairActorRelation::Opp;
        const Square                   center   = result.orientation.orient(snapshot.epSquare);
        const Square                   captured = result.orientation.orient(
          Square(int(snapshot.epSquare) - int(pawn_push(snapshot.sideToMove))));
        for (int fromIndex = 0; fromIndex < SQUARE_NB; ++fromIndex)
        {
            const Square from  = Square(fromIndex);
            const Piece  actor = orientedBoard[from];
            if (actor != make_piece(snapshot.sideToMove, PAWN)
                || !capture_pair_ep_edge(relation, from, center))
                continue;

            IndexType edgeOrdinal;
            IndexType epOrdinal;
            IndexType localIndex;
            if (!capture_pair_edge_ordinal(PAWN, relation, from, center, edgeOrdinal)
                || !capture_pair_ep_ordinal(relation, from, center, epOrdinal)
                || !capture_pair_ep_index(relation, epOrdinal, localIndex))
            {
                result = {};
                return CapturePairError::NonCanonicalOrder;
            }
            const CapturePairFeature feature{result.orientation.orient(from),
                                             snapshot.epSquare,
                                             result.orientation.orient(captured),
                                             from,
                                             center,
                                             captured,
                                             actor,
                                             orientedBoard[captured],
                                             relation,
                                             CapturePairTargetClass::EnPassant,
                                             edgeOrdinal,
                                             epOrdinal,
                                             localIndex,
                                             CapturePairPhysicalOffset + localIndex,
                                             true};
            if (!add_feature(result, feature))
            {
                const bool overflow = result.size >= CapturePairMaximumActiveFeatures;
                result              = {};
                return overflow ? CapturePairError::TooManyFeatures
                                : CapturePairError::NonCanonicalOrder;
            }
        }
    }

    return CapturePairError::None;
}

CapturePairError emit_capture_pairs(const CapturePairSnapshot& snapshot,
                                    Color                      perspective,
                                    CapturePairEmission&       result) {
    result = {};

    HmEmission          hm{};
    const HmOracleError hmError = emit_hm_features(snapshot.board, perspective, hm);
    if (hmError != HmOracleError::None)
        return Detail::capture_pair_error_from_hm(hmError);
    return Detail::emit_capture_pairs_from_hm(snapshot, perspective, hm, result);
}

CapturePairError
emit_capture_pairs(const Position& position, Color perspective, CapturePairEmission& result) {
    CapturePairSnapshot snapshot{};
    snapshot.sideToMove = position.side_to_move();
    snapshot.epSquare   = position.ep_square();
    for (int squareIndex = 0; squareIndex < SQUARE_NB; ++squareIndex)
        snapshot.board[squareIndex] = position.piece_on(Square(squareIndex));
    return emit_capture_pairs(snapshot, perspective, result);
}

const char* capture_pair_error_message(CapturePairError error) {
    switch (error)
    {
    case CapturePairError::None :
        return "none";
    case CapturePairError::InvalidPerspective :
        return "invalid perspective";
    case CapturePairError::InvalidSideToMove :
        return "invalid side to move";
    case CapturePairError::MissingWhiteKing :
        return "missing white king";
    case CapturePairError::MissingBlackKing :
        return "missing black king";
    case CapturePairError::MultipleWhiteKings :
        return "multiple white kings";
    case CapturePairError::MultipleBlackKings :
        return "multiple black kings";
    case CapturePairError::TooManyPiecesPerColor :
        return "too many pieces per color";
    case CapturePairError::TooManyPieces :
        return "too many pieces";
    case CapturePairError::InvalidPiece :
        return "invalid piece";
    case CapturePairError::TooManyFeatures :
        return "too many capture-pair features";
    case CapturePairError::NonCanonicalOrder :
        return "non-canonical capture-pair order";
    }
    return "unknown capture-pair error";
}

}  // namespace Stockfish::Eval::NNUE::AtomicV3
