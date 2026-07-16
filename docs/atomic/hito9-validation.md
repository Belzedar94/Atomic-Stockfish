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

## H9.3f scalar full-refresh composition oracle

H9.3f composes the four scalar slice contracts without introducing a network
backend. For each perspective the Position adapter takes one immutable
board/side-to-move/EP snapshot, HM is emitted exactly once, CapturePair is
emitted exactly once through a trusted HM-to-CP seam, and the same immutable CP
emission is supplied to both KingBlastEP and BlastRing. The standalone slice
APIs remain unchanged and are compared record-for-record against the combined
bundle.

The combined error domain is `CapturePairError`. HM errors map losslessly and
any failure clears all four slices. King-absent terminals therefore remain an
empty mapped error at this isolated boundary, while malformed optional EP is a
successful composition that preserves every normal relation. All slice
orientations must be identical and the conservative aggregate bound is
`32 + 240 + 35 + 240 = 547` active physical rows.

The frozen cross-language corpus contains 102 positions and both
perspectives, including independent mirror branches, valid and malformed EP,
promotion geometry, touching kings, self-blast relations and an Atomic960
layout. Its SHA-256 is
`22ae9a6188fa0ebdd0faff9b4a23c25d25380f9b47ebc0e9da2d1b28fe2441b6`.
The C++ selftest and Python reference additionally replay immutable inputs at
1, 2, 4 and 8 threads/workers. A source-contract gate rejects any future
combined implementation that calls HM or CP more than once, calls a public
downstream emitter, takes more than one Position snapshot, or fails to pass
the same CP object to both projectors.

Local acceptance on Windows passed:

- MinGW 15.2 x86-64 release and debug/assert builds and selftests for HM,
  CapturePair, KingBlastEP, BlastRing and FullRefresh;
- all five C++/Python differentials in both builds, including the 204-case
  FullRefresh corpus above and four exact missing/multiple-king fail-closed
  snapshots;
- the complete Python tree against a `pyffish` extension rebuilt from this
  exact worktree: 975/975, followed by historical `test.py` 22/22 with its
  Atomic and Atomic960 perft coverage;
- Python 3.9 grammar, `py_compile`, canonical YAML/JSON parsing,
  `clang-format --dry-run --Werror` and `git diff --check`.

The CLI wire reports the HM king-orientation bucket and the material-selected
network bucket as separate fields; the differential validates both so these
unrelated bucket domains cannot be conflated. The resulting V3 schema SHA-256
is `c81d8f777b7390beebf37bab4b285f29dfc5614d13987df6846dc5d5375dbde4`.

CI adds the combined selftest and differential to GCC, Clang, debug/assert and
MinGW, and executes the selftest under ASan, UBSan and TSan. Valgrind uses a
separate 20-minute job rather than extending the existing V2 plus isolated-V3
lane, whose observed runtime was already close enough to its 25-minute budget
to make silent expansion unsafe. Python 3.9 and 3.12 execute the composition,
ownership, malformed-EP, concurrency and source-contract gates.

H9.3f intentionally does not touch quantized weights, runtime accumulator
arithmetic, incremental updates, SIMD, network hashes, loader, serializer,
dispatcher, UCI, WASM, data generator or trainer. Engine bench, signature and
OpenBench/Elo are therefore not applicable: no reachable playing decision has
changed. The next numeric milestone must prove accumulator range and compare
incremental/SIMD updates bit-exactly against this full-refresh oracle before a
V3 file can be accepted. Local Discord evidence motivating this sequencing is
recorded under `docs/atomic/evidence/hito9-3f-full-refresh/`.

## H9.3g frozen numeric and mixed-wire contract

H9.3g freezes the first private AtomicNNUEV3 file identity and its checked
integer arithmetic without making V3 a production backend. The wire header is
version `0xA70C0003`; the HM, CapturePair, KingBlastEP and BlastRing descriptor
hashes are respectively `0xA34A8666`, `0x9AEDB186`, `0xF5172BC0` and
`0x38377946`. Their folded feature hash is `0xA3FBDBE8`. The 799-byte ASCII
transformer descriptor hashes to `0xCC31067A`; the feature-transformer,
architecture and complete-network hashes are `0x6FCAD592`, `0x63337116` and
`0x0CF9A484`.

The private reader and writer freeze little-endian headers, canonical signed
LEB128 for i16/i32 tensors, raw two's-complement i8 tensors, strict EOF,
transactional load, eight independently hashed dense stacks and the direct
canonical-to-internal SIMD block permutations. Save uses an inverse copy and
never mutates live parameters. The numeric contract uses checked i32 feature
accumulators and i64 intermediates for PSQT, affine bounds and final scaling;
malformed, non-canonical, truncated, overflowing or trailing input fails
closed.

