from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path
import stat
import sys

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools.atomic_mining import common
from tools.atomic_mining import probe_disagreements as probe
from tools.atomic_mining.uci_session import (
    FenInspection,
    ProbeResult,
    UciInfo,
    UciOptionSetting,
    UciScore,
)


PREVIOUS_FEN = "7k/8/8/3pP3/8/8/p7/KR6 w - d6 0 1"
CURRENT_FEN = "7k/8/3P4/8/8/8/p7/KR6 b - - 0 1"
TEACHER_CHILD_FEN = "7k/8/3P4/8/8/8/8/8 w - - 0 2"
QUIET_FEN = "7k/8/8/8/8/8/8/K7 w - - 0 1"


def _hash(character: str) -> str:
    return character * 64


def _digest(namespace: str, value: object) -> str:
    return common.sha256_bytes(
        common.canonical_json_bytes({"namespace": namespace, "value": value})
    )


def _candidate(
    *,
    root_fen: str = PREVIOUS_FEN,
    fen: str = CURRENT_FEN,
    history: list[str] | None = None,
    checkers: list[str] | None = None,
) -> dict[str, object]:
    history = ["e5d6"] if history is None else history
    checkers = ["d6"] if checkers is None else checkers
    source_sha256 = _hash("a")
    game_index = 1
    marker_line = 10
    played_move = "h8g8"
    source_game_id = _digest(
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
            "path": "C:/evidence/match.log",
            "sha256": source_sha256,
            "size_bytes": 123,
        },
        "game": {
            "game_index": game_index,
            "marker_line": marker_line,
            "root_fen": root_fen,
            "root_fen_sha256": _digest("atomic-root-fen-v1", root_fen),
            "source_game_id": source_game_id,
            "full_game_history_sha256": _digest(
                "atomic-full-game-history-v1",
                {"root_fen": root_fen, "moves": [*history, played_move]},
            ),
            "move_count": max(1, len(history) + 1),
        },
        "position": {
            "root_ply": len(history),
            "fen": fen,
            "played_move": played_move,
            "legal_move_count": 3,
            "terminal_state": "nonterminal",
            "history_uci": history,
            "full_history_sha256": _digest(
                "atomic-position-full-history-v1",
                {"root_fen": root_fen, "moves": history},
            ),
            "transposition_key": common.transposition_key(fen),
            "transposition_key_scope": "informational-only",
            "engine_key": "ABCDEF",
            "checkers": checkers,
        },
    }


def _config(*, repeats: int = 2, multipv: int = 3) -> dict[str, object]:
    def artifact(path: str, digest: str) -> dict[str, object]:
        return {"path": path, "sha256": digest, "size_bytes": 42}

    return {
        "engine": artifact("engine.exe", _hash("1")),
        "current_net": artifact("current.nnue", _hash("2")),
        "teacher_net": artifact("teacher.nnue", _hash("3")),
        "nodes": 128_000_000,
        "multipv": multipv,
        "threads": 1,
        "hash_mb": 512,
        "clear_hash": True,
        "teacher_repeat_count": repeats,
        "engine_options": [
            {"name": "UCI_Variant", "value": "atomic"},
            {"name": "Threads", "value": 1},
            {"name": "Hash", "value": 512},
            {"name": "MultiPV", "value": multipv},
            {"name": "Ponder", "value": False},
            {"name": "SyzygyPath", "value": "<empty>"},
            {"name": "SyzygyProbeLimit", "value": 0},
            {"name": "Use NNUE", "value": "pure"},
        ],
        "probe_order_policy": "position-id-parity-alternating-v1",
    }


def _global_assignment(
    candidate: dict[str, object],
    *,
    partition: str = "CONF",
    component_label: str = "probe-component",
) -> dict[str, object]:
    identity = probe.split_components.validate_candidate(candidate)
    return {
        "schema": probe.GLOBAL_ASSIGNMENT_SCHEMA,
        "partition": partition,
        "component_id": _digest("component", component_label),
        "node_id": identity.position_id,
        "node_kind": "E00_CANDIDATE",
        "candidate": candidate,
        "inventory_entry": None,
    }


def _write_authenticated_split(
    tmp_path: Path,
    assignment: dict[str, object],
    *,
    receipt_mutator: object | None = None,
) -> tuple[Path, Path, str]:
    partition = str(assignment["partition"])
    source = tmp_path / f"{partition.lower()}.jsonl"
    common.write_new_jsonl(source, [assignment])
    payload = source.read_bytes()
    component_id = str(assignment["component_id"])
    node_id = str(assignment["node_id"])
    identity = probe.split_components.validate_candidate(
        assignment["candidate"]
    )
    output_paths = {
        current: (
            source
            if current == partition
            else tmp_path / f"{current.lower()}.jsonl"
        )
        for current in probe.split_components.PARTITIONS
    }
    outputs = {
        current: {
            "path": str(output_paths[current].absolute()),
            "sha256": (
                common.sha256_bytes(payload)
                if current == partition
                else common.sha256_bytes(b"")
            ),
            "size_bytes": len(payload) if current == partition else 0,
            "row_count": 1 if current == partition else 0,
        }
        for current in probe.split_components.PARTITIONS
    }
    nodes_by_partition = {
        current: [node_id] if current == partition else []
        for current in probe.split_components.PARTITIONS
    }
    components_by_partition = {
        current: [component_id] if current == partition else []
        for current in probe.split_components.PARTITIONS
    }
    receipt: dict[str, object] = {
        "schema": probe.split_components.RECEIPT_SCHEMA,
        "mode": "scientific",
        "seed": 7,
        "fractions": {"CAL": 0.2, "CONF": 0.2, "COHORT_ELIGIBLE": 0.6},
        "counts": {
            "input_positions": 1,
            "nodes": 1,
            "edges": 0,
            "components": 1,
            "nodes_by_partition": {
                current: len(nodes_by_partition[current])
                for current in probe.split_components.PARTITIONS
            },
            "components_by_partition": {
                current: len(components_by_partition[current])
                for current in probe.split_components.PARTITIONS
            },
        },
        "source_sha256s": [],
        "inventory_contracts": {},
        "nodes": [
            {
                "node_id": node_id,
                "node_kind": "E00_CANDIDATE",
                "component_id": component_id,
                "partition": partition,
            }
        ],
        "edges": [],
        "components": [
            {
                "component_id": component_id,
                "partition": partition,
                "node_ids": [node_id],
                "node_kinds": ["E00_CANDIDATE"],
                "source_game_ids": [identity.source_game_id],
                "root_fen_sha256s": [identity.root_fen_sha256],
                "transposition_keys": [identity.transposition_key],
            }
        ],
        "zero_overlap": {
            "all_nodes_assigned_once": True,
            "all_components_assigned_once": True,
            "node_overlap_count": 0,
            "component_overlap_count": 0,
            "node_ids_by_partition": nodes_by_partition,
            "component_ids_by_partition": components_by_partition,
        },
        "inputs": {},
        "outputs": outputs,
    }
    if receipt_mutator is not None:
        receipt_mutator(receipt)  # type: ignore[operator]
    receipt_path = tmp_path / "split-receipt.json"
    common.write_new_json(receipt_path, receipt)
    return source, receipt_path, common.sha256_file(receipt_path)


