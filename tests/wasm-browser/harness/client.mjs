// Test-side client of the Atomic-Stockfish worker protocol. The browser tests
// and benchmarks drive the worker through this class from the page's main
// thread, exactly as an analysis board would.

export class EngineClient {
  constructor(workerUrl) {
    this.worker = new Worker(workerUrl, { type: 'module' });
    this.messages = [];
    this.cursor = 0;
    this.waiters = new Set();
    this.closed = false;
    EngineClient.live.add(this);
    this.worker.onmessage = (event) => this.receive({ ...event.data, at: performance.now() });
    this.worker.onerror = (event) =>
      this.receive({ type: 'workerError', message: event.message ?? String(event), at: performance.now() });
  }

  receive(message) {
    this.messages.push(message);
    for (const waiter of [...this.waiters]) waiter();
  }

  send(message) {
    this.worker.postMessage(message);
  }

  command(command) {
    this.send({ type: 'command', command });
  }

  // Resolve with the first message after the cursor that satisfies `predicate`
  // and move the cursor past it.
  next(predicate, timeoutMs = 30000, label = 'message') {
    return new Promise((resolve, reject) => {
      const check = () => {
        for (let i = this.cursor; i < this.messages.length; ++i) {
          if (predicate(this.messages[i])) {
            this.cursor = i + 1;
            cleanup();
            resolve(this.messages[i]);
            return true;
          }
        }
        return false;
      };
      const timer = setTimeout(() => {
        cleanup();
        const tail = this.messages.slice(-12).map((m) => JSON.stringify(m)).join('\n');
        reject(new Error(`timeout waiting for ${label}; last messages:\n${tail}`));
      }, timeoutMs);
      const cleanup = () => {
        clearTimeout(timer);
        this.waiters.delete(check);
      };
      if (!check()) this.waiters.add(check);
    });
  }

  nextLine(pattern, timeoutMs, label = String(pattern)) {
    return this.next((m) => m.type === 'line' && pattern.test(m.line), timeoutMs, label);
  }

  linesSince(index) {
    return this.messages.slice(index).filter((m) => m.type === 'line');
  }

  async initialize(options, timeoutMs = 180000) {
    this.send({ type: 'initialize', ...options });
    const message = await this.next((m) => m.type === 'ready' || m.type === 'error', timeoutMs, 'ready');
    if (message.type === 'error') {
      const error = new Error(message.message);
      error.code = message.code;
      throw error;
    }
    return message;
  }

  async isready(timeoutMs = 30000) {
    this.command('isready');
    return this.nextLine(/^readyok$/, timeoutMs, 'readyok');
  }

  // Start a search; resolves when its bestmove has arrived.
  async search(command, timeoutMs = 120000) {
    const start = this.messages.length;
    this.command(command);
    const started = await this.next((m) => m.type === 'search', timeoutMs, 'search');
    const bestmove = await this.next(
      (m) => m.type === 'line' && m.searchId === started.searchId && m.line.startsWith('bestmove'),
      timeoutMs,
      `bestmove of search ${started.searchId}`,
    );
    const lines = this.messages
      .slice(start)
      .filter((m) => m.type === 'line' && m.searchId === started.searchId);
    return { searchId: started.searchId, startedAt: started.at, bestmove, lines };
  }

  async dispose(timeoutMs = 30000) {
    this.send({ type: 'dispose' });
    await this.next((m) => m.type === 'disposed', timeoutMs, 'disposed');
    this.terminate();
  }

  terminate() {
    this.worker.terminate();
    this.closed = true;
    EngineClient.live.delete(this);
  }
}

EngineClient.live = new Set();

// Parse a UCI info line into the fields an analysis board shows.
export function parseInfo(line) {
  if (!line.startsWith('info ') || line.startsWith('info string')) return null;
  const words = line.split(' ');
  const info = {};
  for (let i = 1; i < words.length; ++i) {
    const word = words[i];
    if (['depth', 'seldepth', 'multipv', 'nodes', 'nps', 'time', 'hashfull', 'tbhits'].includes(word))
      info[word] = Number(words[++i]);
    else if (word === 'score') {
      info.score = { kind: words[++i], value: Number(words[++i]) };
      if (words[i + 1] === 'lowerbound' || words[i + 1] === 'upperbound') info.score.bound = words[++i];
    } else if (word === 'wdl') {
      info.wdl = [Number(words[++i]), Number(words[++i]), Number(words[++i])];
    } else if (word === 'pv') {
      // A search stopped before its first iteration prints an empty pv.
      info.pv = words.slice(i + 1).filter(Boolean);
      break;
    }
  }
  return info;
}

window.EngineClient = EngineClient;
window.parseInfo = parseInfo;
window.harnessReady = true;
