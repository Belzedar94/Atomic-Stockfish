# Atomic training-data generator

Atomic-Stockfish provides a separate `data-generator` build for NNUE dataset
production. The normal UCI/XBoard playing binary does not link the generator
codec, coordinator or commands.

## Build and capability handshake

From `src`:

```sh
make -j data-generator ARCH=x86-64-bmi2
./atomic-stockfish-data-generator
```

The output executable is `atomic-stockfish-data-generator` (with `.exe` on
Windows). Its `atomic_data_schema` command reports the exact write capability:

```json
{"schema_sha256":"acca0f551f1c012c31a6c727dedccaebb7b5ebbc46810edb87e31bb208d5abe1","formats":{"legacy-atomic-v1":{"read":false,"write":true,"record_size":72}}}
```

The schema is frozen in `schemas/atomic-schema.json`. The generator is
write-only. The `variant-nnue-tools` `atomic` line is now a thin reader/writer
wrapper pinned to Atomic-Stockfish: its `atomic-data-tools` artifact validates,
decodes, converts and reports statistics, while PV self-play exists only in
this Atomic-Stockfish generator.

Clients that support version negotiation may issue `atomic_data_schemas`.
Its `capability_version: 2` envelope preserves the same Legacy V1 write
capability and reports the frozen `atomic-bin-v2` schema hash
`0352b036...f93cb6`. From H7.3-B the V2 entry reports
`read:false,write:true`; readers are exposed by a separate H7.3-C data-tools
artifact. The historical
singular response above remains byte-exact for pinned Legacy clients.

H7.3-C1 provides the authoritative C++ reader in Atomic-Stockfish and H7.3-C2
exposes it as `atomic-stockfish-data-tools`. A V2 dataset is opened only
through its `.atbin.manifest.json` sidecar; passing a raw `.atbin` shard is an
error. The generator capability remains `read:false,write:true` because it
describes this writer binary, while the separate validator advertises
`read:true,write:false`. See [data-tools.md](data-tools.md) for its frozen CLI
and delegation contract.

## Generate Legacy Atomic V1 data

Load a compatible Atomic network and select `pure` before starting generation:

```text
setoption name EvalFile value /absolute/path/to/atomic.nnue
setoption name Use NNUE value pure
setoption name Threads value 1
isready
generate_training_data depth 3 count 1000000 output_file_name training_data data_format bin seed run-001
```

`pure` is reserved for dataset generation. Normal play and Elo/LOS matches use
`Use NNUE=true`.

The supported PV self-play options retain the historical names: `depth`,
`min_depth`, `max_depth`, `nodes`, `count`, `eval_limit`, `eval_diff_limit`,
the random-move and random-MultiPV controls, `write_min_ply`, `write_max_ply`,
`save_every`, `book`, `random_file_name`, `keep_draws`, both draw-adjudication
flags, the three position filters, `data_format`, `seed`, and
`set_recommended_uci_options`.

Legacy V1 keeps its deliberately narrow compatibility contract:

- Atomic960 is rejected because V1 has no castling-rook origins;
- both kings must be present in every saved record;
- depth starts at one; `depth=0` is rejected rather than silently changing to
  quiescence behavior;
- non-PV generation, converters, puzzle generation and statistics remain in
  the thin tools wrapper during the Legacy V1 consolidation.

## Generate Atomic BIN V2 data

Select the additive format explicitly:

```text
setoption name EvalFile value /absolute/path/to/atomic.nnue
setoption name Use NNUE value pure
setoption name UCI_Chess960 value false
setoption name Threads value 1
setoption name Hash value 512
isready
generate_training_data depth 3 count 1000000 output_file_name training_data data_format atomic-bin-v2 seed run-001
```

The first shard is `training_data.atbin`; sharding retains the historical
`save_every` naming. A mandatory adjacent
`training_data.atbin.manifest.json` records the full engine commit, network and
book SHA-256, resolved seed, UCI/generator settings, record statistics and the
size/SHA-256 of every shard. The manifest schema is frozen at
`83d63922...da5b42`. It is canonical minified UTF-8 with one LF, portable
basenames only, no timestamp and no absolute path.

Set `UCI_Chess960=true` before the command to generate Atomic960 V2 records.
Every record then carries the Atomic960 flag and preserves castling-rook
origins. This option remains invalid with `data_format bin`.

## Safety and reproducibility

Output shards and the V2 sidecar use exclusive creation. Existing paths are
never appended, overwritten or deleted when the output directory has a single
writer for the duration of the command. Inputs are authenticated around load;
V2 output is published only after its final header, exact size and checksum are
verified. On Linux the complete sidecar is synchronized in an anonymous inode
before a no-replace publication; support for that primitive and its descriptor
link is exercised inside a uniquely named private probe directory before the
first shard is created, so incompatible filesystems fail immediately. Orderly
probe cleanup is checked; an abrupt process termination can leave only a
clearly prefixed probe directory, never the final manifest path. The final
manifest path is never removed. A failed V2 run removes only identity-matched
V2 shards created by that run; empty and partial datasets are invalid. Legacy
V1 retains its historical pathname cleanup and therefore shares the same
single-writer requirement. Windows can bind V2 deletion to an open handle.
POSIX has no portable unlink-if-inode primitive, so concurrent writers in the
output directory are explicitly outside the supported contract and must be
serialized by the caller.

