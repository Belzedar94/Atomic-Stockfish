#!/usr/bin/env python3
"""Draw the bench set from candidates.tsv: fixed seed, stratified by value.

Most of the set sits where a single search closes some positions and not
others (+16 to +35); the two ends are thin anchors.  From +16 up, half of each
band comes from White's strategy tree and half from outside it.  Two positions
that share six or more ranks are treated as the same family and only one is
kept, so one opening line cannot weigh the result.

Usage: draw_set.py candidates.tsv positions.tsv [seed]
"""
import csv
import random
import sys

SRC, OUT = sys.argv[1], sys.argv[2]
SEED = int(sys.argv[3]) if len(sys.argv) > 3 else 20261114
# (low cp, high cp, positions)
BANDS = [(1000, 1300, 40), (1300, 1600, 60), (1600, 2000, 100),
         (2000, 2500, 100), (2500, 3500, 100), (3500, 5000, 60),
         (5000, 9000, 40)]
SAME_FAMILY_RANKS = 6

rows = sorted(csv.DictReader(open(SRC, encoding='utf-8'), delimiter='\t'),
              key=lambda r: r['key'])
rng = random.Random(SEED)
rng.shuffle(rows)
chosen, boards = [], []


def fresh(fen):
    ranks = fen.split(' ')[0].split('/')
    for other in boards:
        if sum(a == b for a, b in zip(ranks, other)) >= SAME_FAMILY_RANKS:
            return False
    boards.append(ranks)
    return True


def take(pool, want):
    got = 0
    for r in pool:
        if got >= want:
            break
        if r.get('_used') or not fresh(r['fen']):
            continue
        r['_used'] = True
        chosen.append(r)
        got += 1
    return got


for low, high, want in BANDS:
    name = '%d-%d' % (low // 100, high // 100)
    pool = [r for r in rows if low <= int(r['value']) < high]
    for r in pool:
        r['_band'] = name
    tree = [r for r in pool if r['where'] == 'tree']
    got = take(tree, want // 2) if tree else 0
    got += take([r for r in pool if r['where'] != 'tree'], want - got)
    if got < want:
        got += take(pool, want - got)
    sys.stderr.write('%s: %d of %d (pool %d, tree %d)\n'
                     % (name, got, want, len(pool), len(tree)))

chosen.sort(key=lambda r: (int(r['value']), r['key']))
with open(OUT, 'w', encoding='utf-8', newline='\n') as out:
    out.write('\t'.join(['id', 'band', 'where', 'value', 'pieces',
                         'last_search', 'engine_sha', 'key', 'fen']) + '\n')
    for i, r in enumerate(chosen, 1):
        out.write('\t'.join([
            'cb%03d' % i, r['_band'], r['where'], r['value'], r['pieces'],
            r['last_search'], r['engine_sha'], r['key'], r['fen']]) + '\n')
sys.stderr.write('%d positions -> %s\n' % (len(chosen), OUT))
