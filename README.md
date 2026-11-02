# ci/legal-quiet-clang-1102

Clang measurement of two legality shortcuts against main `722b4735`:

- `lq`: `a2ec395d` (branch `speed/legal-quiet-shortcut-1102`). Outside check,
  `legal()` accepts a quiet move of a piece other than the king that is not in
  `blockers_for_king(us)` without the post-move attack scan. It is the
  quiet-move part of the legality shortcut of pull request #66, alone.
- `p66`: `5c29397c`, the head of pull request #66, merged into main. Its
  check-status cache is already in main (#72), so the merge adds the rest of
  that pull request: the same quiet shortcut, an exception for a blocker that
  moves along its own line, and king-ray shortcuts for captures in `legal()`
  and `gives_check()`.

Neither may change the search, so the three variants must have bench 433197
and search exactly the same nodes. This branch holds no engine source.

- `.github/workflows/legal_quiet_clang.yml` checks out `722b4735` by SHA, merges
  each commit into it (the trees are pinned), and builds the three variants,
  `main`, `lq` and `p66`, with `make profile-build` and clang for
  `linux x86-64-avx2` and `windows x86-64-bmi2`, with the recipe of the published
  AtomicDB worker engines (ci/worker-engine-pgo-1028). The Windows binaries embed
  the default network; the Linux binaries read it from the run directory under
  its default name, as in the earlier measurements of this lineage.
- `ci/build_variants.sh` does the merges and the builds.
- `ci/engine_check.py` requires `main`, `lq` and `p66` to search exactly the same
  nodes (bench signature 433197, a worker-like UCI session, every timed bench),
  and times the three in 24 rounds whose order rotates and reverses. It also
  requires every search to report the expected network backend. The binaries of
  each platform are timed on four runners.
- `ci/pool_checks.py` pools the per-round speed ratios of those runners, per
  platform.

The workflows of the earlier measurements on this lineage only run on their own
branches. Nothing is published from here: binaries and reports are artifacts.
