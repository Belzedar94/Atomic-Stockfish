import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { readFileSync } from 'node:fs';
import { mkdtemp, readFile, rm, writeFile } from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import process from 'node:process';
import { spawn } from 'node:child_process';

const MIB = 1024 * 1024;

function argument(name, defaultValue) {
  const index = process.argv.indexOf(name);
  if (index < 0) {
    if (defaultValue !== undefined) return defaultValue;
    throw new Error(`Missing required ${name} argument`);
  }
  if (index + 1 >= process.argv.length) throw new Error(`Missing value for ${name}`);
  return process.argv[index + 1];
}

function fileOption(name) {
  return path.resolve(argument(name));
}

function sha256(bytes) {
  return createHash('sha256').update(bytes).digest('hex');
}

function uciPath(value) {
  return value.replaceAll('\\', '/');
}

function readLinuxProcessRss(pid) {
  try {
    const status = readFileSync(`/proc/${pid}/status`, 'utf8');
    const match = /^VmRSS:\s+(\d+)\s+kB$/m.exec(status);
    return match ? Number(match[1]) * 1024 : 0;
  } catch {
    return 0;
  }
}

function readLinuxChildren(pid) {
  try {
    const children = readFileSync(`/proc/${pid}/task/${pid}/children`, 'utf8').trim();
    return children ? children.split(/\s+/).map(Number) : [];
  } catch {
    return [];
  }
}

function processTreeRss(rootPid) {
  if (process.platform !== 'linux') return undefined;
  const pending = [rootPid];
  const visited = new Set();
  let total = 0;
  while (pending.length) {
    const pid = pending.pop();
    if (!Number.isInteger(pid) || visited.has(pid)) continue;
    visited.add(pid);
    total += readLinuxProcessRss(pid);
    pending.push(...readLinuxChildren(pid));
  }
  return total || undefined;
}

class MemoryMonitor {
  constructor(rootPid) {
    this.rootPid = rootPid;
    this.peakBytes = 0;
    this.samples = 0;
    this.timer = setInterval(() => this.sample(), 20);
  }

  sample() {
    const value = processTreeRss(this.rootPid);
    if (value !== undefined) {
      this.peakBytes = Math.max(this.peakBytes, value);
      this.samples += 1;
    }
    return value;
  }

  stop() {
    clearInterval(this.timer);
    return this.sample();
  }
}

class UciProcess {
  constructor(executable, timeoutMs) {
    this.timeoutMs = timeoutMs;
    this.transcript = '';
    this.exitCode = undefined;
    this.child = spawn(process.execPath, [executable], {
      cwd: path.dirname(executable),
      env: { ...process.env, SOURCE_DATE_EPOCH: '0' },
      stdio: ['pipe', 'pipe', 'pipe'],
    });
    this.child.stdout.setEncoding('utf8');
    this.child.stderr.setEncoding('utf8');
    this.child.stdout.on('data', (chunk) => {
      this.transcript += chunk;
      process.stdout.write(chunk);
    });
    this.child.stderr.on('data', (chunk) => {
      this.transcript += chunk;
      process.stderr.write(chunk);
    });
    this.child.on('exit', (code) => {
      this.exitCode = code;
    });
  }

  send(text) {
    if (this.exitCode !== undefined) {
      throw new Error(`engine exited with ${this.exitCode}`);
    }
    this.child.stdin.write(`${text}\n`);
  }

  waitFor(pattern, checkpoint, timeoutMs = this.timeoutMs) {
    return new Promise((resolve, reject) => {
      const started = Date.now();
      const poll = () => {
        const output = this.transcript.slice(checkpoint);
        if (pattern.test(output)) {
          resolve(output);
          return;
        }
        if (this.exitCode !== undefined) {
          reject(
            new Error(
              `engine exited with ${this.exitCode} before ${pattern}; transcript:\n${this.transcript}`,
            ),
          );
          return;
        }
        if (Date.now() - started >= timeoutMs) {
          reject(new Error(`timeout waiting for ${pattern}; transcript:\n${this.transcript}`));
          return;
        }
        setTimeout(poll, 20);
      };
      poll();
    });
  }

  command(text, pattern, timeoutMs = this.timeoutMs) {
    const checkpoint = this.transcript.length;
    this.send(text);
    return this.waitFor(pattern, checkpoint, timeoutMs);
  }

  async setOption(name, value) {
    this.send(`setoption name ${name} value ${value}`);
    await this.command('isready', /(?:^|\n)readyok(?:\r?\n|$)/);
  }

  async search() {
    this.send('position startpos');
    return this.command('go nodes 64', /bestmove\s+\S+/);
  }

  async close() {
    if (this.exitCode !== undefined) return;
    this.send('quit');
    this.child.stdin.end();
    await new Promise((resolve, reject) => {
      const timer = setTimeout(() => {
        this.child.kill();
        reject(new Error('engine did not exit after quit'));
      }, 30_000);
      this.child.once('exit', (code) => {
        clearTimeout(timer);
        if (code === 0) resolve();
        else reject(new Error(`engine exited with ${code}`));
      });
    });
  }

