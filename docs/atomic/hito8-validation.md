# Hito 8 performance specialization validation

Hito 8 specializes memory layout and hot paths only after Atomic rules, search,
Legacy Atomic V1 NNUE, and the data pipeline have stable gates. Every block is
kept small enough to attribute a regression. A block that changes the playing
signature requires the normal OpenBench strength workflow; a no-functional-
change block must preserve the signature exactly and still pass all applicable
functional and performance gates.

## H8.1 - Remove inactive NNUE threat-delta runtime state

The active feature set is `HalfKAv2Atomic`. It declares
`UsesThreatDeltas == false` and uses `DirtyPiece` as its incremental-difference
type. Nevertheless, each accumulator state still contained a 392-byte
`DirtyThreats` member, and `Position` retained another 392-byte scratch object.
Those objects were never consumed by the active network.

H8.1 removes that runtime state and the unused parameters that carried it
through make/undo. It also adds a compile-time assertion that the active feature
set remains a `DirtyPiece` feature set without threat deltas. The legacy network
reader, feature indices, quantization, accumulator values, and serialized NNUE
format are unchanged.

The functional code commit is
`6153609c8b454e13bb3941789b9184f9b4825dad`, based on Hito 7 merge
`281bcc382eb4449886d0ee930ef7e23cb12b4dba`. The measured release artifact was
then rebuilt from the clean committed tree
`85b2c909a5fa48c02c83104498567d32a454347d`; the commits after that build only
add the reusable A/B runner and this corrected evidence record.

### Layout and artifact evidence

Both artifacts below were rebuilt from clean, commit-bound trees with the same
MinGW g++ 15.2 toolchain and the exact normative `ARCH=x86-64-bmi2` release
target. The candidate additionally uses `-Wl,--no-insert-timestamp`, embeds
`85b2c909`, and records the full source commit above in `.build_full_sha.txt`.

| Item | Hito 7 control | H8.1 candidate | Change |
| --- | ---: | ---: | ---: |
| `AccumulatorState` | 2,560 B | 2,176 B | -384 B |
| `AccumulatorStack` | 632,384 B | 537,536 B | -94,848 B (-15.0%) |
| `Position` | 1,056 B | 664 B | -392 B (-37.1%) |
| Native executable | 4,269,764 B | 4,263,264 B | -6,500 B |

| Artifact | SHA-256 |
| --- | --- |
| Hito 7 control executable | `92E9C3C254741B628996D2F6617FF871EA1C06DAEEFB8AC749BF755FAAAC2323` |
| H8.1 candidate executable | `B2F750FEE129D04ECAA9360E6AE6BE94F525A4453E085F03B02F2200AE56EB2C` |
| Frozen Legacy Atomic V1 net | `99DC67EABF26A64FAEECA3A88B4C38597A840B8D4A874B9F2CF658C6F92A04A6` |

The machine-readable manifest and complete benchmark output are retained in
[`evidence/hito8-dirty-threats`](evidence/hito8-dirty-threats/README.md).

### Functional validation

The source-equivalent release BMI2 candidate passed:

- C++ Atomic unit tests: 63/63.
- Shared board API tests: 34/34.
- All eight frozen Atomic/Atomic960 perft vectors.
- Focused rules and state transitions: 19/19.
- Fixed search corpus with classical evaluation: 16/16.
- The same search corpus with `Use NNUE=true` and the frozen net: 16/16.
- Reprosearch with the frozen net: 12/12.
- One million deterministic incremental make/undo operations: 500,000 make,
  500,000 undo, 18,761 captures, zero capture-forced refreshes, and 241,087
  comparisons against a full accumulator refresh. The state signature was
  `0x8742E39B793C46AB`.
- A 10,000-position differential against frozen Fairy-Stockfish: 10,000/10,000
  accepted, maximum `Use NNUE=true` delta 0, and maximum pure trace delta
  0.005. The separately reported rule-50 oracle diagnostic reached 0.740 in
  866 rule-50-damped positions; it is not an accumulator mismatch.

The debug/assert build independently passed 63/63 C++ tests, 34/34 API tests,
all perft and 19 focused rule cases, both 16/16 search corpora, and an
incremental 4,096-operation smoke with 4,104 full-refresh comparisons and state
signature `0xDDB8196C6A0BE4A8`.

