# ci/atomic-wins-clang-1102

Clang measurement of `20dd2b92` (branch `speed/atomic-wins-dead-1102`), which
drops the `atomic_wins()` calls of the `search()` and `qsearch()` move loops that
`king_explosion_move()` makes dead below the root, against main `722b4735`. The
commit must not change the search, so both variants must have bench 433197 and
search exactly the same nodes. This branch holds no engine source.

- `.github/workflows/atomic_wins_clang.yml` checks out `722b4735` by SHA, merges
  the commit into it (the trees are pinned), and builds the two variants, `main`
  and `wins`, with `make profile-build` and clang for `linux x86-64-avx2` and
  `windows x86-64-bmi2`, with the recipe of the published AtomicDB worker engines
  (ci/worker-engine-pgo-1028). The Windows binaries embed the default network;
  the Linux binaries read it from the run directory under its default name, as in
  the earlier measurements of this lineage.
- `ci/build_variants.sh` does the merge and the builds.
- `ci/engine_check.py` requires `main` and `wins` to search exactly the same
  nodes (bench signature 433197, a worker-like UCI session, every timed bench),
  and times the two in 24 alternating rounds (ABBA). It also requires every
  search to report the expected network backend. The binaries of each platform
  are timed on four runners.
- `ci/pool_checks.py` pools the per-round speed ratios of those runners, per
  platform.

The workflows of the earlier measurements on this lineage only run on their own
branches. Nothing is published from here: binaries and reports are artifacts.
