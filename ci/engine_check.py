#!/usr/bin/env python3
"""Check that several builds of one engine search exactly the same tree, and time them.

Standard library only; runs on Linux, macOS and Windows. Every engine process is
single-threaded, started without a console window, and killed on timeout.

The engine evaluates with NNUE only (there is no classical evaluation and no
classical bench any more). For every binary given as LABEL=PATH it
  1. records what the binary says about itself (`compiler`, `id name`, its options);
  2. runs a signature bench (the default `bench`, or with --signature-limit
     `bench 16 1 <limit> default <limit type>`), requires every search to report the
     network backend --expect-backend ("NNUE evaluation using <backend> ..."), and
     compares the node count with --expect-bench, or, without it, with the first
     binary's;
  3. replays a worker-like UCI session (go nodes, MultiPV, searchmoves, optional
     SyzygyPath) and requires the engine output to be identical to the first binary's,
     apart from the time and nps fields; one progress line per position shows how
     deep each search got and how long it took;
  4. times `bench 16 1 <limit> default <limit type>`, several rounds, the order
     rotating and alternating (two binaries: ABBA); every run of every binary must
     report the same nodes and the expected backend.
A stage that fails (a timeout, an aborted bench) is recorded as a failure and the
next stage still runs, so the report says how far each check got.

With --evalfile every engine process loads that network (setoption name EvalFile)
before it searches; without it the engine uses its default network.

Exit status 0 only if every identity check passed. Speed is reported, never judged.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import platform
import re
import statistics
import subprocess
import sys
import threading
import time
from pathlib import Path

NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
TIME_RE = re.compile(r"Total time \(ms\)\s*:\s*(\d+)")
NODES_RE = re.compile(r"Nodes searched\s*:\s*(\d+)")
BACKEND_RE = re.compile(r"NNUE evaluation using (Legacy Atomic V1|AtomicNNUEV2|AtomicNNUEV3) ")
INFO_RE = re.compile(r"^info depth (\d+) seldepth (\d+).* nodes (\d+)")
# two-sided 95% Student t quantiles for n-1 degrees of freedom
T95 = {1: 12.706, 2: 4.303, 3: 3.182, 4: 2.776, 5: 2.571, 6: 2.447, 7: 2.365, 8: 2.306,
       9: 2.262, 10: 2.228, 11: 2.201, 12: 2.179, 13: 2.160, 14: 2.145, 15: 2.131,
       16: 2.120, 17: 2.110, 18: 2.101, 19: 2.093, 20: 2.086, 24: 2.064, 29: 2.045}

# (fen, nodes, multipv, searchmoves) -- the shape of an AtomicDB analysis task.
SESSION = [
    ("rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1", 400000, 3, None),
    ("rn2kb1r/1pp1p2p/p2q1pp1/3P4/2P3b1/4PN2/PP3PPP/R2QKB1R b KQkq - 0 1", 400000, 4, None),
    ("rn1qkb1r/p5pp/2p5/3p4/N3P3/5P2/PPP4P/R1BQK3 w Qkq - 0 1", 300000, 1, None),
    ("r4b1r/2kb1N2/p2Bpnp1/8/2Pp3p/1P1PPP2/P5PP/R3K2R b KQ - 0 1", 300000, 2, None),
    ("rnbqkbnr/pppp1ppp/8/4p3/4P3/8/PPPP1PPP/RNBQKBNR w KQkq e6 0 2", 300000, 2,
     ["g1f3", "d2d4", "b1c3"]),
    ("4K1R1/RR6/8/8/8/8/qqq3p1/7k b - - 0 1", 200000, 3, None),
    ("7k/8/8/3pP3/8/8/8/K7 w - d6 0 1", 100000, 2, None),
    # tablebase material (3 to 5 men, no castling, no en passant)
    ("8/8/8/3k4/8/8/8/KQ6 w - - 0 1", 100000, 3, None),
    ("8/8/8/3k4/8/8/P7/K7 w - - 0 1", 100000, 2, None),
    ("7r/8/4k3/8/8/8/1R6/K7 w - - 0 1", 100000, 3, None),
    ("7r/8/4k3/8/8/2N5/1R6/K7 w - - 0 1", 200000, 3, None),
    ("3q4/8/4k3/8/8/8/1R6/K7 b - - 0 1", 100000, 2, None),
]


def pin(proc: subprocess.Popen, cpu: int | None) -> None:
    if cpu is None:
        return
    try:
        if hasattr(os, "sched_setaffinity"):
            os.sched_setaffinity(proc.pid, {cpu})
        elif os.name == "nt":
            import ctypes
            ctypes.windll.kernel32.SetProcessAffinityMask(int(proc._handle), ctypes.c_size_t(1 << cpu))
    except Exception as exc:  # pinning is a nicety, never a reason to fail
        print(f"[pin] could not pin to cpu {cpu}: {exc}", flush=True)


def run_batch(exe: Path, cwd: Path, lines: list[str], timeout: float, cpu: int | None = None) -> str:
    """Commands that are safe to feed all at once (setoption and bench are synchronous)."""
    proc = subprocess.Popen([str(exe)], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, text=True, encoding="utf-8",
                            errors="replace", cwd=str(cwd), creationflags=NO_WINDOW)
    pin(proc, cpu)
    try:
        out, _ = proc.communicate("\n".join(lines) + "\nquit\n", timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.communicate()
        raise RuntimeError(f"{exe.name}: timeout after {timeout:.0f}s")
    if proc.returncode != 0:
        raise RuntimeError(f"{exe.name}: exit code {proc.returncode}\n{out[-1500:]}")
    return out


def self_description(exe: Path, cwd: Path) -> dict:
    out = run_batch(exe, cwd, ["compiler", "uci"], 60)
    info = {"banner": out.strip().splitlines()[0] if out.strip() else ""}
    for key, pat in (("compiled_by", r"Compiled by\s*:\s*(.+)"), ("arch", r"Compilation architecture\s*:\s*(.+)"),
                     ("settings", r"Compilation settings\s*:\s*(.+)"), ("version_macro", r"__VERSION__ macro\s*:\s*(.+)"),
                     ("id_name", r"id name (.+)")):
        m = re.search(pat, out)
        info[key] = m.group(1).strip() if m else ""
    info["options"] = re.findall(r"^option name (.+?) type ", out, re.M)
    return info


def bench(exe: Path, cwd: Path, setup: list[str], cmd: str, cpu: int | None, timeout: float) -> dict:
    out = run_batch(exe, cwd, setup + [cmd], timeout, cpu)
    if "Bench aborted" in out:
        raise RuntimeError(f"{exe.name}: bench aborted\n{out[-1500:]}")
    t, n = TIME_RE.search(out), NODES_RE.search(out)
    if not (t and n):
        raise RuntimeError(f"{exe.name}: incomplete bench output\n{out[-1500:]}")
    ms, nodes = int(t.group(1)), int(n.group(1))
    return {"ms": ms, "nodes": nodes, "nps": 1000.0 * nodes / max(ms, 1),
            "backends": sorted(set(BACKEND_RE.findall(out))),
            "searches_with_backend_line": len(BACKEND_RE.findall(out))}


def session(exe: Path, label: str, cwd: Path, setup: list[str], syzygy: str,
            timeout: float) -> tuple[list[str], list[dict]]:
    """Drive the engine the way the AtomicDB worker does; return its normalised output and progress."""
    proc = subprocess.Popen([str(exe)], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, text=True, encoding="utf-8",
                            errors="replace", cwd=str(cwd), bufsize=1, creationflags=NO_WINDOW)
    killer = threading.Timer(timeout, proc.kill)
    killer.start()
    transcript: list[str] = []
    progress: list[dict] = []
    last_info: list[str] = [""]

    def send(line: str) -> None:
        proc.stdin.write(line + "\n")
        proc.stdin.flush()

    def read_until(token: str) -> None:
        while True:
            line = proc.stdout.readline()
            if not line:
                raise RuntimeError(f"{exe.name}: engine closed its output while waiting for {token}"
                                   f" (killed after {timeout:.0f}s?)")
            line = line.rstrip("\r\n")
            if (line.startswith("info depth") or line.startswith("bestmove") or "tablebase" in line.lower()
                    or "NNUE evaluation using" in line):
                if line.startswith("info depth"):
                    last_info[0] = line
                line = re.sub(r" nps \d+", "", line)
                line = re.sub(r" time \d+", "", line)
                transcript.append(line)
            if line.startswith(token):
                return

    try:
        send("uci")
        read_until("uciok")
        send("setoption name Threads value 1")
        send("setoption name Hash value 256")
        for line in setup:
            send(line)
        if syzygy:
            send(f"setoption name SyzygyPath value {syzygy}")
        send("isready")
        read_until("readyok")
        for i, (fen, nodes, multipv, searchmoves) in enumerate(SESSION, 1):
            transcript.append(f"## {fen} nodes {nodes} multipv {multipv} searchmoves {searchmoves}")
            send(f"setoption name MultiPV value {multipv}")
            send(f"position fen {fen}")
            go = f"go nodes {nodes}"
            if searchmoves:
                go += " searchmoves " + " ".join(searchmoves)
            last_info[0] = ""
            t0 = time.time()
            send(go)
            read_until("bestmove")
            seconds = time.time() - t0
            m = INFO_RE.match(last_info[0])
            row = {"position": i, "fen": fen, "go_nodes": nodes, "seconds": round(seconds, 2),
                   "depth": int(m.group(1)) if m else None, "seldepth": int(m.group(2)) if m else None,
                   "nodes": int(m.group(3)) if m else None, "bestmove": transcript[-1].split()[1]}
            progress.append(row)
            print(f"   [{label}] session {i}/{len(SESSION)}: go nodes {nodes} -> {row['bestmove']}, "
                  f"depth {row['depth']} seldepth {row['seldepth']}, {row['nodes']} nodes, {seconds:.1f}s",
                  flush=True)
        send("quit")
        proc.wait(timeout=20)
    finally:
        killer.cancel()
        if proc.poll() is None:
            proc.kill()
    return transcript, progress


def ci95(values: list[float]) -> float:
    n = len(values)
    if n < 2:
        return float("nan")
    t = T95.get(n - 1) or (2.0 if n - 1 > 29 else T95[max(k for k in T95 if k <= n - 1)])
    return t * statistics.stdev(values) / math.sqrt(n)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--bin", action="append", required=True, metavar="LABEL=PATH",
                    help="first one is the reference the others are compared with")
    ap.add_argument("--cwd", type=Path, required=True, help="working directory of every engine run")
    ap.add_argument("--net", default="default", help="label of the network, stored in the report")
    ap.add_argument("--evalfile", default="", help="network every engine process loads first (optional)")
    ap.add_argument("--expect-backend", required=True,
                    help='backend every search must report, e.g. "Legacy Atomic V1" or "AtomicNNUEV2"')
    ap.add_argument("--expect-bench", type=int, default=None,
                    help="node count of the signature bench; without it, the first binary's count")
    ap.add_argument("--limit-type", choices=("depth", "nodes"), default="depth",
                    help="limit type of the timed bench and of a --signature-limit bench")
    ap.add_argument("--limit", "--depth", dest="limit", type=int, default=18,
                    help="limit (depth or nodes per position) of the timed bench")
    ap.add_argument("--signature-limit", type=int, default=None,
                    help="signature bench `bench 16 1 <this> default <limit type>` instead of `bench`")
    ap.add_argument("--rounds", type=int, default=8)
    ap.add_argument("--timeout", type=float, default=1800, help="seconds allowed to one bench process")
    ap.add_argument("--session-timeout", type=float, default=900, help="seconds allowed to one session")
    ap.add_argument("--cpu", type=int, default=None, help="logical CPU to pin every engine to")
    ap.add_argument("--syzygy", default="", help="SyzygyPath for the session (optional)")
    ap.add_argument("--skip-session", action="store_true")
    ap.add_argument("--json", type=Path)
    ap.add_argument("--md", type=Path, help="append a Markdown summary here")
    ap.add_argument("--title", default="engine check")
    a = ap.parse_args()

    bins = [(b.split("=", 1)[0], Path(b.split("=", 1)[1]).resolve()) for b in a.bin]
    ref = bins[0][0]
    evalfile = str(Path(a.evalfile).resolve()) if a.evalfile else ""
    if evalfile and not Path(evalfile).is_file():
        print(f"no network at {evalfile}", flush=True)
        return 1
    setup = [f"setoption name EvalFile value {evalfile}"] if evalfile else []
    signature_cmd = ("bench" if a.signature_limit is None
                     else f"bench 16 1 {a.signature_limit} default {a.limit_type}")
    timed_cmd = f"bench 16 1 {a.limit} default {a.limit_type}"
    failures: list[str] = []
    result: dict = {"title": a.title, "net": a.net, "evalfile": evalfile or None,
                    "expect_backend": a.expect_backend, "expect_bench": a.expect_bench,
                    "signature_cmd": signature_cmd, "timed_cmd": timed_cmd,
                    "host": {"platform": platform.platform(), "machine": platform.machine(),
                             "processor": platform.processor()},
                    "reference": ref, "binaries": {}, "depth": a.limit, "limit_type": a.limit_type,
                    "rounds": a.rounds}
    print(f"# {a.title}\nhost: {platform.platform()} / {platform.machine()} / {platform.processor()}", flush=True)
    print(f"network: {evalfile or 'the default network of each binary'}; expected backend: {a.expect_backend}; "
          f"signature: {signature_cmd}; timed: {timed_cmd}", flush=True)

    # 1. what each binary says about itself
    for label, exe in bins:
        d = self_description(exe, a.cwd)
        d["size"] = exe.stat().st_size
        result["binaries"][label] = {"path": str(exe), "self": d}
        print(f"[{label}] {d['id_name']} | {d['compiled_by']} | {d['arch']} | {d['settings']} | {d['size']} bytes",
              flush=True)
    print(f"[{ref}] UCI options: {', '.join(result['binaries'][ref]['self']['options'])}", flush=True)

    # 2. bench signature: the backend every search used, and the node count
    expected_bench = a.expect_bench
    for label, exe in bins:
        t0 = time.time()
        try:
            b = bench(exe, a.cwd, setup, signature_cmd, a.cpu, a.timeout)
        except RuntimeError as exc:
            msg = str(exc).splitlines()[0]
            print(f"[{label}] signature bench FAILED after {time.time() - t0:.0f}s: {msg}", flush=True)
            failures.append(f"{label}: signature bench: {msg}")
            result["binaries"][label]["signature"] = {"nodes": None, "backends": [], "ok": False, "error": msg}
            continue
        if expected_bench is None:
            expected_bench = b["nodes"]  # the reference binary sets it
        ok = b["backends"] == [a.expect_backend] and b["nodes"] == expected_bench
        result["binaries"][label]["signature"] = {"nodes": b["nodes"], "backends": b["backends"],
                                                  "searches_with_backend_line": b["searches_with_backend_line"],
                                                  "ms": b["ms"], "ok": ok}
        print(f"[{label}] signature {b['nodes']} nodes, backend {' / '.join(b['backends']) or 'none reported'} "
              f"({b['searches_with_backend_line']} searches, {b['ms']} ms)  {'OK' if ok else 'MISMATCH'}", flush=True)
        if not ok:
            failures.append(f"{label}: signature {b['nodes']} nodes with backend {b['backends']}, expected "
                            f"{expected_bench} with {a.expect_backend}")
    result["expected_bench_used"] = expected_bench

    # 3. worker-like session, output must be identical
    if not a.skip_session:
        base = None
        for label, exe in bins:
            try:
                tr, progress = session(exe, label, a.cwd, setup, a.syzygy, a.session_timeout)
            except RuntimeError as exc:
                msg = str(exc).splitlines()[0]
                print(f"[{label}] session FAILED: {msg}", flush=True)
                failures.append(f"{label}: session: {msg}")
                result["binaries"][label]["session"] = {"error": msg}
                continue
            infos = sum(1 for l in tr if l.startswith("info depth"))
            tb = [l for l in tr if "tablebase" in l.lower()]
            backends = sorted(set(m for l in tr for m in BACKEND_RE.findall(l)))
            result["binaries"][label]["session"] = {"lines": len(tr), "info_lines": infos, "backends": backends,
                                                    "tablebase_lines": tb[:3], "progress": progress}
            if backends != [a.expect_backend]:
                failures.append(f"{label}: session backend {backends}, expected {a.expect_backend}")
            if base is None:
                base = tr
                print(f"[{label}] session: {len(tr)} lines ({infos} info lines), backend {' / '.join(backends)}"
                      f"{'; ' + tb[0] if tb else ''}", flush=True)
                result["session_tail"] = tr[-3:]
            else:
                same = tr == base
                result["binaries"][label]["session"]["identical_to_reference"] = same
                print(f"[{label}] session: {len(tr)} lines, identical to {ref}: {same}", flush=True)
                if not same:
                    failures.append(f"{label}: session output differs from {ref}")
                    for i, (x, y) in enumerate(zip(base, tr)):
                        if x != y:
                            print(f"   first difference at line {i}:\n   {ref}: {x}\n   {label}: {y}", flush=True)
                            break

    # 4. timing
    if a.rounds > 0:
        samples: dict[str, list[float]] = {label: [] for label, _ in bins}
        nodes_seen: set[int] = set()
        backends_seen: set[str] = set()
        try:
            for label, exe in bins:  # one discarded warm-up each
                m = bench(exe, a.cwd, setup, timed_cmd, a.cpu, a.timeout)
                nodes_seen.add(m["nodes"])
                backends_seen.update(m["backends"])
            for r in range(a.rounds):
                # Rotate by r // 2 and reverse every other round: over 2 * len(bins) rounds
                # every binary starts every position in both directions. (Rotating by r
                # instead gives the same order in every round when there are two binaries.)
                k = (r // 2) % len(bins)
                order = bins[k:] + bins[:k]
                if r % 2:
                    order = order[::-1]
                for label, exe in order:
                    m = bench(exe, a.cwd, setup, timed_cmd, a.cpu, a.timeout)
                    nodes_seen.add(m["nodes"])
                    backends_seen.update(m["backends"])
                    samples[label].append(m["nps"])
                print(f"round {r + 1}/{a.rounds}: " + "  ".join(f"{l}={samples[l][-1]:.0f}" for l, _ in bins),
                      flush=True)
        except RuntimeError as exc:
            msg = str(exc).splitlines()[0]
            print(f"timed bench FAILED: {msg}", flush=True)
            failures.append(f"timed bench: {msg}")
        if len(nodes_seen) > 1:
            failures.append(f"{timed_cmd}: node counts differ between runs or binaries: {sorted(nodes_seen)}")
        if backends_seen and sorted(backends_seen) != [a.expect_backend]:
            failures.append(f"{timed_cmd}: backends {sorted(backends_seen)}, expected {a.expect_backend}")
        print(f"{timed_cmd}: nodes in every run of every binary = {sorted(nodes_seen)}, "
              f"backend {' / '.join(sorted(backends_seen))}", flush=True)
        n = min(len(v) for v in samples.values())
        for label, _ in bins:
            v = samples[label][:n]
            if not v:
                continue
            ratios = [x / y for x, y in zip(v, samples[ref][:n])]
            row = {"nodes": sorted(nodes_seen), "backends": sorted(backends_seen),
                   "mean_nps": statistics.mean(v), "median_nps": statistics.median(v),
                   "sd": statistics.stdev(v) if len(v) > 1 else 0.0, "ci95": ci95(v), "min": min(v), "max": max(v),
                   "vs_ref_pct": 100 * (statistics.mean(ratios) - 1),
                   "vs_ref_ci95_pct": 100 * ci95(ratios) if label != ref else 0.0, "samples": v}
            result["binaries"][label]["timing"] = row
            print(f"  {label:14s} {row['mean_nps']:10.0f} +/- {row['ci95']:7.0f} nps (95%)  median {row['median_nps']:.0f}  "
                  f"vs {ref}: {row['vs_ref_pct']:+6.2f}% +/- {row['vs_ref_ci95_pct']:.2f}", flush=True)
        result["rounds_completed"] = n

    result["failures"] = failures
    if a.json:
        a.json.write_text(json.dumps(result, indent=1))
    if a.md:
        with a.md.open("a", encoding="utf-8") as md:
            md.write(f"### {a.title}\n\n")
            md.write(f"| binary | says | backend | signature | session | nps ({timed_cmd}) | vs {ref} |\n")
            md.write("|---|---|---|---|---|---|---|\n")
            for label, _ in bins:
                b = result["binaries"][label]
                s = b["self"]
                sig = b.get("signature", {})
                sess = b.get("session", {})
                if "error" in sess:
                    sess_txt = "FAILED"
                elif label == ref:
                    sess_txt = "reference" if sess else "-"
                else:
                    sess_txt = "identical" if sess.get("identical_to_reference") else ("DIFFERENT" if sess else "-")
                t = b.get("timing")
                speed = (f"{t['mean_nps']:.0f} ± {t['ci95']:.0f} | {t['vs_ref_pct']:+.2f}% ± {t['vs_ref_ci95_pct']:.2f}"
                         if t else "- | -")
                md.write(f"| {label} | {s['id_name']}; {s['compiled_by']}; {s['arch']} | "
                         f"{' / '.join(sig.get('backends', []))} | {sig.get('nodes')} | {sess_txt} | {speed} |\n")
            nodes_txt = result["binaries"][ref].get("timing", {}).get("nodes", "-")
            md.write(f"\nhost: `{platform.platform()}`; network: {evalfile or 'default'}; signature: `{signature_cmd}`; "
                     f"timed bench nodes: {nodes_txt}; {result.get('rounds_completed', 0)} alternating rounds.\n\n")
            if failures:
                md.write("**FAILED:** " + "; ".join(failures) + "\n\n")
    if failures:
        print("FAILED:\n  " + "\n  ".join(failures), flush=True)
        return 1
    print("all identity checks passed", flush=True)
    return 0


if __name__ == "__main__":
    t0 = time.time()
    rc = main()
    print(f"elapsed {time.time() - t0:.0f}s", flush=True)
    raise SystemExit(rc)
