#!/usr/bin/env python3
"""Pool several ci/engine_check.py reports that timed the same binaries.

Each report comes from one runner, which timed every binary once per round, in
alternating order. For every binary and evaluation mode this takes the ratio of
its speed to the reference binary's speed in the same round, on the same runner,
and pools those ratios over all the runners of a platform: mean and a 95%
Student t interval over all rounds. The per-runner means are listed as well, so a
speed-up that depends on the processor shows up.

Reports are grouped by the name of the directory that holds them, without a
leading "check-" and a trailing "-<number>": check-linux-2/check.json -> linux.

Standard library only.
"""
from __future__ import annotations

import argparse
import json
import math
import re
import statistics
from pathlib import Path

T95 = {1: 12.706, 2: 4.303, 3: 3.182, 4: 2.776, 5: 2.571, 6: 2.447, 7: 2.365, 8: 2.306,
       9: 2.262, 10: 2.228, 11: 2.201, 12: 2.179, 13: 2.160, 14: 2.145, 15: 2.131,
       16: 2.120, 17: 2.110, 18: 2.101, 19: 2.093, 20: 2.086, 24: 2.064, 29: 2.045,
       39: 2.023, 49: 2.010, 59: 2.001}


def t95(df: int) -> float:
    if df in T95:
        return T95[df]
    if df > 59:
        return 1.96 + 2.4 / df
    return T95[max(k for k in T95 if k <= df)]


def pct(ratios: list[float]) -> tuple[float, float]:
    mean = statistics.mean(ratios)
    if len(ratios) < 2:
        return 100 * (mean - 1), float("nan")
    return 100 * (mean - 1), 100 * t95(len(ratios) - 1) * statistics.stdev(ratios) / math.sqrt(len(ratios))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("reports", nargs="+", type=Path)
    ap.add_argument("--md", type=Path, help="append the Markdown tables here")
    a = ap.parse_args()

    groups: dict[str, list[tuple[Path, dict]]] = {}
    for path in sorted(a.reports):
        name = re.sub(r"-\d+$", "", re.sub(r"^check-", "", path.parent.name))
        groups.setdefault(name, []).append((path, json.loads(path.read_text())))

    out: list[str] = []
    for name, reports in sorted(groups.items()):
        ref = reports[0][1]["reference"]
        labels = list(reports[0][1]["binaries"])
        rounds = sum(r["rounds"] for _, r in reports)
        out.append(f"### {name}: {len(reports)} runner(s), {rounds} rounds, speed relative to {ref}\n")
        for _, r in reports:
            fails = "; ".join(r.get("failures", [])) or "none"
            out.append(f"- {r['title']}: identity failures: {fails}")
        out.append("")
        out.append("| binary | bench NNUE / classical | NNUE vs " + ref + " (pooled, 95%) | per runner |"
                   " classical vs " + ref + " (pooled, 95%) | per runner |")
        out.append("|---|---|---|---|---|---|")
        for label in labels:
            sigs = {(r["binaries"][label]["signature"]["nnue"], r["binaries"][label]["signature"]["classical"])
                    for _, r in reports}
            sig = " or ".join(f"{n} / {c}" for n, c in sorted(sigs))
            cells = [label, sig]
            for mode in ("nnue", "classical"):
                pooled: list[float] = []
                per_runner: list[str] = []
                for _, r in reports:
                    mine = r["binaries"][label][f"timing_{mode}"]["samples"]
                    base = r["binaries"][ref][f"timing_{mode}"]["samples"]
                    ratios = [x / y for x, y in zip(mine, base)]
                    pooled += ratios
                    per_runner.append(f"{pct(ratios)[0]:+.2f}")
                mean, ci = pct(pooled)
                cells.append(f"{mean:+.2f}% ± {ci:.2f}" if label != ref else "reference")
                cells.append(", ".join(per_runner) if label != ref else "")
            out.append("| " + " | ".join(cells) + " |")
        out.append("")

    text = "\n".join(out) + "\n"
    print(text)
    if a.md:
        with a.md.open("a", encoding="utf-8") as md:
            md.write(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
