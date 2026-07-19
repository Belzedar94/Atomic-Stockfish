# Atomic-Stockfish on OpenBench

Atomic-Stockfish is a public-source OpenBench engine. A worker builds it from
`src/` with this contract:

```text
make -j EXE=<output> CXX=<compiler> EVALFILE=<absolute-network-path>
```

When `EVALFILE` is present, the default Make goal selects `x86-64-bmi2` and the
host compiler family (`mingw` on Windows, `gcc` elsewhere), while forwarding
the worker's `EXE` and `CXX` unchanged. Without `EVALFILE`, ordinary developer
invocations keep the upstream `help` default. OpenBench first authenticates the
SHA-addressed network; the shim copies it to the canonical short name inside
the worker's ephemeral checkout and embeds it in the executable. The resulting
binary has no dependency on the temporary source directory or a worker-specific
absolute path.

The frozen `atomic_run3b_e202_l05.nnue` network has SHA-256
`99DC67EABF26A64FAEECA3A88B4C38597A840B8D4A874B9F2CF658C6F92A04A6`.
With that network, the deterministic bare `bench` contract is:

```text
Bench: 338376
```

OpenBench strength tests use `Use NNUE=true`; `Use NNUE=pure` remains reserved
for data generation. Classical comparison presets still assign the frozen
network because OpenBench validates the same bare NNUE bench before games, then
set `Use NNUE=false` for play.

Atomic tablebases are worker-local artifacts. Start capable workers with
`--atomic-syzygy <path>`; the OpenBench server schedules required six-man jobs
only to workers that validate a complete `.atbw` set. Cutechess Atomic
adjudication does not understand `.atbw`/`.atbz`, so Atomic tests must keep
Syzygy adjudication disabled and use engine-side `SyzygyPath` probing only.

An additive experimental datagen contract V2 consumes OpenBench v40's complete
`syzygy`, `syzygy_manifest_sha256`, `syzygy_max`, `teacher_mode` field group.
The worker is authoritative for inventory-file authentication; the engine pins
the same official SHA, requires exactly loaded six-piece tables, derives the
three fixed probe options and binds the claim plus native probe/hit counters to
new manifest/attestation/bundle schemas. `producer_sha256` is supported as an
orthogonal executable-artifact identity. Teacher mode is byte-exact `pure` or
`true`. ADR 0007 fixes publicable production datagen to `pure`; `true` remains
available only as an authenticated control. Implementing this capability does
not launch or use OpenBench.
