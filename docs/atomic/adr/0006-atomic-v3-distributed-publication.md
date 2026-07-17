# ADR 0006: Publish distributed AtomicNNUEV3 datasets through an acyclic evidence chain

- Status: accepted; H9.3l-a contract, H9.3l-b audited producer, controlled non-publication bootstrap training and final tools pin are merged; publication campaign evidence remains pending
- Date: 2026-07-16

## Implementation status

- Atomic-Stockfish PR #42 merged the publication contract as
  `dde43fc08fb2bd45eec09d3dbe9f6d06845eeb24`.
- Atomic-Stockfish PR #43 merged the audited trajectory producer as
  `420c9f35266fbdc2167dc5b9d8d20d90281c60c9`.
- `variant-nnue-pytorch` PR #14 merged the authenticated provider, strict
  serializer, checkpoint/resume and four-run production launcher into `atomic`
  as `3a19c16fc3d477b1ee7602ccc6510736bc7604cc`. It accepts the owner-capped
  bootstrap only as `non-publication-bootstrap`; its outputs cannot claim
  dataset publication or release-candidate readiness.
- `variant-nnue-tools` PR #33 merged the final engine pin to
  `420c9f35266fbdc2167dc5b9d8d20d90281c60c9` into `atomic` as
  `450049ee7a0ece32694b11f6c55deb7df1d42a84` after exact-head review and all
  five platform/instrumentation checks passed.

## Context

The original AtomicNNUEV3 dataset contracts authenticate one train manifest,
one validation manifest, their trajectory ledgers, per-index coverage,
statistics, one coverage policy and one split audit. That is sufficient for a
single generation seed. Distributed OpenBench generation is different: every
chunk has a distinct truthful seed and its own finalized Atomic BIN V2
manifests. Collapsing those chunks into a synthetic manifest would either lose
their provenance or falsely claim that one seed produced every record.

The structural V1 bundle also deliberately stops before four publication
claims: legal engine-backed replay, exact V3 feature-input decontamination,
independent reproduction of the physical reachability masks and producer
evidence that evaluation resignation was disabled. The completed training-run
manifest V1 cannot add those artifacts without changing its frozen ordered
input hash.

The new publication layer must therefore support many chunks, close all four
gates and remain acyclic. Legacy Atomic V1, AtomicNNUEV2, Atomic BIN V2 and all
existing V3 schemas must remain byte-identical.

## Decision

1. Preserve every existing schema and hash recipe. Add six independently
   versioned documents:

   - `atomic-v3-dataset-campaign-v1`
   - `atomic-v3-producer-attestation-v1`
   - `atomic-v3-semantic-audit-v1`
   - `atomic-v3-reachability-attestation-v1`
   - `atomic-v3-training-environment-v1`
   - `atomic-v3-training-run-manifest-v2`

   Validators dispatch by exact schema identity, version and release-pinned
   schema SHA-256. A caller-supplied, relaxed schema cannot redefine
   publication readiness. A V1 document is never upgraded, reserialized or
   interpreted as V2.

2. A campaign is the ordered collection root. Every chunk keeps its own
   generation seed, partition config, coverage policy, train and validation
   manifests, ledgers, index coverage, statistics and split audit. Its
   `collection_sha256` covers the campaign ID, eight frozen schema hashes,
   seed schedule, all ordered chunk identities and artifact hashes, and the
   recomputed totals. It also covers a homogeneous campaign-profile hash, the
   exact producer build-set root and the producer binary digest selected by
   each chunk. It excludes `collection_sha256` and `verification`.
   Chunk indices and role record offsets are contiguous and seeds are exactly
   `base_seed + chunk.index`, with checked uint64 arithmetic.

   The four train/validation duplicate ceilings must be identical in every
   authenticated chunk policy. Publication reapplies those ceilings to the
   complete campaign: raw-record uniqueness is recomputed from the global
   shard index, and feature-input uniqueness comes from the engine-backed
   semantic audit's authenticated ordered sets. This prevents repeated chunks
   from evading a limit that each chunk satisfies in isolation.

