// Atomic-Stockfish browser Web Worker.
//
// Start it as a module worker, only when the user selects Atomic-Stockfish:
//
//   const worker = new Worker(new URL('./atomic-stockfish.worker.js', import.meta.url),
//                             { type: 'module' });
//
// Protocol (see tests/wasm-browser/README.md for the full contract):
//
//   requests                                   responses
//   { type: 'initialize', networkUrl,          { type: 'downloadProgress', loaded, total }
//     expectedSha256, threads?, hashMiB?,      { type: 'ready', ... }
//     multiPv? }                               { type: 'search', searchId, command }
//   { type: 'command', command }               { type: 'line', line, searchId? }
//   { type: 'stop', searchId }                 { type: 'error', message, code, fatal }
//   { type: 'dispose' }                        { type: 'disposed' }
//
// Nothing is downloaded before 'initialize'. The engine runtime is imported
// lazily, and the network is fetched from the caller's URL, verified against
// the caller's SHA-256 and only then written to the Emscripten filesystem.
//
// Atomic-Stockfish is free software under the GNU GPLv3; see Copying.txt.

const PTHREAD_POOL_SIZE = @PTHREAD_POOL_SIZE@;
const MAX_HASH_MIB = @MAX_HASH_MIB@;
const MAX_NETWORK_BYTES = @MAX_NETWORK_BYTES@;
const MAX_MULTIPV = 256;
const NETWORK_DIRECTORY = '/nnue';
const INTERNAL_TIMEOUT_MS = 120000;
const PROGRESS_INTERVAL_MS = 50;

// Smallest module using a 128-bit SIMD instruction (wasm-feature-detect).
const SIMD_PROBE = new Uint8Array([
  0, 97, 115, 109, 1, 0, 0, 0, 1, 5, 1, 96, 0, 1, 123, 3, 2, 1, 0, 10, 10, 1, 8, 0, 65, 0, 253,
  15, 253, 98, 11,
]);

let state = 'new'; // new | initializing | ready | failed | disposing | disposed
let engine = null; // the Emscripten module
let engineAlive = false; // false once 'quit' destroyed the C++ engine
let disposeRequested = false;
let engineInfo = null;
let collector = null; // pending internal command
let active = null; // { id, stopSent } of the search whose bestmove is pending
let idleWaiters = [];
let pumpScheduled = false;
let nextSearchId = 0;
const queue = []; // commands waiting for the active search to finish
const options = new Map(); // lower-case option name -> { name, type, min, max }

class WorkerError extends Error {
  constructor(code, message, fatal = false) {
    super(message);
    this.code = code;
    this.fatal = fatal;
  }
}

function post(message) {
  self.postMessage(message);
}

function postError(error, fallbackCode = 'internal-error') {
  post({
    type: 'error',
    code: error instanceof WorkerError ? error.code : fallbackCode,
    message: error instanceof Error ? error.message : String(error),
    fatal: error instanceof WorkerError ? error.fatal : true,
  });
}

// ---------------------------------------------------------------- engine I/O

function onEngineLine(line, stream) {
  if (collector) {
    collector.lines.push(line);
    if (collector.until && collector.until.test(line)) finishCollector(collector);
    return;
  }
  if (state === 'initializing' || state === 'new') {
    if (engineInfo === null && stream === 'stdout') engineInfo = line;
    return;
  }

  const message = { type: 'line', line };
  if (stream === 'stderr') message.stream = 'stderr';
  // A readyok answers isready, not the search, so it is never tagged.
  if (active && line !== 'readyok') message.searchId = active.id;
  post(message);

  if (active && line.startsWith('bestmove')) {
    active = null;
    for (const resolve of idleWaiters.splice(0)) resolve();
    schedulePump();
  }
}

function callEngine(command) {
  if (!engine || !engineAlive) throw new WorkerError('engine-unavailable', 'the engine is not running');
  try {
    engine.ccall('atomic_uci', null, ['string'], [command]);
  } catch (error) {
    fail(new WorkerError('engine-abort', `the engine stopped: ${error?.message ?? error}`, true));
    throw error;
  }
  if (command === 'quit') engineAlive = false;
}

