#!/usr/bin/env python3
"""Server side, read-only: candidates for the closing-power bench.

A candidate is an OPEN position (status UNKNOWN), White to move, that the tree
values between +10 and +90 for White, searched exactly once at about 128M
nodes BEFORE the cutoff date and never since.  That is: a position an older
engine looked at with the production budget and left open.  No engine released
after the cutoff has been tried on it, so none of them is the one that
"selected" the set.

Prints one TSV row per candidate; the stratified draw is done locally
(draw_set.py) so it can be repeated without touching the server.

Usage (as the postgres user, with the OpenBench virtualenv):
    cat /var/lib/atomicdb/sweep16.json | \
      sudo -u postgres nice -n 10 /opt/openbench/.venv/bin/python \
      sample_open_positions.py 6 20261114 2026-11-02 > candidates.tsv

Arguments: sample_pct  seed  cutoff_date.  The sweep's list on stdin is
optional; with it each row says whether the key is in White's strategy tree
(only known for +16 and above).
"""
import json
import sys
import time

import psycopg

PCT = float(sys.argv[1]) if len(sys.argv) > 1 else 6.0
SEED = int(sys.argv[2]) if len(sys.argv) > 2 else 20261114
CUTOFF = sys.argv[3] if len(sys.argv) > 3 else '2026-11-02'
LOW, HIGH = 1000, 9000
MIN_NODES, MAX_NODES = 100_000_000, 141_000_000

tree, rest = set(), set()
if not sys.stdin.isatty():
    raw = sys.stdin.read()
    if raw.strip():
        plan = json.loads(raw)
        cut = int(plan.get('tree_count', 0))
        tree = set(plan['keys'][:cut])
        rest = set(plan['keys'][cut:])

conn = psycopg.connect('dbname=atomicdb', autocommit=True)
conn.read_only = True
cur = conn.cursor()
cur.execute('SET statement_timeout=280000')
t0 = time.time()

cur.execute(
    "SELECT key, fen, eval_cp, backed_eval, nodes_invested, depth_invested, "
    "reachable, expanded FROM atomicdb_position "
    "TABLESAMPLE SYSTEM (%s) REPEATABLE (%s) "
    "WHERE status = 'UNKNOWN' AND visits = 1 "
    "AND nodes_invested BETWEEN %s AND %s AND mate_in IS NULL "
    "AND split_part(fen, ' ', 2) = 'w' "
    "AND coalesce(backed_eval, eval_cp) >= %s "
    "AND coalesce(backed_eval, eval_cp) < %s"
    % (PCT, SEED, MIN_NODES, MAX_NODES, LOW, HIGH))
rows = cur.fetchall()
sys.stderr.write('sampled %d rows in %.1fs\n' % (len(rows), time.time() - t0))

info = {}
keys = [r[0] for r in rows]
for i in range(0, len(keys), 2000):
    cur.execute(
        "SELECT position_id, "
        "count(*) FILTER (WHERE state = 'COMPLETED' AND nodes_searched > 0), "
        "max(completed) FILTER (WHERE state = 'COMPLETED' AND nodes_searched > 0), "
        "(array_agg(engine_sha ORDER BY completed DESC) "
        "   FILTER (WHERE state = 'COMPLETED' AND nodes_searched > 0))[1], "
        "(array_agg(multipv ORDER BY completed DESC) "
        "   FILTER (WHERE state = 'COMPLETED' AND nodes_searched > 0))[1], "
        "count(*) FILTER (WHERE state IN ('PENDING', 'LEASED')) "
        "FROM atomicdb_analysistask WHERE position_id = ANY(%s) GROUP BY 1",
        (keys[i:i + 2000],))
    for r in cur.fetchall():
        info[r[0]] = r[1:]

print('\t'.join(['key', 'value', 'eval_cp', 'backed_eval', 'nodes_invested',
                 'depth', 'pieces', 'reachable', 'expanded', 'where',
                 'searches', 'last_search', 'engine_sha', 'multipv',
                 'waiting', 'fen']))
kept = 0
for key, fen, ev, backed, nodes, depth, reach, expanded in rows:
    n, last, sha, mpv, waiting = info.get(key, (0, None, '', None, 0))
    if n != 1 or last is None or last.date().isoformat() >= CUTOFF:
        continue
    pieces = sum(c.isalpha() for c in fen.split(' ')[0])
    if pieces <= 6:
        continue
    where = 'tree' if key in tree else ('rest' if key in rest else 'na')
    value = backed if backed is not None else ev
    print('\t'.join(str(x) for x in [
        key, value, ev, '' if backed is None else backed, nodes, depth,
        pieces, int(bool(reach)), int(bool(expanded)), where, n,
        last.date().isoformat(), (sha or '')[:12], mpv, waiting, fen]))
    kept += 1
sys.stderr.write('candidates %d of %d sampled, %.1fs\n'
                 % (kept, len(rows), time.time() - t0))
