#!/usr/bin/env python3
"""Add the temporary cut-off counters of the Atomic speed profile to an orthodox
Stockfish source tree (official-stockfish at the commit Atomic-Stockfish main is
based on), so that the two engines are measured with the same instrument.

    orthodox_patch.py ENGINE_SRC_DIR

Only counters: the search must not change (the bench signature is checked by the
workflow). Each anchor must be found exactly once, or the script fails.
"""
import sys
from pathlib import Path

src = Path(sys.argv[1])

HEADER = r'''
// TEMPORARY MEASUREMENT COUNTERS (the instrument of the Atomic speed profile)
#include <cstdio>
namespace Stockfish::Prof {
constexpr int K = 2, S = 2, ST = 6, RB = 9, CP = 2, CK = 2, SE = 3, DB = 6;
struct CutCell { unsigned long long count, waste, subtree, searchedBefore, capsBefore, emittedCapsBefore; };
CutCell Cut[K][S][ST][RB][CP][CK][SE][DB];
unsigned long long RankCount[K][S][DB][65], RankWaste[K][S][DB][65], RankSubtree[K][S][DB][65];
unsigned long long NodeLoopNodes[K][S][DB], NodeLoopCount[K][S][DB];
unsigned long long QCut[2][ST][RB][CP][SE];
unsigned long long MainCut, MainNonCut, MainNoMove, QsCut, Nodes;
inline int rank_bucket(int r) { return r <= 5 ? r - 1 : r <= 8 ? 5 : r <= 12 ? 6 : r <= 20 ? 7 : 8; }
inline int depth_bucket(int d) { return d <= 1 ? 0 : d == 2 ? 1 : d <= 4 ? 2 : d <= 7 ? 3 : d <= 11 ? 4 : 5; }
// Upstream stages: MAIN_TT 0, CAPTURE_INIT 1, GOOD_CAPTURE 2, QUIET_INIT 3, GOOD_QUIET 4,
// BAD_CAPTURE 5, BAD_QUIET 6, EVASION_TT 7, EVASION_INIT 8, EVASION 9, PROBCUT_TT 10,
// PROBCUT_INIT 11, PROBCUT 12, QSEARCH_TT 13, QCAPTURE_INIT 14, QCAPTURE 15.
inline int stage_class(int s) {
    switch (s) { case 1: case 8: case 11: case 14: return 0; case 2: case 15: return 1; case 4: return 2;
                 case 5: return 3; case 6: return 4; default: return 5; }
}
struct Dumper {
    ~Dumper() {
        const char* path = std::getenv("PROF_OUT");
        FILE* f = path ? std::fopen(path, "w") : stderr;
        if (!f) return;
        std::fprintf(f, "C MAIN_CUT_NODES %llu\nC MAIN_NONCUT_NODES %llu\nC MAIN_NOMOVE_NODES %llu\nC QS_CUT_NODES %llu\n",
                     MainCut, MainNonCut, MainNoMove, QsCut);
        for (int k = 0; k < K; ++k) for (int s = 0; s < S; ++s) for (int st = 0; st < ST; ++st)
        for (int rb = 0; rb < RB; ++rb) for (int cp = 0; cp < CP; ++cp) for (int ck = 0; ck < CK; ++ck)
        for (int se = 0; se < SE; ++se) for (int db = 0; db < DB; ++db) {
            const CutCell& c = Cut[k][s][st][rb][cp][ck][se][db];
            if (c.count)
                std::fprintf(f, "CUT %d %d %d %d %d %d %d %d %llu %llu %llu %llu %llu %llu\n", k, s, st, rb, cp, ck, se, db,
                             c.count, c.waste, c.subtree, c.searchedBefore, c.capsBefore, c.emittedCapsBefore);
        }
        for (int k = 0; k < K; ++k) for (int s = 0; s < S; ++s) for (int db = 0; db < DB; ++db) {
            std::fprintf(f, "LOOP %d %d %d %llu %llu\n", k, s, db, NodeLoopCount[k][s][db], NodeLoopNodes[k][s][db]);
            for (int r = 1; r <= 64; ++r)
                if (RankCount[k][s][db][r])
                    std::fprintf(f, "RANK %d %d %d %d %llu %llu %llu\n", k, s, db, r, RankCount[k][s][db][r],
                                 RankWaste[k][s][db][r], RankSubtree[k][s][db][r]);
        }
        for (int ic = 0; ic < 2; ++ic) for (int st = 0; st < ST; ++st) for (int rb = 0; rb < RB; ++rb)
        for (int cp = 0; cp < CP; ++cp) for (int se = 0; se < SE; ++se)
            if (QCut[ic][st][rb][cp][se])
                std::fprintf(f, "QCUT %d %d %d %d %d %llu\n", ic, st, rb, cp, se, QCut[ic][st][rb][cp][se]);
        if (f != stderr) std::fclose(f);
    }
} dumper;
}  // namespace Stockfish::Prof
'''

