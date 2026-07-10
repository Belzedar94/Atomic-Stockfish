#!/usr/bin/env python3
"""Validate the deterministic Atomic binding inventory and fixture corpus."""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path
from typing import Any


HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[1]
WORKSPACE_ROOT = REPO_ROOT.parent
DEFAULT_FAIRY_REPO = WORKSPACE_ROOT / "Fairy-Stockfish"
VALID_STATUSES = {"port", "adapt", "replace", "not-applicable"}
EXPECTED_PERFT = [197326, 1434825, 714499, 148, 61401, 98729, 241478, 17915]


class ValidationError(RuntimeError):
    pass


def load_json(path: Path) -> dict[str, Any]:
    def reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        counts = Counter(key for key, _ in pairs)
        duplicates = sorted(key for key, count in counts.items() if count > 1)
        if duplicates:
            raise ValidationError(f"{path}: duplicate JSON keys: {', '.join(duplicates)}")
        return dict(pairs)

    try:
        return json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=reject_duplicate_keys)
    except (OSError, json.JSONDecodeError) as error:
        raise ValidationError(f"{path}: {error}") from error


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
        raise ValidationError(f"cannot read Fairy git HEAD: {result.stderr.strip()}")
    return result.stdout.strip()


def python_tests(path: Path) -> dict[str, int]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    return {
        node.name: node.lineno
        for parent in tree.body
        if isinstance(parent, ast.ClassDef)
        for node in parent.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name.startswith("test_")
    }


JS_DESCRIBE = re.compile(r"^describe\((['\"])(.*?)\1", re.MULTILINE)


def javascript_tests(path: Path) -> dict[str, int]:
    source = path.read_text(encoding="utf-8")
    result: dict[str, int] = {}
    for match in JS_DESCRIBE.finditer(source):
        name = match.group(2).strip()
        if name in result:
            raise ValidationError(f"duplicate JS describe name: {name}")
        result[name] = source.count("\n", 0, match.start()) + 1
    return result


PERFT_LINE = re.compile(
    r"^\s*expect\s+perft\.exp\s+(?P<variant>\S+)\s+(?P<position>.+?)\s+"
    r"(?P<depth>\d+)\s+(?P<nodes>\d+)(?:\s+(?P<chess960>true))?\s+>\s+/dev/null\s*$",
    re.MULTILINE,
)


def fairy_perft_cases(path: Path) -> list[dict[str, Any]]:
    cases: list[dict[str, Any]] = []
    for match in PERFT_LINE.finditer(path.read_text(encoding="utf-8")):
        position = match.group("position")
        if len(position) >= 2 and position[0] == position[-1] == '"':
            position = position[1:-1]
        cases.append(
            {
                "variant": match.group("variant"),
                "position": position,
                "depth": int(match.group("depth")),
                "nodes": int(match.group("nodes")),
                "chess960": bool(match.group("chess960")),
            }
        )
    if not cases:
        raise ValidationError(f"no Fairy perft invocations parsed from {path}")
    return cases


def compare_inventory_rows(
    label: str, expected: dict[str, int], rows: list[dict[str, Any]], fixture_ids: set[str]
) -> None:
    actual_names = [row.get("sourceId") for row in rows]
    if len(actual_names) != len(set(actual_names)):
        raise ValidationError(f"{label}: duplicate sourceId")
    if set(actual_names) != set(expected):
        missing = sorted(set(expected) - set(actual_names))
        extra = sorted(set(actual_names) - set(expected))
        raise ValidationError(f"{label}: source coverage mismatch; missing={missing}, extra={extra}")

    for row in rows:
        name = row["sourceId"]
        if row.get("line") != expected[name]:
            raise ValidationError(
                f"{label}:{name}: line {row.get('line')} != source line {expected[name]}"
            )
        status = row.get("status")
        if status not in VALID_STATUSES:
            raise ValidationError(f"{label}:{name}: invalid status {status!r}")
        refs = row.get("fixtures")
        if not isinstance(refs, list) or len(refs) != len(set(refs)):
            raise ValidationError(f"{label}:{name}: fixtures must be a unique list")
        unknown = sorted(set(refs) - fixture_ids)
        if unknown:
            raise ValidationError(f"{label}:{name}: unknown fixture ids {unknown}")
        if status == "not-applicable" and refs:
            raise ValidationError(f"{label}:{name}: not-applicable tests cannot cite fixtures")
        if status != "not-applicable" and not refs:
            raise ValidationError(f"{label}:{name}: applicable test has no fixture")
        if not row.get("rationale"):
            raise ValidationError(f"{label}:{name}: missing rationale")


