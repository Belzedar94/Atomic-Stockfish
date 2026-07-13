# Hito 7 final Atomic BIN V2 pipeline gate

`tests/atomic_bin_v2_pipeline_e2e.py` is the fail-closed release gate for the
complete Atomic BIN V2 path. It is intentionally separate from the small
synthetic Legacy V1 E2E. This gate generates real data, performs one real CPU
optimizer update, serializes the resulting network and proves that the current
Atomic playing engine can evaluate and search with it.

The gate is implemented and covered by mocked/unit tests. The final tools
decode-wrapper dependency is now available on its `atomic` branch. The first
normative real execution is still separate evidence and must use the exact
reviewed Atomic gate commit; a PR or release must not claim the real gate as
passed from unit-test evidence alone.

| Dependency | Immutable commit |
| --- | --- |
| Contract engine pinned by tools/trainer | `76764c3c01ce5965a793a65e4580dd5c95cd2916` |
| `variant-nnue-tools` `atomic` | `40d2db224ef890f76b346ff4687e18fb33c98e23` |
| `variant-nnue-pytorch` `atomic` | `3e5651a977eca1351d7ef101acb8ff5c45588b12` |

The Atomic gate commit is intentionally not written here until the gate PR has
a reviewed immutable head.

## Frozen workload

The run has no tuning switches. It performs all of the following:

1. Authenticates clean Atomic-Stockfish, tools and trainer checkouts at exact
   commits and exact refs. The tools and trainer gitlinks must both point to the
   requested contract-engine commit.
2. Authenticates all three schema hashes, the tools lock, every executed binary,
   the Python executable, the trainer loader and the source network.
3. Requires both direct data-tools and the thin tools wrapper to expose the
   exact validate/decode capability contract. Their output must be byte-exact.
4. Generates independent train and validation datasets with distinct explicit
   seeds. Each dataset contains 128 records in two 64-record shards.
5. Generates with `Use NNUE=pure`, depth 3, one thread, Hash 512,
   `eval_limit=32000`, capture and promotion filtering enabled, and check
   filtering disabled. Every other data-affecting generator option is explicit
   on the UCI wire and verified again in the manifest.
6. Validates and decodes both manifests through the direct binary and wrapper.
   The gate checks all 128 global/shard/local indexes, complete ordered record,
   position and move schemas, FEN/clocks/castling/en-passant consistency,
   score/ply/result domains, lossless 32-bit move decomposition, provenance and
   record-to-footer WDL reconciliation. It requires byte-identical JSON/JSONL
   from both entrypoints.
7. Runs `train.py` on CPU with batch size, epoch size and validation size all
   fixed to 96. Smart filtering and random FEN skipping are disabled. Exactly
   one optimizer update and a checkpoint with `global_step=1`, `epoch=0` are
   required.
8. Serializes the checkpoint as Legacy Atomic HalfKAv2, imports it again and
   reserializes it. The two `.nnue` files must be byte-identical and the reader
   verifies version `0x7AF32F20` and architecture `0x3C103E72`.
9. Loads that candidate in the current playing engine with `Use NNUE=true`,
   runs `eval`, and requires a legal result from `go nodes 1`.
10. Deletes the private Lightning work tree, repeats checkout and artifact
    authentication after the workload, then writes canonical provenance,
    result and whole-archive checksum indexes.

`pure` is confined to data generation. The final engine-load probe uses
`Use NNUE=true`.

Network compatibility is never inferred from a filename. The source bytes are
authenticated by SHA-256, the generator authenticates and records them, the
serialized candidate header is checked, and the engine must load the exact
candidate path. The manifest basename is provenance only.

## Invocation

Use clean detached worktrees or clean branch worktrees. `--atomic-ref`,
`--tools-ref` and `--trainer-ref` must each resolve locally to their respective
commit. Tools and trainer must use their project-specific `atomic` branches;
they are not tested from or targeted directly at `main`.

The audit directory must not exist, must be outside all three checkouts, and
its absolute path cannot contain whitespace. The latter is a current property
of the generator command wire, which deliberately does not implement shell
quoting. A path such as `C:\AtomicH7\run-20260713` is suitable; a directory
under this workspace is not.

Run from the Atomic-Stockfish checkout:

```powershell
python -B tests/atomic_bin_v2_pipeline_e2e.py `
  --atomic-root C:\AtomicH7\Atomic-Stockfish `
  --atomic-commit <atomic-gate-commit> `
  --atomic-ref refs/remotes/origin/main `
  --tools-root C:\AtomicH7\variant-nnue-tools `
  --tools-commit <merged-tools-atomic-commit> `
  --tools-ref refs/remotes/origin/atomic `
  --trainer-root C:\AtomicH7\variant-nnue-pytorch `
  --trainer-commit <merged-trainer-atomic-commit> `
  --trainer-ref refs/remotes/origin/atomic `
  --contract-engine-commit <shared-engine-pin> `
  --engine C:\AtomicH7\Atomic-Stockfish\src\atomic-stockfish.exe `
  --engine-sha256 <sha256> `
  --data-generator C:\AtomicH7\Atomic-Stockfish\src\atomic-stockfish-data-generator.exe `
  --data-generator-sha256 <sha256> `
  --data-tools C:\AtomicH7\Atomic-Stockfish\src\atomic-stockfish-data-tools.exe `
  --data-tools-sha256 <sha256> `
  --tools-wrapper C:\AtomicH7\variant-nnue-tools\script\atomic_bin_v2_tools.py `
  --wrapper-data-tools C:\AtomicH7\variant-nnue-tools\engine\Atomic-Stockfish\src\atomic-stockfish-data-tools.exe `
  --wrapper-data-tools-sha256 <sha256> `
  --trainer-loader C:\AtomicH7\variant-nnue-pytorch\<build-path>\nnue_dataset.dll `
  --trainer-loader-sha256 <sha256> `
  --train-script C:\AtomicH7\variant-nnue-pytorch\train.py `
  --serialize-script C:\AtomicH7\variant-nnue-pytorch\serialize.py `
  --python C:\AtomicH7\venv\Scripts\python.exe `
  --python-sha256 <sha256> `
  --source-net C:\AtomicH7\nets\source.nnue `
  --source-net-sha256 <sha256> `
  --output-dir C:\AtomicH7\run-20260713 `
  --train-seed 2026071301 `
  --validation-seed 2026071302
```

The wrapper artifact is supplied separately because the wrapper executes the
binary inside its pinned Atomic-Stockfish submodule. The gate enforces that
canonical location and authenticates its digest even when it differs from the
direct data-tools build.

Every public command, stdin, stdout, stderr, failure and provenance field is
redacted to stable labels such as `<ATOMIC_ROOT>`, `<SOURCE_NET>` and
`<PYTHON>`. A final scan rejects known or otherwise recognizable private
Windows/user/temp absolute paths. The Lightning work directory is a private
sibling of the public archive and is deleted before postflight; its checkpoint
is represented publicly only by byte count, SHA-256, epoch and global step.

## Evidence archive

On success the archive contains:

- both manifests and all four authenticated data shards;
- numbered command, stdin, stdout and stderr logs;
- the direct/wrapper decode streams' SHA-256 values;
- the trainer checkpoint digest, byte count and checkpoint metadata (not the
  potentially host-bearing checkpoint file);
- the candidate, imported model and byte-exact round-trip network;
- `provenance.json`, `result.json` and `hashes.json`.

On any failure after archive creation, `failure.json` records the redacted
exception and traceback and the existing public evidence is retained. The
private trainer work tree is removed on both success and handled failure. Files
are created exclusively; the gate never overwrites an archive or appends
implicitly. A preflight failure creates no archive because the destination and
authenticated roots have not yet been trusted.

## Lightweight validation

The unit suite uses temporary fixtures and injected process results; it does
not generate positions or train a network:

```text
python -B -m pytest -q tests/python/test_atomic_bin_v2_pipeline_e2e.py
```

It covers CLI domains, exact generation/training policies, strict manifests,
WDL reconciliation, all decode fields/indexes/provenance and adversarial field
mutations, capability byte parity, Windows-safe explicit-LF Python probes,
checkpoint step count, NNUE header validation, exclusive archives, canonical
wrapper-child authentication, public-path redaction and pre/post artifact
immutability.

This pipeline-only gate does not change search, evaluation, move generation or
playing-network bytes, so it does not itself trigger an Elo/LOS match. Any
future code change that can affect play remains subject to the established
OpenBench strength methodology.
