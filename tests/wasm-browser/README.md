# Atomic-Stockfish in a browser Web Worker

This target builds the complete Atomic-Stockfish search engine (search, NNUE
and all three network readers) for a browser Web Worker, for use by any
Lichess-style Atomic analysis board. It does not contain a board or any other
UI, and it is not tied to a website.

It is a separate target from the two existing WebAssembly builds, which it
leaves unchanged:

| Target | Build | Contents | Runs in |
|---|---|---|---|
| Board WASM | `src/Makefile_js` | rules and board API, no search | Node and browsers |
| Node UCI/NNUE WASM | `tests/wasm-engine/build.py` | complete engine, stdin/stdout | Node only |
| **Browser worker** | `tests/wasm-browser/build.py` | complete engine, postMessage | Chromium, Firefox |

The browser build compiles the same source inventory as the Node build (read
from `tests/wasm-engine/build.ps1`), with `src/main.cpp` replaced by
`src/wasm/browser_main.cpp`. The network is never embedded.

## Build

Emscripten **4.0.10** is required; it is the release of the digest-pinned
`emscripten/emsdk:4.0.10` image the other WASM targets use in CI. The builder
refuses another version unless `--allow-other-emscripten` is given.

With the image (as CI does):

```sh
docker run --rm --volume "$PWD:/src" --workdir /src \
  emscripten/emsdk:4.0.10@sha256:90b757eb11fa9a0e3ce4d2d9f76d932a56018e4accc37b5a28b2783751e60eb7 \
  python3 tests/wasm-browser/build.py --out-dir build/wasm-browser
```

With a local emsdk:

```sh
git clone https://github.com/emscripten-core/emsdk && cd emsdk
./emsdk install 4.0.10 && ./emsdk activate 4.0.10 && source ./emsdk_env.sh
cd <Atomic-Stockfish> && python3 tests/wasm-browser/build.py --out-dir build/wasm-browser
```

The build sets `SOURCE_DATE_EPOCH` (default 0) and maps source paths, like the
Node build. A development build embeds the commit (`GIT_SHA`) in the engine's
id string, so artifact hashes change with the commit; a release build sets
`ATOMIC_VERSION=MAJOR.MINOR.PATCH` instead.

### Artifacts

| File | What it is |
|---|---|
| `atomic-stockfish.worker.js` | The public entry point: the Web Worker that implements the protocol below. |
| `atomic-stockfish.mjs` | Emscripten ES-module runtime (`createAtomicStockfish`). The worker imports it on `initialize`; it is also the script of every pthread worker. |
| `atomic-stockfish.wasm` | The engine. |
| `manifest.json` | Source commit, Emscripten and clang versions, complete compile and link flags, source list, memory and thread settings, entry points, supported network formats, and the size, gzip size and SHA-256 of each artifact. |

Build products and networks are not committed. The manifest of a build is the
authority for its checksums. For reference, the build of commit `03724fee`
with Emscripten 4.0.10 produced:

| File | Bytes | gzip -9 | SHA-256 |
|---|---|---|---|
| `atomic-stockfish.mjs` | 76,295 | 21,221 | `5298e1b38e7f08c9b09a2ff1c05feb3a576ddf9580036b1ede2b151efbd52f5a` |
| `atomic-stockfish.wasm` | 625,941 | 227,702 | `7180c6168fc8cc1ce4eb94ba69bd9e0eab9d899e0f593e9678075ffed49e59d5` |
| `atomic-stockfish.worker.js` | 19,817 | 6,137 | `b25e996326946f195d343026e1827b5573144b65be590fbf25a5228ef5236cc9` |

### Flags

Compile: `-std=c++17 -fno-exceptions -pthread -msimd128 -msse2 -msse3 -mssse3
-msse4.1 -DNO_TABLEBASES -DNO_PREFETCH -DNNUE_EMBEDDING_OFF -DUSE_POPCNT
-DUSE_PTHREADS -DUSE_SSE2 -DUSE_SSSE3 -DUSE_SSE41 -DUSE_SLOPPY_ATOMICS
-DATOMIC_BROWSER_WORKER -O3 -flto -DNDEBUG`.