def _info(
    move: str,
    *,
    lane: int = 1,
    value: int = 25,
    kind: str = "cp",
) -> UciInfo:
    return UciInfo(
        depth=18,
        seldepth=27,
        nodes=128_000_000,
        multipv=lane,
        score=UciScore(kind=kind, value=value),  # type: ignore[arg-type]
        wdl=None,
        pv=(move, "h8g8"),
        raw=f"info multipv {lane} pv {move}",
    )


def _result(
    bestmove: str,
    *,
    lanes: tuple[tuple[str, int], ...],
    suffix: str,
) -> ProbeResult:
    infos = tuple(
        _info(move, lane=index + 1, value=value)
        for index, (move, value) in enumerate(lanes)
    )
    return ProbeResult(
        bestmove=bestmove,
        ponder=None,
        infos=infos,
        raw_lines=tuple(info.raw for info in infos)
        + (f"bestmove {bestmove} ; {suffix}",),
    )


class FakeEngine:
    def __init__(
        self,
        results: list[ProbeResult],
        inspections: dict[tuple[str, tuple[str, ...]], FenInspection],
        *,
        label: str = "engine",
        events: list[str] | None = None,
    ) -> None:
        self.results = list(results)
        self.inspections = inspections
        self.search_calls: list[dict[str, object]] = []
        self.inspect_calls: list[tuple[str, tuple[str, ...], float | None]] = []
        self.new_game_calls: list[float | None] = []
        self.label = label
        self.events = events

    def new_game(self, *, timeout: float | None = None) -> None:
        self.new_game_calls.append(timeout)
        if self.events is not None:
            self.events.append(f"{self.label}:new_game")

    def search(
        self,
        root_fen: str,
        *,
        nodes: int,
        moves: tuple[str, ...] = (),
        searchmoves: tuple[str, ...] = (),
        pre_search_options: tuple[UciOptionSetting, ...] = (),
        timeout: float | None = None,
    ) -> ProbeResult:
        if self.events is not None:
            self.events.append(f"{self.label}:search")
        self.search_calls.append(
            {
                "fen": root_fen,
                "nodes": nodes,
                "moves": tuple(moves),
                "searchmoves": tuple(searchmoves),
                "pre_search_options": tuple(pre_search_options),
                "timeout": timeout,
            }
        )
        if not self.results:
            raise AssertionError("unexpected search")
        return self.results.pop(0)

    def inspect_fen(
        self,
        root_fen: str,
        moves: tuple[str, ...] = (),
        *,
        timeout: float | None = None,
    ) -> FenInspection:
        key = (root_fen, tuple(moves))
        self.inspect_calls.append((root_fen, tuple(moves), timeout))
        if key not in self.inspections:
            raise AssertionError(f"unexpected inspection {key!r}")
        return self.inspections[key]


class FakeProcess:
    def __init__(self) -> None:
        self.returncode: int | None = None

    def poll(self) -> int | None:
        return self.returncode


class FakeThread:
    def is_alive(self) -> bool:
        return False


class ManagedFakeEngine(FakeEngine):
    """Lifecycle-authentic fake; it never starts a real engine process."""

    def __init__(
        self,
        *,
        role: str,
        engine_path: Path,
        network_path: Path,
        config: dict[str, object],
        launched: list[dict[str, object]],
    ) -> None:
        if role == "current":
            results = [
                _result(
                    "a1a2",
                    lanes=(("a1a2", 1),),
                    suffix="current",
                )
            ]
            inspections = {(QUIET_FEN, ()): _inspection(QUIET_FEN)}
        elif role == "teacher":
            teacher_result = _result(
                "a1a2",
                lanes=(("a1a2", 2), ("a1b1", 1)),
                suffix="teacher",
            )
            results = [
                teacher_result,
                teacher_result,
                _result(
                    "a1a2",
                    lanes=(("a1a2", 2),),
                    suffix="restricted",
                ),
            ]
            inspections = {
                (QUIET_FEN, ("a1a2",)): _inspection(QUIET_FEN)
            }
        else:
            raise AssertionError(f"unexpected role {role}")
        super().__init__(results, inspections, label=role)
        self.command = (str(engine_path),)
        self.initial_options = probe._engine_settings(config, network_path)
        self._closed = False
        self._process = FakeProcess()
        self._stdout_thread = FakeThread()
        self._stderr_thread = FakeThread()
        self._launched = launched
        self._launch_record = {
            "role": role,
            "engine_path": engine_path,
            "network_path": network_path,
            "engine_payload": engine_path.read_bytes(),
            "network_payload": network_path.read_bytes(),
        }

    def __enter__(self) -> ManagedFakeEngine:
        self._launched.append(self._launch_record)
        return self

    def __exit__(
        self,
        _exc_type: object,
        _exc: object,
        _traceback: object,
    ) -> bool:
        self._closed = True
        self._process.returncode = 0
        return False


