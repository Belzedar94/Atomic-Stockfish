# Closing-power bench

How many open AtomicDB positions does one search of an engine close, and
after how many nodes. It is the number the solving effort depends on, and
no Elo test measures it.

## The set

`positions.tsv`: 500 real positions, frozen on 14-Nov-2026.

- Open in production (`status = UNKNOWN`), White to move, valued by the tree
  between +10 and +90 for White.
- Searched exactly once, at about 128M nodes (MultiPV 5), before 2-Nov-2026,
  and never since. An engine from July to October looked at each of them
  with the production budget and left it open.
- Seven value bands. Most positions sit where one search closes some and not
  others; the two ends are thin anchors.

| Band (pawns) | 10-13 | 13-16 | 16-20 | 20-25 | 25-35 | 35-50 | 50-90 |
|---|---|---|---|---|---|---|---|
| Positions | 40 | 60 | 100 | 100 | 100 | 60 | 40 |

- From +16 up, half of each band is inside White's strategy tree (the
  sweep's list of 11-Nov) and half outside it.
- Two positions that share six or more ranks count as one family and only
  one is kept.

How it was drawn, both steps with a fixed seed:

    # server, read-only, about 75 seconds
    cat /var/lib/atomicdb/sweep16.json | sudo -u postgres nice -n 10 \
      /opt/openbench/.venv/bin/python sample_open_positions.py 6 20261114 2026-11-02 > candidates.tsv
    # local
    python draw_set.py candidates.tsv positions.tsv

`candidates.tsv` (33,098 rows, a 6% sample of the table) is not kept in git.

## Running it

    python closing_bench.py run --engine <engine.exe> --out results/<name>.jsonl \
        --syzygy <3-4-5 tables> [--nodes 128e6] [--jobs 6] [--hash 256] [--multipv 5]
    python closing_bench.py report results/<name>.jsonl
    python closing_bench.py compare results/<a>.jsonl results/<b>.jsonl [--at 32e6] [--skip-left-by <sha,...>]

Each position is searched the way an AtomicDB worker searches it: 1 thread,
MultiPV 1, a clean hash table, `go nodes N mate 250`. A position is closed
when the search ends on a mate for the side to move, or when the score has
been a tablebase win without a break to the end of the budget.

- Everything is counted in nodes. The same engine, hash and tables give the
  same result to the node, on any machine and with any `--jobs`.
- A run at 128M holds the answer for every smaller budget, so `report` and
  `compare --at` read 1M, 8M and 32M out of the same file.
- `run` can be stopped and started again; it skips what the file has. It
  refuses to mix engines, budgets or hash sizes in one file.
- One run per output file at a time (lock file), a cap of `--max-hours`, and
  on Windows the engines die with the runner.
- Cost at 128M: about 100 seconds of one core per position that stays open.
- `--multipv N` searches with N lines, as user requests do in production
  (5). Only the best line decides whether the position is closed.
- `--open-in results/<name>.jsonl` searches only the positions that run
  left open, to price a bigger budget without searching again what the
  smaller one closed (the search is deterministic, so the first 128M of a
  512M search are the 128M search).
- `open_set.py results/<name>.jsonl out.tsv [bands]` writes the positions a
  run left open in the input format of the census tools' `local_prover.py`
  (label, key, fen, nodes already spent; not in this repository).

## Reading it

- `report` gives positions closed by band at each budget, and the value from
  which half of the positions close (a logistic fit on the whole set).
- `report` also prices a closure: nodes spent per position closed had every
  search stopped at 1M, 2M, 4M and so on, and what each extra closure cost
  in the last doubling.
- `compare` pairs two runs position by position: closed by both, by one
  only, by neither, and how likely a split that uneven is by chance. Where
  both close, it gives the ratio of nodes each needed.
- The set was left open by the engines of July to October. Their own numbers
  on it are low by construction; the whole set is a fair comparison only
  between engines released after 1-Nov-2026.
- To judge an older engine, leave out the positions it left open itself:
  `compare --skip-left-by <sha256 prefixes>`. The September engine is
  `2990a6f47cb6` (Linux) and `88a5c04e8ac1` (Windows) and left 387 of the
  500; the other 113 are fair to it.
- Before reading a difference between two engines as a gain, compare it with
  the difference between two runs of the same engine with another hash size
  (`--hash`): that is how much the count moves for no reason.

## Results, 14-Nov-2026

Positions closed out of 500, by budget. All runs: Hash 256, tables 3-4-5.

