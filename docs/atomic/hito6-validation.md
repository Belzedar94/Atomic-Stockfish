# Hito 6 search integration validation

Hito 6 integrates Atomic search policy in small, attributable blocks. This
record is cumulative: blocks 1 through 3 retain their block-local acceptance
evidence, and block 4 is the current search change. The clean commit-pinned
pipeline passed in both profiles. The final hardened runtime artifact also
passed the matched-BMI2 speed comparison and all three current exact LOS gates.
The acceptance record is valid only while PR #3's exact-head CI matrix, Codex
review and review-thread audit are clean.

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
and `e4d3` en passant blast in both classical and frozen-NNUE modes. Block 3
evaluates the qsearch counterpart separately so its correctness, selectivity,
speed and strength remain attributable.

The block-2 signature is `379531`, up from block 1's `347633`, because the
engine searches explosive tactical replies that the unsafe bound discarded.
All three exact LOS gates passed for this block; the immutable artifact and
complete logs are identified below.

## Block 3: qsearch capture-futility safety

The same destination-victim estimate also guarded qsearch futility. Block 3
reuses the reviewed Atomic capture-futility eligibility rule there, preserving
en passant and captures with non-pawn blast bycatch from the orthodox bound.
That block passed its then-current correctness, pipeline, speed and normative
strength gates against the exact immutable artifact identified below. It is
historical block-local evidence; block 4 is the current milestone candidate.

The immutable block-2 artifact `CB35D5` fails both new depth-one regressions:
it prunes the `e6d5` non-pawn blast and the `e4d3` en passant blast in qsearch.
The final candidate passes `15/15` search regressions with NNUE disabled and
with the frozen network loaded. Three independent signature runs produced
`380061` each time; this is the block-3 Atomic signature.

On the fixed search corpus, the candidate visits `1,300,936` nodes, compared
with `1,300,804` for block 2: 132 additional nodes, or `+0.010%`, attributable
to the protected qsearch captures. The persisted direct interleaved comparison
against the block-2 artifact measured `1.0090x` (`+0.90%`); an earlier transient
sample inverted that small ordering, so block-local wall-clock data is treated
as diagnostic rather than a strength claim. The versioned 2,424-byte
`evidence/hito6-qsearch-futility/speed-vs-block2.log` is SHA-256
`17663569E64710272DD897C83AA0CAD0D3ADE3EED281E91911B7F84E6B31F220`.
The original paired Fairy speed sample reported `+33.12%`, and all three
strength samples passed. Those runs are retained as historical search-block
evidence only: the candidate was AVX2 while the old Fairy executable was SSE.
The final acceptance section below repeats speed and strength with matched
BMI2 release builds.

## Block 4: shallower Atomic null-move reduction

Modern Stockfish reduces a null-move search by `7 + depth / 3`. Atomic threats
make a speculative pass less trustworthy, and Fairy's historical Atomic search
also moved toward a less aggressive reduction. Block 4 changes only that
expression to the tested helper `6 + depth / 3`; it does not alter the NMP entry
condition, verification threshold, `nmpMinPly`, child futility, ProbCut, LMR or
time management.

Seven direct C++ cases pin depths 1, 3, 6, 9, 15, 16 and 18, raising the current
unit marker to `63/63`. A depth-14 black-box fixture uses
`rnbqkbnr/pppp1ppp/8/4p3/4P3/5N2/PPPP1PPP/RNBQKB1R b KQkq - 1 2`: the block-3
artifact chooses `f7f6` without NNUE and `h7h6` with the frozen network under
the test's fixed 16-MiB hash, while the block-4 candidate reproducibly searches
`g7g5` and `f8b4` respectively.
The current search suite passes `16/16` in both modes, and three independent
NNUE signature runs produced `338376`. The historical and focused perft suites
also pass unchanged.

Temporary one-worker counters confirm that both reductions enter the same NMP
code path and make the attribution observable without shipping instrumentation.
Across four positions, R6 changed total nodes by `-19.1701%` at depth 18 and
`+35.0623%` at depth 20; its verification rate rose from `7.7315%` to
`11.5990%` over all eight searches. The sign reversal and strong per-position
spread make this coverage evidence, not a speed claim. The normalized raw log
is `evidence/hito6-nmp/diagnostic-counters.log` (6,274 bytes, SHA-256
`1D40421F700F32B0F6C183821734B725F6388B5E9F2DEFBD86CCAFE74C870BB6`).

### Final hardened matched-BMI2 acceptance

