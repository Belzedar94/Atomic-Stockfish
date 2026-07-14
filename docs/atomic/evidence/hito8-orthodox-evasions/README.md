# H8.3b unreachable orthodox evasions evidence

H8.3b removes the concrete orthodox `EVASIONS` consumers after H8.3a made
`Position::checkers()` a compile-time constant zero. Atomic check handling is
unchanged: main search and qsearch use `atomic_in_check()`, and legal move
generation continues to enumerate the complete capture-plus-quiet set before
the Atomic legality filter. The generic `GenType::EVASIONS` enum and
compile-time source branches remain available to avoid unrelated ordinal and
upstream-source churn.

The measured source commit is
`1fecfec95eb9b2b50166be882a5d315a4396882f`, based on H8.3a squash merge
`063eede4f6176c2f438a7fea54ce682d293997dd`.

## Validation

- BMI2 release artifact: 4,257,648 bytes, SHA-256
  `B47DD600D41BC47AF996C4A3ABC6C8189F3EF89A91ACC032C4FD2B687EDC71F5`.
- H8.3a control: 4,262,552 bytes, SHA-256
  `5335201E2D4EFBA9B34814D7258E83118D2C8A60EA0C4D538750D31E3118911E`.
- C++ unit tests 67/67 in release and debug/assert; shared API 34/34.
- `test.py` 22/22 and focused Python/structural pytest 67/67 after an MSVC
  stable-ABI extension build.
- Eight Atomic/Atomic960 perfts and 19/19 focused rule transitions.
- Search 16/16 with classical evaluation and 16/16 with LegacyAtomicV1;
  XBoard, all three NNUE modes, invalid-network recovery, and reprosearch
  12/12 passed.
- The playing signature remained exactly `338376`.
- One million deterministic make/undo operations reproduced 18,761 captures,
  241,087 full-refresh comparisons, and state signature
  `0x8742E39B793C46AB`.
- The 10,000-position frozen-Fairy differential had zero final playing-mode
  delta and maximum pure-trace delta 0.005.
- The real MinGW data generator reproduced seven fixtures with data SHA-256
  `7E89411B84C2036DEEB2DB56F3E43FEA89917C5546C72C37F8E082F103B27CC0`.
- The optimized object contains no concrete `EVASIONS` specialization symbol.
- An independent audit found no P0-P3 issue and additionally exercised an
  en-passant Atomic check evasion, promotion blast evasion, castling, and
  Atomic960.

Two setup errors were rejected rather than counted. The first perft command
did not give nested Bash the MSYS path and therefore selected unavailable WSL;
a second command named a nonexistent Python 3.13 path after completing only
the historical perfts. The complete suite was then rerun with MSYS and the
installed Python 3.12 interpreter. Separately, an absolute MSYS network path
containing spaces could not be consumed by the Windows engine in
`signature.sh`; the same frozen network was passed as a relative Windows-safe
path and the exact signature gate passed.

## Serialized speed measurement

The OpenBench worker was paused as one process tree for each measurement
window and restored in `finally` with a newly generated 32-byte password sent
only through environment variables. Ten complete batches produced 50 samples
per side:

- initial 25-sample median: candidate 1,382,409, control 1,386,830,
  ratio 0.9968 (-0.32%);
- independent extension median: candidate 1,400,266, control 1,391,280,
  ratio 1.0065 (+0.65%);
- pooled 50-sample median: candidate 1,392,770, control 1,388,311,
  ratio 1.0032 (+0.32%); and
- six batches favored H8.3b, three favored H8.3a, and one tied.

This is a small positive pooled signal with visible short-run noise, not a
precise speed claim. The invariant results are an unchanged playing signature,
4,904 fewer executable bytes, and removal of an unreachable code path. Every
raw sample is in `commit-ab.log`.
