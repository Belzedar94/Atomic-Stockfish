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
#include "../blast_ring.h"

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

const char* collateral_relation_name(BlastRingCollateralRelation relation) {
    return relation == BlastRingCollateralRelation::Own ? "own"
         : relation == BlastRingCollateralRelation::Opp ? "opp"
                                                        : "invalid";
}

const char* direction_name(BlastRingDirection direction) {
    switch (direction)
    {
    case BlastRingDirection::N :
        return "n";
    case BlastRingDirection::NE :
        return "ne";
    case BlastRingDirection::E :
        return "e";
    case BlastRingDirection::SE :
        return "se";
    case BlastRingDirection::S :
        return "s";
    case BlastRingDirection::SW :
        return "sw";
    case BlastRingDirection::W :
        return "w";
    case BlastRingDirection::NW :
        return "nw";
    }
    return "invalid";
}

const char* class_name(BlastRingCollateralClass collateralClass) {
    switch (collateralClass)
    {
    case BlastRingCollateralClass::Knight :
        return "knight";
    case BlastRingCollateralClass::Bishop :
        return "bishop";
    case BlastRingCollateralClass::Rook :
        return "rook";
    case BlastRingCollateralClass::Queen :
        return "queen";
    case BlastRingCollateralClass::AdjacentPawnSurvives :
        return "adjacent_pawn_survives";
    }
    return "invalid";
}

