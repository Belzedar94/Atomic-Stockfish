# H9.3i local Discord evidence: incremental Atomic NNUE

This note records the non-normative community context consulted before the
AtomicNNUEV3 incremental implementation. The executable contract remains the
ADR plus the C++ full-refresh and independent Python differentials. The local
archive was read only; no live bot, credential or Discord network API was used.

## Archive provenance

- Guild export: `779317816897699850`.
- Source JSONL files and SHA-256 identities:

  | File | SHA-256 |
  | --- | --- |
  | `channel_779319972614242354.jsonl` | `7DE859292CF50060995E1658C3E41289A6AB4633A22C349BF00F1AB86678DD2C` |
  | `channel_784418118503235625.jsonl` | `CF41E8D7B79103CBF30A94AB3465B31527DEB5DB7CB576D77459C152CA5F5467` |
  | `channel_791249497090686987.jsonl` | `F1059B5C046406E4FF3E334E844F7BA2BF0582EB95B86514F44EAFA32DC483D4` |
  | `channel_966610323987660830.jsonl` | `16AA3E83AAF2B5FBAB0AD802944BA9921BEBA944FC978E8B1BDDCFFF53C0CAA5` |

The immutable Discord message id and JSONL line below jointly identify each
record in those files.

## Relevant records

| File / line | Message id | Date (UTC) | Author | Architectural signal |
| --- | --- | --- | --- | --- |
| `channel_791249497090686987.jsonl:181` | `801571930120388668` | 2021-01-20 | `ubdip` | Atomic NNUE incremental input updates must explicitly account for both the historical king mapping and explosion removals. |
| `channel_784418118503235625.jsonl:468` | `817438294178136104` | 2021-03-05 | `tttak` | Atomic failed the historical active-versus-changed-feature consistency test, showing that a plausible delta implementation can disagree with a full enumeration. |
| `channel_784418118503235625.jsonl:470` | `817441651173359666` | 2021-03-05 | `ubdip` | Generic NNUE changes had omitted Atomic's special king representation; variant semantics cannot be inherited accidentally. |
| `channel_966610323987660830.jsonl:1869` | `1096379475693944832` | 2023-04-14 | `ubdip` | For Atomic, forcing refresh is the quick correctness oracle because it is the most problematic variant for incremental updates. |
| `channel_779319972614242354.jsonl:3613` | `1406390727965020230` | 2025-08-16 | `ubdip` | Applying a move and inspecting the resulting state can be safer and simpler than predicting complex Atomic effects ahead of time. |
| `channel_791249497090686987.jsonl:1974` | `1421422312078577704` | 2025-09-27 | `ubdip` | An incremental change is trustworthy only when disabling it leaves evaluation identical and changes speed alone. |

## H9.3i consequence

The archive supports a conservative implementation order rather than any Elo
or speed claim:

1. keep the H9.3h scalar full refresh as the immutable source of truth;
2. derive current feature sets from the post-move snapshot instead of trying
   to predict global CapturePair, KingBlastEP or BlastRing consequences from a
   bounded `DirtyPiece` alone;
3. increment only the HM slice first, while all three relation slices refresh
   from the exact current snapshot;
4. compare every intermediate published by the frozen H9.3i-a event trace
   bit-for-bit with both fresh C++ and independent Python; H9.3i-b then keeps
   that trace unchanged, compares every stress evaluation with fresh C++ and
   signs HM sources, distances, counters and outputs while relying on the
   already independent Python differentials for each H9.3a-h feature/scalar
   layer;
5. test lazy make/undo chains, explosions and the no-stack-push null-move path,
   including parent EP, cleared EP under null and exact parent restoration;
6. add SIMD and cache layers only after the scalar event trace is frozen, and
   measure correctness separately from speed.

No historical implementation is copied. AtomicNNUEV3 uses the compact `KING`,
its already frozen joint orientation and its own i32/i64 numeric contract.
