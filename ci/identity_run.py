#!/usr/bin/env python3
"""Search a list of positions with one engine at fixed nodes and record its output.

    identity_run.py --engine EXE --fens FILE --nodes N --out FILE [--multipv K]
                    [--hash MB] [--limit N] [--timeout-per-pos S] [--total-timeout S]

FILE is a TSV with a header that has a `fen` column, or a plain list of FENs.
For every position: ucinewgame, isready, position fen, go nodes N, and every
line is read up to `bestmove` before anything else is sent (never `quit` right
after `go`). The `info depth` lines are kept without their `time` and `nps`
fields, so two builds that search the same tree give byte-identical files.

If the engine dies (a failed assert in a debug build), the position is recorded
as CRASH with the tail of stderr, the engine is restarted and the run goes on.
One engine process at a time, single-threaded, killed on timeout.
"""
from __future__ import annotations

import argparse
import csv
import re
import subprocess
import sys
import threading
import time
from pathlib import Path

TIME_RE = re.compile(r" time \d+")
NPS_RE = re.compile(r" nps \d+")
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def load_fens(path: Path) -> list[str]:
    text = path.read_text(encoding="utf-8").splitlines()
    if text and "\t" in text[0] and "fen" in text[0].split("\t"):
        rows = csv.DictReader(text, delimiter="\t")
        return [r["fen"].strip() for r in rows if r.get("fen")]
    return [l.strip() for l in text if l.strip() and not l.startswith("#")]


class Engine:
    def __init__(self, exe: str, cwd: str, stderr_path: Path, hash_mb: int, multipv: int):
        self.stderr_file = stderr_path.open("a", encoding="utf-8", errors="replace")
        self.proc = subprocess.Popen([exe], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                     stderr=self.stderr_file, text=True, encoding="utf-8",
                                     errors="replace", cwd=cwd, bufsize=1, creationflags=NO_WINDOW)
        self.send("uci")
        self.read_until("uciok", 60)
        self.send("setoption name Threads value 1")
        self.send(f"setoption name Hash value {hash_mb}")
        self.send(f"setoption name MultiPV value {multipv}")
        self.send("isready")
        self.read_until("readyok", 60)

    def send(self, line: str) -> None:
        self.proc.stdin.write(line + "\n")
        self.proc.stdin.flush()

    def read_until(self, token: str, timeout: float, keep: list[str] | None = None) -> None:
        timer = threading.Timer(timeout, self.proc.kill)
        timer.start()
        try:
            while True:
                line = self.proc.stdout.readline()
                if not line:
                    raise EOFError(f"engine output closed while waiting for {token}")
                line = line.rstrip("\r\n")
                if keep is not None and (line.startswith("info depth") or line.startswith("bestmove")):
                    keep.append(NPS_RE.sub("", TIME_RE.sub("", line)))
                if line.startswith(token):
                    return
        finally:
            timer.cancel()

    def close(self) -> None:
        try:
            if self.proc.poll() is None:
                self.send("quit")
                self.proc.wait(timeout=20)
        except Exception:
            pass
        if self.proc.poll() is None:
            self.proc.kill()
        self.stderr_file.close()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--engine", required=True)
    ap.add_argument("--fens", type=Path, required=True)
    ap.add_argument("--nodes", type=int, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--multipv", type=int, default=1)
    ap.add_argument("--hash", type=int, default=16)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--cwd", default=".")
    ap.add_argument("--timeout-per-pos", type=float, default=300)
    ap.add_argument("--total-timeout", type=float, default=4 * 3600)
    a = ap.parse_args()

    fens = load_fens(a.fens)
    if a.limit:
        fens = fens[: a.limit]
    stderr_path = a.out.with_suffix(a.out.suffix + ".stderr")
    stderr_path.write_text("", encoding="utf-8")
    t0 = time.time()
    crashes = 0
    eng = Engine(a.engine, a.cwd, stderr_path, a.hash, a.multipv)
    with a.out.open("w", encoding="utf-8", newline="\n") as out:
        for i, fen in enumerate(fens, 1):
            if time.time() - t0 > a.total_timeout:
                out.write(f"## STOPPED at {i}: total timeout\n")
                print(f"total timeout at position {i}", flush=True)
                break
            out.write(f"## {i} {fen}\n")
            lines: list[str] = []
            try:
                eng.send("ucinewgame")
                eng.send("isready")
                eng.read_until("readyok", 120)
                eng.send(f"position fen {fen}")
                eng.send(f"go nodes {a.nodes}")
                eng.read_until("bestmove", a.timeout_per_pos, lines)
                out.write("\n".join(lines) + "\n")
            except (EOFError, OSError, BrokenPipeError) as exc:
                crashes += 1
                eng.close()
                tail = stderr_path.read_text(encoding="utf-8", errors="replace").strip().splitlines()[-3:]
                out.write("\n".join(lines) + ("\n" if lines else ""))
                out.write(f"CRASH {exc}; stderr: {' | '.join(tail)}\n")
                print(f"CRASH at position {i}: {fen}: {' | '.join(tail)}", flush=True)
                eng = Engine(a.engine, a.cwd, stderr_path, a.hash, a.multipv)
            if i % 250 == 0:
                print(f"{i}/{len(fens)} positions, {time.time() - t0:.0f}s, crashes {crashes}", flush=True)
    eng.close()
    print(f"done: {len(fens)} positions, {time.time() - t0:.0f}s, crashes {crashes}", flush=True)
    return 1 if crashes else 0


if __name__ == "__main__":
    sys.exit(main())
