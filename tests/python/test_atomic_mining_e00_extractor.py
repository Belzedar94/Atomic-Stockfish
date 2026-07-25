from __future__ import annotations

import copy
from dataclasses import dataclass
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
from tools.atomic_mining import extract_e00_positions as extractor


ROOT = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"
MOVES = ("f2f3", "e7e5", "g2g4", "d8h4")
SCHEDULE_BYTES = b"authenticated synthetic E00 schedule\n"
SCHEDULE_RECEIPT_BYTES = b"authenticated synthetic E00 schedule receipt\n"
ENGINE_BYTES = b"authenticated synthetic E00 engine\n"
CURRENT_BYTES = b"authenticated synthetic current network\n"
TEACHER_BYTES = b"authenticated synthetic teacher network\n"
VARIANT_CONFIG_BYTES = b"authenticated synthetic variants configuration\n"
RUNNER_BYTES = b"authenticated synthetic E00 runner\n"
BOOK_BYTES = b"authenticated synthetic E00 opening book\n"
SCHEDULE_SHA = common.sha256_bytes(SCHEDULE_BYTES)
PAIR_ID = "2" * 64
ENGINE_SHA = common.sha256_bytes(ENGINE_BYTES)
CURRENT_SHA = common.sha256_bytes(CURRENT_BYTES)
TEACHER_SHA = common.sha256_bytes(TEACHER_BYTES)
BOOK_SHA = common.sha256_bytes(BOOK_BYTES)


@dataclass(frozen=True)
class FakeInspection:
    fen: str
    key: str | None
    checkers: tuple[str, ...] = ()


class FakeReplayEngine:
    def __init__(
        self,
        *,
        illegal_ply: int | None = None,
        mismatch_checkers_ply: int | None = None,
    ) -> None:
        self.illegal_ply = illegal_ply
        self.mismatch_checkers_ply = mismatch_checkers_ply
        self.inspect_calls: list[tuple[str, tuple[str, ...], float | None]] = []
        self.legal_calls: list[tuple[str, float | None]] = []
        self.states = [ROOT]
        for _move in MOVES:
            self.states.append(self._advance(self.states[-1]))
        self.state_index = {fen: index for index, fen in enumerate(self.states)}

    @staticmethod
    def _advance(fen: str) -> str:
        fields = fen.split()
        before = fields[1]
        fields[1] = "b" if before == "w" else "w"
        fields[4] = str(int(fields[4]) + 1)
        if before == "b":
            fields[5] = str(int(fields[5]) + 1)
        return " ".join(fields)

    def _inspection(
        self, fen: str, *, checker_mismatch: bool = False
    ) -> FakeInspection:
        key = hashlib.sha256(fen.encode()).hexdigest()[:16]
        return FakeInspection(
            fen=fen,
            key=key,
            checkers=("h8",) if checker_mismatch else (),
        )

    def inspect_fen(
        self,
        root_fen: str,
        moves: tuple[str, ...] = (),
        *,
        timeout: float | None = None,
    ) -> FakeInspection:
        requested = tuple(moves)
        self.inspect_calls.append((root_fen, requested, timeout))
        if root_fen not in self.state_index:
            raise RuntimeError("unknown synthetic FEN")
        index = self.state_index[root_fen]
        for move in requested:
            if index >= len(MOVES) or move != MOVES[index]:
                raise RuntimeError("unknown synthetic trajectory")
            index += 1
        mismatch = (
            self.mismatch_checkers_ply is not None
            and root_fen == ROOT
            and len(requested) == self.mismatch_checkers_ply + 1
            and len(requested) > 1
        )
        return self._inspection(self.states[index], checker_mismatch=mismatch)

    def legal_moves(
        self,
        root_fen: str,
        moves: tuple[str, ...] = (),
        *,
        timeout: float | None = None,
    ) -> tuple[str, ...]:
        assert not moves
        self.legal_calls.append((root_fen, timeout))
        index = self.state_index[root_fen]
        if index >= len(MOVES):
            return ()
        if self.illegal_ply == index:
            return ("a2a3",)
        return (MOVES[index], "a2a3")


class MutatingReplayEngine(FakeReplayEngine):
    def __init__(self, replay_engine_path: Path) -> None:
        super().__init__()
        self.replay_engine_path = replay_engine_path
        self.mutated = False

    def inspect_fen(
        self,
        root_fen: str,
        moves: tuple[str, ...] = (),
        *,
        timeout: float | None = None,
    ) -> FakeInspection:
        if not self.mutated:
            self.replay_engine_path.write_bytes(b"mutated replay engine\n")
            self.mutated = True
        return super().inspect_fen(root_fen, moves, timeout=timeout)


class FakeProcess:
    def __init__(self) -> None:
        self.returncode: int | None = None

    def poll(self) -> int | None:
        return self.returncode


class FakeThread:
    def __init__(self, *, alive: bool = False) -> None:
        self.alive = alive

    def is_alive(self) -> bool:
        return self.alive


class ManagedReplayEngine(FakeReplayEngine):
    def __init__(
        self,
        snapshot_path: Path,
        *,
        clean_close: bool = True,
        mutate_during_replay: bool = False,
    ) -> None:
        super().__init__()
        self.snapshot_path = snapshot_path
        self.command = (str(snapshot_path),)
        self.clean_close = clean_close
        self.mutate_during_replay = mutate_during_replay
        self.mutated = False
        self._closed = False
        self._process = FakeProcess()
        self._stdout_thread = FakeThread()
        self._stderr_thread = FakeThread()

    def __enter__(self) -> ManagedReplayEngine:
        return self

    def __exit__(
        self,
        _exc_type: object,
        _exc: object,
        _traceback: object,
    ) -> bool:
        if self.clean_close:
            self._closed = True
            self._process.returncode = 0
        return False

    def inspect_fen(
        self,
        root_fen: str,
        moves: tuple[str, ...] = (),
        *,
        timeout: float | None = None,
    ) -> FakeInspection:
        if self.mutate_during_replay and not self.mutated:
            os.chmod(self.snapshot_path, 0o600)
            self.snapshot_path.write_bytes(b"mutated executed snapshot\n")
            self.mutated = True
        return super().inspect_fen(root_fen, moves, timeout=timeout)


@pytest.fixture
def replay_engine_binding(tmp_path: Path) -> extractor.ReplayEngineBinding:
    path = tmp_path / "fake-replay-engine.bin"
    payload = b"authenticated synthetic replay engine\n"
    path.write_bytes(payload)
    return extractor.ReplayEngineBinding(
        path=path,
        sha256=common.sha256_bytes(payload),
        size_bytes=len(payload),
    )


def _digest(namespace: str, value: object) -> str:
    return common.sha256_bytes(
        common.canonical_json_bytes({"namespace": namespace, "value": value})
    )


def _game_row(leg: int) -> dict[str, object]:
    result_white = "0-1"
    trajectory = extractor.trajectory_sha256(ROOT, MOVES, result_white)
    game_id = extractor.source_game_id(
        experiment_id="atomic-e00-exp",
        battery_id="atomic-e00-battery",
        schedule_sha256=SCHEDULE_SHA,
        pair_id=PAIR_ID,
        leg=leg,
        trajectory_sha256_value=trajectory,
    )
    stdout = f"leg-{leg}-stdout\n".encode()
    return {
        "schema": extractor.GAME_SCHEMA,
        "experiment_id": "atomic-e00-exp",
        "battery_id": "atomic-e00-battery",
        "schedule_seed": "e00-test-seed",
        "schedule_sha256": SCHEDULE_SHA,
        "pair_id": PAIR_ID,
        "pair_ordinal": 1,
        "leg": leg,
        "stratum": "VSTC",
        "time_control": {
            "base_ms": 2000,
            "increment_ms": 20,
            "name": "VSTC",
        },
        "book_sha256": BOOK_SHA,
        "book_line": 17,
        "root_fen": ROOT,
        "root_fen_sha256": common.derive_id("atomic-root-fen-v1", ROOT),
        "engine_sha256": ENGINE_SHA,
        "current_net_sha256": CURRENT_SHA,
        "teacher_net_sha256": TEACHER_SHA,
        "white_network_role": "current-v3" if leg == 0 else "run3b",
        "black_network_role": "run3b" if leg == 0 else "current-v3",
        "result_white": result_white,
        "result_current": "loss" if leg == 0 else "win",
        "time_loss": False,
        "terminal_reason": "checkmate",
        "moves": list(MOVES),
        "ply_count": len(MOVES),
        "trajectory_sha256": trajectory,
        "source_game_id": game_id,
        "stdout_sha256": common.sha256_bytes(stdout),
        "stderr_sha256": common.sha256_bytes(b""),
        "engine_evidence": {},
        "referee_evidence": {},
        "process_evidence": {},
        "verifier_evidence": {},
    }


def _entry(root: Path, relative: str, kind: str) -> dict[str, object]:
    payload = (root / relative).read_bytes()
    return {
        "kind": kind,
        "path": relative,
        "sha256": common.sha256_bytes(payload),
        "size_bytes": len(payload),
    }


def _write_bytes(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)


def _artifact_binding(path: Path) -> dict[str, object]:
    payload = path.read_bytes()
    return {
        "path": str(path.absolute()),
        "sha256": common.sha256_bytes(payload),
        "size_bytes": len(payload),
        "identity": extractor._actual_identity(path, directory=False),
    }


def _artifact_evidence(binding: dict[str, object]) -> dict[str, object]:
    return {
        "bytes": binding["size_bytes"],
        "path": binding["path"],
        "sha256": binding["sha256"],
    }


def _process_evidence() -> dict[str, object]:
    return {
        "schema": extractor.OWNED_PROCESS_SCHEMA,
        "containment": "windows-job-object",
        "kill_on_close": True,
        "created_suspended_before_assignment": True,
        "resumed_primary_thread": True,
        "active_processes_zero": True,
        "termination_requested": False,
    }


