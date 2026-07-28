/*
  SURVIVE50 — the anti-fortress subsystem of the atomic weak solve.

  WHAT THIS ANSWERS
  -----------------
  The df-pn prover in ``dfpn.cpp`` answers "can White force a win here". On a
  fortress it burns 10^8 nodes and returns UNKNOWN, because the defender's
  refutation is not a tree: it is a strategy that survives the fifty-move
  counter, and the counter is reset by every White capture and every White
  pawn move. There is no bounded tree to enumerate.

  This module answers the dual question exactly:

      tau(s) = the smallest halfmove clock from which Black has a certified
               strategy that avoids a White win.

  A root with entry clock c is refuted as a White win the moment we hold a
  certificate with tau(root) <= c.

  WHY tau IS A NUMBER AND NOT A GRAPH
  -----------------------------------
  The naive move is to search (position, clock) pairs. That multiplies the
  state space by 101 and is unaffordable: 100k base states at out-degree 20
  become 10.1M states and ~202M lifted edges — 1.6 GB of edge array alone.
  Instead the clock never enters the key. It is a VALUE attached to the base
  position, and the whole 101-layer structure is recovered by arithmetic.

  WHY THE GAME TERMINATES AT ALL
  ------------------------------
  Every White reset (capture or pawn move) restarts the horizon, so "Black
  only needs to survive 100-c plies" is false. The repair is specific to
  atomic. For a nonterminal state define

      R(s) = sum over pawns of remaining rank-steps to promotion
             + floor((N(s) - 2) / 2)            [N = piece count, kings included]

  Every pawn move drops the first term by at least one. Every capture in
  atomic removes at least the capturer and the captured, so it drops the
  second term by at least one. Therefore EVERY zeroing move strictly decreases
  R, while every quiet move leaves R untouched and advances the clock. The
  lexicographic measure

      M(s, h) = (R(s), 100 - h)

  strictly decreases on every nonterminal move, so the exact (position, clock)
  survival game is a well-founded DAG. That is what makes the fixed point
  below finite and the certificate in phase 3 checkable without unrolling a
  single cycle.

  The structural consequence used everywhere in this file: in the BASE graph,
  every cycle consists exclusively of quiet edges, because a zeroing edge
  strictly lowers R and R cannot come back up.

  NO REPETITION SHORTCUTS
  -----------------------
  v1 runs with ``repetition_shortcuts = forbidden``. A survival strategy that
  reaches the fifty-move horizon without ever invoking a repetition claim is
  valid under ANY move history, which removes the graph-history-interaction
  problem from this subsystem entirely rather than papering over it.

  THREE IMPLEMENTATIONS, ON PURPOSE
  ---------------------------------
    * ``reference_solve``  — phase 1. Exhaustive DP over the fully expanded
      (node, clock) state space. No tau, no rank, no monotonicity, no
      short-circuit evaluation. This is the oracle; it is allowed to be slow.
    * ``threshold_solve``  — phase 2 production. Reverse worklist over tau
      bounds on base positions. O(states + edges) memory.
    * ``threshold_solve_sweep`` — phase 2 second opinion. Rank-ordered clock
      sweep: the expanded DP again, but realised one clock layer at a time so
      it costs O(states) memory. It reaches the same answer by a different
      route and, unlike the worklist, it depends on R being correct — which
      makes disagreement between the two informative rather than merely fatal.

  All three must agree exactly. That is the kill gate for phase 2.
*/

#ifndef SURVIVE50_H_INCLUDED
#define SURVIVE50_H_INCLUDED

#include <cstdint>
#include <iosfwd>
#include <string>
#include <vector>

#include "position.h"

