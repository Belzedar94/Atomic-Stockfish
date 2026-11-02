#!/usr/bin/env python3
"""Replay an AtomicDB-like analysis load on one engine process.

    atomicdb_load.py --engine EXE --seeds TSV --nodes N --multipv K --walk L
                     [--threads T] [--hash MB] [--limit S] [--wrap "perf record ... --"]
                     --out FILE.jsonl

TSV has the columns label and fen. For every seed the engine searches the seed
position, then the position after its best move, and so on, L positions in all:
the positions of one walk are related, as consecutive AtomicDB tasks are, and
there is no ucinewgame anywhere, so the hash table and the histories survive
from task to task as on the workers (Hash 256 by default, MultiPV K, go nodes N).
Every line is read up to bestmove before anything else is sent; `quit` is only
sent at the end. One JSON line per task: what was searched and what came back.

--wrap is a command the engine is started under (perf record), split with shlex.
Standard library only.
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import shlex
import subprocess
import sys
import threading
import time
from pathlib import Path

INFO_RE = re.compile(r"^info depth (\d+) seldepth (\d+) multipv (\d+) score (cp|mate) (-?\d+).* nodes (\d+)")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--engine", required=True)
    ap.add_argument("--seeds", required=True, type=Path)
    ap.add_argument("--nodes", type=int, default=3_000_000)
    ap.add_argument("--multipv", type=int, default=1)
    ap.add_argument("--walk", type=int, default=3)
    ap.add_argument("--threads", type=int, default=1)
    ap.add_argument("--hash", type=int, default=256)
    ap.add_argument("--limit", type=int, default=0, help="first N seeds only")
    ap.add_argument("--select", default="", help="comma-separated seed labels to use instead of --limit")
    ap.add_argument("--wrap", default="")
    ap.add_argument("--cwd", default=".")
    ap.add_argument("--timeout", type=float, default=5400, help="seconds for the whole session")
    ap.add_argument("--out", required=True, type=Path)
    a = ap.parse_args()

    with a.seeds.open(encoding="utf-8") as f:
        seeds = [(r["label"], r["fen"]) for r in csv.DictReader(f, delimiter="\t")]
    if a.select:
        want = a.select.split(",")
        seeds = [s for s in seeds if s[0] in want]
    elif a.limit:
        seeds = seeds[: a.limit]

    cmd = shlex.split(a.wrap) + [str(Path(a.engine).resolve())]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=None,
                            text=True, encoding="utf-8", errors="replace", cwd=a.cwd, bufsize=1)
    killer = threading.Timer(a.timeout, proc.kill)
    killer.start()

    def send(line: str) -> None:
        proc.stdin.write(line + "\n")
        proc.stdin.flush()

    def read_until(token: str) -> list[str]:
        lines = []
        while True:
            line = proc.stdout.readline()
            if not line:
                raise RuntimeError(f"engine output closed while waiting for {token}")
            line = line.rstrip("\r\n")
            lines.append(line)
            if line.startswith(token):
                return lines

    t_start = time.time()
    total_nodes = 0
    tasks = 0
    try:
        send("uci")
        read_until("uciok")
        send(f"setoption name Threads value {a.threads}")
        send(f"setoption name Hash value {a.hash}")
        send("isready")
        read_until("readyok")
        with a.out.open("w", encoding="utf-8") as out:
            for si, (label, fen) in enumerate(seeds):
                moves: list[str] = []
                if " moves " in fen:  # a seed given as a position plus moves
                    fen, tail = fen.split(" moves ", 1)
                    moves = tail.split()
                for step in range(a.walk):
                    send(f"setoption name MultiPV value {a.multipv}")
                    send(f"position fen {fen}" + (" moves " + " ".join(moves) if moves else ""))
                    t0 = time.time()
                    send(f"go nodes {a.nodes}")
                    lines = read_until("bestmove")
                    secs = time.time() - t0
                    best = lines[-1].split()[1] if len(lines[-1].split()) > 1 else "(none)"
                    infos = [m for m in (INFO_RE.match(l) for l in lines) if m]
                    last = infos[-1] if infos else None
                    first_pv = [m for m in infos if m.group(3) == "1"]
                    lp = first_pv[-1] if first_pv else None
                    nodes = int(last.group(6)) if last else 0
                    total_nodes += nodes
                    tasks += 1
                    rec = {"seed": si, "label": label, "step": step, "fen": fen, "moves": list(moves),
                           "multipv": a.multipv, "go_nodes": a.nodes, "nodes": nodes,
                           "depth": int(lp.group(1)) if lp else None, "seldepth": int(lp.group(2)) if lp else None,
                           "score": f"{lp.group(4)} {lp.group(5)}" if lp else None,
                           "bestmove": best, "seconds": round(secs, 3)}
                    out.write(json.dumps(rec) + "\n")
                    out.flush()
                    print(f"[{label} {step}] {best} depth {rec['depth']} {rec['score']} {nodes} nodes "
                          f"{secs:.1f}s", flush=True)
                    if best in ("(none)", "0000"):
                        break
                    moves.append(best)
        send("quit")
        proc.wait(timeout=600)
    finally:
        killer.cancel()
        if proc.poll() is None:
            proc.kill()
    el = time.time() - t_start
    print(f"{tasks} tasks, {total_nodes} nodes, {el:.0f}s, {total_nodes / max(el, 1e-9):.0f} nodes/s overall",
          flush=True)
    return 0 if proc.returncode == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
