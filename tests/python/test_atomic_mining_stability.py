from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import sys

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools.atomic_mining import common
from tools.atomic_mining import probe_label_stability as stability


ROOT_FEN = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"
POSITION_FEN = "rnbqkbnr/pppppppp/8/8/8/4P3/PPPP1PPP/RNBQKBNR b KQkq - 0 1"


def _digest(namespace: str, value: object) -> str:
    return common.sha256_bytes(
        common.canonical_json_bytes({"namespace": namespace, "value": value})
    )


def _candidate(index: int, *, source_sha256: str = "a" * 64) -> tuple[dict[str, object], str]:
    game_index = index + 1
    marker_line = 10 * index + 1
    history = {"root_fen": ROOT_FEN, "moves": ["e2e3"]}
    full_game_history = {
        "root_fen": ROOT_FEN,
        "moves": ["e2e3", "e7e6"],
    }
    source_game_id = _digest(
        "atomic-source-game-v1",
        {
            "source_sha256": source_sha256,
            "game_index": game_index,
            "marker_line": marker_line,
        },
    )
    history_sha256 = _digest("atomic-position-full-history-v1", history)
    candidate: dict[str, object] = {
        "schema": "atomic-match-position-v1",
        "variant": "atomic",
        "scientific_role": "discovery-only",
        "outcome": None,
        "source": {
            "path": f"C:/evidence/match-{index}.log",
            "sha256": source_sha256,
            "size_bytes": 1000 + index,
        },
        "game": {
            "game_index": game_index,
            "marker_line": marker_line,
            "root_fen": ROOT_FEN,
            "root_fen_sha256": _digest("atomic-root-fen-v1", ROOT_FEN),
            "source_game_id": source_game_id,
            "full_game_history_sha256": _digest(
                "atomic-full-game-history-v1", full_game_history
            ),
            "move_count": 2,
        },
        "position": {
            "root_ply": 1,
            "fen": POSITION_FEN,
            "played_move": "e7e6",
            "legal_move_count": 20,
            "terminal_state": "nonterminal",
            "history_uci": ["e2e3"],
            "full_history_sha256": history_sha256,
            "transposition_key": common.transposition_key(POSITION_FEN),
            "transposition_key_scope": "informational-only",
            "engine_key": f"{index:016X}",
            "checkers": [],
        },
    }
    position_id = common.derive_id(
        "atomic-match-position-id-v1",
        source_game_id,
        history_sha256,
        POSITION_FEN,
    )
    return candidate, position_id


def _info(
    *,
    kind: str = "cp",
    value: int = 30,
    bound: str = "exact",
    wdl: list[int] | None = None,
    move: str = "e7e6",
    lane: int = 1,
) -> dict[str, object]:
    return {
        "depth": 7,
        "seldepth": 12,
        "nodes": 64000,
        "multipv": lane,
        "score": {"kind": kind, "value": value, "bound": bound},
        "wdl": [500, 400, 100] if wdl is None else wdl,
        "pv": [move, "d2d4"],
    }


def _snapshot(
    *,
    kind: str = "cp",
    value: int = 30,
    bound: str = "exact",
    wdl: list[int] | None = None,
    move: str = "e7e6",
    raw_tag: str = "snapshot",
) -> dict[str, object]:
    info = _info(kind=kind, value=value, bound=bound, wdl=wdl, move=move)
    alternate_move = "d7d5" if move != "d7d5" else "e7e6"
    alternate = _info(
        kind=kind,
        value=value - 1,
        bound=bound,
        wdl=wdl,
        move=alternate_move,
        lane=2,
    )
    return {
        "bestmove": move,
        "ponder": "d2d4",
        "principal": deepcopy(info),
        "multipv": [deepcopy(info), alternate],
        "raw_sha256": _digest("raw", raw_tag),
        "raw_line_count": 3,
    }


def _artifact(name: str, character: str) -> dict[str, object]:
    return {
        "path": f"C:/artifacts/{name}",
        "sha256": character * 64,
        "size_bytes": 1234,
    }


