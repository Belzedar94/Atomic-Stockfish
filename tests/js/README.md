# Atomic-Stockfish JavaScript and WebAssembly

This package exposes the Atomic-only rules API backed by the same C++ `Atomic::Board`
used by the native and Python bindings. It does not contain the search engine or an
NNUE network; those belong to the separate engine WASM target.

## Build and test

Docker builds use a digest-pinned Emscripten 4.0.10 image. Generated JavaScript and
WASM files live under `dist/` and are intentionally ignored by Git.

```sh
npm run build
npm test
npm run repro
```

`repro` performs two clean CommonJS and ES-module builds and requires all four
artifacts to be byte-identical.

## CommonJS

```js
const createAtomicStockfish = require('./dist/cjs/ffish.js');

const ffish = await createAtomicStockfish();
const board = new ffish.Board('atomic');
console.log(board.legalMoves());
board.delete();
```

## ES module

```js
import createAtomicStockfish from './dist/esm/ffish.mjs';

const ffish = await createAtomicStockfish();
const board = new ffish.Board('atomic', '', false);
console.log(board.perft(4));
board.delete();
```

Only `atomic` is accepted as a variant. Atomic960 is selected with the third Board
constructor argument. C++ validation failures are surfaced as JavaScript exceptions;
single invalid moves return `false`, while invalid bulk operations throw without
partially changing the board.