3. The campaign does not reference later attestations. After all campaign
   bytes are finalized, one producer attestation binds the exact campaign and
   a strictly sorted, content-addressed set of generator builds. Every chunk
   maps to exactly one authenticated build; that build's commit must equal the
   generator commit in the chunk policy. The campaign and attestation both
   bind the domain-separated build-set root. Its generation policy requires `Use NNUE=pure`,
   score-draw adjudication and evaluation resignation disabled, Syzygy disabled,
   complete played trajectories preserved and content-hash role partitioning
   before publication. Its evidence hash excludes itself.

4. Semantic and reachability attestations independently consume the campaign
   and producer attestation and may be produced in parallel:

   - The semantic audit uses a pinned engine-backed scanner. It legally sets
     every root, replays every played move, matches every retained pre-move
     position, independently validates each stored best move, reproduces every
     result and terminal, recomputes the ordered V3 feature-input keys and
     proves zero train/validation intersections for raw records, feature inputs
     and split groups. Stop-reason slots 7 and 8 are exactly zero.
   - The reachability attestation uses a pinned symbolic oracle that accepts no
     dataset artifact as input. Its 18,772-byte output is the eight physical
     bitmaps in a frozen WHITE-then-BLACK order. Validation derives HM training
     and virtual-factor masks from each physical HM bitmap, recomputes all
     twelve per-mask hashes and matches the aggregate reachability hash in
     every coverage policy authenticated by the campaign. Dataset counters and
     labels are forbidden oracle inputs.

   Both attestations bind the earlier campaign and producer hashes and exclude
   their own `evidence_sha256`. Neither points to training artifacts.

5. Training-run manifest V2 replaces V1's single-partition inputs with seventeen
   ordered artifacts: the eight original schemas, the four new evidence
   schemas, the authenticated training-environment schema, and the campaign,
   producer, semantic and reachability documents.
   The campaign transitively authenticates every per-chunk V1 structural
   artifact; it is neither necessary nor safe to flatten thousands of chunk
   artifacts into the training-run manifest.

6. V2 uses new domain separators for `run_definition_sha256` and
   `input_bundle_sha256`. The run definition covers only the trainer binary,
   authenticated dependency-lock artifact, config, authenticated canonical
   deterministic-environment artifact, seed and schedule. The environment
   cross-binds the trainer and lock digests. The input bundle covers the
   seventeen input hashes followed by the run-definition hash. It excludes the
   checkpoint, network, logs, metrics, completed manifest bytes and its own
   result. The checkpoint stores:

   - `input_bundle_sha256`
   - `run_definition_sha256`
   - `feature_schema_sha256`
   - `campaign_sha256`
   - `producer_attestation_sha256`
   - `semantic_audit_sha256`
   - `reachability_attestation_sha256`

   The completed manifest may then authenticate all outputs without
   self-reference.

7. Every evidence reference is an exact basename, byte count, SHA-256 and,
   where applicable, schema SHA-256. All JSON uses the existing canonical wire:
   UTF-8 without BOM, schema-declaration key order, minified separators and one
   trailing LF. Binary and JSON inputs are opened as regular non-symlink files,
   authenticated through one stable handle and rejected if they change during
   validation.

8. Publication is transactional. Producers write shards before manifests and
   manifests before ledgers; scanners then produce index coverage and stats,
   followed by split audits and the campaign. Attestations and training outputs
   remain on temporary paths until every declared byte, hash, count and
   cross-document binding has been re-read. Append and overwrite are forbidden,
   and failure leaves no final sidecar visible.

9. Dataset publication readiness has an explicit operator trust boundary. The release
   invocation supplies independent SHA-256 trust pins for the producer build set,
   semantic scanner and reachability oracle, plus the trainer for a V2
   training run. Merely naming a binary inside an attestation is insufficient.
   The validator authenticates the pinned binary bytes, evidence artifacts and
   every cross-document claim; the controlled release job is responsible for
   invoking those exact binaries.

