# Hito 4 validation record

This record consolidates the protocol and binding milestone. The local Hito 4
release gate passes, including the full UCI/NNUE WASM artifact and its final
reproducible hashes. This is not acceptance of the whole project: platform
matrix jobs and Elo/LOS matches remain separately governed.

## Reproduced result

On 2026-07-10 the fail-fast release run completed every built surface:

| Gate | Result |
| --- | --- |
| C++ rules/state | `30/30` |
| C++ shared API | `29/29` |
| Frozen fixture inventory | 58 fixtures; 22 Python; 58 JavaScript; eight perft |
| Historical `test.py` | 22 passed |
| Extended `pytest` | 54 passed |
| CommonJS Board WASM | 58 fixtures passed |
| ES-module Board WASM | 58 fixtures passed |
| Exact cross-surface parity | Python/CommonJS/ESM `40/40`; native UCI `25/25` |
| Historical perft | eight exact Atomic/Atomic960 vectors |
| Focused rules/transitions | `19/19` |
| NNUE terminal/check-aware search | `7/7` |
| XBoard/CECP | passed, including live ponder promotion/cancellation and analyze |
| Atomic Syzygy fixtures | 11 header/hash checks; driver `5/5` |
| Atomic Syzygy UCI | passed with NNUE false/true |
| Legacy Atomic V1 modes | false/true/pure, invalid recovery and byte-exact export passed |
| Reprosearch | `12/12` |
| Atomic search signature | `356852` |
| Native protocol runtime | UCI/XBoard smoke passed |
| Full UCI/NNUE WASM | passed: interactive false/true/pure, Atomic/Atomic960 perft, terminal handling and external net hash |

There were no skipped tests. The default release invocation completed with
`Hito 4 validation passed`. A separate development run also proved that an
explicit WASM omission is labelled non-releasable.

## Validation snapshot

These hashes identify the artifacts used for the reproduced release run.
They are a local evidence snapshot, not release asset names.

| Artifact | Bytes | SHA-256 |
| --- | ---: | --- |
| `src/atomic-stockfish.exe` | 4,260,135 | `6AA6580B296DDFCE28D6A2FBF7CAF1F78BD66AE8F9CA9A1BC303931FC0FC950C` |
| `src/atomic-unit-tests.exe` | 3,689,439 | `D61210818EE935D6478A39E3240365E0CC34A3B7F2CD725FEF9585593F41B3FE` |
| `src/atomic-api-tests.exe` | 3,695,663 | `8AC5EA90BC6A9B2E351A9021FDA631657BCEF42197065D9EDEF323BF8A596E5D` |
| `pyffish.pyd` | 142,848 | `FEA555F8A9514340FE5E2664A044B5F0ECF0179D883876255D69D66E7143D378` |
| `tests/bindings/atomic-fixtures.json` | 27,840 | `3FF2F6AC231F931360DFA92EAC02B0C4EB4EBE371DCE4FE13F2D28C91BE23529` |
| `tests/bindings/inventory.json` | 24,809 | `A990075951D2391F7FBB1CF50A67A73DE98BCACD018FBFD67861B5512AB86350` |
| CommonJS `ffish.js` | 56,151 | `B5C3D624071A25F297C1993CEF63A6602E5DA0BB4AD38BA5A7CCCF55374178C7` |
| CommonJS `ffish.wasm` | 265,132 | `E849DF763055F40EFD54EA069049391D860F28352AAEE5EA25DE4ED35BAE39B5` |
| ES module `ffish.mjs` | 55,929 | `AF17E8BA6FC9BED8C56088446F28D87498A80842FD38D1F3125A83F821F9E122` |
| ES module `ffish.wasm` | 265,132 | `E849DF763055F40EFD54EA069049391D860F28352AAEE5EA25DE4ED35BAE39B5` |
| Atomic Syzygy test driver | 5,363,969 | `77B45E48B1534325A91614A282E9AA8D31ED51E6AA94CFC58D8E67B5B864EE36` |
| `atomic-stockfish-nnue-node.mjs` | 3,234 | `F9B40EAA35C7B3338F92754127B02B4EE26E480B1321D6BC55141105C2C3737D` |
| `atomic-stockfish-nnue.js` | 103,600 | `D0BD0C360BB8ADC636952F6833F0DD280EC732D00D379D63F0FE99F8857DF0E5` |
| `atomic-stockfish-nnue.worker.js` | 2,828 | `C18C2918C9F8FEDF3009F4A260A1185E919B0C6D421FF5403CB918B61C358A24` |
| `atomic-stockfish-nnue.wasm` | 543,046 | `A38390C73CB59DD762D6C2F4E3872C54128E39CC1CC29000E8FABAEAB9942998` |
| UCI/NNUE WASM `manifest.json` | 1,930 | `79636C494E0604917C18B6E98E55CA86A8408B95088F6F09C10EE90A40A729AC` |
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
