# Hito 6 search integration validation

Hito 6 integrates Atomic search policy in small, attributable blocks. This
record is cumulative: blocks 1 and 2 are accepted after correctness, pipeline,
speed and all three exact strength gates. The milestone and its PR remain open
until the remaining search blocks and the full release matrix are closed.

`Use NNUE=true` is the only playing mode used for speed and strength. `pure`
remains a data-generation mode: its option and network-loading contract is
tested, but it is not used for play, speed measurements or strength matches.

## Block 1: Atomic move-count pruning

Fairy-Stockfish commit `b0b230f3` changed the quiet move-count threshold to
account for explosive captures and passed independent Atomic STC, LTC and VLTC
tests. With `blast_on_capture=1` and `walling=0`, its generic expression reduces
exactly to:

```text
(5 + depth * depth) / (3 - improving)
```

Atomic-Stockfish applies that threshold only to quiet moves. Captures and quiet
Atomic checks are emitted before the skipped quiet tail. Six direct C++ tests
pin the integer thresholds for both values of `improving` at depths 3, 4 and 6.

An earlier experiment changed child-node futility from `depth < 17` directly
to `< 6`. It was rejected: the shared 25,000-node corpus measured `0.9520x`
against the clean control. Fairy's historical change was relative (`9` to `6`),
so copying its final absolute value into the modern search disabled eleven
additional plies of pruning.

## Block 2: capture-futility safety

Modern Stockfish's main-search capture futility prices only the victim on the
destination square. In Atomic that can be an unsafe lower estimate when the
explosion also removes opposing non-pawns; en passant has no victim on the
destination square at all. The specialized guard therefore retains the
orthodox pruning only for normal captures whose blast ring has no non-pawn
bycatch. It excludes the capturer's origin square and adjacent pawns, which are
immune to the explosion.

Six direct C++ cases cover a normal capture (including capturer exclusion),
pawn immunity, non-pawn bycatch, en passant, capture-promotion and a quiet move.
Two depth-two UCI regressions pin the previously pruned `e6d5` non-pawn blast
and `e4d3` en passant blast in both classical and frozen-NNUE modes. The
qsearch counterpart is evaluated separately in block 3 so its correctness,
selectivity, speed and strength remain attributable.

The current signature is `379531`, up from block 1's `347633`, because the
engine now searches explosive tactical replies that the unsafe bound discarded.
All three exact LOS gates passed for this block; the immutable artifact and
complete logs are identified below.

## Block 3: qsearch capture-futility safety (preliminary)

The same destination-victim estimate also guarded qsearch futility. Block 3
reuses the reviewed Atomic capture-futility eligibility rule there, preserving
en passant and captures with non-pawn blast bycatch from the orthodox bound.
This block is not accepted yet: code, correctness and speed validation below
is **PRELIMINARY**, and all three normative strength gates are **PENDING**.

The immutable block-2 artifact `CB35D5` fails both new depth-one regressions:
it prunes the `e6d5` non-pawn blast and the `e4d3` en passant blast in qsearch.
The current candidate passes `15/15` search regressions with NNUE disabled and
with the frozen network loaded. Three independent signature runs produced
`380061` each time.

On the fixed search corpus, the current candidate visits `1,300,936` nodes,
compared with `1,300,804` for block 2. The preliminary clean five-run speed
snapshot measured a median `1,493,612` NPS for the 4,263,216-byte candidate and
`1,098,700` NPS for frozen Fairy, a ratio of `1.3594`. A separate interleaved
comparison against the block-2 artifact measured `1.0059x`; these measurements
prove the current code and test shape only. No final artifact hash or strength
result is claimed before the full block gate completes.

## Correctness snapshot

The final clean release build currently reports:

- C++ rules/state `56/56` and shared API `34/34`;
- search regressions `15/15` with NNUE disabled and with the frozen network;
- all eight historical Atomic/Atomic960 perft vectors;
- all `19/19` focused rule transitions and terminal outcomes.

The signature update was propagated through the complete release runner. That
runner passed native UCI and XBoard, Python, CommonJS, ES module, both WASM
surfaces, Syzygy, NNUE modes, reproducibility, perft and protocol coverage. The
full Hito 5 runner additionally passed exactly 1,000,000 incremental NNUE
operations and the 10,000-position Fairy differential corpus. That corpus now
contains a hard rule-50 split: `866` post-boundary positions require a neutral
candidate trace and the C++ gate proves an exact internal zero, while Fairy's
historical post-boundary final value is retained only as diagnostic telemetry.

The independent build matrix also passed:

- Windows MinGW release and debug/assert builds;
- a Python `sdist` rebuilt into an ABI3 wheel, isolated import and PEP 561
  discovery with mypy;
- Linux GCC 15.2 and Clang 20 portable release builds, including `56/56` C++
  units, both perft suites and `13/13` search cases in classical and NNUE modes;
- Linux Clang 20 ASan+UBSan over the same rules/API/perft/search surfaces;
- Linux Clang 20 TSan over the C++ rules, shared API and live XBoard suite.