def _rules_call(operation: str, ordinal: int) -> dict[str, object]:
    request = {"operation": operation, "ordinal": ordinal}
    response = {"accepted": True, "ordinal": ordinal}
    return {
        "operation": operation,
        "request": request,
        "response": response,
        "request_sha256": common.sha256_bytes(
            common.canonical_json_bytes(request)
        ),
        "response_sha256": common.sha256_bytes(
            common.canonical_json_bytes(response)
        ),
    }


def _hydrate_game_evidence(
    row: dict[str, object],
    *,
    inputs: dict[str, dict[str, object]],
    snapshots: dict[str, dict[str, object]],
    executed: dict[str, dict[str, object]],
    runtime_manifest: dict[str, object],
) -> None:
    execution_uci = [
        {"name": "UCI_Variant", "value": "atomic"},
        {"name": "Threads", "value": 1},
        {"name": "Hash", "value": 512},
        {"name": "MultiPV", "value": 1},
        {"name": "Ponder", "value": False},
        {"name": "SyzygyPath", "value": ""},
        {"name": "SyzygyProbeLimit", "value": 0},
        {"name": "Use NNUE", "value": "true"},
    ]
    advertised_names = sorted(
        {str(option["name"]) for option in execution_uci}
        | {"EvalFile"},
        key=str.casefold,
    )
    advertised = [
        {
            "name": name,
            "kind": "string",
            "default": "",
            "minimum": None,
            "maximum": None,
            "choices": [],
            "raw": f"option name {name} type string",
        }
        for name in advertised_names
    ]
    backends = {
        "current-v3": "AtomicNNUEV3",
        "run3b": "Legacy Atomic V1",
    }
    network_keys = {"current-v3": "current_net", "run3b": "teacher_net"}
    roles = (row["white_network_role"], row["black_network_role"])
    engine_id = {
        "name": "Atomic-Stockfish 1.0.3",
        "author": "the Atomic-Stockfish developers (see AUTHORS file)",
    }
    handshake_core = {
        "expected_preamble": list(extractor.ENGINE_HANDSHAKE_PREAMBLE),
        "observed_preamble": list(extractor.ENGINE_HANDSHAKE_PREAMBLE),
        "expected_identity_order": list(
            extractor.ENGINE_HANDSHAKE_IDENTITY_ORDER
        ),
        "observed_identity_order": list(
            extractor.ENGINE_HANDSHAKE_IDENTITY_ORDER
        ),
        "expect_single_blank_after_ids": True,
        "observed_blank_after_ids": True,
    }
    engines: list[dict[str, object]] = []
    for role in roles:
        network = snapshots[network_keys[str(role)]]
        marker = (
            "info string NNUE evaluation using "
            f"{backends[str(role)]} {network['path']} "
            f"({network['sha256']})"
        )
        raw_lines = [marker, "bestmove f2f3"]
        engines.append(
            {
                "role": role,
                "id": copy.deepcopy(engine_id),
                "handshake": {
                    **copy.deepcopy(handshake_core),
                    "sha256": _digest(
                        "atomic-e00-uci-handshake-v1",
                        handshake_core,
                    ),
                },
                "advertised_options": advertised,
                "advertised_options_sha256": _digest(
                    "atomic-e00-advertised-options-v2", advertised
                ),
                "configured_options": [
                    *execution_uci,
                    {"name": "EvalFile", "value": network["path"]},
                ],
                "network_proof": {
                    "backend": backends[str(role)],
                    "evalfile_path": network["path"],
                    "evalfile_sha256": network["sha256"],
                    "first_go": "preflight-nodes-1",
                    "line": marker,
                    "line_sha256": common.sha256_bytes(
                        marker.encode("utf-8")
                    ),
                    "raw_lines": raw_lines,
                    "raw_lines_sha256": _digest(
                        "atomic-e00-uci-go-raw-lines-v2", raw_lines
                    ),
                },
            }
        )
    row["engine_evidence"] = {
        "schema": extractor.ENGINE_EVIDENCE_SCHEMA,
        "variant_path_policy": "unavailable-explicit",
        "engines": engines,
    }

    runtime = runtime_manifest["runtime"]
    assert isinstance(runtime, dict)
    runtime_python = runtime["python"]
    assert isinstance(runtime_python, dict)
    native_rules = runtime["native_rules"]
    assert isinstance(native_rules, dict)
    native = {
        "build_manifest": _artifact_evidence(
            snapshots["pyffish_build_manifest"]
        ),
        "helper": _artifact_evidence(executed["atomic_outcome_helper"]),
        "pyffish": _artifact_evidence(snapshots["pyffish"]),
        "python": {
            "bytes": inputs["python_executable"]["size_bytes"],
            "cache_tag": "cpython-313",
            "executable": inputs["python_executable"]["path"],
            "runtime_libraries": runtime_python["runtime_libraries"],
            "sha256": inputs["python_executable"]["sha256"],
            "version": runtime_python["version"],
        },
        "rules_source": {
            "commit": native_rules["source_commit"],
            "files": native_rules["build_contract"]["source"]["files"],
            "root": native_rules["source_root"],
        },
        "schema": "atomic-e00-native-outcome-provenance-v1",
    }
    provenance = {
        "native": native,
        "builder": _artifact_evidence(executed["binding_builder"]),
        "uci_session": _artifact_evidence(executed["uci_session"]),
        "owned_process": _artifact_evidence(executed["owned_process"]),
        "manifest_binding_path": inputs["pyffish"]["path"],
    }
    child_inventory = runtime_python["child_import_inventory"]
    calls: list[dict[str, object]] = [_rules_call("outcome", 0)]
    events: list[dict[str, object]] = []
    clocks = [2_000_000_000, 2_000_000_000]
    for ply, move in enumerate(MOVES):
        side = ply % 2
        role = roles[side]
        network = snapshots[network_keys[str(role)]]
        marker = (
            "info string NNUE evaluation using "
            f"{backends[str(role)]} {network['path']} "
            f"({network['sha256']})"
        )
        raw_lines = [marker, f"bestmove {move}"]
        started = 1_000_000_000 + ply * 10_000_000
        completed = started + 1_000_000
        before = clocks[side]
        after = before - 1_000_000 + 20_000_000
        events.append(
            {
                "ply": ply,
                "role": role,
                "side_index": side,
                "remaining_before_ns": before,
                "white_clock_ms": clocks[0] // 1_000_000,
                "black_clock_ms": clocks[1] // 1_000_000,
                "go_started_ns": started,
                "bestmove_completed_ns": completed,
                "elapsed_ns": 1_000_000,
                "on_time": True,
                "bestmove": move,
                "move_applied": True,
                "remaining_after_ns": after,
                "network_marker": marker,
                "network_marker_sha256": common.sha256_bytes(
                    marker.encode("utf-8")
                ),
                "network_raw_lines_sha256": _digest(
                    "atomic-e00-uci-go-raw-lines-v2", raw_lines
                ),
                "network_raw_lines": raw_lines,
            }
        )
        clocks[side] = after
        calls.extend(
            (
                _rules_call("legal-moves", 1 + ply * 2),
                _rules_call("outcome", 2 + ply * 2),
            )
        )
    row["referee_evidence"] = {
        "schema": extractor.REFEREE_EVIDENCE_SCHEMA,
        "provenance": copy.deepcopy(provenance),
        "rules_calls": calls,
        "timing_events": events,
        "child_import_inventory": copy.deepcopy(child_inventory),
    }
    row["process_evidence"] = _process_evidence()
    row["verifier_evidence"] = {
        "schema": extractor.VERIFIER_EVIDENCE_SCHEMA,
        "process": _process_evidence(),
        "provenance": copy.deepcopy(provenance),
        "rules_calls": copy.deepcopy(calls),
        "verified": {
            "moves": row["moves"],
            "result_white": row["result_white"],
            "root_fen": row["root_fen"],
            "terminal_reason": row["terminal_reason"],
            "time_loss": row["time_loss"],
            "trajectory_sha256": row["trajectory_sha256"],
        },
        "child_import_inventory": copy.deepcopy(child_inventory),
    }


