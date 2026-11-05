#!/usr/bin/env python3
"""Check that engine principal variations are legal Atomic / Atomic960 lines.

Reads a JSON list of {"fen": str | null, "moves": [uci...], "chess960": bool,
"pv": [uci...]} from the file given as argument and verifies every PV move
with python-chess's independent Atomic implementation. Prints a JSON summary
and exits non-zero on the first failures.
"""

from __future__ import annotations

import json
import sys

import chess
import chess.variant


def check(case: dict) -> str | None:
    fen = case.get("fen") or chess.STARTING_FEN
    board = chess.variant.AtomicBoard(fen, chess960=bool(case.get("chess960")))
    for move in case.get("moves", []):
        board.push(board.parse_uci(move))
    for ply, text in enumerate(case["pv"]):
        if board.is_variant_end() or board.is_game_over(claim_draw=False):
            return f"PV continues after the game ended at ply {ply}: {text}"
        try:
            move = board.parse_uci(text)
        except ValueError:
            return f"illegal PV move {text} at ply {ply} in {board.fen()}"
        board.push(move)
    return None


def main() -> int:
    with open(sys.argv[1], encoding="utf-8") as stream:
        cases = json.load(stream)
    failures = []
    for case in cases:
        problem = check(case)
        if problem:
            failures.append({"case": case, "problem": problem})
    print(json.dumps({"checked": len(cases), "failures": failures[:20]}, indent=2))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
