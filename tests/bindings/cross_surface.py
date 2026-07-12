#!/usr/bin/env python3
"""Exact Atomic binding parity across native UCI, Python, CJS, and ESM/WASM.

The gate deliberately compares rules and protocol facts, never evaluation
centipawns.  Native UCI participates only where it exposes the same operation;
SAN, capture classification, and claimable results are binding-only APIs.
"""

from __future__ import annotations

import argparse
import importlib.machinery
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from types import ModuleType
from typing import Any, Iterable, Sequence


HERE = Path(__file__).resolve().parent
DEFAULT_FIXTURES = HERE / "atomic-fixtures.json"
JS_HELPER = HERE.parent / "js" / "cross-surface.mjs"
PARITY_PROBES = (
    "legal_moves",
    "get_fen",
    "get_san",
    "gives_check",
    "is_capture",
    "game_result",
    "is_immediate_game_end",
    "is_optional_game_end",
    "perft",
)
NATIVE_PROBES = {
    "legal_moves",
    "get_fen",
    "game_result",
    "is_immediate_game_end",
    "perft",
}

ROOT_LINE = re.compile(r"^\s*([a-h][1-8][a-h][1-8][qrbn]?)\s*:\s*(\d+)\s*$")
NODE_LINE = re.compile(r"^\s*Nodes searched:\s*(\d+)\s*$")
FEN_LINE = re.compile(r"^Fen:\s*(.+?)\s*$")
SCORE_LINE = re.compile(r"\bscore\s+(mate|cp)\s+(-?\d+)\b")
BESTMOVE_NONE = re.compile(r"^bestmove\s+\(none\)\s*$", re.MULTILINE)


class GateFailure(RuntimeError):
    pass


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--native", required=True, type=Path, help="native UCI executable")
    parser.add_argument(
        "--pyffish",
        required=True,
        type=Path,
        help="exact pyffish .pyd/.so file or a directory containing it",
    )
    parser.add_argument("--cjs", required=True, type=Path, help="CommonJS glue artifact")
    parser.add_argument("--esm", required=True, type=Path, help="ES module glue artifact")
    parser.add_argument("--fixtures", type=Path, default=DEFAULT_FIXTURES)
    parser.add_argument("--node", default="node", help="Node executable or command name")
    parser.add_argument("--timeout", type=float, default=60.0, help="timeout per process")
    return parser.parse_args()


def existing_file(path: Path, label: str) -> Path:
    resolved = path.expanduser().resolve()
    if not resolved.is_file():
        raise GateFailure(f"{label} does not exist or is not a file: {resolved}")
    return resolved


def validate_inputs(args: argparse.Namespace) -> dict[str, Path | str]:
    native = existing_file(args.native, "native UCI artifact")
    fixtures = existing_file(args.fixtures, "fixture corpus")
    cjs = existing_file(args.cjs, "CommonJS artifact")
    esm = existing_file(args.esm, "ES module artifact")
    helper = existing_file(JS_HELPER, "JavaScript parity helper")
    existing_file(cjs.parent / "ffish.wasm", "CommonJS WASM artifact")
    existing_file(esm.parent / "ffish.wasm", "ES module WASM artifact")

    pyffish = args.pyffish.expanduser().resolve()
    if not (pyffish.is_file() or pyffish.is_dir()):
        raise GateFailure(f"pyffish artifact does not exist: {pyffish}")

    node_path = shutil.which(args.node)
    if node_path is None:
        candidate = Path(args.node).expanduser().resolve()
        if not candidate.is_file():
            raise GateFailure(f"Node executable was not found: {args.node}")
        node_path = str(candidate)

    if args.timeout <= 0:
        raise GateFailure("--timeout must be positive")
    return {
        "native": native,
        "pyffish": pyffish,
        "cjs": cjs,
        "esm": esm,
        "fixtures": fixtures,
        "helper": helper,
        "node": node_path,
    }


def load_fixtures(path: Path) -> list[dict[str, Any]]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise GateFailure(f"could not read fixture corpus {path}: {error}") from error

    fixtures = [row for row in payload.get("fixtures", []) if row.get("probe") in PARITY_PROBES]
    ids = [row.get("id") for row in fixtures]
    if len(fixtures) != 40:
        raise GateFailure(f"expected 40 selected parity fixtures, found {len(fixtures)}")
    if any(not isinstance(fixture_id, str) or not fixture_id for fixture_id in ids):
        raise GateFailure("every selected fixture must have a non-empty string id")
    if len(set(ids)) != len(ids):
        raise GateFailure("selected parity fixture ids are not unique")

    counts = {probe: sum(row["probe"] == probe for row in fixtures) for probe in PARITY_PROBES}
    required_counts = {
        "legal_moves": 6,
        "get_fen": 6,
        "get_san": 5,
        "gives_check": 5,
        "is_capture": 3,
        "game_result": 4,
        "is_immediate_game_end": 1,
        "is_optional_game_end": 2,
        "perft": 8,
    }
    if counts != required_counts:
        raise GateFailure(f"parity fixture coverage changed: {counts} (expected {required_counts})")
    return fixtures