def _publish_source(
    root: Path,
    *,
    games: list[dict[str, object]] | None = None,
    staged_games: list[dict[str, object]] | None = None,
    rejections: bytes = b"",
    omit_inventory_path: str | None = None,
    extra_staging: bool = False,
    status: str = "committed",
    runtime_manifest_schema: str | None = None,
    engine_evidence_schema: str | None = None,
    omit_runtime_import_key: str | None = None,
) -> tuple[Path, list[dict[str, object]]]:
    root.mkdir()
    original_root = root.parent / f".{root.name}-inputs"
    original_root.mkdir()
    native_root = original_root / "native-build"
    native_root.mkdir()
    rules_source_root = original_root / "rules-source"
    rules_source_files = {
        "src/variant.cpp": {
            "bytes": 1,
            "sha256": "7" * 64,
        }
    }
    build_contract = {
        "schema": "synthetic-atomic-native-build-v1",
        "source": {
            "commit": "f" * 40,
            "files": rules_source_files,
            "root": str(rules_source_root.absolute()),
        },
    }
    build_manifest_bytes = common.canonical_json_bytes(build_contract)
    original_payloads = {
        "atomic_mining_init": (
            "source/tools/atomic_mining/__init__.py",
            b"synthetic atomic mining package\n",
        ),
        "atomic_outcome_helper": (
            "source/tools/atomic_mining/atomic_outcome_helper.py",
            b"synthetic atomic outcome helper\n",
        ),
        "binding_builder": (
            "source/tools/atomic_mining/build_atomic_outcome_binding.py",
            b"synthetic atomic outcome binding builder\n",
        ),
        "book": ("opening.epd", BOOK_BYTES),
        "common": (
            "source/tools/atomic_mining/common.py",
            b"synthetic atomic mining common\n",
        ),
        "engine": ("engine.exe", ENGINE_BYTES),
        "current_net": ("current.nnue", CURRENT_BYTES),
        "teacher_net": ("teacher.nnue", TEACHER_BYTES),
        "variant_config": ("variants.ini", VARIANT_CONFIG_BYTES),
        "owned_process": (
            "source/tools/atomic_mining/owned_process.py",
            b"synthetic owned process\n",
        ),
        "pyffish": (
            "native-build/pyffish.pyd",
            b"synthetic authenticated pyffish\n",
        ),
        "pyffish_build_manifest": (
            "native-build/build-manifest.json",
            build_manifest_bytes,
        ),
        "runner": (
            "source/tools/atomic_mining/run_e00_source.py",
            RUNNER_BYTES,
        ),
        "schedule": ("schedule.jsonl", SCHEDULE_BYTES),
        "schedule_builder": (
            "source/tools/atomic_mining/build_e00_schedule.py",
            b"synthetic E00 schedule builder\n",
        ),
        "schedule_receipt": (
            "schedule.receipt.json",
            SCHEDULE_RECEIPT_BYTES,
        ),
        "tools_init": (
            "source/tools/__init__.py",
            b"synthetic tools package\n",
        ),
        "uci_session": (
            "source/tools/atomic_mining/uci_session.py",
            b"synthetic UCI session\n",
        ),
    }
    input_bindings: dict[str, dict[str, object]] = {}
    for key, (name, payload) in original_payloads.items():
        path = original_root / name
        _write_bytes(path, payload)
        input_bindings[key] = _artifact_binding(path)

    python_path = original_root / "python.exe"
    runtime_library_path = original_root / "python313.dll"
    imported_module_path = original_root / "python-install" / "encodings.py"
    _write_bytes(python_path, b"synthetic Python executable\n")
    _write_bytes(runtime_library_path, b"synthetic Python runtime DLL\n")
    _write_bytes(imported_module_path, b"synthetic imported module\n")
    input_bindings["python_executable"] = _artifact_binding(python_path)
    input_bindings["python_runtime_library_001"] = _artifact_binding(
        runtime_library_path
    )
    input_bindings["python_imported_module_001"] = _artifact_binding(
        imported_module_path
    )

    uci_options = [
        {"name": "UCI_Variant", "value": "atomic"},
        {"name": "Threads", "value": 1},
        {"name": "Hash", "value": 512},
        {"name": "MultiPV", "value": 1},
        {"name": "Ponder", "value": False},
        {"name": "SyzygyPath", "value": ""},
        {"name": "SyzygyProbeLimit", "value": 0},
        {"name": "Use NNUE", "value": "true"},
    ]
    clock_policy = {
        "charged_interval": (
            "complete-go-write-flush-to-complete-bestmove-newline"
        ),
        "equality": "elapsed-ns-equal-remaining-ns-is-on-time",
        "uci_millisecond_conversion": "floor-nanoseconds",
    }
    manifest_execution = {
        "threads": 1,
        "maximum_plies": 1024,
        "command_timeout_seconds": 120.0,
        "maximum_wall_seconds": 14_400.0,
        "maximum_game_wall_seconds": 1_800.0,
        "uci_options": uci_options,
        "clock_policy": clock_policy,
    }
    runtime_layout = {
        key: relative.as_posix()
        for key, relative in extractor._RUNTIME_LAYOUT.items()
    }
    child_import_inventory: list[dict[str, object]] = [
        {
            "source": "python-installation",
            "path": input_bindings["python_imported_module_001"]["path"],
            "sha256": input_bindings["python_imported_module_001"]["sha256"],
            "size_bytes": input_bindings[
                "python_imported_module_001"
            ]["size_bytes"],
            "module_names": ["encodings"],
        },
        {
            "source": "pyffish",
            "path": "pyffish",
            "sha256": input_bindings["pyffish"]["sha256"],
            "size_bytes": input_bindings["pyffish"]["size_bytes"],
            "module_names": ["pyffish"],
        },
    ]
    for key, relative in runtime_layout.items():
        if key == omit_runtime_import_key:
            continue
        child_import_inventory.append(
            {
                "source": "runtime-package",
                "path": relative,
                "sha256": input_bindings[key]["sha256"],
                "size_bytes": input_bindings[key]["size_bytes"],
                "module_names": [f"sealed_runtime.{key}"],
            }
        )
    child_import_inventory.sort(
        key=lambda row: (str(row["source"]), str(row["path"]))
    )

    def runtime_artifact(key: str) -> dict[str, object]:
        binding = input_bindings[key]
        return {
            "path": binding["path"],
            "sha256": binding["sha256"],
            "size_bytes": binding["size_bytes"],
        }

    runtime_manifest = {
        "schema": (
            runtime_manifest_schema or extractor.RUNTIME_MANIFEST_SCHEMA
        ),
        "runtime": {
            "python": {
                "executable": input_bindings["python_executable"]["path"],
                "executable_sha256": input_bindings[
                    "python_executable"
                ]["sha256"],
                "executable_size_bytes": input_bindings[
                    "python_executable"
                ]["size_bytes"],
                "runtime_libraries": {
                    "python313.dll": {
                        "bytes": input_bindings[
                            "python_runtime_library_001"
                        ]["size_bytes"],
                        "path": input_bindings[
                            "python_runtime_library_001"
                        ]["path"],
                        "sha256": input_bindings[
                            "python_runtime_library_001"
                        ]["sha256"],
                    }
                },
                "child_import_inventory": child_import_inventory,
                "version": "3.13.5 synthetic",
            },
            "modules": {
                "atomic_mining_init": runtime_artifact(
                    "atomic_mining_init"
                ),
                "atomic_outcome_helper": runtime_artifact(
                    "atomic_outcome_helper"
                ),
                "binding_builder": runtime_artifact("binding_builder"),
                "common": runtime_artifact("common"),
                "owned_process": runtime_artifact("owned_process"),
                "schedule_builder": runtime_artifact("schedule_builder"),
                "tools_init": runtime_artifact("tools_init"),
                "uci_session": runtime_artifact("uci_session"),
                "wrapper": runtime_artifact("runner"),
            },
            "native_rules": {
                "binding": runtime_artifact("pyffish"),
                "build_contract": build_contract,
                "build_manifest": runtime_artifact(
                    "pyffish_build_manifest"
                ),
                "source_commit": "f" * 40,
                "source_root": str(rules_source_root.absolute()),
            },
            "source": {
                "root": str(rules_source_root.absolute()),
                "commit": "f" * 40,
                "tree": "e" * 40,
                "clean": True,
            },
        },
        "execution": manifest_execution,
        "inputs": {
            key: input_bindings[key]["sha256"]
            for key in sorted(extractor._CORE_INPUT_KEYS)
        },
    }
    runtime_manifest_payload = common.canonical_json_bytes(runtime_manifest)
    runtime_manifest_path = original_root / "runtime-manifest.json"
    _write_bytes(runtime_manifest_path, runtime_manifest_payload)
    input_bindings["runtime_manifest"] = _artifact_binding(
        runtime_manifest_path
    )

    snapshot_root = root / ".input-snapshots"
    snapshot_root.mkdir()
    input_snapshots: dict[str, dict[str, object]] = {}
    for key, binding in sorted(input_bindings.items()):
        original_path = Path(str(binding["path"]))
        snapshot_path = snapshot_root / (
            f"{key}-{binding['sha256']}{original_path.suffix}"
        )
        payload = original_path.read_bytes()
        _write_bytes(snapshot_path, payload)
        os.chmod(snapshot_path, stat.S_IREAD)
        input_snapshots[key] = _artifact_binding(snapshot_path)

    runtime_root = root / ".runtime-package"
    executed_runtime_package: dict[str, dict[str, object]] = {}
    for key, relative in extractor._RUNTIME_LAYOUT.items():
        runtime_path = runtime_root.joinpath(*relative.parts)
        source_path = Path(str(input_snapshots[key]["path"]))
        _write_bytes(runtime_path, source_path.read_bytes())
        os.chmod(runtime_path, stat.S_IREAD)
        executed_runtime_package[key] = _artifact_binding(runtime_path)

    native_build_artifacts: dict[str, dict[str, object]] = {}
    for path in sorted(
        (candidate for candidate in native_root.rglob("*") if candidate.is_file()),
        key=lambda candidate: candidate.relative_to(native_root).as_posix(),
    ):
        native_build_artifacts[
            path.relative_to(native_root).as_posix()
        ] = _artifact_binding(path)

    rows = copy.deepcopy(games or [_game_row(0), _game_row(1)])
    staged = copy.deepcopy(staged_games or rows)
    for row in rows:
        _hydrate_game_evidence(
            row,
            inputs=input_bindings,
            snapshots=input_snapshots,
            executed=executed_runtime_package,
            runtime_manifest=runtime_manifest,
        )
        if engine_evidence_schema is not None:
            row["engine_evidence"]["schema"] = engine_evidence_schema
    for row in staged:
        _hydrate_game_evidence(
            row,
            inputs=input_bindings,
            snapshots=input_snapshots,
            executed=executed_runtime_package,
            runtime_manifest=runtime_manifest,
        )
        if engine_evidence_schema is not None:
            row["engine_evidence"]["schema"] = engine_evidence_schema
    _write_bytes(
        root / "games.jsonl",
        b"".join(common.canonical_json_bytes(row) for row in rows),
    )
    _write_bytes(root / "rejections.jsonl", rejections)
    pair_dir = root / ".pair-temporaries" / f"000001-{PAIR_ID}"
    pair_dir.mkdir(parents=True)
    for leg in (0, 1):
        staged_row = staged[leg]
        stdout = f"leg-{leg}-stdout\n".encode()
        _write_bytes(pair_dir / f"leg-{leg}.json", common.canonical_json_bytes(staged_row))
        _write_bytes(pair_dir / f"leg-{leg}.stdout.bin", stdout)
        _write_bytes(pair_dir / f"leg-{leg}.stderr.bin", b"")
    _write_bytes(
        pair_dir / "pair.json",
        common.canonical_json_bytes(
            {
                "schema": extractor.PAIR_SCHEMA,
                "pair_id": PAIR_ID,
                "rows_sha256": common.sha256_bytes(
                    b"".join(common.canonical_json_bytes(row) for row in staged)
                ),
                "legs": [0, 1],
            }
        ),
    )
    if extra_staging:
        _write_bytes(pair_dir / "unexpected.bin", b"extra")

    relatives = [
        ("games.jsonl", "accepted-games"),
        ("rejections.jsonl", "rejection-ledger"),
    ]
    relatives.extend(
        (
            f".pair-temporaries/000001-{PAIR_ID}/{name}",
            "pair-staging",
        )
        for name in (
            "leg-0.json",
            "leg-0.stderr.bin",
            "leg-0.stdout.bin",
            "leg-1.json",
            "leg-1.stderr.bin",
            "leg-1.stdout.bin",
            "pair.json",
        )
    )
    inventory_entries = [
        _entry(root, relative, kind)
        for relative, kind in relatives
        if relative != omit_inventory_path
    ]
    inventory = {
        "schema": extractor.INVENTORY_SCHEMA,
        "entries": inventory_entries,
    }
    _write_bytes(root / "inventory.json", common.canonical_json_bytes(inventory))

    wins = sum(row.get("result_current") == "win" for row in rows)
    losses = sum(row.get("result_current") == "loss" for row in rows)
    draws = sum(row.get("result_current") == "draw" for row in rows)
    execution = {
        "threads": 1,
        "retry_policy": "none",
        "pair_order": "schedule-ordinal-then-leg",
        "uci_options": uci_options,
        "network_options": {
            "current-v3": [
                {
                    "name": "EvalFile",
                    "value": input_snapshots["current_net"]["path"],
                }
            ],
            "run3b": [
                {
                    "name": "EvalFile",
                    "value": input_snapshots["teacher_net"]["path"],
                }
            ],
        },
        "maximum_plies": manifest_execution["maximum_plies"],
        "command_timeout_seconds": manifest_execution[
            "command_timeout_seconds"
        ],
        "maximum_wall_seconds": manifest_execution[
            "maximum_wall_seconds"
        ],
        "maximum_game_wall_seconds": manifest_execution[
            "maximum_game_wall_seconds"
        ],
        "time_loss_rule": (
            "draw-if-nonflagging-side-has-insufficient-atomic-"
            "mating-material-v1"
        ),
        "clock_policy": clock_policy,
        "referee_policy": "fresh-exact-pyffish-root-plus-history-v2",
        "owned_process_policy": (
            "windows-job-create-suspended-assign-resume-active-zero-v1"
        ),
        "verifier_policy": (
            "independent-fresh-pyffish-owned-process-replay-v2"
        ),
    }
    guard_paths = {
        "native_build": native_root,
        "output_parent": root.parent,
        "output": root,
        "snapshots": snapshot_root,
        "runtime_package": runtime_root,
    }
    static_inventories = {
        "native_build": extractor._directory_inventory_wire(native_root),
        "snapshots": extractor._directory_inventory_wire(snapshot_root),
        "runtime_package": extractor._directory_inventory_wire(runtime_root),
    }
    namespace_guards = {
        key: {
            "path": str(path.absolute()),
            "identity": extractor._actual_identity(path, directory=True),
            "parent_identity": extractor._actual_identity(
                path.parent, directory=True
            ),
            "enumeration": static_inventories.get(key),
        }
        for key, path in sorted(guard_paths.items())
    }
    trust_boundary = {
        "hermetic": False,
        "guarded_runtime": (
            "sys.executable, native Python runtime libraries, every "
            "precommitted file-backed imported module, executed runtime "
            "package, pyffish, engine, networks, and all other "
            "result-bearing file inputs"
        ),
        "child_import_policy": (
            "canonical exact inventory; no extras, omissions, duplicates, "
            "identity drift, or late imports"
        ),
        "namespace_policy": (
            "no-delete-share directory handles, immutable locks for every "
            "known result-bearing file, exact static enumeration checkpoints, "
            "and per-child output enumeration"
        ),
        "residual_tcb": [
            "Windows kernel and loader",
            "Python standard-library code not imported or executed",
            (
                "cooperative non-hostile local host; a newly created "
                "unreferenced namespace entry is detected at the next "
                "checkpoint but is not kernel-prevented"
            ),
            "hardware and firmware",
        ],
    }
    receipt = {
        "schema": extractor.EXECUTION_RECEIPT_SCHEMA,
        "status": status,
        "experiment_id": "atomic-e00-exp",
        "battery_id": "atomic-e00-battery",
        "schedule": {
            "sha256": SCHEDULE_SHA,
            "size_bytes": len(SCHEDULE_BYTES),
            "receipt_sha256": common.sha256_bytes(
                SCHEDULE_RECEIPT_BYTES
            ),
            "receipt_size_bytes": len(SCHEDULE_RECEIPT_BYTES),
        },
        "inputs": {
            key: copy.deepcopy(value)
            for key, value in sorted(input_bindings.items())
        },
        "input_snapshots": input_snapshots,
        "executed_runtime_package": executed_runtime_package,
        "native_build_artifacts": native_build_artifacts,
        "runtime": {
            "manifest": runtime_manifest,
            "observed_pre_and_post_equal": True,
        },
        "namespace_guards": namespace_guards,
        "trust_boundary": trust_boundary,
        "execution": execution,
        "reconciliation": {
            "accepted_pairs": 1,
            "accepted_games": len(rows),
            "wins_current": wins,
            "losses_current": losses,
            "draws": draws,
            "time_losses": sum(bool(row.get("time_loss")) for row in rows),
            "all_pairs_atomic": True,
            "all_trajectories_legally_replayed": True,
            "all_ids_recomputed": True,
            "zero_extra_duplicate_partial_rows": True,
        },
        "outputs": {
            name: {
                "path": relative,
                "sha256": common.sha256_file(root / relative),
                "size_bytes": (root / relative).stat().st_size,
            }
            for name, relative in (
                ("games", "games.jsonl"),
                ("rejections", "rejections.jsonl"),
                ("inventory", "inventory.json"),
            )
        },
    }
    _write_bytes(root / "receipt.json", common.canonical_json_bytes(receipt))
    return root, rows


