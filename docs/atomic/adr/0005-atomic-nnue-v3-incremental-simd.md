# ADR 0005: Vectorize only AtomicNNUEV3 incremental HM row application

- Status: accepted for H9.3j-b; local validation complete, reviewed-head CI/PR pending
- Date: 2026-07-15

## Context

H9.3i froze one private incremental state machine for AtomicNNUEV3. It derives
sorted HM row differences from the exact post-move position, refreshes HM when
the joint orientation changes, and leaves CapturePair, KingBlastEP, BlastRing,
the transform and the dense tail on their scalar full-refresh paths. H9.3j-a
then established explicit Scalar, SSE4.1 and AVX2 execution for full-refresh
row accumulation without automatic fallback.

Atomic deltas are not the usual one-add/one-remove chess case: an explosion can
remove many pieces, en passant removes a pawn away from the destination and a
move can change the king-dependent orientation. SIMD must therefore accelerate
only reviewed arithmetic. It must not create a second delta algorithm, change
the wire layout or weaken the full-refresh oracle.

## Decision

1. H9.3j-b keeps the H9.3i `IncrementalStack`, frame/source selection and HM
   row-set difference unchanged. Only application of an already validated i16
   HM weight row to the accumulator is dispatched to the selected kernel.
2. The caller requests exactly `scalar`, `sse41` or `avx2`. An ISA that is not
   compiled and available returns `UnsupportedIsa` before observable state can
   change. There is no implicit downgrade or scalar fallback. Each stable
   kernel returns the ISA that actually executed; the dispatcher propagates
   that identity and the incremental stack rejects any request/result mismatch
   before publication. Stable SSE4.1 and AVX2 add/sub symbols make the generated
   instructions and call path auditable.
3. Every source lane is sign-extended from i16 to i64 before addition or
   subtraction. This includes `-32768` and `32767`. All removed rows are
   applied before all added rows, preserving the frozen H9.3i arithmetic
   order. Kernel arithmetic is defined modulo 2^64, matching the x86 vector
   instructions without signed-C++ overflow; the real backend's stronger i32
   publication envelope remains unchanged. HM PSQT remains scalar i64
   arithmetic.
4. The published HM accumulator stays in canonical logical order. A delta
   copies the canonical i32 source to stack-owned internal-order i64 scratch,
   applies rows in their authenticated load-time layout, then permutes back to
   canonical order. Publication occurs only after every lane and PSQT value
   passes the existing numeric envelopes. V3 bytes, hashes and descriptors do
   not change.
5. Frames, diagnostics and both semantic and kernel counters are transactional.
   Per-evaluation counters are accumulated locally and committed only with the
   completed frame. Any feature, numeric, identity, injected or ISA failure
   clears the diagnostic and preserves depth, frames and cumulative counters.
   Same-frame reuse and orientation-triggered full refresh execute no delta
   kernel. The legacy `reset(network)` overload restores Scalar policy and
   disables HM-delta execution; an exact-ISA reset then enables only its
   requested mode.
6. The network remains immutable and may be shared. Each incremental stack is
   single-owner and owns its frames and aligned scratch. The hot row kernel
   performs no allocation.
7. Correctness gates require exact Scalar/SSE4.1/AVX2 equality with the frozen
   scalar/full-refresh results, exact requested-ISA counters, zero fallback and
   an object-code audit of signed i16-to-i64 widening plus 64-bit add/sub.
8. CI gates exactness, fail-closed behavior and generated instructions; it does
   not gate on noisy timing. A separate local benchmark covers quiet, capture,
   promotion, en-passant and maximum nine-piece-blast transitions with one
   warm-up and five order-alternating trials. It reports raw samples, medians
   and ratios without a hard speed threshold.
9. H9.3j-b remains private. Its sources are excluded from the production NNUE
   dispatcher, search, UCI/XBoard, Python, JavaScript, WASM, generator and
   trainer build graphs. Product dispatch and automatic ISA selection require
   a later milestone.

## Consequences

The scalar, SSE4.1 and AVX2 policies exercise the same state machine and produce
the same public diagnostics. The canonical/internal permutations are explicit
overhead and remain visible in counters and the representative benchmark.
Relation slices, transform and dense-tail SIMD are separate work; this ADR does
not authorize them implicitly.

Until V3 is connected to the production dispatcher and a trained V3 network
can alter moves, this milestone makes no engine-NPS, Elo, LOS, OpenBench,
data-generation or training claim. The validation index, completed local
evidence and outstanding reviewed-head CI/PR artifacts are recorded in
[`hito9-3j-b-v3-incremental-simd`](../evidence/hito9-3j-b-v3-incremental-simd/README.md).
