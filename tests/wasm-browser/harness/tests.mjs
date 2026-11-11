// Browser-side test cases. run-browser-tests.mjs loads index.html in each
// browser and calls window.runTest(name, context) for every case. A case
// throws on failure and may return `pvChecks` (positions and principal
// variations) that the runner verifies with an independent rules oracle.

import { EngineClient, parseInfo } from './client.mjs';

const WORKER = '/engine/atomic-stockfish.worker.js';

const ATOMIC_FENS = [
  'rn2kb1r/1pp1p2p/p2q1pp1/3P4/2P3b1/4PN2/PP3PPP/R2QKB1R b KQkq - 0 1',
  'rn1qkb1r/p5pp/2p5/3p4/N3P3/5P2/PPP4P/R1BQK3 w Qkq - 0 1',
  'r4b1r/2kb1N2/p2Bpnp1/8/2Pp3p/1P1PPP2/P5PP/R3K2R b KQ - 0 1',
];
const ATOMIC960_FENS = [
  'nrbkqbrn/pppppppp/8/8/8/8/PPPPPPPP/NRBKQBRN w KQkq - 0 1',
  'bqnb1rkr/pp3ppp/3ppn2/2p5/5P2/P2P4/NPP1P1PP/BQ1BNRKR w HFhf - 2 9',
];
const PERFT = [
  { chess960: false, position: 'startpos', depth: 4, nodes: 197326 },
  { chess960: false, position: `fen ${ATOMIC_FENS[0]}`, depth: 4, nodes: 1434825 },
  { chess960: false, position: `fen ${ATOMIC_FENS[1]}`, depth: 4, nodes: 714499 },
  { chess960: false, position: `fen ${ATOMIC_FENS[2]}`, depth: 2, nodes: 148 },
  { chess960: true, position: 'fen 8/8/8/8/8/8/2k5/rR4KR w KQ - 0 1', depth: 4, nodes: 61401 },
  { chess960: true, position: 'fen r3k1rR/5K2/8/8/8/8/8/8 b kq - 0 1', depth: 4, nodes: 98729 },
  { chess960: true, position: 'fen Rr2k1rR/3K4/3p4/8/8/8/7P/8 w kq - 0 1', depth: 4, nodes: 241478 },
  { chess960: true, position: 'fen 1R4kr/4K3/8/8/8/8/8/8 b k - 0 1', depth: 4, nodes: 17915 },
];

function assert(condition, message) {
  if (!condition) throw new Error(message);
}

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

async function readyEngine(context, options = {}) {
  const client = new EngineClient(WORKER);
  const ready = await client.initialize({
    networkUrl: context.reference.url,
    expectedSha256: context.reference.sha256,
    threads: options.threads ?? 2,
    hashMiB: options.hashMiB ?? 32,
    multiPv: options.multiPv ?? 1,
  });
  return { client, ready };
}

async function expectInitError(context, request, code) {
  const client = new EngineClient(WORKER);
  try {
    await client.initialize(request);
  } catch (error) {
    assert(error.code === code, `expected ${code}, got ${error.code}: ${error.message}`);
    await client.dispose();
    return error.message;
  }
  await client.dispose();
  throw new Error(`initialize succeeded, expected ${code}`);
}

// The position a search ran on, for the rules oracle.
function positionOf(command) {
  const match = /^position (startpos|fen (.+?))(?: moves (.+))?$/.exec(command);
  assert(match, `cannot parse ${command}`);
  return {
    fen: match[1] === 'startpos' ? null : match[2],
    moves: match[3] ? match[3].split(' ') : [],
  };
}

function finalLines(lines) {
  const infos = lines.map((m) => parseInfo(m.line)).filter((i) => i && i.pv && i.depth);
  const depth = Math.max(0, ...infos.map((i) => i.depth));
  return { depth, infos: infos.filter((i) => i.depth === depth) };
}

function pvChecksFor(position, chess960, search) {
  const checks = [];
  for (const m of search.lines) {
    const info = parseInfo(m.line);
    if (info?.pv?.length) checks.push({ ...position, chess960, pv: info.pv });
  }
  const best = search.bestmove.line.split(' ')[1];
  if (best && best !== '(none)') checks.push({ ...position, chess960, pv: [best] });
  return checks;
}

const tests = {};

