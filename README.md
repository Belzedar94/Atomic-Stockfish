# Atomic-Stockfish

Atomic-Stockfish is a dedicated [Atomic Chess](https://lichess.org/variant/atomic)
engine derived from modern [Stockfish](https://github.com/official-stockfish/Stockfish),
with [Fairy-Stockfish](https://github.com/fairy-stockfish/Fairy-Stockfish) used as
the frozen rules and playing-strength reference.

> [!WARNING]
> This repository is under active development and has no strength-qualified
> release yet. The Hito 4 interface matrix and Hito 5 engine/backend matrix have
> passed. The clean, commit-pinned cross-repository pipeline also passed both
> `strong-local` and public `synthetic-ci`. Hito 6 search blocks 1 through 4 are
> implemented, and the hardened `ebfe9342` BMI2 artifact passed the reproducible
> speed gate plus all three exact LOS gates. This is still a pre-release: the
> Hito 6 record is accepted only when PR #3 also has a clean exact-head CI
> matrix and Codex review.

## Scope

The project deliberately supports one ruleset instead of Fairy-Stockfish's
large variant matrix:

- Atomic and Atomic960 positions;
- UCI and XBoard/CECP;
- the `pyffish` Python API;
- CommonJS and ES-module JavaScript APIs;
- lightweight Board WebAssembly and a Node UCI/NNUE WebAssembly engine;
- Legacy Atomic NNUE V1 networks;
- Atomic Syzygy WDL/DTZ tables (`.atbw` and `.atbz`).

UCCI, USI and non-Atomic variants are outside the release contract.

## Neural network

NNUE files are external artifacts and are never downloaded or embedded by the
native build. Set `EvalFile` to a compatible Legacy Atomic V1 network and select
one of:

```text
setoption name Use NNUE value false
setoption name Use NNUE value true
setoption name Use NNUE value pure
```

`true` is the normal playing mode and the only NNUE mode used for Elo/LOS
matches. `pure` exposes the unadjusted network result for data generation and
training-pipeline compatibility; it is tested on every release surface but is
not a playing-strength mode. `false` disables NNUE.

An incompatible or missing network blocks `go` when NNUE is enabled. The frozen
development reference is `atomic_run3b_e202_l05.nnue`, identified by SHA-256
`99DC67EABF26A64FAEECA3A88B4C38597A840B8D4A874B9F2CF658C6F92A04A6`.
The file is not distributed here.

## Native build

From `src`, inspect the available architectures with `make help`, then build an
appropriate target. On a machine with BMI2/PEXT support, the optimized target
used by the normative Hito 6 comparison is:

```sh
cd src
make -j build ARCH=x86-64-bmi2
```

The resulting executable is `atomic-stockfish` (or
`atomic-stockfish.exe` on Windows).

## Training-data generator

The playing binary deliberately excludes data-generation commands. Build the
isolated executable from `src` instead:

```sh
make -j data-generator ARCH=x86-64-bmi2
```

It writes the frozen 72-byte `legacy-atomic-v1` format by default and the
manifested `atomic-bin-v2` format on request. It requires a compatible network
with `Use NNUE=pure`; V2 preserves Atomic960 while Legacy V1 rejects it. Neither
format appends to or overwrites an existing output. See
[`docs/atomic/data-generator.md`](docs/atomic/data-generator.md) for the command
contract, reproducibility rules and integration tests.

Completed Atomic BIN V2 datasets are validated by the separate manifest-only
reader executable:

```sh
make -j data-tools ARCH=x86-64-bmi2
./atomic-stockfish-data-tools validate --format atomic-bin-v2 \
  --manifest training_data.atbin.manifest.json
./atomic-stockfish-data-tools decode --format atomic-bin-v2 \
  --manifest training_data.atbin.manifest.json --offset 0 --limit 128
```

Its validation and bounded lossless JSONL decode authenticate the complete
dataset before stdout, including corruption beyond a requested slice. The
canonical JSON/exit-code/schema contract and pinned tools-wrapper delegation
boundary are frozen in
[`docs/atomic/data-tools.md`](docs/atomic/data-tools.md).

## Bindings and WebAssembly

Build the Python extension or wheel from the repository root:

```sh
python -m pip install -e .
python test.py
python -m pytest -q tests/python/test_pyffish.py
```

With Emscripten installed, build and test both lightweight JavaScript module
formats from `src`:

```sh
make -f Makefile_js test
```

The full Node UCI/NNUE WebAssembly build is documented in
[`tests/wasm-engine/README.md`](tests/wasm-engine/README.md). Its generated
`.mjs` launcher is the supported entrypoint; the generated runtime glue is not.

## Validation

The release-oriented Hito 4 runner requires explicit native, Python,
JavaScript, tablebase and WASM artifacts. It covers C++ units, `test.py`,
extended Python tests, CommonJS, ES modules, both WASM surfaces, XBoard, Atomic
Syzygy, NNUE modes, perft, reproducibility and the Atomic search signature.

See [`docs/atomic/hito4-validation.md`](docs/atomic/hito4-validation.md) for the
exact invocation and reproduced artifact hashes. The migration inventory is in
[`docs/atomic/test-inventory.md`](docs/atomic/test-inventory.md), and cumulative
search evidence is in
[`docs/atomic/hito6-validation.md`](docs/atomic/hito6-validation.md).

The final project gate is stricter than functional correctness:

1. Atomic-Stockfish must be faster than the frozen Fairy-Stockfish binary on
   the same corpus, CPU affinity, hash, thread count and NNUE network.
2. At each of 2000+20 ms, 10000+100 ms and 30000+300 ms, the match runner must
   report both `Total > 100` and exactly `LOS: 100.0%`.

Small centipawn differences against Fairy are diagnostic rather than a
bit-exact acceptance condition. Unit tests, perft and the match gates are
normative.

## Attribution and license

Atomic-Stockfish retains Stockfish's copyright notices and is distributed under
the [GNU General Public License version 3](Copying.txt). See [AUTHORS](AUTHORS)
for the upstream author list. The Atomic rules, NNUE compatibility and protocol
work are informed by Fairy-Stockfish; its source remains an independently
versioned oracle rather than vendored code.
