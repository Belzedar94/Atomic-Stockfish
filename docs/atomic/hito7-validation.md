# Hito 7 validation

## H7.1 — Legacy schema handshake

This first Hito 7 block freezes the data contract that already exists before
introducing `atomic-bin-v2`. It does not change generated Legacy Atomic V1
bytes, search, evaluation, move generation or NNUE arithmetic.

The normative source is `schemas/atomic-schema.json`, stored as UTF-8 with LF
line endings and SHA-256:

```text
acca0f551f1c012c31a6c727dedccaebb7b5ebbc46810edb87e31bb208d5abe1
```

The schema records the headerless 72-byte layout, 16-bit move wire, score and
result perspectives, the twelve zero-valued hand-count fields that remain in
the historical packed position, split clock fields and the little-endian host
requirement. It also records current limitations instead of promising states
that the common tools/trainer contract cannot preserve:

- PackedSfen V1 is not a unique byte-canonical encoding after decode/repack.
- A missing/exploded king is not a valid Legacy V1 record.
- `MOVE_NONE` is forbidden.
- Atomic960 rook origins are not representable.

`variant-nnue-tools` exposes `atomic_data_schema` and advertises read/write
support. `variant-nnue-pytorch` exports
`get_atomic_training_data_schema_json()` and the Python
`atomic_training_data_schema()` wrapper, advertising read-only support. The
release E2E fails before generation unless both report the exact schema hash,
format ID and record size.

Pinned implementation commits used by the local release run are:

| Repository | Commit |
| --- | --- |
| Atomic-Stockfish code/test commit | `518175e20f127bf47b57ba1faad580b975e45929` |
| variant-nnue-tools | `b409ea6f1a6740021c0691b6529ccf264c7a9d81` |
| variant-nnue-pytorch | `350a28f2cee225c546333aded75b9db64caa526d` |

The documentation-only follow-up does not alter the compiled source tree.

## Local release evidence

All workloads were preceded by the global workload-tree preflight. The final
release gate used clean, commit-bound Windows builds:

| Artifact | Target | SHA-256 |
| --- | --- | --- |
| Atomic-Stockfish | MinGW BMI2 | `557E39B6...F7FC0B44` |
| data tools | MinGW x86-64 SSE2 | `8F54AE22...A2164396` |
| trainer loader | MSVC x64 Release | `8E322B1A...968923A0` |

The following gates passed:

- Schema/lock/E2E focused suite: `59/59`.
- Tools C++ units and full integration, including frozen hashes
  `C8F5C7FE...B229B2AA` and `1E7A3166...CC12E9CE`.
- Trainer native CTest: `1/1`; Python CPU and required CUDA gates: `58/58`
  each on the RTX 3080.
- Pipeline lock/build/profile suite: `87/87`.
- Strong-local generator → decode → train → serialize → re-import → engine
  E2E: PASS with eight records, the frozen playing-network hash and unchanged
  data hash `7de72b13...7a261a2d`.
- Hito 4 release surface: C++ `63/63`, API `34/34`, historical `test.py`
  `22/22`, Python `60/60`, CommonJS, ES module, Board WASM, native parity,
  eight exact perfts, focused rules `19/19`, UCI, XBoard, Syzygy, NNUE modes,
  reprosearch and interactive UCI/NNUE WASM.
- Legacy NNUE incremental/full-refresh equivalence: 1,000,000 operations with
  `capture-forced-refresh=0`.
- Structural/NNUE differential: `10,000/10,000` positions.

The normative serialized BMI2 speed gate also passed against the frozen Fairy
BMI2 baseline:

| Binary | Median NPS | Bytes |
| --- | ---: | ---: |
| Atomic-Stockfish H7.1 | 1,316,647 | 4,264,325 |
| Fairy-Stockfish baseline | 1,158,380 | 4,281,871 |

The ratio was `1.1366` (`+13.66%`). Since H7.1 does not modify engine source,
search, evaluation or generated data bytes, it does not trigger the three-TC
strength-change gate. Those matches remain mandatory for every future change
that can affect play.

## Remaining Hito 7 work

H7.1 is a compatibility boundary, not completion of Hito 7. The next blocks
must add the Atomic-Stockfish `data-generator` target, make tools a thin pinned
wrapper, implement the versioned `atomic-bin-v2` header/manifest and 32-bit
move wire, add Atomic960-capable canonical positions, and validate every V2
record in tools and trainer. Legacy V1 remains a supported fallback throughout
the 1.x line.
