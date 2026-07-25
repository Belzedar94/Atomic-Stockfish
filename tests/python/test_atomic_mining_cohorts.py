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
from tools.atomic_mining import select_confirmatory_cohorts as selector


ROOT_FEN = "8/8/8/8/8/8/P7/K6k w - - 0 1"


def _sha(label: str) -> str:
    return hashlib.sha256(label.encode("utf-8")).hexdigest()


def _digest(namespace: str, value: object) -> str:
    return common.sha256_bytes(
        common.canonical_json_bytes({"namespace": namespace, "value": value})
    )


def _artifact(label: str) -> dict[str, object]:
    return {"path": label, "sha256": _sha(label), "size_bytes": len(label)}


def _info(
    *,
    value: int,
    bestmove: str,
) -> dict[str, object]:
    return {
        "depth": 7,
        "seldepth": 9,
        "nodes": 1000,
        "multipv": 1,
        "score": {"kind": "cp", "value": value, "bound": "exact"},
        "wdl": None,
        "pv": [bestmove],
    }


def _snapshot(
    *,
    value: int,
    bestmove: str,
    raw_label: str,
) -> dict[str, object]:
    principal = _info(value=value, bestmove=bestmove)
    return {
        "bestmove": bestmove,
        "ponder": None,
        "principal": principal,
        "multipv": [copy.deepcopy(principal)],
        "raw_sha256": _sha(raw_label),
        "raw_line_count": 3,
    }


def _candidate(
    *,
    source_game_id: str,
    label: str,
) -> dict[str, object]:
    source_sha256 = source_game_id
    game_index = 1
    marker_line = 10
    computed_game_id = _digest(
        "atomic-source-game-v1",
        {
            "source_sha256": source_sha256,
            "game_index": game_index,
            "marker_line": marker_line,
        },
    )
    return {
        "schema": "atomic-match-position-v1",
        "variant": "atomic",
        "scientific_role": "discovery-only",
        "outcome": None,
        "source": {
            "path": "source.log",
            "sha256": source_sha256,
            "size_bytes": len("source.log"),
        },
        "game": {
            "game_index": game_index,
            "marker_line": marker_line,
            "root_fen": ROOT_FEN,
            "root_fen_sha256": _digest("atomic-root-fen-v1", ROOT_FEN),
            "source_game_id": computed_game_id,
            "full_game_history_sha256": _digest(
                "atomic-full-game-history-v1",
                {"root_fen": ROOT_FEN, "moves": ["a2a3"]},
            ),
            "move_count": 1,
        },
        "position": {
            "root_ply": 0,
            "fen": ROOT_FEN,
            "played_move": "a2a3",
            "legal_move_count": 3,
            "terminal_state": "nonterminal",
            "history_uci": [],
            "full_history_sha256": _digest(
                "atomic-position-full-history-v1",
                {"root_fen": ROOT_FEN, "moves": []},
            ),
            "transposition_key": common.transposition_key(ROOT_FEN),
            "transposition_key_scope": "informational-only",
            "engine_key": f"key-{label}",
            "checkers": [],
        },
    }


def _dual_row(
    *,
    label: str,
    component: str,
    source_game: str,
    stratum: str,
    teacher_value: int,
    split: str = "confirmation",
) -> dict[str, object]:
    return {
        "schema": "atomic-dual-probe-v1",
        "position_id": _sha(f"position-{label}"),
        "component_id": _sha(f"component-{component}"),
        "split": split,
        "candidate": _candidate(
            source_game_id=_sha(f"game-{source_game}"),
            label=label,
        ),
        "probe_config": {
            "engine": _artifact("engine"),
            "current_net": _artifact("current.nnue"),
            "teacher_net": _artifact("teacher.nnue"),
            "nodes": 1000,
            "multipv": 1,
            "threads": 1,
            "hash_mb": 16,
            "clear_hash": True,
            "teacher_repeat_count": 2,
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
            "post_capture": stratum.startswith("post-capture"),
            "post_capture_in_check": stratum == "post-capture-check",
            "teacher_move_capture": stratum == "capture",
            "teacher_move_gives_check": stratum == "check",
            "teacher_move_promotion": stratum == "promotion",
            "forced_chain": stratum == "forced-chain",
            "primary_stratum": stratum,
            "strata": [stratum],
        },
        "current": _snapshot(
            value=0,
            bestmove="a2a3",
            raw_label=f"current-{label}",
        ),
        "teacher_repeats": [
            _snapshot(
                value=teacher_value,
                bestmove="h1h2",
                raw_label=f"teacher-a-{label}",
            ),
            _snapshot(
                value=teacher_value + 2,
                bestmove="h1h2",
                raw_label=f"teacher-b-{label}",
            ),
        ],
        "teacher_on_current_move": _snapshot(
            value=0,
            bestmove="a2a3",
            raw_label=f"restricted-{label}",
        ),
    }