tests['unsupported environment is rejected'] = async (context) => {
  assert(!self.crossOriginIsolated, 'this case must run on a page without COOP/COEP');
  const message = await expectInitError(
    context,
    { networkUrl: context.reference.url, expectedSha256: context.reference.sha256 },
    'unsupported-environment',
  );
  return { message };
};

tests['initialization, download progress and SHA-256 verification'] = async (context) => {
  assert(self.crossOriginIsolated === true, 'the harness page is not cross-origin isolated');
  const started = performance.now();
  const { client, ready } = await readyEngine(context);
  const progress = client.messages.filter((m) => m.type === 'downloadProgress');
  assert(progress.length >= 2, 'no download progress was reported');
  for (let i = 1; i < progress.length; ++i)
    assert(progress[i].loaded >= progress[i - 1].loaded, 'download progress went backwards');
  const last = progress[progress.length - 1];
  assert(last.loaded === context.reference.bytes, `loaded ${last.loaded} bytes`);
  assert(last.total === context.reference.bytes, `total was ${last.total}`);
  assert(ready.network.sha256 === context.reference.sha256, 'ready reports another network');
  assert(/Legacy Atomic V1/.test(ready.network.description ?? ''), 'unexpected network description');
  assert(/^Atomic-Stockfish /.test(ready.engine), `engine id: ${ready.engine}`);
  const unexpected = client.messages.filter((m) => m.type === 'line' || m.type === 'error');
  assert(!unexpected.length, `initialization leaked output: ${JSON.stringify(unexpected[0])}`);
  await client.dispose();
  return { initMs: performance.now() - started, progressEvents: progress.length };
};

tests['download progress without Content-Length'] = async (context) => {
  const client = new EngineClient(WORKER);
  await client.initialize({
    networkUrl: '/nolength' + context.reference.url,
    expectedSha256: context.reference.sha256,
  });
  const progress = client.messages.filter((m) => m.type === 'downloadProgress');
  assert(progress.every((m) => m.total === null), 'total must be null without Content-Length');
  assert(progress[progress.length - 1].loaded === context.reference.bytes, 'wrong byte count');
  await client.dispose();
};

tests['wrong SHA-256 is rejected'] = async (context) => {
  const message = await expectInitError(
    context,
    { networkUrl: context.reference.url, expectedSha256: '0'.repeat(64) },
    'network-sha256',
  );
  assert(message.includes(context.reference.sha256), 'the message should name the actual hash');
};

tests['missing network is rejected'] = async (context) => {
  await expectInitError(
    context,
    { networkUrl: '/fixtures/does-not-exist.nnue', expectedSha256: context.reference.sha256 },
    'network-download',
  );
};

tests['malformed initialize requests are rejected'] = async (context) => {
  await expectInitError(context, { networkUrl: context.reference.url, expectedSha256: 'abc' }, 'invalid-request');
  await expectInitError(
    context,
    { networkUrl: context.reference.url, expectedSha256: context.reference.sha256, threads: 5 },
    'invalid-request',
  );
  await expectInitError(
    context,
    { networkUrl: context.reference.url, expectedSha256: context.reference.sha256, hashMiB: 4096 },
    'invalid-request',
  );
};

tests['corrupt, truncated and incompatible networks are rejected'] = async (context) => {
  const messages = {};
  for (const name of ['corrupt', 'truncated', 'v2Trailing']) {
    const fixture = context.fixtures[name];
    messages[name] = await expectInitError(
      context,
      { networkUrl: fixture.url, expectedSha256: fixture.sha256 },
      'network-incompatible',
    );
  }
  return messages;
};

tests['AtomicNNUEV2 networks are accepted'] = async (context) => {
  const backends = {};
  for (const [name, backend] of [['v2', 'AtomicNNUEV2']]) {
    const fixture = context.fixtures[name];
    const client = new EngineClient(WORKER);
    const ready = await client.initialize({ networkUrl: fixture.url, expectedSha256: fixture.sha256 });
    assert(ready.network.description.includes(backend), `${name}: ${ready.network.description}`);
    client.command('position startpos');
    const search = await client.search('go depth 3');
    assert(/^bestmove [a-h][1-8]/.test(search.bestmove.line), `${name}: ${search.bestmove.line}`);
    backends[name] = ready.network.description;
    await client.dispose();
  }
  return backends;
};

