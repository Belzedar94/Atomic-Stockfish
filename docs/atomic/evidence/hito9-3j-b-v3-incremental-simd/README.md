# H9.3j-b private AtomicNNUEV3 incremental SIMD evidence

H9.3j-b adds exact Scalar, SSE4.1 and AVX2 row application to the existing
private HM incremental stack. The normative implementation contract is
[ADR 0005](../../adr/0005-atomic-nnue-v3-incremental-simd.md). Community context
is preserved separately in [`discord-research.md`](discord-research.md) and is
non-normative.

This file separates the completed final-head local record from evidence that
still depends on the reviewed PR and CI. The private V3 backend remains
unreachable from every product graph, so product compatibility and product NPS
are regression gates rather than playing-strength evidence for V3.

## Frozen contract identities

The inputs and semantic oracles are unchanged. The final-head local kernel and
smoke runs reproduced the expected values below for every available exact ISA.
The release and soak signatures remain frozen oracles, but their H9.3j-b runs
preceded the final reset-mode correction and are therefore not claimed as
exact-head evidence.

```text
fixture bytes   77349879
fixture sha256  00E46223822D06D7927E884EEC10739BA19EF8DD82A6E262F627D361658080C2
H9.3h scalar fingerprint  0x46F68EAB20FF9D50
H9.3h scalar digest       22ae9a6188fa0ebdd0faff9b4a23c25d25380f9b47ebc0e9da2d1b28fe2441b6
expected kernel probe fingerprint  0x21E9FF9A77F881F2
accepted scalar smoke signature    45D43FB02CAA9A3D
accepted scalar release signature  E86C39BDF8187078
accepted scalar soak signature     AF6B51180815972B
```

The product regression gates used the strongest Legacy Atomic V1 network:

```text
network           atomic_run3b_e202_l05.nnue
network bytes     47721376
network sha256    99DC67EABF26A64FAEECA3A88B4C38597A840B8D4A874B9F2CF658C6F92A04A6
playing signature 338376
```

The fixture, H9.3h oracle and 39-event H9.3i trace are documented in the
[H9.3i-a record](../hito9-3i-v3-incremental/README.md). The scalar stress
signatures and directed Atomic/Atomic960 inventory are frozen by the
[H9.3i-b record](../hito9-3i-b-v3-incremental-stress/README.md). H9.3j-a's
accepted exact-ISA and object-code precedent is in the
[full-refresh SIMD record](../hito9-3j-a-v3-simd/README.md).

## Validation index

| Gate | Required evidence | Current status |
| --- | --- | --- |
| Direct row kernels | Scalar/SSE4.1/AVX2 add, remove and restore over tails `0,1,3,4,7,8,9,15,16,17,1023,1024,1025`; source/canary integrity; nonzero and wrap-boundary i64 bases; i16 extrema; null and unavailable-ISA probes; kernel-reported execution identity; fingerprint above | **PASS locally on final head** for Scalar, SSE4.1 and AVX2; each produced `0x21E9FF9A77F881F2` |
| 39-event differential | Independent Python reconstruction of all 39 events (`35` success, `4` fail closed), complete scalar diagnostics, HM source choice, permutation/kernel counters and zero fallback for each requested ISA | **PASS locally on final head** for Scalar, SSE4.1 and AVX2; `35` successes, `4` expected failures, exact requested/executed ISA and zero fallback |
| Reset and fail-closed contract | A legacy reset returns to Scalar and disables HM-delta execution; a subsequent exact-ISA reset enables only that mode; every named but unavailable ISA plus an invalid enum preserves frames, diagnostics and counters | **PASS locally on final head**; review fixes cover reset-mode persistence and prove the stack-level unavailable-ISA transaction |
| Deterministic stress | Full-refresh equality, directed special moves, randomized make/undo, failures, immutable-network concurrency and exact counter accounting; smoke signature unchanged across each ISA | **PASS locally on final head** for Scalar, SSE4.1 and AVX2; each produced `0x45D43FB02CAA9A3D` with zero fallback |
| Release and soak | Reproduce the frozen release and soak semantic signatures with exact ISA and zero fallback; soak remains a local gate | H9.3j-b reproduced `0xE86C39BDF8187078` at 65,536 operations and `0xAF6B51180815972B` at 1,048,576 operations **before** the final reset-mode correction; these runs are retained as supporting evidence, not presented as exact-head results |
| Object code | Stable SSE4.1/AVX2 add and sub symbols contain signed i16-to-i32, signed i32-to-i64 and 64-bit add/sub instructions; portable scalar binary contains no x86 SIMD symbol | **PASS locally for AVX2**: both stable symbols and `vpmovsxwd`, `vpmovsxdq`, `vpaddq`/`vpsubq` were present; complete cross-compiler audit remains a CI gate |
| Wire layouts | Identity, AVX2/LASX and AVX512 forced layouts retain exact results while an AVX2 kernel consumes the authenticated internal layout | CI matrix configured; reviewed-head CI evidence outstanding |
| Platforms and instrumentation | GCC Scalar/SSE4.1/AVX2, Clang AVX2, MinGW scalar, debug/assert, ASan+UBSan, TSan, Valgrind and the 128,000-byte stack guard | MinGW release Scalar/SSE4.1/AVX2 and debug/assert SSE4.1 passed locally, including the stack-use guard; Linux GCC/Clang and instrumented evidence remain CI gates |
| Isolation | New ISA, kernel, backend and harness sources remain absent from production, Python, JavaScript and WASM source graphs; production still registers only Legacy V1 and V2 | **PASS locally** through source/build-graph guards and the complete product-surface gate; reviewed-head CI evidence outstanding |
| Representative benchmark | Exact scalar/requested-ISA results before timing; quiet, capture, promotion, en-passant and maximum-blast transitions; one warm-up, five alternating trials, raw samples and medians | **PASS exactness; report-only timing**. The latest AVX2 aggregate scalar/SIMD ratio was `0.968590`; this is not a speed improvement and no private-V3 performance claim is made |

