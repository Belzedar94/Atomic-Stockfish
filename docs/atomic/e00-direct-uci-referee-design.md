# E00 direct-UCI / native-referee design

Status: wire-frozen implementation candidate; no scientific execution is
permitted until the checklist at the end receives an independent GO, the
source is committed cleanly and the real-engine smoke is authenticated.

## Decision

The result-bearing E00 runner must not use `python-chess` as either the game
referee or the UCI gameplay controller.

The first implementation demonstrated three scientific failure modes:

1. `AtomicBoard.outcome(claim_draw=True)` differs from the repository's
   `Atomic::outcome()` contract for prospective rule-50/repetition claims and
   insufficient material.
2. applying options through private `Protocol._setoption()` changes the live
   configuration but not every `python-chess` `target_config` entry;
   `SimpleEngine.play()` may therefore restore defaults immediately before a
   search.
3. timing the whole `SimpleEngine.play()` call charges `ucinewgame`,
   `isready`, `position` and Python setup overhead to the moving side,
   especially on the first move.

The minimal trusted architecture is:

- the existing strict `UciEngine` owns each engine process directly;
- a clocked search operation sends exactly one `position` command followed by
  `go wtime ... btime ... winc ... binc ...`;
- the measured move interval starts immediately before the complete `go` line
  is flushed and ends when the complete `bestmove` line is received;
- every frozen option is sent once through `UciEngine`'s validated option
  handshake, then `isready` is acknowledged before `ucinewgame`;
- the UCI handshake is not treated as a generic banner-tolerant stream: it
  must begin with the exact authenticated Atomic-Stockfish startup line,
  followed by the exact `id name` and `id author` declarations and exactly
  one empty separator line before the first `option`;
- a freshly built, exact-path `pyffish` from the same clean source commit
  supplies legal moves, FEN transitions, insufficient-material state and the
  exact `Atomic::outcome()` ordering;
- the complete leg remains inside an externally bounded wrapper process, so a
  native crash or hang aborts the leg and produces no commit receipt;
- a second isolated native-verifier child independently replays and verifies
  every completed game before either leg can enter an accepted pair.

## Artifact and runtime bindings

The precommitted runtime manifest must bind:

- engine, current V3 and run3b network bytes;
- variant configuration and opening schedule;
- direct-UCI wrapper, native-referee helper and all imported mining modules;
- freshly built `pyffish` bytes and exact import path;
- CPython executable/version and compiler/linker identity used for `pyffish`;
- source commit/tree, clean `setup.py`/`src` proof and hashes of every binding
  source/header;
- all UCI options, clocks, maximum plies, command deadline, leg deadline and
  battery deadline.

The public runtime wire is `atomic-e00-runtime-manifest-v2`. It is built only
by `tools.atomic_mining.build_e00_runtime_manifest`, which:

1. authenticates the exact clean commit/tree and all 18 file/module inputs;
2. stages the exact nine-module runtime package and native binding snapshots;
3. invokes `DirectUciBackend.discover_runtime` through the real isolated
   `-I`/`runpy(..., run_name="__main__")` child bootstrap;
4. records an exact child import inventory under
   `atomic-e00-runtime-discovery-receipt-v2`;
5. starts no chess engine; and
6. publishes `build.receipt.json` last as the only runtime-design commit
   marker.

The native rules bundle is built separately by
`tools.atomic_mining.build_atomic_outcome_binding_cli` into a new directory
outside the authenticated source root. The builder rejects a dirty source,
wrong commit/tree, output reuse, in-tree output or post-build source drift.

Inputs are copied once to content-addressed, create-new snapshots.  Engines and
the rules binding run from those snapshots.  Original and snapshot bytes are
rehash-checked before the first leg, after every accepted pair and before the
final receipt.

An ambient `pyffish` import is always a hard failure.

All regular files in the runtime-package, native-build, input-snapshot and
rules-source namespaces are held by strict-static Windows guards for their
relevant lifetime. The runner checkpoints both file identity and directory
enumeration. This detects host-side residual creation and fails closed; it
does not claim that Windows prevents a cooperative external process from
creating a new name between checkpoints.