def _rewrite_receipt(root: Path, mutate: object) -> None:
    receipt = json.loads((root / "receipt.json").read_text(encoding="utf-8"))
    assert isinstance(receipt, dict)
    mutate(receipt)
    (root / "receipt.json").write_bytes(common.canonical_json_bytes(receipt))


def _receipt_sha(root: Path) -> str:
    return common.sha256_file(root / "receipt.json")


def test_committed_source_extracts_every_pre_move_position_without_dedup(
    tmp_path: Path,
    replay_engine_binding: extractor.ReplayEngineBinding,
) -> None:
    source, _rows = _publish_source(tmp_path / "source")
    output = tmp_path / "positions.jsonl"
    receipt_path = tmp_path / "positions.receipt.json"
    engine = FakeReplayEngine()

    summary = extractor.extract_e00_positions(
        source,
        output,
        receipt_path,
        engine,
        replay_engine_binding=replay_engine_binding,
        expected_execution_receipt_sha256=_receipt_sha(source),
        timeout=3.0,
    )

    positions = common.load_jsonl(output)
    assert summary.pairs == 1
    assert summary.games == 2
    assert summary.positions == 8
    assert len(positions) == 8
    assert len({row["position"]["position_id"] for row in positions}) == 8
    assert [row["source"]["leg"] for row in positions] == [0] * 4 + [1] * 4
    assert all(
        row["source"]["leakage_group_id"] == PAIR_ID for row in positions
    )
    assert positions[0]["position"]["history_uci"] == []
    assert positions[1]["position"]["history_uci"] == ["f2f3"]
    assert positions[0]["position"]["result_white"] == "0-1"
    assert positions[0]["position"]["result_side_to_move"] == "loss"
    assert positions[1]["position"]["result_side_to_move"] == "win"
    assert "moves" not in positions[0]["game"]
    assert "future_uci" not in positions[0]["position"]
    assert len(engine.legal_calls) == 8
    assert all(timeout == 3.0 for _fen, timeout in engine.legal_calls)

    extraction_receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    assert extraction_receipt["status"] == "committed"
    assert extraction_receipt["counts"] == {
        "pairs": 1,
        "games": 2,
        "positions": 8,
    }
    assert extraction_receipt["replay"]["deduplication"] == "none"
    binding_receipt = {
        "path": str(replay_engine_binding.path.absolute()),
        "sha256": replay_engine_binding.sha256,
        "size_bytes": replay_engine_binding.size_bytes,
    }
    assert extraction_receipt["replay"]["engine"] == {
        "policy": "injected-preauthenticated-engine-v1",
        "original": binding_receipt,
        "executed_snapshot": binding_receipt,
        "snapshot": {
            "content_addressed": False,
            "exclusive_directory": False,
            "read_only_applied": False,
            "verified_before_launch": False,
            "verified_before_replay": True,
            "verified_after_replay": True,
            "verified_after_clean_close": False,
            "removed_after_clean_close": False,
        },
        "process_cleanup_verified": False,
    }
    assert extraction_receipt["replay"]["result_adjudication"] == {
        "independently_reauthenticated": False,
        "status": "pending-exact-rules-helper",
    }
    assert extraction_receipt["output"]["sha256"] == common.sha256_file(output)
    assert common.sha256_file(receipt_path) == summary.receipt_sha256