def _row(
    index: int,
    *,
    repeats: list[dict[str, object]] | None = None,
    split: str = "calibration",
    stratum: str = "quiet",
    source_sha256: str = "a" * 64,
    nodes: int = 64000,
) -> dict[str, object]:
    candidate, position_id = _candidate(index, source_sha256=source_sha256)
    teacher_repeats = (
        repeats
        if repeats is not None
        else [
            _snapshot(value=30, raw_tag=f"{index}-teacher-0"),
            _snapshot(value=30, raw_tag=f"{index}-teacher-1"),
        ]
    )
    return {
        "schema": "atomic-dual-probe-v1",
        "split": split,
        "position_id": position_id,
        "component_id": _digest("component", index),
        "candidate": candidate,
        "probe_config": {
            "engine": _artifact("atomic.exe", "b"),
            "current_net": _artifact("v3.nnue", "c"),
            "teacher_net": _artifact("run3b.nnue", "d"),
            "nodes": nodes,
            "multipv": 2,
            "threads": 1,
            "hash_mb": 64,
            "clear_hash": True,
            "teacher_repeat_count": len(teacher_repeats),
            "engine_options": [
                {"name": "UCI_Variant", "value": "atomic"},
                {"name": "Threads", "value": 1},
                {"name": "Hash", "value": 64},
                {"name": "MultiPV", "value": 2},
                {"name": "Ponder", "value": False},
                {"name": "SyzygyPath", "value": "<empty>"},
                {"name": "SyzygyProbeLimit", "value": 0},
                {"name": "Use NNUE", "value": "pure"},
            ],
            "probe_order_policy": "position-id-parity-alternating-v1",
        },
        "features": {
            "classifier": "atomic-tactical-v1",
            "in_check": False,
            "post_capture": False,
            "post_capture_in_check": False,
            "teacher_move_capture": False,
            "teacher_move_gives_check": False,
            "teacher_move_promotion": False,
            "forced_chain": False,
            "primary_stratum": stratum,
            "strata": [stratum],
        },
        "current": _snapshot(value=20, raw_tag=f"{index}-current"),
        "teacher_repeats": teacher_repeats,
        "teacher_on_current_move": None,
    }


def _set_score(
    snapshot: dict[str, object],
    *,
    kind: str,
    value: int,
    bound: str = "exact",
) -> None:
    for key in ("principal",):
        info = snapshot[key]
        assert isinstance(info, dict)
        info["score"] = {"kind": kind, "value": value, "bound": bound}
    multipv = snapshot["multipv"]
    assert isinstance(multipv, list)
    info = multipv[0]
    assert isinstance(info, dict)
    info["score"] = {"kind": kind, "value": value, "bound": bound}


def _set_move(snapshot: dict[str, object], move: str) -> None:
    snapshot["bestmove"] = move
    principal = snapshot["principal"]
    assert isinstance(principal, dict)
    principal["pv"] = [move, "d7d5"]
    multipv = snapshot["multipv"]
    assert isinstance(multipv, list)
    lane = multipv[0]
    assert isinstance(lane, dict)
    lane["pv"] = [move, "d7d5"]