| Engine | sha256 | <=1M | <=8M | <=32M | <=128M | Half close from | M nodes per closure at 8M / 128M |
|---|---|---|---|---|---|---|---|
| September | `88a5c04e8ac1` | 0 | 5 | 17 | 47 | +66.7 | 797 / 1286 |
| 18-Oct (1018) | `4c7b378750a1` | 9 | 39 | 68 | 105 | +44.5 | 98 / 513 |
| 20-Oct (1020b) | `4e48ed37aaac` | 15 | 61 | 102 | 136 | +36.5 | 60 / 366 |
| 2-Nov (1102) | `131fe12b1ec7` | 22 | 75 | 110 | 152 | +33.1 | 48 / 320 |
| 11-Nov (1111) | `846508c91246` | 18 | 71 | 115 | 148 | +34.2 | 52 / 326 |
| 14-Nov (1114, cbc0504a) | `590b78b6ac44` | 20 | 71 | 122 | 155 | +32.8 | 51 / 306 |
| 1114 with `--multipv 5` | `590b78b6ac44` | 12 | 56 | 98 | 157 | +31.9 | 67 / 316 |

On the 113 positions the September engine did not leave open itself:
September 7, 1018 12, 1020b 21, 1102 25, 1111 24, 1114 27. No engine of the
six closes 319 of the 500.

The current engine (1111) by band, closed at 128M: +10-13 0 of 40, +13-16 2
of 60, +16-20 17 of 100, +20-25 13 of 100, +25-35 48 of 100, +35-50 36 of
60, +50-90 32 of 40. Inside White's strategy tree 37%, outside it 36%.

Paired, at 128M:

| A -> B | Positions | Only A | Only B | p | Nodes of B where both close |
|---|---|---|---|---|---|
| 1018 -> 1020b | 387 (neither left them open) | 2 | 24 | <0.001 | x0.42 (x0.34 to x0.52) |
| 1020b -> 1102 | 387 (neither left them open) | 7 | 19 | 0.03 | x0.68 (x0.56 to x0.83) |
| 1018 -> 1102 | 387 (neither left them open) | 2 | 36 | <0.001 | x0.29 (x0.23 to x0.37) |
| 1102 -> 1111 | 500 | 16 | 12 | 0.57 | x1.14 (x0.96 to x1.35) |
| 1111 -> 1114 | 500 | 10 | 17 | 0.25 | x0.94 (x0.84 to x1.06) |
| 1114 -> 1114 with 5 lines | 500 | 19 | 21 | 0.88 | x1.98 (x1.63 to x2.41) |
| 1111 -> 1111 with Hash 128, at 32M | 500 | 8 | 11 | 0.65 | x1.07 (x0.99 to x1.16) |

- The last row is the noise floor: the same engine against itself moves 19
  positions out of 126. With this set a change shows from about 14 closures
  at 128M, or about 20% in nodes where both close.
- The price of a closure climbs steeply with the budget. For 1111 the first
  million nodes cost 27M per closure and the doubling from 64M to 128M costs
  1440M per extra closure.
- Five lines against one, same engine: the same closures at 128M (157 and
  155) for 3% more nodes, and the mate comes at twice the nodes. At 8M and
  32M five lines close about 20% fewer (56 against 71, 98 against 122). Those
  two figures are a lower bound for five lines: they are read out of the 128M
  run, and a five-line search cut mid-iteration can still end on a mate.
## Results, 18-Nov-2026

Same settings, `make build ARCH=x86-64-bmi2 COMP=mingw`, default net ft5
(the net of 1114 too, so the differences are search only).

| Engine | bench | sha256 | <=1M | <=8M | <=32M | <=128M | M nodes per extra closure 64M -> 128M |
|---|---|---|---|---|---|---|---|
| 14-Nov (1114) | 587120 | `590b78b6ac44` | 20 | 71 | 122 | 155 | 1318 |
| main 63238876 (SPSA block 2) | 659511 | `ac672d9a7f19` | 25 | 73 | 121 | 158 | 1247 |
| tune/t527-values c5ef908f (block 3) | 494449 | `23a8a2565383` | 31 | 82 | 120 | 163 | 1054 |

Paired against 1114 (only 1114 / only B, p, nodes of B where both close):

| B | at 8M | at 32M | at 128M |
|---|---|---|---|
| main | 10 / 12, p 0.83, x0.75 | 11 / 10, p 1.00, x0.79 | 8 / 11, p 0.65, x0.84 |
| T527 values | 8 / 19, p 0.05, x0.60 | 15 / 13, p 0.85, x0.69 | 7 / 15, p 0.13, x0.74 |

Main -> T527 values at 128M: 9 / 14, p 0.41, x0.88 (x0.76 to x1.03).
No count difference is outside the noise floor; the mates come with fewer
nodes (x0.74 to x0.84 at 128M, intervals clear of 1).

128M -> 512M, engine 1114 on the 345 positions it leaves open at 128M
(`results/asf-1114-512m.jsonl`, `--open-in results/asf-1114.jsonl`): 17 close
by 256M and 38 by 512M (193 of 500 in all). The extra nodes cost 2532M per
extra closure from 128M to 256M, 3844M from 256M to 512M, and 3257M over the
whole step, against 1318M from 64M to 128M. None of them closes at or below
128M, as the determinism requires. Closed by band: +16-20 9, +20-25 9,
+25-35 11, +35-50 8, +50-90 1, none below +16.

## Notes

- A search can end on a mate a few hundred nodes past its budget, because
  the engine checks the limit at intervals. It counts as closed with that
  budget.
