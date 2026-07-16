# Atomic-Stockfish 1.0.0

Atomic-Stockfish 1.0.0 is the first stable release of the dedicated Atomic and
Atomic960 engine derived from Stockfish and validated against the frozen
Fairy-Stockfish Atomic reference.

## Included surfaces

- One native executable with UCI and XBoard/CECP support.
- Portable x86-64, AVX2 and BMI2 builds for Linux and Windows.
- The `atomic-pyffish` CPython 3.9 stable-ABI package for Windows and manylinux
  x86-64.
- The `@atomic-stockfish/ffish` CommonJS and ES-module Board API.
- A complete pthread Node UCI WebAssembly engine with external NNUE loading.
- Atomic Syzygy probing, including the Atomic six-man domain.
- Legacy Atomic V1 and AtomicNNUEV2 network loading, switching and export.
- Legacy V1 and Atomic BIN V2 data-generation and training pipeline contracts.

All protocol and binding variant enumerations intentionally expose only
`atomic`; Atomic960 is selected through `UCI_Chess960` or the corresponding
board-construction flag.

## External files

No NNUE network or third-party tablebase is embedded in or redistributed with
the release. `EvalFile` accepts authenticated compatible Legacy Atomic V1 or
AtomicNNUEV2 files. `SyzygyPath` points at Atomic tablebases; orthodox Syzygy
files are rejected by the Atomic domain checks.

The strongest frozen validation network was
`atomic_run3b_e202_l05.nnue`, SHA-256
`99dc67eabf26a64faeeca3a88b4c38597a840b8d4a874b9f2cf658c6f92a04a6`.
Its name is descriptive only: the engine validates network headers,
architecture and bytes rather than trusting a filename.

## Scope and known limits

- `Use NNUE=true` is the supported NNUE playing mode. `Use NNUE=pure` is a
  data-generation mode and is not presented as a playing-strength setting.
- The AtomicNNUEV3 feature, trajectory, reachability and trainer execution
  blocks are research infrastructure. Version 1.0 does not dispatch V3 networks
  in the playing engine.
- UCCI, USI and non-Atomic variants are deliberately out of scope.
- The completed, owner-capped 375-million-position Atomic BIN V2 bootstrap is a
  distributed-generation pilot, not a V3 publication dataset, and its
  completion is not a 1.0 release dependency.

Verify every downloaded asset with `SHA256SUMS` and
`atomic-stockfish-release-manifest.json` before use.

## Reproducibility and publication boundary

The release inventory records the immutable Linux GCC and Windows MinGW image
digests. Native executables and archives are built twice in isolated roots and
must be byte-identical; the packaged Windows executables are then downloaded by
a Windows runner for UCI/XBoard smoke. Python wheels consume only the normalized
sdist emitted by the source job, build twice with separate caches under the
tagged commit timestamp, must be byte-identical, and pass strict `abi3audit
0.0.26` plus installed-stub `mypy 1.19.1` checks. The Windows producer is also
restricted to CPython 3.9.13 and the exact hosted-runner, Visual Studio, SDK,
packaging-tool and compiler fingerprint frozen in the inventory. Because GitHub
hosted runner images cannot be selected by immutable digest, this guarantee
applies only while that exact image remains schedulable; image rotation requires
a new reviewed release commit and fingerprint. The canonical fingerprint JSON is
versioned beside the inventory and compared byte-for-byte by both Windows builds.
Board and UCI WASM provenance
records the exact digest-pinned Docker command used for each artifact. Every
provenance record also freezes the producer-side asset SHA-256, which is
rechecked during assembly.

The tagged release additionally requires a protected Windows exact-tag job.
It downloads the assembled bundle by same-run artifact ID, re-authenticates the
frozen Legacy V1 network, both Fairy oracles, the Atomic Syzygy inventory and
six-man fixtures, and the pinned tools/trainer repositories, then executes all
six commands in the tag-tracked plan with zero skips. Its canonical manifest
contains the raw five-run BMI2 samples so the medians and speed ratio can be
recomputed independently. The assembled bundle and the bounded text/JSON gate
evidence are both covered by GitHub artifact attestations produced with a
commit-pinned action.
The sequential gates have 1,470 minutes of tracked execution budget inside a
1,620-minute self-hosted job, leaving 150 minutes for setup and release-evidence
work. Child output is streamed under one 32 MiB bound per gate, and timeout or
overflow terminates the complete child process tree before cleanup continues.

Automation may create only a draft after GitHub release immutability is already
enabled, release PR #44 is proven to be a traditional two-parent merge on
`main`, the annotated tag object and peeled commit are exact, `Atomic CI` has
succeeded for the same tag/SHA push, the protected exact gates have passed, and
the same-run attestations verify. It downloads that draft from GitHub and
verifies the exact asset list and every byte. Publication remains a separate
manual decision; the workflow never promotes the draft automatically.
