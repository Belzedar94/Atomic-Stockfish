# AtomicNNUEV3 relation inputs: what to keep

AtomicNNUEV3 was archived on 11 November 2026 (tag
`archive/pre-cleanup-20261111`). Its code, wire format, trainer contracts,
oracles and evidence are all in that tag, together with the full design in
`docs/atomic/adr/0004-atomic-nnue-v3.md`. This note keeps the one idea that the
engine plan still lists as a bet (E4, "architecture"). It also records what was
and was not established about that idea.

## The idea

Keep the stable piece-square inputs (HalfKAv2Atomic, horizontally mirrored)
and add separate slices of *relations* that only Atomic has. They are computed
from the pseudo-captures in the position, not from legal moves:

- **CapturePair**: one row per occupancy-based pseudo-capture: actor
  own/opponent, directed geometric edge (from/to square), victim class. Pins,
  checks and self-blasting captures are kept on purpose, so the network can
  learn why they are unusable. En passant gets a compact tail of its own.
  About 40,000 rows, i8 weights.
- **KingBlastEP**: for each capture landing square, whether the enemy king is
  on it, whether an enemy king is next to it (the capture explodes it), whether
  one's own king is next to it (the capture is self-blasting), and an en-passant
  marker. 2,304 rows, i16 weights.
- **BlastRing**: for each capture landing square, the non-king pieces on the
  eight neighbouring squares that the blast would remove (N, B, R, Q), plus
  adjacent pawns, which survive. 10,240 rows, i8 weights.

Within one perspective, all slices share the same orientation: vertical flip
for Black, then a horizontal mirror when that perspective's king is on files
a to d. Every relation is boolean and deduplicated, so several attackers of one
square never multiply a weight.

The design reused V2's SFNNv15 dense head unchanged, so any strength change
could be attributed to the inputs alone.

## What was established

- The feature extractors were exact. Independent scalar oracles and full-refresh
  differentials agreed bit for bit with the incremental and SIMD paths across
  Atomic960, en passant and promotion fixtures. That correctness work is the
  expensive part, and it is reusable from the tag.
- The relation slices were refresh-only: a quiet move can open or close capture
  rays anywhere on the board, so they were never updated incrementally. The
  transformer was about 98 MiB, against 45 MiB for the V1 networks in
  production.
- The audit of November 2026 cites a +106 Elo ablation for these inputs from a
  July forensic report. That figure was **not reproduced**, and no V3 network
  ever beat the V1 champion on OpenBench. Treat it as a hypothesis.

## If the bet is taken up again

The plan's version is narrower than V3: compact relations centred on the
king's blast zone (essentially KingBlastEP and the part of BlastRing next to the
kings), added to the current V1 architecture rather than to a new backend.
That keeps the input small enough to update or refresh cheaply. Decide it like
any other network change: train on the same data as the champion, then SPRT at
STC and, if it passes, at LTC.

The extractors to start from, in the archive tag:

- `src/nnue/atomic_v3/capture_pair.{h,cpp}`
- `src/nnue/atomic_v3/king_blast_ep.{h,cpp}`
- `src/nnue/atomic_v3/blast_ring.{h,cpp}`
- `src/nnue/atomic_v3/orientation.h`
- `tests/python/atomic_v3_*_reference.py` (independent Python oracles)