  kill() {
    this.child.kill();
  }
}

function requireBackend(output, backend, label) {
  assert.match(output, /bestmove\s+(?!\(none\)|0000)\S+/, `${label} did not search`);
  assert.ok(
    output.includes('NNUE evaluation using') && output.includes(backend),
    `${label} did not report ${backend}: ${output}`,
  );
}

async function main() {
  const enginePath = fileOption('--engine');
  const legacyPath = fileOption('--legacy-net');
  const v2Path = fileOption('--v2-net');
  const v3Path = fileOption('--v3-net');
  const expectedLegacy = argument('--legacy-sha256').toLowerCase();
  const expectedV2 = argument('--v2-sha256').toLowerCase();
  const expectedV3 = argument('--v3-sha256').toLowerCase();
  const timeoutMs = Number(argument('--timeout-ms', '180000'));
  const maximumRssMiB = Number(argument('--max-rss-mib', '0'));
  const reloadCycles = Number(argument('--reload-cycles', '3'));
  assert.match(expectedLegacy, /^[0-9a-f]{64}$/);
  assert.match(expectedV2, /^[0-9a-f]{64}$/);
  assert.match(expectedV3, /^[0-9a-f]{64}$/);
  assert.ok(Number.isFinite(timeoutMs) && timeoutMs > 0);
  assert.ok(Number.isFinite(maximumRssMiB) && maximumRssMiB >= 0);
  assert.ok(Number.isInteger(reloadCycles) && reloadCycles >= 3);

  const [legacyBytes, v2Bytes, v3Bytes] = await Promise.all([
    readFile(legacyPath),
    readFile(v2Path),
    readFile(v3Path),
  ]);
  assert.equal(sha256(legacyBytes), expectedLegacy, 'Legacy V1 network hash mismatch');
  assert.equal(sha256(v2Bytes), expectedV2, 'AtomicNNUEV2 network hash mismatch');
  assert.equal(sha256(v3Bytes), expectedV3, 'AtomicNNUEV3 network hash mismatch');

  const manifest = JSON.parse(
    await readFile(path.join(path.dirname(enginePath), 'manifest.json'), 'utf8'),
  );
  assert.equal(manifest.schemaVersion, 2);
  assert.equal(manifest.initialMemoryBytes, 536870912);
  assert.equal(manifest.memoryGrowth, false);
  assert.equal(manifest.pthreadPoolSize, 4);
  assert.deepEqual(manifest.supportedNetworkBackends, [
    'Legacy Atomic V1',
    'AtomicNNUEV2',
    'AtomicNNUEV3',
  ]);
  assert.deepEqual(manifest.networkFileVersions, [
    '0x7AF32F20',
    '0xA70C0002',
    '0xA70C0003',
  ]);

  const temp = await mkdtemp(path.join(os.tmpdir(), 'atomic-wasm-v3-'));
  const engine = new UciProcess(enginePath, timeoutMs);
  const memory = new MemoryMonitor(engine.child.pid);
  let warmRss;
  let finalRss;
  try {
    await engine.waitFor(/Atomic-Stockfish/, 0);
    const uci = await engine.command('uci', /uciok/);
    assert.match(uci, /id name Atomic-Stockfish/);
    assert.match(uci, /option name Use NNUE type combo default true var false var true var pure/);
    await engine.setOption('Hash', '16');
    await engine.setOption('Threads', '4');

    await engine.setOption('EvalFile', uciPath(legacyPath));
    await engine.setOption('Use NNUE', 'true');
    requireBackend(await engine.search(), 'Legacy Atomic V1', 'initial Legacy V1 load');
    const legacyExport = path.join(temp, 'legacy-export.nnue');
    const legacyExportOutput = await engine.command(
      `export_net ${uciPath(legacyExport)}`,
      /Network saved successfully|Failed to export/,
    );
    assert.match(legacyExportOutput, /Network saved successfully/);
    assert.equal(sha256(await readFile(legacyExport)), expectedLegacy, 'Legacy export changed');

    await engine.setOption('EvalFile', uciPath(v2Path));
    requireBackend(await engine.search(), 'AtomicNNUEV2', 'initial V2 load');
    await engine.setOption('Use NNUE', 'pure');
    requireBackend(await engine.search(), 'AtomicNNUEV2', 'V2 pure mode');
    await engine.setOption('Use NNUE', 'true');

    const v2Export = path.join(temp, 'v2-export.nnue');
    const v2ExportOutput = await engine.command(
      `export_net ${uciPath(v2Export)}`,
      /Network saved successfully|Failed to export/,
    );
    assert.match(v2ExportOutput, /Network saved successfully/);
    assert.equal(sha256(await readFile(v2Export)), expectedV2, 'V2 export changed');

    await engine.setOption('EvalFile', uciPath(v3Path));
    requireBackend(await engine.search(), 'AtomicNNUEV3', 'initial V3 load');
    await engine.setOption('Use NNUE', 'pure');
    requireBackend(await engine.search(), 'AtomicNNUEV3', 'V3 pure mode');
    await engine.setOption('Use NNUE', 'true');

    const v3Export = path.join(temp, 'v3-export.nnue');
    const v3ExportOutput = await engine.command(
      `export_net ${uciPath(v3Export)}`,
      /Network saved successfully|Failed to export/,
    );
    assert.match(v3ExportOutput, /Network saved successfully/);
    assert.equal(sha256(await readFile(v3Export)), expectedV3, 'V3 export changed');
    await engine.setOption('EvalFile', uciPath(v3Export));
    requireBackend(await engine.search(), 'AtomicNNUEV3', 'V3 exported-byte reimport');

    await engine.setOption('EvalFile', uciPath(v2Path));
    requireBackend(await engine.search(), 'AtomicNNUEV2', 'V3 to V2 rollback baseline');

    const invalidV2 = path.join(temp, 'v2-trailing-byte.nnue');
    await writeFile(invalidV2, Buffer.concat([v2Bytes, Buffer.from([0xa5])]));
    await engine.setOption('EvalFile', uciPath(invalidV2));
    const rejection = await engine.search();
    assert.match(rejection, /bestmove \(none\)/);
    assert.match(
      rejection,
      /compatible Legacy Atomic V1, AtomicNNUEV2, or AtomicNNUEV3/,
    );

    const rollbackExport = path.join(temp, 'rollback-export.nnue');
    const rollbackOutput = await engine.command(
      `export_net ${uciPath(rollbackExport)}`,
      /Network saved successfully|Failed to export/,
    );
    assert.match(rollbackOutput, /Network saved successfully/);
    assert.equal(
      sha256(await readFile(rollbackExport)),
      expectedV2,
      'invalid reload mutated the active V2 backend',
    );

    await engine.setOption('EvalFile', uciPath(v2Path));
    requireBackend(await engine.search(), 'AtomicNNUEV2', 'V2 recovery');
    await engine.setOption('EvalFile', uciPath(legacyPath));
    requireBackend(await engine.search(), 'Legacy Atomic V1', 'V2 to V1 switch');
    await engine.setOption('EvalFile', uciPath(v2Path));
    requireBackend(await engine.search(), 'AtomicNNUEV2', 'V1 to V2 switch');
    await engine.setOption('EvalFile', uciPath(v3Path));
    requireBackend(await engine.search(), 'AtomicNNUEV3', 'V2 to V3 switch');

    await new Promise((resolve) => setTimeout(resolve, 150));
    warmRss = memory.sample();

    for (let cycle = 1; cycle <= reloadCycles; cycle += 1) {
      for (const threads of [1, 4, 2, 4]) {
        await engine.setOption('Threads', String(threads));
        await engine.setOption('EvalFile', uciPath(legacyPath));
        requireBackend(
          await engine.search(),
          'Legacy Atomic V1',
          `Legacy reload cycle=${cycle} with Threads=${threads}`,
        );
        await engine.setOption('EvalFile', uciPath(v2Path));
        requireBackend(
          await engine.search(),
          'AtomicNNUEV2',
          `V2 reload cycle=${cycle} with Threads=${threads}`,
        );
        await engine.setOption('EvalFile', uciPath(v3Path));
        requireBackend(
          await engine.search(),
          'AtomicNNUEV3',
          `V3 reload cycle=${cycle} with Threads=${threads}`,
        );
      }
    }

    await new Promise((resolve) => setTimeout(resolve, 250));
    finalRss = memory.sample();
    const configuredMaximum = maximumRssMiB > 0
      ? maximumRssMiB * MIB
      : manifest.initialMemoryBytes + 768 * MIB;
    if (process.platform === 'linux') {
      assert.ok(memory.samples > 0 && warmRss !== undefined && finalRss !== undefined);
      assert.ok(
        memory.peakBytes <= configuredMaximum,
        `WASM process-tree RSS peak ${Math.ceil(memory.peakBytes / MIB)} MiB exceeded ` +
          `${Math.ceil(configuredMaximum / MIB)} MiB`,
      );
      assert.ok(
        finalRss <= warmRss + 192 * MIB,
        `WASM process-tree RSS grew from ${Math.ceil(warmRss / MIB)} to ` +
          `${Math.ceil(finalRss / MIB)} MiB after repeated backend reloads`,
      );
    }

    memory.stop();
    await engine.close();
    console.log(
      `WASM three-backend NNUE gate: PASS ` +
        `(legacy=${expectedLegacy}, v2=${expectedV2}, v3=${expectedV3}, ` +
        `rssWarmMiB=${warmRss === undefined ? 'n/a' : Math.ceil(warmRss / MIB)}, ` +
        `rssFinalMiB=${finalRss === undefined ? 'n/a' : Math.ceil(finalRss / MIB)}, ` +
        `rssPeakMiB=${memory.peakBytes ? Math.ceil(memory.peakBytes / MIB) : 'n/a'}, ` +
        `reloadCycles=${reloadCycles})`,
    );
  } catch (error) {
    memory.stop();
    engine.kill();
    throw error;
  } finally {
    await rm(temp, { recursive: true, force: true });
  }
}

await main();