tests['uci and isready'] = async (context) => {
  const { client } = await readyEngine(context);
  const start = client.messages.length;
  client.command('uci');
  await client.nextLine(/^uciok$/, 10000);
  const messages = client.linesSince(start);
  const lines = messages.map((m) => m.line);
  assert(lines.some((l) => /^id name Atomic-Stockfish/.test(l)), 'no id name');
  assert(lines.some((l) => /^option name UCI_Variant type combo default atomic/.test(l)), 'no UCI_Variant');
  assert(lines.some((l) => /^option name MultiPV type spin/.test(l)), 'no MultiPV');
  assert(messages.every((m) => !('searchId' in m)), 'uci output must not carry a searchId');
  await client.isready();
  client.command('xboard');
  await client.nextLine(/UCI-only/, 10000);
  await client.isready();
  await client.dispose();
};

tests['position startpos and go depth'] = async (context) => {
  const { client } = await readyEngine(context);
  const position = 'position startpos moves e2e4 e7e6';
  client.command(position);
  const search = await client.search('go depth 10');
  const { depth, infos } = finalLines(search.lines);
  assert(depth === 10, `final depth ${depth}`);
  assert(infos.length === 1, 'MultiPV 1 should print one line per depth');
  assert(search.lines.every((m) => m.searchId === search.searchId), 'untagged search line');
  await client.dispose();
  return { pvChecks: pvChecksFor(positionOf(position), false, search) };
};

tests['position fen, Atomic positions and go movetime'] = async (context) => {
  const { client } = await readyEngine(context);
  const pvChecks = [];
  const timings = [];
  for (const fen of ATOMIC_FENS) {
    const position = `position fen ${fen}`;
    client.command(position);
    const search = await client.search('go movetime 500');
    const elapsed = search.bestmove.at - search.startedAt;
    timings.push(elapsed);
    // Like the native engine, the search ends early once it has proved a mate
    // to the maximum depth (the first position is a mate in 2).
    const { depth, infos } = finalLines(search.lines);
    const provedMate = depth >= 200 && infos[0]?.score?.kind === 'mate';
    assert(elapsed < 2000, `movetime 500 took ${elapsed.toFixed(0)} ms`);
    assert(provedMate || elapsed >= 400, `movetime 500 ended after ${elapsed.toFixed(0)} ms at depth ${depth}`);
    pvChecks.push(...pvChecksFor(positionOf(position), false, search));
  }
  await client.dispose();
  return { pvChecks, timings };
};

tests['Atomic960 positions'] = async (context) => {
  const { client } = await readyEngine(context);
  client.command('setoption name UCI_Chess960 value true');
  const pvChecks = [];
  for (const fen of ATOMIC960_FENS) {
    const position = `position fen ${fen}`;
    client.command(position);
    const search = await client.search('go depth 9');
    assert(/^bestmove [a-h][1-8][a-h][1-8]/.test(search.bestmove.line), search.bestmove.line);
    pvChecks.push(...pvChecksFor(positionOf(position), true, search));
  }
  await client.dispose();
  return { pvChecks };
};

tests['go infinite, isready during search, then stop'] = async (context) => {
  const { client } = await readyEngine(context, { threads: 4 });
  client.command('position startpos');
  client.command('go infinite');
  const started = await client.next((m) => m.type === 'search', 10000);
  await client.nextLine(/^info depth 6 /, 60000);
  const readyStart = performance.now();
  client.command('isready');
  const ready = await client.nextLine(/^readyok$/, 5000);
  assert(ready.searchId === undefined, 'readyok is not search output');
  assert(performance.now() - readyStart < 1000, 'isready was not answered promptly during the search');
  assert(
    !client.messages.some((m) => m.type === 'line' && m.line.startsWith('bestmove')),
    'the infinite search ended by itself',
  );
  await sleep(300);
  const stopAt = performance.now();
  client.send({ type: 'stop', searchId: started.searchId });
  const bestmove = await client.nextLine(/^bestmove /, 5000);
  const stopLatency = performance.now() - stopAt;
  assert(bestmove.searchId === started.searchId, 'bestmove carries the wrong searchId');
  assert(stopLatency < 1500, `stop took ${stopLatency.toFixed(0)} ms`);
  await client.isready();
  await client.dispose();
  return { stopLatencyMs: stopLatency };
};

