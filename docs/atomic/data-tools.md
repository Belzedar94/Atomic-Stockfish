# Atomic BIN V2 data-tools contract

Atomic-Stockfish owns a separate production validator for completed Atomic BIN
V2 datasets. It links the authoritative C1 reader, but no playing-engine
`main`, search, thread or transposition-table object and no generator writer.
The normal UCI/XBoard binary and its playing signature are therefore outside
this build.

## Build and commands

From `src`:

```sh
make -j data-tools ARCH=x86-64
./atomic-stockfish-data-tools capabilities
./atomic-stockfish-data-tools validate \
  --format atomic-bin-v2 \
  --manifest /data/run.atbin.manifest.json
```

The Windows artifact is `atomic-stockfish-data-tools.exe`. Contract version 1
has exactly two commands. Options to `validate` are order-independent, but both
named arguments are mandatory. Positional input, duplicate/unknown options and
format guessing are errors. The only V2 entrypoint is the canonical
`.atbin.manifest.json` sidecar; a raw `.atbin` shard is rejected by the C1
reader even when it exists.

Windows uses the Unicode console entrypoint, converts every UTF-16 argument to
validated UTF-8 for the JSON surface, and reconstructs manifest paths from that
UTF-8 without the active-code-page ambiguity of narrow `argv`. POSIX rejects
an invalid UTF-8 argument as a contract error. JSON escaping also replaces an
invalid byte from an operating-system diagnostic rather than emitting malformed
UTF-8. Both Windows output descriptors are put in binary mode so the canonical
terminator is raw `0A`, never CRT-translated `0D 0A`.

`capabilities` writes exactly one minified UTF-8 JSON line to stdout:

```json
{"type":"atomic-data-tools-capabilities","contract_version":1,"formats":{"atomic-bin-v2":{"data_schema_sha256":"0352b036f2a140c609e3eb9c9d635dc553e8d77253d8faa92437390f5cf93cb6","manifest_schema_sha256":"83d63922df3ac4a0c81a21ec9d9fd9e180efe50f26efee62fe01710e09da5b42","entrypoint":"manifest","read":true,"write":false,"operations":["validate"]}}}
```

The hashes are compiled from the same C++ constants used by the reader. This
capability is deliberately separate from the data-generator handshake. The
generator remains `read:false,write:true`; the data-tools validator is
`read:true,write:false`.

## Validation result and errors

`validate` streams `AtomicBinV2DatasetReader::next` through EOF. Consequently
success means every declared shard was identity-checked, copied into one
authenticated private snapshot at a time, matched by size and SHA-256, decoded
through Atomic legal move generation, re-encoded byte-exactly, and reconciled
against manifest record/draw totals. It does not stop after parsing the
sidecar. A successful two-shard dataset, for example, emits one canonical line:

```json
{"type":"atomic-data-tools-validation","contract_version":1,"status":"ok","format":"atomic-bin-v2","entrypoint":"manifest","shards":2,"records":"2","side_to_move_wins":"0","draws":"0","side_to_move_losses":"2","atomic960_records":"0"}
```

`records`, the three result totals and `atomic960_records` are canonical
unsigned decimal strings. They retain the complete 64-bit counter domain even
in JSON implementations whose numeric type cannot exactly represent integers
above `2^53`. `shards` remains a JSON number because the manifest contract caps
it at 100,000.

Success uses stdout only. Failure uses stderr only and preserves the C1 reader
message verbatim inside a JSON string, including its `shard`, `local` and
`global` indexes:

```json
{"type":"atomic-data-tools-error","contract_version":1,"status":"error","operation":"validate","format":"atomic-bin-v2","code":"invalid_record","message":"Atomic BIN V2 shard=1 local=0 global=1: ..."}
```

Every response is one minified JSON object followed by one LF. The fixed exit
classes are:

| Exit | Meaning |
| ---: | --- |
| `0` | capability or full validation succeeded |
| `2` | CLI/contract error; no dataset was accepted |
| `3` | authoritative parser, authentication or semantic validation error |

`code` is the lower-snake-case spelling of the C++ `DataError` value for exit
3. Contract errors use stable codes such as `missing_format`,
`unsupported_format`, `missing_manifest`, `duplicate_argument` and
`unknown_argument`. A following `--format` or `--manifest` token is never
consumed as another option's value and produces `missing_value`. The command
intentionally has no public rewind operation:
full validation is a single bounded-memory pass, and the C1 library already
retains and tests rewind for future in-process consumers.

## Pinned wrapper delegation

The future `variant-nnue-tools` Atomic wrapper must treat its authenticated
Atomic-Stockfish gitlink as the implementation boundary:

1. verify the pinned engine commit and schema files;
2. build `make -C engine/Atomic-Stockfish/src data-tools`;
3. require the exact version-1 `capabilities` object above;
4. delegate V2 validation only as
   `validate --format atomic-bin-v2 --manifest <sidecar>`;
5. preserve the child exit class and canonical JSON response; and
6. never translate a raw shard path into a sidecar or infer V2 by extension or
   contents.

Legacy Atomic V1 commands remain in the wrapper until their own ownership
boundary changes. A caller chooses Legacy V1 or Atomic BIN V2 explicitly; the
new engine endpoint does not weaken, replace or auto-detect the historical
72-byte format. Trainer loading remains a separate Hito 7 integration and must
use the same manifest-only rule.

## Gates

Run the black-box contract from `src` with:

```sh
make -j data-tools-tests ARCH=x86-64
```

The fixture covers exact capabilities, a valid multi-shard stream, result and
Atomic960 statistics, raw-shard rejection, unsupported/missing/duplicate
arguments, checksum corruption, and a semantically corrupt second shard whose
manifest SHA is recomputed so the indexed C1 diagnostic is exercised. CI runs
the target on GCC and Clang release builds, debug/assert, MinGW and the memory
sanitizer lane. Raw-byte tests cover LF-only output and non-ASCII commands and
manifest directories on Windows. Static source-list tests keep data-tools out
of the playing binary, keep writer objects out of data-tools, and require
`objclean` to remove the executable and link-time scratch files. The same
production response renderer has a C++ vector for `2^53 + 1` and `UINT64_MAX`
that locks quoted counters and the numeric 100,000-shard boundary in every
`data-tools-tests` CI lane.
