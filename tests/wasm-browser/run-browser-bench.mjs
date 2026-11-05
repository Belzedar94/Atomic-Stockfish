// Analysis-board benchmark: the Atomic-Stockfish browser worker against the
// Fairy-Stockfish 14 browser engine (npm fairy-stockfish-nnue.wasm, pinned in
// package.json), in Chromium and Firefox.
//
//   node run-browser-bench.mjs --net ../../src/atomic_run3b_e202_l05.nnue \
//        [--fsf-net <a Fairy-Stockfish 14 (HalfKAv2) Atomic network>] \
//        [--engine ../../build/wasm-browser] [--out <dir>] [--runs 3]
//
// Six fixed Atomic positions, go depth 15, MultiPV 3, Threads 4, Hash 64 MiB,
// ucinewgame (hash cleared) before each position, three runs per engine with
// alternating engine order, and a fresh page per run so that the cold
// initialization is measured apart from the warm analysis. Writes
// results.json (raw) and report.md (summary) to --out.

import { createHash } from 'node:crypto';
import { spawn } from 'node:child_process';
import { existsSync } from 'node:fs';
import { mkdir, readFile, rm, writeFile } from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import process from 'node:process';
import { fileURLToPath } from 'node:url';
import zlib from 'node:zlib';
import { chromium, firefox } from 'playwright';

import { startServer } from './serve.mjs';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const REPO = path.resolve(HERE, '..', '..');

function argument(name, fallback) {
  const index = process.argv.indexOf(name);
  return index === -1 ? fallback : process.argv[index + 1];
}

const engineDir = path.resolve(argument('--engine', path.join(REPO, 'build', 'wasm-browser')));
const netPath = path.resolve(argument('--net', path.join(REPO, 'src', 'atomic_run3b_e202_l05.nnue')));
const fsfNetPath = argument('--fsf-net') ? path.resolve(argument('--fsf-net')) : null;
const fsfDir = path.join(HERE, 'node_modules', 'fairy-stockfish-nnue.wasm');
const outDir = path.resolve(argument('--out', path.join(REPO, 'build', 'wasm-browser-bench')));
const runs = Number(argument('--runs', 3));
const browsers = argument('--browsers', 'chromium,firefox').split(',');
const python = argument('--python', process.platform === 'win32' ? 'python' : 'python3');
const browserPathMatch = argument('--browser-path-match', process.env.PLAYWRIGHT_BROWSERS_PATH ?? 'ms-playwright');

const CONFIG = { depth: 15, multiPv: 3, threads: 4, hashMiB: 64 };
const POSITIONS = [
  { name: 'start', uci: 'startpos' },
  { name: 'opening e3 e6', uci: 'startpos moves e2e3 e7e6 g1f3' },
  { name: 'Italian', uci: 'fen r1bqkb1r/pppp1ppp/2n2n2/4p3/2B1P3/5N2/PPPP1PPP/RNBQK2R w KQkq - 4 4' },
  { name: 'perft-2 middlegame', uci: 'fen rn1qkb1r/p5pp/2p5/3p4/N3P3/5P2/PPP4P/R1BQK3 w Qkq - 0 1' },
  { name: 'perft-3 middlegame', uci: 'fen r4b1r/2kb1N2/p2Bpnp1/8/2Pp3p/1P1PPP2/P5PP/R3K2R b KQ - 0 1' },
  { name: 'rook endgame', uci: 'fen r4rk1/1pp2ppp/p1n5/3p4/3P4/2P2N2/PP3PPP/R4RK1 w - - 0 15' },
];

const sha256 = (bytes) => createHash('sha256').update(bytes).digest('hex');
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
const median = (values) => {
  const sorted = [...values].sort((a, b) => a - b);
  const mid = Math.floor(sorted.length / 2);
  return sorted.length % 2 ? sorted[mid] : (sorted[mid - 1] + sorted[mid]) / 2;
};
const mean = (values) => values.reduce((a, b) => a + b, 0) / values.length;

