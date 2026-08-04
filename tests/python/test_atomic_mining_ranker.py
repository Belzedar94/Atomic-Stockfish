from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import sys

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools.atomic_mining import common
from tools.atomic_mining import rank_disagreements as ranker


SHA_A = "a" * 64
SHA_B = "b" * 64
SHA_C = "c" * 64
ROOT_FEN = "8/8/8/8/8/8/P7/K6k w - - 0 1"


def _digest(namespace: str, value: object) -> str:
    return common.sha256_bytes(
        common.canonical_json_bytes({"namespace": namespace, "value": value})
    )


def _info(
    *,
    kind: str = "cp",
    value: int = 0,
    bound: str = "exact",
    wdl: list[int] | None = None,
    bestmove: str = "a2a3",
    multipv: int | None = 1,
) -> dict[str, object]:
    return {
        "depth": 7,
        "seldepth": 9,
        "nodes": 1000,
        "multipv": multipv,
        "score": {"kind": kind, "value": value, "bound": bound},
        "wdl": wdl,
        "pv": [bestmove],
    }


def _snapshot(
    *,
    bestmove: str = "a2a3",
    kind: str = "cp",
    value: int = 0,
    bound: str = "exact",
    wdl: list[int] | None = None,
    raw: str = SHA_B,
) -> dict[str, object]:
    principal = _info(
        kind=kind,
        value=value,
        bound=bound,
        wdl=wdl,
        bestmove=bestmove,
    )
    return {
        "bestmove": bestmove,
        "ponder": None,
        "principal": principal,
        "multipv": [copy.deepcopy(principal)],
        "raw_sha256": raw,
        "raw_line_count": 3,
    }


def _candidate() -> dict[str, object]:
    source_sha256 = SHA_A
    game_index = 1
    marker_line = 10
    source_game_id = _digest(
        "atomic-source-game-v1",
        {
            "source_sha256": source_sha256,
            "game_index": game_index,
            "marker_line": marker_line,
        },
    )
    history = ["a2a3"]
    fen = "8/8/8/8/8/P7/8/K6k b - - 1 1"
    return {
        "schema": "atomic-match-position-v1",
        "variant": "atomic",
        "scientific_role": "discovery-only",
        "outcome": None,
        "source": {
            "path": "match.log",
            "sha256": source_sha256,
            "size_bytes": 100,
        },
        "game": {
            "game_index": game_index,
            "marker_line": marker_line,
            "root_fen": ROOT_FEN,
            "root_fen_sha256": _digest("atomic-root-fen-v1", ROOT_FEN),
            "source_game_id": source_game_id,
            "full_game_history_sha256": _digest(
                "atomic-full-game-history-v1",
                {
                    "root_fen": ROOT_FEN,
                    "moves": ["a2a3", "h1h2", "a3a4", "h2h3"],
                },
            ),
            "move_count": 4,
        },
        "position": {
            "root_ply": 1,
            "fen": fen,
            "played_move": "h1h2",
            "legal_move_count": 3,
            "terminal_state": "nonterminal",
            "history_uci": history,
            "full_history_sha256": _digest(
                "atomic-position-full-history-v1",
                {"root_fen": ROOT_FEN, "moves": history},
            ),
            "transposition_key": common.transposition_key(fen),
            "transposition_key_scope": "informational-only",
            "engine_key": "0x123",
            "checkers": [],
        },
    }


