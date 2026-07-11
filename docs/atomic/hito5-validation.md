# Hito 5 validation contract

Hito 5 is the release gate for the legacy Atomic NNUE backend. Its default
runner is `tests/run_hito5.py`; invoking it without `--mode` selects the
normative `release` contract. The native, Python, JavaScript and UCI/NNUE WASM
release matrix passed on 2026-07-11 with freshly rebuilt artifacts. The first
cross-repository E2E used a fully fingerprinted but dirty local tools/trainer
snapshot. The stricter clean, commit-pinned public-CI closeout described below
is now part of the release contract. Its synthetic hashes are measured and
locked; the public release rerun and clean local `strong-local` execution remain
pending before closeout.

`Use NNUE=true` is the playing mode and is mandatory for all Elo/LOS gates.
`Use NNUE=pure` exists for data generation: it exposes the unadjusted network
output used by the dataset/training pipeline. Release tests preserve `pure`
across native and WASM surfaces, but no strength result is measured in that
mode.

## Hard gates

| Gate | Release requirement |
| --- | --- |
| Hito 4 | Complete release runner; no WASM omission is accepted |
| NNUE modes | `false`, `true` and `pure`; invalid networks reject `go` without killing UCI |
| NNUE export | Export/import is byte-exact for the frozen Legacy Atomic V1 network |
| UCI/NNUE WASM | Supported Node launcher, external SHA-pinned network, true/pure and reproducible manifest |
| Cross-repository pipeline | Required in release: clean SHA-pinned tools, trainer and Atomic checkouts; generate twice with `pure`, validate/decode, train one Ranger step, serialize/reimport and load the resulting network in the native engine |
| Incremental accumulator | Exactly 1,000,000 deterministic make/undo operations plus fixed special-move fixtures |
| Atomic captures | At least one capture exercised and exact marker `capture-forced-refresh=0` |
| Full refresh | Incremental raw output, scaled true/pure values and accumulator lanes equal a fresh accumulator |
| Diagnostic differential | 10,000 deterministic positions; corpus identity, loader, protocol, modes, trace shape and candidate true/pure internal consistency are hard checks; small numeric deltas are informational, with a loose `0.10` pawn gross-regression bound |

The incremental executable must fail at the first Atomic capture that sets the
global `DirtyPiece.requiresRefresh` flag. The consolidated runner independently
parses its summary and fails unless every reported `capture-forced-refresh`
counter is zero and a positive `captures` counter is present. A king explosion
may still make `HalfKAv2Atomic::requires_refresh` true for the affected king
perspective; that local feature-transformer decision is not the forbidden
global capture fallback.

The frozen network is accepted only at SHA-256
`99DC67EABF26A64FAEECA3A88B4C38597A840B8D4A874B9F2CF658C6F92A04A6`.
The frozen Fairy executable is likewise pinned to
`1AE6D680F03128C8404F31A3F264F28B132B557ED3A91A6445EC563A7A33F623`.
In release mode the deterministic 10,000-position corpus must be exactly
`46C96F405BC15D468D94BC1E2186B577CE55128832E1108066581D35037FA2DE`;
smoke mode prints its smaller corpus hash but does not compare it with the
release constant.

### Reproducible multi-repository profiles

`tests/legacy_pipeline.lock.json` is the single machine-readable lock for the
two sibling repositories and both data-generation profiles. Repository entries
name `Belzedar94/variant-nnue-tools` and
`Belzedar94/variant-nnue-pytorch` and accept only full 40-character commits.
The release reader rejects the all-zero placeholder, an unresolved status,
the wrong HEAD, a dirty checkout, or an engine artifact outside its asserted
checkout. Atomic-Stockfish itself must also be clean; CI supplies its exact
checked-out HEAD because a repository cannot pin its own future merge commit.

The profiles deliberately serve different purposes:

| Profile | Source network | Use |
| --- | --- | --- |
| `strong-local` | External `atomic_run3b_e202_l05.nnue`, SHA-256 `99DC67EABF26A64FAEECA3A88B4C38597A840B8D4A874B9F2CF658C6F92A04A6` | Normative local release and real dataset-pipeline compatibility |
| `synthetic-ci` | Ephemeral zero-weight HalfKAv2 network created by the pinned trainer's model and Legacy V1 serializer | Public CI without downloading or redistributing the strong network |

