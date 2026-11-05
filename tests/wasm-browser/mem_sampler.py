#!/usr/bin/env python3
"""Sample the summed resident memory of every browser process under a path.

    python mem_sampler.py --match <substring of the browser executable path>
                          --out <file> [--interval 0.25]

Runs until the file named by --stop exists (or it is killed), then writes
{"peakBytes", "samples", "peakProcesses"} as JSON to --out. Used by
run-browser-bench.mjs to record browser peak memory per engine run.
"""

from __future__ import annotations

import argparse
import json
import os
import time

import psutil


def sample(match: str) -> tuple[int, int]:
    total = 0
    count = 0
    for process in psutil.process_iter(["exe", "memory_info"]):
        exe = process.info.get("exe") or ""
        memory = process.info.get("memory_info")
        if match.lower() in exe.lower() and memory is not None:
            total += memory.rss
            count += 1
    return total, count


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--match", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--stop", required=True)
    parser.add_argument("--interval", type=float, default=0.25)
    parser.add_argument("--max-seconds", type=float, default=3600)
    args = parser.parse_args()

    peak = 0
    peak_processes = 0
    samples = 0
    deadline = time.monotonic() + args.max_seconds
    while not os.path.exists(args.stop) and time.monotonic() < deadline:
        total, count = sample(args.match)
        samples += 1
        if total > peak:
            peak, peak_processes = total, count
        time.sleep(args.interval)
    with open(args.out, "w", encoding="utf-8") as stream:
        json.dump({"peakBytes": peak, "samples": samples, "peakProcesses": peak_processes}, stream)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