No sanitizer reported an error. Clang's release build retains five existing
upstream warnings: one unused lambda capture and four libstdc++ temporary-buffer
deprecations.

The full block-3 rerun exposed a stale external Syzygy test driver that had
been linked before the quiet-mate fix in `92154082`. The old artifact
deterministically returned DTZ 2 instead of 1 and is no longer accepted. A new
cross-platform `make atomic-syzygy-driver` target now links the driver from the
same checkout as the engine; Hito 4 derives that sibling from `--native`, and
CI builds it before executing the source/domain contract. The rebuilt driver
passes all 13 fixture hashes, the quiet-mate probe and the complete `5/5` suite.
Its final release hash will be recorded after the block is committed and
rebuilt from the committed HEAD.

## Generator and trainer compatibility

The current `variant-nnue-tools` integration gate passed its Atomic-only engine
selection, historical 72-byte records, special moves, deterministic defaults,
overwrite refusal, conversions and seeded generation smoke. Its C++ unit gate
also passed in a clean Clang 20 container. The new cross-repository pipeline
gate uses `Use NNUE=pure` only while generating training records; all
conversion and final engine-loading commands use their appropriate non-pure
modes.

The current `variant-nnue-pytorch` native loader passed CTest `1/1`, and its
Python suite passed `30/30` on CPU and CUDA. That covers the pinned 32-record
fixture, native decoding, seeded one-versus-four-worker determinism, ownership
and error cleanup, and a complete HalfKAv2 forward/backward pass. Search block 1
does not alter positions, the wire format, features, accumulators or network
serialization, so this audit found no pipeline compatibility regression.

`tests/legacy_pipeline_e2e.py` now closes the executable cross-repository path:
it generates twice from the frozen network with a fixed seed, requires
byte-identical 72-byte data, validates and round-trips every record, checks the
exact HalfKAv2^ sparse batch, executes one real training/backward/Ranger step,
requires finite gradients and parameters plus non-zero FT/FC updates,
serializes, reimports byte-exactly and finally loads the newly written network
in the engine for evaluation and search. The current run passed with:

- source network SHA-256
  `99DC67EABF26A64FAEECA3A88B4C38597A840B8D4A874B9F2CF658C6F92A04A6`;
- deterministic data SHA-256
  `7DE72B1385DBC8E37312A513D1CF4C7D99F889ECB8B747F548ED32E8D7A261A2D`;
- serialized network SHA-256
  `A69FB0A7DC211AA4D8BB0974BA881F6CA0F98C5FBC30E0203B9E08B99076E3DC`;
- loss `0.0347999074`, FT delta `8.66651535e-05`, FC delta
  `8.454262e-07`, and final engine move `b2b3`.

The normative Hito 5 release runner now requires
`--pipeline-tools-engine` and `--pipeline-trainer-root`, reusing its already
SHA-pinned `--net`. Only smoke mode may omit both cross-repo paths, and it then
receives the explicit `LEGACY PIPELINE E2E NOT REQUESTED (NON-RELEASE)` marker;
partial configuration is always a hard error. A pinned multi-repository CI job
is still required before Hito 6 closes. Automatic continuation from an
existing `.nnue` and the production general dataset validator remain separate
trainer/tools release debts; this gate does not claim to implement either
feature.

## Block 1 selectivity and speed

At fixed depth 13, the deterministic Atomic signature changed from `404217` to
`347633`, a `14.0%` smaller tree. On CPU 0, the clean five-run snapshot was:

| Engine policy | Nodes | Median time | Median NPS |
| --- | ---: | ---: | ---: |
| Modern control | 404,217 | 231 ms | 1,746,085 |
| Atomic move-count | 347,633 | 210 ms | 1,655,395 |

NPS per node is diagnostic for this strength block; the lower node count and
the three LOS gates decide whether the selectivity is useful.

The strict shared-corpus speed gate used 13 positions, SHA-256
`2738065A8A70D61DA46FA3C75F95D645E50E601B43792DF0E7B3CC97B1D891A1`,
one thread, 64 MiB hash, CPU 0, one warm-up and five measured repetitions at
100,000 nodes per position. The clean final rebuild produced:

| Binary | Median NPS | Bytes |
| --- | ---: | ---: |
| Atomic-Stockfish | 1,464,982 | 4,262,793 |
| Frozen Fairy baseline | 1,109,003 | 4,477,632 |

The NPS ratio is `1.3210`, or `+32.10%`, and the specialized binary is 214,839
bytes smaller. The performance gate therefore passes.

### Block 2 clean performance snapshot

After integrating the reviewed Hito 4 and Hito 5 fixes, the clean AVX2 build
used the same pinned corpus, CPU, network and five-run procedure. The capture
futility block produced:

| Binary | Median NPS | Bytes | SHA-256 |
| --- | ---: | ---: | --- |
| Atomic-Stockfish | 1,483,243 | 4,263,813 | `CB35D57E3AD107C279781AF3E764EC412D59341C21D5FDC75AFAB08239CFFC14` |
| Frozen Fairy baseline | 1,104,296 | 4,477,632 | `1AE6D680F03128C8404F31A3F264F28B132B557ED3A91A6445EC563A7A33F623` |

