# Atomic mining loop overnight journal — 2026-07-25

This journal is the short chronological handoff for the unattended execution
window.  The normative contracts and full evidence remain in the sibling
`README.md`, `../../mining-loop.md`, `../../e00-source-contract.md` and
`../../e00-direct-uci-referee-design.md`.

## Scope and invariants

- Objective: finish the Atomic mining/teacher-expansion experiment chain,
  beginning with E00-SRC V3 versus the run3b legacy champion.
- The official 1B DATAGEN campaign is read-only and must not be modified.
- The user-managed OpenBench worker must remain stopped.
- Existing unrelated processes are never stopped, reprioritized or inspected
  by command line.
- Real engine or GPU work starts only after an immediate generic resource
  sample and all scientific/provenance gates are green.
- Every source battery is fixed-size and result-bearing; no interim look or
  LOS optional stopping is allowed in E00-SRC.

## Timeline

### 00:35 CEST — first post-LoL sample

The user reported closing League of Legends.  A generic sample observed CPU
`27%`, RAM `23.62/31.92 GiB`, pagefile `22.49 GiB` used and GPU `33%`,
`1,378/10,240 MiB`.  This authorized bounded engineering work only, not a
future game launch.

### 00:58–01:00 CEST — native Atomic referee binding

The isolated native helper reached component-level `GO`:

- source commit: `01a74371d6c947bb03ae12a8cff6a35044d3aa0b`;
- fresh `pyffish.pyd`: `142,336` bytes,
  SHA-256
  `0869efbb6d0bacf1f5e0c193f6dd4ac2102706af17faa94787ae380350dd35bf`;
- source/toolchain build manifest SHA-256
  `f26a18b1327142b7c4fdc1d4fa917fb4cbbf3cec0027b1854857c1c42a037ceb`;
- `16` exact-path tests passed.

These are development artifacts.  The production binding must be rebuilt
after the final implementation is committed cleanly.

### 01:04 CEST — fresh resource gate

CPU was still `55.4%`, RAM `26.06/31.92 GiB`, pagefile `22,972 MiB` and GPU
`24%`, `1,365/10,240 MiB`.  The decision remained `NO-START` for real games.

### 01:05 CEST — independent binary runner audit

Verdict: `NO-GO`, despite `105` focal tests, `py_compile` and
`git diff --check` passing.

P0:

1. Gameplay/adjudication still depended on `python-chess`, not frozen
   `Atomic::outcome`.
2. `SimpleEngine.play()` could reset non-managed UCI options.
3. first-ply timing charged setup/protocol work.
4. cleanup proved wrapper death, not zero owned descendants.
5. the extractor launched a mutable original engine path.

P1:

- exact equality boundary not frozen;
- receipts did not fully identify executed snapshots/VariantPath policy;
- no independent final semantic reconcile;
- inherited child environment/runtime identity;
- incomplete helper manifest and optional-result validation;
- missing extractor caller trust anchor/deep source authentication;
- swallowed UCI cleanup failures;
- possible temporary-file leak on stderr-open failure.

No real engine was launched.

### Active correction streams

1. Direct-UCI E00 runner with exact pyffish legality/outcome, setup outside
   clocks, immutable inputs and Windows owned-tree containment.
2. Extractor create-new content-addressed executable snapshot plus explicit
   execution-receipt trust anchor.
3. Result-blind downstream identities, split/rank/selection and metamorphic
   tests.

### 01:10 CEST — correction-stream checkpoints

- Direct UCI: first/second search, persistent options, setup outside the
  charged interval and complete-`bestmove` timestamp tests reached `25/25`
  PASS.  The planned outer containment is
  `CREATE_SUSPENDED -> Job Object assignment -> resume`, with
  kill-on-close and an `ActiveProcesses == 0` postcondition.
- Extractor: source authentication now starts from a caller-supplied expected
  execution-receipt SHA.  Deep validation covers the exact snapshot set,
  content-addressed names, runtime manifest, execution/options/network policy,
  runtime modules and clean source identity.  Publication is being separated
  from process close and snapshot cleanup so a cleanup failure can never
  create an accepted receipt.
- Downstream: split, probe, stability, rank and cohort modules now use
  pre-result structural identities.  A five-case metamorphic suite passed:
  changing a valid result and every result-derived provenance ID left
  structural split/rank/selection invariant, while bypass and stale/extra
  metadata failed closed.  Legacy focal suites were `45/47` before two
  fixture-only corrections and are being rerun.
- Browser Oracle attempts for these bounded follow-ups did not deliver usable
  answers (closed/disconnected browser or prompt-commit timeout).  No advice
  is attributed to Oracle and no API fallback was used.

### 01:13 CEST — result-blind downstream closed

The downstream hardening stream completed without real probes or matches:

- focal suite: `101/101` PASS;
- result-mutation metamorphic/adversarial suite: `5/5` PASS;
- `py_compile`: PASS;
- expanded mining suite at handoff: `215` PASS / `10` FAIL, with all ten
  failures confined to extractor fixtures that had not yet supplied the new
  mandatory execution-receipt trust anchor.  The extractor owner accepted
  that handoff.

Final module hashes:

- `split_components.py`
  `8bd781dfce41b58e1fdd0e2c0d272ec1b15243b1eeae08b540821621577df36b`;
- `probe_disagreements.py`
  `855ddf9d5479e7d54df603f3483f7b72b395a7c812d02ec998885332a6f31f27`;
- `probe_label_stability.py`
  `7cf65669faf48b623db192a65da4f67ec1a2dfe7b343beae6cd1dd0455e8713e`;
- `rank_disagreements.py`
  `b3f7dc006eccc597261c010eceb0b878fc6c38c3a98efd90a1bdb59de3d71a45`;
- `select_confirmatory_cohorts.py`
  `3fa8e289ce91634575dca6936ea0f84b98a98a9e3a7f60b1866c3e8ce65b36cc`;
- `test_atomic_mining_result_bearing_downstream.py`
  `770f278331e8cba370cee705895a8acffeccd7378880a0b6ffb99030f2f40595`.

The accepted invariant is now explicit: source outcome and every
outcome-derived trajectory/source/position identifier are provenance only.
Components, partitions, features, ordering, caps and cohort sampling use
pre-result structural identities and fail closed on stale, extra or bypassed
outcome metadata.

### 01:23 CEST — independent downstream audit

Binary verdict: `NO-GO`, P0=`0`, P1=`2`.

1. The probe accepted a structurally valid assignment whose
   `component_id` was replaced by `f...f` and whose partition was changed from
   `CONF` to `CAL`, because it did not require the sealed split receipt and
   exact artifact membership.
2. The cohort selector recomputed row metrics but trusted a supplied,
   contiguous `global_rank`; swapping ranks `1` and `4` was accepted and
   changed the TOP component.

The underlying structural-game/position derivation and leakage edges remain
sound.  Corrections are now required to authenticate the split receipt and
CAL/CONF artifact membership, and to reconstruct/rerun the complete canonical
ranker before accepting any ranked input.  Swap, permutation, stale-rank and
assignment-mutation adversarials are mandatory.  No real science consumed
the rejected version.

### 01:18 CEST — production-launch preflight

