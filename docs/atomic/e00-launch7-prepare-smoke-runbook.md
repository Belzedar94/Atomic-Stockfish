# E00 source v3 Launch7 prepare-and-smoke runbook

This is the only supported entrypoint for creating the fresh Launch7 design
and running its one-pair T1 smoke:

```text
tools/atomic_mining/invoke_e00_prepare_smoke.ps1
```

It is a Windows PowerShell 5.1 launcher. It is self-contained and does not
reuse variables, functions, roots, experiment IDs or battery IDs from Launch1
through Launch6. Those launches are terminal forensic evidence and must never
be executed, recovered, edited, deleted or reused.

When `-RepositoryRoot` is omitted or empty, the launcher resolves the exact
repository root from `$PSCommandPath` in the script body, after parameter
binding. It does not use `$PSScriptRoot` in a parameter default, which is not
reliably populated by fresh Windows PowerShell 5.1 `-File` invocation. The
derived path must be the exact
`tools\atomic_mining\invoke_e00_prepare_smoke.ps1` layout or validation fails.

The launcher does **not** execute the 84-pair full battery. It stops after:

1. authenticating one exact clean source commit/tree and all pinned inputs;
2. building a fresh native Atomic rules binding;
3. creating deterministic v3 smoke and full schedules;
4. creating separate smoke and full runtime-manifest-v2 packages;
5. sealing the design with a create-new inventory and receipt;
6. executing exactly one serial T1 smoke pair;
7. authenticating the committed v3 smoke and proving the full root is absent.

The design, smoke, full-output and capture roots must all be absent and
pairwise disjoint. All child stdout/stderr captures are create-new files in the
external capture root, never inside the sealed design. Any appearance or
partial creation makes that exact Launch7 root terminal.

## Frozen identity

- experiment: `atomic-e00-src-v3-launch7-20260725`;
- smoke battery: `atomic-e00-src-v3-launch7-smoke`;
- full battery: `atomic-e00-src-v3-launch7-full`;
- smoke seed: `atomic-e00-src-smoke-v3-launch7-20260725`;
- full seed: `atomic-e00-src-full-v3-launch7-20260725`;
- smoke schedule: one `VSTC:2000:20` pair / two games;
- full schedule: 48 VSTC, 24 STC and 12 LTC pairs / 168 games;
- runtime: `Threads=1`, 1024 plies, 120-second command timeout and
  1,800-second per-game wall limit;
- smoke battery wall limit: 3,600 seconds;
- full battery wall limit: 14,400 seconds;
- game/result wire: `atomic-e00-game-v3` and
  `atomic-e00-execution-receipt-v3`;
- exact startup preamble, `id name`, `id author`, and one blank post-ID UCI
  separator frozen by the v3 source contract;
- procedural independent audit, with no invented signing key or claim of
  cryptographic operator identity.

The launcher rejects dirty/untracked source, source commit/tree drift, ignored
`__pycache__`, `.pyc` or `.pyo` below `tools`, input-hash drift, any
pre-existing target, malformed or non-v3 output, nonempty stderr, nonempty
rejection ledger, incomplete color pair or handshake drift.

Launch7 preserves both earlier corrected Windows serialization boundaries:
design directories, design entries, referee rule operations, timing
operations and execution-input keys must serialize as flat arrays whose
elements have the exact scalar/object type. Nested arrays are rejected. This
is the minimal correction for the terminal Launch3 seal failure.

The runner also writes its canonical summary through `sys.stdout.buffer`.
This avoids Python text-mode LF-to-CRLF translation on Windows and preserves
the launcher's strict LF-only JSON evidence contract. It is the minimal
correction for the terminal Launch4 wrapper failure.

Finally, the wrapper serializes JSON strings character by character with the
same contract as Python `json.dumps(..., ensure_ascii=False)`: quote,
backslash and U+0000-U+001F are escaped, while `<`, `>`, `&`, Unicode and
valid surrogate pairs remain literal. Windows PowerShell 5.1
`ConvertTo-Json` is never used for this digest boundary. The cross-runtime
regression includes the exact 19-option engine contract and reproduces the
recorded canonical digest
`0c2ef87b8de44c286f0f6032ad7f0906835a731df5038c903959ce189a8e6cf5`.
This is the sole code correction for the terminal Launch5 wrapper false
negative.

This encoder is authorized only for the frozen E00 documents, whose object
keys are ASCII and whose numeric fields use the existing integer and ordinary
finite-double domain. It is not claimed as a general cross-runtime JSON
canonicalization standard for arbitrary Unicode keys or pathological floating
point lexemes.

