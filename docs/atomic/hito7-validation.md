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
compiler target and immutable role-specific `*-pipeline` artifact SHA rather
than treating it as an unauthenticated side product of the playing binary.
The normal and generator copies survive the later restoration build, avoiding
any assumption that independent MinGW LTO links are byte-reproducible.

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
- Pipeline/lock/build-manifest/E2E Python units: `128/128`.
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

## H7.2-B/C - Thin tools wrapper and Atomic-owned pipeline

The `variant-nnue-tools` `atomic` line is pinned at merge
`521f841098eeee19c4234417181b0b441feb3499`. It vendors Atomic-Stockfish as a
git submodule pinned to merged `main` commit
`d1d04c34834232c34ce76ed226923e8191f711da`, authenticates that gitlink and
schema before every build, and exposes the dedicated `atomic-data-tools`
artifact. Its historical PV self-play implementation is removed. The remaining
Legacy V1 validator, decoder, converter, statistics and non-PV utilities are a
temporary Atomic-only compatibility backend/front-end. The authoritative
playing engine and PV writer, plus the schema pin used by the wrapper, come
from the authenticated Atomic submodule.

Atomic-Stockfish lock schema v4 completes the ownership transfer. Each profile
contains exactly one dataset hash, generated by the isolated Atomic data
generator: `d95f5180...c2071639` for `strong-local` and
`60308342...e0c1484` for `synthetic-ci`. The obsolete tools-only dataset hashes
and the second `atomic_data_sha256` key are rejected.

The clean-build recipes are `strong-local-tools-windows-v2` and
`synthetic-ci-tools-linux-v2`. Both run root `make verify-engine-pin` followed
by root `make data-tools`; they authenticate `src/atomic-data-tools.exe` or
`src/atomic-data-tools`, never the former Fairy `src/stockfish` artifact. CI
checks out tools with recursive submodules, unshallows the nested engine when
necessary, and fetches its authenticated `origin/main` before invoking those
recipes.

The cross-repository E2E is now unidirectional:

1. Atomic-Stockfish generates the same locked dataset twice with
   `Use NNUE=pure` and requires byte identity.
2. `atomic-data-tools` validates, decodes and performs the strict
   bin/plain/bin semantic round trip.
3. The trainer native loader decodes that same Atomic dataset and performs one
   real Ranger update.
4. The resulting Legacy V1 network is serialized, reimported byte-exactly and
   loaded by the current Atomic playing engine for evaluation and search.

No search, evaluation or move-generation source changes in H7.2-B/C, so this
pipeline consolidation does not trigger the three-TC strength gate.

## H7.3-A - Frozen Atomic BIN V2 codec contract

The additive V2 contract is frozen in `schemas/atomic-bin-v2.json` (SHA-256
`0352b036f2a140c609e3eb9c9d635dc553e8d77253d8faa92437390f5cf93cb6`).
It defines a 96-byte `ATBINV2\0` little-endian header, 48-byte canonical
position and 64-byte record. All serialization is byte-wise: no C++ struct,
piece enum or native 16-bit move is copied to wire.

The position stores A1-to-H8 piece nibbles, explicit side-to-move mapping,
castling-right bits and all four rook origins, canonical en-passant state,
rule50 and fullmove clocks. The record adds an int32 score (excluding the
INT32_MIN sentinel), independent 32-bit move, uint32 ply, side-to-move result
and an Atomic960 flag. The move contract explicitly forbids wire zero and equal
origin/destination squares, and requires a nonzero promotion code if and only
if the move type is promotion. Exactly one king per color is required by this
dataset format even though the playing engine can represent a terminal
exploded king.

`atomic-bin-v2-tests` covers exact header/record goldens, normal, en-passant,
all promotions, orthodox castling, the Atomic960 `c1b1` wire, Atomic960 with
no rights, clock/count/file-size boundaries and corrupt reserved, enum, king,
rook-origin, EP and move combinations. Adapter decoding reconstructs a strict
canonical FEN and requires the move in `MoveList<LEGAL>`. Python separately
validates the exact UTF-8/LF schema bytes and rejects structural drift.

