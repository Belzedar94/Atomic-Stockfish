# ci/moky-null-blockers-1022

Measurement of pull request #78 (`do_null_move()` stops recomputing the king
blockers with `set_check_info()` and calls `set_check_squares()` instead, which
only recomputes the side-dependent check squares; the null move copies the whole
`StateInfo` and leaves the board unchanged, so the copied blockers are already
right) against the current main, `main@74ab89ad`. The pull request was opened
against `8e81598f`; main has only #77 on top of it, which touches NNUE files
only, and the merge is clean. The pull request claims the search is unchanged,
so it must search exactly the nodes main searches. This branch holds no engine
source.

- `.github/workflows/moky_null_blockers.yml` checks out main by SHA, merges the
  pull request head `4f40b6a1` into it (the trees are pinned), and builds the
  two variants, `main` and `pr78`, with `make profile-build` (GCC) for
  `linux x86-64-avx2` and `windows x86-64-bmi2`. The Windows binaries embed the
  default network; the Linux binaries read it from the run directory under its
  default name, as in the earlier measurements of this lineage.
- `ci/build_variants.sh` does the merge and the builds.
- `ci/engine_check.py` requires both variants to search exactly the nodes main
  searches and times them in 20 alternating rounds, in ABBA order. The engine
  is NNUE-only, so there is one bench signature (438711); the check also
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
