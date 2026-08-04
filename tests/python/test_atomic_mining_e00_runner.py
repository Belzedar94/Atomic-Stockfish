from __future__ import annotations

import copy
import inspect
import json
from pathlib import Path
import subprocess
import sys
from types import ModuleType, SimpleNamespace
from typing import Mapping

import pytest

from tools.atomic_mining import build_e00_schedule as schedule
from tools.atomic_mining import common
from tools.atomic_mining import run_e00_source as runner


REPO_ROOT = Path(__file__).resolve().parents[2]
START = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"
UCI_OPTIONS = (
    ("UCI_Variant", "atomic"),
    ("Threads", 1),
    ("Hash", 512),
    ("MultiPV", 1),
    ("Ponder", False),
    ("SyzygyPath", ""),
    ("SyzygyProbeLimit", 0),
    ("Use NNUE", "true"),
)
CLOCK_POLICY = {
    "charged_interval": (
        "complete-go-write-flush-to-complete-bestmove-newline"
    ),
    "equality": "elapsed-ns-equal-remaining-ns-is-on-time",
    "uci_millisecond_conversion": "floor-nanoseconds",
}


def test_success_summary_stdout_is_canonical_lf_in_real_child() -> None:
    """Exercise the Windows text-translation boundary in a fresh process."""

    script = (
        "from tools.atomic_mining.run_e00_source "
        "import _write_success_summary;"
        "_write_success_summary({'schema':'probe','status':'ok'})"
    )
    completed = subprocess.run(
        [sys.executable, "-B", "-c", script],
        cwd=REPO_ROOT,
        capture_output=True,
        timeout=30,
    )
    assert completed.returncode == 0, completed.stderr.decode(
        "utf-8", errors="replace"
    )
    assert completed.stdout == b'{"schema":"probe","status":"ok"}\n'
    assert b"\r" not in completed.stdout
    assert completed.stderr == b""


def test_runner_main_uses_binary_success_summary_writer() -> None:
    source = inspect.getsource(runner.main)
    assert "_write_success_summary(summary.__dict__)" in source
    assert "print(common.canonical_json_line" not in source
    writer = inspect.getsource(runner._write_success_summary)
    assert "common.canonical_json_bytes(value)" in writer
    assert "sys.stdout.buffer.write(payload)" in writer
    assert "written != len(payload)" in writer
    assert "sys.stdout.buffer.flush()" in writer


def _sha(path: Path) -> str:
    return common.sha256_file(path)


def _read_json(path: Path) -> dict[str, object]:
    value = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def _module_path(module: object) -> Path:
    value = getattr(module, "__file__", None)
    assert isinstance(value, str)
    return Path(value).absolute()


def _publish_schedule(
    root: Path, *, pairs: int = 1
) -> tuple[Path, Path, Path]:
    root.mkdir(parents=True)
    book = root / "book.epd"
    positions = [
        START.replace(" 0 1", f" {index} {index + 1}")
        for index in range(1, pairs + 3)
    ]
    book.write_text(
        "\n".join(positions) + "\n", encoding="utf-8", newline="\n"
    )
    schedule_path = root / "schedule.jsonl"
    receipt_path = root / "schedule.receipt.json"
    schedule.build_schedule_file(
        book,
        seed="e00-v2-stub-suite",
        time_controls=[
            schedule.TimeControl("VSTC", 2_000, 20, pairs),
        ],
        output_path=schedule_path,
        receipt_path=receipt_path,
    )
    return schedule_path, receipt_path, book


def _runtime_package_sources() -> dict[str, Path]:
    tools_module = sys.modules["tools"]
    mining_module = sys.modules["tools.atomic_mining"]
    return {
        "tools_init": _module_path(tools_module),
        "atomic_mining_init": _module_path(mining_module),
        "common": _module_path(common),
        "schedule_builder": _module_path(schedule),
        "runner": _module_path(runner),
        "uci_session": _module_path(runner.uci_session),
        "atomic_outcome_helper": _module_path(
            runner.atomic_outcome_helper
        ),
        "binding_builder": _module_path(
            runner.build_atomic_outcome_binding
        ),
        "owned_process": _module_path(runner.owned_process),
    }


