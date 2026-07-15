# H9.3i-a AtomicNNUEV3 scalar HM incremental evidence

H9.3i-a adds the first private AtomicNNUEV3 incremental execution stack. It
incrementally maintains only HM accumulator and HM PSQT state. CapturePair,
KingBlastEP and BlastRing continue to refresh from the current immutable
snapshot before every composition. V3 remains absent from the production
dispatcher, search, protocols, bindings, WASM, generator and trainer.

## Frozen execution contract

- Each perspective stores sorted unique HM physical rows, the joint
  orientation, canonical i32 accumulator values and i64 PSQT values.
- A same-orientation update subtracts old-only rows before adding new-only
  rows in i64 scratch. Publication requires the frozen i32 accumulator and
  PSQT envelopes.
- An orientation change rebuilds HM from authenticated network biases and the
  current full emission.
- Stack reset, push and pop are explicit. A search null move does not push a
  frame; same-frame reuse first compares the current snapshot and EP square
  with the values that produced the cached HM state.
- Relation slices always refresh, and the current side to move always selects
  the first transformed half and dense-tail perspective.
- A feature, numeric, identity or composition failure clears the complete
  diagnostic and leaves stack depth, frames and counters unchanged.
- Caller-assembled composition emissions are rejected before array access if
  an HM or relation size exceeds capacity, an HM orientation is noncanonical,
  or any relation orientation differs from its HM orientation.

The C++ sequence runner executes ten blocks covering reset, quiet updates,
capture, Atomic explosion, en passant, promotion, orientation changes, lazy
make/undo, no-push null moves, network identity, numeric failures and recovery.
Its frozen machine-readable trace contains 39 events: 35 successful evaluations
and four expected fail-closed events.

An independent Python implementation reconstructs each snapshot from FEN,
authenticates the 77,349,879-byte fixture directly and compares the complete
event trace: source selection, frame/depth state, HM rows, orientations,
accumulator, PSQT, all four emissions, transform, every dense intermediate,
raw/scaled/public output and counters. The accepted fixture is:

```text
size    77349879
sha256  00E46223822D06D7927E884EEC10739BA19EF8DD82A6E262F627D361658080C2
```

The H9.3h full-refresh corpus remains unchanged:

```text
scalar fingerprint  0x46F68EAB20FF9D50
scalar digest       22ae9a6188fa0ebdd0faff9b4a23c25d25380f9b47ebc0e9da2d1b28fe2441b6
```

## Local Windows acceptance

The exact worktree passed MinGW release, debug/assert, BMI2 and AVX2 builds.
The wire, scalar and incremental targets also passed under forced identity,
AVX2/LASX and AVX512 parameter layouts. Every layout retained the fixture
identity, scalar fingerprint and 39-event incremental trace above.

The complete Python tree reported `1030 passed`; historical `test.py` passed
`22/22` while importing the `pyffish.pyd` built from this worktree. Source
contracts passed `15/15`, including exactly-once differential execution and
proof that the incremental sources are absent from every production, Python,
JavaScript and WASM build graph. YAML parsing, every extracted workflow shell
block, Python grammar/byte compilation, clang-format and `git diff --check`
are also clean.

A clean BMI2 production build retained exactly two NNUE backends. The V3
fixture was rejected for `Use NNUE=true` and data-generation-only `pure`, while
`Use NNUE=false` remained searchable.

GCC stack-usage measurement reported 57,184 bytes for incremental evaluation
and 48,176 bytes for scalar composition. The conservative largest nested raw
sum is 105,456 bytes when including the incremental fail-closed helper, below
the enforced 128,000-byte warning threshold. Frames and the runner-owned stack
live on the heap because the runner allocates its `IncrementalStack` there;
evaluation itself performs no dynamic allocation.

CI repeats the private target and differential with GCC, Clang, debug/assert,
MinGW, real AVX2, all three forced layouts, ASan, UBSan, TSan and Valgrind. The
instrumented jobs invoke the differential through the instrumented binary or
Valgrind wrapper exactly once.

No bench, Elo, LOS, OpenBench or training claim applies. H9.3i-a cannot alter a
move because V3 is still private. H9.3i-b owns the larger randomized,
special-move, concurrency and fail-closed stress corpus; real SIMD follows
only after that scalar trace is frozen.
