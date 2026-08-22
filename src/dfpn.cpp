/*
  Atomic-Stockfish df-pn solver.

  WHY THIS EXISTS
  ---------------
  AtomicDB's only prover was a Python AND/OR search of about 200k positions,
  which is why its online proof deadline is twenty seconds and why anything
  past a mate in ten is out of reach. This is the same question asked of the
  engine's own move generator: two to three orders of magnitude more proof
  muscle per second of wall clock, and — the part that actually matters — a
  CERTIFICATE that something else can check.

  WHAT IT PROVES
  --------------
  "The attacker can force <goal> from this position", under the frozen ruleset
  `atomic-fide-claim-v1`: the defender may ALWAYS claim a draw on reaching 50
  moves without a capture or pawn move, and mate/explosion take precedence
  over that adjudication. Proving against a defender who claims as soon as it
  may is the pessimistic reading, so a proof here also holds under the more
  forgiving variants.

  ALGORITHM
  ---------
  Depth-first proof-number search (df-pn), the standard Nagai formulation:
  a recursive procedure with (thpn, thdn) thresholds and a transposition table
  holding pn/dn. Depth-first is what makes 10^8 nodes possible at all —
  ordinary PNS keeps its whole tree in memory.

  GRAPH HISTORY INTERACTION
  -------------------------
  Whether a continuation repeats depends on the branch, not on the board, so
  the two are kept strictly apart:

    * the TT stores pn/dn ONLY. They are heuristics — a wrong pn costs time,
      never correctness.
    * repetition legality is decided by the PATH: a position already on the
      current branch is a draw by repetition, and therefore a failed branch
      for the attacker. This is the same discipline the Python prover uses,
      and the reason it deliberately has no transposition cache.
    * the fifty-move counter travels in the position itself; a branch where
      the defender reaches FIFTY_MOVE_PLIES before the goal is a failed
      branch for the attacker.

  Because a proved/disproved verdict is never read back out of the TT, a
  collision or an overwrite cannot manufacture a proof. It can only make the
  solver redo work.

  THE CERTIFICATE
  ---------------
  After the root is proved, a separate pass writes the strategy tree out. That
  pass is itself a small exhaustive prover, ordered by the TT: it does not
  trust the TT, it re-derives every node it emits. What it writes is therefore
  true by construction, and it is exactly what the server's independent
  verifier will replay against a different move generator.

  Format, one node per line in DFS preorder (documented here because the file
  is its own specification):

      T <reason>              terminal node that achieves the goal
      O <move>                attacker to move plays <move>; its subtree follows
      A <n> <m1> ... <mn>     defender to move has exactly these n legal moves;
                              their n subtrees follow, in this order

  Preorder makes the structure implicit: no indices, no back-references, and
  the whole thing streams. An OR node costs about seven bytes, an AND node
  about five per legal reply.
*/

#include "dfpn.h"

#include <algorithm>
#include <chrono>
#include <cstdlib>
#include <fstream>
#include <iostream>
#include <sstream>
#include <string>
#include <unordered_set>
#include <vector>

#include "movegen.h"
#include "position.h"
#include "types.h"
#include "uci_move.h"

namespace Stockfish::DFPN {

namespace {

// Infinity of the proof arithmetic. Far from any real pn/dn and small enough
// that adding a handful of them cannot wrap a 64-bit unsigned.
constexpr uint64_t INF = 1ULL << 62;

uint64_t saturating_add(uint64_t a, uint64_t b) {
    if (a >= INF || b >= INF || a > INF - b)
        return INF;
    return a + b;
}

int64_t now_ms() {
    using namespace std::chrono;
    return duration_cast<milliseconds>(steady_clock::now().time_since_epoch()).count();
}

// One transposition entry. Deliberately tiny and lossy: it carries no verdict,
// only the two numbers that steer the descent.
struct TTEntry {
    Key      key = 0;
    uint64_t pn = 1;
    uint64_t dn = 1;
};

class ProofTT {
   public:
    void resize(size_t mb) {
        size_t entries = std::max<size_t>(1024, (mb * 1024 * 1024) / sizeof(TTEntry));
        size_t power = 1;
        while (power * 2 <= entries)
            power *= 2;
        table.assign(power, TTEntry{});
        mask = power - 1;
    }

