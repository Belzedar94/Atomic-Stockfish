# ADR 0007: Authenticated teacher modes and Atomic Syzygy datagen contract V2

- Status: Accepted; production teacher is `pure`, with `true` retained only as a control
- Date: 2026-07-19
- Owners: Atomic engine and data pipeline
- Supersedes: nothing; manifest V1 and `ATOBNDL1` remain frozen

## Context

The historical OpenBench datagen bridge publishes one Atomic BIN V2 shard with
manifest V1 and `ATOBNDL1`. That contract deliberately requires `Use NNUE=pure`
and disables Syzygy. Its bytes, hashes, option meaning and validator must remain
unchanged.

Two legitimate teacher configurations need controlled comparison:

- `pure`: the established variant-nnue-pytorch/gensfen recipe;
- `true`: ordinary playing evaluation (descriptively, the legacy-playing
  teacher), represented by the exact UCI value `Use NNUE=true`.

The evidence does not justify replacing the established `pure` recipe with
`true`. The FAQ warning about `pure` applies to variants carrying supplementary
state (for example three-check); it is not, by itself, evidence that Atomic
must switch. Production is therefore fixed to `pure`. The authenticated `true`
mode remains available only for controlled comparison and diagnosis; it is not
an alternative production default.

Atomic six-piece Syzygy data is useful to the teacher, but enabling it changes
sample selection and search results. That provenance cannot be added to
manifest V1 or placed in unused `ATOBNDL1` bytes without reinterpreting frozen
contracts.

## Decision

Introduce an additive bridge selected only by OpenBench v40's complete
`syzygy`, `syzygy_manifest_sha256`, `syzygy_max`, `teacher_mode` field group.
There is no `contract_version` token in the v40 command. Absence of all four
fields preserves V1; a partial group fails closed. V2 has no implicit teacher
default and accepts exactly one of these authenticated pairs:

| `teacher_mode` | exact `Use NNUE` |
| --- | --- |
| `pure` | `pure` |
| `true` | `true` |

Every crossed, omitted or unknown pair is rejected before generation. Aliases,
including `legacy-playing`, are also rejected: the authenticated wire value is
byte-exact `true`. The V1 bridge remains the default when the complete group is
absent and continues to set `Use NNUE=pure` with an empty `SyzygyPath`.

Contract V2 requires all of the following before a shard may be created:

- authenticated network SHA-256;
- authenticated book SHA-256 when a file book is used;
- exact `book NONE book_sha256 NONE` when the built-in start position is used;
- the exact pinned official Syzygy inventory SHA-256 supplied by v40;
- nonempty `SyzygyPath` whose load reports exactly six-piece cardinality;
- `SyzygyProbeLimit=6`, `SyzygyProbeDepth=1` and
  `Syzygy50MoveRule=true`;
- `UCI_Chess960=false`;
- a clean build with a pinned 40-hex producer commit;
- absent shard, manifest, attestation and bundle destinations.

V2 rejects repeated core or forwarded generator options and rejects unsafe
retokenization of reconstructed values. Ordinary reconstructed values are one
unquoted token; quoted-path syntax is confined to the direct `syzygy` value.

An optional `producer_sha256` binds protocol-v39 executable provenance without
changing which contract is selected. When supplied it appears in both manifest
and attestation; publicable presets should activate `{PRODUCER_SHA256}`.

Failure at an identity, option, cardinality, search or publication gate creates
no accepted output and never overwrites an existing path. Partial files created
by the producer are removed only through captured file identity.

## New byte contracts

V2 uses three new, independently hashed schema files:

- `schemas/atomic-bin-v2-manifest-v2.json`;
- `schemas/atomic-datagen-attestation-v1.json`;
- `schemas/atomic-openbench-datagen-bundle-v2.json`.

Manifest V2 records the teacher pair, network/book identities, inventory SHA,
optional producer SHA, six-piece option values, shard identity and
`tb_probes`/`tb_hits`. Attestation V1 cross-binds the manifest, shard,
inventory, teacher pair, tablebase options and counters.

`ATOBNDL2` has a new 384-byte little-endian header and three entries:

1. canonical manifest V2;
2. canonical attestation V1;
3. one 64-byte-aligned Atomic BIN V2 shard.

The header authenticates the bundle, data, manifest and attestation schema
hashes, all entry hashes, record count, tablebase counters and inventory SHA.
Bytes 352..383 are reserved and must be zero. V2 is validated by
`tools/validate_authenticated_datagen_bundle_v2.py`, which requires the exact
local inventory and refuses extraction until every cross-binding passes.

## Tablebase counter semantics

The backend exposes process-global atomic reset/snapshot counters. A probe is a
native public WDL or DTZ entrypoint invocation during the bounded generation
interval. A hit is an invocation whose final `ProbeState` is not `FAIL`.
Recursive DTZ entrypoint calls count because they perform real backend work.
Counters are provenance/diagnostic evidence, not a claim that every record was
tablebase-resolved.

## Inventory trust boundary

The v40 worker rehashes the inventory JSON, proves every local `.atbw`/`.atbz`
name and byte count, verifies runtime/source hardlink identity and checks the
official-MD5 acquisition markers. Its server lease and upload receipt bind that
capability to the assigned chunk. The command intentionally exposes only the
table path, inventory SHA, required maximum and teacher mode. The engine
therefore does not claim to rehash an inventory path it never receives: it
requires the pinned official SHA, proves that the configured path loads exactly
six-piece Atomic tables, fixes the probe options, and binds the same SHA in the
manifest, attestation and bundle header. The offline validator rehashes the
supplied inventory and joins both proof layers without storing a
machine-dependent table path in the dataset.

## Production mode and controlled calibration

Every publicable production preset must use `teacher_mode pure` and activate
`producer_sha256`. A production worker must not choose `true` merely because
the authenticated capability exists. `true` is retained as a control whose
comparison record should include:

1. paired `pure` and `true` shards with identical engine commit,
   network, book, inventory, seeds, options and record counts;
2. successful V2 validation and tablebase counter review;
3. fixed-position evaluation/PV parity reports, separated into tablebase and
   non-tablebase positions;
4. trainer acceptance and finite-loss checks for both datasets;
5. a controlled strength/calibration comparison of resulting networks;
6. an explicit experimental conclusion that does not change the production
   preset without a new reviewed ADR and contract-compatible rollout.

This decision fixes the publicable production mode; it does not turn the
control mode into an alias or weaken any V2 authentication gate.

A possible future pre-correction/calibrated legacy teacher is not an alias for
`true`. It requires a new contract version, new schema hashes, explicit
correction parameters and a separate validator so neither V1 nor this V2 wire
contract changes meaning.

## Consequences

- Old consumers continue to receive byte-identical V1 output.
- V1 validators reject `ATOBNDL2`; the V2 validator rejects `ATOBNDL1`.
- Syzygy provenance is explicit rather than inferred from a worker directory.
- Teacher experiments are comparable because mode choice cannot be implicit.
- Verification adds small fixed overhead before and after self-play.