def test_stable_report_has_counts_percentiles_and_provenance() -> None:
    first = _row(
        0,
        repeats=[
            _snapshot(
                value=10, wdl=[500, 400, 100], raw_tag="first-a"
            ),
            _snapshot(
                value=30, wdl=[520, 380, 100], raw_tag="first-b"
            ),
        ],
        split="calibration",
        stratum="quiet",
        source_sha256="a" * 64,
    )
    second = _row(
        1,
        repeats=[
            _snapshot(
                value=100, wdl=[550, 350, 100], raw_tag="second-a"
            ),
            _snapshot(
                value=105, wdl=[560, 340, 100], raw_tag="second-b"
            ),
        ],
        split="confirmation",
        stratum="post-explosion",
        source_sha256="e" * 64,
    )

    report = stability.analyze_rows([first, second])

    assert report["schema"] == "atomic-label-stability-v1"
    assert report["total"] == 2
    assert report["exact_rows"] == 2
    assert report["protocol_error_rows"] == 0
    assert report["non_exact_rows"] == 0
    assert report["bestmove_flip_rate"] == 0.0
    assert report["kind_flip_rate"] == 0.0
    assert report["sign_flip_rate"] == 0.0
    assert report["score_spread"] == {
        "cp": {"rows": 2, "p50": 5, "p95": 20},
        "mate": {"rows": 0, "p50": None, "p95": None},
    }
    wdl = report["wdl_expectation_spread"]
    assert isinstance(wdl, dict)
    assert wdl == {"rows": 2, "p50": 0.01, "p95": 0.02}
    counts = report["counts"]
    assert isinstance(counts, dict)
    assert counts["split"] == {"calibration": 1, "confirmation": 1}
    assert counts["primary_stratum"] == {
        "post-explosion": 1,
        "quiet": 1,
    }
    assert counts["source_sha256"] == {"a" * 64: 1, "e" * 64: 1}
    recommendation = report["kill_recommendation"]
    assert isinstance(recommendation, dict)
    assert recommendation == {
        "decision": "GO",
        "kill": False,
        "reasons": [],
        "target_met": {"bestmove": True, "kind": True, "sign": True},
    }
    input_provenance = report["input"]
    assert isinstance(input_provenance, dict)
    assert input_provenance["path"] is None
    assert len(str(input_provenance["sha256"])) == 64
    configs = report["probe_configs"]
    assert isinstance(configs, list)
    assert len(configs) == 1
    assert configs[0]["count"] == 2
    assert configs[0]["config"] == first["probe_config"]
    assert configs[0]["config"]["probe_order_policy"] == (
        "position-id-parity-alternating-v1"
    )
    assert configs[0]["config"]["engine_options"][-1] == {
        "name": "Use NNUE",
        "value": "pure",
    }


def test_bestmove_kind_and_sign_flips_above_one_percent_recommend_kill() -> None:
    rows = [_row(index) for index in range(10)]

    first_repeats = rows[0]["teacher_repeats"]
    assert isinstance(first_repeats, list)
    _set_move(first_repeats[1], "d7d5")

    second_repeats = rows[1]["teacher_repeats"]
    assert isinstance(second_repeats, list)
    _set_score(second_repeats[1], kind="mate", value=4)

    third_repeats = rows[2]["teacher_repeats"]
    assert isinstance(third_repeats, list)
    _set_score(third_repeats[1], kind="cp", value=-30)

    report = stability.analyze_rows(rows)

    assert report["exact_rows"] == 10
    assert report["bestmove_flip_rate"] == 0.1
    assert report["kind_flip_rate"] == 0.1
    assert report["sign_flip_rate"] == 0.1
    recommendation = report["kill_recommendation"]
    assert isinstance(recommendation, dict)
    assert recommendation["decision"] == "KILL"
    assert recommendation["reasons"] == [
        "bestmove-flip-rate-above-limit",
        "kind-flip-rate-above-limit",
        "sign-flip-rate-above-limit",
    ]
    assert recommendation["target_met"] == {
        "bestmove": False,
        "kind": False,
        "sign": False,
    }


def test_exactly_one_percent_is_not_kill_boundary_but_misses_zero_target() -> None:
    rows = [_row(index) for index in range(100)]
    repeats = rows[0]["teacher_repeats"]
    assert isinstance(repeats, list)
    _set_move(repeats[1], "d7d5")

    report = stability.analyze_rows(rows)

    assert report["bestmove_flip_rate"] == 0.01
    recommendation = report["kill_recommendation"]
    assert isinstance(recommendation, dict)
    assert recommendation["decision"] == "GO"
    assert recommendation["reasons"] == []
    assert recommendation["target_met"]["bestmove"] is False


def test_non_exact_bound_is_reported_and_forces_kill() -> None:
    row = _row(0)
    repeats = row["teacher_repeats"]
    assert isinstance(repeats, list)
    _set_score(repeats[1], kind="cp", value=30, bound="lower")

    report = stability.analyze_rows([row])

    assert report["exact_rows"] == 0
    assert report["non_exact_rows"] == 1
    recommendation = report["kill_recommendation"]
    assert isinstance(recommendation, dict)
    assert recommendation["reasons"] == ["non-exact-rows", "no-exact-rows"]


