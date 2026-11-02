#!/usr/bin/env python3
"""Attribute perf samples to source functions, inlined ones included.

    perf_attr.py --script perf.script --binary BIN [--symbolizer llvm-symbolizer-23]
                 --title T --json out.json --md out.md

perf.script is `perf script -F comm,tid,ip,sym,dso --show-mmap-events` of a
recording made with call graphs (--call-graph dwarf). Every address in BIN is
turned into its chain of inlined source functions with llvm-symbolizer (link-time
addresses, computed from the MMAP2 event of BIN and its program headers; return
addresses minus one). Then, per sample:

  self:   the innermost function of the leaf address that is not a trivial helper
          (bitboard/attack helpers, accessors in position.h, simd.h, std:: ...);
          every sample has exactly one owner, so the self column adds up to 100%.
  incl:   every function that appears anywhere in the sample's stack, inlined or
          not, counted once per sample (the "accumulated" share).

Functions are grouped into categories (CATEGORIES below). The callchains are
partial (a copy of the top of the stack), so `incl` is reliable for functions that
sit near the leaves, not for search() itself; the report gives the share of
samples whose chain reaches search() or qsearch().
Standard library only.
"""
from __future__ import annotations

import argparse
import collections
import json
import re
import subprocess
import sys
from pathlib import Path

CATEGORIES = [
    ("king_explosion_move", r"king_explosion_move"),
    ("gives_check", r"Position::gives_check\("),
    ("atomic_in_check", r"Position::atomic_in_check\("),
    ("under_blast_threat", r"Position::under_blast_threat\("),
    ("set_check_info", r"Position::(set_check_info|update_slider_blockers|set_check_squares)\("),
    ("do_move", r"(Position|Worker)::do_move\("),
    ("undo_move", r"(Position|Worker)::undo_move\("),
    ("null_move", r"(Position|Worker)::(do|undo)_null_move\("),
    ("nnue_acc_update", r"AccumulatorStack::update\(|append_changed_indices"),
    ("nnue_acc_refresh", r"AccumulatorStack::refresh(_cached)?\(|append_active_indices"),
    ("nnue_acc_scan", r"AccumulatorStack::(evaluate|evaluate_side|push|pop|reset)\(|requires_refresh"),
    ("nnue_ft_output", r"FeatureTransformer::transform\("),
    ("nnue_dense", r"AffineTransform|ClippedReLU|::propagate\(|find_nnz|SqrClipped"),
    ("nnue_eval_glue", r"Eval::evaluate\(|Network::evaluate|AnyNetwork::evaluate|Worker::evaluate\("),
    ("tt_prefetch", r"prefetch_key|(^|::)prefetch\("),
    ("tt", r"TranspositionTable::|TTWriter::|TTEntry::|TTData"),
    ("movepicker_score", r"MovePicker::score<"),
    ("movepicker_next", r"MovePicker::(next_move|select|MovePicker)|partial_insertion_sort|MoveSorter"),
    ("movegen", r"(^|::)generate<|MoveList<|generate_(pawn_moves|moves|all|promotions)|splat_(pawn_)?moves|make_promotions"),
    ("legal", r"Position::legal\("),
    ("pseudo_legal", r"Position::pseudo_legal\("),
    ("see", r"Position::see_ge\("),
    ("stalemate_guard", r"Position::has_legal_(move|quiet)\("),
    ("draw_rep", r"Position::(is_draw|upcoming_repetition|is_repetition|has_repeated)\("),
    ("histories", r"update_(all_stats|quiet_histories|continuation_histories|correction_history|quiet_stats)|StatsEntry|::Stats<"),
    ("correction_read", r"correction_value|to_corrected_static_eval"),
    ("attackers_to", r"Position::attackers_to"),
    ("tablebases", r"Tablebases::|(^|::)TB::|probe_wdl|probe_dtz"),
    ("qsearch_own", r"Worker::qsearch<"),
    ("search_own", r"Worker::search<|Worker::reduction\(|is_shuffling|value_(to|from)_tt|value_draw"),
    ("iter_deepening", r"Worker::iterative_deepening|Worker::start_searching"),
    ("time_check", r"check_time|TimeManagement|::elapsed|::now\("),
    ("libc", r"^(__)?(memset|memcpy|memmove|memcmp)"),
    ("kernel", r"^\[kernel"),
]
CAT_RE = [(k, re.compile(p)) for k, p in CATEGORIES]