def _inspection(fen: str, *checkers: str) -> FenInspection:
    return FenInspection(
        fen=fen,
        key="KEY",
        checkers=tuple(checkers),
        raw_lines=(f"Fen: {fen}",),
    )


def test_dual_probe_exact_wire_repeats_restricted_regret_and_atomic_features() -> None:
    candidate = _candidate()
    assignment = {
        "schema": "atomic-mining-assignment-v1",
        "split": "confirmation",
        "position_id": _hash("4"),
        "component_id": _hash("5"),
        "candidate": candidate,
    }
    events: list[str] = []
    current = FakeEngine(
        [
            _result(
                "h8g8",
                lanes=(("h8g8", 20), ("h8h7", 10), ("h8g7", 5)),
                suffix="current",
            )
        ],
        {
            (PREVIOUS_FEN, ("e5d6",)): _inspection(CURRENT_FEN, "d6"),
            (PREVIOUS_FEN, ()): _inspection(PREVIOUS_FEN),
        },
        label="current",
        events=events,
    )
    teacher = FakeEngine(
        [
            _result(
                "a2b1q",
                lanes=(("a2b1q", 300),),
                suffix="teacher-1",
            ),
            _result(
                "a2b1q",
                lanes=(("a2b1q", 295),),
                suffix="teacher-2",
            ),
            _result(
                "h8g8",
                lanes=(("h8g8", -80),),
                suffix="teacher-restricted",
            ),
        ],
        {
            (CURRENT_FEN, ("a2b1q",)): _inspection(
                TEACHER_CHILD_FEN, "b1"
            ),
        },
        label="teacher",
        events=events,
    )

    row = probe.probe_position(
        assignment,
        current,
        teacher,
        probe_config=_config(),
        timeout=3.5,
    )

    assert set(row) == {
        "schema",
        "split",
        "position_id",
        "component_id",
        "candidate",
        "probe_config",
        "features",
        "current",
        "teacher_repeats",
        "teacher_on_current_move",
    }
    assert row["schema"] == "atomic-dual-probe-v1"
    assert row["split"] == "confirmation"
    assert row["position_id"] == _hash("4")
    assert row["component_id"] == _hash("5")
    assert row["candidate"] is candidate
    assert row["probe_config"] == _config()
    assert row["features"] == {
        "classifier": "atomic-tactical-strata-v1",
        "in_check": True,
        "post_capture": True,
        "post_capture_in_check": True,
        "teacher_move_capture": True,
        "teacher_move_gives_check": True,
        "teacher_move_promotion": True,
        "forced_chain": True,
        "primary_stratum": "post_capture_in_check",
        "strata": [
            "post_capture_in_check",
            "in_check",
            "post_capture",
            "teacher_move_gives_check",
            "teacher_move_capture",
            "teacher_move_promotion",
            "forced_chain",
        ],
    }

    current_snapshot = row["current"]
    assert current_snapshot["bestmove"] == "h8g8"
    assert current_snapshot["principal"] == current_snapshot["multipv"][0]
    assert current_snapshot["principal"] == {
        "depth": 18,
        "seldepth": 27,
        "nodes": 128_000_000,
        "multipv": 1,
        "score": {"kind": "cp", "value": 20, "bound": "exact"},
        "wdl": None,
        "pv": ["h8g8", "h8g8"],
    }
    expected_raw = (
        "\n".join(
            [
                "info multipv 1 pv h8g8",
                "info multipv 2 pv h8h7",
                "info multipv 3 pv h8g7",
                "bestmove h8g8 ; current",
            ]
        )
        + "\n"
    ).encode("utf-8")
    assert current_snapshot["raw_sha256"] == hashlib.sha256(
        expected_raw
    ).hexdigest()
    assert current_snapshot["raw_line_count"] == 4
    assert len(row["teacher_repeats"]) == 2
    assert row["teacher_on_current_move"]["bestmove"] == "h8g8"

    all_searches = current.search_calls + teacher.search_calls
    assert len(all_searches) == 4
    assert all(call["nodes"] == 128_000_000 for call in all_searches)
    assert all(call["timeout"] == 3.5 for call in all_searches)
    assert all(
        call["pre_search_options"] == (UciOptionSetting("Clear Hash"),)
        for call in all_searches
    )
    assert teacher.search_calls[-1]["searchmoves"] == ("h8g8",)
    assert events == [
        "current:new_game",
        "current:search",
        "teacher:new_game",
        "teacher:search",
        "teacher:new_game",
        "teacher:search",
        "teacher:new_game",
        "teacher:search",
    ]


def test_raw_candidate_is_technical_smoke_and_agreement_still_probes_regret() -> None:
    candidate = _candidate(
        root_fen=QUIET_FEN,
        fen=QUIET_FEN,
        history=[],
        checkers=[],
    )
    current = FakeEngine(
        [_result("a1a2", lanes=(("a1a2", 4),), suffix="current")],
        {(QUIET_FEN, ()): _inspection(QUIET_FEN)},
    )
    teacher_result = _result(
        "a1a2",
        lanes=(("a1a2", 5), ("a1b1", 3)),
        suffix="teacher",
    )
    teacher = FakeEngine(
        [
            teacher_result,
            teacher_result,
            _result("a1a2", lanes=(("a1a2", 5),), suffix="restricted"),
        ],
        {(QUIET_FEN, ("a1a2",)): _inspection(QUIET_FEN)},
    )

    row = probe.probe_position(
        candidate,
        current,
        teacher,
        probe_config=_config(),
    )

    assert row["split"] == "technical-smoke"
    assert row["component_id"] == candidate["game"]["source_game_id"]
    assert row["position_id"] == common.derive_id(
        "atomic-dual-probe-position-v1",
        candidate["position"]["full_history_sha256"],
        QUIET_FEN,
    )
    assert row["teacher_on_current_move"]["bestmove"] == "a1a2"
    assert row["features"]["primary_stratum"] == "quiet"
    assert row["features"]["strata"] == ["quiet"]
    assert all(
        row["features"][field] is False
        for field in (
            "in_check",
            "post_capture",
            "post_capture_in_check",
            "teacher_move_capture",
            "teacher_move_gives_check",
            "teacher_move_promotion",
            "forced_chain",
        )
    )
    assert len(teacher.search_calls) == 3
    assert teacher.search_calls[-1]["searchmoves"] == ("a1a2",)


