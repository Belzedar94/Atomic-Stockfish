from __future__ import annotations

import copy
import json
from pathlib import Path
import sys

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools.atomic_mining import common, split_components


START = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"


def _sha(label: str) -> str:
    return common.sha256_bytes(label.encode("utf-8"))


def _digest(namespace: str, value: object) -> str:
    return common.sha256_bytes(
        common.canonical_json_bytes({"namespace": namespace, "value": value})
    )


def _identity_fen(label: str) -> str:
    fingerprint = int(_sha(label)[:8], 16)
    side = "w" if fingerprint & 1 else "b"
    rights = "".join(
        right
        for bit, right in enumerate("KQkq", start=1)
        if fingerprint & (1 << bit)
    ) or "-"
    ep_options = ("-",) + tuple(
        f"{file_name}{rank}"
        for rank in ("3", "6")
        for file_name in "abcdefgh"
    )
    en_passant = ep_options[(fingerprint >> 5) % len(ep_options)]
    return (
        "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR "
        f"{side} {rights} {en_passant} 0 1"
    )


def _candidate(
    *,
    source: str,
    game: str,
    history: str,
    fen: str | None = None,
    game_index: int = 1,
    root_ply: int = 5,
    root_fen: str | None = None,
    moves: tuple[str, ...] | None = None,
) -> dict[str, object]:
    source_sha = _sha(source)
    marker_line = game_index * 10
    fen = fen or _identity_fen(f"position:{source}:{game}:{history}")
    if root_fen is None:
        halfmove = int(_sha(f"root:{source}:{game}")[:4], 16)
        root_fen = START.replace(" 0 1", f" {halfmove} 1")
    if moves is None:
        moves = tuple(
            "g1f3" if index % 2 == 0 else "g8f6"
            for index in range(root_ply + 1)
        )
    source_game_id = _digest(
        "atomic-source-game-v1",
        {
            "source_sha256": source_sha,
            "game_index": game_index,
            "marker_line": marker_line,
        },
    )
    history_moves = list(moves[:root_ply])
    return {
        "schema": "atomic-match-position-v1",
        "variant": "atomic",
        "scientific_role": "discovery-only",
        "outcome": None,
        "source": {
            "path": f"C:/fixtures/{source}.log",
            "sha256": source_sha,
            "size_bytes": 100,
        },
        "game": {
            "game_index": game_index,
            "marker_line": marker_line,
            "root_fen": root_fen,
            "root_fen_sha256": _digest("atomic-root-fen-v1", root_fen),
            "source_game_id": source_game_id,
            "full_game_history_sha256": _digest(
                "atomic-full-game-history-v1",
                {"root_fen": root_fen, "moves": list(moves)},
            ),
            "move_count": len(moves),
        },
        "position": {
            "root_ply": root_ply,
            "fen": fen,
            "played_move": moves[root_ply],
            "legal_move_count": 20,
            "terminal_state": "nonterminal",
            "history_uci": history_moves,
            "full_history_sha256": _digest(
                "atomic-position-full-history-v1",
                {"root_fen": root_fen, "moves": history_moves},
            ),
            "transposition_key": common.transposition_key(fen),
            "transposition_key_scope": "informational-only",
            "engine_key": "0x1234",
            "checkers": [],
        },
    }


def _candidate_ids(
    candidates: list[dict[str, object]],
) -> list[str]:
    return sorted(
        split_components.validate_candidate(candidate).position_id
        for candidate in candidates
    )


def _contract(records: list[dict[str, object]]) -> dict[str, object]:
    return {
        "count": len(records),
        "sha256": common.sha256_bytes(common.canonical_json_bytes(records)),
    }


def _inventory_entry(
    namespace: str,
    *,
    label: str,
    parent_node_id: str | None = None,
) -> dict[str, object]:
    core: dict[str, object] = {
        "source_game_id": _sha(f"{label}:source-game"),
        "root_fen_sha256": _sha(f"{label}:root"),
        "full_history_sha256": _sha(f"{label}:history"),
        "transposition_key": _sha(f"{label}:transposition"),
        "leakage_group_ids": [_sha(f"{label}:leakage")],
    }
    if parent_node_id is not None:
        core["parent_node_id"] = parent_node_id
    return {
        "inventory_id": _digest(namespace, core),
        **core,
    }