The low-level `*_record_structural` helpers are deliberately internal wire
building blocks: they validate representation and local field relationships,
but do not claim that a plausible move is legal in Atomic. The public
`encode_atomic_bin_v2` and `decode_atomic_bin_v2` adapter is the semantic
boundary used by sinks and readers; it parses the canonical position and
requires the move in `MoveList<LEGAL>`. A dedicated test feeds the same
structurally valid but illegal move through both layers and locks this split.

The V2 objects are linked only into the isolated data-generator/test build;
the playing engine source/object set is unchanged. The plural
`atomic_data_schemas` capability is additive and reports V2 as codec-only
(`read:false,write:false`) until the H7.3-B sink. The historical singular
Legacy V1 handshake and all 72-byte output remain byte-exact. Consequently
this contract-only block does not trigger LOS matches.

## H7.3-B - Atomic BIN V2 sink and manifest

The isolated generator now accepts `data_format atomic-bin-v2` while retaining
`data_format bin` as its byte-exact default. Each V2 shard is created with
exclusive, non-appending semantics, starts with a provisional zero-count
header, and is published only after the final count, exact file-size formula,
same-descriptor SHA-256 and durable close succeed. The sink retains a file
identity token to detect a replacement before rollback. Windows binds deletion
to an open handle; on POSIX the caller must serialize writers in the output
directory because the platform has no portable unlink-if-inode primitive.

Every successful run requires an adjacent sidecar whose frozen schema is
`schemas/atomic-bin-v2-manifest.json` (SHA-256
`83d63922df3ac4a0c81a21ec9d9fd9e180efe50f26efee62fe01710e09da5b42`).
The canonical one-line UTF-8 JSON records the full engine commit, version,
authenticated network/book basenames and hashes, resolved seed, Atomic960 and
UCI state, all effective generator settings, totals, and every shard's exact
record count, byte count and checksum. It contains neither timestamps nor
absolute paths and is never appended or overwritten. Linux writes it to an
anonymous `O_TMPFILE` inode, synchronizes that inode and publishes it exactly
once with a no-replace hard link. The generator preflights `O_TMPFILE` and the
descriptor-link route in a uniquely named private directory before creating any
shard, so an incompatible filesystem fails immediately. Cleanup is checked; a
process killed inside the probe can leave its clearly prefixed private
directory, but no error path ever unlinks the final pathname. Windows retains
the exact exclusive handle through write, synchronization and identity-bound
cleanup. Consequently a concurrent replacement of the final sidecar is never a
rollback target.

V2 rejects a non-empty `SyzygyPath` before creating any shard. This prevents
tablebase-dependent moves or scores from escaping the authenticated manifest;
tablebase files are deliberately not hashed as generator inputs.

The build injects the full engine SHA only when `git status --porcelain` is
empty. Dirty/source-export builds serialize `engine.commit=unknown`, preventing
false attribution to a clean `HEAD`. File authentication rechecks size on the
same descriptor after SHA-256 and fails closed if it changed during hashing.

V2 accepts Atomic960 and sets its record flag while preserving castling-rook
origins. Legacy V1 continues to reject Atomic960 and its singular capability,
fixtures and 72-byte wire remain unchanged. The plural handshake now advertises
V2 as `read:false,write:true`; H7.3-C is the separate reader rollout.

The new gates cover SHA-256 boundaries, exclusive sink lifecycle, provisional
and final headers, single-writer identity-checked abort, canonical manifest
rendering and publication, native V2 generation, Atomic960, input/output
checksums and overwrite refusal on GCC, Clang, MinGW, debug, sanitizers and
Valgrind. Memcheck also runs a real one-record V2 generator transaction, not
only the isolated codecs. These objects remain outside the playing binary, so
the Atomic playing signature is the relevant non-regression gate and LOS is not
triggered by this block.

