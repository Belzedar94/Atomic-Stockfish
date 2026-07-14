# H8.5 Atomic capture-prefilter evidence

H8.5 prevents the capture-only generator from emitting captures whose Atomic
explosion would also remove the moving side's king. The filter is deliberately
limited to `CAPTURES`: `NON_EVASIONS`, legal move generation, the final Atomic
legality oracle, and all search semantics remain unchanged. En passant uses an
explicit destination-square guard, while quiet queen promotions remain present
in the capture stage exactly as before.

The measured source commit is
`be56098f484f92596792e1a35310b65de36f6b17`, based directly on the H8.4b
squash merge `6cc61af1f108f17ac47a23ca03450590da882457`.

## Functional validation

- C++ Atomic units: 87/87 in release; the production-equivalent predecessor
  passed 86/86 in debug/assert before the final test-only amendment.
- Shared API: 34/34; Python `test.py`: 22/22; focused pytest: 71/71.
- Eight historical Atomic/Atomic960 perfts and 19/19 focused rule/transition
  cases.
- Fixed search corpus: 16/16 classical and 16/16 LegacyAtomicV1.
- UCI, XBoard, NNUE modes `false`, `true`, and data-generation-only `pure`,
  invalid-network rejection, and reprosearch 12/12.
- Playing signature remains exactly `338376`.
- The new direct corpus covers 13 cases: sliders, pawns, kings, capture
  promotions, en passant, direct king captures, quiet queen promotions, and
  the separation between `CAPTURES`, `NON_EVASIONS`, and `LEGAL`.
- Three independent Fairy differentials compared legal moves and depth-two
  perft in 2,489 positions with no mismatch.
- Frozen-Fairy NNUE differential: 10,000/10,000, maximum final playing delta 0,
  maximum pure-trace delta 0.005, corpus SHA-256
  `46C96F405BC15D468D94BC1E2186B577CE55128832E1108066581D35037FA2DE`.
- One million deterministic incremental operations retained 18,761 captures,
  241,087 full-refresh comparisons, and state signature
  `0x8742E39B793C46AB`.
- The real generator reproduced all seven fixtures with data SHA-256
  `7E89411B84C2036DEEB2DB56F3E43FEA89917C5546C72C37F8E082F103B27CC0`.

## Platform validation

- Native MinGW BMI2 release passed the complete block gate.
- Native scalar SSE2 passed units, API, perft, rules, search, and NNUE modes.
- The production-equivalent debug/assert build and MSVC Python extension passed
  before the final test-only amendment.
- Board WASM under pinned Emscripten 4.0.10 passed 58 CommonJS and 58 ES-module
  fixtures and lifecycle checks. The shared payload is 262,971 bytes, SHA-256
  `50AB0958775BA2FF4C7AE33EA48F1F92D1339DC4F33013652BC44DDDCB0F4F63`.
- Full pthread NNUE WASM passed classical, `true`, and `pure` search, Atomic
  perft `197326`, Atomic960 perft `61401`, interactive stdin, terminal, and
  protocol checks. The payload is 540,136 bytes, SHA-256
  `0BF34E110499EEA3E82493BC932BE52ED9CA9332EE83ADFBD6A2176CD2C4EB1D`.

## Serialized speed measurement

The complete OpenBench client tree was paused before measurement and restored
afterward. Candidate and exact H8.4b control were pinned to CPU 24 and
alternated over the fixed 13-position Atomic/Atomic960 corpus. Each of five
batches used one warm-up and five measured repetitions per side at 100,000
nodes per FEN.

| Batch | H8.5 median NPS | H8.4b median NPS | Ratio |
| ---: | ---: | ---: | ---: |
| 1 | 1,397,258 | 1,341,079 | 1.041891 |
| 2 | 1,346,633 | 1,341,079 | 1.004141 |
| 3 | 1,383,880 | 1,365,002 | 1.013830 |
| 4 | 1,376,558 | 1,372,201 | 1.003175 |
| 5 | 1,386,830 | 1,352,232 | 1.025586 |

The pooled 25 samples per side have median 1,378,016 NPS for H8.5 and
1,359,297 for H8.4b: ratio `1.013771`, or +1.377%. All five batch medians favor
the candidate. The executable is also 512 bytes smaller. This is a strong
local speed signal for an exact-signature mechanical optimization, not a claim
of Elo gain.

No OpenBench Elo match was submitted for this block because its search
signature is identical and the intended observable is throughput. Fixed-game
STC/LTC remains mandatory for changes that alter search, evaluation, time
management, network behavior, or tablebase decisions.

The candidate artifact is 4,255,610 bytes, SHA-256
`A1E984FE5E0E604B84BB840B9FAF2B9A6010636B4E7DFE065178C014B4BFC34F`.
The exact H8.4b control is 4,256,122 bytes, SHA-256
`E9E7357537E1830A3DAA9EDAF478B7E1F9319438B8EC58EE66580F33488790B8`.
Every raw sample and the complete configuration are retained in
`commit-ab.log` and `manifest.json`.
