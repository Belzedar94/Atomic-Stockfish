#!/usr/bin/env python3
"""Positions a run left open, in the input format of the local prover
(tools/atomicdb-census/local_prover.py): label, key, fen, nodes already spent.

Usage: open_set.py results/NAME.jsonl out.tsv [band,band,...]
       open_set.py results/asf-1114.jsonl expand.tsv 25-35,35-50
"""
import csv
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
res, out = sys.argv[1], sys.argv[2]
bands = set(sys.argv[3].split(',')) if len(sys.argv) > 3 else None
with open(os.path.join(HERE, 'positions.tsv'), encoding='utf-8') as handle:
    meta = {r['id']: r for r in csv.DictReader(handle, delimiter='\t')}
with open(res, encoding='utf-8') as handle:
    rows = [json.loads(line) for line in handle if line.strip()]
rows = [r for r in rows if 'bench' not in r and r['result'] == 'open'
        and (bands is None or r['band'] in bands)]
with open(out, 'w', encoding='utf-8', newline='\n') as handle:
    for r in sorted(rows, key=lambda r: r['id']):
        handle.write('%s\t%s\t%s\t%d\n' % (r['id'], meta[r['id']]['key'],
                                           meta[r['id']]['fen'], r['searched']))
sys.stderr.write('%d open positions -> %s\n' % (len(rows), out))
