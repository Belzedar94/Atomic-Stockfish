# ci/avx2-clang-1101

Clang measurement of the AVX2 attacks (`a908e669`, pull request #87, a port of
upstream Stockfish `82d73d0a` and `205f0052` on the AVX2 `both_attacks_bb()`
path), merged into main as `c2736596`, against `00f68d61`, main before #87.
In this branch the variant called `main` is `00f68d61`. #87 went in on an
OpenBench non-regression run, which cannot resolve a 1% speed difference. The
commit claims the search is unchanged, so it must search exactly the nodes
`00f68d61` searches, and with it merged the tree is exactly the one of
`c2736596`. This branch holds no engine
source.

- `.github/workflows/avx2_clang.yml` checks out `00f68d61` by SHA, merges
  the commit `a908e669` into it (the trees are pinned), and builds the two
  variants, `main` and `avx2`, with `make profile-build` and clang for
  `linux x86-64-avx2` and `windows x86-64-bmi2`. The Windows binaries embed the
  default network; the Linux binaries read it from the run directory under its
  default name, as in the earlier measurements of this lineage.
- `ci/build_variants.sh` does the merge and the builds.
- `ci/engine_check.py` requires both variants to search exactly the nodes main
  searches and times them in 20 alternating rounds, in ABBA order. The engine
  is NNUE-only, so there is one bench signature (433197); the check also
  requires every search to report the expected network backend. The Linux
  binaries are timed on three runners and the Windows binaries on four.
- The default network (`atomic_run3b_e202_l05.nnue`) is a Legacy Atomic V1
  network. As a cheap extra (the change is not in NNUE code), every runner also
  checks identity with node-limited benches and the session on the synthetic
  AtomicNNUEV2 fixture that main's own CI generates
  (`tests/create_synthetic_atomic_v2_nnue.py`, pinned by its sha256).
- `ci/pool_checks.py` pools the per-round speed ratios of those runners, per
  platform and network.

The workflows of the earlier measurements on this lineage only run on their own
branches. Nothing is published from here: binaries and reports are artifacts.
