/*
  Atomic-Stockfish direct unit tests for explosive Static Exchange Evaluation.

  The expected values were derived from Fairy-Stockfish's blast_see() at
  fairy-stockfish/Fairy-Stockfish@fb78cb561aa01708338e35b3dc3b65a42149a3c4.
*/

#include <array>
#include <cstddef>
#include <iostream>
#include <limits>
#include <memory>
#include <string>
#include <string_view>
#include <type_traits>

#include "attacks.h"
#include "bitboard.h"
#include "evaluate.h"
#include "movegen.h"
#include "position.h"
#include "search.h"
#include "uci.h"
#include "types.h"
#include "uci_move.h"

using namespace Stockfish;

namespace {

static_assert(std::is_standard_layout_v<StateInfo>);
static_assert(std::is_trivially_copyable_v<StateInfo>);
static_assert(StateInfo::CHECK_SQUARE_NB == 5);
static_assert(StateInfo::check_square_index(PAWN) == 0);
static_assert(StateInfo::check_square_index(QUEEN) == StateInfo::CHECK_SQUARE_NB - 1);
static_assert(offsetof(StateInfo, key)
              >= offsetof(StateInfo, atomicOpponentInCheck) + sizeof(bool));
static_assert(offsetof(StateInfo, key) < offsetof(StateInfo, previous));
static_assert(Eval::NNUE::FeatureSet::MaxActiveDimensions == 32);
static_assert(Eval::NNUE::FeatureSet::MaxRemovedDimensions
              == 2 + DirtyPiece::MAX_ATOMIC_BLAST_PIECES);
static_assert(Eval::NNUE::FeatureSet::MaxAddedDimensions == 2);
static_assert(sizeof(Eval::NNUE::FeatureSet::AddedIndexList)
              < sizeof(Eval::NNUE::FeatureSet::RemovedIndexList));
static_assert(sizeof(Eval::NNUE::FeatureSet::RemovedIndexList)
              < sizeof(Eval::NNUE::FeatureSet::ActiveIndexList));

struct SeeCase {
    std::string_view name;
    std::string_view fen;
    Square           from;
    Square           to;
    int              expected;
};

constexpr std::array<SeeCase, 9> SeeCases = {{
  {"enemy bycatch", "7k/8/8/8/3pq3/2P5/8/K7 w - - 0 1", SQ_C3, SQ_D4, 1861},
  {"friendly bycatch", "7k/8/8/8/3q4/2P1Q3/8/K7 w - - 0 1", SQ_C3, SQ_D4, -302},
  {"adjacent pawns immune", "7k/8/8/3p4/2PPP3/2N5/8/K7 w - - 0 1", SQ_C3, SQ_D5, -402},
  {"attacker inside blast", "7k/8/8/3r4/8/8/8/K2Q4 w - - 0 1", SQ_D1, SQ_D4, -783},
  {"opponent declines quiet capture", "k7/8/8/3rq3/8/8/3P4/7K w - - 0 1", SQ_D2, SQ_D4, 0},
  {"king excluded as quiet attacker", "K7/8/8/8/8/8/8/4N2k w - - 0 1", SQ_E1, SQ_G2, 0},
  {"opponent king explodes", "6rk/8/8/8/8/8/8/K5R1 w - - 0 1", SQ_G1, SQ_G8, VALUE_MATE},
  {"own king explodes", "7k/8/8/8/8/8/3r4/2KR4 w - - 0 1", SQ_D1, SQ_D2, -VALUE_MATE},
  {"Atomic values change SEE sign", "7k/8/8/8/3qN3/2P1R3/8/K7 w - - 0 1", SQ_C3, SQ_D4, -221},
}};

bool expect_exact(const SeeCase& test) {
    Position  pos;
    StateInfo state{};

    if (pos.set(std::string(test.fen), false, &state))
    {
        std::cerr << "FAIL " << test.name << ": invalid fixture FEN\n";
        return false;
    }

    const Move move(test.from, test.to);
    const bool atExpected  = pos.see_ge(move, test.expected);
    const bool aboveResult = pos.see_ge(move, test.expected + 1);

    if (!atExpected || aboveResult)
    {
        std::cerr << "FAIL " << test.name << ": expected exact SEE " << test.expected
                  << ", see_ge(expected)=" << atExpected << ", see_ge(expected + 1)=" << aboveResult
                  << '\n';
        return false;
    }

    std::cout << "PASS " << test.name << " SEE=" << test.expected << '\n';
    return true;
}

bool expect_special_moves_are_neutral() {
    struct SpecialCase {
        std::string_view name;
        std::string_view fen;
        Move             move;
    };

    const std::array<SpecialCase, 3> tests = {{
      {"en passant", "7k/8/8/3pP3/8/8/8/K7 w - d6 0 1", Move::make<EN_PASSANT>(SQ_E5, SQ_D6)},
      {"promotion", "7k/P7/8/8/8/8/8/K7 w - - 0 1", Move::make<PROMOTION>(SQ_A7, SQ_A8, QUEEN)},
      {"castling", "4k3/8/8/8/8/8/8/R3K2R w KQ - 0 1", Move::make<CASTLING>(SQ_E1, SQ_H1)},
    }};

    bool ok = true;
    for (const auto& test : tests)
    {
        Position  pos;
        StateInfo state{};

        if (pos.set(std::string(test.fen), false, &state) || !pos.see_ge(test.move, 0)
            || pos.see_ge(test.move, 1))
        {
            std::cerr << "FAIL " << test.name << ": non-normal SEE must be exactly zero\n";
            ok = false;
        }
        else
            std::cout << "PASS " << test.name << " SEE=0\n";
    }

    return ok;
}

bool expect_atomic_wins() {
    struct AtomicWinCase {
        std::string_view name;
        std::string_view fen;
        Move             move;
        bool             expected;
    };

    const std::array<AtomicWinCase, 7> tests = {{
      {"direct king capture", "7k/7R/8/8/8/8/8/K7 w - - 0 1", Move(SQ_H7, SQ_H8), true},
      {"adjacent king bycatch", "6rk/8/8/8/8/8/8/K5R1 w - - 0 1", Move(SQ_G1, SQ_G8), true},
      {"en-passant king bycatch", "8/2k5/8/3pP3/8/8/8/K7 w - d6 0 1",
       Move::make<EN_PASSANT>(SQ_E5, SQ_D6), true},
      {"capture-promotion king bycatch", "6kr/6P1/8/8/8/8/8/K7 w - - 0 1",
       Move::make<PROMOTION>(SQ_G7, SQ_H8, QUEEN), true},
      {"remote capture", "7k/8/8/8/3r4/2P5/8/K7 w - - 0 1", Move(SQ_C3, SQ_D4), false},
      {"quiet move beside king", "7k/8/8/8/8/8/6R1/K7 w - - 0 1", Move(SQ_G2, SQ_G7), false},
      {"remote en passant", "7k/8/8/3pP3/8/8/8/K7 w - d6 0 1", Move::make<EN_PASSANT>(SQ_E5, SQ_D6),
       false},
    }};

    bool ok = true;
    for (const auto& test : tests)
    {
        Position  pos;
        StateInfo state{};

        if (pos.set(std::string(test.fen), false, &state)
            || !MoveList<LEGAL>(pos).contains(test.move))
        {
            std::cerr << "FAIL " << test.name << ": invalid or illegal atomic_wins fixture\n";
            ok = false;
            continue;
        }

        const bool actual = pos.atomic_wins(test.move);
        if (actual != test.expected)
        {
            std::cerr << "FAIL " << test.name << ": expected atomic_wins=" << test.expected
                      << ", got " << actual << '\n';
            ok = false;
        }
        else
            std::cout << "PASS " << test.name << " atomic_wins=" << actual << '\n';
    }

    return ok;
}

bool expect_atomic_gives_check() {
    struct GivesCheckCase {
        std::string_view name;
        std::string_view fen;
        Move             move;
        bool             chess960;
        bool             expected;
    };

    const std::array<GivesCheckCase, 14> tests = {{
      {"quiet direct check", "7k/8/8/8/8/8/8/KR6 w - - 0 1", Move(SQ_B1, SQ_H1), false, true},
      {"quiet discovered check", "R2B3k/8/8/8/8/8/8/K7 w - - 0 1", Move(SQ_D8, SQ_C7), false, true},
      {"capturer explosion removes checker", "8/p6k/8/8/8/8/8/R6K w - - 0 1", Move(SQ_A1, SQ_A7),
       false, false},
      {"capture blast discovers check", "R2n3k/8/8/8/8/8/8/K2R4 w - - 0 1", Move(SQ_D1, SQ_D8),
       false, true},
      {"en passant discovers check", "8/8/8/R2pP2k/8/8/8/K7 w - d6 0 1",
       Move::make<EN_PASSANT>(SQ_E5, SQ_D6), false, true},
      {"promotion gives check", "7k/6P1/8/8/8/8/8/K7 w - - 0 1",
       Move::make<PROMOTION>(SQ_G7, SQ_G8, QUEEN), false, true},
      {"castling rook gives check", "5k2/8/8/8/8/8/8/4K2R w K - 0 1",
       Move::make<CASTLING>(SQ_E1, SQ_H1), false, true},
      {"adjacent kings suppress discovered check", "R4K1k/8/8/8/8/8/8/8 w - - 0 1",
       Move(SQ_F8, SQ_G7), false, false},
      {"analysis check survives unrelated quiet", "4k3/8/8/1B6/8/8/8/4K3 w - - 0 1",
       Move(SQ_E1, SQ_D1), false, true},
      {"analysis rook check survives b1 quiet", "k7/8/8/8/8/8/7B/RN5K w - - 0 1",
       Move(SQ_B1, SQ_C3), false, true},
      {"Atomic960 castling with king already on g1", "5k2/8/8/8/8/8/8/6KR w H - 0 1",
       Move::make<CASTLING>(SQ_G1, SQ_H1), true, true},
      {"Atomic960 castling with rook already on f1", "5k2/8/8/8/8/8/8/4KR2 w F - 0 1",
       Move::make<CASTLING>(SQ_E1, SQ_F1), true, true},
      {"Atomic960 castling with king starting on f1", "5k2/8/8/8/8/8/8/5K1R w H - 0 1",
       Move::make<CASTLING>(SQ_F1, SQ_H1), true, true},
      {"Atomic960 castling with rook starting on g1", "5k2/8/8/8/8/8/8/4K1R1 w G - 0 1",
       Move::make<CASTLING>(SQ_E1, SQ_G1), true, true},
    }};

    bool ok = true;
    for (const auto& test : tests)
    {
        Position  pos;
        StateInfo state{};

        if (pos.set(std::string(test.fen), test.chess960, &state)
            || !MoveList<LEGAL>(pos).contains(test.move))
        {
            std::cerr << "FAIL " << test.name << ": invalid or illegal gives_check fixture\n";
            ok = false;
            continue;
        }

        const bool actual = pos.gives_check(test.move);
        if (actual != test.expected)
        {
            std::cerr << "FAIL " << test.name << ": expected gives_check=" << test.expected
                      << ", got " << actual << '\n';
            ok = false;
        }
        else
            std::cout << "PASS " << test.name << " gives_check=" << actual << '\n';
    }

    return ok;
}

bool expect_non_orthodox_atomic_evasions() {
    struct EvasionCase {
        std::string_view name;
        std::string_view fen;
        Move             move;
    };

    const std::array<EvasionCase, 2> tests = {{
      {"quiet Atomic check evasion", "7k/8/8/8/8/8/7R/K7 b - - 1 1", Move(SQ_H8, SQ_G7)},
      {"promotion blast removes checking rook", "rn5k/2P5/8/8/8/8/8/K7 w - - 0 1",
       Move::make<PROMOTION>(SQ_C7, SQ_B8, QUEEN)},
    }};

    bool ok = true;
    for (const auto& test : tests)
    {
        Position  pos;
        StateInfo state{};

        if (pos.set(std::string(test.fen), false, &state))
        {
            std::cerr << "FAIL " << test.name << ": invalid fixture\n";
            ok = false;
            continue;
        }

        const bool inCheck     = pos.atomic_in_check(pos.side_to_move());
        const bool pseudoLegal = pos.pseudo_legal(test.move);
        const bool legal       = pos.legal(test.move);
        const bool listed      = MoveList<LEGAL>(pos).contains(test.move);

        if (!inCheck || !pseudoLegal || !legal || !listed)
        {
            std::cerr << "FAIL " << test.name << ": inCheck=" << inCheck
                      << " pseudoLegal=" << pseudoLegal << " legal=" << legal
                      << " listed=" << listed << '\n';
            ok = false;
        }
        else
            std::cout << "PASS " << test.name << '\n';
    }

    return ok;
}

bool expect_atomic_capture_generation_prefilter() {
    struct CaptureCase {
        std::string_view name;
        std::string_view fen;
        Move             move;
        bool             inCaptures;
        bool             inNonEvasions;
        bool             isLegal;
    };

    const std::array<CaptureCase, 13> tests = {{
      {"self-exploding slider capture", "7k/8/8/3R4/8/8/3n4/4K3 w - - 0 1", Move(SQ_D5, SQ_D2),
       false, true, false},
      {"self-exploding king capture", "7k/8/8/8/8/8/3n4/4K3 w - - 0 1", Move(SQ_E1, SQ_D2), false,
       true, false},
      {"self-exploding pawn capture", "7k/8/8/8/3n4/2P1K3/8/8 w - - 0 1", Move(SQ_C3, SQ_D4), false,
       true, false},
      {"self-exploding capture promotion", "k4r2/4P1K1/8/8/8/8/8/8 w - - 0 1",
       Move::make<PROMOTION>(SQ_E7, SQ_F8, QUEEN), false, true, false},
      {"self-exploding en passant", "7k/8/4K3/3pP3/8/8/8/8 w - d6 0 1",
       Move::make<EN_PASSANT>(SQ_E5, SQ_D6), false, true, false},
      {"remote capture retained", "7k/8/8/3R4/8/8/3n4/K7 w - - 0 1", Move(SQ_D5, SQ_D2), true, true,
       true},
      {"remote pawn capture retained", "7k/8/8/8/3n4/2P5/8/K7 w - - 0 1", Move(SQ_C3, SQ_D4), true,
       true, true},
      {"remote capture promotion retained", "k4r2/4P3/8/8/8/8/8/K7 w - - 0 1",
       Move::make<PROMOTION>(SQ_E7, SQ_F8, QUEEN), true, true, true},
      {"remote en passant retained", "7k/8/8/3pP3/8/8/8/K7 w - d6 0 1",
       Move::make<EN_PASSANT>(SQ_E5, SQ_D6), true, true, true},
      {"terminal direct king capture retained", "7k/7R/8/8/8/8/8/K7 w - - 0 1", Move(SQ_H7, SQ_H8),
       true, true, true},
      {"quiet queen promotion retained in captures", "7k/P7/8/8/8/8/8/K7 w - - 0 1",
       Move::make<PROMOTION>(SQ_A7, SQ_A8, QUEEN), true, true, true},
      {"quiet queen promotion beside own king retained", "7k/PK6/8/8/8/8/8/8 w - - 0 1",
       Move::make<PROMOTION>(SQ_A7, SQ_A8, QUEEN), true, true, true},
      {"legal quiet move beside own king retained", "7k/8/8/8/8/3R4/8/4K3 w - - 0 1",
       Move(SQ_D3, SQ_D2), false, true, true},
    }};

    bool ok = true;
    for (const auto& test : tests)
    {
        Position  pos;
        StateInfo state{};
        if (pos.set(std::string(test.fen), false, &state))
        {
            std::cerr << "FAIL Atomic capture prefilter " << test.name << ": invalid fixture\n";
            ok = false;
            continue;
        }

        const bool inCaptures    = MoveList<CAPTURES>(pos).contains(test.move);
        const bool inNonEvasions = MoveList<NON_EVASIONS>(pos).contains(test.move);
        const bool isLegal       = MoveList<LEGAL>(pos).contains(test.move);

        if (inCaptures != test.inCaptures || inNonEvasions != test.inNonEvasions
            || isLegal != test.isLegal)
        {
            std::cerr << "FAIL Atomic capture prefilter " << test.name
                      << ": captures=" << inCaptures << " expected=" << test.inCaptures
                      << " non-evasions=" << inNonEvasions << " expected=" << test.inNonEvasions
                      << " legal=" << isLegal << " expected=" << test.isLegal << '\n';
            ok = false;
        }
        else
            std::cout << "PASS Atomic capture prefilter " << test.name << " captures=" << inCaptures
                      << " non-evasions=" << inNonEvasions << " legal=" << isLegal << '\n';
    }

    return ok;
}

bool expect_state_info_layout_contract() {
    Position  pos;
    StateInfo state{};

    if (pos.set("rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1", false, &state)
        || pos.check_squares(KING) != 0)
    {
        std::cerr << "FAIL StateInfo layout: KING check squares must stay empty\n";
        return false;
    }

    std::cout << "PASS StateInfo layout sizeof=" << sizeof(StateInfo)
              << " copied-prefix=" << offsetof(StateInfo, key) << '\n';
    return true;
}

bool expect_gives_check_matches_child() {
    struct CorpusPosition {
        std::string_view name;
        std::string_view fen;
        bool             chess960;
    };

    const std::array<CorpusPosition, 11> corpus = {{
      {"start position", "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1", false},
      {"terminal king explosion", "7k/7R/8/8/8/8/8/K7 w - - 0 1", false},
      {"en passant", "8/8/8/R2pP2k/8/8/8/K7 w - d6 0 1", false},
      {"promotion", "7k/6P1/8/8/8/8/8/K7 w - - 0 1", false},
      {"orthodox-layout castling", "5k2/8/8/8/8/8/8/4K2R w K - 0 1", false},
      {"discovered check", "R2B3k/8/8/8/8/8/8/K7 w - - 0 1", false},
      {"adjacent kings", "R4K1k/8/8/8/8/8/8/8 w - - 0 1", false},
      {"Atomic960 king on g1", "5k2/8/8/8/8/8/8/6KR w H - 0 1", true},
      {"Atomic960 rook on f1", "5k2/8/8/8/8/8/8/4KR2 w F - 0 1", true},
      {"Atomic960 king on f1", "5k2/8/8/8/8/8/8/5K1R w H - 0 1", true},
      {"Atomic960 rook on g1", "5k2/8/8/8/8/8/8/4K1R1 w G - 0 1", true},
    }};

    usize checkedMoves = 0;
    bool  ok           = true;

    for (const auto& fixture : corpus)
    {
        Position  pos;
        StateInfo rootState{};
        StateInfo childState{};

        if (pos.set(std::string(fixture.fen), fixture.chess960, &rootState))
        {
            std::cerr << "FAIL gives_check child parity " << fixture.name << ": invalid FEN\n";
            ok = false;
            continue;
        }

        const std::string     rootFen = pos.fen();
        const Key             rootKey = pos.key();
        const MoveList<LEGAL> moves(pos);

        for (Move move : moves)
        {
            const bool predicted = pos.gives_check(move);
            pos.do_move(move, childState);
            const bool actual = pos.atomic_in_check(pos.side_to_move());
            pos.undo_move(move);
            ++checkedMoves;

            if (predicted != actual || pos.fen() != rootFen || pos.key() != rootKey)
            {
                std::cerr << "FAIL gives_check child parity " << fixture.name
                          << " move=" << UCI::move(move, fixture.chess960)
                          << " predicted=" << predicted << " actual=" << actual << '\n';
                ok = false;
            }
        }
    }

    if (!checkedMoves)
    {
        std::cerr << "FAIL gives_check child parity: corpus had no legal moves\n";
        return false;
    }

    if (ok)
        std::cout << "PASS gives_check child parity moves=" << checkedMoves << '\n';
    return ok;
}

bool same_incremental_state(const Position& actual, const Position& rebuilt) {
    return actual.fen() == rebuilt.fen() && actual.key() == rebuilt.key()
        && actual.material_key() == rebuilt.material_key()
        && actual.pawn_key() == rebuilt.pawn_key()
        && actual.minor_piece_key() == rebuilt.minor_piece_key()
        && actual.non_pawn_key(WHITE) == rebuilt.non_pawn_key(WHITE)
        && actual.non_pawn_key(BLACK) == rebuilt.non_pawn_key(BLACK)
        && actual.non_pawn_material(WHITE) == rebuilt.non_pawn_material(WHITE)
        && actual.non_pawn_material(BLACK) == rebuilt.non_pawn_material(BLACK)
        && actual.rule50_count() == rebuilt.rule50_count()
        && actual.ep_square() == rebuilt.ep_square();
}

bool expect_nnue_index_list_bounds() {
    using FeatureSet = Eval::NNUE::FeatureSet;

    Position  start;
    StateInfo startState{};
    if (start.set("rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1", false, &startState))
    {
        std::cerr << "FAIL NNUE index lists: invalid start-position fixture\n";
        return false;
    }

    for (Color perspective : {WHITE, BLACK})
    {
        FeatureSet::ActiveIndexList active;
        FeatureSet::append_active_indices(start, perspective, active);
        if (active.size() != FeatureSet::MaxActiveDimensions)
        {
            std::cerr << "FAIL NNUE index lists: start position active=" << active.size()
                      << " expected=" << FeatureSet::MaxActiveDimensions << '\n';
            return false;
        }
    }
    std::cout << "PASS NNUE active index boundary startpos=32\n";

    struct IndexListCase {
        std::string_view name;
        std::string_view fen;
        std::string_view move;
        bool             chess960;
        usize            blastCount;
        usize            removedCount;
        usize            addedCount;
        usize            activeAfter;
    };

    constexpr std::array<IndexListCase, 6> tests = {{
      {"maximum capture", "7k/8/8/2nnn3/2nrn3/2nnnN2/8/K7 w - - 0 1", "f3d4", false,
       DirtyPiece::MAX_ATOMIC_BLAST_PIECES, 11, 1, 2},
      {"maximum en passant", "7k/2n1n3/2n1n3/2npP3/8/8/8/K7 w - d6 0 2", "e5d6", false, 6, 8, 1, 2},
      {"maximum capture promotion", "2nrn2k/2nnP3/8/8/8/8/8/K7 w - - 0 1", "e7d8q", false, 5, 7, 1,
       2},
      {"castling", "4k3/8/8/8/8/8/8/R3K2R w KQ - 0 1", "e1g1", false, 0, 2, 2, 4},
      {"Atomic960 castling", "7k/8/8/8/8/8/8/1RK5 w Q - 0 1", "c1b1", true, 0, 2, 2, 3},
      {"terminal king capture", "7k/7R/8/8/8/8/8/K7 w - - 0 1", "h7h8", false, 1, 3, 1, 1},
    }};

    bool ok = true;
    for (const auto& test : tests)
    {
        Position  pos;
        StateInfo rootState{};
        StateInfo childState{};
        if (pos.set(std::string(test.fen), test.chess960, &rootState))
        {
            std::cerr << "FAIL NNUE index lists " << test.name << ": invalid fixture\n";
            ok = false;
            continue;
        }

        const Move move = UCI::to_move(pos, std::string(test.move));
        if (!move)
        {
            std::cerr << "FAIL NNUE index lists " << test.name << ": illegal fixture move\n";
            ok = false;
            continue;
        }

        const std::string rootFen = pos.fen();
        const Key         rootKey = pos.key();
        DirtyPiece        dirtyPiece{};
        pos.do_move(move, childState, pos.gives_check(move), dirtyPiece, nullptr, nullptr);

        FeatureSet::RemovedIndexList removed;
        FeatureSet::AddedIndexList   added;
        const Color                  perspective = pos.has_king(BLACK) ? BLACK : WHITE;
        FeatureSet::append_changed_indices(perspective, pos.square<KING>(perspective), dirtyPiece,
                                           removed, added);

        bool matches = dirtyPiece.atomicBlast.size() == test.blastCount
                    && removed.size() == test.removedCount && added.size() == test.addedCount;
        for (Color activePerspective : {WHITE, BLACK})
        {
            FeatureSet::ActiveIndexList active;
            FeatureSet::append_active_indices(pos, activePerspective, active);
            matches &= active.size() == test.activeAfter;
        }

        if (!matches)
        {
            std::cerr << "FAIL NNUE index lists " << test.name
                      << ": blast=" << dirtyPiece.atomicBlast.size()
                      << " removed=" << removed.size() << " added=" << added.size()
                      << " active=" << popcount(pos.pieces()) << '\n';
            ok = false;
        }
        else
            std::cout << "PASS NNUE index lists " << test.name << " removed=" << removed.size()
                      << " added=" << added.size() << " active=" << test.activeAfter << '\n';

        pos.undo_move(move);
        if (pos.fen() != rootFen || pos.key() != rootKey)
        {
            std::cerr << "FAIL NNUE index lists " << test.name << ": undo mismatch\n";
            ok = false;
        }
    }

    return ok;
}

bool expect_make_undo_state() {
    struct StateCase {
        std::string_view name;
        std::string_view fen;
        Move             move;
        bool             chess960;
    };

    const std::array<StateCase, 8> tests = {{
      {"explosive capture state", "7k/8/8/2pBn3/3r4/2PQN3/8/K7 w - - 0 1", Move(SQ_D3, SQ_D4),
       false},
      {"en passant state", "7k/8/2N1b3/2ppP3/8/8/8/K7 w - d6 0 2",
       Move::make<EN_PASSANT>(SQ_E5, SQ_D6), false},
      {"capture promotion state", "k5br/6P1/8/8/8/8/8/K7 w - - 0 1",
       Move::make<PROMOTION>(SQ_G7, SQ_H8, QUEEN), false},
      {"direct king capture state", "7k/7R/8/8/8/8/8/K7 w - - 0 1", Move(SQ_H7, SQ_H8), false},
      {"blast clears castling state", "6k1/8/8/8/8/8/1r6/RN2K2R b KQ - 0 1", Move(SQ_B2, SQ_B1),
       false},
      {"double push en passant key", "7k/8/8/8/3p4/8/4P3/K7 w - - 0 1", Move(SQ_E2, SQ_E4), false},
      {"orthodox-layout Atomic castling", "7k/8/8/8/8/8/8/R3K2R w KQ - 0 1",
       Move::make<CASTLING>(SQ_E1, SQ_H1), false},
      {"Atomic960 castling", "7k/8/8/8/8/8/8/1RK5 w Q - 0 1", Move::make<CASTLING>(SQ_C1, SQ_B1),
       true},
    }};

    bool ok = true;
    for (const auto& test : tests)
    {
        Position  pos;
        Position  original;
        Position  rebuilt;
        StateInfo rootState{};
        StateInfo originalState{};
        StateInfo nextState{};
        StateInfo rebuiltState{};

        if (pos.set(std::string(test.fen), test.chess960, &rootState)
            || original.set(std::string(test.fen), test.chess960, &originalState)
            || !MoveList<LEGAL>(pos).contains(test.move))
        {
            std::cerr << "FAIL " << test.name << ": invalid or illegal fixture\n";
            ok = false;
            continue;
        }

        pos.do_move(test.move, nextState);
        if (rebuilt.set(pos.fen(), test.chess960, &rebuiltState)
            || !same_incremental_state(pos, rebuilt))
        {
            std::cerr << "FAIL " << test.name
                      << ": incremental state differs from FEN reconstruction\n";
            ok = false;
        }

        pos.undo_move(test.move);
        if (!same_incremental_state(pos, original))
        {
            std::cerr << "FAIL " << test.name << ": undo did not restore original state\n";
            ok = false;
        }
        else
            std::cout << "PASS " << test.name << " make/undo state\n";
    }

    return ok;
}

bool expect_repetition_state() {
    Position                  pos;
    std::array<StateInfo, 9>  states{};
    const std::array<Move, 4> cycle = {Move(SQ_G2, SQ_F4), Move(SQ_H8, SQ_G8), Move(SQ_F4, SQ_G2),
                                       Move(SQ_G8, SQ_H8)};

    if (pos.set("7k/8/8/8/8/8/6N1/K7 w - - 0 1", false, &states[0]))
    {
        std::cerr << "FAIL repetition: invalid fixture\n";
        return false;
    }

    for (usize ply = 0; ply < 8; ++ply)
    {
        const Move move = cycle[ply % cycle.size()];
        if (!MoveList<LEGAL>(pos).contains(move))
        {
            std::cerr << "FAIL repetition: illegal move at ply " << ply + 1 << '\n';
            return false;
        }

        pos.do_move(move, states[ply + 1]);

        if (ply == 3
            && (pos.state()->repetition != 4 || !pos.has_repeated() || pos.is_draw(4)
                || !pos.is_draw(5)))
        {
            std::cerr << "FAIL repetition: first-cycle distance semantics differ\n";
            return false;
        }
    }

    if (pos.state()->repetition != -4 || !pos.is_draw(0))
    {
        std::cerr << "FAIL repetition: threefold-before-root semantics differ\n";
        return false;
    }

    for (usize ply = 8; ply > 0; --ply)
        pos.undo_move(cycle[(ply - 1) % cycle.size()]);

    if (pos.fen() != "7k/8/8/8/8/8/6N1/K7 w - - 0 1")
    {
        std::cerr << "FAIL repetition: undo chain did not restore root\n";
        return false;
    }

    std::cout << "PASS repetition and threefold state\n";
    return true;
}

bool expect_rule50_state() {
    Position  quiet;
    Position  pawn;
    Position  capture;
    Position  mate;
    StateInfo quietRoot{};
    StateInfo quietNext{};
    StateInfo pawnRoot{};
    StateInfo pawnNext{};
    StateInfo captureRoot{};
    StateInfo captureNext{};
    StateInfo mateRoot{};
    StateInfo mateNext{};

    if (quiet.set("7k/8/8/8/8/8/6N1/K7 w - - 99 1", false, &quietRoot)
        || pawn.set("7k/8/8/8/8/8/4P3/K7 w - - 99 1", false, &pawnRoot)
        || capture.set("7k/8/8/8/3q4/2P1Q3/8/K7 w - - 99 1", false, &captureRoot)
        || mate.set("B7/Rk6/8/8/8/8/7Q/4K3 w - - 99 1", false, &mateRoot))
    {
        std::cerr << "FAIL rule50: invalid fixture\n";
        return false;
    }

    quiet.do_move(Move(SQ_G2, SQ_F4), quietNext);
    pawn.do_move(Move(SQ_E2, SQ_E3), pawnNext);
    capture.do_move(Move(SQ_C3, SQ_D4), captureNext);
    mate.do_move(Move(SQ_H2, SQ_B8), mateNext);

    if (quiet.rule50_count() != 100 || !quiet.is_draw(1))
    {
        std::cerr << "FAIL rule50: quiet move did not reach the draw threshold\n";
        return false;
    }
    if (pawn.rule50_count() != 0 || pawn.is_draw(1))
    {
        std::cerr << "FAIL rule50: pawn move did not reset the counter\n";
        return false;
    }
    if (capture.rule50_count() != 0 || capture.is_draw(1))
    {
        std::cerr << "FAIL rule50: Atomic capture did not reset the counter\n";
        return false;
    }
    if (mate.rule50_count() != 100 || !mate.atomic_in_check(BLACK) || MoveList<LEGAL>(mate).size()
        || mate.is_draw(1))
    {
        std::cerr << "FAIL rule50: draw claim obscured Atomic checkmate\n";
        return false;
    }

    std::cout << "PASS fifty-move and reset state\n";
    return true;
}

bool expect_uci_move_notation() {
    Position  start;
    Position  castling;
    Position  promotion;
    Position  atomic960;
    StateInfo startState{};
    StateInfo castlingState{};
    StateInfo promotionState{};
    StateInfo atomic960State{};

    if (start.set("rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1", false, &startState)
        || castling.set("4k3/8/8/8/8/8/8/R3K2R w KQ - 0 1", false, &castlingState)
        || promotion.set("7k/P7/8/8/8/8/8/K7 w - - 0 1", false, &promotionState)
        || atomic960.set("7k/8/8/8/8/8/8/1RK5 w Q - 0 1", true, &atomic960State))
    {
        std::cerr << "FAIL UCI move notation: invalid fixture\n";
        return false;
    }

    const Move kingSideCastle = Move::make<CASTLING>(SQ_E1, SQ_H1);
    const Move frcCastle      = Move::make<CASTLING>(SQ_C1, SQ_B1);
    const Move queenPromotion = Move::make<PROMOTION>(SQ_A7, SQ_A8, QUEEN);

    const bool ok =
      UCI::square(SQ_A1) == "a1" && UCI::square(SQ_H8) == "h8"
      && UCI::move(Move::none()) == "(none)" && UCI::move(Move::null()) == "0000"
      && UCI::move(kingSideCastle, false) == "e1g1" && UCI::move(kingSideCastle, true) == "e1h1"
      && UCI::move(frcCastle, true) == "c1b1" && UCI::move(queenPromotion) == "a7a8q"
      && UCI::to_lower("A7A8Q") == "a7a8q" && UCI::to_move(start, "E2E4") == Move(SQ_E2, SQ_E4)
      && UCI::to_move(start, "e2e5") == Move::none()
      && UCI::to_move(castling, "E1G1") == kingSideCastle
      && UCI::to_move(promotion, "A7A8Q") == queenPromotion
      && UCI::to_move(atomic960, "C1B1") == frcCastle;

    if (!ok)
    {
        std::cerr << "FAIL UCI move notation: format/parse contract differs\n";
        return false;
    }

    std::cout << "PASS UCI move notation, case folding and Atomic960 castling\n";
    return true;
}

bool expect_atomic_move_count_thresholds() {
    struct ThresholdCase {
        bool improving;
        int  depth;
        int  expected;
    };

    constexpr std::array<ThresholdCase, 6> tests = {
      {{false, 3, 4}, {false, 4, 7}, {false, 6, 13}, {true, 3, 7}, {true, 4, 10}, {true, 6, 20}}};

    bool ok = true;
    for (const auto& test : tests)
    {
        const int actual = Search::atomic_move_count_pruning_threshold(test.improving, test.depth);
        if (actual != test.expected)
        {
            std::cerr << "FAIL Atomic move-count threshold: improving=" << test.improving
                      << " depth=" << test.depth << " expected=" << test.expected
                      << " actual=" << actual << '\n';
            ok = false;
        }
        else
            std::cout << "PASS Atomic move-count threshold improving=" << test.improving
                      << " depth=" << test.depth << " threshold=" << actual << '\n';
    }

    return ok;
}

bool expect_atomic_null_move_reductions() {
    struct ReductionCase {
        int depth;
        int expected;
    };

    constexpr std::array<ReductionCase, 7> tests = {
      {{1, 6}, {3, 7}, {6, 8}, {9, 9}, {15, 11}, {16, 11}, {18, 12}}};

    bool ok = true;
    for (const auto& test : tests)
    {
        const int actual = Search::atomic_null_move_reduction(test.depth);
        if (actual != test.expected)
        {
            std::cerr << "FAIL Atomic null-move reduction: depth=" << test.depth
                      << " expected=" << test.expected << " actual=" << actual << '\n';
            ok = false;
        }
        else
            std::cout << "PASS Atomic null-move reduction depth=" << test.depth
                      << " reduction=" << actual << '\n';
    }

    return ok;
}

bool expect_atomic_capture_futility_eligibility() {
    struct EligibilityCase {
        std::string_view name;
        std::string_view fen;
        std::string_view move;
        bool             expected;
    };

    constexpr std::array<EligibilityCase, 6> tests = {{
      {"normal victim only", "7k/8/8/8/3p4/2B5/8/K7 w - - 0 1", "c3d4", true},
      {"adjacent pawn survives", "7k/8/8/8/3pP3/2B5/8/K7 w - - 0 1", "c3d4", true},
      {"non-pawn explosion bycatch", "7k/8/8/8/3pR3/2B5/8/K7 w - - 0 1", "c3d4", false},
      {"en passant", "7k/8/8/3pP3/8/8/8/K7 w - d6 0 1", "e5d6", false},
      {"capture promotion", "k5br/6P1/8/8/8/8/8/K7 w - - 0 1", "g7h8q", false},
      {"quiet normal move", "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1", "e2e4",
       false},
    }};

    bool ok = true;
    for (const auto& test : tests)
    {
        Position  pos;
        StateInfo state{};
        if (pos.set(std::string(test.fen), false, &state))
        {
            std::cerr << "FAIL Atomic capture-futility eligibility " << test.name
                      << ": invalid fixture\n";
            ok = false;
            continue;
        }

        const Move move = UCI::to_move(pos, std::string(test.move));
        if (!move)
        {
            std::cerr << "FAIL Atomic capture-futility eligibility " << test.name
                      << ": move is not legal\n";
            ok = false;
            continue;
        }

        const bool actual = Search::atomic_capture_futility_eligible(pos, move);
        if (actual != test.expected)
        {
            std::cerr << "FAIL Atomic capture-futility eligibility " << test.name
                      << ": expected=" << test.expected << " actual=" << actual << '\n';
            ok = false;
        }
        else
            std::cout << "PASS Atomic capture-futility eligibility " << test.name
                      << " eligible=" << actual << '\n';
    }

    return ok;
}

bool expect_atomic_nnue_wide_sum() {
    constexpr Value Minimum = Value(VALUE_TB_LOSS_IN_MAX_PLY + 1);
    constexpr Value Maximum = Value(VALUE_TB_WIN_IN_MAX_PLY - 1);

    struct WideSumCase {
        std::string_view name;
        i32              rawPsqt;
        i32              rawPositional;
        Value            expected;
    };

    constexpr std::array<WideSumCase, 8> tests = {{
      {"positive boundary", i32(Maximum) * 16, 0, Maximum},
      {"positive clamp", i32(Maximum) * 16 + 16, 0, Maximum},
      {"negative boundary", i32(Minimum) * 16, 0, Minimum},
      {"negative clamp", i32(Minimum) * 16 - 16, 0, Minimum},
      {"full positive i32 components", std::numeric_limits<i32>::max(),
       std::numeric_limits<i32>::max(), Maximum},
      {"full negative i32 components", std::numeric_limits<i32>::min(),
       std::numeric_limits<i32>::min(), Minimum},
      {"opposite i32 limits", std::numeric_limits<i32>::max(), std::numeric_limits<i32>::min(),
       VALUE_ZERO},
      {"wide non-clamped sum", 1'000'000'000, -999'840'000, Value(10'000)},
    }};

