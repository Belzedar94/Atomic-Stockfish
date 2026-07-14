/*
  Atomic-Stockfish, a UCI chess playing engine derived from Stockfish
  Copyright (C) 2004-2026 The Stockfish developers (see AUTHORS file)

  Atomic-Stockfish is free software: you can redistribute it and/or modify
  it under the terms of the GNU General Public License as published by
  the Free Software Foundation, either version 3 of the License, or
  (at your option) any later version.
*/

#include <algorithm>
#include <array>
#include <cstdint>
#include <cstdlib>
#include <initializer_list>
#include <iostream>
#include <string>
#include <string_view>
#include <thread>
#include <tuple>
#include <vector>

#include "../../../attacks.h"
#include "../../../bitboard.h"
#include "../../../position.h"
#include "../capture_pair.h"

namespace Stockfish::Eval::NNUE::AtomicV3 {
namespace {

int failures = 0;

void expect(bool condition, std::string_view label) {
    if (condition)
        std::cout << "PASS " << label << '\n';
    else
    {
        std::cerr << "FAIL " << label << '\n';
        ++failures;
    }
}

const char* color_name(Color color) {
    return color == WHITE ? "white" : color == BLACK ? "black" : "invalid";
}

const char* relation_name(CapturePairActorRelation relation) {
    return relation == CapturePairActorRelation::Own ? "own"
         : relation == CapturePairActorRelation::Opp ? "opp"
                                                     : "invalid";
}

const char* target_name(CapturePairTargetClass target) {
    switch (target)
    {
    case CapturePairTargetClass::Pawn :
        return "pawn";
    case CapturePairTargetClass::Knight :
        return "knight";
    case CapturePairTargetClass::Bishop :
        return "bishop";
    case CapturePairTargetClass::Rook :
        return "rook";
    case CapturePairTargetClass::Queen :
        return "queen";
    case CapturePairTargetClass::King :
        return "king";
    case CapturePairTargetClass::EnPassant :
        return "en_passant";
    }
    return "invalid";
}

const char* error_name(CapturePairError error) {
    switch (error)
    {
    case CapturePairError::None :
        return "none";
    case CapturePairError::InvalidPerspective :
        return "invalid_perspective";
    case CapturePairError::InvalidSideToMove :
        return "invalid_side_to_move";
    case CapturePairError::MissingWhiteKing :
        return "missing_white_king";
    case CapturePairError::MissingBlackKing :
        return "missing_black_king";
    case CapturePairError::MultipleWhiteKings :
        return "multiple_white_kings";
    case CapturePairError::MultipleBlackKings :
        return "multiple_black_kings";
    case CapturePairError::TooManyPiecesPerColor :
        return "too_many_pieces_per_color";
    case CapturePairError::TooManyPieces :
        return "too_many_pieces";
    case CapturePairError::InvalidPiece :
        return "invalid_piece";
    case CapturePairError::TooManyFeatures :
        return "too_many_features";
    case CapturePairError::NonCanonicalOrder :
        return "non_canonical_order";
    }
    return "unknown";
}

struct BoardPiece {
    Square square;
    Piece  piece;
};

CapturePairSnapshot make_snapshot(std::initializer_list<BoardPiece> pieces,
                                  Color                             sideToMove = WHITE,
                                  Square                            epSquare   = SQ_NONE) {
    CapturePairSnapshot result{};
    result.sideToMove = sideToMove;
    result.epSquare   = epSquare;
    for (const BoardPiece entry : pieces)
        result.board[entry.square] = entry.piece;
    return result;
}

CapturePairSnapshot snapshot_from_position(const Position& position) {
    CapturePairSnapshot result{};
    result.sideToMove = position.side_to_move();
    result.epSquare   = position.ep_square();
    for (int squareIndex = 0; squareIndex < SQUARE_NB; ++squareIndex)
        result.board[squareIndex] = position.piece_on(Square(squareIndex));
    return result;
}

CapturePairSnapshot horizontal_mirror(const CapturePairSnapshot& source) {
    CapturePairSnapshot result{};
    result.sideToMove = source.sideToMove;
    result.epSquare   = source.epSquare == SQ_NONE ? SQ_NONE : Square(int(source.epSquare) ^ 7);
    for (int squareIndex = 0; squareIndex < SQUARE_NB; ++squareIndex)
        result.board[squareIndex ^ 7] = source.board[squareIndex];
    return result;
}

const CapturePairFeature* find_feature(const CapturePairEmission& emission,
                                       Square                     rawFrom,
                                       Square                     rawCenter,
                                       bool                       enPassant = false) {
    for (IndexType index = 0; index < emission.size; ++index)
    {
        const CapturePairFeature& feature = emission.features[index];
        if (feature.rawFrom == rawFrom && feature.rawCenter == rawCenter
            && feature.enPassant == enPassant)
            return &feature;
    }
    return nullptr;
}

IndexType feature_count(const CapturePairEmission& emission, Square rawFrom) {
    IndexType result = 0;
    for (IndexType index = 0; index < emission.size; ++index)
        result += emission.features[index].rawFrom == rawFrom;
    return result;
}

using NormalProjection = std::tuple<IndexType, Square, Square, Square, Piece, Piece, bool>;

std::vector<NormalProjection> normal_projection(const CapturePairEmission& emission) {
    std::vector<NormalProjection> result;
    for (IndexType index = 0; index < emission.size; ++index)
    {
        const CapturePairFeature& feature = emission.features[index];
        if (!feature.enPassant)
            result.emplace_back(feature.localIndex, feature.rawFrom, feature.rawCenter,
                                feature.rawCaptured, feature.actor, feature.captured,
                                feature.enPassant);
    }
    return result;
}

bool same_oriented_emission(const CapturePairEmission& lhs, const CapturePairEmission& rhs) {
    if (lhs.size != rhs.size || lhs.orientation.kingBucket != rhs.orientation.kingBucket)
        return false;
    for (IndexType index = 0; index < lhs.size; ++index)
    {
        const CapturePairFeature& a = lhs.features[index];
        const CapturePairFeature& b = rhs.features[index];
        if (a.orientedFrom != b.orientedFrom || a.orientedCenter != b.orientedCenter
            || a.orientedCaptured != b.orientedCaptured || a.actor != b.actor
            || a.captured != b.captured || a.actorRelation != b.actorRelation
            || a.targetClass != b.targetClass || a.edgeOrdinal != b.edgeOrdinal
            || a.epOrdinal != b.epOrdinal || a.localIndex != b.localIndex
            || a.physicalIndex != b.physicalIndex || a.enPassant != b.enPassant)
            return false;
    }
    return true;
}

bool same_exact_emission(const CapturePairEmission& lhs, const CapturePairEmission& rhs) {
    if (!same_oriented_emission(lhs, rhs)
        || lhs.orientation.perspective != rhs.orientation.perspective
        || lhs.orientation.ownKing != rhs.orientation.ownKing
        || lhs.orientation.orientedOwnKing != rhs.orientation.orientedOwnKing
        || lhs.orientation.verticalXor != rhs.orientation.verticalXor
        || lhs.orientation.horizontalXor != rhs.orientation.horizontalXor)
        return false;
    for (IndexType index = 0; index < lhs.size; ++index)
    {
        const CapturePairFeature& a = lhs.features[index];
        const CapturePairFeature& b = rhs.features[index];
        if (a.rawFrom != b.rawFrom || a.rawCenter != b.rawCenter || a.rawCaptured != b.rawCaptured)
            return false;
    }
    return true;
}

bool set_position(Position&          position,
                  StateInfo&         state,
                  const std::string& fen,
                  bool               chess960,
                  std::string_view   label) {
    const auto error = position.set(fen, chess960, &state);
    expect(!error, label);
    if (error)
        std::cerr << "FEN error: " << error->what() << '\n';
    return !error;
}

void test_constants_geometry_and_indexing() {
    expect(CapturePairActorRelations == 2 && CapturePairGeometryDimensions == 3332
             && CapturePairNormalTargetClasses == 6 && CapturePairNormalDimensions == 39984
             && CapturePairEpEdgesPerRelation == 14 && CapturePairEpDimensions == 28
             && CapturePairPhysicalDimensions == 40012 && CapturePairPhysicalOffset == 22528
             && CapturePairMaximumActiveFeatures == 240,
           "CapturePair constants match the provisional compact contract");

    constexpr std::array<PieceType, 5>            PieceTypes{PAWN, KNIGHT, BISHOP, ROOK, QUEEN};
    constexpr std::array<IndexType, 5>            Bases{0, 84, 420, 980, 1876};
    constexpr std::array<IndexType, 5>            Counts{84, 336, 560, 896, 1456};
    std::array<bool, CapturePairNormalDimensions> seenNormal{};
    bool                                          exact = true;

    for (CapturePairActorRelation relation :
         {CapturePairActorRelation::Own, CapturePairActorRelation::Opp})
        for (usize typeIndex = 0; typeIndex < PieceTypes.size(); ++typeIndex)
        {
            const PieceType pieceType = PieceTypes[typeIndex];
            exact = exact && capture_pair_geometry_base(pieceType) == Bases[typeIndex]
                 && capture_pair_geometry_count(pieceType, relation) == Counts[typeIndex];
            IndexType expectedOrdinal = Bases[typeIndex];
            for (int from = 0; from < SQUARE_NB; ++from)
                for (int to = 0; to < SQUARE_NB; ++to)
                {
                    const bool edge =
                      capture_pair_geometric_edge(pieceType, relation, Square(from), Square(to));
                    IndexType  ordinal = CapturePairGeometryDimensions;
                    const bool mapped = capture_pair_edge_ordinal(pieceType, relation, Square(from),
                                                                  Square(to), ordinal);
                    exact             = exact && edge == mapped;
                    if (!edge)
                        continue;
                    exact = exact && ordinal == expectedOrdinal++;
                    for (IndexType target = 0; target < CapturePairNormalTargetClasses; ++target)
                    {
                        IndexType localIndex = CapturePairNormalDimensions;
                        exact                = exact
                             && capture_pair_normal_index(
                                  relation, ordinal, CapturePairTargetClass(target), localIndex)
                             && localIndex
                                  == (IndexType(relation) * CapturePairGeometryDimensions + ordinal)
                                         * CapturePairNormalTargetClasses
                                       + target
                             && localIndex < CapturePairNormalDimensions && !seenNormal[localIndex];
                        if (localIndex < CapturePairNormalDimensions)
                            seenNormal[localIndex] = true;
                    }
                }
            exact = exact && expectedOrdinal == Bases[typeIndex] + Counts[typeIndex];
        }
    exact =
      exact && std::all_of(seenNormal.begin(), seenNormal.end(), [](bool value) { return value; });
    expect(exact,
           "all 3,332 geometry edges and 39,984 occupied-target rows are contiguous and unique");

    std::array<bool, CapturePairEpDimensions> seenEp{};
    exact = true;
    for (CapturePairActorRelation relation :
         {CapturePairActorRelation::Own, CapturePairActorRelation::Opp})
    {
        IndexType expectedOrdinal = 0;
        for (int from = 0; from < SQUARE_NB; ++from)
            for (int center = 0; center < SQUARE_NB; ++center)
            {
                const bool edge    = capture_pair_ep_edge(relation, Square(from), Square(center));
                IndexType  ordinal = CapturePairEpEdgesPerRelation;
                const bool mapped =
                  capture_pair_ep_ordinal(relation, Square(from), Square(center), ordinal);
                exact = exact && edge == mapped;
                if (!edge)
                    continue;
                exact                = exact && ordinal == expectedOrdinal++;
                IndexType localIndex = CapturePairPhysicalDimensions;
                exact                = exact && capture_pair_ep_index(relation, ordinal, localIndex)
                     && localIndex
                          == CapturePairNormalDimensions
                               + IndexType(relation) * CapturePairEpEdgesPerRelation + ordinal
                     && localIndex >= CapturePairNormalDimensions
                     && localIndex < CapturePairPhysicalDimensions;
                if (localIndex >= CapturePairNormalDimensions
                    && localIndex < CapturePairPhysicalDimensions)
                {
                    const IndexType tail = localIndex - CapturePairNormalDimensions;
                    exact                = exact && !seenEp[tail];
                    seenEp[tail]         = true;
                }
            }
        exact = exact && expectedOrdinal == CapturePairEpEdgesPerRelation;
    }
    exact = exact && std::all_of(seenEp.begin(), seenEp.end(), [](bool value) { return value; });
    expect(exact, "both strict EP edge tables fill the 28-row compact tail exactly once");

    IndexType ordinal = 0;
    IndexType index   = 0;
    expect(capture_pair_edge_ordinal(PAWN, CapturePairActorRelation::Own, SQ_E4, SQ_D5, ordinal)
             && ordinal == 35
             && capture_pair_normal_index(CapturePairActorRelation::Own, ordinal,
                                          CapturePairTargetClass::Pawn, index)
             && index == 210
             && capture_pair_ep_ordinal(CapturePairActorRelation::Own, SQ_D5, SQ_E6, ordinal)
             && ordinal == 6 && capture_pair_ep_index(CapturePairActorRelation::Own, ordinal, index)
             && index == 39990,
           "golden normal and EP indices use canonical A1-to-H8 lexicographic ordering");

    const auto invalidRelation = CapturePairActorRelation(CapturePairActorRelations);
    expect(
      !capture_pair_geometric_edge(KING, CapturePairActorRelation::Own, SQ_A1, SQ_A2)
        && !capture_pair_geometric_edge(ROOK, invalidRelation, SQ_A1, SQ_A2)
        && !capture_pair_geometric_edge(ROOK, CapturePairActorRelation::Own, SQ_A1, SQ_B2)
        && !capture_pair_edge_ordinal(ROOK, CapturePairActorRelation::Own, SQ_A1, SQ_B2, ordinal)
        && !capture_pair_normal_index(invalidRelation, 0, CapturePairTargetClass::Pawn, index)
        && !capture_pair_normal_index(CapturePairActorRelation::Own, CapturePairGeometryDimensions,
                                      CapturePairTargetClass::Pawn, index)
        && !capture_pair_normal_index(CapturePairActorRelation::Own, 0,
                                      CapturePairTargetClass::EnPassant, index)
        && !capture_pair_ep_ordinal(CapturePairActorRelation::Own, SQ_A5, SQ_A6, ordinal)
        && !capture_pair_ep_index(invalidRelation, 0, index)
        && !capture_pair_ep_index(CapturePairActorRelation::Own, CapturePairEpEdgesPerRelation,
                                  index),
      "geometry and index helpers reject invalid actors, relations, edges and bounds");
}

void test_normal_captures_targets_and_blockers() {
    struct ActorCase {
        Piece  actor;
        Square from;
        Square center;
    };
    constexpr std::array<ActorCase, 5> ActorCases{{{W_PAWN, SQ_E4, SQ_D5},
                                                   {W_KNIGHT, SQ_C3, SQ_D5},
                                                   {W_BISHOP, SQ_B1, SQ_E4},
                                                   {W_ROOK, SQ_A1, SQ_A4},
                                                   {W_QUEEN, SQ_D1, SQ_H5}}};
    bool                               allActors = true;
    for (const ActorCase& actorCase : ActorCases)
    {
        const CapturePairSnapshot snapshot = make_snapshot({{SQ_H1, W_KING},
                                                            {SQ_H8, B_KING},
                                                            {actorCase.from, actorCase.actor},
                                                            {actorCase.center, B_PAWN}});
        CapturePairEmission       emission{};
        const CapturePairError    error = emit_capture_pairs(snapshot, WHITE, emission);
        const CapturePairFeature* feature =
          find_feature(emission, actorCase.from, actorCase.center);
        allActors = allActors && error == CapturePairError::None && feature
                 && feature->actor == actorCase.actor && feature->captured == B_PAWN
                 && feature->actorRelation == CapturePairActorRelation::Own
                 && feature->targetClass == CapturePairTargetClass::Pawn;
    }
    expect(allActors, "pawn, knight, bishop, rook and queen occupied-target captures all emit");

    const CapturePairSnapshot opponentPawn =
      make_snapshot({{SQ_H1, W_KING}, {SQ_H8, B_KING}, {SQ_E5, B_PAWN}, {SQ_D4, W_PAWN}});
    CapturePairEmission       opponentEmission{};
    const CapturePairFeature* opponentFeature = nullptr;
    if (emit_capture_pairs(opponentPawn, WHITE, opponentEmission) == CapturePairError::None)
        opponentFeature = find_feature(opponentEmission, SQ_E5, SQ_D4);
    expect(opponentFeature && opponentFeature->actorRelation == CapturePairActorRelation::Opp
             && opponentFeature->edgeOrdinal == 49 && opponentFeature->localIndex == 20286,
           "opponent pawn geometry points south after the shared perspective orientation");

    const CapturePairSnapshot pawnPush =
      make_snapshot({{SQ_H1, W_KING}, {SQ_H8, B_KING}, {SQ_E2, W_PAWN}, {SQ_E3, B_ROOK}});
    CapturePairEmission pawnPushEmission{};
    expect(emit_capture_pairs(pawnPush, WHITE, pawnPushEmission) == CapturePairError::None
             && !find_feature(pawnPushEmission, SQ_E2, SQ_E3),
           "an occupied pawn push square is not a CapturePair pseudocapture");

    bool allTargets = true;
    for (IndexType target = 0; target < CapturePairNormalTargetClasses; ++target)
    {
        const Piece         targetPiece = make_piece(BLACK, PieceType(PAWN + target));
        CapturePairSnapshot snapshot{};
        snapshot.sideToMove   = WHITE;
        snapshot.board[SQ_H1] = W_KING;
        snapshot.board[SQ_C3] = W_KNIGHT;
        snapshot.board[SQ_D5] = targetPiece;
        if (targetPiece != B_KING)
            snapshot.board[SQ_H8] = B_KING;
        CapturePairEmission       emission{};
        const CapturePairError    error   = emit_capture_pairs(snapshot, WHITE, emission);
        const CapturePairFeature* feature = find_feature(emission, SQ_C3, SQ_D5);
        allTargets                        = allTargets && error == CapturePairError::None && feature
                  && IndexType(feature->targetClass) == target
                  && feature->localIndex % CapturePairNormalTargetClasses == target;
    }
    expect(allTargets, "all six occupied target classes, including KING, have distinct rows");

    const CapturePairSnapshot enemyBlocker = make_snapshot({{SQ_H1, W_KING},
                                                            {SQ_H8, B_KING},
                                                            {SQ_A1, W_ROOK},
                                                            {SQ_A4, B_BISHOP},
                                                            {SQ_A6, B_QUEEN},
                                                            {SQ_B1, W_BISHOP},
                                                            {SQ_D3, B_PAWN},
                                                            {SQ_F5, B_QUEEN}});
    CapturePairEmission       enemyEmission{};
    const bool                enemyEmitted =
      emit_capture_pairs(enemyBlocker, WHITE, enemyEmission) == CapturePairError::None;
    expect(enemyEmitted && find_feature(enemyEmission, SQ_A1, SQ_A4)
             && !find_feature(enemyEmission, SQ_A1, SQ_A6)
             && find_feature(enemyEmission, SQ_B1, SQ_D3)
             && !find_feature(enemyEmission, SQ_B1, SQ_F5),
           "the first enemy blocker emits and suppresses slider x-rays behind it");

    const CapturePairSnapshot friendlyBlocker = make_snapshot(
      {{SQ_H1, W_KING}, {SQ_H8, B_KING}, {SQ_A1, W_ROOK}, {SQ_A3, W_PAWN}, {SQ_A4, B_BISHOP}});
    CapturePairEmission friendlyEmission{};
    const bool          friendlyEmitted =
      emit_capture_pairs(friendlyBlocker, WHITE, friendlyEmission) == CapturePairError::None;
    expect(friendlyEmitted && !find_feature(friendlyEmission, SQ_A1, SQ_A4),
           "a friendly blocker stops a slider ray without emitting a row");

    const CapturePairSnapshot kingsOnly = make_snapshot({{SQ_E4, W_KING}, {SQ_E5, B_KING}});
    CapturePairEmission       kingsWhite{};
    CapturePairEmission       kingsBlack{};
    expect(emit_capture_pairs(kingsOnly, WHITE, kingsWhite) == CapturePairError::None
             && emit_capture_pairs(kingsOnly, BLACK, kingsBlack) == CapturePairError::None
             && kingsWhite.size == 0 && kingsBlack.size == 0,
           "kings may be occupied targets but never CapturePair actors");

    const CapturePairSnapshot pinned = make_snapshot(
      {{SQ_E1, W_KING}, {SQ_A8, B_KING}, {SQ_E2, W_ROOK}, {SQ_E8, B_ROOK}, {SQ_H2, B_PAWN}});
    CapturePairEmission pinnedEmission{};
    expect(emit_capture_pairs(pinned, WHITE, pinnedEmission) == CapturePairError::None
             && find_feature(pinnedEmission, SQ_E2, SQ_H2),
           "occupancy pseudocaptures retain pinned actors without move-legality filtering");

    const CapturePairSnapshot selfBlast =
      make_snapshot({{SQ_D4, W_KING}, {SQ_H8, B_KING}, {SQ_F3, W_KNIGHT}, {SQ_E5, B_PAWN}});
    CapturePairEmission selfBlastEmission{};
    expect(emit_capture_pairs(selfBlast, WHITE, selfBlastEmission) == CapturePairError::None
             && find_feature(selfBlastEmission, SQ_F3, SQ_E5),
           "occupancy pseudocaptures retain Atomic self-blasting candidates");
}

void test_en_passant() {
    const CapturePairSnapshot whiteEp = make_snapshot(
      {{SQ_H1, W_KING}, {SQ_H8, B_KING}, {SQ_D5, W_PAWN}, {SQ_F5, W_PAWN}, {SQ_E5, B_PAWN}}, WHITE,
      SQ_E6);
    CapturePairEmission whiteOwn{};
    CapturePairEmission whiteOpp{};
    const bool          whiteEmitted =
      emit_capture_pairs(whiteEp, WHITE, whiteOwn) == CapturePairError::None
      && emit_capture_pairs(whiteEp, BLACK, whiteOpp) == CapturePairError::None;
    const CapturePairFeature* d5         = find_feature(whiteOwn, SQ_D5, SQ_E6, true);
    const CapturePairFeature* f5         = find_feature(whiteOwn, SQ_F5, SQ_E6, true);
    const CapturePairFeature* opponentD5 = find_feature(whiteOpp, SQ_D5, SQ_E6, true);
    expect(
      whiteEmitted && d5 && f5 && opponentD5 && d5->rawCaptured == SQ_E5 && f5->rawCaptured == SQ_E5
        && d5->captured == B_PAWN && d5->targetClass == CapturePairTargetClass::EnPassant
        && d5->actorRelation == CapturePairActorRelation::Own
        && opponentD5->actorRelation == CapturePairActorRelation::Opp && d5->epOrdinal == 6
        && d5->edgeOrdinal == 48 && opponentD5->edgeOrdinal == 34 && d5->localIndex == 39990
        && f5->localIndex == 39993 && opponentD5->localIndex >= CapturePairNormalDimensions + 14,
      "valid WHITE EP emits each attacker once with center, off-center pawn and relation");

    const CapturePairSnapshot blackEp = make_snapshot(
      {{SQ_A1, W_KING}, {SQ_H8, B_KING}, {SQ_C4, B_PAWN}, {SQ_E4, B_PAWN}, {SQ_D4, W_PAWN}}, BLACK,
      SQ_D3);
    CapturePairEmission blackOwn{};
    const bool          blackEmitted =
      emit_capture_pairs(blackEp, BLACK, blackOwn) == CapturePairError::None;
    const CapturePairFeature* c4 = find_feature(blackOwn, SQ_C4, SQ_D3, true);
    const CapturePairFeature* e4 = find_feature(blackOwn, SQ_E4, SQ_D3, true);
    expect(blackEmitted && c4 && e4 && c4->rawCaptured == SQ_D4 && e4->rawCaptured == SQ_D4
             && c4->actorRelation == CapturePairActorRelation::Own
             && e4->actorRelation == CapturePairActorRelation::Own,
           "valid BLACK EP normalizes through the shared BLACK orientation");

    std::array<CapturePairSnapshot, 7> malformed{};
    malformed[0] = make_snapshot({{SQ_A1, W_KING},
                                  {SQ_H8, B_KING},
                                  {SQ_C3, W_KNIGHT},
                                  {SQ_B5, B_ROOK},
                                  {SQ_D5, W_PAWN},
                                  {SQ_E5, B_PAWN}},
                                 WHITE, SQ_E3);  // Wrong rank.
    malformed[1] = make_snapshot({{SQ_A1, W_KING},
                                  {SQ_H8, B_KING},
                                  {SQ_C3, W_KNIGHT},
                                  {SQ_B5, B_ROOK},
                                  {SQ_D5, W_PAWN},
                                  {SQ_E5, B_PAWN},
                                  {SQ_E6, B_BISHOP}},
                                 WHITE, SQ_E6);  // Occupied center.
    malformed[2] = make_snapshot(
      {{SQ_A1, W_KING}, {SQ_H8, B_KING}, {SQ_C3, W_KNIGHT}, {SQ_B5, B_ROOK}, {SQ_D5, W_PAWN}},
      WHITE, SQ_E6);  // Missing off-center piece.
    malformed[3] = make_snapshot({{SQ_A1, W_KING},
                                  {SQ_H8, B_KING},
                                  {SQ_C3, W_KNIGHT},
                                  {SQ_B5, B_ROOK},
                                  {SQ_D5, W_PAWN},
                                  {SQ_E5, B_ROOK}},
                                 WHITE, SQ_E6);  // Off-center non-pawn.
    malformed[4] = make_snapshot({{SQ_A1, W_KING},
                                  {SQ_H8, B_KING},
                                  {SQ_C3, W_KNIGHT},
                                  {SQ_B5, B_ROOK},
                                  {SQ_D5, W_PAWN},
                                  {SQ_E5, W_PAWN}},
                                 WHITE, SQ_E6);  // Friendly off-center pawn.
    malformed[5] = make_snapshot({{SQ_A1, W_KING},
                                  {SQ_H8, B_KING},
                                  {SQ_C3, W_KNIGHT},
                                  {SQ_B5, B_ROOK},
                                  {SQ_D5, W_ROOK},
                                  {SQ_E5, B_PAWN}},
                                 WHITE, SQ_E6);  // No pawn attacker.
    malformed[6] =
      make_snapshot({{SQ_A1, W_KING}, {SQ_H8, B_KING}, {SQ_C3, W_KNIGHT}, {SQ_B5, B_ROOK}}, WHITE,
                    Square(SQUARE_NB + 1));  // Non-square metadata.

    bool malformedSoftFails = true;
    for (CapturePairSnapshot value : malformed)
    {
        CapturePairSnapshot baseline = value;
        baseline.epSquare            = SQ_NONE;
        CapturePairEmission    actual{};
        CapturePairEmission    expected{};
        const CapturePairError actualError   = emit_capture_pairs(value, WHITE, actual);
        const CapturePairError expectedError = emit_capture_pairs(baseline, WHITE, expected);
        malformedSoftFails =
          malformedSoftFails && actualError == CapturePairError::None
          && expectedError == CapturePairError::None
          && normal_projection(actual) == normal_projection(expected)
          && std::none_of(actual.features.begin(), actual.features.begin() + actual.size,
                          [](const CapturePairFeature& feature) {
                              return feature.enPassant
                                  || feature.localIndex >= CapturePairNormalDimensions;
                          })
          && find_feature(actual, SQ_C3, SQ_B5);
    }
    expect(malformedSoftFails,
           "malformed EP is ignored: normal captures survive and no EP-tail row is emitted");
}

void test_orientation_mirror_order_bounds_and_domain() {
    const CapturePairSnapshot original    = make_snapshot({{SQ_B1, W_KING},
                                                           {SQ_G8, B_KING},
                                                           {SQ_D4, W_QUEEN},
                                                           {SQ_D7, B_ROOK},
                                                           {SQ_D5, W_PAWN},
                                                           {SQ_E5, B_PAWN},
                                                           {SQ_B3, W_BISHOP},
                                                           {SQ_F7, B_KNIGHT}},
                                                          WHITE, SQ_E6);
    const CapturePairSnapshot mirrored    = horizontal_mirror(original);
    bool                      mirrorExact = true;
    for (Color perspective : {WHITE, BLACK})
    {
        CapturePairEmission lhs{};
        CapturePairEmission rhs{};
        const bool          emitted =
          emit_capture_pairs(original, perspective, lhs) == CapturePairError::None
          && emit_capture_pairs(mirrored, perspective, rhs) == CapturePairError::None;
        mirrorExact = mirrorExact && emitted
                   && lhs.orientation.horizontalXor != rhs.orientation.horizontalXor
                   && lhs.orientation.verticalXor == rhs.orientation.verticalXor
                   && same_oriented_emission(lhs, rhs);
        if (emitted && lhs.size == rhs.size)
            for (IndexType index = 0; index < lhs.size; ++index)
                mirrorExact =
                  mirrorExact
                  && rhs.features[index].rawFrom == Square(int(lhs.features[index].rawFrom) ^ 7)
                  && rhs.features[index].rawCenter == Square(int(lhs.features[index].rawCenter) ^ 7)
                  && rhs.features[index].rawCaptured
                       == Square(int(lhs.features[index].rawCaptured) ^ 7);
    }
    expect(mirrorExact,
           "horizontal mirror toggles the orientation branch and preserves every compact index");

    std::uint32_t random     = 0xA70CC0DEU;
    const auto    nextRandom = [&random]() {
        random = random * 1664525U + 1013904223U;
        return random;
    };
    bool bounded = true;
    for (int sample = 0; sample < 64; ++sample)
    {
        CapturePairSnapshot         snapshot{};
        std::array<bool, SQUARE_NB> occupied{};
        const auto                  takeSquare = [&]() {
            Square square = SQ_A1;
            do
                square = Square(nextRandom() % SQUARE_NB);
            while (occupied[square]);
            occupied[square] = true;
            return square;
        };

        snapshot.board[takeSquare()] = W_KING;
        snapshot.board[takeSquare()] = B_KING;
        const int whiteExtras        = int(nextRandom() % 16U);
        const int blackExtras        = int(nextRandom() % 16U);
        for (int index = 0; index < whiteExtras; ++index)
            snapshot.board[takeSquare()] = make_piece(WHITE, PieceType(PAWN + nextRandom() % 5U));
        for (int index = 0; index < blackExtras; ++index)
            snapshot.board[takeSquare()] = make_piece(BLACK, PieceType(PAWN + nextRandom() % 5U));
        snapshot.sideToMove = nextRandom() & 1U ? WHITE : BLACK;

        for (Color perspective : {WHITE, BLACK})
        {
            CapturePairEmission emission{};
            bounded = bounded
                   && emit_capture_pairs(snapshot, perspective, emission) == CapturePairError::None
                   && emission.size <= CapturePairMaximumActiveFeatures
                   && is_canonical_joint_orientation(emission.orientation);
            std::array<IndexType, SQUARE_NB> perActor{};
            for (IndexType index = 0; index < emission.size; ++index)
            {
                const CapturePairFeature& feature = emission.features[index];
                bounded =
                  bounded && feature.localIndex < CapturePairPhysicalDimensions
                  && feature.physicalIndex == CapturePairPhysicalOffset + feature.localIndex
                  && (!index || emission.features[index - 1].localIndex < feature.localIndex)
                  && feature.orientedFrom == emission.orientation.orient(feature.rawFrom)
                  && feature.orientedCenter == emission.orientation.orient(feature.rawCenter)
                  && feature.orientedCaptured == emission.orientation.orient(feature.rawCaptured)
                  && valid_capture_pair_relation(feature.actorRelation)
                  && feature.actorRelation
                       == (color_of(feature.actor) == perspective ? CapturePairActorRelation::Own
                                                                  : CapturePairActorRelation::Opp)
                  && IndexType(feature.targetClass) < CapturePairNormalTargetClasses
                  && !feature.enPassant && feature.rawCenter == feature.rawCaptured;
                ++perActor[feature.rawFrom];
            }
            bounded = bounded && std::all_of(perActor.begin(), perActor.end(), [](IndexType count) {
                          return count <= 8;
                      });
        }
    }
    expect(
      bounded,
      "deterministic dense corpus is sorted, unique, bounded and limited to eight rows per actor");

    CapturePairEmission emission{};
    emission.size                   = 1;
    const CapturePairSnapshot valid = make_snapshot({{SQ_E1, W_KING}, {SQ_E8, B_KING}});
    const bool                invalidPerspective =
      emit_capture_pairs(valid, Color(COLOR_NB), emission) == CapturePairError::InvalidPerspective
      && emission.size == 0;
    CapturePairSnapshot invalidSide = valid;
    invalidSide.sideToMove          = Color(COLOR_NB);
    const bool rejectsSide =
      emit_capture_pairs(invalidSide, WHITE, emission) == CapturePairError::InvalidSideToMove
      && emission.size == 0;
    CapturePairSnapshot missingKing{};
    missingKing.board[SQ_E1] = W_KING;
    const bool rejectsMissing =
      emit_capture_pairs(missingKing, WHITE, emission) == CapturePairError::MissingBlackKing
      && emission.size == 0;
    CapturePairSnapshot multipleKings = valid;
    multipleKings.board[SQ_D1]        = W_KING;
    const bool rejectsMultiple =
      emit_capture_pairs(multipleKings, WHITE, emission) == CapturePairError::MultipleWhiteKings
      && emission.size == 0;
    CapturePairSnapshot invalidPiece = valid;
    invalidPiece.board[SQ_A1]        = Piece(7);
    const bool rejectsPiece =
      emit_capture_pairs(invalidPiece, WHITE, emission) == CapturePairError::InvalidPiece
      && emission.size == 0;
    CapturePairSnapshot tooManyWhite{};
    for (int squareIndex = 0; squareIndex < 17; ++squareIndex)
        tooManyWhite.board[squareIndex] = W_QUEEN;
    tooManyWhite.board[SQ_A1] = W_KING;
    tooManyWhite.board[SQ_H8] = B_KING;
    const bool rejectsMaterial =
      emit_capture_pairs(tooManyWhite, WHITE, emission) == CapturePairError::TooManyPiecesPerColor
      && emission.size == 0;
    expect(invalidPerspective && rejectsSide && rejectsMissing && rejectsMultiple && rejectsPiece
             && rejectsMaterial,
           "invalid perspective, state, kings, pieces and material fail closed with empty output");
}

void test_concurrent_reentrancy_and_determinism() {
    struct ConcurrentCase {
        CapturePairSnapshot                snapshot{};
        std::array<CapturePairError, 2>    errors{};
        std::array<CapturePairEmission, 2> emissions{};
    };

    std::array<ConcurrentCase, 5> cases{};
    cases[0].snapshot = make_snapshot({{SQ_B1, W_KING},
                                       {SQ_G8, B_KING},
                                       {SQ_D4, W_QUEEN},
                                       {SQ_D7, B_ROOK},
                                       {SQ_B3, W_BISHOP},
                                       {SQ_F7, B_KNIGHT}});
    cases[1].snapshot = make_snapshot(
      {{SQ_H1, W_KING}, {SQ_H8, B_KING}, {SQ_D5, W_PAWN}, {SQ_F5, W_PAWN}, {SQ_E5, B_PAWN}}, WHITE,
      SQ_E6);
    cases[2].snapshot =
      make_snapshot({{SQ_A1, W_KING},
                     {SQ_H8, B_KING},
                     {SQ_C3, W_KNIGHT},
                     {SQ_B5, B_ROOK},
                     {SQ_D5, W_PAWN},
                     {SQ_E5, B_ROOK}},
                    WHITE, SQ_E6);  // Malformed EP must retain the normal knight capture.
    cases[3].snapshot            = make_snapshot({{SQ_E1, W_KING}, {SQ_D4, W_ROOK}});
    cases[4].snapshot            = make_snapshot({{SQ_E1, W_KING}, {SQ_E8, B_KING}});
    cases[4].snapshot.sideToMove = Color(COLOR_NB);

    bool baselinesValid = true;
    for (ConcurrentCase& testCase : cases)
        for (int perspectiveIndex = 0; perspectiveIndex < 2; ++perspectiveIndex)
        {
            const Color perspective           = perspectiveIndex == 0 ? WHITE : BLACK;
            testCase.errors[perspectiveIndex] = emit_capture_pairs(
              testCase.snapshot, perspective, testCase.emissions[perspectiveIndex]);
        }
    baselinesValid =
      baselinesValid && cases[0].errors[0] == CapturePairError::None
      && cases[0].errors[1] == CapturePairError::None
      && cases[1].errors[0] == CapturePairError::None
      && cases[1].errors[1] == CapturePairError::None
      && cases[2].errors[0] == CapturePairError::None
      && cases[2].errors[1] == CapturePairError::None
      && cases[3].errors[0] == CapturePairError::MissingBlackKing
      && cases[3].errors[1] == CapturePairError::MissingBlackKing
      && cases[4].errors[0] == CapturePairError::InvalidSideToMove
      && cases[4].errors[1] == CapturePairError::InvalidSideToMove
      && find_feature(cases[2].emissions[0], SQ_C3, SQ_B5)
      && std::none_of(cases[2].emissions[0].features.begin(),
                      cases[2].emissions[0].features.begin() + cases[2].emissions[0].size,
                      [](const CapturePairFeature& feature) { return feature.enPassant; });

    constexpr std::array<int, 4> ThreadCounts{1, 2, 4, 8};
    constexpr int                IterationsPerWorker = 64;
    bool                         deterministic       = baselinesValid;
    const auto&                  immutableCases      = cases;
    for (const int threadCount : ThreadCounts)
    {
        std::array<bool, ThreadCounts.back()> threadResults{};
        std::vector<std::thread>              workers;
        workers.reserve(threadCount);
        for (int workerIndex = 0; workerIndex < threadCount; ++workerIndex)
            workers.emplace_back([&immutableCases, &threadResults, workerIndex]() {
                bool matches = true;
                for (int iteration = 0; iteration < IterationsPerWorker && matches; ++iteration)
                    for (const ConcurrentCase& testCase : immutableCases)
                        for (int perspectiveIndex = 0; perspectiveIndex < 2; ++perspectiveIndex)
                        {
                            CapturePairEmission actual{};
                            const Color         perspective = perspectiveIndex == 0 ? WHITE : BLACK;
                            const CapturePairError error =
                              emit_capture_pairs(testCase.snapshot, perspective, actual);
                            matches =
                              matches && error == testCase.errors[perspectiveIndex]
                              && same_exact_emission(actual, testCase.emissions[perspectiveIndex]);
                        }
                threadResults[workerIndex] = matches;
            });
        for (std::thread& worker : workers)
            worker.join();
        deterministic = deterministic
                     && std::all_of(threadResults.begin(), threadResults.begin() + threadCount,
                                    [](bool matches) { return matches; });
    }
    expect(deterministic,
           "immutable CapturePair snapshots are reentrant and exact across 1/2/4/8 threads");
}

void test_promotions_atomic960_and_position_adapter() {
    const CapturePairSnapshot promotion = make_snapshot(
      {{SQ_A1, W_KING}, {SQ_A8, B_KING}, {SQ_G7, W_PAWN}, {SQ_F8, B_BISHOP}, {SQ_H8, B_ROOK}});
    CapturePairEmission promotionEmission{};
    const bool          promotionEmitted =
      emit_capture_pairs(promotion, WHITE, promotionEmission) == CapturePairError::None;
    const CapturePairFeature* bishop = find_feature(promotionEmission, SQ_G7, SQ_F8);
    const CapturePairFeature* rook   = find_feature(promotionEmission, SQ_G7, SQ_H8);
    expect(promotionEmitted && bishop && rook && feature_count(promotionEmission, SQ_G7) == 2
             && bishop->actor == W_PAWN && rook->actor == W_PAWN
             && bishop->targetClass == CapturePairTargetClass::Bishop
             && rook->targetClass == CapturePairTargetClass::Rook && !bishop->enPassant
             && !rook->enPassant,
           "promotion captures emit one pawn row per occupied target, not four promotion rows");

    constexpr const char* Stateful = "r1k4r/8/8/8/3Q4/8/8/R1K4R w AHah - 0 1";
    constexpr const char* Neutral  = "r1k4r/8/8/8/3Q4/8/8/R1K4R b - - 37 19";
    Position              stateful;
    Position              neutral;
    StateInfo             statefulState{};
    StateInfo             neutralState{};
    if (!set_position(stateful, statefulState, Stateful, true,
                      "Atomic960 stateful CapturePair fixture accepted")
        || !set_position(neutral, neutralState, Neutral, true,
                         "Atomic960 neutral CapturePair fixture accepted"))
        return;

    bool neutralityExact = true;
    for (Color perspective : {WHITE, BLACK})
    {
        CapturePairEmission       fromPosition{};
        CapturePairEmission       fromSnapshot{};
        CapturePairEmission       neutralEmission{};
        const CapturePairSnapshot snapshot = snapshot_from_position(stateful);
        const bool                emitted =
          emit_capture_pairs(stateful, perspective, fromPosition) == CapturePairError::None
          && emit_capture_pairs(snapshot, perspective, fromSnapshot) == CapturePairError::None
          && emit_capture_pairs(neutral, perspective, neutralEmission) == CapturePairError::None;
        neutralityExact = neutralityExact && emitted
                       && same_exact_emission(fromPosition, fromSnapshot)
                       && same_exact_emission(fromPosition, neutralEmission);
    }
    expect(
      neutralityExact,
      "Position adapter is exact and Atomic960 castling, clocks and STM are neutral without EP");
}

void run_tests() {
    test_constants_geometry_and_indexing();
    test_normal_captures_targets_and_blockers();
    test_en_passant();
    test_orientation_mirror_order_bounds_and_domain();
    test_concurrent_reentrancy_and_determinism();
    test_promotions_atomic960_and_position_adapter();
}

Piece piece_from_fen(char token) {
    switch (token)
    {
    case 'P' :
        return W_PAWN;
    case 'N' :
        return W_KNIGHT;
    case 'B' :
        return W_BISHOP;
    case 'R' :
        return W_ROOK;
    case 'Q' :
        return W_QUEEN;
    case 'K' :
        return W_KING;
    case 'p' :
        return B_PAWN;
    case 'n' :
        return B_KNIGHT;
    case 'b' :
        return B_BISHOP;
    case 'r' :
        return B_ROOK;
    case 'q' :
        return B_QUEEN;
    case 'k' :
        return B_KING;
    default :
        return NO_PIECE;
    }
}

bool parse_board_placement(std::string_view placement, HmBoard& board) {
    board    = {};
    int rank = 7;
    int file = 0;
    for (char token : placement)
    {
        if (token == '/')
        {
            if (file != 8 || rank == 0)
                return false;
            --rank;
            file = 0;
            continue;
        }
        if (token >= '1' && token <= '8')
        {
            file += token - '0';
            if (file > 8)
                return false;
            continue;
        }
        const Piece piece = piece_from_fen(token);
        if (piece == NO_PIECE || file >= 8)
            return false;
        board[rank * 8 + file] = piece;
        ++file;
    }
    return rank == 0 && file == 8;
}

bool parse_color(std::string_view text, Color& color) {
    if (text == "white" || text == "w")
    {
        color = WHITE;
        return true;
    }
    if (text == "black" || text == "b")
    {
        color = BLACK;
        return true;
    }
    return false;
}

bool parse_square(std::string_view text, Square& square) {
    if (text == "-")
    {
        square = SQ_NONE;
        return true;
    }
    if (text.size() != 2 || text[0] < 'a' || text[0] > 'h' || text[1] < '1' || text[1] > '8')
        return false;
    square = make_square(File(text[0] - 'a'), Rank(text[1] - '1'));
    return true;
}

void dump_emission(const CapturePairSnapshot& snapshot,
                   Color                      perspective,
                   CapturePairError           error,
                   const CapturePairEmission& emission) {
    const JointOrientation& orientation = emission.orientation;
    std::cout << "record=capture_pair"
              << " perspective=" << color_name(perspective)
              << " side_to_move=" << color_name(snapshot.sideToMove)
              << " ep_square=" << int(snapshot.epSquare) << " error=" << error_name(error)
              << " error_code=" << int(error) << " vertical_xor=" << int(orientation.verticalXor)
              << " horizontal_xor=" << int(orientation.horizontalXor)
              << " own_king=" << int(orientation.ownKing)
              << " oriented_own_king=" << int(orientation.orientedOwnKing)
              << " king_bucket=" << orientation.kingBucket << " features=" << emission.size << '\n';
    for (IndexType index = 0; index < emission.size; ++index)
    {
        const CapturePairFeature& feature = emission.features[index];
        std::cout << "feature=" << index << " local=" << feature.localIndex
                  << " physical=" << feature.physicalIndex
                  << " relation=" << relation_name(feature.actorRelation)
                  << " target=" << target_name(feature.targetClass)
                  << " en_passant=" << int(feature.enPassant) << " actor=" << int(feature.actor)
                  << " captured=" << int(feature.captured) << " raw_from=" << int(feature.rawFrom)
                  << " raw_center=" << int(feature.rawCenter)
                  << " raw_captured=" << int(feature.rawCaptured)
                  << " oriented_from=" << int(feature.orientedFrom)
                  << " oriented_center=" << int(feature.orientedCenter)
                  << " oriented_captured=" << int(feature.orientedCaptured)
                  << " edge=" << feature.edgeOrdinal << " ep_ordinal=" << feature.epOrdinal << '\n';
    }
}

int dump_fen(const std::string& fen, bool chess960) {
    Position  position;
    StateInfo state{};
    if (const auto error = position.set(fen, chess960, &state))
    {
        std::cerr << error->what() << '\n';
        return EXIT_FAILURE;
    }
    const CapturePairSnapshot snapshot = snapshot_from_position(position);
    for (Color perspective : {WHITE, BLACK})
    {
        CapturePairEmission    emission{};
        const CapturePairError error = emit_capture_pairs(position, perspective, emission);
        dump_emission(snapshot, perspective, error, emission);
    }
    return EXIT_SUCCESS;
}

int dump_snapshot(std::string_view perspectiveText,
                  std::string_view sideToMoveText,
                  std::string_view epText,
                  std::string_view placement) {
    Color               perspective = WHITE;
    CapturePairSnapshot snapshot{};
    if (!parse_color(perspectiveText, perspective)
        || !parse_color(sideToMoveText, snapshot.sideToMove)
        || !parse_square(epText, snapshot.epSquare)
        || !parse_board_placement(placement, snapshot.board))
    {
        std::cerr << "snapshot requires PERSPECTIVE STM EP PLACEMENT as white|black, white|black, "
                     "-|a1..h8 and an eight-rank FEN placement\n";
        return EXIT_FAILURE;
    }

    CapturePairEmission    emission{};
    const CapturePairError error = emit_capture_pairs(snapshot, perspective, emission);
    dump_emission(snapshot, perspective, error, emission);
    return EXIT_SUCCESS;
}

}  // namespace
}  // namespace Stockfish::Eval::NNUE::AtomicV3

int main(int argc, char* argv[]) {
    using namespace Stockfish;
    using namespace Stockfish::Eval::NNUE::AtomicV3;

    Bitboards::init();
    Attacks::init();
    Position::init();

    if (argc == 1)
    {
        run_tests();
        if (failures != 0)
        {
            std::cerr << "Atomic V3 CapturePair oracle tests failed: " << failures << '\n';
            return EXIT_FAILURE;
        }
        std::cout << "Atomic V3 CapturePair oracle tests passed\n";
        return EXIT_SUCCESS;
    }

    if (argc == 3 && (std::string(argv[1]) == "--fen" || std::string(argv[1]) == "--chess960-fen"))
        return dump_fen(argv[2], std::string(argv[1]) == "--chess960-fen");

    if (argc == 6 && std::string(argv[1]) == "--snapshot")
        return dump_snapshot(argv[2], argv[3], argv[4], argv[5]);

    std::cerr << "usage: atomic-v3-capture-pair-tests [--fen FEN | --chess960-fen FEN | "
                 "--snapshot PERSPECTIVE STM EP PLACEMENT]\n";
    return EXIT_FAILURE;
}