A separate read-only preflight found sufficient disk but no execution window:
CPU `62%`, RAM `26.18/31.92 GiB`, pagefile approximately
`26.83/95.99 GiB`, GPU `34%`, `1,366/10,240 MiB`, `46 C`,
`28.28 W`.  Verdict: `NO-START`.

The production order is frozen as:

1. integrated suite and independent P0=`0`/P1=`0`;
2. clean commit;
3. serial native-binding rebuild from that commit;
4. fresh create-new one-pair smoke schedule;
5. fresh create-new `84`-pair source schedule;
6. one distinct runtime manifest per schedule;
7. rehash plus immediate resource gate;
8. one-pair T1 smoke and complete authentication;
9. another resource gate;
10. full `84` pairs / `168` games.

The full schedule must reproduce SHA-256
`bf3f51fae0bebf5b89d2304366218fcc97fdf1231b7bb31f2d089ebcf581f5fc`
and receipt
`0b1af3158f0cbc3d62cd8e48d2dc059db338a3e4be987b2560097ce64c694d9b`
from `6,199` unique roots.  The development copy under `%TEMP%` is not
production evidence: it is writable, not bound to the final commit/tree and
exists only to prove deterministic generation.

The engine, V3, run3b, V3 receipt, variants and book were rechecked read-only
against their previously recorded sizes and hashes.  The historical
`variantfishtest_new1.py` remains a fixture only and will not execute.

## Next admissible transition

The next transition is not “run another launch”.  It is:

1. all three correction streams pass focused and integrated tests;
2. an independent audit returns binary `GO`, P0=`0`, P1=`0`;
3. commit a clean implementation;
4. rebuild and seal production runtime artifacts from that commit;
5. re-sample resources;
6. execute exactly one `Threads=1` pair as the real-engine smoke.

Only an authenticated smoke permits the fixed `84`-pair / `168`-game E00-SRC
battery.

### 01:42 CEST — post-LoL resource sample and hardening checkpoint

The user closed League of Legends.  A fresh generic, process-name-free sample
reported CPU `19%`, RAM `24.98/31.92 GiB`, committed memory
`53.89/127.91 GiB`, pagefile use `22,989 MiB`, GPU `29%`,
`1,370/10,240 MiB`, `46 C` and `29.30 W`.  Free space was
`124.4 GiB` on C, `128.5 GiB` on D and `1,126.9 GiB` on F.

This is materially quieter on CPU, but it did not authorize a launch: the
runner wire and independent audit were still open, and the GPU sample was
above the conservative candidate threshold used in prior preflights.  No
engine, probe, trainer or worker was started.

Two additional trust-boundary requirements were raised before wire freeze:

1. each `go` must preserve the exact Stockfish
   `NNUE evaluation using <backend> <exact snapshot path>` success marker
   before its `bestmove`, rejecting missing, duplicate, classical, error or
   wrong-path markers; and
2. every executable/input/runtime snapshot must be create-new, read-only,
   non-reparse and identity-stable immediately before launch and after close.

The downstream probe/cohort implementer also closed the two prior P1 findings:
the probe now authenticates the sealed split receipt and exact CAL/CONF
membership, while the selector reconstructs the canonical global ranking and
binds canonical metrics to it.  Its focal suite was `45/45` PASS.  Independent
re-audit remains required before GO.

### 01:51 CEST — downstream P1 re-audit GO

An independent re-audit returned binary `GO`, P0=`0`, P1=`0` for the two
previously rejected downstream edges.  Independent tests were `40/40` PASS
for probe plus cohorts and `61/61` PASS with the ranker included.

- The public single-position probe rejects globally partitioned rows.
- The file probe requires the caller-trusted SHA of the canonical scientific
  split receipt and exact CAL/CONF path, digest, size, row count, node and
  component manifests, provenance and zero-overlap evidence.
- Probe execution consumes only the authenticated in-memory snapshot.
- The cohort selector reconstructs the complete deterministic ranking,
  requires exact canonical order and `global_rank`, authenticates canonical
  metrics with a caller-trusted SHA, binds them to the ranked snapshot and
  recomputes the metrics.

The audit also found an adjacent pre-execution issue that does not reopen
those two P1s but is material before real probes: the probe CLI hashes the
engine and networks and later reopens their original mutable paths.  A
replace/execute/restore race could therefore differ from the authenticated
bytes.  Real probes remain blocked until they use the same create-new,
read-only, non-reparse, identity-stable executable/network snapshot policy as
E00.  This did not consume any scientific probe.

### 02:02 CEST — Python runtime boundary frozen

The runner audit found that isolated children still launched the original
`sys.executable` while the Python executable and runtime libraries were only
sampled before and after the battery.  A transient replacement could therefore
evade those two samples.  The accepted pragmatic boundary avoids copying the
multi-gigabyte Python installation:

1. hold no-write/no-delete identity guards for `sys.executable`, every Python
   runtime DLL and every file-backed module imported by the closed child
   runtime;
2. require each child to return the complete canonical file-backed inventory,
   with exact normalized path, identity, hash and size, and reject any missing,
   extra or duplicate entry;
3. revalidate the same inventory after execution and prove clean guard close;
4. reject late imports outside the inventory before science or publication;
5. test replace/delete/rename/restore attempts while the locks are live; and
6. state the residual TCB honestly in the receipt: the OS loader/kernel and
   standard-library code that is neither imported nor executed.

The independent auditor considers this sufficient for P0/P1 closure if all six
conditions and their adversarial tests pass.  A 6 GiB stdlib/site-packages copy
is explicitly not required.

### 02:13 CEST — probe execution boundary GO

The replay/probe hardening stream is now independently closed with binary
`GO`, P0=`0`, P1=`0`.

- Engine, V3 and run3b are copied create-new into an authenticated snapshot
  directory; only those snapshot paths can reach the UCI command line.
- Live Windows handles use `GENERIC_READ` with read-only sharing and retain
  no-write/no-delete namespace locks for the directory and all three files
  until both UCI contexts close cleanly.
- BY-HANDLE identity, path reopen identity, size and SHA-256 are revalidated
  around launch and search.
- The receipt binds the original, snapshot, actually executed file and guard
  evidence.
- Real Windows write/delete/rename/replace attempts were blocked `13/13`; a
  forced guard-close followed by transient write/restore aborted, retained
  evidence and published zero science.

Independent focal tests were `35/35` PASS and the probe plus downstream suite
was `99/99` PASS.  The frozen implementation hashes are:

- `probe_disagreements.py`: `3339bf09f4734df68143e3ee9fe4b4b05e63398c416990d7fe1fecc2e7dee374`
  (`86,064` bytes);
- `test_atomic_mining_probe.py`:
  `3a90a9e68f79670529456c2e337a34511dbe8adcd408485ef258a3633fbb20a7`
  (`49,337` bytes).

No real engine, probe, trainer, generator or OpenBench worker was started.

### 02:16 CEST — exact float-checkpoint route located

Read-only inspection recovered the exact trainer boundary rather than
guessing from the exported `.nnue`:

- trainer repository:
  `variant-nnue-pytorch-v3-repair`;
- trainer commit:
  `87ab94ccc549bf159a9d5950881ba8a1a9f8e3d6`;
