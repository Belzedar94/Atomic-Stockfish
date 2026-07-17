import copy
import json
import math
import re
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
EVIDENCE = (
    ROOT
    / "docs"
    / "atomic"
    / "evidence"
    / "release-1.0-syzygy-openbench"
    / "results.json"
)
README = EVIDENCE.with_name("README.md")
SHA256 = re.compile(r"[0-9A-F]{64}")
COMMIT = re.compile(r"[0-9a-f]{40}")
NORMAL_95 = 1.959963984540054


class EvidenceError(ValueError):
    pass


def exact_keys(value: object, expected: set[str], label: str) -> dict:
    if type(value) is not dict:
        raise EvidenceError(f"{label} must be an object")
    actual = set(value)
    if actual != expected:
        raise EvidenceError(
            f"{label} keys differ: missing={sorted(expected - actual)}, "
            f"unknown={sorted(actual - expected)}"
        )
    return value


def exact_type(value: object, expected: type, label: str) -> None:
    if type(value) is not expected:
        raise EvidenceError(f"{label} must be {expected.__name__}")


def no_duplicate_keys(pairs: list[tuple[str, object]]) -> dict:
    value = {}
    for key, item in pairs:
        if key in value:
            raise EvidenceError(f"duplicate JSON key: {key}")
        value[key] = item
    return value


def load_evidence(path: Path = EVIDENCE) -> dict:
    return json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=no_duplicate_keys)


def elo(results: list[int]) -> tuple[float, float]:
    count = sum(results)
    mean = sum(index / 4 * result for index, result in enumerate(results)) / count
    variance = (
        sum((index / 4 - mean) ** 2 * result for index, result in enumerate(results))
        / count
    )
    radius = NORMAL_95 * math.sqrt(variance / count)

    def logistic(score: float) -> float:
        score = min(max(score, 0.001), 0.999)
        return -400 * math.log10(1 / score - 1)

    lower, point, upper = map(logistic, (mean - radius, mean, mean + radius))
    return point, max(upper - point, point - lower)


def validate_side(side: object, label: str) -> dict:
    side = exact_keys(side, {"engine", "commit", "options"}, label)
    for key in ("engine", "commit", "options"):
        exact_type(side[key], str, f"{label}.{key}")
    if not COMMIT.fullmatch(side["commit"]):
        raise EvidenceError(f"{label}.commit is not a full commit")
    return side


def validate_games(games: object, label: str) -> dict:
    games = exact_keys(
        games,
        {
            "requested",
            "accepted",
            "wins",
            "draws",
            "losses",
            "pentanomial",
            "crashes",
            "time_losses",
            "time_loss_side",
            "worker_illegal_results",
        },
        label,
    )
    for key in (
        "requested",
        "accepted",
        "wins",
        "draws",
        "losses",
        "crashes",
        "time_losses",
        "worker_illegal_results",
    ):
        exact_type(games[key], int, f"{label}.{key}")
        if games[key] < 0:
            raise EvidenceError(f"{label}.{key} must be non-negative")
    exact_type(games["time_loss_side"], str, f"{label}.time_loss_side")
    penta = games["pentanomial"]
    if type(penta) is not list or len(penta) != 5 or any(type(x) is not int or x < 0 for x in penta):
        raise EvidenceError(f"{label}.pentanomial must contain five non-negative ints")
    if games["accepted"] != games["wins"] + games["draws"] + games["losses"]:
        raise EvidenceError(f"{label} W/D/L does not sum to accepted games")
    if games["accepted"] != 2 * sum(penta):
        raise EvidenceError(f"{label} pentanomial pairs do not sum to accepted games")
    if 2 * games["wins"] + games["draws"] != sum(index * value for index, value in enumerate(penta)):
        raise EvidenceError(f"{label} pentanomial score disagrees with W/D/L")
    if games["requested"] != 2000 or games["accepted"] >= games["requested"]:
        raise EvidenceError(f"{label} must preserve the stopped-before-2000 fact")
    return games


