import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { readFile } from 'node:fs/promises';
import path from 'node:path';
import process from 'node:process';
import { spawn } from 'node:child_process';

const DEFAULT_NET_SHA256 =
  '99dc67eabf26a64faeeca3a88b4c38597a840b8d4a874b9f2cf658c6f92a04a6';

function argument(name) {
  const index = process.argv.indexOf(name);
  if (index < 0 || index + 1 >= process.argv.length) {
    throw new Error(`Missing required ${name} argument`);
  }
  return process.argv[index + 1];
}

function option(name) {
  return path.resolve(argument(name));
}

const engine = option('--engine');
const net = option('--net');
const expectedNetSha256 = process.argv.includes('--expected-net-sha256')
  ? argument('--expected-net-sha256').toLowerCase()
  : DEFAULT_NET_SHA256;
assert.match(expectedNetSha256, /^[0-9a-f]{64}$/, 'invalid expected NNUE SHA-256');
const netBytes = await readFile(net);
const netSha256 = createHash('sha256').update(netBytes).digest('hex');
assert.equal(netSha256, expectedNetSha256, 'the requested Atomic NNUE SHA-256 changed');

const manifest = JSON.parse(
  await readFile(path.join(path.dirname(engine), 'manifest.json'), 'utf8'),
);
assert.equal(manifest.supportedEntrypoint, path.basename(engine));
assert.equal(manifest.generatedRuntimeGlue, 'atomic-stockfish-nnue.js');
assert.equal(manifest.directRuntimeGlueSupported, false);
assert.equal(manifest.stdinPump?.maxOutstandingPrivatePumps, 1);
assert.equal(manifest.stdinPump?.preservesUserReadyok, true);
if (manifest.schemaVersion >= 2) {
  assert.deepEqual(manifest.supportedNetworkBackends, [
    'Legacy Atomic V1',
    'AtomicNNUEV2',
  ]);
  assert.deepEqual(manifest.networkFileVersions, ['0x7AF32F20', '0xA70C0002']);
}
const launcherArtifact = manifest.artifacts.find(
  (artifact) => artifact.name === path.basename(engine),
);
assert.ok(launcherArtifact, 'supported launcher is missing from the artifact manifest');
const engineBytes = await readFile(engine);
assert.equal(
  createHash('sha256').update(engineBytes).digest('hex'),
  launcherArtifact.sha256,
  'supported launcher differs from the artifact recorded in the manifest',
);

const child = spawn(process.execPath, [engine], {
  cwd: path.dirname(engine),
  env: { ...process.env, SOURCE_DATE_EPOCH: '0' },
  stdio: ['pipe', 'pipe', 'pipe'],
});

let transcript = '';
let exitCode;
child.stdout.setEncoding('utf8');
child.stderr.setEncoding('utf8');
child.stdout.on('data', (chunk) => {
  transcript += chunk;
  process.stdout.write(chunk);
});
child.stderr.on('data', (chunk) => {
  transcript += chunk;
  process.stderr.write(chunk);
});
child.on('exit', (code) => {
  exitCode = code;
});

function waitFor(pattern, from, timeoutMs = 120_000) {
  return new Promise((resolve, reject) => {
    const started = Date.now();
    const poll = () => {
      const segment = transcript.slice(from);
      if (pattern.test(segment)) {
        resolve(segment);
        return;
      }
      if (exitCode !== undefined) {
        reject(
          new Error(
            `engine exited with ${exitCode} before ${pattern}; transcript:\n${transcript}`,
          ),
        );
        return;
      }
      if (Date.now() - started >= timeoutMs) {
        reject(new Error(`timeout waiting for ${pattern}; transcript:\n${transcript}`));
        return;
      }
      setTimeout(poll, 20);
    };
    poll();
  });
}

function pause(milliseconds) {
  return new Promise((resolve) => setTimeout(resolve, milliseconds));
}

async function command(text, expected, timeoutMs) {
  const checkpoint = transcript.length;
  child.stdin.write(`${text}\n`);
  return waitFor(expected, checkpoint, timeoutMs);
}

