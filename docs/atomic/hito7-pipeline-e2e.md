# Hito 7 final Atomic BIN V2 pipeline gate

`tests/atomic_bin_v2_pipeline_e2e.py` is the fail-closed release gate for the
complete Atomic BIN V2 path. It is intentionally separate from the small
synthetic Legacy V1 E2E. This gate generates real data, performs one real CPU
optimizer update, serializes the resulting network and proves that the current
Atomic playing engine can evaluate and search with it.

The gate is implemented and covered by mocked/unit tests. The final tools
decode-wrapper dependency is available on its `atomic` branch. The first
normative real execution passed on 2026-07-13 against the immutable gate-code
commit `e3a4ae87354b255c3bf2aeafa682a78ca4ae9dc3`. Its compact, path-free evidence
is recorded in [`evidence/hito7-final-e2e/summary.log`](evidence/hito7-final-e2e/summary.log)
and [`evidence/hito7-final-e2e/result.json`](evidence/hito7-final-e2e/result.json).
Unit-test evidence alone must still never be reported as a real gate pass.

| Dependency | Immutable commit |
| --- | --- |
| Contract engine pinned by tools/trainer | `76764c3c01ce5965a793a65e4580dd5c95cd2916` |
| `variant-nnue-tools` `atomic` | `40d2db224ef890f76b346ff4687e18fb33c98e23` |
| `variant-nnue-pytorch` `atomic` | `3e5651a977eca1351d7ef101acb8ff5c45588b12` |
| Atomic gate code | `e3a4ae87354b255c3bf2aeafa682a78ca4ae9dc3` |

## Frozen workload

The run has no tuning switches. It performs all of the following:

1. Authenticates clean Atomic-Stockfish, tools and trainer checkouts at exact
   commits and exact refs. The tools and trainer gitlinks must both point to the
   requested contract-engine commit.
2. Authenticates all three schema hashes, the tools lock, every executed binary,
   the Python executable, the sole root-level trainer loader and the source
   network. CPython plus the imported NumPy, PyTorch, PyTorch Lightning and
   TorchMetrics origins/files are hashed before the workload and re-imported
   and rehashed afterwards; `PYTHONPATH` and `PYTHONHOME` must be unset.
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
   score/ply/result domains, lossless 32-bit move decomposition, and each JSON
   move word against the independent little-endian 32-bit word at record offset
   52 in its authenticated shard. It also checks provenance and record-to-footer
   WDL reconciliation. It requires byte-identical JSON/JSONL from both
   entrypoints.
7. Runs `train.py` on CPU with batch size, epoch size and validation size all
   fixed to 96. Smart filtering and random FEN skipping are disabled. Exactly
   one optimizer update and a checkpoint with `global_step=1`, `epoch=0` are
   required.
8. Serializes the checkpoint as Legacy Atomic HalfKAv2, imports it again and
   reserializes it. The two `.nnue` files must be byte-identical and the reader
   verifies version `0x7AF32F20` and architecture `0x3C103E72`. Serializer
   targets live in a fresh private staging directory; only authenticated bytes
   are then published to new, exclusive archive paths.
9. Loads that candidate in the current playing engine with `Use NNUE=true`,
   runs `eval`, and requires a legal result from `go nodes 1`.
10. Freezes both manifests and all four shards immediately after generation and
    rehashes them after decode, training, serialization, UCI and immediately
    before inventory. The candidate is checked before/after UCI and the
    candidate plus round-trip are reconciled with provenance, result and the
    whole-archive checksum index.
11. Deletes the private work tree, repeats checkout, input-artifact and Python
    dependency authentication, then writes canonical provenance and result
    evidence followed by the reconciled archive inventory.

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

Run from the Atomic-Stockfish checkout with the exact same interpreter passed
to `--python` (the gate rejects a launcher/interpreter mismatch):