After the clean commit-bound rebuild, the exact measured artifact loaded the
frozen network and reproduced the playing signature `338376`. GitHub CI on the
same `85b2c909` source commit passed all 14 jobs: native GCC and Clang,
debug/assert, ASan+UBSan, TSan, Valgrind, MinGW data generator, both Windows and
Linux Python 3.9/3.12 jobs, CommonJS/ES-module WASM, format, and the pinned
Legacy Atomic V1 pipeline. The strong-network million-operation and 10,000-
position local gates above exercise the identical engine source; their first
artifact was precommit and is not used for the performance or reproducibility
claim.

CI now compiles the modified incremental NNUE test executable under native GCC,
Clang, debug/assert, Windows MinGW, ASan+UBSan, and TSan. CI does not execute the
strong-network gate because that network is deliberately external; the full
local release gate above authenticates and executes it.

### Fixed-corpus performance evidence

The runner used 10 Atomic and three Atomic960 positions, one thread, 64 MiB
hash, CPU affinity 24, 100,000 nodes per FEN, one warm-up, five alternating
serialized repetitions, and the frozen net. The corpus SHA-256 was
`2738065A8A70D61DA46FA3C75F95D645E50E601B43792DF0E7B3CC97B1D891A1`.
The compiler preflight identified both sides as g++ 15.2, 64-bit BMI2 release
builds, and the artifact postflight re-authenticated every binary, the net, and
the pinned process-affinity dependency. The commit comparison uses the tracked
`tests/atomic_bench_ab.py`; the frozen-Fairy comparison continues to use the
normative `tests/atomic_bench_compare.py`.

Against the exact Hito 7 control:

| Side | NPS samples | Median NPS |
| --- | --- | ---: |
| H8.1 candidate | 828,565; 762,513; 840,884; 867,231; 869,550 | 840,884 |
| Hito 7 control | 861,488; 869,550; 824,364; 777,553; 801,014 | 824,364 |

The commit A/B gate passed with ratio `1.0200` (+2.00%) and a 6,500-byte
smaller executable.

Against the frozen Fairy-Stockfish BMI2 baseline
`4EACAAB40DCA84F5A255EA57231F2795D43B5DDA85CE50EBBA1A1B2937B46331`:

| Side | NPS samples | Median NPS |
| --- | --- | ---: |
| H8.1 candidate | 859,780; 781,289; 679,293; 779,417; 912,875 | 781,289 |
| Frozen Fairy baseline | 756,755; 635,186; 748,482; 736,614; 803,001 | 748,482 |

The normative performance gate passed with ratio `1.0438` (+4.38%) and an
18,607-byte smaller executable. These percentages are observations from a
machine with one concurrent assigned workload, not universal speed promises;
the alternating order and median reduce but do not eliminate shared-load noise.
The gate-relevant facts are that both comparisons use versioned runners and
clean commit-bound artifacts, authenticate every input before and after the
workload, and are positive.

### Playing-strength gate

The playing signature remains exactly:

```text
Bench: 338376
```

H8.1 is therefore a no-functional-change optimization under the project's
OpenBench rules. It does not require an Elo/LOS match. Any later Hito 8 block
that changes this signature must use the normal Atomic OpenBench STC/LTC
methodology before acceptance.

### Deferred work

H8.1 intentionally leaves the inactive `FullThreats`, `HalfKAv2_hm`, and
`DirtyThreats` implementation sources buildable. H8.2 will remove that dead
feature family as a separately reviewable build-graph change, including native,
bindings, WASM, and data-generator compilation gates. Later Hito 8 blocks will
profile make/undo, move generation, accumulator caching, and NNUE-trained PGO
before proposing further changes.

## H8.2a - Remove inactive NNUE feature extractors

H8.2a deletes the `FullThreats` and `HalfKAv2_hm` implementations and removes
them from the native and NNUE-WASM build graphs. The active legacy backend is
still exactly `HalfKAv2Atomic`; its version, feature hash, 45,056 inputs,
transformer dimensions, network architecture, quantization, and serialized
format do not change. A structural test now requires native and NNUE-WASM to
compile only the Atomic extractor.

The measured source commit is
`1288bef27dc4344d69b182c929118a07cacf82fc`, based on the H8.1 squash merge
`2d85e90603825069bdb7aaf16b2115424323f603`. The exact clean BMI2 artifact
embeds that source SHA.

### Source, artifact, and functional evidence

