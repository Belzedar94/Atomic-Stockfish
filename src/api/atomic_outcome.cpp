/*
  Atomic-Stockfish, a specialized Atomic Chess engine derived from Stockfish
  Copyright (C) 2004-2026 The Stockfish developers (see AUTHORS file)

  Atomic-Stockfish is free software: you can redistribute it and/or modify
  it under the terms of the GNU General Public License as published by
  the Free Software Foundation, either version 3 of the License, or
  (at your option) any later version.
*/

#include "atomic_outcome.h"

#include "../bitboard.h"
#include "../movegen.h"
#include "../position.h"

namespace Stockfish::Atomic {
namespace {

constexpr Bitboard DarkSquareBB = 0xAA55AA55AA55AA55ULL;

Outcome decisive(Termination termination, Value value, Color sideToMove) {
    return {termination, value,
            value > VALUE_ZERO ? std::optional<Color>{sideToMove}
                               : std::optional<Color>{~sideToMove}};
}

Outcome drawn(Termination termination) { return {termination, VALUE_DRAW, std::nullopt}; }

}  // namespace

std::string Outcome::result() const {
    if (!terminal())
        return "*";
    if (!winner)
        return "1/2-1/2";
    return *winner == WHITE ? "1-0" : "0-1";
}

bool has_insufficient_material(Color color, const Position& pos) {
    // A lone Atomic king has no winning mechanism. Pawns can promote and major
    // pieces can force a win, so either is immediately sufficient.
    if (pos.pieces(color, PAWN, ROOK, QUEEN))
        return false;

    const Bitboard allBishops  = pos.pieces(BISHOP);
    const Bitboard otherPieces = pos.pieces() ^ pos.pieces(KING) ^ allBishops;

    // A color-bound piece needs access to both square colors, or a non-bishop
    // helper of either color that can become an Atomic blast target.
    if (pos.pieces(color, BISHOP)
        && (((allBishops & DarkSquareBB) && (allBishops & ~DarkSquareBB)) || otherPieces))
        return false;

    // A lone knight cannot win K+N v K. Any second non-king piece (friendly or
    // enemy) can provide the additional mating/blast mechanism.
    if (pos.pieces(color, KNIGHT) && popcount(pos.pieces() ^ pos.pieces(KING)) >= 2)
        return false;

    return true;
}

Bitboard checked_squares(const Position& pos) {
    const Color sideToMove = pos.side_to_move();
    return pos.atomic_in_check(sideToMove) ? pos.pieces(sideToMove, KING) : Bitboard(0);
}

Outcome outcome(const Position& pos, bool claimDraw, int repetitionPly) {
    const Color sideToMove = pos.side_to_move();

    // An exploded king is authoritative even if the remaining material would
    // otherwise be insufficient. This ordering is part of the Fairy contract.
    if (!pos.has_king(sideToMove))
        return decisive(Termination::AtomicExplosion, -VALUE_MATE, sideToMove);
    if (!pos.has_king(~sideToMove))
        return decisive(Termination::AtomicExplosion, VALUE_MATE, sideToMove);

    if (has_insufficient_material(WHITE, pos) && has_insufficient_material(BLACK, pos))
        return drawn(Termination::InsufficientMaterial);

    if (!pos.has_legal_move())
        return pos.atomic_in_check(sideToMove)
               ? decisive(Termination::Checkmate, -VALUE_MATE, sideToMove)
               : drawn(Termination::Stalemate);

    if (claimDraw)
    {
        if (pos.rule50_count() >= 100)
            return drawn(Termination::FiftyMoveRule);
        if (pos.is_repetition(repetitionPly))
            return drawn(Termination::ThreefoldRepetition);
    }

    return {};
}

}  // namespace Stockfish::Atomic
