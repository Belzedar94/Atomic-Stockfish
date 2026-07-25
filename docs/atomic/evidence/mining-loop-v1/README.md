# Atomic match-mining loop v1 — execution ledger

This directory is the append-oriented scientific ledger for the first Atomic
match-mining pilot.  Durable machine artifacts are emitted separately as
canonical JSON/JSONL with create-new semantics; this file records the human
decision trail.

## Scope

- Objective: locate positions where the current best V3 network lacks
  knowledge already present in the legacy `run3b` champion, then compare
  matched `MINED`, `RANDOM`, and `REPLAY` training arms.
- The official 1B DATAGEN campaign is read-only and out of scope.
- The shared Spell workload and unrelated processes must not be stopped or
  modified.
- Heavy CPU/GPU work requires a fresh utilization sample immediately before
  launch.

## Source identity

- Worktree branch: `agent/atomic-mining-loop-v1`
- Base commit: `01a74371d6c947bb03ae12a8cff6a35044d3aa0b`
- Base tree: `b81d7aadde7334af4e25e1654754afa096e299ec`

## 2026-07-24

### Design and repository audit

- Selected a clean worktree from the authenticated Atomic teacher/Syzygy v2
  source.  Dirty evaluation worktrees were left untouched.
- Confirmed that historical match PGNs and Hito 6 UCI logs are suitable only
  for parser/replay tests.  They are not fresh evidence for current-V3 versus
  `run3b`; a fresh authenticated discovery match is required before a
  confirmatory mining claim.
- Pinned the current V3 network from its final receipt and the pure `run3b`
  teacher from the authenticated 1B publication contract.  Both files must be
  re-hashed immediately before any probe.
- Kept Syzygy as a diagnostic canary only: WDL/DTZ evidence is not a centipawn
  label and cannot be silently converted into an ATBIN training result.
- Added the `E02-M` match-mined disagreement experiment ahead of synthetic
  tactical expansion.

### Independent review

- The Oracle browser invocation selected and verified GPT-5.6 Pro.
- No advisory answer was obtained: the Chrome session became unreachable
  during generation.  There was no API fallback and no Oracle advice is
  represented as received.
- Three independent implementation/science reviewers were assigned the UCI
  session, source extractor, and scientific/TB contract respectively.
- A second Oracle review was requested for the E00 source contract with the
  actual historical runner attached.  The first retry failed in the browser
  model picker even though `GPT-5.6 Sol` was listed; a second browser run was
  started with the current-model strategy rather than silently switching to an
  API or another model.  Browser evidence recorded
  `requested=GPT-5.6 Sol`, `resolved=Pro`, `already-selected`; the response
  streamed for at least 90 seconds, then Chrome disconnected before an answer
  could be captured.  No API fallback or uncaptured Oracle advice is used.

### E00 source audit

- The frozen historical match runner is not result-bearing one-to-one: it
  writes root plus moves but not each game's result, uses an unseeded random
  root choice, appends logs, and continues after pair exceptions.
- The defect is observable in the historical TC1 artifact: `131` game records
  are present while the final accepted aggregate is `130`.  The extra
  trajectory cannot be identified retrospectively.
- Therefore no scientific E00 was launched with the historical runner.
  `docs/atomic/e00-source-contract.md` now separates a fixed-size,
  result-bearing `E00-SRC` battery from the independent VSTC/STC/LTC
  `E00-LOS` strength gates.
- The proposed E00-SRC pilot contains `84` fixed color pairs (`168` games):
  `48` VSTC, `24` STC and `12` LTC, expected to yield approximately
  `10,000`–`20,000` raw positions.  It requires a deterministic schedule,
  per-game result sidecar, pair atomicity and final receipt before execution.

### Resource samples

At `2026-07-24T21:10Z`, after the user closed League of Legends:

- CPU: approximately `29.2%, 29.5%, 33.4%` across three one-second samples.
- RAM: `5.91 GiB` free of `31.92 GiB`.
- Pagefile: `22.64 GiB` used of `95.99 GiB`.
- GPU: `36%`, `1380/10240 MiB`, `41 C`, `27.11 W`.
- Free disk: `C: 126.4 GiB`, `D: 128.6 GiB`, `F: 1126.9 GiB`.

This was accepted for coding and focused tests only.  It was not treated as an
authorization to launch a large probe.

### Validation so far

- `python -B -m pytest -q tests/python/test_atomic_mining_common.py`
- Result: `13 passed in 0.27s`.
- Extractor plus common: `23 passed`.
- UCI session plus extractor/common: `42 passed`.
- Global component split: `8 passed`.
- The first integrated mining suite reached `114 passed`.  A subsequent
  independent contract audit still returned `NO-GO` for scientific use:
  unit coverage had not yet proved move-by-move legal replay, authenticated
  content-derived IDs, the complete pre-probe component graph, or all five
  globally isolated partitions.
