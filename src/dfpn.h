/*
  Atomic-Stockfish df-pn solver: an exact AND/OR prover over the engine's own
  Atomic move generator, with a proof certificate as its output.

  It is not a search in the usual sense. The engine's evaluation, its
  transposition table and its NNUE play no part here: this module answers
  "can the attacker force the goal from this position, under the frozen
  ruleset" with PROVED / DISPROVED / UNKNOWN, and when it answers PROVED it
  writes a certificate that a completely separate implementation can replay.
*/

#ifndef DFPN_H_INCLUDED
#define DFPN_H_INCLUDED

#include <cstdint>
#include <iosfwd>
#include <string>

#include "position.h"
#include "types.h"

namespace Stockfish::DFPN {

// Goal of the proposition being proved, in absolute colours. The DAG stores
// everything from White's point of view and so does this.
enum class Goal {
    WhiteWin,
    BlackWin
};

enum class Outcome {
    Proved,
    Disproved,
    Unknown
};

struct Limits {
    // Search budget in expanded nodes. Zero means "no node limit".
    uint64_t nodes = 10'000'000;
    // Wall-clock budget in milliseconds. Zero means "no time limit".
    int64_t movetime = 0;
    // Transposition table size for the pn/dn heuristic, in MiB.
    size_t hashMB = 256;
    // Hard ceilings on the emitted certificate; a proof that does not fit
    // reports PROVED without one rather than writing a truncated tree.
    uint64_t certificateNodes = 2'000'000;
    int      certificateDepth = 512;
};

// Fortress classifier telemetry (doc 18 §5).  Four cheap indicators that say
// how much this position LOOKS like a drag-out fortress.  They are advisory:
// they steer scheduling and they never, ever authorise a verdict.
struct Telemetry {
    double ttHitRate = 0.0;       // transposition hits / probes
    double quietSccShare = 0.0;   // quiet transpositions / expansions
    double resetRate = 0.0;       // zeroing moves / moves generated
    double stagnation = 0.0;      // root pn+dn growth over the last stretch
    int    score = 0;             // how many of the four fired (0..4)
};

// Thresholds straight from doc 18 §5. Three of four is the classifier.
constexpr double FORTRESS_TT_HIT = 0.65;
constexpr double FORTRESS_QUIET_SCC = 0.50;
constexpr double FORTRESS_RESET_MAX = 0.05;
constexpr double FORTRESS_STAGNATION_MAX = 2.0;

struct Result {
    Outcome  outcome = Outcome::Unknown;
    Telemetry telemetry;
    uint64_t rootPn = 1;
    uint64_t rootDn = 1;
    // ``nodes`` counts df-pn expansions; ``positions`` counts board positions
    // actually made and unmade. The second is the number comparable with the
    // Python prover's budget, and it is roughly branching times the first.
    uint64_t nodes = 0;
    uint64_t positions = 0;
    int64_t  elapsedMs = 0;
    // Populated only when the certificate fits inside the limits.
    bool        haveCertificate = false;
    uint64_t    certificateNodes = 0;
    std::string certificate;
};

// The identity of the statement being proved. It travels inside every
// certificate so a verifier cannot silently apply different rules.
constexpr const char* RULESET_ID = "atomic-fide-claim-v1";
constexpr const char* CERTIFICATE_FORMAT = "atomicdb-proof/1";
// Plies of the fifty-move counter at which the defender may claim a draw.
constexpr int FIFTY_MOVE_PLIES = 100;

Result solve(Position& pos, Goal goal, const Limits& limits);

// Parses "solve <fen> [goal WHITE_WIN|BLACK_WIN] [nodes N] [movetime MS]
// [hash MB] [certfile PATH]" and prints a plain-text report.
void solve_command(std::istringstream& is, std::ostream& out);

// Built-in regression suite: known atomic mates, trivial refutations and the
// budget-exhaustion contract. Returns the number of failures.
int solve_selftest(std::ostream& out);

// Node and position throughput on a fixed reference position.
void solve_bench(std::istringstream& is, std::ostream& out);

}  // namespace Stockfish::DFPN

#endif  // #ifndef DFPN_H_INCLUDED
