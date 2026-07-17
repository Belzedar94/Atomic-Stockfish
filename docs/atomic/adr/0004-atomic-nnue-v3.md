# ADR 0004: Design AtomicNNUEV3 as a blast-aware SFNNv15 backend

- Status: accepted; wire v1 frozen; private scalar, incremental, SIMD and trajectory backends implemented; runtime dispatch and serialization promoted in H9.3n; production scanning and controlled training remain pending
- Date: 2026-07-14

## Context

AtomicNNUEV2 proves that the modern SFNNv15 dense head, strict mixed-version
dispatch, serialization and the full engine/trainer pipeline work without
changing Legacy Atomic V1. V2 intentionally retains the historical 45,056
HalfKAv2Atomic inputs, however, so a large V2 training campaign could not tell
whether modern Atomic features are better than the representation created for
Fairy-Stockfish years ago.

Current official Stockfish uses horizontally mirrored HalfKAv2 together with a
separate i8 threat slice. The dense graph remains the 1024-wide SFNNv15 head
already implemented by V2. The useful architectural lesson is the separation
of stable piece-square inputs from dynamic relations; the orthodox
`FullThreats` semantics are not suitable as the production Atomic relation
slice. They include pawn pushes, omit Atomic blast collateral and do not
distinguish a direct check, an enemy-king explosion threat and a capture that
would explode the moving side's own king.

Community history also makes full refresh the required correctness oracle for
Atomic NNUE. Explosions remove multiple pieces, quiet moves can open several
sliding capture rays, en passant removes a pawn away from the explosion center,
and touching kings create legal relations that do not exist in orthodox chess.
An incremental implementation is acceptable only after it is bit-identical to
an independent full-board enumerator.

## Decision

1. Keep Legacy Atomic V1 and AtomicNNUEV2 byte-exact. Add V3 as a third,
   independently versioned backend with reserved file version `0xA70C0003`.
   No V1 or V2 file is reinterpreted.
2. Reuse V2's byte-identical SFNNv15 dense tail and architecture hash
   `0x63337116`. V3 changes only the feature transformer, accumulator and the
   corresponding structural hashes.
3. Use four jointly oriented slices for each accumulator perspective. Compute
   that perspective's orientation independently: for the black perspective
   flip the board vertically, then mirror horizontally when that perspective's
   pre-horizontal own king is on files a-d. WHITE and BLACK may therefore use
   different horizontal mirrors for the same position. Within one perspective,
   HM piece squares, CapturePair from/to squares, KingBlastEP centers and kings,
   and BlastRing centers and collateral squares all use the same two XORs.
   Actor and collateral relations relabel colors but never cause a second
   rotation or mirror. "Never mix orientations" means never mix them among the
   four slices of one perspective, not that both perspectives share one mirror:

   | Slice | Physical dimensions | Trainer dimensions | Wire weight | PSQT |
   | --- | ---: | ---: | --- | --- |
   | HalfKAv2Atomic_hm | 22,528 | 24,576 + 768 virtual | i16 | yes |
   | AtomicCapturePair | 40,012 | 40,012 | i8 | no |
   | AtomicKingBlastEP | 2,304 | 2,304 | i16 | no |
   | AtomicBlastRing | 10,240 | 10,240 | i8 | no |

   The physical total is 75,084 rows. The trainer has 77,132 ordinary input
   rows and another 768 virtual factorizer rows, for 77,900 parameter rows.
4. `HalfKAv2Atomic_hm` follows the modern 12-plane training and 11-plane
   export mapping. Non-terminal positions use the compact `KING`; evaluation
   after either king has exploded is forbidden. Horizontal mirror invariance
   is a bit-exact metamorphic gate, including Atomic960 and EP fixtures. Square
   ordinals are rank-major from A1=0. Training planes are own/opponent pairs in
   P, N, B, R, Q, K order; export merges the two king planes into plane 10 by
   taking the opponent-king rows and replacing the oriented own-king square
   with its own-king row. For an oriented king on files e-h, the bucket is
   `(7-rank)*4 + (7-file)`, ordered h8=0 through e1=31.
   Every active training feature contributes both its bucket-specific row and
   the matching 768-row virtual factor. For every output column, coalescing is
   `coalesced[b,p,s,o] = bucket_weight[b,p,s,o] + virtual_weight[p,s,o]`.
   Coalesce all 1,032 outputs first, then apply the 12-to-11 mapping. The first
   1,024 outputs become accumulator weights and the last eight become PSQT, so
   factorization and king-plane merging are byte-identical for both tensors.
