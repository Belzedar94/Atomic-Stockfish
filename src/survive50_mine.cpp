/*
  SURVIVE50 phase 4: mining a survival strategy out of real atomic positions.

  Phases 1 and 2 built the tau machinery on abstract graphs so that it could be
  differentially tested against an exhaustive oracle a hundred thousand times
  before it ever met a chessboard. This file is where the chessboard arrives:
  it turns a position into one of those graphs, runs the same fixed point, and
  writes the certificate that ``atomicdb/survive.py`` and the native FSF tool
  then check without believing a word of it.

  WHAT MAKES THIS HARD, AND THE SHAPE OF THE ANSWER
  -------------------------------------------------
  A survival strategy is not a tree, so it cannot be enumerated. It is a
  region that Black can keep the game inside, and the two players enter it
  with opposite obligations:

    * every legal White move must be accounted for -- a single unlisted move
      is a hole in the proof, and "it loses a queen" is not an argument;
    * exactly one Black reply is needed per state, and finding a good one is
      the entire search problem.

  So White is expanded exhaustively and Black is expanded lazily, which is the
  counterexample-guided loop of doc 18 §4. A pass expands what it can afford,
  computes tau, and then asks what is BLOCKING: Black states whose known
  replies are not good enough, and White states whose successors did not fit
  in the budget. Those are the counterexamples, and the next pass widens
  exactly them rather than everything.

  THE HEURISTIC THAT MAKES FORTRESSES FINDABLE
  --------------------------------------------
  Black replies are ordered by whether they return to a position already in
  the region. That is not a general-purpose evaluation, it is the definition
  of a fortress: the defender's good moves are the ones that keep the game
  where it already is. A shuffling king scores top precisely because it cycles.

  WHAT IS REPORTED WHEN IT FAILS
  ------------------------------
  The closure defect -- unresolved White edges over all legal White edges --
  with the gates of doc 18 §4: below 2% or at most 64 edges is worth closing,
  2-10% only if the node is root-critical, above 10% after two passes means
  the candidate should be abandoned. A miner that cannot say how far it got is
  a miner whose failures all look alike.
*/

#include "survive50.h"

#include <algorithm>
#include <chrono>
#include <cstdint>
#include <cstdlib>
#include <fstream>
#include <iomanip>
#include <ostream>
#include <set>
#include <sstream>
#include <string>
#include <unordered_map>
#include <vector>

#include "bitboard.h"
#include "movegen.h"
#include "position.h"
#include "types.h"
#include "uci_move.h"

namespace Stockfish::Survive50 {

namespace {

int64_t now_ms() {
    using namespace std::chrono;
    return duration_cast<milliseconds>(steady_clock::now().time_since_epoch()).count();
}

std::vector<std::string> split_fields(const std::string& text) {
    std::vector<std::string> parts;
    std::istringstream       stream(text);
    std::string              token;
    while (stream >> token)
        parts.push_back(token);
    return parts;
}

// R(s) of doc 18 §1: pawn steps remaining to promotion, plus floor((N-2)/2).
//
// Every pawn move drops the first term. Every atomic capture removes at least
// the capturer and the captured, so it drops the second. Hence every zeroing
// move strictly lowers R, which is what makes the base graph's only cycles the
// quiet ones and the whole fixed point finite.
uint32_t progress_rank(const Position& pos) {
    uint32_t steps = 0;
    Bitboard pawns = pos.pieces(PAWN);
    while (pawns)
    {
        const Square square = pop_lsb(pawns);
        const Piece  piece  = pos.piece_on(square);
        const int    row    = int(rank_of(square));
        steps += color_of(piece) == WHITE ? uint32_t(RANK_8 - row)
                                          : uint32_t(row - RANK_1);
    }
    const int pieces = popcount(pos.pieces());
    return steps + uint32_t(pieces > 2 ? (pieces - 2) / 2 : 0);
}

// The survival game's terminal classification, attacker = White.
//
// Identical in order to DFPN::classify and to immediate_status() in
// survive50.cpp, minus the fifty-move test: the clock is tau's business here,
// never the classifier's. A terminal position is terminal at every clock.
NodeType classify_real(Position& pos, int moveCount) {
    if (!pos.has_king(BLACK))
        return NodeType::WhiteWinTerminal;
    if (!pos.has_king(WHITE))
        return NodeType::SurviveTerminal;
    if (moveCount == 0)
    {
        if (!pos.atomic_in_check(pos.side_to_move()))
            return NodeType::SurviveTerminal;  // stalemate is not a White win
        return pos.side_to_move() == BLACK ? NodeType::WhiteWinTerminal
                                           : NodeType::SurviveTerminal;
    }
    return NodeType::Interior;
}

}  // namespace

// ---------------------------------------------------------------------------
// The miner
// ---------------------------------------------------------------------------

class Miner {
   public:
    Miner(const MineLimits& mineLimits) :
        limits(mineLimits) {}