async function sizes(file) {
  const bytes = await readFile(file);
  return {
    name: path.basename(file),
    bytes: bytes.length,
    gzipBytes: zlib.gzipSync(bytes, { level: 9 }).length,
    brotliBytes: zlib.brotliCompressSync(bytes, {
      params: { [zlib.constants.BROTLI_PARAM_QUALITY]: 11, [zlib.constants.BROTLI_PARAM_SIZE_HINT]: bytes.length },
    }).length,
    sha256: sha256(bytes),
  };
}

function startSampler(tag) {
  const outFile = path.join(outDir, `mem-${tag}.json`);
  const stopFile = path.join(outDir, `mem-${tag}.stop`);
  const child = spawn(python, [path.join(HERE, 'mem_sampler.py'), '--match', browserPathMatch, '--out', outFile, '--stop', stopFile], {
    stdio: 'ignore',
  });
  return async () => {
    await writeFile(stopFile, '');
    await new Promise((resolve) => child.on('exit', resolve));
    const result = existsSync(outFile) ? JSON.parse(await readFile(outFile, 'utf-8')) : null;
    await rm(outFile, { force: true });
    await rm(stopFile, { force: true });
    return result;
  };
}

async function runOne(browser, origin, engine, network) {
  const context = await browser.newContext();
  const page = await context.newPage();
  const stopSampler = startSampler(`${engine}`);
  await sleep(300);
  try {
    await page.goto(`${origin}/bench.html`);
    await page.waitForFunction(() => window.benchReady === true);
    let workersDuringAnalysis = 0;
    const counter = setInterval(() => {
      workersDuringAnalysis = Math.max(workersDuringAnalysis, page.workers().length);
    }, 200);
    const result = await page.evaluate((args) => window.benchRun(args), {
      engine,
      network,
      positions: POSITIONS,
      ...CONFIG,
    });
    clearInterval(counter);
    result.workers = workersDuringAnalysis;
    result.workerUrls = [...new Set(page.workers().map((w) => w.url()))];
    return result;
  } finally {
    await context.close();
    const memory = await stopSampler();
    runOne.lastMemory = memory;
  }
}

function summarize(raw) {
  const rows = [];
  for (const browser of raw.browsers) {
    for (const engine of ['atomic', 'fsf']) {
      const engineRuns = browser.runs.filter((r) => r.engine === engine && !r.error);
      if (!engineRuns.length) continue;
      const completes = engineRuns.flatMap((r) => r.positions.map((p) => p.completeMs));
      const perPosition = POSITIONS.map((position) => {
        const samples = engineRuns.map((r) => r.positions.find((p) => p.name === position.name));
        return {
          name: position.name,
          medianCompleteMs: median(samples.map((s) => s.completeMs)),
          medianFirstEvalMs: median(samples.map((s) => s.firstEvalMs)),
          medianNodes: median(samples.map((s) => s.nodes)),
          medianNps: median(samples.map((s) => s.nps)),
          bestmoves: samples.map((s) => s.bestmove),
        };
      });
      rows.push({
        browser: browser.name,
        engine,
        runs: engineRuns.length,
        medianCompleteMs: median(completes),
        meanCompleteMs: mean(completes),
        medianColdInitMs: median(engineRuns.map((r) => r.coldInitMs)),
        medianNps: median(engineRuns.flatMap((r) => r.positions.map((p) => p.nps))),
        maxMainThreadLatenessMs: Math.max(...engineRuns.flatMap((r) => r.positions.map((p) => p.responsiveness.maxMs))),
        p99MainThreadLatenessMs: median(engineRuns.flatMap((r) => r.positions.map((p) => p.responsiveness.p99Ms))),
        peakBrowserRssBytes: Math.max(...engineRuns.map((r) => r.peakBrowserMemory?.peakBytes ?? 0)),
        wasmHeapBytes: Math.max(...engineRuns.map((r) => r.wasmHeapBytes ?? 0)),
        measuredMemoryBytes: median(engineRuns.map((r) => r.measureUserAgentSpecificMemoryBytes ?? NaN)),
        workers: Math.max(...engineRuns.map((r) => r.workers)),
        perPosition,
      });
    }
  }
  return rows;
}

