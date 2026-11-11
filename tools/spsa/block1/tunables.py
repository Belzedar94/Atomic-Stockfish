"""SPSA block 1 parameter table: the single source for the tuning branch.

P lists the 47 parameters this block adds as TUNABLE_INT, each as
(name, default, min, max, c_end, file, old, new): `old` is the literal text on
main, `new` the text on the tuning branch. Rows with file None share the
snippet of the previous row. EXISTING lists the 13 parameters main already
declares as TUNABLE_INT.

    python tunables.py <src>            # main -> tuning branch (what commit 1 did)
    python tunables.py <src> --inputs   # OpenBench spsa_inputs, one line per param

Every replacement must match exactly once (EXPECT overrides); the script
aborts otherwise. apply_results.py reuses this table in the other direction.
"""
import sys
from pathlib import Path

S = "search.cpp"
E = "evaluate.cpp"

P = [
    # ---- aspiration windows
    ("AspDeltaMsqDiv", 10588, 4000, 30000, 1000, S,
     "std::abs(rootMoves[pvIdx].meanSquaredScore) / 10588;",
     "std::abs(rootMoves[pvIdx].meanSquaredScore) / AspDeltaMsqDiv;"),
    # ---- hindsight depth adjustment
    ("HindsightEvalSum", 173, 50, 400, 18, S,
     "ss->staticEval + (ss - 1)->staticEval > 173)",
     "ss->staticEval + (ss - 1)->staticEval > HindsightEvalSum)"),
    # ---- razoring
    ("RazorBase", 465, 100, 1200, 50, S,
     "eval < alpha - 465 - 300 * depth * depth)",
     "eval < alpha - RazorBase - RazorDepthSq * depth * depth)"),
    ("RazorDepthSq", 300, 50, 800, 35, None, None, None),
    # ---- child futility (reverse futility)
    ("FutImprovingMult", 2934, 1000, 5000, 200, S,
     "- (2934 * improving + 343 * opponentWorsening) * futilityMult / 1024",
     "- (FutImprovingMult * improving + FutOppWorseningMult * opponentWorsening) * futilityMult / 1024"),
    ("FutOppWorseningMult", 343, 0, 1000, 50, None, None, None),
    ("FutReturnBetaWeight", 716, 400, 1000, 30, S,
     "return (716 * beta + 308 * eval) / 1024;",
     "return (FutReturnBetaWeight * beta + (1024 - FutReturnBetaWeight) * eval) / 1024;"),
    # ---- null move
    ("NmpEvalDepthMult", 14, 0, 40, 3, S,
     "ss->staticEval >= beta - 14 * depth - 45 * improving + 374 && !excludedMove",
     "ss->staticEval >= beta - NmpEvalDepthMult * depth - NmpImprovingMult * improving + NmpEvalBase\n        && !excludedMove"),
    ("NmpImprovingMult", 45, 0, 120, 8, None, None, None),
    ("NmpEvalBase", 374, 100, 700, 35, None, None, None),
    # ---- probcut
    ("ProbCutBase", 214, 80, 450, 20, S,
     "probCutBeta = beta + 214 - 59 * improving;",
     "probCutBeta = beta + ProbCutBase - ProbCutImprovingMult * improving;"),
    ("ProbCutImprovingMult", 59, 0, 150, 10, None, None, None),
    ("SmallProbCutBase", 428, 200, 800, 30, S,
     "probCutBeta = beta + 428;",
     "probCutBeta = beta + SmallProbCutBase;"),
    # ---- move loop: shallow-depth pruning
    ("LmrTtPvAdd", 1006, 0, 2500, 120, S,
     "            r += 1006;",
     "            r += LmrTtPvAdd;"),
    ("CaptFutHistMult", 131, 0, 300, 15, S,
     "+ PieceValue[capturedPiece] + 131 * captHist / 1024;",
     "+ PieceValue[capturedPiece] + CaptFutHistMult * captHist / 1024;"),
    ("SeeCaptDepthMult", 175, 60, 350, 15, S,
     "int margin = 175 * depth + captHist * 34 / 1024;",
     "int margin = SeeCaptDepthMult * depth + captHist * 34 / 1024;"),
    ("ContHistPruneMult", 4313, 1500, 8000, 300, S,
     "if (history < -4313 * depth)",
     "if (history < -ContHistPruneMult * depth)"),
    ("MainHistLmrWeight", 64, 16, 128, 6, S,
     "history += 64 * mainHistory[us][move.raw()] / 32;",
     "history += MainHistLmrWeight * mainHistory[us][move.raw()] / 32;"),
    ("ParentFutNoBest", 138, 30, 300, 14, S,
     "+ (40 + 138 * !bestMove + 117 * lmrDepth + 90 * (ss->staticEval > alpha))",
     "+ (40 + ParentFutNoBest * !bestMove + ParentFutLmrMult * lmrDepth\n                     + ParentFutAboveAlpha * (ss->staticEval > alpha))"),
    ("ParentFutLmrMult", 117, 40, 250, 10, None, None, None),
    ("ParentFutAboveAlpha", 90, 0, 200, 10, None, None, None),
    ("SeeQuietMult", 25, 5, 60, 3, S,
     "if (!pos.see_ge(move, -25 * lmrDepth * lmrDepth))",
     "if (!pos.see_ge(move, -SeeQuietMult * lmrDepth * lmrDepth))"),
    # ---- singular extensions
    ("DoubleExtNoTtCapture", 157, 0, 350, 18, S,
     "int doubleMargin = -3 + 201 * PvNode - 157 * !ttCapture - corrValAdj",
     "int doubleMargin = -3 + 201 * PvNode - DoubleExtNoTtCapture * !ttCapture - corrValAdj"),
    ("TripleExtBase", 72, 0, 200, 10, S,
     "int tripleMargin = 72 + 306 * PvNode - 188 * !ttCapture + 84 * ss->ttPv - corrValAdj",
     "int tripleMargin = TripleExtBase + 306 * PvNode - TripleExtNoTtCapture * !ttCapture\n                                 + 84 * ss->ttPv - corrValAdj"),
    ("TripleExtNoTtCapture", 188, 0, 400, 20, None, None, None),
    ("SingularDecidedCp", 1500, 500, 4000, 150, S,
     "if (std::abs(ttData.value) * 100 / PawnValue <= 1500)",
     "if (std::abs(ttData.value) * 100 / PawnValue <= SingularDecidedCp)"),
    # ---- LMR adjustments
    ("LmrTtPvBase", 2766, 1000, 4500, 180, S,
     "r -= 2766 + PvNode * 1017",
     "r -= LmrTtPvBase + PvNode * 1017"),
    ("LmrMoveCountMult", 62, 0, 150, 8, S,
     "r -= moveCount * 62;",
     "r -= moveCount * LmrMoveCountMult;"),
    ("LmrCutNode", 3995, 1500, 6000, 220, S,
     "r += 3995 + 1059 * !ttData.move;",
     "r += LmrCutNode + 1059 * !ttData.move;"),
    ("LmrTtCapture", 1039, 0, 2500, 110, S,
     "            r += 1039;",
     "            r += LmrTtCapture;"),
    ("LmrCutoffCntBase", 236, 0, 1000, 50, S,
     "r += 236 + 1079 * ((ss + 1)->cutoffCnt > 2) + 1143 * allNode;",
     "r += LmrCutoffCntBase + 1079 * ((ss + 1)->cutoffCnt > 2) + 1143 * allNode;"),
    ("LmrTtMoveReduce", 2016, 500, 4000, 170, S,
     "r = std::max(0, r - 2016);",
     "r = std::max(0, r - LmrTtMoveReduce);"),
    ("LmrCaptStatValueMult", 809, 300, 1500, 60, S,
     "ss->statScore = 809 * int(PieceValue[pos.captured_piece()]) / 128",
     "ss->statScore = LmrCaptStatValueMult * int(PieceValue[pos.captured_piece()]) / 128"),
    ("LmrStatScoreMult", 445, 150, 800, 35, S,
     "r -= ss->statScore * 445 / 4096;",
     "r -= ss->statScore * LmrStatScoreMult / 4096;"),
    ("LmrDeeperMargin", 52, 10, 120, 6, S,
     "value > bestValue + 52;",
     "value > bestValue + LmrDeeperMargin;"),
    ("LmrNoTtMove", 1085, 0, 2500, 110, S,
     "                r += 1085;",
     "                r += LmrNoTtMove;"),
    ("ReductionDeltaMult", 617, 200, 1200, 50, S,
     "return reductionScale - delta * 617 / rootDelta + !i * reductionScale * 194 / 512",
     "return reductionScale - delta * ReductionDeltaMult / rootDelta\n         + !i * reductionScale * ReductionNotImproving / 512"),
    ("ReductionNotImproving", 194, 50, 400, 18, None, None, None),
    # ---- quiescence
    ("QsSeeMargin", 74, 0, 200, 8, S,
     "if (!pos.see_ge(move, -74))",
     "if (!pos.see_ge(move, -QsSeeMargin))"),
    ("QsStandPatValueWeight", 467, 200, 900, 35, S,
     "bestValue = (467 * bestValue + 557 * beta) / 1024;",
     "bestValue = (QsStandPatValueWeight * bestValue + (1024 - QsStandPatValueWeight) * beta) / 1024;"),
    # ---- history bonuses
    ("StatBonusMult", 134, 50, 260, 12, S,
     "std::min(134 * depth - 79, 1572) + 382 * (bestMove == ttMove)",
     "std::min(StatBonusMult * depth - 79, 1572) + 382 * (bestMove == ttMove)"),
    ("StatMalusMult", 1005, 400, 1600, 60, S,
     "int malus = std::min(1005 * depth - 205, 2218);",
     "int malus = std::min(StatMalusMult * depth - 205, 2218);"),
    # ---- eval layer inherited from Fairy (Legacy Atomic V1 path)
    ("EvalScaleBase", 903, 600, 1300, 40, E,
     "const int scale = 903 + 32 * pos.count<PAWN>() + 32 * legacyNpm / 1024;",
     "const int scale = EvalScaleBase + EvalScalePawn * pos.count<PAWN>()\n                        + EvalScaleNpm * legacyNpm / 1024;"),
    ("EvalScalePawn", 32, 0, 80, 4, None, None, None),
    ("EvalScaleNpm", 32, 0, 80, 4, None, None, None),
    ("EvalEntertainment", 7, -20, 40, 3, E,
     "const int entertainment = deltaNpm <= BishopValue - KnightValue ? 7 : 0;",
     "const int entertainment = deltaNpm <= BishopValue - KnightValue ? EvalEntertainment : 0;"),
    ("EvalOptimismScale", 0, -200, 600, 40, E,
     "        v               = Value((blendedRaw / 16) * scale / 1024);\n",
     "        v               = Value((blendedRaw / 16) * scale / 1024);\n"
     "        // Search optimism (137 * avg / (|avg| + 81), side to move) was computed\n"
     "        // and discarded. 0 keeps the historical contract exactly.\n"
     "        v += optimism * EvalOptimismScale / 1024;\n"),
]