## H7.3-C1 - Authoritative manifest and dataset reader

Atomic-Stockfish now owns the strict C++ reader used by the later tools and
trainer integrations. `parse_atomic_bin_v2_manifest` accepts only the exact
canonical JSON bytes emitted by the frozen renderer, and
`AtomicBinV2DatasetReader::open` accepts only an
`.atbin.manifest.json` entrypoint. Legacy Atomic V1 retains its independent
72-byte loader; there is no format guessing and no raw V2 shard entrypoint.

Opening a dataset resolves portable shard basenames relative to the sidecar,
rejects duplicate pathnames and captures the manifest without touching shard
contents. Streaming lazily rejects duplicate file identities and non-regular,
symlink or reparse-point paths, checks exact size, same-descriptor SHA-256,
frozen header and record count, audits each record through structural decoding
and Atomic/Atomic960 legal move generation, requires a byte-exact re-encode, and
reconciles record/draw totals at EOF. It retains at most one cryptographically
authenticated private snapshot; a source descriptor exists only during
staging, so the producer's 100,000-shard upper bound does not become an
OS-handle limit. Diagnostics carry shard/local/global indexes.

The streaming pass stages one shard at a time in an auto-deleting private file
under the system temporary directory. The complete staged bytes must match the
manifest SHA-256 before the first record is exposed; all records for that shard
then come from the authenticated snapshot. This costs temporary disk equal to
the largest current shard but removes the remaining same-inode mutation race.
Source paths are captured absolutely before manifest parsing, preventing a CWD
change from redirecting a later streaming open.

The public reader invokes the process-wide thread-safe Atomic core initializer
before its first semantic record, so C2 tools and trainer integrations have no
hidden Bitboards/Attacks/Position startup precondition. POSIX sidecars and
shards are opened nonblocking until their regular-file status is established;
FIFO fixtures prove malformed datasets cannot hang the caller.

Windows creates each snapshot with a cryptographically random `CREATE_NEW`
basename, no sharing and `FILE_FLAG_DELETE_ON_CLOSE`. POSIX creates it mode
0600 with `mkstemp` and unlinks it before staging. Both paths are non-inheritable
and keep only the current shard descriptor alive.

The stationary-king Atomic960 regression is explicit: `c1b1` remains the rook-
origin move wire in a legal position, while
`7k/8/8/8/8/8/2PP4/1RK4q w Q - 0 1` rejects the same castle because the king is
in check. Parser and reader targets cover canonical JSON drift, unsafe paths,
missing/directory/link/hardlink/replaced shards, header/SHA/size/count failures,
reserved bytes, illegal moves, aggregate statistics, rewind and streaming.
This C1 library is not yet advertised as a generator read capability; the C2
CLI and sibling tools/trainer integrations are the remaining H7.3-C work.

### 2026-07-13 local pre-PR snapshot

The matched BMI2 MinGW release build completed the generator and playing-engine
links and passed all six native data unit executables: Legacy V1, V2 codec, V2
sink, V2 manifest, strict manifest reader and authenticated dataset reader. The
two focused reader targets were also rebuilt and passed independently after the
Windows native snapshot implementation replaced the unavailable C runtime
`tmpfile` path. Formatting and `git diff --check` are clean.

This is deliberately not the milestone matrix. The Python generator E2E, Linux
GCC/Clang, debug, ASan, UBSan and Valgrind lanes remain for CI after the commit
is pushed. Playing-engine matches, Syzygy A/B and any LOS gate remain deferred
to the shared OpenBench scheduler; C1 changes only data-library objects and do
not independently trigger a local Elo run.

## H7.3-C2 - Production data-tools validation CLI

