# H9.3i-b AtomicNNUEV3 incremental stress evidence

H9.3i-b leaves the frozen 39-event H9.3i-a differential unchanged and adds a
separate private stress executable. AtomicNNUEV3 is still unreachable from
search, UCI/XBoard, Python, JavaScript, WASM, generator and trainer builds.

## Authenticated input and fail-closed boundary

The Python wrapper and the C++ runner both require the H9.3g mixed-wire fixture:

```text
size    77349879
sha256  00E46223822D06D7927E884EEC10739BA19EF8DD82A6E262F627D361658080C2
```

The runner reads and hashes the fixture once into owned memory, then constructs
both network identities from those exact bytes. A path replacement after the
wrapper check therefore cannot change the network consumed by either load.
The C++ consumer is executed directly against a truncated fixture and a
same-size, one-byte-mutated fixture; it must reject the former by size and the
latter by SHA-256 before parsing either network.
Explicit zero values for operations, refresh interval and threads fail instead
of being confused with omitted defaults.

## Frozen corpus

- 4 standard castlings: both colors and both sides.
- 11 Atomic960 layouts: stationary king, stationary rook, overlap and exact
  swap, including black-side cases.
- 32 directed moves, including 23 captures, 8 king-removal terminals, 19
  promotions, 6 en-passant captures, both colors, a five-piece
  capture-promotion blast and the maximum nine-piece Atomic blast.
- King mirror and material-bucket crossings.
- Parent/cleared/restored EP under a no-push null move.
- A lazy stack from the evaluated root to exactly `MAX_PLY`, followed by exact
  FEN/key/scalar restoration.
- Missing/multiple kings, invalid side/piece, both per-color material limits,
  network mismatch/reset and injected failures after one perspective, before
  composition and after composition but before commit.
- Eight fixed legal Atomic/Atomic960 roots with deterministic make/undo
  trajectories. Independent stacks share one immutable network under real
  1/2/4/8-thread scheduling.

Every successful evaluation is bit-identical to the H9.3h scalar full refresh.
Every failure clears its diagnostic and preserves depth, frame and cumulative
counters. The state signature also commits to HM-only accumulators, source
kind/ply/distance, row counts, EP/snapshot diagnostics and event counters, so a
backend that always full-refreshes cannot pass.

The independent Python differential remains the frozen 39-event H9.3i-a trace;
this larger H9.3i-b corpus does not claim a second independent Python model.
Its assurance chain is explicit: every new event is compared with the H9.3h
C++ full-refresh backend, while each H9.3a-h feature and scalar layer and the
unchanged H9.3i-a event trace remain independently checked by Python. Extending
the Python model to duplicate this random corpus would add a second state and
move engine, not an independent check of the incremental scheduling logic.

## Frozen profiles

```text
smoke    operations=4096     interval=1 threads=1 signature=45D43FB02CAA9A3D
release  operations=65536    interval=1 threads=8 signature=E86C39BDF8187078
soak     operations=1048576  interval=1 threads=8 signature=AF6B51180815972B
```

On local MinGW, smoke reproduced `45D43FB02CAA9A3D` with 1, 2, 4 and 8
threads. Release completed 65,544 evaluations. Soak completed 1,048,584
evaluations, including 18,965 random captures and 3,992 random terminal
failures. All profiles fully unwound every trajectory.

The scalar backend, incremental backend and stress runner compile under GCC and
MinGW with a 128,000-byte stack-usage error ceiling. CI repeats smoke/release
with GCC and Clang, smoke with two scheduling workers, forced wire layouts,
debug/assert, MinGW, ASan/UBSan, TSan and Valgrind. Soak remains a local release
gate because it deliberately executes more than one million full-refresh
comparisons.

No bench, Elo, LOS, OpenBench, data generation or training claim applies to
H9.3i-b. Real SIMD begins only after these scalar signatures reproduce in CI.