- No engine pilot was started under that false-green state.  The audit
  findings were split into three bounded fixes: legal replay and state
  transitions; global graph/partitions/ID recomputation; and single-snapshot
  input hashing plus create-new durable publication.
- The pinned engine's live UCI handshake established that it has no
  `UCI_ShowWDL` option.  The probe contract was corrected before execution:
  WDL remains `null`, intra-teacher centipawn regret is the explicit fallback,
  and mate-class comparisons remain categorical.
- Final probe options are explicit and ordered: `UCI_Variant=atomic`,
  `Threads=1`, fixed `Hash`, fixed `MultiPV`, `Ponder=false`, empty
  `SyzygyPath`, `SyzygyProbeLimit=0`, `Use NNUE=pure`, plus the separately
  hashed `EvalFile`.  Every independent search starts with `ucinewgame`,
  `isready`, and `Clear Hash`; network order alternates by position-ID parity.

### Re-authenticated runtime inputs

| Artifact | Bytes | SHA-256 |
| --- | ---: | --- |
| Fresh BMI2 Atomic engine | 4,504,676 | `86d2bb669ff2a56123a78fd1892c2acf8b4294fb1464da049ddb30877ce5127f` |
| Legacy `run3b` teacher | 47,721,376 | `99dc67eabf26a64faeeca3a88b4c38597a840b8d4a874b9f2cf658c6f92a04a6` |
| Current V3 epoch-37 network | 80,274,459 | `0797cdbaf857aa4552d4eab301227ce3ba7c23a731973c255bb1dfc763659e5b` |
| Current V3 final receipt | 3,996 | `a646b4ca0902dab90c7edd10285e975a5d98017f77f11c72e6a100f506f9e562` |

The engine and teacher hashes match the authenticated V2 datagen-golden
ledger.  The current V3 hash and byte count match its final training receipt.

## 2026-07-25

### E00 schedule implementation

- Added a deterministic, create-new `atomic-e00-schedule-v1` builder and a
  public semantic loader used by the result-bearing runner.
- The real `atomic.epd` snapshot parsed as exactly `6,199` unique six-field
  roots, `394,785` bytes, SHA-256
  `28ed51c2f42e723d5e127d2d3f21c0bfa4a9b318615afdb299b93ea62dea2b1`.
- Two independent builds with seed `atomic-e00-src-v1-20260725` and the frozen
  `48/24/12` pair allocation produced byte-identical artifacts:
  - schedule: `168` rows, `119,096` bytes, SHA-256
    `bf3f51fae0bebf5b89d2304366218fcc97fdf1231b7bb31f2d089ebcf581f5fc`;
  - schedule receipt: `6,446` bytes, SHA-256
    `0b1af3158f0cbc3d62cd8e48d2dc059db338a3e4be987b2560097ce64c694d9b`.
- The schedule consumes roots without replacement and keeps the VSTC, STC and
  LTC root sets disjoint.  Both legs bind the same root and invert the two
  network roles exactly.

### Integrated validation before a real-engine smoke

- The complete mining test selection is currently `183 passed`.
- Every `tools/atomic_mining/*.py` module passes `py_compile`.
- `git diff --check` is clean.
- The E00 runner has a real `python-chess`/UCI backend, but its unit tests use
  only an injected fake backend.  A real-engine smoke remains mandatory after
  the independent P0/P1 audit, source commit and fresh resource sample.
- No scientific game, network probe or GPU job has started at this point.

### Independent E00 execution audit

- The schedule builder received `GO`.
- The first execution-runner review returned `NO-GO`; no real-engine smoke was
  permitted under the false-green unit suite.
- P0 findings:
  - a clock-only `python-chess` `Limit` does not apply the engine protocol
    timeout, so an in-flight `play()` call could outlive both the nominal
    command timeout and the outer wall-clock checks;
  - the first runner receipt did not authenticate the actual executing Python
    interpreter, `python-chess` implementation, imported mining modules or
    prove that the caller-supplied runner path was the executing wrapper.
- P1 findings included first-ply time-loss handling, Atomic insufficient-
  material flag adjudication, configure/quit/temp-file cleanup, complete
  `VariantPath`/`EvalFile` evidence and execution from immutable
  content-addressed snapshots.
- The runner is being hardened against those findings.  Its fake-backend
  tests and the `183 passed` integrated result are not accepted as evidence
  that the production UCI lifecycle is safe.

### Resource sample after the interactive GPU load was closed

- At `2026-07-25T00:35:59+02:00`, after the user reported closing League of
  Legends, a generic host sample observed CPU `27%`, RAM
  `23.62/31.92 GiB`, pagefile `22.49 GiB` used, and GPU `33%`,
  `1,378/10,240 MiB`, `45 C`, `29.24 W`.
