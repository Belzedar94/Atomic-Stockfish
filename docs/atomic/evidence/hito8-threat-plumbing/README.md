# H8.2b legacy threat-plumbing evidence

H8.2b removes the now-unreachable `DirtyThreat` update implementation and its
supporting attack tables after H8.1 and H8.2a proved that LegacyAtomicV1 uses
only `DirtyPiece` deltas. The active feature set, network serialization,
incremental accumulator semantics, rules, search, protocols, and bindings are
unchanged.

The removed `LineBB` and `RayPassBB` matrices account for 65,536 bytes of
process-static bitboard storage. The patch also removes the unused AVX-512
writer, `Position::piece_array()`, `PawnPushOrAttacks`, and
`ValueList::make_space()`. A compile-time assertion remains beside the active
accumulator to prevent a future feature set from silently requiring threat
deltas that `Position` no longer records.

The normative speed command was:

```text
python tests/atomic_bench_ab.py --candidate src/atomic-stockfish.exe --control ../Atomic-Stockfish-h8-full-threats/src/atomic-stockfish.exe --eval-file ../atomic_run3b_e202_l05.nnue --nodes 100000 --hash 64 --affinity 24 --timeout 60
```

The local OpenBench worker was stopped for the serialized measurement and
restored in the same script's `finally` block. Its credentials were rotated in
memory and passed to the restarted process only through environment variables.
The server accepted the restored worker through `clientGetBuildInfo`,
`clientWorkerInfo`, `clientGetWorkload`, and subsequent result submissions.

The candidate passed the strict median-NPS gate by 0.88%. Its PE file is 4,188
bytes larger because the code and LTO layout changed; that on-disk observation
does not restore the two deleted 32 KiB runtime matrices. Complete measured
output is retained in `commit-ab.log`, and `manifest.json` binds the source,
artifacts, network, corpus, compiler, and result.
