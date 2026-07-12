# Hito 7 validation

## H7.1 — Legacy schema handshake

This first Hito 7 block freezes the data contract that already exists before
introducing `atomic-bin-v2`. It does not change generated Legacy Atomic V1
bytes, search, evaluation, move generation or NNUE arithmetic.

The normative source is `schemas/atomic-schema.json`, stored as UTF-8 with LF
line endings and SHA-256:

```text
acca0f551f1c012c31a6c727dedccaebb7b5ebbc46810edb87e31bb208d5abe1
```

The schema records the headerless 72-byte layout, 16-bit move wire, score and
result perspectives, the twelve zero-valued hand-count fields that remain in
the historical packed position, split clock fields and the little-endian host
requirement. It also records current limitations instead of promising states
that the common tools/trainer contract cannot preserve:

- PackedSfen V1 is not a unique byte-canonical encoding after decode/repack.
- A missing/exploded king is not a valid Legacy V1 record.
- `MOVE_NONE` is forbidden.
- Atomic960 rook origins are not representable.

`variant-nnue-tools` exposes `atomic_data_schema`; standard `DATA_SIZE=512`
builds advertise read/write support, while incompatible `largedata=yes` builds
return an empty `formats` object. `variant-nnue-pytorch` exports
`get_atomic_training_data_schema_json()` and the Python
`atomic_training_data_schema()` wrapper, advertising read-only support. The
release E2E fails before generation unless both report the exact schema hash,
format ID and record size.

Pinned implementation commits used by the local release run are:

| Repository | Commit |
| --- | --- |
| Atomic-Stockfish schema/code/test commit | `518175e20f127bf47b57ba1faad580b975e45929` |
| Atomic-Stockfish clean validation snapshot | `b4e4dee4a412754ff3ce6728426a0c9d35308239` |
| variant-nnue-tools | `17bfb6eb1bd02f86a63cfbc10aaf1bdf6f0a74c6` |
| variant-nnue-pytorch | `350a28f2cee225c546333aded75b9db64caa526d` |

The follow-up commits only update the reviewed dependency pin and validation
documentation; they do not alter the compiled source tree.

## Local release evidence

All workloads were preceded by the global workload-tree preflight. The final
release gate used clean, commit-bound Windows builds:

| Artifact | Target | SHA-256 |
| --- | --- | --- |
| Atomic-Stockfish | MinGW BMI2 | `52849402...22B13E86` |
| data tools | MinGW x86-64 SSE2 | `C51F7ED0...116F8C29` |
| trainer loader | MSVC x64 Release | `8E322B1A...968923A0` |

The following gates passed:

- Schema/lock/build-manifest/E2E focused suite: `97/97`.
- Tools C++ units in both `DATA_SIZE=512` and `DATA_SIZE=1024`, plus the full
  standard-layout integration, including frozen hashes
  `C8F5C7FE...B229B2AA` and `1E7A3166...CC12E9CE`.
- Trainer native CTest: `1/1`; Python CPU and required CUDA gates: `58/58`
  each on the RTX 3080.
- Pipeline lock/build/profile suite: `87/87`.
- Strong-local generator → decode → train → serialize → re-import → engine
  E2E: PASS with eight records, the frozen playing-network hash and unchanged
  data hash `7de72b13...7a261a2d`. The exact clean commits were tools
  `17bfb6eb1bd02f86a63cfbc10aaf1bdf6f0a74c6`, trainer
  `350a28f2cee225c546333aded75b9db64caa526d` and Atomic
  `b4e4dee4a412754ff3ce6728426a0c9d35308239`.
- Hito 4 release surface: C++ `63/63`, API `34/34`, historical `test.py`
  `22/22`, Python `60/60`, CommonJS, ES module, Board WASM, native parity,
  eight exact perfts, focused rules `19/19`, UCI, XBoard, Syzygy, NNUE modes,
  reprosearch and interactive UCI/NNUE WASM.
- Legacy NNUE incremental/full-refresh equivalence: 1,000,000 operations with
  `capture-forced-refresh=0`.
- Structural/NNUE differential: `10,000/10,000` positions.

The normative serialized BMI2 speed gate also passed against the frozen Fairy
BMI2 baseline:

| Binary | Median NPS | Bytes |
| --- | ---: | ---: |
| Atomic-Stockfish H7.1 | 1,316,647 | 4,264,325 |
| Fairy-Stockfish baseline | 1,158,380 | 4,281,871 |

The ratio was `1.1366` (`+13.66%`). Since H7.1 does not modify engine source,
search, evaluation or generated data bytes, it does not trigger the three-TC
strength-change gate. Those matches remain mandatory for every future change
that can affect play.

