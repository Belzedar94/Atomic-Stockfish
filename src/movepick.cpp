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

#include "movepick.h"

#include <cassert>
#include <limits>
#include <utility>

#include "attacks.h"
#include "bitboard.h"
#include "misc.h"
#include "position.h"
#include "tune.h"

#ifdef MP_AUDIT
    #include "mpaudit.h"
#endif

namespace Stockfish {

// R-A spins. Every default reproduces the classical MovePicker exactly.
int AtomicMpRing       = 0;
int AtomicMpRingBonus  = 8192;
int AtomicMpBlastOrder = 0;
int AtomicMpBlastScale = 7;
int AtomicMpCheckPred  = 0;
int AtomicMpTempo      = 0;
int AtomicCaptureTempo = 160;
int AtomicMpEpFix      = 0;

// R-C spins, consumed inside Position but owned by the same package.
int AtomicSeeExt       = 0;
int AtomicSeeThrPolicy = 0;
int AtomicBlastPawn7   = 250;
int AtomicBlastPawn6   = 80;

TUNE(SetRange(0, 2), AtomicMpRing);
TUNE(SetRange(0, 32768), AtomicMpRingBonus);
TUNE(SetRange(0, 2), AtomicMpBlastOrder);
TUNE(SetRange(1, 24), AtomicMpBlastScale);
TUNE(SetRange(0, 1), AtomicMpCheckPred);
TUNE(SetRange(0, 1), AtomicMpTempo);
TUNE(SetRange(0, 600), AtomicCaptureTempo);
TUNE(SetRange(0, 1), AtomicMpEpFix);
TUNE(SetRange(0, 1), AtomicSeeExt);
TUNE(SetRange(0, 1), AtomicSeeThrPolicy);
TUNE(SetRange(0, 900), AtomicBlastPawn7);
TUNE(SetRange(0, 900), AtomicBlastPawn6);

namespace {

enum Stages {
    // generate main search moves
    MAIN_TT,
    CAPTURE_INIT,
    RING_INIT,
    RING_THREAT,
    GOOD_CAPTURE,
    QUIET_INIT,
    GOOD_QUIET,
    EQUAL_CAPTURE,
    BAD_CAPTURE,
    BAD_QUIET,

    // generate probcut moves
    PROBCUT_TT,
    PROBCUT_INIT,
    PROBCUT,

    // generate qsearch moves
    QSEARCH_TT,
    QCAPTURE_INIT,
    QCAPTURE
};

#ifdef USE_AVX512
// Load the Move, and the ExtMove value, into all lanes of 512-bit registers
static void splat_extmove(const ExtMove& m, __m512i& move, __m512i& value) {
    move  = _mm512_set1_epi32(m.raw());
    value = _mm512_set1_epi32(m.value);
}

// Sorts up to 16 moves.
struct MoveSorter {
    static constexpr int MAX_ELEMENTS = 16;
    __m512i              sortedValues, sortedMoves;

    explicit MoveSorter(const ExtMove& first) {
        splat_extmove(first, sortedMoves, sortedValues);

        // Set the uninitialized move values to INT_MIN, so that they sort less than any other move
        sortedValues = _mm512_mask_set1_epi32(sortedValues, ~1, std::numeric_limits<int>::min());
    }

    void insert(const ExtMove& m) {
        __m512i move, value;
        splat_extmove(m, move, value);

        // Mask of all elements except the insertion point
        assert(m.value != std::numeric_limits<int>::min());
        const u16 expand = _kadd_mask16(_mm512_cmplt_epi32_mask(sortedValues, value), -1);

        sortedValues = _mm512_mask_expand_epi32(value, expand, sortedValues);
        sortedMoves  = _mm512_mask_expand_epi32(move, expand, sortedMoves);
    }

