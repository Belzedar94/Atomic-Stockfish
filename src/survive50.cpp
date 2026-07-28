/*
  SURVIVE50 phases 1 and 2: the reference oracle and the threshold solvers.

  Read survive50.h first; it carries the argument. This file carries the
  arithmetic, and the one rule it never bends is that the three solvers stay
  independent. They share the claim automaton (they must — a one-ply
  disagreement there invalidates every label) and nothing else:

    * reference_solve       materialises (node, clock) and recurses.
    * threshold_solve       propagates tau backwards with a worklist.
    * threshold_solve_sweep replays the clock layer by layer, ordered by R.

  Where two of them could have shared a helper, they do not.
*/

#include "survive50.h"

#include <algorithm>
#include <chrono>
#include <cstddef>
#include <iomanip>
#include <ios>
#include <ostream>
#include <sstream>
#include <string>
#include <vector>

namespace Stockfish::Survive50 {

namespace {

constexpr int LAYERS = FIFTY_MOVE_PLIES + 1;  // clocks 0..100 inclusive

int64_t now_ms() {
    using namespace std::chrono;
    return duration_cast<milliseconds>(steady_clock::now().time_since_epoch()).count();
}

// ---------------------------------------------------------------------------
// The claim automaton
// ---------------------------------------------------------------------------
//
// THE one piece of shared semantics, isolated here so that all three solvers
// provably use the same rules. The order of the tests IS the terminal
// precedence contract of `atomic-fide-claim-v1`:
//
//   1. a terminal position is terminal at every clock. A mate delivered on the
//      hundredth reversible ply is a mate, not a draw. (NodeType already
//      encodes the atomic king/mate/stalemate classification.)
//   2. only then does the fifty-move claim apply, and reaching it denies
//      WHITE_WIN, so Black survives.
//   3. only then does incomplete knowledge apply: a White node whose legal
//      moves are not all enumerated is a universal obligation we cannot
//      discharge, so it counts as not-survived below the claim. This is a
//      sound under-approximation and it is applied identically everywhere.
//
// Returns 1 survive, 0 lost, -1 interior (the clock does not decide it yet).
int immediate_status(const Graph& graph, uint32_t node, int clock) {
    const Graph::Node& n = graph.nodes[node];
    if (n.type == NodeType::SurviveTerminal)
        return 1;
    if (n.type == NodeType::WhiteWinTerminal)
        return 0;
    if (clock >= FIFTY_MOVE_PLIES)
        return 1;
    if (n.whiteToMove && n.openMoves)
        return 0;
    return -1;
}

// Deterministic, self-contained xorshift. std::mt19937 would do, but the
// standard's distributions are not portable and the corpus has to be
// byte-identical on every machine that runs the gate.
struct Rng {
    uint64_t state;

    explicit Rng(uint64_t seed) :
        state(seed ? seed : 0x9E3779B97F4A7C15ull) {
        for (int i = 0; i < 4; ++i)
            next();
    }

    uint64_t next() {
        state ^= state << 13;
        state ^= state >> 7;
        state ^= state << 17;
        return state;
    }