def validate_evidence(value: object) -> None:
    value = exact_keys(
        value,
        {
            "schema",
            "recorded_utc",
            "claim",
            "authority",
            "supporting_artifacts",
            "inputs",
            "healthcheck",
            "tests",
            "subgroups",
        },
        "root",
    )
    if value["schema"] != "atomic-stockfish.syzygy-openbench-evidence.v1":
        raise EvidenceError("unsupported evidence schema")
    exact_type(value["recorded_utc"], str, "recorded_utc")

    claim = exact_keys(
        value["claim"],
        {
            "accepted_by_owner",
            "completion_policy",
            "owner_statement",
            "point_estimate_positive_tests",
            "total_tests",
            "accepted_games",
            "explicitly_not_claimed",
        },
        "claim",
    )
    if claim["accepted_by_owner"] is not True:
        raise EvidenceError("owner waiver must remain explicit")
    if claim["completion_policy"] != "owner-waiver-after-positive-healthcheck":
        raise EvidenceError("unexpected completion policy")
    if claim["point_estimate_positive_tests"] != 6 or claim["total_tests"] != 6:
        raise EvidenceError("claim must remain exactly six of six point-estimate-positive")
    forbidden_claims = {
        "six-of-six-openbench-pass",
        "six-of-six-2000-games-complete",
        "six-of-six-los-gate",
        "aggregate-elo-across-heterogeneous-comparators",
    }
    if set(claim["explicitly_not_claimed"]) != forbidden_claims:
        raise EvidenceError("explicit non-claims changed")

    authority = exact_keys(
        value["authority"],
        {
            "kind",
            "filename",
            "bytes",
            "sha256",
            "openbench_commit_at_execution",
            "result_policy",
        },
        "authority",
    )
    if authority["filename"] != "db.sqlite3.bak-predatagen-20260715-1638":
        raise EvidenceError("authority filename changed")
    if authority["bytes"] != 299008 or not SHA256.fullmatch(authority["sha256"]):
        raise EvidenceError("authority size or digest is invalid")
    if not COMMIT.fullmatch(authority["openbench_commit_at_execution"]):
        raise EvidenceError("OpenBench commit is invalid")

    artifacts = value["supporting_artifacts"]
    if type(artifacts) is not list or len(artifacts) != 2:
        raise EvidenceError("exactly two supporting worker logs are required")
    for index, artifact in enumerate(artifacts):
        artifact = exact_keys(artifact, {"role", "filename", "bytes", "sha256"}, f"artifact[{index}]")
        if type(artifact["bytes"]) is not int or artifact["bytes"] <= 0:
            raise EvidenceError(f"artifact[{index}].bytes is invalid")
        if not SHA256.fullmatch(artifact["sha256"]):
            raise EvidenceError(f"artifact[{index}].sha256 is invalid")

    inputs = exact_keys(
        value["inputs"],
        {
            "atomic_tested_commit",
            "atomic_release_head_at_recording",
            "atomic_bench_signature",
            "syzygy_source_tree_tested",
            "syzygy_source_tree_release",
            "syzygy_driver_test_blob_tested_and_release",
            "syzygy_uci_test_blob_tested_and_release",
            "syzygy_fixture_tree_tested_and_release",
            "fairy_harness_commit",
            "fairy_upstream_commit",
            "network",
            "book",
            "tables",
        },
        "inputs",
    )
    for key in (
        "atomic_tested_commit",
        "atomic_release_head_at_recording",
        "syzygy_source_tree_tested",
        "syzygy_source_tree_release",
        "syzygy_driver_test_blob_tested_and_release",
        "syzygy_uci_test_blob_tested_and_release",
        "syzygy_fixture_tree_tested_and_release",
        "fairy_harness_commit",
        "fairy_upstream_commit",
    ):
        if not COMMIT.fullmatch(inputs[key]):
            raise EvidenceError(f"inputs.{key} is invalid")
    if inputs["syzygy_source_tree_tested"] != inputs["syzygy_source_tree_release"]:
        raise EvidenceError("tested and release Syzygy source trees differ")
    if inputs["atomic_bench_signature"] != 338376:
        raise EvidenceError("playing signature changed")
    network = exact_keys(inputs["network"], {"name", "sha256"}, "inputs.network")
    book = exact_keys(inputs["book"], {"name", "bytes", "sha256"}, "inputs.book")
    if not SHA256.fullmatch(network["sha256"]) or not SHA256.fullmatch(book["sha256"]):
        raise EvidenceError("network or book digest is invalid")
    tables = exact_keys(inputs["tables"], {"inventory_sha256", "files", "bytes", "parts"}, "inputs.tables")
    if not SHA256.fullmatch(tables["inventory_sha256"]):
        raise EvidenceError("table inventory digest is invalid")
    if type(tables["parts"]) is not list or len(tables["parts"]) != 3:
        raise EvidenceError("table inventory must contain three parts")
    table_files = table_bytes = 0
    for index, part in enumerate(tables["parts"]):
        part = exact_keys(part, {"name", "files", "bytes", "official_md5_verification"}, f"tables.parts[{index}]")
        if part["official_md5_verification"] != "pass":
            raise EvidenceError(f"tables.parts[{index}] MD5 verification did not pass")
        table_files += part["files"]
        table_bytes += part["bytes"]
    if (table_files, table_bytes) != (tables["files"], tables["bytes"]):
        raise EvidenceError("table part totals disagree with inventory totals")

    health = exact_keys(
        value["healthcheck"],
        {
            "database_error_tests",
            "worker_crashes",
            "worker_illegal_results",
            "candidate_time_losses",
            "base_time_losses",
            "baseline_uci_variant_warnings",
            "baseline_warning_followup",
            "raw_test_38_attempted_games",
            "database_test_38_accepted_games",
            "pgns_uploaded",
        },
        "healthcheck",
    )
    if any(health[key] != 0 for key in ("database_error_tests", "worker_crashes", "worker_illegal_results", "base_time_losses")):
        raise EvidenceError("healthcheck zero counters changed")
    if health["candidate_time_losses"] != 20:
        raise EvidenceError("candidate time-loss accounting changed")
    if health["raw_test_38_attempted_games"] != 140 or health["database_test_38_accepted_games"] != 134:
        raise EvidenceError("test 38 raw/database distinction changed")
    if health["pgns_uploaded"] is not False:
        raise EvidenceError("PGN availability must not be overstated")

    tests = value["tests"]
    if type(tests) is not list or [test.get("id") for test in tests if type(test) is dict] != list(range(37, 43)):
        raise EvidenceError("tests must be ordered IDs 37 through 42")
    by_id = {}
    accepted_games = 0
    time_losses = 0
    for index, test in enumerate(tests):
        test = exact_keys(
            test,
            {
                "id",
                "comparison",
                "evaluation",
                "tc_class",
                "time_control",
                "hash_mb",
                "book_index",
                "dev",
                "base",
                "games",
                "statistics",
                "database_status",
            },
            f"tests[{index}]",
        )
        test_id = test["id"]
        by_id[test_id] = test
        if test["comparison"] not in {"same-binary-syzygy-on-vs-off", "atomic-syzygy-vs-fairy-no-syzygy"}:
            raise EvidenceError(f"test {test_id} has unknown comparison")
        if test["evaluation"] not in {"nnue", "classical"} or test["tc_class"] not in {"STC", "LTC"}:
            raise EvidenceError(f"test {test_id} has unknown matrix value")
        dev = validate_side(test["dev"], f"test {test_id}.dev")
        base = validate_side(test["base"], f"test {test_id}.base")
        if "SyzygyProbeLimit=6" not in dev["options"] or "SyzygyProbeLimit=0" not in base["options"]:
            raise EvidenceError(f"test {test_id} is not TB6 versus TB0")
        games = validate_games(test["games"], f"test {test_id}.games")
        accepted_games += games["accepted"]
        time_losses += games["time_losses"]
        if games["wins"] <= games["losses"]:
            raise EvidenceError(f"test {test_id} point estimate is not positive")
        statistics = exact_keys(
            test["statistics"],
            {"elo_point", "elo_error_95", "diagnostic_los_percent", "diagnostic_los_display"},
            f"test {test_id}.statistics",
        )
        computed_elo, computed_error = elo(games["pentanomial"])
        if round(computed_elo, 2) != statistics["elo_point"] or round(computed_error, 2) != statistics["elo_error_95"]:
            raise EvidenceError(f"test {test_id} OpenBench Elo arithmetic changed")
        if statistics["elo_point"] <= 0 or not 50 < statistics["diagnostic_los_percent"] <= 100:
            raise EvidenceError(f"test {test_id} positive diagnostics changed")
        status = exact_keys(
            test["database_status"],
            {
                "approved",
                "finished",
                "passed",
                "failed",
                "error",
                "created_utc",
                "updated_utc",
                "stop_event_utc",
                "completion",
            },
            f"test {test_id}.database_status",
        )
        if status["approved"] is not True or status["finished"] is not True:
            raise EvidenceError(f"test {test_id} approval/finished state changed")
        if status["passed"] is not False or status["failed"] is not False or status["error"] is not False:
            raise EvidenceError(f"test {test_id} must not be presented as passed, failed, or errored")
        if status["completion"] != "manually-stopped-before-requested-games" or not status["stop_event_utc"].endswith("Z"):
            raise EvidenceError(f"test {test_id} STOP state changed")

    if accepted_games != claim["accepted_games"] or time_losses != health["candidate_time_losses"]:
        raise EvidenceError("claim/healthcheck totals disagree with tests")
    expected_matrix = {
        37: ("same-binary-syzygy-on-vs-off", "nnue", "STC"),
        38: ("same-binary-syzygy-on-vs-off", "nnue", "LTC"),
        39: ("same-binary-syzygy-on-vs-off", "classical", "STC"),
        40: ("same-binary-syzygy-on-vs-off", "classical", "LTC"),
        41: ("atomic-syzygy-vs-fairy-no-syzygy", "nnue", "STC"),
        42: ("atomic-syzygy-vs-fairy-no-syzygy", "nnue", "LTC"),
    }
    for test_id, expected in expected_matrix.items():
        actual = tuple(by_id[test_id][key] for key in ("comparison", "evaluation", "tc_class"))
        if actual != expected:
            raise EvidenceError(f"test {test_id} coverage matrix changed")

    subgroups = value["subgroups"]
    expected_groups = {
        "same-binary-nnue": [37, 38],
        "same-binary-classical": [39, 40],
        "fairy-baseline-nnue": [41, 42],
    }
    if type(subgroups) is not list or [group.get("name") for group in subgroups if type(group) is dict] != list(expected_groups):
        raise EvidenceError("homogeneous subgroup list changed")
    for group in subgroups:
        group = exact_keys(
            group,
            {
                "name",
                "test_ids",
                "games",
                "wins",
                "draws",
                "losses",
                "pentanomial",
                "elo_point",
                "elo_error_95",
                "diagnostic_los_percent",
            },
            f"subgroup {group.get('name')}",
        )
        ids = expected_groups[group["name"]]
        if group["test_ids"] != ids:
            raise EvidenceError(f"subgroup {group['name']} membership changed")
        members = [by_id[test_id]["games"] for test_id in ids]
        for key in ("accepted", "wins", "draws", "losses"):
            output_key = "games" if key == "accepted" else key
            if group[output_key] != sum(member[key] for member in members):
                raise EvidenceError(f"subgroup {group['name']} {output_key} total changed")
        expected_penta = [sum(member["pentanomial"][index] for member in members) for index in range(5)]
        if group["pentanomial"] != expected_penta:
            raise EvidenceError(f"subgroup {group['name']} pentanomial total changed")
        computed_elo, computed_error = elo(expected_penta)
        if round(computed_elo, 2) != group["elo_point"] or round(computed_error, 2) != group["elo_error_95"]:
            raise EvidenceError(f"subgroup {group['name']} Elo arithmetic changed")