def _ranked(rows: list[dict[str, object]]) -> list[dict[str, object]]:
    return ranker.rank_disagreements(rows)


def _config(
    *,
    target: int,
    seed: str = "seed-1",
    game_cap: int = 10,
    component_cap: int = 10,
    ceiling: int = 1_000_000,
    min_rows: int = 1,
) -> selector.SelectionConfig:
    return selector.SelectionConfig(
        target_rows=target,
        seed=seed,
        max_rows_per_source_game=game_cap,
        max_rows_per_component=component_cap,
        dominance_ceiling_ppm=ceiling,
        min_rows=min_rows,
    )


def _write_ranked_and_metrics(
    tmp_path: Path,
    rows: list[dict[str, object]],
) -> tuple[Path, Path, str]:
    source = tmp_path / "ranked.jsonl"
    metrics_path = tmp_path / "rank-metrics.json"
    common.write_new_jsonl(source, rows)
    payload = source.read_bytes()
    metrics = ranker.build_metrics(
        rows,
        input_path=tmp_path / "dual-probe.jsonl",
        input_sha256=_sha("dual-probe"),
        input_size_bytes=123,
        output_sha256=common.sha256_bytes(payload),
    )
    common.write_new_json(metrics_path, metrics)
    return source, metrics_path, common.sha256_file(metrics_path)


def test_selects_complete_components_with_exact_multistratum_match() -> None:
    rows = _ranked(
        [
            _dual_row(
                label="a-q",
                component="a",
                source_game="a",
                stratum="quiet",
                teacher_value=500,
            ),
            _dual_row(
                label="a-c",
                component="a",
                source_game="a",
                stratum="capture",
                teacher_value=450,
            ),
            _dual_row(
                label="b-q",
                component="b",
                source_game="b",
                stratum="quiet",
                teacher_value=100,
            ),
            _dual_row(
                label="b-c",
                component="b",
                source_game="b",
                stratum="capture",
                teacher_value=90,
            ),
            _dual_row(
                label="c-q",
                component="c",
                source_game="c",
                stratum="quiet",
                teacher_value=80,
            ),
            _dual_row(
                label="c-c",
                component="c",
                source_game="c",
                stratum="capture",
                teacher_value=70,
            ),
        ]
    )

    result = selector.select_confirmatory_cohorts(
        rows,
        input_sha256=_sha("input"),
        config=_config(target=2),
    )

    assert result.top_component_ids == (_sha("component-a"),)
    assert len(result.top_rows) == len(result.random_rows) == 2
    assert result.counts_by_stratum == {"capture": 1, "quiet": 1}
    assert {row["component_id"] for row in result.top_rows} == {
        _sha("component-a")
    }
    assert len({row["component_id"] for row in result.random_rows}) == 1
    assert not (
        set(result.top_component_ids) & set(result.random_component_ids)
    )


def test_common_support_reduction_never_relaxes_stratum_signature() -> None:
    rows = _ranked(
        [
            _dual_row(
                label="a",
                component="a",
                source_game="a",
                stratum="quiet",
                teacher_value=300,
            ),
            _dual_row(
                label="b",
                component="b",
                source_game="b",
                stratum="quiet",
                teacher_value=100,
            ),
            _dual_row(
                label="unmatched",
                component="unmatched",
                source_game="unmatched",
                stratum="capture",
                teacher_value=1000,
            ),
        ]
    )
    result = selector.select_confirmatory_cohorts(
        rows,
        input_sha256=_sha("input"),
        config=_config(target=2),
    )
    assert len(result.top_rows) == len(result.random_rows) == 1
    assert result.counts_by_stratum == {"quiet": 1}
    assert result.reasons["components_excluded_no_common_support"] == 1
    assert result.reasons["requested_rows_not_selected"] == 1


def test_random_choice_is_seeded_by_structural_input_and_component() -> None:
    rows = _ranked(
        [
            _dual_row(
                label=label,
                component=label,
                source_game=label,
                stratum="quiet",
                teacher_value=value,
            )
            for label, value in (
                ("a", 400),
                ("b", 300),
                ("c", 200),
                ("d", 100),
            )
        ]
    )
    input_sha = _sha("input")
    structural_input_sha = selector._structural_input_sha256(
        selector._build_components(rows)
    )
    result = selector.select_confirmatory_cohorts(
        rows,
        input_sha256=input_sha,
        config=_config(target=1, seed="stable-seed"),
    )
    random_candidates = [
        _sha("component-b"),
        _sha("component-c"),
        _sha("component-d"),
    ]
    expected = min(
        random_candidates,
        key=lambda component_id: (
            common.derive_id(
                "atomic-confirmatory-random-order-v1",
                "stable-seed",
                structural_input_sha,
                component_id,
            ),
            component_id,
        ),
    )
    assert result.top_component_ids == (_sha("component-a"),)
    assert result.random_component_ids == (expected,)