def _child_import_inventory(
    runtime_sources: Mapping[str, Path], pyffish: Path
) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = [
        {
            "source": "pyffish",
            "path": "pyffish",
            "sha256": _sha(pyffish),
            "size_bytes": pyffish.stat().st_size,
            "module_names": ["pyffish"],
        }
    ]
    for key, relative in runner._RUNTIME_PACKAGE_LAYOUT.items():
        source = runtime_sources[key]
        rows.append(
            {
                "source": "runtime-package",
                "path": relative.as_posix(),
                "sha256": _sha(source),
                "size_bytes": source.stat().st_size,
                "module_names": [f"sealed_runtime.{key}"],
            }
        )
    return sorted(rows, key=lambda row: (str(row["source"]), str(row["path"])))


class StubRuntimeProbe:
    def __init__(
        self, contract: Mapping[str, object], *, drift_after: int | None = None
    ) -> None:
        self.contract = dict(contract)
        self.drift_after = drift_after
        self.calls = 0

    def capture(self) -> Mapping[str, object]:
        self.calls += 1
        value = copy.deepcopy(self.contract)
        if self.drift_after is not None and self.calls >= self.drift_after:
            value["source"]["tree"] = "d" * 40  # type: ignore[index]
        return value


class StubBackend:
    def __init__(self, *, fail_call: int | None = None) -> None:
        self.fail_call = fail_call
        self.calls: list[runner.LegRequest] = []

    def play(self, request: runner.LegRequest) -> runner.GameExecution:
        self.calls.append(request)
        if self.fail_call == len(self.calls):
            raise RuntimeError("synthetic stub backend failure")
        return runner.GameExecution(
            result_white="1-0",
            time_loss=False,
            terminal_reason="stub-terminal",
            moves=("e2e4",),
            stdout=f"stub-leg-{request.leg}\n".encode("ascii"),
            stderr=b"",
            engine_evidence={},
            referee_evidence={},
            process_evidence={},
            verifier_evidence={},
        )


class StubVerifier:
    def __init__(self) -> None:
        self.calls: list[runner.LegRequest] = []

    def verify(
        self,
        request: runner.LegRequest,
        _execution: runner.GameExecution,
    ) -> Mapping[str, object]:
        self.calls.append(request)
        return {"stub-independent-verifier": True}


