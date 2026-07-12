# Cooperative Atomic LOS gate

`tests/atomic_los_gate.py` is a thin wrapper around the external
`variantfishtest_new1.py`. It imports that runner by its file path and does not
copy or modify it. The import is accepted only at SHA-256
`37D1790096520D9F3A1003746CDFBED59D2CC125A9B3D3192FF3399295EC9D70`.
Match scheduling, score accounting and the LOS formula stay in that frozen
external runner. In particular, the wrapper reads the one-decimal LOS text
produced by the runner's own `elo_stats()` function.

The normative gate is `Total > 100` and displayed `LOS: 100.0%`. The wrapper
checks the condition only between color-swapped pairs. With multiple workers,
already-running pairs are allowed to finish; therefore the final total can be a
few pairs above the first eligible total of 102. Exit status is zero only when
the final, joined result still passes the gate. A final miss returns one, while
configuration or runner errors return two.

The exclusive Total threshold and exact one-decimal LOS target are required
options. Runner options follow a literal `--` separator. For the first time
control, from the match-script directory:

```powershell
python "<Atomic-Stockfish>\tests\atomic_los_gate.py" `
  --runner ".\variantfishtest_new1.py" `
  --min-total-exclusive 100 `
  --target-los 100.0 `
  -- `
  "<candidate>\Atomic-Stockfish_hito6.exe" `
  --e1-options EvalFile=atomic_run3b_e202_l05.nnue `
  --e1-options "Use NNUE=true" `
  --e1-options Threads=1 `
  --e1-options Hash=512 `
  "<frozen-bmi2-baseline>\FSF-fb78cb56-bmi2-all-no-largeboards-no.exe" `
  --e2-options EvalFile=atomic_run3b_e202_l05.nnue `
  --e2-options "Use NNUE=true" `
  --e2-options Threads=1 `
  --e2-options Hash=512 `
  -t 2000 -i 20 -c variants.ini -v atomic -n 64000 -T 4 -b --verbosity 2
```

Repeat unchanged except for `-t 10000 -i 100` and then
`-t 30000 -i 300`. These are the only three accepted time controls. The
wrapper also requires exactly `-v atomic -n 64000 -T 4 --verbosity 2`, the
frozen `variants.ini`, and the frozen Atomic EPD book; a merely even but
different game cap is not normative.

Elo/LOS games use normal playing evaluation. The wrapper requires both engines
to receive `Use NNUE=true` explicitly and rejects `false`, a missing mode, and
`pure`; `pure` is reserved for data generation. SPRT is also rejected because
it changes the runner's reported statistics and stop contract.

Before the external runner starts any game, the Windows-only normative wrapper
requires CPython 3.12.0 and hashes `python.exe`, `python312.dll`, the runner's
local chess 0.8.0 `__init__.py`/`uci.py`, every loaded psutil 7.2.2 module
(`__init__.py`, `_common.py`, `_ntuples.py`, `_pswindows.py` and
`_psutil_windows.pyd`), the runner, statistical helper, both engines, both
`EvalFile` paths, book and config. All psutil files must come from the same
canonical package directory and are rehashed after the match because psutil
governs the process snapshot and bounded leak cleanup. It
requires the frozen book, config and Legacy Atomic V1 network hashes, plus the
Fairy baseline SHA-256
`4EACAAB40DCA84F5A255EA57231F2795D43B5DDA85CE50EBBA1A1B2937B46331`.
Both engines must receive exactly `Use NNUE=true`, `Threads=1`, `Hash=512`, and
an existing `EvalFile` with the frozen network hash; additional engine options
are rejected because they can change the result. A successful check emits a
`Normative LOS assets: PASS` line.

All consumed paths are replaced with their canonical fingerprinted targets
before the runner starts. A two-engine playing smoke then loads the exact net
with `Use NNUE=true` and requires each engine to complete a one-node Atomic
search without an NNUE/protocol error.

The wrapper then executes each engine's real `compiler` command. Both outputs
must report the exact release signature
`64bit BMI2 AVX2 SSE41 SSSE3 SSE2 POPCNT`, and their parsed compiler family and
version must match. This prevents an equal-but-wrong target, or a BMI2
candidate compared with an SSE baseline, from becoming normative.

After normal completion all worker pairs have joined and the engines are
closed before every input is hashed again. Any path, size or SHA drift is a
hard configuration error; only unchanged inputs emit
`LOS artifact postflight: PASS`.

Any match-instance or worker exception invalidates the complete TC instead of
silently discarding a pair. A 900-second interval with no completed score pair
also trips the watchdog. On watchdog, failure or interruption, the wrapper
sets a cooperative abort, uses `psutil` to terminate only new descendants that
resolve to either fingerprinted engine, and gives its daemon worker threads a
bounded 30 seconds to join. A surviving worker is reported explicitly rather
than hanging Python shutdown indefinitely. Infrastructure/configuration
failures return code 2; only a completed statistical miss returns code 1, and
Ctrl-C returns 130 after cleanup.

Before starting every local test, match, build-test or benchmark, enumerate
active test workloads system-wide. If more than one is already running, wait;
with zero or one, the next workload may start. Consequently no more than two
local test workloads may overlap under the current project policy. For the
three LOS controls, TC3 waits whenever TC1 and TC2 are both still active.

The fast wrapper tests are part of the normal pytest discovery path:

```powershell
python -m pytest -q `
  tests/python/test_atomic_los_gate.py `
  tests/python/test_atomic_compiler_preflight.py
```
