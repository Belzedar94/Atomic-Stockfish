"""Create the five globally disjoint pre-probe mining partitions.

The graph covers the complete E00 candidate inventory plus hash-bound REPLAY,
teacher-continuation, opening/book and blind E05-deny inventories.  It unions
source games, source roots, exact histories, informational transposition keys
and explicit leakage edges before any network probe.  Complete components are
then assigned once to CAL, CONF, COHORT_ELIGIBLE, REPLAY_ELIGIBLE or DENY_E05.
The full FEN and trajectory identity remain attached to every E00 node; the
shortened transposition key is only a leakage-prevention edge.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
from pathlib import Path
import re
from typing import Iterable, Mapping, Sequence

from tools.atomic_mining import common


DISCOVERY_INPUT_SCHEMA = "atomic-match-position-v1"
RESULT_BEARING_INPUT_SCHEMA = "atomic-e00-position-v1"
# Historical public name retained for callers that intentionally build the
# discovery-only schema.
INPUT_SCHEMA = DISCOVERY_INPUT_SCHEMA
INPUT_SCHEMAS = frozenset(
    {DISCOVERY_INPUT_SCHEMA, RESULT_BEARING_INPUT_SCHEMA}
)
INVENTORY_SCHEMA = "atomic-mining-preprobe-inventory-v1"
ASSIGNMENT_SCHEMA = "atomic-mining-global-assignment-v1"
RECEIPT_SCHEMA = "atomic-mining-global-split-receipt-v1"
UCI_MOVE = re.compile(r"^[a-h][1-8][a-h][1-8][qrbn]?$")
SQUARE = re.compile(r"^[a-h][1-8]$")
PARTITIONS = (
    "CAL",
    "CONF",
    "COHORT_ELIGIBLE",
    "REPLAY_ELIGIBLE",
    "DENY_E05",
)


class SplitContractError(common.MiningArtifactError):
    """The pre-probe split contract is not satisfied."""


@dataclass(frozen=True)
class PositionIdentity:
    candidate_schema: str
    position_id: str
    source_game_id: str
    source_group_sha256: str
    provenance_position_id: str
    provenance_source_game_id: str
    pair_id: str | None
    transposition_key: str
    root_fen_sha256: str
    full_history_sha256: str
    structural_history_sha256: str
    root_ply: int
    history_uci: tuple[str, ...]
    played_move: str
    root_fen: str
    fen: str
    move_count: int
    legal_move_count: int
    engine_key: str | None
    checkers: tuple[str, ...]


@dataclass(frozen=True)
class SplitSummary:
    positions: int
    nodes: int
    edges: int
    components: int
    partition_counts: Mapping[str, int]
    receipt_sha256: str


@dataclass(frozen=True)
class _Node:
    node_id: str
    kind: str
    source_game_id: str | None
    root_fen_sha256: str
    full_history_sha256: str | None
    structural_history_sha256: str | None
    transposition_key: str | None
    pair_id: str | None
    leakage_group_ids: tuple[str, ...]
    candidate: Mapping[str, object] | None
    inventory_entry: Mapping[str, object] | None


class _UnionFind:
    def __init__(self, values: Iterable[str]) -> None:
        self.parent = {value: value for value in values}

    def find(self, value: str) -> str:
        parent = self.parent[value]
        if parent != value:
            self.parent[value] = self.find(parent)
        return self.parent[value]

    def union(self, left: str, right: str) -> None:
        left_root = self.find(left)
        right_root = self.find(right)
        if left_root == right_root:
            return
        if left_root < right_root:
            self.parent[right_root] = left_root
        else:
            self.parent[left_root] = right_root


def _require_mapping(value: object, *, label: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise SplitContractError(f"{label} must be an object")
    return value


def _digest(namespace: str, value: object) -> str:
    return common.sha256_bytes(
        common.canonical_json_bytes({"namespace": namespace, "value": value})
    )


def _require_int(
    value: object,
    *,
    label: str,
    minimum: int,
) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise SplitContractError(f"{label} must be an integer >= {minimum}")
    return value


def _require_string(value: object, *, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise SplitContractError(f"{label} must be a non-empty string")
    return value


def _require_sha(value: object, *, label: str) -> str:
    try:
        return common.require_lower_hex_sha256(value, label=label)
    except common.MiningArtifactError as error:
        raise SplitContractError(str(error)) from error


def _require_fen(value: object, *, label: str) -> str:
    fen = _require_string(value, label=label)
    if " ".join(fen.split()) != fen:
        raise SplitContractError(f"{label} must be normalized")
    try:
        common.fen_fields(fen)
    except common.MiningArtifactError as error:
        raise SplitContractError(f"{label}: {error}") from error
    return fen


def _require_move(value: object, *, label: str) -> str:
    move = _require_string(value, label=label)
    if UCI_MOVE.fullmatch(move) is None:
        raise SplitContractError(f"{label} must be a lowercase UCI move")
    return move


def _validate_discovery_candidate(
    row: Mapping[str, object],
) -> PositionIdentity:
    try:
        common.require_exact_fields(
            row,
            (
                "schema",
                "variant",
                "scientific_role",
                "outcome",
                "source",
                "game",
                "position",
            ),
            label="candidate",
        )
    except common.MiningArtifactError as error:
        raise SplitContractError(str(error)) from error
    if row["schema"] != DISCOVERY_INPUT_SCHEMA:
        raise SplitContractError(
            f"candidate schema must be {DISCOVERY_INPUT_SCHEMA}"
        )
    if row["variant"] != "atomic":
        raise SplitContractError("candidate variant must be atomic")
    if row["scientific_role"] != "discovery-only":
        raise SplitContractError("candidate must be discovery-only")
    if row["outcome"] is not None:
        raise SplitContractError("discovery candidate outcome must be null")

    source = _require_mapping(row["source"], label="candidate.source")
    try:
        common.require_exact_fields(
            source, ("path", "sha256", "size_bytes"), label="candidate.source"
        )
    except common.MiningArtifactError as error:
        raise SplitContractError(str(error)) from error
    _require_string(source["path"], label="candidate.source.path")
    source_sha256 = _require_sha(
        source["sha256"], label="candidate.source.sha256"
    )
    _require_int(
        source["size_bytes"],
        label="candidate.source.size_bytes",
        minimum=0,
    )

    game = _require_mapping(row["game"], label="candidate.game")
    try:
        common.require_exact_fields(
            game,
            (
                "game_index",
                "marker_line",
                "root_fen",
                "root_fen_sha256",
                "source_game_id",
                "full_game_history_sha256",
                "move_count",
            ),
            label="candidate.game",
        )
    except common.MiningArtifactError as error:
        raise SplitContractError(str(error)) from error
    game_index = _require_int(
        game["game_index"], label="candidate.game.game_index", minimum=1
    )
    marker_line = _require_int(
        game["marker_line"], label="candidate.game.marker_line", minimum=1
    )
    move_count = _require_int(
        game["move_count"], label="candidate.game.move_count", minimum=1
    )
    root_fen = _require_fen(
        game["root_fen"], label="candidate.game.root_fen"
    )
    root_fen_sha256 = _require_sha(
        game["root_fen_sha256"],
        label="candidate.game.root_fen_sha256",
    )
    expected_root_fen_sha256 = _digest("atomic-root-fen-v1", root_fen)
    if root_fen_sha256 != expected_root_fen_sha256:
        raise SplitContractError(
            "candidate.game.root_fen_sha256 does not match root_fen"
        )
    source_game_id = _require_sha(
        game["source_game_id"], label="candidate.game.source_game_id"
    )
    expected_source_game_id = _digest(
        "atomic-source-game-v1",
        {
            "source_sha256": source_sha256,
            "game_index": game_index,
            "marker_line": marker_line,
        },
    )
    if source_game_id != expected_source_game_id:
        raise SplitContractError(
            "candidate.game.source_game_id does not match source coordinates"
        )
    _require_sha(
        game["full_game_history_sha256"],
        label="candidate.game.full_game_history_sha256",
    )

    position = _require_mapping(row["position"], label="candidate.position")
    try:
        common.require_exact_fields(
            position,
            (
                "root_ply",
                "fen",
                "played_move",
                "legal_move_count",
                "terminal_state",
                "history_uci",
                "full_history_sha256",
                "transposition_key",
                "transposition_key_scope",
                "engine_key",
                "checkers",
            ),
            label="candidate.position",
        )
    except common.MiningArtifactError as error:
        raise SplitContractError(str(error)) from error
    root_ply = _require_int(
        position["root_ply"], label="candidate.position.root_ply", minimum=0
    )
    if root_ply >= move_count:
        raise SplitContractError(
            "candidate.position.root_ply must be below game.move_count"
        )
    history_value = position["history_uci"]
    if not isinstance(history_value, list):
        raise SplitContractError(
            "candidate.position.history_uci must be an array"
        )
    history_uci = tuple(
        _require_move(
            move,
            label=f"candidate.position.history_uci[{index}]",
        )
        for index, move in enumerate(history_value)
    )
    if root_ply != len(history_uci):
        raise SplitContractError(
            "candidate.position.root_ply must equal history_uci length"
        )
    fen = _require_fen(position["fen"], label="candidate.position.fen")
    played_move = _require_move(
        position["played_move"], label="candidate.position.played_move"
    )
    legal_move_count = position["legal_move_count"]
    if isinstance(legal_move_count, bool) or not isinstance(
        legal_move_count, int
    ):
        raise SplitContractError(
            "candidate.position.legal_move_count must be an integer"
        )
    if legal_move_count < 1:
        raise SplitContractError(
            "candidate.position.legal_move_count must be >= 1"
        )
    if position["terminal_state"] != "nonterminal":
        raise SplitContractError(
            "candidate.position.terminal_state must be nonterminal"
        )
    history_sha256 = _require_sha(
        position["full_history_sha256"],
        label="candidate.position.full_history_sha256",
    )
    expected_history_sha256 = _digest(
        "atomic-position-full-history-v1",
        {"root_fen": root_fen, "moves": list(history_uci)},
    )
    if history_sha256 != expected_history_sha256:
        raise SplitContractError(
            "candidate.position.full_history_sha256 does not match history"
        )
    transposition = _require_sha(
        position["transposition_key"],
        label="candidate.position.transposition_key",
    )
    expected_transposition = common.transposition_key(fen)
    if transposition != expected_transposition:
        raise SplitContractError(
            "candidate.position.transposition_key does not match fen"
        )
    if position["transposition_key_scope"] != "informational-only":
        raise SplitContractError("transposition key scope must be informational-only")
    engine_key = position["engine_key"]
    if engine_key is not None:
        _require_string(engine_key, label="candidate.position.engine_key")
    checkers = position["checkers"]
    if not isinstance(checkers, list):
        raise SplitContractError("candidate.position.checkers must be an array")
    checked_squares: list[str] = []
    for index, square in enumerate(checkers):
        square_value = _require_string(
            square, label=f"candidate.position.checkers[{index}]"
        )
        if SQUARE.fullmatch(square_value) is None:
            raise SplitContractError(
                f"candidate.position.checkers[{index}] must be a square"
            )
        checked_squares.append(square_value)
    if len(set(checked_squares)) != len(checked_squares):
        raise SplitContractError(
            "candidate.position.checkers must not contain duplicates"
        )

    position_id = common.derive_id(
        "atomic-match-position-id-v1",
        source_game_id,
        history_sha256,
        fen,
    )
    structural_history_sha256 = _digest(
        "atomic-mining-structural-history-v1",
        {"root_fen": root_fen, "moves": list(history_uci)},
    )
    return PositionIdentity(
        candidate_schema=DISCOVERY_INPUT_SCHEMA,
        position_id=position_id,
        source_game_id=source_game_id,
        source_group_sha256=source_sha256,
        provenance_position_id=position_id,
        provenance_source_game_id=source_game_id,
        pair_id=None,
        transposition_key=transposition,
        root_fen_sha256=root_fen_sha256,
        full_history_sha256=history_sha256,
        structural_history_sha256=structural_history_sha256,
        root_ply=root_ply,
        history_uci=history_uci,
        played_move=played_move,
        root_fen=root_fen,
        fen=fen,
        move_count=move_count,
        legal_move_count=legal_move_count,
        engine_key=(str(engine_key) if engine_key is not None else None),
        checkers=tuple(checked_squares),
    )


def _validate_result_bearing_candidate(
    row: Mapping[str, object],
) -> PositionIdentity:
    try:
        common.require_exact_fields(
            row,
            (
                "schema",
                "variant",
                "scientific_role",
                "source",
                "game",
                "position",
            ),
            label="candidate",
        )
    except common.MiningArtifactError as error:
        raise SplitContractError(str(error)) from error
    if row["schema"] != RESULT_BEARING_INPUT_SCHEMA:
        raise SplitContractError(
            f"candidate schema must be {RESULT_BEARING_INPUT_SCHEMA}"
        )
    if row["variant"] != "atomic":
        raise SplitContractError("candidate variant must be atomic")
    if row["scientific_role"] != "result-bearing-source":
        raise SplitContractError(
            "result-bearing candidate scientific_role differs"
        )

    source = _require_mapping(row["source"], label="candidate.source")
    try:
        common.require_exact_fields(
            source,
            (
                "execution_receipt_sha256",
                "games_sha256",
                "inventory_sha256",
                "experiment_id",
                "battery_id",
                "schedule_sha256",
                "pair_id",
                "pair_ordinal",
                "leg",
                "leakage_group_id",
                "source_game_id",
                "trajectory_sha256",
            ),
            label="candidate.source",
        )
    except common.MiningArtifactError as error:
        raise SplitContractError(str(error)) from error
    for field in (
        "execution_receipt_sha256",
        "games_sha256",
        "inventory_sha256",
        "schedule_sha256",
        "pair_id",
        "leakage_group_id",
        "source_game_id",
        "trajectory_sha256",
    ):
        _require_sha(source[field], label=f"candidate.source.{field}")
    _require_string(source["experiment_id"], label="candidate.source.experiment_id")
    _require_string(source["battery_id"], label="candidate.source.battery_id")
    pair_id = str(source["pair_id"])
    if source["leakage_group_id"] != pair_id:
        raise SplitContractError(
            "candidate.source.leakage_group_id must equal pair_id"
        )
    _require_int(
        source["pair_ordinal"],
        label="candidate.source.pair_ordinal",
        minimum=1,
    )
    leg = _require_int(source["leg"], label="candidate.source.leg", minimum=0)
    if leg not in {0, 1}:
        raise SplitContractError("candidate.source.leg must be 0 or 1")
    provenance_source_game_id = str(source["source_game_id"])

    game = _require_mapping(row["game"], label="candidate.game")
    try:
        common.require_exact_fields(
            game,
            (
                "root_fen",
                "root_fen_sha256",
                "ply_count",
                "stratum",
                "time_control",
                "book_sha256",
                "book_line",
                "white_network_role",
                "black_network_role",
                "result_current",
                "time_loss",
                "terminal_reason",
            ),
            label="candidate.game",
        )
    except common.MiningArtifactError as error:
        raise SplitContractError(str(error)) from error
    root_fen = _require_fen(game["root_fen"], label="candidate.game.root_fen")
    root_fen_sha256 = _require_sha(
        game["root_fen_sha256"], label="candidate.game.root_fen_sha256"
    )
    if root_fen_sha256 != _digest("atomic-root-fen-v1", root_fen):
        raise SplitContractError(
            "candidate.game.root_fen_sha256 does not match root_fen"
        )
    move_count = _require_int(
        game["ply_count"], label="candidate.game.ply_count", minimum=1
    )
    stratum = _require_string(game["stratum"], label="candidate.game.stratum")
    time_control = _require_mapping(
        game["time_control"], label="candidate.game.time_control"
    )
    try:
        common.require_exact_fields(
            time_control,
            ("base_ms", "increment_ms", "name"),
            label="candidate.game.time_control",
        )
    except common.MiningArtifactError as error:
        raise SplitContractError(str(error)) from error
    _require_int(
        time_control["base_ms"],
        label="candidate.game.time_control.base_ms",
        minimum=0,
    )
    _require_int(
        time_control["increment_ms"],
        label="candidate.game.time_control.increment_ms",
        minimum=0,
    )
    if _require_string(
        time_control["name"], label="candidate.game.time_control.name"
    ) != stratum:
        raise SplitContractError(
            "candidate.game.time_control.name must equal stratum"
        )
    _require_sha(game["book_sha256"], label="candidate.game.book_sha256")
    _require_int(game["book_line"], label="candidate.game.book_line", minimum=0)
    white_role = game["white_network_role"]
    black_role = game["black_network_role"]
    if {white_role, black_role} != {"current-v3", "run3b"}:
        raise SplitContractError(
            "candidate.game network roles must be current-v3 versus run3b"
        )
    if game["result_current"] not in {"win", "loss", "draw"}:
        raise SplitContractError("candidate.game.result_current is invalid")
    if not isinstance(game["time_loss"], bool):
        raise SplitContractError("candidate.game.time_loss must be boolean")
    terminal_reason = _require_string(
        game["terminal_reason"], label="candidate.game.terminal_reason"
    )
    time_loss_reasons = {
        "time-loss",
        "time-loss-insufficient-material-draw",
    }
    if bool(game["time_loss"]) != (terminal_reason in time_loss_reasons):
        raise SplitContractError(
            "candidate.game time_loss and terminal_reason differ"
        )

    position = _require_mapping(row["position"], label="candidate.position")
    try:
        common.require_exact_fields(
            position,
            (
                "position_id",
                "root_ply",
                "fen",
                "side_to_move",
                "history_uci",
                "history_sha256",
                "played_move",
                "result_white",
                "result_side_to_move",
                "transposition_key",
                "transposition_key_scope",
                "engine_key",
                "checkers",
                "legal_move_count",
            ),
            label="candidate.position",
        )
    except common.MiningArtifactError as error:
        raise SplitContractError(str(error)) from error
    provenance_position_id = _require_sha(
        position["position_id"], label="candidate.position.position_id"
    )
    root_ply = _require_int(
        position["root_ply"], label="candidate.position.root_ply", minimum=0
    )
    if root_ply >= move_count:
        raise SplitContractError(
            "candidate.position.root_ply must be below game.ply_count"
        )
    fen = _require_fen(position["fen"], label="candidate.position.fen")
    fen_side = common.fen_fields(fen)[1]
    if position["side_to_move"] != fen_side:
        raise SplitContractError(
            "candidate.position.side_to_move differs from FEN"
        )
    raw_history = position["history_uci"]
    if not isinstance(raw_history, list):
        raise SplitContractError(
            "candidate.position.history_uci must be an array"
        )
    history_uci = tuple(
        _require_move(
            move, label=f"candidate.position.history_uci[{index}]"
        )
        for index, move in enumerate(raw_history)
    )
    if len(history_uci) != root_ply:
        raise SplitContractError(
            "candidate.position.history_uci length must equal root_ply"
        )
    history_sha256 = _require_sha(
        position["history_sha256"],
        label="candidate.position.history_sha256",
    )
    if history_sha256 != _digest(
        "atomic-e00-position-history-v1",
        {"root_fen": root_fen, "moves": list(history_uci)},
    ):
        raise SplitContractError(
            "candidate.position.history_sha256 does not match history"
        )
    played_move = _require_move(
        position["played_move"], label="candidate.position.played_move"
    )
    legal_move_count = _require_int(
        position["legal_move_count"],
        label="candidate.position.legal_move_count",
        minimum=1,
    )
    result_white = position["result_white"]
    if result_white not in {"1-0", "0-1", "1/2-1/2"}:
        raise SplitContractError("candidate.position.result_white is invalid")
    expected_side_result = (
        "draw"
        if result_white == "1/2-1/2"
        else "win"
        if (result_white == "1-0") == (fen_side == "w")
        else "loss"
    )
    if position["result_side_to_move"] != expected_side_result:
        raise SplitContractError(
            "candidate.position.result_side_to_move does not recompute"
        )
    current_is_white = white_role == "current-v3"
    expected_current_result = (
        "draw"
        if result_white == "1/2-1/2"
        else "win"
        if (result_white == "1-0") == current_is_white
        else "loss"
    )
    if game["result_current"] != expected_current_result:
        raise SplitContractError(
            "candidate.game.result_current does not recompute"
        )
    expected_provenance_position_id = _digest(
        "atomic-e00-position-id-v1",
        {
            "history_sha256": history_sha256,
            "root_ply": root_ply,
            "source_game_id": provenance_source_game_id,
        },
    )
    if provenance_position_id != expected_provenance_position_id:
        raise SplitContractError(
            "candidate.position.position_id does not match provenance"
        )
    transposition = _require_sha(
        position["transposition_key"],
        label="candidate.position.transposition_key",
    )
    if transposition != common.transposition_key(fen):
        raise SplitContractError(
            "candidate.position.transposition_key does not match FEN"
        )
    if position["transposition_key_scope"] != "informational-only":
        raise SplitContractError(
            "candidate.position.transposition_key_scope is invalid"
        )
    engine_key_value = position["engine_key"]
    if engine_key_value is not None:
        _require_string(
            engine_key_value, label="candidate.position.engine_key"
        )
    checkers_value = position["checkers"]
    if not isinstance(checkers_value, list):
        raise SplitContractError("candidate.position.checkers must be an array")
    checkers: list[str] = []
    for index, square in enumerate(checkers_value):
        rendered = _require_string(
            square, label=f"candidate.position.checkers[{index}]"
        )
        if SQUARE.fullmatch(rendered) is None:
            raise SplitContractError(
                f"candidate.position.checkers[{index}] must be a square"
            )
        checkers.append(rendered)
    if len(set(checkers)) != len(checkers):
        raise SplitContractError(
            "candidate.position.checkers must not contain duplicates"
        )

    structural_history_sha256 = _digest(
        "atomic-mining-structural-history-v1",
        {"root_fen": root_fen, "moves": list(history_uci)},
    )
    structural_game_id = _digest(
        "atomic-mining-e00-structural-game-v1",
        {
            "leg": leg,
            "pair_id": pair_id,
            "root_fen_sha256": root_fen_sha256,
            "schedule_sha256": source["schedule_sha256"],
        },
    )
    structural_position_id = _digest(
        "atomic-mining-e00-structural-position-v1",
        {
            "fen": fen,
            "history_sha256": structural_history_sha256,
            "root_ply": root_ply,
            "structural_game_id": structural_game_id,
        },
    )
    return PositionIdentity(
        candidate_schema=RESULT_BEARING_INPUT_SCHEMA,
        position_id=structural_position_id,
        source_game_id=structural_game_id,
        source_group_sha256=str(source["schedule_sha256"]),
        provenance_position_id=provenance_position_id,
        provenance_source_game_id=provenance_source_game_id,
        pair_id=pair_id,
        transposition_key=transposition,
        root_fen_sha256=root_fen_sha256,
        full_history_sha256=history_sha256,
        structural_history_sha256=structural_history_sha256,
        root_ply=root_ply,
        history_uci=history_uci,
        played_move=played_move,
        root_fen=root_fen,
        fen=fen,
        move_count=move_count,
        legal_move_count=legal_move_count,
        engine_key=(
            str(engine_key_value) if engine_key_value is not None else None
        ),
        checkers=tuple(checkers),
    )


def validate_candidate(row: Mapping[str, object]) -> PositionIdentity:
    schema = row.get("schema")
    if schema == DISCOVERY_INPUT_SCHEMA:
        return _validate_discovery_candidate(row)
    if schema == RESULT_BEARING_INPUT_SCHEMA:
        return _validate_result_bearing_candidate(row)
    raise SplitContractError(
        "candidate schema must be one of "
        f"{sorted(INPUT_SCHEMAS)}"
    )


def _validate_source_game_histories(
    candidates: Sequence[Mapping[str, object]],
    identities: Sequence[PositionIdentity],
) -> None:
    rows_by_game: dict[
        str, list[tuple[Mapping[str, object], PositionIdentity]]
    ] = {}
    for row, identity in zip(candidates, identities, strict=True):
        rows_by_game.setdefault(identity.source_game_id, []).append(
            (row, identity)
        )

    for source_game_id, entries in rows_by_game.items():
        first_row = entries[0][0]
        first_source = _require_mapping(
            first_row["source"], label="candidate.source"
        )
        first_game = _require_mapping(first_row["game"], label="candidate.game")
        first_identity = entries[0][1]
        move_count = first_identity.move_count
        seen_plies: set[int] = set()
        terminal_prefix: tuple[str, ...] | None = None
        for row, identity in entries:
            source = _require_mapping(row["source"], label="candidate.source")
            game = _require_mapping(row["game"], label="candidate.game")
            if identity.candidate_schema != first_identity.candidate_schema:
                raise SplitContractError(
                    f"source game mixes candidate schemas: {source_game_id}"
                )
            if source != first_source or game != first_game:
                raise SplitContractError(
                    f"source game metadata is inconsistent: {source_game_id}"
                )
            if identity.root_ply in seen_plies:
                raise SplitContractError(
                    f"source game repeats root_ply {identity.root_ply}: "
                    f"{source_game_id}"
                )
            seen_plies.add(identity.root_ply)
            if identity.root_ply == move_count - 1:
                terminal_prefix = (
                    *identity.history_uci,
                    identity.played_move,
                )

        if terminal_prefix is None:
            raise SplitContractError(
                "source game inventory is incomplete: no candidate authenticates "
                f"the final played move for {source_game_id}"
            )
        for _row, identity in entries:
            if identity.history_uci != terminal_prefix[: identity.root_ply]:
                raise SplitContractError(
                    f"candidate history is not a source-game prefix: "
                    f"{identity.position_id}"
                )
            if identity.played_move != terminal_prefix[identity.root_ply]:
                raise SplitContractError(
                    f"candidate played_move is inconsistent with source game: "
                    f"{identity.position_id}"
                )
        if (
            first_identity.candidate_schema == RESULT_BEARING_INPUT_SCHEMA
            and seen_plies != set(range(move_count))
        ):
            raise SplitContractError(
                f"source game inventory is incomplete: {source_game_id}"
            )

        if first_identity.candidate_schema == DISCOVERY_INPUT_SCHEMA:
            expected_full_history_sha256 = _digest(
                "atomic-full-game-history-v1",
                {
                    "root_fen": first_identity.root_fen,
                    "moves": list(terminal_prefix),
                },
            )
            observed_full_history_sha256 = str(
                first_game["full_game_history_sha256"]
            )
            if observed_full_history_sha256 != expected_full_history_sha256:
                raise SplitContractError(
                    "candidate.game.full_game_history_sha256 does not match "
                    f"the authenticated full game for {source_game_id}"
                )
            continue

        first_position = _require_mapping(
            first_row["position"], label="candidate.position"
        )
        result_white = str(first_position["result_white"])
        expected_trajectory = _digest(
            "atomic-e00-trajectory-v1",
            {
                "moves": list(terminal_prefix),
                "result_white": result_white,
                "root_fen": first_identity.root_fen,
            },
        )
        if first_source["trajectory_sha256"] != expected_trajectory:
            raise SplitContractError(
                "candidate.source.trajectory_sha256 does not match "
                f"the authenticated full game for {source_game_id}"
            )
        expected_provenance_game_id = _digest(
            "atomic-e00-source-game-v1",
            {
                "battery_id": first_source["battery_id"],
                "experiment_id": first_source["experiment_id"],
                "leg": first_source["leg"],
                "pair_id": first_source["pair_id"],
                "schedule_sha256": first_source["schedule_sha256"],
                "trajectory_sha256": expected_trajectory,
            },
        )
        if (
            first_identity.provenance_source_game_id
            != expected_provenance_game_id
        ):
            raise SplitContractError(
                "candidate.source.source_game_id does not match provenance"
            )


def _require_sorted_unique_sha_list(
    value: object,
    *,
    label: str,
) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise SplitContractError(f"{label} must be an array")
    values = tuple(
        _require_sha(item, label=f"{label}[{index}]")
        for index, item in enumerate(value)
    )
    if tuple(sorted(set(values))) != values:
        raise SplitContractError(f"{label} must be sorted and unique")
    return values


def _inventory_id(
    namespace: str,
    entry: Mapping[str, object],
) -> str:
    core = {key: value for key, value in entry.items() if key != "inventory_id"}
    return _digest(namespace, core)


def _validate_inventory_contract(
    value: object,
    records: Sequence[Mapping[str, object]],
    *,
    label: str,
) -> dict[str, object]:
    contract = _require_mapping(value, label=label)
    try:
        common.require_exact_fields(
            contract, ("count", "sha256"), label=label
        )
    except common.MiningArtifactError as error:
        raise SplitContractError(str(error)) from error
    count = _require_int(contract["count"], label=f"{label}.count", minimum=0)
    digest = _require_sha(contract["sha256"], label=f"{label}.sha256")
    expected_digest = common.sha256_bytes(common.canonical_json_bytes(records))
    if count != len(records) or digest != expected_digest:
        raise SplitContractError(f"{label} does not bind its exact inventory")
    return {"count": count, "sha256": digest}


def _validate_identity_entry(
    value: object,
    *,
    label: str,
    kind: str,
) -> tuple[_Node, str | None]:
    entry = _require_mapping(value, label=label)
    fields = [
        "inventory_id",
        "source_game_id",
        "root_fen_sha256",
        "full_history_sha256",
        "transposition_key",
        "leakage_group_ids",
    ]
    namespace = "atomic-mining-replay-inventory-v1"
    parent_node_id: str | None = None
    if kind == "TEACHER_CONTINUATION":
        fields.append("parent_node_id")
        namespace = "atomic-mining-teacher-continuation-v1"
    try:
        common.require_exact_fields(entry, tuple(fields), label=label)
    except common.MiningArtifactError as error:
        raise SplitContractError(str(error)) from error
    inventory_id = _require_sha(
        entry["inventory_id"], label=f"{label}.inventory_id"
    )
    if inventory_id != _inventory_id(namespace, entry):
        raise SplitContractError(f"{label}.inventory_id does not match content")
    source_game_id = _require_sha(
        entry["source_game_id"], label=f"{label}.source_game_id"
    )
    root_fen_sha256 = _require_sha(
        entry["root_fen_sha256"], label=f"{label}.root_fen_sha256"
    )
    full_history_sha256 = _require_sha(
        entry["full_history_sha256"], label=f"{label}.full_history_sha256"
    )
    transposition_key = _require_sha(
        entry["transposition_key"], label=f"{label}.transposition_key"
    )
    leakage_group_ids = _require_sorted_unique_sha_list(
        entry["leakage_group_ids"],
        label=f"{label}.leakage_group_ids",
    )
    if kind == "TEACHER_CONTINUATION":
        parent_node_id = _require_sha(
            entry["parent_node_id"], label=f"{label}.parent_node_id"
        )
    return (
        _Node(
            node_id=inventory_id,
            kind=kind,
            source_game_id=source_game_id,
            root_fen_sha256=root_fen_sha256,
            full_history_sha256=full_history_sha256,
            structural_history_sha256=None,
            transposition_key=transposition_key,
            pair_id=None,
            leakage_group_ids=leakage_group_ids,
            candidate=None,
            inventory_entry=dict(entry),
        ),
        parent_node_id,
    )


def _validate_deny_entry(value: object, *, label: str) -> _Node:
    entry = _require_mapping(value, label=label)
    try:
        common.require_exact_fields(
            entry,
            ("inventory_id", "root_fen_sha256", "leakage_group_ids"),
            label=label,
        )
    except common.MiningArtifactError as error:
        raise SplitContractError(str(error)) from error
    inventory_id = _require_sha(
        entry["inventory_id"], label=f"{label}.inventory_id"
    )
    if inventory_id != _inventory_id("atomic-mining-deny-e05-v1", entry):
        raise SplitContractError(f"{label}.inventory_id does not match content")
    return _Node(
        node_id=inventory_id,
        kind="DENY_E05",
        source_game_id=None,
        root_fen_sha256=_require_sha(
            entry["root_fen_sha256"], label=f"{label}.root_fen_sha256"
        ),
        full_history_sha256=None,
        structural_history_sha256=None,
        transposition_key=None,
        pair_id=None,
        leakage_group_ids=_require_sorted_unique_sha_list(
            entry["leakage_group_ids"],
            label=f"{label}.leakage_group_ids",
        ),
        candidate=None,
        inventory_entry=dict(entry),
    )


def _validate_preprobe_inventory(
    value: Mapping[str, object],
    *,
    candidates: Sequence[Mapping[str, object]],
    identities: Sequence[PositionIdentity],
) -> tuple[
    str,
    dict[str, _Node],
    tuple[tuple[str, str, str, str], ...],
    dict[str, object],
]:
    try:
        common.require_exact_fields(
            value,
            (
                "schema",
                "mode",
                "candidate_position_ids",
                "replay",
                "teacher_continuations",
                "deny_e05",
                "opening_book_groups",
                "replay_contract",
                "deny_e05_contract",
            ),
            label="preprobe inventory",
        )
    except common.MiningArtifactError as error:
        raise SplitContractError(str(error)) from error
    if value["schema"] != INVENTORY_SCHEMA:
        raise SplitContractError(
            f"preprobe inventory schema must be {INVENTORY_SCHEMA}"
        )
    mode = value["mode"]
    if mode not in {"scientific", "technical-smoke"}:
        raise SplitContractError(
            "preprobe inventory mode must be scientific or technical-smoke"
        )
    candidate_position_ids = _require_sorted_unique_sha_list(
        value["candidate_position_ids"],
        label="preprobe inventory.candidate_position_ids",
    )
    expected_candidate_ids = tuple(
        sorted(identity.position_id for identity in identities)
    )
    if candidate_position_ids != expected_candidate_ids:
        raise SplitContractError(
            "candidate_position_ids must exactly equal the input candidate set"
        )

    replay_values = value["replay"]
    teacher_values = value["teacher_continuations"]
    deny_values = value["deny_e05"]
    group_values = value["opening_book_groups"]
    if not isinstance(replay_values, list):
        raise SplitContractError("preprobe inventory.replay must be an array")
    if not isinstance(teacher_values, list):
        raise SplitContractError(
            "preprobe inventory.teacher_continuations must be an array"
        )
    if not isinstance(deny_values, list):
        raise SplitContractError("preprobe inventory.deny_e05 must be an array")
    if not isinstance(group_values, list):
        raise SplitContractError(
            "preprobe inventory.opening_book_groups must be an array"
        )

    if mode == "scientific":
        if not replay_values or not deny_values:
            raise SplitContractError(
                "scientific mode requires non-empty REPLAY and deny-E05 inventories"
            )
        replay_contract = _validate_inventory_contract(
            value["replay_contract"],
            replay_values,
            label="preprobe inventory.replay_contract",
        )
        deny_contract = _validate_inventory_contract(
            value["deny_e05_contract"],
            deny_values,
            label="preprobe inventory.deny_e05_contract",
        )
    else:
        if (
            replay_values
            or teacher_values
            or deny_values
            or group_values
            or value["replay_contract"] is not None
            or value["deny_e05_contract"] is not None
        ):
            raise SplitContractError(
                "technical-smoke inventory must not declare scientific inventories"
            )
        replay_contract = {"count": 0, "sha256": None}
        deny_contract = {"count": 0, "sha256": None}

    nodes: dict[str, _Node] = {}
    for row, identity in zip(candidates, identities, strict=True):
        game = _require_mapping(row["game"], label="candidate.game")
        nodes[identity.position_id] = _Node(
            node_id=identity.position_id,
            kind="E00_CANDIDATE",
            source_game_id=identity.source_game_id,
            root_fen_sha256=identity.root_fen_sha256,
            full_history_sha256=identity.full_history_sha256,
            structural_history_sha256=identity.structural_history_sha256,
            transposition_key=identity.transposition_key,
            pair_id=identity.pair_id,
            leakage_group_ids=(),
            candidate=dict(row),
            inventory_entry=None,
        )

    teacher_parents: list[tuple[str, str]] = []
    replay_ids: list[str] = []
    for index, raw in enumerate(replay_values):
        node, _parent = _validate_identity_entry(
            raw,
            label=f"preprobe inventory.replay[{index}]",
            kind="REPLAY",
        )
        if node.node_id in nodes:
            raise SplitContractError(
                f"inventory node belongs to multiple roles: {node.node_id}"
            )
        nodes[node.node_id] = node
        replay_ids.append(node.node_id)
    if replay_ids != sorted(replay_ids):
        raise SplitContractError(
            "REPLAY inventory must be sorted by inventory_id"
        )
    teacher_ids: list[str] = []
    for index, raw in enumerate(teacher_values):
        node, parent = _validate_identity_entry(
            raw,
            label=f"preprobe inventory.teacher_continuations[{index}]",
            kind="TEACHER_CONTINUATION",
        )
        if node.node_id in nodes:
            raise SplitContractError(
                f"inventory node belongs to multiple roles: {node.node_id}"
            )
        assert parent is not None
        nodes[node.node_id] = node
        teacher_parents.append((node.node_id, parent))
        teacher_ids.append(node.node_id)
    if teacher_ids != sorted(teacher_ids):
        raise SplitContractError(
            "teacher continuation inventory must be sorted by inventory_id"
        )
    deny_ids: list[str] = []
    for index, raw in enumerate(deny_values):
        node = _validate_deny_entry(
            raw, label=f"preprobe inventory.deny_e05[{index}]"
        )
        if node.node_id in nodes:
            raise SplitContractError(
                f"inventory node belongs to multiple roles: {node.node_id}"
            )
        nodes[node.node_id] = node
        deny_ids.append(node.node_id)
    if deny_ids != sorted(deny_ids):
        raise SplitContractError(
            "deny-E05 inventory must be sorted by inventory_id"
        )

    explicit_edges: set[tuple[str, str, str, str]] = set()
    for child_id, parent_id in teacher_parents:
        parent = nodes.get(parent_id)
        if parent is None or parent.kind == "DENY_E05":
            raise SplitContractError(
                f"teacher continuation parent is absent or denied: {parent_id}"
            )
        left, right = sorted((child_id, parent_id))
        explicit_edges.add(("teacher_continuation", child_id, left, right))

    previous_group_id = ""
    for index, raw_group in enumerate(group_values):
        group = _require_mapping(
            raw_group,
            label=f"preprobe inventory.opening_book_groups[{index}]",
        )
        try:
            common.require_exact_fields(
                group,
                ("group_id", "member_node_ids"),
                label=f"preprobe inventory.opening_book_groups[{index}]",
            )
        except common.MiningArtifactError as error:
            raise SplitContractError(str(error)) from error
        members = _require_sorted_unique_sha_list(
            group["member_node_ids"],
            label=(
                f"preprobe inventory.opening_book_groups[{index}]"
                ".member_node_ids"
            ),
        )
        if len(members) < 2:
            raise SplitContractError(
                "opening/book group must contain at least two nodes"
            )
        missing = [member for member in members if member not in nodes]
        if missing:
            raise SplitContractError(
                f"opening/book group references absent node: {missing[0]}"
            )
        group_id = _require_sha(
            group["group_id"],
            label=f"preprobe inventory.opening_book_groups[{index}].group_id",
        )
        expected_group_id = _digest(
            "atomic-mining-opening-book-group-v1", list(members)
        )
        if group_id != expected_group_id:
            raise SplitContractError(
                "opening/book group_id does not match member_node_ids"
            )
        if group_id <= previous_group_id:
            raise SplitContractError(
                "opening/book groups must be sorted and unique by group_id"
            )
        previous_group_id = group_id
        anchor = members[0]
        for member in members[1:]:
            left, right = sorted((anchor, member))
            explicit_edges.add(("opening_book", group_id, left, right))

    contracts = {
        "replay": replay_contract,
        "deny_e05": deny_contract,
    }
    return mode, nodes, tuple(sorted(explicit_edges)), contracts


def build_assignments(
    candidates: Sequence[Mapping[str, object]],
    *,
    inventory: Mapping[str, object],
    seed: int,
    calibration_fraction: float,
    confirmation_fraction: float,
) -> tuple[dict[str, list[dict[str, object]]], dict[str, object]]:
    """Build one frozen five-way split over the complete pre-probe graph."""

    if not candidates:
        raise SplitContractError("at least one candidate is required")
    _require_int(seed, label="seed", minimum=0)
    if seed > (1 << 64) - 1:
        raise SplitContractError("seed must fit unsigned 64 bits")
    if (
        isinstance(calibration_fraction, bool)
        or not isinstance(calibration_fraction, (float, int))
        or not 0.0 < float(calibration_fraction) < 1.0
    ):
        raise SplitContractError(
            "calibration_fraction must be strictly between 0 and 1"
        )
    if (
        isinstance(confirmation_fraction, bool)
        or not isinstance(confirmation_fraction, (float, int))
        or not 0.0 < float(confirmation_fraction) < 1.0
    ):
        raise SplitContractError(
            "confirmation_fraction must be strictly between 0 and 1"
        )
    calibration_fraction = float(calibration_fraction)
    confirmation_fraction = float(confirmation_fraction)
    if calibration_fraction + confirmation_fraction >= 1.0:
        raise SplitContractError(
            "calibration_fraction + confirmation_fraction must be below 1"
        )

    identities: list[PositionIdentity] = []
    seen_positions: set[str] = set()
    source_sha256s: set[str] = set()
    for row in candidates:
        identity = validate_candidate(row)
        if identity.position_id in seen_positions:
            raise SplitContractError(
                f"duplicate trajectory position: {identity.position_id}"
            )
        seen_positions.add(identity.position_id)
        identities.append(identity)
        source_sha256s.add(identity.source_group_sha256)
    _validate_source_game_histories(candidates, identities)
    mode, nodes, explicit_edges, inventory_contracts = (
        _validate_preprobe_inventory(
            inventory,
            candidates=candidates,
            identities=identities,
        )
    )

    edge_set = set(explicit_edges)
    for kind, attribute in (
        ("source_game", "source_game_id"),
        ("transposition", "transposition_key"),
        ("root_fen", "root_fen_sha256"),
        ("full_history", "full_history_sha256"),
        ("structural_history", "structural_history_sha256"),
        ("pair", "pair_id"),
    ):
        members_by_key: dict[str, list[str]] = {}
        for node in nodes.values():
            key = getattr(node, attribute)
            if key is not None:
                members_by_key.setdefault(key, []).append(node.node_id)
        for key, member_ids in members_by_key.items():
            ordered = sorted(member_ids)
            for member in ordered[1:]:
                edge_set.add((kind, key, ordered[0], member))
    members_by_leakage_group: dict[str, list[str]] = {}
    for node in nodes.values():
        for group_id in node.leakage_group_ids:
            members_by_leakage_group.setdefault(group_id, []).append(node.node_id)
    for group_id, member_ids in members_by_leakage_group.items():
        ordered = sorted(member_ids)
        for member in ordered[1:]:
            edge_set.add(("leakage_group", group_id, ordered[0], member))

    edges = tuple(sorted(edge_set))
    union_find = _UnionFind(nodes)
    for _kind, _key, left, right in edges:
        union_find.union(left, right)
    member_ids_by_root: dict[str, list[str]] = {}
    for node_id in sorted(nodes):
        member_ids_by_root.setdefault(union_find.find(node_id), []).append(node_id)
    members_by_component: dict[str, tuple[str, ...]] = {}
    component_by_node: dict[str, str] = {}
    for member_ids in member_ids_by_root.values():
        members = tuple(sorted(member_ids))
        component_id = common.derive_id(
            "atomic-mining-global-component-v1", *members
        )
        members_by_component[component_id] = members
        for node_id in members:
            component_by_node[node_id] = component_id

    component_partition: dict[str, str] = {}
    dynamic_components: list[str] = []
    candidate_count_by_component: dict[str, int] = {}
    for component_id, member_ids in members_by_component.items():
        kinds = {nodes[node_id].kind for node_id in member_ids}
        candidate_count_by_component[component_id] = sum(
            nodes[node_id].kind == "E00_CANDIDATE" for node_id in member_ids
        )
        if "DENY_E05" in kinds:
            component_partition[component_id] = "DENY_E05"
        elif "REPLAY" in kinds:
            component_partition[component_id] = "REPLAY_ELIGIBLE"
        else:
            if not kinds <= {"E00_CANDIDATE", "TEACHER_CONTINUATION"}:
                raise SplitContractError(
                    f"component has no partition policy: {component_id}"
                )
            dynamic_components.append(component_id)

    ordered_dynamic = sorted(
        dynamic_components,
        key=lambda component_id: (
            common.derive_id(
                "atomic-mining-global-split-order-v1",
                str(seed),
                component_id,
            ),
            component_id,
        ),
    )
    if mode == "scientific" and len(ordered_dynamic) < 3:
        raise SplitContractError(
            "scientific split needs at least three eligible global components"
        )
    if mode == "technical-smoke":
        for component_id in ordered_dynamic:
            component_partition[component_id] = "COHORT_ELIGIBLE"
    else:
        total_candidates = sum(
            candidate_count_by_component[component_id]
            for component_id in ordered_dynamic
        )
        cal_target = round(total_candidates * calibration_fraction)
        conf_target = round(total_candidates * confirmation_fraction)
        calibration_components = {ordered_dynamic[0]}
        calibration_count = candidate_count_by_component[ordered_dynamic[0]]
        for component_id in ordered_dynamic[1:-2]:
            size = candidate_count_by_component[component_id]
            if abs(calibration_count + size - cal_target) < abs(
                calibration_count - cal_target
            ):
                calibration_components.add(component_id)
                calibration_count += size
        remaining = [
            component_id
            for component_id in ordered_dynamic
            if component_id not in calibration_components
        ]
        confirmation_components = {remaining[0]}
        confirmation_count = candidate_count_by_component[remaining[0]]
        for component_id in remaining[1:-1]:
            size = candidate_count_by_component[component_id]
            if abs(confirmation_count + size - conf_target) < abs(
                confirmation_count - conf_target
            ):
                confirmation_components.add(component_id)
                confirmation_count += size
        for component_id in ordered_dynamic:
            if component_id in calibration_components:
                component_partition[component_id] = "CAL"
            elif component_id in confirmation_components:
                component_partition[component_id] = "CONF"
            else:
                component_partition[component_id] = "COHORT_ELIGIBLE"

    partitions: dict[str, list[dict[str, object]]] = {
        partition: [] for partition in PARTITIONS
    }
    node_manifest: list[dict[str, object]] = []
    for node_id in sorted(nodes):
        node = nodes[node_id]
        component_id = component_by_node[node_id]
        partition = component_partition[component_id]
        assignment = {
            "schema": ASSIGNMENT_SCHEMA,
            "partition": partition,
            "component_id": component_id,
            "node_id": node.node_id,
            "node_kind": node.kind,
            "candidate": (
                dict(node.candidate) if node.candidate is not None else None
            ),
            "inventory_entry": (
                dict(node.inventory_entry)
                if node.inventory_entry is not None
                else None
            ),
        }
        partitions[partition].append(assignment)
        node_manifest.append(
            {
                "node_id": node.node_id,
                "node_kind": node.kind,
                "component_id": component_id,
                "partition": partition,
            }
        )

    component_manifest: list[dict[str, object]] = []
    for component_id in sorted(members_by_component):
        member_ids = members_by_component[component_id]
        component_nodes = [nodes[node_id] for node_id in member_ids]
        component_manifest.append(
            {
                "component_id": component_id,
                "partition": component_partition[component_id],
                "node_ids": list(member_ids),
                "node_kinds": sorted(
                    {node.kind for node in component_nodes}
                ),
                "source_game_ids": sorted(
                    {
                        node.source_game_id
                        for node in component_nodes
                        if node.source_game_id is not None
                    }
                ),
                "root_fen_sha256s": sorted(
                    {node.root_fen_sha256 for node in component_nodes}
                ),
                "transposition_keys": sorted(
                    {
                        node.transposition_key
                        for node in component_nodes
                        if node.transposition_key is not None
                    }
                ),
            }
        )

    partition_node_ids = {
        partition: sorted(str(row["node_id"]) for row in rows)
        for partition, rows in partitions.items()
    }
    partition_component_ids = {
        partition: sorted(
            {
                str(row["component_id"])
                for row in rows
            }
        )
        for partition, rows in partitions.items()
    }
    all_partition_nodes = [
        node_id
        for partition in PARTITIONS
        for node_id in partition_node_ids[partition]
    ]
    all_partition_components = [
        component_id
        for partition in PARTITIONS
        for component_id in partition_component_ids[partition]
    ]
    if len(set(all_partition_nodes)) != len(nodes):
        raise AssertionError("global node partitions overlap or are incomplete")
    if len(set(all_partition_components)) != len(members_by_component):
        raise AssertionError(
            "global component partitions overlap or are incomplete"
        )
    if mode == "scientific" and any(not partitions[name] for name in PARTITIONS):
        raise SplitContractError(
            "scientific split must represent all five non-empty partitions"
        )

    edge_manifest = [
        {
            "edge_kind": kind,
            "edge_key": key,
            "left_node_id": left,
            "right_node_id": right,
        }
        for kind, key, left, right in edges
    ]
    metadata = {
        "schema": RECEIPT_SCHEMA,
        "mode": mode,
        "seed": seed,
        "fractions": {
            "CAL": calibration_fraction,
            "CONF": confirmation_fraction,
            "COHORT_ELIGIBLE": 1.0
            - calibration_fraction
            - confirmation_fraction,
        },
        "counts": {
            "input_positions": len(candidates),
            "nodes": len(nodes),
            "edges": len(edges),
            "components": len(members_by_component),
            "nodes_by_partition": {
                partition: len(partitions[partition])
                for partition in PARTITIONS
            },
            "components_by_partition": {
                partition: len(partition_component_ids[partition])
                for partition in PARTITIONS
            },
        },
        "source_sha256s": sorted(source_sha256s),
        "inventory_contracts": inventory_contracts,
        "nodes": node_manifest,
        "edges": edge_manifest,
        "components": component_manifest,
        "zero_overlap": {
            "all_nodes_assigned_once": True,
            "all_components_assigned_once": True,
            "node_overlap_count": 0,
            "component_overlap_count": 0,
            "node_ids_by_partition": partition_node_ids,
            "component_ids_by_partition": partition_component_ids,
        },
    }
    return partitions, metadata


def split_file(
    input_path: Path,
    inventory_path: Path,
    output_paths: Mapping[str, Path],
    receipt_path: Path,
    *,
    seed: int,
    calibration_fraction: float,
    confirmation_fraction: float,
) -> SplitSummary:
    if set(output_paths) != set(PARTITIONS):
        raise ValueError(
            f"output_paths keys must be exactly {list(PARTITIONS)}"
        )
    targets = (*[output_paths[name] for name in PARTITIONS], receipt_path)
    if len({path.absolute() for path in targets}) != len(targets):
        raise ValueError("split output paths must be distinct")
    for path in targets:
        if path.exists():
            raise FileExistsError(f"refusing to overwrite mining artifact: {path}")

    input_path = input_path.resolve(strict=True)
    inventory_path = inventory_path.resolve(strict=True)
    input_snapshot = common.load_jsonl_snapshot(input_path)
    inventory_snapshot = common.load_jsonl_snapshot(inventory_path)
    if len(inventory_snapshot.rows) != 1:
        raise SplitContractError(
            "preprobe inventory JSONL must contain exactly one row"
        )
    candidates = list(input_snapshot.rows)
    partitions, metadata = build_assignments(
        candidates,
        inventory=inventory_snapshot.rows[0],
        seed=seed,
        calibration_fraction=calibration_fraction,
        confirmation_fraction=confirmation_fraction,
    )
    output_payloads = {
        partition: b"".join(
            common.canonical_json_bytes(row)
            for row in partitions[partition]
        )
        for partition in PARTITIONS
    }
    output_receipts = {
        partition: {
            "path": str(output_paths[partition].absolute()),
            "sha256": common.sha256_bytes(output_payloads[partition]),
            "size_bytes": len(output_payloads[partition]),
            "row_count": len(partitions[partition]),
        }
        for partition in PARTITIONS
    }
    receipt = {
        **metadata,
        "inputs": {
            "candidates": {
                "path": str(input_snapshot.path),
                "sha256": input_snapshot.sha256,
                "size_bytes": input_snapshot.size_bytes,
                "row_count": len(input_snapshot.rows),
            },
            "preprobe_inventory": {
                "path": str(inventory_snapshot.path),
                "sha256": inventory_snapshot.sha256,
                "size_bytes": inventory_snapshot.size_bytes,
                "row_count": len(inventory_snapshot.rows),
            },
        },
        "outputs": output_receipts,
    }
    receipt_payload = common.canonical_json_bytes(receipt)
    try:
        prepared_receipt = json.loads(
            receipt_payload.decode("utf-8", errors="strict")
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise SplitContractError(
            "prepared split receipt is not valid JSON"
        ) from error
    if (
        prepared_receipt != receipt
        or common.canonical_json_bytes(prepared_receipt) != receipt_payload
    ):
        raise SplitContractError(
            "prepared split receipt is not canonical"
        )
    summary = SplitSummary(
        positions=len(candidates),
        nodes=int(metadata["counts"]["nodes"]),  # type: ignore[index]
        edges=int(metadata["counts"]["edges"]),  # type: ignore[index]
        components=int(
            metadata["counts"]["components"]  # type: ignore[index]
        ),
        partition_counts={
            partition: len(partitions[partition])
            for partition in PARTITIONS
        },
        receipt_sha256=common.sha256_bytes(receipt_payload),
    )
    published: list[Path] = []
    try:
        for partition in PARTITIONS:
            path = output_paths[partition]
            common.write_new_bytes(path, output_payloads[partition])
            published.append(path)
        for partition in PARTITIONS:
            snapshot = common.load_jsonl_snapshot(output_paths[partition])
            if (
                snapshot.rows != tuple(partitions[partition])
                or snapshot.sha256
                != output_receipts[partition]["sha256"]
                or snapshot.size_bytes
                != output_receipts[partition]["size_bytes"]
            ):
                raise SplitContractError(
                    f"published {partition} split changed before commit"
                )
        for label, path, snapshot in (
            ("candidates", input_path, input_snapshot),
            ("preprobe inventory", inventory_path, inventory_snapshot),
        ):
            payload = common.read_stable_file_bytes(
                path, label=f"postflight split {label}"
            )
            if (
                common.sha256_bytes(payload) != snapshot.sha256
                or len(payload) != snapshot.size_bytes
            ):
                raise SplitContractError(
                    f"split {label} changed before commit"
                )
        # Atomic create-new publication is the final fallible operation.
        common.write_new_bytes(receipt_path, receipt_payload)
        return summary
    except BaseException:
        for path in reversed(published):
            path.unlink(missing_ok=True)
        raise


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--inventory", required=True, type=Path)
    parser.add_argument("--cal-output", required=True, type=Path)
    parser.add_argument("--conf-output", required=True, type=Path)
    parser.add_argument("--cohort-eligible-output", required=True, type=Path)
    parser.add_argument("--replay-eligible-output", required=True, type=Path)
    parser.add_argument("--deny-e05-output", required=True, type=Path)
    parser.add_argument("--receipt", required=True, type=Path)
    parser.add_argument("--seed", required=True, type=int)
    parser.add_argument("--calibration-fraction", required=True, type=float)
    parser.add_argument("--confirmation-fraction", required=True, type=float)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    output_paths = {
        "CAL": args.cal_output,
        "CONF": args.conf_output,
        "COHORT_ELIGIBLE": args.cohort_eligible_output,
        "REPLAY_ELIGIBLE": args.replay_eligible_output,
        "DENY_E05": args.deny_e05_output,
    }
    summary = split_file(
        args.input,
        args.inventory,
        output_paths,
        args.receipt,
        seed=args.seed,
        calibration_fraction=args.calibration_fraction,
        confirmation_fraction=args.confirmation_fraction,
    )
    print(
        common.canonical_json_line(
            {
                "positions": summary.positions,
                "nodes": summary.nodes,
                "edges": summary.edges,
                "components": summary.components,
                "partition_counts": dict(summary.partition_counts),
                "receipt_sha256": summary.receipt_sha256,
            }
        ),
        end="",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