5. `AtomicCapturePair` records occupancy-based pseudocaptures, not legal moves.
   It deliberately retains pinned, checked and self-blasting candidates so the
   network can learn why they are unusable. Kings are excluded as actors, pawn
   pushes are absent, sliders stop at the first blocker, and EP uses a compact
   cold tail rather than a normal victim class. Its 3,332 directed geometric
   edges are 84 pawn, 336 knight, 560 bishop, 896 rook and 1,456 queen edges.
   Actor relation is own or
   opponent relative to the accumulator perspective.
   The joint position orientation leaves own pawns moving north and opponent
   pawns moving south. CapturePair therefore has two actor-relative 84-edge
   pawn lookup tables. Each is ordered by the jointly oriented from/to squares;
   no second flip is applied, so capture destinations remain spatially aligned
   with KingBlastEP and BlastRing centers.
   Edge ordinals are lexicographic by ascending oriented A1=0 from-square and
   then ascending oriented to-square. Segment bases are pawn 0, knight 84,
   bishop 420, rook 980 and queen 1,876. The hot normal rectangle has six
   victim classes P, N, B, R, Q and K and uses the branchless local formula
   `((actor_rel * 3332 + edge) * 6 + target)`, giving rows 0 through 39,983.
   `actor_rel` is OWN=0 exactly when the actual actor color equals the
   accumulator perspective; it only relabels color and never applies a second
   square transform. The physical index is `22,528 + local_index`.

   En passant occupies a compact cold tail instead of reserving an impossible
   seventh target class for all 3,332 edges. For each actor relation, enumerate
   14 `(oriented_from, oriented_center)` pairs lexicographically. OWN is
   `a5-b6; b5-a6,b5-c6; ...; h5-g6`, while OPP is
   `a4-b3; b4-a3,b4-c3; ...; h4-g3`. Its local formula is
   `39,984 + actor_rel * 14 + ep_ordinal`, giving rows 39,984 through 40,011.
   This retains all 28 geometric EP rows and removes 6,636 impossible rows
   from the former 46,648-row rectangle; there are no holes or zero-reserved
   rows. This compaction does not assert that every normal row is structurally
   reachable; the authenticated reachability bitmap remains authoritative for
   that separate question.

   EP metadata fails closed before any cold-tail index is formed. The raw EP
   center must be on rank 6 for White to move or rank 3 for Black to move, be
   empty, and have an opponent pawn at
   `center - pawn_push(side_to_move)`. Each origin must contain a side-to-move
   pawn and its jointly oriented origin/center must occur in the appropriate
   14-edge table. A failure emits no EP feature and supplies no EP candidate to
   downstream slices, while normal CP enumeration continues. Static evaluation
   does not reconstruct the prior move. Once these metadata checks pass, pins,
   check evasion and self-blast remain deliberately unfiltered.

   A pawn capture onto its promotion rank activates one PAWN-actor relation,
   not one row per promotion choice. A piece already promoted on the board is
   classified by its current N, B, R or Q type, never by move history. Emitted
   active indices are unique and strictly ascending. The enumerator uses only
   caller-owned output/scratch storage, retains no references after return and
   has no mutable static or global state. It is reentrant for concurrent reads
   of immutable positions; callers synchronize any Position mutation. This
   exact CP set is the sole candidate source for the two downstream relation
   slices.
6. `AtomicKingBlastEP` is a boolean set over 64 centers, two actor relations
   and 18 frozen classes relative to the capture actor: enemy king at the
   center, eight enemy-king blast offsets, eight actor-own-king/self-blast
   offsets and an EP marker. Its local index is
   `((oriented_center * 2 + actor_rel) * 18 + class)`, spanning 0 through
   2,303; physical rows are 62,540 through 64,843. The full rectangle is
   serialized without holes, while off-board directional rows remain inactive.
   Several attackers of one center never multiply the same relation weight.
   Emission is the sorted, unique boolean union, so its result never depends on
   CapturePair traversal order. When both
   king-offset classes are present, that center would explode both kings and
   the candidate is illegal because it is self-blasting, but remains emitted
   because CapturePair is deliberately unfiltered. Touching kings and
   their mutual king immunity are represented by HM; kings are not CP actors.
   For every directional class, `related_king = oriented_center + delta`; for
   example, `ENEMY_KING_N` means the enemy king is one rank north of the center.
   File/rank adjacency is checked explicitly to prevent edge wrap. Enemy/own is
   relative to the capture actor, but the delta remains in the joint
   accumulator-perspective frame. The EP marker is active exactly when the CP
   set contains at least one validated geometric `EN_PASSANT` candidate for
   that landing center and actor relation. Two possible EP origins deduplicate
   to one marker; the captured pawn off-center is never treated as the center.
   Malformed EP metadata is not a global CP error: normal CP/KBR projection is
   preserved and only the EP tail and marker are absent. The marker may coexist
   with either king-offset class. `ENEMY_KING_CENTER`
   is instead restricted to a normal CP target `KING`; it describes a direct
   enemy-king pseudocapture target, not a legal-check predicate.

   KingBlastEP calls CapturePair once and projects that exact output. It does
   not construct candidates from attack maps, legal move generation or an
   independent EP read. CP errors propagate with an empty output, including
   missing-king terminal snapshots that the engine adjudicates before NNUE.
   Pinned, checked, self-blasting and otherwise illegal candidates stay present
   because this is a learned relation, not a legality filter. The maximum of 35
   active rows follows from 17 king relations per actor relation (nine enemy
   center/neighbor positions plus eight own neighbors), twice, plus one EP
   marker belonging to the single side-to-move actor relation. The emitter uses
   only caller-owned fixed storage and is reentrant for concurrent immutable
   Position reads; callers synchronize mutation. Historical Fairy-Stockfish
   implementation evidence motivating the full-refresh oracle, EP gates and
   separation from check semantics is catalogued in
   `docs/atomic/evidence/hito9-3d-king-blast-ep/discord-research.md`.

   The reusable no-reenumeration projector is an internal trusted seam, not an
   untrusted input API. Its precondition is the exact successful CapturePair
   emission for the same snapshot and perspective. Defensive checks validate
   orientation, indices and record shape, but deliberately do not reenumerate
   CP to prove completeness; the combined V3 call graph owns that invariant.