def test_wrong_split_or_tampered_rank_fails_closed() -> None:
    wrong_split = _ranked(
        [
            _dual_row(
                label="a",
                component="a",
                source_game="a",
                stratum="quiet",
                teacher_value=200,
                split="calibration",
            ),
            _dual_row(
                label="b",
                component="b",
                source_game="b",
                stratum="quiet",
                teacher_value=100,
                split="calibration",
            ),
        ]
    )
    with pytest.raises(selector.CohortSelectionError, match="only split"):
        selector.select_confirmatory_cohorts(
            wrong_split,
            input_sha256=_sha("input"),
            config=_config(target=1),
        )

    valid = _ranked(
        [
            _dual_row(
                label="c",
                component="c",
                source_game="c",
                stratum="quiet",
                teacher_value=200,
            ),
            _dual_row(
                label="d",
                component="d",
                source_game="d",
                stratum="quiet",
                teacher_value=100,
            ),
        ]
    )
    valid[0]["rank"]["cp_gap"] += 1
    with pytest.raises(
        selector.CohortSelectionError,
        match="complete deterministic ranking",
    ):
        selector.select_confirmatory_cohorts(
            valid,
            input_sha256=_sha("input"),
            config=_config(target=1),
        )


def test_component_and_source_game_caps_exclude_whole_components() -> None:
    rows = _ranked(
        [
            _dual_row(
                label="a1",
                component="a",
                source_game="a",
                stratum="quiet",
                teacher_value=300,
            ),
            _dual_row(
                label="a2",
                component="a",
                source_game="a",
                stratum="quiet",
                teacher_value=290,
            ),
            _dual_row(
                label="b1",
                component="b",
                source_game="b",
                stratum="quiet",
                teacher_value=100,
            ),
            _dual_row(
                label="b2",
                component="b",
                source_game="b",
                stratum="quiet",
                teacher_value=90,
            ),
        ]
    )
    with pytest.raises(selector.CohortSelectionError, match="selected zero"):
        selector.select_confirmatory_cohorts(
            rows,
            input_sha256=_sha("input"),
            config=_config(target=2, component_cap=1),
        )
    with pytest.raises(selector.CohortSelectionError, match="selected zero"):
        selector.select_confirmatory_cohorts(
            rows,
            input_sha256=_sha("input"),
            config=_config(target=2, component_cap=2, game_cap=1),
        )


def test_dominance_ceiling_is_a_kill_when_common_support_becomes_empty() -> None:
    rows = _ranked(
        [
            _dual_row(
                label=label,
                component=label,
                source_game=label,
                stratum="quiet",
                teacher_value=value,
            )
            for label, value in (
                ("a", 400),
                ("b", 300),
                ("c", 200),
                ("d", 100),
            )
        ]
    )
    with pytest.raises(selector.CohortSelectionError, match="selected zero"):
        selector.select_confirmatory_cohorts(
            rows,
            input_sha256=_sha("input"),
            config=_config(target=2, ceiling=400_000),
        )


def test_min_rows_gate_is_enforced_after_common_support_reduction() -> None:
    rows = _ranked(
        [
            _dual_row(
                label="a",
                component="a",
                source_game="a",
                stratum="quiet",
                teacher_value=200,
            ),
            _dual_row(
                label="b",
                component="b",
                source_game="b",
                stratum="quiet",
                teacher_value=100,
            ),
        ]
    )
    with pytest.raises(selector.CohortSelectionError, match="below min_rows"):
        selector.select_confirmatory_cohorts(
            rows,
            input_sha256=_sha("input"),
            config=_config(target=2, min_rows=2),
        )


