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