def side_after_fixture(fixture: dict[str, Any]) -> str:
    fen = fixture.get("fen")
    if not fen:
        position = fixture.get("position", "")
        fen = "w" if position == "startpos" else position.removeprefix("fen ")
    fields = fen.split()
    side = "w" if fixture.get("position") == "startpos" else fields[1]
    if len(fixture.get("moves", [])) % 2:
        side = "b" if side == "w" else "w"
    return side


def result_from_score(score: int, side_to_move: str) -> str:
    if score == 0:
        return "1/2-1/2"
    side_wins = score > 0
    white_wins = (side_to_move == "w") == side_wins
    return "1-0" if white_wins else "0-1"


def expected_value(fixture: dict[str, Any]) -> Any:
    probe = fixture["probe"]
    if probe == "legal_moves":
        return sorted(fixture["expected"])
    if probe == "game_result":
        return fixture["bindingExpected"]["javascript"]
    if probe in {"is_immediate_game_end", "is_optional_game_end"}:
        ended, score = fixture["expected"]
        if not ended:
            return "*"
        return result_from_score(int(score), side_after_fixture(fixture))
    return fixture["expected"]


def import_pyffish(path: Path) -> ModuleType:
    if path.is_file():
        spec = importlib.util.spec_from_file_location("pyffish", path)
        expected_file = path
    else:
        spec = importlib.machinery.PathFinder.find_spec("pyffish", [str(path)])
        expected_file = None
    if spec is None or spec.loader is None:
        raise GateFailure(f"could not find an importable pyffish at {path}")

    module = importlib.util.module_from_spec(spec)
    previous = sys.modules.pop("pyffish", None)
    sys.modules["pyffish"] = module
    try:
        spec.loader.exec_module(module)
    except Exception as error:
        sys.modules.pop("pyffish", None)
        if previous is not None:
            sys.modules["pyffish"] = previous
        raise GateFailure(f"could not import pyffish from {path}: {error}") from error

    actual_file = Path(module.__file__ or "").resolve()
    if expected_file is not None and actual_file != expected_file:
        raise GateFailure(f"pyffish resolved to {actual_file}, expected {expected_file}")
    if expected_file is None and path != actual_file and path not in actual_file.parents:
        raise GateFailure(f"pyffish resolved outside the requested directory: {actual_file}")
    return module


def evaluate_python(pyffish: ModuleType, fixture: dict[str, Any]) -> Any:
    probe = fixture["probe"]
    variant = fixture["variant"]
    fen = fixture.get("fen")
    moves = fixture.get("moves", [])
    chess960 = fixture.get("chess960", False)

    if probe == "legal_moves":
        return sorted(pyffish.legal_moves(variant, fen, moves, chess960))
    if probe == "get_fen":
        return pyffish.get_fen(variant, fen, moves, chess960)
    if probe == "get_san":
        return pyffish.get_san(variant, fen, fixture["move"], chess960)
    if probe == "gives_check":
        return pyffish.gives_check(variant, fen, moves, chess960)
    if probe == "is_capture":
        return pyffish.is_capture(variant, fen, moves, fixture["move"], chess960)
    if probe == "perft":
        return pyffish.perft(variant, fixture["position"], fixture["depth"], chess960)

    final_fen = pyffish.get_fen(variant, fen, moves, chess960)
    side_to_move = final_fen.split()[1]
    if probe == "game_result":
        score = int(pyffish.game_result(variant, fen, moves, chess960))
        return result_from_score(score, side_to_move)
    if probe == "is_immediate_game_end":
        ended, score = pyffish.is_immediate_game_end(variant, fen, moves, chess960)
    elif probe == "is_optional_game_end":
        ended, score = pyffish.is_optional_game_end(variant, fen, moves, chess960)
    else:
        raise GateFailure(f"unsupported Python probe {probe}")
    return result_from_score(int(score), side_to_move) if ended else "*"


def evaluate_all_python(pyffish: ModuleType, fixtures: Iterable[dict[str, Any]]) -> dict[str, Any]:
    try:
        pyffish.set_option("UCI_Variant", "atomic")
        pyffish.set_option("Use NNUE", "false")
        return {fixture["id"]: evaluate_python(pyffish, fixture) for fixture in fixtures}
    except Exception as error:
        if isinstance(error, GateFailure):
            raise
        raise GateFailure(f"Python surface failed: {error}") from error