- Free space was `125.1 GiB` on `C:`, `128.6 GiB` on `D:` and
  `1,126.9 GiB` on `F:`.
- This sample authorizes only continued code/test work.  It is not the
  pre-execution gate: all inputs must be rehashed and CPU/RAM/GPU resampled
  immediately before the first real-engine smoke.  The first smoke remains
  `Threads=1`, and the user-managed OpenBench worker remains stopped.

### Oracle review of the exact Atomic referee

- A major-step review was requested through the browser Oracle using the
  user's ChatGPT Pro session; no API or paid-credit fallback was used.
- The first attempt, session `atomic-e00-referee`, failed before submission
  because eight individual attachments did not reach a send-ready state.
- The second attempt, `atomic-e00-referee-bundle`, packed the same eight files
  into one bundle.  Browser evidence records requested/resolved `Pro`,
  selection status `already-selected`, and `verified=yes`; the response
  entered streaming, but Chrome disconnected before Oracle captured any
  answer.
- Consequently there is no Oracle recommendation to apply or cite.  The
  referee choice remains governed by the repository's C++ `Atomic::outcome`
  contract, local adversarial fixtures, provenance binding and independent
  audit.

### Native-referee ambient-binding rejection

- A first native outcome-helper prototype correctly targeted the C++
  `Atomic::outcome` surface and added adversarial fixtures for rule50,
  repetition, Atomic explosion, mate, stalemate and material.
- Its initial tests were nevertheless `NO-GO`: bare `import pyffish` resolved
  to the ambient
  `Python312\Lib\site-packages\pyffish.cp312-win_amd64.pyd`, `800,768`
  bytes, SHA-256
  `6be79e4c76a0d3c0ae4736d747a44492c6912327fc4c6b265f2ba68b497e958f`,
  reporting `Fairy-Stockfish 010526 LB` and version `(0, 0, 89)`.
- That binary is not evidence for this repository's frozen Atomic rules, even
  when individual golden cases happen to agree.  It is rejected for E00.
  The gate now requires a freshly built, exact-path import whose artifact,
  toolchain, source commit/tree and source-file inventory are all bound before
  the helper can be integrated.
- A later generic resource sample observed CPU `54%`, RAM
  `26.65/31.92 GiB` and GPU `36%` with `1,427/10,240 MiB`.  This is an
  explicit `NO-START` sample for games or network probes; only bounded
  analysis/tests and the already isolated serial binding build may continue.
- A subsequent sample reached CPU `76%` with multiple pre-existing
  `stockfish` processes visible.  They are treated as unrelated shared work:
  none was inspected by command line, stopped, reprioritized or reused, and
  E00 remained closed.

### Result-blind downstream identity

- The E00 `trajectory_sha256` incorporates `result_white`, and the original
  `source_game_id`/`position_id` inherit that dependency.  They therefore
  remain provenance only and are forbidden from component IDs, split hashes,
  ordering, tie-breaks, sampling, ranking and caps.
- Downstream E00 nodes use new pre-result structural identities derived from
  the sealed schedule/pair/leg/root/history/FEN/ply fields.  `pair_id` joins
  the two color-swapped legs; a structural game ID joins positions within one
  leg.  Source result remains attached only as auditable metadata.
- Metamorphic tests must show that changing a syntactically valid result and
  all result-derived provenance IDs leaves the structural split, rank and
  selection decisions unchanged.

### Overnight continuation and exact-referee gate

- The user reported closing League of Legends and authorized the experiment
  plan to continue to completion overnight.  That observation is treated as a
  possible source of freed capacity, not as a resource grant: every real
  CPU/GPU step still requires a fresh generic utilization sample immediately
  before launch.  Existing unrelated Stockfish/Python processes remain
  untouched, and the user-managed OpenBench worker is not to be started.
- The first fresh post-message sample at
  `2026-07-25T01:04:09+02:00` still observed CPU `55.4%`, RAM
  `26.06/31.92 GiB`, pagefile `22,972 MiB` used and GPU `24%`,
  `1,365/10,240 MiB`, `46 C`, `29.27 W`; free space was `124.9 GiB`
  on `C:`, `128.5 GiB` on `D:` and `1,126.9 GiB` on `F:`.  It is a
  `NO-START` sample for real games despite the lower GPU load; code and bounded
  tests may continue.
- The native Atomic outcome helper is independently `GO` as an isolated
  component.  A fresh serial build from source commit
  `01a74371d6c947bb03ae12a8cff6a35044d3aa0b` produced
  `pyffish.pyd` (`142,336` bytes, SHA-256
  `0869efbb6d0bacf1f5e0c193f6dd4ac2102706af17faa94787ae380350dd35bf`)
  and a source/toolchain manifest (SHA-256
  `f26a18b1327142b7c4fdc1d4fa917fb4cbbf3cec0027b1854857c1c42a037ceb`).
  Sixteen exact-path tests passed.  These hashes are development evidence
  only; production artifacts must be rebuilt from the eventual clean,
  committed E00 tree.
