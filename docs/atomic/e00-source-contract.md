# Atomic E00 source battery contract v3

- Status: wire-frozen implementation candidate; no scientific E00 has
  started.
- Purpose: produce a fixed, authenticated current-V3 versus run3b trajectory
  corpus for the Atomic disagreement-mining loop.
- Distinct from: VSTC/STC/LTC strength gates that stop at the project's
  predeclared displayed LOS boundary.

The scientific schedule remains `atomic-e00-schedule-v1`. The version bump in
this document refers to the result-bearing runtime, evidence and receipt wire,
not to a change in pair allocation, seeds, time controls or intended science.

## Why the historical runner is fixture-only

The frozen `variantfishtest_new1.py` runner, SHA-256
`37d1790096520d9f3a1003746cdfbed59d2cc125a9b3d3192ff3399295ec9d70`,
cannot be the result-bearing E00 source:

- its game log contains only variant, root FEN and moves, not the per-game
  result;
- it chooses roots with unseeded `random.choice`;
- it opens the log in append mode;
- a failed pair is logged and silently skipped;
- games are played and logged before the worker decides whether they fit the
  accepted `max_games` total.

The last point is observed, not hypothetical. The historical TC1 log contains
131 `Game (atomic):` records but its accepted aggregate ends at `Total: 130`.
Retrospective joining cannot determine which trajectory was excluded.

Historical logs remain valid only as parser and legal-replay fixtures.

## Two independent E00 products

### E00-SRC

E00-SRC is a fixed-size paired source battery. It never stops on Elo, LOS,
score or a network disagreement. Its output may be mined only after every
scheduled color pair and every accepted game reconciles exactly.

Initial pilot schedule:

| Stratum | Time control | Pairs | Games |
| --- | --- | ---: | ---: |
| VSTC | `2000+20` ms | 48 | 96 |
| STC | `10000+100` ms | 24 | 48 |
| LTC | `30000+300` ms | 12 | 24 |
| Total | | 84 | 168 |

At the historical median and mean trajectory lengths this should yield roughly
10,000-20,000 raw pre-move positions before filtering.

### E00-LOS

E00-LOS consists of three separate direct current-V3 versus run3b strength
tests at VSTC, STC and LTC. Each test:

- uses a paired color-swapped schedule;
- reaches the exact project-defined displayed `0.0%` or `100.0%` LOS boundary
  after the predeclared minimum sample, or ends `INCONCLUSIVE` at its fixed
  maximum game count;
- never expands E00-SRC automatically;
- remains descriptive of playing strength and is not a label source.

The fixed E00-SRC schedule prevents optional stopping from changing which
positions enter the mining population.

## Frozen identities

Before schedule creation, bind:

- experiment and battery IDs;
- source commit and clean tree;
- runner/wrapper bytes and hashes;
- Atomic engine bytes and hash;
- current V3 network bytes and hash;
- run3b network bytes and hash;
- opening-book bytes, hash and exact root count;
- configuration bytes and hash;
- all UCI options;
- schedule seed and derivation algorithm;
- time controls, pair counts, maximum wall/engine time and resource plan.

The first pilot uses the same engine binary on both sides. The only intended
network difference is `EvalFile`.

Playing options:

- `UCI_Variant=atomic`;
- `Threads=1`;
- `Hash=512`;
- `MultiPV=1`;
- `Ponder=false`;
- `SyzygyPath` empty;
- `SyzygyProbeLimit=0`;
- `Use NNUE=true`;
- explicit network-specific `EvalFile`.

The later labeler retains its different frozen policy:
`Use NNUE=pure`, `Threads=1`, tablebases off and exactly `go depth 7`.

## Schedule

`atomic-e00-schedule-v1` is canonical JSONL plus a create-new receipt.

The schedule:

1. parses and authenticates every book root;
2. derives a deterministic ordering from
   `SHA256(schema || seed || book_sha256 || root_identity)`;
3. selects roots without replacement;
4. keeps root sets disjoint across time-control strata;
5. assigns one `pair_id` and two color-inverted legs per root;
6. records ordinal, book line, normalized six-field FEN and FEN hash;
7. is completely written and hashed before any engine starts.

No filesystem order, Python RNG state, clock time or thread completion order
may affect the schedule.

The exact post-commit native-build, schedule, runtime-manifest and smoke
commands are maintained in
[`e00-launch5-prepare-smoke-runbook.md`](e00-launch5-prepare-smoke-runbook.md).
The separately authorized full-only entrypoint is documented in
[`e00-launch5-full-runbook.md`](e00-launch5-full-runbook.md). The smoke and
full battery use different create-new schedule, runtime-design and execution
roots.

## Result-bearing game wire

The frozen public schemas are:

