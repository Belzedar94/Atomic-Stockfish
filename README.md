# ci/blast-order-clang-1102

Clang measurement of the speed cost of ordering captures by their blast value
(`d09ed03e`, branch `mate/capture-blast-order`), against main `722b4735`. The
commit changes the move order, so it changes the search (bench 383181 against
433197) and its speed on the bench is a speed on its own tree. To get the cost
of the term on main's own tree as well, a third variant, `cost` (`50167df6`,
branch `mate/capture-blast-order-cost`, measurement only), computes the term on
every capture exactly as `blast` does and then drops it through a volatile zero
mask: it must search exactly the nodes main searches. This branch holds no
engine source.

- `.github/workflows/blast_order_clang.yml` checks out `722b4735` by SHA, merges
  each commit into it (the trees are pinned), and builds the three variants,
  `main`, `blast` and `cost`, with `make profile-build` and clang for
  `linux x86-64-avx2` and `windows x86-64-bmi2`, with the recipe of the published
  AtomicDB worker engines (ci/worker-engine-pgo-1028). The Windows binaries embed
  the default network; the Linux binaries read it from the run directory under
  its default name, as in the earlier measurements of this lineage.
- `ci/build_variants.sh` does the merges and the builds.
- `ci/engine_check.py` requires `main` and `cost` to search exactly the same
  nodes (bench signature 433197, a worker-like UCI session, every timed bench),
  and `blast` (named with `--own-search`) to have its own signature, 383181, and
  the same nodes in every one of its timed benches. It times the three in 18
  rounds whose order rotates and reverses. It also requires every search to
  report the expected network backend. The Linux binaries are timed on three
  runners and the Windows binaries on four.
- `ci/pool_checks.py` pools the per-round speed ratios of those runners, per
  platform.

The workflows of the earlier measurements on this lineage only run on their own
branches. Nothing is published from here: binaries and reports are artifacts.
