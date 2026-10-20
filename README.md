# ci/moky-quiet-order-1020

Measurement of pull request #75 (resolve the history-table references outside
the move-scoring loops of `src/movepick.cpp`, compute the low-ply history row
and divisor once per quiet-scoring call, and accumulate each quiet score locally
before writing `m.value`) against its base, `main@0c8c6084`. The pull request
claims the scoring formulas and the move order are unchanged, so it must search
exactly the nodes main searches. This branch holds no engine source.

- `.github/workflows/moky_quiet_order.yml` checks out main by SHA, merges the
  pull request head `3f890681` into it (the trees are pinned), and builds the
  two variants, `main` and `pr75`, with `make profile-build` (GCC) for
  `linux x86-64-avx2` and `windows x86-64-bmi2` (network embedded). The change
  is scalar code, so there is no AVX-512 or SSE4.1 build this time.
- `ci/build_variants.sh` does the merge and the builds.
- `ci/engine_check.py` requires both variants to search exactly the nodes main
  searches (bench signatures 438711 with NNUE and 735506 classical, a
  worker-like UCI session, every timed bench) and times them in 20 alternating
  rounds, in ABBA order. The Linux binaries are timed on three runners and the
  Windows binaries on four.
- `ci/pool_checks.py` pools the per-round speed ratios of those runners.

The workflows of the earlier measurements on this lineage only run on their own
branches. Nothing is published from here: binaries and reports are artifacts.