def validate_fixture_document(fixtures: dict[str, Any]) -> set[str]:
    if fixtures.get("schemaVersion") != 1:
        raise ValidationError("atomic-fixtures.json: unsupported schemaVersion")
    if fixtures.get("publicVariants") != ["atomic"]:
        raise ValidationError("atomic-fixtures.json: publicVariants must be exactly ['atomic']")
    oracle = fixtures.get("oracle", {})
    if not re.fullmatch(r"[0-9a-f]{40}", oracle.get("commit", "")):
        raise ValidationError("atomic-fixtures.json: oracle commit is not a lowercase SHA-1")
    if not re.fullmatch(r"[0-9a-f]{64}", oracle.get("engineSha256", "")):
        raise ValidationError("atomic-fixtures.json: oracle engine hash is not a lowercase SHA-256")

    rows = fixtures.get("fixtures")
    if not isinstance(rows, list) or not rows:
        raise ValidationError("atomic-fixtures.json: fixtures must be a non-empty list")
    ids = [row.get("id") for row in rows]
    if ids != sorted(ids) or len(ids) != len(set(ids)):
        raise ValidationError("atomic-fixtures.json: fixture ids must be sorted and unique")

    required_tags = {
        "adjacent-kings",
        "atomic960",
        "castling",
        "checkmate",
        "en-passant",
        "explosion",
        "promotion",
        "stalemate",
    }
    seen_tags: set[str] = set()
    for row in rows:
        fixture_id = row["id"]
        if row.get("variant") != "atomic":
            raise ValidationError(f"{fixture_id}: variant must be atomic")
        if "expected" not in row:
            raise ValidationError(f"{fixture_id}: missing expected value")
        if not row.get("probe") or not row.get("sourceRefs"):
            raise ValidationError(f"{fixture_id}: missing probe or sourceRefs")
        tags = row.get("tags", [])
        if not isinstance(tags, list) or tags != sorted(tags) or len(tags) != len(set(tags)):
            raise ValidationError(f"{fixture_id}: tags must be a sorted unique list")
        seen_tags.update(tags)
        if row.get("probe") == "legal_moves" and row["expected"] != sorted(row["expected"]):
            raise ValidationError(f"{fixture_id}: legal moves are not sorted")

    missing_tags = sorted(required_tags - seen_tags)
    if missing_tags:
        raise ValidationError(f"atomic-fixtures.json: required rule tags missing: {missing_tags}")

    by_id = {row["id"]: row for row in rows}
    expected_checked = {
        "check.adjacent-kings": [],
        "check.bishop": ["e8"],
        "check.queen": ["e8"],
        "check.quiet": [],
        "check.start": [],
    }
    for fixture_id, squares in expected_checked.items():
        binding = by_id[fixture_id].get("bindingExpected", {})
        if binding.get("checkedPieces") != squares:
            raise ValidationError(f"{fixture_id}: checkedPieces contract changed")
        if binding.get("isCheck") != bool(squares):
            raise ValidationError(f"{fixture_id}: isCheck contract changed")
    mate_binding = by_id["result.mate"].get("bindingExpected", {})
    if mate_binding.get("javascript") != "1-0" or mate_binding.get("winner") != "white":
        raise ValidationError("result.mate: black-to-move loss must map to a white win")
    if by_id["result.immediate-king-explosion"]["expected"] != [True, -32000]:
        raise ValidationError("result.immediate-king-explosion: terminal contract changed")
    for fixture_id in ("result.optional-50move", "result.optional-repetition"):
        if by_id[fixture_id]["expected"] != [True, 0]:
            raise ValidationError(f"{fixture_id}: optional draw contract changed")

    perft = [row for row in rows if row.get("probe") == "perft"]
    if [row["expected"] for row in perft] != EXPECTED_PERFT:
        raise ValidationError("atomic-fixtures.json: normative perft vector changed")
    if [row["chess960"] for row in perft] != [False] * 4 + [True] * 4:
        raise ValidationError("atomic-fixtures.json: Atomic/Atomic960 perft split changed")
    return set(ids)


