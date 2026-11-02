# ci/pgo-atomicdb-load-1102

Does training the clang PGO profile on AtomicDB-like searches, instead of the
default `bench`, make the AtomicDB worker engine faster on AtomicDB-like
searches? Both builds come from main `7f705c8f` (bench 433197) with the recipe of
the published worker engines (ci/worker-engine-pgo-1102):

- `A`: `make profile-build` as published. Its training run is the Makefile's
  `PGOBENCH`, `printf 'bench\nquit\n' | ./EXE`: the 19 default positions at depth
  13, Hash 16, one thread.
- `B`: the same build, with `PGOBENCH` replaced on the make command line by
  `ci/pgo_load.py` over `ci/positions/train.tsv`: 40 positions from AtomicDB (26
  open positions, 14 decided ones with a proven mate), `go nodes` 1M to 2M, Hash
  256, no `ucinewgame`, Threads 1 for the first 20 searches and 4 for the other
  20, MultiPV 1 and 3 alternating, six searches with `searchmoves`.

Only the compiler profile differs, so the two must search exactly the same tree.
This branch holds no engine source.

- `.github/workflows/pgo_atomicdb_load.yml` checks out `7f705c8f` by SHA (the tree
  is pinned) and builds `A` and `B` for `linux x86-64-avx2` (clang 23 from
  apt.llvm.org) and `windows x86-64-bmi2` (MSYS2 clang, network embedded). The
  build jobs show what each profile was trained on and how the profiles differ,
  and check that `A` and `B` give bench 433197 and byte-identical output at fixed
  nodes on the 200 positions of `ci/positions/identity.tsv`. Each platform's pair
  is then timed on four runners.
- `ci/build_pgo_pair.sh` does the two builds. `ci/pgo_train.sh` is the training
  run given to make as `PGOBENCH` (for `A` it runs the default command), and keeps
  a copy of the raw profile, which the build deletes. `ci/show_profiles.sh` merges
  the kept profiles again and compares them.
- `ci/engine_check.py` requires `A` and `B` to search exactly the same nodes (bench
  signature, a worker-like UCI session, every timed run), and times them in
  alternating rounds, single-threaded, on two loads: `bench 16 1 18 default depth`
  (the reference), and with `--load`, `ci/positions/load.tsv`: 60 AtomicDB
  positions, disjoint from the training ones, `go nodes` 2M to 4M, Hash 512, no
  `ucinewgame`, MultiPV 3 on every fifth search, three searches with
  `searchmoves`. On the load, every run of every binary must give the same
  bestmove, depth, seldepth and nodes on every position.
- `ci/identity_run.py` records the fixed-node output without the time and nps
  fields. `ci/pool_checks.py` pools the per-round speed ratios of the runners of
  each platform, for each load.
- `ci/positions/` holds the three disjoint position sets, drawn from public
  AtomicDB data (open positions searched in AtomicDB, proven mates, and the
  atomic matetrack suite), each screened for a playable position on which a
  `go nodes 150000` uses its whole budget.

The workflows of the earlier measurements on this lineage only run on their own
branches. Nothing is published from here: binaries and reports are artifacts.
