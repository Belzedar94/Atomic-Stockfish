# Atomic test migration inventory

No Fairy-Stockfish test is removed silently during specialization. Every source
test is classified as ported, adapted to Atomic-only behavior, replaced by an
Atomic equivalent covering the same shared infrastructure, or not applicable
because it exists only for an excluded variant. The machine-readable binding
classification is in `tests/bindings/inventory.json`; its validator compares
the inventory with the frozen Fairy source instead of trusting hand-maintained
counts.

## Current executable coverage

The following evidence was reproduced locally on 2026-07-16 by
`tests/run_hito5.py` in release mode, including the cross-repository training
pipeline and full Node UCI/NNUE WASM launcher. The development-only
`--allow-missing-wasm` switch cannot produce a release pass.

| Surface | Migrated coverage | Current evidence |
| --- | --- | --- |
| C++ Atomic rules/state | Atomic SEE, explosion deltas, terminal captures, en passant, promotions, castling/Atomic960, repetition, rule 50, UCI moves, Atomic move-count thresholds, null-move reductions, capture-futility eligibility and capture-prefilter invariants | `88/88` PASS lines and terminal success marker |
| Shared C++ board API | SAN, outcomes, checked pieces, material, FEN validation and Atomic960 | `34/34` PASS lines and terminal success marker |
| Historical Python API | Frozen Fairy `test.py` contracts | `22/22`; two removed-variant APIs are classified, not skipped |
| Extended Python API | Fixtures, perft, errors, transactional calls, wheel layout, concurrent independent calls and pipeline/source contracts | Hito 4 lifecycle subset: `60 passed`; exact-head complete `tests/python`: `1420 passed`; sdist-to-wheel import and PEP 561 discovery passed |
| Fixture accounting | Frozen Python, JavaScript and perft source inventory | `58` fixtures, `22` Python source tests, `58` JavaScript source tests, eight perft vectors |
| CommonJS Board WASM | Full Atomic binding/lifecycle suite | `58` fixtures passed |
| ES-module Board WASM | Full Atomic binding/lifecycle suite | `58` fixtures passed |
| Cross-surface parity | Exact native/Python/CommonJS/ESM results | `40` shared fixtures; `25` native UCI intersections |
| Move generation | Eight historical Atomic/Atomic960 vectors plus focused rule/transition corpus | eight exact perfts and `19/19` focused checks |
| UCI search | Quiet Atomic checks/evasions, preserved analysis checks, mate-before-rule-50, stalemate ordering, terminal explosions, preservation of explosive captures in main search/qsearch and the Atomic NMP tactical-defense fixture | `16/16` with NNUE disabled and with the frozen network loaded |
| Comparison provenance | Stockfish/Fairy compiler-output parsing, bitness/ISA/debug-mode mismatch rejection, canonical immutable assets including every loaded psutil Python/native module, exact playing-net smoke, fail-fast workers/watchdog and process cleanup | combined LOS/compiler units `92/92` |
| XBoard/CECP | Atomic-only negotiation, clocks, state edits, analyze, playother, hard/easy and live ponder cancellation/promotion | complete protocol suite passed |
| Search repeatability | Two-position Atomic NNUE corpus over increasing node budgets | `12/12`; signature `338376` |
| Legacy Atomic NNUE | `false`, `true`, `pure`, invalid/truncated recovery, transactional load and byte-exact export | mode contract passed; network SHA-256 pinned |
| Pipeline reproducibility | Normative `atomic-schema.json`, Atomic-owned generation, thin tools validation/conversion, exact tools/trainer capability handshake, machine-readable sibling commits, tracked clean-build recipes, strict build manifests, exact HEAD/clean-tree/artifact pre/postflight, authenticated Git identity, pinned Python runtime/dependency provenance, locked strong-local fixture and trainer-generated synthetic CI fixture | Legacy strong-local passed with tools `521f8410`, trainer `350a28f2` and data hash `d95f5180...`. The current real `atomic-bin-v2` CI gate authenticates tools merge `450049ee` and trainer merge `3a19c16f`, both with engine `420c9f35`; it requires an explicit strong-local or synthetic-CI network profile, generates disjoint 128-position train/validation datasets, performs exactly one CPU update, serializes, reimports and loads the result in the playing engine. The older `40d2db22`/`3e5651a9` pass remains historical evidence only. |
| Atomic Syzygy | Atomic magics/suffixes, connected-kings domain 518, multi-directory paths, real 3-6-man WDL/DTZ, root/interior, six-man limit 5/6, five-position oracle/tbhit analysis, Atomic960 eligibility and recoverable paths | same-checkout Makefile driver, 13 fixture headers/hashes, driver `5/5` and production UCI suite with NNUE false/true are established. OpenBench IDs 37–42 provide six positive point estimates across same-binary classical/NNUE STC/LTC plus the requested Fairy NNUE comparison; the owner stopped and accepted them after healthcheck. The canonical evidence explicitly records that none completed 2,000 games or has `passed=true`, and does not claim aggregate `6/6` LOS. Exact-tag functional conformance remains a release gate. |
| Full engine UCI/NNUE WASM | Interactive Node launcher, external NNUE, true/pure, perft, terminal positions and pthread operation | integration passed; all four artifact hashes match the reproducible manifest |