## Game loop

For each color-swapped leg:

1. start two single-use `UciEngine` instances with identical options except
   `EvalFile`;
2. authenticate and record the exact startup preamble, both engine IDs, the
   one empty post-ID separator, and all advertised options; acknowledge
   `isready`, send `ucinewgame`, and acknowledge `isready` again before
   starting clocks;
3. ask the native rules binding whether the root is terminal;
4. for each ply, send the position outside the charged interval, execute the
   clocked `go`, and receive one exact `bestmove`;
5. if the measured interval exhausts the mover's remaining clock under the
   frozen equality/rounding policy, adjudicate the opponent's mating material
   through the native binding and stop without applying the move;
6. otherwise require the move in the native legal-move set, subtract elapsed
   time, add increment, append the move, and query native outcome;
7. reject no-move, malformed output, illegal move, option drift, stderr,
   timeout, maximum-ply exhaustion or any cleanup uncertainty.

Only a complete pair is accepted.  There are no in-place retries.

The leg child, verifier child and discovery child all use
`atomic-e00-owned-process-v1` evidence. An accepted result on the success path
requires direct-child exit zero, `terminate_job_called=false`, and
`zero_active_after_cleanup=true`.

An isolated child that exits with code 70 must publish exactly one canonical
`atomic-e00-internal-failure-v1` record and no result record. The failure binds
the exact request SHA-256, child mode, an allowlisted stage for that mode,
failure class and exception type, but never exception text, paths or a
traceback. The parent validates that record and the owned-process evidence
before it may copy the diagnostic into an `atomic-e00-rejection-v2` terminal
record. Code zero plus a failure, code 70 plus a result, a missing or
non-canonical failure, or any mode/stage/digest drift is a hard failure.

## Required adversarial tests

- exact UCI command ordering and options on both first and second move;
- exact startup preamble, `id name`/`id author` and one empty separator, with
  omitted, moved, duplicated, type-coerced and digest-drift adversarials;
- no `target_config` or managed-option reset path remains;
- setup/position latency is excluded from the charged move interval;
- exact clock equality and sub-millisecond rounding boundary;
- hung search, hung wrapper and stubborn child cleanup;
- ambient/wrong/mutated `pyffish` and dirty/wrong rules source;
- native outcomes for rook material, rule50 99/100, prospective/current
  repetition, explosion priority, mate, stalemate and insufficient material;
- first-ply time loss and insufficient-material time-loss draw;
- content snapshot mutation before/during/after a pair;
- malformed, duplicate or stale `info`/`bestmove` output;
- isolated-child result/failure exclusivity, canonical diagnostics, request
  binding, mode/stage allowlists and owned-process evidence drift;
- final independent receipt/inventory/staging recomputation.

## GO checklist

- [x] fresh exact-path native binding builder has create-new/reopen tests;
- [x] direct clocked UCI wrapper has focused stub-engine integration tests;
- [x] no gameplay code calls `SimpleEngine.play()` or
      `AtomicBoard.outcome()`;
- [x] parent records strict owned-tree zero-descendant evidence;
- [x] runtime discovery exercises the exact isolated child bootstrap without
      starting an engine;
- [x] runtime package, native namespace, snapshots and rules source have
      strict-static guards and checkpoints;
- [x] focused packaging and integrated runner suites plus `py_compile` pass;
- [ ] independent code audit returns zero P0/P1;
- [ ] source is committed with an exact clean commit/tree;
- [ ] native bundle, schedule and runtime manifest are rebuilt from that
      commit using the exact runbook;
- [ ] resources are resampled and every production input is rehashed;
- [ ] a one-pair T1 real-engine smoke exits zero with byte-empty stderr;
- [ ] smoke is authenticated before the fixed 84-pair battery is launched.

The exact post-commit builder and runner commands are maintained in
[`e00-launch4-full-runbook.md`](e00-launch4-full-runbook.md). Any command-line,
hash, schema or timeout change requires a new design root and a fresh audit.