def test_engine_evidence_v3_handshake_is_exact_and_type_strict(
    tmp_path: Path,
) -> None:
    source, rows = _publish_source(tmp_path / "handshake-v3")
    receipt = json.loads(
        (source / "receipt.json").read_text(encoding="utf-8")
    )
    evidence = rows[0]["engine_evidence"]

    extractor._validate_engine_evidence_v3(
        evidence,
        row=rows[0],
        receipt=receipt,
        label="test evidence",
    )

    int_flag = copy.deepcopy(evidence)
    int_flag["engines"][0]["handshake"][
        "expect_single_blank_after_ids"
    ] = 1
    with pytest.raises(extractor.E00ExtractionError, match="must be bool"):
        extractor._validate_engine_evidence_v3(
            int_flag,
            row=rows[0],
            receipt=receipt,
            label="test evidence",
        )

    copied_digest = copy.deepcopy(evidence)
    copied_digest["engines"][0]["handshake"][
        "observed_preamble"
    ] = ["changed banner"]
    with pytest.raises(
        extractor.E00ExtractionError, match="handshake differs"
    ):
        extractor._validate_engine_evidence_v3(
            copied_digest,
            row=rows[0],
            receipt=receipt,
            label="test evidence",
        )

    recomputed_drift = copy.deepcopy(evidence)
    handshake = recomputed_drift["engines"][0]["handshake"]
    handshake["observed_preamble"] = ["changed banner"]
    handshake["sha256"] = _digest(
        "atomic-e00-uci-handshake-v1",
        {
            key: handshake[key]
            for key in (
                "expected_preamble",
                "observed_preamble",
                "expected_identity_order",
                "observed_identity_order",
                "expect_single_blank_after_ids",
                "observed_blank_after_ids",
            )
        },
    )
    with pytest.raises(
        extractor.E00ExtractionError, match="handshake differs"
    ):
        extractor._validate_engine_evidence_v3(
            recomputed_drift,
            row=rows[0],
            receipt=receipt,
            label="test evidence",
        )


