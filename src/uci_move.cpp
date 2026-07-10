/*
  Atomic-Stockfish, a specialized Atomic Chess engine derived from Stockfish
  Copyright (C) 2004-2026 The Stockfish developers (see AUTHORS file)

  Atomic-Stockfish is free software: you can redistribute it and/or modify
  it under the terms of the GNU General Public License as published by
  the Free Software Foundation, either version 3 of the License, or
  (at your option) any later version.
*/

#include "uci_move.h"

#include <algorithm>
#include <cctype>
#include <utility>

#include "movegen.h"
#include "position.h"

namespace Stockfish::UCI {

std::string square(Square s) { return std::string{char('a' + file_of(s)), char('1' + rank_of(s))}; }

std::string move(Move m, bool chess960) {
    if (m == Move::none())
        return "(none)";

    if (m == Move::null())
        return "0000";

    Square from = m.from_sq();
    Square to   = m.to_sq();

    if (m.type_of() == CASTLING && !chess960)
        to = make_square(to > from ? FILE_G : FILE_C, rank_of(from));

    std::string result = square(from) + square(to);

    if (m.type_of() == PROMOTION)
        result += " pnbrqk"[m.promotion_type()];

    return result;
}

std::string to_lower(std::string str) {
    std::transform(str.begin(), str.end(), str.begin(),
                   [](unsigned char c) { return static_cast<char>(std::tolower(c)); });

    return str;
}

Move to_move(const Position& pos, std::string str) {
    str = to_lower(std::move(str));

    for (const Move move : MoveList<LEGAL>(pos))
        if (str == UCI::move(move, pos.is_chess960()))
            return move;

    return Move::none();
}

}  // namespace Stockfish::UCI
