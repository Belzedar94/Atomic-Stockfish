#!/usr/bin/env python3
"""Pool several ci/engine_check.py reports that timed the same binaries.

Each report comes from one runner, which timed every binary once per round, in
alternating order, with one network. For every binary this takes the ratio of
its speed to the reference binary's speed in the same round, on the same runner,
and pools those ratios over all the runners of a platform that used the same
network: mean and a 95% Student t interval over all rounds. The per-runner means
and intervals are listed as well, so a speed-up that depends on the processor
shows up. A report without timing (a check that stopped early) still shows its
identity results.

Reports are grouped by the name of the directory that holds them, without a
leading "check-" and a trailing "-<number>" (check-linux-2/check.json -> linux),
and by the network label each report carries ("net").

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


def session_state(report: dict, label: str) -> str:
    s = report["binaries"][label].get("session")
    if not s:
        return "-"
    if "error" in s:
        return "FAILED"
    if label == report["reference"]:
        return "reference"
    return "identical" if s.get("identical_to_reference") else "DIFFERENT"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("reports", nargs="+", type=Path)
    ap.add_argument("--md", type=Path, help="append the Markdown tables here")
    ap.add_argument("--json", type=Path, help="write the pooled numbers here")
    a = ap.parse_args()

    groups: dict[tuple[str, str], list[tuple[Path, dict]]] = {}
    for path in sorted(a.reports):
        report = json.loads(path.read_text())
        platform = re.sub(r"-\d+$", "", re.sub(r"^check-", "", path.parent.name))
        groups.setdefault((platform, report.get("net", "default")), []).append((path, report))

    out: list[str] = []
    pooled_json: dict = {}
    for (platform, net), reports in sorted(groups.items()):
        ref = reports[0][1]["reference"]
        labels = list(reports[0][1]["binaries"])
        rounds = sum(r.get("rounds_completed", r["rounds"]) for _, r in reports)
        timed = sorted({r.get("timed_cmd", f"bench 16 1 {r['depth']} default depth") for _, r in reports})
        sig_cmds = sorted({r.get("signature_cmd", "bench") for _, r in reports})
        out.append(f"### {platform}, network {net}: {len(reports)} runner(s), {rounds} rounds of "
                   f"`{' / '.join(timed)}`, speed relative to {ref}\n")
        out.append(f"Signature bench: `{' / '.join(sig_cmds)}`.\n")
        for _, r in reports:
            fails = "; ".join(r.get("failures", [])) or "none"
            out.append(f"- {r['title']}: identity failures: {fails}")
        out.append("")
        out.append(f"| binary | backend | signature nodes | session | timed-bench nodes | vs {ref} (pooled, 95%) | per runner (95%) |")
        out.append("|---|---|---|---|---|---|---|")
        for label in labels:
            sigs_b = [r["binaries"][label].get("signature", {}) for _, r in reports]
            backends = sorted({b for s in sigs_b for b in s.get("backends", [])})
            sigs = sorted({str(s.get("nodes")) for s in sigs_b})
            sess = sorted({session_state(r, label) for _, r in reports})
            timed_nodes = sorted({n for _, r in reports for n in r["binaries"][label].get("timing", {}).get("nodes", [])})
            pooled: list[float] = []
            per_runner: list[str] = []
            runner_rows: list[dict] = []
            for _, r in reports:
                mine = r["binaries"][label].get("timing", {}).get("samples", [])
                base = r["binaries"][ref].get("timing", {}).get("samples", [])
                ratios = [x / y for x, y in zip(mine, base)]
                if not ratios:
                    per_runner.append("no timing")
                    continue
                pooled += ratios
                m, c = pct(ratios)
                per_runner.append(f"{m:+.2f} ± {c:.2f}")
                runner_rows.append({"title": r["title"], "mean_pct": m, "ci95_pct": c, "rounds": len(ratios)})
            if pooled:
                mean, ci = pct(pooled)
                speed = f"{mean:+.2f}% ± {ci:.2f}"
            else:
                mean, ci, speed = None, None, "no timing"
            out.append("| " + " | ".join([
                label, " / ".join(backends) or "-", " or ".join(sigs), " or ".join(sess),
                " or ".join(map(str, timed_nodes)) or "-",
                speed if label != ref else "reference",
                ", ".join(per_runner) if label != ref else ""]) + " |")
            if label != ref:
                pooled_json.setdefault(f"{platform}/{net}", {})[label] = {
                    "pooled_mean_pct": mean, "pooled_ci95_pct": ci, "rounds": len(pooled),
                    "per_runner": runner_rows, "signature_nodes": sigs, "timed_nodes": timed_nodes,
                    "backends": backends, "session": sess}
        out.append("")

    text = "\n".join(out) + "\n"
    print(text)
    if a.md:
        with a.md.open("a", encoding="utf-8") as md:
            md.write(text)
    if a.json:
        a.json.write_text(json.dumps(pooled_json, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