    bool probe(Key key, uint64_t& pn, uint64_t& dn) {
        ++probes;
        const TTEntry& e = table[key & mask];
        if (e.key != key)
            return false;
        ++hits;
        pn = e.pn;
        dn = e.dn;
        return true;
    }

    uint64_t probe_count() const { return probes; }
    uint64_t hit_count() const { return hits; }

    void store(Key key, uint64_t pn, uint64_t dn) {
        TTEntry& e = table[key & mask];
        e.key = key;
        e.pn = pn;
        e.dn = dn;
    }

   private:
    std::vector<TTEntry> table;
    size_t               mask = 0;
    uint64_t             probes = 0;
    uint64_t             hits = 0;
};

// What a node is, before any search happens.
enum class NodeKind {
    GoalReached,   // terminal and the goal holds here
    GoalDenied,    // terminal, or adjudicated, and the goal fails here
    Interior
};

class Solver {
   public:
    Solver(Goal solveGoal, const Limits& solveLimits) :
        goal(solveGoal),
        limits(solveLimits) {
        tt.resize(std::max<size_t>(1, limits.hashMB));
        deadline = limits.movetime > 0 ? now_ms() + limits.movetime : 0;
    }

    Result run(Position& pos) {
        const int64_t started = now_ms();
        path.clear();
        path.insert(pos.repetition_key());

        uint64_t pn = 1, dn = 1;
        // Baseline for the stagnation indicator: what the root bound looked
        // like before any search happened.
        {
            uint64_t basePn = 1, baseDn = 1;
            lookup(pos, basePn, baseDn);
            lastBound = saturating_add(std::min(basePn, INF),
                                       std::min(baseDn, INF));
        }
        mid(pos, INF, INF, 0, pn, dn);

        Result result;
        result.nodes = nodes;
        result.positions = positions;
        result.telemetry = telemetry(pn, dn);
        result.rootPn = pn;
        result.rootDn = dn;
        result.elapsedMs = now_ms() - started;
        if (pn == 0)
            result.outcome = Outcome::Proved;
        else if (dn == 0)
            result.outcome = Outcome::Disproved;
        else
            result.outcome = Outcome::Unknown;

        if (result.outcome == Outcome::Proved)
            emit(pos, result);
        return result;
    }

   private:
    // ---- fortress telemetry (advisory only) ----

    Telemetry telemetry(uint64_t rootPn, uint64_t rootDn) const {
        Telemetry out;
        const uint64_t probes = tt.probe_count();
        if (probes)
            out.ttHitRate = double(tt.hit_count()) / double(probes);
        // Normalised over MOVES, not over nodes: a probe happens once per
        // child, so dividing by expansions would let this exceed 1 and stop
        // being a share of anything.
        if (movesSeen)
            out.quietSccShare = double(quietTranspositions) / double(movesSeen);
        if (movesSeen)
            out.resetRate = double(zeroingSeen) / double(movesSeen);
        // Growth of the root bound over the last checkpoint. A bound that has
        // barely moved while a million nodes went by is the signature of a
        // search that is going round in circles rather than closing in.
        const uint64_t bound = saturating_add(std::min(rootPn, INF),
                                              std::min(rootDn, INF));
        out.stagnation = lastBound ? double(bound) / double(lastBound) : 0.0;

        out.score = (out.ttHitRate >= FORTRESS_TT_HIT)
                  + (out.quietSccShare >= FORTRESS_QUIET_SCC)
                  + (out.resetRate <= FORTRESS_RESET_MAX && movesSeen > 0)
                  + (lastBound != 0 && out.stagnation < FORTRESS_STAGNATION_MAX);
        return out;
    }

    // ---- rules ----

    bool attacker_is_white() const { return goal == Goal::WhiteWin; }

    bool attacker_to_move(const Position& pos) const {
        return (pos.side_to_move() == WHITE) == attacker_is_white();
    }

