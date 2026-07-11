/*
  Atomic-Stockfish direct unit tests for explosive Static Exchange Evaluation.

  The expected values were derived from Fairy-Stockfish's blast_see() at
  fairy-stockfish/Fairy-Stockfish@fb78cb561aa01708338e35b3dc3b65a42149a3c4.
*/

#include <array>
#include <iostream>
#include <string>
#include <string_view>

#include "attacks.h"
#include "bitboard.h"
#include "movegen.h"
#include "position.h"
#include "search.h"
#include "types.h"
#include "uci_move.h"

using namespace Stockfish;

namespace {

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
    ok &= expect_make_undo_state();
    ok &= expect_repetition_state();
    ok &= expect_rule50_state();
    ok &= expect_uci_move_notation();
    ok &= expect_atomic_move_count_thresholds();

    if (!ok)
        return 1;

    constexpr usize TestCount = SeeCases.size() + 3 + 7 + 8 + 14 + 3 + 6;
    std::cout << "Atomic C++ unit tests passed: " << TestCount << "/" << TestCount << '\n';
    return 0;
}