LABELS = {
    "king_explosion_move": "king_explosion_move()",
    "gives_check": "Position::gives_check()",
    "atomic_in_check": "Position::atomic_in_check()",
    "under_blast_threat": "Position::under_blast_threat()",
    "set_check_info": "set_check_info() (slider blockers + check squares)",
    "do_move": "do_move() (Position + Worker)",
    "undo_move": "undo_move() (Position + Worker)",
    "null_move": "do/undo_null_move()",
    "nnue_acc_update": "NNUE V1 accumulator: incremental update()",
    "nnue_acc_refresh": "NNUE V1 accumulator: refresh (cached + full)",
    "nnue_acc_scan": "NNUE V1 accumulator: evaluate_side() scan, push/pop",
    "nnue_ft_output": "NNUE feature transformer output (transform)",
    "nnue_dense": "NNUE dense layers (fc_0 sparse, fc_1, fc_2, ReLUs)",
    "nnue_eval_glue": "NNUE evaluate() glue (Eval/Network::evaluate)",
    "tt_prefetch": "TT prefetch (prefetch_key + prefetch)",
    "tt": "TT probe / first_entry / write",
    "movegen": "movegen (generate<>, MoveList)",
    "legal": "Position::legal()",
    "pseudo_legal": "Position::pseudo_legal()",
    "movepicker_score": "MovePicker::score<>()",
    "movepicker_next": "MovePicker::next_move() (+ select, sort)",
    "see": "Position::see_ge() (atomic SEE)",
    "stalemate_guard": "has_legal_move()/has_legal_quiet()",
    "draw_rep": "is_draw()/upcoming_repetition()",
    "histories": "history tables (reads in scoring/pruning + updates)",
    "correction_read": "correction_value()",
    "attackers_to": "Position::attackers_to()",
    "tablebases": "tablebases",
    "qsearch_own": "qsearch() own code",
    "search_own": "search() own code",
    "iter_deepening": "iterative_deepening()",
    "time_check": "time checks",
    "libc": "libc (memset/memcpy)",
    "kernel": "kernel",
    "other": "other",
}

HELPER_FILE = re.compile(
    r"(^|[/\\])(bitboard\.h|bitboard\.cpp|attacks\.h|attacks\.cpp|types\.h|position\.h|misc\.h|simd\.h|memory\.h|"
    r"movegen\.h|search\.h|score\.h|history\.h)$|^/usr/|[/\\]c\+\+[/\\]|[/\\]include[/\\]c\+\+")
NOT_HELPER_FN = re.compile(r"(^|::)prefetch\(|StatsEntry|::Stats<")

MMAP_RE = re.compile(r"PERF_RECORD_MMAP2?\s+(\d+)/\d+:\s+\[(0x[0-9a-f]+)\((0x[0-9a-f]+)\) @ (0x[0-9a-f]+|\d+)[^\]]*\]:\s+(\S+)\s+(\S.*?)\s*$")
FRAME_RE = re.compile(r"^\s+([0-9a-f]+)\s+(.*?)\s+\(([^()]*)\)\s*$")
HEAD_RE = re.compile(r"^(\S.*?)\s+(\d+)\s*$")
ONELINE_RE = re.compile(r"^(\S.*?)\s+(\d+)\s+([0-9a-f]+)\s+(.*?)\s+\(([^()]*)\)\s*$")


def qualified_name(fn: str) -> str:
    """The qualified name of a demangled function: no return type, no parameter list."""
    s = fn.replace("(anonymous namespace)", "{anon}")
    angle = 0
    cut = len(s)
    for i, ch in enumerate(s):
        if ch == "<":
            angle += 1
        elif ch == ">":
            angle = max(angle - 1, 0)
        elif ch == "(" and angle == 0:
            cut = i
            break
    head = s[:cut]
    # A conversion operator ("...::operator short") has a space in its own name:
    # only spaces before "::operator" can end a return type.
    op = head.find("::operator")
    limit = op if op >= 0 else len(head)
    angle = 0
    start = 0
    for i, ch in enumerate(head[:limit]):
        if ch == "<":
            angle += 1
        elif ch == ">":
            angle = max(angle - 1, 0)
        elif ch == " " and angle == 0:
            start = i + 1
    return head[start:] + ("(" if cut < len(s) else "")


_cat_cache: dict[str, str] = {}


def category(fn: str) -> str:
    c = _cat_cache.get(fn)
    if c is not None:
        return c
    q = qualified_name(fn.split(" [")[0] if not fn.startswith("[") else fn)
    c = "other"
    for k, r in CAT_RE:
        if r.search(q):
            c = k
            break
    _cat_cache[fn] = c
    return c


