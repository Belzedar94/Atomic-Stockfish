# Hito 9 validation

H9.1 introduces the backend boundary required by AtomicNNUEV2 without changing
the active Legacy Atomic V1 network, evaluation, search or dataset wire. The
measured source commit is `2ab3d9c03f60817575d5ad3f7452cda4ab38d43b`,
based directly on H8 commit
`c0197b3bb2474265b95f81c30a8f855453af1b72`.

## Single-backend facade

`src/nnue/nnue_dispatcher.h` names the existing implementation
`LegacyAtomicV1` and exposes it through `AnyNetwork` and `AnyAccumulator`.
The H9.1 facade deliberately has one backend:

- `AnyNetwork` is exactly the size and alignment of the Legacy network and is
  trivially copyable and destructible.
- `AnyAccumulator` uses empty-base optimization for the empty cache owner and
  is exactly the size and alignment of the 537,536-byte Legacy accumulator
  stack.
- There is no heap allocation, virtual dispatch, `std::variant`, function
  pointer or runtime backend branch in H9.1.
- Network load remains transactional. A failed, truncated or incompatible
  load cannot replace the active network.
- Load, save, NUMA replication, worker rebinding and accumulator clearing are
  routed through the facade. Threads are stopped before publication and save
  reads the first replica without rebuilding the replica set.
- `Use NNUE=pure` remains a data-generation mode only.

The generated assembly is unchanged at both hot call sites: `Eval::evaluate`
contains 218 instructions before and after the facade, and
`Worker::evaluate` contains 329. The differences are limited to relocations
and field offsets.

## Gate hardening found during validation

The incremental wrapper now rejects a zero-exit binary unless it emits every
required mode, counter and deterministic signature. A focused adversarial
suite covers missing sentinels, incomplete accounting and wrong signatures.

The release run also exposed that an MSYS2 login shell can lack Git even when
the Python preflight has authenticated the checkout. Clean-build recipes now
derive the exact SHA and commit date through Python and pass `GIT_SHA`,
`GIT_SHA_FULL` and `GIT_DATE` explicitly to Make on Windows and Linux. Invalid
identity values fail closed. The corrected data-generator records the exact
40-character engine commit instead of `unknown`.

## Acceptance matrix

The following gates passed on the measured source:

- MinGW 15.2 BMI2 release: C++ `87/87`, API `34/34`, historical perft `8/8`,
  focused rules/transitions `19/19`, search `16/16` both classical and NNUE,
  UCI, XBoard, NNUE modes/export, reprosearch `12/12` and signature `338376`.
- BMI2 debug/assert and portable SSE2 release passed the same native matrix.
  A 4,096-operation ISA comparison across BMI2, SSE2 and AVX2-debug produced
  `0xDDB8196C6A0BE4A8` for all three.
- The release incremental gate executed 1,000,000 operations, including
  18,761 captures and 241,087 full-refresh comparisons, with zero
  capture-forced refreshes and signature `0x8742E39B793C46AB`.
- The frozen-Fairy differential passed `10,000/10,000`; maximum playing delta
  was `0`, maximum pure trace delta was `0.005`, and the corpus SHA-256 was
  `46C96F405BC15D468D94BC1E2186B577CE55128832E1108066581D35037FA2DE`.
- Atomic Syzygy passed the `5/5` driver, 13 real-table fixtures and production
  UCI probes over the installed 3-6-man tables with NNUE disabled and enabled.
- Python passed historical `test.py` `22/22` and the complete pytest tree
  `588/588`.
- CommonJS and ES-module Board WASM passed `58/58` each and reproduced
  byte-for-byte. Full pthread UCI/NNUE WASM passed release, debug with
  `ASSERTIONS=2` and `SAFE_HEAP=1`, and two byte-identical release builds.
- The Legacy 72-byte pipeline passed generate, validate, decode, one training
  update, serialize, re-import and engine load using locked tools `521f8410`,
  trainer `350a28f2` and the strong network.
- The independent `atomic-bin-v2` pipeline passed with engine `2ab3d9c0`, tools
  `40d2db22`, trainer `3e5651a9` and contract engine `76764c3c`: independent
  train/validation shards, direct/wrapper validation and decode, one training
  step, byte-exact serialization round trip and engine load.