- Four inactive extractor files removed: 640 deleted lines versus 25 lines of
  source-inventory test coverage.
- Native executable: 4,263,264 B to 4,262,279 B, a further 985-byte reduction.
- Candidate SHA-256:
  `DD789CC09190E50DED9A5DC59076774CEF67BE6026B851CCC48782381DF00379`.
- C++ Atomic unit tests: 63/63.
- Shared board API tests: 34/34.
- All eight Atomic/Atomic960 perft vectors and 19/19 focused rule transitions.
- Fixed search corpus: 16/16 with classical evaluation and 16/16 with
  `Use NNUE=true` and the frozen net.
- NNUE mode contract: `false`, `true`, `pure`, and invalid-net handling.
- Reprosearch: 12/12.
- One million deterministic incremental make/undo operations, including 18,761
  captures, zero capture-forced refreshes, 241,087 full-refresh comparisons,
  and state signature `0x8742E39B793C46AB`.
- A 10,000-position differential against frozen Fairy passed 10,000/10,000,
  with maximum final `Use NNUE=true` delta 0 and maximum pure trace delta
  0.005.
- GitHub CI on the exact source SHA passed all 14 jobs, including GCC, Clang,
  debug/assert, sanitizers, Windows MinGW data generator, Python 3.9/3.12,
  CommonJS/ES-module WASM, and the pinned legacy pipeline.

### Clean-machine commit A/B

OpenBench was paused for the serialized benchmark and restored before further
project work. Both artifacts were clean MinGW g++ 15.2 BMI2 release builds with
`-Wl,--no-insert-timestamp`. The runner authenticated the executables, frozen
net, corpus, and pinned dependency before and after the run.

| Side | NPS samples | Median NPS |
| --- | --- | ---: |
| H8.2a candidate | 1,420,139; 1,417,045; 1,426,367; 1,398,760; 1,412,429 | 1,417,045 |
| H8.1 control | 1,412,429; 1,400,266; 1,383,880; 1,394,263; 1,424,805 | 1,400,266 |

The commit A/B gate passed with ratio `1.0120` (+1.20%) and the 985-byte size
reduction. Complete output and the machine-readable manifest are retained in
[`evidence/hito8-dead-feature-extractors`](evidence/hito8-dead-feature-extractors/README.md).

The playing signature remains exactly `338376`, so H8.2a is a
no-functional-change specialization and does not require an Elo test. H8.2b
removes the now-orphaned threat-update plumbing and attack tables as a separate
attributable block.

## H8.2b - Remove legacy threat-update plumbing

H8.2b removes the unreachable `DirtyThreat` representation, updater, and
AVX-512 writer after the active LegacyAtomicV1 accumulator was proven to use
only `DirtyPiece`. It also removes `LineBB`, `RayPassBB`,
`PawnPushOrAttacks`, the unused board-array accessor, and the orphaned
`ValueList::make_space()` helper. The accumulator now owns a compile-time
assertion that its feature-set delta type remains `DirtyPiece`.

The measured source commit is
`20b3bd6c191636599ecad3e09aea7b78eb762d77`, based on the H8.2a squash merge
`8113cab087e03b668623a8fbab8bf9508cf6bd68`. The exact clean BMI2 artifact
embeds that source SHA.

### Source, artifact, and functional evidence

- Patch size: 45 insertions and 296 deletions across ten files.
- Removed process-static attack storage: 65,536 bytes from the two 64 by 64
  bitboard matrices.
- Candidate executable: 4,266,467 bytes, SHA-256
  `78AAE3D35C3D76F3EDCA2E4CA7600F52A2BF7793283E158B835197C3C0D9EDB9`.
- C++ Atomic unit tests: 63/63.
- Shared board API tests: 34/34.
- All eight Atomic/Atomic960 perft vectors and 19/19 focused rule transitions.
- Fixed search corpus: 16/16 classical and 16/16 with LegacyAtomicV1.
- NNUE modes: `false`, `true`, `pure`, and invalid-net handling.
- Reprosearch: 12/12.
- One million deterministic incremental operations: 500,000 makes, 500,000
  undos, 18,761 captures, zero capture-forced refreshes, 241,087 full-refresh
  comparisons, and state signature `0x8742E39B793C46AB`.
- Frozen-Fairy differential: 10,000/10,000; maximum final NNUE delta 0,
  maximum pure trace delta 0.005, corpus SHA-256
  `46C96F405BC15D468D94BC1E2186B577CE55128832E1108066581D35037FA2DE`.

