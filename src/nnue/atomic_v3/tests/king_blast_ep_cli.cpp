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
#include <vector>

#include "../../../attacks.h"
#include "../../../bitboard.h"
#include "../../../position.h"
#include "../king_blast_ep.h"

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

const char* class_name(KingBlastEpRelationClass relationClass) {
    switch (relationClass)
    {
    case KingBlastEpRelationClass::EnemyKingCenter :
        return "enemy_king_center";
    case KingBlastEpRelationClass::EnemyKingN :
        return "enemy_king_n";
    case KingBlastEpRelationClass::EnemyKingNE :
        return "enemy_king_ne";
    case KingBlastEpRelationClass::EnemyKingE :
        return "enemy_king_e";
    case KingBlastEpRelationClass::EnemyKingSE :
        return "enemy_king_se";
    case KingBlastEpRelationClass::EnemyKingS :
        return "enemy_king_s";
    case KingBlastEpRelationClass::EnemyKingSW :
        return "enemy_king_sw";
    case KingBlastEpRelationClass::EnemyKingW :
        return "enemy_king_w";
    case KingBlastEpRelationClass::EnemyKingNW :
        return "enemy_king_nw";
    case KingBlastEpRelationClass::OwnKingN :
        return "own_king_n";
    case KingBlastEpRelationClass::OwnKingNE :
        return "own_king_ne";
    case KingBlastEpRelationClass::OwnKingE :
        return "own_king_e";
    case KingBlastEpRelationClass::OwnKingSE :
        return "own_king_se";
    case KingBlastEpRelationClass::OwnKingS :
        return "own_king_s";
    case KingBlastEpRelationClass::OwnKingSW :
        return "own_king_sw";
    case KingBlastEpRelationClass::OwnKingW :
        return "own_king_w";
    case KingBlastEpRelationClass::OwnKingNW :
        return "own_king_nw";
    case KingBlastEpRelationClass::EnPassantMarker :
        return "en_passant_marker";
    }
    return "invalid";
}

