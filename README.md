# ci/moky-sparse-wide-1020

Measurement of pull request #71 (scan the sparse input of the first layer in
groups of eight four-byte chunks with SSSE3/AVX2, or sixteen with AVX-512,
instead of four, with a 256-entry lookup table) against its base,
`main@d1de8c5d`. The pull request claims accumulation and evaluation are
unchanged, so it must search exactly the nodes main searches. This branch holds
no engine source.

- `.github/workflows/moky_sparse_wide.yml` checks out main by SHA, merges the
  pull request head `18b04c0f` into it (the trees are pinned), and builds the
  two variants, `main` and `pr71`, with `make profile-build` (GCC) for
  `linux x86-64-avx2` and `windows x86-64-bmi2` (network embedded), the AVX2
  scan; `linux x86-64-vnni512`, the AVX-512 scan, on each of three runners that
  has AVX-512 VNNI; and `linux x86-64-sse41-popcnt`, the scan without AVX2.
- `ci/build_variants.sh` does the merge and the builds.
- `ci/engine_check.py` requires both variants to search exactly the nodes main
  searches (bench signatures 438711 with NNUE and 735506 classical, a
  worker-like UCI session, every timed bench) and times them in 20 alternating
  rounds, in ABBA order. The Linux and Windows AVX2 binaries are timed on
  several runners each.
- `ci/pool_checks.py` pools the per-round speed ratios of those runners.

The workflows of the earlier measurements on this lineage only run on their own
branches. Nothing is published from here: binaries and reports are artifacts.