The primary exactness matrix in `.github/workflows/atomic.yml` requests:

- `general-64` Scalar with GCC;
- `x86-64-sse41-popcnt` SSE4.1 with GCC;
- `x86-64-avx2` AVX2 with GCC and Clang.

Each leg runs the direct kernel probe, the unchanged 39-event differential and
the smoke stress profile. Additional jobs cover forced wire layouts, MinGW,
sanitizers and Valgrind. A green workflow is necessary but is not recorded here
until it belongs to the final reviewed PR head.

The final local audit also removed two self-reporting ambiguities: cumulative
kernel counters now derive from the identity returned by the kernel that ran,
not from the requested enum, and the backend rejects an identity mismatch
before committing a frame. Scalar tail arithmetic uses explicit unsigned
modulo-2^64 storage, matching SSE4.1/AVX2 wrap semantics without signed C++
overflow; direct wrap-boundary probes cover both dispatcher and stable-symbol
paths.

## Product regression and speed record

The final local BMI2 product build passed the complete Hito 4 aggregate gate
with the authenticated Legacy V1 network above. The record includes C++
`87/87`, API `34/34`, historical `test.py` `22/22`, the complete Python tree
`1178/1178`, eight historical perft vectors in `false`, `true` and
data-generation-only `pure` mode, 19 focused Atomic/Atomic960 rule cases,
classical and NNUE search `16/16`, UCI, XBoard, Syzygy, NNUE export/reload,
reprosearch `12/12` in both `true` and `pure`, and signature `338376`.

CommonJS and ES-module Board WASM each passed 58 fixtures. Cross-surface parity
passed `40/40`, with native intersections `25/25`; two complete local UCI/NNUE
WASM builds were byte-identical and loaded the real Legacy V1 network. The
product dispatcher continued to expose only Legacy V1 and AtomicNNUEV2.

The normative product-speed comparison used the final Atomic-Stockfish BMI2
binary and the optimized Fairy baseline built with matching MinGW GCC 15.2
BMI2 settings. Over the fixed 13-position corpus at 100,000 nodes per FEN and
five alternating repetitions, the candidate median was `1,262,958` NPS and
the Fairy median was `1,106,174` NPS: ratio `1.1417`, or **+14.17%**. This is
the product speed gate. It is independent of the private incremental-V3
microbenchmark and does not imply that H9.3j-b changes playing strength.

## Benchmark policy

The benchmark's deterministic preflight and transcript parser belong in CI,
but elapsed time is host evidence only. CI must not fail because a ratio is
below `1.0`, and local acceptance must not hide raw samples behind a single
ratio. The report records the compiler, ARCH, CPU, affinity/isolation
conditions, binary and fixture hashes, per-case samples, medians and aggregate
ratio. It measures private incremental V3 evaluation, not search NPS or
playing strength.

## Acceptance artifacts still outstanding

- reviewed PR head and final CI URL;
- green reviewed-head Linux GCC/Clang, forced-layout, MinGW and instrumented CI
  matrix, including the complete object-code audit;
- an exact-head release/soak rerun if those two long profiles are to be claimed
  as final-head rather than supporting pre-reset-fix evidence.

No H9.3j-b Elo or OpenBench run is applicable: the backend is still
unreachable from the engine's playing path.