Atomic-Stockfish now exposes the C1 dataset reader through a standalone
`atomic-stockfish-data-tools` artifact and `make data-tools` target. Its frozen
version-1 contract has only `capabilities` and
`validate --format atomic-bin-v2 --manifest <sidecar>`. Both the format and the
manifest entrypoint are explicit: positional input, format guessing and a raw
`.atbin` shard are rejected. Full details and the exact canonical JSON are in
`docs/atomic/data-tools.md`.

Validation consumes `AtomicBinV2DatasetReader::next` until EOF rather than
merely accepting the sidecar. The success object reports shard/record counts,
side-to-move result totals and Atomic960 records. Every potentially `uint64`
record counter is a decimal string so JavaScript and other IEEE-754 JSON
consumers retain exact values above `2^53`; the bounded shard count remains a
number. Contract failures exit 2;
authoritative parse, authentication and semantic failures exit 3. Failures
write one canonical JSON line to stderr and preserve the C1 diagnostic text,
including `shard/local/global` indexes. Rewind remains available and tested at
the C++ library layer but is intentionally absent from this one-pass CLI.

The executable uses an explicit reader-only object list. It excludes playing
`main`, search, threads and TT, and excludes the V2 sink and generator writer.
Conversely the normal playing `SRCS` do not include the CLI or reader. The
generator's capability therefore remains honestly
`atomic-bin-v2 read:false,write:true`; only data-tools advertises
`read:true,write:false`. Legacy V1 behavior and its byte-exact handshakes are
unchanged.

The black-box contract gate creates two authenticated shards, validates every
record, exercises checksum corruption and a semantically corrupt record in the
second shard with a recomputed manifest SHA, and locks manifest-only behavior,
canonical raw-LF output, Unicode paths/arguments, error codes and argument
handling. A C++ unit calls the same production success-response renderer with
`2^53 + 1` and `UINT64_MAX`, proving the five record counters remain strings
while the bounded shard count remains numeric. GCC/Clang release, debug/assert,
MinGW and memory-sanitized CI lanes build and run both the endpoint and this
unit.
Because this block changes only separately linked data objects, its playing
non-regression gate is the unchanged Atomic NNUE signature `338376`; it does
not trigger the three-TC strength gate.

The expected next wrapper change is also frozen without modifying the sibling
repository here: `variant-nnue-tools` must authenticate its Atomic-Stockfish
gitlink, build this target, verify the exact capabilities response, and
delegate V2 validation with the explicit format and sidecar arguments while
preserving the child JSON and exit class. It must never infer or rewrite a raw
shard path. The trainer remains a separate manifest-only integration.

### 2026-07-13 local pre-review snapshot

The focused MinGW x86-64 release build passed the production data-tools
black-box contract, the canonical manifest reader, the authenticated streaming
reader and the frozen Legacy V1 suite. The source/CI isolation tests passed
`4/4`; raw-byte output remained LF-only for capabilities, success and errors;
non-ASCII commands and a valid manifest directory round-tripped as UTF-8; and
`objclean` removed the executable and its LTO residues. The workflow parsed as
YAML, formatting/diff checks were clean, and a fresh playing-engine link with
the external network retained signature `338376`. No local strength match was
run because this separately linked data surface cannot affect play. Linux
GCC/Clang, debug/assert and sanitizer runs remain assigned to CI after review.

## Remaining Hito 7 work

H7.1, H7.2 and H7.3-A/B/C1/C2 are compatibility boundaries, not completion of
Hito 7. The Atomic-owned V2 validation endpoint is now defined; the pinned
`variant-nnue-tools` `atomic` branch must adopt that delegation contract, and
the trainer still needs its manifest-only V2 loader and end-to-end validation.
Legacy V1 remains a supported fallback throughout the 1.x line.

This project remains isolated in the sibling repositories: every tools or
trainer implementation branch starts from its dedicated `atomic` branch and
every corresponding pull request targets `atomic`, never the upstream default
branch (`main` or `master`). The pipeline lock records the exact commits from
those Atomic-only lines.