def run_process(
    command: Sequence[str], *, input_text: str | None, timeout: float, label: str
) -> str:
    startup: dict[str, Any] = {}
    if os.name == "nt":
        startup["creationflags"] = subprocess.CREATE_NO_WINDOW
    try:
        completed = subprocess.run(
            list(command),
            input=input_text,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
            **startup,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise GateFailure(f"{label} failed: {error}") from error
    if completed.returncode:
        details = completed.stderr.strip() or completed.stdout.strip()
        raise GateFailure(f"{label} exited with code {completed.returncode}:\n{details}")
    return completed.stdout


def evaluate_javascript(
    node: str,
    helper: Path,
    fixture_path: Path,
    cjs: Path,
    esm: Path,
    fixtures: Sequence[dict[str, Any]],
    timeout: float,
) -> dict[str, dict[str, Any]]:
    ids = ",".join(fixture["id"] for fixture in fixtures)
    output = run_process(
        (
            node,
            str(helper),
            "--fixtures",
            str(fixture_path),
            "--ids",
            ids,
            "--cjs",
            str(cjs),
            "--esm",
            str(esm),
        ),
        input_text=None,
        timeout=timeout,
        label="JavaScript/WASM parity helper",
    )
    try:
        payload = json.loads(output)
    except json.JSONDecodeError as error:
        raise GateFailure(f"JavaScript helper emitted invalid JSON: {error}\n{output}") from error
    if set(payload) != {"commonjs", "esm"}:
        raise GateFailure(f"JavaScript helper returned unexpected surfaces: {sorted(payload)}")
    return payload


def native_options(chess960: bool) -> list[str]:
    return [
        "setoption name UCI_Variant value atomic",
        "setoption name Use NNUE value false",
        f"setoption name UCI_Chess960 value {'true' if chess960 else 'false'}",
        "isready",
    ]


def position_command(fixture: dict[str, Any]) -> str:
    if fixture["probe"] == "perft":
        base = fixture["position"]
    else:
        base = f"fen {fixture['fen']}"
    moves = fixture.get("moves", [])
    suffix = f" moves {' '.join(moves)}" if moves else ""
    return f"position {base}{suffix}"


def run_native(native: Path, commands: Sequence[str], timeout: float, fixture_id: str) -> str:
    stream = "\n".join(("uci", *commands, "quit", ""))
    return run_process(
        (str(native),),
        input_text=stream,
        timeout=timeout,
        label=f"native UCI fixture {fixture_id}",
    )


def parse_native_fen(output: str) -> str:
    fens = [match.group(1) for line in output.splitlines() if (match := FEN_LINE.match(line))]
    if not fens:
        raise GateFailure(f"native diagnostic output has no Fen line:\n{output}")
    return fens[-1]


def parse_native_nodes(output: str) -> int:
    nodes = [
        int(match.group(1))
        for line in output.splitlines()
        if (match := NODE_LINE.match(line))
    ]
    if not nodes:
        raise GateFailure(f"native perft output has no node total:\n{output}")
    return nodes[-1]


def evaluate_native(native: Path, fixture: dict[str, Any], timeout: float) -> Any:
    probe = fixture["probe"]
    commands = [*native_options(fixture.get("chess960", False)), position_command(fixture)]
    if probe == "legal_moves":
        commands.append("go perft 1")
        output = run_native(native, commands, timeout, fixture["id"])
        return sorted(
            match.group(1) for line in output.splitlines() if (match := ROOT_LINE.match(line))
        )
    if probe == "get_fen":
        commands.append("d")
        return parse_native_fen(run_native(native, commands, timeout, fixture["id"]))
    if probe == "perft":
        commands.append(f"go perft {fixture['depth']}")
        return parse_native_nodes(run_native(native, commands, timeout, fixture["id"]))
    if probe in {"game_result", "is_immediate_game_end"}:
        commands.extend(("d", "go depth 1"))
        output = run_native(native, commands, timeout, fixture["id"])
        if not BESTMOVE_NONE.search(output):
            raise GateFailure(
                f"native terminal fixture {fixture['id']} did not emit bestmove (none)"
            )
        scores = SCORE_LINE.findall(output)
        if not scores:
            raise GateFailure(f"native terminal fixture {fixture['id']} emitted no UCI score")
        kind, raw_value = scores[-1]
        value = int(raw_value)
        side_to_move = parse_native_fen(output).split()[1]
        if kind == "cp" and value == 0:
            return "1/2-1/2"
        if kind == "mate" and value == 0:
            return result_from_score(-1, side_to_move)
        raise GateFailure(
            f"native terminal fixture {fixture['id']} emitted unexpected score {kind} {value}"
        )
    raise GateFailure(f"native UCI does not expose probe {probe}")


def evaluate_all_native(
    native: Path, fixtures: Iterable[dict[str, Any]], timeout: float
) -> dict[str, Any]:
    return {
        fixture["id"]: evaluate_native(native, fixture, timeout)
        for fixture in fixtures
        if fixture["probe"] in NATIVE_PROBES
    }


def compare_values(
    label: str,
    actual: dict[str, Any],
    expected: dict[str, Any],
    fixture_ids: Iterable[str],
) -> list[str]:
    errors: list[str] = []
    ids = list(fixture_ids)
    if set(actual) != set(ids):
        missing = sorted(set(ids) - set(actual))
        extra = sorted(set(actual) - set(ids))
        if missing:
            errors.append(f"{label}: missing fixture ids: {', '.join(missing)}")
        if extra:
            errors.append(f"{label}: unexpected fixture ids: {', '.join(extra)}")
    for fixture_id in ids:
        if fixture_id in actual and actual[fixture_id] != expected[fixture_id]:
            errors.append(
                f"{label} {fixture_id}: expected "
                f"{json.dumps(expected[fixture_id], sort_keys=True)}, got "
                f"{json.dumps(actual[fixture_id], sort_keys=True)}"
            )
    return errors


def compare_surfaces(
    left_label: str,
    left: dict[str, Any],
    right_label: str,
    right: dict[str, Any],
    fixture_ids: Iterable[str],
) -> list[str]:
    errors: list[str] = []
    for fixture_id in fixture_ids:
        if fixture_id in left and fixture_id in right and left[fixture_id] != right[fixture_id]:
            errors.append(
                f"{left_label} != {right_label} for {fixture_id}: "
                f"{json.dumps(left[fixture_id], sort_keys=True)} != "
                f"{json.dumps(right[fixture_id], sort_keys=True)}"
            )
    return errors


def main() -> int:
    try:
        args = parse_args()
        inputs = validate_inputs(args)
        fixtures = load_fixtures(inputs["fixtures"])  # type: ignore[arg-type]
        expected = {fixture["id"]: expected_value(fixture) for fixture in fixtures}
        all_ids = [fixture["id"] for fixture in fixtures]
        native_ids = [fixture["id"] for fixture in fixtures if fixture["probe"] in NATIVE_PROBES]

        pyffish = import_pyffish(inputs["pyffish"])  # type: ignore[arg-type]
        python_values = evaluate_all_python(pyffish, fixtures)
        js_values = evaluate_javascript(
            inputs["node"],  # type: ignore[arg-type]
            inputs["helper"],  # type: ignore[arg-type]
            inputs["fixtures"],  # type: ignore[arg-type]
            inputs["cjs"],  # type: ignore[arg-type]
            inputs["esm"],  # type: ignore[arg-type]
            fixtures,
            args.timeout,
        )
        native_values = evaluate_all_native(
            inputs["native"], fixtures, args.timeout  # type: ignore[arg-type]
        )

        errors: list[str] = []
        errors.extend(compare_values("Python", python_values, expected, all_ids))
        errors.extend(compare_values("CommonJS/WASM", js_values["commonjs"], expected, all_ids))
        errors.extend(compare_values("ES module/WASM", js_values["esm"], expected, all_ids))
        errors.extend(compare_values("Native UCI", native_values, expected, native_ids))
        errors.extend(
            compare_surfaces(
                "Python", python_values, "CommonJS/WASM", js_values["commonjs"], all_ids
            )
        )
        errors.extend(
            compare_surfaces(
                "Python", python_values, "ES module/WASM", js_values["esm"], all_ids
            )
        )
        errors.extend(
            compare_surfaces(
                "CommonJS/WASM",
                js_values["commonjs"],
                "ES module/WASM",
                js_values["esm"],
                all_ids,
            )
        )
        errors.extend(
            compare_surfaces(
                "Native UCI", native_values, "Python", python_values, native_ids
            )
        )
        if errors:
            print("FAIL exact cross-surface parity", file=sys.stderr)
            for error in errors:
                print(f"  {error}", file=sys.stderr)
            return 1

        print(f"PASS Python {len(all_ids)}/{len(all_ids)}")
        print(f"PASS CommonJS/WASM {len(all_ids)}/{len(all_ids)}")
        print(f"PASS ES module/WASM {len(all_ids)}/{len(all_ids)}")
        print(f"PASS Native UCI {len(native_ids)}/{len(native_ids)} (supported operations)")
        print(
            "PASS Exact cross-surface parity "
            f"{len(all_ids)} binding fixtures, {len(native_ids)} native intersections"
        )
        return 0
    except GateFailure as error:
        print(f"FAIL exact cross-surface parity: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
