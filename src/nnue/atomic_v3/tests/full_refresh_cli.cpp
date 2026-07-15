/*
  Atomic-Stockfish, a UCI chess playing engine derived from Stockfish
  Copyright (C) 2004-2026 The Stockfish developers (see AUTHORS file)

  Atomic-Stockfish is free software: you can redistribute it and/or modify
  it under the terms of the GNU General Public License as published by
  the Free Software Foundation, either version 3 of the License, or
  (at your option) any later version.
*/

#include <array>
#include <atomic>
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
#include "../full_refresh.h"

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

const char* error_name(FullRefreshError error) {
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

bool same_orientation(const JointOrientation& lhs, const JointOrientation& rhs) {
    return lhs.perspective == rhs.perspective && lhs.ownKing == rhs.ownKing
        && lhs.orientedOwnKing == rhs.orientedOwnKing && lhs.verticalXor == rhs.verticalXor
        && lhs.horizontalXor == rhs.horizontalXor && lhs.kingBucket == rhs.kingBucket;
}

bool same_hm(const HmEmission& lhs, const HmEmission& rhs) {
    if (!same_orientation(lhs.orientation, rhs.orientation) || lhs.size != rhs.size
        || lhs.networkBucket != rhs.networkBucket)
        return false;
    for (IndexType index = 0; index < lhs.size; ++index)
    {
        const HmFeature& a = lhs.features[index];
        const HmFeature& b = rhs.features[index];
        if (a.boardSquare != b.boardSquare || a.orientedSquare != b.orientedSquare
            || a.piece != b.piece || a.trainingPlane != b.trainingPlane
            || a.physicalPlane != b.physicalPlane || a.trainingIndex != b.trainingIndex
            || a.virtualIndex != b.virtualIndex || a.physicalIndex != b.physicalIndex
            || a.psqtRow != b.psqtRow)
            return false;
    }
    return true;
}

bool same_capture_pairs(const CapturePairEmission& lhs, const CapturePairEmission& rhs) {
    if (!same_orientation(lhs.orientation, rhs.orientation) || lhs.size != rhs.size)
        return false;
    for (IndexType index = 0; index < lhs.size; ++index)
    {
        const CapturePairFeature& a = lhs.features[index];
        const CapturePairFeature& b = rhs.features[index];
        if (a.rawFrom != b.rawFrom || a.rawCenter != b.rawCenter || a.rawCaptured != b.rawCaptured
            || a.orientedFrom != b.orientedFrom || a.orientedCenter != b.orientedCenter
            || a.orientedCaptured != b.orientedCaptured || a.actor != b.actor
            || a.captured != b.captured || a.actorRelation != b.actorRelation
            || a.targetClass != b.targetClass || a.edgeOrdinal != b.edgeOrdinal
            || a.epOrdinal != b.epOrdinal || a.localIndex != b.localIndex
            || a.physicalIndex != b.physicalIndex || a.enPassant != b.enPassant)
            return false;
    }
    return true;
}

bool same_king_blast_ep(const KingBlastEpEmission& lhs, const KingBlastEpEmission& rhs) {
    if (!same_orientation(lhs.orientation, rhs.orientation) || lhs.size != rhs.size)
        return false;
    for (IndexType index = 0; index < lhs.size; ++index)
    {
        const KingBlastEpFeature& a = lhs.features[index];
        const KingBlastEpFeature& b = rhs.features[index];
        if (a.rawCenter != b.rawCenter || a.orientedCenter != b.orientedCenter
            || a.rawRelatedKing != b.rawRelatedKing
            || a.orientedRelatedKing != b.orientedRelatedKing || a.actorRelation != b.actorRelation
            || a.relationClass != b.relationClass || a.localIndex != b.localIndex
            || a.physicalIndex != b.physicalIndex || a.enPassantMarker != b.enPassantMarker)
            return false;
    }
    return true;
}

bool same_blast_ring(const BlastRingEmission& lhs, const BlastRingEmission& rhs) {
    if (!same_orientation(lhs.orientation, rhs.orientation) || lhs.size != rhs.size)
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

bool same_full_refresh(const FullRefreshEmission& lhs, const FullRefreshEmission& rhs) {
    return same_hm(lhs.hm, rhs.hm) && same_capture_pairs(lhs.capturePairs, rhs.capturePairs)
        && same_king_blast_ep(lhs.kingBlastEp, rhs.kingBlastEp)
        && same_blast_ring(lhs.blastRing, rhs.blastRing);
}

bool empty(const FullRefreshEmission& emission) {
    return emission.hm.size == 0 && emission.capturePairs.size == 0
        && emission.kingBlastEp.size == 0 && emission.blastRing.size == 0;
}

bool standalone_parity(const CapturePairSnapshot& snapshot, Color perspective) {
    FullRefreshEmission    combined{};
    const FullRefreshError combinedError = emit_full_refresh(snapshot, perspective, combined);

    HmEmission          hm{};
    CapturePairEmission capturePairs{};
    KingBlastEpEmission kingBlastEp{};
    BlastRingEmission   blastRing{};
    const HmOracleError hmError = emit_hm_features(snapshot.board, perspective, hm);
    if (hmError != HmOracleError::None)
        return combinedError == Detail::capture_pair_error_from_hm(hmError) && empty(combined);

    const CapturePairError captureError = emit_capture_pairs(snapshot, perspective, capturePairs);
    if (captureError != CapturePairError::None)
        return combinedError == captureError && empty(combined);
    const KingBlastEpError kingError = emit_king_blast_ep(snapshot, perspective, kingBlastEp);
    const BlastRingError   ringError = emit_blast_ring(snapshot, perspective, blastRing);
    return combinedError == CapturePairError::None && kingError == CapturePairError::None
        && ringError == CapturePairError::None && same_hm(combined.hm, hm)
        && same_capture_pairs(combined.capturePairs, capturePairs)
        && same_king_blast_ep(combined.kingBlastEp, kingBlastEp)
        && same_blast_ring(combined.blastRing, blastRing);
}

void test_contract_and_parity() {
    expect(FullRefreshMaximumActiveFeatures == 547
             && BlastRingPhysicalOffset + BlastRingPhysicalDimensions == 75084,
           "aggregate dimensions and active bound are frozen");

    const std::array<CapturePairSnapshot, 5> snapshots{
      make_snapshot({{SQ_C1, W_KING},
                     {SQ_C8, B_KING},
                     {SQ_D4, W_QUEEN},
                     {SQ_D5, B_ROOK},
                     {SQ_E5, B_KNIGHT},
                     {SQ_E4, W_PAWN},
                     {SQ_C5, W_BISHOP},
                     {SQ_C4, B_PAWN}}),
      make_snapshot({{SQ_A1, W_KING},
                     {SQ_H8, B_KING},
                     {SQ_E5, W_PAWN},
                     {SQ_D5, B_PAWN},
                     {SQ_C5, W_ROOK},
                     {SQ_D7, B_BISHOP}},
                    WHITE, SQ_D6),
      make_snapshot(
        {{SQ_A1, W_KING}, {SQ_H8, B_KING}, {SQ_E5, W_PAWN}, {SQ_C5, W_ROOK}, {SQ_D7, B_BISHOP}},
        WHITE, SQ_D6),
      make_snapshot(
        {{SQ_D4, W_KING}, {SQ_E4, B_KING}, {SQ_D2, W_ROOK}, {SQ_E2, B_QUEEN}, {SQ_C3, W_KNIGHT}}),
      make_snapshot({{SQ_A1, W_KING},
                     {SQ_H7, B_KING},
                     {SQ_G7, W_PAWN},
                     {SQ_H8, B_ROOK},
                     {SQ_F8, B_BISHOP},
                     {SQ_G6, W_KNIGHT}})};

    bool parity = true;
    for (const CapturePairSnapshot& snapshot : snapshots)
        for (Color perspective : {WHITE, BLACK})
            parity = parity && standalone_parity(snapshot, perspective);
    expect(parity, "combined slices exactly match all standalone public oracles");

    FullRefreshEmission    emission{};
    const FullRefreshError error = emit_full_refresh(snapshots[0], WHITE, emission);
    const bool             orientations =
      error == CapturePairError::None
      && same_orientation(emission.hm.orientation, emission.capturePairs.orientation)
      && same_orientation(emission.hm.orientation, emission.kingBlastEp.orientation)
      && same_orientation(emission.hm.orientation, emission.blastRing.orientation);
    expect(orientations, "all slices share one exact perspective orientation");
    expect(error == CapturePairError::None
             && emission.active_feature_count() <= FullRefreshMaximumActiveFeatures,
           "aggregate active count fits the 547-row proof");
}

void test_trusted_seam_and_errors() {
    const CapturePairSnapshot snapshot =
      make_snapshot({{SQ_C1, W_KING}, {SQ_C8, B_KING}, {SQ_D4, W_QUEEN}, {SQ_D5, B_ROOK}});
    HmEmission          hm{};
    CapturePairEmission publicEmission{};
    CapturePairEmission seamEmission{};
    const bool          seam = emit_hm_features(snapshot.board, WHITE, hm) == HmOracleError::None
                   && emit_capture_pairs(snapshot, WHITE, publicEmission) == CapturePairError::None
                   && Detail::emit_capture_pairs_from_hm(snapshot, WHITE, hm, seamEmission)
                        == CapturePairError::None
                   && same_capture_pairs(publicEmission, seamEmission);
    expect(seam, "trusted HM-to-CapturePair seam preserves public behavior");

    HmEmission tampered = hm;
    tampered.orientation.horizontalXor ^= 7;
    expect(Detail::emit_capture_pairs_from_hm(snapshot, WHITE, tampered, seamEmission)
               == CapturePairError::NonCanonicalOrder
             && seamEmission.size == 0,
           "trusted HM seam rejects a corrupted orientation without partial output");

    CapturePairSnapshot invalidPiece = snapshot;
    invalidPiece.board[SQ_D4]        = Piece(7);
    expect(Detail::emit_capture_pairs_from_hm(invalidPiece, WHITE, hm, seamEmission)
               == CapturePairError::NonCanonicalOrder
             && seamEmission.size == 0,
           "trusted HM seam validates piece codes before color and type access");

    FullRefreshEmission filled{};
    bool prefilled = emit_full_refresh(snapshot, WHITE, filled) == CapturePairError::None;
    CapturePairSnapshot invalidSide = snapshot;
    invalidSide.sideToMove          = Color(COLOR_NB);
    expect(prefilled
             && emit_full_refresh(invalidSide, WHITE, filled) == CapturePairError::InvalidSideToMove
             && empty(filled),
           "invalid side-to-move clears the whole bundle");

    prefilled = emit_full_refresh(snapshot, WHITE, filled) == CapturePairError::None;
    CapturePairSnapshot missingKing = snapshot;
    missingKing.board[SQ_C8]        = NO_PIECE;
    expect(prefilled
             && emit_full_refresh(missingKing, WHITE, filled) == CapturePairError::MissingBlackKing
             && empty(filled),
           "king-absent terminal domain maps the HM error and clears the bundle");

    prefilled = emit_full_refresh(snapshot, WHITE, filled) == CapturePairError::None;
    expect(prefilled
             && emit_full_refresh(snapshot, Color(COLOR_NB), filled)
                  == CapturePairError::InvalidPerspective
             && empty(filled),
           "invalid perspective maps losslessly and leaves no partial slice");
}

void test_position_adapter_and_threads() {
    Position   position;
    StateInfo  state{};
    const auto setError = position.set("r1k4r/8/8/3r4/3Qb3/8/8/R1K4R w AHah - 0 1", true, &state);
    bool       adapter  = !setError.has_value();
    if (adapter)
        for (Color perspective : {WHITE, BLACK})
        {
            FullRefreshEmission fromPosition{};
            FullRefreshEmission fromSnapshot{};
            adapter =
              adapter
              && emit_full_refresh(position, perspective, fromPosition) == CapturePairError::None
              && emit_full_refresh(make_capture_pair_snapshot(position), perspective, fromSnapshot)
                   == CapturePairError::None
              && same_full_refresh(fromPosition, fromSnapshot);
        }
    expect(adapter, "Atomic960 Position adapter takes one equivalent immutable snapshot");

    const CapturePairSnapshot                 snapshot = make_snapshot({{SQ_A1, W_KING},
                                                                        {SQ_H8, B_KING},
                                                                        {SQ_E5, W_PAWN},
                                                                        {SQ_D5, B_PAWN},
                                                                        {SQ_C5, W_ROOK},
                                                                        {SQ_D7, B_BISHOP}},
                                                                       WHITE, SQ_D6);
    std::array<FullRefreshEmission, COLOR_NB> baseline{};
    bool                                      baselineOk = true;
    for (Color perspective : {WHITE, BLACK})
        baselineOk = baselineOk
                  && emit_full_refresh(snapshot, perspective, baseline[perspective])
                       == CapturePairError::None;

    bool allThreads = baselineOk;
    for (unsigned threadCount : {1U, 2U, 4U, 8U})
    {
        std::atomic<bool>        matches{true};
        std::vector<std::thread> threads;
        for (unsigned threadIndex = 0; threadIndex < threadCount; ++threadIndex)
            threads.emplace_back([&]() {
                for (unsigned iteration = 0; iteration < 64 && matches.load(); ++iteration)
                    for (Color perspective : {WHITE, BLACK})
                    {
                        FullRefreshEmission actual{};
                        if (emit_full_refresh(snapshot, perspective, actual)
                              != CapturePairError::None
                            || !same_full_refresh(actual, baseline[perspective]))
                            matches.store(false);
                    }
            });
        for (std::thread& thread : threads)
            thread.join();
        allThreads = allThreads && matches.load();
    }
    expect(allThreads, "full refresh is bit-exact with 1/2/4/8 concurrent readers");
}

void run_tests() {
    test_contract_and_parity();
    test_trusted_seam_and_errors();
    test_position_adapter_and_threads();
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

void dump_slice(std::string_view slice, const HmEmission& emission) {
    for (IndexType index = 0; index < emission.size; ++index)
        std::cout << "slice=" << slice << " feature=" << index
                  << " local=" << emission.features[index].physicalIndex
                  << " physical=" << emission.features[index].physicalIndex << '\n';
}

template<typename Emission>
void dump_relation_slice(std::string_view slice, const Emission& emission) {
    for (IndexType index = 0; index < emission.size; ++index)
        std::cout << "slice=" << slice << " feature=" << index
                  << " local=" << emission.features[index].localIndex
                  << " physical=" << emission.features[index].physicalIndex << '\n';
}

void dump_emission(const CapturePairSnapshot& snapshot,
                   Color                      perspective,
                   FullRefreshError           error,
                   const FullRefreshEmission& emission) {
    const JointOrientation& orientation = emission.hm.orientation;
    std::cout << "record=full_refresh"
              << " perspective=" << color_name(perspective)
              << " side_to_move=" << color_name(snapshot.sideToMove)
              << " ep_square=" << int(snapshot.epSquare) << " error=" << error_name(error)
              << " error_code=" << int(error) << " vertical_xor=" << int(orientation.verticalXor)
              << " horizontal_xor=" << int(orientation.horizontalXor)
              << " own_king=" << int(orientation.ownKing)
              << " oriented_own_king=" << int(orientation.orientedOwnKing)
              << " king_bucket=" << orientation.kingBucket
              << " network_bucket=" << emission.hm.networkBucket << " hm=" << emission.hm.size
              << " capture_pair=" << emission.capturePairs.size
              << " king_blast_ep=" << emission.kingBlastEp.size
              << " blast_ring=" << emission.blastRing.size
              << " total=" << emission.active_feature_count() << '\n';
    dump_slice("hm", emission.hm);
    dump_relation_slice("capture_pair", emission.capturePairs);
    dump_relation_slice("king_blast_ep", emission.kingBlastEp);
    dump_relation_slice("blast_ring", emission.blastRing);
}

int dump_fen(const std::string& fen, bool chess960) {
    Position  position;
    StateInfo state{};
    if (const auto error = position.set(fen, chess960, &state))
    {
        std::cerr << error->what() << '\n';
        return EXIT_FAILURE;
    }
    const CapturePairSnapshot snapshot = make_capture_pair_snapshot(position);
    for (Color perspective : {WHITE, BLACK})
    {
        FullRefreshEmission    emission{};
        const FullRefreshError error = emit_full_refresh(position, perspective, emission);
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

    FullRefreshEmission    emission{};
    const FullRefreshError error = emit_full_refresh(snapshot, perspective, emission);
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
            std::cerr << "Atomic V3 full-refresh tests failed: " << failures << '\n';
            return EXIT_FAILURE;
        }
        std::cout << "Atomic V3 full-refresh tests passed\n";
        return EXIT_SUCCESS;
    }

    if (argc == 3 && (std::string(argv[1]) == "--fen" || std::string(argv[1]) == "--chess960-fen"))
        return dump_fen(argv[2], std::string(argv[1]) == "--chess960-fen");

    if (argc == 6 && std::string(argv[1]) == "--snapshot")
        return dump_snapshot(argv[2], argv[3], argv[4], argv[5]);

    std::cerr << "usage: atomic-v3-full-refresh-tests [--fen FEN | --chess960-fen FEN | "
                 "--snapshot PERSPECTIVE STM EP PLACEMENT]\n";
    return EXIT_FAILURE;
}
