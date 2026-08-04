# Atomic match-disagreement mining loop

- Status: E02-M experiment contract; implementation and production execution
  remain gated
- Scope: Atomic 8x8, current V3 network versus the frozen run3b legacy champion
- Baseline source revision:
  `01a74371d6c947bb03ae12a8cff6a35044d3aa0b`, tree
  `b81d7aadde7334af4e25e1654754afa096e299ec`

## Purpose

E02-M tests whether positions mined from real V3-versus-run3b games by
network disagreement are a more Elo-efficient fine-tuning source than an
equally sized random sample or ordinary replay. It is a controlled experiment,
not a license to turn every surprising engine score into training data.

The intended loop is:

1. play a fresh, sealed E00 match directly between the current best V3 network
   and the run3b legacy champion;
2. legally replay those games and extract exact Atomic positions;
3. reject unstable or unauthenticated observations;
4. rank the remaining positions with Atomic-aware disagreement signals;
5. confirm the ranking on an unseen component split;
6. build equal-budget `MINED`, `RANDOM` and `REPLAY` cohorts;
7. label them under one frozen teacher policy and train otherwise identical
   continuations;
8. measure the resulting networks directly against the frozen V3 target at
   VSTC, STC and LTC.

The experiment succeeds only if the match results attribute a repeatable gain
to the selected positions. Offline loss, score correlation, rank enrichment
and tablebase agreement are diagnostics; none of them is an Elo result.

## Non-goals and protected work

E02-M MUST NOT:

- edit, stop, reconfigure, relabel or consume partial artifacts from the
  official one-billion-position campaign or its OpenBench tests 72--81;
- mine its own confirmation set, E05 books, E05 games, E05 results or any
  future final-evaluation artifact;
- use historical matches as scientific E02-M input. Historical PGNs and logs
  may be parser fixtures and discovery material only;
- silently change the current V3 network, run3b teacher, engine binary, UCI
  options, node/depth budget, hash size, tablebase-off policy or cohort budget;
- claim that run3b is globally correct merely because it is the stronger
  legacy reference;
- train directly on Syzygy DTZ or map DTZ to a centipawn target;
- invent a draw result for a score-only record;
- advance to training, OpenBench or E05 after any kill gate fires.

## Frozen identities

Every run begins with an immutable experiment definition. At minimum it binds:

- E02-M schema/version and experiment ID;
- exact engine repository commit, tree and executable SHA-256;
- exact run3b and V3 network basenames, byte counts and SHA-256 values;
- run3b teacher mode (`pure`), `Threads=1`, fixed `Hash`, search budget and
  tablebases disabled;
- E00 OpenBench test IDs, configuration hashes, PGN/log hashes, time controls,
  books, seeds and accepted game/result counters;
- extractor, replay scanner, probe, ranker, cohort builder, trainer and match
  runner commits plus executable or script hashes;
- all thresholds, tactical strata, component caps, split seeds, cohort sizes,
  training seeds, training recipe and match gates;
- the resource and worker restoration policy;
- the E05 denylist hash without exposing E05 positions or results.

The known run3b teacher network
`atomic_run3b_e202_l05.nnue` has SHA-256
`99dc67eabf26a64faeeca3a88b4c38597a840b8d4a874b9f2cf658c6f92a04a6`.
An execution MUST rehash the selected local bytes and MUST NOT accept this
document as proof that those bytes are present. The V3 network identity is
run-specific and must be supplied rather than inferred from a filename.

Paths are not identities. A clean Git name, a network basename or an
OpenBench display name is insufficient without its authenticated content
hash.

## Current implementation boundary

The first repository slice provides deterministic artifact helpers, a narrow
variantfishtest-log extractor and a fail-closed UCI session. It is scaffolding,
not a complete E02-M executor.