- tree:
  `3b4bb977a48cf5a1ce780a7bf07270f71f0c62c9`;
- production entrypoint:
  `train_atomic_v3.py`;
- float checkpoint:
  `D:\NNUE training\Atomic-v2\campaign-28eaed5-high-lambda\lambda-100\runs\lambda-100\last.ckpt`,
  `1,285,346,662` bytes,
  SHA-256 `7055a51a8df63a77e2a9f690e6d0e5a3194f80a1938ccad3040b3bc77239d8c5`;
- provider:
  `training_data_loader.dll`,
  SHA-256 `4a6042978ba1a9b7540bb4f4f7a8ee178eff5060a4993f49d230697644bf1230`;
- final launch manifest:
  `resume800-launch-manifest.json` (`4,946` bytes).

The existing launcher authenticates an exact bootstrap receipt and provider,
and its normal `--resume` path requires unchanged run/dataset/provider/shared
state bindings.  Therefore no future score-only cohort will be substituted
under the legacy bootstrap receipt.  The minimum admissible route is a new
reviewed dataset contract and explicit transfer/fine-tune boundary from the
authenticated float checkpoint; its exact command remains blocked until the
post-E00 cohort format and sizes are known.

### 02:19 CEST — resource gate remains closed

A generic sample after the user closed League of Legends reported CPU `35%`,
RAM `25.17/31.92 GiB`, committed memory `54.01/127.91 GiB`, pagefile use
`22,946 MiB`, GPU `34%`, `1,360/10,240 MiB`, `46 C` and `27.89 W`.
Free space was `124.2 GiB` on C, `128.1 GiB` on D and `1,126.9 GiB` on F.

The sample did not inspect or alter unrelated command lines or processes.  The
GPU was still above the candidate threshold and the E00 runner was not wire
frozen, so the execution gate remained `NO-START`.  No worker or scientific
process was launched.

### 02:25 CEST — score-only training boundary audit

A separate read-only trainer audit closed the initialization question but
returned `NO-GO` for using the existing bootstrap CLI on future cohorts.

The authoritative completed launch was `resume800-clean3`; the earlier
`resume800-launch-manifest.json` inspected above is historical launch evidence,
not the final successful-launch manifest.  The latter is `2,519` bytes with
SHA-256
`96bb82368578c8af5b902062d462ab4312396e55ea7cbd798f01348e1705e5e2`.
Final stdout is `13,269` bytes with SHA-256
`8eea7ebaad3e70c60dc0318d4ffcc9a6ac00267739e649d0fdd345b26dea36c8`.

The existing `load_last_checkpoint` contract requires exact equality of
configuration, bootstrap dataset and commits, then restores the full model,
optimizer, scheduler, provider cursor, RNG and counters.  Consequently:

- `--resume` must never be pointed at MINED/RANDOM/REPLAY;
- `--resume-from-trainer-commit` changes only the trainer commit and does not
  authorize a new dataset;
- Atomic BIN V2 cannot represent an absent counterfactual result, and
  fabricating `result=0` remains a kill condition even under lambda `1.0`.

The reviewed minimum downstream route is a separate
`atomic-v3-score-only-v1` lane.  It will authenticate the V3 final receipt and
float checkpoint, validate the complete checkpoint document, load only
`model_state` with `strict=True`, recompute its tensor digest, and create fresh
Ranger, StepLR, RNG, providers, cursor and counters for every cohort/seed.
The batch and loss will contain the signed side-to-move teacher score but no
outcome tensor or result sentinel.  Resume will be permitted only inside the
same sealed score-only fine-tune.

This lane is deliberately not implemented before E00: cohort sizes, retained
label domain and equal compute budget must be derived from the sealed
confirmation result.  No GPU allocation was made.

### 02:31 CEST — packaging P0 caught before production

An independent packaging audit found that the scientific components had no
complete reproducible command path:

- the deterministic schedule builder has a create-new CLI;
- `build_atomic_outcome_binding.py` exposes only a Python function and has no
  CLI;
- no producer exists for `atomic-e00-runtime-manifest-v2`;
- existing runner tests construct a manifest through `FakeRuntimeProbe`;
- the required child-import inventory is precommitted by the manifest but the
  default runtime probe consumes rather than discovers it.

This is an operational P0: no ad-hoc JSON or interactive Python snippet may
bridge clean commit -> native binding -> runtime manifest -> runner.  A
separate reviewed builder with create-new outputs, closed child introspection,
strict hashes and end-to-end CLI tests is now required before smoke.

The first real stub integration also exposed a Windows lock bug before any
engine ran: every guarded artifact attempted an exclusive delete lock on the
same parent directory, so the second module in one directory failed with
Win32 error `32`.  The runner remains unfrozen while directory locks are
deduplicated/reference-counted within the execution scope.  This is
development evidence only; no scientific root was created.

### 02:34 CEST — Oracle review attempted, response unavailable

A minimal browser-only Oracle review was submitted for the runtime-manifest
discovery boundary and score-only weights-only initialization.  Oracle
`0.16.0` recorded picker evidence:

- requested model `gpt-5-pro`;
- resolved label `Pro`;
- picker status `already-selected`;
- verification `true`;
- prompt submitted in the `Atomic-Stockfish` ChatGPT project.

The response began streaming, but the hidden Chrome session disconnected
before Oracle could harvest it (`chrome-disconnected`).  Reattachment/harvest
did not recover a stored answer.  No API fallback was used and no advice is
attributed to the incomplete run.  Local independent P0/P1 audit therefore
remains authoritative.

### 02:42 CEST — exact inventory discovery hook integrated

The runtime circularity now has a result-blind implementation hook:
`--internal-discover-runtime`.  It uses the same isolated `-I` bootstrap,
`OwnedProcess`, regular-file stdio, runtime-package snapshot and
`runpy(..., run_name="__main__", alter_sys=True)` semantics as the real play
and verifier children.  Its request authenticates the nine runtime modules,
exact `pyffish`, build manifest and rules source, but contains no engine,
network, position, move or result.

The discovery baseline equals `_assert_child_import_inventory`; a late import
is rejected.  Runner focal tests were `79/79` PASS and the complete mining
selection was `276/276` PASS.  The runner is intentionally not yet wire frozen:
the separate production builders and a real end-to-end
discovery/play/verifier inventory-equality smoke remain required.

To break the gate circularity, the packaging stream was authorized to add only
the two separate builders and their tests against this hook.  It may not edit
the runner or start an engine.

### 02:48 CEST — transient loader-namespace P1 rejected

The independent auditor rejected before/after directory enumeration as the
sole protection for static input, native and executed-runtime directories.
A transient create/load/delete or namespace swap could affect Python/native
loader resolution while restoring the same final enumeration.

The accepted policy is two-phase:

1. construct and authenticate the complete directory;
2. acquire strict post-construction namespace seals and keep them live through
   inventory discovery, play, verification and clean child close.

The native smoke must attempt transient file creation, rename and replacement
inside every loader-visible namespace and prove fail-closed behavior.  This P1
was caught before wire freeze and before any scientific execution.

### 02:54 CEST — namespace-create threat model made explicit

The Windows directory handle was empirically shown to block rename, delete and
link operations but not creation of a new child file.  ACL mutation or a new
single-file import architecture would be disproportionate and crash-prone for
this experiment.