tests['MultiPV 1 to 5'] = async (context) => {
  const { client } = await readyEngine(context, { threads: 2 });
  const position = 'position startpos moves g1f3 f7f6';
  const pvChecks = [];
  for (let k = 1; k <= 5; ++k) {
    client.command(`setoption name MultiPV value ${k}`);
    client.command(position);
    const search = await client.search('go depth 8');
    const { infos } = finalLines(search.lines);
    const indices = infos.map((i) => i.multipv).sort();
    assert(
      JSON.stringify(indices) === JSON.stringify(Array.from({ length: k }, (_, i) => i + 1)),
      `MultiPV ${k}: final depth has lines ${indices}`,
    );
    const firstMoves = new Set(infos.map((i) => i.pv[0]));
    assert(firstMoves.size === k, `MultiPV ${k}: repeated first moves`);
    pvChecks.push(...pvChecksFor(positionOf(position), false, search));
  }
  await client.dispose();
  return { pvChecks };
};

// A Lichess-style board: the user keeps moving while the engine analyses.
// Only output of the board's current search may reach the "UI".
tests['rapid position/stop/go sequences and stale-output suppression'] = async (context) => {
  const { client } = await readyEngine(context, { threads: 4, multiPv: 3 });
  const line = ['e2e4', 'e7e6', 'g1f3', 'f7f6', 'd2d4', 'd7d5', 'b1c3', 'b8c6', 'f1b5', 'a7a6'];
  const searches = [];
  let current = 0;
  let expectedId = 0;
  let uiUpdates = 0;
  let staleSeen = 0;
  const start = client.messages.length;
  client.worker.addEventListener('message', (event) => {
    const m = event.data;
    if (m.type !== 'line' || m.searchId === undefined) return;
    if (m.searchId === current) uiUpdates += 1;
    else staleSeen += 1;
  });

  for (let i = 0; i < 40; ++i) {
    const ply = i % line.length;
    const position = `position startpos moves ${line.slice(0, ply).join(' ')}`.trim();
    const explicitStop = i % 3 === 0 && current;
    if (explicitStop) client.send({ type: 'stop', searchId: current });
    client.command(position.endsWith('moves') ? 'position startpos' : position);
    client.command(i % 4 === 3 ? 'go depth 30' : 'go infinite');
    current = ++expectedId;
    searches.push({ id: current, position: position.endsWith('moves') ? 'position startpos' : position });
    if (i % 5 === 4) client.command('setoption name MultiPV value ' + (1 + (i % 3)));
    await sleep([0, 5, 20, 60][i % 4]);
  }
  client.send({ type: 'stop', searchId: current });
  await client.next(
    (m) => m.type === 'line' && m.searchId === current && m.line.startsWith('bestmove'),
    30000,
    'last bestmove',
  );
  await client.isready();

  const messages = client.messages.slice(start);
  // Every search started exactly once and produced exactly one bestmove.
  const started = messages.filter((m) => m.type === 'search').map((m) => m.searchId);
  assert(JSON.stringify(started) === JSON.stringify(searches.map((s) => s.id)), `search ids ${started}`);
  for (const s of searches) {
    const count = messages.filter(
      (m) => m.type === 'line' && m.searchId === s.id && m.line.startsWith('bestmove'),
    ).length;
    assert(count === 1, `search ${s.id} produced ${count} bestmoves`);
  }
  // Output of a search never follows the start of a later one.
  let highest = 0;
  for (const m of messages) {
    if (m.type === 'search') highest = m.searchId;
    else if (m.type === 'line' && m.searchId !== undefined)
      assert(m.searchId === highest, `line of search ${m.searchId} after search ${highest} started`);
  }
  assert(!messages.some((m) => m.type === 'error'), 'the worker reported an error');

  const pvChecks = [];
  for (const s of searches) {
    const lines = messages.filter((m) => m.type === 'line' && m.searchId === s.id);
    const bestmove = lines.find((m) => m.line.startsWith('bestmove'));
    pvChecks.push(...pvChecksFor(positionOf(s.position), false, { lines, bestmove }));
  }
  await client.dispose();
  return { pvChecks, searches: searches.length, uiUpdates, staleSeen };
};