def _deny_entry(*, label: str, root_sha: str | None = None) -> dict[str, object]:
    core = {
        "root_fen_sha256": root_sha or _sha(f"{label}:root"),
        "leakage_group_ids": [_sha(f"{label}:leakage")],
    }
    return {
        "inventory_id": _digest("atomic-mining-deny-e05-v1", core),
        **core,
    }


def _technical_manifest(
    candidates: list[dict[str, object]],
) -> dict[str, object]:
    return {
        "schema": split_components.INVENTORY_SCHEMA,
        "mode": "technical-smoke",
        "candidate_position_ids": _candidate_ids(candidates),
        "replay": [],
        "teacher_continuations": [],
        "deny_e05": [],
        "opening_book_groups": [],
        "replay_contract": None,
        "deny_e05_contract": None,
    }


def _scientific_manifest(
    candidates: list[dict[str, object]],
) -> dict[str, object]:
    candidate_ids = _candidate_ids(candidates)
    replay = [
        _inventory_entry(
            "atomic-mining-replay-inventory-v1",
            label="replay",
        )
    ]
    teacher = [
        _inventory_entry(
            "atomic-mining-teacher-continuation-v1",
            label="teacher",
            parent_node_id=candidate_ids[2],
        )
    ]
    candidate_game = candidates[4]["game"]
    assert isinstance(candidate_game, dict)
    deny = [
        _deny_entry(
            label="blind",
            root_sha=str(candidate_game["root_fen_sha256"]),
        )
    ]
    opening_members = sorted(candidate_ids[:2])
    opening_groups = [
        {
            "group_id": _digest(
                "atomic-mining-opening-book-group-v1", opening_members
            ),
            "member_node_ids": opening_members,
        }
    ]
    return {
        "schema": split_components.INVENTORY_SCHEMA,
        "mode": "scientific",
        "candidate_position_ids": candidate_ids,
        "replay": replay,
        "teacher_continuations": teacher,
        "deny_e05": deny,
        "opening_book_groups": opening_groups,
        "replay_contract": _contract(replay),
        "deny_e05_contract": _contract(deny),
    }


def _build(
    candidates: list[dict[str, object]],
    *,
    inventory: dict[str, object] | None = None,
    seed: int = 7,
) -> tuple[dict[str, list[dict[str, object]]], dict[str, object]]:
    return split_components.build_assignments(
        candidates,
        inventory=inventory or _technical_manifest(candidates),
        seed=seed,
        calibration_fraction=0.25,
        confirmation_fraction=0.25,
    )


def _component_by_candidate(
    partitions: dict[str, list[dict[str, object]]],
) -> dict[str, str]:
    result: dict[str, str] = {}
    for rows in partitions.values():
        for row in rows:
            candidate = row["candidate"]
            if not isinstance(candidate, dict):
                continue
            game = candidate["game"]
            assert isinstance(game, dict)
            result[str(game["source_game_id"])] = str(row["component_id"])
    return result


def test_global_graph_unions_source_game_transposition_and_root() -> None:
    moves = ("g1f3", "g8f6", "b1c3", "b8c6", "e2e4", "e7e5", "f1b5")
    shared_position = START
    shared_root = START.replace(" 0 1", " 12 1")
    candidates = [
        _candidate(
            source="a",
            game="g1",
            history="p1",
            fen=shared_position,
            root_ply=5,
            moves=moves,
        ),
        _candidate(
            source="a",
            game="g1",
            history="p2",
            root_ply=6,
            moves=moves,
        ),
        _candidate(
            source="b",
            game="g2",
            history="p3",
            fen=START.replace(" 0 1", " 42 19"),
        ),
        _candidate(
            source="c",
            game="g3",
            history="p4",
            root_fen=shared_root,
        ),
        _candidate(
            source="d",
            game="g4",
            history="p5",
            root_fen=shared_root,
        ),
    ]
    partitions, receipt = _build(candidates)
    component_by_game = _component_by_candidate(partitions)
    games = [candidate["game"] for candidate in candidates]
    assert all(isinstance(game, dict) for game in games)
    assert component_by_game[str(games[0]["source_game_id"])] == (
        component_by_game[str(games[1]["source_game_id"])]
    )
    assert component_by_game[str(games[0]["source_game_id"])] == (
        component_by_game[str(games[2]["source_game_id"])]
    )
    assert component_by_game[str(games[3]["source_game_id"])] == (
        component_by_game[str(games[4]["source_game_id"])]
    )
    edge_kinds = {edge["edge_kind"] for edge in receipt["edges"]}
    assert {"source_game", "transposition", "root_fen"} <= edge_kinds