    bool ok = true;
    for (const auto& test : tests)
    {
        const Value actual =
          Eval::Detail::atomic_nnue_value_from_raw(test.rawPsqt, test.rawPositional);
        if (actual != test.expected)
        {
            std::cerr << "FAIL Atomic NNUE wide sum " << test.name << ": expected=" << test.expected
                      << " actual=" << actual << '\n';
            ok = false;
        }
        else
            std::cout << "PASS Atomic NNUE wide sum " << test.name << " value=" << actual << '\n';
    }

    return ok;
}

bool expect_atomic_wide_cp_conversion() {
    struct WideCpCase {
        std::string_view name;
        Value            value;
    };

    constexpr std::array<WideCpCase, 7> tests = {{
      {"zero", VALUE_ZERO},
      {"positive pawn", Value(PawnValue)},
      {"negative pawn", Value(-PawnValue)},
      {"positive V3 component", Value(std::numeric_limits<i32>::max() / 16)},
      {"negative V3 component", Value(std::numeric_limits<i32>::min() / 16)},
      {"full positive Value", Value(std::numeric_limits<int>::max())},
      {"full negative Value", Value(std::numeric_limits<int>::min())},
    }};

    Position position;
    bool     ok = true;
    for (const auto& test : tests)
    {
        const int expected = int(i64(100) * int(test.value) / PawnValue);
        const int actual   = UCIEngine::to_cp(test.value, position);
        if (actual != expected)
        {
            std::cerr << "FAIL Atomic wide cp conversion " << test.name << ": expected=" << expected
                      << " actual=" << actual << '\n';
            ok = false;
        }
        else
            std::cout << "PASS Atomic wide cp conversion " << test.name << " cp=" << actual << '\n';
    }

    bool ordinaryDomainExact = true;
    for (int value = VALUE_TB_LOSS_IN_MAX_PLY + 1; value < VALUE_TB_WIN_IN_MAX_PLY; ++value)
        ordinaryDomainExact = ordinaryDomainExact
                           && UCIEngine::to_cp(Value(value), position) == 100 * value / PawnValue;
    if (!ordinaryDomainExact)
    {
        std::cerr << "FAIL Atomic cp conversion changed the ordinary evaluation domain\n";
        ok = false;
    }
    else
        std::cout << "PASS Atomic cp conversion is byte-for-byte integer-exact across the ordinary "
                     "evaluation domain\n";

    return ok;
}