Launch6 then completed and committed its exact one-pair smoke, but the outer
validator failed closed on a legitimate zero-byte Python standard-library
module, `urllib\__init__.py`. The row was already bound by exact path,
module name, SHA-256 and canonical equality to the sealed runtime inventory;
only the generic `size_bytes >= 1` assertion was false. Launch7 changes that
one assertion to type-exact `size_bytes >= 0`. Negative values, booleans,
numeric strings, field drift, digest drift and inventory drift remain rejected.
No schedule, engine, network, runtime, referee, verifier, game or result
contract changes.

## Read-only validation

Commit the launcher, tests and documentation first. In a genuinely new
PowerShell process, obtain the exact clean Git identity:

```powershell
$Repo = 'C:\Users\djime\Documents\Chess_variants\Codex\Fairy-Stockfish organization\Atomic Project\Atomic-Stockfish-mining-loop-launch7'
$Commit = (& git -C $Repo rev-parse HEAD).Trim().ToLowerInvariant()
$Tree = (& git -C $Repo rev-parse 'HEAD^{tree}').Trim().ToLowerInvariant()

& powershell.exe -NoLogo -NoProfile -NonInteractive `
  -ExecutionPolicy Bypass `
  -File (Join-Path $Repo 'tools\atomic_mining\invoke_e00_prepare_smoke.ps1') `
  -ExpectedSourceCommit $Commit `
  -ExpectedSourceTree $Tree `
  -ValidateOnly
if ($LASTEXITCODE -ne 0) {
    throw 'Launch7 prepare/smoke validation failed; do not execute'
}
```

`-ValidateOnly` is read-only. It does not create a directory or file and does
not invoke Python, the native builder, an engine, a schedule builder or the
runner. Its JSON must say `validated-not-started`, identify the exact commit,
tree, fresh roots, IDs, seeds and 1/84 pair plan. The regression suite invokes
this exact fresh-shell form without `-RepositoryRoot` from a temporary clean
repository and proves exit 0, strict JSON and zero target-root writes.

## Resource gate and prepare/smoke execution

Immediately before execution:

1. record a fresh generic CPU, RAM, pagefile, GPU and relevant-disk sample;
2. prove the exact E00 process count is zero;
3. do not stop or modify unrelated processes;
4. apply the campaign's current resource decision to that sample.

If the gate is GO, immediately launch from another genuinely fresh PowerShell
process with the same commit and tree:

```powershell
& powershell.exe -NoLogo -NoProfile -NonInteractive `
  -ExecutionPolicy Bypass `
  -File (Join-Path $Repo 'tools\atomic_mining\invoke_e00_prepare_smoke.ps1') `
  -ExpectedSourceCommit $Commit `
  -ExpectedSourceTree $Tree
if ($LASTEXITCODE -ne 0) {
    throw 'Launch7 prepare/smoke is terminal; do not retry or recover its roots'
}
```

The first mutation occurs only after the launcher repeats the complete
read-only preflight. Native build, both schedules and both runtime manifests
must finish with exit 0, byte-empty stderr and strict compact JSON stdout. The
design receipt is published before the engine starts. The design inventory is
recomputed before and after the smoke.

Success is one final compact JSON object with status
`sealed-smoke-committed`, exact design inventory/receipt hashes, a committed
smoke receipt hash and `full.output_absent=true`.

Before emitting that status, the launcher revalidates the complete smoke
contract rather than trusting only its commit marker. It requires two exact
`atomic-e00-game-v3` rows in schedule order, recomputes trajectory and source
game IDs, checks type-exact booleans/integers/numbers, verifies exact engine
handshakes and UCI/network configuration, replays the referee timing/rules
journals, authenticates the independent verifier and natural-zero-descendant
process evidence, and checks the exact pair-staging and output inventories.
Every durable artifact map is nonempty with its expected keyset and file
binding; omissions, extra keys, numeric strings and integer-for-boolean
coercions fail closed.

## Independent audit before full

Freeze the successful roots read-only. An independent auditor must recompute:

- clean source commit/tree and every design inventory entry;
- native binding/build-manifest hashes;
- both schedule receipts, seeds, strata and 1/84 pair counts;
- both runtime manifests/build receipts and child import inventories;
- the smoke's two canonical v3 game rows, exact handshake evidence,
  native referee/verifier evidence and natural-zero-descendant proofs;
- exact output mapping and byte-empty rejection ledger;
- unchanged pinned inputs and absent full root.

Only that independent P0=0/P1=0 GO may issue the canonical authorization
receipt consumed by
[`e00-launch7-full-runbook.md`](e00-launch7-full-runbook.md). The full launcher
must still perform a separate fresh-shell validation and a new immediate
resource gate.