@pytest.mark.parametrize(
    "mutator, message",
    (
        (
            lambda row: row.update({"extra": True}),
            "fields differ",
        ),
        (
            lambda row: row.update({"split": "training"}),
            "calibration.*confirmation",
        ),
        (
            lambda row: row.update({"position_id": "not-a-hash"}),
            "lowercase SHA-256",
        ),
            (
                lambda row: row["candidate"]["position"].update({"root_ply": 9}),
                "root_ply must be below game.move_count",
            ),
            (
                lambda row: row["candidate"]["position"].update(
                    {"legal_move_count": 0}
                ),
                "legal_move_count must be >= 1",
            ),
            (
                lambda row: row["candidate"]["position"].update(
                    {"legal_move_count": True}
                ),
                "legal_move_count must be an integer",
            ),
        (
            lambda row: row["candidate"]["position"].update(
                {"terminal_state": "no-legal-moves"}
            ),
            "terminal_state must be nonterminal",
        ),
    ),
)
def test_assignment_and_candidate_fail_closed(
    mutator: object, message: str
) -> None:
    assignment = {
        "schema": "atomic-mining-assignment-v1",
        "split": "calibration",
        "position_id": _hash("4"),
        "component_id": _hash("5"),
        "candidate": _candidate(),
    }
    mutator(assignment)  # type: ignore[operator]
    with pytest.raises(probe.ProbeError, match=message):
        probe.probe_position(
            assignment,
            FakeEngine([], {}),
            FakeEngine([], {}),
            probe_config=_config(),
        )


@pytest.mark.parametrize(
    "field, value, message",
    (
        ("threads", 2, "exactly 1"),
        ("clear_hash", False, "exactly true"),
        ("teacher_repeat_count", 1, "at least 2"),
        ("multipv", 1, "at least 2"),
        ("nodes", 0, "positive"),
    ),
)
def test_probe_config_is_strict(
    field: str, value: object, message: str
) -> None:
    config = _config()
    config[field] = value
    with pytest.raises(probe.ProbeError, match=message):
        probe.validate_probe_config(config)


def test_exact_engine_options_exclude_showwdl_and_bind_evalfile(
    tmp_path: Path,
) -> None:
    config = _config()
    network = tmp_path / "current v3.nnue"
    network.write_bytes(b"net")
    settings = probe._engine_settings(config, network)

    assert [(setting.name, setting.value) for setting in settings] == [
        ("UCI_Variant", "atomic"),
        ("Threads", 1),
        ("Hash", 512),
        ("MultiPV", 3),
        ("Ponder", False),
        ("SyzygyPath", "<empty>"),
        ("SyzygyProbeLimit", 0),
        ("Use NNUE", "pure"),
        ("EvalFile", str(network.resolve())),
    ]
    assert all(setting.name != "UCI_ShowWDL" for setting in settings)

    drifted = _config()
    drifted["engine_options"] = list(drifted["engine_options"])  # type: ignore[arg-type]
    drifted["engine_options"][2] = {"name": "Hash", "value": 256}  # type: ignore[index]
    with pytest.raises(probe.ProbeError, match="exact ordered"):
        probe.validate_probe_config(drifted)

    drifted_policy = _config()
    drifted_policy["probe_order_policy"] = "current-first"
    with pytest.raises(probe.ProbeError, match="position-id-parity"):
        probe.validate_probe_config(drifted_policy)


def test_odd_position_id_runs_first_teacher_before_current() -> None:
    candidate = _candidate(
        root_fen=QUIET_FEN,
        fen=QUIET_FEN,
        history=[],
        checkers=[],
    )
    assignment = {
        "schema": "atomic-mining-assignment-v1",
        "split": "calibration",
        "position_id": _hash("5"),
        "component_id": _hash("6"),
        "candidate": candidate,
    }
    events: list[str] = []
    current = FakeEngine(
        [_result("a1a2", lanes=(("a1a2", 1),), suffix="current")],
        {(QUIET_FEN, ()): _inspection(QUIET_FEN)},
        label="current",
        events=events,
    )
    teacher_result = _result(
        "a1a2",
        lanes=(("a1a2", 2), ("a1b1", 1)),
        suffix="teacher",
    )
    teacher = FakeEngine(
        [
            teacher_result,
            teacher_result,
            _result("a1a2", lanes=(("a1a2", 2),), suffix="restricted"),
        ],
        {(QUIET_FEN, ("a1a2",)): _inspection(QUIET_FEN)},
        label="teacher",
        events=events,
    )

    row = probe.probe_position(
        assignment,
        current,
        teacher,
        probe_config=_config(),
    )

    assert row["probe_config"]["probe_order_policy"] == (
        "position-id-parity-alternating-v1"
    )
    assert events == [
        "teacher:new_game",
        "teacher:search",
        "current:new_game",
        "current:search",
        "teacher:new_game",
        "teacher:search",
        "teacher:new_game",
        "teacher:search",
    ]


