// Browser lifecycle tests for the Atomic-Stockfish Web Worker build.
//
//   cd tests/wasm-browser && npm ci && npx playwright install chromium firefox
//   node run-browser-tests.mjs --engine ../../build/wasm-browser \
//        --net ../../src/atomic_run3b_e202_l05.nnue [--net-sha256 <hex>] \
//        [--browsers chromium,firefox] [--only <substring of a case name>]
//
// Each case runs inside a real browser page (harness/tests.mjs) and drives the
// worker through its postMessage protocol. The runner adds the checks that
// need the browser from outside: lazy loading (no request for the engine or
// the network before initialize), worker teardown after dispose, and an
// independent legality check of every principal variation (pv_oracle.py,
// python-chess).

import { createHash } from 'node:crypto';
import { spawnSync } from 'node:child_process';
import { mkdir, readFile, rm, writeFile } from 'node:fs/promises';
import path from 'node:path';
import process from 'node:process';
import { fileURLToPath } from 'node:url';
import { chromium, firefox } from 'playwright';

import { startServer } from './serve.mjs';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const REPO = path.resolve(HERE, '..', '..');
const REFERENCE_SHA256 = '99dc67eabf26a64faeeca3a88b4c38597a840b8d4a874b9f2cf658c6f92a04a6';

function argument(name, fallback) {
  const index = process.argv.indexOf(name);
  return index === -1 ? fallback : process.argv[index + 1];
}

const engineDir = path.resolve(argument('--engine', path.join(REPO, 'build', 'wasm-browser')));
const netPath = path.resolve(argument('--net', path.join(REPO, 'src', 'atomic_run3b_e202_l05.nnue')));
const fixtureDir = path.resolve(argument('--fixtures', path.join(REPO, 'build', 'wasm-browser-fixtures')));
const reportPath = argument('--report', path.join(fixtureDir, 'browser-test-report.json'));
const browsers = argument('--browsers', 'chromium,firefox').split(',');
const only = argument('--only');
const python = argument('--python', process.platform === 'win32' ? 'python' : 'python3');
// CI runs the suite with the synthetic Legacy Atomic V1 network
// (tests/create_synthetic_zero_nnue.py) instead of the 47.7 MB reference net.
const expectedNetSha256 = argument('--net-sha256', REFERENCE_SHA256).toLowerCase();

const sha256 = (bytes) => createHash('sha256').update(bytes).digest('hex');

function runPython(args) {
  const result = spawnSync(python, args, { cwd: REPO, encoding: 'utf-8' });
  if (result.status !== 0) throw new Error(`${python} ${args.join(' ')} failed:\n${result.stderr}`);
  return result.stdout;
}

async function prepareFixtures() {
  await mkdir(fixtureDir, { recursive: true });
  const reference = await readFile(netPath);
  if (sha256(reference) !== expectedNetSha256)
    throw new Error(`${netPath} does not have the SHA-256 ${expectedNetSha256}`);

  const fixtures = {};
  const add = async (name, file, bytes) => {
    await writeFile(path.join(fixtureDir, file), bytes);
    fixtures[name] = { url: `/fixtures/${file}`, sha256: sha256(bytes), bytes: bytes.length };
  };
  // Deterministic pseudo-random bytes: a "network" whose hash the caller knows
  // but which no reader accepts.
  const corrupt = Buffer.alloc(1 << 20);
  let x = 0x9e3779b9;
  for (let i = 0; i < corrupt.length; ++i) {
    x ^= x << 13;
    x ^= x >>> 17;
    x ^= x << 5;
    corrupt[i] = x & 0xff;
  }
  await add('corrupt', 'corrupt.nnue', corrupt);
  await add('truncated', 'truncated.nnue', reference.subarray(0, 8 << 20));

  for (const [name, script] of [
    ['v2', 'create_synthetic_atomic_v2_nnue.py'],
  ]) {
    const file = path.join(fixtureDir, `${name}.nnue`);
    await rm(file, { force: true }); // the generators refuse to overwrite
    runPython([path.join('tests', script), '--output', file]);
    const bytes = await readFile(file);
    fixtures[name] = { url: `/fixtures/${name}.nnue`, sha256: sha256(bytes), bytes: bytes.length };
  }
  const v2 = await readFile(path.join(fixtureDir, 'v2.nnue'));
  await add('v2Trailing', 'v2-trailing-byte.nnue', Buffer.concat([v2, Buffer.from([0xa5])]));
  return {
    reference: { url: `/ref/${path.basename(netPath)}`, sha256: expectedNetSha256, bytes: reference.length },
    fixtures,
  };
}

function withTimeout(promise, ms, label) {
  let timer;
  return Promise.race([
    promise.finally(() => clearTimeout(timer)),
    new Promise((_, reject) => {
      timer = setTimeout(() => reject(new Error(`${label} timed out after ${ms} ms`)), ms);
    }),
  ]);
}

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