bool expect_shared_search_history_baseline() {
    auto histories = std::make_unique<SharedHistories>(1);
    histories->clear_for_search(0, 1);

    const auto correction_matches = [](const auto& bundle) {
        return i16(bundle.pawn) == -6 && i16(bundle.minor) == -6 && i16(bundle.nonPawnWhite) == -6
            && i16(bundle.nonPawnBlack) == -6;
    };

    bool ok =
      correction_matches(histories->correctionHistory[0][WHITE])
      && correction_matches(
        histories->correctionHistory[histories->correctionHistory.get_size() - 1][BLACK])
      && i16(histories->pawnHistory[0][NO_PIECE][SQ_A1]) == -1262
      && i16(histories->pawnHistory[histories->pawnHistory.get_size() - 1][B_KING][SQ_H8]) == -1262;

    for (bool inCheck : {false, true})
        for (StatsType captures : {NoCaptures, Captures})
            ok &=
              i16(
                histories->continuationHistory[inCheck][captures][NO_PIECE][SQ_A1][NO_PIECE][SQ_A1])
                == -552
              && i16(
                   histories->continuationHistory[inCheck][captures][B_KING][SQ_H8][B_KING][SQ_H8])
                   == -552;

    if (!ok)
        std::cerr << "FAIL shared search histories: tuned baseline differs\n";
    else
        std::cout << "PASS shared search histories tuned baseline\n";
    return ok;
}

}  // namespace