In particular, the linear variantfishtest record
`Game (atomic):` + `fen` + UCI moves does not contain an individual game
result. The extractor correctly emits `outcome:null` and
`scientific_role:"discovery-only"`. Those rows may test parsing, replay and
component construction, but they are not eligible for result matching or
confirmatory E02-M until a separate authenticated per-game PGN/receipt is
joined one-to-one. Stability, ranking, cohort selection, score-only labeling,
training and match orchestration remain separately gated stages.

## Durable artifact rules

All durable control and row artifacts are canonical UTF-8 JSON or JSONL:

- object keys are sorted;
- separators are minified;
- non-finite numbers are forbidden;
- each document or row ends in exactly one LF;
- CR characters and a UTF-8 BOM are forbidden;
- final paths are create-new and never overwritten;
- a producer writes a temporary regular file, flushes it, re-reads and hashes
  it, then publishes it atomically;
- failed or interrupted work never leaves a final-looking artifact.

SHA-256 is computed over exact bytes. A manifest cannot cover its own hash.
`hashes.json` is therefore written last and is itself bound by the final
external receipt. These hashes are integrity evidence, not signatures.

## Position and result identities

### Exact position

Every retained row stores the full six-field FEN. The halfmove clock, fullmove
number, castling rights and en-passant square MUST NOT be replaced by defaults.
FEN4 deduplication is forbidden.

FEN alone does not encode repetition history. Each row also stores:

- source game ID and authenticated source hash;
- root FEN and complete legal move prefix, or a hash-bound replay reference;
- ply, side to move and fullmove number;
- a history digest sufficient to distinguish repetition state;
- exact-position ID;
- transposition key;
- split-component ID.

The transposition key may deliberately omit move counters to conservatively
join related states for splitting. It is never a replacement for the exact
scientific identity.

### Three different meanings of "result"

The following fields MUST remain separate:

1. **Source result** is the authentic result recorded by a sealed E00
   per-game PGN/receipt and joined one-to-one to the replayed trajectory. It is
   metadata used for matching and audit. It is not a teacher target.
2. **Teacher label** is a score, WDL distribution, mate class/distance and PV
   produced under the frozen run3b policy. It is not a completed-game result.
3. **Experimental result** is the accepted paired-game result of a trained
   candidate against frozen V3. This is the only strength result.

For an original E00 position, `source_result_stm` is derived from the
authenticated per-game result and the retained side to move. A match log
without its one-to-one result-bearing companion keeps
`source_result_present=false` and remains discovery-only. For a
counterfactual teacher continuation, the parent game's result MUST NOT be
inherited. Such a row has no authentic result unless that exact continuation
is legally played to an authenticated terminal.

Score-only continuation training therefore uses a dedicated score-only
interchange with an absent result and a loss that consumes only the score
label. If a downstream binary format mandates a result field, the row is
ineligible until the pipeline can provide an authentic value or a reviewed
format that represents absence. Writing `result=0` to satisfy a serializer is
a kill condition, even when a nominal lambda would ignore it.

Timeouts, disconnects, aborted games, illegal-move losses and administrative
results remain in a rejection ledger. They are never quietly converted to
draws. Time or adjudication results may be retained as source metadata only
when E00's sealed policy explicitly permits them; they cannot establish the
correct evaluation of an intermediate position.

## Source and split contract

### Eligible source

The scientific source is one fresh E00 battery of V3 versus run3b, created for
this loop and sealed before extraction. E00 directly compares those two
networks; there is no intermediate 1B-network comparison.

The source inventory is closed before replay and records every accepted PGN,
per-game result receipt and log, its byte count and hash, expected games,
book/root identity, seed, time-control stratum and result. The result-bearing
artifact is joined one-to-one to root plus complete move history; aggregate
W/L/D counters cannot supply individual outcomes. Missing, extra, mutable,
ambiguous or duplicate source files fail closed.

The complete authenticated `REPLAY` candidate inventory is also frozen before
component construction. Its result-presence policy, source classes and common
support are part of the experiment definition. Adding replay records after
ranking would make component isolation unverifiable and is forbidden.

### Global components before probes

