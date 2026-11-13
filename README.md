# Atomic-Stockfish

Atomic-Stockfish is a dedicated [Atomic Chess](https://lichess.org/variant/atomic)
engine derived from modern [Stockfish](https://github.com/official-stockfish/Stockfish),
with [Fairy-Stockfish](https://github.com/fairy-stockfish/Fairy-Stockfish) used as
the frozen rules and playing-strength reference.

> [!NOTE]
> Version 1.0.3 is the first stable, strength-qualified release. The earlier
> `v1.0.0`, `v1.0.1` and `v1.0.2` tags are failed prepublication candidates
> and have no release assets. Every merge to `main` also publishes binaries in
> the rolling `rolling-main` prerelease.

## Development

Development happens on `main`. It is the default branch, and it is always the
baseline that our [OpenBench instance](https://belzedar.duckdns.org) tests every
patch against.

A patch travels this path:

1. Branch off `main`, named `UB<n>-<slug>` for search and evaluation work or
   `MV-R<n>-<slug>` for move-generation and rules work. One idea per branch,
   and branches are independent rather than stacked on each other.
2. STC test on OpenBench: 8+0.08s, SPRT bounds [0.00, 3.00].
3. If it passes, LTC test: 40+0.4s, SPRT bounds [0.00, 2.50].
4. If that passes too, open a pull request against `main`.
5. CI has to be green before the merge. Once merged, `main` is the new baseline
   that every later patch is measured against.

Rules fixes are the one exception to the Elo gate. When a change makes the
engine agree with the frozen Fairy-Stockfish reference, a match against a base
that does not implement the rule at all is not a meaningful measurement, so
those changes land on correctness evidence instead: perft counts and parity
runs against the reference.

Every merge to `main` publishes binaries to the rolling `rolling-main`
prerelease ([`.github/workflows/atomic_binaries.yml`](.github/workflows/atomic_binaries.yml)).
`scripts/build_atomic_native_release.sh` builds a versioned native release
archive. The reproducible, tag-driven release pipeline used for 1.0 was
archived; it lives in the `archive/pre-cleanup-20261111` tag.

See [CONTRIBUTING.md](CONTRIBUTING.md) for the practical details.

## Scope

The project deliberately supports one ruleset instead of Fairy-Stockfish's
large variant matrix:

- Atomic and Atomic960 positions;
- UCI and XBoard/CECP;
- the `pyffish` Python API;
- CommonJS and ES-module JavaScript APIs;
- lightweight Board WebAssembly and a Node UCI/NNUE WebAssembly engine;
- Legacy Atomic NNUE V1 and AtomicNNUEV2 networks;
- Atomic Syzygy WDL/DTZ tables (`.atbw` and `.atbz`).

UCCI, USI and non-Atomic variants are outside the release contract.

## Neural network

Atomic-Stockfish evaluates with NNUE only: it has no classical evaluation, so
it needs a network to search. `make build` and `make profile-build` download the
default network (see [Native build](#native-build)) and embed it in the
binary, which then needs no companion file. Set `EvalFile` to use another
compatible Legacy Atomic V1 or AtomicNNUEV2 network. `Use NNUE`
selects how the network result is used:

```text
setoption name Use NNUE value true
setoption name Use NNUE value pure
```

`true` is the default and normal playing mode, and the only mode used for
Elo/LOS matches. `pure` is reserved for isolated data-generation and
training-pipeline compatibility; it is not a playing-strength mode. The former
`false` value, which selected a material-only classical evaluation, was removed;
like any value outside the list, it is ignored and NNUE stays on.

An incompatible or missing network stops `go`, `bench` and `eval` with an
explicit error, and `go` answers `bestmove (none)`; `go perft` needs no
network. The default network (`EvalFileDefaultName`) is `atomic-ft5-0p8B.nnue`,
identified by SHA-256
`C8E590D82EFC3D29DFEBA37323EFD5FB094744E3561A07D58D9780C5C05DB807` and published
as the asset of the
[`net-ft5-0p8B`](https://github.com/Belzedar94/Atomic-Stockfish/releases/tag/net-ft5-0p8B)
release. The frozen development reference used by the validation gates remains
`atomic_run3b_e202_l05.nnue`, identified by SHA-256
`99DC67EABF26A64FAEECA3A88B4C38597A840B8D4A874B9F2CF658C6F92A04A6`.
Neither file is committed to this repository.

AtomicNNUEV2 is kept as a loadable format: the search, rules, XBoard, Syzygy
and bindings test suites use a deterministic material-only V2 network as their
evaluation oracle. The AtomicNNUEV3 format and its tooling were archived in the
`archive/pre-cleanup-20261111` tag; the idea worth keeping from it is noted in
[`docs/atomic/v3-relations-note.md`](docs/atomic/v3-relations-note.md).

## Native build

From `src`, inspect the available architectures with `make help`, then build an
appropriate target. On a machine with BMI2/PEXT support, the optimized target
used by the normative Hito 6 comparison is:

```sh
cd src
make -j build ARCH=x86-64-bmi2
```

The resulting executable is `atomic-stockfish` (or
`atomic-stockfish.exe` on Windows), with the default network embedded.

Like upstream Stockfish, the build first runs `make net`: it downloads the
default network into `src` with `curl` or `wget`, checks its SHA-256 and keeps
it there for later builds. A file already in `src` under the default name is
reused when its SHA-256 matches, and downloaded again when it does not; a
download that fails or does not match never replaces it. Without network
access, put the file in `src` yourself, or build with `embed=no` for a binary
without a network that loads one through `EvalFile` (run `make clean` when
switching between the two). `make -j EVALFILE=<network>`, the OpenBench
invocation, embeds the given network instead and downloads nothing.

`make -j profile-build ARCH=x86-64-bmi2` adds profile-guided optimization. Its
profiling run is a bench, which searches with the embedded network. With
`embed=no` and no network in `src`, the build still completes, but it ends with
a warning: nothing of the search was profiled, so that binary is much slower
than a plain `make build`.

Every build names itself in `id name`, the same way upstream Stockfish does. A
development build reports `Atomic-Stockfish dev-YYYYMMDD-<commit>`, with the
commit date and short SHA taken from git. Without git it falls back to the
compile date and `nogit`, or to the commit that OpenBench passes as
`GIT_SHA_FULL`. The source tree carries no release number: a release build
passes it, for example `make -j build ARCH=x86-64-bmi2 ATOMIC_VERSION=1.0.4`,
and reports `Atomic-Stockfish 1.0.4`. `scripts/build_atomic_native_release.sh`
passes its version this way; a release rebuilt from its source archive needs
the same `ATOMIC_VERSION`. `setup.py` and the WebAssembly
builds read `ATOMIC_VERSION` from the environment, and the bindings report the
same name as the engine.

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

Every pull request runs `.github/workflows/atomic.yml`. Its "Production
engine" job tests what OpenBench and the AtomicDB workers run: the default
build with the network `make net` fetches, its bench against the `Bench:` line
of the commit history (`tests/signature.sh`), the LegacyAtomicV1
incremental-accumulator gate with that network, and a fixed-node battery of
proven mates (`tests/atomic_mate_battery.py`). The other jobs cover the rules,
the data generator and its OpenBench bundle, sanitizers, Valgrind, the bindings
and the WebAssembly builds.

The Hito validation runners, the evidence logs they produced and the E00
match-mining harness were archived in the `archive/pre-cleanup-20261111` tag.

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