def test_replay_drift_and_incomplete_search_abort() -> None:
    candidate = _candidate()
    current = FakeEngine(
        [_result("h8g8", lanes=(("h8g8", 1),), suffix="current")],
        {
            (PREVIOUS_FEN, ("e5d6",)): _inspection(CURRENT_FEN),
            (PREVIOUS_FEN, ()): _inspection(PREVIOUS_FEN),
        },
    )
    teacher_result = _result(
        "a2b1q", lanes=(("a2b1q", 2),), suffix="teacher"
    )
    teacher = FakeEngine(
        [teacher_result, teacher_result],
        {(CURRENT_FEN, ("a2b1q",)): _inspection(TEACHER_CHILD_FEN)},
    )
    with pytest.raises(probe.ProbeError, match="checkers differ"):
        probe.probe_position(
            candidate,
            current,
            teacher,
            probe_config=_config(),
        )

    incomplete = ProbeResult(
        bestmove=None,
        ponder=None,
        infos=(),
        raw_lines=("bestmove (none)",),
    )
    quiet = _candidate(
        root_fen=QUIET_FEN,
        fen=QUIET_FEN,
        history=[],
        checkers=[],
    )
    quiet_assignment = {
        "schema": "atomic-mining-assignment-v1",
        "split": "calibration",
        "position_id": _hash("4"),
        "component_id": _hash("5"),
        "candidate": quiet,
    }
    with pytest.raises(probe.ProbeError, match="best move"):
        probe.probe_position(
            quiet_assignment,
            FakeEngine([incomplete], {}),
            FakeEngine([], {}),
            probe_config=_config(),
        )


def test_probe_file_is_canonical_and_refuses_overwrite_before_engine_use(
    tmp_path: Path,
) -> None:
    candidate = _candidate(
        root_fen=QUIET_FEN,
        fen=QUIET_FEN,
        history=[],
        checkers=[],
    )
    assignment = _global_assignment(candidate)
    source, split_receipt, split_receipt_sha256 = (
        _write_authenticated_split(tmp_path, assignment)
    )
    output = tmp_path / "output.jsonl"

    current = FakeEngine(
        [_result("a1a2", lanes=(("a1a2", 1),), suffix="current")],
        {(QUIET_FEN, ()): _inspection(QUIET_FEN)},
    )
    teacher_result = _result(
        "a1a2",
        lanes=(("a1a2", 2), ("a1b1", 1)),
        suffix="teacher",
    )
    teacher = FakeEngine(
        [
            teacher_result,
            teacher_result,
            _result("a1a2", lanes=(("a1a2", 2),), suffix="restricted"),
        ],
        {(QUIET_FEN, ("a1a2",)): _inspection(QUIET_FEN)},
    )
    summary = probe.probe_file(
        source,
        split_receipt,
        split_receipt_sha256,
        output,
        current,
        teacher,
        probe_config=_config(),
    )
    assert summary == probe.ProbeSummary(1, 1, 2, 1)
    rows = common.load_jsonl(output)
    assert len(rows) == 1
    assert rows[0]["schema"] == "atomic-dual-probe-v1"

    with pytest.raises(FileExistsError, match="refusing to overwrite"):
        probe.probe_file(
            source,
            split_receipt,
            split_receipt_sha256,
            output,
            FakeEngine([], {}),
            FakeEngine([], {}),
            probe_config=_config(),
        )


def test_global_probe_requires_exact_authenticated_membership(
    tmp_path: Path,
) -> None:
    candidate = _candidate(
        root_fen=QUIET_FEN,
        fen=QUIET_FEN,
        history=[],
        checkers=[],
    )
    assignment = _global_assignment(candidate)
    with pytest.raises(probe.ProbeError, match="authenticated split receipt"):
        probe.probe_position(
            assignment,
            FakeEngine([], {}),
            FakeEngine([], {}),
            probe_config=_config(),
        )

    source, receipt, receipt_sha256 = _write_authenticated_split(
        tmp_path,
        assignment,
    )
    authenticated = probe.authenticate_split_artifact(
        source,
        receipt,
        receipt_sha256,
    )
    assert authenticated.partition == "CONF"
    assert authenticated.row_count == 1
    assert authenticated.rows == (assignment,)
    mutated = copy.deepcopy(assignment)
    mutated["component_id"] = _hash("9")
    with pytest.raises(probe.ProbeError, match="authenticated split receipt"):
        probe.probe_position(
            mutated,
            FakeEngine([], {}),
            FakeEngine([], {}),
            probe_config=_config(),
        )


@pytest.mark.parametrize(
    "mutation, message",
    (
        ("component_id", "artifact SHA-256"),
        ("partition", "artifact SHA-256"),
        ("node_kind", "artifact SHA-256"),
        ("candidate", "artifact SHA-256"),
        ("extra", "artifact SHA-256"),
        ("missing", "at least one row"),
    ),
)
def test_split_artifact_mutation_fails_before_engine_use(
    tmp_path: Path,
    mutation: str,
    message: str,
) -> None:
    candidate = _candidate(
        root_fen=QUIET_FEN,
        fen=QUIET_FEN,
        history=[],
        checkers=[],
    )
    assignment = _global_assignment(candidate)
    source, receipt, receipt_sha256 = _write_authenticated_split(
        tmp_path,
        assignment,
    )
    mutated = copy.deepcopy(assignment)
    if mutation == "component_id":
        mutated["component_id"] = _hash("9")
        rows = [mutated]
    elif mutation == "partition":
        mutated["partition"] = "CAL"
        rows = [mutated]
    elif mutation == "node_kind":
        mutated["node_kind"] = "REPLAY"
        rows = [mutated]
    elif mutation == "candidate":
        mutated["candidate"]["position"]["engine_key"] = "mutated"  # type: ignore[index]
        rows = [mutated]
    elif mutation == "extra":
        rows = [assignment, mutated]
    else:
        rows = []
    source.write_bytes(
        b"".join(common.canonical_json_bytes(row) for row in rows)
    )
    with pytest.raises(probe.ProbeError, match=message):
        probe.probe_file(
            source,
            receipt,
            receipt_sha256,
            tmp_path / "out.jsonl",
            FakeEngine([], {}),
            FakeEngine([], {}),
            probe_config=_config(),
        )