7. `AtomicBlastRing` is a boolean compact union over 64 centers, two actor
   relations, two collateral relations, eight offsets and five classes:
   knight, bishop, rook, queen and an adjacent pawn that survives. Its local
   index is
   `((((oriented_center * 2 + actor_rel) * 2 + collateral_rel) * 8 + offset) * 5 + class)`,
   spanning 0 through 10,239; physical rows are 64,844 through 75,083 and the
   next exclusive physical row is 75,084. The order is centers A1=0 through
   H8=63, actor OWN/OPP, collateral OWN/OPP, offsets N, NE, E, SE, S, SW, W,
   NW, then N, B, R, Q and `ADJACENT_PAWN_SURVIVES`. The full rectangle is
   serialized without holes; off-board and structurally impossible rows remain
   inactive.

   BlastRing calls CapturePair exactly once and projects that exact successful
   unfiltered emission. It never reconstructs candidate centers from attack
   maps, legal moves or a second read of EP metadata. CP records are first
   grouped by `(oriented_center, actor_rel)` and distinct origins are counted
   for the complete group. A collateral square matching an origin is excluded
   only when that group has exactly one distinct origin. If the group has two
   or more origins, retain every adjacent origin: another CP outcome can capture
   the same center while leaving that square as exploded collateral or as a
   surviving pawn. This prevents a sole capturing pawn from being mislabeled
   as a survivor without discarding multiple-route information. Duplicate
   routes are idempotent and the boolean result is emitted in strict ascending
   local-index order, independent of CapturePair traversal.

   Inspect the eight occupied neighbors in the same jointly oriented
   pre-capture snapshot. Actor and collateral OWN/OPP are both relative to the
   accumulator perspective; collateral is never relative to the actor and
   neither relation applies another transform. Spatially,
   `collateral_square = oriented_center + delta`; `N` means one rank north,
   with explicit file/rank edge checks. Current N, B, R and Q pieces activate
   their class because the blast removes them. An adjacent pawn instead
   activates `ADJACENT_PAWN_SURVIVES`, because pawns are immune to collateral
   blast. An adjacent king emits no ring row because KingBlastEP owns all king
   relations. Current snapshot type is authoritative, including promoted
   pieces; promotion choices never multiply a relation.

   For EP, the landing square is the center. The off-center captured pawn at
   `center - (actor_rel == OWN ? 8 : -8)`, equivalently the orientation of
   `raw_ep_center - pawn_push(actual_actor_color)`, is always excluded because
   it is removed before the blast. This exclusion holds even when the group has
   two origins. Malformed EP contributes no EP candidate while successful
   normal CP groups and their ring projection remain unchanged. A CP error maps
   to an empty BlastRing result with no partial projection; king-absent
   explosion terminals are adjudicated before NNUE.

   The reusable no-reenumeration projector is an internal trusted seam with the
   same precondition as KingBlastEP: the exact successful CapturePair emission
   for the same snapshot and perspective. Shape checks can reject corrupt
   orientation, indices or records but cannot authenticate completeness of an
   arbitrary caller-built subset. The emitter retains no Position or output
   references, uses caller-owned fixed storage and is reentrant for concurrent
   immutable reads. Its conservative active bound is `30 * 8 = 240`: at most
   30 non-king collateral pieces, each adjacent to at most eight candidate
   centers, with each center/collateral pair fixing one actor relation. Kings,
   sole origins and off-center EP exclusions only reduce that count. Normative
   engine evidence and auxiliary community history are catalogued in
   `docs/atomic/evidence/hito9-3e-blast-ring/discord-research.md`.
8. Before introducing any weights, compose the four scalar slices through one
   fail-closed full-refresh oracle per perspective. The Position adapter takes
   one immutable board/side-to-move/EP snapshot. HM is emitted exactly once;
   an internal trusted HM-to-CapturePair seam emits CP exactly once using that
   exact orientation; the same immutable CP object is then passed by reference
   to the KingBlastEP and BlastRing projectors. The composer never calls the
   three standalone relation APIs, because doing so would enumerate HM four
   times and CapturePair three times.

   `CapturePairError` is the lossless combined error domain. Any HM, CP,
   KingBlastEP or BlastRing error clears the whole typed bundle, so callers
   never observe a successful prefix. Missing-king terminals remain errors at
   this isolated boundary and must be adjudicated before engine evaluation.
   Malformed optional EP remains successful and preserves all normal rows.
   Every slice must carry the identical `JointOrientation`, the aggregate
   active count must not exceed `32 + 240 + 35 + 240 = 547`, and all scratch
   and results remain caller-owned and reentrant for immutable concurrent
   reads. Defensive trusted-seam validation does not claim to prove an
   arbitrary caller-built subset complete without re-enumeration.

   This step is deliberately not a network backend. It changes no weights,
   numeric accumulator policy, SIMD, incremental state, loader, serializer,
   dispatcher, UCI, WASM, generator or trainer surface. Those remain blocked
   on the numeric range proof and frozen hashes below.
