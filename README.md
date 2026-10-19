# ci/moky-speedups-1019

Measurement of three NNUE speed-up pull requests against `main@bfc828f6`:
#67 (refresh cache by king square), #68 (sparse first layer) and #70 (SSE2
clamp of the feature transformer output). This branch holds no engine source.

- `.github/workflows/moky_speedups.yml` checks out main by SHA, merges each pull
  request head into it on its own and all three together (the trees are
  pinned), and builds the five variants with `make profile-build` (GCC) for
  `linux x86-64-avx2`, `windows x86-64-bmi2` (network embedded) and, when the
  runner has AVX-512 VNNI, `linux x86-64-vnni512`.
- `ci/build_variants.sh` does the merges and the builds.
- `ci/engine_check.py` requires every variant to search exactly the nodes main
  searches (bench signatures 450539 with NNUE and 657485 classical, a
  worker-like UCI session, every timed bench) and times them in alternating
  rounds. The Linux and Windows binaries are timed on several runners each.
- `ci/pool_checks.py` pools the per-round speed ratios of those runners.

The workflows of the earlier measurements on this lineage only run on their own
branches. Nothing is published from here: binaries and reports are artifacts.