const mib = (bytes) => (bytes / 1048576).toFixed(1);

function markdown(raw, rows) {
  const out = [];
  out.push('# Atomic browser engine benchmark', '');
  out.push(`Generated ${raw.generatedAt} on ${raw.host.cpu} (${raw.host.cpus} logical CPUs), ${raw.host.platform}.`);
  out.push(
    `Configuration: ${POSITIONS.length} positions, go depth ${CONFIG.depth}, MultiPV ${CONFIG.multiPv}, Threads ${CONFIG.threads}, Hash ${CONFIG.hashMiB} MiB, ucinewgame before each position, ${runs} runs per engine, alternating order, fresh page per run.`,
  );
  out.push(
    `Engines: Atomic-Stockfish browser worker (${raw.manifest.sourceCommit?.slice(0, 8)}, Emscripten ${raw.manifest.emscriptenVersion}, network ${path.basename(netPath)}) and Fairy-Stockfish 14 (npm fairy-stockfish-nnue.wasm 1.1.12, ${raw.networks.fsf ? 'NNUE ' + raw.networks.fsf.name : 'classical evaluation: no Fairy-Stockfish 14 (HalfKAv2) Atomic network was available'}).`,
  );
  out.push('');
  out.push('| Browser | Engine | Runs | Median depth-15 ms | Mean ms | Median NPS | Cold init ms | Peak browser RSS MiB | WASM heap MiB | Workers | Main-thread lateness p99 / max ms |');
  out.push('|---|---|---|---|---|---|---|---|---|---|---|');
  for (const r of rows)
    out.push(
      `| ${r.browser} | ${r.engine} | ${r.runs} | ${r.medianCompleteMs.toFixed(0)} | ${r.meanCompleteMs.toFixed(0)} | ${Math.round(r.medianNps)} | ${r.medianColdInitMs.toFixed(0)} | ${mib(r.peakBrowserRssBytes)} | ${mib(r.wasmHeapBytes)} | ${r.workers} | ${r.p99MainThreadLatenessMs.toFixed(1)} / ${r.maxMainThreadLatenessMs.toFixed(1)} |`,
    );
  out.push('', '## Per position (median of runs)', '');
  out.push('| Browser | Position | Atomic-SF ms | Atomic-SF first eval ms | Atomic-SF nodes | FSF ms | FSF first eval ms | FSF nodes | Atomic-SF move | FSF move |');
  out.push('|---|---|---|---|---|---|---|---|---|---|');
  for (const browser of raw.browsers) {
    const a = rows.find((r) => r.browser === browser.name && r.engine === 'atomic');
    const f = rows.find((r) => r.browser === browser.name && r.engine === 'fsf');
    for (const position of POSITIONS) {
      const pa = a?.perPosition.find((p) => p.name === position.name);
      const pf = f?.perPosition.find((p) => p.name === position.name);
      out.push(
        `| ${browser.name} | ${position.name} | ${pa ? pa.medianCompleteMs.toFixed(0) : '-'} | ${pa ? pa.medianFirstEvalMs.toFixed(0) : '-'} | ${pa?.medianNodes ?? '-'} | ${pf ? pf.medianCompleteMs.toFixed(0) : '-'} | ${pf ? pf.medianFirstEvalMs.toFixed(0) : '-'} | ${pf?.medianNodes ?? '-'} | ${pa?.bestmoves.join(' ') ?? '-'} | ${pf?.bestmoves.join(' ') ?? '-'} |`,
      );
    }
  }
  out.push('', '## Download sizes', '');
  out.push('| File | Bytes | gzip -9 | brotli 11 |');
  out.push('|---|---|---|---|');
  const nets = [raw.networks.atomic, raw.networks.fsf].filter(Boolean);
  for (const s of [...raw.artifacts.atomic, ...raw.artifacts.fsf, ...nets])
    out.push(`| ${s.name} | ${s.bytes} | ${s.gzipBytes} | ${s.brotliBytes} |`);
  out.push('', 'Primary-move differences are informational only. Depth-15 node counts are not comparable between the engines (different search and evaluation).');
  return out.join('\n') + '\n';
}