9. Serialize in this exact order after the standard outer header: transformer
   hash, i16 biases, HM i16 weights, CapturePair raw i8 weights, KingBlastEP i16
   weights, BlastRing raw i8 weights, HM-only i32 PSQT, eight V2 SFNNv15 stacks
   and strict EOF. i16/i32 arrays use the canonical V2 SLEB framing, whose u32
   field is the compressed byte count rather than the number of elements. SIMD
   permutation is per slice: 16-byte output blocks for i16 and 8-byte output
   blocks for i8. File parameters are feature-major with 1,024 contiguous
   output values per row, canonical and unpermuted. Raw i8 is signed two's
   complement. Loading permutes only after strict parsing succeeds; saving
   unpermutes a copy and never mutates the live network.
   The exact transformer shapes are biases `[1024]`, HM `[22528][1024]`,
   CapturePair `[40012][1024]`, KingBlastEP `[2304][1024]`, BlastRing
   `[10240][1024]` and HM PSQT `[22528][8]`. The eight PSQT buckets are
   contiguous inside each feature-major HM row and are never SIMD-permuted.
   V3 inherits V2's one runtime bucket
   `clamp(integer_divide(piece_count - 1, 4), 0, 7)`. That same bucket selects
   both HM PSQT and the SFNNv15 dense stack, is counted once in dataset
   statistics and is serialized once in the feature-input decontamination key.
   The trainer therefore uses a V3-specific composed transformer: HM owns 1,024
   accumulator columns plus eight trainable PSQT columns, while every relation
   slice owns exactly 1,024 accumulator columns and no PSQT parameter. Merely
   initializing generic relation-PSQT columns to zero is incorrect because the
   upstream composed forward would train them and make export differ from
   inference.
10. Compute every slice hash with FNV-1a-32 over one exact ASCII descriptor, then
   combine the ordered HM, CapturePair, KingBlastEP and BlastRing hashes with
   the official rotate-left/XOR fold. Separately hash the global transformer
   descriptor, which authenticates mixed dtypes, wire order and SIMD
   permutation. The transformer hash is feature-hash XOR 2,048 XOR global
   descriptor-hash; XOR the unchanged architecture hash for the network hash.
   Do not use `std::hash` or serialized JSON as a wire identity.
   The frozen descriptors authenticate the independent
   per-perspective mirror rule, virtual-factor coalesce and 12-to-11 mapping,
   CP segment bases and geometric EP rule, center-to-related-square polarity,
   BlastRing origin/pawn rules and the exact PSQT shape/order. A golden alone
   does not change a wire identity.
11. Freeze wire v1 only from reproducible exact descriptor bytes. The slice
    hashes are HM `0xA34A8666`, CapturePair `0x9AEDB186`, KingBlastEP
    `0xF5172BC0` and BlastRing `0x38377946`; their ordered fold is
    `0xA3FBDBE8`. The 799-byte global transformer descriptor is copied exactly
    in C++, JSON and Python and hashes to `0xCC31067A`, giving feature
    transformer hash `0x6FCAD592` and network hash `0x0CF9A484` with the
    unchanged architecture `0x63337116`. File version `0xA70C0003` is frozen.
    The previously proposed descriptor hash `0x74198ECE` and its derived
    hashes are rejected because no exact descriptor bytes reproduce it.

    H9.3g supplies a private transactional canonical reader/writer and fixture,
    but the engine dispatcher still recognizes only V1 and V2. A production
    V3 loader is forbidden until the scalar backend is bit-exact against the
    full-refresh oracle. HM-only PSQT is the wire-v1 engineering decision;
    adding relation PSQT requires a new descriptor and network identity.
12. Use an independent full-refresh oracle and the runtime accumulator with i32
    state. It enumerates
    semantic indices directly from the board, adds i8 weights with sign
    extension, checks range before any narrowing, and compares index sets,
    accumulators, PSQT, transformed bytes and raw output. The conservative
    dtype-times-active-maxima envelope is `[-2,289,664, +2,289,116]`, so i16 is
    not a valid representation and i32 is safe without claiming that one legal
    position simultaneously reaches every per-slice maximum.

    PSQT state is i64. For each bucket the reader promotes before taking a
    magnitude and requires the sum of the 32 largest absolute HM PSQT weights
    to be at most `INT32_MAX`; this rejects `INT32_MIN` without overflowing
    `abs(i32)`. Every dense output independently requires
    `abs64(bias) + 127 * sum(abs64(weight)) <= INT32_MAX`. In addition, each
    stack derives signed intervals and gates `fc2 + fc0[30] - fc0[31]` to
    `[-3,665,038,760, +3,665,038,759]`, the exact asymmetric pre-scale range
    whose `* 9600 / 16384` result fits i32 under truncation toward zero. The
    skip, multiplication and division all execute in i64 before a checked
    narrow. The inherited PSQT convention is frozen as
    `(first_perspective - second_perspective) / 2`, also in i64 with signed
    truncation toward zero before the checked i32 raw result; the network output
    then applies the inherited `/ 16` output scale, again truncating toward
    zero. Product code may never wrap, saturate,
    cast after an overflowing expression or truncate an active list.

    H9.3h implements this contract as a private scalar full-refresh backend.
    It consumes the strict H9.3g `Network`, publishes canonical i32
    accumulators and i64 PSQT diagnostics, executes the exact SFNNv15 transform
    and every dense intermediate, and fails transactionally. Its frozen
    identity/AVX2-LASX/AVX512 diagnostic fingerprint is
    `0x46F68EAB20FF9D50`. A separate adversarial dense seam covers signed block
    boundaries, skip outputs, affine narrowing and the reachable post-layer
    raw-composition rejection. This satisfies the scalar-oracle precondition;
    it does not authorize dispatcher, SIMD, incremental, protocol or training
    integration.
