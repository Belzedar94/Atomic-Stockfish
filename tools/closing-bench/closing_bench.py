#!/usr/bin/env python3
"""Closing-power bench for Atomic-Stockfish.

How many open AtomicDB positions does ONE search of an engine close, and after
how many nodes?  The set (positions.tsv) is 500 real positions that an older
engine searched once at the production budget and left open; each is searched
here the way an AtomicDB worker searches it: 1 thread, MultiPV 1, a clean hash
table, ``go nodes N mate 250`` (stop at the first mate).

Everything is counted in nodes, never in time, so a run is reproducible to the
node on any machine and with any number of engines side by side.  A run at
128M also contains the answer for every smaller budget: a position closed
after 5M nodes would have closed with a budget of 8M and not with one of 4M.

    closing_bench.py run    --engine E.exe --out results/NAME.jsonl [--nodes 128e6] [--jobs 6]
    closing_bench.py report results/A.jsonl [results/B.jsonl ...]
    closing_bench.py compare results/A.jsonl results/B.jsonl [--at 128e6]

``run`` can be stopped and started again: it skips what the file already has.
"""
import argparse
import csv
import hashlib
import json
import math
import os
import queue
import subprocess
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
MATE_STOP_MOVES = 250          # the worker's: "stop at any mate"
TB_WIN_CP = 19000              # a tablebase win reported inside the search
STEPS = [1e6, 8e6, 32e6, 128e6]
PRIORITY = {'normal': 0, 'below': 0x4000, 'idle': 0x40}


# ----------------------------------------------------------- housekeeping ---