const char* error_name(BlastRingError error) {
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

const BlastRingFeature* find_feature(const BlastRingEmission& emission,
                                     Square                   rawCenter,
                                     Square                   rawCollateral,
                                     CapturePairActorRelation actorRelation) {
    for (IndexType index = 0; index < emission.size; ++index)
    {
        const BlastRingFeature& feature = emission.features[index];
        if (feature.rawCenter == rawCenter && feature.rawCollateral == rawCollateral
            && feature.actorRelation == actorRelation)
            return &feature;
    }
    return nullptr;
}

bool same_emission(const BlastRingEmission& lhs, const BlastRingEmission& rhs) {
    if (lhs.size != rhs.size || lhs.orientation.perspective != rhs.orientation.perspective
        || lhs.orientation.ownKing != rhs.orientation.ownKing
        || lhs.orientation.orientedOwnKing != rhs.orientation.orientedOwnKing
        || lhs.orientation.verticalXor != rhs.orientation.verticalXor
        || lhs.orientation.horizontalXor != rhs.orientation.horizontalXor
        || lhs.orientation.kingBucket != rhs.orientation.kingBucket)
        return false;
    for (IndexType index = 0; index < lhs.size; ++index)
    {
        const BlastRingFeature& a = lhs.features[index];
        const BlastRingFeature& b = rhs.features[index];
        if (a.rawCenter != b.rawCenter || a.orientedCenter != b.orientedCenter
            || a.rawCollateral != b.rawCollateral || a.orientedCollateral != b.orientedCollateral
            || a.collateral != b.collateral || a.actorRelation != b.actorRelation
            || a.collateralRelation != b.collateralRelation || a.direction != b.direction
            || a.collateralClass != b.collateralClass || a.localIndex != b.localIndex
            || a.physicalIndex != b.physicalIndex
            || a.adjacentPawnSurvives != b.adjacentPawnSurvives)
            return false;
    }
    return true;
}

void test_constants_index_and_directions() {
    expect(BlastRingCenterDimensions == 64 && BlastRingActorRelations == 2
             && BlastRingCollateralRelations == 2 && BlastRingDirections == 8
             && BlastRingCollateralClasses == 5 && BlastRingPhysicalDimensions == 10240
             && BlastRingPhysicalOffset == 64844
             && BlastRingPhysicalOffset + BlastRingPhysicalDimensions == 75084
             && BlastRingMaximumActiveFeatures == 240,
           "BlastRing constants match the frozen 64x2x2x8x5 contract");

    std::array<bool, BlastRingPhysicalDimensions> seen{};
    bool                                          exact = true;
    for (int center = 0; center < SQUARE_NB; ++center)
        for (IndexType actor = 0; actor < BlastRingActorRelations; ++actor)
            for (IndexType collateral = 0; collateral < BlastRingCollateralRelations; ++collateral)
                for (IndexType direction = 0; direction < BlastRingDirections; ++direction)
                    for (IndexType collateralClass = 0;
                         collateralClass < BlastRingCollateralClasses; ++collateralClass)
                    {
                        IndexType       local = BlastRingPhysicalDimensions;
                        const IndexType expected =
                          ((((IndexType(center) * 2 + actor) * 2 + collateral) * 8 + direction) * 5
                           + collateralClass);
                        exact = exact
                             && blast_ring_index(Square(center), CapturePairActorRelation(actor),
                                                 BlastRingCollateralRelation(collateral),
                                                 BlastRingDirection(direction),
                                                 BlastRingCollateralClass(collateralClass), local)
                             && local == expected && local < seen.size() && !seen[local];
                        if (local < seen.size())
                            seen[local] = true;
                    }
    exact = exact && std::all_of(seen.begin(), seen.end(), [](bool value) { return value; });
    expect(exact, "all 10,240 BlastRing rows are dense, unique and formula-exact");

    constexpr std::array<Square, 8> Adjacent{SQ_D5, SQ_E5, SQ_E4, SQ_E3,
                                             SQ_D3, SQ_C3, SQ_C4, SQ_C5};
    bool                            directions = true;
    for (IndexType index = 0; index < Adjacent.size(); ++index)
    {
        Square             square = SQ_NONE;
        BlastRingDirection inverse{};
        directions = directions
                  && blast_ring_directional_square(SQ_D4, BlastRingDirection(index), square)
                  && square == Adjacent[index] && blast_ring_direction(SQ_D4, square, inverse)
                  && inverse == BlastRingDirection(index);
    }
    Square offboard = SQ_NONE;
    directions      = directions
              && !blast_ring_directional_square(SQ_A1, BlastRingDirection::W, offboard)
              && !blast_ring_directional_square(SQ_H8, BlastRingDirection::NE, offboard);
    expect(directions, "joint-frame directions are exact and never wrap board edges");
}

void test_classes_relations_origins_and_kings() {
    constexpr std::array<Piece, 5> Collaterals{B_KNIGHT, B_BISHOP, B_ROOK, B_QUEEN, B_PAWN};
    bool                           classes = true;
    for (IndexType index = 0; index < Collaterals.size(); ++index)
    {
        const CapturePairSnapshot snapshot = make_snapshot({{SQ_H1, W_KING},
                                                            {SQ_H8, B_KING},
                                                            {SQ_B3, W_KNIGHT},
                                                            {SQ_D4, B_PAWN},
                                                            {SQ_D5, Collaterals[index]}});
        BlastRingEmission         emission{};
        const BlastRingFeature*   feature = nullptr;
        classes = classes && emit_blast_ring(snapshot, WHITE, emission) == CapturePairError::None
               && (feature = find_feature(emission, SQ_D4, SQ_D5, CapturePairActorRelation::Own))
               && feature->collateralRelation == BlastRingCollateralRelation::Opp
               && feature->direction == BlastRingDirection::N
               && feature->collateralClass == BlastRingCollateralClass(index)
               && feature->adjacentPawnSurvives
                    == (index == IndexType(BlastRingCollateralClass::AdjacentPawnSurvives));
    }
    expect(classes, "all five collateral classes and their survival bit are exact");

    const CapturePairSnapshot sole = make_snapshot(
      {{SQ_H1, W_KING}, {SQ_H8, B_KING}, {SQ_D3, W_ROOK}, {SQ_D4, B_PAWN}, {SQ_E4, B_KNIGHT}});
    BlastRingEmission soleEmission{};
    bool              origins = emit_blast_ring(sole, WHITE, soleEmission) == CapturePairError::None
                && !find_feature(soleEmission, SQ_D4, SQ_D3, CapturePairActorRelation::Own)
                && find_feature(soleEmission, SQ_D4, SQ_E4, CapturePairActorRelation::Own);

    const CapturePairSnapshot multiple = make_snapshot(
      {{SQ_H1, W_KING}, {SQ_H8, B_KING}, {SQ_D3, W_ROOK}, {SQ_C4, W_ROOK}, {SQ_D4, B_PAWN}});
    BlastRingEmission multipleEmission{};
    origins = origins
           && emit_blast_ring(multiple, WHITE, multipleEmission) == CapturePairError::None
           && find_feature(multipleEmission, SQ_D4, SQ_D3, CapturePairActorRelation::Own)
           && find_feature(multipleEmission, SQ_D4, SQ_C4, CapturePairActorRelation::Own);
    expect(origins, "sole origins are excluded while multi-route origins are retained");

    const CapturePairSnapshot kings = make_snapshot(
      {{SQ_E4, W_KING}, {SQ_D5, B_KING}, {SQ_B3, W_KNIGHT}, {SQ_D4, B_PAWN}, {SQ_E5, B_BISHOP}});
    BlastRingEmission kingEmission{};
    const bool        kingSeparated =
      emit_blast_ring(kings, WHITE, kingEmission) == CapturePairError::None
      && !find_feature(kingEmission, SQ_D4, SQ_E4, CapturePairActorRelation::Own)
      && !find_feature(kingEmission, SQ_D4, SQ_D5, CapturePairActorRelation::Own)
      && find_feature(kingEmission, SQ_D4, SQ_E5, CapturePairActorRelation::Own);
    expect(kingSeparated, "kings stay in KingBlastEP while non-king collateral remains");
}

void test_en_passant_malformed_and_seam() {
    const CapturePairSnapshot ep      = make_snapshot({{SQ_H1, W_KING},
                                                       {SQ_A8, B_KING},
                                                       {SQ_D5, W_PAWN},
                                                       {SQ_F5, W_PAWN},
                                                       {SQ_E5, B_PAWN},
                                                       {SQ_E7, B_KNIGHT}},
                                                      WHITE, SQ_E6);
    bool                      epExact = true;
    for (Color perspective : {WHITE, BLACK})
    {
        BlastRingEmission emission{};
        const auto        relation =
          perspective == WHITE ? CapturePairActorRelation::Own : CapturePairActorRelation::Opp;
        epExact = epExact && emit_blast_ring(ep, perspective, emission) == CapturePairError::None
               && find_feature(emission, SQ_E6, SQ_D5, relation)
               && find_feature(emission, SQ_E6, SQ_F5, relation)
               && !find_feature(emission, SQ_E6, SQ_E5, relation)
               && find_feature(emission, SQ_E6, SQ_E7, relation);
    }
    expect(epExact, "two EP origins survive as alternatives while the off-center pawn is excluded");

    CapturePairSnapshot malformed = ep;
    malformed.board[SQ_E5]        = NO_PIECE;
    CapturePairSnapshot noEp      = malformed;
    noEp.epSquare                 = SQ_NONE;
    BlastRingEmission malformedEmission{};
    BlastRingEmission noEpEmission{};
    expect(emit_blast_ring(malformed, WHITE, malformedEmission) == CapturePairError::None
             && emit_blast_ring(noEp, WHITE, noEpEmission) == CapturePairError::None
             && same_emission(malformedEmission, noEpEmission),
           "malformed EP omits only the optional candidate and preserves normal projection");

    const CapturePairSnapshot seamSnapshot = make_snapshot({{SQ_H1, W_KING},
                                                            {SQ_H8, B_KING},
                                                            {SQ_D3, W_ROOK},
                                                            {SQ_C4, W_ROOK},
                                                            {SQ_D4, B_PAWN},
                                                            {SQ_E4, B_BISHOP}});
    CapturePairEmission       capturePairs{};
    BlastRingEmission         publicEmission{};
    BlastRingEmission         projected{};
    bool seam = emit_capture_pairs(seamSnapshot, WHITE, capturePairs) == CapturePairError::None
             && emit_blast_ring(seamSnapshot, WHITE, publicEmission) == CapturePairError::None
             && Detail::project_blast_ring(seamSnapshot, WHITE, capturePairs, projected)
                  == CapturePairError::None
             && same_emission(publicEmission, projected);
    CapturePairEmission tampered = capturePairs;
    if (tampered.size)
        ++tampered.features[0].localIndex;
    projected.size = 1;
    seam           = seam && tampered.size
        && Detail::project_blast_ring(seamSnapshot, WHITE, tampered, projected)
             == CapturePairError::NonCanonicalOrder
        && projected.size == 0;
    CapturePairSnapshot invalid = seamSnapshot;
    invalid.board[SQ_A1]        = Piece(7);
    seam                        = seam
        && Detail::project_blast_ring(invalid, WHITE, capturePairs, projected)
             == CapturePairError::NonCanonicalOrder
        && projected.size == 0;
    expect(seam, "trusted CP seam is exact and rejects corrupt indices and piece holes");
}

void test_errors_order_position_and_threads() {
    CapturePairSnapshot missingBlack{};
    missingBlack.sideToMove   = WHITE;
    missingBlack.board[SQ_E1] = W_KING;
    BlastRingEmission errorEmission{};
    bool              errors =
      emit_blast_ring(missingBlack, WHITE, errorEmission) == CapturePairError::MissingBlackKing
      && errorEmission.size == 0;
    CapturePairSnapshot invalidSide = make_snapshot({{SQ_E1, W_KING}, {SQ_E8, B_KING}});
    invalidSide.sideToMove          = Color(COLOR_NB);
    errors =
      errors
      && emit_blast_ring(invalidSide, WHITE, errorEmission) == CapturePairError::InvalidSideToMove
      && errorEmission.size == 0;
    expect(errors, "CapturePair errors propagate without a partial BlastRing output");

    const CapturePairSnapshot               snapshot = make_snapshot({{SQ_H1, W_KING},
                                                                      {SQ_A8, B_KING},
                                                                      {SQ_D5, W_PAWN},
                                                                      {SQ_F5, W_PAWN},
                                                                      {SQ_E5, B_PAWN},
                                                                      {SQ_E7, B_KNIGHT}},
                                                                     WHITE, SQ_E6);
    std::array<BlastRingEmission, COLOR_NB> baselines{};
    bool                                    canonical = true;
    for (Color perspective : {WHITE, BLACK})
    {
        canonical = canonical
                 && emit_blast_ring(snapshot, perspective, baselines[perspective])
                      == CapturePairError::None
                 && baselines[perspective].size <= BlastRingMaximumActiveFeatures;
        for (IndexType index = 0; index < baselines[perspective].size; ++index)
        {
            const BlastRingFeature& feature = baselines[perspective].features[index];
            canonical =
              canonical && feature.localIndex < BlastRingPhysicalDimensions
              && feature.physicalIndex == BlastRingPhysicalOffset + feature.localIndex
              && (!index
                  || baselines[perspective].features[index - 1].localIndex < feature.localIndex);
        }
    }
    expect(canonical, "emissions are sorted, unique, bounded and physically offset exactly");

    bool concurrent = true;
    for (unsigned threadCount : {1U, 2U, 4U, 8U})
    {
        std::vector<std::thread>  threads;
        std::vector<unsigned int> ok(threadCount, 1U);
        for (unsigned threadIndex = 0; threadIndex < threadCount; ++threadIndex)
            threads.emplace_back([&, threadIndex]() {
                for (unsigned iteration = 0; iteration < 64; ++iteration)
                {
                    const Color       perspective = Color((threadIndex + iteration) & 1U);
                    BlastRingEmission actual{};
                    ok[threadIndex] =
                      ok[threadIndex]
                      && emit_blast_ring(snapshot, perspective, actual) == CapturePairError::None
                      && same_emission(actual, baselines[perspective]);
                }
            });
        for (std::thread& thread : threads)
            thread.join();
        concurrent =
          concurrent && std::all_of(ok.begin(), ok.end(), [](unsigned int value) { return value; });
    }
    expect(concurrent, "immutable snapshots are exact and reentrant across 1/2/4/8 threads");

    Position   position;
    StateInfo  state{};
    const auto setError = position.set("r1k4r/8/8/3r4/3Qb3/8/8/R1K4R w AHah - 0 1", true, &state);
    bool       adapter  = !setError;
    if (!setError)
        for (Color perspective : {WHITE, BLACK})
        {
            BlastRingEmission fromPosition{};
            BlastRingEmission fromSnapshot{};
            adapter = adapter
                   && emit_blast_ring(position, perspective, fromPosition) == CapturePairError::None
                   && emit_blast_ring(snapshot_from_position(position), perspective, fromSnapshot)
                        == CapturePairError::None
                   && same_emission(fromPosition, fromSnapshot);
        }
    expect(adapter, "Position adapter is exact and Atomic960 metadata is projection-neutral");
}

void run_tests() {
    test_constants_index_and_directions();
    test_classes_relations_origins_and_kings();
    test_en_passant_malformed_and_seam();
    test_errors_order_position_and_threads();
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
                   BlastRingError             error,
                   const BlastRingEmission&   emission) {
    const JointOrientation& orientation = emission.orientation;
    std::cout << "record=blast_ring"
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
        const BlastRingFeature& feature = emission.features[index];
        std::cout << "feature=" << index << " local=" << feature.localIndex
                  << " physical=" << feature.physicalIndex
                  << " actor_relation=" << relation_name(feature.actorRelation)
                  << " collateral_relation=" << collateral_relation_name(feature.collateralRelation)
                  << " class=" << class_name(feature.collateralClass)
                  << " offset=" << direction_name(feature.direction)
                  << " pawn_survives=" << int(feature.adjacentPawnSurvives)
                  << " raw_center=" << int(feature.rawCenter)
                  << " oriented_center=" << int(feature.orientedCenter)
                  << " raw_collateral=" << int(feature.rawCollateral)
                  << " oriented_collateral=" << int(feature.orientedCollateral) << '\n';
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
        BlastRingEmission    emission{};
        const BlastRingError error = emit_blast_ring(position, perspective, emission);
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
        std::cerr << "snapshot requires PERSPECTIVE STM EP PLACEMENT as white|black, "
                     "white|black, -|a1..h8 and an eight-rank FEN placement\n";
        return EXIT_FAILURE;
    }

    BlastRingEmission    emission{};
    const BlastRingError error = emit_blast_ring(snapshot, perspective, emission);
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
            std::cerr << "Atomic V3 BlastRing oracle tests failed: " << failures << '\n';
            return EXIT_FAILURE;
        }
        std::cout << "Atomic V3 BlastRing oracle tests passed\n";
        return EXIT_SUCCESS;
    }

    if (argc == 3 && (std::string(argv[1]) == "--fen" || std::string(argv[1]) == "--chess960-fen"))
        return dump_fen(argv[2], std::string(argv[1]) == "--chess960-fen");

    if (argc == 6 && std::string(argv[1]) == "--snapshot")
        return dump_snapshot(argv[2], argv[3], argv[4], argv[5]);

    std::cerr << "usage: atomic-v3-blast-ring-tests [--fen FEN | --chess960-fen FEN | "
                 "--snapshot PERSPECTIVE STM EP PLACEMENT]\n";
    return EXIT_FAILURE;
}