namespace Stockfish::Survive50 {

// Plies of the fifty-move counter at which the defender may claim. Identical
// to DFPN::FIFTY_MOVE_PLIES by construction: a one-ply disagreement between
// the two subsystems would invalidate every tau label in the database.
constexpr int FIFTY_MOVE_PLIES = 100;

// tau lives in [0, 100] for every state, plus one sentinel above the range.
//
//   0    structural fortress: Black holds from any clock, so it survives even
//        immediately after a White reset.
//   73   Black holds if it enters with clock >= 73.
//   100  only the already-available claim saves Black. An INTERIOR state is
//        auto-drawn at h = 100, so tau = 100 really means "Black is lost at
//        every clock at which it still has to move".
//   101  TAU_LOST. Reserved for terminal White wins, where mate takes
//        precedence over the counter and no clock whatsoever saves Black.
//        No interior state ever carries it.
constexpr int TAU_MIN  = 0;
constexpr int TAU_MAX  = FIFTY_MOVE_PLIES;
constexpr int TAU_LOST = FIFTY_MOVE_PLIES + 1;

// Identity of the statement. It travels inside every certificate.
constexpr const char* RULESET_ID          = "atomic-fide-claim-v1";
constexpr const char* CERTIFICATE_FORMAT  = "atomic-survival-threshold-v1";
constexpr const char* REPETITION_MODE     = "NO_REPETITION_SHORTCUTS";
// Identity of the CLAIM AUTOMATON: terminal beats the counter, so a mate on
// the hundredth reversible ply is a mate. Both verifiers refuse a certificate
// that names a different one.
constexpr const char* TERMINAL_PRECEDENCE_ID = "terminal-before-clock/1";
// Identity of the CANONICALISER, which decides which positions are the same
// position. Independent of the ruleset, but it moves keys, so it travels too.
constexpr int CANONICAL_VERSION = 2;

// ---------------------------------------------------------------------------
// First-class results (oracle 5.1, doc 18 §6.1)
// ---------------------------------------------------------------------------
//
// The one rule that must never be relaxed: a survival certificate REFUTES the
// boolean objective WHITE_WIN. It is not a proof of the independent objective
// BLACK_WIN, and a goal-specific disproof must never be cached as one. The
// enum exists so that conflation is a compile-time impossibility rather than a
// convention someone remembers.
enum class ProofResult : uint8_t {
    Unknown,
    ProvenWhiteWin,
    ProvenBlackWin,
    ProvenDraw50,
    ProvenDrawRepetition,
    ProvenDrawOther,
    DisprovedWhiteWin
};

const char* result_name(ProofResult result);

// ---------------------------------------------------------------------------
// The survival game graph
// ---------------------------------------------------------------------------
//
// Deliberately abstracted away from the chessboard. Phases 1 and 2 are pure
// graph algorithms and are tested on millions of tiny random instances before
// either of them is allowed near a position; phase 4 builds these graphs from
// the engine's own move generator.

enum class NodeType : uint8_t {
    Interior,          // Black still has to hold; the clock decides
    WhiteWinTerminal,  // White has won here, at every clock. Mate beats the counter.
    SurviveTerminal    // Black win, stalemate, or any other non-White-win end
};

struct Graph {
    struct Edge {
        uint32_t child   = 0;
        bool     zeroing = false;  // capture or pawn move: resets the counter
    };

    struct Node {
        NodeType type        = NodeType::Interior;
        bool     whiteToMove = true;
        // Set when this node's successor list is INCOMPLETE. At a White node
        // that is a universal proof obligation we cannot discharge, so tau is
        // pinned at TAU_MAX; at a Black node it is harmless, because one
        // surviving reply is enough and a known reply stays known.
        bool     openMoves   = false;
        // R(s). Quiet edges preserve it exactly, zeroing edges strictly lower
        // it. Only the sweep solver reads it; the worklist does not need it,
        // and the reference solver deliberately ignores it so that it can
        // DETECT a violation instead of assuming one away.
        uint32_t rank        = 0;
        uint32_t firstEdge   = 0;
        uint32_t edgeCount   = 0;
    };

    std::vector<Node> nodes;
    std::vector<Edge> edges;

    size_t node_count() const { return nodes.size(); }
    size_t edge_count() const { return edges.size(); }
};

// Incremental builder; finalise() lays the edges out contiguously.
class GraphBuilder {
   public:
    uint32_t add_node(NodeType type, bool whiteToMove, uint32_t rank = 0,
                      bool openMoves = false);
    void     add_edge(uint32_t from, uint32_t to, bool zeroing);
    Graph    finalise() const;

   private:
    struct Pending {
        NodeType type;
        bool     whiteToMove;
        bool     openMoves;
        uint32_t rank;
    };
    std::vector<Pending>                       pending;
    std::vector<std::vector<Graph::Edge>>      adjacency;
};

// Structural preconditions. Everything downstream assumes these hold.
struct Validation {
    bool        ok = true;
    std::string error;
};

// ``requireRank`` also enforces the well-foundedness hypothesis: quiet edges
// preserve R, zeroing edges strictly lower it. The worklist solver does not
// need it; the sweep solver and every soundness argument do.
Validation validate(const Graph& graph, bool requireRank = true);

// ---------------------------------------------------------------------------
// Phase 1: the reference oracle
// ---------------------------------------------------------------------------

struct ReferenceResult {
    // survive[node * 101 + h] == 1 when Black avoids a White win from that
    // exact (node, clock) state. The whole expanded space, materialised.
    std::vector<uint8_t> survive;
    uint64_t             statesVisited = 0;
    // Set when the expanded graph turned out to contain a cycle, which would
    // refute the M = (R, 100 - h) argument. Never expected to fire; if it
    // ever does, the theory is wrong and nothing below is trustworthy.
    bool                 cycleDetected = false;
    uint32_t             cycleNode     = 0;
    int                  cycleClock    = 0;