V2 generation also requires an empty `SyzygyPath`. Tablebase probing can change
the stored move and score, but authenticating an entire tablebase set would make
the manifest prohibitively expensive. A dataset therefore fails before creating
any output if Syzygy is active.

The manifest reports the exact 40-character engine commit only for a clean Git
worktree. Builds from a dirty tree or a source export report `engine.commit` as
`unknown`; they can be used for development, but not misidentified as a clean
release artifact. Network, book and shard hashing also rechecks descriptor size
after the hash and rejects an input that changes while being authenticated.

The resolved decimal seed is printed as `PRNG::initial_seed`. Replaying it is
byte-deterministic when the binary, network, command, book, UCI options and
`Threads=1` are identical. Every run clears worker state and the transposition
table first. With multiple threads, record ordering is intentionally not
defined.

An EPD book is shuffled once with worker zero's historical PRNG stream and then
served round-robin across workers. With no book, selecting the single Atomic
start position consumes no random number.

## Tests

With the external development network available at
`../../atomic_run3b_e202_l05.nnue`, run from `src`:

```sh
make -j data-generator-tests ARCH=x86-64-bmi2
```

The gate covers the complete 504-byte Fairy wire fixture, deterministic
same/fresh-process generation, exact Threads=2 execution, real Apery reply
insertion, random-MultiPV replay and extreme diff arithmetic,
`random_move_min_ply=-1`, shuffled multi-FEN books across game boundaries,
invalid network/mode/Atomic960 and configuration failures, exclusive output
behavior, and proof that the normal playing binary does not expose generator
commands. All valid fixtures and normal-binary isolation run in sanitizer and
platform smoke jobs. The full `tests/data_generator.py` invocation additionally
passes every generated dataset through the tools validator and trainer native
loader.

The same target also runs the isolated Atomic BIN V2 contract and publication
gates: exact
96-byte header and 64-byte record goldens, canonical nibble-board layout,
32-bit move vectors, Atomic960 rook origins, strict reserved/range checks and
adapter round trips through Atomic legal move generation, SHA/sink lifecycle,
canonical manifests, native V2 generation and Atomic960 records. It does not
change the generated Legacy V1 fixture bytes.

The H7.3-C1 reader targets add strict canonical-sidecar parsing and an
authenticated streaming audit. They reject BOM/CRLF/whitespace, invalid UTF-8,
noncanonical escapes or key order, unknown/duplicate/missing fields, schema or
integer drift, unsafe basenames, repeated shard paths or identities, links and
reparse points, bad headers/sizes/checksums/counts, and every structurally or
semantically invalid record. Each decoded record must also re-encode to its
exact 64 input bytes. Errors report shard, local and global record indexes.
Network and book entries remain provenance: a reader does not require those
inputs to be present beside a completed dataset.

Generator parsing and manifest loading share the exact `keep_draws` validator:
the input and expanded canonical decimal are capped at 4096 bytes and the value
must round-trip through the generator's effective `double` without changing.
Basenames retain the already-frozen manifest-schema contract exactly; the C++
reader does not add platform-specific device-name, length or trailing-character
rules under an unchanged schema SHA. A host filesystem can still reject a name
when the corresponding file is actually accessed.

Opening a dataset parses the authoritative manifest and captures absolute paths
without reading any shard. Authentication and semantic validation are lazy and
process shards sequentially, retaining at most one authenticated snapshot;
one-record sharding therefore does not consume one OS handle per shard. A source
descriptor exists only while copying the current shard into that snapshot.
Before streaming any record from that shard, the reader copies its complete
contents to a private auto-deleting file in the system temporary directory and
authenticates the snapshot against the manifest SHA-256. Windows creates it
atomically with a cryptographic name, no sharing and delete-on-close; POSIX uses
a mode-0600 `mkstemp` file and unlinks it immediately. Records are exposed only
from the private authenticated descriptor, so a later source mutation cannot
create a hash-to-read race. Peak temporary disk use is therefore one complete
shard; snapshot creation or exhaustion of temporary storage fails closed before
a record is returned. Manifest and shard paths are captured as absolute paths
during open, so changing the process CWD cannot rebind them.
POSIX opens candidate sidecars and shards nonblocking until `fstat` proves they
are regular files, so a FIFO or other special file cannot hang validation.

The focused reader gates can also be run independently from `src`:

```sh
make -j atomic-bin-v2-manifest-reader-tests ARCH=x86-64
make -j atomic-bin-v2-reader-tests ARCH=x86-64
make -j data-tools-tests ARCH=x86-64
```