10. Dataset and training readiness are separate states. A complete authenticated
    campaign may report `dataset_publication_ready` without any training run.
    Supplying a run makes overall `publication_ready` depend on training
    publication instead. Frozen V1 is structural-only. V2 may authenticate its
    declared graph, lock, environment and opaque output bytes, but self-asserted
    booleans or arbitrary checkpoint/network blobs are not execution evidence.
    Until a controlled executor proves the checkpoint bindings, strict network
    reimport and pinned-engine load, both V1 and V2 fail closed for training
    publication. A future signed in-toto/SLSA execution transcript can close
    that gate without weakening dataset publication.

## Hash DAG

```text
per-chunk shards
      |
      v
per-chunk manifests
      |
      +-----------> per-chunk index coverage
      |                         |
      v                         v
per-chunk ledgers ----------> stats
      |                         |
      +-----------> split audit+
                                |
                                v
                         dataset campaign C
                                |
                                v
                     producer attestation P
                          /             \
                         v               v
                semantic audit S   reachability audit R
                          \             /
                           v           v
                      training input bundle B
                                |
                                v
                    checkpoint/network/logs/metrics
                                |
                                v
                  completed training-run manifest V2
```

The following backward edges are invalid:

- campaign to producer, semantic or reachability attestation;
- producer attestation to semantic or reachability attestation;
- semantic or reachability attestation to training outputs;
- a V2 manifest to its ledger or producer attestation;
- an input-bundle hash to checkpoint, network, completed manifest or itself;
- any document to the hash of its own exact bytes.

The graph intentionally permits semantic and reachability evidence to run in
parallel after producer evidence is fixed.

## Validation gates

A distributed dataset is eligible for training only when:

1. every chunk passes the unchanged structural V1 validator;
2. campaign order, seed derivation, role offsets, totals and collection hash
   recompute exactly;
3. duplicate ceilings are homogeneous and pass again over the global raw-record
   and V3 feature-input identities;
4. producer evidence authenticates the campaign and disabled adjudications;
5. semantic replay reports no illegal roots or moves, position, result or
   terminal mismatch and zero cross-split identities;
6. reachability evidence reproduces all physical and derived masks without any
   dataset input;
7. all four evidence documents bind the same exact campaign and producer
   hashes;
8. training-run V2 recomputes its run definition and seventeen-input bundle;
9. a controlled execution authenticates every checkpoint binding and proves
   strict network reimport and pinned-engine load.

Dataset release validation additionally requires the operator trust pins described
above. Synthetic fixtures use isolated fixture-only pins to test framing,
goldens and negative cases; such a test result is not release provenance and
must never reuse production trust pins.

## Consequences

Distributed generation remains honest about every seed and chunk while the
trainer consumes one bounded collection root. The extra attestations increase
the number of sidecars and require full replay, external sorting and an
independent oracle, but those costs occur outside the search hot path. They
make the dataset reproducible from authenticated inputs; they do not claim
that a network is reproducible until controlled training execution is
implemented and verified.

The completed, owner-capped 375-million-position Atomic BIN V2 bootstrap is a
distributed-generation transport and throughput pilot only. Its completion
does not alter its provenance: it began without complete V3 trajectory ledgers
or this evidence chain and can never be
relabelled as a V3 release-candidate dataset. OpenBench v39 contains a reviewed
multi-artifact transport, but that transport must preserve these exact hashes
and cannot replace any publication gate.

The evidence documents are cryptographic attestations, not signatures. A
hostile party controlling both the files and the validator invocation could
fabricate claims while pointing at copied trusted binaries. Production
publication therefore runs in controlled CI that executes the pinned tools and
protects the trust-pin configuration. Signed execution provenance is a
compatible follow-up hardening item, not something this local offline
validator can infer from output bytes alone.