def _prepared_invocation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    pairs: int = 1,
    backend: StubBackend | None = None,
    probe: StubRuntimeProbe | None = None,
) -> tuple[
    Path,
    Path,
    Path,
    dict[str, object],
    StubBackend,
    StubVerifier,
    StubRuntimeProbe,
]:
    schedule_path, receipt_path, book = _publish_schedule(
        tmp_path / "schedule-source", pairs=pairs
    )
    native_build_root = tmp_path / "native-build"
    native_build_root.mkdir()
    binaries: dict[str, Path] = {}
    for name in (
        "engine.exe",
        "current.nnue",
        "teacher.nnue",
        "variants.ini",
        "pyffish.pyd",
    ):
        path = (
            native_build_root / name
            if name == "pyffish.pyd"
            else tmp_path / name
        )
        path.write_bytes(f"sealed-{name}".encode("ascii"))
        binaries[name] = path
    build_manifest = native_build_root / "pyffish-build.json"
    common.write_new_json(build_manifest, {"stub": "sealed"})

    runtime_sources = _runtime_package_sources()
    inventory = _child_import_inventory(
        runtime_sources, binaries["pyffish.pyd"]
    )
    executable = Path(sys.executable).absolute()
    executable_bytes = common.read_stable_file_bytes(
        executable, label="stub Python executable"
    )
    runtime = {
        "python": {
            "executable": str(executable),
            "executable_sha256": common.sha256_bytes(executable_bytes),
            "executable_size_bytes": len(executable_bytes),
            "runtime_libraries": (
                runner.atomic_outcome_helper._python_runtime_artifacts()
            ),
            "child_import_inventory": inventory,
            "version": sys.version,
        },
        "modules": {
            key: {
                "path": str(path),
                "sha256": _sha(path),
                "size_bytes": path.stat().st_size,
            }
            for key, path in sorted(runtime_sources.items())
        },
        "native_rules": {"stub": "precommitted"},
        "source": {
            "root": str(REPO_ROOT),
            "commit": "a" * 40,
            "tree": "b" * 40,
            "clean": True,
        },
    }

    initial_paths = {
        **runtime_sources,
        "book": book,
        "engine": binaries["engine.exe"],
        "current_net": binaries["current.nnue"],
        "teacher_net": binaries["teacher.nnue"],
        "variant_config": binaries["variants.ini"],
        "pyffish": binaries["pyffish.pyd"],
        "pyffish_build_manifest": build_manifest,
        "schedule": schedule_path,
        "schedule_receipt": receipt_path,
    }
    execution = {
        "threads": 1,
        "maximum_plies": 32,
        "command_timeout_seconds": 5.0,
        "maximum_wall_seconds": 300.0,
        "maximum_game_wall_seconds": 60.0,
        "uci_options": [
            {"name": name, "value": value}
            for name, value in UCI_OPTIONS
        ],
        "clock_policy": CLOCK_POLICY,
    }
    runtime_manifest = tmp_path / "runtime-manifest.json"
    common.write_new_json(
        runtime_manifest,
        {
            "schema": runner.RUNTIME_MANIFEST_SCHEMA,
            "runtime": runtime,
            "execution": execution,
            "inputs": {
                key: _sha(path)
                for key, path in sorted(initial_paths.items())
            },
        },
    )

    selected_backend = backend or StubBackend()
    verifier = StubVerifier()
    selected_probe = probe or StubRuntimeProbe(runtime)
    arguments: dict[str, object] = {
        "experiment_id": "atomic-e00-v2-stub",
        "battery_id": "source-runner-stub",
        "book": book,
        "book_sha256": _sha(book),
        "engine": binaries["engine.exe"],
        "engine_sha256": _sha(binaries["engine.exe"]),
        "current_net": binaries["current.nnue"],
        "current_net_sha256": _sha(binaries["current.nnue"]),
        "teacher_net": binaries["teacher.nnue"],
        "teacher_net_sha256": _sha(binaries["teacher.nnue"]),
        "variant_config": binaries["variants.ini"],
        "variant_config_sha256": _sha(binaries["variants.ini"]),
        "pyffish": binaries["pyffish.pyd"],
        "pyffish_sha256": _sha(binaries["pyffish.pyd"]),
        "pyffish_build_manifest": build_manifest,
        "pyffish_build_manifest_sha256": _sha(build_manifest),
        "rules_source_root": REPO_ROOT,
        "rules_source_commit": "a" * 40,
        "runner": runtime_sources["runner"],
        "runner_sha256": _sha(runtime_sources["runner"]),
        "tools_init_sha256": _sha(runtime_sources["tools_init"]),
        "atomic_mining_init_sha256": _sha(
            runtime_sources["atomic_mining_init"]
        ),
        "common_sha256": _sha(runtime_sources["common"]),
        "schedule_builder_sha256": _sha(
            runtime_sources["schedule_builder"]
        ),
        "uci_session_sha256": _sha(runtime_sources["uci_session"]),
        "atomic_outcome_helper_sha256": _sha(
            runtime_sources["atomic_outcome_helper"]
        ),
        "binding_builder_sha256": _sha(
            runtime_sources["binding_builder"]
        ),
        "owned_process_sha256": _sha(
            runtime_sources["owned_process"]
        ),
        "runtime_manifest": runtime_manifest,
        "runtime_manifest_sha256": _sha(runtime_manifest),
        "backend": selected_backend,
        "semantic_verifier": verifier,
        "runtime_probe": selected_probe,
        "threads": 1,
        "maximum_plies": 32,
        "command_timeout_seconds": 5.0,
        "maximum_wall_seconds": 300.0,
        "maximum_game_wall_seconds": 60.0,
    }
    monkeypatch.setattr(
        runner.atomic_outcome_helper,
        "_load_build_manifest",
        lambda *_args, **_kwargs: {"stub": "authenticated"},
    )
    monkeypatch.setattr(
        runner, "_validate_execution", lambda *_args, **_kwargs: None
    )
    return (
        schedule_path,
        receipt_path,
        tmp_path / "output",
        arguments,
        selected_backend,
        verifier,
        selected_probe,
    )