The boundary between structural FEN validation and legal-game-history
reachability is recorded in [ADR 0002](adr/0002-atomic-analysis-fen-validation.md).
In particular, structurally valid Atomic analysis positions are not rejected
merely because the side to move attacks the opposing king.

Small evaluation differences against Fairy are diagnostic, not a bit-exact
acceptance condition. Deterministic structural failures remain bugs. Normative
correctness gates are unit tests and perft; strength evidence follows the
explicitly recorded policy for each playing change. The release 1.0 Syzygy
strength disposition is an owner waiver over stopped positive measurements and
must not be presented as a completed LOS or fixed-game gate.

## Source-to-test treatment

| Original surface | Atomic-Stockfish treatment |
| --- | --- |
| `test.py` | Port every generic and Atomic/Atomic960 contract. The partner-piece and fog-of-war APIs are explicitly not applicable; no runtime skip represents them. |
| `tests/perft.sh` | Preserve the eight exact Atomic/Atomic960 vectors and add explosion, check, en-passant, promotion, castling-rights, mate, stalemate and transition fixtures. |
| Protocol tests | Retain applicable UCI behavior and implement the complete Atomic XBoard/CECP surface. UCCI and USI are excluded protocols. |
| `tests/reprosearch.sh` | Use a fixed Atomic corpus and positively load the frozen network. |
| `tests/signature.sh` | Use the multi-position Atomic benchmark corpus, never an orthodox default bench. |
| Instrumented runtime | Exercise UCI, XBoard, threads and all NNUE modes; ASan, UBSan, TSan and Valgrind remain separate platform jobs. |
| `tests/js/test.js` | Account for all 58 source `describe` contracts and run the applicable Atomic API through CommonJS and ES modules. |
| Python packaging | Import the exact requested `pyffish` artifact, run historical and extended tests, and exercise concurrent calls. |
| Board WASM lifecycle | Repeated construction/destruction, push/pop/reset, perft, Atomic terminals and error rollback share the 58-fixture suite. |
| UCI/NNUE WASM | Run the actual interactive search engine and load the external SHA-pinned network; this is distinct from the lightweight Board WASM library. |
| Atomic data schema | Validate the exact UTF-8/LF bytes, hash, 72-byte layout, legacy move wire, hand-count fields, clock order, little-endian host requirement and intentionally unsupported v1 states; require matching tools/trainer capabilities before generation. |
| Atomic BIN V2 contract | Validate exact UTF-8/LF data/manifest schema bytes and hashes, 96-byte header, 48-byte canonical Atomic/Atomic960 position, 64-byte record, independent 32-bit move mapping, golden special moves including stationary-king Atomic960 castling, count/size overflow, SHA-256 boundaries, exclusive single-writer sink/rollback with replacement detection, canonical mandatory sidecar, native generation, Atomic960 flagging, Syzygy rejection and fail-closed corrupt wire/adapter behavior. H7.3-C1 adds the authoritative manifest-only entrypoint, exact frozen-schema filename validation, shared exact `keep_draws` normalization, absolute path capture, lazy private SHA-authenticated per-shard snapshots, process-wide thread-safe Atomic initialization, nonblocking POSIX FIFO rejection, legal Atomic/Atomic960 decode, byte re-encode, aggregate EOF reconciliation and indexed failures. H7.3-C2 adds the separate production data-tools binary, explicit format/manifest-only CLI, canonical capabilities/stats/errors and exit classes, lossless decimal-string `uint64` counters tested above `2^53` and at `UINT64_MAX`, valid multi-shard streaming plus authenticated and semantic corruption fixtures, and playing/writer source-list isolation. H7.5 freezes a SHA-advertised JSONL decode schema and a `1..4096` bounded slice operation that authenticates/decodes/re-encodes the whole dataset before atomic stdout, preserves FEN/Shredder-FEN and every V2 semantic field, and pins exact bytes, all move types, Atomic960, max ply, cross-shard indexes, Unicode, raw/output-file rejection, bounds and post-slice corruption. Pinned tools delegation remains a separate sibling integration. |
| NNUE pipeline | The Legacy Atomic V1 generator/decode/train/serialize/re-import E2E is mandatory from Hito 5 onward and must run from clean, SHA-pinned sibling repositories. `strong-local` uses the external frozen playing net; `synthetic-ci` creates a deterministic ephemeral trainer net so public CI redistributes no strong weights. Hito 7 adds `atomic-bin-v2` and generator consolidation without weakening either profile. |
| AtomicNNUEV3 runtime | H9.3n promotes the byte-frozen V3 format to the third production dispatcher backend without rewriting its archival wire schema. C++ and an independent Python decoder retain the scalar/incremental/SIMD oracle gates across 102 frozen positions, targeted rows/buckets, Atomic960, EP, signed truncation, adversarial boundaries, rollback and 1/2/4/8 threads. Native, data-generator and pthread Node UCI/WASM tests require V1/V2/V3 switching, V3 `true`/`pure` searches, byte-exact export/reimport and a classical rollback path. This is a compatibility/correctness gate; the non-publication bootstrap nets are not 1.0 strength evidence. |

