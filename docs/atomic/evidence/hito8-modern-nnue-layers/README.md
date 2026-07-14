# H8.2c unused modern-NNUE layer evidence

H8.2c removes three modern NNUE implementation headers and their now-orphaned
SIMD helpers from the LegacyAtomicV1 source inventory:

- `affine_transform_sparse_input.h`
- `sqr_clipped_relu.h`
- `nnz_helper.h`

LegacyAtomicV1 uses only dense `AffineTransform` and `ClippedReLU`, so none of
the deleted code reached the native, data-generator, Python, JavaScript, or
WASM build graphs. The structural guard is deliberately scoped to the legacy
inventory: H9 may add modern layers again under an isolated `AtomicNNUEV2`
backend and must then split the guard by backend.

The normative speed command was:

```text
python tests/atomic_bench_ab.py --candidate src/atomic-stockfish.exe --control ../Atomic-Stockfish-h8-threat-plumbing/src/atomic-stockfish.exe --eval-file ../atomic_run3b_e202_l05.nnue --nodes 100000 --hash 64 --affinity 24 --timeout 60
```

Both BMI2 executables have the same size because the removed headers were never
compiled. The measured +0.22% is recorded as neutral measurement noise, not as
a claimed runtime optimization. The relevant H8.2c gains are a smaller source
and build inventory and removal of unused architecture-specific code.

The local OpenBench worker was paused for the serialized A/B and restored from
the same script's `finally` block using environment-only credentials. A
separate compile-only `x86-64-avx512icl` build passed, exercising the AVX-512
preprocessor branch affected by the SIMD cleanup.