Link: `-sENVIRONMENT=worker -sMODULARIZE=1 -sEXPORT_ES6=1
-sEXPORT_NAME=createAtomicStockfish -sPTHREAD_POOL_SIZE=4
-sPTHREAD_POOL_SIZE_STRICT=2 -sINITIAL_MEMORY=536870912 -sALLOW_MEMORY_GROWTH=0
-sSTACK_SIZE=8388608 -sFORCE_FILESYSTEM=1 -sNO_EXIT_RUNTIME=1
-sEXPORTED_FUNCTIONS=['_main','_atomic_uci','_malloc','_free']
-sEXPORTED_RUNTIME_METHODS=['FS','ccall'] -sWASM_BIGINT=1 -sASSERTIONS=0`.

Differences from the Node build, all deliberate:

- `-fno-exceptions`, as in the native build. The engine has no `try`/`catch`;
  Emscripten's JavaScript exception support would route calls through
  JavaScript trampolines. The worker validates the option values that would
  otherwise make the C++ library abort (see `setoption` below).
- No `-sDETERMINISTIC`: it replaces the clock with a call counter, which breaks
  `go movetime` and every clock-based time control.
- `-DATOMIC_BROWSER_WORKER` makes `now()` (`src/misc.h`) read the wall clock.
  Each search thread is a separate Web Worker with its own monotonic time
  origin; measured in Chromium and Firefox they disagree by hundreds of
  milliseconds, which made `go movetime 500` stop after 10-15 ms.
- `_main` is exported: with an explicit export list Emscripten otherwise drops
  `main()` and the linker drops the whole engine with it.
- `-sPTHREAD_POOL_SIZE_STRICT=2`: a thread beyond the pool fails at once
  instead of waiting forever for a worker that the busy engine thread could
  never start. The worker never asks for more than the pool (Threads <= 4).

## Hosting

The page and every engine asset must be served with

```
Cross-Origin-Opener-Policy: same-origin
Cross-Origin-Embedder-Policy: require-corp
```

(and assets from another origin need `Cross-Origin-Resource-Policy` or CORS).
The engine's search threads need `SharedArrayBuffer`. `initialize` fails with
`unsupported-environment` unless `crossOriginIsolated === true`,
`SharedArrayBuffer` exists, WebAssembly SIMD is available and Web Crypto
(`crypto.subtle`, a secure context) is present.

Serve `.wasm` as `application/wasm` and `.mjs`/`.js` as `text/javascript`.
`node tests/wasm-browser/serve.mjs --engine build/wasm-browser --nets <dir>`
serves a build with the right headers for local experiments.

## Browser support

Tested by the automated suite in Chromium 156 (Chrome for Testing) and
Firefox 157, headless, on Windows 10. Requirements and limits:

- Module workers (`new Worker(url, { type: 'module' })`): Chromium 80+,
  Firefox 114+. Nested workers (the pthreads are created by the engine worker):
  Chromium 69+, all supported Firefox versions.
- WebAssembly SIMD (Chromium 91+, Firefox 89+), shared memory and bulk memory.
- `performance.measureUserAgentSpecificMemory()` exists only in Chromium; the
  memory-growth check of the dispose test is skipped in Firefox.
- Safari is not tested.

## Worker protocol

```ts
type AtomicStockfishRequest =
  | { type: 'initialize'; networkUrl: string; expectedSha256: string;
      threads?: number; hashMiB?: number; multiPv?: number }
  | { type: 'command'; command: string }
  | { type: 'stop'; searchId: number }
  | { type: 'dispose' };

type AtomicStockfishResponse =
  | { type: 'downloadProgress'; loaded: number; total: number | null }
  | { type: 'ready'; engine: string;
      network: { path: string; bytes: number; sha256: string; description: string | null };
      threads: number; hashMiB: number; multiPv: number;
      maxThreads: number; maxHashMiB: number }
  | { type: 'search'; searchId: number; command: string }   // extension
  | { type: 'line'; line: string; searchId?: number; stream?: 'stderr' }
  | { type: 'error'; message: string; code: string; fatal: boolean }
  | { type: 'disposed' };                                    // extension
```

`search` and `disposed`, and the extra fields of `ready` and `error`, extend the
contract in the specification; an adapter may ignore them.

### initialize

Sent once per worker. It validates the request (`threads` 1-4, default 1;
`hashMiB` 1-128, default 16; `multiPv` 1-256, default 1), checks the
environment, then in parallel imports the engine runtime and downloads the
network. Internally it runs

