# H8.3a compact `StateInfo` evidence

H8.3a removes Atomic-inactive orthodox check metadata from every search state:

- `checkersBB`, whose only stored value was zero;
- `pinners[COLOR_NB]`, which had no consumers; and
- the unused KING and sentinel entries from `checkSquares`.

The compact x64 layout is 160 bytes instead of 208 bytes. The copied prefix
still ends at `offsetof(StateInfo, key) == 64`, so the `do_move()` state-copy
boundary is unchanged. C++ contracts require `StateInfo` to remain standard
layout and trivially copyable. A deterministic unit corpus checks 166 legal
moves, including terminal explosion, en passant, all promotions available in
the position, orthodox-layout castling, and four Atomic960 castling
geometries. For every move, the pre-move `gives_check()` prediction matches
the complete child `atomic_in_check()` result and undo restores FEN and key.

## Validation

- BMI2 release artifact: 4,262,552 bytes, SHA-256
  `5335201E2D4EFBA9B34814D7258E83118D2C8A60EA0C4D538750D31E3118911E`.
- H8.2c control: 4,266,467 bytes, SHA-256
  `ACA0C03907D750D1991A61F94439B2539AAA25070FDCF0F22D0558E9EB9335E7`.
- C++ unit tests 65/65 in release and debug/assert; shared API 34/34.
- `test.py` 22/22 and focused Python/structural pytest 66/66 after an MSVC
  stable-ABI extension build.
- Real MinGW data-generator smoke: seven fixtures, frozen strong network, data
  SHA-256 `7E89411B84C2036DEEB2DB56F3E43FEA89917C5546C72C37F8E082F103B27CC0`.
- Eight Atomic/Atomic960 perfts and 19/19 focused rule transitions.
- Search 16/16 with classical evaluation and 16/16 with LegacyAtomicV1.
- XBoard, `Use NNUE=false|true|pure`, invalid-network recovery, and
  reprosearch 12/12.
- One million deterministic make/undo operations reproduced the frozen
  counters and state signature `0x8742E39B793C46AB`.
- The 10,000-position frozen-Fairy differential passed exactly in playing
  mode; maximum pure trace delta was 0.005.
- Playing signature remained exactly `338376`.

One orchestration command initially requested the Linux incremental-test
suffix `.bin` from MinGW. The release engine had already built successfully;
the nonexistent target was corrected to `.exe`, then the complete one-million
operation gate passed. This was a harness invocation error, not a source or
test failure.

## Serialized speed measurement

The extended measurement harness reported that it stopped the OpenBench
worker as a process tree and restored it in `finally` with a freshly rotated
32-byte credential passed only through environment variables. The preliminary
run exposed that this Windows PowerShell lacked the static RNG `Fill` method;
the worker still restarted, but the resulting credential had no entropy. It
was immediately stopped, securely rotated with the compatible instance API,
and health-checked before the extended measurement. The first five-sample
batch measured -0.33%, demonstrating the expected short-run noise. A fixed
extension of five complete batches produced 25 candidate and 25 control
samples:

- pooled candidate median: 1,420,139 NPS;
- pooled control median: 1,410,897 NPS;
- pooled ratio: 1.0066 (+0.66%);
- four of five extended batches passed the strict positive-median gate; and
- executable size fell by 3,915 bytes.

The result is recorded as a small positive signal with visible batch noise,
not as a precise +0.66% speed claim. The invariant conclusions are the 23.1%
smaller search-state layout, smaller executable, no differences observed in
the listed functional gates, and unchanged playing signature. Raw samples are
in `commit-ab.log`.