Contamination is prevented before any network score is observed. Build a
global undirected component graph over every extracted candidate and union
positions that share any of:

- one source game or source root;
- an exact position;
- a transposition key;
- an ancestor/descendant relationship;
- a teacher-generated continuation;
- an opening/book split group declared by E00;
- any other project-defined leakage group.

Assign complete components, never individual rows, to mutually exclusive
partitions using one frozen deterministic seed:

- `CAL`: calibrate score-to-WDL transforms, thresholds and rank weights;
- `CONF`: untouched confirmation of the frozen miner;
- `COHORT_ELIGIBLE`: E00 components from which `MINED` and `RANDOM` may later
  be selected;
- `REPLAY_ELIGIBLE`: frozen baseline components from which `REPLAY` may later
  be selected;
- `DENY_E05`.

Component construction and partition assignment are written and hashed before
the first rank probe. `MINED`, `RANDOM` and their train/dev subsets do not exist
yet: they are selected only after the frozen ranker passes `CONF`.
`REPLAY` is selected only from `REPLAY_ELIGIBLE`. Selection and train/dev
assignment use precommitted, content-derived rules and never move a component
after observing a training or match outcome. A component cannot be split to
improve balance. Duplicates discovered later cause the affected partitions to
be rejected, not repaired after looking at outcomes.

E05 supplies only a hash-bound manifest of blind root and leakage-group
identities. The miner receives the exclusion identities, not E05 positions,
games, scores or results, and never retunes itself on E05.

## Pipeline

### 1. Extractor and legal replay

The extractor reads the sealed E00 inventory and replays every game through
the pinned Atomic engine. Textual SAN/UCI parsing alone is not legal replay.
For each move it:

1. sets the authenticated root;
2. reconstructs the exact move history;
3. verifies that the played move is legal under Atomic rules;
4. obtains the engine's exact six-field FEN before the move;
5. records check, terminal and result state;
6. records legal-move count and deterministic tactical features;
7. advances the move and checks the expected next state.

Extraction preserves, rather than filters away, captures, checks, evasions,
post-explosion states, en-passant, promotions, king-blast threats and terminal
boundaries. Filters such as minimum ply are configuration, never hidden
defaults. Rejections have stable reason codes and source coordinates.

Recommended Atomic strata include:

- quiet;
- check or forced evasion;
- capture or immediate post-explosion;
- direct king-blast threat;
- promotion or en-passant;
- low-material/tablebase-eligible.

Classification is engine-backed. String matching over SAN or FEN is not enough
to establish an Atomic tactical class.

### 2. Stability probes

Stability probing separates real network disagreement from search noise. Both
networks run with:

- the same pinned engine bytes;
- `Threads=1`;
- fixed `Hash`;
- identical explicit UCI options;
- tablebases disabled unless the run is the separate E01-TB canary;
- `ucinewgame` and a cleared hash between independent probes;
- fixed node budgets for paired ranking probes;
- no wall-clock search limit.

Probe order alternates by a precommitted schedule so warm-up or host load does
not always favor one network. Each record stores every raw `info` line,
reported depth/seldepth/nodes, score kind and bound, WDL when available,
MultiPV rank, legal PV, best move, ponder and elapsed diagnostics.

The stability policy is frozen on `CAL`. It defines required repeat count,
node tolerance, score/WDL tolerance, accepted bounds, mate-class agreement,
best-move stability and minimum MultiPV completeness. Unstable positions are
reported separately; they are not silently retried until they rank well.

Any undeclared UCI option, option fallback, network-load fallback, missing
best move, illegal PV, early process exit, malformed `info`, timeout, or node
budget drift rejects the probe. A retry is a new attempt with its own identity
under a predeclared limit.

### 3. Atomic-aware ranker

Raw centipawn difference is not a valid cross-network ranking. Atomic has
forced explosions, discontinuous king safety and long forcing lines; mate
scores also do not share a linear scale with centipawns.

The ranker is fitted and frozen on `CAL`, then applied byte-for-byte to `CONF`.
Its inputs are:

- difference between per-network calibrated WDL distributions;
- sign or WDL-class disagreement;
- best-move disagreement;
- top-three move-set overlap;
- run3b depth-7 teacher regret of the V3-selected move relative to the
  teacher's own best move;
- tactical stratum, phase, branching factor and stability diagnostics.

Teacher regret is evaluated from the original side-to-move perspective as the
teacher's value for its selected move minus its value for the V3-selected
move, using the same complete `go depth 7` root analysis. It is compared in
calibrated WDL space; mate-class disagreement remains categorical.

Calibration may consume authentic `CAL` outcomes, but the resulting mapping,
parameters and hash are frozen before `CONF`. Bounds and missing WDL values
have explicit policies. Raw cp values remain evidence and never become the
primary cross-network distance.

Mate cases form separate classes:

- mate versus non-mate;
- winning mate versus losing mate;
- same mate class with different distance.

Mate distance is compared only inside a compatible class. It is never clipped
into a cp score. Terminal positions are a separate stratum.

The final ordering is a deterministic tuple or a fully specified formula.
Tie-breakers are content-derived IDs, not input order or filesystem order.
Per-source, per-component, per-opening, per-result and mate-class ceilings are
predeclared to prevent one long forced line from filling the top K.

### 4. Confirmation gate

The unseen `CONF` partition tests whether the frozen ranker finds teacher
errors rather than merely fitting `CAL`. Use component-grouped bootstrap
intervals. E02-M may build training cohorts only when all of the following
hold:

- the 95% confidence interval for mean teacher regret of the top 20% minus a
  matched random 20% is strictly above zero;
- the lower 95% confidence bound for hard-case-rate enrichment is greater
  than `1.5`;
- the signal is present in at least three predeclared Atomic tactical strata;
- at least 95% of expected depth-7 labels and PVs are complete and legal;
- there are zero invalid accepted labels;
- no source, opening, component or mate class exceeds its frozen dominance
  ceiling.

The hard-case threshold, matching keys, bootstrap method, resample count and
confidence convention are all frozen on `CAL`. Failure is informative: archive
the dossier and change the next experiment ID rather than tuning on `CONF`.

### 5. Equal-budget cohorts

Create three mutually component-disjoint cohorts:

- `MINED`: top-ranked, stable and confirmed positions;
- `RANDOM`: random E00 positions matched to `MINED`;
- `REPLAY`: ordinary positions from the frozen current/legacy replay baseline,
  representing the benefit of equal additional training without mining.

The cohorts have exactly equal retained rows, teacher-search budget, labeled
continuations, serialized bytes or another predeclared compute proxy, optimizer
steps and GPU budget. `RANDOM` matching is exact on:

- source and time control;
- `source_result_present`, and authentic source result when present;
- ply bin and material/piece-count bin;
- tactical stratum;
- branching-factor bin.

If exact matching is impossible, reduce all cohorts to the common support or
abort. Do not loosen matching after observing training or match results.
Original positions and outcome-null counterfactual continuations are separate
matching subcohorts; neither may be used to satisfy the other's quota.

`REPLAY` is selected only from the authenticated baseline inventory that
entered the global component graph before probes. It uses the same
result-presence subcohorts, common-support rules and component exclusions. It
is not a duplicate of `MINED`, and it cannot read E05. Its role is to
distinguish useful position selection from the generic benefit of more
optimizer steps.

### 6. Teacher labels and continuations

The teacher is the frozen run3b champion under:

- depth 7;
- `Use NNUE=pure`;
- `Threads=1`;
- fixed `Hash`;
- `SyzygyPath` empty and `SyzygyProbeLimit=0`;
- deterministic root setup and explicit UCI options.

The label command is exactly `go depth 7`. Every required MultiPV lane must
complete depth 7. A partial lower depth or an opportunistic `depth >= 7`
observation is not an equivalent label and is rejected. E01-TB is the only
stage that enables direct Syzygy probing.