```
uci
setoption name EvalFile value /nnue/<file>
setoption name Threads value <threads>
setoption name Hash value <hashMiB>
setoption name MultiPV value <multiPv>
isready
position startpos
go depth 1          (proves the network loads; see below)
ucinewgame
isready
```

and answers `ready`. The output of these commands is not forwarded. On any
failure it answers `error` with `fatal: true`; the worker then accepts only
`dispose`.

### command

Any UCI command line (several may be separated by newlines). Output arrives
as `line` messages, one per engine output line, exactly as the native engine
prints it.

- `go` (except `go perft`) starts a search. The worker numbers every search
  request in arrival order, starting at 1, sends `{ type: 'search', searchId }`
  when it hands the command to the engine, and tags every output line of that
  search, including its `bestmove`, with the `searchId`.
- `stop` and `ponderhit` act on the running search at once.
- `isready` is answered at once, also during a search (`readyok` is never
  tagged), unless earlier commands are still queued: then it waits behind them.
- Every other command (`position`, `setoption`, `ucinewgame`, another `go`,
  `uci`, `d`, `eval`, `go perft`, ...) changes or reads engine state. If a
  search is running, the worker first stops it and holds the command until that
  search's `bestmove` has been delivered. Commands keep their order.
- `quit` disposes of the worker.
- `setoption` is checked before it reaches the engine: `EvalFile` cannot be
  changed (start a new worker for another network), spin options need an
  integer inside the engine's range, `Threads` at most 4, `Hash` at most 128.
  A rejected line answers `error` with `code: 'invalid-command'` and
  `fatal: false`; the engine never sees it.

### stop

`{ type: 'stop', searchId }` stops that search if it is running. If the search
is still queued it starts and stops at once (it still delivers one `bestmove`).
A stop for a search that already ended is ignored, so a late stop can never
end a newer search. Without a `searchId` it stops whatever runs.

### dispose

Stops a running search, waits up to 10 s for its `bestmove`, destroys the
engine (`quit` joins every search thread), answers `disposed` and closes the
worker, which ends the pthread workers it owns. Call `worker.terminate()`
afterwards as well; it is harmless and also covers a worker that hung.

### Errors

| code | fatal | meaning |
|---|---|---|
| `unsupported-environment` | yes | not cross-origin isolated, no SharedArrayBuffer, SIMD or Web Crypto |
| `invalid-request` | yes for initialize | malformed request or initialize parameter |
| `network-download` | yes | fetch failed or HTTP error (e.g. 404) |
| `network-sha256` | yes | the downloaded bytes do not have `expectedSha256` |
| `network-invalid` | yes | empty, or larger than 128 MiB |
| `network-incompatible` | yes | the hash matched but the engine cannot use the file (corrupt, truncated, unknown format) |
| `engine-abort` | yes | the WebAssembly runtime aborted (e.g. out of memory) |
| `engine-timeout` | yes | an internal command got no answer |
| `not-ready` | no | a command arrived before `ready` or after a fatal error |
| `invalid-command` | no | a rejected `setoption` |

## NNUE loading and verification

Nothing is downloaded before `initialize`: creating the worker fetches only
`atomic-stockfish.worker.js` (about 20 kB). On `initialize` the worker

1. fetches `networkUrl` (same-origin credentials), posting `downloadProgress`
   at most every 50 ms; `total` is the `Content-Length`, or `null` when the
   response is compressed or has no length;
2. computes the SHA-256 with Web Crypto and compares it with `expectedSha256`;
3. writes the verified bytes to `/nnue/<file name>` in Emscripten's in-memory
   filesystem, handing the buffer over without a copy (`canOwn`);
4. sets `EvalFile` to that path; the engine reads it into WASM memory;
5. runs a depth-1 search: the engine refuses to search with a missing,
   corrupt, truncated or incompatible network and says why, which the worker
   reports as `network-incompatible`;
6. deletes the file. After that the network exists once, inside WASM memory,
   and the worker holds no JavaScript copy.

The reference network is the engine's default, `atomic-ft5-0p8B.nnue`
(47,721,376 bytes, SHA-256
`c8e590d82efc3d29dfeba37323efd5fb094744e3561a07d58d9780c5c05db807`).
Legacy Atomic V1 (`0x7AF32F20`) and AtomicNNUEV2 (`0xA70C0002`) networks are
accepted. Serve the network with long-lived cache
headers; the worker does not cache it itself.