    // Terminal classification under the frozen ruleset. Nothing here consults
    // an evaluation: a node is decided by the rules or it is interior.
    //
    // ``knownMoveCount`` lets a caller that has ALREADY generated the legal
    // list pass its size in.  Movegen is by far the hottest thing here, and
    // the naive shape called it three times per child: once to classify, once
    // to size the leaf, once to iterate.
    NodeKind classify(Position& pos, int knownMoveCount = -1) const {
        const Color attacker = attacker_is_white() ? WHITE : BLACK;
        const Color defender = ~attacker;

        // Atomic ends the moment a king is gone, whatever the counters say.
        if (!pos.has_king(defender))
            return NodeKind::GoalReached;
        if (!pos.has_king(attacker))
            return NodeKind::GoalDenied;

        const bool anyMove =
          knownMoveCount >= 0 ? knownMoveCount != 0 : pos.has_legal_move();
        if (!anyMove)
        {
            // No legal move: mate if in check, stalemate otherwise. A
            // stalemate is a draw, which denies a win proposition.
            if (!pos.atomic_in_check(pos.side_to_move()))
                return NodeKind::GoalDenied;
            return pos.side_to_move() == defender ? NodeKind::GoalReached
                                                  : NodeKind::GoalDenied;
        }

        // The defender may claim on reaching fifty moves without a reset, so
        // a branch that gets there without the goal is a failed branch.
        if (pos.rule50_count() >= FIFTY_MOVE_PLIES)
            return NodeKind::GoalDenied;

        return NodeKind::Interior;
    }

    bool budget_spent() {
        if (limits.nodes && nodes >= limits.nodes)
            return true;
        // Checking the clock is not free; once every 4096 nodes is plenty for
        // a job measured in tens of seconds.
        if (deadline && (nodes & 0xFFF) == 0 && now_ms() >= deadline)
            return true;
        return false;
    }

    // ---- df-pn ----

    // Returns true when the value handed back is BRANCH-LOCAL: derived, here
    // or through a child, from a repetition on the current path.
    //
    // The repetition guard below has always declared that such a fact must
    // never reach the table, and the aggregate carried it there anyway. A
    // repeating child contributes dn = 0, its parent inherits that through
    // ``aggregate``, and ``store`` then publishes a refutation that is only
    // true while those particular ancestors are on the board. Read back on a
    // branch where the repetition does not exist, it refutes a line that wins,
    // and the root comes back UNKNOWN with budget to spare.
    //
    // A tainted value is still correct for the path that produced it, so it is
    // used here and simply not published.
    bool mid(Position& pos, uint64_t thpn, uint64_t thdn, int depth,
             uint64_t& pn, uint64_t& dn) {

        // One movegen for the whole node: classification, budgeting and the
        // child loop all read the same list.
        const bool hasKings = pos.has_king(WHITE) && pos.has_king(BLACK);
        std::vector<Move> moves;
        if (hasKings)
        {
            moves.reserve(48);
            for (const auto& m : MoveList<LEGAL>(pos))
                moves.push_back(m);
        }

        const NodeKind kind = classify(pos, int(moves.size()));
        if (kind == NodeKind::GoalReached)
        {
            pn = 0;
            dn = INF;
            return false;
        }
        if (kind == NodeKind::GoalDenied)
        {
            pn = INF;
            dn = 0;
            return false;
        }

        ++nodes;
        if (budget_spent() || depth >= maxDepth)
        {
            // Out of budget: the node stays genuinely unknown. Reporting
            // anything else here is how solvers invent proofs.
            pn = 1;
            dn = 1;
            return false;
        }

        const bool orNode = attacker_to_move(pos);
        ++expansions;
        movesSeen += moves.size();

        std::vector<uint64_t> childPn(moves.size(), 1);
        std::vector<uint64_t> childDn(moves.size(), 1);
        std::vector<bool>     repeats(moves.size(), false);
        bool                  tainted = false;

        StateInfo st;
        for (size_t i = 0; i < moves.size(); ++i)
        {
            const bool zeroing = pos.rule50_count() == 0;
            pos.do_move(moves[i], st);
            ++positions;
            if (pos.rule50_count() == 0)
                ++zeroingSeen;
            else if (!zeroing)
            {
                // A quiet move landing on a position we have already scored is
                // a step around a quiet cycle: the cheap stand-in for "how big
                // is the quiet SCC" that doc 18 says an approximation suffices
                // for.
                uint64_t seenPn = 0, seenDn = 0;
                if (tt.probe(pos.key(), seenPn, seenDn))
                    ++quietTranspositions;
            }
            if (path.count(pos.repetition_key()))
            {
                // A repetition on THIS branch. The defender can hold the draw,
                // so for the attacker the branch is dead; the fact is
                // branch-local and must never reach the TT.
                repeats[i] = true;
                tainted    = true;
                childPn[i] = INF;
                childDn[i] = 0;
            }
            else
            {
                uint64_t p = 1, d = 1;
                lookup(pos, p, d);
                childPn[i] = p;
                childDn[i] = d;
            }
            pos.undo_move(moves[i]);
        }

        for (;;)
        {
            aggregate(orNode, childPn, childDn, pn, dn);
            if (pn >= thpn || dn >= thdn || pn == 0 || dn == 0)
                break;
            if (budget_spent())
            {
                pn = std::max<uint64_t>(pn, 1);
                dn = std::max<uint64_t>(dn, 1);
                break;
            }

            size_t   best = 0;
            uint64_t childThPn = 0, childThDn = 0;
            select(orNode, childPn, childDn, thpn, thdn, repeats, best, childThPn,
                   childThDn);
            if (best == moves.size())
                break;

            pos.do_move(moves[best], st);
            ++positions;
            const Key childKey = pos.key();
            const Key childRep = pos.repetition_key();
            path.insert(childRep);
            uint64_t p = childPn[best], d = childDn[best];
            if (mid(pos, childThPn, childThDn, depth + 1, p, d))
                tainted = true;
            else
                store(childKey, p, d);
            path.erase(childRep);
            pos.undo_move(moves[best]);
            childPn[best] = p;
            childDn[best] = d;
        }
        if (!tainted)
            store(pos.key(), pn, dn);
        return tainted;
    }