def test_protocol_mismatch_is_preserved_in_kill_sidecar() -> None:
    row = _row(0)
    current = row["current"]
    assert isinstance(current, dict)
    current["bestmove"] = "d7d5"

    report = stability.analyze_rows([row])

    assert report["protocol_error_rows"] == 1
    errors = report["protocol_error_counts"]
    assert isinstance(errors, dict)
    assert errors == {"current:bestmove-pv-mismatch": 1}
    assert report["exact_rows"] == 0
    recommendation = report["kill_recommendation"]
    assert isinstance(recommendation, dict)
    assert recommendation["reasons"] == [
        "protocol-error-rows",
        "no-exact-rows",
    ]


def test_mixed_probe_configs_are_protocol_kill() -> None:
    rows = [_row(0, nodes=64000), _row(1, nodes=128000)]

    report = stability.analyze_rows(rows)

    assert report["protocol_error_rows"] == 2
    assert report["protocol_error_counts"] == {"row:mixed-probe-config": 2}
    hashes = report["probe_config_sha256_counts"]
    assert isinstance(hashes, dict)
    assert len(hashes) == 2
    assert set(hashes.values()) == {1}
    assert report["kill_recommendation"]["decision"] == "KILL"


def test_forced_position_with_fewer_contiguous_lanes_than_config_is_valid() -> None:
    row = _row(0)
    for role in ("current",):
        snapshot = row[role]
        assert isinstance(snapshot, dict)
        snapshot["multipv"] = snapshot["multipv"][:1]
    repeats = row["teacher_repeats"]
    assert isinstance(repeats, list)
    for snapshot in repeats:
        snapshot["multipv"] = snapshot["multipv"][:1]

    report = stability.analyze_rows([row])

    assert report["protocol_error_rows"] == 0
    assert report["exact_rows"] == 1
    assert report["kill_recommendation"]["decision"] == "GO"


def test_null_wdl_is_allowed_but_excluded_from_expectation_spread() -> None:
    repeats = [
        _snapshot(value=10, raw_tag="no-wdl-a"),
        _snapshot(value=20, raw_tag="no-wdl-b"),
    ]
    for snapshot in repeats:
        principal = snapshot["principal"]
        multipv = snapshot["multipv"]
        assert isinstance(principal, dict)
        assert isinstance(multipv, list)
        principal["wdl"] = None
        lane = multipv[0]
        assert isinstance(lane, dict)
        lane["wdl"] = None

    report = stability.analyze_rows([_row(0, repeats=repeats)])

    assert report["wdl_expectation_spread"] == {
        "rows": 0,
        "p50": None,
        "p95": None,
    }
    assert report["kill_recommendation"]["decision"] == "GO"


def test_mate_spread_is_separate_from_cp() -> None:
    row = _row(
        0,
        repeats=[
            _snapshot(kind="mate", value=3, raw_tag="mate-a"),
            _snapshot(kind="mate", value=7, raw_tag="mate-b"),
        ],
    )

    report = stability.analyze_rows([row])

    assert report["score_spread"] == {
        "cp": {"rows": 0, "p50": None, "p95": None},
        "mate": {"rows": 1, "p50": 4, "p95": 4},
    }


def test_empty_input_is_a_reportable_kill() -> None:
    report = stability.analyze_rows([])

    assert report["total"] == 0
    assert report["exact_rows"] == 0
    assert report["bestmove_flip_rate"] is None
    assert report["kill_recommendation"]["reasons"] == ["empty-input"]