int main() {
    Bitboards::init();
    Attacks::init();
    Position::init();

    bool ok = true;
    for (const auto& test : SeeCases)
        ok &= expect_exact(test);

    ok &= expect_special_moves_are_neutral();
    ok &= expect_atomic_wins();
    ok &= expect_atomic_gives_check();
    ok &= expect_non_orthodox_atomic_evasions();
    ok &= expect_atomic_capture_generation_prefilter();
    ok &= expect_state_info_layout_contract();
    ok &= expect_gives_check_matches_child();
    ok &= expect_nnue_index_list_bounds();
    ok &= expect_make_undo_state();
    ok &= expect_repetition_state();
    ok &= expect_rule50_state();
    ok &= expect_uci_move_notation();
    ok &= expect_atomic_move_count_thresholds();
    ok &= expect_atomic_null_move_reductions();
    ok &= expect_atomic_capture_futility_eligibility();
    ok &= expect_atomic_nnue_wide_sum();
    ok &= expect_atomic_wide_cp_conversion();
    ok &= expect_shared_search_history_baseline();

    if (!ok)
        return 1;

    constexpr usize TestCount =
      SeeCases.size() + 3 + 7 + 8 + 14 + 2 + 13 + 3 + 6 + 7 + 7 + 6 + 2 + 8 + 8 + 1;
    std::cout << "Atomic C++ unit tests passed: " << TestCount << "/" << TestCount << '\n';
    return 0;
}