The normative runtime artifact is built from commit
`ebfe93420c9998e4ea7dcbf4c6ba20516f1dee63`, tree
`a390c1e4156fad9bff57e7d66494d01821b9aa02`. It is 4,264,325 bytes with
SHA-256
`47B7873E40887C213C623E45ADBADD307DA77E5EA02953BE2F1E33363F0390A0`.
The clean-build manifest is SHA-256
`169D4C1A9D93F95555FDD74B5149C7B26894CC1A1C153C35B5073C2969C9DBBC`;
its compiler report is `g++ (GNUC) 15.2.0` with
`64bit BMI2 AVX2 SSE41 SSSE3 SSE2 POPCNT`. The frozen Fairy artifact uses the
same compiler signature, is 4,281,871 bytes and has SHA-256
`4EACAAB40DCA84F5A255EA57231F2795D43B5DDA85CE50EBBA1A1B2937B46331`.

The serialized, affinity-pinned benchmark used the fixed 13-position
Atomic/Atomic960 corpus, one warm-up and five alternating repetitions at
100,000 nodes per position:

| Binary | Median NPS | Bytes |
| --- | ---: | ---: |
| Atomic-Stockfish `ebfe9342` | 1,338,320 | 4,264,325 |
| Frozen Fairy BMI2 | 1,165,646 | 4,281,871 |

Atomic-Stockfish was `+14.81%` faster (`1.1481x`) and 17,546 bytes smaller.
The runner verified the compiler target, NNUE marker, CPU affinity and all
artifact hashes before and after the run. It emitted `PERFORMANCE GATE: PASS`.

All three strength controls used the same Legacy Atomic V1 network with
`Use NNUE=true`, `Threads=1`, `Hash=512`, four runner workers, the fixed Atomic
book and color-swapped pairs. `pure` remained exclusively a data-generation
mode.

| TC | Total | W-L-D | Elo (95%) | Normalised Elo | Pentanomial | Draws | Time losses | LOS |
| --- | ---: | ---: | ---: | ---: | --- | ---: | ---: | ---: |
| 2+0.02 | 110 | 50-22-38 | +90.43 +/-53.7 | +111.77 | `[2,7,17,19,10]` | 34.55% | 0 / 0 | 100.0% |
| 10+0.1 | 184 | 66-33-85 | +62.99 +/-37.0 | +85.88 | `[1,7,47,32,5]` | 46.20% | 0 / 0 | 100.0% |
| 30+0.3 | 136 | 46-19-71 | +69.90 +/-40.4 | +101.12 | `[0,3,37,26,2]` | 52.21% | 0 / 0 | 100.0% |

Every control ended on a complete color-swapped pair with `Total > 100`, exact
displayed `LOS: 100.0%`, zero time losses, no leaked engine processes and a
17-file artifact postflight. The normalized versioned evidence is under
`evidence/hito6-nmp/bmi2/final-ebfe9342/`. The TC transcodes also remove the
single trailing space from a captured blank separator line. Their hashes are:

| Evidence | Raw UTF-16LE bytes / SHA-256 | Versioned UTF-8/LF bytes / SHA-256 |
| --- | --- | --- |
| Benchmark | 3,282 / `A550D729053960CEC8F4D2D418B636DC5B3D948CD8A5184DE9439F5237094A7D` | 1,619 / `1AAE639BB6908137D40245472AC64738375AA37A2EE8FE7D75A66153BBABF734` |
| TC1 | 155,254 / `B1ABACCE45DE5F3BB81C2424A96BB0C59086C213AB879487CCF8AE3C0F3FF556` | 76,622 / `EBAA87422053A9E5DD28E1CFD2A595262B15DF4A7EEC27F7612F5F22285AF359` |
| TC2 | 268,176 / `0E37B18EDBC861B048A32EC2EFB95B14072451EE68351FE9A9CAC71FBB8CE9B4` | 132,455 / `B02F8065FADF2E48D4399A4EC322DF7191DA12A9F92A03978887C26C5B41733D` |
| TC3 | 223,288 / `A01504138F6308DA227E5E2B5EA4730A985B75F6230E8B5EC35AD64824DA3884` | 110,419 / `D3896FADAC0F5564373300910C20A5E0E969536D2F67A188F9FBBC8A1951EFD5` |

The canonical 47-file package manifest is
`evidence/hito6-nmp/manifest.json`, SHA-256
`4094341162F500C68D806DA83A21FC7C3D1CC00F138CAEE2844C1789B411196B`.

### Historical matched-BMI2 evidence