@pytest.mark.parametrize(
    "mutation, message",
    (
        (lambda row: row.update({"extra": 1}), "fields differ"),
        (lambda row: row.update({"split": "train"}), "split is invalid"),
        (
            lambda row: row.update({"position_id": "f" * 64}),
            "does not match",
        ),
        (
            lambda row: row["teacher_repeats"].pop(),  # type: ignore[union-attr]
            "at least two",
        ),
        (
            lambda row: row["features"].update({"extra": True}),  # type: ignore[union-attr]
            "fields differ",
        ),
        (
            lambda row: row["probe_config"].update({"threads": 2}),  # type: ignore[union-attr]
            "exactly 1",
        ),
        (
            lambda row: row["probe_config"].update({"clear_hash": False}),  # type: ignore[union-attr]
            "exactly true",
        ),
        (
            lambda row: row["probe_config"].update(  # type: ignore[union-attr]
                {"probe_order_policy": "current-first"}
            ),
            "probe_order_policy",
        ),
        (
            lambda row: row["probe_config"]["engine_options"].reverse(),  # type: ignore[index,union-attr]
            "exact ordered",
        ),
        (
            lambda row: row["candidate"]["position"].update(  # type: ignore[union-attr]
                {"legal_move_count": 0}
            ),
            "legal_move_count must be >= 1",
        ),
        (
            lambda row: row["candidate"]["position"].update(  # type: ignore[union-attr]
                {"legal_move_count": True}
            ),
            "legal_move_count must be an integer",
        ),
        (
            lambda row: row["candidate"]["position"].update(  # type: ignore[union-attr]
                {"terminal_state": "no-legal-moves-in-check"}
            ),
            "terminal_state must be nonterminal",
        ),
    ),
)
def test_structural_wire_violations_fail_closed(
    mutation: object,
    message: str,
) -> None:
    row = _row(0)
    assert callable(mutation)
    mutation(row)
    with pytest.raises(stability.StabilityContractError, match=message):
        stability.analyze_rows([row])


def test_duplicate_position_id_fails_closed() -> None:
    row = _row(0)
    with pytest.raises(stability.StabilityContractError, match="duplicate"):
        stability.analyze_rows([row, deepcopy(row)])


def test_file_api_writes_canonical_provenance_and_never_overwrites(
    tmp_path: Path,
) -> None:
    input_path = tmp_path / "dual-probe.jsonl"
    output_path = tmp_path / "stability.json"
    common.write_new_jsonl(input_path, [_row(0)])

    report = stability.analyze_file(input_path, output_path)

    payload = output_path.read_bytes()
    assert payload == common.canonical_json_bytes(report)
    assert not payload.startswith(b"\xef\xbb\xbf")
    assert b"\r" not in payload
    input_provenance = report["input"]
    assert isinstance(input_provenance, dict)
    assert input_provenance == {
        "path": str(input_path.resolve()),
        "sha256": common.sha256_file(input_path),
        "size_bytes": input_path.stat().st_size,
    }
    with pytest.raises(FileExistsError, match="overwrite"):
        stability.analyze_file(input_path, output_path)
    assert output_path.read_bytes() == payload


def test_noncanonical_input_fails_without_sidecar(tmp_path: Path) -> None:
    input_path = tmp_path / "noncanonical.jsonl"
    output_path = tmp_path / "stability.json"
    input_path.write_bytes((json.dumps(_row(0)) + "\n").encode("utf-8"))

    with pytest.raises(stability.StabilityContractError, match="not canonical"):
        stability.analyze_file(input_path, output_path)
    assert not output_path.exists()


def test_cli_returns_go_or_kill_and_preserves_sidecar(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    stable_input = tmp_path / "stable.jsonl"
    stable_output = tmp_path / "stable-report.json"
    common.write_new_jsonl(stable_input, [_row(0)])

    assert stability.main(
        ["--input", str(stable_input), "--output", str(stable_output)]
    ) == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["schema"] == "atomic-label-stability-v1"
    assert summary["decision"] == "GO"
    assert summary["output_sha256"] == common.sha256_file(stable_output)

    kill_row = _row(1)
    repeats = kill_row["teacher_repeats"]
    assert isinstance(repeats, list)
    _set_move(repeats[1], "d7d5")
    kill_input = tmp_path / "kill.jsonl"
    kill_output = tmp_path / "kill-report.json"
    common.write_new_jsonl(kill_input, [kill_row])

    assert stability.main(
        ["--input", str(kill_input), "--output", str(kill_output)]
    ) == 2
    kill_summary = json.loads(capsys.readouterr().out)
    assert kill_summary["decision"] == "KILL"
    assert json.loads(kill_output.read_text(encoding="utf-8"))[
        "bestmove_flip_rate"
    ] == 1.0