The clean ratio is `1.3432`, or `+34.32%`; the candidate is 213,819 bytes
smaller. This passes the speed gate; the independently executed strength gates
are recorded below.

## Normative strength gates

All matches used the original `variantfishtest_new1.py`, runner SHA-256
`37D1790096520D9F3A1003746CDFBED59D2CC125A9B3D3192FF3399295EC9D70`,
the frozen network SHA-256
`99DC67EABF26A64FAEECA3A88B4C38597A840B8D4A874B9F2CF658C6F92A04A6`,
`Use NNUE=true`, one thread, 512 MiB hash, four workers, the Atomic book and
color-swapped pairs. Each stopped only after `Total > 100` and exact displayed
`LOS: 100.0%`.

### Block 1: Atomic move-count pruning

| TC | Total | W-L-D | Elo (95%) | Pentanomial | Draws | Time losses | LOS |
| --- | ---: | ---: | ---: | --- | ---: | ---: | ---: |
| 2+0.02 | 130 | 56-16-58 | +110.48 +/-45.0 | `[1,4,25,24,11]` | 44.62% | 0 / 0 | 100.0% |
| 10+0.1 | 102 | 34-6-62 | +97.88 +/-41.2 | `[0,1,23,25,2]` | 60.78% | 0 / 0 | 100.0% |
| 30+0.3 | 104 | 35-8-61 | +92.31 +/-42.3 | `[0,2,21,29,0]` | 58.65% | 0 / 0 | 100.0% |

The complete logs are versioned beside this record, normalized from the
PowerShell capture encoding to reviewable UTF-8/LF without changing their
textual contents:

The startup `Variant template ... does not exist` / `NativeCommandError`
diagnostics are emitted while the shared `variants.ini` enumerates non-Atomic
templates that this specialized engine intentionally does not expose. The
requested `atomic` template loads, every match continues normally, and the
recorded time-loss counters remain zero for both engines.

| Log | Bytes | SHA-256 |
| --- | ---: | --- |
| `evidence/hito6-movecount/tc1-2000-20.log` | 96,482 | `9F8327D11E092071C8D579A70A163C0886AF5508314CF8D3EF78420342C3C3DE` |
| `evidence/hito6-movecount/tc2-10000-100.log` | 76,485 | `9ED196A835E36EA8DEE5C78FD04A05DE527ECC0B24BB04EDCFE3F2BC64CFB3FA` |
| `evidence/hito6-movecount/tc3-30000-300.log` | 82,452 | `9C728F5CE3A3530F9C36E13F152E2C178D66018BFF5CB9EFE65CCB1916A733A4` |

All three matches used the final clean rebuilt binary, size 4,262,793 and
SHA-256
`DF7BB853E5DF7FC4F418F49F5AF8135B892D388D44A7E6D5D4CDE44E2883FDAE`.
The logs therefore exercise the exact artifact identified by this record,
without transferring results from a semantically equivalent earlier build.

### Block 2: capture-futility safety

The final clean capture-futility artifact passed every control at `Total 108`.
The normalized Elo values were respectively `137.20`, `147.38` and `158.96`.

| TC | Total | W-L-D | Elo (95%) | Pentanomial | Draws | Time losses | LOS |
| --- | ---: | ---: | ---: | --- | ---: | ---: | ---: |
| 2+0.02 | 108 | 51-18-39 | +109.66 +/-53.9 | `[0,2,24,21,7]` | 36.11% | 0 / 0 | 100.0% |
| 10+0.1 | 108 | 44-12-52 | +106.13 +/-47.4 | `[0,3,20,27,4]` | 48.15% | 0 / 0 | 100.0% |
| 30+0.3 | 108 | 38-7-63 | +102.61 +/-41.5 | `[0,0,24,29,1]` | 58.33% | 0 / 0 | 100.0% |

Each raw capture ended with the wrapper's exact
`Atomic LOS gate: PASS Total: 108 LOS: 100.0% complete_pairs: True` marker.
The versioned copies are UTF-8/LF normalizations of those captures:

| Log | Bytes | SHA-256 |
| --- | ---: | --- |
| `evidence/hito6-capture-futility/tc1-2000-20.log` | 78,813 | `EBE4A16B6832375114633EB1F0106EA74A98D5EF6E6E77FBF4A20D162F298465` |
| `evidence/hito6-capture-futility/tc2-10000-100.log` | 79,566 | `7E0DCCF36AF5E6E96249BEA1454EC7A034A6AAB1C19C91D0A5A55FF9B3776794` |
| `evidence/hito6-capture-futility/tc3-30000-300.log` | 85,748 | `977927475E2AEC55C221246E86E07696AB94CC79DE30C8F5F3ACE2924E39C406` |

All three matches used the 4,263,813-byte artifact with SHA-256
`CB35D57E3AD107C279781AF3E764EC412D59341C21D5FDC75AFAB08239CFFC14`.
The logs identify that exact candidate path and record `EvalFile`,
`Use NNUE=true`, `Threads=1`, `Hash=512`, Atomic book use, four workers and
the absence of SPRT. Both engines finished with zero time losses.
