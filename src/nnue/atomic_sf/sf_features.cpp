/*
  Atomic-Stockfish, a specialized Atomic Chess engine derived from Stockfish
  Copyright (C) 2004-2026 The Stockfish developers (see AUTHORS file)

  Atomic-Stockfish is free software: you can redistribute it and/or modify
  it under the terms of the GNU General Public License as published by
  the Free Software Foundation, either version 3 of the License, or
  (at your option) any later version.
*/

#include "sf_features.h"

#include <algorithm>
#include <array>
#include <cassert>
#include <utility>

#include "../../attacks.h"
#include "../../bitboard.h"
#include "../../position.h"

namespace Stockfish::Eval::NNUE::AtomicSF {

using namespace Attacks;

namespace {

// ---------------------------------------------------------------------------
// Tables removed from the Atomic fork in H8 and restored locally, so that the
// Legacy V1 code path stays untouched.

constexpr auto PawnPushOrAttacks = []() constexpr {
    std::array<std::array<Bitboard, SQUARE_NB>, COLOR_NB> attacks{};
    for (Square s1 = SQ_A1; s1 <= SQ_H8; ++s1)
    {
        attacks[WHITE][s1] = pawn_single_push_bb(WHITE, square_bb(s1)) | PseudoAttacks[WHITE][s1];
        attacks[BLACK][s1] = pawn_single_push_bb(BLACK, square_bb(s1)) | PseudoAttacks[BLACK][s1];
    }
    return attacks;
}();

struct RayPassTable {
    std::array<std::array<Bitboard, SQUARE_NB>, SQUARE_NB> bb{};