def test_split_receipt_trust_anchor_and_exact_manifest_fail_closed(
    tmp_path: Path,
) -> None:
    assignment = _global_assignment(
        _candidate(
            root_fen=QUIET_FEN,
            fen=QUIET_FEN,
            history=[],
            checkers=[],
        )
    )
    source, receipt, receipt_sha256 = _write_authenticated_split(
        tmp_path,
        assignment,
    )
    document = json.loads(receipt.read_text(encoding="utf-8"))
    document["seed"] = 8
    receipt.write_bytes(common.canonical_json_bytes(document))
    with pytest.raises(probe.ProbeError, match="caller trust anchor"):
        probe.authenticate_split_artifact(
            source,
            receipt,
            receipt_sha256,
        )

    receipt.unlink()
    document["nodes"][0]["component_id"] = _hash("8")
    receipt.write_bytes(common.canonical_json_bytes(document))
    with pytest.raises(probe.ProbeError, match="node manifest"):
        probe.authenticate_split_artifact(
            source,
            receipt,
            common.sha256_file(receipt),
        )


def test_split_receipt_must_be_canonical(
    tmp_path: Path,
) -> None:
    assignment = _global_assignment(
        _candidate(
            root_fen=QUIET_FEN,
            fen=QUIET_FEN,
            history=[],
            checkers=[],
        )
    )
    source, receipt, _receipt_sha256 = _write_authenticated_split(
        tmp_path,
        assignment,
    )
    document = json.loads(receipt.read_text(encoding="utf-8"))
    receipt.write_text(
        json.dumps(document, indent=2),
        encoding="utf-8",
    )
    with pytest.raises(probe.ProbeError, match="canonical UTF-8 JSON"):
        probe.authenticate_split_artifact(
            source,
            receipt,
            common.sha256_file(receipt),
        )


def test_split_receipt_requires_scientific_mode_and_binds_manifest_size_count(
    tmp_path: Path,
) -> None:
    assignment = _global_assignment(
        _candidate(
            root_fen=QUIET_FEN,
            fen=QUIET_FEN,
            history=[],
            checkers=[],
        )
    )
    source, receipt, _receipt_sha256 = _write_authenticated_split(
        tmp_path,
        assignment,
    )
    document = json.loads(receipt.read_text(encoding="utf-8"))
    document["mode"] = "technical-smoke"
    receipt.write_bytes(common.canonical_json_bytes(document))
    with pytest.raises(probe.ProbeError, match="mode must be scientific"):
        probe.authenticate_split_artifact(
            source,
            receipt,
            common.sha256_file(receipt),
        )

    document["mode"] = "scientific"
    document["components"][0]["transposition_keys"] = [_hash("9")]
    receipt.write_bytes(common.canonical_json_bytes(document))
    with pytest.raises(probe.ProbeError, match="component manifest"):
        probe.authenticate_split_artifact(
            source,
            receipt,
            common.sha256_file(receipt),
        )

    identity = probe.split_components.validate_candidate(
        assignment["candidate"]
    )
    document["components"][0]["transposition_keys"] = [
        identity.transposition_key
    ]
    receipt.unlink()
    document["outputs"]["CONF"]["row_count"] = 2
    receipt.write_bytes(common.canonical_json_bytes(document))
    with pytest.raises(probe.ProbeError, match="row count"):
        probe.authenticate_split_artifact(
            source,
            receipt,
            common.sha256_file(receipt),
        )

    receipt.unlink()
    document["outputs"]["CONF"]["row_count"] = 1
    document["outputs"]["CONF"]["size_bytes"] += 1
    receipt.write_bytes(common.canonical_json_bytes(document))
    with pytest.raises(probe.ProbeError, match="artifact size"):
        probe.authenticate_split_artifact(
            source,
            receipt,
            common.sha256_file(receipt),
        )


def _cli_inputs(
    tmp_path: Path,
) -> tuple[Path, Path, str, dict[str, Path], dict[str, bytes]]:
    tmp_path.mkdir(parents=True, exist_ok=True)
    assignment = _global_assignment(
        _candidate(
            root_fen=QUIET_FEN,
            fen=QUIET_FEN,
            history=[],
            checkers=[],
        )
    )
    source, split_receipt, split_receipt_sha256 = (
        _write_authenticated_split(tmp_path, assignment)
    )
    payloads = {
        "engine": b"authenticated fake atomic engine\n",
        "current_net": b"authenticated current v3 network\n",
        "teacher_net": b"authenticated run3b teacher network\n",
    }
    paths = {
        "engine": tmp_path / "atomic-engine.exe",
        "current_net": tmp_path / "current.nnue",
        "teacher_net": tmp_path / "teacher.nnue",
    }
    for role, path in paths.items():
        path.write_bytes(payloads[role])
    return (
        source,
        split_receipt,
        split_receipt_sha256,
        paths,
        payloads,
    )


def _run_fake_cli(
    tmp_path: Path,
    *,
    before_launch_hook: object | None = None,
    after_probe_hook: object | None = None,
    factory_override: object | None = None,
) -> tuple[probe.ProbeSummary, list[dict[str, object]], Path, Path]:
    (
        source,
        split_receipt,
        split_receipt_sha256,
        paths,
        payloads,
    ) = _cli_inputs(tmp_path)
    launched: list[dict[str, object]] = []

    def factory(
        role: str,
        engine_path: Path,
        network_path: Path,
        config: dict[str, object],
        _timeout: float | None,
    ) -> ManagedFakeEngine:
        return ManagedFakeEngine(
            role=role,
            engine_path=engine_path,
            network_path=network_path,
            config=config,
            launched=launched,
        )

    selected_factory = factory_override or factory
    output = tmp_path / "probes.jsonl"
    receipt = tmp_path / "probes.receipt.json"
    summary = probe._run_cli_probe(
        input_path=source,
        split_receipt_path=split_receipt,
        expected_split_receipt_sha256=split_receipt_sha256,
        output_path=output,
        execution_receipt_path=receipt,
        engine_path=paths["engine"],
        expected_engine_sha256=common.sha256_bytes(payloads["engine"]),
        current_net_path=paths["current_net"],
        expected_current_net_sha256=common.sha256_bytes(
            payloads["current_net"]
        ),
        teacher_net_path=paths["teacher_net"],
        expected_teacher_net_sha256=common.sha256_bytes(
            payloads["teacher_net"]
        ),
        nodes=128_000_000,
        multipv=3,
        hash_mb=512,
        teacher_repeats=2,
        timeout=3.0,
        engine_context_factory=selected_factory,  # type: ignore[arg-type]
        snapshot_parent=tmp_path,
        before_launch_hook=before_launch_hook,  # type: ignore[arg-type]
        after_probe_hook=after_probe_hook,  # type: ignore[arg-type]
    )
    return summary, launched, output, receipt