    uint32_t below(uint32_t bound) { return bound ? uint32_t(next() % bound) : 0; }
    bool     chance(uint32_t percent) { return below(100) < percent; }
};

}  // namespace

// ---------------------------------------------------------------------------
// Results
// ---------------------------------------------------------------------------

const char* result_name(ProofResult result) {
    switch (result)
    {
    case ProofResult::ProvenWhiteWin :
        return "PROVEN_WHITE_WIN";
    case ProofResult::ProvenBlackWin :
        return "PROVEN_BLACK_WIN";
    case ProofResult::ProvenDraw50 :
        return "PROVEN_DRAW_50";
    case ProofResult::ProvenDrawRepetition :
        return "PROVEN_DRAW_REPETITION";
    case ProofResult::ProvenDrawOther :
        return "PROVEN_DRAW_OTHER";
    case ProofResult::DisprovedWhiteWin :
        return "DISPROVED_WHITE_WIN";
    case ProofResult::Unknown :
        break;
    }
    return "UNKNOWN";
}

ProofResult root_verdict(int rootTau, int entryClock, bool complete) {
    if (rootTau <= entryClock)
        // Black survives. This refutes WHITE_WIN and says NOTHING about
        // BLACK_WIN; the caller may not upgrade it.
        return ProofResult::DisprovedWhiteWin;
    if (complete)
        // Every successor of every node is accounted for, so tau is exact and
        // Black demonstrably cannot hold. Only then is this a positive result.
        return ProofResult::ProvenWhiteWin;
    return ProofResult::Unknown;
}

// ---------------------------------------------------------------------------
// Graph construction and validation
// ---------------------------------------------------------------------------

uint32_t GraphBuilder::add_node(NodeType type, bool whiteToMove, uint32_t rank,
                                bool openMoves) {
    pending.push_back({type, whiteToMove, openMoves, rank});
    adjacency.emplace_back();
    return uint32_t(pending.size() - 1);
}

void GraphBuilder::add_edge(uint32_t from, uint32_t to, bool zeroing) {
    Graph::Edge edge;
    edge.child   = to;
    edge.zeroing = zeroing;
    adjacency[from].push_back(edge);
}

Graph GraphBuilder::finalise() const {
    Graph graph;
    graph.nodes.resize(pending.size());
    size_t total = 0;
    for (const auto& list : adjacency)
        total += list.size();
    graph.edges.reserve(total);

    for (size_t i = 0; i < pending.size(); ++i)
    {
        Graph::Node& node = graph.nodes[i];
        node.type         = pending[i].type;
        node.whiteToMove  = pending[i].whiteToMove;
        node.openMoves    = pending[i].openMoves;
        node.rank         = pending[i].rank;
        node.firstEdge    = uint32_t(graph.edges.size());
        node.edgeCount    = uint32_t(adjacency[i].size());
        for (const auto& edge : adjacency[i])
            graph.edges.push_back(edge);
    }
    return graph;
}

Validation validate(const Graph& graph, bool requireRank) {
    Validation report;
    const size_t count = graph.nodes.size();

    for (size_t i = 0; i < count; ++i)
    {
        const Graph::Node& node = graph.nodes[i];
        std::ostringstream where;
        where << "node " << i << ": ";

        if (node.type != NodeType::Interior && node.edgeCount != 0)
        {
            report.ok    = false;
            report.error = where.str() + "a terminal node has successors";
            return report;
        }
        if (node.type == NodeType::Interior && node.edgeCount == 0
            && !node.openMoves)
        {
            report.ok    = false;
            report.error = where.str() + "an interior node has no legal move";
            return report;
        }
        if (size_t(node.firstEdge) + node.edgeCount > graph.edges.size())
        {
            report.ok    = false;
            report.error = where.str() + "edge range runs past the edge array";
            return report;
        }

        for (uint32_t e = 0; e < node.edgeCount; ++e)
        {
            const Graph::Edge& edge = graph.edges[node.firstEdge + e];
            if (edge.child >= count)
            {
                report.ok    = false;
                report.error = where.str() + "edge points outside the graph";
                return report;
            }
            if (!requireRank)
                continue;
            // The well-foundedness hypothesis, checked rather than assumed.
            // Terminals are exempt: they have no successors, so they cannot
            // take part in a cycle and their rank is meaningless.
            if (graph.nodes[edge.child].type != NodeType::Interior)
                continue;
            const uint32_t childRank = graph.nodes[edge.child].rank;
            if (edge.zeroing && childRank >= node.rank)
            {
                report.ok    = false;
                report.error = where.str()
                             + "a zeroing edge does not strictly lower R";
                return report;
            }
            if (!edge.zeroing && childRank != node.rank)
            {
                report.ok    = false;
                report.error = where.str() + "a quiet edge changes R";
                return report;
            }
        }
    }
    return report;
}

// ---------------------------------------------------------------------------
// Phase 1: exhaustive DP over the expanded (node, clock) space
// ---------------------------------------------------------------------------
//
// Everything the production solver is clever about, this is stupid about on
// purpose. It does not collapse the clock, it does not use R, it does not
// assume the survival set is upward closed, and it does not short-circuit an
// AND node on its first losing child. Its only concession to the machine is an
// explicit stack instead of recursion, because the expanded depth reaches
// |V| * 101 and a blown call stack is not a proof either.
//
// Cycle detection is not defensive programming, it is a measurement: the
// expanded graph is acyclic if and only if M = (R, 100 - h) is a well-founded
// measure, so a cycle here would refute the argument the whole subsystem
// stands on.

ReferenceResult reference_solve(const Graph& graph) {
    ReferenceResult out;
    const size_t    count = graph.nodes.size();
    out.survive.assign(count * LAYERS, 0);

    enum : uint8_t { Unseen = 0, OnStack = 1, ResolvedLost = 2, ResolvedSurvive = 3 };
    std::vector<uint8_t> status(count * LAYERS, Unseen);

    // Seed every state the rules already decide, at every clock.
    for (size_t node = 0; node < count; ++node)
        for (int clock = 0; clock < LAYERS; ++clock)
        {
            const int decided = immediate_status(graph, uint32_t(node), clock);
            if (decided < 0)
                continue;
            const size_t index = node * LAYERS + size_t(clock);
            out.survive[index] = uint8_t(decided);
            status[index]      = decided ? ResolvedSurvive : ResolvedLost;
        }

    struct Frame {
        uint32_t node;
        int32_t  clock;
        uint32_t edgeAt;
        uint8_t  accumulator;
    };
    std::vector<Frame> stack;
    stack.reserve(256);

    for (size_t startNode = 0; startNode < count && !out.cycleDetected; ++startNode)
        for (int startClock = 0; startClock < LAYERS; ++startClock)
        {
            if (status[startNode * LAYERS + size_t(startClock)] != Unseen)
                continue;

            const bool startWhite = graph.nodes[startNode].whiteToMove;
            stack.push_back({uint32_t(startNode), startClock, 0,
                             uint8_t(startWhite ? 1 : 0)});
            status[startNode * LAYERS + size_t(startClock)] = OnStack;
            ++out.statesVisited;

            while (!stack.empty())
            {
                const Frame        frame = stack.back();
                const Graph::Node& node  = graph.nodes[frame.node];

                if (frame.edgeAt < node.edgeCount)
                {
                    const Graph::Edge& edge =
                      graph.edges[node.firstEdge + frame.edgeAt];
                    const int childClock = edge.zeroing ? 0 : frame.clock + 1;
                    const int decided =
                      immediate_status(graph, edge.child, childClock);

                    uint8_t value = 0;
                    if (decided >= 0)
                        value = uint8_t(decided);
                    else
                    {
                        const size_t childIndex =
                          size_t(edge.child) * LAYERS + size_t(childClock);
                        const uint8_t childStatus = status[childIndex];
                        if (childStatus == Unseen)
                        {
                            const bool childWhite =
                              graph.nodes[edge.child].whiteToMove;
                            stack.push_back({edge.child, childClock, 0,
                                             uint8_t(childWhite ? 1 : 0)});
                            status[childIndex] = OnStack;
                            ++out.statesVisited;
                            continue;  // resume this frame after the child
                        }
                        if (childStatus == OnStack)
                        {
                            out.cycleDetected = true;
                            out.cycleNode     = edge.child;
                            out.cycleClock    = childClock;
                            stack.clear();
                            break;
                        }
                        value = uint8_t(childStatus == ResolvedSurvive);
                    }

                    Frame& live = stack.back();
                    ++live.edgeAt;
                    // White must survive EVERY legal move, Black needs one.
                    live.accumulator = node.whiteToMove
                                       ? uint8_t(live.accumulator & value)
                                       : uint8_t(live.accumulator | value);
                    continue;
                }

                const size_t index =
                  size_t(frame.node) * LAYERS + size_t(frame.clock);
                out.survive[index] = frame.accumulator;
                status[index] = frame.accumulator ? ResolvedSurvive : ResolvedLost;
                stack.pop_back();
            }

            if (out.cycleDetected)
                break;
        }

    return out;
}

ReferenceThresholds thresholds_from_reference(const Graph&           graph,
                                              const ReferenceResult& reference) {
    ReferenceThresholds out;
    const size_t        count = graph.nodes.size();
    out.tau.assign(count, uint8_t(TAU_LOST));

    for (size_t node = 0; node < count; ++node)
    {
        int least = TAU_LOST;
        for (int clock = FIFTY_MOVE_PLIES; clock >= 0; --clock)
        {
            if (reference.at(node, clock))
                least = clock;
            // Upward closure in the clock is what makes tau a THRESHOLD
            // rather than an arbitrary set. It is a theorem, so it is checked
            // and never assumed.
            if (clock < FIFTY_MOVE_PLIES && reference.at(node, clock)
                && !reference.at(node, clock + 1))
            {
                out.monotone       = false;
                out.offendingNode  = uint32_t(node);
                out.offendingClock = clock;
            }
        }
        out.tau[node] = uint8_t(least);
    }
    return out;
}

// ---------------------------------------------------------------------------
// Phase 2a: the reverse worklist
// ---------------------------------------------------------------------------
//
// Start every unknown state at TAU_MAX and lower monotonically. Because
// zeroing edges strictly lower R, every cycle in this graph is made purely of
// quiet edges, and along a quiet edge the requirement is max(0, tau - 1) — a
// map that drives any pure-quiet cycle to zero. The fixed point is therefore
// unique, and an iteration from above reaches it.

ThresholdResult threshold_solve(const Graph& graph) {
    ThresholdResult out;
    const int64_t   started = now_ms();
    const size_t    count   = graph.nodes.size();
    out.tau.assign(count, uint8_t(TAU_MAX));

    for (size_t i = 0; i < count; ++i)
    {
        if (graph.nodes[i].type == NodeType::SurviveTerminal)
            out.tau[i] = uint8_t(TAU_MIN);
        else if (graph.nodes[i].type == NodeType::WhiteWinTerminal)
            out.tau[i] = uint8_t(TAU_LOST);
    }

    // Reverse adjacency in CSR form. The worklist is driven backwards: when a
    // node's tau drops, only its predecessors can possibly drop with it.
    std::vector<uint32_t> predStart(count + 1, 0);
    for (const auto& edge : graph.edges)
        ++predStart[size_t(edge.child) + 1];
    for (size_t i = 0; i < count; ++i)
        predStart[i + 1] += predStart[i];
    std::vector<uint32_t> predList(graph.edges.size(), 0);
    {
        std::vector<uint32_t> cursor(predStart.begin(), predStart.end() - 1);
        for (size_t parent = 0; parent < count; ++parent)
        {
            const Graph::Node& node = graph.nodes[parent];
            for (uint32_t e = 0; e < node.edgeCount; ++e)
                predList[cursor[graph.edges[node.firstEdge + e].child]++] =
                  uint32_t(parent);
        }
    }

    std::vector<uint8_t>  queued(count, 0);
    std::vector<uint32_t> work;
    work.reserve(count);
    for (size_t i = 0; i < count; ++i)
        if (graph.nodes[i].type == NodeType::Interior)
        {
            work.push_back(uint32_t(i));
            queued[i] = 1;
        }

    while (!work.empty())
    {
        const uint32_t current = work.back();
        work.pop_back();
        queued[current] = 0;
        ++out.relaxations;

        const Graph::Node& node = graph.nodes[current];
        int                value;
        if (node.whiteToMove && node.openMoves)
        {
            // A universal obligation with unknown members. Nothing below 100
            // can be claimed; the closure defect is phase 4's business.
            value = TAU_MAX;
        }
        else
        {
            // max over all White moves, min over Black's. The identities are
            // the empty-aggregate values and they are correct as such: an
            // exhausted White node survives vacuously, an exhausted Black node
            // has nothing to play.
            value = node.whiteToMove ? TAU_MIN : TAU_MAX;
            out.edgeReads += node.edgeCount;
            for (uint32_t e = 0; e < node.edgeCount; ++e)
            {
                const Graph::Edge& edge = graph.edges[node.firstEdge + e];
                const int          need =
                  required_entry_clock(out.tau[edge.child], edge.zeroing);
                value = node.whiteToMove ? std::max(value, need)
                                         : std::min(value, need);
            }
            value = std::min(value, TAU_MAX);
        }

        if (value >= out.tau[current])
            continue;
        out.tau[current] = uint8_t(value);
        ++out.lowerings;
        for (uint32_t p = predStart[current]; p < predStart[current + 1]; ++p)
        {
            const uint32_t parent = predList[p];
            if (graph.nodes[parent].type != NodeType::Interior || queued[parent])
                continue;
            queued[parent] = 1;
            work.push_back(parent);
        }
    }

    out.elapsedMs = now_ms() - started;
    return out;
}

// ---------------------------------------------------------------------------
// Phase 2b: the rank-ordered clock sweep
// ---------------------------------------------------------------------------
//
// The expanded DP again, but never more than two clock layers of one rank
// level are in memory at a time. The order is forced and is the whole point:
// R ascending, then clock 100 down to 0. A quiet edge stays inside its level
// and reads the layer immediately above; a zeroing edge leaves for a strictly
// lower level whose clock-zero layer is already final. Nothing is ever read
// before it is written.
//
// It shares no code with the worklist, so agreement between them is evidence
// and not tautology.

ThresholdResult threshold_solve_sweep(const Graph& graph) {
    ThresholdResult out;
    const int64_t   started = now_ms();
    const size_t    count   = graph.nodes.size();

    const Validation report = validate(graph, true);
    if (!report.ok)
    {
        out.ok = false;
        out.tau.assign(count, uint8_t(TAU_MAX));
        return out;
    }

    out.tau.assign(count, uint8_t(TAU_MAX));
    std::vector<uint8_t> aliveAtZero(count, 0);
    for (size_t i = 0; i < count; ++i)
        if (graph.nodes[i].type == NodeType::SurviveTerminal)
        {
            out.tau[i]     = uint8_t(TAU_MIN);
            aliveAtZero[i] = 1;
        }
        else if (graph.nodes[i].type == NodeType::WhiteWinTerminal)
            out.tau[i] = uint8_t(TAU_LOST);

    // Bucket the interior nodes by R, ascending.
    uint32_t maxRank = 0;
    for (const auto& node : graph.nodes)
        if (node.type == NodeType::Interior)
            maxRank = std::max(maxRank, node.rank);

    std::vector<uint32_t> bucketStart(size_t(maxRank) + 2, 0);
    for (const auto& node : graph.nodes)
        if (node.type == NodeType::Interior)
            ++bucketStart[size_t(node.rank) + 1];
    for (size_t r = 0; r + 1 < bucketStart.size(); ++r)
        bucketStart[r + 1] += bucketStart[r];
    std::vector<uint32_t> byRank(bucketStart.back(), 0);
    {
        std::vector<uint32_t> cursor(bucketStart.begin(), bucketStart.end() - 1);
        for (size_t i = 0; i < count; ++i)
            if (graph.nodes[i].type == NodeType::Interior)
                byRank[cursor[graph.nodes[i].rank]++] = uint32_t(i);
    }

    std::vector<uint32_t> localIndex(count, 0);
    std::vector<uint8_t>  above, below;

    for (uint32_t rank = 0; rank <= maxRank; ++rank)
    {
        const uint32_t from = bucketStart[rank];
        const uint32_t to   = bucketStart[size_t(rank) + 1];
        const size_t   size = to - from;
        if (size == 0)
            continue;

        for (uint32_t i = from; i < to; ++i)
            localIndex[byRank[i]] = i - from;

        // Clock 100: the claim is available, so every interior state holds.
        above.assign(size, 1);
        below.assign(size, 0);
        for (uint32_t i = from; i < to; ++i)
            out.tau[byRank[i]] = uint8_t(FIFTY_MOVE_PLIES);

        for (int clock = FIFTY_MOVE_PLIES - 1; clock >= 0; --clock)
        {
            for (uint32_t i = from; i < to; ++i)
            {
                const uint32_t     id   = byRank[i];
                const Graph::Node& node = graph.nodes[id];
                uint8_t            alive;

                if (node.whiteToMove && node.openMoves)
                    alive = 0;
                else
                {
                    alive = node.whiteToMove ? 1 : 0;
                    out.edgeReads += node.edgeCount;
                    for (uint32_t e = 0; e < node.edgeCount; ++e)
                    {
                        const Graph::Edge& edge = graph.edges[node.firstEdge + e];
                        const Graph::Node& kid  = graph.nodes[edge.child];
                        uint8_t            childAlive;
                        if (kid.type == NodeType::SurviveTerminal)
                            childAlive = 1;
                        else if (kid.type == NodeType::WhiteWinTerminal)
                            childAlive = 0;
                        else if (edge.zeroing)
                            childAlive = aliveAtZero[edge.child];
                        else
                            childAlive = above[localIndex[edge.child]];

                        alive = node.whiteToMove ? uint8_t(alive & childAlive)
                                                 : uint8_t(alive | childAlive);
                    }
                }

                below[i - from] = alive;
                if (alive && !above[i - from])
                    out.monotone = false;  // survival must be upward closed
                if (alive)
                    out.tau[id] = uint8_t(clock);
            }
            above.swap(below);
        }

        for (uint32_t i = from; i < to; ++i)
            aliveAtZero[byRank[i]] = above[i - from];
        ++out.relaxations;
    }

    out.elapsedMs = now_ms() - started;
    return out;
}

// ---------------------------------------------------------------------------
// Corpus generation
// ---------------------------------------------------------------------------

namespace {

struct Shape {
    uint32_t minRanks, maxRanks;
    uint32_t minPerRank, maxPerRank;
    uint32_t minDegree, maxDegree;
    uint32_t zeroingPercent;
    uint32_t openPercent;
    uint32_t terminalPercent;  // chance an edge points at a terminal instead
};

Graph build_layered(Rng& rng, const Shape& shape) {
    GraphBuilder builder;

    // Two terminals, always present, so that every family can express both
    // outcomes. Rank is meaningless for a terminal and validate() knows it.
    const uint32_t winTerminal = builder.add_node(NodeType::WhiteWinTerminal, false, 0);
    const uint32_t drawTerminal = builder.add_node(NodeType::SurviveTerminal, false, 0);

    const uint32_t ranks =
      shape.minRanks + rng.below(shape.maxRanks - shape.minRanks + 1);
    std::vector<std::vector<uint32_t>> levels(ranks);
    for (uint32_t r = 0; r < ranks; ++r)
    {
        const uint32_t howMany =
          shape.minPerRank + rng.below(shape.maxPerRank - shape.minPerRank + 1);
        for (uint32_t i = 0; i < howMany; ++i)
            levels[r].push_back(builder.add_node(
              NodeType::Interior, rng.chance(50), r,
              rng.chance(shape.openPercent)));
    }

    for (uint32_t r = 0; r < ranks; ++r)
        for (uint32_t id : levels[r])
        {
            const uint32_t degree =
              shape.minDegree + rng.below(shape.maxDegree - shape.minDegree + 1);
            for (uint32_t d = 0; d < degree; ++d)
            {
                // A zeroing edge must land strictly lower, so at rank 0 the
                // only legal reset target is a terminal.
                bool zeroing = rng.chance(shape.zeroingPercent);
                if (rng.chance(shape.terminalPercent))
                {
                    builder.add_edge(id, rng.chance(50) ? winTerminal : drawTerminal,
                                     zeroing);
                    continue;
                }
                if (zeroing && r == 0)
                {
                    builder.add_edge(id, rng.chance(50) ? winTerminal : drawTerminal,
                                     true);
                    continue;
                }
                if (zeroing)
                {
                    const uint32_t target = rng.below(r);
                    const auto&    pool   = levels[target];
                    builder.add_edge(id, pool[rng.below(uint32_t(pool.size()))], true);
                }
                else
                {
                    const auto& pool = levels[r];
                    builder.add_edge(id, pool[rng.below(uint32_t(pool.size()))], false);
                }
            }
        }

    return builder.finalise();
}

// A chain is the sharpest instrument in the corpus: n_0 -> n_1 -> ... -> T is
// mate in k quiet plies from n_i, so tau(n_i) is forced to i and one graph
// sweeps the entire [0, 100] range plus both of its ends.
Graph build_chain(Rng& rng) {
    GraphBuilder   builder;
    const uint32_t winTerminal = builder.add_node(NodeType::WhiteWinTerminal, false, 0);
    const uint32_t drawTerminal = builder.add_node(NodeType::SurviveTerminal, false, 0);

    const uint32_t length = 1 + rng.below(120);
    const bool     allWhite = rng.chance(40);

    std::vector<uint32_t> chain;
    for (uint32_t i = 0; i < length; ++i)
        chain.push_back(builder.add_node(NodeType::Interior,
                                         allWhite ? true : rng.chance(50), 0));

    for (uint32_t i = 0; i < length; ++i)
    {
        if (i + 1 < length)
            builder.add_edge(chain[i], chain[i + 1], false);
        else
            builder.add_edge(chain[i], rng.chance(70) ? winTerminal : drawTerminal,
                             false);
        // Occasional side exits, so the chain is not merely a path.
        if (rng.chance(12))
            builder.add_edge(chain[i], rng.chance(50) ? drawTerminal : winTerminal,
                             rng.chance(30));
        if (rng.chance(10))
            builder.add_edge(chain[i], chain[rng.below(length)], false);
    }
    return builder.finalise();
}

// A ladder is a calibration chain with reset traffic pointed INTO it, and it
// exists because of a hole mutation testing found in the other three families:
// every reset they generate lands on a child whose tau is 0, 100 or 101, so
// the rule "a reset is admissible only into tau == 0" was being tested at its
// extremes and nowhere in between. A reset into a child of tau 1 is the case
// that separates the rule from its most plausible misstatement, and only this
// family produces one.
Graph build_ladder(Rng& rng) {
    GraphBuilder   builder;
    const uint32_t win  = builder.add_node(NodeType::WhiteWinTerminal, false, 0);
    const uint32_t draw = builder.add_node(NodeType::SurviveTerminal, false, 0);

    const uint32_t        length = 20 + rng.below(100);
    std::vector<uint32_t> chain;
    for (uint32_t i = 0; i < length; ++i)
        chain.push_back(builder.add_node(NodeType::Interior, true, 0));
    for (uint32_t i = 0; i < length; ++i)
        builder.add_edge(chain[i], i + 1 < length ? chain[i + 1] : win, false);

    const uint32_t                     ranks = 1 + rng.below(2);
    std::vector<std::vector<uint32_t>> levels(size_t(ranks) + 1);
    levels[0] = chain;
    for (uint32_t r = 1; r <= ranks; ++r)
    {
        const uint32_t howMany = 1 + rng.below(5);
        for (uint32_t i = 0; i < howMany; ++i)
            levels[r].push_back(
              builder.add_node(NodeType::Interior, rng.chance(50), r));
    }

    for (uint32_t r = 1; r <= ranks; ++r)
        for (uint32_t id : levels[r])
        {
            const uint32_t degree = 1 + rng.below(3);
            for (uint32_t d = 0; d < degree; ++d)
            {
                if (rng.chance(10))
                {
                    builder.add_edge(id, rng.chance(50) ? draw : win, rng.chance(50));
                    continue;
                }
                if (rng.chance(55))
                {
                    const auto& pool = levels[rng.below(r)];
                    builder.add_edge(id, pool[rng.below(uint32_t(pool.size()))], true);
                }
                else
                {
                    const auto& pool = levels[r];
                    builder.add_edge(id, pool[rng.below(uint32_t(pool.size()))], false);
                }
            }
        }
    return builder.finalise();
}

}  // namespace

Graph random_graph(uint64_t seed, Family family) {
    Rng rng(seed * 0x9E3779B97F4A7C15ull + uint64_t(family) + 1);
    switch (family)
    {
    case Family::Chain :
        return build_chain(rng);
    case Family::Ladder :
        return build_ladder(rng);
    case Family::Layered :
        return build_layered(rng, Shape{3, 6, 2, 5, 1, 3, 50, 3, 12});
    case Family::Sparse :
        return build_layered(rng, Shape{1, 2, 3, 10, 1, 2, 20, 3, 10});
    case Family::Dense :
        break;
    }
    return build_layered(rng, Shape{1, 3, 2, 6, 1, 4, 30, 4, 15});
}

// ---------------------------------------------------------------------------
// Hand-built gates
// ---------------------------------------------------------------------------

namespace {

struct Gate {
    std::string           name;
    Graph                 graph;
    std::vector<uint32_t> node;
    std::vector<int>      expected;
};

// The one graph that pins the whole scale: a chain of 101 interior nodes ending
// in a White win. Node i is mate in (101 - i) quiet plies, so tau(node i) = i.
Gate scale_gate() {
    GraphBuilder builder;
    const uint32_t win = builder.add_node(NodeType::WhiteWinTerminal, false, 0);
    std::vector<uint32_t> chain;
    for (int i = 0; i <= FIFTY_MOVE_PLIES; ++i)
        chain.push_back(builder.add_node(NodeType::Interior, true, 0));
    for (int i = 0; i <= FIFTY_MOVE_PLIES; ++i)
        builder.add_edge(chain[size_t(i)],
                         i == FIFTY_MOVE_PLIES ? win : chain[size_t(i) + 1], false);

    Gate gate;
    gate.name  = "scale: tau(node i) == i over the whole [0,100] range";
    gate.graph = builder.finalise();
    gate.node.push_back(win);
    gate.expected.push_back(TAU_LOST);  // mate beats the counter at every clock
    for (int i = 0; i <= FIFTY_MOVE_PLIES; ++i)
    {
        gate.node.push_back(chain[size_t(i)]);
        gate.expected.push_back(i);
    }
    return gate;
}

Gate fortress_gate() {
    GraphBuilder   builder;
    const uint32_t a = builder.add_node(NodeType::Interior, true, 0);
    const uint32_t b = builder.add_node(NodeType::Interior, false, 0);
    builder.add_edge(a, b, false);
    builder.add_edge(b, a, false);

    Gate gate;
    gate.name  = "structural fortress: a pure quiet cycle has tau 0";
    gate.graph = builder.finalise();
    gate.node  = {a, b};
    gate.expected = {0, 0};
    return gate;
}

// Resets are the reason the naive bounded-depth idea fails, so they get their
// own gate: a reset is admissible only into tau == 0, and anything else is
// worth exactly nothing.
//
// The probe list sweeps the NEIGHBOURHOOD of the boundary rather than its two
// extremes. "tau(child) <= 1" is a perfectly plausible way to write this rule
// wrong, and only a reset landing on a child of tau exactly 1 tells the two
// apart; a gate that tests 0 against 5 passes the broken version.
Gate reset_gate() {
    GraphBuilder builder;
    const uint32_t win  = builder.add_node(NodeType::WhiteWinTerminal, false, 0);
    const uint32_t draw = builder.add_node(NodeType::SurviveTerminal, false, 0);

    // The calibration chain: chain[i] is mate in (101 - i) quiet plies, so
    // tau(chain[i]) == i and the reset probes below have a known target.
    std::vector<uint32_t> chain;
    for (int i = 0; i <= FIFTY_MOVE_PLIES; ++i)
        chain.push_back(builder.add_node(NodeType::Interior, true, 0));
    for (int i = 0; i <= FIFTY_MOVE_PLIES; ++i)
        builder.add_edge(chain[size_t(i)],
                         i == FIFTY_MOVE_PLIES ? win : chain[size_t(i) + 1], false);

    static const int      probes[] = {0, 1, 2, 3, 4, 5, 50, 98, 99, 100};
    std::vector<uint32_t> resetters;
    for (int probe : probes)
    {
        const uint32_t node = builder.add_node(NodeType::Interior, true, 1);
        builder.add_edge(node, chain[size_t(probe)], true);
        resetters.push_back(node);
    }
    const uint32_t intoDraw = builder.add_node(NodeType::Interior, true, 1);
    const uint32_t intoWin  = builder.add_node(NodeType::Interior, true, 1);
    builder.add_edge(intoDraw, draw, true);
    builder.add_edge(intoWin, win, true);

    Gate gate;
    gate.name  = "reset: admissible into tau 0 and into nothing else";
    gate.graph = builder.finalise();
    for (size_t i = 0; i < resetters.size(); ++i)
    {
        gate.node.push_back(resetters[i]);
        gate.expected.push_back(probes[i] == 0 ? 0 : TAU_MAX);
    }
    gate.node.push_back(intoDraw);
    gate.expected.push_back(0);
    gate.node.push_back(intoWin);
    gate.expected.push_back(TAU_MAX);
    for (int i = 0; i <= FIFTY_MOVE_PLIES; ++i)
    {
        gate.node.push_back(chain[size_t(i)]);
        gate.expected.push_back(i);
    }
    return gate;
}

Gate quantifier_gate() {
    GraphBuilder   builder;
    const uint32_t win   = builder.add_node(NodeType::WhiteWinTerminal, false, 0);
    const uint32_t draw  = builder.add_node(NodeType::SurviveTerminal, false, 0);
    const uint32_t white = builder.add_node(NodeType::Interior, true, 0);
    const uint32_t black = builder.add_node(NodeType::Interior, false, 0);
    builder.add_edge(white, draw, false);
    builder.add_edge(white, win, false);
    builder.add_edge(black, win, false);
    builder.add_edge(black, draw, false);

    Gate gate;
    gate.name  = "quantifiers: White is a MAX over all moves, Black a MIN";
    gate.graph = builder.finalise();
    gate.node  = {white, black};
    // White is dragged up by its losing move; Black is saved by its one good one.
    gate.expected = {TAU_MAX, 0};
    return gate;
}

Gate openness_gate() {
    GraphBuilder   builder;
    const uint32_t draw       = builder.add_node(NodeType::SurviveTerminal, false, 0);
    const uint32_t openWhite  = builder.add_node(NodeType::Interior, true, 0, true);
    const uint32_t openBlack  = builder.add_node(NodeType::Interior, false, 0, true);
    builder.add_edge(openWhite, draw, false);
    builder.add_edge(openBlack, draw, false);

    Gate gate;
    gate.name  = "openness: an unenumerated White node is pinned, a Black one is not";
    gate.graph = builder.finalise();
    gate.node  = {openWhite, openBlack};
    gate.expected = {TAU_MAX, 0};
    return gate;
}

std::vector<Gate> hand_built_gates() {
    std::vector<Gate> gates;
    gates.push_back(scale_gate());
    gates.push_back(fortress_gate());
    gates.push_back(reset_gate());
    gates.push_back(quantifier_gate());
    gates.push_back(openness_gate());
    return gates;
}

const char* family_name(Family family) {
    switch (family)
    {
    case Family::Chain :
        return "chain";
    case Family::Ladder :
        return "ladder";
    case Family::Layered :
        return "layered";
    case Family::Sparse :
        return "sparse";
    case Family::Dense :
        break;
    }
    return "dense";
}

}  // namespace

// ---------------------------------------------------------------------------
// Commands
// ---------------------------------------------------------------------------

int selftest(std::istringstream& is, std::ostream& out) {
    uint64_t    perFamily = 2500;
    uint64_t    seed      = 0xA701C0DEull;
    std::string token;
    while (is >> token)
    {
        if (token == "graphs")
            is >> perFamily;
        else if (token == "seed")
            is >> seed;
    }
    if (!seed)
        seed = 1;

    int failures = 0;
    out << "survive50 selftest\n";
    out << "  ruleset " << RULESET_ID << ", repetition " << REPETITION_MODE << "\n";

    // ---- hand-built gates ----
    for (const Gate& gate : hand_built_gates())
    {
        const Validation report = validate(gate.graph, true);
        if (!report.ok)
        {
            out << "  FAIL  " << gate.name << " : invalid graph: " << report.error
                << "\n";
            ++failures;
            continue;
        }
        const ReferenceResult     reference = reference_solve(gate.graph);
        const ReferenceThresholds oracle =
          thresholds_from_reference(gate.graph, reference);
        const ThresholdResult worklist = threshold_solve(gate.graph);
        const ThresholdResult sweep    = threshold_solve_sweep(gate.graph);

        int bad = 0;
        for (size_t i = 0; i < gate.node.size(); ++i)
        {
            const uint32_t id = gate.node[i];
            if (oracle.tau[id] != gate.expected[i]
                || worklist.tau[id] != gate.expected[i]
                || sweep.tau[id] != gate.expected[i])
            {
                if (bad < 3)
                    out << "         node " << id << " expected "
                        << gate.expected[i] << " got reference "
                        << int(oracle.tau[id]) << " worklist "
                        << int(worklist.tau[id]) << " sweep "
                        << int(sweep.tau[id]) << "\n";
                ++bad;
            }
        }
        if (reference.cycleDetected)
        {
            out << "         expanded graph is CYCLIC: the (R, 100-h) measure "
                   "does not hold\n";
            ++bad;
        }
        if (!oracle.monotone || !sweep.monotone)
        {
            out << "         survival is not upward closed in the clock\n";
            ++bad;
        }
        out << (bad ? "  FAIL  " : "  ok    ") << gate.name << "\n";
        failures += bad ? 1 : 0;
    }

    // ---- typed results (oracle 5.1, doc 18 §6.1) ----
    //
    // The distinction this pins is the one the proof database cannot recover
    // from if it is ever blurred: surviving refutes the boolean objective
    // WHITE_WIN and says nothing at all about BLACK_WIN.
    {
        int bad = 0;
        bad += root_verdict(50, 50, true) != ProofResult::DisprovedWhiteWin;
        bad += root_verdict(50, 99, false) != ProofResult::DisprovedWhiteWin;
        bad += root_verdict(0, 0, true) != ProofResult::DisprovedWhiteWin;
        bad += root_verdict(0, 0, true) == ProofResult::ProvenBlackWin;
        // tau above the entry clock is only a White win when every successor
        // of every state is accounted for. With one unresolved exit it means
        // the certificate does not reach the root, which is not the same
        // statement and must not be typed as one.
        bad += root_verdict(51, 50, true) != ProofResult::ProvenWhiteWin;
        bad += root_verdict(51, 50, false) != ProofResult::Unknown;
        out << (bad ? "  FAIL  " : "  ok    ")
            << "typed results: a survival is DISPROVED_WHITE_WIN, never "
               "PROVEN_BLACK_WIN\n";
        failures += bad ? 1 : 0;
    }

    // ---- differential corpus ----
    const Family families[] = {Family::Dense, Family::Chain, Family::Ladder,
                               Family::Layered, Family::Sparse};

    // Coverage, reported rather than assumed. A differential suite that agrees
    // on a corpus which never builds the interesting case proves nothing, and
    // this subsystem has already been bitten once by exactly that: resets were
    // only ever generated into children of tau 0, 100 or 101, so the rule
    // "a reset is admissible only into tau == 0" was never actually under
    // test. These three counters are what makes that visible next time.
    uint64_t             expandedStates = 0;
    uint64_t             resetsIntoMid  = 0;
    uint64_t             quietIntoMid   = 0;
    std::vector<uint64_t> tauSeen(size_t(TAU_LOST) + 1, 0);

    for (Family family : families)
    {
        uint64_t mismatchReference = 0, mismatchSweep = 0, cyclic = 0;
        uint64_t notMonotone = 0, invalid = 0, nodesSeen = 0, edgesSeen = 0;
        uint64_t firstBadSeed = 0;

        for (uint64_t i = 0; i < perFamily; ++i)
        {
            const uint64_t graphSeed = seed + i;
            const Graph    graph     = random_graph(graphSeed, family);
            const Validation report  = validate(graph, true);
            if (!report.ok)
            {
                ++invalid;
                if (!firstBadSeed)
                    firstBadSeed = graphSeed;
                continue;
            }
            nodesSeen += graph.node_count();
            edgesSeen += graph.edge_count();

            const ReferenceResult reference = reference_solve(graph);
            if (reference.cycleDetected)
            {
                ++cyclic;
                if (!firstBadSeed)
                    firstBadSeed = graphSeed;
                continue;
            }
            const ReferenceThresholds oracle =
              thresholds_from_reference(graph, reference);
            const ThresholdResult worklist = threshold_solve(graph);
            const ThresholdResult sweep    = threshold_solve_sweep(graph);

            expandedStates += reference.statesVisited;
            for (size_t node = 0; node < graph.nodes.size(); ++node)
            {
                ++tauSeen[oracle.tau[node]];
                const Graph::Node& parent = graph.nodes[node];
                for (uint32_t e = 0; e < parent.edgeCount; ++e)
                {
                    const Graph::Edge& edge = graph.edges[parent.firstEdge + e];
                    const int          kid  = oracle.tau[edge.child];
                    if (kid <= 0 || kid >= TAU_MAX)
                        continue;
                    if (edge.zeroing)
                        ++resetsIntoMid;
                    else
                        ++quietIntoMid;
                }
            }

            if (!oracle.monotone || !sweep.monotone)
            {
                ++notMonotone;
                if (!firstBadSeed)
                    firstBadSeed = graphSeed;
            }
            if (oracle.tau != worklist.tau)
            {
                ++mismatchReference;
                if (!firstBadSeed)
                    firstBadSeed = graphSeed;
            }
            if (oracle.tau != sweep.tau)
            {
                ++mismatchSweep;
                if (!firstBadSeed)
                    firstBadSeed = graphSeed;
            }
        }

        const bool clean = !mismatchReference && !mismatchSweep && !cyclic
                        && !notMonotone && !invalid;
        out << (clean ? "  ok    " : "  FAIL  ") << family_name(family) << ": "
            << perFamily << " graphs, " << nodesSeen << " nodes, " << edgesSeen
            << " edges";
        if (!clean)
            out << " | worklist mismatches " << mismatchReference
                << ", sweep mismatches " << mismatchSweep << ", cyclic " << cyclic
                << ", non-monotone " << notMonotone << ", invalid " << invalid
                << ", first bad seed " << firstBadSeed;
        out << "\n";
        failures += clean ? 0 : 1;
    }

    size_t distinct = 0;
    for (size_t value = 0; value <= size_t(TAU_LOST); ++value)
        distinct += tauSeen[value] != 0;
    out << "  coverage: " << expandedStates
        << " expanded (node,clock) states, 0 cyclic; " << distinct << "/"
        << (size_t(TAU_LOST) + 1) << " distinct tau values reached; "
        << resetsIntoMid << " resets and " << quietIntoMid
        << " quiet edges land on a child of tau in [1,99]\n";
    if (!resetsIntoMid)
    {
        out << "  FAIL  corpus never resets into a mid-range tau: the reset "
               "rule is untested\n";
        ++failures;
    }

    out << (failures ? "FAILED" : "OK") << " (" << failures << " failing groups)\n";
    return failures;
}

namespace {

// A graph large enough to mean something, laid out the way a real fortress
// looks: mostly quiet inside a level, a thin skin of resets leaving it.
Graph bench_graph(uint32_t states, uint32_t degree, uint64_t seed) {
    Rng            rng(seed);
    GraphBuilder   builder;
    const uint32_t win  = builder.add_node(NodeType::WhiteWinTerminal, false, 0);
    const uint32_t draw = builder.add_node(NodeType::SurviveTerminal, false, 0);

    const uint32_t ranks   = 8;
    const uint32_t perRank = std::max<uint32_t>(1, states / ranks);
    std::vector<std::vector<uint32_t>> levels(ranks);
    for (uint32_t r = 0; r < ranks; ++r)
        for (uint32_t i = 0; i < perRank; ++i)
            levels[r].push_back(
              builder.add_node(NodeType::Interior, rng.chance(50), r));

    for (uint32_t r = 0; r < ranks; ++r)
        for (uint32_t id : levels[r])
            for (uint32_t d = 0; d < degree; ++d)
            {
                if (rng.chance(3))
                {
                    builder.add_edge(id, rng.chance(80) ? draw : win, rng.chance(50));
                    continue;
                }
                if (rng.chance(8))
                {
                    if (r == 0)
                        builder.add_edge(id, rng.chance(80) ? draw : win, true);
                    else
                    {
                        const auto& pool = levels[rng.below(r)];
                        builder.add_edge(id, pool[rng.below(uint32_t(pool.size()))],
                                         true);
                    }
                    continue;
                }
                const auto& pool = levels[r];
                builder.add_edge(id, pool[rng.below(uint32_t(pool.size()))], false);
            }

    return builder.finalise();
}

}  // namespace

void bench(std::istringstream& is, std::ostream& out) {
    uint32_t    states = 100000;
    uint32_t    degree = 20;
    uint64_t    seed   = 20260728;
    std::string token;
    while (is >> token)
    {
        if (token == "states")
            is >> states;
        else if (token == "degree")
            is >> degree;
        else if (token == "seed")
            is >> seed;
    }
    states = std::max<uint32_t>(8, states);
    degree = std::max<uint32_t>(1, degree);

    const Graph      graph  = bench_graph(states, degree, seed);
    const Validation report = validate(graph, true);
    out << "survive50 bench\n";
    out << "  states " << graph.node_count() << "  edges " << graph.edge_count()
        << "  valid " << (report.ok ? "yes" : report.error) << "\n";
    if (!report.ok)
        return;

    const size_t graphBytes = graph.nodes.size() * sizeof(Graph::Node)
                            + graph.edges.size() * sizeof(Graph::Edge);
    // The worklist also builds a reverse CSR: one uint32 per edge plus offsets.
    const size_t solverBytes = graph.edges.size() * sizeof(uint32_t)
                             + (graph.nodes.size() + 1) * sizeof(uint32_t)
                             + graph.nodes.size() * 2;

    const ThresholdResult worklist = threshold_solve(graph);
    const ThresholdResult sweep    = threshold_solve_sweep(graph);

    const bool agree = worklist.tau == sweep.tau;
    const double worklistMs = double(std::max<int64_t>(1, worklist.elapsedMs));
    const double sweepMs    = double(std::max<int64_t>(1, sweep.elapsedMs));

    out << std::fixed << std::setprecision(0);
    out << "  worklist  " << worklist.elapsedMs << " ms   "
        << double(graph.node_count()) * 1000.0 / worklistMs << " states/s   "
        << double(worklist.edgeReads) * 1000.0 / worklistMs << " edge-reads/s   "
        << worklist.relaxations << " relaxations, " << worklist.lowerings
        << " lowerings\n";
    out << "  sweep     " << sweep.elapsedMs << " ms   "
        << double(graph.node_count()) * 1000.0 / sweepMs << " states/s   "
        << double(sweep.edgeReads) * 1000.0 / sweepMs << " edge-reads/s\n";
    out << "  memory    graph " << (graphBytes >> 20) << " MiB, worklist index "
        << (solverBytes >> 20) << " MiB\n";
    out << "  agreement " << (agree ? "exact" : "MISMATCH")
        << ", sweep monotone " << (sweep.monotone ? "yes" : "NO") << "\n";
    out << std::defaultfloat;
}

}  // namespace Stockfish::Survive50
