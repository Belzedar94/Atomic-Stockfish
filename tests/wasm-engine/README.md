# Atomic-Stockfish Node UCI/NNUE WebAssembly

This target builds the complete Atomic search engine, including the Legacy
Atomic V1, AtomicNNUEV2 and AtomicNNUEV3 readers, as a pthread-enabled
Emscripten command-line program. Networks remain external and are read at
runtime through Node's filesystem. Each test runner authenticates its requested
network by SHA-256.

From `src`, with Emscripten installed at `C:\emsdk\emsdk`:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File ..\tests\wasm-engine\build.ps1
node ..\tests\wasm-engine\run-engine-tests.mjs --engine ..\build\wasm-engine\atomic-stockfish-nnue-node.mjs --net ..\atomic_run3b_e202_l05.nnue
python ..\tests\create_synthetic_zero_nnue.py --output ..\build\wasm-engine\atomic-zero.nnue
python ..\tests\create_synthetic_atomic_v2_nnue.py --output ..\build\wasm-engine\atomic-v2.nnue
python ..\tests\create_synthetic_atomic_v3_nnue.py --output ..\build\wasm-engine\atomic-v3.nnue
node ..\tests\wasm-engine\run-dual-backend-tests.mjs --engine ..\build\wasm-engine\atomic-stockfish-nnue-node.mjs --legacy-net ..\build\wasm-engine\atomic-zero.nnue --legacy-sha256 9CF054CA00B82AB53A34473DE52D1104AEDDAA19B2E7B24091B5E613AF485985 --v2-net ..\build\wasm-engine\atomic-v2.nnue --v2-sha256 4DEB05CFF79B5D5EBA51C560F64ED24224671C188B6C5DB27521033E587C87C6 --v3-net ..\build\wasm-engine\atomic-v3.nnue --v3-sha256 00E46223822D06D7927E884EEC10739BA19EF8DD82A6E262F627D361658080C2
powershell -NoProfile -ExecutionPolicy Bypass -File ..\tests\wasm-engine\check-reproducible.ps1
```

Linux CI invokes `build.py` inside the digest-pinned Emscripten image. The
Python builder reads the canonical source inventory from `build.ps1`, so native
Windows and container builds cannot silently select different NNUE backends.

The output directory contains `atomic-stockfish-nnue.js`, its `.wasm` module,
the Emscripten pthread worker, `atomic-stockfish-nnue-node.mjs`, and a checksum
manifest. **The `.mjs` launcher is the only supported Node UCI entrypoint.** The
generated `.js` glue is an implementation detail and running it directly is not
supported. The manifest records this contract and includes the launcher in its
reproducible checksummed artifact set.

The launcher is a transparent stdin/stdout proxy that supplies private
`isready` pumps while a search is active. This is necessary because Emscripten's
command-line stdin performs a blocking read that otherwise starves pthread
delivery until another line arrives. At most one private pump is outstanding,
and every real GUI `isready` is tracked separately so its `readyok` remains
visible. Integration tests exercise genuinely interactive stdin—with wall-clock
pauses during active classical, NNUE `true`, and NNUE `pure` searches. Build
products and the NNUE file are ignored and must not be committed.

The three-backend gate additionally verifies V1/V2/V3 switching, byte-exact V3
export/reimport, transactional rejection of a V2 file with trailing data,
three complete V1/V2/V3 reload cycles at `Threads=1,4,2,4`, and stable
process-tree RSS after warm-up. On Linux it polls the wrapper and runtime process
tree rather than measuring only the lightweight launcher process.

The engine uses a fixed 512 MiB initial memory and disables memory growth. It
starts four pthread workers; consumers should size their Node process and host
limits accordingly.

## Browser-worker boundary

This artifact is deliberately Node-only. It uses synchronous stdin/stdout for
UCI and `NODERAWFS` to load a 47.7 MB external network without copying or
embedding it. A browser worker cannot provide either facility: it needs a
message-to-UCI adapter and an explicit network byte loader into Emscripten's
virtual filesystem (or a C++ memory entry point). It also needs COOP/COEP headers
for `SharedArrayBuffer` because the search engine uses pthreads. The lightweight
Board WASM target is separate; silently bundling the network into that artifact
would duplicate it and break the external SHA-pinned release contract.
