# ADR 0003: Add AtomicNNUEV2 as an isolated SFNNv15 backend

- Status: accepted; implementation in progress
- Date: 2026-07-14

## Context

Legacy Atomic V1 is the strongest proven evaluator and remains the compatibility
baseline for the 1.x series.  Its `0x7AF32F20` file contains 45,056 Atomic
HalfKAv2 features, a 512-value accumulator per perspective and the historical
`1024 -> 16 -> 32 -> 1` head.  Replacing that implementation in place would
invalidate the strongest network, the trainer and reproducibility evidence.

The pinned official Stockfish engine `eca43a97` and trainer `b8512291` provide
a substantially newer SFNNv15 topology.  Its physical graph is more specific
than the shorthand `1024 -> 32 -> 32 -> 1`: it performs pairwise multiplication,
uses square and linear activation paths and has an `fc0[30] - fc0[31]` skip.
The historical architecture hash does not encode all of those details, so a
simple MLP can have the same hash while being wire-incompatible.

Community history adds two Atomic-specific constraints:

- Atomic is an especially difficult variant for incremental NNUE updates.  A
  full-refresh implementation is the recommended correctness oracle, and an
  incremental implementation is accepted only when it evaluates identically
  ([Discord: full-refresh diagnostic](https://discord.com/channels/779317816897699850/966610323987660830/1096379475693944832),
  [Discord: later confirmation](https://discord.com/channels/779317816897699850/791249497090686987/1421422312078577704)).
- An explosion can remove many more pieces than an orthodox capture, although
  that cost is naturally bounded by the remaining material
  ([Discord: bounded incremental cost](https://discord.com/channels/779317816897699850/779317816897699854/783721822628610048)).

## Decision

1. Legacy Atomic V1 remains a byte-exact, independently selectable backend.
   AtomicNNUEV2 is added beside it; V1 files, arithmetic and export are not
   rewritten.
2. The authoritative machine-readable contract is
   `schemas/atomic-nnue-v2.json`.  V2 uses:

   - file version `0xA70C0002`;
   - feature hash `0x5F234CB8` and exactly the existing 45,056 Atomic indices;
   - 1,024 accumulator values per perspective;
   - feature-transformer hash `0x5F2344B8`;
   - architecture hash `0x63337116`;
   - network hash `0x3C1035AE`;
   - eight PSQT buckets and eight layer stacks.

3. The physical head is the pinned SFNNv15 graph without `FullThreats`:

   - multiply the two 512-value halves of each perspective and concatenate the
     two 512-byte results;
   - sparse `fc0 1024 -> 32`;
   - concatenate squared and linear clipped activations into 64 inputs;
   - `fc1 64 -> 32`;
   - concatenate both activation paths from `fc0` and `fc1` into 128 inputs;
   - `fc2 128 -> 1` plus the `fc0[30] - fc0[31]` skip;
   - scale by `9600 / 16384`, then apply `OutputScale=16`.

4. V2 does not add orthodox threat features.  Atomic threat features require a
   separately designed schema and experiment; they must not inherit
   `FullThreats` merely because the official trainer exposes it.
5. Network selection is based on the version and all structural hashes, never
   on the filename.  The reader validates description length, canonical signed
   LEB128 payloads, truncation, overflow, per-stack hashes and exact EOF.
6. The engine stores a tagged inline union of the two networks and another of
   their accumulators.  It retains the trivial copy/move/destruction contract
   required by shared-memory NUMA replication and does not retain a concrete
   replica pointer in a worker.
7. Loading is transactional.  A fresh heap candidate is parsed completely and
   becomes the NUMA source only after validation.  A rejected file preserves
   the active backend, bytes, metadata, evaluation and worker state.
8. Evaluation policy is backend-specific:

   - `Use NNUE=pure` remains data-generation-only and returns the raw
     `(PSQT + positional) / 16` result;
   - Legacy `true` keeps its historical blend, royal `COMMONER` material proxy,
     Atomic960 correction and rule-50 damping unchanged;
   - V2 `true` uses its direct result, Atomic960 correction and rule-50 damping,
     but never the Legacy blend or material proxy.

9. The trainer gets a separate `atomic_v2` package.  V1 checkpoints are not
   silently widened or accepted by V2: the pairwise transform, quantization,
   activations and skip prevent an exact conversion.  A future warm start must
   be an explicit approximation or distillation experiment with a report.
10. `atomic-bin-v2` remains unchanged.  It already stores the canonical
    position and labels needed by either trainer.  Dataset filtering remains a
    generation policy recorded in the manifest, not a model or wire-format
    property.

## Training baseline

The first serious V2 training run will use the strongest measured Atomic data
policy as an explicit experiment: qsearch threshold 32,000, capture and
promotion filters enabled, check filter disabled, and the Atomic opening book
recorded by checksum.  Community experiments found roughly +70 Elo from
disabling the standard qsearch filter and roughly +50 Elo from capture
filtering, later measured promotion filtering at about +15 Elo, and converged
on the same combined configuration
([Discord: qsearch/capture results](https://discord.com/channels/779317816897699850/966610323987660830/1260883780911104110),
[Discord: promotion result](https://discord.com/channels/779317816897699850/966610323987660830/1263386224064729158),
[Discord: final configuration](https://discord.com/channels/779317816897699850/966610323987660830/1413933791491391620)).
Filtering remains in data generation, matching the maintainer's separation of
generic training from variant-specific sampling
([Discord](https://discord.com/channels/779317816897699850/784418118503235625/879710963270549575)).

These measurements motivate a baseline; they do not exempt the resulting net
from OpenBench.

## Required validation

- Freeze all dimensions, hashes, wire order and physical layer inputs in tests.
- Reject independent mutations of version, network/transformer/stack hashes,
  description length, LEB128 magic/count/canonical form, truncation and trailing
  data.
- Switch `V1 -> V2 -> V1 -> V2` in one process, including invalid loads between
  successful loads; export each backend byte-exactly.
- Compare incremental updates with full refresh after ordinary moves, king
  moves, castling, en passant, promotions and maximum Atomic explosions.
- Compare scalar, SSE2, BMI2/AVX2 and WASM evaluation against the trainer's
  quantized integer reference.
- Exercise Threads 1 and 4, NUMA replication and repeated WASM loads within the
  fixed 512 MiB memory budget.
- Re-run every Legacy native, protocol, Python, JavaScript, WASM, generator and
  trainer gate without changing the frozen Legacy signatures.
- Submit OpenBench STC and LTC fixed-game matches only after a genuinely trained
  V2 network can alter move decisions.  Synthetic parser fixtures do not measure
  playing strength.

## Consequences

The inline union reserves approximately 90 MiB even while Legacy is active, and
a transactional V2 reload temporarily needs another candidate of that size.
That is acceptable for native builds but makes explicit WASM peak-memory tests
mandatory.  In return, releases retain the strongest current network and gain
an authenticated path to the modern trainer without coupling either backend's
wire format or evaluation calibration to the other.
