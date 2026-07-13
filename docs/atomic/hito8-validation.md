# Hito 8 performance specialization validation

Hito 8 specializes memory layout and hot paths only after Atomic rules, search,
Legacy Atomic V1 NNUE, and the data pipeline have stable gates. Every block is
kept small enough to attribute a regression. A block that changes the playing
signature requires the normal OpenBench strength workflow; a no-functional-
change block must preserve the signature exactly and still pass all applicable
functional and performance gates.

## H8.1 - Remove inactive NNUE threat-delta runtime state

The active feature set is `HalfKAv2Atomic`. It declares
`UsesThreatDeltas == false` and uses `DirtyPiece` as its incremental-difference
type. Nevertheless, each accumulator state still contained a 392-byte
`DirtyThreats` member, and `Position` retained another 392-byte scratch object.
Those objects were never consumed by the active network.

H8.1 removes that runtime state and the unused parameters that carried it
through make/undo. It also adds a compile-time assertion that the active feature
set remains a `DirtyPiece` feature set without threat deltas. The legacy network
reader, feature indices, quantization, accumulator values, and serialized NNUE
format are unchanged.

The functional code commit is
`6153609c8b454e13bb3941789b9184f9b4825dad`, based on Hito 7 merge
`281bcc382eb4449886d0ee930ef7e23cb12b4dba`.

### Layout and artifact evidence

Both artifacts below were rebuilt with the same MinGW g++ 15.2 toolchain and
the exact normative `ARCH=x86-64-bmi2` release target.

| Item | Hito 7 control | H8.1 candidate | Change |
| --- | ---: | ---: | ---: |
| `AccumulatorState` | 2,560 B | 2,176 B | -384 B |
| `AccumulatorStack` | 632,384 B | 537,536 B | -94,848 B (-15.0%) |
| `Position` | 1,056 B | 664 B | -392 B (-37.1%) |
| Native executable | 4,269,764 B | 4,268,408 B | -1,356 B |

| Artifact | SHA-256 |
| --- | --- |
| Hito 7 control executable | `92E9C3C254741B628996D2F6617FF871EA1C06DAEEFB8AC749BF755FAAAC2323` |
| H8.1 candidate executable | `0CCAD79A60D8E0C20F9168464C5A0921BC5CA58F4D39685C526C5019573BE8D7` |
| Frozen Legacy Atomic V1 net | `99DC67EABF26A64FAEECA3A88B4C38597A840B8D4A874B9F2CF658C6F92A04A6` |

### Functional validation

The release BMI2 candidate passed:

- C++ Atomic unit tests: 63/63.
- Shared board API tests: 34/34.
- All eight frozen Atomic/Atomic960 perft vectors.
- Focused rules and state transitions: 19/19.
- Fixed search corpus with classical evaluation: 16/16.
- The same search corpus with `Use NNUE=true` and the frozen net: 16/16.
- Reprosearch with the frozen net: 12/12.
- One million deterministic incremental make/undo operations: 500,000 make,
  500,000 undo, 18,761 captures, zero capture-forced refreshes, and 241,087
  comparisons against a full accumulator refresh. The state signature was
  `0x8742E39B793C46AB`.
- A 10,000-position differential against frozen Fairy-Stockfish: 10,000/10,000
  accepted, maximum `Use NNUE=true` delta 0, and maximum pure trace delta
  0.005. The separately reported rule-50 oracle diagnostic reached 0.740 in
  866 rule-50-damped positions; it is not an accumulator mismatch.

The debug/assert build independently passed 63/63 C++ tests, 34/34 API tests,
all perft and 19 focused rule cases, both 16/16 search corpora, and an
incremental 4,096-operation smoke with 4,104 full-refresh comparisons and state
signature `0xDDB8196C6A0BE4A8`.

CI now compiles the modified incremental NNUE test executable under native GCC,
Clang, debug/assert, Windows MinGW, ASan+UBSan, and TSan. CI does not execute the
strong-network gate because that network is deliberately external; the full
local release gate above authenticates and executes it.

### Fixed-corpus performance evidence

The runner used 10 Atomic and three Atomic960 positions, one thread, 64 MiB
hash, CPU affinity 24, 100,000 nodes per FEN, one warm-up, five alternating
serialized repetitions, and the frozen net. The corpus SHA-256 was
`2738065A8A70D61DA46FA3C75F95D645E50E601B43792DF0E7B3CC97B1D891A1`.
The compiler preflight identified both sides as g++ 15.2, 64-bit BMI2 release
builds, and the artifact postflight re-authenticated every binary, the net, and
the pinned process-affinity dependency.

Against the exact Hito 7 control:

| Side | NPS samples | Median NPS |
| --- | --- | ---: |
| H8.1 candidate | 764,754; 864,925; 803,488; 806,477; 772,933 | 803,488 |
| Hito 7 control | 775,699; 679,293; 718,303; 793,199; 683,577 | 718,303 |

The observed candidate/control ratio was `1.1186` (+11.86%).

Against the frozen Fairy-Stockfish BMI2 baseline
`4EACAAB40DCA84F5A255EA57231F2795D43B5DDA85CE50EBBA1A1B2937B46331`:

| Side | NPS samples | Median NPS |
| --- | --- | ---: |
| H8.1 candidate | 790,788; 717,907; 824,364; 836,558; 794,653 | 794,653 |
| Frozen Fairy baseline | 747,621; 759,405; 695,648; 664,722; 717,518 | 717,518 |

The normative performance gate passed with ratio `1.1075` (+10.75%) and a
13,463-byte smaller executable. These percentages are observations from a
machine with one concurrent assigned workload, not universal speed promises;
the alternating order and median reduce but do not eliminate shared-load noise.
The gate-relevant facts are that both comparisons are matched, reproducible,
authenticated, and positive.

### Playing-strength gate

The playing signature remains exactly:

```text
Bench: 338376
```

H8.1 is therefore a no-functional-change optimization under the project's
OpenBench rules. It does not require an Elo/LOS match. Any later Hito 8 block
that changes this signature must use the normal Atomic OpenBench STC/LTC
methodology before acceptance.

### Deferred work

H8.1 intentionally leaves the inactive `FullThreats`, `HalfKAv2_hm`, and
`DirtyThreats` implementation sources buildable. H8.2 will remove that dead
feature family as a separately reviewable build-graph change, including native,
bindings, WASM, and data-generator compilation gates. Later Hito 8 blocks will
profile make/undo, move generation, accumulator caching, and NNUE-trained PGO
before proposing further changes.