Every accepted label records score kind/value/bound, calibrated WDL when
available, best move, MultiPV entries, PV, depth, seldepth, nodes and raw
protocol transcript. The best move and complete PV prefix are replayed by the
Atomic engine. A legal teacher best move may produce one continuation child;
the child receives a new exact identity, is unioned into its parent's
component and is independently labeled. Explosion, en-passant, promotion,
terminal and missing-king outcomes are derived from the engine state, not a
handwritten board updater.

No teacher label overwrites a source result, and no source result substitutes
for a teacher label.

### 7. Identical training

The run definition MUST name one available initialization route before cohort
selection. Prefer the exact frozen V3 float checkpoint. If it does not exist,
the experiment either blocks or predeclares one reviewed network-import route
whose identical result is proven for every cohort. There is no runtime fallback
from checkpoint to quantized-network initialization.

The candidates share:

- architecture and feature schema;
- trainer commit, binary, dependency lock and environment;
- optimizer, learning-rate schedule, lambda/loss and epoch/step count;
- batch size, shuffle policy and data-loader count;
- seed family and number of replicas;
- checkpoint, export, quantization and reimport gates.

The only intended factor is `MINED` versus `RANDOM` versus `REPLAY`. Use at
least three training seeds when the short fine-tune cost permits it. Initial
loss is recorded as a cheap diagnostic but is not a strength claim.

### 8. Authentic strength gate

Each trained network is tested directly against the frozen V3 target, never
inferred through a chain of opponents. Run VSTC, STC and LTC, with the exact
books, seeds, concurrency and paired-game schedule predeclared.

Non-transitivity also forbids attributing mining from separate Elo estimates
against V3. Run direct `MINED` versus `RANDOM` and direct `MINED` versus
`REPLAY` at VSTC, STC and LTC under the same sealed gate family. A predeclared
multiway paired design may replace those tests only if its direct contrasts
are equivalent and reviewed before launch.

Every test runs until the configured LOS display/gate reaches its exact
project-defined 0% or 100% decision boundary, including the frozen numerical
threshold and rounding convention. This language does not claim a literal
mathematical posterior probability of zero or one. A maximum game budget and
inconclusive disposition are fixed before launch.

To attribute a gain to mining, `MINED` must clear both direct control contrasts
under the predeclared paired confidence/LOS rule and show no predeclared
tactical or quiet regression. LTC is the primary decision surface; VSTC alone
cannot promote a network.

OpenBench accepted result counters, PGNs and final test state are
authoritative. Dashboard names, provisional Elo, offline loss and local
summaries are not substitutes.

## Shared-resource contract

E02-M runs only on spare resources or during an explicitly approved worker
pause.

- CPU probes are `Threads=1`. The orchestrator caps concurrent engines from a
  sealed resource plan; it does not infer "free" cores by starting work until
  the machine stalls.
- GPU use is limited to short, identical cohort training jobs and explicit
  validation. One cohort does not receive a less-contended machine state than
  another.
- Disk quotas include temporary outputs, raw transcripts and a safety margin.
  A low-space condition stops before writing a partial final artifact.
- Unrelated processes, credentials, LoL/Riot and the official OpenBench server
  are outside scope.
- The official 1B DATAGEN contracts and artifacts remain read-only.

If the OpenBench worker is paused, the run records its command, target, PID
tree and connection state before requesting a graceful stop, then verifies
that the client and its engine children have exited. Removing a stop flag
before that verification is forbidden. The current operator instruction for
this E02-M execution is `worker_restore_policy=leave-stopped`: the experiment
must not relaunch T24 at the end. A future run may use a different policy only
through a new sealed experiment definition and explicit authorization.

Child process priority is explicit and identical across paired probes. The
pipeline never manipulates processes by a broad executable-name match.

## E01-TB arbitration canary

E01-TB is a small, independent canary that asks whether the disagreement
ranker enriches for positions with objectively resolvable errors. It is not a
Syzygy training shard.