13. Treat EP state as accumulator input. A search null move clears `epSquare`
    without pushing the NNUE accumulator, so V3 state stores the EP square used
    for its last computation and refreshes both perspectives when the current
    position differs. This guard runs before any `computed` early return.
    Relation slices do not reuse the HM Finny cache; any future relation cache
    includes EP in its key. The mandatory regression is parent-with-EP, null,
    eval, undo-null and parent eval without an accumulator push. It begins with
    both parent perspectives computed, reaches the EP guard with that computed
    state, invalidates both relations before the early return, and records real
    HM Finny hits for both perspectives while relations refresh. Undoing null
    changes the EP key back and forces a second two-perspective relation refresh;
    the final accumulators, PSQT, transformed bytes and raw output must be
    bit-identical to their pre-null values.
14. `atomic-bin-v2` remains the dataset format. It already stores the full
    canonical position needed to derive every V3 slice. V3 adds an authenticated
    statistics layer and feature-schema SHA to the training-run manifest, not a
    third position record format. Because bin-v2 records intentionally contain
    no game ID, the generator buffers each generated game and emits one compact
    binary trajectory ledger per role. Its label-free split-group hash covers
    the canonical root, complete played move sequence and Atomic960 flag. Result
    and stop reason are authenticated by the ledger but excluded from the split
    identity, so relabeling cannot move the same positions across partitions.
    One domain-separated hash assigns every retained sample from the complete
    trajectory to train or validation; the final game may retain only the sample
    prefix needed to meet the requested record count, while its ledger still
    preserves the complete played sequence.

    Authentication and replay are two different gates. The portable Python
    bundle validator streams and authenticates the manifest, shards and ledger,
    checks every fixed-width field, recomputes split-group and partition hashes,
    and verifies that record and move ranges are complete and contiguous. It
    does not claim that an arbitrary 48-byte position or 32-bit move is legal
    Atomic. Accordingly, the portable stats and split-audit booleans are named
    `all_ledger_entries_structurally_scanned` and
    `full_ledger_structural_scans`; no portable structural result is called a
    replay. A separate engine-backed scanner must set every canonical root,
    replay every played move, compare every retained pre-move position, validate
    each stored best move independently and reproduce the terminal outcome
    before a dataset can be published or used for a release-candidate run.

    The current ledger cannot prove score-draw or evaluation-resignation
    adjudications: the former needs the complete score window and the latter
    also depends on the resignation counter and per-game random decision.
    Their wire IDs 7 and 8 remain reserved so pilot artifacts can be inspected,
    but a release-candidate policy requires both counts to be zero. V3 release
    generation therefore binds a normalized semantic hash of each frozen V2
    `manifest.generation` object. The hash excludes only
    `requested_records`, `records_per_shard` and `random_file_name`, which are
    role-specific volume/layout choices; it retains Atomic960, Threads, Hash,
    NNUE mode and every search, randomization, adjudication and filter option.
    Train and validation must have the same normalized hash. The actual V2
    manifest authenticates `adjudicate_draws_by_score=false`. A policy field
    also records `adjudicate_resignations=false`, but this is only a structural
    attestation until H9.3b adds producer/manifest evidence. It cannot satisfy
    publication readiness by itself. Re-enabling either policy for a release
    dataset requires a new evidence-bearing ledger version and an engine-backed
    verifier for that evidence; a self-declared boolean is not a substitute.

    Train and validation each have a separate full-scan stats sidecar. Every
    distribution, feature class and semantic counter is reported separately for
    WHITE and BLACK accumulator perspectives. A compact `u64-le` companion keeps
    all 200,856 physical, HM-training and virtual-factor activation counters,
    followed by twelve canonical structural-reachability bitmaps: four
    physical slices plus HM training and HM virtual for each perspective. The
    physical bitmaps come from an independent symbolic feature oracle; HM
    training and virtual bitmaps must equal the exact 12-to-11/bucket projection
    of physical HM bits, so they are not a second trusted oracle. No bitmap is
    derived from `counter > 0`. Each bitmap popcount defines its own coverage
    denominator; counters only distinguish observed from unseen reachable
    indices, and a nonzero counter under an unreachable bit is fatal. Histogram boundaries and
    acceptance thresholds live in a
    precommitted hashed policy, never in post-hoc mutable prose. A separate
    split audit external-sorts raw-record, V3-feature-input and split-group keys
    and requires all three train/validation intersections to be empty. A
    trajectory split alone cannot prevent two different games from reaching the
    same NNUE input. Before publication, the pipeline therefore external-sorts
    train feature-input keys and deterministically omits matching validation
    records, then regenerates its manifest, ledger ranges and stats. Generation
    continues until validation still satisfies the precommitted policy after
    this decontamination. Historical bin-v2 data cannot receive the game-level
    guarantee retrospectively because its filtered played moves were never
    retained.

    The release producer implements train-wins decontamination as two
    sequential retention phases without changing the frozen partition hash.
    It first fills only train, external-sorts the exact V3 model-input keys,
    then retains validation records only after an exact disk-backed lookup
    proves they are absent from train. Bloom state may reject an unnecessary
    disk lookup but can never establish absence. Filtered collisions are not
    counted; generation continues until both requested role counts are exact.
    Before publication, the retained validation keys are independently
    external-sorted and merge-compared with the train set. Split-group IDs are
    likewise externally sorted per role and any duplicate is fatal. Every
    external sort uses at most 64 MiB initial runs and deterministic multipass
    merges with fan-in 64, so release-scale audits open at most 64 run readers
    plus one synchronized output while preserving the same canonical bytes and
    hashes.

    `game_id` is deterministic producer scheduling state, not a new ledger
    field or sidecar. For a fixed complete configuration, including `Threads`,
    worker `i` owns IDs `i, i + Threads, ...`; each game receives a
    domain-separated seed from `(generation_seed, game_id)`, owns its TT and
    search histories, and the coordinator commits completed games in total ID
    order. Games speculatively completed beyond the stopping ID, filtered
    candidates and resulting holes are operational diagnostics only. The
    authenticated retained contract remains the canonical root and complete
    moves behind each `split_group_id`, the generation/split seeds, configured
    Threads and the final artifact hashes. Re-running the same configuration
    and Threads must produce byte-identical data and ledger payloads; log
    first/last/count fields do not substitute for that evidence.

    Network and opening-book pins are computed from locked regular,
    non-symlink/non-reparse byte snapshots, and the parsers consume those exact
    bytes without reopening the names. All six role artifacts remain in a
    private directory through semantic replay, exact set audits and global
    hash/size reread. Publication uses exclusive hard links under one fsynced
    recovery journal, globally re-reads all public names, and removes the
    journal only after the transaction is complete. Checked rollback removes
    only names still identifying the owned staged inode; an identity mismatch
    fails closed and retains the journal for recovery. This temporary journal
    is transaction metadata, not a seventh DAG artifact.

    Active-count summaries preserve the proven per-perspective bounds for an
    evaluable nonterminal position with both kings and at most 32 board pieces;
    a king-absent terminal is resolved before NNUE enumeration. HM's tight bound
    is 32 (one feature per board piece). CapturePair is conservatively at most
    `30 * 8 = 240`: every non-king actor belongs to one actor relation and has
    at most eight candidate edges, with an empty en-passant target substituting
    a pawn diagonal rather than adding one. KingBlastEP is conservatively at
    most `2 * (9 + 8) + 1 = 35`: enemy-king center/ring plus own-king ring for
    two actor relations and one deduplicated side-to-move EP marker. BlastRing
    is conservatively at most `30 * 8 = 240`: each non-king collateral piece
    has at most eight adjacent occupied-or-EP centers, and each center/collateral
    pair fixes one actor relation. For each
    perspective, `scan.max_active_observed` is recomputed as the maximum over
    records of the sum of those four active counts. It must be at least every
    reported per-slice maximum, at most their conservative independent-slice
    sum `32 + 240 + 35 + 240 = 547`, and no greater than 547. The 1,024-entry
    capacity therefore has 477 entries of proved headroom; 547 is a safe upper
    bound in the physical/runtime-export activation domain and does not claim
    that all slice maxima are simultaneously attainable. Factorized training
    additionally activates at most 32 HM virtual parameter rows, so its distinct
    parameter-row bound is `547 + 32 = 579`; runtime export coalesces each such
    virtual row into its physical HM row, so it does not raise the runtime bound.
    Histogram boundaries for ply and
    rule50 are nonnegative, while piece-count boundaries are restricted to
    0..32. The two 32-entry HM king-bucket histograms and the shared eight-entry
    network-bucket histogram have different meanings and are not elementwise
    comparable, but each sum must equal the same scanned-record count.

    The training-run manifest is the root of trust. It hashes both manifest,
    ledger, per-index coverage and stats sets, the policy, the split audit and
    this feature schema. Its acyclic input-bundle hash is embedded in every
    checkpoint; the completed run manifest can then authenticate checkpoint,
    network, logs and metrics without a self-referential hash. Publication is
    transactional: no final sidecar remains visible if any member fails
    validation. A publishable run has at least one optimizer step, a nonzero
    validation interval and nonempty checkpoint and network artifacts; a
    zero-byte file with a syntactically valid digest is not a completed output.
    The policy, both statistics sidecars, split audit and optional training-run
    manifest use one frozen JSON wire: UTF-8 without BOM, schema-declaration key
    order, minified separators and exactly one trailing LF. The validator reads
    each through one regular, non-symlink handle with a 16 MiB bound and matching
    pre/post `fstat`; whitespace/order variants and files changed during parsing
    are rejected before their declarations can become trusted inputs. Atomic
    bin-v2 manifests use the same canonical wire with their separate 64 MiB
    shard-list bound, and all later provenance checks consume the already
    authenticated in-memory summary rather than reopening the pathname.
