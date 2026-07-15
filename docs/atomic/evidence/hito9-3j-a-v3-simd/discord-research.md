# H9.3j-a local Discord evidence: private AtomicNNUEV3 SIMD

This note records the non-normative community context consulted before the
first AtomicNNUEV3 SIMD implementation. The executable contract remains the
ADR plus the scalar C++ and independent Python differentials. The local
archive was read only; no live bot, credential or Discord network API was
used.

## Archive provenance

- Guild export: `779317816897699850`.
- Source JSONL files and SHA-256 identities:

  | File | SHA-256 |
  | --- | --- |
  | `channel_779317816897699854.jsonl` | `F0BA83E33231942FF9CAD0AC40973D3A497EE950FAC64EA685033CD5AC710678` |
  | `channel_791247944463417374.jsonl` | `D0D00FFB9FDF80EB28E3D26E2AEFC317A1AB5A7F543101B80F995862F3204D2B` |
  | `channel_791249497090686987.jsonl` | `F1059B5C046406E4FF3E334E844F7BA2BF0582EB95B86514F44EAFA32DC483D4` |
  | `channel_966610323987660830.jsonl` | `16AA3E83AAF2B5FBAB0AD802944BA9921BEBA944FC978E8B1BDDCFFF53C0CAA5` |

The immutable Discord message id and JSONL line below jointly identify each
record in those files.

## Relevant records

| File / line | Message id | Date (UTC) | Author | Architectural signal |
| --- | --- | --- | --- | --- |
| `channel_779317816897699854.jsonl:306` | `783721822628610048` | 2020-12-02 | `ubdip` | Atomic can remove up to ten pieces in one move rather than the orthodox maximum of three, so NNUE input-update cost has an Atomic-specific active-row distribution. |
| `channel_791249497090686987.jsonl:181` | `801571930120388668` | 2021-01-20 | `ubdip` | Explosion removals and the historical king/commoner mapping must both be represented by an Atomic NNUE update. |
| `channel_779317816897699854.jsonl:1661` | `809898476796117033` | 2021-02-12 | `ubdip` | Merely removing unrelated generalist code produced little speed; material gains require targeted specialized fast paths rather than assumed specialization benefits. |
| `channel_779317816897699854.jsonl:3303` | `895937664258285568` | 2021-10-08 | `ubdip` | Earlier WASM work kept SIMD-specific additions isolated and identified threading and memory management as separate integration concerns. |
| `channel_966610323987660830.jsonl:1869` | `1096379475693944832` | 2023-04-14 | `ubdip` | Forced full refresh is the quick correctness oracle for Atomic, described as the most problematic variant for incremental NNUE updates. |
| `channel_791247944463417374.jsonl:12716` | `1372579397755736066` | 2025-05-15 | `ubdip` | A nominally stronger x86 build can be slower on particular CPUs; ISA availability is not evidence of a speed win on the measured host. |
| `channel_791249497090686987.jsonl:1974` | `1421422312078577704` | 2025-09-27 | `ubdip` | The trusted update test is identical evaluation with refresh forced, leaving speed as the only difference. |

## H9.3j-a consequence

The archive supports a narrow implementation and measurement order, not an
Elo or engine-NPS claim:

1. keep the H9.3h scalar full refresh as the immutable correctness oracle;
2. vectorize only full-refresh i32 feature-row accumulation, with real SSE4.1
   and AVX2 kernels plus a forced, explicitly selectable scalar oracle;
3. keep feature emission, HM PSQT, transform and the dense tail scalar, and do
   not combine the first SIMD proof with incremental SIMD or cache work;
4. preserve the canonical wire and its hashes independently of ISA: kernels
   consume the authenticated load-time layout and diagnostics return to
   canonical logical coordinates;
5. require bit-exact scalar/SSE4.1/AVX2 diagnostics before measuring the
   isolated accumulation kernels on one deterministic synthetic i16 row and
   one deterministic synthetic i8 row; this narrow probe does not model the
   Atomic active-row distribution highlighted by the archive;
6. keep the backend private: an ISA absent from an ARCH-specific binary is
   rejected and there is no runtime fallback or CPUID dispatcher in H9.3j-a;
   no production dispatcher, OpenBench, Elo, LOS or playing-strength test is
   justified while V3 remains unreachable from search.

No historical implementation is copied. H9.3j-a retains AtomicNNUEV3's frozen
i32 accumulator, i64 PSQT and fail-closed arithmetic contracts.