def _row(
    *,
    position_id: str = SHA_A,
    component_id: str = SHA_B,
    current: dict[str, object] | None = None,
    teachers: list[dict[str, object]] | None = None,
    restricted: dict[str, object] | None = None,
    stratum: str = "quiet",
) -> dict[str, object]:
    current_value = current or _snapshot(
        bestmove="a2a3", value=0, wdl=[300, 400, 300], raw=SHA_A
    )
    teacher_values = teachers or [
        _snapshot(bestmove="h1h2", value=100, wdl=[600, 300, 100]),
        _snapshot(bestmove="h1h2", value=120, wdl=[620, 280, 100], raw=SHA_C),
    ]
    restricted_value = restricted or _snapshot(
        bestmove=str(current_value["bestmove"]),
        value=20,
        wdl=[400, 400, 200],
        raw=SHA_C,
    )
    return {
        "schema": "atomic-dual-probe-v1",
        "position_id": position_id,
        "component_id": component_id,
        "split": "calibration",
        "candidate": _candidate(),
        "probe_config": {
            "engine": {"path": "engine", "sha256": SHA_A, "size_bytes": 1},
            "current_net": {"path": "v3.nnue", "sha256": SHA_B, "size_bytes": 2},
            "teacher_net": {
                "path": "run3b.nnue",
                "sha256": SHA_C,
                "size_bytes": 3,
            },
            "nodes": 1000,
            "multipv": 1,
            "threads": 1,
            "hash_mb": 16,
            "clear_hash": True,
            "teacher_repeat_count": len(teacher_values),
            "engine_options": [
                {"name": "UCI_Variant", "value": "atomic"},
                {"name": "Threads", "value": 1},
                {"name": "Hash", "value": 16},
                {"name": "MultiPV", "value": 1},
                {"name": "Ponder", "value": False},
                {"name": "SyzygyPath", "value": "<empty>"},
                {"name": "SyzygyProbeLimit", "value": 0},
                {"name": "Use NNUE", "value": "pure"},
            ],
            "probe_order_policy": "position-id-parity-alternating-v1",
        },
        "features": {
            "classifier": "atomic-tactical-strata-v1",
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
        "current": current_value,
        "teacher_repeats": teacher_values,
        "teacher_on_current_move": restricted_value,
    }


def test_stable_teacher_disagreement_regret_and_side_to_move_wdl() -> None:
    ranked = ranker.rank_probe_row(_row())
    metrics = ranked["rank"]
    assert metrics["teacher_stable"] is True
    assert metrics["teacher_bestmove"] == "h1h2"
    assert metrics["teacher_score_kind"] == "cp"
    assert metrics["teacher_score_sign"] == 1
    assert metrics["teacher_score_spread"] == 20
    assert metrics["bestmove_disagreement"] is True
    assert metrics["opposite_mate"] is False
    assert metrics["teacher_regret_wdl_ppm"] == 310_000
    assert metrics["teacher_regret_cp"] == 90
    assert metrics["teacher_regret_basis"] == "wdl_ppm"
    assert metrics["wdl_gap_ppm"] == 510_000
    assert metrics["cp_gap"] == 110
    assert metrics["priority_vector"] == [0, 1, 310_000, 510_000, 110]
    assert metrics["priority_class"] == "stable-bestmove-disagreement"


def test_opposite_mate_is_ranked_before_nonmate_disagreement() -> None:
    mate = _row(
        position_id=SHA_A,
        current=_snapshot(
            bestmove="a2a3", kind="mate", value=-4, wdl=[0, 0, 1000]
        ),
        teachers=[
            _snapshot(
                bestmove="h1h2", kind="mate", value=5, wdl=[1000, 0, 0]
            ),
            _snapshot(
                bestmove="h1h2",
                kind="mate",
                value=7,
                wdl=[1000, 0, 0],
                raw=SHA_C,
            ),
        ],
        restricted=_snapshot(
            bestmove="a2a3", kind="mate", value=-3, wdl=[0, 0, 1000]
        ),
    )
    disagreement = _row(position_id=SHA_B, component_id=SHA_C)
    ranked = ranker.rank_disagreements([disagreement, mate])
    assert [row["position_id"] for row in ranked] == [SHA_A, SHA_B]
    assert ranked[0]["rank"]["opposite_mate"] is True
    assert ranked[0]["rank"]["priority_class"] == "opposite-mate"
    assert [row["rank"]["global_rank"] for row in ranked] == [1, 2]


def test_unstable_teacher_disables_bestmove_disagreement_and_regret() -> None:
    unstable = _row(
        teachers=[
            _snapshot(bestmove="h1h2", value=100, wdl=[600, 300, 100]),
            _snapshot(
                bestmove="h1g1", value=120, wdl=[620, 280, 100], raw=SHA_C
            ),
        ]
    )
    ranked = ranker.rank_probe_row(unstable)
    metrics = ranked["rank"]
    assert metrics["teacher_stable"] is False
    assert metrics["teacher_bestmove"] is None
    assert metrics["bestmove_disagreement"] is False
    assert metrics["teacher_regret_wdl_ppm"] is None
    assert metrics["teacher_regret_cp"] is None
    assert metrics["teacher_regret_basis"] == "none"
    assert metrics["wdl_gap_ppm"] == 510_000
    assert metrics["cp_gap"] == 110
    assert metrics["priority_class"] == "wdl-gap"


def test_numeric_spread_does_not_make_teacher_unstable() -> None:
    row = _row(
        teachers=[
            _snapshot(bestmove="h1h2", value=5, wdl=[510, 300, 190]),
            _snapshot(
                bestmove="h1h2", value=500, wdl=[510, 300, 190], raw=SHA_C
            ),
        ]
    )
    metrics = ranker.rank_probe_row(row)["rank"]
    assert metrics["teacher_stable"] is True
    assert metrics["teacher_score_spread"] == 495


def test_cp_teacher_regret_is_used_when_wdl_is_absent() -> None:
    row = _row(
        current=_snapshot(bestmove="a2a3", value=0, wdl=None),
        teachers=[
            _snapshot(bestmove="h1h2", value=100, wdl=None),
            _snapshot(bestmove="h1h2", value=120, wdl=None, raw=SHA_C),
        ],
        restricted=_snapshot(bestmove="a2a3", value=20, wdl=None, raw=SHA_C),
    )
    metrics = ranker.rank_probe_row(row)["rank"]
    assert metrics["teacher_regret_wdl_ppm"] is None
    assert metrics["teacher_regret_cp"] == 90
    assert metrics["teacher_regret_basis"] == "cp"
    assert metrics["priority_vector"] == [0, 1, 90, 0, 110]


@pytest.mark.parametrize(
    "mutation, message",
    (
        ("reorder", "ordered"),
        ("nnue", "pure/T1/TB-off"),
        ("hash", "pure/T1/TB-off"),
        ("policy", "probe_order_policy"),
    ),
)
def test_engine_options_and_probe_order_are_exact(
    mutation: str, message: str
) -> None:
    row = _row()
    options = row["probe_config"]["engine_options"]
    if mutation == "reorder":
        options[0], options[1] = options[1], options[0]
    elif mutation == "nnue":
        options[-1]["value"] = "true"
    elif mutation == "hash":
        options[2]["value"] = 32
    else:
        row["probe_config"]["probe_order_policy"] = "teacher-first"
    with pytest.raises(ranker.RankContractError, match=message):
        ranker.rank_probe_row(row)


@pytest.mark.parametrize(
    "location",
    ("current", "teacher", "restricted", "teacher_secondary"),
)
def test_any_nonexact_bound_is_rejected(location: str) -> None:
    row = _row()
    if location == "current":
        row["current"]["principal"]["score"]["bound"] = "lower"
        row["current"]["multipv"][0]["score"]["bound"] = "lower"
    elif location == "teacher":
        row["teacher_repeats"][0]["principal"]["score"]["bound"] = "upper"
        row["teacher_repeats"][0]["multipv"][0]["score"]["bound"] = "upper"
    elif location == "restricted":
        row["teacher_on_current_move"]["principal"]["score"]["bound"] = "lower"
        row["teacher_on_current_move"]["multipv"][0]["score"]["bound"] = "lower"
    else:
        secondary = _info(
            value=2, bound="lower", bestmove="a2a4", multipv=2
        )
        row["teacher_repeats"][0]["multipv"].append(secondary)
    with pytest.raises(ranker.RankContractError, match="bound must be exact"):
        ranker.rank_probe_row(row)


def test_restricted_teacher_move_must_match_current_bestmove() -> None:
    row = _row()
    row["teacher_on_current_move"] = _snapshot(
        bestmove="h1h2", value=20, wdl=[400, 400, 200]
    )
    with pytest.raises(
        ranker.RankContractError,
        match="must equal current.bestmove",
    ):
        ranker.rank_probe_row(row)


def test_strict_schema_and_no_fabricated_candidate_outcome() -> None:
    extra = _row()
    extra["surprise"] = 1
    with pytest.raises(ranker.RankContractError, match="fields differ"):
        ranker.rank_probe_row(extra)

    fabricated = _row()
    fabricated["candidate"]["outcome"] = "1/2-1/2"
    with pytest.raises(ranker.RankContractError, match="outcome must be null"):
        ranker.rank_probe_row(fabricated)


@pytest.mark.parametrize(
    "field, value, message",
    (
        ("legal_move_count", 0, "legal_move_count must be >= 1"),
        ("legal_move_count", True, "legal_move_count must be an integer"),
        (
            "terminal_state",
            "no-legal-moves",
            "terminal_state must be nonterminal",
        ),
    ),
)
def test_candidate_pre_move_legality_contract_is_exact(
    field: str,
    value: object,
    message: str,
) -> None:
    row = _row()
    row["candidate"]["position"][field] = value
    with pytest.raises(ranker.RankContractError, match=message):
        ranker.rank_probe_row(row)


def test_duplicate_position_ids_fail_closed() -> None:
    with pytest.raises(ranker.RankContractError, match="must be unique"):
        ranker.rank_disagreements([_row(), _row(component_id=SHA_C)])


def test_component_cannot_cross_splits() -> None:
    calibration = _row(position_id=SHA_A, component_id=SHA_C)
    confirmation = _row(position_id=SHA_B, component_id=SHA_C)
    confirmation["split"] = "confirmation"
    with pytest.raises(ranker.RankContractError, match="multiple splits"):
        ranker.rank_disagreements([calibration, confirmation])


def test_rank_file_writes_canonical_outputs_metrics_and_spread_percentiles(
    tmp_path: Path,
) -> None:
    rows = [
        _row(position_id=SHA_A, component_id=SHA_A, stratum="quiet"),
        _row(
            position_id=SHA_B,
            component_id=SHA_B,
            teachers=[
                _snapshot(bestmove="h1h2", value=10, wdl=[600, 300, 100]),
                _snapshot(
                    bestmove="h1h2", value=50, wdl=[620, 280, 100], raw=SHA_C
                ),
            ],
            stratum="capture",
        ),
        _row(
            position_id=SHA_C,
            component_id=SHA_B,
            current=_snapshot(
                bestmove="a2a3", kind="mate", value=-2, wdl=[0, 0, 1000]
            ),
            teachers=[
                _snapshot(
                    bestmove="h1h2", kind="mate", value=3, wdl=[1000, 0, 0]
                ),
                _snapshot(
                    bestmove="h1h2",
                    kind="mate",
                    value=9,
                    wdl=[1000, 0, 0],
                    raw=SHA_C,
                ),
            ],
            restricted=_snapshot(
                bestmove="a2a3", kind="mate", value=-1, wdl=[0, 0, 1000]
            ),
            stratum="evasion",
        ),
    ]
    source = tmp_path / "dual.jsonl"
    output = tmp_path / "ranked.jsonl"
    metrics_path = tmp_path / "metrics.json"
    common.write_new_jsonl(source, rows)

    ranked, metrics = ranker.rank_file(source, output, metrics_path)

    assert common.load_jsonl(output) == ranked
    loaded_metrics = json.loads(metrics_path.read_text(encoding="utf-8"))
    assert loaded_metrics == metrics
    assert metrics["input"]["sha256"] == hashlib.sha256(
        source.read_bytes()
    ).hexdigest()
    assert metrics["ranked_output"]["sha256"] == hashlib.sha256(
        output.read_bytes()
    ).hexdigest()
    assert metrics["diversity"]["distinct_components"] == 2
    assert metrics["diversity"]["counts_by_primary_stratum"] == {
        "capture": 1,
        "evasion": 1,
        "quiet": 1,
    }
    assert metrics["diversity"]["counts_by_split"] == {"calibration": 3}
    assert metrics["teacher_score_spread_by_kind"]["cp"] == {
        "count": 2,
        "min": 20,
        "max": 40,
        "p50": 20,
        "p95": 40,
    }
    assert metrics["teacher_score_spread_by_kind"]["mate"]["p95"] == 6
    assert metrics["signals"]["counts_by_teacher_regret_basis"] == {
        "wdl_ppm": 3
    }
    assert ranked[0]["rank"]["priority_class"] == "opposite-mate"

    output_before = output.read_bytes()
    metrics_before = metrics_path.read_bytes()
    with pytest.raises(FileExistsError, match="refusing to overwrite"):
        ranker.rank_file(source, output, metrics_path)
    assert output.read_bytes() == output_before
    assert metrics_path.read_bytes() == metrics_before
