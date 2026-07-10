/*
  Focused rules-only API tests for Atomic notation and outcomes.

  The fixture IDs and expected SAN/result/material values mirror
  tests/bindings/atomic-fixtures.json. Additional cases lock LAN,
  disambiguation, checked squares, mate suffixes and optional draws.
*/

#include <array>
#include <deque>
#include <iostream>
#include <optional>
#include <string>
#include <string_view>
#include <vector>

#include "api/atomic_notation.h"
#include "api/atomic_outcome.h"
#include "api/atomic_fen.h"
#include "attacks.h"
#include "bitboard.h"
#include "position.h"
#include "uci_move.h"

using namespace Stockfish;

namespace {

class TestPosition {
   public:
    TestPosition(std::string_view fen, bool chess960 = false) :
        states(1) {
        valid = !position.set(std::string(fen), chess960, &states.back());
    }

    Move find(std::string_view uci) { return UCI::to_move(position, std::string(uci)); }

    bool push(std::string_view uci) {
        const Move move = find(uci);
        if (move == Move::none())
            return false;
        states.emplace_back();
        position.do_move(move, states.back());
        return true;
    }

    bool push_all(std::initializer_list<std::string_view> moves) {
        for (std::string_view move : moves)
            if (!push(move))
                return false;
        return true;
    }