## H7.2-A — Isolated in-engine Legacy V1 generator

Atomic-Stockfish now owns the PV self-play path behind a separate
`data-generator` build. The resulting `atomic-stockfish-data-generator`
artifact compiles dedicated `uci.cpp` and `search.cpp` objects with
`ATOMIC_DATA_GENERATOR`; the normal UCI/XBoard binary neither links the codec
nor recognizes the generator commands. The public playing path compiles to the
same search behavior and retains the exact NNUE signature `338376`.

The generator writes only the frozen 72-byte Legacy Atomic V1 format in this
block. It requires a valid compatible network and `Use NNUE=pure`, rejects
Atomic960 and invalid configuration before creating output, uses exclusive
creation, removes only files owned by a failed run, and clears search/TT state
between commands. A separate clean-build manifest authenticates its commit,
compiler target and artifact SHA rather than treating it as an unauthenticated
side product of the playing binary.

The deterministic synthetic network and generated fixtures are frozen as:

| Fixture | SHA-256 |
| --- | --- |
| Zero-weight Legacy Atomic V1 network | `9CF054CA...F485985` |
| Basic same/fresh-process dataset | `762555D8...C1F339A` |
| True Apery insert/reply dataset | `CF2B0F7B...76D91F0` |
| Random-MultiPV same-process replay dataset | `2EDCD682...F809A3D` |
| `INT_MAX` MultiPV-diff dataset | `2EDCD682...F809A3D` |
| `random_move_min_ply=-1` RNG dataset | `2EDCD682...F809A3D` |
| Two-opening/two-game shuffled-book dataset | `B77E197E...9C750D1` |

All seven valid fixtures and normal-binary isolation run in the GCC, Clang,
MinGW, debug/assert, ASan/UBSan and TSan smoke matrices. The full gate also
exercises the historical PRNG order, true Apery vector insertion, terminal
search/RNG ordering across games, multi-FEN book shuffle/round-robin,
direct-mapped 64M-key deduplication table, Threads=2, invalid
network/mode/Atomic960/options, output collision behavior, tools validation and
trainer native decoding. With the frozen playing network, the basic two-record
dataset SHA-256 is `7E89411B...3B27CC0`.

Local validation for this block passed:

- GCC 15, Clang 20 and MinGW BMI2 release builds; MinGW debug/assert build.
- The complete 504-byte Fairy codec fixture and sink cleanup/destructor tests.
- ASan+UBSan and TSan functional self-play, including Threads=2.
- Pipeline/lock/build-manifest/E2E Python units: `126/126`.
- Full cross-repository validation of all seven generated datasets in tools and
  the trainer native loader.
- Mutation checks rejected Apery overwrite (`FB6B314B...B87C2E8`), omitted
  `-1` RNG consumption (`0FD4B1AA...D074F92`), early terminal resolution
  (`A8F93701...FC4ABC`) and signed `INT_MAX` overflow (`762555D8...C1F339A`).
- Hito 4 release and debug surfaces, including C++ `63/63`, API `34/34`,
  `test.py` `22/22`, Python `60/60`, CommonJS/ESM/WASM, XBoard, Syzygy, all
  eight exact perfts, NNUE modes, reprosearch and signature.
- Legacy NNUE incremental/full-refresh equivalence for 1,000,000 operations
  and the `10,000/10,000` Fairy differential.
- Normative BMI2 speed gate: candidate median `1,132,156` NPS versus Fairy
  `914,168` NPS, ratio `1.2385` (`+23.85%`), with the candidate 17,546 bytes
  smaller in that run.

The three LOS controls are not triggered by H7.2-A: every generator/search
addition is compile-time isolated from the playing target, and its exact
playing signature is unchanged. They remain mandatory as soon as a shared
search, evaluation or move-generation change can affect games.

## Remaining Hito 7 work

H7.1 and H7.2-A are compatibility boundaries, not completion of Hito 7. The
next blocks must make tools a thin wrapper pinned to the Atomic engine, then
implement the versioned `atomic-bin-v2` header/manifest and 32-bit move wire,
add Atomic960-capable canonical positions, and validate every V2 record in
tools and trainer. Legacy V1 remains a supported fallback throughout the 1.x
line.

This project remains isolated in the sibling repositories: every tools or
trainer implementation branch starts from its dedicated `atomic` branch and
every corresponding pull request targets `atomic`, never the upstream default
branch (`main` or `master`). The pipeline lock records the exact commits from
those Atomic-only lines.
