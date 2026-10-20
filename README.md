# ci/moky-check-cache-1020

Measurement of pull request #72 (cache the side-to-move check status in
StateInfo: do_move stores its givesCheck, null moves recompute it, search and
qsearch read the cached value) against its base, `main@62c60604`. The pull
request claims to be a pure speed-up, so it must search exactly the nodes main
searches. This branch holds no engine source.

- `.github/workflows/moky_check_cache.yml` checks out main by SHA, merges the
  pull request head `ca35937f` into it (the trees are pinned), and builds the
  two variants, `main` and `pr72`, with `make profile-build` (GCC) for
  `linux x86-64-avx2`, `windows x86-64-bmi2` (network embedded) and, when the
  runner has AVX-512 VNNI, `linux x86-64-vnni512`.
- `ci/build_variants.sh` does the merge and the builds.
- `ci/engine_check.py` requires both variants to search exactly the nodes main
  searches (bench signatures 450539 with NNUE and 657485 classical, a
  worker-like UCI session, every timed bench) and times them in 20 alternating
  rounds, in ABBA order. The Linux and Windows binaries are timed on several
  runners each.
- `ci/pool_checks.py` pools the per-round speed ratios of those runners.

The workflows of the earlier measurements on this lineage only run on their own
branches. Nothing is published from here: binaries and reports are artifacts.
