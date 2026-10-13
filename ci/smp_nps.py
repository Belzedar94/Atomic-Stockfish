#!/usr/bin/env python3
"""Nodes per second of two engine binaries when they search with several threads.

Workers run the engine with more than one thread, and the profile that guided the new
build was recorded with one. A multi-threaded search is not reproducible, so there is
no node count to compare here: this only times `bench <hash> <threads> <depth>` for
both binaries over alternating rounds and reports the ratio.

usage: smp_nps.py --old PATH --new PATH --cwd DIR --threads N [--depth D] [--rounds R] [--hash MB]
"""
import argparse
import math
import re
import statistics
import subprocess
from pathlib import Path

NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
T95 = {1: 12.706, 2: 4.303, 3: 3.182, 4: 2.776, 5: 2.571, 6: 2.447, 7: 2.365, 8: 2.306, 9: 2.262,
       10: 2.228, 11: 2.201, 12: 2.179, 13: 2.160, 14: 2.145, 15: 2.131}


def bench(exe: Path, cwd: Path, hash_mb: int, threads: int, depth: int) -> tuple[int, float]:
    out = subprocess.run([str(exe)], input=f"bench {hash_mb} {threads} {depth} default depth\nquit\n",
                         capture_output=True, text=True, errors="replace", cwd=str(cwd),
                         creationflags=NO_WINDOW, timeout=1800)
    text = out.stdout + out.stderr
    ms = int(re.search(r"Total time \(ms\)\s*:\s*(\d+)", text).group(1))
    nodes = int(re.search(r"Nodes searched\s*:\s*(\d+)", text).group(1))
    if "NNUE evaluation using" not in text:
        raise RuntimeError(f"{exe.name}: the bench did not run with NNUE")
    return nodes, 1000.0 * nodes / max(ms, 1)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--old", type=Path, required=True)
    ap.add_argument("--new", type=Path, required=True)
    ap.add_argument("--cwd", type=Path, required=True)
    ap.add_argument("--threads", type=int, required=True)
    ap.add_argument("--depth", type=int, default=20)
    ap.add_argument("--rounds", type=int, default=8)
    ap.add_argument("--hash", type=int, default=256)
    ap.add_argument("--md", type=Path)
    a = ap.parse_args()
    bins = [("old", a.old.resolve()), ("new", a.new.resolve())]
    for _, exe in bins:  # warm-up
        bench(exe, a.cwd, a.hash, a.threads, a.depth)
    nps = {"old": [], "new": []}
    for r in range(a.rounds):
        for label, exe in (bins if r % 2 == 0 else bins[::-1]):
            nodes, v = bench(exe, a.cwd, a.hash, a.threads, a.depth)
            nps[label].append(v)
        print(f"round {r + 1}/{a.rounds}: old={nps['old'][-1]:.0f} new={nps['new'][-1]:.0f}", flush=True)
    ratios = [n / o for o, n in zip(nps["old"], nps["new"])]
    ci = T95.get(len(ratios) - 1, 2.1) * statistics.stdev(ratios) / math.sqrt(len(ratios))
    line = (f"{a.threads} threads, hash {a.hash}, depth {a.depth}, {a.rounds} alternating rounds: "
            f"old {statistics.mean(nps['old']):.0f} nps, new {statistics.mean(nps['new']):.0f} nps, "
            f"new/old {100 * (statistics.mean(ratios) - 1):+.2f}% +/- {100 * ci:.2f} (95%)")
    print(line, flush=True)
    if a.md:
        with a.md.open("a", encoding="utf-8") as md:
            md.write(f"\nMulti-threaded: {line}\n\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