## Mandatory execution matrix

At every major milestone, all implemented surfaces run in these configurations:

1. Portable release and optimized release.
2. Debug/assert.
3. ASan and UBSan on Linux.
4. `Use NNUE=false`.
5. `Use NNUE=true` with a positive frozen-network load assertion.
6. `Use NNUE=pure` with the same assertion.
7. Native UCI and XBoard.
8. Python, CommonJS, ES module, Board WASM and UCI/NNUE WASM.
9. No eligible table, eligible local Atomic table, and corrupt/missing table
   directories.

The `pure` matrix entry protects the data-generation contract only. Speed and
Elo/LOS comparisons always use the normal playing mode, `Use NNUE=true`.

Perft itself does not evaluate positions, but NNUE-on jobs still assert that
the expected network was loaded before running the vectors.

## Coverage accounting policy

- Fixture identifiers are sorted and unique; required Atomic/Atomic960 rule
  tags are validated.
- Inventory source line numbers and source-file hashes are checked against the
  frozen Fairy checkout.
- Applicable source tests must reference at least one Atomic fixture.
- A `not-applicable` row cannot cite a fixture and must include a rationale.
- A test may be deleted only in the same change that records its exclusion or
  Atomic replacement.
- New skips are forbidden except for a documented platform dependency. The
  Hito 4 runner fails at the first failing command and pins the expected test
  counts so a skip or silently reduced collection cannot become a pass.