The independent auditor reclassified this from P1 to residual P2/TCB after the
actual threat model was stated explicitly:

- the host and unrelated processes are cooperative, not an arbitrary local
  attacker;
- authenticated code performs no unknown dynamic import;
- every executed Python module already exists and its exact file is held
  no-write/no-delete;
- Python runs with `-I`;
- `pyffish` is loaded by exact authenticated path;
- exact imported-module inventories are checked before/after critical work and
  any late import aborts.

Under those premises a newly created file is not causally executable merely by
existing.  Existing namespace locks remain defense in depth, but inability to
deny arbitrary child creation does not block wire freeze or science.  No ACL
or bundle redesign is authorized in this timebox.

### 03:01 CEST — E00 wire frozen candidate

The runner declared `WIRE FROZEN CANDIDATE`.  No further wire edit is permitted
unless the independent audit finds a P0/P1.

- Complete scoped Atomic-mining suite: `292/292` PASS.
- Python `compileall`: PASS.
- Focused native/strict integration: PASS.
- Direct runner surface contains zero `python-chess` gameplay/referee use.
- Native build-root files are individually held by exact live locks and its
  inventory is checkpointed in the receipt.
- The inability to deny creation of an otherwise unreferenced child entry is
  recorded as the residual cooperative-host P2/TCB described above.

One full-repository collection failure is an unrelated ambient `pyffish` ABI
mismatch in the legacy `test_pyffish` surface; it does not enter the isolated
mining selection or runtime package.  It is not being repaired as part of
E00.

The frozen wire now unblocks v2-only extractor synchronization and the final
independent P0/P1 audit.  Real native build and engine smoke remain post-clean-
commit execution gates.

### 03:10 CEST — premature commit-marker P1 caught

The first independent final audit passed `298/298` scoped tests, `18/18`
`py_compile` targets and diff-check, then found a systemic publication-order
P1:

- `run_battery` wrote the committed execution receipt before its outer guard
  decorator closed every file/directory handle;
- `build_runtime_manifest` likewise wrote its build receipt before decorated
  cleanup completed.

If success-path cleanup failed, the CLI would return NO-GO while leaving a
valid-looking commit marker.  This violates the rule that the receipt alone
means a completely clean terminal success.

The only authorized refactor is to separate prepared result state from commit:
close and verify all guards first, then write the create-new receipt as the
last action.  Adversarial tests must force cleanup failure after an otherwise
successful body and prove that no receipt exists.  The candidate wire is
reopened solely for this P1.

The same commit-marker anti-pattern was then found by a static sweep in the
schedule builder, component splitter and E00 extractor: each published its
receipt before one final reopen/hash operation.  The fix is being applied
uniformly rather than one-off:

- packaging owns runtime-manifest, schedule and split receipts;
- the E00 auditor owns runner and extractor receipts;
- the final auditor remains read-only and is sweeping every
  receipt/commit-marker write site.

No result-bearing execution is allowed until every producer proves that a
failure in any precommit validation or success-path cleanup leaves no commit
marker.

### 03:32 CEST — final double audit GO

The marker-last repair is complete across every result-bearing producer.
The final selector defect was also caught before execution: its top/random
cohort files could previously survive a failure after the receipt write.
The selector now owns both data files in one rollback scope and publishes its
receipt only after all reopen/hash checks, as its final user-space operation.
An injected failure after the random cohort write proves that all three files
remain absent.

Independent final verdicts on the frozen 40-file snapshot:

- binary `GO`, `P0=0`, `P1=0`;
- `306/306` integrated tests PASS;
- `18/18` Python compile targets PASS;
- `git diff --check` and every PowerShell runbook AST PASS;
- stable double SHA inventory over five seconds;
- build, schedule, execution, extraction, split, dual-probe and cohort
  receipts are marker-last;
- no `python-chess` gameplay, optional stopping, result leakage,
  OpenBench/DATAGEN interaction or worker control is present.

The only declared residual is the already accepted P2/TCB possibility of an
operating-system failure after a hardlink publication in `write_new_bytes`.
It is outside the P0/P1 gate under the cooperative-host threat model.

The root validation independently repeated the complete suite:
`306/306` PASS in `51.87 s`. The pre-validation generic resource sample was
CPU `34%`, RAM `25.09/31.92 GiB`, committed memory
`53.80/127.91 GiB`, pagefile `22,964 MiB`, GPU `27%`,
VRAM `1,297/10,240 MiB`, `45 C` and `29.20 W`; free space was
`123.7 GiB` on `C:`, `128.0 GiB` on `D:` and `1,126.9 GiB` on `F:`.
This authorizes the lightweight validation only. A fresh resource sample and
complete production-input rehash remain mandatory immediately before native
build or engine execution. The OpenBench worker remains stopped.

### 03:38 CEST — operational preflight stopped before root creation

The first post-commit runbook invocation stopped at the input-hash gate before
creating any design, smoke or full root. Diagnosis showed a documentation-only
transcription error: the expected `atomic.epd` SHA-256 literal had 63
characters because its final `e` was missing. The actual book remained
byte-identical at `394,785` bytes and SHA-256
`28ed51c2f42e723d5e127d2d3f21c0bfa4a9b318615afdb299b93ea62dea2b1e`;
all other pinned inputs also matched.

Only the missing final character in the runbook was repaired. No native build,
engine, schedule, runtime package or scientific output was created. The three
launch1 roots remain absent and must pass the same no-reuse check after this
documentation fix is committed into a new clean source identity.

### Launch1 runtime-package bytecode P1 — terminal before science

The fresh Launch1 native build and both schedules completed, but the first
runtime-manifest builder failed closed. Its outer stderr was exactly 99 bytes,
SHA-256
`1c7d2763bcb30cdea58054464367c3ae40abc560e977c6951af3cefbdeefd92d`,
with:

`runtime manifest NO-GO: strict namespace enumeration changed for runtime builder executed package`

Read-only forensics authenticated the cause. The builder sealed and inventoried
the staged nine-module runtime package, then `DirectUciBackend` started its real
child as `python -I -c ...` while relying on
`PYTHONDONTWRITEBYTECODE=1`. Python isolated mode implies `-E`, so it ignored
that `PYTHON*` environment variable. Importing the authenticated package
created two `__pycache__` directories and nine `.pyc` files approximately one
second after the nine source files were staged. All nine staged `.py` files
still matched the clean source byte-for-byte. The strict namespace guard then
detected its own child's additions exactly as designed.

This is a deterministic operational P1, not a P0: it blocks every real runtime
build, but it failed closed before `runtime-discovery.receipt.json`,
`runtime-manifest.json` or the final `build.receipt.json` existed. No engine or
result-bearing science started. The Launch1 design root is terminal, frozen and
must never be recovered, overwritten, edited or reused.

The minimal authorized repair is an explicit interpreter `-B` beside `-I`.
The environment variable remains defense in depth; the namespace guard is not
weakened and bytecode files are not added to the accepted inventory. A
structural command test freezes the exact `-I -B -c` order, and a real isolated
child regression imports a freshly staged runtime package, then proves its
recursive inventory remains identical with no `__pycache__` or `.pyc`.