### Existing trusted surface

Use the native Atomic probe implementation and existing conformance surface:

- `src/syzygy/tbprobe.h`:
  `probe_wdl`, `probe_dtz`, `root_probe`, `root_probe_wdl` and
  `rank_root_moves`;
- `tests/atomic_syzygy_driver.cpp` for direct WDL5/DTZ and root probes;
- `tests/atomic_syzygy.py` and `tests/atomic_syzygy_uci.py`;
- `tests/fixtures/atomic-syzygy/` for the frozen six-man fixtures;
- the authenticated worker inventory validator rather than a new file finder.

The published six-man inventory SHA-256 is
`3d4b7fd0ab387f4f60da2078f612c9e8890e6026f551aebe8631efc157788f23`.
An execution still validates every runtime file, size and digest.

### Canary protocol

1. Pass the existing direct and UCI fixture suites.
2. Select a precommitted, component-disjoint sample of real positions with at
   most six pieces.
3. In the first tranche require no castling rights, halfmove clock zero, no
   known repetition dependence and a full six-field FEN.
4. Rank run3b versus V3 with tablebases disabled and the normal E02-M paired
   probe contract.
5. Probe the retained positions directly through the C++ Syzygy API.
6. Record WDL5, DTZ, both `ProbeState` values, root moves/ranks, rule50 mode,
   probe counters and inventory hash.
7. Compare exact-error and hard-case enrichment in top-ranked versus matched
   random components.

`tb_hits` is provenance, not proof that one record was resolved. An accepted
canary label requires a direct non-`FAIL` probe state. WDL5 and DTZ remain
separate. DTZ is an arbitration/diagnostic field only: it is never converted
to cp, never used as a scalar NNUE label and never placed in `MINED`,
`RANDOM` or `REPLAY` training data.

Clock-boundary, repetition-dependent, castling and Atomic960 cases belong in
later separately reported canaries. They cannot be mixed into the clean first
tranche to improve the headline result.

## Experiment dossier

Use one create-new root such as:

```text
E02-M-<UTC>-<experiment-id>/
  experiment.json
  environment.json
  commands.jsonl
  source/
    inventory.json
    games/
    rejections.jsonl
  extraction/
    positions.jsonl
    replay.jsonl
    components.jsonl
    split-ledger.jsonl
  probes/
    stability.jsonl
    raw/
  ranking/
    calibration.json
    ranked-cal.jsonl
    ranked-conf.jsonl
    confirmation.json
  cohorts/
    mined.jsonl
    random.jsonl
    replay.jsonl
    matching.jsonl
    labels/
  training/
    <cohort>-<seed>/
  matches/
    openbench-tests.json
    results.json
    pgn-inventory.json
  e01-tb/
    inventory-receipt.json
    probes.jsonl
    result.json
  hashes.json
  receipt.json
```

The exact layout may evolve before implementation, but the dossier must bind:

- exact commands and ordered arguments, with secrets redacted rather than
  copied;
- UTC start/end, host and resource plan;
- Git commits/trees and dirty-state proof;
- executable, script, schema, network, book, source and output hashes;
- complete UCI options and engine-reported network identity;
- component and split ledgers;
- counts at every stage and every rejection reason;
- cohort matching and compute reconciliation;
- trainer checkpoints, exported networks, logs and metrics;
- OpenBench IDs, accepted counters, PGNs and terminal LOS decisions;
- worker pause/restore evidence;
- failures and retries, including outputs from failed attempts.

An experiment with an incomplete dossier may inform debugging but cannot
promote a network.

## Checklist

### Before extraction

- [ ] E02-M definition is final, canonical and hashed.
- [ ] Engine, run3b, V3, tools, schemas and binaries are clean and SHA-pinned.
- [ ] E00 is fresh, complete and sealed; historical sources are fixture-only.
- [ ] Every eligible E00 trajectory has a one-to-one authenticated per-game
      result join; outcome-null rows remain discovery-only.