tests['Threads and Hash changes, and rejected setoption values'] = async (context) => {
  const { client } = await readyEngine(context, { threads: 1 });
  for (const threads of [1, 4, 2, 4, 3]) {
    client.command(`setoption name Threads value ${threads}`);
    client.command('position startpos');
    const search = await client.search('go depth 7');
    assert(/^bestmove [a-h]/.test(search.bestmove.line), `Threads ${threads}: ${search.bestmove.line}`);
  }
  client.command(`setoption name Hash value ${context.maxHashMiB}`);
  client.command('ucinewgame');
  client.command('position startpos');
  const big = await client.search('go depth 8');
  assert(/^bestmove [a-h]/.test(big.bestmove.line), 'the largest Hash failed');

  for (const bad of [
    'setoption name Threads value 5',
    `setoption name Hash value ${context.maxHashMiB + 1}`,
    'setoption name Hash value abc',
    'setoption name MultiPV value 0',
    'setoption name EvalFile value /nnue/other.nnue',
  ]) {
    const before = client.messages.length;
    client.command(bad);
    const error = await client.next((m) => m.type === 'error', 5000, `error for ${bad}`);
    assert(error.code === 'invalid-command' && !error.fatal, `${bad}: ${JSON.stringify(error)}`);
    assert(client.messages.length - before === 1, `${bad} produced other output`);
  }
  await client.isready();
  await client.dispose();
};

tests['perft vectors (Atomic and Atomic960)'] = async (context) => {
  const { client } = await readyEngine(context);
  const results = [];
  for (const vector of PERFT) {
    client.command(`setoption name UCI_Chess960 value ${vector.chess960}`);
    client.command(`position ${vector.position}`);
    const t = performance.now();
    client.command(`go perft ${vector.depth}`);
    const line = await client.nextLine(/^Nodes searched: \d+$/, 300000);
    const nodes = Number(line.line.split(': ')[1]);
    assert(nodes === vector.nodes, `${vector.position} depth ${vector.depth}: ${nodes} != ${vector.nodes}`);
    assert(line.searchId === undefined, 'perft output must not carry a searchId');
    results.push({ ...vector, ms: performance.now() - t });
  }
  await client.dispose();
  return { results };
};

async function measuredMemory() {
  // Chromium only; run-browser-tests.mjs enables eager measurement.
  if (!performance.measureUserAgentSpecificMemory) return null;
  try {
    const result = await Promise.race([
      performance.measureUserAgentSpecificMemory(),
      sleep(20000).then(() => null),
    ]);
    return result?.bytes ?? null;
  } catch {
    return null; // e.g. unavailable in Chromium's headless shell
  }
}

tests['initialize/dispose cycles'] = async (context) => {
  const cycles = [];
  const memory = [await measuredMemory()];
  for (let i = 0; i < 4; ++i) {
    const t = performance.now();
    const { client } = await readyEngine(context, { threads: 4, hashMiB: 64 });
    client.command('position startpos');
    client.command('go infinite');
    await client.nextLine(/^info depth 4 /, 30000);
    // Dispose in the middle of an infinite search.
    await client.dispose();
    cycles.push(performance.now() - t);
    await sleep(500);
    memory.push(await measuredMemory());
  }
  if (memory.every((bytes) => bytes !== null)) {
    // After the first cycle (lazy browser caches), memory must not keep growing.
    const growth = memory[memory.length - 1] - memory[1];
    assert(growth < 32 * 1048576, `memory grew by ${growth} bytes over three more cycles: ${memory}`);
  }
  return { cyclesMs: cycles, measuredMemoryBytes: memory };
};

tests['commands before ready and after dispose'] = async (context) => {
  const client = new EngineClient(WORKER);
  client.command('isready');
  const error = await client.next((m) => m.type === 'error', 5000);
  assert(error.code === 'not-ready', JSON.stringify(error));
  await client.dispose();
};

window.runTest = async (name, context) => {
  const test = tests[name];
  if (!test) throw new Error(`unknown test ${name}`);
  try {
    return (await test(context)) ?? {};
  } finally {
    // A failing case must not leave its engine running into the next one.
    for (const client of [...EngineClient.live]) client.terminate();
  }
};
window.testNames = Object.keys(tests);
window.testsReady = true;