# Parameters already on main (registered in search.cpp); listed so the report
# and the OB spsa_inputs carry one table. (name, default, min, max, c_end)
EXISTING = [
    ("AtomicMcpBase", 7, 0, 20, 1.5),
    ("AtomicNmpBase", 6, 1, 12, 1),
    ("AtomicNmpDepthDiv", 4, 1, 8, 1),
    ("AtomicFutilityScale", 128, 32, 320, 10),
    ("AtomicCaptFutBase", 227, 0, 600, 25),
    ("AtomicCaptFutLmrMult", 244, 0, 600, 25),
    ("QsFutilityBase", 345, 0, 800, 30),
    ("SingularDepthMin", 8, 2, 14, 1),
    ("SingularMarginBase", 65, 0, 250, 8),
    ("SingularMarginTtPv", 83, 0, 250, 8),
    ("SingularMarginDiv", 48, 16, 150, 4),
    ("LmrLogScale", 2736, 1000, 6000, 150),
    ("LmrBaseOffset", 1049, 0, 3000, 120),
]


# Same literal in the data-generator search (training_search) and the
# playing search: both read the one parameter.
EXPECT = {"AspDeltaMsqDiv": 2}


def patch(SRC):
    files = {S: (SRC / S).read_text(encoding="utf-8"), E: (SRC / E).read_text(encoding="utf-8")}
    for name, _, _, _, _, f, old, new in P:
        if old is None:
            continue
        n = files[f].count(old)
        if n != EXPECT.get(name, 1):
            sys.exit(f"{name}: expected 1 match in {f}, got {n}: {old!r}")
        files[f] = files[f].replace(old, new)

    def block(fname):
        decl = [p for p in P if p[5] == fname or (p[5] is None and owner(p[0]) == fname)]
        width = max(len(p[0]) for p in decl)
        lines = ["", "// SPSA block 1 (tuning branch only). Defaults are the values main",
                 "// ships; a tune=yes build with these defaults searches the same tree.",
                 "namespace {"]
        for p in decl:
            lines.append(f"TUNABLE_INT {p[0]:<{width}} = {p[1]};")
        lines.append("}")
        lines.append("#ifdef ATOMIC_TUNE_SEARCH")
        for p in decl:
            lines.append(f"TUNE(SetRange({p[2]}, {p[3]}), {p[0]});")
        lines.append("#endif")
        return "\n".join(lines) + "\n"

    # search.cpp: append after the existing registrations
    anchor = "TUNE(SetRange(0, 3000), LmrBaseOffset);\n#endif\n"
    assert files[S].count(anchor) == 1
    files[S] = files[S].replace(anchor, anchor + block(S))

    # evaluate.cpp: include tune.h and declare after LegacyAtomicRoyalValue
    inc = '#include "position.h"\n'
    assert files[E].count(inc) == 1
    files[E] = files[E].replace(inc, inc + '#include "tune.h"\n')
    # declarations go at namespace Stockfish scope, before the anonymous namespace
    ns = "namespace Stockfish {\n"
    assert files[E].count(ns) == 1
    files[E] = files[E].replace(ns, ns + block(E))

    # the eval no longer discards optimism
    old_void = ("    // Modern Stockfish optimism belongs to its current orthodox net/search\n"
                "    // calibration and must not leak into the legacy Atomic V1 contract.\n"
                "    (void) optimism;\n\n")
    assert files[E].count(old_void) == 1
    files[E] = files[E].replace(old_void, "")

    for f, txt in files.items():
        (SRC / f).write_text(txt, encoding="utf-8", newline="\n")


def owner(name):
    # parameters without their own replacement row belong to the file of the
    # previous row that had one
    last = None
    for p in P:
        if p[5] is not None:
            last = p[5]
        if p[0] == name:
            return last
    raise KeyError(name)


def spsa_inputs():
    rows = []
    for name, d, lo, hi, c in EXISTING:
        rows.append((name, d, lo, hi, c))
    for name, d, lo, hi, c, *_ in P:
        rows.append((name, d, lo, hi, c))
    return rows


if __name__ == "__main__":
    if len(sys.argv) > 2 and sys.argv[2] == "--inputs":
        for name, d, lo, hi, c in spsa_inputs():
            print(f"{name},int,{d},{lo},{hi},{c},0.002")
    else:
        patch(Path(sys.argv[1]))
        print(f"patched {len(P)} new parameters")