### Clean-machine commit A/B

The OpenBench worker was paused and restored inside a `finally` block. Both
artifacts were clean MinGW g++ 15.2 BMI2 release builds, and the runner
authenticated all eight inputs before and after the measurement.

| Side | NPS samples | Median NPS |
| --- | --- | ---: |
| H8.2b candidate | 1,429,502; 1,427,933; 1,421,691; 1,316,647; 1,424,805 | 1,424,805 |
| H8.2a control | 1,412,429; 1,400,266; 1,412,429; 1,413,964; 1,409,368 | 1,412,429 |

The strict gate passed with ratio `1.0088` (+0.88%). The PE grew by 4,188
bytes after code/LTO layout changed; this is tracked separately from the
65,536 bytes of deleted runtime-static matrices. Complete evidence is in
[`evidence/hito8-threat-plumbing`](evidence/hito8-threat-plumbing/README.md).

The playing signature remains exactly `338376`; H8.2b is therefore a
no-functional-change specialization and does not require an Elo test.

## H8.2c - Remove unused modern NNUE layers

H8.2c removes `AffineTransformSparseInput`, `SqrClippedReLU`, and the
non-zero-index helper from the LegacyAtomicV1 source inventory. Their only
consumers were each other; the active Atomic network continues to use dense
`AffineTransform`, `ClippedReLU`, and `HalfKAv2Atomic`. The associated dead
`vec_nnz` implementations are removed from AVX-512, AVX2, SSE, NEON, LSX, and
LASX branches.

The measured source commit is
`2728a7798fbc525e3397c736a46736b3dd15bf5b`, based on the H8.2b squash merge
`caa075a19eef822a01019c77f4c5faf00c84de11`.

### Source, artifact, and functional evidence

- Patch size: 23 insertions and 790 deletions across seven files.
- Three implementation headers removed from disk and from the native Makefile.
- BMI2 executable: 4,266,467 bytes, SHA-256
  `ACA0C03907D750D1991A61F94439B2539AAA25070FDCF0F22D0558E9EB9335E7`.
- Compile-only AVX-512 ICL artifact: 4,283,265 bytes, SHA-256
  `426F6DE9D5762B8F374467860AEC10FE46C17C4EF0E47CF5EB2CDE52B0A65AE6`.
- C++ Atomic unit tests: 63/63; shared API tests: 34/34.
- All eight Atomic/Atomic960 perfts and 19/19 focused rules/transitions.
- Fixed search corpus: 16/16 classical and 16/16 LegacyAtomicV1.
- NNUE modes `false`, `true`, `pure`, invalid-net rejection, and reprosearch
  12/12.
- One million deterministic incremental operations retained the exact counters
  and state signature `0x8742E39B793C46AB`.
- Frozen-Fairy differential: 10,000/10,000, maximum final NNUE delta 0 and
  maximum pure trace delta 0.005.

### Clean-machine commit A/B

| Side | NPS samples | Median NPS |
| --- | --- | ---: |
| H8.2c candidate | 1,412,429; 1,409,368; 1,421,691; 1,363,571; 1,382,409 | 1,409,368 |
| H8.2b control | 1,406,321; 1,397,258; 1,407,843; 1,424,805; 1,373,650 | 1,406,321 |

The strict runner reports ratio `1.0022` (+0.22%) with identical executable
sizes. Because none of the removed headers participated in either binary, this
result is classified as neutral measurement noise rather than a speed claim.
Complete evidence is in
[`evidence/hito8-modern-nnue-layers`](evidence/hito8-modern-nnue-layers/README.md).

The structural test protects the current single-backend tree globally and
records that H9 must split or replace the guard when it adds modern layers
under an independent `AtomicNNUEV2` build graph. LegacyAtomicV1 remains
bit-compatible, and the playing signature remains exactly `338376`.

## H8.3a - Compact Atomic search state

H8.3a removes the zero-only orthodox `checkersBB`, the unconsumed
`pinners[COLOR_NB]`, and three unused `checkSquares` entries from `StateInfo`.
The x64 layout falls from 208 to 160 bytes (-48 bytes, -23.1%) while the copied
prefix remains exactly 64 bytes. `Position::checkers()` remains constant-zero,
KING check squares remain empty, and all live PAWN-through-QUEEN indices are
covered by compile-time contracts.