15. Keep orthodox `FullThreats` only as an ablation control. The serious order
    is HM, then CapturePair, then KingBlast/EP, then BlastRing. Every comparison
    uses the same dataset, seed, training budget and dense head.
16. Stage incremental execution by slice and keep it private until every event
    is bit-identical to H9.3h. The first scalar stack incrementally maintains
    only HM and HM PSQT. It derives sorted active HM rows from the immutable
    post-move snapshot, subtracts the old-only rows before adding the new-only
    rows in i64 scratch, publishes i32 only after the frozen range check and
    rebuilds from biases when the joint orientation changes. `DirtyPiece` may
    retain stack/API compatibility, but it is not an oracle for global Atomic
    relations: a quiet blocker move can change distant CapturePair,
    KingBlastEP and BlastRing rows. Those three slices therefore refresh from
    the exact current full emission and are combined only in temporary output.

    Search null moves remain a no-stack-push path. The stack compares the
    current EP square with `epSquareWhenComputed` before any same-frame reuse,
    refreshes both relation perspectives for parent EP, cleared null EP and
    restored parent EP, and always composes the dense input using the current
    side to move. A failed feature, numeric, network-identity or scalar
    composition publishes zero and leaves the prior frame unchanged. Stack
    depth itself retains Stockfish's asserted `MAX_PLY + 1` caller contract.
    H9.3i-a freezes scalar make/undo/null/lazy event traces; H9.3i-b adds the
    complete special-move, randomized, concurrency and fail-closed corpus
    before real SIMD.

    H9.3i-b is a separate private stress target so its larger corpus cannot
    weaken or silently renumber the frozen 39-event H9.3i-a differential. Its
    directed cases include four orthodox and eleven Atomic960 castling layouts,
    bilateral en passant, bilateral quiet/capture promotions, maximum Atomic blast geometry,
    terminal king removal with exact undo, mirror and material-bucket
    crossings, no-push null moves, lazy/deep stack restoration and network
    reset/mismatch recovery. Deterministic legal trajectories cover both
    Atomic and Atomic960 roots. Every successful incremental result is
    bit-identical to the H9.3h full-refresh scalar diagnostic; every expected
    failure—including injected faults after one perspective, before composition
    and after successful composition but before commit—clears its diagnostic
    and preserves the previously committed frame, depth and counters. Independent stacks may share one immutable
    network concurrently, but one stack remains single-owner. Smoke, release,
    instrumented and local soak profiles have frozen operation accounting and
    state signatures. A signature commits not only to FEN, key and the scalar
    output, but also to canonical HM-only accumulators, source kind/ply/distance,
    row deltas, stack depth, same-frame EP diagnostics and per-call counters; a
    backend that silently full-refreshes every frame therefore cannot satisfy
    the stress contract. Random counters and the exact directed inventory are
    published separately. The runner hashes one 77,349,879-byte fixture into an
    owned buffer and constructs both network identities from those exact bytes,
    closing the wrapper-to-loader check/use gap. GCC and MinGW compile the
    scalar backend, incremental backend and isolated runner with a 128,000-byte
    stack-usage error ceiling. The stress backend and runner remain
    absent from search, protocols, bindings, WASM, generator and trainer build
    graphs.
    A V3 Finny or relation cache is a separate measured layer after scalar and
    ISA equivalence, never a prerequisite for establishing correctness.