- The second independent runner audit remains `NO-GO`.  The audited runner
  still adjudicates with `python-chess`, does not call the exact native helper,
  can have `SimpleEngine.play()` reset target UCI options, charges setup time
  inconsistently, and proves only wrapper death rather than zero owned
  descendants.  The extractor also starts the replay engine before taking an
  immutable snapshot.  No real E00 game is permitted while any of these
  conditions remains.
- One additional helper hardening item is accepted: a terminal optional
  outcome must be exactly a two-element `(bool, plain-int)` value with
  `(True, 0)` for draw, rather than validating only its first element.
- The replacement runner is therefore constrained to direct persistent UCI,
  exact freshly built pyffish legality/outcome, setup outside the charged
  search interval, immutable pre-execution snapshots, and Windows owned-tree
  containment with a verifiable zero-descendant postcondition.  Stub-engine
  integration tests and a second independent binary audit are mandatory
  before the first one-pair `Threads=1` smoke.
- The final binary audit of the pre-rewrite implementation recorded five P0s:
  exact native referee not integrated; UCI options resettable by
  `SimpleEngine.play()`; setup incorrectly chargeable to the first-ply clock;
  wrapper-only rather than zero-descendant cleanup proof; and mutable-path
  execution in the production extractor.  Eight P1s cover the equality
  boundary, executed snapshot paths and `VariantPath` policy, final semantic
  reconciliation, sanitized child runtime identity, helper
  manifest/optional validation, extractor trust anchors, UCI cleanup errors
  and temporary-file leakage.
- The audited pre-rewrite hashes were:
  `run_e00_source.py`
  `ef283f99624f8cbb53c1b9f223eb90e0cf32844ccd3db4343f4105aa20cd91a9`,
  `atomic_outcome_helper.py`
  `54ee0328d900cad5b8353b33d5876257de1f4a44808b2119832d23bef05d3c09`,
  `build_atomic_outcome_binding.py`
  `730f5c799a7e7ccd76abe296f353d5e09b7c5aa2d4dac4767afd296c7565702b`,
  `extract_e00_positions.py`
  `547e76df5dab1173e627dd748654fbcb30a5632b5db6898d08074c644fbda573`,
  and `uci_session.py`
  `c41a2ae66a1c77213f3aa05a1101d0560c436565117bcfa69f3b5e2c238e19a7`.
  The audit ran `105` focal tests, `py_compile` and `git diff --check`
  successfully, but those checks correctly did not override the P0 verdict.

### E00 v2 wire and packaging closure

- The direct-UCI candidate now freezes `atomic-e00-game-v2`,
  `atomic-e00-execution-receipt-v2` and
  `atomic-e00-runtime-manifest-v2`. Internal leg, verifier and runtime
  discovery request/results are also v2; schedule and its receipt remain v1.
- `build_atomic_outcome_binding_cli.py` is the separate operational native
  builder. It requires the exact clean commit/tree, writes only to a new
  directory outside the source root, reopens the published binding/manifest
  and fails on pre/post source drift.
- `build_e00_runtime_manifest.py` authenticates all 18 file/module inputs,
  stages the exact nine-module runtime package and native snapshots, and uses
  the real isolated child bootstrap to discover the import inventory without
  starting an engine. `build.receipt.json` is its final commit marker.
- The runner holds strict-static guards over every regular file in the
  runtime-package, native-build, snapshot and rules-source namespaces. Every
  accepted leg and independent verifier carries an
  `atomic-e00-owned-process-v1` natural-zero-descendant proof.
- Packaging tests are `13 passed`; the combined packaging/schedule/helper/
  owned-process/runner/direct-v2 selection is `83 passed`. The frozen
  Atomic-mining selection is `306 passed`, and
  `compileall`/`py_compile` pass. These are implementation results, not
  authority to execute science.

### Historical E00 v2 Launch2 runbook — terminal, never execute

> **Historical evidence only.** Launch2 is terminal and immutable. Never run,
> recover, edit, delete or reuse any command, root or battery ID in this
> section. The only current prepare/smoke authority is
> [`../../e00-launch6-prepare-smoke-runbook.md`](../../e00-launch6-prepare-smoke-runbook.md).
> The only current full-launch authority is
> [`../../e00-launch6-full-runbook.md`](../../e00-launch6-full-runbook.md).

The block below records the exact path that was used for Launch2. It is kept
only to preserve forensic provenance.

