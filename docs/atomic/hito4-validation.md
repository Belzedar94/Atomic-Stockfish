# Hito 4 validation record

This record consolidates the protocol and binding milestone. The local Hito 4
release gate passes, including the full UCI/NNUE WASM artifact and its final
reproducible hashes. This is not acceptance of the whole project: platform
matrix jobs and Elo/LOS matches remain separately governed.

## Reproduced result

On 2026-07-11 the fail-fast release run completed every built surface:

| Gate | Result |
| --- | --- |
| C++ rules/state | `44/44` |
| C++ shared API | `33/33` |
| Frozen fixture inventory | 58 fixtures; 22 Python; 58 JavaScript; eight perft |
| Historical `test.py` | 22 passed |
| Extended `pytest` | 58 passed |
| CommonJS Board WASM | 58 fixtures passed |
| ES-module Board WASM | 58 fixtures passed |
| Exact cross-surface parity | Python/CommonJS/ESM `40/40`; native UCI `25/25` |
| Historical perft | eight exact Atomic/Atomic960 vectors |
| Focused rules/transitions | `19/19` |
| NNUE terminal/check-aware search | `11/11` |
| XBoard/CECP | passed, including live ponder promotion/cancellation and analyze |
| Atomic Syzygy fixtures | 11 header/hash checks; driver `5/5` |
| Atomic Syzygy UCI | passed with NNUE false/true |
| Legacy Atomic V1 modes | false/true/pure, invalid recovery and byte-exact export passed |
| Reprosearch | `12/12` |
| Atomic search signature | `404217` |
| Native protocol runtime | UCI/XBoard smoke passed |
| Full UCI/NNUE WASM | passed: interactive false/true/pure, Atomic/Atomic960 perft, terminal handling and external net hash |

There were no skipped tests. The default release invocation completed with
`Hito 4 validation passed`. A separate development run also proved that an
explicit WASM omission is labelled non-releasable.

The signature changed from `445618` to `404217` when the search began preserving
pre-existing Atomic checks from analysis FENs. Three independent release runs
reproduced `404217`; the dedicated `gives_check` and search regressions pin the
corrected behavior.

Hito 6 subsequently changed the current signature to `347633` through the
independently gated Atomic move-count pruning block. The table above remains
the historical Hito 4 closeout record; current runners use the Hito 6
signature documented in `hito6-validation.md`. The second Hito 6 search block,
which protects explosive captures from orthodox futility pruning, moved the
live signature again to `379531`; `347633` remains the block-one artifact.

## Validation snapshot

These hashes identify the artifacts used for the reproduced release run.
They are a local evidence snapshot, not release asset names.

