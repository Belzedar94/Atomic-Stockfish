from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys
import time
from typing import Any

import psutil
import pytest


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "tests" / "run_atomic_release_exact_tag_gate.py"
SPEC = importlib.util.spec_from_file_location("atomic_exact_gate_orchestrator", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
GATE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = GATE
SPEC.loader.exec_module(GATE)

COMMIT = "1" * 40


def context(tmp_path: Path, gate: str = "hito4-release") -> Any:
    evidence = tmp_path / "evidence"
    (evidence / "receipts").mkdir(parents=True)
    return GATE.ReleaseContext(
        GATE.REPOSITORY,
        GATE.RELEASE_REF,
        COMMIT,
        gate,
        evidence.resolve(),
    )


def touch(path: Path, payload: bytes = b"artifact\n") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    return path.resolve()


def wait_process_gone(pid: int, timeout: float = 10.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            process = psutil.Process(pid)
            if process.status() == psutil.STATUS_ZOMBIE:
                return True
        except psutil.NoSuchProcess:
            return True
        time.sleep(0.05)
    return False


def cleanup_process_tree(pid: int) -> None:
    try:
        root = psutil.Process(pid)
    except psutil.NoSuchProcess:
        return
    processes = [*root.children(recursive=True), root]
    for process in reversed(processes):
        try:
            process.kill()
        except psutil.Error:
            pass
    psutil.wait_procs(processes, timeout=5)


def namespace(**values: Any) -> argparse.Namespace:
    return argparse.Namespace(**values)


def marker_bytes(markers: tuple[tuple[bytes, int], ...]) -> bytes:
    output = bytearray()
    for marker, count in markers:
        for _ in range(count):
            output.extend(marker)
            output.extend(b"\n")
    return bytes(output)


def test_cli_has_only_fixed_gate_ids_and_no_command_option() -> None:
    parser = GATE.build_parser()
    assert set(parser._subparsers._group_actions[0].choices) == set(GATE.GATE_IDS)
    assert "--command" not in parser.format_help()
    with pytest.raises(SystemExit):
        parser.parse_args(["arbitrary-command", "--receipt", "x"])


def test_duplicate_and_unknown_options_fail_closed() -> None:
    with pytest.raises(GATE.GateError, match="duplicate option"):
        GATE.parse_args(
            [
                "hito4-release",
                "--receipt",
                "receipts/hito4-release.json",
                "--receipt",
                "receipts/hito4-release.json",
            ]
        )
    with pytest.raises(SystemExit):
        GATE.parse_args(["hito4-release", "--unknown", "value"])


@pytest.mark.parametrize(
    "value",
    (
        "../receipt.json",
        "/receipt.json",
        r"receipts\hito4-release.json",
        "receipts/./hito4-release.json",
        "receipts/not-the-gate.json",
        "receipts/subdir/hito4-release.json",
    ),
)
def test_receipt_path_tricks_are_rejected(tmp_path: Path, value: str) -> None:
    with pytest.raises(GATE.GateError, match="receipt"):
        GATE._safe_receipt_destination(context(tmp_path), value)


def test_receipt_destination_rejects_preexisting_file(tmp_path: Path) -> None:
    ctx = context(tmp_path)
    target = ctx.evidence_root / "receipts" / "hito4-release.json"
    target.write_text("old", encoding="ascii")
    with pytest.raises(GATE.GateError, match="already exists"):
        GATE._safe_receipt_destination(ctx, GATE.EXPECTED_RECEIPTS[ctx.gate])


def test_canonical_receipt_is_exclusive_lf_and_no_overwrite(tmp_path: Path) -> None:
    ctx = context(tmp_path)
    target = GATE._safe_receipt_destination(ctx, GATE.EXPECTED_RECEIPTS[ctx.gate])
    payload = GATE._write_receipt(target, ctx.gate, 17)
    expected = (
        b'{"failed":0,"gate":"hito4-release","passed":17,'
        b'"schemaVersion":1,"skipped":0,"status":"pass"}\n'
    )
    assert payload == expected
    assert target.read_bytes() == expected
    assert target.stat().st_nlink == 1
    assert not tuple(target.parent.glob(f".{target.name}.*.tmp"))
    with pytest.raises(GATE.GateError, match="already exists"):
        GATE._write_receipt(target, ctx.gate, 17)


def test_receipt_publication_failure_leaves_no_partial_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ctx = context(tmp_path)
    target = GATE._safe_receipt_destination(ctx, GATE.EXPECTED_RECEIPTS[ctx.gate])

    def fail_link(source: Path, destination: Path) -> None:
        raise OSError("no atomic link")

    monkeypatch.setattr(GATE.os, "link", fail_link)
    with pytest.raises(GATE.GateError, match="exclusive atomic receipt"):
        GATE._write_receipt(target, ctx.gate, 1)
    assert not target.exists()
    assert not tuple(target.parent.glob(f".{target.name}.*.tmp"))


def test_hito4_marker_validator_requires_every_exact_count() -> None:
    output = marker_bytes(GATE.HITO4_MARKERS)
    validator = GATE._marker_validator("hito4-release", GATE.HITO4_MARKERS)
    assert validator(output) == sum(count for _, count in GATE.HITO4_MARKERS)
    missing = output.replace(GATE.HITO4_MARKERS[0][0], b"not-a-pass", 1)
    with pytest.raises(GATE.GateError, match="expected 1"):
        validator(missing)
    duplicate = output + GATE.HITO4_MARKERS[0][0]
    with pytest.raises(GATE.GateError, match="expected 1"):
        validator(duplicate)


def test_legacy_marker_is_locked_to_profile_commits_and_postflights() -> None:
    line = (
        "LEGACY PIPELINE E2E PASSED profile=strong-local records=32 "
        f"tools_commit={GATE.TOOLS_COMMIT} trainer_commit={GATE.TRAINER_COMMIT} "
        f"atomic_commit={COMMIT} source_sha256={GATE.NET_SHA256} "
        f"data_sha256={'2' * 64} nnue_sha256={'3' * 64} "
        "loss=0.5 ft_delta=1 fc_delta=2 bestmove=e2e4\n"
        "LEGACY PIPELINE ARTIFACT POSTFLIGHT ok\n"
        "LEGACY PIPELINE CHECKOUT POSTFLIGHT ok\n"
    ).encode("ascii")
    assert GATE._validate_legacy_output(line, COMMIT) == 3
    with pytest.raises(GATE.GateError, match="locked E2E"):
        GATE._validate_legacy_output(line.replace(b"records=32", b"records=31"), COMMIT)


def valid_h7_result() -> dict[str, Any]:
    return {
        "schema_version": 2,
        "status": "passed",
        "atomic_commit": COMMIT,
        "tools_commit": GATE.TOOLS_COMMIT,
        "trainer_commit": GATE.TRAINER_COMMIT,
        "profile": "strong-local",
        "tools_engine_commit": GATE.TOOLS_ENGINE_COMMIT,
        "trainer_engine_commit": GATE.TRAINER_ENGINE_COMMIT,
        "train_manifest_sha256": "4" * 64,
        "validation_manifest_sha256": "5" * 64,
        "candidate_sha256": "6" * 64,
        "global_step": 1,
        "bestmove_nodes_1": "e2e4",
    }


def test_h7_requires_canonical_terminal_and_matching_archive(tmp_path: Path) -> None:
    ctx = context(tmp_path, "atomic-bin-v2-strong-local")
    archive = tmp_path / "archive"
    archive.mkdir()
    payload = GATE.canonical_json(valid_h7_result())
    (archive / "result.json").write_bytes(payload)
    assert GATE._validate_h7_output(payload, archive, ctx) == 1
    with pytest.raises(GATE.GateError, match="canonical"):
        GATE._validate_h7_output(json.dumps(valid_h7_result()).encode(), archive, ctx)
    changed = valid_h7_result()
    changed["status"] = "failed"
    failed = GATE.canonical_json(changed)
    (archive / "result.json").write_bytes(failed)
    with pytest.raises(GATE.GateError, match="identity/status"):
        GATE._validate_h7_output(failed, archive, ctx)


def test_hito4_command_is_fixed_and_explicit(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_program = Path(sys.executable).resolve()
    monkeypatch.setattr(GATE, "_tracked_gate_script", lambda relative: ROOT / relative)
    monkeypatch.setattr(GATE, "_resolve_program", lambda name: fake_program)
    paths = {
        name: ROOT / name
        for name in (
            "native",
            "net",
            "pyffish",
            "cjs",
            "esm",
            "tables",
            "wasm_wrapper",
            "cpp_unit",
            "cpp_api",
            "syzygy_driver",
            "fairy_repo",
        )
    }
    command = GATE._hito4_command(paths)
    assert command[:2] == (str(fake_program), str(ROOT / "tests/run_hito4.py"))
    assert command[command.index("--timeout") + 1] == "600"
    assert "--allow-missing-wasm" not in command
    for option in (
        "--native",
        "--net",
        "--pyffish",
        "--cjs",
        "--esm",
        "--tables",
        "--wasm-wrapper",
        "--cpp-unit",
        "--cpp-api",
        "--syzygy-driver",
        "--fairy-repo",
    ):
        assert command.count(option) == 1


def test_packaged_candidate_may_be_outside_build_root_but_helpers_may_not(
    tmp_path: Path,
) -> None:
    build = (tmp_path / "build").resolve()
    build.mkdir()
    packaged = touch(tmp_path / "package" / "Atomic-Stockfish.exe")
    paths = {
        "native": packaged,
        "cpp_unit": touch(build / "atomic-unit-tests.exe"),
        "cpp_api": touch(build / "atomic-api-tests.exe"),
        "syzygy_driver": touch(build / "atomic-syzygy-driver.exe"),
    }
    GATE._validate_common_surface_containment(paths, build)
    paths["cpp_api"] = touch(tmp_path / "package" / "atomic-api-tests.exe")
    with pytest.raises(GATE.GateError, match="outside its authenticated build"):
        GATE._validate_common_surface_containment(paths, build)


def test_syzygy_candidate_may_be_packaged_but_driver_must_be_build_owned(
    tmp_path: Path,
) -> None:
    build = (tmp_path / "build").resolve()
    build.mkdir()
    paths = {
        "native": touch(tmp_path / "package" / "Atomic-Stockfish.exe"),
        "syzygy_driver": touch(build / "atomic-syzygy-driver.exe"),
    }
    GATE._validate_syzygy_artifact_containment(paths, build)
    paths["syzygy_driver"] = touch(tmp_path / "package" / "driver.exe")
    with pytest.raises(GATE.GateError, match="outside its authenticated build"):
        GATE._validate_syzygy_artifact_containment(paths, build)


def test_legacy_and_hito5_commands_pin_normative_modes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_program = Path(sys.executable).resolve()
    monkeypatch.setattr(GATE, "_tracked_gate_script", lambda relative: ROOT / relative)
    monkeypatch.setattr(GATE, "_resolve_program", lambda name: fake_program)
    monkeypatch.setattr(GATE, "_run_git", lambda *args: COMMIT)
    ctx = context(tmp_path, "legacy-v1-strong-local")
    paths = {
        name: ROOT / name
        for name in (
            "tools_engine",
            "trainer_root",
            "pipeline_python",
            "atomic_pipeline_engine",
            "atomic_data_generator",
            "tools_build_manifest",
            "trainer_build_manifest",
            "atomic_build_manifest",
            "atomic_data_generator_build_manifest",
            "net",
            "atomic_root",
            "native",
            "pyffish",
            "cjs",
            "esm",
            "tables",
            "wasm_wrapper",
            "incremental_binary",
            "oracle",
            "cpp_unit",
            "cpp_api",
            "syzygy_driver",
            "fairy_repo",
        )
    }
    paths["pipeline_python"] = touch(tmp_path / "python310" / "python.exe")
    legacy = GATE._legacy_command(paths, ctx)
    assert legacy[0] == str(paths["pipeline_python"])
    assert legacy[legacy.index("--profile") + 1] == "strong-local"
    assert legacy[legacy.index("--atomic-root") + 1] == str(paths["atomic_root"])
    assert legacy[legacy.index("--atomic-commit") + 1] == COMMIT

    hito5 = GATE._hito5_command(paths)
    assert hito5[0] == str(fake_program)
    assert hito5[hito5.index("--mode") + 1] == "release"
    assert hito5[hito5.index("--python") + 1] == str(fake_program)
    assert hito5[hito5.index("--pipeline-python") + 1] == str(
        paths["pipeline_python"]
    )
    assert hito5[hito5.index("--pipeline-atomic-root") + 1] == str(paths["atomic_root"])
    assert hito5[hito5.index("--pipeline-atomic-commit") + 1] == COMMIT
    assert hito5[hito5.index("--incremental-timeout") + 1] == "7200"
    assert hito5[hito5.index("--differential-timeout") + 1] == "7200"
    hito5_spec = importlib.util.spec_from_file_location(
        "atomic_hito5_orchestrator_contract", ROOT / "tests" / "run_hito5.py"
    )
    assert hito5_spec is not None and hito5_spec.loader is not None
    hito5_module = importlib.util.module_from_spec(hito5_spec)
    sys.modules[hito5_spec.name] = hito5_module
    hito5_spec.loader.exec_module(hito5_module)
    parsed = hito5_module.parse_args(hito5[2:])
    assert parsed.mode == "release"
    assert parsed.pipeline_python == paths["pipeline_python"]
    assert parsed.pipeline_atomic_root == paths["atomic_root"]
    assert parsed.pipeline_atomic_commit == COMMIT
    complete_pipeline = (object(),) * 9 + (COMMIT,)
    hito5_module.validate_pipeline_configuration(complete_pipeline, "release")
    with pytest.raises(hito5_module.GateFailure, match="supplied together"):
        hito5_module.validate_pipeline_configuration(
            (None,) + complete_pipeline[1:], "release"
        )
    nested = hito5_module.build_pipeline_e2e_command(
        python=str(paths["pipeline_python"]),
        tools_engine=paths["tools_engine"],
        trainer_root=paths["trainer_root"],
        native=paths["atomic_pipeline_engine"],
        atomic_data_generator=paths["atomic_data_generator"],
        tools_build_manifest=paths["tools_build_manifest"],
        trainer_build_manifest=paths["trainer_build_manifest"],
        atomic_build_manifest=paths["atomic_build_manifest"],
        atomic_data_generator_build_manifest=paths[
            "atomic_data_generator_build_manifest"
        ],
        atomic_root=paths["atomic_root"],
        atomic_commit=COMMIT,
        source_net=paths["net"],
    )
    assert nested[0] == str(paths["pipeline_python"])


def test_syzygy_command_always_requires_six_man_and_nnue(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(GATE, "_tracked_gate_script", lambda relative: ROOT / relative)
    paths = {
        "native": ROOT / "engine",
        "tables": ROOT / "tables",
        "syzygy_driver": ROOT / "driver",
        "net": ROOT / "net",
    }
    command = GATE._syzygy_command(paths)
    assert command.count("--require-six-man") == 1
    assert command[command.index("--eval-file") + 1] == str(paths["net"])
    assert command[command.index("--timeout") + 1] == "60"


def test_h7_command_has_fixed_profile_refs_seeds_and_hashes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    atomic = tmp_path / "atomic"
    tools = tmp_path / "tools"
    trainer = tmp_path / "trainer"
    for root in (atomic, tools, trainer):
        root.mkdir()
    paths = {
        "atomic_root": atomic.resolve(),
        "tools_root": tools.resolve(),
        "trainer_root": trainer.resolve(),
        "engine": touch(atomic / "atomic-pipeline-engine.exe", b"pipeline-engine"),
        "atomic_data_generator": touch(atomic / "generator.exe", b"generator"),
        "data_tools": touch(atomic / "data-tools.exe", b"data-tools"),
        "wrapper_data_tools": touch(tools / "wrapper.exe", b"wrapper"),
        "trainer_loader": touch(trainer / "loader.dll", b"loader"),
        "source_net": touch(tmp_path / "net.nnue", b"net"),
        "pipeline_python": touch(tmp_path / "python310" / "python.exe", b"python310"),
    }

    def tracked(root: Path, relative: str, label: str) -> Path:
        return touch(root / relative, relative.encode())

    monkeypatch.setattr(GATE, "_require_tracked_file", tracked)
    monkeypatch.setattr(GATE, "_tracked_gate_script", lambda relative: ROOT / relative)
    ctx = context(tmp_path, "atomic-bin-v2-strong-local")
    command = GATE._h7_command(paths, ctx, tmp_path / "output")
    assert command[0] == str(paths["pipeline_python"])
    assert command[command.index("--profile") + 1] == "strong-local"
    assert command[command.index("--atomic-root") + 1] == str(atomic.resolve())
    assert command[command.index("--atomic-ref") + 1] == GATE.RELEASE_REF
    assert command[command.index("--tools-ref") + 1] == GATE.TOOLS_REF
    assert command[command.index("--trainer-ref") + 1] == GATE.TRAINER_REF
    assert command[command.index("--train-seed") + 1] == "2026071301"
    assert command[command.index("--validation-seed") + 1] == "2026071302"
    assert command[command.index("--engine") + 1] == str(paths["engine"])
    assert command[command.index("--engine-sha256") + 1] == hashlib.sha256(
        b"pipeline-engine"
    ).hexdigest()
    assert command[command.index("--python") + 1] == str(paths["pipeline_python"])
    assert command[command.index("--python-sha256") + 1] == GATE._sha256(
        paths["pipeline_python"]
    )
    assert command[command.index("--timeout-seconds") + 1] == "1800"


def test_h7_cli_and_binding_use_pipeline_engine_not_packaged_candidate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    h7_parser = GATE.build_parser()._subparsers._group_actions[0].choices[
        "atomic-bin-v2-strong-local"
    ]
    option_strings = {
        option
        for action in h7_parser._actions
        for option in action.option_strings
    }
    assert "--atomic-pipeline-engine" in option_strings
    assert "--pipeline-python" in option_strings
    assert "--candidate-bmi2" not in option_strings

    atomic = (tmp_path / "atomic").resolve()
    tools = (tmp_path / "tools").resolve()
    trainer = (tmp_path / "trainer").resolve()
    workspace = (tmp_path / "workspace").resolve()
    for root in (atomic, tools, trainer, workspace):
        root.mkdir()
    artifact_names: list[str] = []

    def artifact(
        arguments: argparse.Namespace,
        name: str,
        environment: Any,
        *,
        label: str | None = None,
    ) -> Path:
        artifact_names.append(name)
        owner = trainer if name == "trainer_loader" else tools if name == "wrapper_data_tools" else atomic
        return touch(owner / name)

    monkeypatch.setattr(
        GATE,
        "_pipeline_roots",
        lambda *args: {
            "atomic_root": atomic,
            "tools_root": tools,
            "trainer_root": trainer,
            "tools_authority": tools,
            "trainer_authority": trainer,
        },
    )
    monkeypatch.setattr(GATE, "_artifact", artifact)
    monkeypatch.setattr(GATE, "_validate_pipeline_python", lambda path: path)
    monkeypatch.setattr(GATE, "_external", lambda *args, **kwargs: touch(tmp_path / "net"))
    monkeypatch.setattr(GATE, "_bound_directory", lambda *args, **kwargs: workspace)
    monkeypatch.setattr(GATE, "_require_empty_directory", lambda *args: None)
    ctx = context(tmp_path, "atomic-bin-v2-strong-local")
    args = namespace(
        gate=ctx.gate,
        pipeline_python=atomic / "pipeline_python",
        atomic_pipeline_engine=atomic / "atomic_pipeline_engine",
        atomic_data_generator=atomic / "atomic_data_generator",
        data_tools=atomic / "data_tools",
        wrapper_data_tools=tools / "wrapper_data_tools",
        trainer_loader=trainer / "trainer_loader",
        legacy_net=tmp_path / "net",
        gate_workspace=workspace,
    )
    paths = GATE._validate_and_bind_arguments(args, ctx, {})
    assert paths["engine"].name == "atomic_pipeline_engine"
    assert "atomic_pipeline_engine" in artifact_names
    assert "pipeline_python" in artifact_names
    assert "candidate_bmi2" not in artifact_names


def test_exit_zero_without_required_marker_never_writes_receipt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ctx = context(tmp_path)
    args = namespace(gate=ctx.gate, receipt=GATE.EXPECTED_RECEIPTS[ctx.gate])
    monkeypatch.setattr(GATE, "_authenticate_release_context", lambda gate, env: ctx)
    monkeypatch.setattr(GATE, "_validate_and_bind_arguments", lambda *args: {})
    prepared = GATE.PreparedGate(
        (str(Path(sys.executable).resolve()), "fake.py"),
        60,
        GATE._marker_validator(ctx.gate, ((b"REQUIRED PASS", 1),)),
        Path(sys.executable).resolve(),
    )
    monkeypatch.setattr(GATE, "_prepare_gate", lambda paths, release: prepared)
    monkeypatch.setattr(GATE, "_execute_command", lambda *args, **kwargs: b"looks good\n")
    with pytest.raises(GATE.GateError, match="REQUIRED PASS"):
        GATE.run(args, {})
    assert not (ctx.evidence_root / GATE.EXPECTED_RECEIPTS[ctx.gate]).exists()


@pytest.mark.parametrize("message", ("child exited 1", "gate timed out"))
def test_child_failure_or_timeout_never_writes_receipt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, message: str
) -> None:
    ctx = context(tmp_path)
    args = namespace(gate=ctx.gate, receipt=GATE.EXPECTED_RECEIPTS[ctx.gate])
    monkeypatch.setattr(GATE, "_authenticate_release_context", lambda gate, env: ctx)
    monkeypatch.setattr(GATE, "_validate_and_bind_arguments", lambda *args: {})
    prepared = GATE.PreparedGate(
        (str(Path(sys.executable).resolve()), "fake.py"),
        60,
        lambda output: 1,
        Path(sys.executable).resolve(),
    )
    monkeypatch.setattr(GATE, "_prepare_gate", lambda paths, release: prepared)

    def fail(*args: Any, **kwargs: Any) -> bytes:
        raise GATE.GateError(message)

    monkeypatch.setattr(GATE, "_execute_command", fail)
    with pytest.raises(GATE.GateError, match=message):
        GATE.run(args, {})
    assert not (ctx.evidence_root / GATE.EXPECTED_RECEIPTS[ctx.gate]).exists()


def test_successful_run_reauthenticates_before_receipt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ctx = context(tmp_path)
    args = namespace(gate=ctx.gate, receipt=GATE.EXPECTED_RECEIPTS[ctx.gate])
    authentications: list[str] = []

    def authenticate(gate: str, env: Any) -> Any:
        authentications.append(gate)
        return ctx

    monkeypatch.setattr(GATE, "_authenticate_release_context", authenticate)
    monkeypatch.setattr(GATE, "_validate_and_bind_arguments", lambda *args: {})
    prepared = GATE.PreparedGate(
        (str(Path(sys.executable).resolve()), "fake.py"),
        60,
        lambda output: 4,
        Path(sys.executable).resolve(),
    )
    monkeypatch.setattr(GATE, "_prepare_gate", lambda paths, release: prepared)
    monkeypatch.setattr(GATE, "_execute_command", lambda *args, **kwargs: b"PASS\n")
    payload = GATE.run(args, {})
    assert authentications == [ctx.gate, ctx.gate]
    assert json.loads(payload)["passed"] == 4
    assert (ctx.evidence_root / GATE.EXPECTED_RECEIPTS[ctx.gate]).read_bytes() == payload


def test_bound_file_rejects_different_authenticated_path(tmp_path: Path) -> None:
    requested = touch(tmp_path / "requested")
    bound = touch(tmp_path / "bound")
    with pytest.raises(GATE.GateError, match="differs from authenticated binding"):
        GATE._bound_file(
            requested,
            environment={"BOUND": str(bound)},
            binding="BOUND",
            label="artifact",
        )


def test_pipeline_python_is_required_and_uses_the_exact_artifact_binding(
    tmp_path: Path,
) -> None:
    subparsers = GATE.build_parser()._subparsers._group_actions[0].choices
    for gate in (
        "legacy-v1-strong-local",
        "hito5-release",
        "atomic-bin-v2-strong-local",
    ):
        actions = {
            option: action
            for action in subparsers[gate]._actions
            for option in action.option_strings
        }
        assert actions["--pipeline-python"].required

    requested = touch(tmp_path / "python310" / "python.exe")
    substituted = touch(tmp_path / "attacker" / "python.exe")
    args = namespace(pipeline_python=requested)
    with pytest.raises(GATE.GateError, match="ATOMIC_ARTIFACT_PIPELINE_PYTHON"):
        GATE._artifact(
            args,
            "pipeline_python",
            {"ATOMIC_ARTIFACT_PIPELINE_PYTHON": str(substituted)},
        )


def test_pipeline_python_identity_requires_exact_cpython_3_10_18(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    interpreter = touch(tmp_path / "python310" / "python.exe")

    def result(identity: dict[str, Any]) -> Any:
        return GATE.subprocess.CompletedProcess(
            args=[str(interpreter)],
            returncode=0,
            stdout=GATE.canonical_json(identity),
            stderr=b"",
        )

    good = {
        "executable": str(interpreter),
        "implementation": "cpython",
        "version": [3, 10, 18],
    }
    monkeypatch.setattr(GATE.subprocess, "run", lambda *args, **kwargs: result(good))
    assert GATE._validate_pipeline_python(interpreter) == interpreter

    wrong_version = {**good, "version": [3, 10, 17]}
    monkeypatch.setattr(
        GATE.subprocess, "run", lambda *args, **kwargs: result(wrong_version)
    )
    with pytest.raises(GATE.GateError, match=r"expected 3\.10\.18"):
        GATE._validate_pipeline_python(interpreter)

    wrong_implementation = {**good, "implementation": "pypy"}
    monkeypatch.setattr(
        GATE.subprocess, "run", lambda *args, **kwargs: result(wrong_implementation)
    )
    with pytest.raises(GATE.GateError, match="must be CPython"):
        GATE._validate_pipeline_python(interpreter)


def test_pipeline_python_identity_change_is_rejected_on_revalidation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    interpreter = touch(tmp_path / "python310" / "python.exe")
    identities = iter(
        (
            {
                "executable": str(interpreter),
                "implementation": "cpython",
                "version": [3, 10, 18],
            },
            {
                "executable": str(interpreter),
                "implementation": "cpython",
                "version": [3, 11, 0],
            },
        )
    )

    def probe(*args: Any, **kwargs: Any) -> Any:
        return GATE.subprocess.CompletedProcess(
            args=[str(interpreter)],
            returncode=0,
            stdout=GATE.canonical_json(next(identities)),
            stderr=b"",
        )

    monkeypatch.setattr(GATE.subprocess, "run", probe)
    assert GATE._validate_pipeline_python(interpreter) == interpreter
    with pytest.raises(GATE.GateError, match=r"expected 3\.10\.18"):
        GATE._validate_pipeline_python(interpreter)


@pytest.mark.parametrize(
    "override,pattern",
    (
        ({"ATOMIC_EXACT_GATE_ID": "hito5-release"}, "GATE_ID"),
        ({"ATOMIC_EXACT_GATE_REPOSITORY": "attacker/repo"}, "identity"),
        ({"ATOMIC_EXACT_GATE_REF": "refs/heads/main"}, "identity"),
        ({"ATOMIC_EXACT_GATE_COMMIT": "not-a-commit"}, "identity"),
    ),
)
def test_release_context_rejects_wrong_gate_repository_ref_or_commit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    override: dict[str, str],
    pattern: str,
) -> None:
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    environment = {
        "ATOMIC_EXACT_GATE_ID": "hito4-release",
        "ATOMIC_EXACT_GATE_REPOSITORY": GATE.REPOSITORY,
        "ATOMIC_EXACT_GATE_REF": GATE.RELEASE_REF,
        "ATOMIC_EXACT_GATE_COMMIT": COMMIT,
        "ATOMIC_EXACT_GATE_EVIDENCE_ROOT": str(evidence.resolve()),
        **override,
    }
    monkeypatch.setattr(GATE, "_authenticate_git_repository", lambda *args, **kwargs: GATE.REPO_ROOT)
    monkeypatch.setattr(GATE, "_require_tracked_file", lambda *args, **kwargs: SCRIPT)
    with pytest.raises(GATE.GateError, match=pattern):
        GATE._authenticate_release_context("hito4-release", environment)


@pytest.mark.parametrize(
    "failure,pattern",
    (
        ("head", "HEAD"),
        ("ref", "resolves"),
        ("remote", "origin"),
        ("dirty", "dirty"),
    ),
)
def test_repository_authentication_rejects_wrong_head_ref_origin_and_dirty_tree(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
    pattern: str,
) -> None:
    root = tmp_path.resolve()

    def fake_git(checkout: Path, *arguments: str) -> str:
        if arguments == ("rev-parse", "--show-toplevel"):
            return str(root)
        if arguments == ("rev-parse", "HEAD"):
            return "2" * 40 if failure == "head" else COMMIT
        if arguments == ("rev-parse", "--verify", f"{GATE.RELEASE_REF}^{{commit}}"):
            return "3" * 40 if failure == "ref" else COMMIT
        if arguments == ("remote", "get-url", "origin"):
            return (
                "https://github.com/attacker/repo.git"
                if failure == "remote"
                else "https://github.com/Belzedar94/Atomic-Stockfish.git"
            )
        if arguments[:2] == ("status", "--porcelain=v1"):
            return " M source.cpp" if failure == "dirty" else ""
        raise AssertionError(arguments)

    monkeypatch.setattr(GATE, "_run_git", fake_git)
    with pytest.raises(GATE.GateError, match=pattern):
        GATE._authenticate_git_repository(
            root,
            label="Atomic",
            repository=GATE.REPOSITORY,
            commit=COMMIT,
            ref=GATE.RELEASE_REF,
        )


def test_build_repository_still_rejects_nonignored_dirt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path.resolve()

    def fake_git(checkout: Path, *arguments: str) -> str:
        if arguments == ("rev-parse", "--show-toplevel"):
            return str(root)
        if arguments == ("rev-parse", "HEAD"):
            return COMMIT
        if arguments == ("rev-parse", "--verify", f"{GATE.RELEASE_REF}^{{commit}}"):
            return COMMIT
        if arguments == ("remote", "get-url", "origin"):
            return "https://github.com/Belzedar94/Atomic-Stockfish.git"
        if arguments[:2] == ("status", "--porcelain=v1"):
            assert "--ignore-submodules=dirty" in arguments
            return "?? injected.cpp"
        raise AssertionError(arguments)

    monkeypatch.setattr(GATE, "_run_git", fake_git)
    with pytest.raises(GATE.GateError, match="outside ignored build outputs"):
        GATE._authenticate_git_repository(
            root,
            label="Atomic build",
            repository=GATE.REPOSITORY,
            commit=COMMIT,
            ref=GATE.RELEASE_REF,
            clean=False,
        )


def test_symlink_or_reparse_input_is_rejected_when_supported(tmp_path: Path) -> None:
    target = touch(tmp_path / "target")
    link = tmp_path / "link"
    try:
        link.symlink_to(target)
    except OSError:
        pytest.skip("symlink creation is not available")
    with pytest.raises(GATE.GateError, match="symlink or reparse"):
        GATE._real_file(link.resolve(strict=False) if False else link.absolute(), "linked file")


def test_syzygy_inventory_reconciles_consumed_md5_and_frozen_sha256(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tables = tmp_path / "tables"
    tables.mkdir()
    definitions = {
        "KBBBvK.atbw": ("3-4-5", b"a"),
        "KBBBvK.atbz": ("3-4-5", b"bb"),
        "KRvK.atbw": ("3-4-5", b"ccc"),
        "KPPPPvK.atbw": ("6-wdl", b"dddd"),
        "KPPPPvK.atbz": ("6-dtz", b"eeeee"),
    }
    rows = []
    for name, (directory, payload) in definitions.items():
        touch(tables / name, payload)
        rows.append(
            {
                "directory": directory,
                "name": name,
                "bytes": len(payload),
                "md5": hashlib.md5(payload).hexdigest(),
            }
        )
    inventory = touch(tmp_path / "inventory.json", json.dumps(rows).encode())
    external_wdl = touch(tmp_path / "external-wdl", definitions["KPPPPvK.atbw"][1])
    external_dtz = touch(tmp_path / "external-dtz", definitions["KPPPPvK.atbz"][1])
    fixture_sha256 = {
        name: hashlib.sha256(payload).hexdigest()
        for name, (_, payload) in definitions.items()
    }
    monkeypatch.setattr(GATE, "SYZYGY_TABLE_SHA256", fixture_sha256)
    paths = {
        "tables": tables.resolve(),
        "inventory": inventory,
        "kppppvk_wdl": external_wdl,
        "kppppvk_dtz": external_dtz,
    }
    GATE._validate_syzygy_inventory(paths)
    replacement = b"ddd"
    (tables / "KRvK.atbw").write_bytes(replacement)
    for row in rows:
        if row["name"] == "KRvK.atbw":
            row["md5"] = hashlib.md5(replacement).hexdigest()
    inventory.write_text(json.dumps(rows), encoding="utf-8")
    with pytest.raises(GATE.GateError, match="SHA-256 differs for KRvK.atbw"):
        GATE._validate_syzygy_inventory(paths)


def test_syzygy_consumed_table_sha256_values_are_frozen() -> None:
    assert GATE.SYZYGY_TABLE_SHA256 == {
        "KBBBvK.atbw": "114f101f74ab1469d749777b5b7e8b2ada5f47d31627ff60031f4832e6bf76a8",
        "KBBBvK.atbz": "f731d407f3ad8a0368d7f29762d0a70e407ee791dc0f5dcb88fc94eba987e31f",
        "KRvK.atbw": "a17ff195ef2738f00f180e3dd8eb8bcd1d21e57642e78ff8f7b7ebffd233cceb",
        "KPPPPvK.atbw": GATE.KPPPPVK_WDL_SHA256,
        "KPPPPvK.atbz": GATE.KPPPPVK_DTZ_SHA256,
    }


def test_syzygy_inventory_rejects_duplicate_consumed_record(
    tmp_path: Path,
) -> None:
    tables = tmp_path / "tables"
    tables.mkdir()
    row = {
        "directory": "3-4-5",
        "name": "KBBBvK.atbw",
        "bytes": 1,
        "md5": hashlib.md5(b"a").hexdigest(),
    }
    inventory = touch(tmp_path / "inventory.json", json.dumps([row, row]).encode())
    with pytest.raises(GATE.GateError, match="duplicates"):
        GATE._validate_syzygy_inventory(
            {
                "tables": tables.resolve(),
                "inventory": inventory,
                "kppppvk_wdl": touch(tmp_path / "wdl"),
                "kppppvk_dtz": touch(tmp_path / "dtz"),
            }
        )


@pytest.mark.skipif(
    os.name != "nt", reason="exact-tag containment requires a Windows Job Object"
)
def test_real_subprocess_success_failure_and_timeout(tmp_path: Path) -> None:
    success = touch(tmp_path / "success.py", b"print('REQUIRED PASS')\n")
    output = GATE._execute_command(
        (str(Path(sys.executable).resolve()), str(success)),
        timeout_seconds=10,
        environment=GATE._minimal_environment(),
    )
    assert output.replace(b"\r\n", b"\n") == b"REQUIRED PASS\n"

    failure = touch(tmp_path / "failure.py", b"raise SystemExit(7)\n")
    with pytest.raises(GATE.GateError, match="exited 7"):
        GATE._execute_command(
            (str(Path(sys.executable).resolve()), str(failure)),
            timeout_seconds=10,
            environment=GATE._minimal_environment(),
        )

    timeout = touch(tmp_path / "timeout.py", b"import time; time.sleep(3)\n")
    with pytest.raises(GATE.GateError, match="timed out"):
        GATE._execute_command(
            (str(Path(sys.executable).resolve()), str(timeout)),
            timeout_seconds=1,
            environment=GATE._minimal_environment(),
        )


@pytest.mark.skipif(
    os.name != "nt", reason="exact-tag containment requires a Windows Job Object"
)
def test_execute_command_timeout_terminates_a_real_grandchild(tmp_path: Path) -> None:
    pid_file = tmp_path / "grandchild.pid"
    parent = touch(
        tmp_path / "parent.py",
        (
            "import pathlib, subprocess, sys, time\n"
            "child = subprocess.Popen([sys.executable, '-c', "
            "'import time; time.sleep(120)'])\n"
            "pathlib.Path(sys.argv[1]).write_text(str(child.pid), encoding='ascii')\n"
            "print('grandchild ready', flush=True)\n"
            "time.sleep(120)\n"
        ).encode("ascii"),
    )
    try:
        with pytest.raises(GATE.GateError, match="timed out"):
            GATE._execute_command(
                (str(Path(sys.executable).resolve()), str(parent), str(pid_file)),
                timeout_seconds=1,
                environment=GATE._minimal_environment(),
            )
        grandchild_pid = int(pid_file.read_text(encoding="ascii"))
        assert wait_process_gone(grandchild_pid), (
            f"grandchild {grandchild_pid} survived the inner gate timeout"
        )
    finally:
        if pid_file.exists():
            cleanup_process_tree(int(pid_file.read_text(encoding="ascii")))


@pytest.mark.skipif(
    os.name != "nt", reason="exact-tag containment requires a Windows Job Object"
)
def test_execute_command_root_exit_terminates_inherited_pipe_grandchild(
    tmp_path: Path,
) -> None:
    pid_file = tmp_path / "grandchild.pid"
    parent = touch(
        tmp_path / "root-exit-parent.py",
        (
            "import pathlib, subprocess, sys\n"
            "child = subprocess.Popen([sys.executable, '-c', "
            "'import time; time.sleep(120)'])\n"
            "pathlib.Path(sys.argv[1]).write_text(str(child.pid), encoding='ascii')\n"
            "print('grandchild inherited output', flush=True)\n"
        ).encode("ascii"),
    )
    started = time.monotonic()
    try:
        output = GATE._execute_command(
            (str(Path(sys.executable).resolve()), str(parent), str(pid_file)),
            timeout_seconds=30,
            environment=GATE._minimal_environment(),
        )
        assert time.monotonic() - started < 10
        assert output.replace(b"\r\n", b"\n") == b"grandchild inherited output\n"
        grandchild_pid = int(pid_file.read_text(encoding="ascii"))
        assert wait_process_gone(grandchild_pid), (
            f"grandchild {grandchild_pid} survived its inner gate root"
        )
    finally:
        if pid_file.exists():
            cleanup_process_tree(int(pid_file.read_text(encoding="ascii")))


@pytest.mark.skipif(
    os.name != "nt", reason="exact-tag containment requires a Windows Job Object"
)
def test_execute_command_enforces_output_limit_during_execution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pid_file = tmp_path / "writer.pid"
    writer = touch(
        tmp_path / "writer.py",
        (
            "import os, pathlib, sys, time\n"
            "pathlib.Path(sys.argv[1]).write_text(str(os.getpid()), encoding='ascii')\n"
            "while True:\n"
            "    os.write(1, b'x' * 4096)\n"
            "    time.sleep(0.01)\n"
        ).encode("ascii"),
    )
    monkeypatch.setattr(GATE, "MAX_CHILD_OUTPUT", 1024)
    started = time.monotonic()
    try:
        with pytest.raises(GATE.GateError, match="output exceeded"):
            GATE._execute_command(
                (str(Path(sys.executable).resolve()), str(writer), str(pid_file)),
                timeout_seconds=30,
                environment=GATE._minimal_environment(),
            )
        assert time.monotonic() - started < 10
        writer_pid = int(pid_file.read_text(encoding="ascii"))
        assert wait_process_gone(writer_pid), (
            f"writer {writer_pid} survived the inner output limit"
        )
    finally:
        if pid_file.exists():
            cleanup_process_tree(int(pid_file.read_text(encoding="ascii")))


def test_execute_command_rejects_a_launcher_other_than_the_bound_runtime(
    tmp_path: Path,
) -> None:
    other = touch(tmp_path / "other-python.exe")
    with pytest.raises(GATE.GateError, match="authenticated Python launcher"):
        GATE._execute_command(
            (str(Path(sys.executable).resolve()), str(tmp_path / "unused.py")),
            timeout_seconds=10,
            environment=GATE._minimal_environment(),
            expected_launcher=other,
        )


def test_minimal_child_environment_drops_secrets_and_python_overrides() -> None:
    source = {
        "PATH": os.environ.get("PATH", ""),
        "HOME": str(Path.home()),
        "PYTHONPATH": "attacker",
        "PYTHONHOME": "attacker",
        "GITHUB_TOKEN": "secret",
        "DISCORD_TOKEN": "secret",
    }
    result = GATE._minimal_environment(source)
    assert result["PYTHONHASHSEED"] == "0"
    assert "PYTHONPATH" not in result
    assert "PYTHONHOME" not in result
    assert "GITHUB_TOKEN" not in result
    assert "DISCORD_TOKEN" not in result