EDITS = {
    "movepick.h": [
        ("    void skip_quiet_moves();\n",
         "    void skip_quiet_moves();\n    int  prof_stage() const { return stage; }\n"),
    ],
    "search.cpp": [
        ('#include "ucioption.h"\n', '#include "ucioption.h"\n' + HEADER),
        ("    int moveCount = 0;\n\n    // Step 13.",
         "    int moveCount = 0;\n"
         "    const u64   prof_n0 = u64(nodes);\n"
         "    const Depth prof_depth = depth;\n"
         "    int prof_searched = 0, prof_capsSearched = 0, prof_capsEmitted = 0, prof_stage = -1;\n"
         "    u64 prof_nc = prof_n0;\n"
         "    bool prof_cut = false;\n\n    // Step 13."),
        ("        assert(move.is_ok());\n\n        if (move == excludedMove)\n            continue;\n",
         "        assert(move.is_ok());\n        prof_stage = mp.prof_stage();\n\n"
         "        if (move == excludedMove)\n            continue;\n"),
        ("        givesCheck = pos.gives_check(move);\n\n        // Calculate new depth for this move\n",
         "        givesCheck = pos.gives_check(move);\n        if (capture)\n            ++prof_capsEmitted;\n\n"
         "        // Calculate new depth for this move\n"),
        ("        // (*Scaler) Generally, higher singularBeta",
         "        prof_nc = u64(nodes);\n        // (*Scaler) Generally, higher singularBeta"),
        ("        // Step 16. Make the move\n        do_move(pos, move, st, givesCheck, ss);\n",
         "        // Step 16. Make the move\n        ++prof_searched;\n        if (capture)\n            ++prof_capsSearched;\n"
         "        do_move(pos, move, st, givesCheck, ss);\n"),
        ("                    assert(value >= beta);  // Fail high\n                    break;\n",
         "                    assert(value >= beta);  // Fail high\n"
         "                    {\n"
         "                        prof_cut = true;\n"
         "                        const int k = PvNode ? 0 : 1, s = 0;\n"
         "                        const int db = Prof::depth_bucket(prof_depth), rb = Prof::rank_bucket(moveCount);\n"
         "                        const int stc = Prof::stage_class(prof_stage), cp = capture ? 1 : 0, ck = givesCheck ? 1 : 0;\n"
         "                        const int se = pos.see_ge(move, 1) ? 2 : pos.see_ge(move, 0) ? 1 : 0;\n"
         "                        const u64 now = u64(nodes);\n"
         "                        auto& c = Prof::Cut[k][s][stc][rb][cp][ck][se][db];\n"
         "                        ++c.count; c.waste += prof_nc - prof_n0; c.subtree += now - prof_n0;\n"
         "                        c.searchedBefore += prof_searched - 1; c.capsBefore += prof_capsSearched - cp;\n"
         "                        c.emittedCapsBefore += prof_capsEmitted - cp;\n"
         "                        const int rr = std::min(moveCount, 64);\n"
         "                        ++Prof::RankCount[k][s][db][rr]; Prof::RankWaste[k][s][db][rr] += prof_nc - prof_n0;\n"
         "                        Prof::RankSubtree[k][s][db][rr] += now - prof_n0;\n"
         "                        ++Prof::MainCut;\n"
         "                    }\n"
         "                    break;\n"),
        ("    // Step 21. Check for mate and stalemate\n",
         "    {\n"
         "        const int k = PvNode ? 0 : 1, s = 0, db = Prof::depth_bucket(prof_depth);\n"
         "        ++Prof::NodeLoopCount[k][s][db]; Prof::NodeLoopNodes[k][s][db] += u64(nodes) - prof_n0;\n"
         "        if (!prof_cut) { if (moveCount) ++Prof::MainNonCut; else ++Prof::MainNoMove; }\n"
         "    }\n"
         "    // Step 21. Check for mate and stalemate\n"),
        ("        assert(move.is_ok());\n\n        if (!pos.legal(move))\n            continue;\n\n        givesCheck = pos.gives_check(move);\n",
         "        assert(move.is_ok());\n        const int prof_qstage = mp.prof_stage();\n\n"
         "        if (!pos.legal(move))\n            continue;\n\n        givesCheck = pos.gives_check(move);\n"),
        ("                else\n                    break;  // Fail high\n",
         "                else\n"
         "                {\n"
         "                    const int ic = ss->inCheck ? 1 : 0, stc = Prof::stage_class(prof_qstage);\n"
         "                    const int rb = Prof::rank_bucket(moveCount), cp = capture ? 1 : 0;\n"
         "                    const int se = pos.see_ge(move, 1) ? 2 : pos.see_ge(move, 0) ? 1 : 0;\n"
         "                    ++Prof::QCut[ic][stc][rb][cp][se]; ++Prof::QsCut;\n"
         "                    break;  // Fail high\n"
         "                }\n"),
    ],
}

for name, edits in EDITS.items():
    p = src / name
    text = p.read_text()
    for old, new in edits:
        n = text.count(old)
        if n != 1:
            print(f"{name}: anchor found {n} times:\n{old}")
            sys.exit(1)
        text = text.replace(old, new)
    p.write_text(text)
    print(f"{name}: {len(edits)} edits")
