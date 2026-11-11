"""Land SPSA block 1 results on a main checkout as plain constants.

The tuning branch is never merged. Its result reaches main as a minimal diff:
the 13 parameters main already declares get their TUNABLE_INT default
changed, and the 47 block-1 parameters are written back into the original
expressions as literals (no new TUNABLE_INT, no UCI option, nothing new in the
public surface). Values are rounded to the nearest integer.

    # 1. dump the final values from OpenBench (Django shell on the server):
    #    t = Test.objects.get(id=<SPSA id>)
    #    print(json.dumps({k: p['value'] for k, p in t.spsa['parameters'].items()}))
    #    and save the line as results.json
    # 2. on a clean checkout of current main:
    python tools/spsa/block1/apply_results.py <main>/src results.json
    # 3. clang-format the touched hunks, build, `bench`, commit with "Bench: N"
    #    and verify with the LTC SPRT described in ob_create_spsa_block1.py.

Parameters whose tuned value rounds to the current default are left alone, so
the diff only shows what moved. EvalOptimismScale enters main only when it
rounds to a non-zero value.
"""
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from tunables import EXISTING, P, S, E  # noqa: E402

NAMES = [p[0] for p in P]


def groups():
    """Yield (file, old, new, [names]) for each snippet of P."""
    cur = None
    for name, _, _, _, _, f, old, new in P:
        if f is not None:
            if cur:
                yield cur
            cur = [f, old, new, [name]]
        else:
            cur[3].append(name)
    if cur:
        yield cur


def main():
    src = Path(sys.argv[1])
    results = json.loads(Path(sys.argv[2]).read_text())
    defaults = {p[0]: p[1] for p in P}
    defaults.update({e[0]: e[1] for e in EXISTING})
    missing = set(defaults) - set(results)
    if missing:
        sys.exit(f"results.json lacks {sorted(missing)}")
    tuned = {k: int(round(float(results[k]))) for k in defaults}
    moved = {k: v for k, v in tuned.items() if v != defaults[k]}

    texts = {}

    def text(f):
        if f not in texts:
            texts[f] = (src / f).read_text(encoding="utf-8")
        return texts[f]

    # 13 parameters already declared on main
    for name, *_ in EXISTING:
        if name not in moved:
            continue
        pat = re.compile(r"(TUNABLE_INT\s+" + name + r"\s*=\s*)-?\d+;")
        hits = [f for f in ("search.h", S) if pat.search(text(f))]
        if len(hits) != 1:
            sys.exit(f"{name}: declaration found in {hits}")
        texts[hits[0]] = pat.sub(lambda m: m.group(1) + str(tuned[name]) + ";", text(hits[0]))

    # 47 block-1 parameters: rewrite the literals in place
    for f, old, new, names in groups():
        if not any(n in moved for n in names):
            continue
        if names == ["EvalOptimismScale"]:
            v = tuned["EvalOptimismScale"]
            block = ("    // Modern Stockfish optimism belongs to its current orthodox net/search\n"
                     "    // calibration and must not leak into the legacy Atomic V1 contract.\n"
                     "    (void) optimism;\n\n")
            if text(f).count(block) != 1:
                sys.exit("EvalOptimismScale: optimism discard block not found")
            texts[f] = text(f).replace(block, "")
            new = (old + "        // Search optimism, scaled by SPSA block 1.\n"
                   f"        v += optimism * {v} / 1024;\n")
        else:
            for n in names:
                new = re.sub(r"\b" + n + r"\b", str(tuned[n]), new)
            new = re.sub(r"\(1024 - (\d+)\)", lambda m: str(1024 - int(m.group(1))), new)
        if text(f).count(old) != 1:
            sys.exit(f"{names}: main text not found exactly once in {f}: {old!r}")
        texts[f] = text(f).replace(old, new)

    for f, t in texts.items():
        (src / f).write_text(t, encoding="utf-8", newline="\n")

    print(f"{len(moved)} of {len(defaults)} parameters moved:")
    for k in sorted(moved, key=lambda k: abs(moved[k] - defaults[k]) / max(1, abs(defaults[k])),
                    reverse=True):
        print(f"  {k:24s} {defaults[k]:>7d} -> {moved[k]:>7d}")


if __name__ == "__main__":
    main()