def load_segments(binary: Path) -> list[tuple[int, int, int]]:
    """(file offset, vaddr, filesz) of the LOAD segments, from readelf -lW."""
    out = subprocess.run(["readelf", "-lW", str(binary)], capture_output=True, text=True, check=True).stdout
    segs = []
    for line in out.splitlines():
        t = line.split()
        if t and t[0] == "LOAD":
            segs.append((int(t[1], 16), int(t[2], 16), int(t[4], 16)))
    return segs


def parse_script(path: Path, bin_name: str):
    samples = []  # list of (tid, [(ip, sym, dso)])
    maps = {}  # pid -> list of (start, len, pgoff)
    cur = None
    with path.open(encoding="utf-8", errors="replace") as f:
        for line in f:
            if "PERF_RECORD_MMAP" in line:
                m = MMAP_RE.search(line)
                if m and Path(m.group(6)).name == bin_name and "x" in m.group(5):
                    pg = m.group(4)
                    maps.setdefault(int(m.group(1)), []).append(
                        (int(m.group(2), 16), int(m.group(3), 16), int(pg, 16) if pg.startswith("0x") else int(pg)))
                continue
            if not line.strip():
                if cur is not None:
                    samples.append(cur)
                    cur = None
                continue
            # Callchain frames start with whitespace and a hex address; sample lines may
            # start with whitespace too, because perf right-aligns the comm field.
            m = FRAME_RE.match(line) if line[0] in " \t" else None
            if m:
                if cur is not None:
                    cur[1].append((int(m.group(1), 16), m.group(2), m.group(3)))
                continue
            if cur is not None:
                samples.append(cur)
                cur = None
            s = line.strip()
            m = ONELINE_RE.match(s)
            if m:
                samples.append((int(m.group(2)), [(int(m.group(3), 16), m.group(4), m.group(5))]))
                continue
            m = HEAD_RE.match(s)
            if m:
                cur = (int(m.group(2)), [])
    if cur is not None:
        samples.append(cur)
    return samples, maps


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--script", required=True, type=Path)
    ap.add_argument("--binary", required=True, type=Path)
    ap.add_argument("--symbolizer", default="llvm-symbolizer")
    ap.add_argument("--title", default="perf attribution")
    ap.add_argument("--json", type=Path)
    ap.add_argument("--md", type=Path)
    ap.add_argument("--top", type=int, default=45)
    a = ap.parse_args()

    bin_name = a.binary.name
    samples, maps = parse_script(a.script, bin_name)
    samples = [s for s in samples if s[1]]
    if not samples:
        print("no samples")
        return 1
    segs = load_segments(a.binary)
    all_maps = [m for v in maps.values() for m in v]
    if not all_maps:
        print(f"no MMAP2 event of {bin_name} in the script")
        return 1

    def link_addr(ip: int):
        for start, length, pgoff in all_maps:
            if start <= ip < start + length:
                off = ip - start + pgoff
                for foff, vaddr, filesz in segs:
                    if foff <= off < foff + filesz:
                        return off - foff + vaddr
        return None

    # link-time address of every frame of BIN (return addresses minus one)
    addrs = set()
    resolved = []
    for tid, frames in samples:
        row = []
        for i, (ip, sym, dso) in enumerate(frames):
            la = link_addr(ip) if Path(dso).name == bin_name else None
            if la is not None and i > 0:
                la -= 1
            if la is not None:
                addrs.add(la)
            row.append((la, sym, dso))
        resolved.append(row)

    addr_list = sorted(addrs)
    proc = subprocess.run([a.symbolizer, f"--obj={a.binary}", "--inlining", "--output-style=JSON"],
                          input="\n".join(f"0x{x:x}" for x in addr_list) + "\n", capture_output=True, text=True)
    if proc.returncode != 0:
        print(f"{a.symbolizer} failed: {proc.stderr[-2000:]}")
        return 1
    chains = {}
    for line in proc.stdout.splitlines():
        if not line.strip():
            continue
        j = json.loads(line)
        x = int(j["Address"], 16)
        chains[x] = [(s.get("FunctionName", "??"), s.get("FileName", "")) for s in j.get("Symbol", [])] or [("??", "")]
    missing = sum(1 for x in addr_list if x not in chains)
    if missing:
        print(f"warning: {missing} of {len(addr_list)} addresses came back without a symbol")

    n = len(resolved)
    self_fn = collections.Counter()
    incl_fn = collections.Counter()
    self_cat = collections.Counter()
    incl_cat = collections.Counter()
    owner_phys = collections.defaultdict(collections.Counter)  # owner fn -> physical function
    agree = total_cmp = reach_search = 0
    depth_sum = 0
    for (tid, frames), row in zip(samples, resolved):
        depth_sum += len(row)
        names_in_stack = set()
        leaf_la, leaf_sym, leaf_dso = row[0]
        if leaf_la is not None:
            ch = chains.get(leaf_la, [("??", "")])
            owner = ch[-1][0]
            for fn, file in ch:
                if not HELPER_FILE.search(file) or NOT_HELPER_FN.search(fn):
                    owner = fn
                    break
            phys = ch[-1][0]
            total_cmp += 1
            base = lambda s: re.split(r"[(<]", s.split(" [")[0])[0].strip()
            if base(phys).split("::")[-1] == base(leaf_sym).split("::")[-1]:
                agree += 1
        else:
            owner = ("[kernel] " if "kernel" in leaf_dso else "") + leaf_sym + f" [{Path(leaf_dso).name}]"
            phys = owner
        self_fn[owner] += 1
        self_cat[category(owner)] += 1
        owner_phys[owner][phys] += 1
        for la, sym, dso in row:
            if la is not None:
                for fn, _ in chains.get(la, []):
                    names_in_stack.add(fn)
            else:
                names_in_stack.add(sym + f" [{Path(dso).name}]")
        for fn in names_in_stack:
            incl_fn[fn] += 1
        cats = {category(fn) for fn in names_in_stack}
        for c in cats:
            incl_cat[c] += 1
        if any(re.search(r"Worker::(q?search)<", fn) for fn in names_in_stack):
            reach_search += 1

    pct = lambda c: 100.0 * c / n
    cats_sorted = sorted(set(self_cat) | set(incl_cat), key=lambda k: -self_cat[k])
    res = {
        "title": a.title, "samples": n, "unique_addresses": len(addr_list),
        "symbolizer_agreement_pct": 100.0 * agree / max(total_cmp, 1),
        "samples_reaching_search_pct": pct(reach_search),
        "mean_frames": depth_sum / n,
        "categories": [{"key": k, "label": LABELS.get(k, k), "self_pct": pct(self_cat[k]), "incl_pct": pct(incl_cat[k])}
                       for k in cats_sorted],
        "functions": [{"function": fn, "self_pct": pct(c), "incl_pct": pct(incl_fn[fn]), "category": category(fn),
                       "inlined_pct": 100.0 * (1 - owner_phys[fn][fn] / max(sum(owner_phys[fn].values()), 1)),
                       "physical": [[p, 100.0 * v / c] for p, v in owner_phys[fn].most_common(3)]}
                      for fn, c in self_fn.most_common(a.top)],
        "incl_top": [{"function": fn, "incl_pct": pct(c)} for fn, c in incl_fn.most_common(a.top)],
    }
    if a.json:
        a.json.write_text(json.dumps(res, indent=1))
    lines = [f"### {a.title}", "",
             f"{n} samples; {len(addr_list)} distinct addresses; leaf symbol agreement with perf "
             f"{res['symbolizer_agreement_pct']:.1f}%; chains reaching search()/qsearch() "
             f"{res['samples_reaching_search_pct']:.1f}%; {res['mean_frames']:.1f} frames per sample.", "",
             "| category | self % | incl % |", "|---|---:|---:|"]
    for c in res["categories"]:
        lines.append(f"| {c['label']} | {c['self_pct']:.2f} | {c['incl_pct']:.2f} |")
    lines += ["", f"Top {a.top} functions by self time (owner = innermost non-helper inlined function):", "",
              "| function | self % | incl % | inlined % | physical function (share) |", "|---|---:|---:|---:|---|"]
    for f in res["functions"]:
        phys = "; ".join(f"{re.sub(r'[(].*', '', p)[:60]} ({s:.0f}%)" for p, s in f["physical"])
        fn = f["function"].replace("|", "\\|")
        lines.append(f"| `{fn[:110]}` | {f['self_pct']:.2f} | {f['incl_pct']:.2f} | {f['inlined_pct']:.0f} | {phys} |")
    text = "\n".join(lines) + "\n"
    if a.md:
        with a.md.open("a", encoding="utf-8") as md:
            md.write(text + "\n")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