def _invoke(
    prepared: tuple[
        Path,
        Path,
        Path,
        dict[str, object],
        StubBackend,
        StubVerifier,
        StubRuntimeProbe,
    ]
) -> runner.BatterySummary:
    schedule_path, receipt_path, output, arguments, *_rest = prepared
    return runner.run_battery(
        schedule_path,
        receipt_path,
        output,
        **arguments,  # type: ignore[arg-type]
    )


def test_runner_source_has_no_python_chess_execution_surface() -> None:
    source = _module_path(runner).read_text(encoding="utf-8")
    forbidden = (
        "import chess",
        "SimpleEngine",
        "AtomicBoard",
        "PythonChessBackend",
    )
    assert not any(token in source for token in forbidden)
    assert "alter_sys=True" in runner._ISOLATED_BOOTSTRAP


def test_isolated_child_uses_explicit_no_bytecode_flag(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    observed: list[str] = []

    class LaunchCaptured(RuntimeError):
        pass

    def capture_launch(
        _cls: type[object], command: list[str], **_kwargs: object
    ) -> None:
        observed.extend(command)
        raise LaunchCaptured

    monkeypatch.setattr(
        runner.owned_process.OwnedProcess,
        "launch",
        classmethod(capture_launch),
    )
    with pytest.raises(LaunchCaptured):
        runner.DirectUciBackend()._run_child(
            mode="--internal-discover-runtime",
            request_wire={"runtime_package_root": str(tmp_path)},
            result_label="no-bytecode command probe",
            deadline_seconds=5.0,
        )

    assert observed[:4] == [sys.executable, "-I", "-B", "-c"]


def test_real_isolated_child_does_not_mutate_fresh_runtime_package(
    tmp_path: Path,
) -> None:
    runtime_root = tmp_path / "runtime-package"
    for key, relative in runner._RUNTIME_PACKAGE_LAYOUT.items():
        destination = runtime_root / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(_runtime_package_sources()[key].read_bytes())

    before = runner._directory_inventory(runtime_root)
    with pytest.raises(
        runner.E00RunnerError,
        match=(
            "runtime import-only probe wrapper failed at decode-request "
            "\\[artifact-contract\\] with code 70"
        ),
    ):
        runner.DirectUciBackend()._run_child(
            mode="--internal-discover-runtime",
            request_wire={"runtime_package_root": str(runtime_root)},
            result_label="runtime import-only probe",
            deadline_seconds=30.0,
        )

    assert runner._directory_inventory(runtime_root) == before
    assert not list(runtime_root.rglob("__pycache__"))
    assert not list(runtime_root.rglob("*.pyc"))


def test_internal_failure_wire_is_classified_and_does_not_leak_message(
    tmp_path: Path,
) -> None:
    request_path = tmp_path / "request.json"
    result_path = tmp_path / "result.json"
    common.write_new_json(request_path, {"request": "bound"})
    secret = "C:/secret/operator/path"

    assert (
        runner._write_internal_failure(
            mode="play-leg",
            stage="play-direct",
            request_path=request_path,
            result_path=result_path,
            error=runner.uci_session.UciProtocolError(secret),
        )
        == 70
    )

    failure_path = tmp_path / "failure.json"
    failure = _read_json(failure_path)
    assert failure == {
        "schema": runner.INTERNAL_FAILURE_SCHEMA,
        "mode": "play-leg",
        "stage": "play-direct",
        "failure_code": "uci-protocol",
        "exception_type": "UciProtocolError",
        "request_sha256": _sha(request_path),
    }
    assert secret not in failure_path.read_text(encoding="utf-8")
    assert not result_path.exists()
    assert (
        runner._validate_internal_failure(
            failure,
            expected_mode="play-leg",
            expected_request_sha256=_sha(request_path),
        )
        == failure
    )


@pytest.mark.parametrize(
    ("field", "value", "message"),
    (
        ("schema", "atomic-e00-internal-failure-v0", "schema differs"),
        ("mode", "verify-game", "mode differs"),
        (
            "stage",
            "verify-game",
            "stage is malformed or not valid for mode",
        ),
        (
            "failure_code",
            ["uci-protocol"],
            "failure code is not allowlisted",
        ),
        ("request_sha256", "f" * 64, "request binding differs"),
    ),
)
def test_internal_failure_wire_rejects_drift(
    field: str,
    value: object,
    message: str,
) -> None:
    failure: dict[str, object] = {
        "schema": runner.INTERNAL_FAILURE_SCHEMA,
        "mode": "play-leg",
        "stage": "play-direct",
        "failure_code": "uci-protocol",
        "exception_type": "UciProtocolError",
        "request_sha256": "a" * 64,
    }
    failure[field] = value
    with pytest.raises(runner.E00RunnerError, match=message):
        runner._validate_internal_failure(
            failure,
            expected_mode="play-leg",
            expected_request_sha256="a" * 64,
        )


def test_isolated_child_result_failure_exclusivity_is_fail_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    valid_process = {
        "schema": "atomic-e00-owned-process-v1",
        "containment": "windows-job-object",
        "kill_on_close": True,
        "created_suspended_before_assignment": True,
        "resumed_primary_thread": True,
        "active_processes_zero": True,
        "termination_requested": False,
    }

    class FakeOwner:
        def __init__(
            self,
            *,
            return_code: int,
            process_evidence: Mapping[str, object],
        ) -> None:
            self.process = SimpleNamespace(
                wait=lambda **_kwargs: return_code
            )
            self._process_evidence = dict(process_evidence)

        def prove_natural_zero(self, **_kwargs: object) -> None:
            return None

        def evidence(self) -> SimpleNamespace:
            return SimpleNamespace(
                payload=lambda: dict(self._process_evidence)
            )

        def close(self) -> None:
            return None

    def invoke(
        *,
        return_code: int,
        publish_result: bool = False,
        publish_failure: bool = False,
        noncanonical_failure: bool = False,
        process_evidence: Mapping[str, object] = valid_process,
    ) -> object:
        def launch(
            _cls: type[object],
            command: list[str],
            **_kwargs: object,
        ) -> FakeOwner:
            request_path = Path(command[-2])
            result_path = Path(command[-1])
            if publish_result:
                common.write_new_json(result_path, {"unexpected": True})
            if publish_failure:
                failure = {
                    "schema": runner.INTERNAL_FAILURE_SCHEMA,
                    "mode": "discover-runtime",
                    "stage": "discover-runtime",
                    "failure_code": "artifact-contract",
                    "exception_type": "MiningArtifactError",
                    "request_sha256": _sha(request_path),
                }
                failure_path = result_path.with_name("failure.json")
                if noncanonical_failure:
                    failure_path.write_bytes(
                        json.dumps(failure, indent=2).encode("utf-8")
                    )
                else:
                    common.write_new_json(failure_path, failure)
            return FakeOwner(
                return_code=return_code,
                process_evidence=process_evidence,
            )

        monkeypatch.setattr(
            runner.owned_process.OwnedProcess,
            "launch",
            classmethod(launch),
        )
        return runner.DirectUciBackend()._run_child(
            mode="--internal-discover-runtime",
            request_wire={"runtime_package_root": str(tmp_path)},
            result_label="synthetic child",
            deadline_seconds=5.0,
        )

    with pytest.raises(
        runner.E00ChildFailure,
        match=(
            "synthetic child wrapper failed at discover-runtime "
            "\\[artifact-contract\\] with code 70"
        ),
    ):
        invoke(return_code=70, publish_failure=True)

    for kwargs, message in (
        (
            {
                "return_code": 70,
                "publish_result": True,
                "publish_failure": True,
            },
            "unclassified failure",
        ),
        (
            {"return_code": 70},
            "unclassified failure",
        ),
        (
            {"return_code": 0, "publish_failure": True},
            "published a failure on success",
        ),
        (
            {
                "return_code": 70,
                "publish_failure": True,
                "noncanonical_failure": True,
            },
            "canonical",
        ),
    ):
        with pytest.raises(runner.E00RunnerError, match=message):
            invoke(**kwargs)

    drifted_process = dict(valid_process)
    drifted_process["active_processes_zero"] = False
    with pytest.raises(
        runner.E00RunnerError,
        match="did not prove active-process-zero",
    ):
        invoke(
            return_code=70,
            publish_failure=True,
            process_evidence=drifted_process,
        )


def test_child_import_inventory_requires_canonical_order_and_unique_modules(
    tmp_path: Path,
) -> None:
    first = tmp_path / "a.py"
    second = tmp_path / "b.py"
    first.write_bytes(b"a")
    second.write_bytes(b"b")
    rows = [
        {
            "source": "python-installation",
            "path": str(first),
            "sha256": _sha(first),
            "size_bytes": 1,
            "module_names": ["a"],
        },
        {
            "source": "python-installation",
            "path": str(second),
            "sha256": _sha(second),
            "size_bytes": 1,
            "module_names": ["b"],
        },
    ]
    assert runner._validate_child_import_inventory(
        rows, label="test inventory"
    ) == rows
    with pytest.raises(runner.E00RunnerError, match="not sorted"):
        runner._validate_child_import_inventory(
            list(reversed(rows)), label="test inventory"
        )
    duplicate = copy.deepcopy(rows)
    duplicate[1]["module_names"] = ["a"]
    with pytest.raises(runner.E00RunnerError, match="duplicates module"):
        runner._validate_child_import_inventory(
            duplicate, label="test inventory"
        )


def test_stub_battery_commits_paired_rows_and_explicit_trust_boundary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    prepared = _prepared_invocation(
        tmp_path, monkeypatch, pairs=2
    )
    summary = _invoke(prepared)
    _schedule, _receipt, output, _arguments, backend, verifier, probe = (
        prepared
    )
    assert summary.pairs == 2
    assert summary.games == 4
    assert (summary.wins_current, summary.losses_current) == (2, 2)
    assert [request.leg for request in backend.calls] == [0, 1, 0, 1]
    assert [request.leg for request in verifier.calls] == [0, 1, 0, 1]
    assert probe.calls == 2
    receipt = _read_json(output / "receipt.json")
    assert receipt["status"] == "committed"
    assert receipt["trust_boundary"]["hermetic"] is False
    assert receipt["execution"]["retry_policy"] == "none"
    assert receipt["reconciliation"]["all_ids_recomputed"] is True
    assert receipt["namespace_guards"]["snapshots"]["enumeration"]
    assert receipt["namespace_guards"]["runtime_package"]["enumeration"]
    assert common.sha256_file(output / "games.jsonl") == (
        summary.games_sha256
    )
    assert common.sha256_file(output / "receipt.json") == (
        summary.receipt_sha256
    )


def test_success_cleanup_failure_never_publishes_commit_receipt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    prepared = _prepared_invocation(tmp_path, monkeypatch)
    close_all_guard_scopes = runner._close_all_guard_scopes

    def close_then_fail_top_level(
        file_guards: object, directory_guards: object
    ) -> None:
        close_all_guard_scopes(file_guards, directory_guards)  # type: ignore[arg-type]
        if any(
            guard.label == "E00 output namespace"
            for guard in directory_guards  # type: ignore[union-attr]
        ):
            raise runner.E00RunnerError(
                "synthetic successful top-level cleanup failure"
            )

    monkeypatch.setattr(
        runner, "_close_all_guard_scopes", close_then_fail_top_level
    )
    with pytest.raises(
        runner.E00RunnerError,
        match="synthetic successful top-level cleanup failure",
    ):
        _invoke(prepared)
    assert not (prepared[2] / "receipt.json").exists()


def test_success_registry_leak_never_publishes_commit_receipt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    prepared = _prepared_invocation(tmp_path, monkeypatch)
    close_all_guard_scopes = runner._close_all_guard_scopes

    def close_then_leak_top_level(
        file_guards: object, directory_guards: object
    ) -> None:
        close_all_guard_scopes(file_guards, directory_guards)  # type: ignore[arg-type]
        if any(
            guard.label == "E00 output namespace"
            for guard in directory_guards  # type: ignore[union-attr]
        ):
            registry = runner._ACTIVE_WINDOWS_DIRECTORY_LOCKS.get()
            assert registry is not None
            registry[("synthetic", 1)] = object()  # type: ignore[index,assignment]

    monkeypatch.setattr(
        runner, "_close_all_guard_scopes", close_then_leak_top_level
    )
    with pytest.raises(
        runner.E00RunnerError,
        match="shared Windows namespace lock registry leaked",
    ):
        _invoke(prepared)
    assert not (prepared[2] / "receipt.json").exists()


def test_existing_output_is_rejected_before_backend_use(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    prepared = _prepared_invocation(tmp_path, monkeypatch)
    output = prepared[2]
    output.mkdir()
    with pytest.raises(FileExistsError, match="refusing to reuse"):
        _invoke(prepared)
    assert prepared[4].calls == []


def test_pair_failure_is_terminal_without_retry_or_receipt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    backend = StubBackend(fail_call=2)
    prepared = _prepared_invocation(
        tmp_path, monkeypatch, backend=backend
    )
    with pytest.raises(runner.E00RunnerError, match="battery aborted"):
        _invoke(prepared)
    output = prepared[2]
    assert len(backend.calls) == 2
    assert not (output / "receipt.json").exists()
    rejections = list(output.glob(".pair-temporaries/*/rejection.json"))
    assert len(rejections) == 1
    rejection = _read_json(rejections[0])
    assert rejection["retry_performed"] is False
    assert rejection["completed_legs"] == 1


def test_runtime_postflight_drift_aborts_without_commit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    prepared_seed = _prepared_invocation(tmp_path, monkeypatch)
    contract = prepared_seed[6].contract
    drift_probe = StubRuntimeProbe(contract, drift_after=2)
    prepared_seed[3]["runtime_probe"] = drift_probe
    with pytest.raises(runner.E00RunnerError, match="runtime identity changed"):
        _invoke(prepared_seed)
    assert drift_probe.calls == 2
    assert not (prepared_seed[2] / "receipt.json").exists()


def test_threads_other_than_one_fail_before_backend_use(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    prepared = _prepared_invocation(tmp_path, monkeypatch)
    prepared[3]["threads"] = 2
    with pytest.raises(runner.E00RunnerError, match="exactly threads=1"):
        _invoke(prepared)
    assert prepared[4].calls == []


def test_runtime_manifest_execution_drift_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    prepared = _prepared_invocation(tmp_path, monkeypatch)
    manifest_path = prepared[3]["runtime_manifest"]
    assert isinstance(manifest_path, Path)
    value = _read_json(manifest_path)
    value["execution"]["maximum_plies"] = 33
    manifest_path.unlink()
    common.write_new_json(manifest_path, value)
    prepared[3]["runtime_manifest_sha256"] = _sha(manifest_path)
    with pytest.raises(
        runner.E00RunnerError, match="execution arguments differ"
    ):
        _invoke(prepared)
    assert prepared[4].calls == []


def test_inventory_only_discovery_matches_play_verify_baseline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pyffish = tmp_path / "pyffish.pyd"
    pyffish.write_bytes(b"sealed-native-binding")
    build_manifest = tmp_path / "pyffish-build.json"
    common.write_new_json(build_manifest, {"stub": "authenticated"})
    wire = runner.build_runtime_discovery_request(
        runtime_package_root=REPO_ROOT,
        pyffish=pyffish,
        pyffish_sha256=_sha(pyffish),
        pyffish_manifest_binding_path=pyffish,
        pyffish_build_manifest=build_manifest,
        pyffish_build_manifest_sha256=_sha(build_manifest),
        source_root=REPO_ROOT,
        source_commit="a" * 40,
    )
    request = runner._runtime_discovery_request_from_wire(wire)

    def load_native(
        path: Path, expected_sha256: str
    ) -> tuple[ModuleType, dict[str, object]]:
        assert path == pyffish
        assert expected_sha256 == _sha(pyffish)
        module = ModuleType("pyffish")
        module.__file__ = str(pyffish)
        sys.modules["pyffish"] = module
        return module, {
            "bytes": pyffish.stat().st_size,
            "path": str(pyffish),
            "sha256": _sha(pyffish),
        }

    monkeypatch.setattr(
        runner.atomic_outcome_helper, "_load_native_binding", load_native
    )
    monkeypatch.setattr(
        runner.atomic_outcome_helper,
        "_load_build_manifest",
        lambda *_args, **_kwargs: {"stub": "authenticated"},
    )
    expected_inventory = [
        {
            "source": "pyffish",
            "path": "pyffish",
            "sha256": _sha(pyffish),
            "size_bytes": pyffish.stat().st_size,
            "module_names": ["pyffish"],
        },
        *[
            {
                "source": "runtime-package",
                "path": runner._RUNTIME_PACKAGE_LAYOUT[
                    str(row["key"])
                ].as_posix(),
                "sha256": row["sha256"],
                "size_bytes": row["size_bytes"],
                "module_names": [f"sealed_runtime.{row['key']}"],
            }
            for row in request.runtime_modules
        ],
    ]
    expected_inventory = sorted(
        expected_inventory,
        key=lambda row: (str(row["source"]), str(row["path"])),
    )
    monkeypatch.setattr(
        runner,
        "_file_backed_module_inventory",
        lambda **_kwargs: copy.deepcopy(expected_inventory),
    )
    sys.modules.pop("pyffish", None)
    try:
        result = runner._discover_runtime(request)
        inventory = result["child_import_inventory"]
        assert inventory == expected_inventory
        play_verify_request = SimpleNamespace(
            runtime_package_root=REPO_ROOT,
            pyffish=pyffish,
            child_import_inventory=tuple(
                dict(row) for row in inventory
            ),
        )
        assert runner._assert_child_import_inventory(
            play_verify_request
        ) == inventory
        assert result["request_sha256"] == common.sha256_bytes(
            common.canonical_json_bytes(wire)
        )
    finally:
        sys.modules.pop("pyffish", None)


def test_runtime_discovery_rejects_missing_snapshot_module(
    tmp_path: Path,
) -> None:
    pyffish = tmp_path / "pyffish.pyd"
    pyffish.write_bytes(b"sealed-native-binding")
    build_manifest = tmp_path / "pyffish-build.json"
    common.write_new_json(build_manifest, {"stub": "authenticated"})
    wire = runner.build_runtime_discovery_request(
        runtime_package_root=REPO_ROOT,
        pyffish=pyffish,
        pyffish_sha256=_sha(pyffish),
        pyffish_manifest_binding_path=pyffish,
        pyffish_build_manifest=build_manifest,
        pyffish_build_manifest_sha256=_sha(build_manifest),
        source_root=REPO_ROOT,
        source_commit="a" * 40,
    )
    wire["runtime_modules"] = wire["runtime_modules"][:-1]
    with pytest.raises(runner.E00RunnerError, match="missing"):
        runner._runtime_discovery_request_from_wire(wire)