    void lookup(Position& pos, uint64_t& pn, uint64_t& dn) {
        // Probe first: a hit needs no rules work at all, and the TT holds only
        // heuristics, so nothing about correctness rides on this order.
        if (tt.probe(pos.key(), pn, dn))
        {
            if (pn != 0 && dn != 0)
                return;
            // A stored 0 would be a verdict, and verdicts are never trusted
            // out of the TT. Fall through and re-derive.
        }
        const bool     hasKings = pos.has_king(WHITE) && pos.has_king(BLACK);
        const size_t   branching =
          hasKings ? MoveList<LEGAL>(pos).size() : size_t(0);
        const NodeKind kind = classify(pos, int(branching));
        if (kind == NodeKind::GoalReached)
        {
            pn = 0;
            dn = INF;
            return;
        }
        if (kind == NodeKind::GoalDenied)
        {
            pn = INF;
            dn = 0;
            return;
        }
        // Leaf initialisation. The defender's branching is the one honest
        // signal available without an evaluation: proving costs every reply,
        // refuting costs one.
        const uint64_t width = std::max<uint64_t>(1, branching);
        if (attacker_to_move(pos))
        {
            pn = 1;
            dn = width;
        }
        else
        {
            pn = width;
            dn = 1;
        }
    }

    static void aggregate(bool orNode, const std::vector<uint64_t>& childPn,
                          const std::vector<uint64_t>& childDn, uint64_t& pn,
                          uint64_t& dn) {
        if (childPn.empty())
        {
            pn = INF;
            dn = 0;
            return;
        }
        if (orNode)
        {
            pn = INF;
            dn = 0;
            for (size_t i = 0; i < childPn.size(); ++i)
            {
                pn = std::min(pn, childPn[i]);
                dn = saturating_add(dn, childDn[i]);
            }
        }
        else
        {
            pn = 0;
            dn = INF;
            for (size_t i = 0; i < childPn.size(); ++i)
            {
                pn = saturating_add(pn, childPn[i]);
                dn = std::min(dn, childDn[i]);
            }
        }
    }