const char* error_name(KingBlastEpError error) {
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

const KingBlastEpFeature* find_feature(const KingBlastEpEmission& emission,
                                       Square                     rawCenter,
                                       CapturePairActorRelation   actorRelation,
                                       KingBlastEpRelationClass   relationClass) {
    for (IndexType index = 0; index < emission.size; ++index)
    {
        const KingBlastEpFeature& feature = emission.features[index];
        if (feature.rawCenter == rawCenter && feature.actorRelation == actorRelation
            && feature.relationClass == relationClass)
            return &feature;
    }
    return nullptr;
}

IndexType count_class(const KingBlastEpEmission& emission,
                      CapturePairActorRelation   actorRelation,
                      KingBlastEpRelationClass   relationClass) {
    IndexType result = 0;
    for (IndexType index = 0; index < emission.size; ++index)
        result += emission.features[index].actorRelation == actorRelation
               && emission.features[index].relationClass == relationClass;
    return result;
}

bool same_oriented_emission(const KingBlastEpEmission& lhs, const KingBlastEpEmission& rhs) {
    if (lhs.size != rhs.size || lhs.orientation.kingBucket != rhs.orientation.kingBucket)
        return false;
    for (IndexType index = 0; index < lhs.size; ++index)
    {
        const KingBlastEpFeature& a = lhs.features[index];
        const KingBlastEpFeature& b = rhs.features[index];
        if (a.orientedCenter != b.orientedCenter || a.orientedRelatedKing != b.orientedRelatedKing
            || a.actorRelation != b.actorRelation || a.relationClass != b.relationClass
            || a.localIndex != b.localIndex || a.physicalIndex != b.physicalIndex
            || a.enPassantMarker != b.enPassantMarker)
            return false;
    }
    return true;
}

bool same_exact_emission(const KingBlastEpEmission& lhs, const KingBlastEpEmission& rhs) {
    if (!same_oriented_emission(lhs, rhs)
        || lhs.orientation.perspective != rhs.orientation.perspective
        || lhs.orientation.ownKing != rhs.orientation.ownKing
        || lhs.orientation.orientedOwnKing != rhs.orientation.orientedOwnKing
        || lhs.orientation.verticalXor != rhs.orientation.verticalXor
        || lhs.orientation.horizontalXor != rhs.orientation.horizontalXor)
        return false;
    for (IndexType index = 0; index < lhs.size; ++index)
        if (lhs.features[index].rawCenter != rhs.features[index].rawCenter
            || lhs.features[index].rawRelatedKing != rhs.features[index].rawRelatedKing)
            return false;
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

void test_constants_directions_and_indexing() {
    expect(KingBlastEpCenterDimensions == 64 && KingBlastEpActorRelations == 2
             && KingBlastEpRelationClasses == 18 && KingBlastEpPhysicalDimensions == 2304
             && KingBlastEpPhysicalOffset == 62540
             && KingBlastEpPhysicalOffset + KingBlastEpPhysicalDimensions == 64844
             && KingBlastEpMaximumActiveFeatures == 35,
           "KingBlastEP constants match the provisional 64x2x18 contract");

    std::array<bool, KingBlastEpPhysicalDimensions> seen{};
    bool                                            exact = true;
    for (int center = 0; center < SQUARE_NB; ++center)
        for (IndexType relation = 0; relation < KingBlastEpActorRelations; ++relation)
            for (IndexType relationClass = 0; relationClass < KingBlastEpRelationClasses;
                 ++relationClass)
            {
                IndexType localIndex = KingBlastEpPhysicalDimensions;
                exact                = exact
                     && king_blast_ep_index(Square(center), CapturePairActorRelation(relation),
                                            KingBlastEpRelationClass(relationClass), localIndex)
                     && localIndex
                          == (IndexType(center) * KingBlastEpActorRelations + relation)
                                 * KingBlastEpRelationClasses
                               + relationClass
                     && localIndex < KingBlastEpPhysicalDimensions && !seen[localIndex];
                if (localIndex < KingBlastEpPhysicalDimensions)
                    seen[localIndex] = true;
            }
    exact = exact && std::all_of(seen.begin(), seen.end(), [](bool value) { return value; });
    expect(exact, "all 2,304 KingBlastEP rows are contiguous and unique");

    constexpr std::array<Square, 8> Related{SQ_D5, SQ_E5, SQ_E4, SQ_E3, SQ_D3, SQ_C3, SQ_C4, SQ_C5};
    bool                            directions = true;
    for (IndexType direction = 0; direction < Related.size(); ++direction)
    {
        KingBlastEpRelationClass enemy{};
        KingBlastEpRelationClass own{};
        directions = directions
                  && king_blast_ep_relation_class(SQ_D4, Related[direction], true, enemy)
                  && enemy == KingBlastEpRelationClass(1 + direction)
                  && king_blast_ep_relation_class(SQ_D4, Related[direction], false, own)
                  && own == KingBlastEpRelationClass(9 + direction);
    }
    KingBlastEpRelationClass direct{};
    directions = directions && king_blast_ep_relation_class(SQ_D4, SQ_D4, true, direct)
              && direct == KingBlastEpRelationClass::EnemyKingCenter
              && !king_blast_ep_relation_class(SQ_D4, SQ_D4, false, direct)
              && !king_blast_ep_relation_class(SQ_A1, SQ_H1, true, direct);
    expect(directions, "center d4 freezes direct and N,NE,E,SE,S,SW,W,NW king direction polarity");

    IndexType  first           = KingBlastEpPhysicalDimensions;
    IndexType  last            = KingBlastEpPhysicalDimensions;
    const auto invalidRelation = CapturePairActorRelation(KingBlastEpActorRelations);
    expect(king_blast_ep_index(SQ_A1, CapturePairActorRelation::Own,
                               KingBlastEpRelationClass::EnemyKingCenter, first)
             && first == 0 && KingBlastEpPhysicalOffset + first == 62540
             && king_blast_ep_index(SQ_H8, CapturePairActorRelation::Opp,
                                    KingBlastEpRelationClass::EnPassantMarker, last)
             && last == 2303 && KingBlastEpPhysicalOffset + last == 64843
             && !king_blast_ep_index(SQ_NONE, CapturePairActorRelation::Own,
                                     KingBlastEpRelationClass::EnemyKingCenter, first)
             && !king_blast_ep_index(SQ_A1, invalidRelation,
                                     KingBlastEpRelationClass::EnemyKingCenter, first)
             && !king_blast_ep_index(SQ_A1, CapturePairActorRelation::Own,
                                     KingBlastEpRelationClass(KingBlastEpRelationClasses), first),
           "local/physical endpoints and invalid index domains are exact");
}

void test_direct_blast_self_blast_and_dedup() {
    const CapturePairSnapshot direct =
      make_snapshot({{SQ_H1, W_KING}, {SQ_D4, B_KING}, {SQ_F3, W_KNIGHT}});
    KingBlastEpEmission directEmission{};
    const bool          directOk =
      emit_king_blast_ep(direct, WHITE, directEmission) == CapturePairError::None;
    const KingBlastEpFeature* directFeature =
      find_feature(directEmission, SQ_D4, CapturePairActorRelation::Own,
                   KingBlastEpRelationClass::EnemyKingCenter);
    expect(directOk && directFeature && directFeature->localIndex == 972
             && directFeature->physicalIndex == 63512 && directFeature->rawRelatedKing == SQ_D4,
           "a CP target KING emits the direct center class without a legality filter");

    const CapturePairSnapshot directAndOwn =
      make_snapshot({{SQ_E3, W_KING}, {SQ_D4, B_KING}, {SQ_F3, W_KNIGHT}});
    KingBlastEpEmission directAndOwnEmission{};
    const bool          directAndOwnOk =
      emit_king_blast_ep(directAndOwn, WHITE, directAndOwnEmission) == CapturePairError::None;
    expect(directAndOwnOk
             && find_feature(directAndOwnEmission, SQ_D4, CapturePairActorRelation::Own,
                             KingBlastEpRelationClass::EnemyKingCenter)
             && find_feature(directAndOwnEmission, SQ_D4, CapturePairActorRelation::Own,
                             KingBlastEpRelationClass::OwnKingSE),
           "direct enemy-king capture and adjacent own-king self-blast coexist");

    const CapturePairSnapshot twoAttackers = make_snapshot(
      {{SQ_H1, W_KING}, {SQ_D5, B_KING}, {SQ_C2, W_KNIGHT}, {SQ_F3, W_KNIGHT}, {SQ_D4, B_PAWN}});
    KingBlastEpEmission twoAttackersEmission{};
    const bool          twoAttackersOk =
      emit_king_blast_ep(twoAttackers, WHITE, twoAttackersEmission) == CapturePairError::None;
    expect(twoAttackersOk
             && count_class(twoAttackersEmission, CapturePairActorRelation::Own,
                            KingBlastEpRelationClass::EnemyKingN)
                  == 1,
           "multiple CP attackers of one center deduplicate the boolean king relation");

    const CapturePairSnapshot bothKings =
      make_snapshot({{SQ_E3, W_KING}, {SQ_D5, B_KING}, {SQ_C2, W_KNIGHT}, {SQ_D4, B_PAWN}});
    KingBlastEpEmission bothKingsEmission{};
    const bool          bothKingsOk =
      emit_king_blast_ep(bothKings, WHITE, bothKingsEmission) == CapturePairError::None;
    expect(bothKingsOk
             && find_feature(bothKingsEmission, SQ_D4, CapturePairActorRelation::Own,
                             KingBlastEpRelationClass::EnemyKingN)
             && find_feature(bothKingsEmission, SQ_D4, CapturePairActorRelation::Own,
                             KingBlastEpRelationClass::OwnKingSE),
           "one capture center may encode simultaneous enemy blast and own self-blast");

    const CapturePairSnapshot bothRelations = make_snapshot({{SQ_F3, W_KING},
                                                             {SQ_E6, B_KING},
                                                             {SQ_C3, W_KNIGHT},
                                                             {SQ_D5, B_PAWN},
                                                             {SQ_F6, B_KNIGHT},
                                                             {SQ_E4, W_PAWN}});
    KingBlastEpEmission       bothRelationsEmission{};
    const bool                bothRelationsOk =
      emit_king_blast_ep(bothRelations, WHITE, bothRelationsEmission) == CapturePairError::None;
    expect(bothRelationsOk
             && find_feature(bothRelationsEmission, SQ_D5, CapturePairActorRelation::Own,
                             KingBlastEpRelationClass::EnemyKingNE)
             && find_feature(bothRelationsEmission, SQ_E4, CapturePairActorRelation::Opp,
                             KingBlastEpRelationClass::EnemyKingSE),
           "OWN and OPP actor relations remain separate on their physically distinct centers");

    const CapturePairSnapshot touching = make_snapshot({{SQ_E4, W_KING}, {SQ_E5, B_KING}});
    KingBlastEpEmission       touchingWhite{};
    KingBlastEpEmission       touchingBlack{};
    expect(emit_king_blast_ep(touching, WHITE, touchingWhite) == CapturePairError::None
             && emit_king_blast_ep(touching, BLACK, touchingBlack) == CapturePairError::None
             && touchingWhite.size == 0 && touchingBlack.size == 0,
           "touching kings alone emit no KBR row because kings are never CP actors");
}

void test_en_passant_and_malformed_metadata() {
    const CapturePairSnapshot whiteEp = make_snapshot(
      {{SQ_F7, W_KING}, {SQ_D7, B_KING}, {SQ_D5, W_PAWN}, {SQ_F5, W_PAWN}, {SQ_E5, B_PAWN}}, WHITE,
      SQ_E6);
    KingBlastEpEmission whiteOwn{};
    KingBlastEpEmission whiteOpp{};
    const bool whiteOk = emit_king_blast_ep(whiteEp, WHITE, whiteOwn) == CapturePairError::None
                      && emit_king_blast_ep(whiteEp, BLACK, whiteOpp) == CapturePairError::None;
    const KingBlastEpFeature* ownMarker = find_feature(
      whiteOwn, SQ_E6, CapturePairActorRelation::Own, KingBlastEpRelationClass::EnPassantMarker);
    const KingBlastEpFeature* oppMarker = find_feature(
      whiteOpp, SQ_E6, CapturePairActorRelation::Opp, KingBlastEpRelationClass::EnPassantMarker);
    expect(whiteOk && ownMarker && oppMarker
             && count_class(whiteOwn, CapturePairActorRelation::Own,
                            KingBlastEpRelationClass::EnPassantMarker)
                  == 1
             && count_class(whiteOpp, CapturePairActorRelation::Opp,
                            KingBlastEpRelationClass::EnPassantMarker)
                  == 1
             && ownMarker->rawRelatedKing == SQ_NONE && ownMarker->orientedRelatedKing == SQ_NONE
             && find_feature(whiteOwn, SQ_E6, CapturePairActorRelation::Own,
                             KingBlastEpRelationClass::EnemyKingNW)
             && find_feature(whiteOwn, SQ_E6, CapturePairActorRelation::Own,
                             KingBlastEpRelationClass::OwnKingNE),
           "two EP origins deduplicate one landing-center marker beside both kings");

    const CapturePairSnapshot blackEp = make_snapshot(
      {{SQ_C2, W_KING}, {SQ_E2, B_KING}, {SQ_C4, B_PAWN}, {SQ_E4, B_PAWN}, {SQ_D4, W_PAWN}}, BLACK,
      SQ_D3);
    KingBlastEpEmission blackOwn{};
    const bool blackOk = emit_king_blast_ep(blackEp, BLACK, blackOwn) == CapturePairError::None;
    KingBlastEpRelationClass expectedEnemy{};
    KingBlastEpRelationClass expectedOwn{};
    const bool               expectedBlackClasses =
      king_blast_ep_relation_class(blackOwn.orientation.orient(SQ_D3),
                                   blackOwn.orientation.orient(SQ_C2), true, expectedEnemy)
      && king_blast_ep_relation_class(blackOwn.orientation.orient(SQ_D3),
                                      blackOwn.orientation.orient(SQ_E2), false, expectedOwn);
    expect(blackOk && expectedBlackClasses
             && find_feature(blackOwn, SQ_D3, CapturePairActorRelation::Own,
                             KingBlastEpRelationClass::EnPassantMarker)
             && find_feature(blackOwn, SQ_D3, CapturePairActorRelation::Own, expectedEnemy)
             && find_feature(blackOwn, SQ_D3, CapturePairActorRelation::Own, expectedOwn),
           "black EP uses the same joint frame and the actor-relative own relation");

    const CapturePairSnapshot malformed = make_snapshot(
      {{SQ_H1, W_KING}, {SQ_C6, B_KING}, {SQ_C3, W_KNIGHT}, {SQ_B5, B_ROOK}}, WHITE, SQ_E3);
    CapturePairSnapshot baseline = malformed;
    baseline.epSquare            = SQ_NONE;
    KingBlastEpEmission malformedEmission{};
    KingBlastEpEmission baselineEmission{};
    const bool          malformedOk =
      emit_king_blast_ep(malformed, WHITE, malformedEmission) == CapturePairError::None
      && emit_king_blast_ep(baseline, WHITE, baselineEmission) == CapturePairError::None;
    expect(malformedOk && same_exact_emission(malformedEmission, baselineEmission)
             && find_feature(malformedEmission, SQ_B5, CapturePairActorRelation::Own,
                             KingBlastEpRelationClass::EnemyKingNE)
             && count_class(malformedEmission, CapturePairActorRelation::Own,
                            KingBlastEpRelationClass::EnPassantMarker)
                  == 0,
           "malformed EP succeeds, preserves normal CP projection and omits only the marker");
}

void test_illegal_candidates_promotions_and_projection_seam() {
    const CapturePairSnapshot pinnedDirect =
      make_snapshot({{SQ_E1, W_KING}, {SQ_H2, B_KING}, {SQ_E2, W_ROOK}, {SQ_E8, B_ROOK}});
    KingBlastEpEmission pinnedEmission{};
    expect(emit_king_blast_ep(pinnedDirect, WHITE, pinnedEmission) == CapturePairError::None
             && find_feature(pinnedEmission, SQ_H2, CapturePairActorRelation::Own,
                             KingBlastEpRelationClass::EnemyKingCenter),
           "a pinned CP actor still projects a direct KING target");

    const CapturePairSnapshot selfBlast =
      make_snapshot({{SQ_E4, W_KING}, {SQ_H8, B_KING}, {SQ_F3, W_KNIGHT}, {SQ_E5, B_PAWN}});
    KingBlastEpEmission selfBlastEmission{};
    expect(emit_king_blast_ep(selfBlast, WHITE, selfBlastEmission) == CapturePairError::None
             && find_feature(selfBlastEmission, SQ_E5, CapturePairActorRelation::Own,
                             KingBlastEpRelationClass::OwnKingS),
           "an illegal self-blasting CP candidate remains visible to KBR");

    const CapturePairSnapshot promotion =
      make_snapshot({{SQ_H1, W_KING}, {SQ_E7, B_KING}, {SQ_G7, W_PAWN}, {SQ_F8, B_BISHOP}});
    KingBlastEpEmission promotionEmission{};
    expect(emit_king_blast_ep(promotion, WHITE, promotionEmission) == CapturePairError::None
             && count_class(promotionEmission, CapturePairActorRelation::Own,
                            KingBlastEpRelationClass::EnemyKingSW)
                  == 1,
           "a promotion-rank pawn capture projects one boolean relation, never four choices");

    CapturePairEmission capturePairs{};
    KingBlastEpEmission publicEmission{};
    KingBlastEpEmission projectedEmission{};
    const bool          seamOk =
      emit_capture_pairs(selfBlast, WHITE, capturePairs) == CapturePairError::None
      && emit_king_blast_ep(selfBlast, WHITE, publicEmission) == CapturePairError::None
      && Detail::project_king_blast_ep(selfBlast, WHITE, capturePairs, projectedEmission)
           == CapturePairError::None;
    expect(seamOk && same_exact_emission(publicEmission, projectedEmission),
           "the reusable projector is exact with the one-call public CP path");

    CapturePairEmission tampered = capturePairs;
    if (tampered.size)
        ++tampered.features[0].localIndex;
    projectedEmission.size = 1;
    expect(tampered.size
             && Detail::project_king_blast_ep(selfBlast, WHITE, tampered, projectedEmission)
                  == CapturePairError::NonCanonicalOrder
             && projectedEmission.size == 0,
           "the projector rejects a non-canonical CP index and empties caller output");

    bool invalidPieceRejected = true;
    for (Piece invalidPiece : {Piece(7), Piece(254)})
    {
        CapturePairSnapshot invalidSnapshot = selfBlast;
        invalidSnapshot.board[SQ_A1]        = invalidPiece;
        projectedEmission.size              = 1;
        invalidPieceRejected =
          invalidPieceRejected
          && Detail::project_king_blast_ep(invalidSnapshot, WHITE, capturePairs, projectedEmission)
               == CapturePairError::NonCanonicalOrder
          && projectedEmission.size == 0;
    }
    expect(invalidPieceRejected,
           "the trusted projector rejects invalid board piece codes before type/color access");
}

void test_orientation_order_bounds_and_errors() {
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
        KingBlastEpEmission lhs{};
        KingBlastEpEmission rhs{};
        const bool          emitted =
          emit_king_blast_ep(original, perspective, lhs) == CapturePairError::None
          && emit_king_blast_ep(mirrored, perspective, rhs) == CapturePairError::None;
        mirrorExact = mirrorExact && emitted
                   && lhs.orientation.horizontalXor != rhs.orientation.horizontalXor
                   && lhs.orientation.verticalXor == rhs.orientation.verticalXor
                   && same_oriented_emission(lhs, rhs);
        if (emitted && lhs.size == rhs.size)
            for (IndexType index = 0; index < lhs.size; ++index)
            {
                mirrorExact = mirrorExact
                           && rhs.features[index].rawCenter
                                == Square(int(lhs.features[index].rawCenter) ^ 7);
                if (lhs.features[index].rawRelatedKing != SQ_NONE)
                    mirrorExact = mirrorExact
                               && rhs.features[index].rawRelatedKing
                                    == Square(int(lhs.features[index].rawRelatedKing) ^ 7);
            }
    }
    expect(mirrorExact,
           "horizontal mirror toggles each perspective branch and preserves every KBR index");

    std::uint32_t random     = 0xA70CB1A5U;
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
            KingBlastEpEmission emission{};
            bounded = bounded
                   && emit_king_blast_ep(snapshot, perspective, emission) == CapturePairError::None
                   && emission.size <= KingBlastEpMaximumActiveFeatures
                   && is_canonical_joint_orientation(emission.orientation);
            std::array<IndexType, KingBlastEpActorRelations> enemyCounts{};
            std::array<IndexType, KingBlastEpActorRelations> ownCounts{};
            IndexType                                        epCount = 0;
            for (IndexType index = 0; index < emission.size; ++index)
            {
                const KingBlastEpFeature& feature       = emission.features[index];
                IndexType                 expectedIndex = KingBlastEpPhysicalDimensions;
                bounded                                 = bounded
                       && king_blast_ep_index(feature.orientedCenter, feature.actorRelation,
                                              feature.relationClass, expectedIndex)
                       && expectedIndex == feature.localIndex
                       && feature.physicalIndex == KingBlastEpPhysicalOffset + feature.localIndex
                       && (!index || emission.features[index - 1].localIndex < feature.localIndex)
                       && feature.orientedCenter == emission.orientation.orient(feature.rawCenter);
                const IndexType relation = IndexType(feature.actorRelation);
                if (feature.relationClass == KingBlastEpRelationClass::EnPassantMarker)
                {
                    ++epCount;
                    bounded = bounded && feature.enPassantMarker
                           && feature.rawRelatedKing == SQ_NONE
                           && feature.orientedRelatedKing == SQ_NONE;
                }
                else
                {
                    const bool enemy = IndexType(feature.relationClass)
                                    <= IndexType(KingBlastEpRelationClass::EnemyKingNW);
                    KingBlastEpRelationClass expectedClass{};
                    bounded = bounded && !feature.enPassantMarker
                           && feature.orientedRelatedKing
                                == emission.orientation.orient(feature.rawRelatedKing)
                           && king_blast_ep_relation_class(feature.orientedCenter,
                                                           feature.orientedRelatedKing, enemy,
                                                           expectedClass)
                           && expectedClass == feature.relationClass;
                    if (enemy)
                        ++enemyCounts[relation];
                    else
                        ++ownCounts[relation];
                }
            }
            bounded = bounded && epCount <= 1;
            for (IndexType relation = 0; relation < KingBlastEpActorRelations; ++relation)
                bounded = bounded && enemyCounts[relation] <= 9 && ownCounts[relation] <= 8;
        }
    }
    expect(bounded,
           "deterministic dense corpus is sorted, unique and obeys the 17*2+1 bound proof");

    KingBlastEpEmission emission{};
    emission.size                   = 1;
    const CapturePairSnapshot valid = make_snapshot({{SQ_E1, W_KING}, {SQ_E8, B_KING}});
    const bool                invalidPerspective =
      emit_king_blast_ep(valid, Color(COLOR_NB), emission) == CapturePairError::InvalidPerspective
      && emission.size == 0;
    CapturePairSnapshot invalidSide = valid;
    invalidSide.sideToMove          = Color(COLOR_NB);
    const bool rejectsSide =
      emit_king_blast_ep(invalidSide, WHITE, emission) == CapturePairError::InvalidSideToMove
      && emission.size == 0;
    CapturePairSnapshot missingKing{};
    missingKing.board[SQ_E1] = W_KING;
    const bool rejectsMissing =
      emit_king_blast_ep(missingKing, WHITE, emission) == CapturePairError::MissingBlackKing
      && emission.size == 0 && emission.orientation.ownKing == SQ_NONE;
    CapturePairSnapshot multipleKings = valid;
    multipleKings.board[SQ_D1]        = W_KING;
    const bool rejectsMultiple =
      emit_king_blast_ep(multipleKings, WHITE, emission) == CapturePairError::MultipleWhiteKings
      && emission.size == 0;
    CapturePairSnapshot invalidPiece = valid;
    invalidPiece.board[SQ_A1]        = Piece(7);
    const bool rejectsPiece =
      emit_king_blast_ep(invalidPiece, WHITE, emission) == CapturePairError::InvalidPiece
      && emission.size == 0;
    CapturePairSnapshot tooManyWhite{};
    for (int squareIndex = 0; squareIndex < 17; ++squareIndex)
        tooManyWhite.board[squareIndex] = W_QUEEN;
    tooManyWhite.board[SQ_A1] = W_KING;
    tooManyWhite.board[SQ_H8] = B_KING;
    const bool rejectsMaterial =
      emit_king_blast_ep(tooManyWhite, WHITE, emission) == CapturePairError::TooManyPiecesPerColor
      && emission.size == 0;
    expect(invalidPerspective && rejectsSide && rejectsMissing && rejectsMultiple && rejectsPiece
             && rejectsMaterial,
           "all CP validation errors propagate exactly and leave empty default output");
}

