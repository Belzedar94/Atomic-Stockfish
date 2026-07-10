#!/usr/bin/env node

const assert = require('node:assert/strict');
const crypto = require('node:crypto');
const fs = require('node:fs');
const path = require('node:path');

function filesBelow(root) {
  const result = [];
  for (const entry of fs.readdirSync(root, { withFileTypes: true })) {
    const absolute = path.join(root, entry.name);
    if (entry.isDirectory()) {
      for (const child of filesBelow(absolute)) result.push(path.join(entry.name, child));
    } else {
      result.push(entry.name);
    }
  }
  return result.sort();
}

function digest(file) {
  return crypto.createHash('sha256').update(fs.readFileSync(file)).digest('hex');
}

const [first, second] = process.argv.slice(2).map((value) => path.resolve(value));
if (!first || !second) {
  console.error('usage: node check-reproducible.cjs first-build second-build');
  process.exit(2);
}

const firstFiles = filesBelow(first);
const secondFiles = filesBelow(second);
assert.deepEqual(secondFiles, firstFiles, 'artifact lists differ');
for (const relative of firstFiles) {
  assert.equal(
    digest(path.join(second, relative)),
    digest(path.join(first, relative)),
    `${relative} is not reproducible`,
  );
}
console.log(`reproducible WASM artifacts: ${firstFiles.length} files`);