## Reproducibility and throughput

Two independent timestamp-free BMI2 links produced the same 4,257,944-byte
artifact with SHA-256
`38ADC76C760257999A821C4023CBC10F83065B84DB93905C5FD6A8FC274BC4E8`.

The serialized commit A/B used 25 samples per side over the fixed 13-position
corpus. The candidate median was 877,173 NPS and the H8 control median was
880,140 NPS, a ratio of `0.996629` or `-0.337%`. Two batch medians favored the
candidate and three favored the control; individual batches ranged from
`-6.50%` to `+8.39%` while an OpenBench assignment was active. This is
consistent with a throughput-neutral facade and noisy host scheduling, not a
measurable speed claim. The exact assembly comparison is the stronger
zero-overhead evidence.

No H9.1 Elo test is submitted to OpenBench because the playing signature,
search, evaluation and network are unchanged. The fixed-game H8 aggregate
tests remain separate. OpenBench becomes mandatory for H9.2 only when a
trained V2 network can alter playing decisions.

Exact hashes and compact evidence are in
`docs/atomic/evidence/hito9-nnue-dispatcher/`.

## H9.2 boundary

H9.1 does not claim that AtomicNNUEV2 exists. H9.2 will add an authenticated
V2 `.nnue` format, a dual inline network/accumulator backend and a separate
trainer architecture while preserving Legacy `0x7AF32F20` byte-for-byte.
`atomic-bin-v2` already contains the complete canonical position required by
the new features and therefore remains frozen.

## H9.3e AtomicBlastRing oracle

H9.3e freezes the final scalar relation slice before the combined V3 backend.
`AtomicBlastRing` occupies physical rows 64,844 through 75,083: 64 oriented
capture centers, two accumulator-relative actor relations, two independent
accumulator-relative collateral relations, eight joint-frame offsets and five
current-piece classes. CapturePair is the sole candidate source and is emitted
exactly once. The projector counts distinct origins per center/relation,
excludes only a sole capturer, always excludes the off-center EP victim, omits
kings, distinguishes exploding N/B/R/Q from surviving adjacent pawns and emits
a sorted boolean union bounded by 240 rows.

The trusted composition seam now shares one defensive CapturePair emission
validator with KingBlastEP. It authenticates board codes, material bounds,
orientation, indices, records and canonical order without pretending that a
caller-supplied subset can be proven complete without re-enumeration. This is
the boundary the combined refresh will use to feed KingBlastEP and BlastRing
from one exact CapturePair result.

Local acceptance on Windows passed:

- the contract/oracle bundle: 200/200 Python tests;
- the complete Python tree: 955/955, followed by historical `test.py` 22/22,
  including its Atomic/Atomic960 perft coverage;
- MinGW x86-64 release and debug/assert builds and selftests for both the
  refactored KingBlastEP slice and the new BlastRing slice;
- the unchanged KingBlastEP cross-language corpus: 265 FENs, 38 snapshots,
  568 emissions and 952 records, SHA-256
  `182e572028e3383544267fd786f763784dee82c6784f8aa141df1d51cfb5f4ae`;
- the frozen BlastRing cross-language corpus: 266 FENs, 36 snapshots, 568
  emissions and 4,340 records, SHA-256
  `ed5ef5c5cb6389724253ad9cd7d2d4aaf9f0053fecdb2842f16d0864cf0affa4`;
- Python 3.9 grammar, canonical JSON and `git diff --check`. The resulting V3
  schema SHA-256 is
  `40c1888cffd23621d3e6a87a1f1734f64267861c2d6614a5f3a89c08663ae4ec`.

CI runs the same oracle and differential under GCC and Clang, debug/assert,
MinGW, ASan, UBSan, TSan and Valgrind, plus the Python 3.9/3.12 matrix. H9.3e
does not enter the playing network or search path, so it deliberately has no
Elo/OpenBench claim; strength testing begins only after a trained combined V3
backend can alter engine decisions. Community-history evidence used to select
the pawn, bycatch, EP and threat regressions is recorded separately under
`docs/atomic/evidence/hito9-3e-blast-ring/` and is non-normative.
