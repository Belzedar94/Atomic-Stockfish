# H9.3j-b local Discord evidence: incremental AtomicNNUEV3 SIMD

This note records the non-normative community context consulted before the
incremental SIMD layer. The executable contract remains the ADR, the frozen
H9.3h full-refresh diagnostic and the H9.3i C++/independent-Python
differentials. The local archive was read only; no live bot, credential or
Discord network API was used.

## Archive provenance

- Guild export: `779317816897699850`.
- Source JSONL files and SHA-256 identities:

  | File | SHA-256 |
  | --- | --- |
  | `channel_779317816897699854.jsonl` | `F0BA83E33231942FF9CAD0AC40973D3A497EE950FAC64EA685033CD5AC710678` |
  | `channel_784418118503235625.jsonl` | `CF41E8D7B79103CBF30A94AB3465B31527DEB5DB7CB576D77459C152CA5F5467` |
  | `channel_791247944463417374.jsonl` | `D0D00FFB9FDF80EB28E3D26E2AEFC317A1AB5A7F543101B80F995862F3204D2B` |
  | `channel_791249497090686987.jsonl` | `F1059B5C046406E4FF3E334E844F7BA2BF0582EB95B86514F44EAFA32DC483D4` |
  | `channel_966610323987660830.jsonl` | `16AA3E83AAF2B5FBAB0AD802944BA9921BEBA944FC978E8B1BDDCFFF53C0CAA5` |

The immutable message id and JSONL line jointly identify each record.

## Relevant records

| File / line | Message id | Date (UTC) | Author | Architectural signal |
| --- | --- | --- | --- | --- |
| `channel_779317816897699854.jsonl:306` | `783721822628610048` | 2020-12-02 | `ubdip` | One Atomic move can remove up to ten pieces, so the incremental row distribution is not the orthodox three-change case. |
| `channel_791249497090686987.jsonl:181` | `801571930120388668` | 2021-01-20 | `ubdip` | Explosion removals and the historical king mapping both had to be represented by Atomic NNUE updates. |
| `channel_784418118503235625.jsonl:468` | `817438294178136104` | 2021-03-05 | `tttak` | Atomic failed a historical active-versus-changed-feature consistency test; a plausible delta is not sufficient evidence. |
| `channel_784418118503235625.jsonl:470` | `817441651173359666` | 2021-03-05 | `ubdip` | Generic NNUE changes had missed Atomic's special king semantics, reinforcing that delta construction must remain variant-specific and independently checked. |
| `channel_966610323987660830.jsonl:1869` | `1096379475693944832` | 2023-04-14 | `ubdip` | Forced full refresh is the quick correctness oracle for Atomic, described as the most problematic incremental NNUE variant. |
| `channel_791247944463417374.jsonl:12716` | `1372579397755736066` | 2025-05-15 | `ubdip` | A nominally stronger ISA build can be slower on a particular CPU, so availability is not evidence of promotion. |
| `channel_791249497090686987.jsonl:1974` | `1421422312078577704` | 2025-09-27 | `ubdip` | The trusted update test is identical evaluation with refresh forced; only speed may differ. |

## H9.3j-b consequence

The archive supports a narrow vectorization boundary:

1. retain the same H9.3i frame/source-selection machine and the post-move HM
   row oracle; SIMD applies rows but does not construct or reinterpret deltas;
2. preserve removal-first arithmetic in i64 scratch, including `-32768`, and
   narrow only after the frozen range check;
3. leave same-frame reuse, orientation-triggered HM refresh, HM PSQT, all three
   relation slices, transform and dense execution scalar;
4. compare scalar, SSE4.1 and AVX2 on the unchanged 39-event trace and the
   complete deterministic Atomic/Atomic960 stress corpus, with forced full
   refresh remaining the permanent oracle;
5. require exact requested-ISA execution and zero fallback; an unavailable ISA
   fails before frame, counters or diagnostics can mutate;
6. measure real quiet, capture, promotion, en-passant and maximum-blast
   transitions only after exactness. No ISA is promoted merely because it is
   present, and no engine-NPS, Elo or playing-strength claim applies while V3
   remains outside the production dispatcher.

No historical implementation is copied. The immutable network remains
shareable, while each incremental stack owns its frames and i64 scratch.
