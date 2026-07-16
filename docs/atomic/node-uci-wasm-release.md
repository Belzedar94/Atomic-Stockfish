# Atomic-Stockfish 1.0.0 Node UCI/NNUE WebAssembly

This archive contains the complete Atomic search engine for Node.js. It is a
UCI-only, pthread-enabled WebAssembly build with both the Legacy Atomic V1 and
AtomicNNUEV2 network readers. It does not embed or redistribute an NNUE network.

Run all commands from the extracted archive directory. The supported entrypoint
is `atomic-stockfish-nnue-node.mjs`; the generated
`atomic-stockfish-nnue.js` file is runtime glue and is not a supported direct
entrypoint.

On Linux or macOS, start an interactive UCI session with:

```sh
node ./atomic-stockfish-nnue-node.mjs
```

On Windows PowerShell, use:

```powershell
node .\atomic-stockfish-nnue-node.mjs
```

Enter `uci`, then `isready`, to inspect the engine and its options. A minimal
classical-evaluation smoke test is:

```text
setoption name Use NNUE value false
position startpos
go perft 1
```

To use NNUE, set `EvalFile` to an external `.nnue` file and enable `Use NNUE`.
Authenticate that external file against the SHA-256 published by its provider;
the network is intentionally not present in this archive. `Use NNUE=pure` is a
data-generation mode, not a playing recommendation.

The archive also contains `manifest.json`, `atomic-stockfish-nnue.wasm`, any
generated pthread worker required by the runtime, `AUTHORS`, `CITATION.cff`,
and `Copying.txt`. `manifest.json` records the exact runtime filenames,
checksums, supported entrypoint, memory model, and network backends. The build
uses a fixed 512 MiB initial memory and four pthread workers, so the Node process
and host limits must provide sufficient memory and threads.

This artifact is Node-only. It relies on synchronous stdin/stdout and Node's
filesystem support for external networks. The separate Board WASM npm package
provides a lightweight rules API for CommonJS and ES modules; it is not this
UCI engine.
