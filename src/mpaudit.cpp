/*
  Audit-only instrumentation for the R-A MovePicker rework. See mpaudit.h.
*/

#ifdef MP_AUDIT

    #include "mpaudit.h"

    #include <cstdio>

namespace Stockfish {
namespace MpAudit {

std::atomic<uint64_t> emits[STAGE_NB];
std::atomic<uint64_t> bestFrom[STAGE_NB];
std::atomic<uint64_t> cutFrom[STAGE_NB];
std::atomic<uint64_t> nodes;
std::atomic<uint64_t> rankSum[CLS_NB];
std::atomic<uint64_t> rankCnt[CLS_NB];
std::atomic<uint64_t> rankFirst[CLS_NB];
std::atomic<uint64_t> stageBest[STAGE_NB][CLS_NB];

namespace {

const char* stage_name(int s) {
    static const char* n[STAGE_NB] = {"1 MAIN_TT",      "CAPTURE_INIT", "RING_INIT",
                                      "2 RING_THREAT",  "3 GOOD_BLAST", "QUIET_INIT",
                                      "5 GOOD_QUIET",   "6 EQUAL_BLAST", "7 BAD_BLAST",
                                      "5b BAD_QUIET",   "PROBCUT_TT",   "PROBCUT_INIT",
                                      "PROBCUT",        "QSEARCH_TT",   "QCAPTURE_INIT",
                                      "QCAPTURE"};
    return n[s];
}

const char* cls_name(int c) {
    static const char* n[CLS_NB] = {"quiet", "quiet-ring", "capture", "en-passant", "promotion",
                                    "quiet-check"};
    return n[c];
}

}  // namespace

void record_best(int stage, int rank, int cls, bool cutoff) {
    if (stage < 0 || stage >= STAGE_NB || cls < 0 || cls >= CLS_NB || rank <= 0)
        return;
    nodes.fetch_add(1, std::memory_order_relaxed);
    bestFrom[stage].fetch_add(1, std::memory_order_relaxed);
    stageBest[stage][cls].fetch_add(1, std::memory_order_relaxed);
    if (cutoff)
        cutFrom[stage].fetch_add(1, std::memory_order_relaxed);
    rankSum[cls].fetch_add(uint64_t(rank), std::memory_order_relaxed);
    rankCnt[cls].fetch_add(1, std::memory_order_relaxed);
    if (rank == 1)
        rankFirst[cls].fetch_add(1, std::memory_order_relaxed);
}

void reset() {
    nodes.store(0);
    for (int s = 0; s < STAGE_NB; s++)
    {
        emits[s].store(0);
        bestFrom[s].store(0);
        cutFrom[s].store(0);
        for (int c = 0; c < CLS_NB; c++)
            stageBest[s][c].store(0);
    }
    for (int c = 0; c < CLS_NB; c++)
    {
        rankSum[c].store(0);
        rankCnt[c].store(0);
        rankFirst[c].store(0);
    }
}

void report() {
    uint64_t totEmit = 0, totBest = 0, totCut = 0;
    for (int s = 0; s < STAGE_NB; s++)
    {
        totEmit += emits[s].load();
        totBest += bestFrom[s].load();
        totCut += cutFrom[s].load();
    }

    std::printf("\n=== R-A fire-rate por etapa ===\n");
    std::printf("%-16s %14s %8s %12s %8s %12s %8s\n", "stage", "emitted", "%emit", "bestMove",
                "%best", "cutoffs", "%cut");
    for (int s = 0; s < STAGE_NB; s++)
    {
        const uint64_t e = emits[s].load(), b = bestFrom[s].load(), c = cutFrom[s].load();
        if (!e && !b)
            continue;
        std::printf("%-16s %14llu %7.3f%% %12llu %7.2f%% %12llu %7.2f%%\n", stage_name(s),
                    (unsigned long long) e, totEmit ? 100.0 * double(e) / double(totEmit) : 0.0,
                    (unsigned long long) b, totBest ? 100.0 * double(b) / double(totBest) : 0.0,
                    (unsigned long long) c, totCut ? 100.0 * double(c) / double(totCut) : 0.0);
    }
    std::printf("%-16s %14llu %8s %12llu %8s %12llu\n", "TOTAL",
                (unsigned long long) totEmit, "", (unsigned long long) totBest, "",
                (unsigned long long) totCut);

    std::printf("\n=== rango medio del bestMove por clase ===\n");
    std::printf("%-14s %14s %12s %10s\n", "class", "bestMoves", "mean rank", "%rank1");
    for (int c = 0; c < CLS_NB; c++)
    {
        const uint64_t n = rankCnt[c].load();
        if (!n)
            continue;
        std::printf("%-14s %14llu %12.4f %9.2f%%\n", cls_name(c), (unsigned long long) n,
                    double(rankSum[c].load()) / double(n),
                    100.0 * double(rankFirst[c].load()) / double(n));
    }

    std::printf("\n=== bestMove: etapa x clase ===\n");
    for (int s = 0; s < STAGE_NB; s++)
    {
        if (!bestFrom[s].load())
            continue;
        std::printf("%-16s", stage_name(s));
        for (int c = 0; c < CLS_NB; c++)
            std::printf(" %s=%llu", cls_name(c), (unsigned long long) stageBest[s][c].load());
        std::printf("\n");
    }
    std::fflush(stdout);
}

}  // namespace MpAudit
}  // namespace Stockfish

#endif  // MP_AUDIT
