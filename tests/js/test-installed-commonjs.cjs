#!/usr/bin/env node

'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const PACKAGE = '@atomic-stockfish/ffish';
const sourceRoot = fs.realpathSync(process.env.ATOMIC_FORBIDDEN_SOURCE_ROOT);
const entry = fs.realpathSync(require.resolve(PACKAGE));
const cwd = fs.realpathSync(process.cwd());

function assertOutsideSource(candidate, label) {
  const relative = path.relative(sourceRoot, candidate);
  assert(
    relative.startsWith(`..${path.sep}`) || relative === '..' || path.isAbsolute(relative),
    `${label} unexpectedly resolved inside the source checkout: ${candidate}`,
  );
}

assertOutsideSource(cwd, 'smoke working directory');
assertOutsideSource(entry, 'CommonJS package entrypoint');

const packageRoot = path.resolve(path.dirname(entry), '..', '..');
const expectedPackageRoot = fs.realpathSync(
  path.join(cwd, 'node_modules', '@atomic-stockfish', 'ffish'),
);
assert.equal(fs.realpathSync(packageRoot), expectedPackageRoot);
const metadata = JSON.parse(fs.readFileSync(path.join(packageRoot, 'package.json'), 'utf8'));
assert.equal(metadata.name, PACKAGE);
assert.equal(metadata.version, '1.0.3');

const createAtomicStockfish = require(PACKAGE);

async function main() {
  const ffish = await createAtomicStockfish({
    locateFile(file) {
      return path.join(path.dirname(entry), file);
    },
  });

  assert.equal(ffish.info(), 'Atomic-Stockfish 1.0.3 JS/WASM');
  assert.equal(ffish.variants(), 'atomic');
  const liveBefore = ffish.debugLiveBoards();
  const board = new ffish.Board('atomic');
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
  console.log(`installed CommonJS package smoke passed: ${entry}`);
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
