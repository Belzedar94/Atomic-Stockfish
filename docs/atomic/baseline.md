# Atomic-Stockfish baseline

This document freezes the inputs used to judge the Atomic-Stockfish port. The
Fairy-Stockfish checkout and binary are an executable oracle, not a source tree
to simplify in place.

## Source revisions

| Component | Revision |
| --- | --- |
| Official Stockfish base | `eca43a97efd2cf0c9b7153c71b85f35e0fd1f5ca` |
| Fairy-Stockfish oracle | `fb78cb561aa01708338e35b3dc3b65a42149a3c4` |
| Variant NNUE tools | `c8df2c39515a2654d5b52ba55b4ee585b20430a8` |
| Variant NNUE trainer | `b15df38a9aae8ab9b40b2378020b3099c7c5d179` |
| Official NNUE trainer reference | `b8512291deb4cd18afa67003bb6bc53dd522cbf0` |

## Toolchain

- Windows 10 / PowerShell
- MSYS2 MinGW g++ 15.2.0
- GNU Make 4.4.1
- Python 3.12.12
- Node.js 24.15.0 and npm 11.13.0
- Emscripten 3.1.46

The historical CI jobs use Emscripten 1.39.16 and Node.js 12. The baseline also
builds with the modern local versions above; Atomic-Stockfish must pin supported
oldest and current versions instead of relying on an unversioned workstation.

## Frozen artifacts

| Artifact | Size | SHA-256 |
| --- | ---: | --- |
| `atomic_run3b_e202_l05.nnue` | 47,721,376 | `99DC67EABF26A64FAEECA3A88B4C38597A840B8D4A874B9F2CF658C6F92A04A6` |
| `FSF_Atomic_baseline.exe` | 4,477,632 | `1AE6D680F03128C8404F31A3F264F28B132B557ED3A91A6445EC563A7A33F623` |
| `variantfishtest_new1.py` | 22,676 | `37D1790096520D9F3A1003746CDFBED59D2CC125A9B3D3192FF3399295EC9D70` |
| Match `variants.ini` | 74,756 | `30A4779FDE75B5259F732A148872AA81DCA96DA7C766238D0153A591D6624E37` |
| Atomic opening book | 394,785 | `28ED51C2F42E723D5E127D2D3F21C0BFA4A9B318615AFDB299B93EA62DEA2B1E` |

The baseline executable is stored outside this Git repository in the workspace
`baseline-artifacts` directory. The network is also an external test artifact;
its hash, not its path or filename, is normative.

## Existing Atomic correctness signatures

The eight Fairy-Stockfish Atomic and Atomic960 perft fixtures currently produce:

```text
197326
1434825
714499
148
61401
98729
241478
17915
```

An optimized single-thread Fairy build loaded the frozen network and searched
3,001,001 deterministic nodes per run. After one warm-up, five measured runs
produced `981681`, `993051`, `971826`, `969942`, and `1007385` NPS: median
`981681`, mean `984777`. CPU affinity was not pinned, so this remains a smoke
baseline; the permanent performance gate uses a multi-position corpus, fixed
affinity, a warm-up, and five serialized repetitions.

Plain `bench atomic` is not an NNUE benchmark in Fairy-Stockfish: the single
default Atomic position is evaluated classically by the `mixed` mode. Tests
must preload `EvalFile` and explicitly request the `NNUE` bench mode.

### Comparative performance gate

The release gate does not compare the engines' built-in `bench` commands,
because those commands use different position sets and evaluation modes.
`tests/atomic_bench_compare.py` instead drives both executables through UCI
over one embedded, hash-identified corpus of ten Atomic and three Atomic960
positions. Both engines load the frozen network and use `Threads=1`, the same
fixed hash size, the same fixed node budget per FEN, and the same pinned logical
CPU. Hash is cleared before every corpus pass. Searches are serialized, with
one warm-up per engine followed by five measured repetitions in alternating
order.

The reported measurement is `sum(nodes) / sum(engine-reported time)` for each
complete corpus pass. The gate compares the median of the five passes and
passes only when Atomic-Stockfish's median NPS is **strictly greater** than the
frozen Fairy-Stockfish baseline. It also records the NPS ratio and both binary
sizes; a smaller binary is informative but is not a substitute for higher NPS.
Example:

```powershell
python tests/atomic_bench_compare.py `
  --candidate src/atomic-stockfish.exe `
  --baseline ../baseline-artifacts/FSF_Atomic_baseline.exe `
  --eval-file ../atomic_run3b_e202_l05.nnue `
  --nodes 100000 --hash 64 --affinity 0
```

Omitting `--affinity` selects the first CPU in the caller's existing affinity
mask. The default node budget keeps a local gate reasonably short; release
reports must preserve the full five measured repetitions.

## Frozen baseline suite

The oracle at the revision above passed:

- Release and debug builds for all eight Atomic/Atomic960 perft vectors with
  the frozen network positively identified.
- The complete `tests/perft.sh` chess, variant, and all-variant matrices.
- All six existing protocol checks, 20 reproducible-search node budgets, and
  search signature `6180480`.
- The MSVC `pyffish` build and all 22 Python tests.
- The CommonJS/WASM build and all 58 JavaScript tests when fixtures use LF.
- Repeated 1,000-node and 10,000-node Atomic searches with identical release
  and debug best moves.

Platform coverage gaps are explicit: MinGW on this workstation lacks the
UBSan runtime, and Valgrind is unavailable on Windows. Those gates run on
Linux CI rather than being skipped globally.

## Pre-existing compatibility gaps

The following are not regressions introduced by Atomic-Stockfish; they are
work items recorded before specialization:

- `Use NNUE=pure` is parsed as ordinary boolean true in the baseline.
- A missing `EvalFile` silently falls back to classical evaluation.
- JavaScript PGN parsing does not strip CRLF, and three async PGN tests are not
  awaited by Mocha.
- `tests/regression.sh` reports a mismatch without failing its exit status.
- XBoard does not implement `playother` and has no Atomic960 baseline test.
- `pyffish` and `ffish.js` expose no perft API.
- The current WASM artifact is a Board/rules library, not a search engine with
  a public NNUE loader.
- The ES-module build targets `web,worker` and cannot be imported directly by
  Node.js.

## Match acceptance gate

Strength testing uses the frozen match script and assets listed above. The
candidate is always engine 1, both engines use the same network, `Threads=1`,
and `Hash=512`. The three time controls are 2000+20 ms, 10000+100 ms, and
30000+300 ms.

Each time control passes only after the script reports both `Total > 100` and
the displayed `LOS: 100.0%`. Since games are scheduled as color-swapped pairs,
the first normally eligible total is 102. All three time controls must pass;
64,000 games is the hard cap for each run.
