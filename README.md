# ci/worker-engine-pgo

Build-and-check recipe for the engine binaries the AtomicDB workers download.
This branch holds no engine source: the workflow checks the engine out by SHA
from `worker-engine-1adc5239-pgo`, which is `main@1adc5239` plus the
`src/Makefile` part of PR #59 (so that `make profile-build` profiles the NNUE
evaluation at that commit).

- `.github/workflows/worker_engine_pgo.yml` builds the engine profile-guided for
  `linux x86-64-avx2`, `windows x86-64-bmi2` and `macos apple-silicon`, next to a
  plain build of the same tree, and compares both with the binary served today.
- `ci/engine_check.py` is the comparison: bench signatures (338376 with NNUE,
  825643 classical), identical output on a worker-like UCI session (go nodes,
  MultiPV, searchmoves, Syzygy), and nodes per second over alternating runs.
- `ci/fetch_tb.py` fetches a dozen small tablebase files for that session.

Nothing is published from here. The binaries are workflow artifacts.
