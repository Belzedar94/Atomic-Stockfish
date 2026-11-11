#!/usr/bin/env python3
"""The training run of a profile-guided build: AtomicDB-like searches instead of `bench`.

    pgo_load.py --engine EXE --positions TSV [--hash 256] [--log FILE]

Runs from `make profile-build` in place of the default `bench` (see
ci/pgo_train.sh), in the engine's src/ directory: the instrumented binary reads
the network from there and writes its raw profile there on exit, where the
Makefile's llvm-profdata merge picks it up.

One engine process searches every row of TSV (columns threads, multipv, nodes,
searchmoves, fen) in order, the way an AtomicDB worker does: Hash 256, `go nodes`
with MultiPV set per search, sometimes `searchmoves`, and no `ucinewgame`, so the
transposition table carries over. A change of Threads resizes the thread pool,
which also clears the table, as it would for a worker restarted with another -T.

Fails (exit status 1) unless every search reports the expected network backend
and a real bestmove, and the engine exits cleanly after `quit`: an engine that is
killed writes no profile. The last four lines summarise the run, for the
Makefile's `tail -n 4 PGOBENCH.out`. Standard library only.
"""
from __future__ import annotations

import argparse
import collections
import csv
import re
import subprocess
import sys
import threading
import time
from pathlib import Path

BACKEND = "NNUE evaluation using Legacy Atomic V1"
INFO_RE = re.compile(r"^info depth (\d+) seldepth (\d+).* nodes (\d+)")
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--engine", required=True)
    ap.add_argument("--positions", type=Path, required=True)
    ap.add_argument("--hash", type=int, default=256)
    ap.add_argument("--log", type=Path, help="full UCI transcript")
    ap.add_argument("--timeout-per-pos", type=float, default=600)
    a = ap.parse_args()

    with a.positions.open(encoding="utf-8") as f:
        rows = list(csv.DictReader(f, delimiter="\t"))
    exe = str(Path(a.engine).resolve())
    log = a.log.open("w", encoding="utf-8", newline="\n") if a.log else None
    proc = subprocess.Popen([exe], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, encoding="utf-8", errors="replace", bufsize=1, creationflags=NO_WINDOW)

    def send(line: str) -> None:
        if log:
            log.write(f"> {line}\n")
        proc.stdin.write(line + "\n")
        proc.stdin.flush()

    def read_until(token: str, timeout: float) -> list[str]:
        killer = threading.Timer(timeout, proc.kill)
        killer.start()
        lines = []
        try:
            while True:
                line = proc.stdout.readline()
                if not line:
                    raise RuntimeError(f"engine output closed while waiting for {token}")
                line = line.rstrip("\r\n")
                if log:
                    log.write(line + "\n")
                lines.append(line)
                if line.startswith(token):
                    return lines
        finally:
            killer.cancel()

    t_start = time.time()
    total_nodes = 0
    threads_now = None
    count = collections.Counter()
    try:
        send("uci")
        read_until("uciok", 60)
        send(f"setoption name Hash value {a.hash}")
        for i, r in enumerate(rows, 1):
            threads, multipv, nodes = int(r["threads"]), int(r["multipv"]), int(r["nodes"])
            searchmoves = r.get("searchmoves", "").split()
            if threads != threads_now:
                send(f"setoption name Threads value {threads}")
                send("isready")
                read_until("readyok", 120)
                threads_now = threads
            send(f"setoption name MultiPV value {multipv}")
            send(f"position fen {r['fen']}")
            go = f"go nodes {nodes}" + (" searchmoves " + " ".join(searchmoves) if searchmoves else "")
            t0 = time.time()
            send(go)
            lines = read_until("bestmove", a.timeout_per_pos)
            seconds = time.time() - t0
            bestmove = lines[-1].split()[1] if len(lines[-1].split()) > 1 else "(none)"
            info = [m for m in (INFO_RE.match(l) for l in lines) if m]
            depth, seldepth, searched = (int(info[-1].group(k)) for k in (1, 2, 3)) if info else (0, 0, 0)
            backend = any(BACKEND in l for l in lines)
            print(f"train {i}/{len(rows)}: threads {threads} multipv {multipv} {go} -> {bestmove}, "
                  f"depth {depth} seldepth {seldepth}, {searched} nodes, {seconds:.1f}s"
                  + ("" if backend else ", NO BACKEND LINE"), flush=True)
            if not backend or bestmove in ("(none)", "0000") or (searchmoves and bestmove not in searchmoves):
                raise RuntimeError(f"position {i}: bestmove {bestmove}, backend line {backend}")
            total_nodes += searched
            count[f"Threads {threads}"] += 1
            count[f"MultiPV {multipv}"] += 1
            count["searchmoves"] += bool(searchmoves)
        send("quit")
        rc = proc.wait(timeout=120)
        if rc != 0:
            raise RuntimeError(f"engine exit code {rc}")
    except Exception as exc:
        print(f"PGO training load FAILED: {exc}", flush=True)
        if proc.poll() is None:
            proc.kill()
        return 1
    finally:
        if log:
            log.close()

    ms = int(1000 * (time.time() - t_start))
    mix = ", ".join(f"{k}: {v}" for k, v in sorted(count.items()))
    print(f"PGO training load ({a.positions.name}): {len(rows)} positions, Hash {a.hash}, no ucinewgame, "
          f"engine exit code 0", flush=True)
    print(f"searches: {mix}", flush=True)
    print(f"Total time (ms) : {ms}", flush=True)
    print(f"Nodes searched  : {total_nodes}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