The next attempt uses only fresh Launch2 design/smoke/full roots and Launch2
battery IDs after a new clean commit, focused and integrated serial tests,
independent zero-P0/P1 audit and a new resource sample. Launch1 remains
immutable evidence.

Independent and root closure both returned binary GO for the four-file fix:
`P0=0`, `P1=0`. The independent suite passed `308/308` in `49.72 s`;
the root repeated `308/308` in `52.26 s`. Both new regressions pass, including
the real isolated child with an unchanged recursive inventory and no bytecode
artifacts. Python compilation, diff-check and both runbook PowerShell ASTs
pass. The pre-validation resource sample was CPU `23%`, RAM
`24.85/31.92 GiB`, committed memory `53.51/127.91 GiB`, pagefile
`22,958 MiB`, GPU `37%`, VRAM `1,305/10,240 MiB`, `46 C` and
`28.17 W`. This sample authorized only the serial validation; Launch2 still
requires a fresh immediate pre-execution sample after its clean commit.

### 04:05 CEST — Launch2 terminal before an accepted leg

Launch2 passed the clean-source preflight, exact native build, smoke/full
schedule creation and both isolated runtime-manifest builds. The following
create-new design evidence was committed before the smoke:

- native binding SHA-256
  `0869efbb6d0bacf1f5e0c193f6dd4ac2102706af17faa94787ae380350dd35bf`
  (`142,336` bytes);
- native manifest SHA-256
  `a107103092f2d0a96c0e4e0dfff00be003655def71aec69fa408e4c7c3d4a3b5`;
- smoke runtime manifest SHA-256
  `dbdb2a42a99759140d4d4735becc059a8a771d520a1e5d9c48c1145fc47f0d1d`;
- smoke discovery/build receipt SHA-256 values
  `dc76ae9cf71eccba15b132f6057311f8f9dd9c5fe92bb609adaed8a5587fbf31`
  and
  `f54d9ac7ce7a043baf2c753c20c5f7f0a22f561e2ecfddb8a2a9396281fdf1ae`;
- full runtime manifest SHA-256
  `90223b330ee4cb74501b25116f0d74e5ea5663281572b9f2d32f6920aa459ead`;
- full runtime build receipt SHA-256
  `7d7be3c1ad3cad21d24cbe079aa1e79276625d7f1107dacdd79c315981b657ea`.

The first smoke pair then failed before either leg became complete. Outer
stderr was exactly 51 bytes with SHA-256
`b554f790f472c8cd768525b54da4cb8dce9ef210fc772d3ddde490522b936d8e`;
the create-new rejection was 261 bytes with SHA-256
`3e4e95fc00f84958803d10019859f8756069cb2a85db5de8ae145c34bc2c99d1`
and recorded `completed_legs=0`. No execution receipt was published and the
full root remained absent.

Read-only reproduction proved the first rejected line was the engine's
non-UCI startup preamble:

`Atomic-Stockfish 1.0.3 by the Atomic-Stockfish developers (see AUTHORS file)`

The engine then emitted the exact `id name`, exact `id author`, and one empty
separator line before its first `option`. The sealed generic UCI parser
rejected the preamble; after accepting that line it would also have rejected
the separator. This is an operational handshake-contract defect, not a
scientific result. The Launch2 design and smoke roots are terminal and
immutable; the Launch2 full root must never be created or reused.

### 04:28 CEST — exact handshake and classified-child v3 candidate

The minimal repair requires the exact authenticated startup preamble, exact
`id name` then exact `id author`, and exactly one empty post-ID separator
before any option. Omitted, changed, duplicated, reordered, moved,
whitespace-only and type-coerced variants fail closed. A direct real-engine
`Threads=1`, `go nodes 1` preflight passed for both exact networks with
byte-empty stderr and the expected backend markers:

- current V3: `AtomicNNUEV3`, best move `g1f3`;
- run3b teacher: `Legacy Atomic V1`, best move `g1f3`.

The immediate diagnostic-only resource sample was CPU `19%`, RAM
`25.74/31.92 GiB`, GPU `10%`, VRAM `1,346/10,240 MiB`, `53 C` and
`93.72 W`.

Because the accepted row now carries exact handshake evidence, the public
wire is explicitly versioned as `atomic-e00-game-v3`,
`atomic-e00-engine-evidence-v3` and
`atomic-e00-execution-receipt-v3`. The downstream extractor accepts that
dialect only and rejects the real v2 predecessor before replay.

Isolated child exit 70 now produces a separate canonical,
request-bound `atomic-e00-internal-failure-v1` file with an allowlisted mode,
stage, failure code and exception type. It never serializes exception text,
paths or tracebacks. The parent requires result/failure exclusivity and
validates natural-zero owned-process evidence before publishing a terminal
`atomic-e00-rejection-v2`.

The focused runner/UCI/extractor selection passes `97/97`. Independent audits
have already caught and closed bool-versus-int coercion, digesting the expected
rather than observed handshake, unbound failure stages, stale v2 extractor
acceptance, unvalidated failure process evidence, identity-order ambiguity and
schema-version ambiguity. This remains a candidate, not execution authority,
until the self-contained Launch3 full-only launcher, documentation, complete
suite and both independent audits return zero P0/P1.

### 04:40 CEST — Launch3 code/packaging double GO

Two independent read-only audits of the complete v3 candidate returned
`GO`, `P0=0`, `P1=0`. Each independently ran the 112-test focal selection;
both runs passed. PowerShell and Python AST checks passed, and
`git diff --check` reported no defect (only the repository's expected
LF-to-CRLF notices).

The audited candidate binds the exact startup preamble, ordered
`id name -> id author` identity, single separator line, v3 game/engine/
execution evidence, canonical classified child failures, result/failure
exclusivity, owned-process cleanup evidence, clean source commit/tree,
full schedule/runtime hashes, a committed one-pair smoke and create-new raw
captures outside the sealed design root. The auditors explicitly authorized
the commit/seal workflow only; execution still requires a hash-pinned
post-smoke authorization receipt, a fresh-shell read-only validation and an
immediate resource gate.

After the user reported closing League of Legends, a new generic sample
observed CPU `26.5%, 33.8%, 41.6%`, RAM `25.60/31.92 GiB`, pagefile
`22,890/98,295 MiB`, GPU `31%`, VRAM `1,331/10,240 MiB`, `46 C` and
`27.66 W`. Free space was `123.6 GiB` on `C:`, `128.0 GiB` on `D:` and
`1,126.8 GiB` on `F:`. A process-name plus E00-command filter found zero
E00 Python/engine processes. This sample is documentary only because the
source is not yet committed and the Launch3 design does not yet exist; the
one-pair smoke will receive a fresh immediate sample.

### 05:09 CEST — final pre-seal audits correctly revoke GO

The two independent final audits both returned `NO-GO`, `P0=0`, after finding
the same remaining `P1`: the prepare/smoke launcher authenticated hashes,
counts, legs and the engine handshake, but did not independently validate the
complete accepted-game result, referee/verifier and owned-process evidence.
One audit also demonstrated a separate type-integrity gap: PowerShell JSON
comparisons and casts could accept `1` for `true` and numeric strings for
integer or floating fields. The launcher digest then reconstructed expected
typed values instead of proving the observed JSON types.