```powershell
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$Repo = (Resolve-Path '.').Path
$Python = 'C:\Users\djime\AppData\Local\Programs\Python\Python312\python.exe'
$DesignRoot = 'F:\Atomic-V3-E00\e00-src-v2-launch2-design'
$SmokeOutput = 'F:\Atomic-V3-E00\e00-src-v2-launch2-smoke'
$FullOutput = 'F:\Atomic-V3-E00\e00-src-v2-launch2-full'

$Book = 'C:\Users\djime\Documents\Chess_variants\Match script\books\atomic.epd'
$Engine = 'C:\Users\djime\Documents\Chess_variants\Codex\Fairy-Stockfish organization\Atomic Project\Atomic-Stockfish-teacher-syzygy-v2-build-launch1\src\atomic-stockfish.exe'
$CurrentNet = 'D:\NNUE training\Atomic-v2\campaign-28eaed5-high-lambda\lambda-100\artifacts\atomic-v3-lambda-100-epoch-37.nnue'
$TeacherNet = 'C:\Users\djime\Documents\Chess_variants\Codex\Fairy-Stockfish organization\Atomic Project\atomic_run3b_e202_l05.nnue'
$VariantConfig = 'C:\Users\djime\Documents\Chess_variants\Match script\variants.ini'

function Get-Sha256([string] $Path) {
    return (Get-FileHash -Algorithm SHA256 -LiteralPath $Path).Hash.ToLowerInvariant()
}

function Invoke-E00Cli {
    param(
        [Parameter(Mandatory = $true)][string[]] $Arguments,
        [Parameter(Mandatory = $true)][string] $Stdout,
        [Parameter(Mandatory = $true)][string] $Stderr
    )
    if ((Test-Path -LiteralPath $Stdout) -or (Test-Path -LiteralPath $Stderr)) {
        throw "refusing to reuse CLI capture"
    }
    $quoted = ($Arguments | ForEach-Object {
        '"' + ([string] $_).Replace('"', '\"') + '"'
    }) -join ' '
    $process = Start-Process -FilePath $Python -ArgumentList $quoted `
        -WorkingDirectory $Repo -NoNewWindow -Wait -PassThru `
        -RedirectStandardOutput $Stdout -RedirectStandardError $Stderr
    if ($process.ExitCode -ne 0) {
        throw "CLI exited $($process.ExitCode); preserve the root as terminal"
    }
    if ((Get-Item -LiteralPath $Stderr).Length -ne 0) {
        throw "CLI stderr is not byte-empty; preserve the root as terminal"
    }
}

if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) {
    throw "pinned Python executable is absent"
}
$dirty = @(& git -C $Repo status --porcelain=v1 --untracked-files=all)
if ($LASTEXITCODE -ne 0 -or $dirty.Count -ne 0) {
    throw "E00 source must be committed and exactly clean"
}
$Commit = (& git -C $Repo rev-parse HEAD).Trim().ToLowerInvariant()
$Tree = (& git -C $Repo rev-parse 'HEAD^{tree}').Trim().ToLowerInvariant()
if ($Commit -notmatch '^[0-9a-f]{40}$' -or $Tree -notmatch '^[0-9a-f]{40}$') {
    throw "invalid Git commit/tree identity"
}

$ExpectedInputs = [ordered]@{
    $Book = '28ed51c2f42e723d5e127d2d3f21c0bfa4a9b318615afdb299b93ea62dea2b1e'
    $Engine = '86d2bb669ff2a56123a78fd1892c2acf8b4294fb1464da049ddb30877ce5127f'
    $CurrentNet = '0797cdbaf857aa4552d4eab301227ce3ba7c23a731973c255bb1dfc763659e5b'
    $TeacherNet = '99dc67eabf26a64faeeca3a88b4c38597a840b8d4a874b9f2cf658c6f92a04a6'
    $VariantConfig = '30a4779fde75b5259f732a148872aa81dca96da7c766238d0153a591d6624e37'
}
foreach ($entry in $ExpectedInputs.GetEnumerator()) {
    if ((Get-Sha256 $entry.Key) -ne $entry.Value) {
        throw "pinned input hash differs: $($entry.Key)"
    }
}

foreach ($root in @($DesignRoot, $SmokeOutput, $FullOutput)) {
    if (Test-Path -LiteralPath $root) {
        throw "refusing to reuse E00 root: $root"
    }
}
New-Item -ItemType Directory -Path $DesignRoot | Out-Null

$NativeRoot = Join-Path $DesignRoot 'native-binding'
$NativeStdout = Join-Path $DesignRoot 'native-binding.stdout.json'
$NativeStderr = Join-Path $DesignRoot 'native-binding.stderr.bin'
Invoke-E00Cli -Arguments @(
    '-B', '-m', 'tools.atomic_mining.build_atomic_outcome_binding_cli',
    '--source-root', $Repo,
    '--expected-source-commit', $Commit,
    '--expected-source-tree', $Tree,
    '--output-dir', $NativeRoot,
    '--timeout-seconds', '300'
) -Stdout $NativeStdout -Stderr $NativeStderr

$NativeSummary = Get-Content -Raw -Encoding UTF8 -LiteralPath $NativeStdout |
    ConvertFrom-Json
if ($NativeSummary.schema -ne 'atomic-e00-native-outcome-build-cli-summary-v1') {
    throw "native builder summary schema differs"
}
$Bindings = @(Get-ChildItem -LiteralPath (Join-Path $NativeRoot 'binding') `
    -File | Where-Object { $_.Name -like 'pyffish*.pyd' })
