from __future__ import annotations

import copy
from pathlib import Path
import sys
from typing import Mapping, Sequence

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools.atomic_mining import common
from tools.atomic_mining import probe_disagreements as probe
from tools.atomic_mining import probe_label_stability as stability
from tools.atomic_mining import rank_disagreements as ranker
from tools.atomic_mining import select_confirmatory_cohorts as selector
from tools.atomic_mining import split_components
from tools.atomic_mining.uci_session import (
    FenInspection,
    ProbeResult,
    UciInfo,
    UciScore,
)


ROOT_A = "8/8/8/8/8/8/P7/K6k w - - 0 1"
ROOT_B = "7k/8/8/8/8/8/P7/K7 w - - 0 1"
CHILD_A = "8/8/8/8/8/P7/8/K6k b - - 0 1"
MOVE = "a2a3"
SCHEDULE_SHA = "1" * 64
BOOK_SHA = "2" * 64


def _digest(namespace: str, value: object) -> str:
    return common.sha256_bytes(
        common.canonical_json_bytes({"namespace": namespace, "value": value})
    )


def _sha(label: str) -> str:
    return common.sha256_bytes(label.encode("utf-8"))


def _side_result(fen: str, result_white: str) -> str:
    if result_white == "1/2-1/2":
        return "draw"
    side = common.fen_fields(fen)[1]
    return "win" if (result_white == "1-0") == (side == "w") else "loss"


def _e00_candidate(
    *,
    pair_label: str,
    leg: int = 0,
    root_fen: str = ROOT_A,
    result_white: str = "1-0",
) -> dict[str, object]:
    pair_id = _sha(f"pair:{pair_label}")
    moves = [MOVE]
    trajectory_sha256 = _digest(
        "atomic-e00-trajectory-v1",
        {
            "moves": moves,
            "result_white": result_white,
            "root_fen": root_fen,
        },
    )
    source_game_id = _digest(
        "atomic-e00-source-game-v1",
        {
            "battery_id": "e00-battery",
            "experiment_id": "e00-experiment",
            "leg": leg,
            "pair_id": pair_id,
            "schedule_sha256": SCHEDULE_SHA,
            "trajectory_sha256": trajectory_sha256,
        },
    )
    history_sha256 = _digest(
        "atomic-e00-position-history-v1",
        {"root_fen": root_fen, "moves": []},
    )
    position_id = _digest(
        "atomic-e00-position-id-v1",
        {
            "history_sha256": history_sha256,
            "root_ply": 0,
            "source_game_id": source_game_id,
        },
    )
    white_role = "current-v3" if leg == 0 else "run3b"
    black_role = "run3b" if leg == 0 else "current-v3"
    current_is_white = white_role == "current-v3"
    result_current = (
        "draw"
        if result_white == "1/2-1/2"
        else "win"
        if (result_white == "1-0") == current_is_white
        else "loss"
    )
    result_tag = f"{pair_label}:{leg}:{result_white}"
    return {
        "schema": "atomic-e00-position-v1",
        "variant": "atomic",
        "scientific_role": "result-bearing-source",
        "source": {
            "execution_receipt_sha256": _sha(f"receipt:{result_tag}"),
            "games_sha256": _sha(f"games:{result_tag}"),
            "inventory_sha256": _sha(f"inventory:{result_tag}"),
            "experiment_id": "e00-experiment",
            "battery_id": "e00-battery",
            "schedule_sha256": SCHEDULE_SHA,
            "pair_id": pair_id,
            "pair_ordinal": int(pair_id[:8], 16) + 1,
            "leg": leg,
            "leakage_group_id": pair_id,
            "source_game_id": source_game_id,
            "trajectory_sha256": trajectory_sha256,
        },
        "game": {
            "root_fen": root_fen,
            "root_fen_sha256": _digest("atomic-root-fen-v1", root_fen),
            "ply_count": 1,
            "stratum": "VSTC",
            "time_control": {
                "base_ms": 2000,
                "increment_ms": 20,
                "name": "VSTC",
            },
            "book_sha256": BOOK_SHA,
            "book_line": 0,
            "white_network_role": white_role,
            "black_network_role": black_role,
            "result_current": result_current,
            "time_loss": False,
            "terminal_reason": "checkmate",
        },
        "position": {
            "position_id": position_id,
            "root_ply": 0,
            "fen": root_fen,
            "side_to_move": "w",
            "history_uci": [],
            "history_sha256": history_sha256,
            "played_move": MOVE,
            "result_white": result_white,
            "result_side_to_move": _side_result(root_fen, result_white),
            "transposition_key": common.transposition_key(root_fen),
            "transposition_key_scope": "informational-only",
            "engine_key": "synthetic-e00-key",
            "checkers": [],
            "legal_move_count": 3,
        },
    }