def test_complete_ranking_rejects_permutation_and_global_rank_swap() -> None:
    rows = _ranked(
        [
            _dual_row(
                label=label,
                component=label,
                source_game=label,
                stratum="quiet",
                teacher_value=value,
            )
            for label, value in (
                ("a", 400),
                ("b", 300),
                ("c", 200),
                ("d", 100),
            )
        ]
    )
    permuted = [rows[1], rows[0], *rows[2:]]
    with pytest.raises(
        selector.CohortSelectionError,
        match="complete deterministic ranking",
    ):
        selector.select_confirmatory_cohorts(
            permuted,
            input_sha256=_sha("input"),
            config=_config(target=1),
        )

    swapped = copy.deepcopy(rows)
    swapped[0]["rank"]["global_rank"], swapped[1]["rank"]["global_rank"] = (
        swapped[1]["rank"]["global_rank"],
        swapped[0]["rank"]["global_rank"],
    )
    with pytest.raises(
        selector.CohortSelectionError,
        match="complete deterministic ranking",
    ):
        selector.select_confirmatory_cohorts(
            swapped,
            input_sha256=_sha("input"),
            config=_config(target=1),
        )


def test_complete_ranking_rejects_stale_contiguous_rank_wire() -> None:
    rows = _ranked(
        [
            _dual_row(
                label=label,
                component=label,
                source_game=label,
                stratum="quiet",
                teacher_value=value,
            )
            for label, value in (
                ("a", 400),
                ("b", 300),
                ("c", 200),
                ("d", 100),
            )
        ]
    )
    stale = copy.deepcopy(rows)
    stale[0]["teacher_repeats"][0]["principal"]["score"]["value"] = -999
    stale[0]["teacher_repeats"][0]["multipv"][0]["score"]["value"] = -999
    with pytest.raises(
        selector.CohortSelectionError,
        match="complete deterministic ranking",
    ):
        selector.select_confirmatory_cohorts(
            stale,
            input_sha256=_sha("input"),
            config=_config(target=1),
        )


def test_write_selection_emits_canonical_hash_bound_receipt_no_overwrite(
    tmp_path: Path,
) -> None:
    rows = _ranked(
        [
            _dual_row(
                label=label,
                component=label,
                source_game=label,
                stratum="quiet",
                teacher_value=value,
            )
            for label, value in (
                ("a", 400),
                ("b", 300),
                ("c", 200),
                ("d", 100),
            )
        ]
    )
    top = tmp_path / "top.jsonl"
    random = tmp_path / "random.jsonl"
    receipt_path = tmp_path / "receipt.json"
    source, metrics_path, metrics_sha256 = _write_ranked_and_metrics(
        tmp_path,
        rows,
    )

    result, receipt = selector.write_selection(
        input_path=source,
        rank_metrics_path=metrics_path,
        expected_rank_metrics_sha256=metrics_sha256,
        top_path=top,
        random_path=random,
        receipt_path=receipt_path,
        config=_config(target=2, ceiling=500_000),
    )

    assert common.load_jsonl(top) == list(result.top_rows)
    assert common.load_jsonl(random) == list(result.random_rows)
    loaded_receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    assert loaded_receipt == receipt
    assert receipt["schema"] == "atomic-confirmatory-cohort-receipt-v1"
    assert receipt["scientific_role"] == "confirmation-diagnostic-only"
    assert receipt["artifacts"]["top"]["sha256"] == hashlib.sha256(
        top.read_bytes()
    ).hexdigest()
    assert receipt["artifacts"]["random"]["sha256"] == hashlib.sha256(
        random.read_bytes()
    ).hexdigest()
    assert receipt["proof"]["zero_component_overlap"] is True
    assert receipt["proof"]["exact_row_count_match"] is True
    assert receipt["proof"]["exact_strata_match"] is True
    assert receipt["selection"]["counts_by_stratum"] == {"quiet": 2}

    before = (top.read_bytes(), random.read_bytes(), receipt_path.read_bytes())
    with pytest.raises(FileExistsError, match="refusing to overwrite"):
        selector.write_selection(
            input_path=source,
            rank_metrics_path=metrics_path,
            expected_rank_metrics_sha256=metrics_sha256,
            top_path=top,
            random_path=random,
            receipt_path=receipt_path,
            config=_config(target=2, ceiling=500_000),
        )
    assert before == (
        top.read_bytes(),
        random.read_bytes(),
        receipt_path.read_bytes(),
    )


