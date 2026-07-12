#!/usr/bin/env node

import fs from 'node:fs';
import path from 'node:path';
import { createRequire } from 'node:module';
import { pathToFileURL } from 'node:url';

function fail(message) {
  throw new Error(message);
}

function parseArguments(argv) {
  const result = {};
  for (let index = 0; index < argv.length; index += 2) {
    const option = argv[index];
    const value = argv[index + 1];
    if (!option?.startsWith('--') || value === undefined) {
      fail(`expected --option value, got ${argv.slice(index).join(' ')}`);
    }
    if (Object.hasOwn(result, option)) fail(`duplicate option ${option}`);
    result[option] = value;
  }

  for (const option of ['--fixtures', '--ids', '--cjs', '--esm']) {
    if (!result[option]) fail(`missing required option ${option}`);
  }
  return result;
}

function requireFile(file, label) {
  const resolved = path.resolve(file);
  if (!fs.statSync(resolved, { throwIfNoEntry: false })?.isFile()) {
    fail(`${label} does not exist or is not a file: ${resolved}`);
  }
  return resolved;
}

function words(value) {
  if (!value) return [];
  return value.trim().split(/\s+/).sort();
}

function fixtureBoard(ffish, fixture) {
  let fen = fixture.fen;
  if (!fen && fixture.position === 'startpos') fen = '';
  if (!fen && fixture.position?.startsWith('fen ')) fen = fixture.position.slice(4);
  return new ffish.Board('atomic', fen, fixture.chess960 || false);
}

function pushFixture(board, fixture) {
  if (fixture.moves?.length) board.pushMoves(fixture.moves.join(' '));
}

function evaluateFixture(ffish, fixture) {
  const board = fixtureBoard(ffish, fixture);
  try {
    switch (fixture.probe) {
      case 'legal_moves':
        return words(board.legalMoves());
      case 'get_fen':
        pushFixture(board, fixture);
        return board.fen();
      case 'get_san':
        return board.sanMove(fixture.move);
      case 'gives_check':
        pushFixture(board, fixture);
        return board.isCheck();
      case 'is_capture':
        return board.isCapture(fixture.move);
      case 'game_result':
        pushFixture(board, fixture);
        return board.result();
      case 'is_immediate_game_end':
        pushFixture(board, fixture);
        return board.result();
      case 'is_optional_game_end':
        pushFixture(board, fixture);
        return board.result(true);
      case 'perft':
        return board.perft(fixture.depth);
      default:
        fail(`unsupported parity probe ${fixture.probe} in ${fixture.id}`);
    }
  } finally {
    board.delete();
  }
}

function evaluateSurface(ffish, fixtures) {
  return Object.fromEntries(
    fixtures.map((fixture) => [fixture.id, evaluateFixture(ffish, fixture)]),
  );
}

async function loadCommonJS(file, wasm) {
  const require = createRequire(import.meta.url);
  const imported = require(file);
  const factory = imported.default || imported;
  if (typeof factory !== 'function') fail(`CommonJS artifact has no module factory: ${file}`);
  return factory({
    locateFile(name) {
      return name.endsWith('.wasm') ? wasm : path.join(path.dirname(file), name);
    },
  });
}

async function loadEsm(file, wasm) {
  const imported = await import(pathToFileURL(file).href);
  if (typeof imported.default !== 'function') {
    fail(`ES module artifact has no default factory: ${file}`);
  }
  return imported.default({
    locateFile(name) {
      return name.endsWith('.wasm') ? wasm : path.join(path.dirname(file), name);
    },
  });
}

async function main() {
  const options = parseArguments(process.argv.slice(2));
  const fixtureFile = requireFile(options['--fixtures'], 'fixture corpus');
  const cjsFile = requireFile(options['--cjs'], 'CommonJS artifact');
  const esmFile = requireFile(options['--esm'], 'ES module artifact');
  const cjsWasm = requireFile(path.join(path.dirname(cjsFile), 'ffish.wasm'), 'CommonJS WASM');
  const esmWasm = requireFile(path.join(path.dirname(esmFile), 'ffish.wasm'), 'ES module WASM');

  const requestedIds = options['--ids'].split(',').filter(Boolean);
  if (!requestedIds.length) fail('the selected fixture id list is empty');
  if (new Set(requestedIds).size !== requestedIds.length) {
    fail('the selected fixture id list has duplicates');
  }

  const corpus = JSON.parse(fs.readFileSync(fixtureFile, 'utf8')).fixtures;
  const byId = new Map(corpus.map((fixture) => [fixture.id, fixture]));
  const missingIds = requestedIds.filter((id) => !byId.has(id));
  if (missingIds.length) fail(`unknown fixture ids: ${missingIds.join(', ')}`);
  const fixtures = requestedIds.map((id) => byId.get(id));

  const commonjs = await loadCommonJS(cjsFile, cjsWasm);
  const esm = await loadEsm(esmFile, esmWasm);
  process.stdout.write(`${JSON.stringify({
    commonjs: evaluateSurface(commonjs, fixtures),
    esm: evaluateSurface(esm, fixtures),
  })}\n`);
}

main().catch((error) => {
  console.error(error?.stack || error);
  process.exitCode = 1;
});