    void write_sorted(ExtMove* moves, isize count) const {
        static_assert(sizeof(ExtMove) == 8);
        assert(count <= MAX_ELEMENTS);

        // Because values and moves are stored separately, we need to reassemble the ExtMoves
        auto write = [&](int offset, const __m512i indices) {
            const __m512i extMoves = _mm512_permutex2var_epi32(sortedMoves, indices, sortedValues);
            const isize   storeCount = count - offset;

            if (storeCount > 0)
                _mm512_mask_storeu_epi64(moves + offset, (1 << storeCount) - 1, extMoves);
        };

        write(0, _mm512_setr_epi32(0, 16, 1, 17, 2, 18, 3, 19, 4, 20, 5, 21, 6, 22, 7, 23));
        write(8, _mm512_setr_epi32(8, 24, 9, 25, 10, 26, 11, 27, 12, 28, 13, 29, 14, 30, 15, 31));
    }
};
#endif

// Sort moves in descending order up to and including a given limit.
// The order of moves smaller than the limit is left unspecified.
void partial_insertion_sort(ExtMove* begin, ExtMove* end, int limit) {
    ExtMove *sortedEnd = begin, *p = begin + 1;

#ifdef USE_AVX512
    if (begin == end)
        return;

    MoveSorter sorter(*begin);
    for (; p < end; ++p)
    {
        if (p->value >= limit)
        {
            if (sortedEnd - begin + 1 >= MoveSorter::MAX_ELEMENTS)  // sorter full
                break;

            sorter.insert(*p);
            *p = *++sortedEnd;
        }
    }
    sorter.write_sorted(begin, sortedEnd - begin + 1);
    // Use scalar implementation for any remaining elements
#endif

    for (; p < end; ++p)
        if (p->value >= limit)
        {
            ExtMove tmp = *p, *q;
            *p          = *++sortedEnd;
            for (q = sortedEnd; q != begin && *(q - 1) < tmp; --q)
                *q = *(q - 1);
            *q = tmp;
        }
}

}  // namespace


// Constructors of the MovePicker class. As arguments, we pass information
// to decide which class of moves to emit, to help sorting the (presumably)
// good moves first, and how important move ordering is at the current node.

// MovePicker constructor for the main search and for the quiescence search
MovePicker::MovePicker(const Position&              p,
                       Move                         ttm,
                       Depth                        d,
                       const ButterflyHistory*      mh,
                       const LowPlyHistory*         lph,
                       const CapturePieceToHistory* cph,
                       const PieceToHistory**       ch,
                       const SharedHistories*       sh,
                       int                          pl) :
    pos(p),
    mainHistory(mh),
    lowPlyHistory(lph),
    captureHistory(cph),
    continuationHistory(ch),
    sharedHistory(sh),
    ttMove(ttm),
    depth(d),
    ply(pl) {

    stage = (depth > 0 ? MAIN_TT : QSEARCH_TT) + !(ttm && pos.pseudo_legal(ttm));
}

// MovePicker constructor for ProbCut: we generate captures with Static Exchange
// Evaluation (SEE) greater than or equal to the given threshold.
MovePicker::MovePicker(const Position& p, Move ttm, int th, const CapturePieceToHistory* cph) :
    pos(p),
    captureHistory(cph),
    ttMove(ttm),
    threshold(th) {
    assert(!pos.checkers());

    stage = PROBCUT_TT + !(ttm && pos.capture_stage(ttm) && pos.pseudo_legal(ttm));
}

// Assigns a numerical value to each move in a list, used for sorting.
// Captures are ordered by Most Valuable Victim (MVV), preferring captures
// with a good history. Quiets moves are ordered using the history tables.
template<GenType Type>
ExtMove* MovePicker::score(const MoveList<Type>& ml) {

    static_assert(Type == CAPTURES || Type == QUIETS, "Wrong type");

    [[maybe_unused]] Color us = pos.side_to_move();

    [[maybe_unused]] Bitboard threatByLesser[KING + 1];
    if constexpr (Type == QUIETS)
    {
        threatByLesser[PAWN]   = 0;
        threatByLesser[KNIGHT] = threatByLesser[BISHOP] = pos.attacks_by<PAWN>(~us);
        threatByLesser[ROOK] =
          pos.attacks_by<KNIGHT>(~us) | pos.attacks_by<BISHOP>(~us) | threatByLesser[KNIGHT];
        threatByLesser[QUEEN] = pos.attacks_by<ROOK>(~us) | threatByLesser[ROOK];
        threatByLesser[KING]  = 0;
    }

    ExtMove* it = cur;
    for (auto move : ml)
    {
        ExtMove& m = *it++;
        m          = move;

        const Square to = m.to_sq();
        const Piece  pc = pos.moved_piece(m);

        if constexpr (Type == CAPTURES)
        {
            const Piece capturedPiece = pos.piece_on(to);

            // Stage 3. The MVV proxy prices a blast by its victim alone, which
            // in Atomic is the one piece the move is guaranteed NOT to keep.
            // Level 1 replaces it by the signed delta, level 2 by the delta
            // measured against what the move invests.
            int material = AtomicMpBlastOrder == 0 ? 7 * int(PieceValue[capturedPiece])
                         : AtomicMpBlastOrder == 1 ? AtomicMpBlastScale * int(pos.blast_see(m))
                                                   : AtomicMpBlastScale * int(pos.blast_see_rel(m));

            // En passant lands on an EMPTY square, so the base scores its
            // victim as PieceValue[NO_PIECE] and files the whole class last.
            if (AtomicMpEpFix && AtomicMpBlastOrder == 0 && m.type_of() == EN_PASSANT)
                material = 7 * int(PieceValue[make_piece(~us, PAWN)]);

            m.value = (*captureHistory)[pc][to][type_of(capturedPiece)] + material;

            if (AtomicMpRing == 2 && ringTargets && ring_threat(m))
                m.value += AtomicMpRingBonus;
        }

        else if constexpr (Type == QUIETS)
        {
            const Square    from = m.from_sq();
            const PieceType pt   = type_of(pc);

            // histories
            m.value = 2 * (*mainHistory)[us][m.raw()];
            m.value += 2 * sharedHistory->pawn_entry(pos)[pc][to];
            m.value += (*continuationHistory[0])[pc][to];
            m.value += (*continuationHistory[1])[pc][to];
            m.value += (*continuationHistory[2])[pc][to];
            m.value += (*continuationHistory[3])[pc][to];
            m.value += (*continuationHistory[5])[pc][to];

            // bonus for checks. Stage 5: check_squares() is blind to king
            // checks and to discovered checks, and fires falsely when the kings
            // are adjacent -- the retention site already uses gives_check().
            const bool givesCheck =
              AtomicMpCheckPred ? pos.gives_check(m) : bool(pos.check_squares(pt) & to);
            m.value += (givesCheck && pos.see_ge(m, -75)) * 16384;

            // penalty for moving to a square threatened by a lesser piece
            // or bonus for escaping an attack by a lesser piece.
            int v = 20 * (bool(threatByLesser[pt] & from) - bool(threatByLesser[pt] & to));
            m.value += PieceValue[pt] * v;


            if (ply < LOW_PLY_HISTORY_SIZE)
                m.value += 8 * (*lowPlyHistory)[ply][m.raw()] / (1 + ply);
        }
    }
    return it;
}

// Stage 2. A blast centred on a square destroys every non-pawn around it, so
// the way to kill a king is to capture one of its neighbours. A move threatens
// the enemy ring when, from its destination, the moved piece attacks a piece
// standing next to the enemy king that we were not already attacking, and that
// capture would be legal for us -- our own king must stay outside that blast,
// which is exactly what connected kings deny (physics 3).
//
// This is the vector the classical stages cannot see at all: the lethal move
// captures nothing, so it is scored as an ordinary quiet.
bool MovePicker::ring_threat(Move m) const {

    const Square from = m.from_sq();
    const Square to   = m.to_sq();
    Piece        pc   = pos.moved_piece(m);

    // Kings cannot capture in Atomic, so a king can never carry the threat.
    if (type_of(pc) == KING)
        return false;

    if (m.type_of() == PROMOTION)
        pc = make_piece(color_of(pc), m.promotion_type());

    const Bitboard occ = (pos.pieces() ^ from) | to;

    return Attacks::attacks_bb(pc, to, occ) & ringTargets;
}

void MovePicker::init_ring_targets() {

    ringTargets = 0;

#ifndef MP_AUDIT
    if (!AtomicMpRing)
        return;
#endif

    if (depth <= 0)
        return;

    const Color  us      = pos.side_to_move();
    const Square ksqThem = pos.square<KING>(~us);
    const Square ksqUs   = pos.square<KING>(us);

    // Neighbours of the enemy king that we may legally detonate: enemy pieces
    // (the king itself cannot be captured) that do not sit in our own ring.
    Bitboard targets = Attacks::attacks_bb<KING>(ksqThem) & pos.pieces(~us) & ~pos.pieces(~us, KING)
                     & ~Attacks::attacks_bb<KING>(ksqUs) & ~square_bb(ksqUs);

    // Only threats the move CREATES are interesting; a target we already attack
    // is a threat the opponent is already answering.
    const Bitboard ours = pos.pieces(us) & ~pos.pieces(us, KING);

    while (targets)
    {
        const Square t = pop_lsb(targets);
        if (!(pos.attackers_to(t) & ours))
            ringTargets |= square_bb(t);
    }
}

// Returns the next move satisfying a predicate function.
// This never returns the TT move, as it was emitted before.
template<typename Pred>
Move MovePicker::select(Pred filter) {

    for (; cur < endCur; ++cur)
        if (*cur != ttMove && filter())
        {
#ifdef MP_AUDIT
            ++emitted;
            emitStage = stage;
            MpAudit::stage_emit(stage);
#endif
            return *cur++;
        }

    return Move::none();
}

// This is the most important method of the MovePicker class. We emit one
// new pseudo-legal move on every call until there are no more moves left,
// picking the move with the highest score from a list of generated moves.
Move MovePicker::next_move() {

    constexpr int goodQuietThreshold = -14000;
top:
    switch (stage)
    {

    case MAIN_TT :
    case QSEARCH_TT :
    case PROBCUT_TT :
#ifdef MP_AUDIT
        ++emitted;
        emitStage = stage;
        MpAudit::stage_emit(stage);
#endif
        ++stage;
        return ttMove;

    case CAPTURE_INIT :
    case PROBCUT_INIT :
    case QCAPTURE_INIT : {
        if (stage == CAPTURE_INIT)
            init_ring_targets();

        MoveList<CAPTURES> ml(pos);

        cur = endBadCaptures = moves;
        endCur = endCaptures = score<CAPTURES>(ml);

        partial_insertion_sort(cur, endCur, std::numeric_limits<int>::min());
        ++stage;
        goto top;
    }

    case RING_INIT : {
        endRingQuiets = endCaptures;

        // With the stage off, or with no fresh target next to the enemy king,
        // fall straight into the classical capture stage. cur and endCur are
        // still exactly what CAPTURE_INIT left behind.
        if (!AtomicMpRing || !ringTargets)
        {
            stage = GOOD_CAPTURE;
            goto top;
        }

        MoveList<QUIETS> ml(pos);

        cur          = endCaptures;
        endGenerated = score<QUIETS>(ml);
        quietsDone   = true;

        ExtMove* w = endCaptures;
        for (ExtMove* p = endCaptures; p < endGenerated; ++p)
            if (ring_threat(*p))
                std::swap(*w++, *p);
        endRingQuiets = w;

        partial_insertion_sort(endCaptures, endRingQuiets, std::numeric_limits<int>::min());
        partial_insertion_sort(endRingQuiets, endGenerated, -3560 * depth);

        cur    = endCaptures;
        endCur = endRingQuiets;
        ++stage;
        [[fallthrough]];
    }

    case RING_THREAT :
        if (select([]() { return true; }))
            return *(cur - 1);

        // Prepare the pointers to loop over the captures
        cur    = moves;
        endCur = endCaptures;

        ++stage;
        [[fallthrough]];

    case GOOD_CAPTURE :
        if (select([&]() {
                // Stage 6 frontier. An equal blast wins nothing and spends the
                // turn: without a recapture it just settles the board. Send it
                // behind the quiets instead of ahead of them.
                if (AtomicMpTempo && pos.capture(*cur))
                {
                    const int v = int(pos.blast_see(*cur));
                    if (v >= -AtomicCaptureTempo - 1 && v <= AtomicCaptureTempo)
                    {
                        std::swap(*endBadCaptures++, *cur);
                        return false;
                    }
                }
                if (pos.see_ge(*cur, -cur->value / 18))
                    return true;
                std::swap(*endBadCaptures++, *cur);
                return false;
            }))
            return *(cur - 1);

        ++stage;
        [[fallthrough]];

    case QUIET_INIT : {
        if (quietsDone)
        {
            cur    = endRingQuiets;
            endCur = endGenerated;
            ++stage;
            goto top;
        }

        MoveList<QUIETS> ml(pos);

        if (skipQuiets)
        {
            // Bulk pruning must retain quiet Atomic checks, but scoring every
            // quiet through all history tables would erase the speed benefit.
            // Compact only checks into the remaining move buffer instead.
            quietChecksOnly = true;
            endGenerated    = cur;
            for (Move move : ml)
                if (pos.gives_check(move))
                {
                    *endGenerated       = move;
                    endGenerated->value = 0;
                    ++endGenerated;
                }
            endCur = endGenerated;
        }
        else
        {
            endCur = endGenerated = score<QUIETS>(ml);
            partial_insertion_sort(cur, endCur, -3560 * depth);
        }

        ++stage;
        [[fallthrough]];
    }

    case GOOD_QUIET :
        if (select([&]() {
                return quietChecksOnly
                    || (cur->value > goodQuietThreshold && (!skipQuiets || pos.gives_check(*cur)));
            }))
            return *(cur - 1);

        // Prepare the pointers to loop over the bad captures
        cur              = moves;
        endCur           = endBadCaptures;
        endEqualCaptures = moves;

        // Stage 6. Split the demoted captures: the equal blasts run first, the
        // losing ones keep the tail.
        if (AtomicMpTempo)
        {
            for (ExtMove* p = moves; p < endBadCaptures; ++p)
            {
                const int v = int(pos.blast_see(*p));
                if (pos.capture(*p) && v >= -AtomicCaptureTempo - 1 && v <= AtomicCaptureTempo)
                    std::swap(*endEqualCaptures++, *p);
            }
            partial_insertion_sort(moves, endEqualCaptures, std::numeric_limits<int>::min());
            endCur = endEqualCaptures;
        }

        ++stage;
        [[fallthrough]];

    case EQUAL_CAPTURE :
        if (AtomicMpTempo && select([]() { return true; }))
            return *(cur - 1);

        // Prepare the pointers to loop over the losing blasts
        cur    = endEqualCaptures;
        endCur = endBadCaptures;

        ++stage;
        [[fallthrough]];

    case BAD_CAPTURE :
        if (select([]() { return true; }))
            return *(cur - 1);

        // Prepare the pointers to loop over quiets again
        cur    = quietsDone ? endRingQuiets : endCaptures;
        endCur = endGenerated;

        ++stage;
        [[fallthrough]];

    case BAD_QUIET :
        if (quietChecksOnly)
            return Move::none();

        return select([&]() {
            return cur->value <= goodQuietThreshold && (!skipQuiets || pos.gives_check(*cur));
        });

    case QCAPTURE :
        return select([]() { return true; });

    case PROBCUT :
        return select([&]() { return pos.atomic_wins(*cur) || pos.see_ge(*cur, threshold); });
    }

    assert(false);
    return Move::none();  // Silence warning
}

void MovePicker::skip_quiet_moves() { skipQuiets = true; }

}  // namespace Stockfish