if ($Bindings.Count -ne 1) {
    throw "native bundle must contain exactly one pyffish.pyd"
}
$Pyffish = $Bindings[0].FullName
$PyffishManifest = Join-Path $NativeRoot 'manifest.json'
if ((Get-Sha256 $Pyffish) -ne $NativeSummary.binding.sha256 -or
    (Get-Sha256 $PyffishManifest) -ne $NativeSummary.build_manifest.sha256) {
    throw "native bundle differs after reopen"
}

$ModulePaths = [ordered]@{
    ToolsInit = Join-Path $Repo 'tools\__init__.py'
    AtomicMiningInit = Join-Path $Repo 'tools\atomic_mining\__init__.py'
    Common = Join-Path $Repo 'tools\atomic_mining\common.py'
    ScheduleBuilder = Join-Path $Repo 'tools\atomic_mining\build_e00_schedule.py'
    Runner = Join-Path $Repo 'tools\atomic_mining\run_e00_source.py'
    UciSession = Join-Path $Repo 'tools\atomic_mining\uci_session.py'
    AtomicOutcomeHelper = Join-Path $Repo 'tools\atomic_mining\atomic_outcome_helper.py'
    BindingBuilder = Join-Path $Repo 'tools\atomic_mining\build_atomic_outcome_binding.py'
    OwnedProcess = Join-Path $Repo 'tools\atomic_mining\owned_process.py'
}

function New-E00Schedule {
    param(
        [string] $Name,
        [string] $Seed,
        [string[]] $TimeControls
    )
    $root = Join-Path $DesignRoot $Name
    New-Item -ItemType Directory -Path $root | Out-Null
    $schedule = Join-Path $root 'schedule.jsonl'
    $receipt = Join-Path $root 'schedule.receipt.json'
    $arguments = @(
        '-B', '-m', 'tools.atomic_mining.build_e00_schedule',
        '--book', $Book, '--seed', $Seed
    )
    foreach ($timeControl in $TimeControls) {
        $arguments += @('--tc', $timeControl)
    }
    $arguments += @('--output', $schedule, '--receipt', $receipt)
    $stdout = Join-Path $root 'builder.stdout.json'
    $stderr = Join-Path $root 'builder.stderr.bin'
    Invoke-E00Cli -Arguments $arguments -Stdout $stdout -Stderr $stderr
    return [pscustomobject]@{
        Root = $root
        Schedule = $schedule
        Receipt = $receipt
        Summary = (Get-Content -Raw -Encoding UTF8 -LiteralPath $stdout |
            ConvertFrom-Json)
    }
}

$SmokeSchedule = New-E00Schedule `
    -Name 'smoke-schedule' `
    -Seed 'atomic-e00-src-smoke-v2-20260725' `
    -TimeControls @('VSTC:2000:20:1')
$FullSchedule = New-E00Schedule `
    -Name 'full-schedule' `
    -Seed 'atomic-e00-src-v1-20260725' `
    -TimeControls @(
        'VSTC:2000:20:48',
        'STC:10000:100:24',
        'LTC:30000:300:12'
    )
if ($FullSchedule.Summary.schedule_sha256 -ne
        'bf3f51fae0bebf5b89d2304366218fcc97fdf1231b7bb31f2d089ebcf581f5fc' -or
    $FullSchedule.Summary.receipt_sha256 -ne
        '0b1af3158f0cbc3d62cd8e48d2dc059db338a3e4be987b2560097ce64c694d9b') {
    throw "full schedule differs from the frozen precommit derivation"
}