- [ ] E00 and REPLAY inventories, totals and accepted results reconcile exactly.
- [ ] Official DATAGEN tests 72--81 are untouched.
- [ ] E05 blind leakage-group manifest and split seed are frozen.
- [ ] Resource plan, disk margin and worker restoration policy are recorded.

### Extraction and splitting

- [ ] Every accepted game replays legally from its authenticated root.
- [ ] Full FEN, history, source result and move prefix are preserved.
- [ ] No timeout, abort, illegal loss or missing result was converted to draw.
- [ ] Component graph includes roots, games, descendants and transpositions.
- [ ] CAL, CONF, COHORT_ELIGIBLE, REPLAY_ELIGIBLE and DENY_E05 were assigned
      before any network probe and have zero component overlap.
- [ ] MINED/RANDOM/REPLAY and train/dev assignment happened only after CONF
      under the precommitted content-derived policy.

### Probing and ranking

- [ ] Both networks use identical T1, Hash, options and fixed-node budgets.
- [ ] Tablebases are disabled outside E01-TB.
- [ ] Every accepted best move and PV is legal.
- [ ] No network, option, binary or search-mode fallback occurred.
- [ ] WDL calibration and rank policy were frozen on CAL.
- [ ] Mate classes/distances were not mixed with cp.
- [ ] CONF passes all confidence, enrichment, strata and dominance gates.

### Cohorts and labels

- [ ] MINED, RANDOM and REPLAY are component-disjoint and equal-sized.
- [ ] RANDOM and REPLAY match result-presence and every declared key on common
      support.
- [ ] Label and training compute reconcile exactly across cohorts.
- [ ] Teacher is run3b exact `go depth 7`, pure, T1, fixed Hash and TB off.
- [ ] Every continuation was made and replayed by the Atomic engine.
- [ ] No fake or inherited counterfactual result entered a training record.

### Training and strength

- [ ] Init, recipe, steps, seed family and environment differ only by cohort.
- [ ] Checkpoints and exported networks reimport into the pinned engine.
- [ ] VSTC, STC and LTC are direct candidate-versus-frozen-V3 gates.
- [ ] VSTC, STC and LTC include direct MINED-versus-RANDOM and
      MINED-versus-REPLAY attribution contrasts.
- [ ] Every match reached a predeclared LOS boundary or is reported
      inconclusive.
- [ ] MINED clears the predeclared attribution comparison versus both controls.
- [ ] Quiet and tactical regression checks pass.
- [ ] Worker remains stopped at completion under the current operator policy.
- [ ] Final hashes and external receipt authenticate the complete dossier.

## Immediate kill gates

Stop the affected run without repairing it in place if any of these occurs:

- mutable, missing, extra or unauthenticated source, engine, network or corpus
  bytes;
- dirty or wrong Git source, wrong feature schema, wrong network, UCI option
  fallback or unexpected embedded net;
- missing, ambiguous or non-bijective per-game result join for any
  confirmatory E00 row;
- REPLAY input added after global component assignment;
- illegal replay, FEN/history loss, terminal/result mismatch or fabricated
  result;
- any component overlap across CAL, CONF, eligible pools, cohort train/dev or
  E05;
- any access to E05 positions, games or results;
- rank tuning after opening CONF;
- invalid accepted label or PV, or less than 95% expected legal depth-7 labels;
- node/depth, Hash, threads, teacher, tablebase or compute-budget drift;
- failed exact cohort matching or unequal training budget;
- one source, opening, component, result or mate class exceeding its frozen
  dominance ceiling;
- E01-TB inventory mismatch, direct probe failure for an expected eligible
  position, or use of DTZ as a training target;
- overwrite of an artifact, unrecorded retry, exhausted disk margin or
  manipulation of an unrelated process;
- promotion based only on offline loss, VSTC, provisional Elo or an indirect
  opponent chain.

After a kill, preserve the immutable failure dossier. Any correction receives
a new experiment ID, new hashes and a fresh blind split.
