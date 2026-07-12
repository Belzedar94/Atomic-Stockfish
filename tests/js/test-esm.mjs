#!/usr/bin/env node

import path from 'node:path';
import { fileURLToPath } from 'node:url';

import createAtomicStockfish from './dist/esm/ffish.mjs';
import suite from './test-suite.cjs';

const directory = path.dirname(fileURLToPath(import.meta.url));

try {
  const module = await createAtomicStockfish({
    locateFile(file) {
      return path.join(directory, 'dist', 'esm', file);
    },
  });
  await suite.runSuite(module, 'ES module/WASM');
} catch (error) {
  console.error(error);
  process.exitCode = 1;
}