def test_shared_root_unions_without_emitted_position_transposition() -> None:
    root = START
    first = _candidate(
        source="root-a",
        game="g1",
        history="h1",
        root_fen=root,
        fen="rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq - 0 1",
    )
    second = _candidate(
        source="root-b",
        game="g2",
        history="h2",
        root_fen=root,
        fen="rnbqkbnr/pppp1ppp/8/4p3/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 2",
    )
    partitions, _receipt = _build([first, second])
    components = _component_by_candidate(partitions)
    first_game = first["game"]
    second_game = second["game"]
    assert isinstance(first_game, dict)
    assert isinstance(second_game, dict)
    assert components[str(first_game["source_game_id"])] == components[
        str(second_game["source_game_id"])
    ]


@pytest.mark.parametrize(
    ("path", "value", "match"),
    (
        (("game", "root_fen_sha256"), "0" * 64, "root_fen_sha256 does not match"),
        (("game", "source_game_id"), "1" * 64, "source_game_id does not match"),
        (
            ("game", "full_game_history_sha256"),
            "2" * 64,
            "full_game_history_sha256 does not match",
        ),
        (
            ("position", "full_history_sha256"),
            "3" * 64,
            "full_history_sha256 does not match",
        ),
        (
            ("position", "transposition_key"),
            "4" * 64,
            "transposition_key does not match",
        ),
        (
            ("position", "root_ply"),
            4,
            "root_ply must equal history_uci length",
        ),
        (("game", "game_index"), True, "game_index must be an integer"),
        (("source", "size_bytes"), -1, "size_bytes must be an integer"),
        (
            ("position", "legal_move_count"),
            0,
            "legal_move_count must be >= 1",
        ),
        (
            ("position", "legal_move_count"),
            True,
            "legal_move_count must be an integer",
        ),
        (
            ("position", "terminal_state"),
            "checkmate",
            "terminal_state must be nonterminal",
        ),
    ),
)
def test_candidate_ids_hashes_and_ranges_are_recomputed(
    path: tuple[str, str],
    value: object,
    match: str,
) -> None:
    original = _candidate(source="strict", game="g1", history="h1")
    inventory = _technical_manifest([original])
    candidate = copy.deepcopy(original)
    parent = candidate[path[0]]
    assert isinstance(parent, dict)
    parent[path[1]] = value
    with pytest.raises(split_components.SplitContractError, match=match):
        _build([candidate], inventory=inventory)


def test_full_game_inventory_must_be_complete_and_prefix_consistent() -> None:
    moves = ("g1f3", "g8f6", "b1c3")
    incomplete = _candidate(
        source="incomplete",
        game="g1",
        history="h1",
        root_ply=1,
        moves=moves,
    )
    with pytest.raises(
        split_components.SplitContractError, match="inventory is incomplete"
    ):
        _build([incomplete], inventory=_technical_manifest([incomplete]))

    rows = [
        _candidate(
            source="prefix",
            game="g2",
            history="h1",
            root_ply=1,
            moves=moves,
        ),
        _candidate(
            source="prefix",
            game="g2",
            history="h2",
            root_ply=2,
            moves=moves,
        ),
    ]
    position = rows[0]["position"]
    assert isinstance(position, dict)
    position["played_move"] = "a2a3"
    position["full_history_sha256"] = _digest(
        "atomic-position-full-history-v1",
        {
            "root_fen": rows[0]["game"]["root_fen"],
            "moves": position["history_uci"],
        },
    )
    with pytest.raises(
        split_components.SplitContractError,
        match="played_move is inconsistent",
    ):
        _build(rows, inventory=_technical_manifest(rows))