This is a successful fail-closed interception. No Launch3 root, native build,
engine invocation or scientific output exists. The candidate remains
uncommitted. The required repair is limited to strict CLR-type predicates,
complete exact-field validation for both smoke game rows, exact natural-zero
owned-process evidence for the leg and verifier child, non-empty/exact
artifact key sets, and adversarial mutation/omission fixtures. A fresh double
audit is mandatory after the repair.

The ChatGPT Pro Oracle review in the `Atomic-Stockfish` project completed after
`10m 39s`. Its critical-path advice agrees with the local inventory:

- E00 may execute while sealed, but no E00 outcome may be opened before the
  downstream mining design and trust anchors are frozen.
- Before opening E00, authenticate real `REPLAY` and opaque `DENY_E05`
  manifests, bind caller-supplied hashes in the split receipt, and publish a
  pre-probe design receipt.
- Post-bestmove children inherit the parent's component; they never reopen or
  merge the split. A post-generation global leakage audit rejects a child that
  touches another component, `REPLAY`, or `DENY_E05`.
- Exact labels require `go depth 7`, legal PV replay and explicit proof that
  the returned lane reached depth 7.
- Score-only training needs a new semantic schema/provider with
  `game_result_present=false`; fabricating a draw is a kill condition. The
  existing binary container may be reused only if it can carry that absence
  without a sentinel result.
- V3 initialization must load model weights only, with fresh optimizer,
  scheduler, RNG, provider, cursor and counters.
- The confirmatory gate must bootstrap component groups, and the later match
  gate must commit complete colour-swapped pairs and support both `0%` and
  `100%` LOS boundaries.

The Oracle therefore returns `GO` for sealed E00 execution and `NO-GO` for
opening its outputs until the downstream pre-result implementation and design
receipt are complete.

### 05:49 CEST — final Launch3 candidate receives double GO

The final type- and evidence-exact candidate is frozen at:

- prepare/smoke launcher
  `65cb19089861086391666e6a61503840442a77d9f49b427423669407f85174fb`;
- prepare/smoke tests
  `a9afcfd56e1734398acb547943268df4ff1d30845f7bd6ff323e369a617b8942`;
- prepare/smoke runbook
  `8bd61818866017a1db9a96d1362cff25a47796b4cce7bb62b5832791cedaa80a`;
- full launcher
  `a774656f0de38b0e08aad43c473f46f59b66a4817d490448a829a5a0ed06cceb`;
- full tests
  `d00853c1e7c48b806de8c2f5207fa11738a2fd0d0794198cc7fae00503b29991`;
- full runbook
  `1f82fdb4c06f92216b797ebae092c8665850cd9ba3c765daba6ef3f97bd324f4`.

Two independent read-only audits returned `GO`, `P0=0`, `P1=0` on those
exact bytes. The first passed `181/181` integrated tests and `56/56`
launcher tests; the second repeated the complete `382/382` atomic-mining
suite and the same 56 launcher cases. Both PowerShell ASTs, all changed
Python ASTs and `git diff --check` pass. The root independently repeated
`382/382` in `100.09 s`.

The repaired boundary rejects PowerShell scalar coercion, validates the full
two-row smoke game/referee/verifier/owned-process wire, requires exact
non-empty artifact maps, and treats the later canonical authorization receipt
as the explicit deep-smoke audit authority for the full launcher. The full
launcher in turn requires complete type-exact bindings for its own result.

The pre-test resource sample was CPU `16.2%, 18.7%, 17.5%`, RAM
`25.82/31.92 GiB`, pagefile `22,829 MiB`, GPU `29%`, VRAM
`1,302/10,240 MiB`, `46 C` and `29.36 W`; free space remained `123.3 GiB`
on `C:`, `128.0 GiB` on `D:` and `1,126.8 GiB` on `F:`. Four unrelated
Stockfish-named processes were visible and were not inspected, stopped or
modified. No E00 root, engine or native build was touched during validation.

### 05:51 CEST — real fresh-shell validation catches a pre-mutation defect

The audited candidate was committed as
`4ee9a0598b662386a22fea329dde28668bbe3a68`, tree
`3b609d864a21f127cbdc00978ba5657259bc90e6`. The first real Windows
PowerShell 5.1 `-File ... -ValidateOnly` invocation then exited 1 before the
launcher body because `$PSScriptRoot` was empty while evaluating the
`RepositoryRoot` default inside the `param` block. The synthetic process
fixture had always supplied `-RepositoryRoot` and therefore missed this
default-path defect.

All four Launch3 roots were explicitly rechecked and remain absent. No Python,
schedule, native binding or engine ran. This is not a terminal Launch3
attempt because the launcher failed before its first mutation and the roots
remain pristine, but the committed candidate is not execution authority.

The minimal repair moves default repository resolution into the script body
and derives it from `$PSCommandPath` after parameter binding. It applies to
both launchers and requires a real Windows PowerShell 5.1 regression without
`-RepositoryRoot`, followed by a new commit, clean source identity and two
fresh independent audits.

### 06:02 CEST — default-path repair receives replacement double GO

Both launchers now accept an omitted or explicitly empty `RepositoryRoot` and
derive the exact repository root in the script body from `$PSCommandPath`.
They reject a missing command path, whitespace input, the wrong launcher name
or a path outside the exact `tools\atomic_mining` layout.

The new frozen hashes are:

- prepare launcher
  `b3f13e108bd993d9a0150a555567e0d876f58114f6a3afac30f76a032527c538`;
- prepare tests
  `52635548dc55d18894eaab4a39a778506c582124aeedafebde3006502c1d3492`;
- prepare runbook
  `4a87b8f499969fb1454bf6f6ed966db4ef4d6800a93e11ac07bf64c8034d5041`;
- full launcher
  `426fa0b345e329aa1dbedf79b7ca52da5f58208c50b40c87ecd7a8bd264b14ad`;
- full tests
  `f831853cc23c82e008f6a47ca376d7ed384eff8c0d7a1ac232aeaf6eead071f8`;
- full runbook
  `c6886807594a0a320efedc438c610b9b1d882c4d4fc23f820adeb59e88fc275e`.

The replacement independent audits both return `GO`, `P0=0`, `P1=0`.
Windows PowerShell `5.1.19041` real-process tests cover prepare and full with
the parameter omitted and explicitly empty: all four exit 0, emit byte-empty
stderr and strict `validated-not-started` JSON, bind the exact source
commit/tree and create zero target/capture files. Launcher tests pass `59/59`;
the root and final auditor each pass the complete `385/385` atomic-mining
suite. Both PowerShell ASTs and `git diff --check` pass. Ignored Python
bytecode created by isolated regression children was removed, and the
pre-commit bytecode count is zero.

### 06:15 CEST — Launch3 aborts before smoke; exact array-shape cause

The committed Launch3 source was
`3559c9b2c8f5206d5aceb3adf826bddb225e54f3`, tree
`e9c6135e30cfe43874b542fc59100f2f99b1843b`. Its fresh-shell
`-ValidateOnly` passed with strict `validated-not-started` JSON and all four
roots absent. The immediate resource sample was CPU
`26.4%, 22.4%, 21.0%`, RAM `25.88/31.92 GiB`, pagefile `22,819 MiB`, GPU
`32%`, VRAM `1,309/10,240 MiB`, `46 C`, `29.15 W`; free space was
`123.3 GiB` on `C:`, `128.0 GiB` on `D:` and `1,126.8 GiB` on `F:`.
The refined exact E00 process count was zero. Unrelated processes were not
modified.

