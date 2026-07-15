# H9.3h AtomicNNUEV3 scalar execution evidence

H9.3h adds a private scalar correctness backend over the strict H9.3g
`Network` and the H9.3f single-snapshot full-refresh oracle. It does not add V3
to the production dispatcher or any playing interface.

## Frozen execution contract

- Feature rows are accumulated in wide scratch from mixed i16/i8 tensors,
  range-checked and published in canonical logical order as i32.
- HM-only PSQT is accumulated in i64, combined as `(stm - opponent) / 2`, and
  exposed after the inherited `/ 16` scale.
- SFNNv15 transforms the side-to-move perspective first and the opponent
  second using `clamp(a, 0, 255) * clamp(b, 0, 255) / 512`.
- FC0, FC1 and FC2 are scalar output-major affine layers with i64 sums and
  checked i32 outputs. Squared/clipped shifts are `21/7` for FC0 and `19/6`
  for FC1.
- Raw dense output is `fc2 + fc0[30] - fc0[31]`; scaling is
  `raw * 9600 / 16384`, followed by `/ 16` for the public positional value.
- Every rejected feature, accumulator or dense operation clears all published
  diagnostics.

The complete diagnostic corpus has the frozen fingerprint
`0x46F68EAB20FF9D50` under forced identity, AVX2/LASX and AVX512 parameter
permutations. The positive adversarial dense vector has fingerprint
`0x7D0DA72A5D16C2F5`, raw output `51544`, scaled output `30201` and positional
value `1887`. Its negative counterpart has fingerprint
`0x783599A803610392`, raw output `-128456`, scaled output `-75267` and positional
value `-4704`.

## Acceptance surface

`make -C src atomic-v3-scalar-tests` loads the authenticated mixed-wire
fixture, runs the C++ transactional/concurrency/save selftests, and invokes an
independent Python wire decoder. The differential compares both perspectives'
emissions, canonical accumulators, all PSQT lanes, transformed bytes, the
selected material bucket, every FC intermediate and final raw/scaled/public
values. Targeted cases make each feature tensor nonzero and cover an odd
negative PSQT difference; dense-only vectors exercise signed block boundaries,
skip outputs, both result signs, affine i32 rejection and final raw-composition
rejection.

CI repeats the target with GCC, Clang, debug/assert, MinGW, real AVX2, forced
identity/AVX2-LASX/AVX512 policies, ASan, UBSan, TSan and Valgrind. The existing
dispatcher gate must continue to reject the V3 fixture for both `Use NNUE=true`
and data-generation-only `Use NNUE=pure`.

Local Windows acceptance passed MinGW x86-64 release, debug/assert, AVX2 and
BMI2 selftests plus the complete scalar differential. Forced identity,
AVX2/LASX and AVX512 layouts all produced the frozen corpus fingerprint. The
strict wire selftest/differential, standalone full-refresh selftest, five new
Python oracle tests, 64 focused V3/dispatcher source tests, the complete Python
tree `1015/1015` and historical `test.py` `22/22` passed. A BMI2 production
build retained exactly two backends: V3 was rejected for `true` and `pure`,
while `false` remained searchable. Python 3.9 grammar, `py_compile`, YAML
parsing, clang-format and `git diff --check` also passed.

No Elo, LOS, bench, OpenBench or training run applies to H9.3h. The backend is
private and cannot affect engine moves; it exists to be the exact oracle for
the later SIMD and incremental implementations.
