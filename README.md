# ci/moky-refresh-once-1022

Measurement of pull request #77 (remove the `requires_refresh()` checks from the
forward accumulator updates of Legacy Atomic V1 and AtomicNNUEV2, because the
backward scan before them already stops at every accumulator that needs a
refresh) against its base, `main@8e81598f`. The pull request claims the
evaluation is unchanged, so it must search exactly the nodes main searches.
This branch holds no engine source.

- `.github/workflows/moky_refresh_once.yml` checks out main by SHA, merges the
  pull request head `fb6de014` into it (the trees are pinned), and builds the
  two variants, `main` and `pr77`, with `make profile-build` (GCC) for
  `linux x86-64-avx2` and `windows x86-64-bmi2`. The Windows binaries embed the
  default network; the Linux binaries read it from the run directory under its
  default name, as in the earlier measurements of this lineage.
- `ci/build_variants.sh` does the merge and the builds.
- `ci/engine_check.py` requires both variants to search exactly the nodes main
  searches and times them in 20 alternating rounds, in ABBA order. The engine
  is NNUE-only since #76, so there is one bench signature (438711) and no
  classical bench; the check also requires every search to report the
  expected network backend. The Linux binaries are timed on three runners and
  the Windows binaries on four.
- The default network (`atomic_run3b_e202_l05.nnue`) is a Legacy Atomic V1
  network. AtomicNNUEV2 networks are external-only and none is published, so
  every runner also checks identity (bench, session, a short timed bench) with
  the synthetic AtomicNNUEV2 fixture that main's own CI generates
  (`tests/create_synthetic_atomic_v2_nnue.py`, pinned by its sha256).
- `ci/pool_checks.py` pools the per-round speed ratios of those runners, per
  platform and network.

The workflows of the earlier measurements on this lineage only run on their own
branches. Nothing is published from here: binaries and reports are artifacts.
