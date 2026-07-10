# ADR 0001: Atomic Syzygy is a required native subsystem

- Status: accepted; implementation pending
- Date: 2026-07-10

## Context

Atomic six-piece Syzygy tablebases already exist and have been used successfully
by Multi-Variant Stockfish (MV-SF). Fairy-Stockfish deliberately did not expose
variant tablebases: their rules and indexing are variant-specific, which conflicts
with a generic engine intended to support thousands of variants. That objection
does not apply to an Atomic-only engine.

Historical implementation material is available in:

- [Fairy-Stockfish issue #330](https://github.com/fairy-stockfish/Fairy-Stockfish/issues/330),
  which identifies the MV-SF probe as the reference implementation.
- [Belzedar94/Fairy-Stockfish PR #9](https://github.com/Belzedar94/Fairy-Stockfish/pull/9),
  an unmerged 2025 port that is useful as an audit artefact but is not accepted
  wholesale.
- [MV-SF `tbprobe.cpp`](https://github.com/ddugovic/Stockfish/blob/master/src/syzygy/tbprobe.cpp),
  whose Atomic support was implemented by Niklas Fiekas.
- [MV-SF commit `c77b9a8f`](https://github.com/ddugovic/Stockfish/commit/c77b9a8f),
  the 2016 implementation titled `Fully support atomic tablebases`.
- [lila-tablebase](https://github.com/lichess-org/lila-tablebase), which provides
  the public Atomic API and an independent result oracle.

Discord history supplied two important review constraints for PR #9: the
material key must remain deterministic, and a variant-to-tablebase association
must not be encoded through pointer values or other process-dependent state.
The same discussion notes that probing must be in-process because search can
perform thousands of probes per second.

## Decision

1. Atomic-Stockfish will support local Atomic Syzygy tables. Tablebase support is
   a release requirement and must not be deleted.
2. The temporary `NO_TABLEBASES` build is a safety quarantine only. Orthodox
   probing remains disabled until Atomic indexing and semantics pass their
   differential tests.
3. The engine will retain the conventional UCI option names (`SyzygyPath`,
   `SyzygyProbeLimit`, `SyzygyProbeDepth`, and `Syzygy50MoveRule`) so existing
   GUIs continue to work. Their help and diagnostics must state that only Atomic
   tables are accepted.
4. The native file pairs use the Atomic suffixes `.atbw` and `.atbz`. The WDL
   `.atbw` magic is `55 8D A4 49`; the DTZ `.atbz` magic is `91 A9 5E EB`.
   Files are validated before they are registered.
5. Atomic support is a compile-time property. No dynamic `Variant` registry,
   variant-pointer salt, or variant-name dispatch will be added. Position,
   pawn, material, and tablebase keys must be deterministic across processes,
   compilers, and repeated runs.
6. MV-SF is the semantic/code reference. PR #9 may contribute test cases and
   isolated mechanics, but every changed indexing and DTZ rule must be checked
   against MV-SF and the public Lichess results.
7. Probing is local and synchronous. The Lichess HTTP API is permitted only in
   developer tooling that builds or verifies frozen fixtures; network requests
   are never made by the search hot path.
8. Atomic960 may probe only after castling rights have disappeared. At that point
   its endgame state is an ordinary Atomic state. Positions with castling rights
   are not tablebase positions.
9. Generator use is opt-in and versioned. If tablebase adjudication or labels are
   introduced, the dataset manifest records table hashes, cardinality, probe
   options, and adjudication policy. Legacy 72-byte data must not silently change
   meaning.

## Rejected implementation shortcuts

The unmerged PR #9 is not a correct probe despite compiling. The port must have
regressions for the defects found during this audit:

- The PR assigned the real WDL magic to DTZ and the real DTZ magic to WDL,
  causing valid Atomic tables to be rejected as corrupt.
- It added the connected-kings index but retained the orthodox `462` group
  multiplier. Atomic tables require `518`; the wrong value reads incorrect
  offsets for material classes with repeated pieces.
- It did not stop before probing a terminal Atomic position with a missing king.
- Interior probing was still guarded by `UCI_Variant == chess`, so only part of
  the root integration could ever run.
- It associated `nocheckatomic` and `atomar` with Lichess Atomic tables even
  though their rules are not the same. Atomic-Stockfish has no such variants.

## Required validation before enabling the UCI options

- Header, magic, suffix, truncated-file, corrupt-file, missing-path, and mixed
  orthodox/Atomic directory tests.
- Explicit swapped-magic and `462`-versus-`518` connected-kings regressions.
- Deterministic table registration and material keys across at least two fresh
  processes and all supported compilers.
- WDL and DTZ fixtures covering both sides to move, immediate explosions,
  adjacent kings, captures that explode a king, zeroing captures, pawn moves,
  en passant, promotions, rule-50 boundaries, and symmetric piece encodings.
- Differential results against MV-SF plus a frozen corpus produced from the
  Lichess `/atomic` API. Online access is not required to execute the suite.
- Root probing, interior probing, `tbhits`, move ordering, score conversion, and
  PV behavior with `Use NNUE=false`, `true`, and `pure`.
- Search results unchanged when no table is eligible, including Atomic960 with
  castling rights.
- A known six-piece regression position from the community discussion:
  `8/5k2/pr6/4R3/P5P1/4K3/1P6/8 w - - 1 1`.

## Consequences

The Stockfish Syzygy subsystem stays in the source tree and will be specialized
rather than generalized. This adds an implementation gate before the first
strength release, but it removes a known weakness of Fairy-Stockfish and gives
future solving work a local exact-endgame foundation.
