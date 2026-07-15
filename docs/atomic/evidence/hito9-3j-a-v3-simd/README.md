# H9.3j-a private AtomicNNUEV3 SIMD evidence

This directory records the correctness and narrow local performance evidence
for the first private SSE4.1/AVX2 full-refresh row-addition seam. H9.3j-a does
not connect V3 to search, protocols, bindings, WASM, generation or training.
It therefore makes no engine-NPS, Elo, LOS or playing-strength claim.

## Measurement contract

Correctness precedes measurement. Each ARCH-specific runner must execute the
exact requested ISA and reproduce the scalar and independent Python diagnostic
over the 109-position deterministic corpus, including both perspectives,
special positions and separate fail-closed probes. The object-code audit must
find all four named kernels and the expected signed-widening/add instructions.

The benchmark is intentionally smaller than that correctness gate:

- the runner authenticates and loads the 77,349,879-byte fixture with SHA-256
  `00E46223822D06D7927E884EEC10739BA19EF8DD82A6E262F627D361658080C2`;
- the timed sources are synthetic arrays, not fixture parameter rows: one
  1,024-lane i16 row and one 1,024-lane i8 row;
- each source is accumulated 8,192 times per sample;
- one warm-up precedes five trials, and scalar/SIMD order alternates by trial;
- the comparator is a volatile scalar loop, which deliberately prevents the
  compiler from silently autovectorizing the reference;
- the transcript contains every raw nanosecond sample, both medians and their
  scalar/SIMD ratio.

Consequently the ratio answers only whether the handwritten widening/add
kernel beats that forced scalar loop on this host. It does not measure empty,
sparse, ordinary or explosion-heavy active-row mixes, a full V3 evaluation,
search throughput, or expected game workload. CPU affinity and host isolation
are external measurement controls; the runner does not provide them.

Plain `--benchmark` is report-only. `--promotion-gate` is an explicit local
decision and requires `median_scalar_ns / median_required_ns > 1.000000` for a
non-scalar ISA. CI never invokes the benchmark or applies this noisy threshold;
it enforces exactness and real generated instructions instead.

Build the fixture and exact SSE4.1 runner first (the target also runs the
correctness differential):

```text
make -C src -j8 ARCH=x86-64-sse41-popcnt \
  ATOMIC_V3_SIMD_REQUIRED_ISA=sse41 atomic-v3-simd-tests
```

Then run the fail-closed local promotion wrapper as:

```text
python tests/atomic_v3_simd_benchmark.py \
  --runner src/atomic-v3-simd-tests.exe \
  --net build/atomic-v3-wire-v1.nnue \
  --require-isa sse41 \
  --promotion-gate
```

The AVX2 measurement uses the corresponding AVX2 runner and
`--require-isa avx2`. The wrapper authenticates the fixture before and after
execution, rejects stderr, extra/missing/reordered fields, wrong ISA execution,
noncanonical samples, incorrect medians or ratio, and a mismatched sentinel.

## Post-fix acceptance record

The first measurement was intentionally discarded after review found an SSE
i8 immediate-count/runtime-count defect. Binary hashes, instruction evidence,
differential fingerprints and raw benchmark transcripts are frozen only after
that correction is rebuilt and every gate is rerun.

The final local runs used base
`53c3ad38eeb57182998fb5513e94d17afd365b28`, Windows `10.0.19045`, MSYS2
`g++.exe (Rev8, Built by MSYS2 project) 15.2.0` and an
`AMD Ryzen 9 5950X 16-Core Processor`. The SSE4.1 runner used
`ARCH=x86-64-sse41-popcnt`; AVX2 used `ARCH=x86-64-avx2` with `-mavx2 -mbmi`.
Both final benchmarks inherited affinity mask `0x4`, and the process guard
reported zero competing engine/OpenBench workloads.

| Gate | SSE4.1 | AVX2 |
| --- | --- | --- |
| 109-position scalar/SIMD/Python differential | 109 exact comparisons, zero errors | 109 exact comparisons, zero errors |
| Corpus fingerprint | `0x4FBDB31B354FC080` | `0x4FBDB31B354FC080` |
| Named-symbol/instruction audit | `pmovsxwd`, `pmovsxbd` and `paddd` in both stable SSE4.1 symbols ([summary](objdump-summary.txt)) | `vpmovsxwd`, `vpmovsxbd` and `vpaddd` in both stable AVX2 symbols ([summary](objdump-summary.txt)) |
| Runner SHA-256 | `D6B0F2298F44173D1421FBAD15E75A973481A41D8719A83028B8D74857DCDA8F` | `7394A93FACABD5628E6B03AD998A2709E66C23F98C6A05788A6F5B41A2D47920` |
| Compiler / ARCH | MSYS2 g++ 15.2.0 / `x86-64-sse41-popcnt` | MSYS2 g++ 15.2.0 / `x86-64-avx2 -mavx2 -mbmi` |
| Raw samples and medians | scalar `4089500,4220100,4309800,4377500,4479100`, median `4309800` ns; SSE4.1 `1097300,1146400,1136000,1153900,1146400`, median `1146400` ns ([transcript](benchmark-sse41-final.txt)) | scalar `4134000,4180600,4530900,4347100,4380400`, median `4347100` ns; AVX2 `754200,787600,815700,825100,849300`, median `815700` ns ([transcript](benchmark-avx2-final.txt)) |
| Local promotion ratio | `3.759421`, passed explicit local gate | `5.329288`, passed explicit local gate |

The wrapper's fixture-free parser units passed `18/18` locally after a process
guard reported zero competing engine/OpenBench processes. These units are not
a substitute for the post-fix native runs above.

Community context and immutable local-vault references are recorded in
[`discord-research.md`](discord-research.md).