17. Introduce H9.3j-a as a private real-ISA full-refresh seam. Only addition of
    active feature rows into the frozen i32 accumulators is vectorized, with
    SSE4.1 and AVX2 kernels plus an explicitly selectable scalar oracle. Feature
    emission, HM PSQT, transform and the dense tail remain scalar. Incremental
    SIMD, caches and production dispatch are explicitly outside this block;
    there is no automatic fallback between ISAs. The immutable network may be
    shared between threads, but scratch is owned by the caller/evaluation
    stack, one stack remains single-owner and the hot kernel performs no dynamic
    allocation. This private seam has no runtime CPUID dispatch: an exact ISA
    request absent from the ARCH-specific binary is rejected, while scalar
    remains explicitly requestable in compatible builds.

    ISA policy cannot change the V3 wire identity. Canonical serialized bytes,
    descriptors, offsets and hashes remain frozen; an authenticated load-time
    permutation is an execution policy, not a new format. SIMD kernels consume
    the already permuted live tensors and publish diagnostics in canonical
    logical coordinates. Identity, AVX2/LASX and AVX512 forced layouts must
    retain the H9.3h scalar diagnostic fingerprint `0x46F68EAB20FF9D50`.
    H9.3j-a separately freezes its 109-position batch aggregate as
    `0x4FBDB31B354FC080`; the two fingerprints cover different transcripts.

    SSE4.1 and AVX2 must be bit-identical to scalar for all four slice
    emissions, both canonical i32 accumulators, i64 PSQT, transform, dense
    intermediates and final diagnostics across both perspectives and every
    frozen valid, special-move and invalid case. Exactness and fail-closed
    behavior gate measurement. The runner authenticates and loads the frozen
    fixture, then isolates synthetic row-addition arithmetic: one 1,024-lane
    i16 row and one 1,024-lane i8 row, each repeated 8,192 times. It uses one
    warm-up plus five trials with alternating scalar/SIMD order and reports raw
    nanoseconds, medians and the ratio against a volatile scalar loop. It does
    not measure fixture rows, active-row mixes, full evaluation or search.
    Plain `--benchmark` is report-only; the explicit local promotion gate needs
    a ratio strictly greater than `1.000`. CI never enforces that noisy speed
    threshold. Failure grants no promotion, and no speed claim is made.
    V3 remains unreachable from search, protocols, bindings, WASM, generator
    and trainer, so H9.3j-a has no engine bench, OpenBench, Elo, LOS or
    playing-strength gate.

## Contract freeze gates

- Golden indices for every piece/normal-target class, CP segment base, compact
  EP-tail edge, board edge, perspective and independent WHITE/BLACK mirror
  branch. These include malformed EP fail-closed cases, promotion capture
  non-expansion, local-to-physical translation and strictly ascending unique
  output. At least one joint position must put the two perspectives on opposite
  horizontal-mirror branches.
- Numeric HM goldens must activate the bucket and virtual rows, coalesce all
  1,032 outputs, and then verify the per-bucket 12-to-11 king mapping in both
  accumulator and PSQT tensors.
- Direct king target, adjacent enemy blast, own self-blast, touching-king
  immunity, simultaneous enemy/own blast classes that make a capture illegal,
  and geometric EP adjacent to either king. Direction goldens use center d4 and
  related square d5 to freeze `related = center + N` before mirroring variants.