The immutable candidate is source commit
`0c45a9bf711814621607f3d0bed546a026cdf4d1`, tree
`f92e23c5104bf30d7e909be558ef23934c401d14`, 4,263,216 bytes and SHA-256
`289267DEEC8A082D375EC25CC6385475487CF6BD7BF907AA1B0D5730F1FC2901`.
Two deterministic MinGW 15.2 BMI2 builds were byte-identical; their independent
build logs are also byte-identical at SHA-256
`D8D2B99B878861028A64DCCC5530E6D3C65D063F1323471842105FF265DB0DC8`.
The frozen Fairy comparison artifact is 4,281,871 bytes, SHA-256
`4EACAAB40DCA84F5A255EA57231F2795D43B5DDA85CE50EBBA1A1B2937B46331`,
built from `fb78cb561aa01708338e35b3dc3b65a42149a3c4` with
`ARCH=x86-64-bmi2`, `all=no`, `largeboards=no`, O3/LTO and no PGO. Both real
`compiler` commands report `64bit BMI2 AVX2 SSE41 SSSE3 SSE2 POPCNT` and
`g++ (GNUC) 15.2.0`.

The prior serialized same-core speed run used the fixed 13-position
Atomic/Atomic960 corpus, one warm-up and five alternating repetitions at
100,000 nodes per position:

| Binary | Median NPS | Bytes |
| --- | ---: | ---: |
| Atomic-Stockfish block 4 | 1,287,967 | 4,263,216 |
| Frozen Fairy BMI2 | 1,145,124 | 4,281,871 |

The candidate was `+12.47%` faster and 18,655 bytes smaller in that run. Its
then-current runner re-hashed both binaries and the network after engine
shutdown and emitted `PERFORMANCE GATE: PASS`. Its 3,262-byte log is
`evidence/hito6-nmp/bmi2/bench-reproducible-vs-fairy.log`, SHA-256
`DDA7C37F70C22CE34522C86DE22186B482CBFA56C16D6929ACE4E483C511E8A2`.
That log predates the current full psutil-module provenance and affinity
readback checks, so it is historical evidence rather than the final speed gate.

All three prior strength runs met the statistical threshold against that exact
Fairy artifact with
the frozen network and the normative `Use NNUE=true`, `Threads=1`, `Hash=512`,
Atomic book, four runner workers and color-swapped pairs:

| TC | Total | W-L-D | Elo (95%) | Normalised Elo | Pentanomial | Draws | Time losses | LOS |
| --- | ---: | ---: | ---: | ---: | --- | ---: | ---: | ---: |
| 2+0.02 | 108 | 53-13-42 | +135.10 +/-52.8 | +172.82 | `[0,3,17,25,9]` | 38.89% | 0 / 0 | 100.0% |
| 10+0.1 | 108 | 39-9-60 | +99.11 +/-43.2 | +148.67 | `[0,4,20,26,4]` | 55.56% | 0 / 0 | 100.0% |
| 30+0.3 | 120 | 41-15-64 | +76.49 +/-42.4 | +111.97 | `[0,1,34,23,2]` | 53.33% | 0 / 0 | 100.0% |

Those prior logs are respectively SHA-256
`D4A8CA60855D0E6E9F897494F9560BF1192F285B9BAF4E54AF9337B1592B4C90`,
`5FBAC4B0987796244F2FF5886A1E7D674EC01390DC309844DE896EE4D3E16D51`
and `5059A326F6A3164CA5048F4E33CDFCFFCEC3283F2FCCE5A63FBE6C75B4CFCD68`
under `evidence/hito6-nmp/bmi2/`. TC3 correctly continued after transient
post-threshold LOS values while already-started worker pairs joined; only its
final `Total: 120` / `LOS: 100.0%` state produced the PASS marker.

A separate audit replays all three configurations without claiming new match
results. It pins the external runner, its `stat_util.py`, config, book, network,
candidate and baseline, verifies the exact compiler signature, and re-hashes
all eight inputs after each TC preflight. The 6,244-byte audit is SHA-256
`6A545AF088D5629F5BBA87100A30C9E8B18594295E92B4861BB4052B0EE72AF7`.
The canonical package index is `evidence/hito6-nmp/manifest.json`.

These samples established that the NNUE search block was materially stronger
and faster in the earlier matched-BMI2 setup. They remain historical because
the final acceptance above uses the hardened runners and immutable `ebfe9342`
artifact.
The material-only `Use NNUE=false` curiosity
match in the evidence package is explicitly non-normative: Fairy retains a
much richer handcrafted Atomic evaluation, so that sample does not isolate
search strength and is not an acceptance gate.