| Artifact | Bytes | SHA-256 |
| --- | ---: | --- |
| `src/atomic-stockfish.exe` | 4,268,032 | `4713F508D7F680F14D66CE41423566F2C13824D7A2A6213AFFE618EA9EB42E08` |
| `src/atomic-unit-tests.exe` | 3,702,111 | `27F5AA4AD8F9C6F82D84ED3190E01899D1CF9B473FF936790B79C98E1D200964` |
| `src/atomic-api-tests.exe` | 3,705,147 | `AE4A1706686D75F857D6C962E030C58DCF3F0ECEE9927DD852D3DF986324BF1D` |
| `pyffish.pyd` | 147,968 | `3A1A99F9022EFF7BFECB788EB26D9D51ACF1577D187E82B856C7598C0ECC7AEF` |
| `tests/bindings/atomic-fixtures.json` | 29,064 | `28C09AA1B63D2B3792D7C92B4061A826ABACBA512125CB31BEC4CEED89726056` |
| `tests/bindings/inventory.json` | 25,685 | `B88E89121FA0C04451B0A0BD1CC2572EE0305F547F55C868267B9067BA2FB47E` |
| CommonJS `ffish.js` | 56,151 | `B5C3D624071A25F297C1993CEF63A6602E5DA0BB4AD38BA5A7CCCF55374178C7` |
| CommonJS `ffish.wasm` | 268,622 | `416FBFA96B39EDE637EF3AE0EC18355A0A28E18828D36F12E46AFEBDB1AA8823` |
| ES module `ffish.mjs` | 55,929 | `AF17E8BA6FC9BED8C56088446F28D87498A80842FD38D1F3125A83F821F9E122` |
| ES module `ffish.wasm` | 268,622 | `416FBFA96B39EDE637EF3AE0EC18355A0A28E18828D36F12E46AFEBDB1AA8823` |
| Atomic Syzygy test driver | 5,363,969 | `77B45E48B1534325A91614A282E9AA8D31ED51E6AA94CFC58D8E67B5B864EE36` |
| `atomic-stockfish-nnue-node.mjs` | 3,342 | `885E7A161EF8D447D41F54BD8ABE413DA3113B14E8B421C4848798C1B02D6DEB` |
| `atomic-stockfish-nnue.js` | 103,600 | `D0BD0C360BB8ADC636952F6833F0DD280EC732D00D379D63F0FE99F8857DF0E5` |
| `atomic-stockfish-nnue.worker.js` | 2,828 | `C18C2918C9F8FEDF3009F4A260A1185E919B0C6D421FF5403CB918B61C358A24` |
| `atomic-stockfish-nnue.wasm` | 546,195 | `186BE9A03012AF5371AB27CFB593F42B13930234B43FFFDC55B6347839FDD739` |
| UCI/NNUE WASM `manifest.json` | 1,930 | `9BF73DD49BB8A7AE7AB572F5D17EEF1328F4D2E4C5159901699AA4D572126533` |
| `atomic_run3b_e202_l05.nnue` | 47,721,376 | `99DC67EABF26A64FAEECA3A88B4C38597A840B8D4A874B9F2CF658C6F92A04A6` |

The supported entrypoint is exclusively `atomic-stockfish-nnue-node.mjs`.
`atomic-stockfish-nnue.js` is generated runtime glue and direct execution is
explicitly unsupported. Two clean builds reproduced the four artifact hashes;
the release runner independently checks every manifest size/hash before
starting integration tests. The lightweight Board WASM artifacts remain a
separate public surface.

## Normative runner

`tests/run_hito4.py` accepts explicit paths for every public artifact. It
validates all inputs and the frozen network hash before running tests, executes
gates in dependency order, checks exact coverage markers, and stops at the
first error.

Release invocation (WASM is mandatory by default):

```powershell
python tests/run_hito4.py `
  --native src/atomic-stockfish.exe `
  --net ../atomic_run3b_e202_l05.nnue `
  --pyffish pyffish.pyd `
  --cjs tests/js/dist/cjs/ffish.js `
  --esm tests/js/dist/esm/ffish.mjs `
  --tables ../research/shakmaty/shakmaty-syzygy/tables/atomic `
  --wasm-wrapper build/wasm-engine/atomic-stockfish-nnue-node.mjs
```

For diagnosis of a partial local build, the only permitted WASM omission is
explicit:

```powershell
python tests/run_hito4.py `
  --native src/atomic-stockfish.exe `
  --net ../atomic_run3b_e202_l05.nnue `
  --pyffish pyffish.pyd `
  --cjs tests/js/dist/cjs/ffish.js `
  --esm tests/js/dist/esm/ffish.mjs `
  --tables ../research/shakmaty/shakmaty-syzygy/tables/atomic `
  --allow-missing-wasm
```

That command returning zero confirms the already-built surfaces only. It can
never be quoted as a release pass because the runner says so in its terminal
summary.

## Remaining project evidence

1. Complete the platform build matrix and sanitizer jobs.
2. Run the separately governed performance and strength gates. Small raw
   evaluation deltas are diagnostic; unit/perft correctness and the three
   exact LOS gates remain normative.