function New-E00Runtime {
    param(
        [string] $Name,
        [pscustomobject] $ScheduleBundle,
        [int] $MaximumWallSeconds
    )
    $root = Join-Path $DesignRoot $Name
    $stdout = Join-Path $DesignRoot "$Name.stdout.json"
    $stderr = Join-Path $DesignRoot "$Name.stderr.bin"
    Invoke-E00Cli -Arguments @(
        '-B', '-m', 'tools.atomic_mining.build_e00_runtime_manifest',
        '--output-dir', $root,
        '--source-root', $Repo,
        '--expected-source-commit', $Commit,
        '--expected-source-tree', $Tree,
        '--book', $Book, '--book-sha256', (Get-Sha256 $Book),
        '--engine', $Engine, '--engine-sha256', (Get-Sha256 $Engine),
        '--current-net', $CurrentNet,
        '--current-net-sha256', (Get-Sha256 $CurrentNet),
        '--teacher-net', $TeacherNet,
        '--teacher-net-sha256', (Get-Sha256 $TeacherNet),
        '--variant-config', $VariantConfig,
        '--variant-config-sha256', (Get-Sha256 $VariantConfig),
        '--pyffish', $Pyffish, '--pyffish-sha256', (Get-Sha256 $Pyffish),
        '--pyffish-build-manifest', $PyffishManifest,
        '--pyffish-build-manifest-sha256', (Get-Sha256 $PyffishManifest),
        '--runner', $ModulePaths.Runner,
        '--runner-sha256', (Get-Sha256 $ModulePaths.Runner),
        '--schedule', $ScheduleBundle.Schedule,
        '--schedule-sha256', (Get-Sha256 $ScheduleBundle.Schedule),
        '--schedule-receipt', $ScheduleBundle.Receipt,
        '--schedule-receipt-sha256', (Get-Sha256 $ScheduleBundle.Receipt),
        '--tools-init-sha256', (Get-Sha256 $ModulePaths.ToolsInit),
        '--atomic-mining-init-sha256', (Get-Sha256 $ModulePaths.AtomicMiningInit),
        '--common-sha256', (Get-Sha256 $ModulePaths.Common),
        '--schedule-builder-sha256', (Get-Sha256 $ModulePaths.ScheduleBuilder),
        '--uci-session-sha256', (Get-Sha256 $ModulePaths.UciSession),
        '--atomic-outcome-helper-sha256',
            (Get-Sha256 $ModulePaths.AtomicOutcomeHelper),
        '--binding-builder-sha256', (Get-Sha256 $ModulePaths.BindingBuilder),
        '--owned-process-sha256', (Get-Sha256 $ModulePaths.OwnedProcess),
        '--threads', '1',
        '--maximum-plies', '1024',
        '--command-timeout-seconds', '120',
        '--maximum-wall-seconds', ([string] $MaximumWallSeconds),
        '--maximum-game-wall-seconds', '1800',
        '--discovery-timeout-seconds', '120'
    ) -Stdout $stdout -Stderr $stderr
    $summary = Get-Content -Raw -Encoding UTF8 -LiteralPath $stdout |
        ConvertFrom-Json
    if (-not (Test-Path -LiteralPath (Join-Path $root 'build.receipt.json'))) {
        throw "runtime build commit marker is absent"
    }
    return [pscustomobject]@{
        Root = $root
        Manifest = Join-Path $root 'runtime-manifest.json'
        Summary = $summary
        MaximumWallSeconds = $MaximumWallSeconds
    }
}

$SmokeRuntime = New-E00Runtime -Name 'smoke-runtime' `
    -ScheduleBundle $SmokeSchedule -MaximumWallSeconds 3600
$FullRuntime = New-E00Runtime -Name 'full-runtime' `
    -ScheduleBundle $FullSchedule -MaximumWallSeconds 14400