function finishCollector(c) {
  if (c.done) return;
  c.done = true;
  clearTimeout(c.timer);
  if (collector === c) collector = null;
  c.resolve(c.lines);
}

// Run a command whose output belongs to the worker, not to the caller.
function internal(command, until = null, timeoutMs = INTERNAL_TIMEOUT_MS) {
  return new Promise((resolve, reject) => {
    const c = { until, lines: [], resolve, done: false };
    c.timer = setTimeout(() => {
      if (c.done) return;
      c.done = true;
      if (collector === c) collector = null;
      reject(new WorkerError('engine-timeout', `the engine did not answer '${command}'`, true));
    }, timeoutMs);
    collector = c;
    try {
      callEngine(command);
    } catch (error) {
      c.done = true;
      clearTimeout(c.timer);
      collector = null;
      reject(error);
      return;
    }
    if (!until) finishCollector(c);
  });
}

function whenIdle(timeoutMs) {
  if (!active) return Promise.resolve(true);
  return new Promise((resolve) => {
    const timer = setTimeout(() => resolve(false), timeoutMs);
    idleWaiters.push(() => {
      clearTimeout(timer);
      resolve(true);
    });
  });
}

function fail(error) {
  if (state === 'failed' || state === 'disposed') return;
  state = 'failed';
  queue.length = 0;
  active = null;
  for (const resolve of idleWaiters.splice(0)) resolve();
  postError(error);
}

// ------------------------------------------------------------ option checks

function parseOptionLine(line) {
  const match = /^option name (.+?) type (\S+)(.*)$/.exec(line);
  if (!match) return;
  const [, name, type, rest] = match;
  const option = { name, type };
  if (type === 'spin') {
    const min = /\bmin (-?\d+)/.exec(rest);
    const max = /\bmax (-?\d+)/.exec(rest);
    option.min = min ? Number(min[1]) : -Infinity;
    option.max = max ? Number(max[1]) : Infinity;
  }
  options.set(name.toLowerCase(), option);
}

function parseSetoption(command) {
  const words = command.split(/\s+/);
  const nameAt = words.indexOf('name');
  if (nameAt !== 1) return null;
  const valueAt = words.indexOf('value', 2);
  const name = words.slice(2, valueAt === -1 ? undefined : valueAt).join(' ');
  const value = valueAt === -1 ? null : words.slice(valueAt + 1).join(' ');
  return { name, value };
}

// Reject setoption values the engine would not survive (a non-numeric spin
// value aborts the runtime) or that cannot fit the fixed browser memory.
function checkSetoption(command) {
  const parsed = parseSetoption(command);
  if (!parsed || !parsed.name) return; // the engine answers malformed lines itself
  const key = parsed.name.toLowerCase();
  if (key === 'evalfile')
    throw new WorkerError(
      'invalid-command',
      'EvalFile is managed by initialize; start a new worker to change the network',
    );
  const option = options.get(key);
  if (!option || option.type !== 'spin') return;
  if (parsed.value === null || !/^-?\d+$/.test(parsed.value))
    throw new WorkerError('invalid-command', `${option.name} needs an integer value`);
  const value = Number(parsed.value);
  let max = option.max;
  if (key === 'threads') max = Math.min(max, PTHREAD_POOL_SIZE);
  if (key === 'hash') max = Math.min(max, MAX_HASH_MIB);
  if (key === 'multipv') max = Math.min(max, MAX_MULTIPV);
  if (value < option.min || value > max)
    throw new WorkerError(
      'invalid-command',
      `${option.name} must be between ${option.min} and ${max} in the browser build`,
    );
}

// ---------------------------------------------------------- command handling

function isSearch(command) {
  return /^go(\s|$)/.test(command) && !/^go\s+perft(\s|$)/.test(command);
}

function dispatch(entry) {
  if (entry.searchId !== undefined) {
    active = { id: entry.searchId, stopSent: false };
    post({ type: 'search', searchId: entry.searchId, command: entry.command });
    callEngine(entry.command);
    if (active && active.id === entry.searchId && entry.stopRequested) stopActive();
  } else {
    callEngine(entry.command);
  }
}