def _technical_inventory(
    candidates: Sequence[Mapping[str, object]],
) -> dict[str, object]:
    return {
        "schema": split_components.INVENTORY_SCHEMA,
        "mode": "technical-smoke",
        "candidate_position_ids": sorted(
            split_components.validate_candidate(candidate).position_id
            for candidate in candidates
        ),
        "replay": [],
        "teacher_continuations": [],
        "deny_e05": [],
        "opening_book_groups": [],
        "replay_contract": None,
        "deny_e05_contract": None,
    }


def _split_projection(
    partitions: Mapping[str, Sequence[Mapping[str, object]]],
) -> dict[str, list[tuple[str, str, str]]]:
    return {
        partition: sorted(
            (
                str(row["node_id"]),
                str(row["component_id"]),
                str(row["node_kind"]),
            )
            for row in rows
        )
        for partition, rows in partitions.items()
    }


def _probe_config() -> dict[str, object]:
    return {
        "engine": {"path": "engine", "sha256": _sha("engine"), "size_bytes": 1},
        "current_net": {
            "path": "current.nnue",
            "sha256": _sha("current"),
            "size_bytes": 1,
        },
        "teacher_net": {
            "path": "teacher.nnue",
            "sha256": _sha("teacher"),
            "size_bytes": 1,
        },
        "nodes": 1000,
        "multipv": 2,
        "threads": 1,
        "hash_mb": 16,
        "clear_hash": True,
        "teacher_repeat_count": 2,
        "engine_options": [
            {"name": "UCI_Variant", "value": "atomic"},
            {"name": "Threads", "value": 1},
            {"name": "Hash", "value": 16},
            {"name": "MultiPV", "value": 2},
            {"name": "Ponder", "value": False},
            {"name": "SyzygyPath", "value": "<empty>"},
            {"name": "SyzygyProbeLimit", "value": 0},
            {"name": "Use NNUE", "value": "pure"},
        ],
        "probe_order_policy": "position-id-parity-alternating-v1",
    }


class _ProbeEngine:
    def __init__(self, value: int) -> None:
        self.value = value

    def new_game(self, *, timeout: float | None = None) -> None:
        del timeout

    def search(
        self,
        root_fen: str,
        *,
        nodes: int,
        moves: Sequence[str] = (),
        searchmoves: Sequence[str] = (),
        pre_search_options: Sequence[object] = (),
        timeout: float | None = None,
    ) -> ProbeResult:
        del root_fen, nodes, moves, pre_search_options, timeout
        bestmove = searchmoves[0] if searchmoves else MOVE
        principal = UciInfo(
            depth=7,
            seldepth=9,
            nodes=1000,
            multipv=1,
            score=UciScore(kind="cp", value=self.value),
            wdl=(500, 300, 200),
            pv=(bestmove,),
            raw=f"info multipv 1 pv {bestmove}",
        )
        alternate = UciInfo(
            depth=7,
            seldepth=9,
            nodes=1000,
            multipv=2,
            score=UciScore(kind="cp", value=self.value - 1),
            wdl=(499, 301, 200),
            pv=(bestmove,),
            raw=f"info multipv 2 pv {bestmove}",
        )
        infos = (principal,) if searchmoves else (principal, alternate)
        return ProbeResult(
            bestmove=bestmove,
            ponder=None,
            infos=infos,
            raw_lines=(principal.raw, "bestmove " + bestmove),
        )

    def inspect_fen(
        self,
        root_fen: str,
        moves: Sequence[str] = (),
        *,
        timeout: float | None = None,
    ) -> FenInspection:
        del timeout
        if not moves:
            return FenInspection(
                fen=root_fen, key=None, checkers=(), raw_lines=()
            )
        if root_fen == ROOT_A and tuple(moves) == (MOVE,):
            return FenInspection(
                fen=CHILD_A, key=None, checkers=(), raw_lines=()
            )
        raise AssertionError((root_fen, tuple(moves)))