## Accepted block-3 correctness snapshot

The final clean block-3 release build reports:

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

The final Windows release artifacts were all built from source HEAD
`125d6d446d1fd407c6122f0409e62314206a202e`. The candidate used by speed and
strength is immutable; the sibling test executables are recorded so later
reruns cannot silently substitute stale drivers:

| Artifact | Bytes | SHA-256 |
| --- | ---: | --- |
| `Atomic-Stockfish-hito6-qsearch-E13F11.exe` | 4,263,216 | `E13F11F4FBC0459F8349B4F6D0B0BDD03600292B1027BFC311C42D236480976B` |
| `atomic-unit-tests.exe` | 3,702,799 | `3C12FE12DE60DFD335C3AD1323FD1D9EAC5AE54DF36B804ADD7CBBE9D39DC6E4` |
| `atomic-api-tests.exe` | 3,720,055 | `70EF0665F20E5CE886696D4072B3BD44B577E37553AB5B42E1B4D7FFF8D055B3` |
| `atomic-nnue-incremental-tests.exe` | 3,753,413 | `F69F316AC380196365FC6AE4B9B6F2C03544CD8BE729221B3CBC7D3AE2CA2064` |
| `atomic-syzygy-driver.exe` | 3,619,835 | `E062A660251EB645CD11E84BF65CB2CD0351FDD26C0F08596BC82BE222E92437` |

The exact release matrix reported C++ `56/56`, shared API `34/34`, historical
`test.py` `22/22`, Python `60/60`, CommonJS/ES module `58/58`, cross-surface
`40/40`, native API `25/25`, search `15/15` in both evaluation modes, and the
eight historical plus 19 focused perft/rule cases. The debug/assert rebuild
repeated the C++ `56/56`, API `34/34`, search, perft, Syzygy and XBoard gates.
The incremental test completed exactly 1,000,000 operations with 18,761
captures, zero forced refreshes and 241,087 full-refresh comparisons. The
10,000-position Fairy differential completed `10000/10000` on corpus SHA-256
`46C96F405BC15D468D94BC1E2186B577CE55128832E1108066581D35037FA2DE`;
the dedicated rule-50 units passed `3/3`, including all 866 post-boundary
positions in the fixed corpus.

The reproducible NNUE WASM build produced a 103,600-byte JavaScript loader
(`D0BD0C360BB8ADC636952F6833F0DD280EC732D00D379D63F0FE99F8857DF0E5`),
a 545,027-byte module
(`DC2FDB8DDBB56C82BA20AA8C184FEC5C35DE5A0D8BAC1C4699B412AEA8EE1D8B`),
a 2,828-byte worker
(`C18C2918C9F8FEDF3009F4A260A1185E919B0C6D421FF5403CB918B61C358A24`)
and a 3,342-byte Node wrapper
(`885E7A161EF8D447D41F54BD8ABE413DA3113B14E8B421C4848798C1B02D6DEB`).

The independent build matrix also passed:

- Windows MinGW release and debug/assert builds;
- a Python `sdist` rebuilt into an ABI3 wheel, isolated import and PEP 561
  discovery with mypy;
- Linux GCC 15.2 and Clang 20 portable release and debug/assert builds,
  including `56/56` C++ units, shared API, both perft suites, `15/15` search
  cases in classical and NNUE modes, real Atomic Syzygy/UCI probes and XBoard;
- Linux Clang 20 ASan+UBSan over the same rules/API/perft/search surfaces;
- Linux Clang 20 TSan over the C++ rules, shared API and live XBoard suite.

Block 3 also reran a fresh four-job Linux gate from detached, tracked-clean
worktrees at HEAD `125d6d446d1fd407c6122f0409e62314206a202e`, tree
`a54c9eea85b5e3ecbb49f3b1e47bc771dd90eead`, and
`SOURCE_DATE_EPOCH=1783780430`. Every GCC 15.2 / Clang 20.1.8 release and
debug/assert log contains C++ `56/56`, API, eight historical perfts, focused
rules `19/19`, search `15/15` twice (NNUE false and true), Atomic Syzygy `5/5`,
production Syzygy UCI with both NNUE modes, XBoard and `gate_exit_code=0`:

| Linux log | Bytes | SHA-256 |
| --- | ---: | --- |
| `evidence/hito6-qsearch-futility/linux/gcc15-release.log` | 28,871 | `FA526E78B8C9F21445E6DDF37EF3F93B694E975CC09A9E5B5385059CE3B7B2C3` |
| `evidence/hito6-qsearch-futility/linux/gcc15-debug.log` | 25,950 | `8D41108F1CD307F8BD6049A1EAFD5BCD549FF162933C505E851AD85F53E59629` |
| `evidence/hito6-qsearch-futility/linux/clang20-release.log` | 40,916 | `2FDCDB2B0C4CC44CECBD17ACD1A31DA15B061CFBD56E19F6FC48AE52C8992965` |
| `evidence/hito6-qsearch-futility/linux/clang20-debug.log` | 37,201 | `25C5BE348BD0EE3635CAD20A459B21CBAD2A944BAC99BEE27D07D04291E9C81C` |

The machine-readable manifest binds those logs and their artifact hashes to the
source tree, frozen network and pinned container images: GCC
`sha256:9ca91b05c7b07d2979f16413e8b2cd6ec8a7c80ffca4121ccab0aeba33f90460`
and Clang
`sha256:e987da8a4ed17dbb42a67319a502bd5fac6759821388949165490dbd20bf7079`.
It is versioned as `evidence/hito6-qsearch-futility/linux/manifest.json`
(14,508 bytes, SHA-256
`04B662BE5CB8B36AB98B95861CF1D922A7298900AF7A947CB5AFE2C6FEA08C63`).
The exact 2,938-byte gate script used by the four jobs is preserved beside the
manifest at SHA-256
`1DE69881B68C9E9000750ABF01E8B0F2E801DF23F2ED3257D3681D77D8331FC0`.
The reusable `tests/linux_hito_gate.sh` additionally rejects malformed metadata
and any short SHA that is not a prefix of the supplied full HEAD.
The mounted Syzygy corpus contains 14 table files: the test pins and validates
the 13 required fixtures; the manifest separately records the unused auxiliary
`KBBvK.atbw` hash instead of overstating its coverage.

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
The final 3,619,835-byte Windows release driver is SHA-256
`E062A660251EB645CD11E84BF65CB2CD0351FDD26C0F08596BC82BE222E92437`.

## Generator and trainer compatibility

Pinned `variant-nnue-tools` commit
`22c271c30641134109252e0665a98837d4f74ba8` passed all 12 release, Fairy and
Stockfish checks in PR #26, including Valgrind, UBSan and TSan, after a clean
Codex review. Its integration gate covers Atomic-only engine selection,
historical 72-byte records, special moves, deterministic defaults, overwrite
refusal, conversions and seeded generation. The hardening reconstructs the
complete unique en-passant state for custom initial moves, rejects ambiguous
legacy-v1 provenance, rejects Chess960 selected either explicitly or by the
variant across every legacy entrypoint, and initializes `Thread::trend` before
direct data-generation evaluation. The cross-repository pipeline uses
`Use NNUE=pure` only while generating training records; conversion and final
engine-loading commands use their appropriate non-pure modes.

The current `variant-nnue-pytorch` native loader passed CTest `1/1`, and its
Python suite passed `30/30` on CPU and CUDA. That covers the pinned 32-record
fixture, native decoding, seeded one-versus-four-worker determinism, ownership
and error cleanup, and a complete HalfKAv2 forward/backward pass. Search blocks
1 through 3 do not alter positions, the wire format, features, accumulators or
network serialization, so this audit found no pipeline compatibility regression.

`tests/legacy_pipeline_e2e.py` now closes the executable cross-repository path:
it generates twice from the frozen network with a fixed seed, requires
byte-identical 72-byte data, validates and round-trips every record, checks the
exact HalfKAv2^ sparse batch, executes one real training/backward/Ranger step,
requires finite gradients and parameters plus non-zero FT/FC updates,
serializes, reimports byte-exactly and finally loads the newly written network
in the engine for evaluation and search. The final block-3 rerun passed with:

- source network SHA-256
  `99DC67EABF26A64FAEECA3A88B4C38597A840B8D4A874B9F2CF658C6F92A04A6`;
- deterministic data SHA-256
  `7DE72B1385DBC8E37312A513D1CF4C7D99F889ECB8B747F548ED32E8D7A261A2D`;
- serialized network SHA-256
  `A69FB0A7DC211AA4D8BB0974BA881F6CA0F98C5FBC30E0203B9E08B99076E3DC`;
- loss `0.0347999074`, FT delta `8.66651535e-05`, FC delta
  `8.454262e-07`, and final engine move `b2b3`.