function pump() {
  pumpScheduled = false;
  try {
    while (state === 'ready' && !active && queue.length) dispatch(queue.shift());
  } catch (error) {
    if (state === 'ready') postError(error);
  }
}

function schedulePump() {
  if (pumpScheduled || !queue.length) return;
  pumpScheduled = true;
  queueMicrotask(pump);
}

function stopActive() {
  if (!active || active.stopSent) return;
  active.stopSent = true;
  callEngine('stop');
}

function handleCommand(text) {
  if (typeof text !== 'string')
    throw new WorkerError('invalid-request', 'command must be a string');
  for (const raw of text.split(/\r?\n/)) {
    const command = raw.trim();
    if (!command) continue;
    const token = command.split(/\s+/, 1)[0];

    if (token === 'quit') {
      void dispose();
      return;
    }
    if (state !== 'ready')
      throw new WorkerError('not-ready', `the engine is not ready (state: ${state})`);

    if (token === 'stop') {
      stopActive();
      continue;
    }
    if (token === 'ponderhit') {
      if (active) callEngine(command);
      continue;
    }
    if (token === 'isready' && !queue.length) {
      // readyok is answered at once, also during a search.
      callEngine(command);
      continue;
    }
    if (token === 'setoption') checkSetoption(command);

    const entry = { command };
    if (isSearch(command)) entry.searchId = ++nextSearchId;

    // Every other command changes or reads engine state, so it waits for the
    // active search, which it ends: the caller has moved on.
    if (active && token !== 'isready') stopActive();
    queue.push(entry);
    if (!active) pump();
  }
}

function handleStop(searchId) {
  if (state !== 'ready') return;
  if (searchId === undefined || searchId === null) {
    stopActive();
    return;
  }
  if (active && active.id === searchId) {
    stopActive();
    return;
  }
  const queued = queue.find((entry) => entry.searchId === searchId);
  if (queued) queued.stopRequested = true;
  // Otherwise the search already ended: a stale stop is ignored.
}

// ------------------------------------------------------------ initialization

function checkEnvironment() {
  if (self.crossOriginIsolated !== true || typeof SharedArrayBuffer === 'undefined')
    throw new WorkerError(
      'unsupported-environment',
      'Atomic-Stockfish needs a cross-origin isolated page (Cross-Origin-Opener-Policy: ' +
        'same-origin and Cross-Origin-Embedder-Policy: require-corp) with SharedArrayBuffer',
      true,
    );
  if (typeof WebAssembly !== 'object' || !WebAssembly.validate(SIMD_PROBE))
    throw new WorkerError(
      'unsupported-environment',
      'Atomic-Stockfish needs WebAssembly 128-bit SIMD',
      true,
    );
  if (!self.crypto?.subtle)
    throw new WorkerError('unsupported-environment', 'Web Crypto (crypto.subtle) is unavailable', true);
}

function integerSetting(value, name, min, max, fallback) {
  if (value === undefined || value === null) return fallback;
  if (!Number.isInteger(value) || value < min || value > max)
    throw new WorkerError('invalid-request', `${name} must be an integer from ${min} to ${max}`, true);
  return value;
}

function networkFileName(url, sha256) {
  try {
    const name = new URL(url, self.location.href).pathname.split('/').pop();
    if (name && /^[A-Za-z0-9._-]{1,128}\.nnue$/.test(name)) return name;
  } catch {
    // fall through
  }
  return `${sha256.slice(0, 12)}.nnue`;
}

function hex(buffer) {
  return Array.from(new Uint8Array(buffer), (b) => b.toString(16).padStart(2, '0')).join('');
}

