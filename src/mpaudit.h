/*
  Audit-only instrumentation for the R-A MovePicker rework. Compiled in only
  with -DMP_AUDIT, so the shipping binary never pays for it and the bench of
  the clean build stays the registration signature.

  It answers the two receipts the programme demands before any fleet is spent:
  the fire-rate of every stage (how often it emits, how often its move is the
  best move, how often it causes the cutoff) and the mean emission rank of the
  best move split by class.
*/

#ifndef MPAUDIT_H_INCLUDED
#define MPAUDIT_H_INCLUDED

#include <atomic>
#include <cstdint>

namespace Stockfish {
namespace MpAudit {

// Mirrors the Stages enum of movepick.cpp, plus a tail slot for qsearch and
// probcut stages that share the counter array.
constexpr int STAGE_NB = 16;

// Move classes for the best-move rank census.
enum Cls {
    CLS_QUIET = 0,
    CLS_RING_QUIET,   // quiet that threatens the enemy king ring
    CLS_CAPTURE,      // normal capture
    CLS_EP,
    CLS_PROMO,
    CLS_CHECK_QUIET,  // quiet that gives check
    CLS_NB
};

extern std::atomic<uint64_t> emits[STAGE_NB];
extern std::atomic<uint64_t> bestFrom[STAGE_NB];
extern std::atomic<uint64_t> cutFrom[STAGE_NB];
extern std::atomic<uint64_t> nodes;
extern std::atomic<uint64_t> rankSum[CLS_NB];
extern std::atomic<uint64_t> rankCnt[CLS_NB];
extern std::atomic<uint64_t> rankFirst[CLS_NB];
extern std::atomic<uint64_t> stageBest[STAGE_NB][CLS_NB];

inline void stage_emit(int stage) {
    if (stage >= 0 && stage < STAGE_NB)
        emits[stage].fetch_add(1, std::memory_order_relaxed);
}

void record_best(int stage, int rank, int cls, bool cutoff);
void reset();
void report();

}  // namespace MpAudit
}  // namespace Stockfish

#endif  // #ifndef MPAUDIT_H_INCLUDED
