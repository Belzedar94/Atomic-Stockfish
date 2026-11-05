// Browser-side benchmark of one engine in one page, as an analysis board sees
// it. run-browser-bench.mjs opens a fresh page per (engine, run), calls
// window.benchRun(), and saves what it returns.

import { EngineClient, parseInfo } from './client.mjs';

// Fairy-Stockfish 14 browser engine (npm fairy-stockfish-nnue.wasm), driven
// the way its package documents: Stockfish() on the page, commands through
// postMessage and output through addMessageListener.
class FairyEngine {
  static loadScript() {
    if (window.Stockfish) return Promise.resolve();
    return new Promise((resolve, reject) => {
      const script = document.createElement('script');
      script.src = '/fsf/stockfish.js';
      script.onload = resolve;
      script.onerror = () => reject(new Error('cannot load /fsf/stockfish.js'));
      document.head.append(script);
    });
  }

  constructor() {
    this.listeners = new Set();
  }

  async initialize({ networkUrl, threads, hashMiB, multiPv }) {
    await FairyEngine.loadScript();
    this.sf = await window.Stockfish();
    this.sf.addMessageListener((line) => {
      for (const listener of [...this.listeners]) listener(line, performance.now());
    });
    await this.until('uci', /^uciok$/);
    this.sf.postMessage('setoption name UCI_Variant value atomic');
    let networkBytes = 0;
    if (networkUrl) {
      const response = await fetch(networkUrl);
      const bytes = new Uint8Array(await response.arrayBuffer());
      const name = '/' + networkUrl.split('/').pop();
      this.sf.FS.writeFile(name, bytes);
      this.sf.postMessage(`setoption name EvalFile value ${name}`);
      networkBytes = bytes.byteLength;
    } else {
      // No Fairy-Stockfish 14 (HalfKAv2) Atomic network: classical evaluation.
      this.sf.postMessage('setoption name Use NNUE value false');
    }
    this.sf.postMessage(`setoption name Threads value ${threads}`);
    this.sf.postMessage(`setoption name Hash value ${hashMiB}`);
    this.sf.postMessage(`setoption name MultiPV value ${multiPv}`);
    await this.until('isready', /^readyok$/);
    return { networkBytes };
  }

  send(command) {
    this.sf.postMessage(command);
  }

  until(command, pattern) {
    return new Promise((resolve) => {
      const lines = [];
      const listener = (line) => {
        lines.push(line);
        if (pattern.test(line)) {
          this.listeners.delete(listener);
          resolve(lines);
        }
      };
      this.listeners.add(listener);
      this.sf.postMessage(command);
    });
  }

  // Calls onLine(line, at) for every line of the search; resolves at bestmove.
  search(position, go, onLine) {
    return new Promise((resolve) => {
      const listener = (line, at) => {
        onLine(line, at);
        if (line.startsWith('bestmove')) {
          this.listeners.delete(listener);
          resolve();
        }
      };
      this.sf.postMessage(position);
      this.listeners.add(listener);
      this.sf.postMessage(go);
    });
  }

  heapBytes() {
    return this.sf?.wasmMemory?.buffer?.byteLength ?? this.sf?.HEAPU8?.byteLength ?? null;
  }
}

class AtomicEngine {
  async initialize({ networkUrl, networkSha256, threads, hashMiB, multiPv }) {
    this.client = new EngineClient('/engine/atomic-stockfish.worker.js');
    const ready = await this.client.initialize({
      networkUrl,
      expectedSha256: networkSha256,
      threads,
      hashMiB,
      multiPv,
    });
    return { networkBytes: ready.network.bytes };
  }

  send(command) {
    this.client.command(command);
  }

  async until(command, pattern) {
    this.client.command(command);
    await this.client.nextLine(pattern, 60000);
  }

