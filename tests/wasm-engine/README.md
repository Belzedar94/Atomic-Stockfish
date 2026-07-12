# Atomic-Stockfish Node UCI/NNUE WebAssembly

This target builds the complete Atomic search engine, including the legacy
Atomic NNUE reader, as a pthread-enabled Emscripten command-line program. The
network remains external and is read at runtime through Node's filesystem. The
test runner rejects any network whose SHA-256 is not
`99dc67eabf26a64faeeca3a88b4c38597a840b8d4a874b9f2cf658c6f92a04a6`.

From `src`, with Emscripten installed at `C:\emsdk\emsdk`:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File ..\tests\wasm-engine\build.ps1
node ..\tests\wasm-engine\run-engine-tests.mjs --engine ..\build\wasm-engine\atomic-stockfish-nnue-node.mjs --net ..\atomic_run3b_e202_l05.nnue
powershell -NoProfile -ExecutionPolicy Bypass -File ..\tests\wasm-engine\check-reproducible.ps1
```

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