def sha256_of(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as handle:
        for block in iter(lambda: handle.read(1 << 20), b''):
            digest.update(block)
    return digest.hexdigest()


_job = None


def tie_to_me(proc):
    """Windows: make ``proc`` die with this process, however this one ends."""
    global _job
    if os.name != 'nt':
        return
    import ctypes
    from ctypes import wintypes
    k32 = ctypes.WinDLL('kernel32', use_last_error=True)
    if _job is None:
        class Basic(ctypes.Structure):
            _fields_ = [('PerProcessUserTimeLimit', ctypes.c_int64),
                        ('PerJobUserTimeLimit', ctypes.c_int64),
                        ('LimitFlags', wintypes.DWORD),
                        ('MinimumWorkingSetSize', ctypes.c_size_t),
                        ('MaximumWorkingSetSize', ctypes.c_size_t),
                        ('ActiveProcessLimit', wintypes.DWORD),
                        ('Affinity', ctypes.c_size_t),
                        ('PriorityClass', wintypes.DWORD),
                        ('SchedulingClass', wintypes.DWORD)]

        class Extended(ctypes.Structure):
            _fields_ = [('Basic', Basic),
                        ('Io', ctypes.c_uint64 * 6),
                        ('ProcessMemoryLimit', ctypes.c_size_t),
                        ('JobMemoryLimit', ctypes.c_size_t),
                        ('PeakProcessMemoryUsed', ctypes.c_size_t),
                        ('PeakJobMemoryUsed', ctypes.c_size_t)]

        k32.CreateJobObjectW.restype = wintypes.HANDLE
        k32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        k32.SetInformationJobObject.argtypes = [
            wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
        k32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        job = k32.CreateJobObjectW(None, None)
        info = Extended()
        info.Basic.LimitFlags = 0x2000          # KILL_ON_JOB_CLOSE
        if not job or not k32.SetInformationJobObject(
                job, 9, ctypes.byref(info), ctypes.sizeof(info)):
            return
        _job = job
    k32.AssignProcessToJobObject(_job, int(proc._handle))


def single_instance(path):
    """Hold a lock on ``path`` for the life of the process; False if taken."""
    handle = open(path, 'a+')
    try:
        if os.name == 'nt':
            import msvcrt
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        handle.close()
        return None
    return handle


# ----------------------------------------------------------------- engine ---

class Engine:
    def __init__(self, path, hash_mb, syzygy, priority, multipv=1):
        self.p = subprocess.Popen(
            [path], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, text=True, bufsize=1,
            cwd=os.path.dirname(os.path.abspath(path)),
            creationflags=PRIORITY[priority] if os.name == 'nt' else 0)
        tie_to_me(self.p)
        self.send('uci')
        self.wait('uciok')
        self.send('setoption name Threads value 1')
        self.send('setoption name Hash value %d' % hash_mb)
        self.send('setoption name MultiPV value %d' % multipv)
        if syzygy:
            self.send('setoption name SyzygyPath value ' + syzygy)
        self.send('isready')
        self.wait('readyok')

    def send(self, text):
        self.p.stdin.write(text + '\n')
        self.p.stdin.flush()

    def wait(self, token):
        while True:
            line = self.p.stdout.readline()
            if not line:
                raise RuntimeError('engine died')
            if line.startswith(token):
                return line

    def search(self, fen, nodes):
        """One search from a clean table.  Scores are the side to move's."""
        self.send('ucinewgame')
        self.send('isready')
        self.wait('readyok')
        self.send('position fen ' + fen)
        self.send('go nodes %d mate %d' % (nodes, MATE_STOP_MOVES))
        last, used, tb_since = None, 0, None
        while True:
            line = self.p.stdout.readline()
            if not line:
                raise RuntimeError('engine died')
            if line.startswith('bestmove'):
                break
            if not line.startswith('info') or ' score ' not in line:
                continue
            t = line.split()
            here = int(t[t.index('nodes') + 1]) if 'nodes' in t else used
            used = max(used, here)
            if 'lowerbound' in t or 'upperbound' in t or 'pv' not in t:
                continue
            # With several lines only the best one counts: it is the one a
            # mate closes the position with, and the one the search stops on.
            if 'multipv' in t and t[t.index('multipv') + 1] != '1':
                continue
            i = t.index('score')
            kind, value = t[i + 1], int(t[i + 2])
            last = {'kind': kind, 'value': value,
                    'depth': int(t[t.index('depth') + 1]),
                    'pv': t[t.index('pv') + 1:]}
            # The node count from which the score has been a tablebase win
            # without a break.
            if kind == 'cp' and value >= TB_WIN_CP:
                tb_since = tb_since or here
            else:
                tb_since = None
        return last, used, tb_since

    def quit(self):
        try:
            self.send('quit')
            self.p.wait(timeout=5)
        except Exception:
            self.p.kill()


def pv_wins(fen, pv):
    """Does the line end in a win for the side to move?  None without python-chess."""
    try:
        import chess
        import chess.variant
    except ImportError:
        return None
    board = chess.variant.AtomicBoard(fen)
    mover = board.turn
    for uci in pv:
        try:
            move = chess.Move.from_uci(uci)
        except ValueError:
            return False
        if move not in board.legal_moves:
            return False
        board.push(move)
    outcome = board.outcome()
    return outcome is not None and outcome.winner == mover


def judge(last, tb_since):
    if last is None:
        return 'open'
    if last['kind'] == 'mate':
        return 'mate' if last['value'] > 0 else 'mated'
    if last['value'] >= TB_WIN_CP and tb_since:
        return 'tb'
    if last['value'] <= -TB_WIN_CP:
        return 'tb_loss'
    return 'open'


# -------------------------------------------------------------------- run ---

def load_set(path):
    with open(path, encoding='utf-8') as handle:
        return list(csv.DictReader(handle, delimiter='\t'))


def read_results(path):
    header, rows = None, {}
    if not os.path.exists(path):
        return header, rows
    with open(path, encoding='utf-8') as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            if 'bench' in row:
                header = row
            else:
                rows[row['id']] = row
    cap = header.get('nodes') if header else None
    for row in rows.values():
        # The engine checks its node limit every few hundred nodes, so a
        # search can end on a mate just past the budget.  It closed the
        # position with that budget.
        if cap and row['result'] in ('mate', 'tb') and row['nodes'] > cap:
            row['nodes'] = cap
    return header, rows


def cmd_run(a):
    nodes = int(float(a.nodes))
    positions = load_set(a.set)
    if a.limit:
        step = max(1, len(positions) // a.limit)
        positions = positions[::step][:a.limit]
    if a.open_in:
        # Only what another run left open: to price a bigger budget without
        # searching again what the smaller one already closed.
        _, before = read_results(a.open_in)
        positions = [p for p in positions if p['id'] in before
                     and before[p['id']]['result'] not in ('mate', 'tb')]
    header = {'bench': 'closing-power', 'set': os.path.basename(a.set),
              'set_sha256': sha256_of(a.set), 'engine': os.path.abspath(a.engine),
              'engine_sha256': sha256_of(a.engine), 'nodes': nodes,
              'hash': a.hash, 'syzygy': a.syzygy or '', 'multipv': a.multipv}
    if a.open_in:
        header['open_in'] = os.path.basename(a.open_in)
    os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
    lock = single_instance(a.out + '.lock')
    if lock is None:
        sys.exit('another run is writing %s' % a.out)
    old, done = read_results(a.out)
    if old is not None:
        old.setdefault('multipv', 1)      # files from before the option
        for field in ('set_sha256', 'engine_sha256', 'nodes', 'hash', 'syzygy',
                      'multipv', 'open_in'):
            if old.get(field) != header.get(field):
                sys.exit('%s was made with another %s: %r, now %r'
                         % (a.out, field, old.get(field), header.get(field)))
    else:
        with open(a.out, 'a', encoding='utf-8') as out:
            out.write(json.dumps(dict(header, started=time.strftime(
                '%Y-%m-%d %H:%M:%S'))) + '\n')
    todo = queue.Queue()
    for row in positions:
        if row['id'] not in done:
            todo.put(row)
    total, left = len(positions), todo.qsize()
    print('%d positions, %d already done, %d to search at %s nodes with %d engines'
          % (total, total - left, left, format(nodes, ','), a.jobs), flush=True)
    deadline = time.time() + a.max_hours * 3600
    write = threading.Lock()
    state = {'done': total - left, 'closed': sum(
        r['result'] in ('mate', 'tb') for r in done.values()), 'nodes': 0}
    engines = []

    def work():
        engine = Engine(a.engine, a.hash, a.syzygy, a.priority, a.multipv)
        engines.append(engine)
        while time.time() < deadline:
            try:
                row = todo.get_nowait()
            except queue.Empty:
                break
            started = time.time()
            last, used, tb_since = engine.search(row['fen'], nodes)
            result = judge(last, tb_since)
            record = {'id': row['id'], 'band': row['band'], 'where': row['where'],
                      'value': int(row['value']), 'result': result,
                      'nodes': (min(tb_since if result == 'tb' else used, nodes)
                                if result in ('mate', 'tb') else used),
                      'searched': used,
                      'score': '%s %d' % (last['kind'], last['value']) if last else '',
                      'depth': last['depth'] if last else 0,
                      'move': last['pv'][0] if last and last['pv'] else '',
                      'secs': round(time.time() - started, 1)}
            if result == 'mate':
                record['pv_wins'] = pv_wins(row['fen'], last['pv'])
            with write:
                with open(a.out, 'a', encoding='utf-8') as out:
                    out.write(json.dumps(record) + '\n')
                state['done'] += 1
                state['nodes'] += used
                state['closed'] += result in ('mate', 'tb')
                if state['done'] % 10 == 0 or state['done'] == total:
                    print('%d/%d  closed %d  %.2f Bn nodes this run'
                          % (state['done'], total, state['closed'],
                             state['nodes'] / 1e9), flush=True)
        engine.quit()

    threads = [threading.Thread(target=work, daemon=True) for _ in range(a.jobs)]
    try:
        for thread in threads:
            thread.start()
        for thread in threads:
            while thread.is_alive():
                thread.join(1.0)
    finally:
        for engine in engines:
            if engine.p.poll() is None:
                engine.p.kill()
    if not todo.empty():
        print('stopped at the %.1f hour cap with %d positions left; run again '
              'to go on' % (a.max_hours, todo.qsize()), flush=True)
    lock.close()


# ----------------------------------------------------------------- report ---

def closed_by(row, budget):
    return row['result'] in ('mate', 'tb') and row['nodes'] <= budget


def spent_by(row, budget):
    """Nodes the search would have used had it been stopped at `budget`."""
    if closed_by(row, budget):
        return row['nodes']
    return min(row['searched'], budget)


def ladder(cap):
    steps, b = [], 1e6
    while b < cap:
        steps.append(b)
        b *= 2
    return steps + [cap]


def half_point(rows, budget):
    """The value (in pawns) at which half the positions close: a logistic fit
    of closed-or-not on ln(value).  None when the data does not bend."""
    xs = [math.log(max(r['value'], 1) / 100.0) for r in rows]
    ys = [1.0 if closed_by(r, budget) else 0.0 for r in rows]
    if not 0 < sum(ys) < len(ys):
        return None
    a = b = 0.0
    for _ in range(60):
        g0 = g1 = h00 = h01 = h11 = 0.0
        for x, y in zip(xs, ys):
            p = 1.0 / (1.0 + math.exp(-max(-30.0, min(30.0, a + b * x))))
            w = p * (1.0 - p)
            g0 += y - p
            g1 += (y - p) * x
            h00 += w
            h01 += w * x
            h11 += w * x * x
        det = h00 * h11 - h01 * h01
        if det < 1e-12:
            return None
        da = (h11 * g0 - h01 * g1) / det
        db = (h00 * g1 - h01 * g0) / det
        a, b = a + da, b + db
        if abs(da) + abs(db) < 1e-9:
            break
    if b <= 0:
        return None
    return math.exp(-a / b)


def bands_of(rows):
    seen = []
    for r in sorted(rows, key=lambda r: r['value']):
        if r['band'] not in seen:
            seen.append(r['band'])
    return seen


def short(n):
    return '%gM' % (n / 1e6)


def cmd_report(a):
    for path in a.results:
        header, rows = read_results(path)
        rows = list(rows.values())
        cap = header['nodes']
        steps = [s for s in STEPS if s < cap] + [cap]
        print('\n%s' % path)
        print('engine %s  sha256 %s  hash %s MB  lines %s  cap %s  positions %d'
              % (os.path.basename(header['engine']), header['engine_sha256'][:12],
                 header['hash'], header.get('multipv', 1), short(cap), len(rows)))
        print('%-8s %4s  %s  %s' % ('band', 'n', '  '.join(
            '%7s' % ('<=' + short(s)) for s in steps), 'mated'))
        for band in bands_of(rows) + ['all']:
            part = [r for r in rows if band == 'all' or r['band'] == band]
            cells = []
            for s in steps:
                k = sum(closed_by(r, s) for r in part)
                cells.append('%3d %3.0f%%' % (k, 100.0 * k / max(len(part), 1)))
            lost = sum(r['result'] in ('mated', 'tb_loss') for r in part)
            print('%-8s %4d  %s  %5d' % (band, len(part), '  '.join(cells), lost))
        mates = [r for r in rows if r['result'] == 'mate']
        print('by tablebase: %d   mates whose printed line reaches the win: %d of %d'
              % (sum(r['result'] == 'tb' for r in rows),
                 sum(bool(r.get('pv_wins')) for r in mates), len(mates)))
        for where in ('tree', 'rest'):
            part = [r for r in rows if r['where'] == where]
            if part:
                k = sum(closed_by(r, cap) for r in part)
                print('%s: %d of %d (%.0f%%)' % (where, k, len(part),
                                                 100.0 * k / len(part)))
        print('nodes spent: %.1f Bn   engine time: %.1f h'
              % (sum(r['searched'] for r in rows) / 1e9,
                 sum(r['secs'] for r in rows) / 3600))
        for s in steps:
            half = half_point(rows, s)
            print('half of the positions close at %s from: %s'
                  % (short(s), '+%.1f' % half if half else 'no fit'))
        print('price of a closure, had every search stopped at the budget '
              '(M nodes):')
        print('%7s %7s %9s %12s %15s' % ('budget', 'closed', 'spent Bn',
                                         'per closure', 'per extra one'))
        was_k = was_n = 0
        for s in ladder(cap):
            k = sum(closed_by(r, s) for r in rows)
            n = sum(spent_by(r, s) for r in rows)
            each = '%.0f' % (n / k / 1e6) if k else '-'
            extra = '%.0f' % ((n - was_n) / (k - was_k) / 1e6) if k > was_k else '-'
            print('%7s %7d %9.2f %12s %15s' % (short(s), k, n / 1e9, each, extra))
            was_k, was_n = k, n


def sign_test(b, c):
    """Two-sided exact probability of a split at least this uneven."""
    n = b + c
    if n == 0:
        return 1.0
    k = min(b, c)
    tail = sum(math.comb(n, i) for i in range(k + 1)) / 2.0 ** n
    return min(1.0, 2.0 * tail)


def cmd_compare(a):
    ha, ra = read_results(a.results[0])
    hb, rb = read_results(a.results[1])
    budget = float(a.at) if a.at else min(ha['nodes'], hb['nodes'])
    if budget > min(ha['nodes'], hb['nodes']):
        sys.exit('one of the runs stops before %s' % short(budget))
    ids = sorted(set(ra) & set(rb))
    skip = tuple(s for s in a.skip_left_by.split(',') if s)
    if skip:
        # An engine cannot be judged on the positions it left open itself:
        # drop the ones whose production search was by one of these binaries.
        left_by = {r['id']: r['engine_sha'] for r in load_set(a.set)}
        ids = [i for i in ids if not left_by.get(i, '').startswith(skip)]
    print('A = %s\nB = %s\nat %s nodes, %d positions in both%s'
          % (a.results[0], a.results[1], short(budget), len(ids),
             ' (without those left open by %s)' % ', '.join(skip) if skip else ''))
    print('%-8s %4s %5s %6s %6s %7s' % ('band', 'n', 'both', 'only A', 'only B', 'neither'))
    rows = [(ra[i], rb[i]) for i in ids]
    tot_b = tot_c = 0
    for band in bands_of([x for x, _ in rows]) + ['all']:
        part = [(x, y) for x, y in rows if band == 'all' or x['band'] == band]
        both = sum(closed_by(x, budget) and closed_by(y, budget) for x, y in part)
        only_a = sum(closed_by(x, budget) and not closed_by(y, budget) for x, y in part)
        only_b = sum(closed_by(y, budget) and not closed_by(x, budget) for x, y in part)
        print('%-8s %4d %5d %6d %6d %7d' % (band, len(part), both, only_a, only_b,
                                            len(part) - both - only_a - only_b))
        if band == 'all':
            tot_b, tot_c = only_a, only_b
    print('B closes %+d more than A; a split this uneven by chance: p = %.3f'
          % (tot_c - tot_b, sign_test(tot_b, tot_c)))
    ratios = [math.log(y['nodes'] / x['nodes']) for x, y in rows
              if closed_by(x, budget) and closed_by(y, budget)
              and x['nodes'] > 0 and y['nodes'] > 0]
    if ratios:
        mean = sum(ratios) / len(ratios)
        sd = (sum((v - mean) ** 2 for v in ratios) / max(len(ratios) - 1, 1)) ** 0.5
        err = sd / len(ratios) ** 0.5
        print('where both close (%d): B needs x%.2f the nodes of A '
              '(x%.2f to x%.2f)' % (len(ratios), math.exp(mean),
                                    math.exp(mean - 1.96 * err),
                                    math.exp(mean + 1.96 * err)))


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    sub = ap.add_subparsers(dest='cmd', required=True)
    run = sub.add_parser('run')
    run.add_argument('--engine', required=True)
    run.add_argument('--out', required=True)
    run.add_argument('--set', default=os.path.join(HERE, 'positions.tsv'))
    run.add_argument('--nodes', default='128e6')
    run.add_argument('--jobs', type=int, default=6)
    run.add_argument('--hash', type=int, default=256)
    run.add_argument('--multipv', type=int, default=1,
                     help='lines per search; only the best one decides')
    run.add_argument('--syzygy', default=os.environ.get('SYZYGY', ''))
    run.add_argument('--priority', choices=sorted(PRIORITY), default='below')
    run.add_argument('--max-hours', type=float, default=8.0)
    run.add_argument('--limit', type=int, default=0,
                     help='only this many positions, spread over the set')
    run.add_argument('--open-in', default='',
                     help='only the positions this results file left open')
    run.set_defaults(func=cmd_run)
    rep = sub.add_parser('report')
    rep.add_argument('results', nargs='+')
    rep.set_defaults(func=cmd_report)
    cmp_ = sub.add_parser('compare')
    cmp_.add_argument('results', nargs=2)
    cmp_.add_argument('--at', default='')
    cmp_.add_argument('--set', default=os.path.join(HERE, 'positions.tsv'))
    cmp_.add_argument('--skip-left-by', default='',
                      help='sha256 prefixes, comma separated: leave out the '
                           'positions those binaries left open in production')
    cmp_.set_defaults(func=cmd_compare)
    a = ap.parse_args()
    a.func(a)


if __name__ == '__main__':
    main()