def test_cli_executes_engine_and_evalfiles_only_from_authenticated_snapshots(
    tmp_path: Path,
) -> None:
    moved: dict[str, tuple[Path, Path]] = {}

    def replace_originals(bundle: probe.ProbeSnapshotBundle) -> None:
        for artifact in bundle.artifacts:
            moved_path = tmp_path / f"{artifact.role}.trusted"
            replacement = tmp_path / f"{artifact.role}.attacker"
            replacement.write_bytes(
                f"attacker controlled {artifact.role}\n".encode("ascii")
            )
            os.replace(artifact.original.path, moved_path)
            os.replace(replacement, artifact.original.path)
            moved[artifact.role] = (artifact.original.path, moved_path)

    def restore_originals(_bundle: probe.ProbeSnapshotBundle) -> None:
        for original, moved_path in moved.values():
            original.unlink()
            os.replace(moved_path, original)

    summary, launched, output, receipt_path = _run_fake_cli(
        tmp_path,
        before_launch_hook=replace_originals,
        after_probe_hook=restore_originals,
    )

    assert summary == probe.ProbeSummary(1, 1, 2, 1)
    assert [record["role"] for record in launched] == [
        "current",
        "teacher",
    ]
    assert all(
        "atomic-dual-probe-snapshot-" in str(record["engine_path"])
        for record in launched
    )
    assert launched[0]["engine_payload"] == (
        b"authenticated fake atomic engine\n"
    )
    assert launched[0]["network_payload"] == (
        b"authenticated current v3 network\n"
    )
    assert launched[1]["network_payload"] == (
        b"authenticated run3b teacher network\n"
    )
    rows = common.load_jsonl(output)
    assert len(rows) == 1
    for role in ("engine", "current_net", "teacher_net"):
        config_path = Path(rows[0]["probe_config"][role]["path"])  # type: ignore[index]
        assert "atomic-dual-probe-snapshot-" in str(config_path)
        assert not config_path.exists()
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    assert receipt["schema"] == probe.EXECUTION_RECEIPT_SCHEMA
    assert receipt["snapshot_policy"] == probe.SNAPSHOT_POLICY
    assert receipt["execution"]["engines_clean_closed"] is True
    assert receipt["execution"]["snapshots_removed_after_clean_close"] is True
    assert receipt["execution"]["directory_guard"][
        "delete_share_denied"
    ] is True
    assert not Path(receipt["execution"]["snapshot_directory"]).exists()
    for role in ("engine", "current_net", "teacher_net"):
        provenance = receipt["artifacts"][role]
        assert provenance["original"]["path"] == str(moved[role][0])
        assert provenance["snapshot"]["path"] == (
            provenance["executed"]["path"]
        )
        assert provenance["executed"]["same_as_snapshot"] is True
        assert provenance["snapshot"]["read_only_applied"] is True
        assert provenance["guard"]["write_share_denied"] is True
        assert provenance["guard"]["delete_share_denied"] is True
        assert provenance["guard"][
            "held_from_post_create_through_clean_close"
        ] is True


@pytest.mark.skipif(os.name != "nt", reason="Windows no-share contract")
def test_live_guards_block_write_delete_rename_and_replace_for_all_snapshots(
    tmp_path: Path,
) -> None:
    blocked: list[tuple[str, str]] = []

    def assert_blocked(role: str, operation: str, action: object) -> None:
        try:
            action()  # type: ignore[operator]
        except OSError:
            blocked.append((role, operation))
            return
        raise AssertionError(f"{role} {operation} unexpectedly succeeded")

    def attack(bundle: probe.ProbeSnapshotBundle) -> None:
        for artifact in bundle.artifacts:
            path = artifact.snapshot.path
            os.chmod(path, stat.S_IREAD | stat.S_IWRITE)
            replacement = tmp_path / f"{artifact.role}.replacement"
            replacement.write_bytes(b"transient attacker payload\n")
            renamed = tmp_path / f"{artifact.role}.renamed"
            assert_blocked(
                artifact.role,
                "write",
                lambda path=path: path.write_bytes(b"attacker write\n"),
            )
            assert_blocked(
                artifact.role,
                "delete",
                lambda path=path: path.unlink(),
            )
            assert_blocked(
                artifact.role,
                "rename",
                lambda path=path, renamed=renamed: os.replace(path, renamed),
            )
            assert_blocked(
                artifact.role,
                "replace",
                lambda path=path, replacement=replacement: os.replace(
                    replacement, path
                ),
            )
            os.chmod(path, artifact.snapshot_mode)
        moved_directory = tmp_path / "moved-snapshot-directory"
        assert_blocked(
            "snapshot_directory",
            "rename",
            lambda: os.replace(bundle.directory, moved_directory),
        )

    summary, _launched, output, receipt = _run_fake_cli(
        tmp_path,
        before_launch_hook=attack,
    )
    assert summary.positions == 1
    assert output.exists() and receipt.exists()
    expected_blocked = [
        (role, operation)
        for role in ("engine", "current_net", "teacher_net")
        for operation in ("write", "delete", "rename", "replace")
    ]
    expected_blocked.append(("snapshot_directory", "rename"))
    assert sorted(blocked) == sorted(expected_blocked)


