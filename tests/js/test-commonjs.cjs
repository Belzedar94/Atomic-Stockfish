#!/usr/bin/env node

const path = require('node:path');
const createAtomicStockfish = require('./dist/cjs/ffish.js');
const { runSuite } = require('./test-suite.cjs');

async function main() {
  const module = await createAtomicStockfish({
    locateFile(file) {
      return path.join(__dirname, 'dist', 'cjs', file);
    },
  });
  await runSuite(module, 'CommonJS/WASM');
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