    Position              position;
    std::deque<StateInfo> states;
    bool                  valid = false;
};

int Failures = 0;

void fail(std::string_view id, const std::string& detail) {
    std::cerr << "FAIL " << id << ": " << detail << '\n';
    ++Failures;
}

void pass(std::string_view id) { std::cout << "PASS " << id << '\n'; }

void expect(bool condition, std::string_view id, const std::string& detail) {
    if (condition)
        pass(id);
    else
        fail(id, detail);
}

struct NotationCase {
    std::string_view id;
    std::string_view fen;
    bool             chess960;
    std::string_view uci;
    std::string_view san;
    std::string_view lan;
};

void test_notation_fixtures() {
    constexpr std::array<NotationCase, 5> Cases = {{
      {"san.atomic960-castle", "7k/8/8/8/8/8/8/1RK5 w Q - 0 1", true, "c1b1", "O-O-O", "O-O-O"},
      {"san.en-passant", "7k/8/2N1b3/2ppP3/8/8/8/K7 w - d6 0 2", false, "e5d6", "exd6", "e5xd6"},
      {"san.explosion", "7k/8/8/2pBn3/3r4/2PQN3/8/K7 w - - 0 1", false, "d3d4", "Qxd4", "Qd3xd4"},
      {"san.promotion", "k5br/6P1/8/8/8/8/8/K7 w - - 0 1", false, "g7h8q", "gxh8=Q", "g7xh8=Q"},
      {"san.quiet", "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1", false, "g1f3",
       "Nf3", "Ng1-f3"},
    }};

    for (const auto& test : Cases)
    {
        TestPosition fixture(test.fen, test.chess960);
        const Move   move = fixture.find(test.uci);
        const bool   ok   = fixture.valid && move != Move::none()
                     && Atomic::to_san(fixture.position, move) == test.san
                     && Atomic::parse_san(fixture.position, test.san) == move
                     && Atomic::to_lan(fixture.position, move) == test.lan
                     && Atomic::parse_lan(fixture.position, test.lan) == move;
        expect(ok, test.id, "SAN/LAN format or round-trip differs from fixture contract");
    }
}

void test_disambiguation_and_suffixes() {
    struct Case {
        std::string_view id;
        std::string_view fen;
        std::string_view uci;
        std::string_view san;
    };

    constexpr std::array<Case, 6> Cases = {{
      {"san.disambiguation-file", "7k/8/8/8/8/8/8/KN3N2 w - - 0 1", "b1d2", "Nbd2"},
      {"san.disambiguation-rank", "7k/8/8/8/8/1N6/8/KN6 w - - 0 1", "b1d2", "N1d2"},
      {"san.disambiguation-square", "8/7k/1Q6/8/8/8/1Q3Q2/K7 w - - 0 1", "b2d4", "Qb2d4"},
      {"san.atomic-check", "rnbqkbnr/ppp1pppp/8/3p4/4P3/8/PPPP1PPP/RNBQKBNR w KQkq - 0 2", "f1b5",
       "Bb5+"},
      {"san.checkmate", "B7/Rk6/8/8/8/8/7Q/4K3 w - - 0 1", "h2b8", "Qb8#"},
      {"san.king-explosion-mate", "7k/7R/8/8/8/8/8/K7 w - - 0 1", "h7h8", "Rxh8#"},
    }};

    for (const auto& test : Cases)
    {
        TestPosition fixture(test.fen);
        const Move   move = fixture.find(test.uci);
        expect(fixture.valid && move != Move::none()
                 && Atomic::to_san(fixture.position, move) == test.san
                 && Atomic::parse_san(fixture.position, test.san) == move,
               test.id, "Atomic SAN disambiguation/check suffix differs");
    }

    TestPosition standardCastle("4k3/8/8/8/8/8/8/R3K2R w KQ - 0 1");
    const Move   castle = standardCastle.find("e1g1");
    expect(castle != Move::none() && Atomic::to_san(standardCastle.position, castle) == "O-O"
             && Atomic::parse_san(standardCastle.position, "O-O") == castle,
           "san.castle-kingside", "O-O did not round-trip");
}

void expect_outcome(std::string_view                        id,
                    std::string_view                        fen,
                    std::initializer_list<std::string_view> moves,
                    Atomic::Termination                     termination,
                    Value                                   value,
                    std::optional<Color>                    winner,
                    std::string_view                        result) {
    TestPosition fixture(fen);
    const bool   built  = fixture.valid && fixture.push_all(moves);
    const auto   actual = built ? Atomic::outcome(fixture.position) : Atomic::Outcome{};
    expect(built && actual.termination == termination && actual.value == value
             && actual.winner == winner && actual.result() == result,
           id, "termination/value/winner/result differs from fixture contract");
}

void test_outcome_fixtures() {
    expect_outcome("result.exploded-king",
                   "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1",
                   {"e2e4", "e7e5", "d1h5", "a7a6", "h5f7"}, Atomic::Termination::AtomicExplosion,
                   -VALUE_MATE, WHITE, "1-0");
    expect_outcome("result.exploded-king-priority", "3qk3/8/8/8/8/8/8/3QK3 w - - 0 1", {"d1d8"},
                   Atomic::Termination::AtomicExplosion, -VALUE_MATE, WHITE, "1-0");
    expect_outcome("result.mate", "BQ6/Rk6/8/8/8/8/8/4K3 b - - 0 1", {},
                   Atomic::Termination::Checkmate, -VALUE_MATE, WHITE, "1-0");
    expect_outcome("result.stalemate", "KQ6/Rk6/2B5/8/8/8/8/8 b - - 0 1", {},
                   Atomic::Termination::Stalemate, VALUE_DRAW, std::nullopt, "1/2-1/2");
}

void test_material_fixtures() {
    struct Case {
        std::string_view id;
        std::string_view fen;
        bool             white;
        bool             black;
    };
    constexpr std::array<Case, 4> Cases = {{
      {"material.k-v-k", "8/8/8/8/3K4/3k4/8/8 b - - 0 1", true, true},
      {"material.k-v-kp", "k7/p7/8/8/8/8/8/K7 w - - 0 1", true, false},
      {"material.k-v-kq", "k7/q7/8/8/8/8/8/K7 w - - 0 1", true, false},
      {"material.start", "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1", false, false},
    }};

    for (const auto& test : Cases)
    {
        TestPosition fixture(test.fen);
        expect(fixture.valid
                 && Atomic::has_insufficient_material(WHITE, fixture.position) == test.white
                 && Atomic::has_insufficient_material(BLACK, fixture.position) == test.black,
               test.id, "Atomic insufficient-material pair differs");
    }
}

void test_checked_squares_and_optional_draws() {
    TestPosition checked("rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1");
    const bool   checkedBuilt = checked.push_all({"e2e4", "d7d5", "f1b5"});
    expect(checkedBuilt && Atomic::checked_squares(checked.position) == square_bb(SQ_E8),
           "checked.atomic-bishop", "checked square is not e8");

    TestPosition adjacent("8/8/kK6/8/8/8/Q7/8 b - - 0 1");
    expect(adjacent.valid && Atomic::checked_squares(adjacent.position) == 0,
           "checked.adjacent-kings", "adjacent Atomic kings must be mutually immune");

    TestPosition fifty("r6k/8/8/8/8/8/8/R6K w - - 100 1");
    const auto   ignored = Atomic::outcome(fifty.position, false);
    const auto   claimed = Atomic::outcome(fifty.position, true);
    expect(ignored.termination == Atomic::Termination::Ongoing
             && claimed.termination == Atomic::Termination::FiftyMoveRule
             && claimed.value == VALUE_DRAW,
           "outcome.fifty-move-claim", "optional fifty-move policy differs");

    TestPosition repetition("rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1");
    const bool   repeated =
      repetition.push_all({"g1f3", "b8c6", "f3g1", "c6b8", "g1f3", "b8c6", "f3g1", "c6b8"});
    const auto repeatedOutcome = Atomic::outcome(repetition.position, true);
    expect(repeated && repeatedOutcome.termination == Atomic::Termination::ThreefoldRepetition,
           "outcome.threefold-claim", "optional repetition policy differs");
}

void test_fen_validation() {
    expect(Atomic::validate_fen("rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1")
             == Atomic::FEN_OK,
           "validation.start", "canonical Atomic FEN must validate");
    expect(Atomic::validate_fen("rnb+qkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1")
             == Atomic::FEN_INVALID_CHAR,
           "validation.atomic-plus-marker", "unsupported piece marker must be invalid char");
    expect(Atomic::validate_fen("rnbqkbnr/pppppppp/8/8/8/RNBQKBNR/PPPPPPPP/8 w KQkq - 0 1")
             == Atomic::FEN_INVALID_CASTLING_INFO,
           "validation.atomic-invalid-castling-rank",
           "sanitized castling rights must remain a validation error");
    expect(Atomic::validate_fen("7k/8/8/8/8/8/2PP4/1RK4q w Q - 0 1", true) == Atomic::FEN_OK,
           "validation.atomic960-castling", "valid Atomic960 castling FEN must pass");
    expect(Atomic::validate_fen("") == Atomic::FEN_EMPTY, "validation.empty",
           "empty FEN must retain the legacy result code");
}

}  // namespace

int main() {
    Bitboards::init();
    Attacks::init();
    Position::init();

    test_notation_fixtures();
    test_disambiguation_and_suffixes();
    test_outcome_fixtures();
    test_material_fixtures();
    test_checked_squares_and_optional_draws();
    test_fen_validation();

    if (Failures)
    {
        std::cerr << "Atomic API unit tests failed: " << Failures << '\n';
        return 1;
    }

    std::cout << "Atomic API unit tests passed\n";
    return 0;
}