def _global_assignment(
    candidate: Mapping[str, object],
    *,
    component_label: str,
) -> dict[str, object]:
    identity = split_components.validate_candidate(candidate)
    return {
        "schema": split_components.ASSIGNMENT_SCHEMA,
        "partition": "CONF",
        "component_id": _sha(f"component:{component_label}"),
        "node_id": identity.position_id,
        "node_kind": "E00_CANDIDATE",
        "candidate": dict(candidate),
        "inventory_entry": None,
    }


def _probe_candidate(
    candidate: Mapping[str, object],
    *,
    component_label: str,
    teacher_value: int,
) -> dict[str, object]:
    assignment = probe._unwrap_assignment(
        _global_assignment(candidate, component_label=component_label),
        global_membership_authenticated=True,
    )
    return probe._probe_assignment(
        assignment,
        _ProbeEngine(0),
        _ProbeEngine(teacher_value),
        probe_config=_probe_config(),
    )


def _forbidden_keys(value: object) -> set[str]:
    result: set[str] = set()
    if isinstance(value, Mapping):
        for key, nested in value.items():
            rendered = str(key).lower()
            if any(
                token in rendered
                for token in ("result", "outcome", "terminal_reason", "time_loss")
            ):
                result.add(rendered)
            result.update(_forbidden_keys(nested))
    elif isinstance(value, list):
        for nested in value:
            result.update(_forbidden_keys(nested))
    return result


def test_pair_and_transposition_edges_cover_result_bearing_candidates() -> None:
    paired = [
        _e00_candidate(pair_label="paired", leg=0, root_fen=ROOT_A),
        _e00_candidate(pair_label="paired", leg=1, root_fen=ROOT_B),
    ]
    partitions, receipt = split_components.build_assignments(
        paired,
        inventory=_technical_inventory(paired),
        seed=7,
        calibration_fraction=0.2,
        confirmation_fraction=0.2,
    )
    component_ids = {
        str(row["component_id"])
        for rows in partitions.values()
        for row in rows
    }
    assert len(component_ids) == 1
    assert "pair" in {str(edge["edge_kind"]) for edge in receipt["edges"]}

    transposed = [
        _e00_candidate(pair_label="left", root_fen=ROOT_A),
        _e00_candidate(pair_label="right", root_fen=ROOT_A),
    ]
    partitions, receipt = split_components.build_assignments(
        transposed,
        inventory=_technical_inventory(transposed),
        seed=7,
        calibration_fraction=0.2,
        confirmation_fraction=0.2,
    )
    component_ids = {
        str(row["component_id"])
        for rows in partitions.values()
        for row in rows
    }
    assert len(component_ids) == 1
    assert "transposition" in {
        str(edge["edge_kind"]) for edge in receipt["edges"]
    }


def test_result_mutation_cannot_change_structural_split() -> None:
    first = [
        _e00_candidate(pair_label="pair", leg=0, root_fen=ROOT_A),
        _e00_candidate(pair_label="pair", leg=1, root_fen=ROOT_B),
    ]
    mutated = [
        _e00_candidate(
            pair_label="pair",
            leg=0,
            root_fen=ROOT_A,
            result_white="0-1",
        ),
        _e00_candidate(
            pair_label="pair",
            leg=1,
            root_fen=ROOT_B,
            result_white="0-1",
        ),
    ]
    first_ids = [split_components.validate_candidate(row) for row in first]
    mutated_ids = [
        split_components.validate_candidate(row) for row in mutated
    ]
    assert [item.position_id for item in first_ids] == [
        item.position_id for item in mutated_ids
    ]
    assert [item.source_game_id for item in first_ids] == [
        item.source_game_id for item in mutated_ids
    ]
    assert [item.provenance_position_id for item in first_ids] != [
        item.provenance_position_id for item in mutated_ids
    ]
    assert [item.provenance_source_game_id for item in first_ids] != [
        item.provenance_source_game_id for item in mutated_ids
    ]

    first_partitions, first_receipt = split_components.build_assignments(
        first,
        inventory=_technical_inventory(first),
        seed=17,
        calibration_fraction=0.2,
        confirmation_fraction=0.2,
    )
    mutated_partitions, mutated_receipt = split_components.build_assignments(
        mutated,
        inventory=_technical_inventory(mutated),
        seed=17,
        calibration_fraction=0.2,
        confirmation_fraction=0.2,
    )
    assert _split_projection(first_partitions) == _split_projection(
        mutated_partitions
    )
    assert first_receipt == mutated_receipt