The deterministic mixed-wire fixture is 77,349,879 bytes with SHA-256
`00E46223822D06D7927E884EEC10739BA19EF8DD82A6E262F627D361658080C2`.
Normal generation authenticates both pins and refuses overwrite; changing the
identity requires the explicit measurement workflow. C++ and Python parse and
round-trip the fixture byte-exactly and agree on the complete set of 248
selected internal values, including signed LEB boundaries, raw i8 signs, SIMD
block boundaries, the exact safe PSQT top-32 limit and eight distinguishable
SFNNv15 stacks. The C++ round-trip acquires the destination directly with
`O_EXCL`/`CREATE_NEW`, streams only through that owned descriptor, synchronizes
and verifies it byte-exactly; a two-writer race with byte-distinct valid inputs
proves exactly one winner without truncating a protected destination. The
resulting V3 schema SHA-256 is
`9D3C77A58E5E55AC1BC798DAB41977451EB523FCE1D6FD3EC3F7C1E574A78750`.

Local acceptance on Windows passed:

- MinGW x86-64 release, debug/assert, AVX2 and production BMI2 builds and
  selftests for the isolated numeric and wire targets;
- the exact C++/Python mixed-wire differential in release, debug/assert, AVX2
  and BMI2, plus forced identity, AVX2/LASX and AVX512 permutation policies and
  the five pre-existing V3 slice/full-refresh differentials;
- 63 focused Python numeric, wire, fixture, contract and dispatcher tests, the
  complete pytest tree `1010/1010`, and historical `test.py` `22/22`;
- production dispatcher rejection of the V3 fixture with `Use NNUE=true` and
  data-generation-only `Use NNUE=pure`, while `Use NNUE=false` remains
  searchable; authenticated Legacy V1 and AtomicNNUEV2 search remain accepted;
- UCI and XBoard protocol tests, Atomic/Atomic960 perft and rules tests with
  `Use NNUE=false`, `true` and data-generation-only `pure` using the
  authenticated V2 network, reprosearch, and playing signature `338376`;
- Python 3.9 grammar, canonical JSON/YAML parsing,
  `clang-format --dry-run --Werror` and `git diff --check`.

CI builds and runs the isolated numeric and wire contracts across GCC, Clang,
debug/assert and MinGW. A dedicated portable matrix recompiles and executes the
same selftest, selected-value oracle, round-trip race and differential with the
identity, AVX2/LASX and AVX512 policies forced independently. ASan, UBSan and
TSan generate the authenticated fixture and execute both targets; the
ASan/UBSan memory lane additionally runs the full wire selftest and
differential. Valgrind runs the numeric target and a complete wire inspection.
The production dispatcher rejection is also a CI gate, so accepting this
private format cannot accidentally expose V3 through UCI or the existing
bindings.

H9.3g has no Elo/OpenBench claim. The V3 reader, writer and arithmetic contract
remain unreachable from production evaluation, search and time management;
the playing signature is unchanged. Strength testing becomes applicable only
after an execution backend and trained V3 network can alter engine decisions.
Compact reproducibility evidence is recorded under
`docs/atomic/evidence/hito9-3g-v3-wire/`.

## H9.3h private scalar execution backend

H9.3h executes the authenticated H9.3g network for the first time, but only
through a private full-refresh correctness backend. The production dispatcher
continues to recognize V1 and V2 only: V3 remains unreachable from UCI,
XBoard, search, bindings, WASM, the generator and the trainer.

For each perspective, one shared immutable Position snapshot feeds the H9.3f
full-refresh oracle. The backend accumulates the i16 HM and KingBlastEP rows
and sign-extends the i8 CapturePair and BlastRing rows into i64 scratch, checks
the frozen i32 envelope, then publishes canonical logical coordinates after
undoing the authenticated runtime permutation. HM PSQT remains i64 and uses
the inherited `(stm - opponent) / 2`, followed by the public `/ 16` output
scale; both signed divisions truncate toward zero.

The scalar transform and dense tail reproduce SFNNv15 exactly. Each
perspective multiplies its clipped `[0, 255]` accumulator halves and divides by
512; side-to-move precedes opponent. The output-major FC0, FC1 and FC2 affine
layers use i64 sums with checked i32 publication. Their squared/clipped paths
use shifts `21/7` and `19/6`; final composition is
`fc2 + fc0[30] - fc0[31]`, followed by checked `* 9600 / 16384` and `/ 16`.
Every failure clears the complete public diagnostic.