This rerun used an explicitly identified **local dirty snapshot**, not a clean
multi-repository release claim. `variant-nnue-tools` is based on
`c8df2c39515a2654d5b52ba55b4ee585b20430a8` with canonical changed-content
SHA-256 `EDA1693CBA433B32DE2B9406FBB16394465DDDF087FA9776AD40E81AE1C557C6`;
`variant-nnue-pytorch` is based on
`b15df38a9aae8ab9b40b2378020b3099c7c5d179` with canonical changed-content
SHA-256 `3175E1B1C3C8455E1392D87BDD9521D370C004FE531650EBBF18EBE50C8B0DA6`.
That canonical hash is the SHA-256 of a UTF-8/LF stream beginning with
`label=<value>`, `base_head=<value>` and `branch=<value>` lines, followed by
file entries sorted ordinally by path as
`kind|path|bytes|UPPERCASE_SHA256|base_blob`. Every line has a final LF and
`base_blob` is empty for untracked files.
The 21,215-byte content manifest is
`evidence/hito6-qsearch-futility/pipeline-local-snapshot.json`, SHA-256
`5BBA7B3F50459A762A692702E67E8DC2E7B6253A4C07BC41407ED64B089A3E5A`.
It records all 16 tools and 20 trainer changed/untracked files plus the exact
tools executable
(`CC17E29E8B4953A2219F3AB63FDF25180DBBF5B3D6AA1CFBCBDE17DC75A024F1`)
and native loader
(`F4349EA5125F13F807087BA5EC15FE4801B6A3567A847B5598CD7741D787DFF7`).
These hashes identify the local state but cannot
reconstruct uncommitted content from the base commits alone. They therefore
remain historical evidence and are not inputs to the new release lock.

The normalized rerun log is 2,591 bytes, SHA-256
`898C172E2A2540E4DD87F1B10DF5101E31F35858C8F03CF80B620443C070F697`,
at `evidence/hito6-qsearch-futility/pipeline-e2e.log`. It records the candidate,
network, generator, loader, runner and snapshot hashes and ends with
`pipeline_exit_code=0`. The local pass does not waive the clean pinned gate.

### Clean commit-pinned pipeline snapshot

The lock/build/profile unit suite passed `76/76`. The clean local
`strong-local` E2E used tools commit
`22c271c30641134109252e0665a98837d4f74ba8`, trainer commit
`dafec1daa594ff7eff3dca79064ed10660702a36` and Atomic commit
`e0b58ebd9c171eb8555dbb2827ccdf90c7f5a924`. Its three clean-build manifests
are preserved under `evidence/hito6-pinned-pipeline/manifests/` with SHA-256:

- tools: `874F3A9313F0E0B29ADC88BC79A4898FE17229F88B9CF2BF03B228E9C8E29FEF`;
- trainer: `C37A0E12C316D4849E05763C126A8CFBFED99E38174356525FA9F075A5787CB2`;
- Atomic BMI2: `6E7C050EAE18A08116E1654EA307779A95FEF50A466593DFECB7107FC29E8C82`.

The local profile reproduced data SHA-256
`7DE72B1385DBC8E37312A513D1CF4C7D99F889EC8B747F548ED32E8D7A261A2D`,
trained and reloaded network SHA-256
`A69FB0A7DC211AA4D8BB0974BA881F6CA0F98C5FBC30E0203B9E08B99076E3DC`,
and finished with loss `0.0347999074`, FT delta `8.66651535e-05`, FC delta
`8.454262e-07` and `bestmove=b2b3`.