async function interactiveSearch(mode, moves = '') {
  child.stdin.write(`setoption name Use NNUE value ${mode}\n`);
  child.stdin.write(`position startpos${moves ? ` moves ${moves}` : ''}\n`);
  const searchCheckpoint = transcript.length;
  child.stdin.write('go infinite\n');

  // These are deliberate wall-clock pauses. They prove that commands are
  // delivered through live stdin while the pthread search remains active,
  // rather than relying on a transcript that was written in one batch.
  await pause(125);
  const readyCheckpoint = transcript.length;
  child.stdin.write('isready\n');
  const ready = await waitFor(/(?:^|\n)readyok(?:\r?\n|$)/, readyCheckpoint, 30_000);
  assert.equal(
    [...ready.matchAll(/(?:^|\n)readyok(?:\r?\n|$)/g)].length,
    1,
    `private pump readyok leaked during ${mode} search`,
  );

  await pause(75);
  child.stdin.write('stop\n');
  const search = await waitFor(/bestmove\s+(?!\(none\))\S+/, searchCheckpoint, 30_000);
  assert.equal(
    (search.match(/^readyok\r?$/gm) ?? []).length,
    1,
    `expected exactly the GUI readyok during ${mode} search`,
  );
  if (mode === 'false') assert.match(search, /Classical Atomic evaluation enabled/);
  else assert.match(search, /NNUE evaluation using/);
}

try {
  await waitFor(/Atomic-Stockfish/, 0);

  const uci = await command('uci', /uciok/);
  assert.match(uci, /id name Atomic-Stockfish/);
  assert.match(uci, /option name UCI_Variant type combo default atomic var atomic/);
  assert.match(uci, /option name Use NNUE type combo default true var false var true var pure/);
  assert.doesNotMatch(uci, /UCCI_Variant|USI_Variant/);
  await command('isready', /readyok/);

  child.stdin.write('setoption name Use NNUE value false\n');
  child.stdin.write('position startpos\n');
  const classicalSearch = await command(
    'go nodes 128',
    /bestmove\s+(?!\(none\))\S+/,
    180_000,
  );
  assert.match(classicalSearch, /Classical Atomic evaluation enabled/);
  await interactiveSearch('false');

  const netForUci = net.replaceAll('\\', '/');
  child.stdin.write(`setoption name EvalFile value ${netForUci}\n`);
  child.stdin.write('setoption name Use NNUE value true\n');

  // Perft is deliberately independent of evaluation. Prove the requested
  // network loads in the same NNUE-enabled session before running its rules
  // vectors instead of making go perft depend on EvalFile.
  child.stdin.write('position startpos\n');
  const nnueLoad = await command('go nodes 1', /bestmove\s+(?!\(none\))\S+/, 180_000);
  assert.match(nnueLoad, /NNUE evaluation using .*Legacy Atomic V1/);

  child.stdin.write('position startpos\n');
  await command('go perft 4', /Nodes searched: 197326/, 180_000);

  child.stdin.write('setoption name UCI_Chess960 value true\n');
  child.stdin.write('position fen 8/8/8/8/8/8/2k5/rR4KR w KQ - 0 1\n');
  await command('go perft 4', /Nodes searched: 61401/, 180_000);
  child.stdin.write('setoption name UCI_Chess960 value false\n');

  child.stdin.write('position startpos\n');
  const trueSearch = await command('go nodes 128', /bestmove\s+(?!\(none\))\S+/, 180_000);
  assert.match(trueSearch, /NNUE evaluation using/);
  await interactiveSearch('true', 'd2d4');

  child.stdin.write('setoption name Use NNUE value pure\n');
  child.stdin.write('position startpos moves e2e4 e7e5\n');
  const pureSearch = await command('go nodes 128', /bestmove\s+(?!\(none\))\S+/, 180_000);
  assert.match(pureSearch, /NNUE evaluation using/);
  await interactiveSearch('pure', 'e2e4 e7e5');

  child.stdin.write('position fen 8/8/8/8/8/8/8/K7 b - - 0 1\n');
  await command('go depth 2', /bestmove \(none\)/, 180_000);

  const xboard = await command('xboard', /WebAssembly artifact is UCI-only/);
  assert.doesNotMatch(xboard, /feature .*variants=/);

  child.stdin.write('quit\n');
  child.stdin.end();
  if (exitCode === undefined) {
    await new Promise((resolve, reject) => {
      const timer = setTimeout(() => reject(new Error('engine did not exit')), 30_000);
      child.once('exit', (code) => {
        clearTimeout(timer);
        assert.equal(code, 0);
        resolve();
      });
    });
  } else {
    assert.equal(exitCode, 0);
  }

  console.log(`WASM engine integration: PASS (net sha256=${netSha256})`);
} catch (error) {
  child.kill();
  throw error;
}