The frozen C++ corpus covers all eight material buckets, both sides to move,
both king-orientation branches, valid and malformed EP, orthodox and
Atomic960 castling layouts, captures, promotions, touching kings and invalid
king/side states. It also proves immutable concurrent reads at 1, 2, 4 and 8
threads and evaluate-then-save byte identity. Adversarial dense vectors place
nonzero signed weights on FC0, FC1 and FC2 block boundaries, exercise the
skip outputs and both output signs, and distinguish an affine i32 overflow
from a valid-affine final-composition overflow. The cross-policy diagnostic
fingerprint is `0x46F68EAB20FF9D50` for identity, AVX2/LASX and AVX512
permutations.

An independent Python decoder authenticates the 77,349,879-byte H9.3g fixture,
decodes its canonical wire spans directly and compares accumulators, all eight
PSQT lanes, transformed bytes, every dense intermediate, raw/scaled outputs
and public values for the frozen full-refresh corpus and targeted nonzero
rows. It does not import the fixture generator's selected-value map.

CI adds the scalar selftest and differential to GCC, Clang, debug/assert,
MinGW, real AVX2 and the three forced permutation policies. The private backend
also runs under ASan, UBSan and TSan; Valgrind loads the complete authenticated
network and evaluates a targeted snapshot. The production-dispatch rejection
gate remains mandatory.

H9.3h has no bench, Elo, LOS, OpenBench or training claim because no playing
path can load V3. SIMD and incremental execution are deliberately deferred;
this scalar diagnostic becomes their bit-exact oracle. Compact evidence is
recorded under `docs/atomic/evidence/hito9-3h-v3-scalar/`.

## H9.3i-a private scalar HM incremental execution

H9.3i-a introduces a private frame stack that incrementally maintains the HM
accumulator and HM PSQT only. It obtains sorted current HM rows from the exact
post-move snapshot, removes old-only rows before adding new-only rows in i64
scratch and publishes only after the frozen i32/PSQT range checks. A joint
orientation change forces an HM rebuild from biases. CapturePair,
KingBlastEP and BlastRing deliberately remain full-refresh slices.

Null move is a no-push path. Before reusing an HM frame, the backend compares
the complete snapshot and the EP square that produced it; relations always
refresh and the current side to move always controls dense-tail ordering.
Push/pop, lazy make/undo, restored parent EP, network identity changes and all
failures are transactional. Forged oversize or crossed-orientation emissions
are rejected before copy or iteration.

The C++ runner freezes ten semantic blocks and a 39-event trace. An independent
Python state machine rebuilds every snapshot from FEN and compares source
selection, stack state, HM rows, accumulators, PSQT, four emissions, transform,
dense intermediates, outputs and counters. Thirty-five events succeed and four
fail closed as expected. The authenticated fixture remains 77,349,879 bytes
with SHA-256
`00E46223822D06D7927E884EEC10739BA19EF8DD82A6E262F627D361658080C2`;
the unchanged H9.3h scalar fingerprint is `0x46F68EAB20FF9D50` and its corpus
digest remains
`22ae9a6188fa0ebdd0faff9b4a23c25d25380f9b47ebc0e9da2d1b28fe2441b6`.

Local MinGW acceptance passed release, debug/assert, BMI2, AVX2 and forced
identity, AVX2/LASX and AVX512 layouts. The complete Python tree passed
`1030` tests and historical `test.py` passed `22/22` against this worktree's
binding. Stack measurement keeps the conservative largest nested raw sum below
the enforced 128,000-byte threshold; the runner allocates its frame stack on
the heap and the evaluation path performs no dynamic allocation.

A clean BMI2 production build retained exactly two registered NNUE backends:
the V3 fixture was rejected for `true` and data-generation-only `pure`, while
`false` remained searchable.

CI covers GCC, Clang, debug/assert, MinGW, real AVX2, the three forced layouts,
ASan, UBSan, TSan and Valgrind. Normal and instrumented legs execute the
differential exactly once. V3 remains absent from every production and binding
build graph, so no bench, Elo, LOS, OpenBench or training claim applies.
Detailed evidence and the consulted local Discord records are under
`docs/atomic/evidence/hito9-3i-v3-incremental/`.

## H9.3j-a private SIMD full-refresh accumulation

