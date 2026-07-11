# Cooperative Atomic LOS gate

`tests/atomic_los_gate.py` is a thin wrapper around the external
`variantfishtest_new1.py`. It imports that runner by its file path and does not
copy or modify it. Match scheduling, score accounting and the LOS formula stay
in the external runner. In particular, the wrapper reads the one-decimal LOS
text produced by the runner's own `elo_stats()` function.

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
  ".\FSF_Atomic_baseline.exe" `
  --e2-options EvalFile=atomic_run3b_e202_l05.nnue `
  --e2-options "Use NNUE=true" `
  --e2-options Threads=1 `
  --e2-options Hash=512 `
  -t 2000 -i 20 -c variants.ini -v atomic -n 64000 -T 4 -b --verbosity 2
```

Repeat unchanged except for `-t 10000 -i 100` and then
`-t 30000 -i 300`. Keep `-n` even; the wrapper rejects odd limits so the
external runner cannot truncate a pair at the cap.

Elo/LOS games use normal playing evaluation. The wrapper requires both engines
to receive `Use NNUE=true` explicitly and rejects `false`, a missing mode, and
`pure`; `pure` is reserved for data generation. SPRT is also rejected because
it changes the runner's reported statistics and stop contract.

The fast wrapper tests are part of the normal pytest discovery path:

```powershell
python -m pytest -q tests/python/test_atomic_los_gate.py
```