void test_concurrent_reentrancy_and_position_adapter() {
    struct ConcurrentCase {
        CapturePairSnapshot                snapshot{};
        std::array<KingBlastEpError, 2>    errors{};
        std::array<KingBlastEpEmission, 2> emissions{};
    };

    std::array<ConcurrentCase, 5> cases{};
    cases[0].snapshot =
      make_snapshot({{SQ_E3, W_KING}, {SQ_D5, B_KING}, {SQ_C2, W_KNIGHT}, {SQ_D4, B_PAWN}});
    cases[1].snapshot = make_snapshot(
      {{SQ_F7, W_KING}, {SQ_D7, B_KING}, {SQ_D5, W_PAWN}, {SQ_F5, W_PAWN}, {SQ_E5, B_PAWN}}, WHITE,
      SQ_E6);
    cases[2].snapshot = make_snapshot(
      {{SQ_H1, W_KING}, {SQ_C6, B_KING}, {SQ_C3, W_KNIGHT}, {SQ_B5, B_ROOK}}, WHITE, SQ_E3);
    cases[3].snapshot            = make_snapshot({{SQ_E1, W_KING}, {SQ_D4, W_ROOK}});
    cases[4].snapshot            = make_snapshot({{SQ_E1, W_KING}, {SQ_E8, B_KING}});
    cases[4].snapshot.sideToMove = Color(COLOR_NB);

    for (ConcurrentCase& testCase : cases)
        for (int perspectiveIndex = 0; perspectiveIndex < 2; ++perspectiveIndex)
            testCase.errors[perspectiveIndex] =
              emit_king_blast_ep(testCase.snapshot, perspectiveIndex == 0 ? WHITE : BLACK,
                                 testCase.emissions[perspectiveIndex]);

    bool deterministic = cases[0].errors[0] == CapturePairError::None
                      && cases[0].errors[1] == CapturePairError::None
                      && cases[1].errors[0] == CapturePairError::None
                      && cases[1].errors[1] == CapturePairError::None
                      && cases[2].errors[0] == CapturePairError::None
                      && cases[2].errors[1] == CapturePairError::None
                      && cases[3].errors[0] == CapturePairError::MissingBlackKing
                      && cases[3].errors[1] == CapturePairError::MissingBlackKing
                      && cases[4].errors[0] == CapturePairError::InvalidSideToMove
                      && cases[4].errors[1] == CapturePairError::InvalidSideToMove;

    constexpr std::array<int, 4> ThreadCounts{1, 2, 4, 8};
    constexpr int                IterationsPerWorker = 64;
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
                            KingBlastEpEmission    actual{};
                            const KingBlastEpError error = emit_king_blast_ep(
                              testCase.snapshot, perspectiveIndex == 0 ? WHITE : BLACK, actual);
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
    expect(deterministic, "immutable KBR snapshots are reentrant and exact across 1/2/4/8 threads");

    constexpr const char* Stateful = "r1k4r/8/8/8/3Q4/8/8/R1K4R w AHah - 0 1";
    constexpr const char* Neutral  = "r1k4r/8/8/8/3Q4/8/8/R1K4R b - - 37 19";
    Position              stateful;
    Position              neutral;
    StateInfo             statefulState{};
    StateInfo             neutralState{};
    if (!set_position(stateful, statefulState, Stateful, true,
                      "Atomic960 stateful KBR fixture accepted")
        || !set_position(neutral, neutralState, Neutral, true,
                         "Atomic960 neutral KBR fixture accepted"))
        return;

    bool adapterExact = true;
    for (Color perspective : {WHITE, BLACK})
    {
        KingBlastEpEmission       fromPosition{};
        KingBlastEpEmission       fromSnapshot{};
        KingBlastEpEmission       neutralEmission{};
        const CapturePairSnapshot snapshot = snapshot_from_position(stateful);
        adapterExact =
          adapterExact
          && emit_king_blast_ep(stateful, perspective, fromPosition) == CapturePairError::None
          && emit_king_blast_ep(snapshot, perspective, fromSnapshot) == CapturePairError::None
          && emit_king_blast_ep(neutral, perspective, neutralEmission) == CapturePairError::None
          && same_exact_emission(fromPosition, fromSnapshot)
          && same_exact_emission(fromPosition, neutralEmission);
    }
    expect(
      adapterExact,
      "Position adapter is exact and Atomic960 castling, clocks and STM are neutral without EP");
}

