# UB8: exploratory design notes (quiets in king rings, connected kings, pruning)

Design notes only, no code. Follows the UB1-UB7 ladder of the ubdip search
program. Line references are against `fable/mv1-futility` (c9edd7de).

The UB1-UB7 patches all fix the same class of bug: a *capture* heuristic that
prices the victim instead of the explosion. This document covers the part of the
program that has no orthodox counterpart at all, so there is nothing to fix,
only something to add. Two Atomic facts drive everything below.

## The two facts

**Fact A: a piece standing next to the enemy king cannot be captured.**
A capture explodes the ring around its destination square, and a capture that
explodes your own king is illegal. So if our piece sits on a square adjacent to
the enemy king, every enemy capture of it is illegal. The engine already knows
this, at `position.cpp:713-715` inside `Position::legal()`:

    // A capture by the king, or any capture next to our king, is a forbidden
    // self-explosion.
    if (!(occupied & ourKing))
        return false;

**Fact B: the same geometry is a catastrophe around our own king.**
Our piece adjacent to *our* king is not immune, it is a live grenade: an enemy
capture of it explodes our king and wins on the spot (`position.cpp:717-719`,
and `Position::atomic_wins()` at `position.cpp:743-752`).

So one identical quiet move, "knight to a square in a king ring", is close to
unanswerable next to their king and close to losing next to ours. No history
table in the engine can tell those apart today: quiet ordering is keyed by
`[piece][to]` and `[from][to]` (`history.h:125`, `:135`, `:140`, `:143`), and
king position is not part of any of those keys.

## Idea 1: king-ring bucket for quiet history

**Mechanism.** Give quiet history the same treatment UB5 gives capture history:
a small bucket describing the relation between the destination square and the two
kings. Four states cover it, and they are mutually exclusive in practice because
adjacency to both kings at once implies the kings are connected (Idea 3):

| bucket | meaning | expected sign |
|---|---|---|
| 0 | destination touches neither king | neutral, the ordinary case |
| 1 | destination touches the enemy king | the piece becomes uncapturable |
| 2 | destination touches our own king | any capture of it loses the game |
| 3 | destination touches both (kings connected) | mixed, see Idea 3 |

**Sites.** Quiet scoring is `movepick.cpp:219-244`. The candidates for the new
dimension, cheapest first:

- `lowPlyHistory` (`history.h:129`, read at `movepick.cpp:242-243`): smallest
  table, only 5 plies, and near the root is exactly where these hooks matter.
  Cheapest possible probe of the idea.
- `mainHistory` (`history.h:125`, read at `movepick.cpp:225`): indexed by
  `[color][from-to]`, already 64K entries per colour; multiplying by 4 costs
  1.5 MiB per thread. Acceptable but not cheap.
- `continuationHistory` / `pawnHistory` (`history.h:140`, `:143`, read at
  `movepick.cpp:226-231`, written through `update_quiet_histories`): the
  continuation tables are the ones already keyed by `[piece][to]`, so the bucket
  fits naturally, but `ContinuationHistory` is nested `PIECE_NB * SQUARE_NB` of
  `PieceToHistory` and a 4x on that is the most expensive option in the set.

Write sites must mirror the reads exactly, the same discipline UB5 needed: the
bucket has to come from a single `Position` helper (mirror
`Position::blast_ring_bucket()`) so a read and a write can never disagree. Note
that `update_quiet_histories` and the `(ss-1)` countermove bonuses run on a
position that is *after* the move, so the bucket has to be precomputed and
carried on the stack, the same problem UB4 and UB5 hit at the `statScore` site.

**Risks.**
1. Sparsity. Quiet history is already the thinnest-populated family; splitting it
   4-fold in a variant whose games are short could leave every bucket
   undertrained. This is the reason to start with `lowPlyHistory`.
2. The bucket is not stationary. Kings move, so the same `[piece][to]` entry
   migrates between buckets within a single search, unlike UB5's ring bucket
   which is a property of the destination geometry and is far more stable.
3. Cost per node. Two `attacks_bb<KING>` lookups plus two compares per quiet is
   small, but quiet scoring is the hottest loop in move ordering, and UB7 exists
   precisely because a per-quiet term in that loop may not pay for itself.

**Cheaper alternative worth trying first.** Skip history entirely and add a
direct ordering term, in the shape of the existing check bonus at
`movepick.cpp:234`: a bonus for quiets landing in the enemy king ring and a
malus for quiets landing in our own, both TUNE-able and both zero by default.
That tests the *signal* without paying the sparsity or the memory, and if the
signal is real, the history split becomes worth the investment. This is the
better first experiment.

