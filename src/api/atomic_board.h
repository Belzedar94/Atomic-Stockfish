/*
  Atomic-Stockfish, a specialized Atomic Chess engine derived from Stockfish
  Copyright (C) 2004-2026 The Stockfish developers (see AUTHORS file)

  Atomic-Stockfish is free software: you can redistribute it and/or modify
  it under the terms of the GNU General Public License as published by
  the Free Software Foundation, either version 3 of the License, or
  (at your option) any later version.
*/

#ifndef ATOMIC_BOARD_H_INCLUDED
#define ATOMIC_BOARD_H_INCLUDED

#include <cstdint>
#include <deque>
#include <memory>
#include <string>
#include <string_view>
#include <vector>

#include "../types.h"

namespace Stockfish {

class Position;
struct StateInfo;

namespace Atomic {

inline constexpr std::string_view StartFEN =
  "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1";

// Initializes immutable board, attack and Zobrist tables once per process.
// Every native, Python and WebAssembly surface uses this same entry point.
void initialize();

// Stateful rules-only board shared by the Python and JavaScript bindings.
// Search state and global engine options deliberately do not live here.
class Board final {
   public:
    explicit Board(std::string variant = "atomic", std::string fen = "", bool chess960 = false);
    ~Board();

    Board(const Board&)            = delete;
    Board& operator=(const Board&) = delete;
    Board(Board&&)                 = delete;
    Board& operator=(Board&&)      = delete;

    static bool        supports_variant(std::string_view variant);
    static std::string normalize_variant(std::string_view variant);
    static int         validate_fen(std::string_view fen, bool chess960 = false);

    const Position& position() const;
    Position&       position();

    std::string variant() const;
    bool        is_chess960() const;
    std::string fen() const;
    void        set_fen(const std::string& fen);
    void        reset();

    std::vector<std::string> legal_moves() const;
    std::vector<std::string> legal_moves_san();
    std::size_t              number_legal_moves() const;

    bool        push(const std::string& uciMove);
    bool        push_san(const std::string& sanMove);
    std::string pop();
    void        push_moves(const std::string& uciMoves);
    void        push_san_moves(const std::string& sanMoves);

    std::string san_move(const std::string& uciMove);
    std::string variation_san(const std::string& uciMoves, bool moveNumbers = true) const;
    std::string move_stack() const;

    bool turn() const;  // true for White, matching the historical JS API
    int  fullmove_number() const;
    int  halfmove_clock() const;
    int  game_ply() const;

    bool        is_check() const;
    std::string checked_pieces() const;
    bool        is_capture(const std::string& uciMove) const;
    bool        gives_check(const std::string& uciMove) const;

    bool        has_insufficient_material(bool white) const;
    bool        is_insufficient_material() const;
    bool        is_game_over(bool claimDraw = false) const;
    std::string result(bool claimDraw = false) const;

    std::uint64_t perft(int depth) const;
    std::string   to_string() const;
    std::string   to_verbose_string() const;

   private:
    void set_position_transactional(const std::string& fen);
    void do_move(Move move);

    std::string                            variantName;
    bool                                   chess960;
    std::unique_ptr<std::deque<StateInfo>> states;
    std::unique_ptr<Position>              pos;
    std::vector<Move>                      moveStack;
};

}  // namespace Atomic
}  // namespace Stockfish

#endif  // ATOMIC_BOARD_H_INCLUDED