def test_write_selection_precommit_failure_rolls_back_without_receipt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rows = _ranked(
        [
            _dual_row(
                label=label,
                component=label,
                source_game=label,
                stratum="quiet",
                teacher_value=value,
            )
            for label, value in (
                ("a", 400),
                ("b", 300),
                ("c", 200),
                ("d", 100),
            )
        ]
    )
    top = tmp_path / "top.jsonl"
    random = tmp_path / "random.jsonl"
    receipt_path = tmp_path / "receipt.json"
    source, metrics_path, metrics_sha256 = _write_ranked_and_metrics(
        tmp_path,
        rows,
    )
    write_new_bytes = common.write_new_bytes

    def write_then_fail_random(path: Path, payload: bytes) -> None:
        write_new_bytes(path, payload)
        if path == random:
            raise OSError("synthetic random output post-write failure")

    monkeypatch.setattr(common, "write_new_bytes", write_then_fail_random)
    with pytest.raises(
        OSError, match="synthetic random output post-write failure"
    ):
        selector.write_selection(
            input_path=source,
            rank_metrics_path=metrics_path,
            expected_rank_metrics_sha256=metrics_sha256,
            top_path=top,
            random_path=random,
            receipt_path=receipt_path,
            config=_config(target=2, ceiling=500_000),
        )
    assert not top.exists()
    assert not random.exists()
    assert not receipt_path.exists()


def test_rank_metrics_trust_anchor_and_ranked_binding_fail_closed(
    tmp_path: Path,
) -> None:
    rows = _ranked(
        [
            _dual_row(
                label=label,
                component=label,
                source_game=label,
                stratum="quiet",
                teacher_value=value,
            )
            for label, value in (("a", 200), ("b", 100))
        ]
    )
    source, metrics_path, metrics_sha256 = _write_ranked_and_metrics(
        tmp_path,
        rows,
    )
    snapshot = common.load_jsonl_snapshot(source)
    with pytest.raises(
        selector.CohortSelectionError,
        match="caller trust anchor",
    ):
        selector._authenticate_rank_metrics(
            metrics_path,
            _sha("wrong-trust-anchor"),
            ranked_rows=rows,
            ranked_path=snapshot.path,
            ranked_sha256=snapshot.sha256,
            ranked_size_bytes=snapshot.size_bytes,
            ranked_row_count=len(snapshot.rows),
        )

    metrics = json.loads(metrics_path.read_text(encoding="utf-8"))
    metrics["ranked_output"]["sha256"] = _sha("stale-ranked-output")
    metrics_path.write_bytes(common.canonical_json_bytes(metrics))
    with pytest.raises(
        selector.CohortSelectionError,
        match="ranked input SHA-256",
    ):
        selector._authenticate_rank_metrics(
            metrics_path,
            common.sha256_file(metrics_path),
            ranked_rows=rows,
            ranked_path=snapshot.path,
            ranked_sha256=snapshot.sha256,
            ranked_size_bytes=snapshot.size_bytes,
            ranked_row_count=len(snapshot.rows),
        )

    metrics["ranked_output"]["sha256"] = snapshot.sha256
    metrics["ranked_output"]["row_count"] = 3
    metrics_path.write_bytes(common.canonical_json_bytes(metrics))
    with pytest.raises(
        selector.CohortSelectionError,
        match="row count",
    ):
        selector._authenticate_rank_metrics(
            metrics_path,
            common.sha256_file(metrics_path),
            ranked_rows=rows,
            ranked_path=snapshot.path,
            ranked_sha256=snapshot.sha256,
            ranked_size_bytes=snapshot.size_bytes,
            ranked_row_count=len(snapshot.rows),
        )


def test_rank_metrics_reject_noncanonical_or_extra_fields(
    tmp_path: Path,
) -> None:
    rows = _ranked(
        [
            _dual_row(
                label=label,
                component=label,
                source_game=label,
                stratum="quiet",
                teacher_value=value,
            )
            for label, value in (("a", 200), ("b", 100))
        ]
    )
    source, metrics_path, _metrics_sha256 = _write_ranked_and_metrics(
        tmp_path,
        rows,
    )
    snapshot = common.load_jsonl_snapshot(source)
    metrics = json.loads(metrics_path.read_text(encoding="utf-8"))
    metrics_path.write_text(json.dumps(metrics, indent=2), encoding="utf-8")
    with pytest.raises(
        selector.CohortSelectionError,
        match="canonical UTF-8 JSON",
    ):
        selector._authenticate_rank_metrics(
            metrics_path,
            common.sha256_file(metrics_path),
            ranked_rows=rows,
            ranked_path=snapshot.path,
            ranked_sha256=snapshot.sha256,
            ranked_size_bytes=snapshot.size_bytes,
            ranked_row_count=len(snapshot.rows),
        )

    metrics["extra"] = True
    metrics_path.write_bytes(common.canonical_json_bytes(metrics))
    with pytest.raises(selector.CohortSelectionError, match="fields differ"):
        selector._authenticate_rank_metrics(
            metrics_path,
            common.sha256_file(metrics_path),
            ranked_rows=rows,
            ranked_path=snapshot.path,
            ranked_sha256=snapshot.sha256,
            ranked_size_bytes=snapshot.size_bytes,
            ranked_row_count=len(snapshot.rows),
        )