## Position and search lifecycle

The analysis-board flow, all through `command` messages:

```
position fen <fen> moves <moves>     (or position startpos moves ...)
go infinite                          -> search N, info lines tagged N
... the user moves or navigates ...
position fen <new fen> ...           -> the worker stops N, waits for its bestmove
go infinite                          -> search N+1 starts only after bestmove N
```

An adapter may also send `{ type: 'stop', searchId: N }` itself before the new
position; the result is the same. Output of search N never arrives after the
`search` message of N+1, and every search delivers exactly one `bestmove`.

### Stale-result handling

The adapter keeps the `searchId` of the search that belongs to the position on
screen and drops every `line` with another `searchId`. Because ids are
assigned in request order, the adapter knows the id of a search when it sends
`go`: one more than the previous one.

### MultiPV

With `MultiPV k` every completed iteration prints k lines, `multipv 1` to
`multipv k`, best first, each with its own score and PV. Lines with
`lowerbound`/`upperbound` are aspiration-window results; most boards skip them.
A search stopped before its first iteration may print `info depth 1 ... pv`
with an empty PV, as the native engine does. To change the number of lines,
send `setoption name MultiPV value k` and a new `go`; the worker ends the
running search first.

### Minimal Lichess-style adapter

```js
export class AtomicStockfishAdapter {
  constructor({ workerUrl, networkUrl, expectedSha256, onProgress, onInfo, onBestMove, onError }) {
    this.worker = new Worker(workerUrl, { type: 'module' }); // only when the user selects the engine
    this.nextSearchId = 0;
    this.current = 0;
    this.ready = new Promise((resolve, reject) => {
      this.worker.onmessage = ({ data: m }) => {
        if (m.type === 'downloadProgress') onProgress?.(m.loaded, m.total);
        else if (m.type === 'ready') resolve(m);
        else if (m.type === 'error') (m.fatal ? reject(new Error(m.message)) : onError?.(m));
        else if (m.type === 'line' && m.searchId === this.current) {
          if (m.line.startsWith('info ') && / pv /.test(m.line)) onInfo?.(parseInfo(m.line));
          else if (m.line.startsWith('bestmove ')) onBestMove?.(m.line.split(' ')[1]);
        }
      };
    });
    this.worker.postMessage({ type: 'initialize', networkUrl, expectedSha256, threads: 4, hashMiB: 64, multiPv: 3 });
  }

  analyse(fen, moves = [], { multiPv } = {}) {
    if (multiPv) this.send(`setoption name MultiPV value ${multiPv}`);
    this.send(`position fen ${fen}${moves.length ? ' moves ' + moves.join(' ') : ''}`);
    this.send('go infinite');
    this.current = ++this.nextSearchId; // earlier searches are now stale
  }

  stop() {
    this.worker.postMessage({ type: 'stop', searchId: this.current });
  }

  send(command) {
    this.worker.postMessage({ type: 'command', command });
  }

  dispose() {
    this.worker.postMessage({ type: 'dispose' });
    setTimeout(() => this.worker.terminate(), 2000);
  }
}

function parseInfo(line) {
  const w = line.split(' ');
  const info = {};
  for (let i = 1; i < w.length; i++) {
    if (['depth', 'seldepth', 'multipv', 'nodes', 'nps', 'time'].includes(w[i])) info[w[i]] = +w[++i];
    else if (w[i] === 'score') info.score = { [w[++i]]: +w[++i] };
    else if (w[i] === 'lowerbound' || w[i] === 'upperbound') info.bound = w[i];
    else if (w[i] === 'pv') { info.pv = w.slice(i + 1).filter(Boolean); break; }
  }
  return info;
}
```

Atomic960 works as in the native engine: `setoption name UCI_Chess960 value
true`, then X-FEN or Shredder-FEN castling rights; castling moves are printed
as king-takes-rook.

### Lazy loading

Create the worker only when the user selects Atomic-Stockfish. Until
`initialize` the browser has fetched only the worker script; the runtime
(`.mjs`, `.wasm`) and the network are requested by `initialize`, in parallel.
The automated suite checks that no `.mjs`, `.wasm` or `.nnue` request happens
before it.

## Costs

The engine is not lightweight. What a user pays when they enable it:

| | |
|---|---|
| Engine download | 722 kB (255 kB gzip, 201 kB brotli): worker script, runtime and WASM |
| Network download | 47.7 MB (`atomic-ft5-0p8B.nnue`); 26.5 MB with gzip, 22.6 MB with brotli if the server compresses it |
| WASM memory | 512 MiB shared memory reserved up front, fixed (no growth); the network, the hash, four 8 MiB thread stacks and the per-thread search data live in it |
| Workers | 1 engine worker + 4 pthread workers, all started on `initialize` |
| Main thread | nothing runs on it; the board only exchanges messages |

Physical memory grows with what is touched: the measured peak of the whole
browser process tree during the benchmark is in the benchmark report. Hash is
limited to 128 MiB: 256 MiB fits a fresh engine but failed with an
out-of-memory abort after several `Threads` changes; 192 MiB was the largest
value that survived them.

## Tests

```sh
cd tests/wasm-browser
npm ci
npx playwright install chromium firefox
python3 -m pip install -r requirements.txt
node run-browser-tests.mjs --engine ../../build/wasm-browser --net <atomic-ft5-0p8B.nnue>
```

CI runs the same suite with the synthetic Legacy Atomic V1 network
(`--net <atomic-zero.nnue> --net-sha256 <hash>`). The suite drives the worker
from a real page in Chromium and Firefox through the protocol above and
covers: rejection of non-isolated pages; no download before `initialize`;
initialization with progress and SHA-256 verification (with and without
`Content-Length`); rejection of a wrong hash, a missing file, malformed
requests, and corrupt, truncated and incompatible networks; AtomicNNUEV2
networks; `uci`, `isready`, `xboard`; `position startpos` and
`position fen`; Atomic and Atomic960 positions; `go depth`, `go movetime`,
`go infinite` with `isready` during the search and `stop`; MultiPV 1-5; 40
rapid position/stop/go/setoption changes with stale-output and ordering
checks; Threads 1/4/2/4/3, the largest Hash, and rejected `setoption` values;
eight Atomic and Atomic960 perft vectors; commands before `ready`; four
initialize/dispose cycles (disposing during an infinite search) with a memory
growth check in Chromium; and that no worker survives. Every principal
variation the engine printed is then replayed by an independent Atomic
implementation (python-chess, `pv_oracle.py`).

## Benchmark

`run-browser-bench.mjs` compares the worker with the Fairy-Stockfish 14
browser engine (npm `fairy-stockfish-nnue.wasm`, pinned in `package.json`) in
Chromium and Firefox: six fixed Atomic positions, `go depth 15`, MultiPV 3,
Threads 4, Hash 64 MiB, `ucinewgame` before each position, three runs per
engine in alternating order, and a fresh page per run so that cold
initialization is measured apart from the warm analysis. It records the time
to the first evaluation and to each depth, the time to complete depth 15,
nodes and NPS per position, cold initialization, artifact and network sizes
(raw, gzip, brotli), the WASM heap, the browser's peak memory, worker counts,
main-thread timer lateness during analysis, and the primary moves. Results of
the last run are in `results/`.

Last run (`results/report.md`, Ryzen 9 5950X, Windows 10, commit 03724fee):
median time to complete depth 15 was 240 ms (Chromium) and 253 ms (Firefox)
for Atomic-Stockfish against 1.9 s and 2.3 s for Fairy-Stockfish 14, at about
4.5 M nodes/s against 2.0-2.4 M. Cold initialization from a local server took
440-470 ms (Fairy-Stockfish 710-770 ms). The page's main thread stayed
responsive (10 ms timer p99 lateness 1.2-1.3 ms) with either engine. Peak
memory of the whole browser process tree was 0.95 GB (Chromium) and 1.14 GB
(Firefox). Fairy-Stockfish ran with its classical evaluation: the only local
Fairy-Stockfish Atomic network is a HalfKP network of an older release, which
Fairy-Stockfish 14 rejects, so the comparison is engine against engine, not
network against network.

## License

Atomic-Stockfish is free software under the GNU General Public License v3
(`Copying.txt`). The three artifacts are the engine in binary form. Whoever
serves them to users distributes the program and must, under GPLv3, make the
Corresponding Source available to those users (the commit recorded in
`manifest.json` together with these build scripts), keep the license and
copyright notices (`Copying.txt`, `AUTHORS`) available, and pass the same
rights on; modified builds must be released under GPLv3 too. The network file
is a separate download; check its own terms before redistributing it.