Prepare/seal exited 1 after `35.8 s` at the first post-publication design
assertion with `Launch3 design directory inventory changed`. It had completed
only native binding, smoke/full schedule and smoke/full runtime packaging.
There is no smoke output root, no full output root and no stage-06 capture:
zero games and zero scientific outcomes were produced.

Read-only forensics proved no actual design mutation. The inventory contained
the exact 20 directories and 53 non-seal files, and every flattened file
size/SHA-256 matched the filesystem. However, both arrays were serialized as
one nested element because their helpers used `return ,$array` while every
caller already wrapped the result in `@(...)`. The malformed receipt therefore
declared `directory_count=1` and `file_count=1`; PowerShell's elementwise
array `-cne` then returned a truthy collection at outer index zero. The latent
same pattern also existed in referee timing/rules and execution-input key
helpers, so all six boundaries require the same minimal correction before a
fresh launch.

Forensic anchors:

- design inventory: `9,079 B`,
  `41f41757271b99aec31f5583d696bac638fff096945c0849fb1488d537581c4e`;
- design receipt: `2,690 B`,
  `1103dad709e8b0950906aaa8dcaae0b531805e990a533b619f61a565184af629`;
- captures 01–05 all have byte-empty stderr
  (`e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855`);
- smoke schedule: 1 pair / 2 games; full schedule: 84 pairs / 168 games;
- smoke and full roots are absent.

Classification: `P0=0`, `P1=1` operational/schema false positive. Launch3
design and capture roots are frozen terminal evidence; they must never be
executed, recovered, collected, analysed, edited, deleted or reused.

Launch4 receives fresh roots, experiment, battery IDs, seeds and
launch-specific schemas. Its only code correction is flat PowerShell array
emission plus scalar/object assertions, extended to every identical latent
boundary. The schedule sizes, time controls, T1 policy, inputs and scientific
contract remain unchanged.

### 06:23 CEST — Launch4 replacement receives triple GO

Launch4 uses fresh identity throughout:

- experiment `atomic-e00-src-v3-launch4-20260725`;
- roots `e00-src-v3-launch4-{design,smoke,full,captures}`;
- batteries `atomic-e00-src-v3-launch4-{smoke,full}`;
- seeds `atomic-e00-src-{smoke,full}-v3-launch4-20260725`;
- design schemas `atomic-e00-launch4-design-{inventory,receipt}-v1`.

The exact frozen candidate hashes are:

- prepare launcher
  `3fed00d692fef071103ba166e18ece22d2ad16f2e9e30ca1711ae603c9e98a72`;
- prepare tests
  `272476e4af0b6565d175f26f8c13aaf0a16ffb481ecc1458fa801ad14267fdc6`;
- prepare runbook
  `39ef4da0d68d14a124e664802b6095d31f801cc1356b395e399c0840492a4dc2`;
- full launcher
  `3bf12d3863d6ba7d2752ec1fd57c5af8e21de41032811fdd8972aa1fcb127a1e`;
- full tests
  `c4c582dede2a84871ebfd58a33ec86756a503f188cfc7b630974ffbb36a0fac9`;
- full runbook
  `32084617ec41de05ad45de96c5dc628d8f4acc473aaf7f1f54619138d49b0543`.

Three independent read-only audits return `GO`, `P0=0`, `P1=0`: code/
fail-closed, PowerShell 5.1 behavior/tests, and identity/documentation.
The behavioral auditor reproduced old versus new array output for zero, one
and multiple elements. Launcher tests pass `61/61`; the focused complete E00
contract suite passes `151/151`; the root passes the entire Atomic-mining
selection `387/387` in `97.14 s`. Both PowerShell ASTs, changed Python ASTs
and `git diff --check` pass. Ignored bytecode below `tools` is zero.

The current generic pre-suite sample was CPU `40%, 38%, 24%`, RAM
`6.24/31.92 GiB`, GPU `43%`, VRAM `1,299/10,240 MiB`, `46 C` and `28.89 W`.
Only serial unit tests were run under that sample; no engine, native build or
GPU job was launched and no unrelated process was changed.

### 06:28 CEST — Launch4 smoke science completes; wrapper fails closed

The committed Launch4 source was
`81535efc42ffb985deacd6fbbac1cbfea9306e4f`, tree
`713204f4123fbc96bd4e9e78504b3bb0ebfee7c1`. Its design sealed exactly
20 directories and 53 non-seal files. The one-pair/two-game runner committed
durable v3 evidence with byte-empty rejection and stderr ledgers:

- design inventory
  `7e177bb3f68de592f3aec8a41fb0ba0816ab768d35ec88f993bd591e3b234908`;
- design receipt
  `810fc3822b004865238178122ef0cb54596fd94adf40d96922123586d2fb88b9`;
- games
  `114e33990067938f9f5ca8f042460a71346bd91e3636654d07c3a9d71a136238`;
- smoke receipt
  `be0c8289a61c73f49affd607636a0d10e1dbb94dc2b9ab9e1c5d548840f9c9e9`.

Three independent read-only audits recomputed the exact color-swapped pair,
trajectory and source-game IDs, native referee/verifier evidence, owned-process
natural-zero proofs, handshakes, networks, 142 imports and all rule-operation
digests. The smoke science is sound and uncontaminated.

The wrapper nevertheless could not reach its final `sealed-smoke-committed`
summary. `06-smoke.stdout.json` is 251 bytes, SHA-256
`1e1d5733c2414c5b0c3d13d6eb784bd408fe5b415296745b746ba021990957c6`,
and terminates with CRLF. The runner used Python `print`, whose Windows text
translation introduced the carriage return; both launchers deliberately reject
all CR bytes. The same defect would make a full run commit its expensive
battery and then fail its wrapper validation.

Classification: `P0=0`, `P1=1`, `NO-GO` for Launch4 full. No authorization
was issued and the full root remains absent. Every Launch4 root is frozen
terminal evidence and must never be reused.

### 06:35 CEST — Launch5 minimal correction enters audit

Launch5 receives fresh experiment, roots, battery IDs, seeds and design schemas.
The only scientific-code correction is binary emission of the already
canonical summary bytes through `sys.stdout.buffer`, with a complete-write
check and flush. The strict LF-only parser remains unchanged. A real fresh
Windows Python regression now verifies exact stdout bytes, zero CR/BOM and
byte-empty stderr; an adversarial PowerShell regression accepts LF and rejects
the same JSON with CRLF. No schedule, input, time control, engine, network,
referee or result contract changes.

### 07:10 CEST — Launch5 smoke sound; validator-only false negative

Launch5 was committed at
`22607146ea2c3c204c5f509b458129b54c61c48f`, tree
`2bb3076a4d52ffecaaecd3a982251e21be80e6bb`. Its fresh design sealed
exactly 53 files and 20 directories, and its one-pair/two-game smoke
completed, committed and cleaned up naturally:

- design inventory: 9,077 B,
  `8cc17c59eb5d188596dce2d2cbca7b1f7e1e77d6cdb1a7e73e29d523496ce1dd`;
- design receipt: 2,714 B,
  `581287d7e3292b04b70c89cb738456d187bdb05e8851c2aa1f7d61a32251ead3`;
- games: 996,908 B,
  `9d2c4dd5b069bb5058469b477a8b4c0b459a54b9ceb7ec0102ef986fd0c71856`;
- execution receipt: 262,891 B,
  `861f3c487640eeb0906f87243375cafac6745be08eb0df14c3a45b0803419a86`;
- runner stdout: 250 B, canonical LF and no CR,
  `278938eeaa9c0c8d6b50ca8fffc781b175362f9f89548c9036564430d7b16ea4`;
- byte-empty runner stderr and rejection ledger;
- zero rejected games, zero time losses, natural zero-descendant cleanup;
- full root absent.

Three independent read-only audits agree: `P0=0`, `P1=1`, `NO-GO` only for
the wrapper authorization; the smoke science itself is sound and
uncontaminated. The producer stored the correct 19-option advertised-options
digest
`0c2ef87b8de44c286f0f6032ad7f0906835a731df5038c903959ce189a8e6cf5`.
The PowerShell validator delegated strings to Windows PowerShell 5.1
`ConvertTo-Json`, which encoded each literal `<empty>` as
`\u003cempty\u003e`. Three empty-string options expose that value in both
`default` and `raw`: six occurrences add exactly 60 bytes and produce the
incorrect validator digest
`91f5b6bc71720937f128ca2d72b9e439b7c7e3c10740b5a79234af413d2a0a08`.

Launch5 is therefore terminal and immutable. No full authorization exists;
its full root must remain absent and forbidden.

### 07:10 CEST — Launch6 canonical-string correction under test

Launch6 receives fresh identity only:

- experiment `atomic-e00-src-v3-launch6-20260725`;
- roots `e00-src-v3-launch6-{design,smoke,full,captures}`;
- batteries `atomic-e00-src-v3-launch6-{smoke,full}`;
- seeds `atomic-e00-src-{smoke,full}-v3-launch6-20260725`;
- design schemas `atomic-e00-launch6-design-{inventory,receipt}-v1`.

The minimal code correction replaces only the PowerShell string branch of
the namespaced canonical-JSON recomputation with a character-wise encoder
matching Python `ensure_ascii=False`: quote, backslash and U+0000-U+001F are
escaped; `<`, `>`, `&`, apostrophe, U+0085, U+2028, U+2029, non-ASCII and
valid surrogate pairs remain literal.

Cross-runtime regressions now cover `<empty>`, every relevant escape class,
literal `\u003c`, Unicode and emoji. The exact 19-option production fixture,
constructed through the real UCI parser and advertised-options builder,
recomputes
`0c2ef87b8de44c286f0f6032ad7f0906835a731df5038c903959ce189a8e6cf5`
in both Python and Windows PowerShell. Focused checks pass 4/4. No engine,
native build, schedule builder or E00 root has been invoked for Launch6 yet.

### 07:23 CEST — Launch6 staged candidate receives triple GO

The two dedicated Launch6 launcher files pass 64/64 tests. The complete
Atomic-mining suite passes 392/392. Both PowerShell ASTs and both changed
Python ASTs are clean; `git diff --cached --check` and the unstaged diff-check
pass; all four Launch6 roots remain absent.

Three independent read-only audits return `GO`, `P0=0`, `P1=0`:

- executable-diff audit: the full launcher differs only by fresh identity,
  and the prepare launcher differs only by fresh identity plus the canonical
  string encoder; all scientific parameters remain byte-equivalent;
- identity/documentation audit: roots, IDs, seeds, schemas, tombstones and
  active links are complete and consistent;
- independent test audit: exact 19-option and adversarial cross-runtime
  coverage, 64/64 launcher tests, ASTs, root absence and staged cleanliness
  all pass.

The independent test process created ignored Python bytecode during imports.
Those exact files and their now-empty cache directories were removed after
verifying that every resolved path remained inside the repository. The final
pre-commit ignored-bytecode count below `tools`, the launcher's source
boundary, is zero.

### 07:48 CEST — Launch6 smoke terminal on an empty-module false invariant

After a fresh resource sample (CPU 43%, 57%, 58%; 4.39 GiB free RAM; GPU
23% and 1,246 MiB VRAM; zero exact E00 processes), the committed Launch6
one-pair/two-game `Threads=1` smoke was started in a fresh PowerShell process.
It ran for approximately 93 seconds. The two games completed and committed,
but the outer wrapper exited 1 while authenticating child import inventory
row 124:

`smoke game 0 referee evidence child import inventory row 124 size is below its minimum`

Launch6 is terminal and immutable. The design and smoke roots exist; the full
root remains absent. Never execute, recover, collect, analyse, edit, delete or
reuse any Launch6 root.

Committed evidence:

- source commit
  `4ef0af27833a871b3ddce6f0305b60a099e0bb93`, tree
  `508817593e6fe9a474977f4590b8da63bed9751d`;
- design inventory
  `e59e9181b36c11c493fa251e533e957c193a0ec2a13460c2c6ada015db104b35`;
- design receipt
  `54f48a2bd7bbe2db8f643a0b7b09247eb83f4df4a12e630a831ea5889e8bf49b`;
- games
  `019572d88882989483ac519fafe8e07e78153ca4da5d86a67f83ecaa55d56cf2`;
- execution receipt
  `e65db654d050d8649107029cd0fd115bd78a2b3d20261f7fc760845a5db50262`;
- smoke inventory
  `f06222a5064e38ba70d03fd5b56c8b86bf30d28eb5bd8ce78467508e483eb2b1`;
- outer runner stdout
  `0b9168ae29964b73e01046bbf69a2d628596948e5e1e5dd951d37d8a3ec93705`;
- all captured stderr and the rejection ledger byte-empty;
- both games natural, with zero descendants and zero time losses.

Independent read-only forensics returned `P0=0`, `P1=1`. The rejected row is
the legitimate empty Python 3.12 standard-library file
`urllib\__init__.py`, source `python-installation`, size zero, SHA-256
`e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855`.
The row is identical in runtime discovery, runtime manifest, build receipt,
smoke snapshot and all referee/verifier inventories. Python correctly accepts
non-negative import sizes; only the PowerShell wrapper required a minimum of
one byte. Changing that one predicate to zero makes the complete in-memory
smoke assertion pass with no hidden next rejection. This is a validator-only
false negative, not a scientific failure.

### Launch7 — fresh correction worktree

Launch7 starts from the exact Launch6 source in an isolated worktree and uses
fresh experiment, design/smoke/full/capture roots, battery IDs, seeds and
design schemas. The child-import validator now accepts a zero-byte file only
when its digest is the canonical empty-file SHA-256, and rejects both a
zero-size/non-empty-digest row and a positive-size/empty-digest row.

The focused real Windows PowerShell regression passes. Launch7 is not yet
authorized: complete suites, AST/diff checks, terminal tombstones, independent
audits, a clean source commit, real `ValidateOnly`, fresh root absence and a
new resource gate must all pass before its smoke may start.