function Invoke-E00Battery {
    param(
        [string] $ExperimentId,
        [string] $BatteryId,
        [pscustomobject] $ScheduleBundle,
        [pscustomobject] $RuntimeBundle,
        [string] $OutputRoot
    )
    $stdout = Join-Path $DesignRoot "$BatteryId.stdout.json"
    $stderr = Join-Path $DesignRoot "$BatteryId.stderr.bin"
    Invoke-E00Cli -Arguments @(
        '-B', '-m', 'tools.atomic_mining.run_e00_source',
        '--schedule', $ScheduleBundle.Schedule,
        '--schedule-receipt', $ScheduleBundle.Receipt,
        '--output-dir', $OutputRoot,
        '--experiment-id', $ExperimentId,
        '--battery-id', $BatteryId,
        '--runtime-manifest', $RuntimeBundle.Manifest,
        '--runtime-manifest-sha256', (Get-Sha256 $RuntimeBundle.Manifest),
        '--book', $Book, '--book-sha256', (Get-Sha256 $Book),
        '--engine', $Engine, '--engine-sha256', (Get-Sha256 $Engine),
        '--current-net', $CurrentNet,
        '--current-net-sha256', (Get-Sha256 $CurrentNet),
        '--teacher-net', $TeacherNet,
        '--teacher-net-sha256', (Get-Sha256 $TeacherNet),
        '--variant-config', $VariantConfig,
        '--variant-config-sha256', (Get-Sha256 $VariantConfig),
        '--pyffish', $Pyffish, '--pyffish-sha256', (Get-Sha256 $Pyffish),
        '--pyffish-build-manifest', $PyffishManifest,
        '--pyffish-build-manifest-sha256', (Get-Sha256 $PyffishManifest),
        '--runner', $ModulePaths.Runner,
        '--runner-sha256', (Get-Sha256 $ModulePaths.Runner),
        '--rules-source-root', $Repo,
        '--rules-source-commit', $Commit,
        '--tools-init-sha256', (Get-Sha256 $ModulePaths.ToolsInit),
        '--atomic-mining-init-sha256', (Get-Sha256 $ModulePaths.AtomicMiningInit),
        '--common-sha256', (Get-Sha256 $ModulePaths.Common),
        '--schedule-builder-sha256', (Get-Sha256 $ModulePaths.ScheduleBuilder),
        '--uci-session-sha256', (Get-Sha256 $ModulePaths.UciSession),
        '--atomic-outcome-helper-sha256',
            (Get-Sha256 $ModulePaths.AtomicOutcomeHelper),
        '--binding-builder-sha256', (Get-Sha256 $ModulePaths.BindingBuilder),
        '--owned-process-sha256', (Get-Sha256 $ModulePaths.OwnedProcess),
        '--threads', '1',
        '--maximum-plies', '1024',
        '--command-timeout-seconds', '120',
        '--maximum-wall-seconds',
            ([string] $RuntimeBundle.MaximumWallSeconds),
        '--maximum-game-wall-seconds', '1800'
    ) -Stdout $stdout -Stderr $stderr
    $receiptPath = Join-Path $OutputRoot 'receipt.json'
    if (-not (Test-Path -LiteralPath $receiptPath -PathType Leaf)) {
        throw "execution commit receipt is absent"
    }
    $receipt = Get-Content -Raw -Encoding UTF8 -LiteralPath $receiptPath |
        ConvertFrom-Json
    if ($receipt.schema -ne 'atomic-e00-execution-receipt-v2' -or
        $receipt.status -ne 'committed') {
        throw "execution receipt is not a committed v2 battery"
    }
}

# Immediately before this call: record a fresh generic CPU/RAM/pagefile/GPU
# sample and require the independent audit GO. Do not start any other worker.
Invoke-E00Battery `
    -ExperimentId 'atomic-e00-src-v2-20260725' `
    -BatteryId 'atomic-e00-src-v2-launch2-smoke' `
    -ScheduleBundle $SmokeSchedule `
    -RuntimeBundle $SmokeRuntime `
    -OutputRoot $SmokeOutput
```

Stop after the smoke. Independently recompute its design/runtime/input hashes,
pair/game count, engine/referee/verifier/process evidence, namespace guards,
inventory and `receipt.json`. Only after that second GO and a new resource
sample may the fixed full battery be invoked:

```powershell
Invoke-E00Battery `
    -ExperimentId 'atomic-e00-src-v2-20260725' `
    -BatteryId 'atomic-e00-src-v2-launch2-full' `
    -ScheduleBundle $FullSchedule `
    -RuntimeBundle $FullRuntime `
    -OutputRoot $FullOutput
```

Any nonzero exit, non-empty outer stderr, missing commit marker, hash mismatch,
unexpected namespace entry or incomplete owned-process proof makes that exact
root terminal. Do not recover, overwrite or reuse it.

### E00 v3 Launch3/Launch4/Launch5 terminal; Launch6 active

Launch3 failed closed before smoke because its PowerShell design inventory
serialized arrays with one unintended nesting level. It is terminal and
immutable. Launch4 completed sound smoke science, but failed closed because
Python text-mode stdout translated its canonical LF to CRLF on Windows; its
full root remains absent and forbidden. Launch5 also completed sound smoke
science, but its wrapper failed closed because Windows PowerShell 5.1
HTML-escaped literal `<empty>` option strings while recomputing a Python
canonical digest. Its full root likewise remains absent and forbidden.

Launch6 uses the corrected, committed, self-contained launcher documented in
[`../../e00-launch6-full-runbook.md`](../../e00-launch6-full-runbook.md).
It authenticates the exact clean source, full schedule/runtime design,
committed one-pair smoke and post-smoke independent GO before starting the
fixed 84-pair battery. It never rehydrates PowerShell state from this
historical document and never uses a Launch1 through Launch5 root.

## Pending gates

1. Commit the complete E00 implementation and record the exact clean
   commit/tree.
2. Execute the native, schedule and runtime builders above into fresh design
   roots and independently authenticate every receipt/inventory/hash.
3. Re-sample CPU/RAM/pagefile/GPU immediately before the one-pair
   `Threads=1` smoke; keep the OpenBench worker stopped.
4. Authenticate the smoke and, only on double GO plus a new resource sample,
   execute the sealed fixed-size E00-SRC battery.
5. Extract, split, probe, rank and select result-blind cohorts; then advance
   through tactical expansion, training and direct VSTC/STC/LTC LOS gates.