    RayPassTable() {
        for (PieceType pt : {BISHOP, ROOK})
            for (Square s1 = SQ_A1; s1 <= SQ_H8; ++s1)
                for (Square s2 = SQ_A1; s2 <= SQ_H8; ++s2)
                    if (sliding_attack(pt, s1, 0) & s2)
                        bb[s1][s2] = sliding_attack(pt, s1, 0)
                                   & (sliding_attack(pt, s2, square_bb(s1)) | s2);
    }
};

const RayPassTable RayPass;

inline Bitboard ray_pass_bb(Square s1, Square s2) { return RayPass.bb[s1][s2]; }

// ---------------------------------------------------------------------------
// Full_Threats index tables (verbatim upstream).

static constexpr int numValidTargets[PIECE_NB] = {0, 6, 10, 8, 8, 10, 0, 0,
                                                  0, 6, 10, 8, 8, 10, 0, 0};

struct HelperOffsets {
    int cumulativePieceOffset, cumulativeOffset;
};

constexpr std::array<Piece, 12> AllPieces = {
  W_PAWN, W_KNIGHT, W_BISHOP, W_ROOK, W_QUEEN, W_KING,
  B_PAWN, B_KNIGHT, B_BISHOP, B_ROOK, B_QUEEN, B_KING,
};

template<PieceType PT>
constexpr auto make_piece_indices_type() {
    static_assert(PT != PieceType::PAWN);
    std::array<std::array<u8, SQUARE_NB>, SQUARE_NB> out{};
    for (Square from = SQ_A1; from <= SQ_H8; ++from)
    {
        Bitboard attacks = PseudoAttacks[PT][from];
        for (Square to = SQ_A1; to <= SQ_H8; ++to)
            out[from][to] = constexpr_popcount(((1ULL << to) - 1) & attacks);
    }
    return out;
}

template<Piece P>
constexpr auto make_piece_indices_piece() {
    static_assert(type_of(P) == PieceType::PAWN);
    std::array<std::array<u8, SQUARE_NB>, SQUARE_NB> out{};
    constexpr Color C = color_of(P);
    for (Square from = SQ_A1; from <= SQ_H8; ++from)
    {
        Bitboard attacks = PawnPushOrAttacks[C][from];
        for (Square to = SQ_A1; to <= SQ_H8; ++to)
            out[from][to] = constexpr_popcount(((1ULL << to) - 1) & attacks);
    }
    return out;
}

constexpr auto index_lut2_array() {
    constexpr auto KNIGHT_ATTACKS = make_piece_indices_type<PieceType::KNIGHT>();
    constexpr auto BISHOP_ATTACKS = make_piece_indices_type<PieceType::BISHOP>();
    constexpr auto ROOK_ATTACKS   = make_piece_indices_type<PieceType::ROOK>();
    constexpr auto QUEEN_ATTACKS  = make_piece_indices_type<PieceType::QUEEN>();
    constexpr auto KING_ATTACKS   = make_piece_indices_type<PieceType::KING>();

    std::array<std::array<std::array<u8, SQUARE_NB>, SQUARE_NB>, PIECE_NB> indices{};

    indices[W_PAWN] = make_piece_indices_piece<W_PAWN>();
    indices[B_PAWN] = make_piece_indices_piece<B_PAWN>();

    indices[W_KNIGHT] = KNIGHT_ATTACKS;
    indices[B_KNIGHT] = KNIGHT_ATTACKS;
    indices[W_BISHOP] = BISHOP_ATTACKS;
    indices[B_BISHOP] = BISHOP_ATTACKS;
    indices[W_ROOK]   = ROOK_ATTACKS;
    indices[B_ROOK]   = ROOK_ATTACKS;
    indices[W_QUEEN]  = QUEEN_ATTACKS;
    indices[B_QUEEN]  = QUEEN_ATTACKS;
    indices[W_KING]   = KING_ATTACKS;
    indices[B_KING]   = KING_ATTACKS;

    return indices;
}

constexpr auto init_threat_offsets() {
    std::array<HelperOffsets, PIECE_NB>                    indices{};
    std::array<std::array<IndexType, SQUARE_NB>, PIECE_NB> offsets{};

    int cumulativeOffset = 0;
    for (Piece piece : AllPieces)
    {
        int pieceIdx              = piece;
        int cumulativePieceOffset = 0;

        for (Square from = SQ_A1; from <= SQ_H8; ++from)
        {
            offsets[pieceIdx][from] = cumulativePieceOffset;

            if (type_of(piece) != PAWN)
            {
                Bitboard attacks = PseudoAttacks[type_of(piece)][from];
                cumulativePieceOffset += constexpr_popcount(attacks);
            }
            else if (from >= SQ_A2 && from <= SQ_H7)
            {
                Bitboard attacks = (pieceIdx < 8) ? PawnPushOrAttacks[WHITE][from]
                                                  : PawnPushOrAttacks[BLACK][from];
                cumulativePieceOffset += constexpr_popcount(attacks);
            }
        }

        indices[pieceIdx] = {cumulativePieceOffset, cumulativeOffset};

        cumulativeOffset += numValidTargets[pieceIdx] * cumulativePieceOffset;
    }

    return std::pair{indices, offsets};
}

constexpr auto helper_offsets = init_threat_offsets().first;
constexpr auto offsets        = init_threat_offsets().second;

constexpr auto init_index_luts() {
    std::array<std::array<std::array<u32, 2>, PIECE_NB>, PIECE_NB> indices{};

    for (Piece attacker : AllPieces)
    {
        for (Piece attacked : AllPieces)
        {
            bool      enemy        = (attacker ^ attacked) == 8;
            PieceType attackerType = type_of(attacker);
            PieceType attackedType = type_of(attacked);

            int  map           = FullThreats::map[attackerType - 1][attackedType - 1];
            bool semi_excluded = attackerType == attackedType && (enemy || attackerType != PAWN);
            IndexType feature  = helper_offsets[attacker].cumulativeOffset
                              + (color_of(attacked) * (numValidTargets[attacker] / 2) + map)
                                  * helper_offsets[attacker].cumulativePieceOffset;

            bool excluded                  = map < 0;
            indices[attacker][attacked][0] = excluded ? FullThreats::Dimensions : feature;
            indices[attacker][attacked][1] =
              excluded || semi_excluded ? FullThreats::Dimensions : feature;
        }
    }

    return indices;
}

constexpr auto index_lut1 = init_index_luts();
constexpr auto index_lut2 = index_lut2_array();

static_assert(helper_offsets[B_KING].cumulativeOffset
                + numValidTargets[B_KING] * helper_offsets[B_KING].cumulativePieceOffset
              == int(FullThreats::Dimensions));

// ---------------------------------------------------------------------------
// A minimal board on which the upstream threat-update routine is replayed.

struct ThreatBoard {
    std::array<Piece, SQUARE_NB> board;
    Bitboard                     byType[PIECE_TYPE_NB];
    Bitboard                     byColor[COLOR_NB];

    explicit ThreatBoard(const Position& pos) :
        board(pos.piece_array()) {
        for (PieceType pt = NO_PIECE_TYPE; pt < PIECE_TYPE_NB; ++pt)
            byType[pt] = 0;
        byType[ALL_PIECES] = pos.pieces();
        for (PieceType pt = PAWN; pt <= KING; ++pt)
            byType[pt] = pos.pieces(pt);
        byColor[WHITE] = pos.pieces(WHITE);
        byColor[BLACK] = pos.pieces(BLACK);
    }