  search(position, go, onLine) {
    return new Promise((resolve) => {
      let searchId = null;
      const listener = (event) => {
        const m = event.data;
        if (m.type === 'search' && searchId === null) searchId = m.searchId;
        if (m.type !== 'line' || m.searchId !== searchId) return;
        onLine(m.line, performance.now());
        if (m.line.startsWith('bestmove')) {
          this.client.worker.removeEventListener('message', listener);
          resolve();
        }
      };
      this.client.worker.addEventListener('message', listener);
      this.client.command(position);
      this.client.command(go);
    });
  }

  heapBytes() {
    return 536870912; // fixed: INITIAL_MEMORY, no growth (manifest.json)
  }
}

// Main-thread responsiveness: lateness of a 10 ms timer while the engine works.
function startJankProbe() {
  const lateness = [];
  let expected = performance.now() + 10;
  const timer = setInterval(() => {
    const now = performance.now();
    lateness.push(Math.max(0, now - expected));
    expected = now + 10;
  }, 10);
  return () => {
    clearInterval(timer);
    lateness.sort((a, b) => a - b);
    const at = (q) => lateness[Math.min(lateness.length - 1, Math.floor(q * lateness.length))] ?? 0;
    return {
      samples: lateness.length,
      maxMs: lateness[lateness.length - 1] ?? 0,
      p99Ms: at(0.99),
      p50Ms: at(0.5),
    };
  };
}

async function measureMemory() {
  if (!performance.measureUserAgentSpecificMemory) return null;
  try {
    const result = await Promise.race([
      performance.measureUserAgentSpecificMemory(),
      new Promise((resolve) => setTimeout(() => resolve(null), 20000)),
    ]);
    return result ? result.bytes : null;
  } catch {
    return null;
  }
}

window.benchRun = async ({ engine, network, positions, depth, multiPv, threads, hashMiB }) => {
  const instance = engine === 'atomic' ? new AtomicEngine() : new FairyEngine();
  const coldStart = performance.now();
  const init = await instance.initialize({
    networkUrl: network.url,
    networkSha256: network.sha256,
    threads,
    hashMiB,
    multiPv,
  });
  const coldInitMs = performance.now() - coldStart;

  const results = [];
  for (const position of positions) {
    instance.send('ucinewgame'); // clear the hash before each position
    await instance.until('isready', /^readyok$/);
    const lines = [];
    const depthMs = {};
    let firstEvalMs = null;
    let last = null;
    const stopProbe = startJankProbe();
    const t0 = performance.now();
    await instance.search(`position ${position.uci}`, `go depth ${depth}`, (line, at) => {
      lines.push(line);
      const info = parseInfo(line);
      if (!info || !info.score) return;
      if (firstEvalMs === null) firstEvalMs = at - t0;
      if ((info.multipv ?? 1) === 1 && info.depth && !(info.depth in depthMs) && !info.score.bound)
        depthMs[info.depth] = at - t0;
      if ((info.multipv ?? 1) === 1 && info.pv?.length) last = info;
    });
    const completeMs = performance.now() - t0;
    const responsiveness = stopProbe();
    const bestmove = lines[lines.length - 1].split(' ')[1];
    const finalDepth = Math.max(0, ...lines.map((l) => parseInfo(l)?.depth ?? 0));
    const finalPvs = lines
      .map(parseInfo)
      .filter((i) => i && i.depth === finalDepth && i.pv?.length)
      .map((i) => ({ multipv: i.multipv ?? 1, score: i.score, pv: i.pv.slice(0, 6) }));
    results.push({
      name: position.name,
      position: position.uci,
      firstEvalMs,
      depthMs,
      completeMs,
      depthReached: finalDepth,
      nodes: last?.nodes ?? null,
      nps: last?.nps ?? null,
      engineTimeMs: last?.time ?? null,
      bestmove,
      finalPvs,
      responsiveness,
    });
  }
  const memoryBytes = await measureMemory();
  return {
    engine,
    coldInitMs,
    networkBytes: init.networkBytes,
    wasmHeapBytes: instance.heapBytes(),
    measureUserAgentSpecificMemoryBytes: memoryBytes,
    positions: results,
  };
};
window.benchReady = true;