def test_receipt_validation_failure_occurs_before_commit_marker(
    tmp_path: Path,
    replay_engine_binding: extractor.ReplayEngineBinding,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source, _rows = _publish_source(tmp_path / "source")
    output = tmp_path / "positions.jsonl"
    receipt_path = tmp_path / "positions.receipt.json"
    canonical_json = extractor._canonical_json

    def reject_prepared_receipt(payload: bytes, *, label: str) -> object:
        if label == "E00 extraction receipt":
            raise extractor.E00ExtractionError(
                "synthetic prepared receipt validation failure"
            )
        return canonical_json(payload, label=label)

    monkeypatch.setattr(extractor, "_canonical_json", reject_prepared_receipt)
    with pytest.raises(
        extractor.E00ExtractionError,
        match="synthetic prepared receipt validation failure",
    ):
        extractor.extract_e00_positions(
            source,
            output,
            receipt_path,
            FakeReplayEngine(),
            replay_engine_binding=replay_engine_binding,
            expected_execution_receipt_sha256=_receipt_sha(source),
        )
    assert output.exists()
    assert not receipt_path.exists()


def test_existing_output_refuses_before_source_or_engine_use(
    tmp_path: Path,
    replay_engine_binding: extractor.ReplayEngineBinding,
) -> None:
    source, _rows = _publish_source(tmp_path / "source")
    output = tmp_path / "positions.jsonl"
    output.write_text("sentinel", encoding="utf-8")
    engine = FakeReplayEngine()

    with pytest.raises(FileExistsError, match="refusing to overwrite"):
        extractor.extract_e00_positions(
            source,
            output,
            tmp_path / "receipt.json",
            engine,
            replay_engine_binding=replay_engine_binding,
            expected_execution_receipt_sha256=_receipt_sha(source),
        )

    assert output.read_text(encoding="utf-8") == "sentinel"
    assert engine.inspect_calls == []


@pytest.mark.parametrize(
    ("mutation", "message"),
    (
        (
            lambda receipt: receipt.__setitem__("status", "running"),
            "not committed",
        ),
        (
            lambda receipt: receipt["outputs"]["games"].__setitem__(
                "path", "../games.jsonl"
            ),
            "path must be",
        ),
    ),
)
def test_receipt_status_and_output_paths_fail_before_replay(
    tmp_path: Path,
    mutation: object,
    message: str,
    replay_engine_binding: extractor.ReplayEngineBinding,
) -> None:
    source, _rows = _publish_source(tmp_path / "source")
    _rewrite_receipt(source, mutation)
    engine = FakeReplayEngine()
    with pytest.raises(extractor.E00ExtractionError, match=message):
        extractor.extract_e00_positions(
            source,
            tmp_path / "positions.jsonl",
            tmp_path / "positions.receipt.json",
            engine,
            replay_engine_binding=replay_engine_binding,
            expected_execution_receipt_sha256=_receipt_sha(source),
        )
    assert engine.inspect_calls == []


def test_games_hash_tamper_fails_before_replay(
    tmp_path: Path,
    replay_engine_binding: extractor.ReplayEngineBinding,
) -> None:
    source, rows = _publish_source(tmp_path / "source")
    tampered = copy.deepcopy(rows)
    tampered[0]["terminal_reason"] = "tampered"
    (source / "games.jsonl").write_bytes(
        b"".join(common.canonical_json_bytes(row) for row in tampered)
    )
    engine = FakeReplayEngine()

    with pytest.raises(extractor.E00ExtractionError, match="hash or size"):
        extractor.extract_e00_positions(
            source,
            tmp_path / "positions.jsonl",
            tmp_path / "positions.receipt.json",
            engine,
            replay_engine_binding=replay_engine_binding,
            expected_execution_receipt_sha256=_receipt_sha(source),
        )
    assert engine.inspect_calls == []


def test_zero_rejections_is_semantic_even_when_fully_resealed(
    tmp_path: Path,
    replay_engine_binding: extractor.ReplayEngineBinding,
) -> None:
    source, _rows = _publish_source(
        tmp_path / "source",
        rejections=common.canonical_json_bytes({"error": "synthetic"}),
    )
    engine = FakeReplayEngine()
    with pytest.raises(extractor.E00ExtractionError, match="zero rejections"):
        extractor.extract_e00_positions(
            source,
            tmp_path / "positions.jsonl",
            tmp_path / "positions.receipt.json",
            engine,
            replay_engine_binding=replay_engine_binding,
            expected_execution_receipt_sha256=_receipt_sha(source),
        )
    assert engine.inspect_calls == []


def test_missing_leg_and_bad_trajectory_identity_fail_before_replay(
    tmp_path: Path,
    replay_engine_binding: extractor.ReplayEngineBinding,
) -> None:
    missing_source, _rows = _publish_source(
        tmp_path / "missing",
        games=[_game_row(0)],
        staged_games=[_game_row(0), _game_row(1)],
    )
    engine = FakeReplayEngine()
    with pytest.raises(extractor.E00ExtractionError, match="ordered|legs"):
        extractor.extract_e00_positions(
            missing_source,
            tmp_path / "missing.positions.jsonl",
            tmp_path / "missing.receipt.json",
            engine,
            replay_engine_binding=replay_engine_binding,
            expected_execution_receipt_sha256=_receipt_sha(missing_source),
        )
    assert engine.inspect_calls == []

    tampered_rows = [_game_row(0), _game_row(1)]
    tampered_rows[0]["trajectory_sha256"] = "f" * 64
    tampered_source, _ = _publish_source(
        tmp_path / "trajectory",
        games=tampered_rows,
    )
    with pytest.raises(extractor.E00ExtractionError, match="trajectory identity"):
        extractor.extract_e00_positions(
            tampered_source,
            tmp_path / "trajectory.positions.jsonl",
            tmp_path / "trajectory.receipt.json",
            engine,
            replay_engine_binding=replay_engine_binding,
            expected_execution_receipt_sha256=_receipt_sha(tampered_source),
        )
    assert engine.inspect_calls == []


def test_inventory_and_staging_are_semantically_authenticated(
    tmp_path: Path,
    replay_engine_binding: extractor.ReplayEngineBinding,
) -> None:
    missing_path = (
        f".pair-temporaries/000001-{PAIR_ID}/leg-1.stdout.bin"
    )
    inventory_source, _ = _publish_source(
        tmp_path / "inventory",
        omit_inventory_path=missing_path,
    )
    engine = FakeReplayEngine()
    with pytest.raises(extractor.E00ExtractionError, match="exactly cover|absent"):
        extractor.extract_e00_positions(
            inventory_source,
            tmp_path / "inventory.positions.jsonl",
            tmp_path / "inventory.receipt.json",
            engine,
            replay_engine_binding=replay_engine_binding,
            expected_execution_receipt_sha256=_receipt_sha(inventory_source),
        )
    assert engine.inspect_calls == []

    staged_rows = [_game_row(0), _game_row(1)]
    staged_rows[1]["terminal_reason"] = "authenticated-but-different"
    staging_source, _ = _publish_source(
        tmp_path / "staging",
        staged_games=staged_rows,
    )
    with pytest.raises(extractor.E00ExtractionError, match="staged leg differs"):
        extractor.extract_e00_positions(
            staging_source,
            tmp_path / "staging.positions.jsonl",
            tmp_path / "staging.receipt.json",
            engine,
            replay_engine_binding=replay_engine_binding,
            expected_execution_receipt_sha256=_receipt_sha(staging_source),
        )
    assert engine.inspect_calls == []

    extra_source, _ = _publish_source(
        tmp_path / "extra",
        extra_staging=True,
    )
    with pytest.raises(
        extractor.E00ExtractionError, match="unexpected paths"
    ):
        extractor.extract_e00_positions(
            extra_source,
            tmp_path / "extra.positions.jsonl",
            tmp_path / "extra.receipt.json",
            engine,
            replay_engine_binding=replay_engine_binding,
            expected_execution_receipt_sha256=_receipt_sha(extra_source),
        )
    assert engine.inspect_calls == []


def test_illegal_move_and_replay_checker_mismatch_publish_nothing(
    tmp_path: Path,
    replay_engine_binding: extractor.ReplayEngineBinding,
) -> None:
    source, _rows = _publish_source(tmp_path / "source")
    illegal_output = tmp_path / "illegal.positions.jsonl"
    with pytest.raises(extractor.E00ExtractionError, match="illegal at ply 2"):
        extractor.extract_e00_positions(
            source,
            illegal_output,
            tmp_path / "illegal.receipt.json",
            FakeReplayEngine(illegal_ply=2),
            replay_engine_binding=replay_engine_binding,
            expected_execution_receipt_sha256=_receipt_sha(source),
        )
    assert not illegal_output.exists()

    mismatch_output = tmp_path / "mismatch.positions.jsonl"
    with pytest.raises(extractor.E00ExtractionError, match="checkers differ"):
        extractor.extract_e00_positions(
            source,
            mismatch_output,
            tmp_path / "mismatch.receipt.json",
            FakeReplayEngine(mismatch_checkers_ply=1),
            replay_engine_binding=replay_engine_binding,
            expected_execution_receipt_sha256=_receipt_sha(source),
        )
    assert not mismatch_output.exists()


def test_replay_engine_hash_mismatch_and_post_replay_mutation_fail_closed(
    tmp_path: Path,
    replay_engine_binding: extractor.ReplayEngineBinding,
) -> None:
    source, _rows = _publish_source(tmp_path / "source")
    bad_binding = extractor.ReplayEngineBinding(
        path=replay_engine_binding.path,
        sha256="f" * 64,
        size_bytes=replay_engine_binding.size_bytes,
    )
    engine = FakeReplayEngine()
    bad_output = tmp_path / "bad-hash.positions.jsonl"
    with pytest.raises(
        extractor.E00ExtractionError,
        match="identity differs|hash or size differs",
    ):
        extractor.extract_e00_positions(
            source,
            bad_output,
            tmp_path / "bad-hash.receipt.json",
            engine,
            replay_engine_binding=bad_binding,
            expected_execution_receipt_sha256=_receipt_sha(source),
        )
    assert engine.inspect_calls == []
    assert not bad_output.exists()

    mutated_output = tmp_path / "mutated.positions.jsonl"
    mutating_engine = MutatingReplayEngine(replay_engine_binding.path)
    with pytest.raises(
        extractor.E00ExtractionError,
        match="identity differs|hash or size differs",
    ):
        extractor.extract_e00_positions(
            source,
            mutated_output,
            tmp_path / "mutated.receipt.json",
            mutating_engine,
            replay_engine_binding=replay_engine_binding,
            expected_execution_receipt_sha256=_receipt_sha(source),
        )
    assert mutating_engine.inspect_calls
    assert not mutated_output.exists()
    assert not (tmp_path / "mutated.receipt.json").exists()


def test_canonical_output_is_deterministic_across_fresh_targets(
    tmp_path: Path,
    replay_engine_binding: extractor.ReplayEngineBinding,
) -> None:
    source, _rows = _publish_source(tmp_path / "source")
    first = tmp_path / "first.jsonl"
    second = tmp_path / "second.jsonl"
    extractor.extract_e00_positions(
        source,
        first,
        tmp_path / "first.receipt.json",
        FakeReplayEngine(),
        replay_engine_binding=replay_engine_binding,
        expected_execution_receipt_sha256=_receipt_sha(source),
    )
    extractor.extract_e00_positions(
        source,
        second,
        tmp_path / "second.receipt.json",
        FakeReplayEngine(),
        replay_engine_binding=replay_engine_binding,
        expected_execution_receipt_sha256=_receipt_sha(source),
    )
    assert first.read_bytes() == second.read_bytes()


def test_cli_executes_only_content_addressed_snapshot_if_original_is_replaced(
    tmp_path: Path,
    replay_engine_binding: extractor.ReplayEngineBinding,
) -> None:
    source, _rows = _publish_source(tmp_path / "source")
    output = tmp_path / "positions.jsonl"
    receipt_path = tmp_path / "positions.receipt.json"
    launched: list[Path] = []
    snapshot_paths: list[Path] = []

    def replace_and_restore_original(
        snapshot: extractor.ReplayEngineSnapshot,
    ) -> None:
        snapshot_paths.append(snapshot.executed.path)
        moved = tmp_path / "original-moved.bin"
        replacement = tmp_path / "replacement.bin"
        replacement.write_bytes(b"attacker-controlled replacement\n")
        os.replace(snapshot.original.path, moved)
        os.replace(replacement, snapshot.original.path)
        snapshot.original.path.unlink()
        os.replace(moved, snapshot.original.path)

    def factory(path: Path) -> ManagedReplayEngine:
        launched.append(path)
        return ManagedReplayEngine(path)

    summary = extractor._run_cli_extraction(
        source_root=source,
        output_path=output,
        extraction_receipt_path=receipt_path,
        engine_path=replay_engine_binding.path,
        expected_engine_sha256=replay_engine_binding.sha256,
        expected_execution_receipt_sha256=_receipt_sha(source),
        timeout=3.0,
        engine_context_factory=factory,
        before_launch_hook=replace_and_restore_original,
    )

    assert summary.positions == 8
    assert launched == snapshot_paths
    assert launched[0] != replay_engine_binding.path
    assert replay_engine_binding.sha256 in launched[0].name
    assert not launched[0].exists()
    assert not launched[0].parent.exists()
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    provenance = receipt["replay"]["engine"]
    assert provenance["policy"] == extractor.REPLAY_SNAPSHOT_POLICY
    assert {
        key: provenance["original"][key]
        for key in ("path", "sha256", "size_bytes")
    } == {
        "path": str(replay_engine_binding.path.absolute()),
        "sha256": replay_engine_binding.sha256,
        "size_bytes": replay_engine_binding.size_bytes,
    }
    assert provenance["original"]["verified_before_snapshot"] is True
    assert set(provenance["original"]["identity"]) == {
        "device",
        "inode",
        "size_bytes",
        "mtime_ns",
        "ctime_ns",
    }
    assert provenance["executed_snapshot"]["path"] == str(launched[0])
    assert provenance["snapshot"] == {
        "content_addressed": True,
        "exclusive_directory": True,
        "read_only_applied": True,
        "live_file_lock": True,
        "live_directory_namespace_seal": True,
        "path_reopened_identity_verified": True,
        "verified_before_launch": True,
        "verified_before_replay": True,
        "verified_after_replay": True,
        "verified_after_clean_close": True,
        "guard_closed_before_removal": True,
        "removed_after_clean_close": True,
    }
    assert provenance["process_cleanup_verified"] is True


def test_cli_snapshot_mutation_before_or_during_replay_publishes_nothing(
    tmp_path: Path,
    replay_engine_binding: extractor.ReplayEngineBinding,
) -> None:
    source, _rows = _publish_source(tmp_path / "source")
    before_factory_calls: list[Path] = []

    def mutate_before(snapshot: extractor.ReplayEngineSnapshot) -> None:
        os.chmod(snapshot.executed.path, 0o600)
        snapshot.executed.path.write_bytes(b"mutated before launch\n")

    with pytest.raises(
        extractor.E00ExtractionError,
        match="live guard|identity differs|hash or size differs|mode changed",
    ):
        extractor._run_cli_extraction(
            source_root=source,
            output_path=tmp_path / "before.jsonl",
            extraction_receipt_path=tmp_path / "before.receipt.json",
            engine_path=replay_engine_binding.path,
            expected_engine_sha256=replay_engine_binding.sha256,
            expected_execution_receipt_sha256=_receipt_sha(source),
            timeout=None,
            engine_context_factory=lambda path: (
                before_factory_calls.append(path) or ManagedReplayEngine(path)
            ),
            before_launch_hook=mutate_before,
        )
    assert before_factory_calls == []
    assert not (tmp_path / "before.jsonl").exists()
    assert not (tmp_path / "before.receipt.json").exists()

    launched: list[Path] = []

    def mutating_factory(path: Path) -> ManagedReplayEngine:
        launched.append(path)
        return ManagedReplayEngine(path, mutate_during_replay=True)

    with pytest.raises(
        extractor.E00ExtractionError,
        match="live guard|identity differs|hash or size differs|mode changed",
    ):
        extractor._run_cli_extraction(
            source_root=source,
            output_path=tmp_path / "during.jsonl",
            extraction_receipt_path=tmp_path / "during.receipt.json",
            engine_path=replay_engine_binding.path,
            expected_engine_sha256=replay_engine_binding.sha256,
            expected_execution_receipt_sha256=_receipt_sha(source),
            timeout=None,
            engine_context_factory=mutating_factory,
        )
    assert len(launched) == 1
    assert not (tmp_path / "during.jsonl").exists()
    assert not (tmp_path / "during.receipt.json").exists()


@pytest.mark.skipif(os.name != "nt", reason="Windows share-lock contract")
@pytest.mark.parametrize(
    "attack_name",
    ("write", "delete", "rename", "replace-and-restore"),
)
def test_cli_live_guard_blocks_transient_snapshot_attacks(
    tmp_path: Path,
    replay_engine_binding: extractor.ReplayEngineBinding,
    attack_name: str,
) -> None:
    source, _rows = _publish_source(tmp_path / f"source-{attack_name}")
    observed: list[extractor.ReplayEngineSnapshot] = []
    replacement = tmp_path / f"{attack_name}-replacement.bin"
    moved = tmp_path / f"{attack_name}-moved.bin"
    replacement.write_bytes(b"transient attacker replacement\n")

    def attack(snapshot: extractor.ReplayEngineSnapshot) -> None:
        observed.append(snapshot)
        if attack_name == "write":
            with snapshot.executed.path.open("r+b") as stream:
                stream.write(b"transient write")
        elif attack_name == "delete":
            snapshot.executed.path.unlink()
        elif attack_name == "rename":
            snapshot.executed.path.rename(
                snapshot.directory / "renamed-engine.bin"
            )
        else:
            # This sequence would leave the committed bytes and pathname
            # unchanged if transient replacement were possible.
            os.replace(snapshot.executed.path, moved)
            os.replace(replacement, snapshot.executed.path)
            snapshot.executed.path.unlink()
            os.replace(moved, snapshot.executed.path)

    output = tmp_path / f"{attack_name}.jsonl"
    receipt_path = tmp_path / f"{attack_name}.receipt.json"
    with pytest.raises(
        extractor.E00ExtractionError,
        match="live guard",
    ):
        extractor._run_cli_extraction(
            source_root=source,
            output_path=output,
            extraction_receipt_path=receipt_path,
            engine_path=replay_engine_binding.path,
            expected_engine_sha256=replay_engine_binding.sha256,
            expected_execution_receipt_sha256=_receipt_sha(source),
            timeout=None,
            engine_context_factory=ManagedReplayEngine,
            before_launch_hook=attack,
        )

    assert len(observed) == 1
    snapshot = observed[0]
    assert snapshot.guard.closed is True
    assert snapshot.executed.path.is_file()
    assert snapshot.executed.path.read_bytes() == (
        replay_engine_binding.path.read_bytes()
    )
    assert not output.exists()
    assert not receipt_path.exists()

    # Verification failures intentionally retain the snapshot. The test owns
    # this synthetic forensic residue and removes it after asserting retention.
    os.chmod(snapshot.executed.path, snapshot.mode | stat.S_IWUSR)
    snapshot.executed.path.unlink()
    snapshot.directory.rmdir()
    replacement.unlink(missing_ok=True)
    moved.unlink(missing_ok=True)


def test_cli_rejects_nonregular_and_symlink_engines_before_launch(
    tmp_path: Path,
    replay_engine_binding: extractor.ReplayEngineBinding,
) -> None:
    source, _rows = _publish_source(tmp_path / "source")
    launched: list[Path] = []
    directory = tmp_path / "not-an-engine"
    directory.mkdir()

    with pytest.raises(
        (extractor.E00ExtractionError, common.MiningArtifactError),
        match="regular|file",
    ):
        extractor._run_cli_extraction(
            source_root=source,
            output_path=tmp_path / "directory.jsonl",
            extraction_receipt_path=tmp_path / "directory.receipt.json",
            engine_path=directory,
            expected_engine_sha256="f" * 64,
            expected_execution_receipt_sha256=_receipt_sha(source),
            timeout=None,
            engine_context_factory=lambda path: (
                launched.append(path) or ManagedReplayEngine(path)
            ),
        )
    assert launched == []

    symlink = tmp_path / "engine-link.bin"
    try:
        symlink.symlink_to(replay_engine_binding.path)
    except OSError:
        pytest.skip("file symlinks are unavailable on this Windows host")
    with pytest.raises(extractor.E00ExtractionError, match="non-symlink regular"):
        extractor._run_cli_extraction(
            source_root=source,
            output_path=tmp_path / "symlink.jsonl",
            extraction_receipt_path=tmp_path / "symlink.receipt.json",
            engine_path=symlink,
            expected_engine_sha256=replay_engine_binding.sha256,
            expected_execution_receipt_sha256=_receipt_sha(source),
            timeout=None,
            engine_context_factory=lambda path: (
                launched.append(path) or ManagedReplayEngine(path)
            ),
        )
    assert launched == []


def test_cli_refuses_existing_outputs_and_bad_receipt_anchor_before_launch(
    tmp_path: Path,
    replay_engine_binding: extractor.ReplayEngineBinding,
) -> None:
    source, _rows = _publish_source(tmp_path / "source")
    launched: list[Path] = []
    output = tmp_path / "existing.jsonl"
    output.write_bytes(b"sentinel")

    with pytest.raises(FileExistsError, match="refusing to overwrite"):
        extractor._run_cli_extraction(
            source_root=source,
            output_path=output,
            extraction_receipt_path=tmp_path / "existing.receipt.json",
            engine_path=replay_engine_binding.path,
            expected_engine_sha256=replay_engine_binding.sha256,
            expected_execution_receipt_sha256=_receipt_sha(source),
            timeout=None,
            engine_context_factory=lambda path: (
                launched.append(path) or ManagedReplayEngine(path)
            ),
        )
    assert launched == []
    assert output.read_bytes() == b"sentinel"

    with pytest.raises(
        extractor.E00ExtractionError,
        match="caller trust anchor",
    ):
        extractor._run_cli_extraction(
            source_root=source,
            output_path=tmp_path / "bad-anchor.jsonl",
            extraction_receipt_path=tmp_path / "bad-anchor.receipt.json",
            engine_path=replay_engine_binding.path,
            expected_engine_sha256=replay_engine_binding.sha256,
            expected_execution_receipt_sha256="0" * 64,
            timeout=None,
            engine_context_factory=lambda path: (
                launched.append(path) or ManagedReplayEngine(path)
            ),
        )
    assert launched == []
    assert not (tmp_path / "bad-anchor.jsonl").exists()


def test_cli_rejects_factory_that_targets_mutable_original(
    tmp_path: Path,
    replay_engine_binding: extractor.ReplayEngineBinding,
) -> None:
    source, _rows = _publish_source(tmp_path / "source")

    def unsafe_factory(_snapshot_path: Path) -> ManagedReplayEngine:
        return ManagedReplayEngine(replay_engine_binding.path)

    with pytest.raises(
        extractor.E00ExtractionError,
        match="target exactly the authenticated snapshot",
    ):
        extractor._run_cli_extraction(
            source_root=source,
            output_path=tmp_path / "unsafe.jsonl",
            extraction_receipt_path=tmp_path / "unsafe.receipt.json",
            engine_path=replay_engine_binding.path,
            expected_engine_sha256=replay_engine_binding.sha256,
            expected_execution_receipt_sha256=_receipt_sha(source),
            timeout=None,
            engine_context_factory=unsafe_factory,
        )
    assert not (tmp_path / "unsafe.jsonl").exists()
    assert not (tmp_path / "unsafe.receipt.json").exists()


def test_cli_process_or_snapshot_cleanup_failure_publishes_nothing(
    tmp_path: Path,
    replay_engine_binding: extractor.ReplayEngineBinding,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source, _rows = _publish_source(tmp_path / "source")
    with pytest.raises(extractor.E00ExtractionError, match="clean close"):
        extractor._run_cli_extraction(
            source_root=source,
            output_path=tmp_path / "unclean.jsonl",
            extraction_receipt_path=tmp_path / "unclean.receipt.json",
            engine_path=replay_engine_binding.path,
            expected_engine_sha256=replay_engine_binding.sha256,
            expected_execution_receipt_sha256=_receipt_sha(source),
            timeout=None,
            engine_context_factory=lambda path: ManagedReplayEngine(
                path, clean_close=False
            ),
        )
    assert not (tmp_path / "unclean.jsonl").exists()
    assert not (tmp_path / "unclean.receipt.json").exists()

    def fail_snapshot_removal(
        _snapshot: extractor.ReplayEngineSnapshot,
    ) -> None:
        raise extractor.E00ExtractionError("synthetic snapshot cleanup failure")

    monkeypatch.setattr(
        extractor, "_remove_replay_engine_snapshot", fail_snapshot_removal
    )
    with pytest.raises(
        extractor.E00ExtractionError,
        match="snapshot cleanup failure",
    ):
        extractor._run_cli_extraction(
            source_root=source,
            output_path=tmp_path / "cleanup.jsonl",
            extraction_receipt_path=tmp_path / "cleanup.receipt.json",
            engine_path=replay_engine_binding.path,
            expected_engine_sha256=replay_engine_binding.sha256,
            expected_execution_receipt_sha256=_receipt_sha(source),
            timeout=None,
            engine_context_factory=ManagedReplayEngine,
        )
    assert not (tmp_path / "cleanup.jsonl").exists()
    assert not (tmp_path / "cleanup.receipt.json").exists()


def test_cli_live_guard_close_failure_retains_snapshot_and_publishes_nothing(
    tmp_path: Path,
    replay_engine_binding: extractor.ReplayEngineBinding,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source, _rows = _publish_source(tmp_path / "source")
    observed: list[extractor.ReplayEngineSnapshot] = []
    original_close = extractor._ReplaySnapshotGuard.close

    def capture(snapshot: extractor.ReplayEngineSnapshot) -> None:
        observed.append(snapshot)

    def fail_after_real_close(
        guard: extractor._ReplaySnapshotGuard,
    ) -> None:
        original_close(guard)
        raise extractor.E00ExtractionError(
            "synthetic live guard close failure"
        )

    monkeypatch.setattr(
        extractor._ReplaySnapshotGuard, "close", fail_after_real_close
    )
    output = tmp_path / "guard-close.jsonl"
    receipt_path = tmp_path / "guard-close.receipt.json"
    with pytest.raises(
        extractor.E00ExtractionError,
        match="live guard close failure",
    ):
        extractor._run_cli_extraction(
            source_root=source,
            output_path=output,
            extraction_receipt_path=receipt_path,
            engine_path=replay_engine_binding.path,
            expected_engine_sha256=replay_engine_binding.sha256,
            expected_execution_receipt_sha256=_receipt_sha(source),
            timeout=None,
            engine_context_factory=ManagedReplayEngine,
            before_launch_hook=capture,
        )

    assert len(observed) == 1
    snapshot = observed[0]
    assert snapshot.guard.closed is True
    assert snapshot.executed.path.is_file()
    assert snapshot.directory.is_dir()
    assert not output.exists()
    assert not receipt_path.exists()

    os.chmod(snapshot.executed.path, snapshot.mode | stat.S_IWUSR)
    snapshot.executed.path.unlink()
    snapshot.directory.rmdir()


def test_deep_input_snapshot_tamper_fails_before_replay(
    tmp_path: Path,
    replay_engine_binding: extractor.ReplayEngineBinding,
) -> None:
    source, _rows = _publish_source(tmp_path / "source")
    receipt = json.loads((source / "receipt.json").read_text(encoding="utf-8"))
    engine_snapshot = Path(receipt["input_snapshots"]["engine"]["path"])
    os.chmod(engine_snapshot, stat.S_IREAD | stat.S_IWRITE)
    engine_snapshot.write_bytes(b"tampered committed engine snapshot\n")
    engine = FakeReplayEngine()

    with pytest.raises(
        extractor.E00ExtractionError,
        match="snapshot engine bytes or immutable identity differ",
    ):
        extractor.extract_e00_positions(
            source,
            tmp_path / "tampered-source.jsonl",
            tmp_path / "tampered-source.receipt.json",
            engine,
            replay_engine_binding=replay_engine_binding,
            expected_execution_receipt_sha256=_receipt_sha(source),
        )
    assert engine.inspect_calls == []
    assert not (tmp_path / "tampered-source.jsonl").exists()


def test_v3_extractor_rejects_v2_and_mixed_wire_contracts_before_replay(
    tmp_path: Path,
    replay_engine_binding: extractor.ReplayEngineBinding,
) -> None:
    engine = FakeReplayEngine()

    receipt_v2, _rows = _publish_source(tmp_path / "receipt-v2")

    def downgrade_receipt(receipt: dict[str, object]) -> None:
        receipt["schema"] = "atomic-e00-execution-receipt-v2"

    _rewrite_receipt(receipt_v2, downgrade_receipt)
    with pytest.raises(
        extractor.E00ExtractionError,
        match="execution-receipt-v3 only",
    ):
        extractor.extract_e00_positions(
            receipt_v2,
            tmp_path / "receipt-v2.positions.jsonl",
            tmp_path / "receipt-v2.extraction.json",
            engine,
            replay_engine_binding=replay_engine_binding,
            expected_execution_receipt_sha256=_receipt_sha(receipt_v2),
        )

    mixed_runtime, _rows = _publish_source(
        tmp_path / "mixed-runtime",
        runtime_manifest_schema="atomic-e00-runtime-manifest-v1",
    )
    with pytest.raises(
        extractor.E00ExtractionError,
        match="runtime-manifest-v2 exactly",
    ):
        extractor.extract_e00_positions(
            mixed_runtime,
            tmp_path / "mixed-runtime.positions.jsonl",
            tmp_path / "mixed-runtime.extraction.json",
            engine,
            replay_engine_binding=replay_engine_binding,
            expected_execution_receipt_sha256=_receipt_sha(mixed_runtime),
        )

    v2_games = [_game_row(0), _game_row(1)]
    for row in v2_games:
        row["schema"] = "atomic-e00-game-v2"
    mixed_game, _rows = _publish_source(
        tmp_path / "mixed-game",
        games=v2_games,
    )
    with pytest.raises(extractor.E00ExtractionError, match="schema differs"):
        extractor.extract_e00_positions(
            mixed_game,
            tmp_path / "mixed-game.positions.jsonl",
            tmp_path / "mixed-game.extraction.json",
            engine,
            replay_engine_binding=replay_engine_binding,
            expected_execution_receipt_sha256=_receipt_sha(mixed_game),
        )

    mixed_evidence, _rows = _publish_source(
        tmp_path / "mixed-evidence",
        engine_evidence_schema="atomic-e00-engine-evidence-v2",
    )
    with pytest.raises(extractor.E00ExtractionError, match="schema differs"):
        extractor.extract_e00_positions(
            mixed_evidence,
            tmp_path / "mixed-evidence.positions.jsonl",
            tmp_path / "mixed-evidence.extraction.json",
            engine,
            replay_engine_binding=replay_engine_binding,
            expected_execution_receipt_sha256=_receipt_sha(mixed_evidence),
        )

    assert engine.inspect_calls == []


def test_v2_runtime_package_and_import_inventory_are_exact_before_replay(
    tmp_path: Path,
    replay_engine_binding: extractor.ReplayEngineBinding,
) -> None:
    engine = FakeReplayEngine()
    tampered, _rows = _publish_source(tmp_path / "runtime-tamper")
    receipt = json.loads(
        (tampered / "receipt.json").read_text(encoding="utf-8")
    )
    runtime_module = Path(
        receipt["executed_runtime_package"]["common"]["path"]
    )
    os.chmod(runtime_module, stat.S_IREAD | stat.S_IWRITE)
    runtime_module.write_bytes(b"tampered executed runtime package\n")
    with pytest.raises(
        extractor.E00ExtractionError,
        match="executed runtime module common bytes or immutable identity differ",
    ):
        extractor.extract_e00_positions(
            tampered,
            tmp_path / "runtime-tamper.positions.jsonl",
            tmp_path / "runtime-tamper.extraction.json",
            engine,
            replay_engine_binding=replay_engine_binding,
            expected_execution_receipt_sha256=_receipt_sha(tampered),
        )

    incomplete, _rows = _publish_source(
        tmp_path / "runtime-incomplete",
        omit_runtime_import_key="common",
    )
    with pytest.raises(
        extractor.E00ExtractionError,
        match="runtime-package import inventory is incomplete",
    ):
        extractor.extract_e00_positions(
            incomplete,
            tmp_path / "runtime-incomplete.positions.jsonl",
            tmp_path / "runtime-incomplete.extraction.json",
            engine,
            replay_engine_binding=replay_engine_binding,
            expected_execution_receipt_sha256=_receipt_sha(incomplete),
        )
    assert engine.inspect_calls == []


def test_v2_schedule_book_and_native_identity_links_fail_closed(
    tmp_path: Path,
    replay_engine_binding: extractor.ReplayEngineBinding,
) -> None:
    engine = FakeReplayEngine()
    schedule_drift, _rows = _publish_source(tmp_path / "schedule-drift")

    def drift_schedule(receipt: dict[str, object]) -> None:
        receipt["schedule"]["size_bytes"] += 1

    _rewrite_receipt(schedule_drift, drift_schedule)
    with pytest.raises(
        extractor.E00ExtractionError,
        match="schedule header differs from bound inputs",
    ):
        extractor.extract_e00_positions(
            schedule_drift,
            tmp_path / "schedule-drift.positions.jsonl",
            tmp_path / "schedule-drift.extraction.json",
            engine,
            replay_engine_binding=replay_engine_binding,
            expected_execution_receipt_sha256=_receipt_sha(schedule_drift),
        )

    book_rows = [_game_row(0), _game_row(1)]
    for row in book_rows:
        row["book_sha256"] = "0" * 64
    book_drift, _rows = _publish_source(
        tmp_path / "book-drift", games=book_rows
    )
    with pytest.raises(
        extractor.E00ExtractionError,
        match="book_sha256 differs from receipt input",
    ):
        extractor.extract_e00_positions(
            book_drift,
            tmp_path / "book-drift.positions.jsonl",
            tmp_path / "book-drift.extraction.json",
            engine,
            replay_engine_binding=replay_engine_binding,
            expected_execution_receipt_sha256=_receipt_sha(book_drift),
        )

    native_drift, _rows = _publish_source(tmp_path / "native-drift")

    def drift_native_identity(receipt: dict[str, object]) -> None:
        identity = receipt["inputs"]["pyffish"]["identity"]
        field = (
            "file_index"
            if "file_index" in identity
            else "inode"
        )
        identity[field] += 1

    _rewrite_receipt(native_drift, drift_native_identity)
    with pytest.raises(
        extractor.E00ExtractionError,
        match="native build does not bind core artifact pyffish",
    ):
        extractor.extract_e00_positions(
            native_drift,
            tmp_path / "native-drift.positions.jsonl",
            tmp_path / "native-drift.extraction.json",
            engine,
            replay_engine_binding=replay_engine_binding,
            expected_execution_receipt_sha256=_receipt_sha(native_drift),
        )
    assert engine.inspect_calls == []


@pytest.mark.parametrize("drift", ("namespace", "trust-boundary"))
def test_v2_namespace_and_trust_boundary_drift_fail_before_replay(
    tmp_path: Path,
    replay_engine_binding: extractor.ReplayEngineBinding,
    drift: str,
) -> None:
    source, _rows = _publish_source(tmp_path / drift)

    def mutate(receipt: dict[str, object]) -> None:
        if drift == "namespace":
            receipt["namespace_guards"]["native_build"][
                "enumeration"
            ].append(
                {
                    "path": "uncommitted.dll",
                    "kind": "file",
                    "device": 0,
                    "inode": 0,
                    "size_bytes": 1,
                }
            )
        else:
            receipt["trust_boundary"]["namespace_policy"] = "weaker-policy"

    _rewrite_receipt(source, mutate)
    engine = FakeReplayEngine()
    with pytest.raises(
        extractor.E00ExtractionError,
        match=(
            "namespace guard native_build enumeration differs"
            if drift == "namespace"
            else "trust-boundary declaration differs"
        ),
    ):
        extractor.extract_e00_positions(
            source,
            tmp_path / f"{drift}.positions.jsonl",
            tmp_path / f"{drift}.extraction.json",
            engine,
            replay_engine_binding=replay_engine_binding,
            expected_execution_receipt_sha256=_receipt_sha(source),
        )
    assert engine.inspect_calls == []


def test_cli_requires_independent_execution_receipt_sha256() -> None:
    base = [
        "--source-root",
        "source",
        "--output",
        "positions.jsonl",
        "--receipt",
        "positions.receipt.json",
        "--engine",
        "engine.exe",
        "--engine-sha256",
        "0" * 64,
    ]
    with pytest.raises(SystemExit):
        extractor._parser().parse_args(base)
    parsed = extractor._parser().parse_args(
        [
            *base,
            "--execution-receipt-sha256",
            "1" * 64,
        ]
    )
    assert parsed.execution_receipt_sha256 == "1" * 64