    Bitboard pieces() const { return byType[ALL_PIECES]; }
    Bitboard pieces(PieceType pt) const { return byType[pt]; }
    template<typename... PieceTypes>
    Bitboard pieces(PieceType pt, PieceTypes... pts) const {
        return byType[pt] | pieces(pts...);
    }
    Bitboard pieces(Color c, PieceType pt) const { return byColor[c] & byType[pt]; }
    Piece    piece_on(Square s) const { return board[s]; }

    void raw_put(Piece pc, Square s) {
        board[s] = pc;
        byType[ALL_PIECES] |= square_bb(s);
        byType[type_of(pc)] |= square_bb(s);
        byColor[color_of(pc)] |= square_bb(s);
    }

    void raw_remove(Square s) {
        const Piece pc = board[s];
        byType[ALL_PIECES] &= ~square_bb(s);
        byType[type_of(pc)] &= ~square_bb(s);
        byColor[color_of(pc)] &= ~square_bb(s);
        board[s] = NO_PIECE;
    }

    void put(Piece pc, Square s, DirtyThreats& dts) {
        raw_put(pc, s);
        update_piece_threats(pc, true, s, dts);
    }

    void remove(Square s, DirtyThreats& dts) {
        update_piece_threats(board[s], false, s, dts);
        raw_remove(s);
    }

    void move(Square from, Square to, DirtyThreats& dts) {
        const Piece    pc     = board[from];
        const Bitboard fromTo = square_bb(from) | square_bb(to);
        update_piece_threats(pc, false, from, dts, fromTo);
        raw_remove(from);
        raw_put(pc, to);
        update_piece_threats(pc, true, to, dts, fromTo);
    }

    static void add(DirtyThreats& dts, bool put, Piece pc, Piece threatened, Square s, Square ts) {
        dts.list.push_back({pc, threatened, s, ts, put});
    }

    static constexpr bool can_slider_threat(Piece pc, Piece slider) {
        return type_of(pc) != QUEEN || type_of(slider) == QUEEN;
    }

