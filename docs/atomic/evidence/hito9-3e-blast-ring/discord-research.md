# H9.3e AtomicBlastRing evidence

This note separates normative executable evidence from auxiliary community
history. The frozen schema, Atomic-Stockfish behavior, differential oracles,
perft and strength gates are authoritative. Discord messages help identify
failure modes and tests, but never override code or executable parity.

The messages were inspected in the local read-only export
`vault/raw/discord/779317816897699850`. No bot credential, live Discord access
or secret is required or recorded. As noted by H9.3d, the sibling
`fairy-vault` index was empty; the raw 35-file export is the actual historical
source, so an empty search index is not treated as negative evidence.

## Normative engine evidence

- `src/position.cpp:703-706` forms the Atomic blast from the capture center and
  its king-neighborhood, excludes adjacent pawns and removes every affected
  square from the post-capture occupancy used by legality.
- `src/position.cpp:976-988` removes the en-passant victim from the square behind
  the empty landing center before the later Atomic blast is assembled.
- `src/position.cpp:1111-1155` removes the capturing piece, the already handled
  target and every adjacent non-pawn, while retaining a bounded record of all
  pieces removed by the blast. This is why N, B, R and Q ring classes describe
  removal while `ADJACENT_PAWN_SURVIVES` deliberately describes survival.
- `src/position.h:52-76` and `src/types.h:301-323` retain a fixed delta of up to
  nine indirectly removed pieces for undo and NNUE updates. That delta is later
  useful for incremental work, but H9.3e remains a full-snapshot oracle.

These source paths fix the H9.3e rule mapping: the landing square is the center;
the off-center EP pawn is excluded unconditionally; adjacent pawns survive;
adjacent non-pawns explode; and kings belong to KingBlastEP rather than the ring
slice.

## Pawn survival and king separation

- `channel_779319972614242354.jsonl:1779`, message
  `1068675519547191298`, 2023-01-27, ubdip: pawn resistance around a blast and
  king immunity were identified as two different concepts rather than one
  generic immunity feature.
- The clarification at `:1784`, message `1068950736815280248`, 2023-01-28,
  rainrat, distinguishes pawns surviving collateral blast from kings being
  immune even at the capture center.
- `channel_779319972614242354.jsonl:2143`, message
  `1095484905502298192`, 2023-04-11, ijhy, records the historical Atomic rule
  lineage and that adjacent-pawn blast immunity predates the Lichess/FICS rule
  set.

This supports separate `ADJACENT_PAWN_SURVIVES` and king-relation classes. The
exact emitted indices still come from the normative source and oracle tests.

## Indirect captures and relation-input cost

- `channel_779317816897699854.jsonl:1533`, message
  `806944245205172275`, 2021-02-04, ubdip: Fairy-Stockfish needed `StateInfo` to
  retain indirectly captured, or bycatch, pieces for Atomic undo.
- `channel_977572442899898388.jsonl:692-696`, messages
  `1400651013882384464` through `1400651325431353354`, 2025-08-01, sscg13:
  NNUE incremental moves are normally represented by piece-square changes, and
  a feature set that cannot be derived from that information requires explicit
  feature-transformer work.
- The same discussion at `:703-720`, messages `1400653259185848443` through
  `1400654429023109181`, describes attack-pair inputs, their increased active
  count and the observation that their changed-input count can be comparable to
  piece-square changes despite higher evaluation cost.

These reports motivate a standalone scalar oracle, a single trusted
CapturePair projection seam and explicit active-count/thread-safety contracts.
They do not prove the H9.3e index formula or justify an incremental shortcut.

## Explosion relations are not direct check

- `channel_779319972614242354.jsonl:2040`, message
  `1092084211797721108`, 2023-04-02, ubdip: a king may face a possible explosion
  on the following move without being in direct check, so explosion threats and
  direct-check legality are distinct Atomic concepts.
- `channel_779317816897699854.jsonl:1803`, message
  `813457086490607701`, 2021-02-22, ubdip, discusses the strategic relevance of
  how many pieces a capture explodes in Atomic Giveaway. That variant-specific
  observation is retained only as weak motivation for exposing collateral;
  it is not a normative Atomic rule source.

BlastRing therefore projects unfiltered CapturePair outcomes rather than a
check predicate. Legality remains outside this input slice.

## En-passant regression history

- `channel_779317816897699854.jsonl:2268`, message
  `862289243337129984`, 2021-07-07, iq_qi94: an Atomic perft discrepancy was
  associated with en passant.
- The follow-up at `:3029`, message `883743430902173748`, 2021-09-04, iq_qi94,
  reports illegal Atomic moves in two EP-related cases.

This history motivates both-color and both-perspective EP goldens, malformed-EP
fail-closed cases, exact landing-center grouping and the unconditional exclusion
of the off-center captured pawn, including a two-origin group.

## Scope caveat

Messages about Atomic Giveaway, Atomar, `nocheckatomic` or historical server
rule differences are not promoted to the contract. Atomic-Stockfish targets the
Fairy/Lichess `atomic` behavior. Community history selects tests; the checked-in
engine source and differential truth determine the result.