    uint8_t at(size_t node, int clock) const {
        return survive[node * (FIFTY_MOVE_PLIES + 1) + size_t(clock)];
    }
};

ReferenceResult reference_solve(const Graph& graph);

// tau read straight off the expanded table: the least clock at which Black
// survives. Also reports whether the survival set is upward closed in the
// clock, which is the property that makes tau a threshold at all.
struct ReferenceThresholds {
    std::vector<uint8_t> tau;
    bool                 monotone       = true;
    uint32_t             offendingNode  = 0;
    int                  offendingClock = 0;
};

ReferenceThresholds thresholds_from_reference(const Graph&           graph,
                                              const ReferenceResult& reference);

// ---------------------------------------------------------------------------
// Phase 2: the threshold solvers
// ---------------------------------------------------------------------------

struct ThresholdResult {
    std::vector<uint8_t> tau;
    // Cleared when the input failed its structural preconditions. Only the
    // sweep can report it: the worklist does not read R and so cannot notice.
    bool                 ok          = true;
    uint64_t             relaxations = 0;  // node re-evaluations
    uint64_t             lowerings   = 0;  // re-evaluations that moved tau
    uint64_t             edgeReads   = 0;
    int64_t              elapsedMs   = 0;
    // Sweep only: the monotonicity assertion, checked layer against layer.
    bool                 monotone    = true;
};

// Reverse worklist over tau bounds. Start every unknown state at TAU_MAX and
// lower monotonically: a Black state lowers as soon as ONE move obtains a
// smaller requirement, a White state only when EVERY legal move is covered.
ThresholdResult threshold_solve(const Graph& graph);

// Rank-ordered clock sweep. Same answer, different route, O(states) memory.
ThresholdResult threshold_solve_sweep(const Graph& graph);

// The local recurrence, exported because phase 3's verifier re-derives it and
// the two must be the same arithmetic to the ply.
//
//     quiet edge    ->  min(100, max(0, tau(child) - 1))
//     zeroing edge  ->  0 if tau(child) == 0, else 100
//
// Terminals need no special case: they carry tau 0 (survive) or 101 (White
// win), and both rules then produce the right requirement on their own.
//
// The clamp at 100 is not cosmetic. A raw "never" is 101, but an interior
// parent is already drawn at h = 100, so it never gets to play the move; 100
// and 101 are therefore the same statement at a parent, and clamping keeps
// every interior tau inside [0, 100].
inline int required_entry_clock(int childTau, bool zeroing) {
    if (zeroing)
        return childTau == 0 ? 0 : TAU_MAX;
    const int quiet = childTau - 1;
    return quiet <= 0 ? 0 : (quiet > TAU_MAX ? TAU_MAX : quiet);
}

// The root verdict. ``complete`` must be true — no open nodes anywhere — for a
// White win to be concluded: with unresolved successors, tau(root) > c only
// means the certificate does not cover the root, not that Black is lost.
ProofResult root_verdict(int rootTau, int entryClock, bool complete);

// ---------------------------------------------------------------------------
// Differential test corpus
// ---------------------------------------------------------------------------

enum class Family : uint8_t {
    Dense,    // small, many ranks, heavy transposition
    Chain,    // long quiet chains: exercises the whole tau range and its ends
    Ladder,   // resets landing on children of ARBITRARY tau, not just 0 and 100
    Layered,  // reset-heavy, several ranks
    Sparse    // out-degree 1-2, long thin cycles
};

Graph random_graph(uint64_t seed, Family family);

// ---------------------------------------------------------------------------
// Phase 4: mining a strategy out of real positions
// ---------------------------------------------------------------------------

struct MineLimits {
    // Hard cap on base states in the mined region. Hitting it does not fail;
    // it turns into closure defect, which is a number rather than a shrug.
    uint64_t states = 20000;
    // Movegen budget, in positions built.
    uint64_t positions = 2000000;
    // Black replies kept per state on the first pass. White is always
    // exhaustive; Black is the search problem and starts narrow.
    uint32_t blackWidth = 3;
    // Refinement passes. Doc 18 §4 abandons a candidate after two.
    int passes = 3;
};

struct MineReport {
    ProofResult result          = ProofResult::Unknown;
    bool        haveCertificate = false;
    std::string certificate;

    int    rootTau    = TAU_LOST;
    int    entryClock = 0;
    size_t states     = 0;  // states actually emitted
    size_t edges      = 0;
    size_t minedStates = 0;  // states explored, emitted or not

    // Doc 18 §4: unresolved White edges over all legal White edges.
    uint64_t    whiteEdges           = 0;
    uint64_t    unresolvedWhiteEdges = 0;
    double      closureDefect        = 0.0;
    const char* gate                 = "closed";

    uint64_t positionsBuilt = 0;
    int64_t  elapsedMs      = 0;
};

MineReport mine(Position& root, const MineLimits& limits);

// "survive50_mine <fen> [states N] [width K] [passes P] [positions N]
//  [certfile PATH]"
void mine_command(std::istringstream& is, std::ostream& out);

// Mines known atomic fortresses and checks the certificates it writes.
int mine_selftest(std::ostream& out);

// ---------------------------------------------------------------------------
// Commands
// ---------------------------------------------------------------------------

// "survive50_selftest [graphs N] [seed S]" — hand-built gates plus the
// differential corpus. Returns the number of failures.
int selftest(std::istringstream& is, std::ostream& out);

// "survive50_bench [states N] [degree D] [seed S]" — fixed-point throughput.
void bench(std::istringstream& is, std::ostream& out);

}  // namespace Stockfish::Survive50

#endif  // #ifndef SURVIVE50_H_INCLUDED