The public `synthetic-ci` profile passed in
[run 29177376824](https://github.com/Belzedar94/Atomic-Stockfish/actions/runs/29177376824),
[job 86608800030](https://github.com/Belzedar94/Atomic-Stockfish/actions/runs/29177376824/job/86608800030).
The run metadata is attached to branch head `e0b58ebd`; because this was a
`pull_request` event, the tested checkout was synthetic merge
`7968672f37f537aed6eeff169e5524f91a88f853` of that head into base
`753060ade187cf6b74428dd4f929b7380d6073a0`. It reproduced source SHA-256
`9CF054CA00B82AB53A34473DE52D1104AEDDAA19B2E7B24091B5E613AF485985` and
data SHA-256
`95565809C53E914A192D095B18C7BAB9A0C35AF9510347DC2C63BAA385D69988`,
then trained and loaded network SHA-256
`0F84B1702A48540BBDED521B227524FA55B72A92E36CA9CADC4FDDF4345F95C7`
with loss `0.000145109807`, FT delta `9.38773155e-07`, FC delta
`1.50070571e-06` and `bestmove=b2b3`.

The normalized 2,051-byte summary at
`evidence/hito6-pinned-pipeline/summary.log`, SHA-256
`00F51FD8F0062AFC62E93ED5EFA799996B6CCE201D630124C770EC267EADE654`,
records the observed local output and GitHub Actions API/job-log facts without
claiming to be raw stdout.

The hardened runtime candidate `ebfe9342` repeated the clean `strong-local`
profile with the same pinned tools and trainer. Its 1,680-byte Atomic build
manifest is SHA-256
`169D4C1A9D93F95555FDD74B5149C7B26894CC1A1C153C35B5073C2969C9DBBC`;
the run reproduced data SHA-256
`7DE72B1385DBC8E37312A513D1CF4C7D99F889EC8B747F548ED32E8D7A261A2D`,
network SHA-256
`A69FB0A7DC211AA4D8BB0974BA881F6CA0F98C5FBC30E0203B9E08B99076E3DC`,
loss `0.0347999074`, FT delta `8.66651535e-05`, FC delta `8.454262e-07`
and `bestmove=b2b3`, with every checkout, environment and artifact postflight
passing. The public pinned profile for the exact same source head passed in
[run 29178527310](https://github.com/Belzedar94/Atomic-Stockfish/actions/runs/29178527310),
[job 86612025190](https://github.com/Belzedar94/Atomic-Stockfish/actions/runs/29178527310/job/86612025190).
The 1,563-byte normalized observed-facts summary is
`evidence/hito6-nmp/bmi2/final-ebfe9342/pipeline-strong-local-summary.log`,
SHA-256
`526B47B30FF98726802AFEE5644F4E36A3E050ECE67DD9A48D154372D3839B4C`;
it is explicitly not represented as raw stdout.
Together with the final speed and LOS evidence above, these results close the
runtime candidate's pipeline component. PR acceptance still requires the
exact-head matrix and review condition stated at the top of this record.

The normative Hito 5 release runner now requires one all-or-none seven-input
set containing the tools artifact, trainer root, Atomic generator and all four
tools/trainer/playing/generator clean-build manifests. It reuses its already
SHA-pinned `--net` through the explicit `strong-local` profile. The E2E reads
`tests/legacy_pipeline.lock.json` and fails on a floating/wrong HEAD, dirty
worktree, cross-checkout artifact, stale manifest or artifact not produced by
the tracked recipe. Only smoke mode may omit the complete seven-input set, and
it then receives the explicit non-release omission marker; partial
configuration is always a hard error.

The dedicated `Pinned Legacy Atomic V1 pipeline` GitHub Actions job implements
the public counterpart without redistributing the strong network. It checks
out the Belzedar94 tools and trainer repositories at lock-file commits, runs
their internal native/CPU suites, builds and tests Atomic-Stockfish, creates an
ephemeral deterministic zero-weight HalfKAv2 network with the pinned trainer,
and runs the complete generator-to-engine E2E in `synthetic-ci`. Since H7.2-C,
PV generation comes only from Atomic-Stockfish; the pinned `atomic-data-tools`
artifact is a validator/converter built through its root wrapper and no
historical tools-generated control dataset remains. Run `29177376824`, job
`86608800030`, passed the resolved pre-H7.2-C profile with the former
tools-control plus Atomic flow; it remains historical baseline evidence and is
not evidence for the new unidirectional gate. H7.2-C acceptance requires its
own exact-head workflow pass. Automatic continuation from an existing `.nnue`
and the production general dataset validator remain separate trainer/tools
release debts; this gate does not claim to implement either feature.

## Historical pre-BMI2 selectivity and speed

The following block-local records predate the compiler-target preflight. They
compare an AVX2 Atomic candidate with the historical SSE Fairy executable and
are therefore useful for tree/selectivity diagnostics, not for a normative
speed claim. Hito 6 acceptance uses only the matched BMI2 comparison later in
this document.

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
bytes smaller. This historical invocation reported a pass under the old
runner, but it is not an accepted performance gate because the ISAs differ.

### Block 2 clean performance snapshot

After integrating the reviewed Hito 4 and Hito 5 fixes, the clean AVX2 build
used the same pinned corpus, CPU, network and five-run procedure. The capture
futility block produced:

| Binary | Median NPS | Bytes | SHA-256 |
| --- | ---: | ---: | --- |
| Atomic-Stockfish | 1,483,243 | 4,263,813 | `CB35D57E3AD107C279781AF3E764EC412D59341C21D5FDC75AFAB08239CFFC14` |
| Frozen Fairy baseline | 1,104,296 | 4,477,632 | `1AE6D680F03128C8404F31A3F264F28B132B557ED3A91A6445EC563A7A33F623` |

The clean ratio is `1.3432`, or `+34.32%`; the candidate is 213,819 bytes
smaller. This is an ISA-confounded historical result; the independently
executed strength samples are retained below for attribution only.

### Block 3 clean performance snapshot

The final qsearch artifact used the same pinned 13-position corpus, network,
CPU affinity, one-thread/64-MiB configuration, one warm-up and five alternating
measured repetitions at 100,000 nodes per position:

| Binary | Median NPS | Bytes | SHA-256 |
| --- | ---: | ---: | --- |
| Atomic-Stockfish | 1,445,484 | 4,263,216 | `E13F11F4FBC0459F8349B4F6D0B0BDD03600292B1027BFC311C42D236480976B` |
| Frozen Fairy baseline | 1,085,861 | 4,477,632 | `1AE6D680F03128C8404F31A3F264F28B132B557ED3A91A6445EC563A7A33F623` |

The ratio is `1.3312`, or `+33.12%`; the candidate is 214,416 bytes smaller.
Because the baseline was SSE and the candidate AVX2, this is not the final
performance gate.
The complete persisted log is
`evidence/hito6-qsearch-futility/speed-vs-fairy.log` (1,222 bytes, SHA-256
`BE73B069406F8928C45C46800923B01383B77AEF0F1047F4138B8E7FA7C25793`).
The table records the preflight-verified immutable candidate and frozen Fairy
artifacts used by that invocation; the raw runner output itself does not print
their paths or hashes.

## Historical block strength samples before BMI2 parity

Blocks 1 through 3 used the old SSE Fairy executable. They remain useful
evidence that each small search change was not catastrophically weak, but the
ISA mismatch also changes effective nodes per second and disqualifies these
matches from the final strength gate. The accepted BMI2-vs-BMI2 matches are
recorded in the final block-4 section.

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

### Block 3: qsearch capture-futility safety

The final qsearch artifact passed all three exact gates. The normalized Elo
values were respectively `97.37`, `144.97` and `134.52`.

| TC | Total | W-L-D | Elo (95%) | Pentanomial | Draws | Time losses | LOS |
| --- | ---: | ---: | ---: | --- | ---: | ---: | ---: |
| 2+0.02 | 156 | 59-27-70 | +72.29 +/-40.8 | `[2,8,33,26,9]` | 44.87% | 0 / 0 | 100.0% |
| 10+0.1 | 108 | 38-9-61 | +95.64 +/-42.8 | `[0,3,24,22,5]` | 56.48% | 0 / 0 | 100.0% |
| 30+0.3 | 108 | 37-10-61 | +88.74 +/-42.9 | `[0,3,22,28,1]` | 56.48% | 0 / 0 | 100.0% |

Every capture ends with the exact wrapper PASS marker, `Total > 100`, complete
color-swapped pairs and zero time losses. The reviewable UTF-8/LF logs are:

| Log | Bytes | SHA-256 |
| --- | ---: | --- |
| `evidence/hito6-qsearch-futility/tc1-2000-20.log` | 114,821 | `80A1C9177E5A61880344CD568B33005F383A7C30809A87F0B385987CA0B2FD70` |
| `evidence/hito6-qsearch-futility/tc2-10000-100.log` | 82,283 | `F90D938A4E270E11635316159C39EF4E7C8DEF16C49FA289ACFC7BB2969372F7` |
| `evidence/hito6-qsearch-futility/tc3-30000-300.log` | 83,757 | `992380CE8610DAAB260E8CA0346D05F402B50F0A3A6AFC1EDFB3E6C1D6368135` |

All three matches used candidate SHA-256
`E13F11F4FBC0459F8349B4F6D0B0BDD03600292B1027BFC311C42D236480976B`
and frozen Fairy SHA-256
`1AE6D680F03128C8404F31A3F264F28B132B557ED3A91A6445EC563A7A33F623`.
The logs record the frozen network, `Use NNUE=true`, `Threads=1`, `Hash=512`,
Atomic book use, four workers and no SPRT. The shared `variants.ini` emits the
same non-fatal missing-template startup diagnostics documented for block 1;
the requested Atomic template loads and every match completes normally.