def test_scientific_manifest_emits_five_disjoint_preprobe_partitions() -> None:
    candidates = [
        _candidate(source=f"s{index}", game=f"g{index}", history=f"h{index}")
        for index in range(5)
    ]
    inventory = _scientific_manifest(candidates)
    partitions, receipt = _build(candidates, inventory=inventory, seed=31)
    assert tuple(partitions) == split_components.PARTITIONS
    assert all(partitions[partition] for partition in split_components.PARTITIONS)
    assert receipt["counts"]["nodes"] == sum(map(len, partitions.values()))
    assert receipt["counts"]["edges"] == len(receipt["edges"])
    for partition, rows in partitions.items():
        for row in rows:
            assert set(row) == {
                "schema",
                "partition",
                "component_id",
                "node_id",
                "node_kind",
                "candidate",
                "inventory_entry",
            }
            assert row["schema"] == split_components.ASSIGNMENT_SCHEMA
            assert row["partition"] == partition
    assert receipt["zero_overlap"] == {
        "all_nodes_assigned_once": True,
        "all_components_assigned_once": True,
        "node_overlap_count": 0,
        "component_overlap_count": 0,
        "node_ids_by_partition": {
            partition: sorted(str(row["node_id"]) for row in partitions[partition])
            for partition in split_components.PARTITIONS
        },
        "component_ids_by_partition": {
            partition: sorted(
                {str(row["component_id"]) for row in partitions[partition]}
            )
            for partition in split_components.PARTITIONS
        },
    }
    candidate_ids = _candidate_ids(candidates)
    component_by_node = {
        str(node["node_id"]): str(node["component_id"])
        for node in receipt["nodes"]
    }
    opening = inventory["opening_book_groups"][0]
    assert component_by_node[opening["member_node_ids"][0]] == (
        component_by_node[opening["member_node_ids"][1]]
    )
    teacher = inventory["teacher_continuations"][0]
    assert component_by_node[teacher["inventory_id"]] == (
        component_by_node[candidate_ids[2]]
    )
    denied_candidate = split_components.validate_candidate(
        candidates[4]
    ).position_id
    denied_rows = {
        str(row["node_id"]) for row in partitions["DENY_E05"]
    }
    assert denied_candidate in denied_rows


def test_manifest_rejects_incomplete_candidate_inventory_and_contract_drift() -> None:
    candidates = [
        _candidate(source=f"s{index}", game=f"g{index}", history=f"h{index}")
        for index in range(5)
    ]
    incomplete = _scientific_manifest(candidates)
    incomplete["candidate_position_ids"] = incomplete["candidate_position_ids"][:-1]
    with pytest.raises(
        split_components.SplitContractError,
        match="exactly equal the input candidate set",
    ):
        _build(candidates, inventory=incomplete)

    drifted = _scientific_manifest(candidates)
    drifted["replay_contract"]["sha256"] = "0" * 64
    with pytest.raises(
        split_components.SplitContractError,
        match="does not bind its exact inventory",
    ):
        _build(candidates, inventory=drifted)

    empty_replay = _scientific_manifest(candidates)
    empty_replay["replay"] = []
    empty_replay["replay_contract"] = _contract([])
    with pytest.raises(
        split_components.SplitContractError,
        match="requires non-empty REPLAY and deny-E05",
    ):
        _build(candidates, inventory=empty_replay)


def test_manifest_rejects_role_overlap_and_unknown_group_member() -> None:
    candidates = [
        _candidate(source=f"s{index}", game=f"g{index}", history=f"h{index}")
        for index in range(5)
    ]
    overlap = _scientific_manifest(candidates)
    overlap["replay"] = [overlap["replay"][0], overlap["replay"][0]]
    overlap["replay_contract"] = _contract(overlap["replay"])
    with pytest.raises(
        split_components.SplitContractError,
        match="multiple roles",
    ):
        _build(candidates, inventory=overlap)

    missing = _scientific_manifest(candidates)
    group = missing["opening_book_groups"][0]
    group["member_node_ids"] = sorted(
        [group["member_node_ids"][0], _sha("absent")]
    )
    group["group_id"] = _digest(
        "atomic-mining-opening-book-group-v1",
        group["member_node_ids"],
    )
    with pytest.raises(
        split_components.SplitContractError,
        match="references absent node",
    ):
        _build(candidates, inventory=missing)


@pytest.mark.parametrize(
    ("calibration", "confirmation"),
    ((0.0, 0.2), (0.2, 0.0), (1.0, 0.1), (0.6, 0.4)),
)
def test_split_rejects_invalid_fractions(
    calibration: float,
    confirmation: float,
) -> None:
    candidates = [_candidate(source="a", game="g1", history="h1")]
    with pytest.raises(split_components.SplitContractError, match="fraction"):
        split_components.build_assignments(
            candidates,
            inventory=_technical_manifest(candidates),
            seed=1,
            calibration_fraction=calibration,
            confirmation_fraction=confirmation,
        )


