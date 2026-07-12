#!/usr/bin/env python3
"""Re-evaluate Atomic binding fixtures against the frozen Fairy oracle.

The script never imports an ambient ``pyffish`` installation. It requires an
in-place extension built from the explicitly selected Fairy repository and
checks both its Git commit and module version before probing. ``--check`` is
the normal CI mode; ``--write`` is an explicit fixture-refresh operation.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import importlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any


HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[1]
WORKSPACE_ROOT = REPO_ROOT.parent
DEFAULT_FAIRY_REPO = WORKSPACE_ROOT / "Fairy-Stockfish"
DEFAULT_ENGINE = WORKSPACE_ROOT / "Fairy-Stockfish-atomic-reference" / "src" / "Fairy-Atomic-Reference.exe"
NODE_LINE = re.compile(r"^Nodes searched:\s*(\d+)\s*$", re.MULTILINE)


class ExportError(RuntimeError):
    pass


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def git_head(repo: Path) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if result.returncode:
        raise ExportError(f"cannot read Fairy git HEAD: {result.stderr.strip()}")
    return result.stdout.strip()


def load_oracle(fairy_repo: Path, expected_version: list[int]) -> Any:
    sys.path.insert(0, str(fairy_repo))
    try:
        module = importlib.import_module("pyffish")
    except ImportError as error:
        raise ExportError(
            "no in-place frozen pyffish module; run `python setup.py build_ext --inplace` "
            f"inside {fairy_repo}"
        ) from error
    module_path = Path(module.__file__).resolve()
    if fairy_repo.resolve() not in module_path.parents:
        raise ExportError(f"refusing ambient pyffish module: {module_path}")
    version = list(module.version())
    if version != expected_version:
        raise ExportError(f"pyffish version {version} != frozen version {expected_version}")
    return module


def run_perft(engine: Path, fixture: dict[str, Any]) -> int:
    commands = "\n".join(
        (
            "uci",
            "setoption name UCI_Variant value atomic",
            "setoption name Use NNUE value false",
            f"setoption name UCI_Chess960 value {'true' if fixture['chess960'] else 'false'}",
            f"position {fixture['position']}",
            f"go perft {fixture['depth']}",
            "quit",
            "",
        )
    )
    startup: dict[str, Any] = {}
    if os.name == "nt":
        startup["creationflags"] = subprocess.CREATE_NO_WINDOW
    try:
        result = subprocess.run(
            [str(engine)],
            input=commands,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=120,
            check=False,
            **startup,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise ExportError(f"perft oracle failed for {fixture['id']}: {error}") from error
    if result.returncode:
        raise ExportError(
            f"perft oracle exited {result.returncode} for {fixture['id']}\n{result.stdout}\n{result.stderr}"
        )
    match = NODE_LINE.search(result.stdout)
    if not match:
        raise ExportError(f"no node count for {fixture['id']}\n{result.stdout}")
    return int(match.group(1))


def probe_pyffish(module: Any, fixture: dict[str, Any]) -> Any:
    probe = fixture["probe"]
    variant = fixture["variant"]
    chess960 = fixture.get("chess960", False)
    fen = fixture.get("fen")
    moves = fixture.get("moves", [])
    if probe == "captures_to_hand":
        return module.captures_to_hand(variant)
    if probe == "two_boards":
        return module.two_boards(variant)
    if probe == "start_fen":
        return module.start_fen(variant)
    if probe == "legal_moves":
        return sorted(module.legal_moves(variant, fen, moves, chess960))
    if probe == "gives_check":
        return module.gives_check(variant, fen, moves, chess960)
    if probe == "is_capture":
        return module.is_capture(variant, fen, moves, fixture["move"], chess960)
    if probe == "game_result":
        return module.game_result(variant, fen, moves, chess960)
    if probe == "is_immediate_game_end":
        return list(module.is_immediate_game_end(variant, fen, moves, chess960))
    if probe == "is_optional_game_end":
        return list(module.is_optional_game_end(variant, fen, moves, chess960))
    if probe == "has_insufficient_material":
        return list(module.has_insufficient_material(variant, fen, moves, chess960))
    if probe == "get_san":
        return module.get_san(variant, fen, fixture["move"], chess960)
    if probe == "get_fen":
        return module.get_fen(variant, fen, moves, chess960)
    if probe == "validate_fen":
        return module.validate_fen(fen, variant, chess960)
    if probe == "lifecycle":
        final_fen = module.get_fen(variant, fen, moves, chess960)
        fields = final_fen.split()
        fullmove_number = int(fields[5])
        black_to_move = fields[1] == "b"
        return {
            "fen": final_fen,
            "fenAfterPop": module.get_fen(variant, fen, moves[:-1], chess960),
            "fenAfterReset": module.start_fen(variant),
            "fullmoveNumber": fullmove_number,
            "gamePly": (fullmove_number - 1) * 2 + int(black_to_move),
            "halfmoveClock": int(fields[4]),
            "is960": chess960,
            "legalMoveCount": len(module.legal_moves(variant, fen, moves, chess960)),
            "moveStack": moves,
            "sanMoves": module.get_san_moves(variant, fen, moves, chess960),
            "turn": "black" if black_to_move else "white",
        }
    raise ExportError(f"unsupported probe {probe!r} in {fixture['id']}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--check", action="store_true", help="compare without writing (default)")
    mode.add_argument("--write", action="store_true", help="explicitly refresh oracle-derived expected values")
    parser.add_argument("--fairy-repo", type=Path, default=DEFAULT_FAIRY_REPO)
    parser.add_argument("--engine", type=Path, default=DEFAULT_ENGINE)
    parser.add_argument("--skip-perft", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    fixture_path = HERE / "atomic-fixtures.json"
    document = json.loads(fixture_path.read_text(encoding="utf-8"))
    fairy_repo = args.fairy_repo.resolve()
    engine = args.engine.resolve()

    try:
        if git_head(fairy_repo) != document["oracle"]["commit"]:
            raise ExportError("Fairy repository is not at the frozen fixture commit")
        module = load_oracle(fairy_repo, document["oracle"]["moduleVersion"])
        if not args.skip_perft:
            if not engine.is_file():
                raise ExportError(f"frozen perft engine does not exist: {engine}")
            if sha256(engine) != document["oracle"]["engineSha256"]:
                raise ExportError(f"frozen perft engine SHA-256 mismatch: {engine}")

        refreshed = copy.deepcopy(document)
        mismatches: list[str] = []
        checked = 0
        skipped = 0
        for fixture in refreshed["fixtures"]:
            if fixture["probe"] == "target_contract":
                skipped += 1
                continue
            if fixture["probe"] == "perft":
                if args.skip_perft:
                    skipped += 1
                    continue
                actual = run_perft(engine, fixture)
            else:
                actual = probe_pyffish(module, fixture)
            checked += 1
            if actual != fixture["expected"]:
                mismatches.append(
                    f"{fixture['id']}: expected {json.dumps(fixture['expected'], sort_keys=True)}, "
                    f"oracle returned {json.dumps(actual, sort_keys=True)}"
                )
                fixture["expected"] = actual

        if args.write:
            fixture_path.write_text(
                json.dumps(refreshed, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
            )
            print(f"refreshed {checked} oracle-derived fixtures; preserved {skipped} target contracts")
            return 0
        if mismatches:
            raise ExportError("fixture drift:\n  " + "\n  ".join(mismatches))
    except (ExportError, OSError, KeyError, TypeError, ValueError) as error:
        print(f"Fairy fixture export failed: {error}", file=sys.stderr)
        return 1

    print(f"Fairy fixture check passed: {checked} oracle-derived fixtures, {skipped} skipped contracts")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
