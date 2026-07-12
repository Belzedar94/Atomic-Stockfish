/*
  Atomic-Stockfish, a specialized Atomic Chess engine derived from Stockfish
  Copyright (C) 2004-2026 The Stockfish developers (see AUTHORS file)

  Atomic-Stockfish is free software: you can redistribute it and/or modify
  it under the terms of the GNU General Public License as published by
  the Free Software Foundation, either version 3 of the License, or
  (at your option) any later version.
*/

#include "atomic_notation.h"

#include <cassert>

#include "../bitboard.h"
#include "../movegen.h"
#include "../position.h"
#include "../uci_move.h"

namespace Stockfish::Atomic {
namespace {

enum class Notation {
    San,
    Lan
};

char piece_letter(PieceType type) {
    switch (type)
    {
    case KNIGHT :
        return 'N';
    case BISHOP :
        return 'B';
    case ROOK :
        return 'R';
    case QUEEN :
        return 'Q';
    case KING :
        return 'K';
    default :
        assert(type == PAWN);
        return '\0';
    }
}

std::string disambiguation(const Position& pos, Move move, Notation notation) {
    const Square    from = move.from_sq();
    const PieceType type = type_of(pos.moved_piece(move));

    if (notation == Notation::Lan)
        return UCI::square(from);

    if (type == PAWN)
        return pos.capture(move) ? std::string(1, char('a' + file_of(from))) : std::string{};

    Bitboard otherOrigins = 0;
    for (Move candidate : MoveList<LEGAL>(pos))
        if (candidate != move && candidate.to_sq() == move.to_sq()
            && candidate.type_of() == move.type_of() && type_of(pos.moved_piece(candidate)) == type)
            otherOrigins |= candidate.from_sq();

    if (!otherOrigins)
        return {};
    if (!(otherOrigins & file_bb(from)))
        return std::string(1, char('a' + file_of(from)));
    if (!(otherOrigins & rank_bb(from)))
        return std::string(1, char('1' + rank_of(from)));
    return UCI::square(from);
}

std::string suffix_after(Position& pos, Move move) {
    StateInfo state{};
    pos.do_move(move, state);

    const Color sideToMove    = pos.side_to_move();
    const bool  kingExploded  = !pos.has_king(sideToMove);
    const bool  inAtomicCheck = !kingExploded && pos.atomic_in_check(sideToMove);
    const bool  noLegalMoves  = kingExploded || MoveList<LEGAL>(pos).size() == 0;

    pos.undo_move(move);

    if (kingExploded || (inAtomicCheck && noLegalMoves))
        return "#";
    return inAtomicCheck ? "+" : "";
}

std::string format(Position& pos, Move move, Notation notation) {
    if (!move.is_ok() || !MoveList<LEGAL>(pos).contains(move))
        return {};

    const Square from = move.from_sq();
    const Square to   = move.to_sq();

    std::string result;
    if (move.type_of() == CASTLING)
        result = to > from ? "O-O" : "O-O-O";
    else
    {
        const PieceType type = type_of(pos.moved_piece(move));
        if (type != PAWN)
            result += piece_letter(type);

        result += disambiguation(pos, move, notation);
        result += pos.capture(move) ? "x" : notation == Notation::Lan ? "-" : "";
        result += UCI::square(to);

        if (move.type_of() == PROMOTION)
        {
            result += '=';
            result += piece_letter(move.promotion_type());
        }
    }

    result += suffix_after(pos, move);
    return result;
}

Move parse(Position& pos, std::string_view notation, Notation style) {
    if (notation.empty())
        return Move::none();

    Move match = Move::none();
    for (Move move : MoveList<LEGAL>(pos))
    {
        if (format(pos, move, style) != notation)
            continue;

        // A correctly disambiguated SAN/LAN string has exactly one legal
        // interpretation. Fail closed if a malformed position ever violates it.
        if (match != Move::none())
            return Move::none();
        match = move;
    }
    return match;
}

}  // namespace

std::string to_san(Position& pos, Move move) { return format(pos, move, Notation::San); }
std::string to_lan(Position& pos, Move move) { return format(pos, move, Notation::Lan); }

Move parse_san(Position& pos, std::string_view notation) {
    return parse(pos, notation, Notation::San);
}

Move parse_lan(Position& pos, std::string_view notation) {
    return parse(pos, notation, Notation::Lan);
}

}  // namespace Stockfish::Atomic