void run_tests() {
    test_constants_directions_and_indexing();
    test_direct_blast_self_blast_and_dedup();
    test_en_passant_and_malformed_metadata();
    test_illegal_candidates_promotions_and_projection_seam();
    test_orientation_order_bounds_and_errors();
    test_concurrent_reentrancy_and_position_adapter();
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
                   KingBlastEpError           error,
                   const KingBlastEpEmission& emission) {
    const JointOrientation& orientation = emission.orientation;
    std::cout << "record=king_blast_ep"
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
        const KingBlastEpFeature& feature = emission.features[index];
        std::cout << "feature=" << index << " local=" << feature.localIndex
                  << " physical=" << feature.physicalIndex
                  << " relation=" << relation_name(feature.actorRelation)
                  << " class=" << class_name(feature.relationClass)
                  << " en_passant=" << int(feature.enPassantMarker)
                  << " raw_center=" << int(feature.rawCenter)
                  << " oriented_center=" << int(feature.orientedCenter)
                  << " raw_related_king=" << int(feature.rawRelatedKing)
                  << " oriented_related_king=" << int(feature.orientedRelatedKing) << '\n';
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
        KingBlastEpEmission    emission{};
        const KingBlastEpError error = emit_king_blast_ep(position, perspective, emission);
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

    KingBlastEpEmission    emission{};
    const KingBlastEpError error = emit_king_blast_ep(snapshot, perspective, emission);
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
            std::cerr << "Atomic V3 KingBlastEP oracle tests failed: " << failures << '\n';
            return EXIT_FAILURE;
        }
        std::cout << "Atomic V3 KingBlastEP oracle tests passed\n";
        return EXIT_SUCCESS;
    }

    if (argc == 3 && (std::string(argv[1]) == "--fen" || std::string(argv[1]) == "--chess960-fen"))
        return dump_fen(argv[2], std::string(argv[1]) == "--chess960-fen");

    if (argc == 6 && std::string(argv[1]) == "--snapshot")
        return dump_snapshot(argv[2], argv[3], argv[4], argv[5]);

    std::cerr << "usage: atomic-v3-king-blast-ep-tests [--fen FEN | --chess960-fen FEN | "
                 "--snapshot PERSPECTIVE STM EP PLACEMENT]\n";
    return EXIT_FAILURE;
}
