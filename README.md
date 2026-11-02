# ci/profile-1102

CPU profile of the engine the AtomicDB workers run, for a report on which speed
work is worth doing. Report only: no engine source here, no patch, nothing
published; every result is a workflow artifact.

- `.github/workflows/profile.yml` checks out main `722b4735` by SHA (its tree is
  checked), builds it twice with the recipe of the published Linux worker engine
  (`make profile-build ARCH=x86-64-avx2 COMP=clang CXX=clang++-23
  RTLIB=compiler-rt`, ci/worker-engine-pgo-1028) with the default network
  embedded, once as is and once with `-g` added, requires the two to have a
  byte-identical `.text`, bench 433197 and the same worker-like session, and
  records the `-g` binary with `perf record --call-graph dwarf`.
- `ci/atomicdb_load.py` is the load: AtomicDB-shaped tasks (go nodes, Hash 256,
  MultiPV, no `ucinewgame` anywhere), each seed followed by the positions after its
  best moves. Every line is read up to `bestmove` before anything else is sent.
- `ci/profile_seeds.tsv`: 24 open positions (logs/fens_open1000.txt of the
  speed lab) and 16 decided ones from the mate battery (atomic_mt.tsv: one W and
  one L for six bands; atomic_mt_hard.tsv: four H).
- `ci/perf_attr.py` turns the samples into self and accumulated shares per source
  function, inlined functions included (llvm-symbolizer `--inlining`).
- `ci/engine_check.py` (from the earlier measurements of this lineage) checks the
  two builds against each other.

Passes: MultiPV 1 and MultiPV 5 with one thread (40 seeds, 3 positions each, 3M
nodes per position), and MultiPV 5 with four threads (12 seeds, 2 positions,
8M nodes).