    static void select(bool orNode, const std::vector<uint64_t>& childPn,
                       const std::vector<uint64_t>& childDn, uint64_t thpn,
                       uint64_t thdn, const std::vector<bool>& repeats, size_t& best,
                       uint64_t& childThPn, uint64_t& childThDn) {
        const size_t n = childPn.size();
        best = n;
        uint64_t bestValue = INF, secondValue = INF, otherSum = 0;
        for (size_t i = 0; i < n; ++i)
        {
            if (repeats[i])
                continue;   // settled by the branch, never re-searched
            const uint64_t value = orNode ? childPn[i] : childDn[i];
            const uint64_t other = orNode ? childDn[i] : childPn[i];
            otherSum = saturating_add(otherSum, other);
            if (value < bestValue)
            {
                secondValue = bestValue;
                bestValue = value;
                best = i;
            }
            else if (value < secondValue)
                secondValue = value;
        }
        if (best == n)
            return;

        const uint64_t otherRest = otherSum >= (orNode ? childDn[best] : childPn[best])
                                   ? otherSum - (orNode ? childDn[best] : childPn[best])
                                   : 0;
        const uint64_t threshold =
          secondValue >= INF ? INF : std::min<uint64_t>(INF, secondValue + 1);
        if (orNode)
        {
            childThPn = std::min(thpn, threshold);
            childThDn = thdn >= otherRest ? std::min<uint64_t>(INF, thdn - otherRest) : 0;
        }
        else
        {
            childThDn = std::min(thdn, threshold);
            childThPn = thpn >= otherRest ? std::min<uint64_t>(INF, thpn - otherRest) : 0;
        }
        childThPn = std::max<uint64_t>(childThPn, 1);
        childThDn = std::max<uint64_t>(childThDn, 1);
    }

    void store(Key key, uint64_t pn, uint64_t dn) { tt.store(key, pn, dn); }

    // Move-ordering hint for the certificate extractor, and for nothing else.
    //
    // ``lookup`` deliberately throws away a stored zero, because a verdict out
    // of the table is not evidence. That is right for the search, and it is
    // what left the extractor blind: a child the search had PROVED came back
    // scored by its branching factor, exactly like a child nobody had ever
    // looked at, so the winning move sorted at random among its siblings and
    // the extractor brute-forced the position all over again.
    //
    // Here the zero is handed back, because the extractor uses it only to
    // decide which move to try FIRST. Every node it writes is still re-derived
    // and re-checked by ``emit_node``, so a stale or wrong hint costs a wasted
    // descent and can never put an unproved line into a certificate. Only the
    // proof side is surfaced: a stored disproof is not used to skip a move,
    // since that would be the extractor trusting the table to prune.
    void order_hint(Position& pos, uint64_t& pn, uint64_t& dn) {
        uint64_t storedPn = 0, storedDn = 0;
        if (tt.probe(pos.key(), storedPn, storedDn) && storedPn == 0)
        {
            pn = 0;
            dn = storedDn;
            return;
        }
        lookup(pos, pn, dn);
    }

    // ---- certificate ----

    void emit(Position& pos, Result& result) {
        certLines.clear();
        certNodes = 0;
        certBudget = limits.certificateNodes ? limits.certificateNodes * 8 : 0;
        certVisited = 0;
        path.clear();
        path.insert(pos.repetition_key());
        if (!emit_node(pos, 0))
            return;

        std::ostringstream out;
        out << "# " << CERTIFICATE_FORMAT << '\n';
        out << "ruleset " << RULESET_ID << '\n';
        out << "goal " << (goal == Goal::WhiteWin ? "WHITE_WIN" : "BLACK_WIN") << '\n';
        out << "root " << pos.fen() << '\n';
        out << "nodes " << certNodes << '\n';
        out << "---\n";
        for (const std::string& line : certLines)
            out << line << '\n';
        result.certificate = out.str();
        result.certificateNodes = certNodes;
        result.haveCertificate = true;
    }