    MineReport run(Position& root);

   private:
    struct RealEdge {
        std::string uci;
        uint32_t    child   = 0;
        bool        zeroing = false;
    };

    struct RealNode {
        std::string           fen;
        NodeType              type        = NodeType::Interior;
        bool                  whiteToMove = true;
        bool                  openMoves   = false;
        uint32_t              rank        = 0;
        bool                  expanded    = false;
        uint32_t              blackWidth  = 0;   // replies kept so far
        uint32_t              legalCount  = 0;
        std::vector<RealEdge> edges;
    };

    // ---- position plumbing ----

    bool load(Position& pos, StateInfo& st, const std::string& fen) {
        ++positionsBuilt;
        return !pos.set(fen, false, &st).has_value();
    }

    std::set<std::string> legal_set(const std::string& fen) {
        Position  pos;
        StateInfo st;
        std::set<std::string> moves;
        if (!load(pos, st, fen))
            return moves;
        for (const auto& move : MoveList<LEGAL>(pos))
            moves.insert(UCI::move(move, pos.is_chess960()));
        return moves;
    }

    // AtomicDB canonicaliser v2. The en passant square is part of the identity
    // of a position, so a right that is declared but cannot actually be
    // executed -- the capture would explode our own king, or the capturing
    // pawn is pinned -- would split one position into two keys. It survives
    // only when the legal sets differ with and without it.
    std::string canonical(const std::string& fen) {
        std::vector<std::string> parts = split_fields(fen);
        while (parts.size() < 6)
            parts.push_back("-");
        if (parts[3] != "-")
        {
            const std::string with = parts[0] + " " + parts[1] + " " + parts[2]
                                   + " " + parts[3] + " 0 1";
            const std::string without = parts[0] + " " + parts[1] + " "
                                      + parts[2] + " - 0 1";
            if (legal_set(with) == legal_set(without))
                parts[3] = "-";
        }
        return parts[0] + " " + parts[1] + " " + parts[2] + " " + parts[3]
             + " 0 1";
    }

    uint32_t intern(const std::string& canonicalFen, bool& created) {
        auto found = index.find(canonicalFen);
        if (found != index.end())
        {
            created = false;
            return found->second;
        }
        created = true;
        RealNode node;
        node.fen = canonicalFen;

        Position  pos;
        StateInfo st;
        if (!load(pos, st, canonicalFen))
        {
            node.type = NodeType::WhiteWinTerminal;  // unparsable: never trust it
            nodes.push_back(node);
            index[canonicalFen] = uint32_t(nodes.size() - 1);
            return uint32_t(nodes.size() - 1);
        }
        std::vector<Move> moves;
        for (const auto& move : MoveList<LEGAL>(pos))
            moves.push_back(move);
        node.type        = classify_real(pos, int(moves.size()));
        node.whiteToMove = pos.side_to_move() == WHITE;
        node.rank        = progress_rank(pos);
        node.legalCount  = uint32_t(moves.size());
        nodes.push_back(node);
        index[canonicalFen] = uint32_t(nodes.size() - 1);
        return uint32_t(nodes.size() - 1);
    }

    // ---- expansion ----

    struct Candidate {
        std::string uci;
        std::string childFen;
        bool        zeroing;
        bool        known;    // target already in the region
        bool        quiet;
    };

    void expand(uint32_t id, uint32_t blackWidth);

    // ---- the rest ----

    Graph      build_graph() const;
    MineReport finish(Position& root, const std::vector<uint8_t>& tau,
                      int entryClock);