async function runBrowser(name, origin, context) {
  const type = name === 'firefox' ? firefox : chromium;
  const browser = await type.launch(
    // The full Chromium build (not the headless shell) has
    // measureUserAgentSpecificMemory, used by the dispose memory check.
    name === 'chromium'
      ? { channel: 'chromium', args: ['--enable-blink-features=ForceEagerMeasureMemory'] }
      : {},
  );
  const browserContext = await browser.newContext();
  const requests = [];
  browserContext.on('request', (request) => requests.push(request.url()));
  const page = await browserContext.newPage();
  const consoleErrors = [];
  page.on('pageerror', (error) => consoleErrors.push(error.message));
  const results = [];
  const pvChecks = [];

  const open = async (prefix = '') => {
    await page.goto(`${origin}${prefix}/index.html`);
    await page.waitForFunction(() => window.testsReady === true);
  };

  const record = async (test, body) => {
    if (only && !test.includes(only)) return;
    const started = Date.now();
    try {
      const details = (await body()) ?? {};
      if (details.pvChecks) {
        pvChecks.push(...details.pvChecks);
        details.pvChecked = details.pvChecks.length;
        delete details.pvChecks;
      }
      results.push({ test, ok: true, ms: Date.now() - started, details });
      console.log(`  ok    ${test} (${Date.now() - started} ms)`);
    } catch (error) {
      results.push({ test, ok: false, ms: Date.now() - started, error: String(error.stack ?? error) });
      console.log(`  FAIL  ${test}\n${String(error.message ?? error).replace(/^/gm, '        ')}`);
    }
  };
  const inPage = (test, timeoutMs = 600000) =>
    record(test, () =>
      withTimeout(page.evaluate(([n, c]) => window.runTest(n, c), [test, context]), timeoutMs, test),
    );

  console.log(`${name} ${browser.version()}`);

  await open('/plain');
  await inPage('unsupported environment is rejected');

  await open();
  await record('nothing is downloaded before initialize', async () => {
    requests.length = 0;
    await page.evaluate(() => {
      window.lazyWorker = new window.EngineClient('/engine/atomic-stockfish.worker.js');
    });
    await sleep(1500);
    const heavy = requests.filter((url) => /\.(wasm|nnue|mjs)(\?|$)/.test(url) && !url.endsWith('client.mjs') && !url.endsWith('tests.mjs'));
    if (heavy.length) throw new Error(`requested before initialize: ${heavy.join(', ')}`);
    await page.evaluate(() => window.lazyWorker.dispose());
    return { requestsBeforeInitialize: requests.map((url) => new URL(url).pathname) };
  });

  const names = await page.evaluate(() => window.testNames);
  for (const test of names) {
    if (test === 'unsupported environment is rejected') continue;
    await inPage(test);
  }

  await record('workers are released after dispose', async () => {
    await sleep(1000);
    const live = page.workers().map((worker) => worker.url());
    if (live.length) throw new Error(`live workers after dispose: ${live.join(', ')}`);
    return { liveWorkers: live.length };
  });

  if (consoleErrors.length)
    results.push({ test: 'no uncaught page errors', ok: false, error: consoleErrors.join('\n') });
  await browser.close();
  return { browser: name, version: browser.version(), results, pvChecks };
}

const manifest = JSON.parse(await readFile(path.join(engineDir, 'manifest.json'), 'utf-8'));
const context = { ...(await prepareFixtures()), maxHashMiB: manifest.maxHashMiB };
const { server, origin } = await startServer({
  mounts: {
    '/': path.join(HERE, 'harness'),
    '/engine': engineDir,
    '/fixtures': fixtureDir,
    '/ref': path.dirname(netPath),
  },
});

const report = { engine: manifest, runs: [] };
let failed = 0;
try {
  for (const name of browsers) {
    const run = await runBrowser(name, origin, context);
    const casesFile = path.join(fixtureDir, `pv-cases-${name}.json`);
    await writeFile(casesFile, JSON.stringify(run.pvChecks));
    const oracle = spawnSync(python, [path.join(HERE, 'pv_oracle.py'), casesFile], { encoding: 'utf-8' });
    const verdict = oracle.stdout ? JSON.parse(oracle.stdout) : { error: oracle.stderr };
    run.results.push({
      test: 'every principal variation is legal (python-chess oracle)',
      ok: oracle.status === 0,
      details: verdict,
    });
    console.log(
      `  ${oracle.status === 0 ? 'ok   ' : 'FAIL '} every principal variation is legal (${verdict.checked ?? 0} lines)`,
    );
    delete run.pvChecks;
    failed += run.results.filter((r) => !r.ok).length;
    report.runs.push(run);
  }
} finally {
  server.close();
}
report.failed = failed;
await writeFile(reportPath, JSON.stringify(report, null, 2) + '\n');
console.log(`${failed ? 'FAILED' : 'passed'}: ${failed} failing checks; report in ${reportPath}`);
process.exitCode = failed ? 1 : 0;