def test_split_is_deterministic_across_input_order() -> None:
    candidates = [
        _candidate(source=f"s{index}", game=f"g{index}", history=f"h{index}")
        for index in range(5)
    ]
    inventory = _scientific_manifest(candidates)
    first = _build(candidates, inventory=inventory, seed=91)
    second = _build(list(reversed(candidates)), inventory=inventory, seed=91)
    assert first == second


def test_split_file_writes_exact_authenticated_snapshots_without_overwrite(
    tmp_path: Path,
) -> None:
    candidates = [
        _candidate(source=f"s{index}", game=f"g{index}", history=f"h{index}")
        for index in range(5)
    ]
    inventory = _scientific_manifest(candidates)
    input_path = tmp_path / "positions.jsonl"
    inventory_path = tmp_path / "inventory.jsonl"
    receipt_path = tmp_path / "receipt.json"
    output_paths = {
        partition: tmp_path / f"{partition.lower()}.jsonl"
        for partition in split_components.PARTITIONS
    }
    common.write_new_jsonl(input_path, candidates)
    common.write_new_jsonl(inventory_path, [inventory])

    summary = split_components.split_file(
        input_path,
        inventory_path,
        output_paths,
        receipt_path,
        seed=42,
        calibration_fraction=0.25,
        confirmation_fraction=0.25,
    )
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    assert set(receipt) == {
        "schema",
        "mode",
        "seed",
        "fractions",
        "counts",
        "source_sha256s",
        "inventory_contracts",
        "nodes",
        "edges",
        "components",
        "zero_overlap",
        "inputs",
        "outputs",
    }
    assert receipt["schema"] == split_components.RECEIPT_SCHEMA
    assert set(receipt["inputs"]) == {"candidates", "preprobe_inventory"}
    assert set(receipt["outputs"]) == set(split_components.PARTITIONS)
    assert summary.positions == len(candidates)
    assert summary.nodes == sum(summary.partition_counts.values())
    assert receipt["inputs"]["candidates"]["sha256"] == common.sha256_file(
        input_path
    )
    assert receipt["inputs"]["preprobe_inventory"][
        "sha256"
    ] == common.sha256_file(inventory_path)
    for partition, path in output_paths.items():
        assert receipt["outputs"][partition] == {
            "path": str(path.absolute()),
            "sha256": common.sha256_file(path),
            "size_bytes": path.stat().st_size,
            "row_count": len(common.load_jsonl(path)),
        }
    with pytest.raises(FileExistsError, match="refusing to overwrite"):
        split_components.split_file(
            input_path,
            inventory_path,
            output_paths,
            receipt_path,
            seed=42,
            calibration_fraction=0.25,
            confirmation_fraction=0.25,
        )


def test_split_postflight_failure_has_no_commit_marker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    candidates = [
        _candidate(source=f"s{index}", game=f"g{index}", history=f"h{index}")
        for index in range(5)
    ]
    inventory = _scientific_manifest(candidates)
    input_path = tmp_path / "positions.jsonl"
    inventory_path = tmp_path / "inventory.jsonl"
    receipt_path = tmp_path / "receipt.json"
    output_paths = {
        partition: tmp_path / f"{partition.lower()}.jsonl"
        for partition in split_components.PARTITIONS
    }
    common.write_new_jsonl(input_path, candidates)
    common.write_new_jsonl(inventory_path, [inventory])
    load_snapshot = common.load_jsonl_snapshot
    calls = 0

    def fail_first_output_postflight(path: Path) -> common.JsonlSnapshot:
        nonlocal calls
        calls += 1
        snapshot = load_snapshot(path)
        if calls == 3:
            raise split_components.SplitContractError(
                "injected split postflight failure"
            )
        return snapshot

    monkeypatch.setattr(
        common, "load_jsonl_snapshot", fail_first_output_postflight
    )
    with pytest.raises(
        split_components.SplitContractError,
        match="injected split postflight failure",
    ):
        split_components.split_file(
            input_path,
            inventory_path,
            output_paths,
            receipt_path,
            seed=42,
            calibration_fraction=0.25,
            confirmation_fraction=0.25,
        )
    assert calls == 3
    assert not receipt_path.exists()
    assert all(not path.exists() for path in output_paths.values())