def test_syzygy_openbench_evidence_contract_and_arithmetic() -> None:
    validate_evidence(load_evidence())


def test_syzygy_evidence_schema_rejects_unknown_and_missing_keys() -> None:
    value = load_evidence()
    unknown = copy.deepcopy(value)
    unknown["claim"]["passed"] = True
    with pytest.raises(EvidenceError, match="unknown"):
        validate_evidence(unknown)

    missing = copy.deepcopy(value)
    del missing["tests"][0]["database_status"]["passed"]
    with pytest.raises(EvidenceError, match="missing"):
        validate_evidence(missing)


def test_syzygy_evidence_refuses_completed_or_inconsistent_results() -> None:
    value = load_evidence()
    forged_pass = copy.deepcopy(value)
    forged_pass["tests"][0]["database_status"]["passed"] = True
    with pytest.raises(EvidenceError, match="must not be presented as passed"):
        validate_evidence(forged_pass)

    forged_count = copy.deepcopy(value)
    forged_count["tests"][1]["games"]["accepted"] = 140
    with pytest.raises(EvidenceError, match="W/D/L does not sum"):
        validate_evidence(forged_count)


def test_syzygy_evidence_json_rejects_duplicate_keys() -> None:
    with pytest.raises(EvidenceError, match="duplicate JSON key"):
        json.loads('{"schema": "one", "schema": "two"}', object_pairs_hook=no_duplicate_keys)


def test_syzygy_evidence_readme_is_utf8_without_mojibake() -> None:
    text = README.read_bytes().decode("utf-8", errors="strict")
    assert text.startswith("# Release 1.0 Atomic Syzygy OpenBench evidence\n")
    assert not any(marker in text for marker in ("Ã", "â€“", "â€”", "Â"))