Both profiles hard-pin record count, seed, source-network hash and generated
72-byte data hash. The synthetic constructor zeroes every trainer parameter
before serialization, so its bytes do not depend on PyTorch RNG or BLAS
behavior. It is not a playing network and is never used for Elo/LOS. The lock
pins the reviewed tools and trainer commits plus the measured synthetic source
SHA-256 `9CF054CA00B82AB53A34473DE52D1104AEDDAA19B2E7B24091B5E613AF485985`
and data SHA-256
`95565809C53E914A192D095B18C7BAB9A0C35AF9510347DC2C63BAA385D69988`.
The one-time
`--profile synthetic-ci --measure-synthetic-fixture` E2E invocation prints the
two observed hashes with an explicit `NON-RELEASE` marker and deliberately
returns a failing CI job after all postflights pass. Those values are now in the
lock and the profile is resolved; only the following run without the
measurement flag can pass the release gate.

## Evaluation differences are diagnostic

Small Fairy-versus-candidate evaluation differences are not a parity criterion
for Hito 5. The frozen Fairy trace is rounded, and execution can contain noise
that does not indicate a rules or strength regression. Maximum true and
pure-trace deltas are printed as telemetry. A deliberately loose finite bound
of `0.10` pawn units only rejects gross regressions such as a broken king plane,
orientation, bucket, or scaling; it is not used to reject a few centipawns of
ordinary disagreement. At or beyond Atomic's 100-ply draw boundary, the
candidate must report a neutral normal trace, while the paired C++ gate proves
that its internal `Value` is exactly zero. Fairy's historical post-boundary
final value is diagnostic only because it can cross through zero and reverse
sign; raw Legacy V1 parity remains checked on those positions.

The structural parts remain hard failures: network loading, required UCI
options, true/pure trace availability, identical candidate raw values between
true and pure, and the rule that pure output is derived from that raw value.
Unit tests, exact Atomic/Atomic960 perft and the separately governed Elo/LOS
matches are normative. A strength change is not accepted until all three exact
time controls report `LOS: 100.0%` with `Total > 100`; this runner does not
replace those matches.

## Release invocation