def validate_inventory(
    inventory: dict[str, Any], fixtures: dict[str, Any], fairy_repo: Path
) -> None:
    if inventory.get("schemaVersion") != 1:
        raise ValidationError("inventory.json: unsupported schemaVersion")
    scope = inventory.get("scope", {})
    if scope.get("publicVariants") != ["atomic"]:
        raise ValidationError("inventory.json: publicVariants must be exactly ['atomic']")
    if scope.get("sourceCommit") != fixtures["oracle"]["commit"]:
        raise ValidationError("inventory and fixture oracle commits differ")

    if git_head(fairy_repo) != scope["sourceCommit"]:
        raise ValidationError("Fairy repository is not at the frozen source commit")
    for source in inventory.get("sources", []):
        path = fairy_repo / source["path"]
        if sha256(path) != source["sha256"]:
            raise ValidationError(f"frozen Fairy source hash changed: {source['path']}")

    fixture_ids = {row["id"] for row in fixtures["fixtures"]}
    compare_inventory_rows(
        "pythonTests",
        python_tests(fairy_repo / "test.py"),
        inventory.get("pythonTests", []),
        fixture_ids,
    )
    compare_inventory_rows(
        "javascriptTests",
        javascript_tests(fairy_repo / "tests" / "js" / "test.js"),
        inventory.get("javascriptTests", []),
        fixture_ids,
    )

    policy = inventory.get("perftPolicy", {})
    ported = policy.get("ported", [])
    source_cases = fairy_perft_cases(fairy_repo / "tests" / "perft.sh")
    atomic_source = [case for case in source_cases if case["variant"] == "atomic"]
    normalized_ported = [
        {
            "variant": "atomic",
            "position": row["position"],
            "depth": row["depth"],
            "nodes": row["nodes"],
            "chess960": row["chess960"],
        }
        for row in ported
    ]
    if normalized_ported != atomic_source:
        raise ValidationError("inventory.json: the eight Atomic perft source cases are not exact")
    if policy.get("excluded", {}).get("selector") != "uciVariant != atomic":
        raise ValidationError("inventory.json: non-Atomic perft exclusion is not explicit")
    if any(row["fixture"] not in fixture_ids for row in ported):
        raise ValidationError("inventory.json: perft policy references an unknown fixture")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fairy-repo", type=Path, default=DEFAULT_FAIRY_REPO)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        fixtures = load_json(HERE / "atomic-fixtures.json")
        inventory = load_json(HERE / "inventory.json")
        validate_fixture_document(fixtures)
        validate_inventory(inventory, fixtures, args.fairy_repo.resolve())
    except ValidationError as error:
        print(f"binding fixture validation failed: {error}", file=sys.stderr)
        return 1
    print(
        "binding fixture validation passed: "
        f"{len(fixtures['fixtures'])} fixtures, "
        f"{len(inventory['pythonTests'])} Python tests, "
        f"{len(inventory['javascriptTests'])} JavaScript tests, 8 perft vectors"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