async function downloadNetwork(url, expectedSha256) {
  let response;
  try {
    response = await fetch(url, { credentials: 'same-origin' });
  } catch (error) {
    throw new WorkerError('network-download', `the network download failed: ${error.message}`, true);
  }
  if (!response.ok || !response.body)
    throw new WorkerError(
      'network-download',
      `the network download failed: HTTP ${response.status}`,
      true,
    );

  const encoding = response.headers.get('content-encoding');
  const lengthHeader = response.headers.get('content-length');
  let total = null;
  if ((!encoding || encoding === 'identity') && lengthHeader && /^\d+$/.test(lengthHeader))
    total = Number(lengthHeader);
  if (total !== null && total > MAX_NETWORK_BYTES)
    throw new WorkerError('network-invalid', `the network is larger than ${MAX_NETWORK_BYTES} bytes`, true);

  post({ type: 'downloadProgress', loaded: 0, total });
  const reader = response.body.getReader();
  let buffer = total !== null ? new Uint8Array(total) : null;
  let chunks = [];
  let loaded = 0;
  let lastProgress = 0;
  for (;;) {
    if (disposeRequested) {
      await reader.cancel();
      throw new WorkerError('disposed', 'disposed during initialization', true);
    }
    const { done, value } = await reader.read();
    if (done) break;
    if (loaded + value.byteLength > MAX_NETWORK_BYTES) {
      await reader.cancel();
      throw new WorkerError('network-invalid', `the network is larger than ${MAX_NETWORK_BYTES} bytes`, true);
    }
    if (buffer && loaded + value.byteLength <= buffer.byteLength) buffer.set(value, loaded);
    else {
      if (buffer) {
        chunks.push(buffer.subarray(0, loaded));
        buffer = null;
      }
      chunks.push(value);
    }
    loaded += value.byteLength;
    const now = performance.now();
    if (now - lastProgress >= PROGRESS_INTERVAL_MS) {
      lastProgress = now;
      post({ type: 'downloadProgress', loaded, total });
    }
  }
  post({ type: 'downloadProgress', loaded, total });

  let bytes;
  if (buffer) bytes = loaded === buffer.byteLength ? buffer : buffer.subarray(0, loaded);
  else {
    bytes = new Uint8Array(loaded);
    let offset = 0;
    for (const chunk of chunks) {
      bytes.set(chunk, offset);
      offset += chunk.byteLength;
    }
  }
  buffer = null;
  chunks = null;
  if (loaded === 0) throw new WorkerError('network-invalid', 'the network file is empty', true);

  const actual = hex(await crypto.subtle.digest('SHA-256', bytes));
  if (actual !== expectedSha256)
    throw new WorkerError(
      'network-sha256',
      `the network SHA-256 is ${actual}, expected ${expectedSha256}`,
      true,
    );
  return bytes;
}

async function startEngine() {
  const { default: createAtomicStockfish } = await import('./atomic-stockfish.mjs');
  const module = await createAtomicStockfish({
    print: (line) => onEngineLine(line, 'stdout'),
    printErr: (line) => onEngineLine(line, 'stderr'),
    onAbort: (what) => fail(new WorkerError('engine-abort', `the engine aborted: ${what}`, true)),
  });
  return module;
}