The measured source commit is
`d35706d8dc7823bd7e423b0413e2d698b1f5b916`, based on H8.2c squash merge
`067f81ce9f41d59b27f93060b7bfc2a180240816`.

### Functional and artifact evidence

- BMI2 executable: 4,262,552 bytes, SHA-256
  `5335201E2D4EFBA9B34814D7258E83118D2C8A60EA0C4D538750D31E3118911E`;
  3,915 bytes smaller than H8.2c.
- C++ units 65/65 in release and debug/assert; shared API 34/34.
- MSVC Python build, `test.py` 22/22, and focused pytest 66/66.
- Real MinGW data-generator smoke passed all seven fixtures with the frozen
  strong network.
- Eight Atomic/Atomic960 perfts; focused rules 19/19.
- Search 16/16 classical and 16/16 LegacyAtomicV1; XBoard passed.
- NNUE modes `false`, `true`, `pure`, invalid-net recovery, and reprosearch
  12/12.
- One million deterministic make/undo operations reproduced 18,761 captures,
  241,087 full-refresh comparisons, zero capture-forced refreshes, and state
  signature `0x8742E39B793C46AB`.
- Frozen-Fairy differential 10,000/10,000, maximum playing delta 0, maximum
  pure-trace delta 0.005, and 866 rule-50-damped positions.
- Playing signature remains exactly `338376`.

The new C++ corpus exhausts every legal move in 11 focused positions (166
moves) and proves `gives_check(move) == atomic_in_check(child)` plus exact
FEN/key restoration after undo.

### Serialized commit A/B

A preliminary five-sample batch measured -0.33%. To distinguish that short
batch from a real regression, five further complete batches were run under the
same isolated conditions. Their pooled 25 samples per side measured median
1,420,139 NPS for H8.3a and 1,410,897 for H8.2c, ratio `1.0066` (+0.66%);
four of five extended batches had a positive median. This is classified as a
small positive signal with visible batch noise, not as a precise speed claim.

Complete artifacts and every sample are in
[`evidence/hito8-compact-stateinfo`](evidence/hito8-compact-stateinfo/README.md).

## H8.3b - Remove unreachable orthodox evasions

H8.3b removes the concrete `EVASIONS` move-generation specialization and the
associated MovePicker stages after H8.3a made `Position::checkers()` a
constant zero. Atomic check handling remains on `atomic_in_check()`: main
search and qsearch still enumerate captures and quiets before the complete
Atomic legality filter. The generic enum and compile-time source branches are
retained to avoid unrelated upstream-source and ordinal churn.

The measured source commit is
`1fecfec95eb9b2b50166be882a5d315a4396882f`, based on H8.3a squash merge
`063eede4f6176c2f438a7fea54ce682d293997dd`.

### Functional and artifact evidence

- BMI2 executable: 4,257,648 bytes, SHA-256
  `B47DD600D41BC47AF996C4A3ABC6C8189F3EF89A91ACC032C4FD2B687EDC71F5`;
  4,904 bytes smaller than H8.3a.
- C++ units 67/67 in release and debug/assert; shared API 34/34.
- MSVC Python build, `test.py` 22/22, and focused pytest 67/67.
- Eight Atomic/Atomic960 perfts, 19/19 focused rules, 16/16 classical search,
  16/16 LegacyAtomicV1 search, XBoard, NNUE modes, and reprosearch 12/12.
- One million deterministic make/undo operations retained state signature
  `0x8742E39B793C46AB`.
- Frozen-Fairy differential 10,000/10,000 with zero final playing delta and
  maximum pure-trace delta 0.005.
- Real generator smoke reproduced the seven-record fixture byte for byte.
- Playing signature remains exactly `338376`; an independent audit found no
  P0-P3 issue.

### Serialized commit A/B

Ten complete isolated batches produced 50 samples per side. The first half
measured -0.32%, while the independent extension measured +0.65%. The pooled
median was 1,392,770 NPS for H8.3b and 1,388,311 for H8.3a, ratio `1.0032`
(+0.32%); six batches favored the candidate, three the control, and one tied.
This is classified as a small positive pooled signal with visible short-run
noise, not a precise speed claim.

Complete artifacts and every sample are in
[`evidence/hito8-orthodox-evasions`](evidence/hito8-orthodox-evasions/README.md).