@pytest.mark.skipif(os.name != "nt", reason="Windows no-share contract")
def test_simulated_guard_evasion_aborts_and_retains_evidence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[probe.SnapshotGuardSet] = []
    retained: list[Path] = []
    real_acquire = probe._acquire_snapshot_guards

    def capture_guards(
        bundle: probe.ProbeSnapshotBundle,
    ) -> probe.SnapshotGuardSet:
        guards = real_acquire(bundle)
        captured.append(guards)
        return guards

    monkeypatch.setattr(
        probe, "_acquire_snapshot_guards", capture_guards
    )

    def evade(bundle: probe.ProbeSnapshotBundle) -> None:
        retained.append(bundle.directory)
        probe._close_snapshot_guards(captured[0])
        artifact = bundle.artifact("teacher_net")
        payload = artifact.snapshot.path.read_bytes()
        os.chmod(
            artifact.snapshot.path,
            stat.S_IREAD | stat.S_IWRITE,
        )
        artifact.snapshot.path.write_bytes(b"transient attacker bytes\n")
        artifact.snapshot.path.write_bytes(payload)
        os.chmod(artifact.snapshot.path, artifact.snapshot_mode)

    with pytest.raises(
        probe.ProbeError,
        match="snapshot finalization was not proven|finalization was not proven",
    ):
        _run_fake_cli(tmp_path, before_launch_hook=evade)
    assert retained and retained[0].exists()
    assert not (tmp_path / "probes.jsonl").exists()
    assert not (tmp_path / "probes.receipt.json").exists()


@pytest.mark.parametrize("phase", ("before", "during"))
def test_cli_snapshot_mutation_is_detected_and_publishes_nothing(
    tmp_path: Path,
    phase: str,
) -> None:
    retained: list[Path] = []

    def mutate(bundle: probe.ProbeSnapshotBundle) -> None:
        target = bundle.artifact("current_net").snapshot.path
        retained.append(bundle.directory)
        os.chmod(target, stat.S_IREAD | stat.S_IWRITE)
        target.write_bytes(b"mutated snapshot bytes\n")

    kwargs = (
        {"before_launch_hook": mutate}
        if phase == "before"
        else {"after_probe_hook": mutate}
    )
    with pytest.raises(
        probe.ProbeError,
        match="snapshot finalization was not proven|finalization was not proven",
    ):
        _run_fake_cli(tmp_path, **kwargs)
    assert retained and retained[0].exists()
    assert not (tmp_path / "probes.jsonl").exists()
    assert not (tmp_path / "probes.receipt.json").exists()


def test_cli_reparse_and_wrong_command_fail_before_engine_use(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (
        source,
        split_receipt,
        split_receipt_sha256,
        paths,
        payloads,
    ) = _cli_inputs(tmp_path)
    engine_identity = paths["engine"].lstat().st_ino
    real_detector = probe._is_link_or_reparse

    def synthetic_reparse(metadata: os.stat_result) -> bool:
        return metadata.st_ino == engine_identity or real_detector(metadata)

    monkeypatch.setattr(probe, "_is_link_or_reparse", synthetic_reparse)
    factory_calls: list[str] = []
    with pytest.raises(probe.ProbeError, match="non-reparse regular"):
        probe._run_cli_probe(
            input_path=source,
            split_receipt_path=split_receipt,
            expected_split_receipt_sha256=split_receipt_sha256,
            output_path=tmp_path / "reparse.jsonl",
            execution_receipt_path=tmp_path / "reparse.receipt.json",
            engine_path=paths["engine"],
            expected_engine_sha256=common.sha256_bytes(payloads["engine"]),
            current_net_path=paths["current_net"],
            expected_current_net_sha256=common.sha256_bytes(
                payloads["current_net"]
            ),
            teacher_net_path=paths["teacher_net"],
            expected_teacher_net_sha256=common.sha256_bytes(
                payloads["teacher_net"]
            ),
            nodes=1,
            multipv=2,
            hash_mb=16,
            teacher_repeats=2,
            timeout=1.0,
            engine_context_factory=lambda *_args: (
                factory_calls.append("called")
            ),  # type: ignore[arg-type]
            snapshot_parent=tmp_path,
        )
    assert factory_calls == []
    monkeypatch.setattr(probe, "_is_link_or_reparse", real_detector)

    def unsafe_factory(
        role: str,
        _engine_snapshot: Path,
        network_snapshot: Path,
        config: dict[str, object],
        _timeout: float | None,
    ) -> ManagedFakeEngine:
        return ManagedFakeEngine(
            role=role,
            engine_path=paths["engine"],
            network_path=network_snapshot,
            config=config,
            launched=[],
        )

    with pytest.raises(
        probe.ProbeError,
        match="command does not target exactly the engine snapshot",
    ):
        _run_fake_cli(
            tmp_path / "wrong-command",
            factory_override=unsafe_factory,
        )
    assert not (tmp_path / "wrong-command" / "probes.jsonl").exists()
    assert not (
        tmp_path / "wrong-command" / "probes.receipt.json"
    ).exists()


def test_cli_cleanup_failure_retains_snapshots_and_publishes_nothing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    retained: list[probe.ProbeSnapshotBundle] = []
    real_remove = probe._remove_snapshot_bundle

    def fail_cleanup(bundle: probe.ProbeSnapshotBundle) -> None:
        retained.append(bundle)
        raise probe.ProbeError("synthetic snapshot cleanup failure")

    monkeypatch.setattr(probe, "_remove_snapshot_bundle", fail_cleanup)
    with pytest.raises(
        probe.ProbeError,
        match="snapshot finalization was not proven",
    ):
        _run_fake_cli(tmp_path)
    assert retained and retained[0].directory.exists()
    assert not (tmp_path / "probes.jsonl").exists()
    assert not (tmp_path / "probes.receipt.json").exists()
    monkeypatch.setattr(probe, "_remove_snapshot_bundle", real_remove)
    real_remove(retained[0])