## Idea 2: pruning modulation by tactical density

**Mechanism.** The engine's pruning aggressiveness is currently uniform, and the
Atomic-specific relaxations are all one-directional: move-count pruning is looser
because captures are forcing (`search.cpp:1469`, `search.h:68`), null move is
reduced one ply less (`search.cpp:1308`, `search.h:74`), and futility margins are
scaled up wholesale by `AtomicFutilityScale` (`search.cpp:1517-1519`). None of
them look at whether *this* position is actually explosive.

A cheap density proxy: the number of enemy non-pawn pieces standing in the ring
of our pieces, or symmetrically the number of squares where a capture would be a
multi-piece blast. Positions where every capture detonates three pieces need
conservative pruning; positions where the material is dispersed and no ring
overlaps behave much more like chess and could take chess-sized margins.

**Sites.** `AtomicFutilityScale` at `search.cpp:1517-1519` is the natural first
knob: make it interpolate between a chess-sized and an Atomic-sized margin based
on density instead of being a constant 128. Then `atomic_move_count_pruning_threshold`
(`search.h:68`) and `atomic_null_move_reduction` (`search.h:74`).

**Risks.**
1. Cost. A density measure computed per node, in the pruning path, has to be
   nearly free or it eats its own gain. It should be derivable from bitboards
   already computed for the position, and probably wants caching in `StateInfo`.
2. Double counting. `AtomicFutilityScale` was tuned by SPSA90 as a constant. Any
   interpolation must keep the tuned value as the midpoint or the branch will
   test the retuning rather than the idea.
3. It is a *scaler* in the Stockfish sense: an effect that behaves differently at
   short and long time controls, so a passing STC result would need the LTC
   confirmation the protocol already prescribes before it means anything.

## Idea 3: connected kings

**Mechanism.** When the two kings are adjacent, Atomic changes character
completely, and the engine already encodes the consequences in three places:

- `Position::atomic_in_check()` (`position.cpp:729-741`) returns `false`
  unconditionally when the kings touch (`:737-738`): with connected kings there
  is no such thing as check.
- `Position::legal()` (`position.cpp:721-724`) returns `true` for the same
  reason: "adjacent kings are mutually immune, capturing either king would
  explode the attacker as well".
- The move generator has to leave orthodox evasions behind entirely, noted at
  `search.cpp:2070`: "Atomic check evasions are not orthodox EVASIONS: adjacent
  kings are ...".

Two search consequences follow, pulling in opposite directions, which is why this
is the least obvious of the three ideas:

- **Lower tactical density.** With kings connected, the entire overlapping ring
  is a dead zone: no capture there is legal for either side. Many pseudo-legal
  captures are illegal, the branching factor of forcing lines drops, and the
  position is quieter than the material count suggests. Argues for *more*
  pruning.
- **Higher volatility.** Connectedness is one king move away from ending, and the
  instant it ends every suppressed check and capture in that zone comes back at
  once. A position that is "quiet" only because the kings touch can become
  decisive in one ply. Argues for *less* pruning, or for an extension.

**Sites.** The predicate is a one-liner already written three times in
`position.cpp`; it should become a named `Position` helper (say
`kings_connected()`) and be cached in `StateInfo`, since it is needed on both the
pruning and the ordering path. Then:
- as a pruning modulator, same knobs as Idea 2;
- as a small extension when a move *breaks* the connection, alongside the
  existing extension logic;
- and, given the above tension, most plausibly as a *bucket* rather than a
  scaler: let history learn the sign rather than guessing it.

**Risks.**
1. The two consequences may cancel. That is the honest prior, and the reason to
   prefer a history bucket (which can learn a sign per position type) over a
   hand-picked scaler (which has to commit to one).
2. Frequency. Connected kings are rare in the opening and common in the endgame,
   so any measurement will be dominated by endgames and a bench-based sanity
   check will barely move. The bench signature is close to useless as a guide
   here; only games will tell.
3. Interaction with UB1-UB7. `blast_see()` already returns a decisive value when
   a capture catches a king, and `atomic_wins()` already special-cases it. A
   connected-kings term must not double-count what those already express.

## Suggested order

1. Idea 1, cheap variant: TUNE-able ordering bonus/malus for quiets in the two
   king rings, defaults zero. Smallest diff, tests the strongest of the two
   facts, and is a clean elimination-style experiment in the shape of UB7.
2. Idea 1, history variant on `lowPlyHistory` only, if the cheap variant shows
   signal.
3. Idea 3 as a history bucket, since it is the one whose sign we genuinely do not
   know.
4. Idea 2 last: it is the most expensive per node and the most likely to be a
   scaler that only shows at long time controls.
