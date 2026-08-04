from __future__ import annotations

import copy
import json
import os
from pathlib import Path
import sys
from typing import Mapping, Sequence

import pytest

from tools.atomic_mining import build_atomic_outcome_binding
from tools.atomic_mining import build_atomic_outcome_binding_cli as native_cli
from tools.atomic_mining import build_e00_runtime_manifest as runtime_builder
from tools.atomic_mining import build_e00_schedule
from tools.atomic_mining import common
from tools.atomic_mining import run_e00_source as runner


REPO_ROOT = Path(__file__).resolve().parents[2]
START = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"
COMMIT = "a" * 40
TREE = "b" * 40


def _sha(path: Path) -> str:
    return common.sha256_file(path)


def _source_identity() -> native_cli.SourceIdentity:
    return native_cli.SourceIdentity(
        root=REPO_ROOT,
        commit=COMMIT,
        tree=TREE,
        clean=True,
    )


def test_native_cli_builds_and_reopens_create_new_bundle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    identity = _source_identity()
    monkeypatch.setattr(native_cli, "_source_identity", lambda _root: identity)

    def fake_build(
        source_root: Path,
        output_dir: Path,
        *,
        expected_source_commit: str | None,
        timeout_seconds: float,
    ) -> build_atomic_outcome_binding.NativeBindingBuild:
        assert source_root == REPO_ROOT
        assert expected_source_commit == COMMIT
        assert timeout_seconds == 9.0
        output_dir.mkdir()
        binding = output_dir / "binding" / "pyffish.pyd"
        binding.parent.mkdir()
        binding.write_bytes(b"native")
        manifest = output_dir / "manifest.json"
        common.write_new_json(manifest, {"schema": "stub-native-build"})
        return build_atomic_outcome_binding.NativeBindingBuild(
            binding_path=binding,
            binding_sha256=_sha(binding),
            manifest_path=manifest,
            manifest_sha256=_sha(manifest),
            source_commit=COMMIT,
        )

    monkeypatch.setattr(
        native_cli.build_atomic_outcome_binding,
        "build_native_rules_binding",
        fake_build,
    )
    output = tmp_path / "native"
    summary = native_cli.build_from_clean_source(
        REPO_ROOT,
        output,
        expected_source_commit=COMMIT,
        expected_source_tree=TREE,
        timeout_seconds=9.0,
    )
    assert summary["schema"] == (
        "atomic-e00-native-outcome-build-cli-summary-v1"
    )
    assert summary["source"] == {
        "commit": COMMIT,
        "root": str(REPO_ROOT),
        "tree": TREE,
    }
    assert summary["binding"]["sha256"] == _sha(  # type: ignore[index]
        output / "binding" / "pyffish.pyd"
    )
    with pytest.raises(FileExistsError, match="refusing to reuse"):
        native_cli.build_from_clean_source(
            REPO_ROOT,
            output,
            expected_source_commit=COMMIT,
            expected_source_tree=TREE,
        )


def test_native_cli_rejects_source_drift_and_in_tree_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    clean = _source_identity()
    dirty = native_cli.SourceIdentity(
        root=REPO_ROOT,
        commit=COMMIT,
        tree=TREE,
        clean=False,
    )
    monkeypatch.setattr(native_cli, "_source_identity", lambda _root: dirty)
    with pytest.raises(native_cli.NativeBindingCliError, match="clean state"):
        native_cli.build_from_clean_source(
            REPO_ROOT,
            tmp_path / "native",
            expected_source_commit=COMMIT,
            expected_source_tree=TREE,
        )
    monkeypatch.setattr(native_cli, "_source_identity", lambda _root: clean)
    with pytest.raises(native_cli.NativeBindingCliError, match="outside"):
        native_cli.build_from_clean_source(
            REPO_ROOT,
            REPO_ROOT / "forbidden-native-output",
            expected_source_commit=COMMIT,
            expected_source_tree=TREE,
        )