The three participating repositories must first be at the exact clean commits
recorded in `tests/legacy_pipeline.lock.json`. From the Atomic-Stockfish root,
create each artifact with its tracked recipe and write the manifests outside
all three checkouts. The manifest generator removes the exact previous
artifact (and the trainer's fixed build directory), builds from scratch,
records the compiler/toolchain and refuses to overwrite an existing manifest:

```powershell
New-Item -ItemType Directory -Force ../pipeline-manifests

python tests/legacy_pipeline_build_manifest.py `
  --recipe strong-local-tools-windows-v1 `
  --repository-root ../variant-nnue-tools `
  --output ../pipeline-manifests/tools-build.json

python tests/legacy_pipeline_build_manifest.py `
  --recipe strong-local-trainer-windows-v1 `
  --repository-root ../variant-nnue-pytorch `
  --output ../pipeline-manifests/trainer-build.json

python tests/legacy_pipeline_build_manifest.py `
  --recipe strong-local-atomic-windows-v1 `
  --repository-root . `
  --output ../pipeline-manifests/atomic-build.json
```

Run the global workload preflight before each of those build/test workloads.
On Windows the tracked recipes require
`C:\msys64\usr\bin\bash.exe` with MinGW64 for both engines and Visual Studio
2022 x64 plus CMake for the trainer. The output paths above are deliberately
outside every checkout: putting a manifest inside a repository would make that
repository dirty and invalidate the release gate.

```powershell
make -C src -j2 ARCH=x86-64-avx2 COMP=mingw atomic-syzygy-driver

python tests/run_hito5.py `
  --native src/atomic-stockfish.exe `
  --net ../atomic_run3b_e202_l05.nnue `
  --pyffish pyffish.pyd `
  --cjs tests/js/dist/cjs/ffish.js `
  --esm tests/js/dist/esm/ffish.mjs `
  --tables ../research/shakmaty/shakmaty-syzygy/tables/atomic `
  --wasm-wrapper build/wasm-engine/atomic-stockfish-nnue-node.mjs `
  --pipeline-tools-engine ../variant-nnue-tools/src/stockfish.exe `
  --pipeline-trainer-root ../variant-nnue-pytorch `
  --pipeline-tools-build-manifest ../pipeline-manifests/tools-build.json `
  --pipeline-trainer-build-manifest ../pipeline-manifests/trainer-build.json `
  --pipeline-atomic-build-manifest ../pipeline-manifests/atomic-build.json
```

The two checkout/artifact paths and all three clean-build manifests form one
all-or-none set of five `--pipeline-*` inputs and are mandatory in release
mode. The runner invokes the `strong-local` profile of
`tests/legacy_pipeline_e2e.py` under a whole-process timeout and reuses the
SHA-pinned `--net` as its `pure` source network. The E2E independently enforces
the lock, exact HEADs, clean worktrees, artifact containment and a strict
manifest-to-artifact match. It also records the exact CPython identity and the
installed torch, NumPy, PyTorch Lightning and torchmetrics versions and
origins. Every runtime/imported artifact used at preflight contributes to a
per-dependency and whole-environment SHA-256 manifest and is rehashed after the
training step; lazily imported additions are accepted only after ownership and
same-distribution origin validation. Smoke mode may omit the complete
five-input set;
it then emits the exact
`LEGACY PIPELINE E2E NOT REQUESTED (NON-RELEASE)` marker. Supplying only one
or any incomplete subset, omitting the set in release, a missing tools binary,
an unbuilt trainer checkout or a stale manifest is a hard configuration error.

Public CI uses the separate `synthetic-ci` profile. Its dedicated job checks
out both sibling repositories at the lock SHAs, clean-builds and runs the tools
and trainer internal suites, clean-builds Atomic-Stockfish, re-verifies all
three checkouts, and finally executes the same decode/train/serialize/load E2E.
Its Python 3.10 dependency closure is generated from
`tests/legacy_pipeline-ci-requirements.in`, contains hashes for every direct
and transitive distribution, is itself SHA-pinned by the workflow, and is
installed with `pip --require-hashes`. The pinned pip bootstrap is a separate
hash-locked requirements file whose own SHA-256 is checked first; no installer
or package byte is accepted by version alone. No CI secret or strong-network
download is required.

Release mode cannot reduce the fixed one-million operations or 10,000
positions. Its final success marker is:

```text
Hito 5 release validation passed: incremental-operations=1000000 structural-positions=10000 capture-forced-refresh=0
```

## Reduced smoke invocation

Smoke mode is configurable for development and is always labelled
`NON-RELEASE`. It still executes the complete Hito 4 release gate, including
the real UCI/NNUE WASM artifact; only the incremental count and structural
corpus are reduced.

```powershell
python tests/run_hito5.py `
  --mode smoke `
  --smoke-operations 4096 `
  --smoke-positions 64 `
  --native src/atomic-stockfish.exe `
  --net ../atomic_run3b_e202_l05.nnue `
  --pyffish pyffish.pyd `
  --cjs tests/js/dist/cjs/ffish.js `
  --esm tests/js/dist/esm/ffish.mjs `
  --tables ../research/shakmaty/shakmaty-syzygy/tables/atomic `
  --wasm-wrapper build/wasm-engine/atomic-stockfish-nnue-node.mjs
```

The operation count must be a positive multiple of eight so all four seeded
random sequences can make and undo a balanced path. A successful smoke run is
useful during implementation but is never evidence that Hito 5 passed.

### Verified smoke snapshot

The final 2026-07-10 smoke run passed the complete Hito 4 release gate and then
reported:

- `4,096` random operations: `2,048` makes and `2,048` undos.
- `120` Atomic captures and `capture-forced-refresh=0`.
- Perspective-local refresh counts: white `206`, black `542`.
- Diagnostic differential `64/64`, corpus SHA-256
  `09E8B0B4227DC71D5735E9522B8BF6E244D93C607298887E2F1EF81DE7D5A1E7`.
- Informational maximum deltas: true `0.000000`, pure trace `0.004808`.

The runner emitted `Hito 5 smoke validation passed (NON-RELEASE)` before the
normative release run.

## Verified engine/backend release snapshot

The complete engine/backend runner subsequently emitted the exact release
marker documented above. Its NNUE-specific evidence was:

- exactly `1,000,000` random actions: `500,000` makes and `500,000` undos;
- `18,761` Atomic captures and `capture-forced-refresh=0`;
- fixed coverage of direct king capture, the maximum nine-piece blast delta
  and Fairy-compatible feature anchor zero after the black king explodes;
- `241,087` comparisons against a fresh accumulator;
- perspective-local refreshes: white `62,282`, black `177,921`;
- diagnostic differential `10,000/10,000` with corpus SHA-256
  `46C96F405BC15D468D94BC1E2186B577CE55128832E1108066581D35037FA2DE`;
- rule-50 differential units `3/3`; the fixed corpus exercised `866`
  positions at or beyond 100 reversible plies, with the candidate's displayed
  normal evaluation neutral in every case and exact internal zero proved by
  the C++ gate;
- informational maximum deltas: comparable final `0.000000`, historical
  Fairy post-boundary final `0.740000`, raw Fairy trace `0.005000`;
- directed rejection of mutated version, architecture hash, transformer hash,
  layer-stack hash, trailing bytes, generic garbage and truncation, with
  valid-network recovery after every failure;
- byte-exact network export/import and all `false`, `true` and `pure` modes.

The frozen reader contract observed version `0x7AF32F20`, architecture hash
`0x3C103E72`, transformer hash `0x5F2348B8`, eight stack hashes
`0x633376CA`, and exact file size `47,721,376` bytes.

### Rebuilt artifact snapshot

The artifacts in this table represent the Hito 5 source state at commit
`4246f7b3`. The direct-capture fixture in that commit only changes the
incremental gate executable; the engine, bindings and WASM source inputs are
identical to its merge parent.

| Artifact | Bytes | SHA-256 |
| --- | ---: | --- |
| Native AVX2 engine | 4,269,041 | `03DD45FBE202C6629C099CD9E5D619992BE0BF400EEACB4553D87DDE47C49A99` |
| Incremental gate | 3,749,195 | `1526D6B48DD23E0DF49FEBDEE9878DC66889E89905538E528BB5C34A1F7F121A` |
| `pyffish.pyd` | 147,968 | `CE20CFA0A97B23097789577EA959522AC9162916B56C0074B2C37297CEAE49B4` |
| CommonJS `ffish.js` | 56,151 | `B5C3D624071A25F297C1993CEF63A6602E5DA0BB4AD38BA5A7CCCF55374178C7` |
| ES module `ffish.mjs` | 55,929 | `AF17E8BA6FC9BED8C56088446F28D87498A80842FD38D1F3125A83F821F9E122` |
| Shared Board WASM | 268,671 | `C653CD013E29031868454499296C453F2179BBDE9AD0A8A684FD7E4BF836BBC1` |
| Full UCI/NNUE WASM | 547,655 | `88B79B0906260B3DC910322E2AD5639D5AC513D7DF84024814DE70F0E24DE247` |
| Full WASM manifest | 1,930 | `488C44F6A23351EF364681E7BFB1D66DAC7A55CFDE68131016525181AF0B5074` |

### Platform evidence

- Windows MinGW AVX2 release: full Hito 5 release runner passed.
- Linux GCC 15 portable x86-64 release and debug/assert: units, API,
  incremental smoke, perft, NNUE reader/export, signature and protocols passed.
- Linux Clang 20 portable x86-64: the same native matrix passed.
- GCC ASan+UBSan: UCI, XBoard, NNUE true/pure, units, API and incremental
  workloads passed without diagnostics.
- Native GitHub TSan passed the complete XBoard workload after serializing
  `Ponder` option updates at joined search boundaries.
- Valgrind Memcheck passed the runtime workload. Threaded Memcheck uses fair
  scheduling; native TSan remains the race detector in Atomic CI because TSan
  cannot disable ASLR inside the local Docker VM.

## Historical Hito 5 performance snapshot

The final Hito 5 paired run used the fixed 13-position corpus (SHA-256
`2738065A8A70D61DA46FA3C75F95D645E50E601B43792DF0E7B3CC97B1D891A1`), one
thread, 64 MiB hash, CPU 0, one warm-up and five measured repetitions at
100,000 nodes per position. Atomic-Stockfish's median was `713,466 NPS`; the
frozen Fairy baseline measured `1,146,133 NPS`, for a ratio of `0.6225`.

The strict project requirement is a ratio greater than `1.0`, so the
performance gate remained **failed at the Hito 5 closeout** despite the
incremental-accumulator gain.
Specialization is not credited in advance; the final engine must demonstrate
that it is faster on the shared corpus. Absolute NPS varies across runs, so the
paired ratio under identical conditions is the governing measurement.

This is the historical Hito 5 closeout measurement. Hito 6 removed the
FullThreats overhead, vectorized the legacy accumulator and passed the same
paired gate; see `hito6-validation.md` for the superseding result.
