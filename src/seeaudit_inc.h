// Audit-only include, pulled into uci.cpp. Compares the pre-UB0 see_ge()
// verdict against the UB1 blast_see()-based verdict over walked positions.
// Not part of the engine's search path.

#ifndef SEEAUDIT_INC_H_INCLUDED
#define SEEAUDIT_INC_H_INCLUDED

#include <array>
#include <cstdio>
#include <deque>
#include <map>
#include <string>
#include <vector>

#include "misc.h"
#include "movegen.h"
#include "position.h"
#include "types.h"

namespace Stockfish {
namespace SeeAudit {

enum Cls {
    C_NORMAL_QUIET = 0,
    C_NORMAL_CAP,
    C_PROMO_CAP,
    C_PROMO_QUIET,
    C_EP,
    C_CASTLE,
    C_NB
};

inline const char* cls_name(int c) {
    static const char* n[] = {"NORMAL-quiet", "NORMAL-cap",  "PROMO-cap",
                              "PROMO-quiet",  "EN-PASSANT",  "CASTLING"};
    return n[c];
}

constexpr int  THR[]  = {-400, -200, -100, -50, 0, 50, 100};
constexpr int  NTHR   = 7;

struct Ex {
    std::string fen, mv;
    int         thr;
    bool        legacy, neu;
    int         val;
    bool        kc;
};

struct Acc {
    // [cls][thr][kingsConnected] -> counts
    long long seen[C_NB][NTHR][2]        = {};
    long long disagree[C_NB][NTHR][2]    = {};
    long long l1n0[C_NB][NTHR][2]        = {};  // legacy true  -> new false (demotion)
    long long l0n1[C_NB][NTHR][2]        = {};  // legacy false -> new true  (promotion)
    long long moves[C_NB][2]             = {};  // move census per class
    long long positions                  = 0;
    std::map<int, long long> epVals;            // blast_see distribution for EP
    std::map<int, long long> promoCapVals;      // ... for capturing promotions
    std::vector<Ex>          examples;
};

inline std::string mv_str(Move m) {
    std::string s;
    Square      f = m.from_sq(), t = m.to_sq();
    s += char('a' + int(file_of(f)));
    s += char('1' + int(rank_of(f)));
    s += char('a' + int(file_of(t)));
    s += char('1' + int(rank_of(t)));
    if (m.type_of() == PROMOTION)
        s += "  nbrq"[int(m.promotion_type())];
    return s;
}

inline int move_class(const Position& pos, Move m) {
    MoveType mt = m.type_of();
    if (mt == CASTLING)
        return C_CASTLE;
    if (mt == EN_PASSANT)
        return C_EP;
    if (mt == PROMOTION)
        return pos.capture(m) ? C_PROMO_CAP : C_PROMO_QUIET;
    return pos.capture(m) ? C_NORMAL_CAP : C_NORMAL_QUIET;
}

inline void audit_pos(const Position& pos, Acc& a, size_t exCap) {

    if (pos.count<KING>(WHITE) != 1 || pos.count<KING>(BLACK) != 1)
        return;

    const bool kc =
      distance<Square>(pos.square<KING>(WHITE), pos.square<KING>(BLACK)) <= 1;
    const int k = kc ? 1 : 0;

    a.positions++;

    for (const Move m : MoveList<LEGAL>(pos))
    {
        const int   cls = move_class(pos, m);
        const Value nv  = pos.blast_see(m);

        a.moves[cls][k]++;
        if (cls == C_EP)
            a.epVals[int(nv)]++;
        if (cls == C_PROMO_CAP)
            a.promoCapVals[int(nv)]++;

        for (int i = 0; i < NTHR; i++)
        {
            const bool L = pos.see_ge_legacy(m, THR[i]);
            const bool N = pos.see_ge(m, THR[i]);
            a.seen[cls][i][k]++;
            if (L != N)
            {
                a.disagree[cls][i][k]++;
                if (L && !N)
                    a.l1n0[cls][i][k]++;
                else
                    a.l0n1[cls][i][k]++;
                if (a.examples.size() < exCap)
                    a.examples.push_back({pos.fen(), mv_str(m), THR[i], L, N, int(nv), kc});
            }
        }
    }
}

// Exhaustive walk to `depth`, auditing every node on the way.
inline void dfs(Position& pos, int depth, std::deque<StateInfo>& st, Acc& a, size_t exCap) {
    audit_pos(pos, a, exCap);
    if (depth <= 0)
        return;
    for (const Move m : MoveList<LEGAL>(pos))
    {
        st.emplace_back();
        pos.do_move(m, st.back());
        dfs(pos, depth - 1, st, a, exCap);
        pos.undo_move(m);
        st.pop_back();
    }
}

// --- Material differential: blast_see() vs the real do_move() bookkeeping ---

struct MatAcc {
    long long checked[C_NB] = {};
    long long bad[C_NB]     = {};
    std::vector<std::string> examples;
    std::map<int, long long> deltaByCls[C_NB];  // (blast_see - expected) histogram
};

inline int material(const Position& pos, Color c) {
    return pos.count<PAWN>(c) * int(AtomicCapturePieceValue[make_piece(c, PAWN)])
         + pos.count<KNIGHT>(c) * int(AtomicCapturePieceValue[make_piece(c, KNIGHT)])
         + pos.count<BISHOP>(c) * int(AtomicCapturePieceValue[make_piece(c, BISHOP)])
         + pos.count<ROOK>(c) * int(AtomicCapturePieceValue[make_piece(c, ROOK)])
         + pos.count<QUEEN>(c) * int(AtomicCapturePieceValue[make_piece(c, QUEEN)]);
}

inline void material_pos(Position& pos, std::deque<StateInfo>& st, MatAcc& a, size_t exCap) {

    if (pos.count<KING>(WHITE) != 1 || pos.count<KING>(BLACK) != 1)
        return;

    const Color us = pos.side_to_move();

    for (const Move m : MoveList<LEGAL>(pos))
    {
        if (!pos.capture(m))
            continue;  // only captures carry blast semantics

        const int cls = move_class(pos, m);
        const int bUs = material(pos, us), bThem = material(pos, ~us);

        st.emplace_back();
        pos.do_move(m, st.back());
        const int aUs = material(pos, us), aThem = material(pos, ~us);
        const int ourKing = pos.count<KING>(us), theirKing = pos.count<KING>(~us);
        pos.undo_move(m);
        st.pop_back();

        int expected;
        if (ourKing == 0)
            expected = -VALUE_MATE;
        else if (theirKing == 0)
            expected = VALUE_MATE;
        else
            expected = (bThem - aThem) - (bUs - aUs) - 1;  // -1 = tempo token

        const int got = int(pos.blast_see(m));
        a.checked[cls]++;
        a.deltaByCls[cls][got - expected]++;
        if (got != expected)
        {
            a.bad[cls]++;
            if (a.examples.size() < exCap)
                a.examples.push_back(std::string(cls_name(cls)) + " " + mv_str(m) + " blast_see="
                                     + std::to_string(got) + " do_move=" + std::to_string(expected)
                                     + "  fen: " + pos.fen());
        }
    }
}

inline void material_dfs(Position&              pos,
                         int                    depth,
                         std::deque<StateInfo>& st,
                         MatAcc&                a,
                         size_t                 exCap) {
    material_pos(pos, st, a, exCap);
    if (depth <= 0)
        return;
    for (const Move m : MoveList<LEGAL>(pos))
    {
        st.emplace_back();
        pos.do_move(m, st.back());
        material_dfs(pos, depth - 1, st, a, exCap);
        pos.undo_move(m);
        st.pop_back();
    }
}

inline void material_report(const MatAcc& a) {
    std::printf("\n=== MATERIAL DIFFERENTIAL: blast_see() vs do_move() ===\n");
    std::printf("%-14s %14s %12s\n", "class", "captures", "mismatches");
    for (int c = 0; c < C_NB; c++)
        if (a.checked[c])
            std::printf("%-14s %14lld %12lld\n", cls_name(c), a.checked[c], a.bad[c]);
    std::printf("\n--- (blast_see - do_move) histogram per class ---\n");
    for (int c = 0; c < C_NB; c++)
    {
        if (!a.checked[c])
            continue;
        std::printf("%s:", cls_name(c));
        int n = 0;
        for (const auto& p : a.deltaByCls[c])
        {
            std::printf("  %d:%lld", p.first, p.second);
            if (++n > 12)
            {
                std::printf("  ...");
                break;
            }
        }
        std::printf("\n");
    }
    std::printf("\n--- mismatch examples ---\n");
    for (const auto& e : a.examples)
        std::printf("%s\n", e.c_str());
    std::fflush(stdout);
}

// Per-FEN receipt: every legal capture/promotion/EP with both verdicts.
inline void explain_fen(Position& pos, std::deque<StateInfo>& st) {
    std::printf("fen: %s\n", pos.fen().c_str());
    std::printf("%-8s %-13s %8s %8s %9s %7s   %s\n", "move", "class", "blast", "do_move",
                "7*PVvict", "gate0", "legacy|new per thr -400 -200 -100 -50 0 +50 +100");
    const Color us = pos.side_to_move();
    for (const Move m : MoveList<LEGAL>(pos))
    {
        const int cls = move_class(pos, m);
        if (cls == C_NORMAL_QUIET || cls == C_PROMO_QUIET || cls == C_CASTLE)
            continue;

        const int bUs = material(pos, us), bThem = material(pos, ~us);
        st.emplace_back();
        pos.do_move(m, st.back());
        const int aUs = material(pos, us), aThem = material(pos, ~us);
        const int ok = pos.count<KING>(us), tk = pos.count<KING>(~us);
        pos.undo_move(m);
        st.pop_back();
        const int expected = ok == 0      ? -VALUE_MATE
                           : tk == 0      ? VALUE_MATE
                                          : (bThem - aThem) - (bUs - aUs) - 1;

        // Exactly what MovePicker::score<CAPTURES> contributes besides captHist,
        // and the GOOD_CAPTURE gate threshold it implies when captHist == 0.
        const int pv7 = 7 * int(PieceValue[pos.piece_on(m.to_sq())]);
        std::printf("%-8s %-13s %8d %8d %9d %7d   ", mv_str(m).c_str(), cls_name(cls),
                    int(pos.blast_see(m)), expected, pv7, -pv7 / 18);
        for (int i = 0; i < NTHR; i++)
        {
            const bool L = pos.see_ge_legacy(m, THR[i]);
            const bool N = pos.see_ge(m, THR[i]);
            std::printf("%d%d%s ", int(L), int(N), L != N ? "*" : " ");
        }
        std::printf("\n");
    }
    std::fflush(stdout);
}

inline void report(const Acc& a) {
    std::printf("\n=== SEE VERDICT PARITY: legacy(pre-UB0) vs UB1 ===\n");
    std::printf("positions audited: %lld\n\n", a.positions);

    std::printf("--- move census (kc = kings connected, dist<=1) ---\n");
    std::printf("%-14s %12s %12s %12s\n", "class", "kc=0", "kc=1", "total");
    for (int c = 0; c < C_NB; c++)
        std::printf("%-14s %12lld %12lld %12lld\n", cls_name(c), a.moves[c][0], a.moves[c][1],
                    a.moves[c][0] + a.moves[c][1]);

    std::printf("\n--- disagreements per class x threshold (D=total, ->F demote, ->T promote) ---\n");
    std::printf("%-14s %6s %12s %10s %10s %10s\n", "class", "thr", "checks", "D", "L1->N0",
                "L0->N1");
    for (int c = 0; c < C_NB; c++)
        for (int i = 0; i < NTHR; i++)
        {
            long long s = a.seen[c][i][0] + a.seen[c][i][1];
            long long d = a.disagree[c][i][0] + a.disagree[c][i][1];
            if (!s)
                continue;
            std::printf("%-14s %6d %12lld %10lld %10lld %10lld\n", cls_name(c), THR[i], s, d,
                        a.l1n0[c][i][0] + a.l1n0[c][i][1], a.l0n1[c][i][0] + a.l0n1[c][i][1]);
        }

    std::printf("\n--- disagreements split by kings-connected ---\n");
    for (int c = 0; c < C_NB; c++)
    {
        long long d0 = 0, d1 = 0, s0 = 0, s1 = 0;
        for (int i = 0; i < NTHR; i++)
        {
            d0 += a.disagree[c][i][0];
            d1 += a.disagree[c][i][1];
            s0 += a.seen[c][i][0];
            s1 += a.seen[c][i][1];
        }
        if (s0 + s1)
            std::printf("%-14s kc=0 %lld/%lld   kc=1 %lld/%lld\n", cls_name(c), d0, s0, d1, s1);
    }

    std::printf("\n--- blast_see value histogram, EN-PASSANT ---\n");
    for (const auto& p : a.epVals)
        std::printf("  %8d : %lld\n", p.first, p.second);
    std::printf("--- blast_see value histogram, PROMO-cap (top buckets) ---\n");
    {
        int n = 0;
        for (const auto& p : a.promoCapVals)
        {
            std::printf("  %8d : %lld\n", p.first, p.second);
            if (++n > 40)
            {
                std::printf("  ... (%zu distinct buckets)\n", a.promoCapVals.size());
                break;
            }
        }
    }

    std::printf("\n--- examples ---\n");
    for (const auto& e : a.examples)
        std::printf("[%s] thr=%5d legacy=%d new=%d blast_see=%6d kc=%d  move=%s\n  fen: %s\n",
                    "x", e.thr, int(e.legacy), int(e.neu), e.val, int(e.kc), e.mv.c_str(),
                    e.fen.c_str());
    std::fflush(stdout);
}

}  // namespace SeeAudit
}  // namespace Stockfish

#endif