def test_result_bearing_probe_requires_global_cal_or_conf_assignment() -> None:
    candidate = _e00_candidate(pair_label="probe")
    assignment = _global_assignment(candidate, component_label="probe")
    with pytest.raises(probe.ProbeError, match="authenticated split receipt"):
        probe._unwrap_assignment(assignment)
    unwrapped = probe._unwrap_assignment(
        assignment,
        global_membership_authenticated=True,
    )
    assert unwrapped.position_id == split_components.validate_candidate(
        candidate
    ).position_id
    assert unwrapped.split == "confirmation"

    with pytest.raises(probe.ProbeError, match="five-way global split"):
        probe._unwrap_assignment(candidate)

    legacy = {
        "schema": probe.ASSIGNMENT_SCHEMA,
        "split": "confirmation",
        "position_id": _sha("legacy-position"),
        "component_id": _sha("legacy-component"),
        "candidate": candidate,
    }
    with pytest.raises(probe.ProbeError, match="legacy assignments"):
        probe._unwrap_assignment(legacy)

    wrong_partition = copy.deepcopy(assignment)
    wrong_partition["partition"] = "COHORT_ELIGIBLE"
    with pytest.raises(probe.ProbeError, match="CAL or CONF"):
        probe._unwrap_assignment(
            wrong_partition,
            global_membership_authenticated=True,
        )


def test_result_metadata_never_enters_features_rank_or_selection() -> None:
    labels_and_values = (
        ("a", 400),
        ("b", 300),
        ("c", 200),
        ("d", 100),
    )
    first_probes = [
        _probe_candidate(
            _e00_candidate(pair_label=label, result_white="1-0"),
            component_label=label,
            teacher_value=value,
        )
        for label, value in labels_and_values
    ]
    mutated_probes = [
        _probe_candidate(
            _e00_candidate(pair_label=label, result_white="0-1"),
            component_label=label,
            teacher_value=value,
        )
        for label, value in labels_and_values
    ]
    for first, mutated in zip(first_probes, mutated_probes, strict=True):
        assert first["position_id"] == mutated["position_id"]
        assert first["component_id"] == mutated["component_id"]
        assert first["features"] == mutated["features"]
        assert not _forbidden_keys(first["features"])

    first_stability = stability.analyze_rows(first_probes)
    mutated_stability = stability.analyze_rows(mutated_probes)
    first_stability.pop("input")
    mutated_stability.pop("input")
    assert first_stability == mutated_stability

    first_ranked = ranker.rank_disagreements(first_probes)
    mutated_ranked = ranker.rank_disagreements(mutated_probes)
    first_projection = [
        {
            "position_id": row["position_id"],
            "component_id": row["component_id"],
            "rank": row["rank"],
            "groups": row["groups"],
        }
        for row in first_ranked
    ]
    mutated_projection = [
        {
            "position_id": row["position_id"],
            "component_id": row["component_id"],
            "rank": row["rank"],
            "groups": row["groups"],
        }
        for row in mutated_ranked
    ]
    assert first_projection == mutated_projection
    assert all(
        not _forbidden_keys(row["rank"]) and not _forbidden_keys(row["groups"])
        for row in first_ranked
    )

    config = selector.SelectionConfig(
        target_rows=1,
        seed="result-independent-seed",
        max_rows_per_source_game=2,
        max_rows_per_component=2,
        dominance_ceiling_ppm=1_000_000,
        min_rows=1,
    )
    first_selection = selector.select_confirmatory_cohorts(
        first_ranked,
        input_sha256=_sha("raw-input-with-result-a"),
        config=config,
    )
    mutated_selection = selector.select_confirmatory_cohorts(
        mutated_ranked,
        input_sha256=_sha("raw-input-with-result-b"),
        config=config,
    )
    assert first_selection.top_component_ids == mutated_selection.top_component_ids
    assert (
        first_selection.random_component_ids
        == mutated_selection.random_component_ids
    )


def test_malformed_or_extra_result_metadata_fails_closed() -> None:
    candidate = _e00_candidate(pair_label="malformed")

    stale = copy.deepcopy(candidate)
    stale["position"]["result_white"] = "0-1"  # type: ignore[index]
    with pytest.raises(
        split_components.SplitContractError,
        match="result_side_to_move",
    ):
        split_components.validate_candidate(stale)

    extra = copy.deepcopy(candidate)
    extra["outcome"] = "1-0"
    with pytest.raises(
        split_components.SplitContractError,
        match="candidate fields differ",
    ):
        split_components.validate_candidate(extra)
