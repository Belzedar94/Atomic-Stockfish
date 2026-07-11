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
- CPython 3.12.0 for the Windows LOS runner
- Node.js 24.15.0 and npm 11.13.0
- Emscripten 3.1.46

The historical CI jobs use Emscripten 1.39.16 and Node.js 12. The baseline also
builds with the modern local versions above; Atomic-Stockfish must pin supported
oldest and current versions instead of relying on an unversioned workstation.

## Frozen artifacts

| Artifact | Size | SHA-256 |
| --- | ---: | --- |
| `atomic_run3b_e202_l05.nnue` | 47,721,376 | `99DC67EABF26A64FAEECA3A88B4C38597A840B8D4A874B9F2CF658C6F92A04A6` |
| Fairy BMI2 (`all=no`, `largeboards=no`) | 4,281,871 | `4EACAAB40DCA84F5A255EA57231F2795D43B5DDA85CE50EBBA1A1B2937B46331` |
| Historical Fairy SSE baseline | 4,477,632 | `1AE6D680F03128C8404F31A3F264F28B132B557ED3A91A6445EC563A7A33F623` |
| `variantfishtest_new1.py` | 22,676 | `37D1790096520D9F3A1003746CDFBED59D2CC125A9B3D3192FF3399295EC9D70` |
| Match `stat_util.py` | 4,476 | `06AF2F59CC22EB17213F67D243BAFC0FB2E4BB6627026787EDE9D4CF337387EA` |
| Match `chess/__init__.py` 0.8.0 | 105,072 | `28BB8423AE3D64752713CB7430821D5B0E7CE3DC9872CA329EC3EC39FAD8EE5E` |
| Match `chess/uci.py` | 45,087 | `B9E5AAD44EB2047698866AB3E141B22B6744DA6A504D96268F3A330654278991` |
| CPython `python.exe` | 103,192 | `42AC541168E97DEDB9AABD8BE335539FC41C682E414B9E8D137B164FB68683B0` |
| CPython `python312.dll` | 6,972,184 | `E7890E38256F04EE0B55AC5276BBF3AC61392C3A3CE150BB5497B709803E17CE` |
| psutil 7.2.2 `__init__.py` | 92,363 | `7B6A0675824EB1FA2FF0CB1EB36E358DC454703E51DFA4E9A0E6CCD26A159F0C` |
| psutil 7.2.2 `_common.py` | 26,115 | `6FC6BF5F86491BA962521374472570238929003CEBEA8E9B6C55224084B52BB0` |
| psutil 7.2.2 `_ntuples.py` | 11,399 | `96F42BC24549636B5707A949B7CC92E89C87F7CEA7D343A6D17821AC670DFCD2` |
| psutil 7.2.2 `_psutil_windows.pyd` | 70,656 | `0035450801BD7D938E9E146C5EC28E619CB5A5F4A18CDC53AC7E9734C7F94F78` |
| psutil 7.2.2 `_pswindows.py` | 36,466 | `0BBD52DCB214735BE4168D11A2AE192D5BC7265C8CF72C611179476479687F54` |
| Match `variants.ini` | 74,756 | `30A4779FDE75B5259F732A148872AA81DCA96DA7C766238D0153A591D6624E37` |
| Atomic opening book | 394,785 | `28ED51C2F42E723D5E127D2D3F21C0BFA4A9B318615AFDB299B93EA62DEA2B1E` |

The normative Fairy executable is stored outside this Git repository under
`reports/fsf-baseline-fb78cb56-bmi2`; its reproducible build manifest is
versioned with the Hito 6 evidence. It uses `ARCH=x86-64-bmi2`, `all=no`,
`largeboards=no`, O3 and LTO without PGO. `nnue=no` disables embedding only;
the external Legacy Atomic network loader remains enabled and passed a load
smoke. Fairy has no engine-side `largedata` option at this revision, so that
setting is not applicable. The older `baseline-artifacts` executable is kept
as historical evidence but is not valid for performance or strength gates
against a BMI2 candidate. The network is also external; its hash, not its path
or filename, is normative.

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

Before any search, the runner snapshots both executables and the network. It
requires the exact frozen Fairy BMI2 SHA, the exact Legacy Atomic V1 network,
all five loaded psutil 7.2.2 Python/native modules that enforce affinity, and
the compiler-reported release signature
`64bit BMI2 AVX2 SSE41 SSSE3 SSE2 POPCNT` from both engines. Compiler family
and version must also match. A BMI2 candidate paired with the historical SSE
baseline, or two equal but non-normative targets, therefore fails configuration
instead of producing an ISA-confounded result. After both engines close, all
three workload files and all psutil modules are hashed again; mutation is a
hard error and an unchanged run
prints `Benchmark artifact postflight: PASS`.

The reported measurement is `sum(nodes) / sum(engine-reported time)` for each
complete corpus pass. The gate compares the median of the five passes and
passes only when Atomic-Stockfish's median NPS is **strictly greater** than the
frozen Fairy-Stockfish baseline. It also records the NPS ratio and both binary
sizes; a smaller binary is informative but is not a substitute for higher NPS.
Example:

```powershell
python tests/atomic_bench_compare.py `
  --candidate src/atomic-stockfish.exe `
  --baseline ../reports/fsf-baseline-fb78cb56-bmi2/artifacts/FSF-fb78cb56-bmi2-all-no-largeboards-no.exe `
  --eval-file ../atomic_run3b_e202_l05.nnue `
  --nodes 100000 --hash 64 --affinity 0
```

`--affinity` is mandatory so the selected logical CPU is explicit in the log.
The normative runner rejects any node budget other than 100,000 per FEN or any
hash other than 64 MiB, and always preserves the full five measured
repetitions. Shorter or differently configured diagnostics must not use this
release-gate command.

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
