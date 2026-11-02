# ci/nnue-backward-clang-1102

Clang measurement of one NNUE accumulator change against main `722b4735`:

- `bw`: `114cd1e8` (branch `speed/nnue-backward-update-1102`). After a refresh,
  the accumulator is walked back down to the last king move of that
  perspective, or to the root, with backward incremental updates, as upstream
  Stockfish does. Both the Legacy Atomic V1 and the AtomicNNUEV2 accumulator
  get it.

It may not change the search, so both variants must have bench 433197 and
search exactly the same nodes. This branch holds no engine source.

- `.github/workflows/nnue_backward_clang.yml` checks out `722b4735` by SHA,
  merges the commit into it (the trees are pinned), and builds the two
  variants, `main` and `bw`, with `make profile-build` and clang for
  `linux x86-64-avx2` and `windows x86-64-bmi2`, with the recipe of the
  published AtomicDB worker engines (ci/worker-engine-pgo-1028). The Windows
  binaries embed the default network; the Linux binaries read it from the run
  directory under its default name, as in the earlier measurements of this
  lineage.
- `ci/build_variants.sh` does the merge and the builds.
- `ci/engine_check.py` requires `main` and `bw` to search exactly the same
  nodes (bench signature 433197, a worker-like UCI session, every timed
  bench), and times the two in 24 alternating (ABBA) rounds. It also requires
  every search to report the expected network backend. The binaries of each
  platform are timed on four runners, with the default network (Legacy Atomic
  V1) and then with the synthetic AtomicNNUEV2 network of main's CI, 24 rounds
  each.
- `ci/pool_checks.py` pools the per-round speed ratios of those runners, per
  platform and network.

The workflows of the earlier measurements on this lineage only run on their own
branches. Nothing is published from here: binaries and reports are artifacts.
