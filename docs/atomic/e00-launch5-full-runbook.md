# E00 source v3 Launch5 full runbook

This is the only supported entrypoint for the fresh Launch5 full battery:

```text
tools/atomic_mining/invoke_e00_full.ps1
```

It is intentionally self-contained. It does not depend on variables, functions
or objects left in an earlier PowerShell process. Launch1 through Launch3, and
any partial Launch4 or Launch5 root, are terminal evidence: never execute,
recover, collect, edit or delete them.

When `-RepositoryRoot` is omitted or empty, the launcher resolves the exact
repository root from `$PSCommandPath` in the script body, after parameter
binding. It does not use `$PSScriptRoot` in a parameter default, which is not
reliably populated by fresh Windows PowerShell 5.1 `-File` invocation. The
derived path must be the exact `tools\atomic_mining\invoke_e00_full.ps1`
layout or validation fails.

## Frozen identity

The launcher accepts only:

- experiment `atomic-e00-src-v3-launch5-20260725`;
- smoke battery `atomic-e00-src-v3-launch5-smoke`;
- full battery `atomic-e00-src-v3-launch5-full`;
- 1 smoke pair / 2 smoke games;
- 84 full pairs / 168 full games;
- `atomic-e00-game-v3` and
  `atomic-e00-execution-receipt-v3`;
- `atomic-e00-runtime-manifest-v2`;
- `Threads=1`, 1024 plies, 120-second command timeout,
  14,400-second battery wall limit and 1,800-second game wall limit;
- the exact ordered UCI options frozen in the E00 source contract.

The source repository must be byte-clean at the exact commit and tree named by
the independent authorization receipt. The final design, smoke output and
authorization receipt must therefore be produced only after the launcher and
its tests have been committed.

## Independent authorization receipt

`-AuditReceiptPath` is mandatory. The file must be strict UTF-8, without BOM or
carriage returns, newline terminated, and byte-canonical in this exact shape:

```json
{"audit":{"p0":0,"p1":0,"verdict":"GO"},"experiment_id":"atomic-e00-src-v3-launch5-20260725","full":{"battery_id":"atomic-e00-src-v3-launch5-full","runtime_build_receipt_sha256":"<lowercase sha256>","schedule_receipt_sha256":"<lowercase sha256>"},"schema":"atomic-e00-full-authorization-v1","smoke":{"battery_id":"atomic-e00-src-v3-launch5-smoke","execution_receipt_sha256":"<lowercase sha256>"},"source":{"commit":"<lowercase git commit>","tree":"<lowercase git tree>"}}
```

The independent auditor, not the launcher operator, issues this receipt after
reviewing the committed source, the full schedule/runtime package and the
committed smoke. Any P0/P1, non-GO verdict, stale hash, noncanonical byte
representation or source drift aborts before the full runner starts.

This v1 gate authenticates the exact reviewed bytes and source identity, but
does not contain a detached signature or an independently held credential.
Auditor independence is therefore procedural, not cryptographically proven.

The smoke commit marker must itself be v3 and `committed`, with exactly one
accepted pair and two accepted games. Its output mapping is fixed as
`games -> games.jsonl`, `inventory -> inventory.json` and
`rejections -> rejections.jsonl`; the rejection ledger must be byte-empty.

The trust boundary is intentionally asymmetric. The prepare/smoke launcher
performs the deep validation of both smoke game rows, including engine,
referee, independent verifier, owned-process, trajectory and source-game-ID
evidence. The independent auditor then authorizes the exact smoke receipt
bytes by SHA-256. This full launcher rehydrates that exact authorization and
its durable top-level bindings; it does not duplicate the prepare launcher's
game-by-game semantic validator. In contrast, after the full runner exits, the
full launcher deeply validates its own newly produced receipt: nonempty exact
input/snapshot/runtime/native artifact keysets, snapshot equality, namespace
guards, trust-boundary fields, execution policy, typed reconciliation and
output bindings are all mandatory before the result is accepted.

## Fresh-shell validation

Open a genuinely new PowerShell process. Do not dot-source old launch scripts
or rehydrate prior variables. With the production defaults, run:

```powershell
$Repo = 'C:\Users\djime\Documents\Chess_variants\Codex\Fairy-Stockfish organization\Atomic Project\Atomic-Stockfish-mining-loop-v1'
$Audit = '<absolute path to the auditor-issued authorization receipt>'

& powershell.exe -NoLogo -NoProfile -NonInteractive `
  -ExecutionPolicy Bypass `
  -File (Join-Path $Repo 'tools\atomic_mining\invoke_e00_full.ps1') `
  -AuditReceiptPath $Audit `
  -ValidateOnly
if ($LASTEXITCODE -ne 0) {
    throw 'Launch5 full validation failed; do not execute'
}
```

`-ValidateOnly` is read-only. It hashes and authenticates the canonical GO,
clean source commit/tree, full schedule, runtime build, Python, every sealed
input, smoke receipt and outputs, then proves that the full output root and both
CLI captures are absent. It also rejects ignored `__pycache__`, `.pyc` and
`.pyo` artifacts in the relevant source packages. The dedicated Launch5 capture
root must already exist outside both the sealed design and smoke roots. The
validation does not invoke Python, the engine, native builder, schedule builder
or runner and does not create the full root or either capture. The regression
suite invokes this exact fresh-shell form without `-RepositoryRoot` from a
temporary clean repository and proves exit 0, strict JSON and zero full-output
or capture writes.

Review the single JSON result. It must say `validated-not-started` and identify
the expected experiment, full battery, source commit/tree, authorization hash
and full argv.

## Resource gate and execution

The launcher deliberately does **not** invent or replace the campaign's
resource gate. Immediately before execution, in the same operator session and
window:

1. record one fresh generic sample of CPU, RAM, pagefile, GPU and relevant disk
   capacity;
2. prove the exact E00 process count is zero;
3. do not stop or modify unrelated processes;
4. apply the currently authorized campaign decision to that sample, without
   introducing an undocumented threshold.

If the gate is GO, invoke the same launcher immediately, with the same receipt
and no `-ValidateOnly`:

```powershell
& powershell.exe -NoLogo -NoProfile -NonInteractive `
  -ExecutionPolicy Bypass `
  -File (Join-Path $Repo 'tools\atomic_mining\invoke_e00_full.ps1') `
  -AuditReceiptPath $Audit
if ($LASTEXITCODE -ne 0) {
    throw 'Launch5 full is terminal; do not retry or recover this root'
}
```

The launcher repeats every immutable and absence check. Its first mutation is
claiming the two captures outside the design root with
`FileMode.CreateNew`; an existence race therefore fails instead of truncating
evidence. Windows PowerShell 5.1 starts the child without `Start-Process`,
using an audited Microsoft-CRT-compatible quoting algorithm and asynchronous
raw-byte copies of both redirected streams, avoiding pipe deadlock. Success
requires runner exit 0, byte-empty stderr, strict JSON stdout, a committed v3
receipt, 84 accepted pairs / 168 accepted games, exact and nonempty artifact
maps, exact namespace/trust/execution contracts, type-exact JSON scalars,
exact output filenames, byte-empty rejection ledger and a summary hash that
matches the receipt. It then prints one `committed` JSON summary.

Any nonzero exit, stderr byte, pre-existing target, hash drift or contract
failure makes this Launch5 full root terminal. Freeze it for forensics and use
a newly named, newly sealed launch only after a new independent audit.