H9.3j-a is limited to real SSE4.1 and AVX2 implementations of full-refresh
i32 feature-row accumulation. A forced scalar implementation remains the
oracle and an explicitly selectable path; H9.3j-a does not automatically
dispatch or fall back between ISAs. Feature emission, HM PSQT, transform and
the SFNNv15 dense tail stay scalar; incremental SIMD, caches, production
dispatch and all playing paths remain out of scope. The network is immutable
and shareable, scratch is caller-owned, each evaluation stack is single-owner
and the hot kernel may not allocate.

Wire and ISA policy remain separate. The canonical V3 bytes, descriptors,
offsets and hashes do not change. Each kernel consumes the authenticated
load-time tensor layout, while all public diagnostics are converted back to
canonical logical coordinates. Forced identity, AVX2/LASX and AVX512 layouts
must retain the H9.3h scalar diagnostic fingerprint
`0x46F68EAB20FF9D50`. The separate H9.3j-a 109-position batch aggregate is
`0x4FBDB31B354FC080`; these fingerprints cover different transcripts and are
not interchangeable. H9.3j-a has no runtime CPUID dispatcher: an exact ISA
request that was not compiled into the ARCH-specific test binary is rejected,
and the scalar path remains explicitly requestable in every compatible build.

Acceptance requires bit-exact scalar/SSE4.1/AVX2 equality for both
perspectives over the complete frozen corpus, including all special and
invalid cases. The comparison covers all four slice emissions, canonical i32
accumulators, i64 PSQT, transformed bytes, dense intermediates, raw/scaled
outputs, public values and cleared diagnostics on failure. The existing scalar
numeric contract must reject values outside the frozen i32 envelope before
publication; no SIMD narrowing, wrapping or partial output is permitted.
Concurrent readers of one immutable network must reproduce the single-thread
result with independent scratch.

Only after exactness passes may the isolated accumulation kernel be measured.
The runner first loads the authenticated 77,349,879-byte fixture to prove the
private V3 build identity, but the timed data is deliberately synthetic: one
deterministic 1,024-lane i16 row and one deterministic 1,024-lane i8 row are
each accumulated 8,192 times. No fixture weight row or empty, sparse, ordinary
or explosion-heavy active-row distribution is measured. The runner performs
one warm-up and five measured trials, alternating scalar/SIMD order between
trials, and reports the raw nanosecond samples, their medians and the median
scalar/SIMD ratio. The scalar comparator is a volatile scalar loop intended to
prevent compiler autovectorization. Transform, dense-tail, full evaluation and
search time are excluded, so the result is only a kernel microbenchmark.

`--benchmark` is report-only. The opt-in local `--promotion-gate` requires a
median ratio strictly greater than `1.000` independently for SSE4.1 and AVX2;
scalar is not a promotion target. CI checks exactness and real instructions but
does not invoke the benchmark or enforce a noisy speed threshold. CPU affinity
and host isolation belong to the external local measurement procedure rather
than being capabilities claimed by this runner. A kernel that does not pass
local promotion remains available only for differential coverage or is
removed; it cannot justify widening the scope.

Because V3 remains absent from search, UCI, XBoard, Python, JavaScript, WASM,
the generator and the trainer, H9.3j-a has no engine bench, OpenBench, Elo, LOS
or training claim. Evidence belongs under
`docs/atomic/evidence/hito9-3j-a-v3-simd/`.

## H9.3j-b private SIMD incremental HM deltas

H9.3j-b keeps the H9.3i frame stack, source selection and row-set difference
as the sole incremental state machine. It vectorizes only application of an
already validated i16 HM row: lanes widen to i64, removed rows precede added
rows, HM PSQT stays scalar and canonical diagnostics cross an explicit
canonical/internal permutation boundary. Frame publication and semantic/kernel
counters remain transactional.

Scalar, SSE4.1 and AVX2 are exact requests rather than a fallback chain. An
unavailable ISA fails before observable mutation. Acceptance requires the
direct tail/extrema kernel probe, the unchanged 39-event independent-Python
differential, the deterministic Atomic/Atomic960 stress signatures, forced
wire layouts and object-code proof of signed i16-to-i64 widening and 64-bit
add/sub. CI gates exactness, not speed.

Final-head local acceptance passed the direct kernel probe for tails
`0,1,3,4,7,8,9,15,16,17,1023,1024,1025` under Scalar, SSE4.1 and AVX2; all
three reproduced fingerprint `0x21E9FF9A77F881F2`. The same three exact modes
passed the independent 39-event trace (`35` successes and `4` expected
fail-closed events) and the smoke stress profile with signature
`0x45D43FB02CAA9A3D`, exact requested/executed ISA counters and zero fallback.
Debug/assert SSE4.1 passed the same focused matrix. An object-code audit found
both stable AVX2 add/sub symbols plus signed widening and 64-bit add/sub
instructions.

