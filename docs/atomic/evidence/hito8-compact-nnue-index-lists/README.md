# H8.4b compact NNUE index-list evidence

H8.4b gives the three LegacyAtomicV1 feature-index lists capacities that match
their actual jobs. Active-position refreshes retain the exact 32-piece limit;
incremental removals use `2 + MAX_ATOMIC_BLAST_PIECES` (11); and additions use
2 for castling. The `.nnue` architecture, accumulator state, evaluation,
dataset formats, and public APIs are unchanged.

The measured source commit is
`7a5ed472e5c1b1fad05f1c57f8f5399a26b84737`, based directly on H8.3b squash
merge `dbb3b6898797850c4760bb3dcd4d79f7a3070652`.

## Structural result

- Removed-plus-added local storage falls from 272 to 72 bytes on x86-64.
- The optimized MinGW object reduces the two relevant stack frames from
  360 to 120 bytes and from 376 to 168 bytes (-240 and -208 bytes).
- BMI2 executable: 4,256,122 bytes, SHA-256
  `0F28C1ED18E95E21C43338068AFC1862143FD870E5057E23E40A0D906160A414`;
  1,526 bytes smaller than H8.3b.
- Compile-time contracts freeze capacities 32/11/2. Runtime fixtures reach
  every boundary: a full 32-piece position, a nine-piece Atomic blast,
  maximum en passant and capture-promotion deltas, standard and Atomic960
  castling, and a terminal king capture.

An independent source audit proved that no legal Atomic transition can exceed
these capacities, including in release builds where `ValueList` assertions are
disabled. It also confirmed that H8.3c and H8.4a are not present in this tree.

## Functional validation

- C++ units 74/74 and shared API 34/34 in release and debug/assert builds.
- Eight Atomic/Atomic960 perfts, 19/19 focused rules, 16/16 classical search,
  16/16 LegacyAtomicV1 search, XBoard, UCI, all three NNUE modes, invalid-net
  rejection, and reprosearch 12/12.
- MSVC Python extension: `test.py` 22/22 and focused pytest 68/68.
- Playing signature remained exactly `338376`.
- One million deterministic make/undo operations retained 18,761 captures,
  241,087 full-refresh comparisons, zero capture-forced refreshes, and state
  signature `0x8742E39B793C46AB`.
- Frozen-Fairy NNUE differential: 10,000/10,000, maximum playing delta 0,
  maximum pure-trace delta 0.005, 866 rule-50-damped positions, and corpus
  SHA-256
  `46C96F405BC15D468D94BC1E2186B577CE55128832E1108066581D35037FA2DE`.
- The real MinGW generator reproduced seven fixtures with data SHA-256
  `7E89411B84C2036DEEB2DB56F3E43FEA89917C5546C72C37F8E082F103B27CC0`.

## Platform validation

- Scalar MinGW `ARCH=x86-64` (SSE2 only) passed units 74/74, API 34/34,
  rules, perft, NNUE modes, search, and a 4,096-operation incremental smoke.
- Board WASM CommonJS and ES module passed 58 fixtures and lifecycle checks
  under both pinned Emscripten 3.1.46 (CI) and documented 4.0.10.
- Full pthread NNUE WASM release and debug/assert/SAFE_HEAP builds loaded the
  frozen network and passed classical, `true`, and `pure` searches, Atomic
  perft `197326`, Atomic960 perft `61401`, terminal, and protocol checks.
- Release full-engine WASM: 540,228 bytes, SHA-256
  `4DD3866D8A6D8B370EE0DBAA6A57A466C1AF8C9D0F92CC2134EDD5FAFB79D41E`.
- Debug full-engine WASM: 11,113,268 bytes, SHA-256
  `6C8E33671980FA0C402C89C801AB80A773F9EF4D5158EF2A77AFB50AD179EFE1`.

The lightweight Board target deliberately has no NNUE code; the full-engine
release and debug WASM gates are the wasm32 coverage for this change.

## Serialized speed measurement

The active 55-process OpenBench assignment tree was paused before the first
sample. Candidate and control were pinned to CPU 24 and alternated over the
fixed 13-position Atomic/Atomic960 corpus, with one warm-up and five measured
runs in each of five independent batches. Every batch passed compiler,
artifact, network, corpus, and postflight authentication.

| Batch | Candidate median NPS | H8.3b median NPS | Ratio |
| ---: | ---: | ---: | ---: |
| 1 | 1,409,368 | 1,413,964 | 0.996750 |
| 2 | 1,403,287 | 1,400,266 | 1.002157 |
| 3 | 1,409,368 | 1,401,775 | 1.005417 |
| 4 | 1,412,429 | 1,397,258 | 1.010858 |
| 5 | 1,392,770 | 1,378,016 | 1.010707 |

The pooled 25 samples per side have median 1,409,368 NPS for H8.4b and
1,401,775 for H8.3b: ratio `1.005417`, or +0.542%. Four batches favored the
candidate and one favored the control. The five-run alternation gives the
candidate three first slots and the control two, and batch ratios rose during
the window, so this is a moderate positive signal rather than a high-confidence
estimate. The consistent sign, smaller binary, and measured stack-frame
reduction justify acceptance; the exact percentage is not expected to
generalize to every CPU.

An older duplicate client launcher remained outside the explicitly rooted
assignment tree, but its server token was already invalid and it had no
workload or engine descendants. It was deduplicated after the measurement;
one healthy 24-thread client root remained. A PowerShell locale/format-string
error occurred only while printing the derived pooled line, after all 50
samples and artifact postflights; the exact integer samples were recomputed
independently and are retained in `commit-ab.log`.

The worker was restored in `finally` with a fresh 32-byte random password sent
only through environment variables. Complete machine-readable metadata is in
`manifest.json`.