```powershell
C:\AtomicH7\venv\Scripts\python.exe -B tests/atomic_bin_v2_pipeline_e2e.py `
  --atomic-root C:\AtomicH7\Atomic-Stockfish `
  --atomic-commit <atomic-gate-commit> `
  --atomic-ref <atomic-gate-ref-resolving-to-that-commit> `
  --tools-root C:\AtomicH7\variant-nnue-tools `
  --tools-commit 40d2db224ef890f76b346ff4687e18fb33c98e23 `
  --tools-ref refs/remotes/origin/atomic `
  --trainer-root C:\AtomicH7\variant-nnue-pytorch `
  --trainer-commit 3e5651a977eca1351d7ef101acb8ff5c45588b12 `
  --trainer-ref refs/remotes/origin/atomic `
  --contract-engine-commit 76764c3c01ce5965a793a65e4580dd5c95cd2916 `
  --engine C:\AtomicH7\Atomic-Stockfish\src\atomic-stockfish.exe `
  --engine-sha256 <sha256> `
  --data-generator C:\AtomicH7\Atomic-Stockfish\src\atomic-stockfish-data-generator.exe `
  --data-generator-sha256 <sha256> `
  --data-tools C:\AtomicH7\Atomic-Stockfish\src\atomic-stockfish-data-tools.exe `
  --data-tools-sha256 <sha256> `
  --tools-wrapper C:\AtomicH7\variant-nnue-tools\script\atomic_bin_v2_tools.py `
  --wrapper-data-tools C:\AtomicH7\variant-nnue-tools\engine\Atomic-Stockfish\src\atomic-stockfish-data-tools.exe `
  --wrapper-data-tools-sha256 <sha256> `
  --trainer-loader C:\AtomicH7\variant-nnue-pytorch\training_data_loader.dll `
  --trainer-loader-sha256 <sha256> `
  --train-script C:\AtomicH7\variant-nnue-pytorch\train.py `
  --serialize-script C:\AtomicH7\variant-nnue-pytorch\serialize.py `
  --python C:\AtomicH7\venv\Scripts\python.exe `
  --python-sha256 <sha256> `
  --source-net C:\AtomicH7\nets\source.nnue `
  --source-net-sha256 99dc67eabf26a64faeeca3a88b4c38597a840b8d4a874b9f2cf658c6f92a04a6 `
  --output-dir C:\AtomicH7\run-20260713 `
  --train-seed 2026071301 `
  --validation-seed 2026071302
```

Only the Atomic gate HEAD is dynamic. The contract engine, tools, trainer and
source-network digests above are normative constants. On Linux the loader is
the sole trainer-root `*training_data_loader*.so`; on macOS it is the sole
trainer-root `*training_data_loader*.dylib`. The training seed domain is
`1..4294967295`; the validation/generator seed domain is
`1..18446744073709551615`. Zero is rejected because the generator normalizes it.

The wrapper artifact is supplied separately because the wrapper executes the
binary inside its pinned Atomic-Stockfish submodule. The gate enforces that
canonical location and authenticates its digest even when it differs from the
direct data-tools build.

Every public command, stdin, stdout, stderr, failure and provenance field is
redacted to stable labels such as `<ATOMIC_ROOT>`, `<SOURCE_NET>` and
`<PYTHON>`. A final scan rejects known or otherwise recognizable private
Windows and POSIX absolute paths, including JSON-escaped paths and Linux root
caches. The Lightning work directory is a private sibling of the public archive
and is deleted before postflight; its checkpoint is represented publicly only
by byte count, SHA-256, epoch and global step.

## Evidence archive

On success the archive contains:

- both manifests and all four authenticated data shards;
- numbered command, stdin, stdout and stderr logs;
- the direct/wrapper decode streams' SHA-256 values;
- the trainer checkpoint digest, byte count and checkpoint metadata (not the
  potentially host-bearing checkpoint file);
- the candidate, byte-exact round-trip network and compact serialization
  evidence (the imported model remains private);
- `provenance.json`, `result.json` and `hashes.json`.

On any failure after archive creation, the private work tree is removed first,
all existing public text is swept, and `failure.json` records only a stable,
redacted exception summary. `failure-hashes.json` inventories the retained safe
evidence. If the sweep itself cannot authenticate a text file, public text
evidence is discarded rather than leaked. Public result/network publication is
exclusive and never appends or overwrites; the emergency privacy sweep may
rewrite already-created text evidence solely to remove host paths. A preflight
failure creates no archive because the destination and authenticated roots have
not yet been trusted.

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
