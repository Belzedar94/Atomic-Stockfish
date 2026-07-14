/*
  Atomic-Stockfish, a UCI chess playing engine derived from Stockfish
  Copyright (C) 2004-2026 The Stockfish developers (see AUTHORS file)

  Atomic-Stockfish is free software: you can redistribute it and/or modify
  it under the terms of the GNU General Public License as published by
  the Free Software Foundation, either version 3 of the License, or
  (at your option) any later version.
*/

#include "hm_oracle.h"

#include "../../position.h"

namespace Stockfish::Eval::NNUE::AtomicV3 {
namespace {

bool piece_planes(Color            perspective,
                  Piece            piece,
                  HmTrainingPlane& trainingPlane,
                  HmPhysicalPlane& physicalPlane) {
    if (piece == NO_PIECE)
        return false;

    const PieceType pieceType = type_of(piece);
    if (pieceType < PAWN || pieceType > KING)
        return false;

    const int relation = color_of(piece) == perspective ? 0 : 1;
    const int training = 2 * (int(pieceType) - int(PAWN)) + relation;
    trainingPlane      = HmTrainingPlane(training);
    physicalPlane = pieceType == KING ? HmPhysicalPlane::MergedKing : HmPhysicalPlane(training);
    return true;
}

bool valid_piece(Piece piece) {
    if (piece == NO_PIECE || int(piece) >= PIECE_NB || (int(piece) >> 3) >= COLOR_NB)
        return false;
    const PieceType pieceType = type_of(piece);
    return pieceType >= PAWN && pieceType <= KING;
}

}  // namespace

bool hm_export_source(const JointOrientation& orientation,
                      HmPhysicalPlane         physicalPlane,
                      Square                  orientedSquare,
                      IndexType               coalescedOutput,
                      HmExportSource&         result) {
    if (!is_canonical_joint_orientation(orientation) || !is_ok(orientedSquare)
        || IndexType(physicalPlane) >= HmPhysicalPlanes || coalescedOutput >= HmCoalescedOutputs)
        return false;

    HmTrainingPlane trainingPlane;
    if (physicalPlane == HmPhysicalPlane::MergedKing)
        trainingPlane = orientedSquare == orientation.orientedOwnKing ? HmTrainingPlane::OwnKing
                                                                      : HmTrainingPlane::OppKing;
    else
        trainingPlane = HmTrainingPlane(physicalPlane);

    result.trainingPlane = trainingPlane;
    result.physicalRow   = hm_physical_index(orientation.kingBucket, physicalPlane, orientedSquare);
    result.bucketWeightRow =
      hm_training_index(orientation.kingBucket, trainingPlane, orientedSquare);
    result.virtualWeightRow = hm_virtual_index(trainingPlane, orientedSquare);
    result.sourceOutput     = coalescedOutput;
    if (coalescedOutput < HmAccumulatorOutputs)
    {
        result.destinationKind   = HmOutputKind::Accumulator;
        result.destinationOutput = coalescedOutput;
    }
    else
    {
        result.destinationKind   = HmOutputKind::Psqt;
        result.destinationOutput = coalescedOutput - HmAccumulatorOutputs;
    }
    return true;
}

HmOracleError emit_hm_features(const HmBoard& board, Color perspective, HmEmission& result) {
    result = {};

    if (perspective != WHITE && perspective != BLACK)
        return HmOracleError::InvalidPerspective;

    std::array<IndexType, COLOR_NB> pieceCounts{};
    std::array<IndexType, COLOR_NB> kingCounts{};
    std::array<Square, COLOR_NB>    kingSquares{SQ_NONE, SQ_NONE};
    IndexType                       totalPieces = 0;
    for (int squareIndex = 0; squareIndex < SQUARE_NB; ++squareIndex)
    {
        const Piece piece = board[squareIndex];
        if (piece == NO_PIECE)
            continue;
        if (!valid_piece(piece))
            return HmOracleError::InvalidPiece;

        const Color color = color_of(piece);
        ++pieceCounts[color];
        ++totalPieces;
        if (type_of(piece) == KING)
        {
            ++kingCounts[color];
            kingSquares[color] = Square(squareIndex);
        }
    }

    if (kingCounts[WHITE] == 0)
        return HmOracleError::MissingWhiteKing;
    if (kingCounts[BLACK] == 0)
        return HmOracleError::MissingBlackKing;
    if (kingCounts[WHITE] != 1)
        return HmOracleError::MultipleWhiteKings;
    if (kingCounts[BLACK] != 1)
        return HmOracleError::MultipleBlackKings;
    if (pieceCounts[WHITE] > HmMaximumPiecesPerColor
        || pieceCounts[BLACK] > HmMaximumPiecesPerColor)
        return HmOracleError::TooManyPiecesPerColor;
    if (totalPieces > HmMaximumActiveDimensions)
        return HmOracleError::TooManyPieces;

    const Square ownKing = kingSquares[perspective];
    if (!make_joint_orientation(perspective, ownKing, result.orientation))
        return perspective == WHITE ? HmOracleError::MissingWhiteKing
                                    : HmOracleError::MissingBlackKing;

    for (int squareIndex = 0; squareIndex < SQUARE_NB; ++squareIndex)
    {
        const Piece piece = board[squareIndex];
        if (piece == NO_PIECE)
            continue;
        const Square square = Square(squareIndex);

        HmTrainingPlane trainingPlane;
        HmPhysicalPlane physicalPlane;
        if (!piece_planes(perspective, piece, trainingPlane, physicalPlane))
        {
            result = {};
            return HmOracleError::InvalidPiece;
        }

        const Square orientedSquare = result.orientation.orient(square);
        HmFeature&   feature        = result.features[result.size++];
        feature.boardSquare         = square;
        feature.orientedSquare      = orientedSquare;
        feature.piece               = piece;
        feature.trainingPlane       = trainingPlane;
        feature.physicalPlane       = physicalPlane;
        feature.trainingIndex =
          hm_training_index(result.orientation.kingBucket, trainingPlane, orientedSquare);
        feature.virtualIndex = hm_virtual_index(trainingPlane, orientedSquare);
        feature.physicalIndex =
          hm_physical_index(result.orientation.kingBucket, physicalPlane, orientedSquare);
        feature.psqtRow = feature.physicalIndex;
    }

    if (!hm_network_bucket(result.size, result.networkBucket))
    {
        result = {};
        return HmOracleError::TooManyPieces;
    }
    return HmOracleError::None;
}

HmOracleError emit_hm_features(const Position& position, Color perspective, HmEmission& result) {
    HmBoard board{};
    for (int squareIndex = 0; squareIndex < SQUARE_NB; ++squareIndex)
        board[squareIndex] = position.piece_on(Square(squareIndex));
    return emit_hm_features(board, perspective, result);
}

const char* hm_oracle_error_message(HmOracleError error) {
    switch (error)
    {
    case HmOracleError::None :
        return "none";
    case HmOracleError::InvalidPerspective :
        return "invalid perspective";
    case HmOracleError::MissingWhiteKing :
        return "missing white king";
    case HmOracleError::MissingBlackKing :
        return "missing black king";
    case HmOracleError::MultipleWhiteKings :
        return "multiple white kings";
    case HmOracleError::MultipleBlackKings :
        return "multiple black kings";
    case HmOracleError::TooManyPiecesPerColor :
        return "more than 16 pieces for one color";
    case HmOracleError::TooManyPieces :
        return "more than 32 pieces";
    case HmOracleError::InvalidPiece :
        return "invalid piece";
    }
    return "unknown HM oracle error";
}

}  // namespace Stockfish::Eval::NNUE::AtomicV3