    // A small exhaustive prover in its own right. It is ordered by the TT but
    // it does not believe it: every node it writes has been re-derived here.
    bool emit_node(Position& pos, int depth) {
        if (certBudget && ++certVisited > certBudget)
            return false;
        if (depth > limits.certificateDepth)
            return false;
        if (limits.certificateNodes && certNodes >= limits.certificateNodes)
            return false;

        const bool hasKings = pos.has_king(WHITE) && pos.has_king(BLACK);
        std::vector<Move> moves;
        if (hasKings)
            for (const auto& m : MoveList<LEGAL>(pos))
                moves.push_back(m);

        const NodeKind kind = classify(pos, int(moves.size()));
        if (kind == NodeKind::GoalReached)
        {
            certLines.push_back(std::string("T ") + terminal_reason(pos));
            ++certNodes;
            return true;
        }
        if (kind == NodeKind::GoalDenied)
            return false;
        if (moves.empty())
            return false;

        StateInfo st;
        if (attacker_to_move(pos))
        {
            // Order by the TT's opinion, then try them for real.
            std::vector<std::pair<uint64_t, size_t>> order;
            order.reserve(moves.size());
            for (size_t i = 0; i < moves.size(); ++i)
            {
                pos.do_move(moves[i], st);
                uint64_t p = 1, d = 1;
                if (path.count(pos.repetition_key()))
                    p = INF;
                else
                    order_hint(pos, p, d);
                pos.undo_move(moves[i]);
                order.emplace_back(p, i);
            }
            std::stable_sort(order.begin(), order.end());

            const size_t mark = certLines.size();
            const uint64_t markNodes = certNodes;
            for (const auto& [score, index] : order)
            {
                if (score >= INF)
                    continue;
                certLines.push_back(std::string("O ") + UCI::move(moves[index], pos.is_chess960()));
                ++certNodes;
                pos.do_move(moves[index], st);
                const Key childKey = pos.repetition_key();
                const bool fresh = path.insert(childKey).second;
                const bool ok = fresh && emit_node(pos, depth + 1);
                if (fresh)
                    path.erase(childKey);
                pos.undo_move(moves[index]);
                if (ok)
                    return true;
                certLines.resize(mark);
                certNodes = markNodes;
            }
            return false;
        }

        // Defender to move: every legal reply has to be covered, in movegen
        // order, and the verifier will regenerate exactly this list.
        std::string header = "A " + std::to_string(moves.size());
        for (const Move& m : moves)
            header += " " + UCI::move(m, pos.is_chess960());
        certLines.push_back(header);
        ++certNodes;
        const size_t mark = certLines.size() - 1;
        const uint64_t markNodes = certNodes - 1;
        for (const Move& m : moves)
        {
            pos.do_move(m, st);
            const Key childKey = pos.repetition_key();
            const bool fresh = path.insert(childKey).second;
            const bool ok = fresh && emit_node(pos, depth + 1);
            if (fresh)
                path.erase(childKey);
            pos.undo_move(m);
            if (!ok)
            {
                certLines.resize(mark);
                certNodes = markNodes;
                return false;
            }
        }
        return true;
    }

    const char* terminal_reason(Position& pos) const {
        const Color attacker = attacker_is_white() ? WHITE : BLACK;
        if (!pos.has_king(~attacker))
            return "explosion";
        return "mate";
    }

    Goal   goal;
    Limits limits;
    ProofTT tt;
    // Set, not multiset: a key already on the path is never descended
    // into, so it can appear at most once.
    std::unordered_set<Key> path;
    uint64_t nodes = 0;
    uint64_t positions = 0;
    uint64_t expansions = 0;
    uint64_t movesSeen = 0;
    uint64_t zeroingSeen = 0;
    uint64_t quietTranspositions = 0;
    uint64_t lastBound = 0;
    int64_t  deadline = 0;
    int      maxDepth = 512;

