#!/usr/bin/env node

import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

import createAtomicStockfish from '@atomic-stockfish/ffish';

const PACKAGE = '@atomic-stockfish/ffish';
const sourceRoot = fs.realpathSync(process.env.ATOMIC_FORBIDDEN_SOURCE_ROOT);
const entry = fs.realpathSync(fileURLToPath(import.meta.resolve(PACKAGE)));
const cwd = fs.realpathSync(process.cwd());

function assertOutsideSource(candidate, label) {
  const relative = path.relative(sourceRoot, candidate);
  assert(
    relative.startsWith(`..${path.sep}`) || relative === '..' || path.isAbsolute(relative),
    `${label} unexpectedly resolved inside the source checkout: ${candidate}`,
  );
}

assertOutsideSource(cwd, 'smoke working directory');
assertOutsideSource(entry, 'ES-module package entrypoint');

const packageRoot = path.resolve(path.dirname(entry), '..', '..');
const expectedPackageRoot = fs.realpathSync(
  path.join(cwd, 'node_modules', '@atomic-stockfish', 'ffish'),
);
assert.equal(fs.realpathSync(packageRoot), expectedPackageRoot);
const metadata = JSON.parse(fs.readFileSync(path.join(packageRoot, 'package.json'), 'utf8'));
assert.equal(metadata.name, PACKAGE);
assert.equal(metadata.version, '1.0.2');

try {
  const ffish = await createAtomicStockfish({
    locateFile(file) {
      return path.join(path.dirname(entry), file);
    },
  });

  assert.equal(ffish.info(), 'Atomic-Stockfish 1.0.2 JS/WASM');
  assert.equal(ffish.variants(), 'atomic');
  const liveBefore = ffish.debugLiveBoards();
  const board = new ffish.Board('atomic', '', true);
  try {
    assert.equal(board.perft(1), 20);
    assert.equal(board.push('e2e4'), true);
    board.pop();
    assert.equal(board.perft(1), 20);
  } finally {
    board.delete();
  }

  for (let index = 0; index < 128; index += 1) {
    const disposable = new ffish.Board('atomic');
    disposable.delete();
  }
  assert.equal(ffish.debugLiveBoards(), liveBefore);
  console.log(`installed ES-module package smoke passed: ${entry}`);
} catch (error) {
  console.error(error);
  process.exitCode = 1;
}