def test_native_cli_main_wires_all_operational_gates(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    captured: dict[str, object] = {}

    def fake_build(
        source_root: Path,
        output_dir: Path,
        *,
        expected_source_commit: str,
        expected_source_tree: str,
        timeout_seconds: float,
    ) -> dict[str, object]:
        captured.update(
            {
                "source_root": source_root,
                "output_dir": output_dir,
                "commit": expected_source_commit,
                "tree": expected_source_tree,
                "timeout": timeout_seconds,
            }
        )
        return {"schema": "native-cli-wiring-test"}

    monkeypatch.setattr(native_cli, "build_from_clean_source", fake_build)
    output = tmp_path / "native"
    assert (
        native_cli.main(
            [
                "--source-root",
                str(REPO_ROOT),
                "--expected-source-commit",
                COMMIT,
                "--expected-source-tree",
                TREE,
                "--output-dir",
                str(output),
                "--timeout-seconds",
                "17",
            ]
        )
        == 0
    )
    assert captured == {
        "source_root": REPO_ROOT,
        "output_dir": output,
        "commit": COMMIT,
        "tree": TREE,
        "timeout": 17.0,
    }
    assert json.loads(capsys.readouterr().out) == {
        "schema": "native-cli-wiring-test"
    }


def test_native_cli_postflight_source_drift_is_no_go(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    clean = _source_identity()
    drifted = native_cli.SourceIdentity(
        root=REPO_ROOT,
        commit=COMMIT,
        tree="c" * 40,
        clean=True,
    )
    identities = iter((clean, drifted))
    monkeypatch.setattr(
        native_cli, "_source_identity", lambda _root: next(identities)
    )

    def fake_build(
        _source_root: Path,
        output_dir: Path,
        **_kwargs: object,
    ) -> build_atomic_outcome_binding.NativeBindingBuild:
        output_dir.mkdir()
        binding = output_dir / "binding" / "pyffish.pyd"
        binding.parent.mkdir()
        binding.write_bytes(b"native")
        manifest = output_dir / "manifest.json"
        common.write_new_json(manifest, {"schema": "stub-native-build"})
        return build_atomic_outcome_binding.NativeBindingBuild(
            binding,
            _sha(binding),
            manifest,
            _sha(manifest),
            COMMIT,
        )

    monkeypatch.setattr(
        native_cli.build_atomic_outcome_binding,
        "build_native_rules_binding",
        fake_build,
    )
    with pytest.raises(
        native_cli.NativeBindingCliError, match="changed during"
    ):
        native_cli.build_from_clean_source(
            REPO_ROOT,
            tmp_path / "native",
            expected_source_commit=COMMIT,
            expected_source_tree=TREE,
        )


class _FakeProbe:
    def __init__(self, value: Mapping[str, object]) -> None:
        self.value = copy.deepcopy(dict(value))

    def capture(self) -> Mapping[str, object]:
        return copy.deepcopy(self.value)


class _FakeDiscoveryBackend:
    def __init__(self, *, terminate: bool = False, omit_module: bool = False) -> None:
        self.terminate = terminate
        self.omit_module = omit_module
        self.calls = 0

    def discover_runtime(
        self,
        request_wire: Mapping[str, object],
        *,
        deadline_seconds: float,
    ) -> dict[str, object]:
        self.calls += 1
        assert deadline_seconds == 7.0
        modules = request_wire["runtime_modules"]
        assert isinstance(modules, list)
        selected = modules[:-1] if self.omit_module else modules
        inventory: list[dict[str, object]] = [
            {
                "source": "runtime-package",
                "path": Path(str(row["path"]))
                .relative_to(Path(str(request_wire["runtime_package_root"])))
                .as_posix(),
                "sha256": row["sha256"],
                "size_bytes": row["size_bytes"],
                "module_names": [f"sealed_runtime.{row['key']}"],
            }
            for row in selected
        ]
        pyffish = Path(str(request_wire["pyffish"]))
        manifest = Path(str(request_wire["pyffish_build_manifest"]))
        inventory.append(
            {
                "source": "pyffish",
                "path": "pyffish",
                "sha256": request_wire["pyffish_sha256"],
                "size_bytes": pyffish.stat().st_size,
                "module_names": ["pyffish"],
            }
        )
        inventory.sort(key=lambda row: (str(row["source"]), str(row["path"])))
        process = {
            "schema": "atomic-e00-owned-process-v1",
            "containment": (
                "windows-job-object"
                if os.name == "nt"
                else "posix-process-group"
            ),
            "kill_on_close": os.name == "nt",
            "created_suspended_before_assignment": os.name == "nt",
            "resumed_primary_thread": os.name == "nt",
            "active_processes_zero": True,
            "termination_requested": self.terminate,
        }
        return {
            "schema": runner.RUNTIME_DISCOVERY_RECEIPT_SCHEMA,
            "request": dict(request_wire),
            "result": {
                "schema": runner.INTERNAL_RUNTIME_DISCOVERY_RESULT_SCHEMA,
                "request_sha256": common.sha256_bytes(
                    common.canonical_json_bytes(dict(request_wire))
                ),
                "runtime_modules_sha256": runner._digest_document(
                    "atomic-e00-runtime-module-inventory-v2", modules
                ),
                "native_validation": {
                    "pyffish_path": str(pyffish),
                    "pyffish_sha256": request_wire["pyffish_sha256"],
                    "pyffish_size_bytes": pyffish.stat().st_size,
                    "manifest_binding_path": request_wire[
                        "pyffish_manifest_binding_path"
                    ],
                    "build_manifest_path": str(manifest),
                    "build_manifest_sha256": request_wire[
                        "pyffish_build_manifest_sha256"
                    ],
                    "build_manifest_size_bytes": manifest.stat().st_size,
                    "source_root": request_wire["source_root"],
                    "source_commit": request_wire["source_commit"],
                },
                "child_import_inventory": inventory,
            },
            "process": process,
        }


def _identity(path: Path) -> dict[str, object]:
    payload = common.read_stable_file_bytes(path, label="test module")
    return {
        "path": str(path),
        "sha256": common.sha256_bytes(payload),
        "size_bytes": len(payload),
    }


def _probe_factory(
    native_rules: Mapping[str, object],
    inventory: Sequence[Mapping[str, object]],
) -> _FakeProbe:
    modules = {
        "tools_init": _identity(REPO_ROOT / "tools" / "__init__.py"),
        "atomic_mining_init": _identity(
            REPO_ROOT / "tools" / "atomic_mining" / "__init__.py"
        ),
        "common": _identity(REPO_ROOT / "tools" / "atomic_mining" / "common.py"),
        "schedule_builder": _identity(
            REPO_ROOT
            / "tools"
            / "atomic_mining"
            / "build_e00_schedule.py"
        ),
        "wrapper": _identity(
            REPO_ROOT / "tools" / "atomic_mining" / "run_e00_source.py"
        ),
        "uci_session": _identity(
            REPO_ROOT / "tools" / "atomic_mining" / "uci_session.py"
        ),
        "atomic_outcome_helper": _identity(
            REPO_ROOT
            / "tools"
            / "atomic_mining"
            / "atomic_outcome_helper.py"
        ),
        "binding_builder": _identity(
            REPO_ROOT
            / "tools"
            / "atomic_mining"
            / "build_atomic_outcome_binding.py"
        ),
        "owned_process": _identity(
            REPO_ROOT / "tools" / "atomic_mining" / "owned_process.py"
        ),
    }
    executable = Path(sys.executable).resolve()
    executable_payload = common.read_stable_file_bytes(
        executable, label="test Python"
    )
    return _FakeProbe(
        {
            "python": {
                "executable": str(executable),
                "executable_sha256": common.sha256_bytes(
                    executable_payload
                ),
                "executable_size_bytes": len(executable_payload),
                "runtime_libraries": (
                    runner.atomic_outcome_helper._python_runtime_artifacts()
                ),
                "child_import_inventory": [
                    dict(row) for row in inventory
                ],
                "version": sys.version,
            },
            "modules": modules,
            "native_rules": dict(native_rules),
            "source": {
                "root": str(REPO_ROOT),
                "commit": COMMIT,
                "tree": TREE,
                "clean": True,
            },
        }
    )


def _runtime_inputs(
    tmp_path: Path,
) -> tuple[
    dict[str, runtime_builder.ArtifactSpec],
    dict[str, str],
]:
    book = tmp_path / "schedule-source" / "book.epd"
    book.parent.mkdir()
    book.write_text(
        START + "\n" + START.replace(" 0 1", " 1 2") + "\n",
        encoding="utf-8",
        newline="\n",
    )
    schedule_path = tmp_path / "schedule.jsonl"
    schedule_receipt = tmp_path / "schedule.receipt.json"
    build_e00_schedule.build_schedule_file(
        book,
        seed="packaging-test-seed",
        time_controls=[
            build_e00_schedule.TimeControl("VSTC", 2_000, 20, 1)
        ],
        output_path=schedule_path,
        receipt_path=schedule_receipt,
    )
    native_root = tmp_path / "native"
    pyffish = native_root / "binding" / "pyffish.pyd"
    pyffish.parent.mkdir(parents=True)
    pyffish.write_bytes(b"fake-native")
    build_manifest = native_root / "manifest.json"
    source_files = {}
    for relative in ("setup.py", "src/atomic_init.cpp"):
        source_path = REPO_ROOT / relative
        source_files[relative] = {
            "bytes": source_path.stat().st_size,
            "sha256": _sha(source_path),
        }
    common.write_new_json(
        build_manifest,
        {
            "schema": "fake-native-build",
            "source": {"files": source_files},
        },
    )
    other: dict[str, Path] = {}
    for name in (
        "engine.exe",
        "current.nnue",
        "teacher.nnue",
        "variants.ini",
    ):
        path = tmp_path / name
        path.write_bytes(name.encode("ascii"))
        other[name] = path
    runner_path = REPO_ROOT / "tools" / "atomic_mining" / "run_e00_source.py"
    files = {
        "book": runtime_builder.ArtifactSpec(book, _sha(book)),
        "engine": runtime_builder.ArtifactSpec(
            other["engine.exe"], _sha(other["engine.exe"])
        ),
        "current_net": runtime_builder.ArtifactSpec(
            other["current.nnue"], _sha(other["current.nnue"])
        ),
        "teacher_net": runtime_builder.ArtifactSpec(
            other["teacher.nnue"], _sha(other["teacher.nnue"])
        ),
        "variant_config": runtime_builder.ArtifactSpec(
            other["variants.ini"], _sha(other["variants.ini"])
        ),
        "pyffish": runtime_builder.ArtifactSpec(pyffish, _sha(pyffish)),
        "pyffish_build_manifest": runtime_builder.ArtifactSpec(
            build_manifest, _sha(build_manifest)
        ),
        "runner": runtime_builder.ArtifactSpec(
            runner_path, _sha(runner_path)
        ),
        "schedule": runtime_builder.ArtifactSpec(
            schedule_path, _sha(schedule_path)
        ),
        "schedule_receipt": runtime_builder.ArtifactSpec(
            schedule_receipt, _sha(schedule_receipt)
        ),
    }
    module_hashes = {
        key: _sha(REPO_ROOT / relative)
        for key, relative in runner._RUNTIME_PACKAGE_LAYOUT.items()
        if key != "runner"
    }
    return files, module_hashes


def _patch_runtime_preconditions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    identity = _source_identity()
    monkeypatch.setattr(
        runtime_builder, "_source_identity", lambda _root: identity
    )
    monkeypatch.setattr(
        runtime_builder.atomic_outcome_helper,
        "_load_build_manifest",
        lambda *_args, **_kwargs: {"stub": "authenticated"},
    )


def test_runtime_builder_commits_exact_v2_after_discovery(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_runtime_preconditions(monkeypatch)
    files, module_hashes = _runtime_inputs(tmp_path)
    backend = _FakeDiscoveryBackend()
    output = tmp_path / "runtime-design"
    result = runtime_builder.build_runtime_manifest(
        output,
        source_root=REPO_ROOT,
        expected_source_commit=COMMIT,
        expected_source_tree=TREE,
        files=files,
        module_hashes=module_hashes,
        execution=runtime_builder.ExecutionSpec(),
        discovery_timeout_seconds=7.0,
        backend=backend,
        runtime_probe_factory=_probe_factory,
    )
    assert backend.calls == 1
    assert result.runtime_manifest_path.is_file()
    assert result.discovery_receipt_path.is_file()
    assert result.build_receipt_path.is_file()
    manifest = __import__("json").loads(
        result.runtime_manifest_path.read_text(encoding="utf-8")
    )
    assert manifest["schema"] == runner.RUNTIME_MANIFEST_SCHEMA
    assert list(sorted(manifest["inputs"])) == list(
        runtime_builder._INPUT_KEYS
    )
    assert manifest["runtime"]["python"]["child_import_inventory"] == list(
        result.child_import_inventory
    )
    receipt = __import__("json").loads(
        result.build_receipt_path.read_text(encoding="utf-8")
    )
    assert receipt["schema"] == runtime_builder.BUILD_RECEIPT_SCHEMA
    assert receipt["runtime_manifest"]["sha256"] == (
        result.runtime_manifest_sha256
    )
    with pytest.raises(FileExistsError, match="refusing to reuse"):
        runtime_builder.build_runtime_manifest(
            output,
            source_root=REPO_ROOT,
            expected_source_commit=COMMIT,
            expected_source_tree=TREE,
            files=files,
            module_hashes=module_hashes,
            backend=backend,
            runtime_probe_factory=_probe_factory,
        )


def test_runtime_builder_cleanup_failure_has_no_commit_marker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_runtime_preconditions(monkeypatch)
    files, module_hashes = _runtime_inputs(tmp_path)
    output = tmp_path / "runtime-cleanup-failure"
    close_scopes = runner._close_all_guard_scopes
    calls = 0

    def fail_after_clean_close(
        file_guards: object, directory_guards: object
    ) -> None:
        nonlocal calls
        calls += 1
        close_scopes(file_guards, directory_guards)  # type: ignore[arg-type]
        raise runner.E00RunnerError("injected final cleanup failure")

    monkeypatch.setattr(
        runner, "_close_all_guard_scopes", fail_after_clean_close
    )
    with pytest.raises(
        runner.E00RunnerError, match="injected final cleanup failure"
    ):
        runtime_builder.build_runtime_manifest(
            output,
            source_root=REPO_ROOT,
            expected_source_commit=COMMIT,
            expected_source_tree=TREE,
            files=files,
            module_hashes=module_hashes,
            discovery_timeout_seconds=7.0,
            backend=_FakeDiscoveryBackend(),
            runtime_probe_factory=_probe_factory,
        )
    assert calls == 1
    assert (output / runtime_builder.RUNTIME_MANIFEST_NAME).is_file()
    assert not (output / runtime_builder.BUILD_RECEIPT_NAME).exists()


@pytest.mark.parametrize(
    ("backend", "message"),
    [
        (
            _FakeDiscoveryBackend(terminate=True),
            "natural zero descendants",
        ),
        (
            _FakeDiscoveryBackend(omit_module=True),
            "omits a runtime module",
        ),
    ],
)
def test_runtime_builder_rejects_bad_discovery_without_commit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    backend: _FakeDiscoveryBackend,
    message: str,
) -> None:
    _patch_runtime_preconditions(monkeypatch)
    files, module_hashes = _runtime_inputs(tmp_path)
    output = tmp_path / "runtime-design"
    with pytest.raises(runtime_builder.RuntimeManifestBuildError, match=message):
        runtime_builder.build_runtime_manifest(
            output,
            source_root=REPO_ROOT,
            expected_source_commit=COMMIT,
            expected_source_tree=TREE,
            files=files,
            module_hashes=module_hashes,
            discovery_timeout_seconds=7.0,
            backend=backend,
            runtime_probe_factory=_probe_factory,
        )
    assert not (output / runtime_builder.BUILD_RECEIPT_NAME).exists()
    assert not (output / runtime_builder.RUNTIME_MANIFEST_NAME).exists()


def test_runtime_builder_rejects_bad_execution_before_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_runtime_preconditions(monkeypatch)
    files, module_hashes = _runtime_inputs(tmp_path)
    output = tmp_path / "runtime-design"
    with pytest.raises(
        runtime_builder.RuntimeManifestBuildError, match="threads=1"
    ):
        runtime_builder.build_runtime_manifest(
            output,
            source_root=REPO_ROOT,
            expected_source_commit=COMMIT,
            expected_source_tree=TREE,
            files=files,
            module_hashes=module_hashes,
            execution=runtime_builder.ExecutionSpec(threads=2),
            backend=_FakeDiscoveryBackend(),
            runtime_probe_factory=_probe_factory,
        )
    assert not output.exists()


def test_runtime_builder_rejects_hash_and_schedule_book_drift_before_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_runtime_preconditions(monkeypatch)
    files, module_hashes = _runtime_inputs(tmp_path)
    engine = files["engine"]
    bad_hash_files = dict(files)
    bad_hash_files["engine"] = runtime_builder.ArtifactSpec(
        engine.path, "0" * 64
    )
    first_output = tmp_path / "runtime-bad-hash"
    with pytest.raises(
        runner.E00RunnerError, match="SHA-256 differs"
    ):
        runtime_builder.build_runtime_manifest(
            first_output,
            source_root=REPO_ROOT,
            expected_source_commit=COMMIT,
            expected_source_tree=TREE,
            files=bad_hash_files,
            module_hashes=module_hashes,
            backend=_FakeDiscoveryBackend(),
            runtime_probe_factory=_probe_factory,
        )
    assert not first_output.exists()

    other_book = tmp_path / "other-book.epd"
    other_book.write_text(START + "\n", encoding="utf-8", newline="\n")
    bad_book_files = dict(files)
    bad_book_files["book"] = runtime_builder.ArtifactSpec(
        other_book, _sha(other_book)
    )
    second_output = tmp_path / "runtime-bad-book"
    with pytest.raises(
        runtime_builder.RuntimeManifestBuildError,
        match="schedule/receipt/book binding differs",
    ):
        runtime_builder.build_runtime_manifest(
            second_output,
            source_root=REPO_ROOT,
            expected_source_commit=COMMIT,
            expected_source_tree=TREE,
            files=bad_book_files,
            module_hashes=module_hashes,
            backend=_FakeDiscoveryBackend(),
            runtime_probe_factory=_probe_factory,
        )
    assert not second_output.exists()


def test_schedule_cli_wires_public_builder_and_reopens_receipt(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    book = tmp_path / "book.epd"
    book.write_text(
        START + "\n" + START.replace(" 0 1", " 1 2") + "\n",
        encoding="utf-8",
        newline="\n",
    )
    schedule_path = tmp_path / "schedule.jsonl"
    receipt_path = tmp_path / "schedule.receipt.json"
    assert (
        build_e00_schedule.main(
            [
                "--book",
                str(book),
                "--seed",
                "schedule-cli-wiring",
                "--tc",
                "VSTC:2000:20:1",
                "--output",
                str(schedule_path),
                "--receipt",
                str(receipt_path),
            ]
        )
        == 0
    )
    summary = json.loads(capsys.readouterr().out)
    loaded = build_e00_schedule.load_schedule(
        schedule_path, receipt_path
    )
    assert summary["schedule_sha256"] == loaded.sha256
    assert summary["receipt_sha256"] == loaded.receipt_sha256
    assert summary["pair_count"] == 1
    assert summary["game_count"] == 2


def test_runtime_builder_cli_wires_every_input_and_deadline(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    captured: dict[str, object] = {}
    output = tmp_path / "runtime-cli-output"
    manifest = tmp_path / "manifest.json"
    discovery = tmp_path / "discovery.json"
    receipt = tmp_path / "receipt.json"
    for path, value in (
        (manifest, {"manifest": True}),
        (discovery, {"discovery": True}),
        (receipt, {"receipt": True}),
    ):
        common.write_new_json(path, value)
    protected = {manifest, discovery, receipt}
    artifact_values = {
        path: (_sha(path), path.stat().st_size) for path in protected
    }
    original_stat = Path.stat
    post_commit = False

    def fake_build(
        output_dir: Path,
        **kwargs: object,
    ) -> runtime_builder.RuntimeManifestBuild:
        nonlocal post_commit
        captured["output_dir"] = output_dir
        captured.update(kwargs)
        result = runtime_builder.RuntimeManifestBuild(
            output_dir=output,
            runtime_manifest_path=manifest,
            runtime_manifest_sha256=artifact_values[manifest][0],
            runtime_manifest_size_bytes=artifact_values[manifest][1],
            discovery_receipt_path=discovery,
            discovery_receipt_sha256=artifact_values[discovery][0],
            discovery_receipt_size_bytes=artifact_values[discovery][1],
            build_receipt_path=receipt,
            build_receipt_sha256=artifact_values[receipt][0],
            build_receipt_size_bytes=artifact_values[receipt][1],
            child_import_inventory=({"source": "test"},),
        )
        post_commit = True
        return result

    def reject_post_commit_stat(
        path: Path, *args: object, **kwargs: object
    ) -> os.stat_result:
        if post_commit and path in protected:
            raise AssertionError("CLI reopened a committed artifact")
        return original_stat(path, *args, **kwargs)

    monkeypatch.setattr(
        runtime_builder, "build_runtime_manifest", fake_build
    )
    monkeypatch.setattr(Path, "stat", reject_post_commit_stat)
    argv = [
        "--output-dir",
        str(output),
        "--source-root",
        str(REPO_ROOT),
        "--expected-source-commit",
        COMMIT,
        "--expected-source-tree",
        TREE,
    ]
    for ordinal, key in enumerate(runtime_builder._FILE_ARGUMENT_KEYS):
        option = key.replace("_", "-")
        argv.extend(
            [
                f"--{option}",
                str(tmp_path / f"{key}.bin"),
                f"--{option}-sha256",
                f"{ordinal:064x}",
            ]
        )
    for ordinal, key in enumerate(runtime_builder._MODULE_INPUT_KEYS):
        if key == "runner":
            continue
        option = key.replace("_", "-")
        argv.extend([f"--{option}-sha256", f"{ordinal + 20:064x}"])
    argv.extend(
        [
            "--threads",
            "1",
            "--maximum-plies",
            "33",
            "--command-timeout-seconds",
            "4",
            "--maximum-wall-seconds",
            "5",
            "--maximum-game-wall-seconds",
            "6",
            "--discovery-timeout-seconds",
            "7",
        ]
    )
    assert runtime_builder.main(argv) == 0
    assert captured["output_dir"] == output
    assert captured["source_root"] == REPO_ROOT
    assert captured["expected_source_commit"] == COMMIT
    assert captured["expected_source_tree"] == TREE
    assert captured["discovery_timeout_seconds"] == 7.0
    execution = captured["execution"]
    assert isinstance(execution, runtime_builder.ExecutionSpec)
    assert execution == runtime_builder.ExecutionSpec(
        threads=1,
        maximum_plies=33,
        command_timeout_seconds=4.0,
        maximum_wall_seconds=5.0,
        maximum_game_wall_seconds=6.0,
    )
    files = captured["files"]
    modules = captured["module_hashes"]
    assert isinstance(files, dict) and set(files) == set(
        runtime_builder._FILE_ARGUMENT_KEYS
    )
    assert isinstance(modules, dict) and set(modules) == (
        set(runtime_builder._MODULE_INPUT_KEYS) - {"runner"}
    )
    summary = json.loads(capsys.readouterr().out)
    assert summary["schema"] == runtime_builder.BUILD_SUMMARY_SCHEMA
    assert summary["child_import_inventory_rows"] == 1
    assert summary["runtime_manifest"]["size_bytes"] == (
        artifact_values[manifest][1]
    )
    assert summary["runtime_discovery"]["size_bytes"] == (
        artifact_values[discovery][1]
    )
    assert summary["build_receipt"]["size_bytes"] == (
        artifact_values[receipt][1]
    )


def test_runtime_builder_postflight_source_drift_has_no_commit_marker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    clean = _source_identity()
    drifted = native_cli.SourceIdentity(
        root=REPO_ROOT,
        commit=COMMIT,
        tree="c" * 40,
        clean=True,
    )
    identities = iter((clean, drifted))
    monkeypatch.setattr(
        runtime_builder,
        "_source_identity",
        lambda _root: next(identities),
    )
    monkeypatch.setattr(
        runtime_builder.atomic_outcome_helper,
        "_load_build_manifest",
        lambda *_args, **_kwargs: {"stub": "authenticated"},
    )
    files, module_hashes = _runtime_inputs(tmp_path)
    output = tmp_path / "runtime-source-drift"
    with pytest.raises(
        runtime_builder.RuntimeManifestBuildError, match="changed during"
    ):
        runtime_builder.build_runtime_manifest(
            output,
            source_root=REPO_ROOT,
            expected_source_commit=COMMIT,
            expected_source_tree=TREE,
            files=files,
            module_hashes=module_hashes,
            discovery_timeout_seconds=7.0,
            backend=_FakeDiscoveryBackend(),
            runtime_probe_factory=_probe_factory,
        )
    assert not (output / runtime_builder.BUILD_RECEIPT_NAME).exists()
