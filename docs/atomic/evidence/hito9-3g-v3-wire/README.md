# H9.3g AtomicNNUEV3 numeric and wire evidence

H9.3g freezes a private, canonical AtomicNNUEV3 file and arithmetic contract.
It does not register V3 with the production dispatcher and does not change any
reachable evaluation, search or time-management path.

## Frozen identity

- File version: `0xA70C0003`.
- Slice hashes: HM `0xA34A8666`, CapturePair `0x9AEDB186`, KingBlastEP
  `0xF5172BC0`, BlastRing `0x38377946`.
- Folded feature hash: `0xA3FBDBE8`.
- Transformer descriptor: 799 ASCII bytes, hash `0xCC31067A`.
- Feature-transformer hash: `0x6FCAD592`.
- SFNNv15 architecture hash: `0x63337116`.
- Complete-network hash: `0x0CF9A484`.
- V3 schema SHA-256:
  `9D3C77A58E5E55AC1BC798DAB41977451EB523FCE1D6FD3EC3F7C1E574A78750`.

The deterministic synthetic fixture is deliberately not retained in Git. It
is reproduced by `tests/create_synthetic_atomic_v3_nnue.py`, is 77,349,879
bytes and has SHA-256
`00E46223822D06D7927E884EEC10739BA19EF8DD82A6E262F627D361658080C2`.
Normal generation authenticates these pins and refuses overwrite. C++ and
Python round trips reproduce the exact bytes and agree on all 248 selected
canonical-to-internal values. The C++ CLI acquires the destination directly
with `O_EXCL`/`CREATE_NEW`, streams through that descriptor, synchronizes and
verifies it byte-exactly. A concurrent race between two byte-distinct valid
networks produces exactly one winner and preserves any foreign destination
byte-exactly; there is no staging pathname to substitute or clean up.

## Acceptance evidence

- Isolated numeric and wire selftests passed in MinGW x86-64 release,
  debug/assert, AVX2 and production BMI2 builds.
- The mixed-wire C++/Python differential passed in all four builds; the five
  existing V3 feature/full-refresh differentials remained exact.
- Portable forced-policy builds passed the same 65 wire selftests and exact
  differential for identity, AVX2/LASX and AVX512 layouts.
- Focused Python tests passed `63/63`; the complete pytest tree passed
  `1010/1010`; historical `test.py` passed `22/22`.
- The production dispatcher rejected this V3 fixture with `Use NNUE=true` and
  `Use NNUE=pure`. `Use NNUE=false` remained searchable, and authenticated
  Legacy V1 and AtomicNNUEV2 search remained accepted.
- UCI, XBoard, Atomic/Atomic960 perft and rules, reprosearch and signature
  `338376` passed. The perft/rules matrix covered `Use NNUE=false`, `true` and
  data-generation-only `pure` with the authenticated V2 network.
- Static gates passed Python 3.9 grammar, JSON/YAML parsing, clang-format and
  `git diff --check`.

CI repeats the contract across GCC, Clang, debug/assert and MinGW, with a
dedicated three-way portable policy matrix for identity, AVX2/LASX and AVX512.
It runs the authenticated fixture and isolated targets under ASan, UBSan and
TSan. The ASan/UBSan memory lane runs the full wire selftest plus differential;
Valgrind runs the numeric target and complete wire inspection. Dispatcher
rejection is a separate fail-closed CI gate.

No Elo or OpenBench test applies to H9.3g because the new code remains private
and unreachable from playing decisions. The unchanged playing signature is
the relevant strength-neutrality evidence. OpenBench starts only after an
execution backend and trained V3 network can affect search.