    std::vector<std::string> certLines;
    uint64_t                 certNodes = 0;
    uint64_t                 certBudget = 0;
    uint64_t                 certVisited = 0;
};

}  // namespace

Result solve(Position& pos, Goal goal, const Limits& limits) {
    Solver solver(goal, limits);
    return solver.run(pos);
}

namespace {

struct SelfTestCase {
    const char* name;
    const char* fen;
    Goal        goal;
    Outcome     expected;
    uint64_t    nodes;
    bool        wantCertificate;
};

// Every position here is legal for THIS engine's parser: the Python prover's
// synthetic fixtures put pawns on the eighth rank, which the engine rejects,
// so the shapes are reproduced rather than copied.
constexpr SelfTestCase SELF_TESTS[] = {
  // One move: Qa1xg7 explodes g7 and h8 with it. The shortest atomic win
  // there is.
  {"mate-in-1-explosion", "7k/6p1/8/8/8/8/8/Q3K3 w - - 0 1", Goal::WhiteWin,
   Outcome::Proved, 1'000'000, true},
  // The queen needs a move to reach the diagonal, so Black gets a reply.
  {"mate-with-one-defence", "7k/6p1/8/8/8/8/8/3QK3 w - - 0 1", Goal::WhiteWin,
   Outcome::Proved, 1'000'000, true},
  {"mate-from-b4", "7k/6p1/8/8/1Q6/8/8/4K3 w - - 0 1", Goal::WhiteWin,
   Outcome::Proved, 1'000'000, true},
  // A real defensive tree: Black has a rook and two pawns to interpose with.
  {"mate-against-a-rook", "r6k/6pp/8/8/8/8/6PP/3QK2R w K - 0 1",
   Goal::WhiteWin, Outcome::Proved, 5'000'000, true},
  // Stalemate: Rb7 covers a7 and b8 and an atomic king may not capture. A
  // draw denies a win proposition outright.
  {"stalemate-refutes-a-win", "k7/1R6/8/8/8/6K1/8/8 b - - 0 1",
   Goal::WhiteWin, Outcome::Disproved, 1'000'000, false},
  // Same board, other proposition: Black cannot win a position with no moves.
  {"stalemate-refutes-both", "k7/1R6/8/8/8/6K1/8/8 b - - 0 1", Goal::BlackWin,
   Outcome::Disproved, 1'000'000, false},
  // One node of budget cannot decide the opening, and the solver must say so
  // rather than guess. This is the contract that keeps it honest.
  {"budget-exhaustion-is-unknown",
   "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1", Goal::WhiteWin,
   Outcome::Unknown, 2, false},
};

const char* outcome_name(Outcome outcome) {
    return outcome == Outcome::Proved      ? "PROVED"
         : outcome == Outcome::Disproved   ? "DISPROVED"
                                           : "UNKNOWN";
}

}  // namespace

int solve_selftest(std::ostream& out) {
    int failures = 0;
    for (const SelfTestCase& test : SELF_TESTS)
    {
        StateInfo st;
        Position  pos;
        if (pos.set(test.fen, false, &st))
        {
            out << "FAIL " << test.name << ": engine rejected the FEN\n";
            ++failures;
            continue;
        }
        Limits limits;
        limits.nodes = test.nodes;
        limits.hashMB = 16;
        const Result result = solve(pos, test.goal, limits);
        bool ok = result.outcome == test.expected;
        if (ok && test.wantCertificate && !result.haveCertificate)
            ok = false;
        if (ok && test.expected == Outcome::Proved && result.rootPn != 0)
            ok = false;
        if (ok && test.expected == Outcome::Disproved && result.rootDn != 0)
            ok = false;
        out << (ok ? "ok   " : "FAIL ") << test.name << "  outcome="
            << outcome_name(result.outcome) << " nodes=" << result.nodes
            << " certnodes=" << result.certificateNodes << '\n';
        if (!ok)
            ++failures;
    }
    out << "solve_selftest: " << failures << " failure(s) out of "
        << (sizeof(SELF_TESTS) / sizeof(SELF_TESTS[0])) << std::endl;
    return failures;
}

void solve_bench(std::istringstream& is, std::ostream& out) {
    // A quiet four-piece endgame: the shape a SOLVE job would actually get.
    std::string fen = "8/8/8/8/8/5k2/6p1/Q3K3 w - - 0 1";
    uint64_t    budget = 3'000'000;
    std::string token;
    if (is >> token)
        budget = std::strtoull(token.c_str(), nullptr, 10);

    StateInfo st;
    Position  pos;
    if (pos.set(fen, false, &st))
    {
        out << "solve_bench error: invalid reference FEN" << std::endl;
        return;
    }
    Limits limits;
    limits.nodes = budget;
    limits.hashMB = 256;
    const Result result = solve(pos, Goal::WhiteWin, limits);
    const uint64_t ms = uint64_t(std::max<int64_t>(1, result.elapsedMs));
    out << "solve_bench fen " << fen << "\nsolve_bench nodes " << result.nodes
        << "\nsolve_bench positions " << result.positions << "\nsolve_bench ms "
        << result.elapsedMs << "\nsolve_bench nps " << result.nodes * 1000 / ms
        << "\nsolve_bench pps " << result.positions * 1000 / ms << std::endl;
}

void solve_command(std::istringstream& is, std::ostream& out) {
    std::string fen, token;
    Goal        goal = Goal::WhiteWin;
    Limits      limits;
    std::string certFile;

    // "solve <fen...> [goal X] [nodes N] [movetime MS] [hash MB] [certfile P]"
    std::vector<std::string> words;
    while (is >> token)
        words.push_back(token);

    size_t i = 0;
    std::vector<std::string> fenParts;
    while (i < words.size() && words[i] != "goal" && words[i] != "nodes"
           && words[i] != "movetime" && words[i] != "hash" && words[i] != "certfile"
           && words[i] != "certnodes")
        fenParts.push_back(words[i++]);
    for (size_t j = 0; j < fenParts.size(); ++j)
        fen += (j ? " " : "") + fenParts[j];

    while (i + 1 < words.size())
    {
        const std::string& name = words[i];
        const std::string& value = words[i + 1];
        if (name == "goal")
            goal = value == "BLACK_WIN" ? Goal::BlackWin : Goal::WhiteWin;
        else if (name == "nodes")
            limits.nodes = std::strtoull(value.c_str(), nullptr, 10);
        else if (name == "movetime")
            limits.movetime = std::strtoll(value.c_str(), nullptr, 10);
        else if (name == "hash")
            limits.hashMB = std::strtoull(value.c_str(), nullptr, 10);
        else if (name == "certnodes")
            limits.certificateNodes = std::strtoull(value.c_str(), nullptr, 10);
        else if (name == "certfile")
            certFile = value;
        i += 2;
    }

    if (fen.empty())
    {
        out << "solve error: no FEN given" << std::endl;
        return;
    }

    StateInfo st;
    Position  pos;
    if (auto error = pos.set(fen, false, &st))
    {
        out << "solve error: invalid FEN (" << error->what() << ")" << std::endl;
        return;
    }

    const Result result = solve(pos, goal, limits);
    out << "solve outcome "
        << (result.outcome == Outcome::Proved      ? "PROVED"
            : result.outcome == Outcome::Disproved ? "DISPROVED"
                                                   : "UNKNOWN")
        << "\nsolve pn " << (result.rootPn >= INF ? std::string("INF")
                                                  : std::to_string(result.rootPn))
        << "\nsolve dn " << (result.rootDn >= INF ? std::string("INF")
                                                  : std::to_string(result.rootDn))
        << "\nsolve nodes " << result.nodes << "\nsolve positions "
        << result.positions << "\nsolve ms " << result.elapsedMs << "\nsolve nps "
        << (result.elapsedMs > 0 ? result.nodes * 1000 / uint64_t(result.elapsedMs) : 0)
        << "\nsolve pps "
        << (result.elapsedMs > 0 ? result.positions * 1000 / uint64_t(result.elapsedMs)
                                 : 0)
        << "\nsolve certificate " << (result.haveCertificate ? "yes" : "no")
        << "\nsolve certnodes " << result.certificateNodes
        << "\nsolve fortress_tt_hit " << result.telemetry.ttHitRate
        << "\nsolve fortress_quiet_scc " << result.telemetry.quietSccShare
        << "\nsolve fortress_reset_rate " << result.telemetry.resetRate
        << "\nsolve fortress_stagnation " << result.telemetry.stagnation
        << "\nsolve fortress_score " << result.telemetry.score << std::endl;

    if (result.haveCertificate)
    {
        if (certFile.empty())
            out << result.certificate << std::flush;
        else
        {
            std::ofstream file(certFile, std::ios::binary);
            file << result.certificate;
        }
    }
}

}  // namespace Stockfish::DFPN