Review found and fixed one reset-mode persistence defect: the legacy
`reset(network)` overload now restores Scalar policy and disables HM-delta
execution, while an exact-ISA reset applies only its requested mode. A focused
constructor/reset transition contract and the complete final-head smoke matrix
cover the correction. A second audit hardened the execution proof: stable
kernels return their actual ISA, counters derive from that result, request/result
mismatches fail before publication, every named unavailable ISA is exercised at
stack level and wrap-boundary arithmetic is defined consistently across Scalar,
SSE4.1 and AVX2. The 65,536-operation release signature
`0xE86C39BDF8187078` and 1,048,576-operation soak signature
`0xAF6B51180815972B` were reproduced before this small correction, so they are
supporting evidence and are not labelled exact-head runs.

The representative local benchmark measures quiet, capture, promotion,
en-passant and maximum-blast transitions with one warm-up and five alternating
trials. It reports raw samples and medians without a hard timing threshold.
The latest exactness-qualified AVX2 run reported aggregate ratio `0.968590`;
it is report-only and establishes no V3 speed improvement.

The independent normative BMI2 product comparison did pass its speed gate:
over 13 positions, 100,000 nodes per FEN and five alternating repetitions,
Atomic-Stockfish reached median `1,262,958` NPS against Fairy's `1,106,174`
NPS, ratio `1.1417` (**+14.17%**), with matching MinGW GCC 15.2 BMI2 builds.
The product also passed the complete Hito 4 aggregate gate using
`atomic_run3b_e202_l05.nnue`, SHA-256
`99DC67EABF26A64FAEECA3A88B4C38597A840B8D4A874B9F2CF658C6F92A04A6`:
C++ `87/87`, API `34/34`, Python `1178/1178`, historical `test.py` `22/22`,
native UCI/XBoard/Syzygy/perft/search/reprosearch/signature, CommonJS and ES
module Board WASM, byte-reproducible UCI/NNUE WASM and real-network loading.
These are product regression and throughput gates; V3 remains private and
cannot affect moves.

Because V3 is still excluded from every production and binding graph, no
engine-NPS, Elo, LOS, OpenBench or training claim applies. The exact contract is
[ADR 0005](adr/0005-atomic-nnue-v3-incremental-simd.md); the reviewed head and
merged acceptance artifacts are indexed under
[`hito9-3j-b-v3-incremental-simd`](evidence/hito9-3j-b-v3-incremental-simd/README.md).

## H9.3l-a distributed publication contract

PR #42 merged the frozen acyclic evidence contract as
`dde43fc08fb2bd45eec09d3dbe9f6d06845eeb24`. It binds distributed Atomic BIN
V2 chunks into one campaign without erasing per-chunk seeds or provenance,
authenticates the producer build set, and separates semantic replay,
reachability evidence and controlled training outputs. Publication remains
fail-closed: structural V1 validation alone cannot claim V3 dataset or training
readiness.

The contract deliberately leaves Legacy Atomic V1, AtomicNNUEV2, Atomic BIN V2
and the existing V3 schemas byte-identical. The separately scheduled 500M
Atomic BIN V2 bootstrap remains a transport and throughput pilot and can never
be relabelled as a V3 release dataset.

## H9.3l-b audited trajectory producer

PR #43 merged the production trajectory writer as
`420c9f35266fbdc2167dc5b9d8d20d90281c60c9` after 34/34 checks and a clean
exact-head review. Each game owns a private transposition table and cleared
history state, public sidecars are staged transactionally, and deterministic
external sorting uses bounded 64 MiB runs and at most 64 readers. Threads 1, 2
and 4 reproduce the same authenticated V3 trajectory result; Atomic960,
rollback and the unchanged seven Legacy/V2 fixtures are covered.

`variant-nnue-pytorch` PR #13 merged the isolated trainer core into `atomic` as
`44663e28c3e5464ff3be2cdaa26c8518b3951c5f`. It authenticates the earlier
H9.3l-a contract boundary and does not yet claim controlled training or V3
publication. The final `variant-nnue-tools/atomic` pin to engine merge
`420c9f35266fbdc2167dc5b9d8d20d90281c60c9` remains a release input until its
reviewed PR is merged and its repository merge SHA is frozen in the pipeline
lock.
