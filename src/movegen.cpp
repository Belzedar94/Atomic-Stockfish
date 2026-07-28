/*
  Stockfish, a UCI chess playing engine derived from Glaurung 2.1
  Copyright (C) 2004-2026 The Stockfish developers (see AUTHORS file)

  Stockfish is free software: you can redistribute it and/or modify
  it under the terms of the GNU General Public License as published by
  the Free Software Foundation, either version 3 of the License, or
  (at your option) any later version.

  Stockfish is distributed in the hope that it will be useful,
  but WITHOUT ANY WARRANTY; without even the implied warranty of
  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
  GNU General Public License for more details.

  You should have received a copy of the GNU General Public License
  along with this program.  If not, see <http://www.gnu.org/licenses/>.
*/

#include "movegen.h"

#include <cassert>
#include <initializer_list>

#include "attacks.h"
#include "bitboard.h"
#include "position.h"

#if defined(USE_AVX512ICL)
    #include <array>
    #include <algorithm>
    #include <immintrin.h>
#endif

namespace Stockfish {

namespace {

#if defined(USE_AVX512ICL)

template<Direction offset>
inline Move* splat_pawn_moves(Move* moveList, Bitboard to_bb) {
    assert(popcount(to_bb) <= 8);  // <= 8 pawns per side

    const __m128i toSquares =
      _mm_cvtepi8_epi16(_mm512_castsi512_si128(_mm512_maskz_compress_epi8(to_bb, AllSquares)));
    const __m128i fromSquares = _mm_subs_epi16(toSquares, _mm_set1_epi16(offset));
    const __m128i moves       = _mm_or_si128(_mm_slli_epi16(fromSquares, Move::FromSqShift),
                                             _mm_slli_epi16(toSquares, Move::ToSqShift));

    _mm_storeu_si128(reinterpret_cast<__m128i*>(moveList), moves);
    return moveList + popcount(to_bb);
}

inline Move* splat_moves(Move* moveList, Square from, Bitboard to_bb) {
    assert(popcount(to_bb) <= 32);  // Q can attack up to 27 squares

    const __m512i fromVec = _mm512_set1_epi16(Move(from, SQUARE_ZERO).raw());
    const __m512i toSquares =
      _mm512_cvtepi8_epi16(_mm512_castsi512_si256(_mm512_maskz_compress_epi8(to_bb, AllSquares)));
    const __m512i moves = _mm512_or_si512(fromVec, _mm512_slli_epi16(toSquares, Move::ToSqShift));

    _mm512_storeu_si512(moveList, moves);
    return moveList + popcount(to_bb);
}

#else

template<Direction offset>
inline Move* splat_pawn_moves(Move* moveList, Bitboard to_bb) {
    while (to_bb)
    {
        Square to   = pop_lsb(to_bb);
        *moveList++ = Move(to - offset, to);
    }
    return moveList;
}

inline Move* splat_moves(Move* moveList, Square from, Bitboard to_bb) {
    while (to_bb)
        *moveList++ = Move(from, pop_lsb(to_bb));
    return moveList;
}

#endif

template<GenType Type, Direction D, bool Enemy>
Move* make_promotions(Move* moveList, [[maybe_unused]] Square to) {

    constexpr bool all = Type == EVASIONS || Type == NON_EVASIONS;

    if constexpr (Type == CAPTURES || all)
        *moveList++ = Move::make<PROMOTION>(to - D, to, QUEEN);

    if constexpr ((Type == CAPTURES && Enemy) || (Type == QUIETS && !Enemy) || all)
    {
        *moveList++ = Move::make<PROMOTION>(to - D, to, ROOK);
        *moveList++ = Move::make<PROMOTION>(to - D, to, BISHOP);
        *moveList++ = Move::make<PROMOTION>(to - D, to, KNIGHT);
    }

    return moveList;
}


template<Color Us, GenType Type>
Move* generate_pawn_moves(const Position& pos,
                          Move*           moveList,
                          Bitboard        target,
                          Bitboard        kingAttacks) {

    constexpr Color     Them     = ~Us;
    constexpr Bitboard  TRank7BB = (Us == WHITE ? Rank7BB : Rank2BB);
    constexpr Bitboard  TRank3BB = (Us == WHITE ? Rank3BB : Rank6BB);
    constexpr Direction Up       = pawn_push(Us);
    constexpr Direction UpRight  = (Us == WHITE ? NORTH_EAST : SOUTH_WEST);
    constexpr Direction UpLeft   = (Us == WHITE ? NORTH_WEST : SOUTH_EAST);

    const Bitboard emptySquares = ~pos.pieces();
    const Bitboard enemies      = Type == EVASIONS ? pos.checkers()
                                : Type == CAPTURES ? target
                                                   : pos.pieces(Them);

    Bitboard pawnsOn7    = pos.pieces(Us, PAWN) & TRank7BB;
    Bitboard pawnsNotOn7 = pos.pieces(Us, PAWN) & ~TRank7BB;

    // Single and double pawn pushes, no promotions
    if constexpr (Type != CAPTURES)
    {
        Bitboard b1 = shift<Up>(pawnsNotOn7) & emptySquares;
        Bitboard b2 = shift<Up>(b1 & TRank3BB) & emptySquares;

        if constexpr (Type == EVASIONS)  // Consider only blocking squares
        {
            b1 &= target;
            b2 &= target;
        }

        moveList = splat_pawn_moves<Up>(moveList, b1);
        moveList = splat_pawn_moves<Up + Up>(moveList, b2);
    }

    // Promotions and underpromotions
    if (pawnsOn7)
    {
        Bitboard b1 = shift<UpRight>(pawnsOn7) & enemies;
        Bitboard b2 = shift<UpLeft>(pawnsOn7) & enemies;
        Bitboard b3 = shift<Up>(pawnsOn7) & emptySquares;

        if constexpr (Type == EVASIONS)
            b3 &= target;

        while (b1)
            moveList = make_promotions<Type, UpRight, true>(moveList, pop_lsb(b1));

        while (b2)
            moveList = make_promotions<Type, UpLeft, true>(moveList, pop_lsb(b2));

        while (b3)
            moveList = make_promotions<Type, Up, false>(moveList, pop_lsb(b3));
    }

    // Standard and en passant captures
    if constexpr (Type == CAPTURES || Type == EVASIONS || Type == NON_EVASIONS)
    {
        Bitboard b1 = shift<UpRight>(pawnsNotOn7) & enemies;
        Bitboard b2 = shift<UpLeft>(pawnsNotOn7) & enemies;

        moveList = splat_pawn_moves<UpRight>(moveList, b1);
        moveList = splat_pawn_moves<UpLeft>(moveList, b2);

        if (pos.ep_square() != SQ_NONE)
        {
            assert(rank_of(pos.ep_square()) == relative_rank(Us, RANK_6));

            if constexpr (Type == CAPTURES)
                if (kingAttacks & pos.ep_square())
                    return moveList;

            // An en passant capture cannot resolve a discovered check
            if (Type == EVASIONS && (target & (pos.ep_square() + Up)))
                return moveList;

            b1 = pawnsNotOn7 & Attacks::attacks_bb<PAWN>(pos.ep_square(), Them);

            assert(b1);

            while (b1)
                *moveList++ = Move::make<EN_PASSANT>(pop_lsb(b1), pos.ep_square());
        }
    }

    return moveList;
}


template<Color Us, PieceType Pt>
Move* generate_moves(const Position& pos, Move* moveList, Bitboard target) {

    static_assert(Pt != KING && Pt != PAWN, "Unsupported piece type in generate_moves()");

    Bitboard bb = pos.pieces(Us, Pt);

    while (bb)
    {
        Square   from = pop_lsb(bb);
        Bitboard b    = Attacks::attacks_bb<Pt>(from, pos.pieces()) & target;

        moveList = splat_moves(moveList, from, b);
    }

    return moveList;
}


template<Color Us, GenType Type>
Move* generate_all(const Position& pos, Move* moveList) {

    static_assert(Type != LEGAL, "Unsupported type in generate_all()");

    const Square   ksq         = pos.square<KING>(Us);
    const Bitboard kingAttacks = Attacks::attacks_bb<KING>(ksq);
    Bitboard       target;

    // Skip generating non-king moves when in double check
    if (Type != EVASIONS || !more_than_one(pos.checkers()))
    {
        target = Type == EVASIONS     ? Attacks::between_bb(ksq, lsb(pos.checkers()))
               : Type == NON_EVASIONS ? ~pos.pieces(Us)
               : Type == CAPTURES     ? pos.pieces(~Us) & ~kingAttacks
                                      : ~pos.pieces();  // QUIETS

        moveList = generate_pawn_moves<Us, Type>(pos, moveList, target, kingAttacks);
        moveList = generate_moves<Us, KNIGHT>(pos, moveList, target);
        moveList = generate_moves<Us, BISHOP>(pos, moveList, target);
        moveList = generate_moves<Us, ROOK>(pos, moveList, target);
        moveList = generate_moves<Us, QUEEN>(pos, moveList, target);
    }

    Bitboard b = kingAttacks & (Type == EVASIONS ? ~pos.pieces(Us) : target);

    moveList = splat_moves(moveList, ksq, b);

    if ((Type == QUIETS || Type == NON_EVASIONS) && pos.can_castle(Us & ANY_CASTLING))
        for (CastlingRights cr : {Us & KING_SIDE, Us & QUEEN_SIDE})
            if (!pos.castling_impeded(cr) && pos.can_castle(cr))
                *moveList++ = Move::make<CASTLING>(ksq, pos.castling_rook_square(cr));

    return moveList;
}

// Atomic check evasions. Orthodox EVASIONS cannot describe them: the checker
// can be removed by exploding any of its neighbours rather than by capturing it,
// the king may not capture at all, and a square attacked by a slider is still a
// refuge if it sits next to the enemy king, whose capture would explode the
// attacker too. MultiVariant-Stockfish generated exactly this small, almost
// legal set (movegen.cpp:739-821 of variant_sf_10) instead of enumerating every
// pseudo-legal move and filtering it. The result stays a superset of the legal
// evasions - Position::legal() remains the authority - but a far smaller one.
template<Color Us>
Move* generate_atomic_evasions(const Position& pos, Move* moveList) {

    constexpr Color     Them     = ~Us;
    constexpr Direction Up       = pawn_push(Us);
    constexpr Direction UpRight  = (Us == WHITE ? NORTH_EAST : SOUTH_WEST);
    constexpr Direction UpLeft   = (Us == WHITE ? NORTH_WEST : SOUTH_EAST);
    constexpr Bitboard  TRank7BB = (Us == WHITE ? Rank7BB : Rank2BB);
    constexpr Bitboard  TRank3BB = (Us == WHITE ? Rank3BB : Rank6BB);

    const Square   ksq          = pos.square<KING>(Us);
    const Square   theirKsq     = pos.square<KING>(Them);
    const Bitboard ourRing      = Attacks::attacks_bb<KING>(ksq);
    const Bitboard theirRing    = Attacks::attacks_bb<KING>(theirKsq);
    const Bitboard emptySquares = ~pos.pieces();
    const Bitboard pawnsOn7     = pos.pieces(Us, PAWN) & TRank7BB;
    const Bitboard pawnsNotOn7  = pos.pieces(Us, PAWN) & ~TRank7BB;

    Bitboard checkers = pos.atomic_checkers();

    assert(checkers);

    // Captures whose blast removes every checker at once, or removes the enemy
    // king. A checker dies if the blast covers its square, so any of its
    // neighbours is a valid thing to capture. Never capture next to our own
    // king: that is a self-explosion.
    Bitboard target = pos.pieces(Them);
    Bitboard b      = checkers;

    while (b)
    {
        const Square s = pop_lsb(b);
        target &= Attacks::attacks_bb<KING>(s) | s;
    }

    target |= theirRing | theirKsq;
    target &= pos.pieces(Them) & ~ourRing;

    moveList = splat_pawn_moves<UpRight>(moveList, shift<UpRight>(pawnsNotOn7) & target);
    moveList = splat_pawn_moves<UpLeft>(moveList, shift<UpLeft>(pawnsNotOn7) & target);

    Bitboard p1 = shift<UpRight>(pawnsOn7) & target;
    Bitboard p2 = shift<UpLeft>(pawnsOn7) & target;

    while (p1)
        moveList = make_promotions<NON_EVASIONS, UpRight, true>(moveList, pop_lsb(p1));

    while (p2)
        moveList = make_promotions<NON_EVASIONS, UpLeft, true>(moveList, pop_lsb(p2));

    moveList = generate_moves<Us, KNIGHT>(pos, moveList, target);
    moveList = generate_moves<Us, BISHOP>(pos, moveList, target);
    moveList = generate_moves<Us, ROOK>(pos, moveList, target);
    moveList = generate_moves<Us, QUEEN>(pos, moveList, target);

    // En passant. The blast is centred on the destination, so it can remove a
    // checker or the enemy king just like any other capture; emit it once and
    // let legality decide, unless it would explode our own king.
    if (pos.ep_square() != SQ_NONE && !(ourRing & pos.ep_square()))
    {
        Bitboard ep = pawnsNotOn7 & Attacks::attacks_bb<PAWN>(pos.ep_square(), Them);

        while (ep)
            *moveList++ = Move::make<EN_PASSANT>(pop_lsb(ep), pos.ep_square());
    }

    // King steps. The king never captures in Atomic, so only empty squares, and
    // a square covered by a checking slider is still safe when it sits next to
    // the enemy king.
    Bitboard sliderAttacks = 0;
    Bitboard sliders       = checkers & ~pos.pieces(KNIGHT, PAWN);

    while (sliders)
    {
        const Square checksq = pop_lsb(sliders);
        sliderAttacks |=
          Attacks::attacks_bb(type_of(pos.piece_on(checksq)), checksq, pos.pieces() ^ ksq);
    }

    moveList =
      splat_moves(moveList, ksq, ourRing & emptySquares & ~(sliderAttacks & ~theirRing));

    // Double check: nothing but a king move or one of the blasts above helps.
    if (more_than_one(checkers))
        return moveList;

    // Blocking moves. Capturing the checker directly already came out above as
    // a blast capture, so only the squares strictly in between are left.
    const Bitboard between = Attacks::between_bb(ksq, lsb(checkers)) ^ lsb(checkers);

    if (!between)
        return moveList;

    Bitboard b1 = shift<Up>(pawnsNotOn7) & emptySquares;
    Bitboard b2 = shift<Up>(b1 & TRank3BB) & emptySquares;

    moveList = splat_pawn_moves<Up>(moveList, b1 & between);
    moveList = splat_pawn_moves<Up + Up>(moveList, b2 & between);

    Bitboard b3 = shift<Up>(pawnsOn7) & emptySquares & between;

    while (b3)
        moveList = make_promotions<NON_EVASIONS, Up, false>(moveList, pop_lsb(b3));

    moveList = generate_moves<Us, KNIGHT>(pos, moveList, between);
    moveList = generate_moves<Us, BISHOP>(pos, moveList, between);
    moveList = generate_moves<Us, ROOK>(pos, moveList, between);
    moveList = generate_moves<Us, QUEEN>(pos, moveList, between);

    return moveList;
}

}  // namespace


// <CAPTURES>     Generates all pseudo-legal captures plus queen promotions
// <QUIETS>       Generates all pseudo-legal non-captures and underpromotions
// <EVASIONS>     Generates all pseudo-legal check evasions
// <NON_EVASIONS> Generates all pseudo-legal captures and non-captures
//
// Returns a pointer to the end of the move list.
template<GenType Type>
Move* generate(const Position& pos, Move* moveList) {

    static_assert(Type != LEGAL, "Unsupported type in generate()");
    assert((Type == EVASIONS) == bool(pos.checkers()));

    Color us = pos.side_to_move();

    return us == WHITE ? generate_all<WHITE, Type>(pos, moveList)
                       : generate_all<BLACK, Type>(pos, moveList);
}

// Explicit template instantiations
template Move* generate<CAPTURES>(const Position&, Move*);
template Move* generate<QUIETS>(const Position&, Move*);
template Move* generate<NON_EVASIONS>(const Position&, Move*);

// generate<EVASIONS> generates the Atomic check evasion candidates
template<>
Move* generate<EVASIONS>(const Position& pos, Move* moveList) {

    return pos.side_to_move() == WHITE ? generate_atomic_evasions<WHITE>(pos, moveList)
                                       : generate_atomic_evasions<BLACK>(pos, moveList);
}

// generate<LEGAL> generates all the legal moves in the given position

template<>
Move* generate<LEGAL>(const Position& pos, Move* moveList) {

    if (pos.is_atomic_terminal())
        return moveList;

    Move* cur = moveList;

    moveList = pos.atomic_in_check(pos.side_to_move()) ? generate<EVASIONS>(pos, moveList)
                                                       : generate<NON_EVASIONS>(pos, moveList);
    while (cur != moveList)
        if (!pos.legal(*cur))
            *cur = *(--moveList);
        else
            ++cur;

    return moveList;
}

}  // namespace Stockfish