- Sole adjacent capturer, two attackers sharing a center, pawn immunity and the
  off-center EP pawn. Ring direction goldens likewise use center d4 and
  collateral d5 to freeze `collateral = center + N`.
- Mixed-wire fixtures assert every declared tensor shape, including feature-major
  `[22528][8]` HM PSQT with contiguous buckets and the same factor/king export.
- HM-only PSQT is frozen for wire v1. Any relation-PSQT experiment uses another
  descriptor and wire identity rather than silently changing this network.
- The i32 accumulator envelope and the per-network PSQT, affine and global
  dense-output gates are tested at their exact inclusive boundaries and at one
  value beyond each boundary.
- Bit-exact C++/Python descriptor, dimensions, offsets and hash generation.
- A streaming structural scanner must authenticate every manifest, shard and
  ledger byte without retaining multi-billion-record artifacts in memory. A
  separate engine-backed scanner must legally replay a real generated fixture;
  synthetic structural fixtures cannot satisfy that gate.
- H9.3a reports only `structural-pass`. It recomputes raw-record and
  split-group sets with disk-backed exact indices, but it never calls that a
  semantic or publication pass. It authenticates the four physical bitmap
  byte strings/hashes and exactly derives HM training/virtual masks from the
  physical HM mask, but it does not independently reproduce the four physical
  masks. Authenticated output from the independent symbolic reachability oracle
  is therefore an explicit H9.3b publication gate. `--require-publication-ready` fails closed
  until an authenticated `atomic-v3-semantic-audit-v1` artifact exists. H9.3b
  must define that artifact to bind both manifests, both ledgers, the coverage
  policy, scanner commit/binary hash, full legal replay totals, terminal/result
  checks, the exact ordered V3 feature-input-key sets and their zero
  train/validation intersection.
- Release-candidate ledgers must contain zero score-draw and
  evaluation-resignation stop reasons until an evidence-bearing ledger contract
  replaces this restriction.

## Implementation sequence

1. Land the initial ADR, JSON contract and dimension/hash tests without a
   runtime loader.
2. Implement the joint orientation and HM emitter, including factorized trainer
   export and mirror metamorphic tests.
3. Add independent CapturePair, KingBlastEP and BlastRing emitters with numeric
   goldens; resolve the semantic gates.
4. Compose all four slices through the scalar single-snapshot full-refresh
   oracle, sharing one HM and one CapturePair emission and keeping the runtime
   dispatcher untouched.
5. Freeze descriptors, PSQT scope and hashes, then add a mixed-wire synthetic
   fixture and strict reader/writer.
6. Add the scalar network backend and compare its arithmetic against the
   independent full-refresh oracle before exposing V3 through the dispatcher.
7. Add the private scalar HM incremental stack, then its complete stress and
   fail-closed corpus. Add real-ISA SIMD only after the per-event scalar trace
   is frozen; overflow always forces refresh. CapturePair, KingBlastEP and
   BlastRing remain refresh-only until each full delta is proved independently.
8. Extend transactional V1/V2/V3 loading, UCI/XBoard, Python, JavaScript, WASM,
   generator and trainer gates. Generation publishes role-separated bin-v2
   manifests, trajectory ledgers, per-index coverage, stats and split audit as
   one transaction. Add producer/manifest evidence for
   `adjudicate_resignations=false`, emit `atomic-v3-semantic-audit-v1`, and run
   the engine-backed replay and feature-input-key scanner before publication.
   The fixed 512 MiB WASM heap and the H9.2 large-page adoption path remain
   mandatory.
9. Run controlled ablations, then 2-3 billion serious records and OpenBench STC
   and LTC only with networks capable of changing moves.

## Consequences

The transformer is estimated at roughly 98.26 MiB before the dense tail. That
is larger than V2, but H9.2 already eliminated the extra fallback copy when a
validated network is published on WASM. The design spends memory on explicit
Atomic relations while keeping the hot dense head stable and every experiment
attributable. It also accepts that the first correct relation implementation
will be refresh-heavy; speed is recovered only after differential evidence,
not by weakening the oracle.

## H9.3n runtime promotion

H9.3n promotes the frozen V3 reader and incremental evaluator as the third
public production backend. The wire version (`0xA70C0003`), network hash,
descriptor bytes and the isolated scalar/SIMD oracles do not change. The
tagged inline dispatcher now accepts V1, V2 and V3 transactionally while
preserving the trivial-copy contract required by NUMA and shared-memory
replication.

Promotion is gated by an exact 77,349,879-byte fixture with SHA-256
`00E46223822D06D7927E884EEC10739BA19EF8DD82A6E262F627D361658080C2`.
Native CI searches it in `true` and data-generation-only `pure` modes with
1, 2, 4 and 8 threads, proves classical `false` remains independent, and
round-trips export/import byte-for-byte. The same backend marker is required by
UCI perft preflight, XBoard and bench. The data generator performs a focused
one-record `pure` publication with matching manifest provenance.

The complete pthread Node UCI WASM source inventory and runtime gate include
V3 and exercise V1/V2/V3 switching plus V3 export/reimport. `pyffish` and the
CommonJS/ES-module `ffish.Board` artifacts remain intentionally rules-only:
they expose no UCI evaluator, but continue to run their complete Atomic and
Atomic960 API suites beside the full NNUE WASM gate. The isolated V3 oracle,
stress and instruction-audit targets remain as independent evidence even though
their reviewed runtime sources are now part of production.