| Product | Schema |
| --- | --- |
| Accepted game | `atomic-e00-game-v3` |
| Execution commit marker | `atomic-e00-execution-receipt-v3` |
| Runtime manifest | `atomic-e00-runtime-manifest-v2` |
| Runtime discovery receipt | `atomic-e00-runtime-discovery-receipt-v2` |
| Engine evidence | `atomic-e00-engine-evidence-v3` |
| Internal leg request/result | `atomic-e00-internal-leg-request-v2` / `atomic-e00-internal-leg-result-v3` |
| Internal verifier request/result | `atomic-e00-internal-verify-request-v3` / `atomic-e00-internal-verify-result-v2` |
| Internal discovery request/result | `atomic-e00-internal-runtime-discovery-request-v2` / `atomic-e00-internal-runtime-discovery-result-v2` |
| Internal classified failure | `atomic-e00-internal-failure-v1` |
| Terminal rejection | `atomic-e00-rejection-v2` |
| Owned-process proof | `atomic-e00-owned-process-v1` |

The pair, inventory, trajectory, source-game, schedule and schedule receipt
schemas remain v1 because their semantics did not change.

Each accepted game is one `atomic-e00-game-v3` canonical JSONL row:

- experiment ID, battery ID and schedule SHA-256;
- pair ID, pair ordinal and leg;
- exact time-control object;
- book SHA-256, line number, normalized root FEN and root-FEN SHA-256;
- engine, current-net and teacher-net SHA-256 values;
- white and black network roles;
- result from White's perspective and current-V3's perspective;
- time-loss flag and terminal reason;
- exact UCI move array and ply count;
- content-derived trajectory SHA-256 and source-game ID;
- stdout/stderr or per-game diagnostic transcript SHA-256;
- exact engine lifecycle/options evidence for both single-use engine
  processes;
- exact startup preamble, `id name`, `id author` and the single empty
  post-ID UCI separator for both engine processes;
- independent native-referee and native-verifier evidence;
- owned-process cleanup evidence proving direct-child exit, no forced
  termination and zero active descendants after cleanup.

The trajectory hash includes root FEN, ordered moves and result. The
source-game ID includes the frozen battery and schedule identities, pair, leg
and trajectory hash.

`runtime-manifest-v2` precommits the exact nine-module isolated Python package,
the CPython executable and runtime libraries, the freshly built native binding
and build manifest, and the discovered child import inventory. Discovery uses
the runner's real `-I`/`runpy(..., run_name="__main__")` bootstrap under
`OwnedProcess`, but starts no chess engine.

## Pair atomicity and failure policy

- A schedule item produces both color-swapped games or no accepted pair.
- Each completed pair is first materialized under an isolated temporary
  identity.
- Any engine exception, malformed move/result, time-accounting error, missing
  leg, unexpected process exit or stderr policy violation aborts the battery.
- An isolated child exit 70 is classified only by a canonical
  `internal-failure-v1` record bound to the exact request, mode and allowlisted
  stage. It must coexist with no result and contains no free-form exception
  text, paths or traceback.
- There is no silent retry. A predeclared retry creates a new attempt identity
  and cannot overwrite or disguise the failed attempt.
- Final JSONL is ordered by pair ordinal and leg, never by completion order.
- Durable outputs use create-new semantics.
- Every regular file in the executed runtime package, native build namespace,
  immutable input snapshots and authenticated rules `src` namespace is held
  through the runner's strict-static Windows guard for the relevant operation.
- Strict guards detect replacement, mutation, deletion and namespace additions
  at every checkpoint. Cooperative host processes can still create a residual
  entry between checkpoints; this is detected and terminates the battery, not
  claimed to be prevented by the operating system.

The final receipt is the commit marker. Consumers reject any directory without
the exact receipt, even when some pair temporaries or outputs exist.

## Final reconciliation

Before publishing the final receipt, prove:

- exactly `2 * scheduled_pairs` accepted rows;
- every scheduled pair has legs `{0,1}` and the same root;
- color roles are inverted exactly;
- every trajectory has one result;
- row W/L/D totals equal the aggregate;
- trajectory and source-game IDs recompute;
- all moves replay legally under Atomic rules;
- all input hashes are unchanged pre/post;
- no extra, duplicate or partial accepted row exists;
- stdout, stderr, schedule, result JSONL, rejection ledger and inventory hashes
  are sealed.
- the precommitted child import inventory is exact, with no missing, extra or
  ambient module;
- every leg and independent verifier has a valid natural-zero-descendant
  `atomic-e00-owned-process-v1` proof;
- the pre/post runtime identities, native-build inventory and strict namespace
  checkpoints are equal.

Any failed condition is a terminal `NO-GO` for that battery ID.

## Initial resource policy

The v3 source runner is CPU-only, requires `Threads=1`, and executes pairs
serially. Any concurrent-pair mode is a new contract and a new launch, not an
operational tuning knob. A fresh generic CPU/RAM/pagefile/GPU sample is
mandatory immediately before the one-pair smoke and again before the fixed
battery. Never infer spare capacity by first starting the battery, and never
start or repurpose the user-managed OpenBench worker.

Conservative runtime for the 84-pair pilot:

- serial pairs: approximately 2-3 hours.

These are planning estimates, not completion guarantees.
