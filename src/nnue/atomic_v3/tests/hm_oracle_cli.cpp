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
#include <charconv>
#include <cstdint>
#include <cstdlib>
#include <iostream>
#include <string>
#include <string_view>
#include <tuple>

#include "../../../attacks.h"
#include "../../../bitboard.h"
#include "../../../position.h"
#include "../hm_oracle.h"

namespace Stockfish::Eval::NNUE::AtomicV3 {
namespace {

int failures = 0;

constexpr std::array<IndexType, 10> ExportSentinelOutputs{0,    1023, 1024, 1025, 1026,
                                                          1027, 1028, 1029, 1030, 1031};

void expect(bool condition, const std::string& label) {
    if (condition)
        std::cout << "PASS " << label << '\n';
    else
    {
        std::cerr << "FAIL " << label << '\n';
        ++failures;
    }
}

bool parse_index_argument(std::string_view text, IndexType upperBound, IndexType& result) {
    if (text.empty() || upperBound == 0 || (text.size() > 1 && text.front() == '0'))
        return false;
    for (const char character : text)
        if (character < '0' || character > '9')
            return false;

    IndexType parsed        = 0;
    const auto [end, error] = std::from_chars(text.data(), text.data() + text.size(), parsed);
    if (error != std::errc{} || end != text.data() + text.size() || parsed >= upperBound)
        return false;
    result = parsed;
    return true;
}

bool synthetic_coalesced_value(const HmExportSource& source, std::int32_t& result) {
    if (source.bucketWeightRow >= HmTrainingDimensions
        || source.virtualWeightRow >= HmVirtualDimensions
        || source.sourceOutput >= HmCoalescedOutputs)
        return false;

    // This deterministic test-only tensor deliberately duplicates the fixture
    // formula. Production supplies every address through hm_export_source().
    const std::int64_t bucket =
      (std::int64_t(source.bucketWeightRow) * 73 + std::int64_t(source.sourceOutput) * 19 + 11)
        % 24001
      - 12000;
    const std::int64_t virtualWeight =
      (std::int64_t(source.virtualWeightRow) * 43 + std::int64_t(source.sourceOutput) * 29 + 5)
        % 8001
      - 4000;
    const std::int64_t coalesced = bucket + virtualWeight;
    if (bucket < -12000 || bucket > 12000 || virtualWeight < -4000 || virtualWeight > 4000
        || coalesced < -16000 || coalesced > 16000)
        return false;
    result = std::int32_t(coalesced);
    return true;
}

template<typename Values>
bool export_sentinels_match(const JointOrientation& orientation,
                            HmPhysicalPlane         physicalPlane,
                            Square                  orientedSquare,
                            const Values&           expected) {
    static_assert(std::tuple_size_v<Values> == ExportSentinelOutputs.size());
    for (usize index = 0; index < ExportSentinelOutputs.size(); ++index)
    {
        HmExportSource source{};
        std::int32_t   value = 0;
        if (!hm_export_source(orientation, physicalPlane, orientedSquare,
                              ExportSentinelOutputs[index], source)
            || !synthetic_coalesced_value(source, value) || value != expected[index])
            return false;
    }
    return true;
}

bool set_position(Position&          position,
                  StateInfo&         state,
                  const std::string& fen,
                  bool               chess960,
                  const std::string& label) {
    const auto error = position.set(fen, chess960, &state);
    expect(!error, label);
    if (error)
        std::cerr << "FEN error: " << error->what() << '\n';
    return !error;
}

const HmFeature* find_feature(const HmEmission& emission, Square boardSquare) {
    for (IndexType i = 0; i < emission.size; ++i)
        if (emission.features[i].boardSquare == boardSquare)
            return &emission.features[i];
    return nullptr;
}

using Projection = std::tuple<IndexType, IndexType, IndexType, IndexType>;

std::array<Projection, HmMaximumActiveDimensions> sorted_projection(const HmEmission& emission) {
    std::array<Projection, HmMaximumActiveDimensions> projection{};
    for (IndexType i = 0; i < emission.size; ++i)
    {
        const HmFeature& feature = emission.features[i];
        projection[i] = {feature.physicalIndex, feature.trainingIndex, feature.virtualIndex,
                         IndexType(feature.piece)};
    }
    std::sort(projection.begin(), projection.begin() + emission.size);
    return projection;
}

bool same_projection(const HmEmission& lhs, const HmEmission& rhs) {
    if (lhs.size != rhs.size || lhs.networkBucket != rhs.networkBucket
        || lhs.orientation.orientedOwnKing != rhs.orientation.orientedOwnKing
        || lhs.orientation.kingBucket != rhs.orientation.kingBucket)
        return false;

    const auto lhsProjection = sorted_projection(lhs);
    const auto rhsProjection = sorted_projection(rhs);
    return std::equal(lhsProjection.begin(), lhsProjection.begin() + lhs.size,
                      rhsProjection.begin());
}

bool same_emission_indices(const HmEmission& lhs, const HmEmission& rhs) {
    if (lhs.size != rhs.size || lhs.networkBucket != rhs.networkBucket
        || lhs.orientation.kingBucket != rhs.orientation.kingBucket)
        return false;

    for (IndexType i = 0; i < lhs.size; ++i)
    {
        const HmFeature& a = lhs.features[i];
        const HmFeature& b = rhs.features[i];
        if (a.boardSquare != b.boardSquare || a.orientedSquare != b.orientedSquare
            || a.piece != b.piece || a.trainingPlane != b.trainingPlane
            || a.physicalPlane != b.physicalPlane || a.trainingIndex != b.trainingIndex
            || a.virtualIndex != b.virtualIndex || a.physicalIndex != b.physicalIndex
            || a.psqtRow != b.psqtRow)
            return false;
    }
    return true;
}

void test_orientation() {
    JointOrientation orientation{};
    expect(make_joint_orientation(WHITE, SQ_C2, orientation),
           "WHITE orientation accepts present king");
    expect(orientation.verticalXor == 0 && orientation.horizontalXor == 7
             && orientation.orientedOwnKing == SQ_F2 && orientation.kingBucket == 26,
           "WHITE a-d branch mirrors C2 to F2 bucket 26");
    expect(orientation.orient(SQ_A1) == SQ_H1,
           "WHITE a-d branch applies its joint xor to another square");

    expect(make_joint_orientation(BLACK, SQ_B7, orientation),
           "BLACK orientation accepts present king");
    expect(orientation.verticalXor == 56 && orientation.horizontalXor == 7
             && orientation.orientedOwnKing == SQ_G2 && orientation.kingBucket == 25,
           "BLACK vertical then horizontal branch maps B7 to G2 bucket 25");
    expect(orientation.orient(SQ_A1) == SQ_H8,
           "BLACK branch shares vertical and horizontal xor with every square");

    expect(make_joint_orientation(WHITE, SQ_G1, orientation) && orientation.horizontalXor == 0
             && orientation.orientedOwnKing == SQ_G1 && orientation.kingBucket == 29,
           "e-h king branch does not mirror");
    expect(!make_joint_orientation(WHITE, SQ_NONE, orientation),
           "orientation rejects an exploded own king");
}

void test_orientation_exhaustive() {
    bool exact = true;
    for (Color perspective : {WHITE, BLACK})
    {
        std::array<IndexType, HmKingBuckets> bucketCounts{};
        IndexType                            mirroredBranches = 0;
        for (int squareIndex = 0; squareIndex < SQUARE_NB; ++squareIndex)
        {
            JointOrientation orientation{};
            JointOrientation fileMirror{};
            exact = exact && make_joint_orientation(perspective, Square(squareIndex), orientation)
                 && make_joint_orientation(perspective, Square(squareIndex ^ 7), fileMirror)
                 && is_canonical_joint_orientation(orientation)
                 && file_of(orientation.orientedOwnKing) >= FILE_E
                 && orientation.orient(Square(squareIndex)) == orientation.orientedOwnKing
                 && orientation.orientedOwnKing == fileMirror.orientedOwnKing
                 && orientation.kingBucket == fileMirror.kingBucket
                 && orientation.horizontalXor != fileMirror.horizontalXor
                 && orientation.verticalXor == (perspective == BLACK ? 56 : 0)
                 && orientation.kingBucket < HmKingBuckets;
            if (orientation.kingBucket < HmKingBuckets)
                ++bucketCounts[orientation.kingBucket];
            mirroredBranches += orientation.horizontalXor == 7;
        }
        exact = exact && mirroredBranches == 32
             && std::all_of(bucketCounts.begin(), bucketCounts.end(),
                            [](IndexType count) { return count == 2; });
    }
    expect(exact,
           "all 64 king squares per perspective normalize to 32 buckets with both mirror branches");
}

void test_bucket_formula() {
    constexpr std::array<IndexType, 7> PieceCounts{2, 4, 5, 8, 9, 29, 32};
    constexpr std::array<IndexType, 7> Expected{0, 0, 1, 1, 2, 7, 7};
    bool                               exact = true;
    for (usize i = 0; i < PieceCounts.size(); ++i)
    {
        IndexType bucket = 0;
        exact = exact && hm_network_bucket(PieceCounts[i], bucket) && bucket == Expected[i];
    }
    IndexType invalidBucket = 0;
    exact                   = exact && !hm_network_bucket(1, invalidBucket)
         && !hm_network_bucket(HmMaximumActiveDimensions + 1, invalidBucket);
    expect(exact, "shared HM PSQT/SFNNv15 runtime bucket formula is exact and fail-closed");
}

void test_export_mapping() {
    JointOrientation orientation{};
    const bool       oriented = make_joint_orientation(WHITE, SQ_B1, orientation);
    expect(oriented && orientation.orientedOwnKing == SQ_G1, "export fixture orientation is valid");

    IndexType ownKingRows = 0;
    IndexType oppKingRows = 0;
    bool      allKingRows = true;
    for (int squareIndex = 0; squareIndex < SQUARE_NB; ++squareIndex)
    {
        const Square   square = Square(squareIndex);
        HmExportSource source{};
        allKingRows = allKingRows
                   && hm_export_source(orientation, HmPhysicalPlane::MergedKing, square, 0, source);
        if (source.trainingPlane == HmTrainingPlane::OwnKing)
            ++ownKingRows;
        else if (source.trainingPlane == HmTrainingPlane::OppKing)
            ++oppKingRows;
        else
            allKingRows = false;

        allKingRows =
          allKingRows
          && source.physicalRow
               == hm_physical_index(orientation.kingBucket, HmPhysicalPlane::MergedKing, square)
          && source.bucketWeightRow
               == hm_training_index(orientation.kingBucket, source.trainingPlane, square)
          && source.virtualWeightRow == hm_virtual_index(source.trainingPlane, square);
    }
    expect(allKingRows && ownKingRows == 1 && oppKingRows == 63,
           "KING export takes OWN_KING only at oriented own-king square");

    bool allOutputs = true;
    for (IndexType output = 0; output < HmCoalescedOutputs; ++output)
    {
        HmExportSource own{};
        HmExportSource opponent{};
        allOutputs =
          allOutputs
          && hm_export_source(orientation, HmPhysicalPlane::MergedKing, orientation.orientedOwnKing,
                              output, own)
          && hm_export_source(orientation, HmPhysicalPlane::MergedKing, SQ_F1, output, opponent)
          && own.trainingPlane == HmTrainingPlane::OwnKing
          && opponent.trainingPlane == HmTrainingPlane::OppKing && own.sourceOutput == output
          && opponent.sourceOutput == output;

        if (output < HmAccumulatorOutputs)
            allOutputs = allOutputs && own.destinationKind == HmOutputKind::Accumulator
                      && own.destinationOutput == output;
        else
            allOutputs = allOutputs && own.destinationKind == HmOutputKind::Psqt
                      && own.destinationOutput == output - HmAccumulatorOutputs;
    }
    expect(allOutputs,
           "12-to-11 KING coalescing is identical across all 1024 accumulator and 8 PSQT outputs");

    HmExportSource nonKing{};
    expect(hm_export_source(orientation, HmPhysicalPlane::OwnQueen, SQ_D4, 1027, nonKing)
             && nonKing.trainingPlane == HmTrainingPlane::OwnQueen
             && nonKing.destinationKind == HmOutputKind::Psqt && nonKing.destinationOutput == 3,
           "non-KING PSQT mapping preserves plane and maps output 1027 to bucket 3");

    constexpr std::array<std::int32_t, 10> NonKingSentinels{3545, 4645, 4693, 4741, 4789,
                                                            4837, 4885, 4933, 4981, 5029};
    constexpr std::array<std::int32_t, 10> OwnKingSentinels{4736,  -2165, -2117, -2069, -2021,
                                                            -1973, -1925, -1877, -1829, -1781};
    constexpr std::array<std::int32_t, 10> OppKingSentinels{-13926, 11175, 11223, 11271, 11319,
                                                            11367,  11415, 11463, 11511, 11559};
    expect(export_sentinels_match(orientation, HmPhysicalPlane::OwnPawn, SQ_H2, NonKingSentinels),
           "non-KING export numeric sentinels cover accumulator boundary and all PSQT outputs");
    expect(
      export_sentinels_match(orientation, HmPhysicalPlane::MergedKing, orientation.orientedOwnKing,
                             OwnKingSentinels),
      "OWN_KING merged export numeric sentinels cover accumulator boundary and all PSQT outputs");
    expect(
      export_sentinels_match(orientation, HmPhysicalPlane::MergedKing, SQ_B8, OppKingSentinels),
      "OPP_KING merged export numeric sentinels cover accumulator boundary and all PSQT outputs");

    HmExportSource invalid{};
    expect(
      !hm_export_source(orientation, HmPhysicalPlane::OwnPawn, SQ_A1, HmCoalescedOutputs, invalid)
        && !hm_export_source(orientation, HmPhysicalPlane::OwnPawn, SQ_NONE, 0, invalid),
      "export mapping rejects out-of-range output and square");

    JointOrientation forged = orientation;
    ++forged.kingBucket;
    const bool rejectsBucket =
      !hm_export_source(forged, HmPhysicalPlane::OwnPawn, SQ_A1, 0, invalid);
    forged = orientation;
    forged.horizontalXor ^= 7;
    const bool rejectsXor  = !hm_export_source(forged, HmPhysicalPlane::OwnPawn, SQ_A1, 0, invalid);
    forged                 = orientation;
    forged.orientedOwnKing = SQ_F1;
    const bool rejectsKing = !hm_export_source(forged, HmPhysicalPlane::OwnPawn, SQ_A1, 0, invalid);
    expect(rejectsBucket && rejectsXor && rejectsKing,
           "export mapping rejects forged inconsistent orientation fields");
}

void test_board_scope_validation() {
    HmEmission emission{};

    HmBoard valid{};
    valid[SQ_E1] = W_KING;
    valid[SQ_E8] = B_KING;
    expect(emit_hm_features(valid, Color(COLOR_NB), emission) == HmOracleError::InvalidPerspective
             && emission.size == 0,
           "board oracle rejects invalid perspective");

    HmBoard missingWhite{};
    missingWhite[SQ_E8] = B_KING;
    HmBoard missingBlack{};
    missingBlack[SQ_E1] = W_KING;
    expect(emit_hm_features(missingWhite, WHITE, emission) == HmOracleError::MissingWhiteKing
             && emit_hm_features(missingBlack, BLACK, emission) == HmOracleError::MissingBlackKing,
           "board oracle requires one king of each color");

    HmBoard multipleWhite = valid;
    multipleWhite[SQ_D1]  = W_KING;
    HmBoard multipleBlack = valid;
    multipleBlack[SQ_D8]  = B_KING;
    expect(emit_hm_features(multipleWhite, WHITE, emission) == HmOracleError::MultipleWhiteKings
             && emit_hm_features(multipleBlack, BLACK, emission)
                  == HmOracleError::MultipleBlackKings,
           "board oracle rejects multiple kings of either color");

    HmBoard white17Black16{};
    for (int squareIndex = 0; squareIndex < 17; ++squareIndex)
        white17Black16[squareIndex] = W_QUEEN;
    for (int squareIndex = 17; squareIndex < 33; ++squareIndex)
        white17Black16[squareIndex] = B_QUEEN;
    white17Black16[0]  = W_KING;
    white17Black16[17] = B_KING;

    HmBoard white16Black17{};
    for (int squareIndex = 0; squareIndex < 16; ++squareIndex)
        white16Black17[squareIndex] = W_QUEEN;
    for (int squareIndex = 16; squareIndex < 33; ++squareIndex)
        white16Black17[squareIndex] = B_QUEEN;
    white16Black17[0]  = W_KING;
    white16Black17[16] = B_KING;
    expect(emit_hm_features(white17Black16, WHITE, emission) == HmOracleError::TooManyPiecesPerColor
             && emit_hm_features(white16Black17, BLACK, emission)
                  == HmOracleError::TooManyPiecesPerColor,
           "board oracle rejects 17/16 and 16/17 material splits");

    HmBoard invalidPiece = valid;
    invalidPiece[SQ_A1]  = Piece(7);
    expect(emit_hm_features(invalidPiece, WHITE, emission) == HmOracleError::InvalidPiece,
           "board oracle rejects unused Piece enum values");
}

void test_start_position() {
    Position  position;
    StateInfo state{};
    if (!set_position(position, state, "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1",
                      false, "start-position FEN accepted"))
        return;

    HmEmission white{};
    HmEmission black{};
    expect(emit_hm_features(position, WHITE, white) == HmOracleError::None,
           "start position emits WHITE HM features");
    expect(emit_hm_features(position, BLACK, black) == HmOracleError::None,
           "start position emits BLACK HM features");
    expect(white.size == 32 && black.size == 32 && white.networkBucket == 7
             && black.networkBucket == 7,
           "start position emits 32 features and shared runtime bucket 7");
    expect(white.orientation.orientedOwnKing == SQ_E1 && white.orientation.kingBucket == 31
             && black.orientation.orientedOwnKing == SQ_E1 && black.orientation.kingBucket == 31,
           "start position computes both perspective orientations independently");

    const HmFeature* whiteA1 = find_feature(white, SQ_A1);
    const HmFeature* blackA1 = find_feature(black, SQ_A1);
    expect(whiteA1 && whiteA1->orientedSquare == SQ_A1
             && whiteA1->trainingPlane == HmTrainingPlane::OwnRook
             && whiteA1->physicalPlane == HmPhysicalPlane::OwnRook
             && whiteA1->trainingIndex == 24192 && whiteA1->virtualIndex == 384
             && whiteA1->physicalIndex == 22208 && whiteA1->psqtRow == 22208,
           "WHITE A1 rook numeric training/virtual/physical/PSQT golden is exact");
    expect(blackA1 && blackA1->orientedSquare == SQ_A8
             && blackA1->trainingPlane == HmTrainingPlane::OppRook
             && blackA1->physicalPlane == HmPhysicalPlane::OppRook
             && blackA1->trainingIndex == 24312 && blackA1->virtualIndex == 504
             && blackA1->physicalIndex == 22328 && blackA1->psqtRow == 22328,
           "BLACK A1 rook numeric training/virtual/physical/PSQT golden is exact");

    const HmFeature* whiteOwnKing = find_feature(white, SQ_E1);
    const HmFeature* whiteOppKing = find_feature(white, SQ_E8);
    expect(whiteOwnKing && whiteOppKing && whiteOwnKing->trainingPlane == HmTrainingPlane::OwnKing
             && whiteOppKing->trainingPlane == HmTrainingPlane::OppKing
             && whiteOwnKing->physicalPlane == HmPhysicalPlane::MergedKing
             && whiteOppKing->physicalPlane == HmPhysicalPlane::MergedKing,
           "active kings use separate training planes and one physical plane");

    bool bounded = true;
    for (const HmEmission* emission : {&white, &black})
        for (IndexType i = 0; i < emission->size; ++i)
        {
            const HmFeature& feature = emission->features[i];
            bounded                  = bounded && feature.trainingIndex < HmTrainingDimensions
                   && feature.virtualIndex < HmVirtualDimensions
                   && feature.physicalIndex < HmPhysicalDimensions
                   && feature.psqtRow == feature.physicalIndex;
        }
    expect(bounded, "all start-position HM indices satisfy declared tensor bounds");
}

void test_horizontal_metamorphism() {
    constexpr const char* Fen         = "6k1/p5p1/8/3pP3/8/2N5/PP4PP/1K4R1 w - d6 0 1";
    constexpr const char* MirroredFen = "1k6/1p5p/8/3Pp3/8/5N2/PP4PP/1R4K1 w - e6 0 1";

    Position  position;
    Position  mirrored;
    StateInfo state{};
    StateInfo mirroredState{};
    if (!set_position(position, state, Fen, false, "horizontal fixture FEN accepted")
        || !set_position(mirrored, mirroredState, MirroredFen, false,
                         "mirrored horizontal fixture FEN accepted"))
        return;

    for (Color perspective : {WHITE, BLACK})
    {
        HmEmission original{};
        HmEmission mirror{};
        const bool emitted =
          emit_hm_features(position, perspective, original) == HmOracleError::None
          && emit_hm_features(mirrored, perspective, mirror) == HmOracleError::None;
        expect(emitted,
               std::string(perspective == WHITE ? "WHITE" : "BLACK") + " horizontal pair emits");
        expect(emitted && original.orientation.horizontalXor != mirror.orientation.horizontalXor
                 && same_projection(original, mirror),
               std::string(perspective == WHITE ? "WHITE" : "BLACK")
                 + " horizontal mirror switches branch but preserves every HM index");
    }
}

void test_position_state_neutrality() {
    // Same Atomic960 placement, but opposite STM and no EP/castling state in
    // the second FEN. HM is purely placement based, so these state fields must
    // not change any emitted index.
    constexpr const char* StatefulFen = "r1k4r/8/8/3pP3/8/8/8/R1K4R w AHah d6 0 1";
    constexpr const char* NeutralFen  = "r1k4r/8/8/3pP3/8/8/8/R1K4R b - - 37 19";

    Position  stateful;
    Position  neutral;
    StateInfo statefulState{};
    StateInfo neutralState{};
    if (!set_position(stateful, statefulState, StatefulFen, true,
                      "Atomic960 stateful HM fixture accepted")
        || !set_position(neutral, neutralState, NeutralFen, true,
                         "Atomic960 neutral HM fixture accepted"))
        return;

    for (Color perspective : {WHITE, BLACK})
    {
        HmEmission lhs{};
        HmEmission rhs{};
        const bool emitted = emit_hm_features(stateful, perspective, lhs) == HmOracleError::None
                          && emit_hm_features(neutral, perspective, rhs) == HmOracleError::None;
        expect(emitted && same_emission_indices(lhs, rhs),
               std::string(perspective == WHITE ? "WHITE" : "BLACK")
                 + " HM indices are neutral to STM, EP, rule50 and Atomic960 castling rights");
    }
}

void test_terminal_rejection() {
    Position  whiteOnly;
    Position  blackOnly;
    StateInfo whiteState{};
    StateInfo blackState{};
    if (!set_position(whiteOnly, whiteState, "8/8/8/8/8/8/8/4K3 w - - 0 1", false,
                      "white-only Atomic terminal accepted by Position")
        || !set_position(blackOnly, blackState, "4k3/8/8/8/8/8/8/8 b - - 0 1", false,
                         "black-only Atomic terminal accepted by Position"))
        return;

    HmEmission emission{};
    expect(emit_hm_features(whiteOnly, WHITE, emission) == HmOracleError::MissingBlackKing
             && emission.size == 0,
           "HM oracle rejects position after BLACK king explosion");
    expect(emit_hm_features(blackOnly, BLACK, emission) == HmOracleError::MissingWhiteKing
             && emission.size == 0,
           "HM oracle rejects position after WHITE king explosion");
}

void run_tests() {
    test_orientation();
    test_orientation_exhaustive();
    test_bucket_formula();
    test_export_mapping();
    test_board_scope_validation();
    test_start_position();
    test_horizontal_metamorphism();
    test_position_state_neutrality();
    test_terminal_rejection();
}

int dump_fen(const std::string& fen, bool chess960) {
    Position  position;
    StateInfo state{};
    if (const auto error = position.set(fen, chess960, &state))
    {
        std::cerr << error->what() << '\n';
        return EXIT_FAILURE;
    }

    for (Color perspective : {WHITE, BLACK})
    {
        HmEmission emission{};
        const auto error = emit_hm_features(position, perspective, emission);
        if (error != HmOracleError::None)
        {
            std::cerr << hm_oracle_error_message(error) << '\n';
            return EXIT_FAILURE;
        }

        const JointOrientation& orientation = emission.orientation;
        std::cout << "perspective=" << (perspective == WHITE ? "white" : "black")
                  << " vertical_xor=" << int(orientation.verticalXor)
                  << " horizontal_xor=" << int(orientation.horizontalXor)
                  << " own_king=" << int(orientation.ownKing)
                  << " oriented_own_king=" << int(orientation.orientedOwnKing)
                  << " king_bucket=" << orientation.kingBucket
                  << " network_bucket=" << emission.networkBucket << " features=" << emission.size
                  << '\n';
        for (IndexType i = 0; i < emission.size; ++i)
        {
            const HmFeature& feature = emission.features[i];
            std::cout << "square=" << int(feature.boardSquare) << " piece=" << int(feature.piece)
                      << " oriented=" << int(feature.orientedSquare)
                      << " training_plane=" << int(feature.trainingPlane)
                      << " training=" << feature.trainingIndex
                      << " virtual=" << feature.virtualIndex
                      << " physical_plane=" << int(feature.physicalPlane)
                      << " physical=" << feature.physicalIndex << " psqt_row=" << feature.psqtRow
                      << '\n';
        }
    }
    return EXIT_SUCCESS;
}

int dump_export_row(std::string_view physicalIndexText, std::string_view orientedOwnKingText) {
    IndexType  physicalIndex = 0;
    IndexType  ownKingIndex  = 0;
    const bool physicalParsed =
      parse_index_argument(physicalIndexText, HmPhysicalDimensions, physicalIndex);
    const bool kingParsed = parse_index_argument(orientedOwnKingText, SQUARE_NB, ownKingIndex);
    if (!physicalParsed || !kingParsed)
    {
        std::cerr << "export row requires canonical unsigned physical-index and oriented-own-king"
                  << '\n';
        return EXIT_FAILURE;
    }

    const Square     orientedOwnKing = Square(ownKingIndex);
    JointOrientation orientation{};
    if (!make_joint_orientation(WHITE, orientedOwnKing, orientation)
        || orientation.orientedOwnKing != orientedOwnKing || orientation.verticalXor != 0
        || orientation.horizontalXor != 0)
    {
        std::cerr << "oriented own king must be a canonical e-h square" << '\n';
        return EXIT_FAILURE;
    }

    const IndexType insideBucket   = physicalIndex % HmPhysicalRowsPerBucket;
    const auto      physicalPlane  = HmPhysicalPlane(insideBucket / HmSquareDimensions);
    const Square    orientedSquare = Square(insideBucket % HmSquareDimensions);

    std::array<HmExportSource, HmCoalescedOutputs> sources{};
    std::array<std::int32_t, HmCoalescedOutputs>   values{};
    for (IndexType output = 0; output < HmCoalescedOutputs; ++output)
    {
        HmExportSource& source = sources[output];
        if (!hm_export_source(orientation, physicalPlane, orientedSquare, output, source)
            || source.physicalRow != physicalIndex
            || (output != 0
                && (source.trainingPlane != sources[0].trainingPlane
                    || source.bucketWeightRow != sources[0].bucketWeightRow
                    || source.virtualWeightRow != sources[0].virtualWeightRow))
            || !synthetic_coalesced_value(source, values[output]))
        {
            std::cerr << "physical row does not belong to the oriented own-king bucket" << '\n';
            return EXIT_FAILURE;
        }
    }

    const HmExportSource& source = sources[0];
    std::cout << "export=source physical_index=" << physicalIndex
              << " king_bucket=" << orientation.kingBucket
              << " physical_plane=" << int(physicalPlane)
              << " oriented_square=" << int(orientedSquare)
              << " training_plane=" << int(source.trainingPlane)
              << " training_index=" << source.bucketWeightRow
              << " virtual_index=" << source.virtualWeightRow << " outputs=" << HmCoalescedOutputs
              << '\n';
    for (IndexType output = 0; output < HmCoalescedOutputs; ++output)
    {
        const HmExportSource& outputSource = sources[output];
        std::cout << "output=" << output << " source_output=" << outputSource.sourceOutput
                  << " destination_kind="
                  << (outputSource.destinationKind == HmOutputKind::Accumulator ? "accumulator"
                                                                                : "psqt")
                  << " destination_output=" << outputSource.destinationOutput
                  << " value=" << values[output] << '\n';
    }
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
            std::cerr << "Atomic V3 HM oracle tests failed: " << failures << '\n';
            return EXIT_FAILURE;
        }
        std::cout << "Atomic V3 HM oracle tests passed\n";
        return EXIT_SUCCESS;
    }

    if (argc == 3 && (std::string(argv[1]) == "--fen" || std::string(argv[1]) == "--chess960-fen"))
        return dump_fen(argv[2], std::string(argv[1]) == "--chess960-fen");

    if (argc == 4 && std::string(argv[1]) == "--export-row")
        return dump_export_row(argv[2], argv[3]);

    std::cerr << "usage: atomic-v3-hm-oracle-tests [--fen FEN | --chess960-fen FEN | "
                 "--export-row PHYSICAL_INDEX ORIENTED_OWN_KING]\n";
    return EXIT_FAILURE;
}
