// Static server for the browser tests, benchmarks and local experiments.
//
//   node tests/wasm-browser/serve.mjs --engine build/wasm-browser --nets <dir> [--port 8080]
//
// Every response carries the headers a cross-origin isolated page needs
// (COOP same-origin, COEP require-corp, CORP same-origin). Paths under /plain/
// are served without COOP/COEP, to test the rejection of non-isolated pages.
// Paths under /nolength/ are streamed without Content-Length.

import { createReadStream } from 'node:fs';
import { stat } from 'node:fs/promises';
import http from 'node:http';
import path from 'node:path';
import process from 'node:process';
import { fileURLToPath, pathToFileURL } from 'node:url';

const HERE = path.dirname(fileURLToPath(import.meta.url));

const TYPES = {
  '.html': 'text/html; charset=utf-8',
  '.js': 'text/javascript; charset=utf-8',
  '.mjs': 'text/javascript; charset=utf-8',
  '.json': 'application/json; charset=utf-8',
  '.wasm': 'application/wasm',
  '.nnue': 'application/octet-stream',
};

export function startServer({ mounts, port = 0, host = '127.0.0.1' }) {
  const roots = Object.entries(mounts).map(([prefix, dir]) => [prefix, path.resolve(dir)]);
  const server = http.createServer(async (request, response) => {
    let url = decodeURIComponent(new URL(request.url, 'http://localhost').pathname);
    let isolate = true;
    let noLength = false;
    if (url.startsWith('/plain/')) {
      isolate = false;
      url = url.slice('/plain'.length);
    } else if (url.startsWith('/nolength/')) {
      noLength = true;
      url = url.slice('/nolength'.length);
    }
    const headers = {
      'Cache-Control': 'no-store',
      'Cross-Origin-Resource-Policy': 'same-origin',
    };
    if (isolate) {
      headers['Cross-Origin-Opener-Policy'] = 'same-origin';
      headers['Cross-Origin-Embedder-Policy'] = 'require-corp';
    }
    const mount = roots
      .filter(([prefix]) => url === prefix || url.startsWith(prefix.endsWith('/') ? prefix : prefix + '/'))
      .sort((a, b) => b[0].length - a[0].length)[0];
    if (!mount) {
      response.writeHead(404, headers).end('not found');
      return;
    }
    const [prefix, root] = mount;
    const file = path.resolve(root, '.' + url.slice(prefix.length).replace(/^\/?/, '/'));
    if (file !== root && !file.startsWith(root + path.sep)) {
      response.writeHead(403, headers).end('forbidden');
      return;
    }
    let info;
    try {
      info = await stat(file);
    } catch {
      response.writeHead(404, headers).end('not found');
      return;
    }
    if (!info.isFile()) {
      response.writeHead(404, headers).end('not found');
      return;
    }
    headers['Content-Type'] = TYPES[path.extname(file)] ?? 'application/octet-stream';
    if (!noLength) headers['Content-Length'] = String(info.size);
    response.writeHead(200, headers);
    if (request.method === 'HEAD') {
      response.end();
      return;
    }
    createReadStream(file).pipe(response);
  });
  return new Promise((resolve) => {
    server.listen(port, host, () => {
      const address = server.address();
      resolve({ server, origin: `http://${host}:${address.port}` });
    });
  });
}

function argument(name, fallback) {
  const index = process.argv.indexOf(name);
  return index === -1 ? fallback : process.argv[index + 1];
}

if (import.meta.url === pathToFileURL(process.argv[1] ?? '').href) {
  const mounts = {
    '/': path.join(HERE, 'harness'),
    '/engine': argument('--engine', path.join(HERE, '..', '..', 'build', 'wasm-browser')),
  };
  const nets = argument('--nets');
  if (nets) mounts['/nets'] = nets;
  const { origin } = await startServer({ mounts, port: Number(argument('--port', 8080)) });
  console.log(`serving on ${origin}`);
}
