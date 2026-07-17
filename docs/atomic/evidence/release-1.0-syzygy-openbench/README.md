# Release 1.0 Atomic Syzygy OpenBench evidence

This directory records the six Atomic Syzygy strength measurements that the
project owner stopped and accepted after inspecting their health. It is an
explicit owner waiver, not a rewrite of OpenBench state: all six point estimates
were positive, but none reached its requested 2,000 games and OpenBench marked
none of them `passed`.

The exact owner disposition was: “He parado los tests porque los he dado por
buenos. Healthcheck hecho.” Therefore the only aggregate acceptance claim is
**6/6 positive point estimates accepted by the owner**. This evidence does not
claim 6/6 completed tests, 6/6 LOS gates, or a combined Elo over unlike
comparators.

## Authority and frozen inputs

`results.json` is transcribed from the read-only backup
`db.sqlite3.bak-predatagen-20260715-1638`, 299,008 bytes, SHA-256
`4E967DB2381129B66EC85E12EE5EC83977A07A69AD62862FCC0613A042C58291`.
The accepted counters in `OpenBench_test` and `OpenBench_result` are normative.
The worker logs are supporting diagnostics only. In particular, the raw log
contains 140 attempted results for test 38, while the database accepted 134
before the STOP; this document uses 134.

The six tests ran on OpenBench commit
`75ef3f220584daf780869f12984470a7ca2c6b74` with:

- Atomic-Stockfish commit
  `388bae077f3c6bd17182ac6883b3436e9eb85e42`, signature `338376`;
- the external NNUE SHA-256
  `99DC67EABF26A64FAEECA3A88B4C38597A840B8D4A874B9F2CF658C6F92A04A6`;
- book `ATOMIC_syzygy_6man.epd`, 176 bytes, SHA-256
  `AD83B0F3B8EE08D0F61F2F9AFA11C1C72978AD0462D63A306C32697C92C5B449`;
- 1,020 external 3–6-man table files totaling 236,554,027,392 bytes,
  inventory SHA-256
  `3D4B7FD0AB387F4F60DA2078F612C9E8890E6026F551AEBE8631EFC157788F23`;
- `SyzygyProbeLimit=6` on the development side and `0` on the control;
- one thread, pentanomial accounting, no Syzygy adjudication, and no uploaded
  PGNs.

Tests 37–40 used the exact same Atomic binary on both sides and changed only
the tablebase probe limit. Tests 41–42 compared that Atomic binary with tables
against the frozen Fairy harness commit
`756810e8925d095d9c569c1a51451d9d1349c290`, which pins Fairy upstream
`fb78cb561aa01708338e35b3dc3b65a42149a3c4` and has no Atomic table probe.

The tested and release-preparation commits have the identical Syzygy source
tree `48d8f935642ca495022c7288820c94c49abf791b`, identical driver/UCI test
blobs, and identical fixture tree. This supports carrying the strength evidence
forward; it does not replace the exact-tag functional gate.

## Per-test results

Elo and 95% error are the OpenBench pentanomial values. LOS is retained only as
a post-hoc diagnostic and is not an acceptance gate.

| ID | Comparison | Eval | TC | Games | W-L-D | Pentanomial | Elo (95%) | Diagnostic LOS | DB state |
| ---: | --- | --- | --- | ---: | --- | --- | --- | ---: | --- |
| 37 | same Atomic, TB6 vs TB0 | NNUE | 8.0+0.08 | 240/2000 | 117-72-51 | `[2,1,67,50,0]` | +65.92 ±19.30 | 99.946844% | STOP; passed=false |
| 38 | same Atomic, TB6 vs TB0 | NNUE | 40.0+0.40 | 134/2000 | 67-50-17 | `[0,0,50,17,0]` | +44.32 ±18.54 | 94.198419% | STOP; passed=false |
| 39 | same Atomic, TB6 vs TB0 | classical | 8.0+0.08 | 144/2000 | 68-31-45 | `[2,2,25,43,0]` | +91.32 ±30.25 | 99.989985% | STOP; passed=false |
| 40 | same Atomic, TB6 vs TB0 | classical | 40.0+0.40 | 48/2000 | 24-7-17 | `[0,0,7,17,0]` | +128.62 ±37.65 | 99.886827% | STOP; passed=false |
| 41 | Atomic TB6 vs Fairy TB0 | NNUE | 8.0+0.08 | 48/2000 | 23-8-17 | `[0,1,7,16,0]` | +112.33 ±45.44 | 99.647083% | STOP; passed=false |
| 42 | Atomic TB6 vs Fairy TB0 | NNUE | 40.0+0.40 | 46/2000 | 23-9-14 | `[0,0,9,14,0]` | +109.20 ±39.68 | 99.333584% | STOP; passed=false |

All six have wins greater than losses and positive Elo point estimates. Tests
37, 39, and 41 recorded respectively 10, 6, and 4 time losses, all on the
development/tablebase side; this makes their positive point estimates
conservative rather than opponent-time-loss artifacts. The database records no
crash, failed test, error test, or illegal worker result.

The worker stderr contains 24 warnings saying that the Fairy baseline lacked
`UCI_Variant`. A follow-up handshake against the stored baseline binary did
advertise `UCI_Variant` with `atomic`, and tests 41–42 recorded no crash or
illegal result. The warning is preserved as a diagnostic caveat, not hidden.

## Homogeneous subgroups

Only like-for-like tests are combined:

| Subgroup | IDs | Games | W-L-D | Pentanomial | Elo (95%) | Diagnostic LOS |
| --- | --- | ---: | --- | --- | --- | ---: |
| same-binary NNUE | 37, 38 | 374 | 184-122-68 | `[2,1,117,67,0]` | +58.13 ±14.04 | 99.980317% |
| same-binary classical | 39, 40 | 192 | 92-38-62 | `[2,2,32,60,0]` | +100.42 ±24.77 | 99.999891% |
| Fairy-baseline NNUE | 41, 42 | 94 | 46-17-31 | `[0,1,16,30,0]` | +110.80 ±29.82 | 99.987073% |

There is intentionally no six-test Elo: the first four tests measure the
incremental effect of tables on one binary, while the final two also change the
engine implementation.

## Source-equivalent functional preflight

On 2026-07-16 the existing BMI2 engine and driver built from commit
`6f12bcefcd8fdb2567437ce91ebcd1ba0dd515b1` were run against the combined
1,020-file corpus. `git diff` showed no changes from that build commit to the
then-current release-preparation HEAD in `src/`, either Syzygy test, or the
Syzygy fixtures. The direct driver suite passed `5/5`; the production UCI suite
passed table loading, root/interior probes, terminal and touching-king cases,
Atomic960, recoverable paths, the six-man limit, all five analysis positions,
and NNUE `false`/`true`. The five positions produced nonzero `tbhits` in both
evaluation modes.

This is a source-equivalent local preflight, not the exact-tag gate: the
executables embed the earlier build commit. The final tag must rebuild them and
repeat the same commands.

## What remains for release 1.0

The strength disposition is closed by the owner's explicit waiver. The release
controller must still run and preserve the driver, production UCI, and real
table on/off fixtures against the exact release tag. Historical OpenBench
results cannot satisfy that conformance gate.

`tests/python/test_atomic_syzygy_openbench_evidence.py` validates this evidence
fail-closed: exact keys and IDs, hashes, STOP/non-pass state, W/D/L and
pentanomial arithmetic, OpenBench Elo recomputation, homogeneous subgroup
aggregation, and the absence of any completed-2,000/LOS/pass claim.