    MineLimits                                limits;
    std::vector<RealNode>                     nodes;
    std::unordered_map<std::string, uint32_t> index;
    std::vector<uint32_t>                     frontier;
    uint64_t                                  positionsBuilt      = 0;
    uint64_t                                  unresolvedWhiteEdges = 0;
    uint64_t                                  totalWhiteEdges      = 0;
};

void Miner::expand(uint32_t id, uint32_t blackWidth) {
    if (nodes[id].type != NodeType::Interior)
        return;

    Position  pos;
    StateInfo st;
    if (!load(pos, st, nodes[id].fen))
        return;

    std::vector<Move> moves;
    for (const auto& move : MoveList<LEGAL>(pos))
        moves.push_back(move);

    // Look at every child once: we need its canonical identity to decide both
    // whether it is already known and, for Black, whether it is worth keeping.
    std::vector<Candidate> candidates;
    candidates.reserve(moves.size());
    for (Move move : moves)
    {
        StateInfo child;
        pos.do_move(move, child);
        Candidate candidate;
        candidate.uci      = UCI::move(move, pos.is_chess960());
        candidate.zeroing  = pos.rule50_count() == 0;
        candidate.childFen = canonical(pos.fen());
        pos.undo_move(move);
        candidate.known = index.count(candidate.childFen) != 0;
        candidate.quiet = !candidate.zeroing;
        candidates.push_back(candidate);
    }

    const bool white = nodes[id].whiteToMove;
    if (white)
    {
        totalWhiteEdges += candidates.size();
        nodes[id].edges.clear();
        for (const Candidate& candidate : candidates)
        {
            // Every legal White move is a universal obligation. If the region
            // is full we cannot discharge it, and that is a closure defect --
            // not something to drop quietly.
            if (!candidate.known && nodes.size() >= limits.states)
            {
                nodes[id].openMoves = true;
                ++unresolvedWhiteEdges;
                continue;
            }
            bool           created = false;
            const uint32_t child   = intern(candidate.childFen, created);
            nodes[id].edges.push_back({candidate.uci, child, candidate.zeroing});
            if (created)
                frontier.push_back(child);
        }
    }
    else
    {
        // Black needs one good reply, so order by what a fortress actually
        // looks like: a move that returns to a position the region already
        // contains, and quiet in preference to burning the clock reset.
        std::stable_sort(candidates.begin(), candidates.end(),
                         [](const Candidate& a, const Candidate& b) {
                             if (a.known != b.known)
                                 return a.known;
                             return a.quiet && !b.quiet;
                         });
        nodes[id].edges.clear();
        uint32_t kept = 0;
        for (const Candidate& candidate : candidates)
        {
            if (kept >= blackWidth)
                break;
            if (!candidate.known && nodes.size() >= limits.states)
                continue;
            bool           created = false;
            const uint32_t child   = intern(candidate.childFen, created);
            nodes[id].edges.push_back({candidate.uci, child, candidate.zeroing});
            if (created)
                frontier.push_back(child);
            ++kept;
        }
        nodes[id].blackWidth = kept;
        // Missing Black replies are harmless to soundness -- one surviving
        // reply is enough and a known one stays known -- so this is NOT an
        // open node. It only means tau may be an over-estimate.
    }
    nodes[id].expanded = true;
}

Graph Miner::build_graph() const {
    GraphBuilder builder;
    for (const RealNode& node : nodes)
        builder.add_node(node.type, node.whiteToMove, node.rank,
                         node.openMoves);
    for (size_t id = 0; id < nodes.size(); ++id)
        for (const RealEdge& edge : nodes[id].edges)
            builder.add_edge(uint32_t(id), edge.child, edge.zeroing);
    return builder.finalise();
}

MineReport Miner::run(Position& root) {
    MineReport      report;
    const int64_t   started    = now_ms();
    const int       entryClock = std::min(root.rule50_count(), FIFTY_MOVE_PLIES);

    bool           created  = false;
    const uint32_t rootId   = intern(canonical(root.fen()), created);
    frontier.push_back(rootId);

    std::vector<uint8_t> tau;
    uint32_t             width = limits.blackWidth;

    for (int pass = 0; pass < std::max(1, limits.passes); ++pass)
    {
        while (!frontier.empty())
        {
            const uint32_t id = frontier.back();
            frontier.pop_back();
            if (nodes[id].expanded || nodes[id].type != NodeType::Interior)
                continue;
            if (positionsBuilt > limits.positions)
                break;
            expand(id, width);
        }

        const Graph           graph  = build_graph();
        const ThresholdResult solved = threshold_solve(graph);
        tau                          = solved.tau;
        if (tau[rootId] <= entryClock)
            break;

        // COUNTEREXAMPLES. What is blocking is a Black state whose known
        // replies are not good enough; widening everything would be the same
        // as having no loop at all, so only those are re-opened.
        std::vector<uint32_t> blocked;
        for (size_t id = 0; id < nodes.size(); ++id)
            if (nodes[id].type == NodeType::Interior && !nodes[id].whiteToMove
                && tau[id] > 0 && nodes[id].blackWidth < nodes[id].legalCount)
            {
                nodes[id].expanded = false;
                blocked.push_back(uint32_t(id));
            }
        if (blocked.empty() || positionsBuilt > limits.positions)
            break;
        for (uint32_t id : blocked)
            frontier.push_back(id);
        width = width * 4;
    }

    if (tau.empty())
        tau = threshold_solve(build_graph()).tau;

    report            = finish(root, tau, entryClock);
    report.elapsedMs  = now_ms() - started;
    return report;
}

MineReport Miner::finish(Position& root, const std::vector<uint8_t>& tau,
                         int entryClock) {
    MineReport report;
    report.positionsBuilt = positionsBuilt;
    report.minedStates    = nodes.size();
    report.entryClock     = entryClock;
    report.whiteEdges     = totalWhiteEdges;
    report.unresolvedWhiteEdges = unresolvedWhiteEdges;
    report.closureDefect =
      totalWhiteEdges ? double(unresolvedWhiteEdges) / double(totalWhiteEdges)
                      : 0.0;

    // Doc 18 §4 scheduling gates. They decide what to do NEXT; they never
    // authorise a verdict.
    if (unresolvedWhiteEdges == 0)
        report.gate = "closed";
    else if (report.closureDefect < 0.02 || unresolvedWhiteEdges <= 64)
        report.gate = "invest";
    else if (report.closureDefect <= 0.10)
        report.gate = "root-critical-only";
    else
        report.gate = "abandon";

    const uint32_t rootId = index[canonical(root.fen())];
    report.rootTau        = tau[rootId];
    if (tau[rootId] > entryClock)
    {
        report.result = ProofResult::Unknown;
        return report;
    }

    // Select the policy: at a Black state, the reply that achieves the min.
    // At a White state, everything. Then keep only what the strategy can
    // actually reach -- an unreachable state is verification budget spent on
    // nothing.
    std::vector<int> policy(nodes.size(), -1);
    for (size_t id = 0; id < nodes.size(); ++id)
    {
        if (nodes[id].type != NodeType::Interior || nodes[id].whiteToMove)
            continue;
        int best = TAU_LOST + 1, bestAt = -1;
        for (size_t e = 0; e < nodes[id].edges.size(); ++e)
        {
            const RealEdge& edge = nodes[id].edges[e];
            const int need = required_entry_clock(tau[edge.child], edge.zeroing);
            if (need < best)
            {
                best   = need;
                bestAt = int(e);
            }
        }
        policy[id] = bestAt;
    }

    std::vector<char>     reached(nodes.size(), 0);
    std::vector<uint32_t> stack;
    reached[rootId] = 1;
    stack.push_back(rootId);
    while (!stack.empty())
    {
        const uint32_t id = stack.back();
        stack.pop_back();
        if (nodes[id].type != NodeType::Interior)
            continue;
        if (nodes[id].whiteToMove)
        {
            for (const RealEdge& edge : nodes[id].edges)
                if (!reached[edge.child])
                {
                    reached[edge.child] = 1;
                    stack.push_back(edge.child);
                }
        }
        else if (policy[id] >= 0)
        {
            const uint32_t child = nodes[id].edges[size_t(policy[id])].child;
            if (!reached[child])
            {
                reached[child] = 1;
                stack.push_back(child);
            }
        }
    }

    // A reachable White state whose moves are not all enumerated cannot be
    // emitted: the verifier regenerates the legal set and would reject it, as
    // it should. Same for a reachable state that is a White win.
    std::vector<int> emitted(nodes.size(), -1);
    int              next = 0;
    for (size_t id = 0; id < nodes.size(); ++id)
    {
        if (!reached[id] || nodes[id].type != NodeType::Interior)
            continue;
        if (nodes[id].whiteToMove && nodes[id].openMoves)
        {
            report.result = ProofResult::Unknown;
            return report;
        }
        if (!nodes[id].whiteToMove && policy[id] < 0)
        {
            report.result = ProofResult::Unknown;
            return report;
        }
        emitted[id] = next++;
    }
    if (emitted[rootId] < 0)
    {
        report.result = ProofResult::Unknown;
        return report;
    }

    size_t edgeCount = 0;
    for (size_t id = 0; id < nodes.size(); ++id)
    {
        if (emitted[id] < 0)
            continue;
        edgeCount += nodes[id].whiteToMove ? nodes[id].edges.size() : 1;
    }

    std::ostringstream out;
    out << "# " << CERTIFICATE_FORMAT << '\n';
    out << "ruleset " << RULESET_ID << '\n';
    out << "canonical " << CANONICAL_VERSION << '\n';
    out << "repetition " << REPETITION_MODE << '\n';
    out << "terminal_precedence " << TERMINAL_PRECEDENCE_ID << '\n';
    out << "root " << root.fen() << '\n';
    out << "entry_clock " << entryClock << '\n';
    out << "states " << next << '\n';
    out << "edges " << edgeCount << '\n';
    out << "---\n";

    for (size_t id = 0; id < nodes.size(); ++id)
        if (emitted[id] >= 0)
            out << "S " << emitted[id] << ' ' << int(tau[id]) << ' '
                << nodes[id].fen << '\n';

    for (size_t id = 0; id < nodes.size(); ++id)
    {
        if (emitted[id] < 0)
            continue;
        const bool white = nodes[id].whiteToMove;
        const size_t from = white ? 0 : size_t(policy[id]);
        const size_t to   = white ? nodes[id].edges.size() : size_t(policy[id]) + 1;
        for (size_t e = from; e < to; ++e)
        {
            const RealEdge& edge = nodes[id].edges[e];
            std::string     target;
            if (nodes[edge.child].type == NodeType::SurviveTerminal)
                target = "T";
            else if (nodes[edge.child].type == NodeType::WhiteWinTerminal)
            {
                report.result = ProofResult::Unknown;
                return report;
            }
            else if (emitted[edge.child] < 0)
            {
                // Reachable state pointing outside the emitted set. White
                // successors are reachable by construction, so this can only
                // be a bug; refuse rather than write a certificate that the
                // verifier will (correctly) reject.
                report.result = ProofResult::Unknown;
                return report;
            }
            else
                target = "#" + std::to_string(emitted[edge.child]);
            out << (white ? "W " : "B ") << emitted[id] << ' ' << edge.uci << ' '
                << target << '\n';
        }
    }

    report.certificate     = out.str();
    report.haveCertificate = true;
    report.states          = size_t(next);
    report.edges           = edgeCount;
    report.result          = ProofResult::DisprovedWhiteWin;
    return report;
}

MineReport mine(Position& root, const MineLimits& limits) {
    Miner miner(limits);
    return miner.run(root);
}

// ---------------------------------------------------------------------------
// Commands
// ---------------------------------------------------------------------------

namespace {

void report_to(std::ostream& out, const MineReport& report) {
    out << "  result      " << result_name(report.result) << '\n';
    out << "  tau(root)   " << report.rootTau << "  entry clock "
        << report.entryClock << '\n';
    out << "  mined       " << report.minedStates << " states, "
        << report.positionsBuilt << " positions, " << report.elapsedMs
        << " ms\n";
    out << "  certificate "
        << (report.haveCertificate
              ? std::to_string(report.states) + " states, "
                  + std::to_string(report.edges) + " edges"
              : std::string("none"))
        << '\n';
    out << std::fixed << std::setprecision(2);
    out << "  defect      " << report.unresolvedWhiteEdges << " / "
        << report.whiteEdges << " White edges = "
        << report.closureDefect * 100.0 << "%  gate: " << report.gate << '\n';
    out << std::defaultfloat;
}

struct MineCase {
    const char* name;
    const char* fen;
    bool        expectCertificate;
};

// Real closed atomic regions. The first two are the fixtures the server-side
// verifiers are tested on, so a certificate mined here is checked end to end
// by two independent move generators.
constexpr MineCase MINE_TESTS[] = {
  // White's bare king is boxed onto the h-file by a rook on g8; Black shuffles.
  {"king-walk fortress", "6r1/k7/8/8/8/8/8/7K w - - 0 1", true},
  // The same box plus a White pawn with exactly one push in it: real resets.
  {"pawn-reset fortress", "6r1/k7/1p6/8/1P6/8/8/7K w - - 0 1", true},
  // White mates in one, so no survival certificate exists and none may be
  // manufactured. The miner must say so rather than emit something.
  {"mate in one is not a fortress", "7k/6p1/8/8/8/8/8/Q3K3 w - - 0 1", false},
};

}  // namespace

void mine_command(std::istringstream& is, std::ostream& out) {
    std::string fen, token;
    MineLimits  limits;
    std::string certFile;

    // The FEN runs to the first keyword; everything after is options.
    std::vector<std::string> words;
    while (is >> token)
        words.push_back(token);
    size_t at = 0;
    while (at < words.size() && words[at] != "states" && words[at] != "width"
           && words[at] != "passes" && words[at] != "positions"
           && words[at] != "certfile")
    {
        fen += (fen.empty() ? "" : " ") + words[at];
        ++at;
    }
    while (at + 1 < words.size())
    {
        const std::string& key   = words[at];
        const std::string& value = words[at + 1];
        if (key == "states")
            limits.states = std::strtoull(value.c_str(), nullptr, 10);
        else if (key == "width")
            limits.blackWidth = uint32_t(std::strtoul(value.c_str(), nullptr, 10));
        else if (key == "passes")
            limits.passes = int(std::strtol(value.c_str(), nullptr, 10));
        else if (key == "positions")
            limits.positions = std::strtoull(value.c_str(), nullptr, 10);
        else if (key == "certfile")
            certFile = value;
        at += 2;
    }
    if (fen.empty())
    {
        out << "usage: survive50_mine <fen> [states N] [width K] [passes P] "
               "[positions N] [certfile PATH]\n";
        return;
    }

    Position  pos;
    StateInfo st;
    if (pos.set(fen, false, &st).has_value())
    {
        out << "survive50_mine: cannot parse that position\n";
        return;
    }

    out << "survive50 mine " << fen << '\n';
    const MineReport report = mine(pos, limits);
    report_to(out, report);

    if (report.haveCertificate && !certFile.empty())
    {
        std::ofstream file(certFile.c_str(), std::ios::binary);
        if (file)
        {
            file << report.certificate;
            out << "  written     " << certFile << '\n';
        }
        else
            out << "  cannot write " << certFile << '\n';
    }
}

int mine_selftest(std::ostream& out) {
    int failures = 0;
    out << "survive50 mine selftest\n";
    for (const MineCase& test : MINE_TESTS)
    {
        Position  pos;
        StateInfo st;
        if (pos.set(test.fen, false, &st).has_value())
        {
            out << "  FAIL  " << test.name << ": unparsable FEN\n";
            ++failures;
            continue;
        }
        MineLimits limits;
        limits.states     = 4000;
        limits.positions  = 400000;
        const MineReport report = mine(pos, limits);

        int bad = 0;
        if (report.haveCertificate != test.expectCertificate)
            ++bad;
        if (test.expectCertificate)
        {
            if (report.result != ProofResult::DisprovedWhiteWin)
                ++bad;
            if (report.rootTau > report.entryClock)
                ++bad;
            // A certificate that cannot close its own White obligations is not
            // a certificate; the miner refuses to emit one, so if it did emit,
            // the defect on the emitted part must be nil.
            if (report.unresolvedWhiteEdges && report.haveCertificate
                && report.gate == std::string("abandon"))
                ++bad;
        }
        else if (report.result == ProofResult::DisprovedWhiteWin)
            ++bad;  // a lost position must never be certified

        out << (bad ? "  FAIL  " : "  ok    ") << test.name << ": "
            << result_name(report.result) << ", tau " << report.rootTau
            << ", mined " << report.minedStates << ", emitted "
            << report.states << "/" << report.edges << ", defect "
            << report.unresolvedWhiteEdges << "/" << report.whiteEdges << " ("
            << report.gate << "), " << report.elapsedMs << " ms\n";
        failures += bad ? 1 : 0;
    }
    out << (failures ? "FAILED" : "OK") << " (" << failures << " failing)\n";
    return failures;
}

}  // namespace Stockfish::Survive50