async function initialize(request) {
  if (state !== 'new') throw new WorkerError('invalid-request', 'initialize may be sent only once per worker');
  state = 'initializing';
  let enginePromise = null;
  try {
    checkEnvironment();
    if (typeof request.networkUrl !== 'string' || !request.networkUrl)
      throw new WorkerError('invalid-request', 'networkUrl is required', true);
    const expectedSha256 = String(request.expectedSha256 ?? '').toLowerCase();
    if (!/^[0-9a-f]{64}$/.test(expectedSha256))
      throw new WorkerError('invalid-request', 'expectedSha256 must be 64 hexadecimal digits', true);
    const threads = integerSetting(request.threads, 'threads', 1, PTHREAD_POOL_SIZE, 1);
    const hashMiB = integerSetting(request.hashMiB, 'hashMiB', 1, MAX_HASH_MIB, 16);
    const multiPv = integerSetting(request.multiPv, 'multiPv', 1, MAX_MULTIPV, 1);

    // The runtime and the network download proceed together.
    enginePromise = startEngine();
    enginePromise.catch(() => {});
    let bytes = await downloadNetwork(request.networkUrl, expectedSha256);
    const bytesLength = bytes.byteLength;
    engine = await enginePromise;
    engineAlive = true;
    if (disposeRequested) throw new WorkerError('disposed', 'disposed during initialization', true);

    const name = networkFileName(request.networkUrl, expectedSha256);
    const path = `${NETWORK_DIRECTORY}/${name}`;
    try {
      engine.FS.mkdir(NETWORK_DIRECTORY);
    } catch {
      // already present
    }
    // canOwn hands the verified buffer to the in-memory filesystem without a copy.
    engine.FS.writeFile(path, bytes, { canOwn: true });
    bytes = null;

    const uciLines = await internal('uci', /^uciok$/);
    for (const line of uciLines) parseOptionLine(line);
    await internal(`setoption name EvalFile value ${path}`);
    await internal(`setoption name Threads value ${threads}`);
    await internal(`setoption name Hash value ${hashMiB}`);
    await internal(`setoption name MultiPV value ${multiPv}`);
    await internal('isready', /^readyok$/);

    // Prove the network is usable: the engine refuses to search without a
    // compatible network and says why.
    await internal('position startpos');
    const probe = await internal('go depth 1', /^bestmove /);
    const errors = probe.filter((line) => /\bERROR\b/.test(line));
    const bestmove = probe[probe.length - 1] ?? '';
    if (errors.length || !/^bestmove [a-h][1-8][a-h][1-8]/.test(bestmove))
      throw new WorkerError(
        'network-incompatible',
        `the network was rejected by the engine${errors.length ? ': ' + errors.join(' ') : ''}`,
        true,
      );
    const description =
      probe
        .find((line) => /^info string .*NNUE/i.test(line))
        ?.replace(/^info string /, '') ?? null;
    await internal('ucinewgame');
    await internal('isready', /^readyok$/);

    // The network now lives in WASM memory; drop the filesystem copy.
    engine.FS.unlink(path);

    if (disposeRequested) throw new WorkerError('disposed', 'disposed during initialization', true);
    state = 'ready';
    post({
      type: 'ready',
      engine: engineInfo,
      network: { path, bytes: bytesLength, sha256: expectedSha256, description },
      threads,
      hashMiB,
      multiPv,
      maxThreads: PTHREAD_POOL_SIZE,
      maxHashMiB: MAX_HASH_MIB,
    });
  } catch (error) {
    if (!engine && enginePromise) {
      try {
        engine = await enginePromise;
        engineAlive = true;
      } catch {
        // the runtime failed to start
      }
    }
    if (state !== 'failed') {
      state = 'failed';
      if (!(error instanceof WorkerError && error.code === 'disposed')) postError(error);
    }
    if (engine && engineAlive) {
      try {
        collector = null;
        callEngine('quit');
      } catch {
        // already gone
      }
    }
    if (disposeRequested) await dispose();
  }
}

// ----------------------------------------------------------------- disposal

async function dispose() {
  if (state === 'disposing' || state === 'disposed') return;
  if (state === 'initializing') {
    disposeRequested = true;
    return;
  }
  state = 'disposing';
  queue.length = 0;
  if (engine && engineAlive) {
    try {
      if (active) {
        stopActive();
        await whenIdle(10000);
      }
      collector = null;
      callEngine('quit'); // joins every search thread
    } catch {
      // the runtime is gone already
    }
  }
  engine = null;
  state = 'disposed';
  post({ type: 'disposed' });
  // Closing the worker also ends every pthread worker it owns.
  self.close();
}

self.onmessage = (event) => {
  const request = event.data;
  try {
    switch (request?.type) {
      case 'initialize':
        void initialize(request).catch(postError);
        break;
      case 'command':
        handleCommand(request.command);
        break;
      case 'stop':
        handleStop(request.searchId);
        break;
      case 'dispose':
        void dispose();
        break;
      default:
        throw new WorkerError('invalid-request', `unknown request type: ${request?.type}`);
    }
  } catch (error) {
    postError(error);
  }
};