await mkdir(outDir, { recursive: true });
const reference = await readFile(netPath);
const networks = {
  atomic: { url: `/nets/${path.basename(netPath)}`, sha256: sha256(reference) },
  // Without --fsf-net, Fairy-Stockfish runs with its classical evaluation.
  fsf: fsfNetPath
    ? { url: `/fsfnet/${path.basename(fsfNetPath)}`, sha256: sha256(await readFile(fsfNetPath)) }
    : { url: null, sha256: null, classical: true },
};
const mounts = {
  '/': path.join(HERE, 'harness'),
  '/engine': engineDir,
  '/nets': path.dirname(netPath),
  '/fsf': fsfDir,
};
if (fsfNetPath) mounts['/fsfnet'] = path.dirname(fsfNetPath);
const { server, origin } = await startServer({ mounts });

const raw = {
  generatedAt: new Date().toISOString(),
  host: { cpu: os.cpus()[0]?.model, cpus: os.cpus().length, platform: `${os.platform()} ${os.release()}` },
  config: { ...CONFIG, runs, positions: POSITIONS },
  manifest: JSON.parse(await readFile(path.join(engineDir, 'manifest.json'), 'utf-8')),
  artifacts: {
    atomic: await Promise.all(
      ['atomic-stockfish.mjs', 'atomic-stockfish.wasm', 'atomic-stockfish.worker.js'].map((f) => sizes(path.join(engineDir, f))),
    ),
    fsf: await Promise.all(['stockfish.js', 'stockfish.wasm', 'stockfish.worker.js'].map((f) => sizes(path.join(fsfDir, f)))),
  },
  networks: {
    atomic: { ...networks.atomic, ...(await sizes(netPath)) },
    fsf: fsfNetPath ? { ...networks.fsf, ...(await sizes(fsfNetPath)) } : null,
  },
  browsers: [],
};

try {
  for (const name of browsers) {
    const type = name === 'firefox' ? firefox : chromium;
    const browser = await type.launch(
      name === 'chromium' ? { args: ['--enable-blink-features=ForceEagerMeasureMemory'] } : {},
    );
    const entry = { name, version: browser.version(), runs: [] };
    console.log(`${name} ${browser.version()}`);
    for (let run = 0; run < runs; ++run) {
      const order = run % 2 === 0 ? ['atomic', 'fsf'] : ['fsf', 'atomic'];
      for (const engine of order) {
        let result;
        try {
          result = await runOne(browser, origin, engine, networks[engine]);
          result.peakBrowserMemory = runOne.lastMemory;
        } catch (error) {
          result = { engine, error: String(error.message ?? error), peakBrowserMemory: runOne.lastMemory };
        }
        result.run = run;
        entry.runs.push(result);
        console.log(
          `  run ${run + 1} ${engine}: ${result.error ? 'ERROR ' + result.error.slice(0, 300) : result.positions.map((p) => p.completeMs.toFixed(0)).join(' ') + ' ms; cold ' + result.coldInitMs.toFixed(0) + ' ms'}`,
        );
      }
    }
    raw.browsers.push(entry);
    await browser.close();
  }
} finally {
  server.close();
}

const rows = summarize(raw);
raw.summary = rows;
await writeFile(path.join(outDir, 'results.json'), JSON.stringify(raw, null, 2) + '\n');
await writeFile(path.join(outDir, 'report.md'), markdown(raw, rows));
console.log(markdown(raw, rows));