    // Upstream Position::update_piece_threats<true> (non-AVX512ICL branch).
    void update_piece_threats(Piece         pc,
                              bool          putPiece,
                              Square        s,
                              DirtyThreats& dts,
                              Bitboard      noRaysContaining = ~Bitboard(0)) const {
        const Bitboard occupied     = pieces();
        const Bitboard rookQueens   = pieces(ROOK, QUEEN);
        const Bitboard bishopQueens = pieces(BISHOP, QUEEN);
        const Bitboard rAttacks     = attacks_bb<ROOK>(s, occupied);
        const Bitboard bAttacks     = attacks_bb<BISHOP>(s, occupied);
        const Bitboard occupiedNoK  = occupied ^ pieces(KING);

        Bitboard sliders = (rookQueens & rAttacks) | (bishopQueens & bAttacks);

        auto process_sliders = [&](bool addDirectAttacks) {
            while (sliders)
            {
                Square sliderSq = pop_lsb(sliders);
                Piece  slider   = piece_on(sliderSq);

                const Bitboard ray        = ray_pass_bb(sliderSq, s);
                const Bitboard discovered = ray & (rAttacks | bAttacks) & occupiedNoK;

                assert(!more_than_one(discovered));
                if (discovered && (ray & noRaysContaining) != noRaysContaining)
                {
                    const Square threatenedSq = lsb(discovered);
                    const Piece  threatenedPc = piece_on(threatenedSq);
                    if (can_slider_threat(threatenedPc, slider))
                        add(dts, !putPiece, slider, threatenedPc, sliderSq, threatenedSq);
                }

                if (addDirectAttacks && can_slider_threat(pc, slider))
                    add(dts, putPiece, slider, pc, sliderSq, s);
            }
        };

        if (type_of(pc) == KING)
        {
            process_sliders(false);
            return;
        }

        const Bitboard knights    = pieces(KNIGHT);
        const Bitboard whitePawns = pieces(WHITE, PAWN);
        const Bitboard blackPawns = pieces(BLACK, PAWN);

        Bitboard threatened       = attacks_bb(pc, s, occupied) & occupiedNoK;
        Bitboard incoming_threats = PseudoAttacks[KNIGHT][s] & knights;

        Bitboard pawnThreats = 0;
        if (type_of(pc) == PAWN)
        {
            Bitboard whiteAttacks = PawnPushOrAttacks[WHITE][s];
            Bitboard blackAttacks = PawnPushOrAttacks[BLACK][s];

            threatened |= (color_of(pc) == WHITE ? whiteAttacks : blackAttacks) & pieces(PAWN);

            pawnThreats = whiteAttacks & blackPawns;
            pawnThreats |= blackAttacks & whitePawns;
        }
        else
            pawnThreats = (attacks_bb<PAWN>(s, WHITE) & blackPawns)
                        | (attacks_bb<PAWN>(s, BLACK) & whitePawns);

        if (type_of(pc) == PAWN || type_of(pc) == KNIGHT || type_of(pc) == ROOK)
            incoming_threats |= pawnThreats;

        switch (type_of(pc))
        {
        case PAWN :
            threatened &= pieces(PAWN, KNIGHT, ROOK);
            break;
        case BISHOP :
        case ROOK :
            threatened &= pieces(PAWN, KNIGHT, BISHOP, ROOK);
            break;
        default :
            threatened &= occupiedNoK;
            break;
        }

        while (threatened)
        {
            Square threatenedSq = pop_lsb(threatened);
            Piece  threatenedPc = piece_on(threatenedSq);

            assert(threatenedSq != s);
            assert(threatenedPc);

            add(dts, putPiece, pc, threatenedPc, s, threatenedSq);
        }

        process_sliders(true);

        while (incoming_threats)
        {
            Square srcSq = pop_lsb(incoming_threats);
            Piece  srcPc = piece_on(srcSq);

            assert(srcSq != s);
            assert(srcPc != NO_PIECE);

            add(dts, putPiece, srcPc, pc, srcSq, s);
        }
    }
};

}  // namespace

// ---------------------------------------------------------------------------
// HalfKAv2_hm

void HalfKAv2Hm::append_changed_indices(Color             perspective,
                                        Square            ksq,
                                        const DirtyPiece& diff,
                                        RemovedIndexList& removed,
                                        AddedIndexList&   added) {
    removed.push_back(make_index(perspective, diff.from, diff.pc, ksq));

    if (diff.remove_sq != SQ_NONE)
        removed.push_back(make_index(perspective, diff.remove_sq, diff.remove_pc, ksq));

    if (diff.atomicBlast.size())
    {
        // The capturing (or promoted) piece explodes on its arrival square:
        // its add and remove cancel exactly in wrapping integer arithmetic.
        const Square arrival = diff.to != SQ_NONE ? diff.to : diff.add_sq;
        for (const auto& blasted : diff.atomicBlast)
            if (blasted.square != arrival)
                removed.push_back(make_index(perspective, blasted.square, blasted.pc, ksq));
        return;
    }

    if (diff.to != SQ_NONE)
        added.push_back(make_index(perspective, diff.to, diff.pc, ksq));

    if (diff.add_sq != SQ_NONE)
        added.push_back(make_index(perspective, diff.add_sq, diff.add_pc, ksq));
}

bool HalfKAv2Hm::requires_refresh(const DirtyPiece& diff, Color perspective) {
    const Piece perspectiveKing = make_piece(perspective, KING);
    if (diff.requiresRefresh || diff.pc == perspectiveKing)
        return true;

    return std::any_of(diff.atomicBlast.begin(), diff.atomicBlast.end(),
                       [perspectiveKing](const DirtyPiece::AtomicBlastPiece& blasted) {
                           return blasted.pc == perspectiveKing;
                       });
}

// ---------------------------------------------------------------------------
// Full_Threats

IndexType FullThreats::make_index(
  Color perspective, Piece attacker, Square from, Square to, Piece attacked, Square ksq) {
    const i8 orientation   = OrientTBL[ksq] ^ (56 * perspective);
    unsigned from_oriented = u8(from) ^ orientation;
    unsigned to_oriented   = u8(to) ^ orientation;

    i8       swap              = 8 * perspective;
    unsigned attacker_oriented = attacker ^ swap;
    unsigned attacked_oriented = attacked ^ swap;

    return index_lut1[attacker_oriented][attacked_oriented][from_oriented < to_oriented]
         + offsets[attacker_oriented][from_oriented]
         + index_lut2[attacker_oriented][from_oriented][to_oriented];
}

void FullThreats::append_active_indices(Color perspective, const Position& pos, IndexList& active) {
    const Square   ksq      = feature_king_square(pos, perspective);
    const Bitboard occupied = pos.pieces();

    const Bitboard pawns              = pos.pieces(PAWN);
    const Bitboard pawnTargets        = pos.pieces(PAWN, KNIGHT, ROOK);
    const Bitboard minorSliderTargets = pos.pieces(PAWN, KNIGHT, BISHOP, ROOK);
    const Bitboard queenTargets       = pos.pieces(PAWN, KNIGHT, BISHOP, ROOK, QUEEN);

    for (Color color : {WHITE, BLACK})
    {
        const Color c = Color(perspective ^ color);

        {
            const Piece    attacker = make_piece(c, PAWN);
            const Bitboard cPawns   = pos.pieces(c, PAWN);
            const Bitboard pushers  = pawn_single_push_bb(~c, pawns) & cPawns;

            auto process_pawn_attacks = [&](Bitboard attacks, Direction attkDir) {
                while (attacks)
                {
                    Square to       = pop_lsb(attacks);
                    Square from     = to - attkDir;
                    Piece  attacked = pos.piece_on(to);
                    IndexType index = make_index(perspective, attacker, from, to, attacked, ksq);
                    active.push_back_if_lt(index, Dimensions);
                }
            };

            if (c == WHITE)
            {
                process_pawn_attacks(shift<NORTH_EAST>(cPawns) & pawnTargets, NORTH_EAST);
                process_pawn_attacks(shift<NORTH_WEST>(cPawns) & pawnTargets, NORTH_WEST);
                process_pawn_attacks(shift<NORTH>(pushers), NORTH);
            }
            else
            {
                process_pawn_attacks(shift<SOUTH_WEST>(cPawns) & pawnTargets, SOUTH_WEST);
                process_pawn_attacks(shift<SOUTH_EAST>(cPawns) & pawnTargets, SOUTH_EAST);
                process_pawn_attacks(shift<SOUTH>(pushers), SOUTH);
            }
        }

        for (PieceType pt = KNIGHT; pt < KING; ++pt)
        {
            Piece    attacker = make_piece(c, pt);
            Bitboard bb       = pos.pieces(c, pt);

            while (bb)
            {
                Square   from    = pop_lsb(bb);
                Bitboard targets = pt == KNIGHT || pt == QUEEN ? queenTargets : minorSliderTargets;
                Bitboard attacks = attacks_bb(pt, from, occupied) & targets;

                while (attacks)
                {
                    Square    to       = pop_lsb(attacks);
                    Piece     attacked = pos.piece_on(to);
                    IndexType index    = make_index(perspective, attacker, from, to, attacked, ksq);
                    active.push_back_if_lt(index, Dimensions);
                }
            }
        }
    }
}

void FullThreats::append_changed_indices(Color               perspective,
                                         Square              ksq,
                                         const DirtyThreats& diff,
                                         IndexList&          removed,
                                         IndexList&          added) {
    for (const auto& dirty : diff.list)
    {
        auto& insert = dirty.add() ? added : removed;
        insert.push_back_if_lt(make_index(perspective, dirty.pc(), dirty.pc_sq(),
                                          dirty.threatened_sq(), dirty.threatened_pc(), ksq),
                               Dimensions);
    }
}

// ---------------------------------------------------------------------------

Square feature_king_square(const Position& pos, Color perspective) {
    return pos.has_king(perspective) ? pos.square<KING>(perspective) : SQ_A1;
}

void compute_dirty_threats(const Position& after, const DirtyPiece& dp, DirtyThreats& dts) {
    dts.list    = {};
    dts.refresh = false;

    // Every Atomic capture (en passant included) blasts at least the capturer.
    if (dp.atomicBlast.size() || dp.requiresRefresh)
    {
        dts.refresh = true;
        return;
    }

    ThreatBoard board(after);

    if (dp.remove_sq != SQ_NONE)
    {
        // Castling: the only non-capture with a removal. King dp.from -> dp.to,
        // rook dp.remove_sq -> dp.add_sq. Rebuild the board before the move,
        // then replay upstream's order (remove both, put both).
        assert(type_of(dp.pc) == KING && dp.add_sq != SQ_NONE);
        board.raw_remove(dp.to);
        board.raw_remove(dp.add_sq);
        board.raw_put(dp.pc, dp.from);
        board.raw_put(dp.remove_pc, dp.remove_sq);

        board.remove(dp.from, dts);
        board.remove(dp.remove_sq, dts);
        board.put(dp.pc, dp.to, dts);
        board.put(dp.add_pc, dp.add_sq, dts);
        return;
    }

    if (dp.add_sq != SQ_NONE)
    {
        // Quiet promotion: pawn dp.from disappears, dp.add_pc appears on add_sq.
        assert(dp.to == SQ_NONE && type_of(dp.pc) == PAWN);
        board.raw_remove(dp.add_sq);
        board.raw_put(dp.pc, dp.from);

        board.remove(dp.from, dts);
        board.put(dp.add_pc, dp.add_sq, dts);
        return;
    }

    // Quiet move.
    assert(dp.to != SQ_NONE);
    board.raw_remove(dp.to);
    board.raw_put(dp.pc, dp.from);
    board.move(dp.from, dp.to, dts);
}

}  // namespace Stockfish::Eval::NNUE::AtomicSF
