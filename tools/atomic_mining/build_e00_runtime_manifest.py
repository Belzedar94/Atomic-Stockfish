"""Build an E00 runtime-manifest-v2 through the real isolated child wire.

This builder does not start an engine or produce scientific results.  It
stages the exact nine-module runtime package used by the E00 runner, snapshots
the authenticated native rules binding, and asks
``DirectUciBackend.discover_runtime`` to execute the runner's real
``-I``/``runpy(..., run_name='__main__')`` bootstrap under ``OwnedProcess``.
The discovered import inventory is then committed into
``atomic-e00-runtime-manifest-v2`` and reopened through the runner's own
manifest validator.

``build.receipt.json`` is the final commit marker for this design bundle.
Any output directory without that receipt is terminal and must not be reused.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
import math
import os
from pathlib import Path
import stat
import sys
from typing import Callable, Mapping, Protocol, Sequence

from . import (
    atomic_outcome_helper,
    build_atomic_outcome_binding,
    build_e00_schedule,
    common,
    owned_process,
    run_e00_source as runner,
    uci_session,
)
from .build_atomic_outcome_binding_cli import (
    SourceIdentity,
    _expected_git_object,
    _outside_root,
    _source_identity,
)


BUILD_RECEIPT_SCHEMA = "atomic-e00-runtime-manifest-build-receipt-v1"
BUILD_SUMMARY_SCHEMA = "atomic-e00-runtime-manifest-build-summary-v1"
DISCOVERY_RECEIPT_NAME = "runtime-discovery.receipt.json"
RUNTIME_MANIFEST_NAME = "runtime-manifest.json"
BUILD_RECEIPT_NAME = "build.receipt.json"

_INPUT_KEYS = (
    "atomic_mining_init",
    "atomic_outcome_helper",
    "binding_builder",
    "book",
    "common",
    "current_net",
    "engine",
    "owned_process",
    "pyffish",
    "pyffish_build_manifest",
    "runner",
    "schedule",
    "schedule_builder",
    "schedule_receipt",
    "teacher_net",
    "tools_init",
    "uci_session",
    "variant_config",
)
_FILE_ARGUMENT_KEYS = (
    "book",
    "engine",
    "current_net",
    "teacher_net",
    "variant_config",
    "pyffish",
    "pyffish_build_manifest",
    "runner",
    "schedule",
    "schedule_receipt",
)
_MODULE_INPUT_KEYS = (
    "tools_init",
    "atomic_mining_init",
    "common",
    "schedule_builder",
    "runner",
    "uci_session",
    "atomic_outcome_helper",
    "binding_builder",
    "owned_process",
)


class RuntimeManifestBuildError(RuntimeError):
    """The runtime design bundle failed a closed precondition."""


@dataclass(frozen=True)
class ArtifactSpec:
    path: Path
    sha256: str


@dataclass(frozen=True)
class ExecutionSpec:
    threads: int = 1
    maximum_plies: int = 1024
    command_timeout_seconds: float = 120.0
    maximum_wall_seconds: float = 14_400.0
    maximum_game_wall_seconds: float = 1_800.0


@dataclass(frozen=True)
class RuntimeManifestBuild:
    output_dir: Path
    runtime_manifest_path: Path
    runtime_manifest_sha256: str
    runtime_manifest_size_bytes: int
    discovery_receipt_path: Path
    discovery_receipt_sha256: str
    discovery_receipt_size_bytes: int
    build_receipt_path: Path
    build_receipt_sha256: str
    build_receipt_size_bytes: int
    child_import_inventory: tuple[dict[str, object], ...]


@dataclass(frozen=True)
class _PreparedRuntimeManifest:
    """A fully validated design whose commit marker is not yet published."""

    result: RuntimeManifestBuild
    build_receipt_payload: bytes


class RuntimeDiscoveryBackend(Protocol):
    def discover_runtime(
        self,
        request_wire: Mapping[str, object],
        *,
        deadline_seconds: float,
    ) -> dict[str, object]:
        """Return the runner's authenticated discovery receipt."""


class RuntimeProbeLike(Protocol):
    def capture(self) -> Mapping[str, object]:
        """Capture one runtime identity."""


RuntimeProbeFactory = Callable[
    [Mapping[str, object], Sequence[Mapping[str, object]]],
    RuntimeProbeLike,
]


def _finite_positive(value: object, *, label: str) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(float(value))
        or float(value) <= 0
    ):
        raise RuntimeManifestBuildError(f"{label} must be finite and positive")
    return float(value)


def _positive_int(value: object, *, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise RuntimeManifestBuildError(f"{label} must be a positive integer")
    return value


def _artifact_payload(spec: ArtifactSpec, *, label: str) -> bytes:
    digest = common.require_lower_hex_sha256(
        spec.sha256, label=f"{label} expected SHA-256"
    )
    path = Path(spec.path).expanduser().resolve()
    payload = common.read_stable_file_bytes(path, label=label)
    if common.sha256_bytes(payload) != digest:
        raise RuntimeManifestBuildError(f"{label} SHA-256 differs")
    return payload


def _module_paths(source_root: Path) -> dict[str, Path]:
    root = source_root.resolve()
    return {
        key: (root / relative).resolve()
        for key, relative in runner._RUNTIME_PACKAGE_LAYOUT.items()
    }


def _module_objects() -> dict[str, object]:
    tools_module = sys.modules.get("tools")
    mining_module = sys.modules.get("tools.atomic_mining")
    if tools_module is None or mining_module is None:
        raise RuntimeManifestBuildError(
            "runtime package initializers are not loaded"
        )
    return {
        "tools_init": tools_module,
        "atomic_mining_init": mining_module,
        "common": common,
        "schedule_builder": build_e00_schedule,
        "runner": runner,
        "uci_session": uci_session,
        "atomic_outcome_helper": atomic_outcome_helper,
        "binding_builder": build_atomic_outcome_binding,
        "owned_process": owned_process,
    }


def _module_source_paths(source_root: Path) -> dict[str, Path]:
    expected = _module_paths(source_root)
    observed: dict[str, Path] = {}
    for key, module in _module_objects().items():
        raw = getattr(module, "__file__", None)
        if not isinstance(raw, str) or not raw:
            raise RuntimeManifestBuildError(
                f"runtime module {key} has no source path"
            )
        path = Path(raw).resolve()
        if path != expected[key]:
            raise RuntimeManifestBuildError(
                f"runtime module {key} is not loaded from the source root"
            )
        observed[key] = path
    return observed


def _read_canonical_object(path: Path, *, label: str) -> dict[str, object]:
    payload = common.read_stable_file_bytes(path, label=label)
    if payload.startswith(b"\xef\xbb\xbf") or b"\r" in payload:
        raise RuntimeManifestBuildError(f"{label} is not canonical UTF-8 JSON")
    try:
        value = json.loads(payload.decode("utf-8", errors="strict"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise RuntimeManifestBuildError(f"{label} is invalid JSON") from error
    if not isinstance(value, dict) or common.canonical_json_bytes(value) != payload:
        raise RuntimeManifestBuildError(f"{label} is not canonical JSON")
    return value


def _rules_source_specs(
    build_contract: Mapping[str, object],
    *,
    source_root: Path,
) -> list[tuple[str, str, Path, str]]:
    source = build_contract.get("source")
    if not isinstance(source, Mapping):
        raise RuntimeManifestBuildError(
            "native build contract source is absent"
        )
    files = source.get("files")
    if not isinstance(files, Mapping) or not files:
        raise RuntimeManifestBuildError(
            "native build contract source files are absent"
        )
    specs: list[tuple[str, str, Path, str]] = []
    root = source_root.resolve()
    for ordinal, (relative, raw) in enumerate(sorted(files.items())):
        if (
            not isinstance(relative, str)
            or not relative
            or Path(relative).is_absolute()
            or ".." in Path(relative).parts
            or not isinstance(raw, Mapping)
            or set(raw) != {"bytes", "sha256"}
            or isinstance(raw["bytes"], bool)
            or not isinstance(raw["bytes"], int)
            or raw["bytes"] < 0
        ):
            raise RuntimeManifestBuildError(
                "native build contract source file is malformed"
            )
        path = (root / relative).resolve()
        try:
            path.relative_to(root)
        except ValueError as error:
            raise RuntimeManifestBuildError(
                "native build contract source file escapes source root"
            ) from error
        specs.append(
            (
                f"rules_source_{ordinal:04d}",
                f"native rules source {relative}",
                path,
                common.require_lower_hex_sha256(
                    raw["sha256"],
                    label=f"native rules source {relative} SHA-256",
                ),
            )
        )
    return specs


def _make_read_only(path: Path) -> None:
    try:
        mode = path.stat().st_mode
        os.chmod(path, mode & ~(stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH))
    except OSError as error:
        raise RuntimeManifestBuildError(
            "cannot make staged discovery input read-only"
        ) from error


def _execution_value(spec: ExecutionSpec) -> dict[str, object]:
    if isinstance(spec.threads, bool) or spec.threads != 1:
        raise RuntimeManifestBuildError(
            "initial E00 runtime requires exactly threads=1"
        )
    maximum_plies = _positive_int(spec.maximum_plies, label="maximum_plies")
    command_timeout = _finite_positive(
        spec.command_timeout_seconds, label="command_timeout_seconds"
    )
    maximum_wall = _finite_positive(
        spec.maximum_wall_seconds, label="maximum_wall_seconds"
    )
    maximum_game_wall = _finite_positive(
        spec.maximum_game_wall_seconds,
        label="maximum_game_wall_seconds",
    )
    return {
        "threads": 1,
        "maximum_plies": maximum_plies,
        "command_timeout_seconds": command_timeout,
        "maximum_wall_seconds": maximum_wall,
        "maximum_game_wall_seconds": maximum_game_wall,
        "uci_options": [
            {"name": "UCI_Variant", "value": "atomic"},
            {"name": "Threads", "value": 1},
            {"name": "Hash", "value": 512},
            {"name": "MultiPV", "value": 1},
            {"name": "Ponder", "value": False},
            {"name": "SyzygyPath", "value": ""},
            {"name": "SyzygyProbeLimit", "value": 0},
            {"name": "Use NNUE", "value": "true"},
        ],
        "clock_policy": {
            "charged_interval": (
                "complete-go-write-flush-to-complete-bestmove-newline"
            ),
            "equality": "elapsed-ns-equal-remaining-ns-is-on-time",
            "uci_millisecond_conversion": "floor-nanoseconds",
        },
    }


def _validate_schedule_binding(
    artifacts: Mapping[str, ArtifactSpec],
) -> None:
    try:
        snapshot = build_e00_schedule.load_schedule(
            artifacts["schedule"].path,
            artifacts["schedule_receipt"].path,
        )
    except (
        build_e00_schedule.E00ScheduleError,
        common.MiningArtifactError,
        OSError,
    ) as error:
        raise RuntimeManifestBuildError(
            "schedule/receipt validation failed"
        ) from error
    if (
        snapshot.sha256 != artifacts["schedule"].sha256
        or snapshot.receipt_sha256
        != artifacts["schedule_receipt"].sha256
        or not snapshot.rows
        or any(
            row.get("book_sha256") != artifacts["book"].sha256
            for row in snapshot.rows
        )
    ):
        raise RuntimeManifestBuildError(
            "schedule/receipt/book binding differs"
        )


def _validate_process_evidence(value: object) -> dict[str, object]:
    if not isinstance(value, Mapping):
        raise RuntimeManifestBuildError(
            "runtime discovery process evidence is absent"
        )
    common.require_exact_fields(
        value,
        (
            "schema",
            "containment",
            "kill_on_close",
            "created_suspended_before_assignment",
            "resumed_primary_thread",
            "active_processes_zero",
            "termination_requested",
        ),
        label="runtime discovery process evidence",
    )
    expected_containment = (
        "windows-job-object" if os.name == "nt" else "posix-process-group"
    )
    if (
        value["schema"] != "atomic-e00-owned-process-v1"
        or value["containment"] != expected_containment
        or value["kill_on_close"] is not (os.name == "nt")
        or value["created_suspended_before_assignment"] is not (os.name == "nt")
        or value["resumed_primary_thread"] is not (os.name == "nt")
        or value["active_processes_zero"] is not True
        or value["termination_requested"] is not False
    ):
        raise RuntimeManifestBuildError(
            "runtime discovery process did not prove natural zero descendants"
        )
    return dict(value)


def _validate_discovery(
    receipt: object,
    *,
    request: Mapping[str, object],
    staged_modules: Mapping[str, Path],
    staged_pyffish: Path,
    staged_build_manifest: Path,
    artifacts: Mapping[str, ArtifactSpec],
    source: SourceIdentity,
) -> tuple[dict[str, object], list[dict[str, object]]]:
    if not isinstance(receipt, Mapping):
        raise RuntimeManifestBuildError("runtime discovery receipt is absent")
    common.require_exact_fields(
        receipt,
        ("schema", "request", "result", "process"),
        label="runtime discovery receipt",
    )
    if (
        receipt["schema"] != runner.RUNTIME_DISCOVERY_RECEIPT_SCHEMA
        or receipt["request"] != dict(request)
    ):
        raise RuntimeManifestBuildError(
            "runtime discovery receipt request binding differs"
        )
    _validate_process_evidence(receipt["process"])
    result = receipt["result"]
    if not isinstance(result, Mapping):
        raise RuntimeManifestBuildError("runtime discovery result is absent")
    common.require_exact_fields(
        result,
        (
            "schema",
            "request_sha256",
            "runtime_modules_sha256",
            "native_validation",
            "child_import_inventory",
        ),
        label="runtime discovery result",
    )
    runtime_rows = request["runtime_modules"]
    if (
        result["schema"] != runner.INTERNAL_RUNTIME_DISCOVERY_RESULT_SCHEMA
        or result["request_sha256"]
        != common.sha256_bytes(common.canonical_json_bytes(dict(request)))
        or result["runtime_modules_sha256"]
        != runner._digest_document(
            "atomic-e00-runtime-module-inventory-v2", runtime_rows
        )
    ):
        raise RuntimeManifestBuildError(
            "runtime discovery result digest binding differs"
        )
    expected_native = {
        "pyffish_path": str(staged_pyffish),
        "pyffish_sha256": artifacts["pyffish"].sha256,
        "pyffish_size_bytes": staged_pyffish.stat().st_size,
        "manifest_binding_path": str(artifacts["pyffish"].path.resolve()),
        "build_manifest_path": str(staged_build_manifest),
        "build_manifest_sha256": artifacts[
            "pyffish_build_manifest"
        ].sha256,
        "build_manifest_size_bytes": staged_build_manifest.stat().st_size,
        "source_root": str(source.root),
        "source_commit": source.commit,
    }
    if result["native_validation"] != expected_native:
        raise RuntimeManifestBuildError(
            "runtime discovery native validation differs"
        )
    inventory = runner._validate_child_import_inventory(
        result["child_import_inventory"],
        label="runtime discovery child import inventory",
    )
    runtime_expected = {
        relative.as_posix(): {
            "sha256": artifacts[key].sha256,
            "size_bytes": staged_modules[key].stat().st_size,
        }
        for key, relative in runner._RUNTIME_PACKAGE_LAYOUT.items()
    }
    observed_runtime: set[str] = set()
    observed_pyffish = 0
    for row in inventory:
        if row["source"] == "runtime-package":
            path = str(row["path"])
            expected = runtime_expected.get(path)
            if expected is None or (
                row["sha256"],
                row["size_bytes"],
            ) != (expected["sha256"], expected["size_bytes"]):
                raise RuntimeManifestBuildError(
                    "runtime discovery package inventory differs"
                )
            observed_runtime.add(path)
        elif row["source"] == "pyffish":
            if (
                row["sha256"] != artifacts["pyffish"].sha256
                or row["size_bytes"] != staged_pyffish.stat().st_size
            ):
                raise RuntimeManifestBuildError(
                    "runtime discovery pyffish inventory differs"
                )
            observed_pyffish += 1
        else:
            path = Path(str(row["path"]))
            payload = common.read_stable_file_bytes(
                path, label="runtime discovery Python import"
            )
            if (
                common.sha256_bytes(payload) != row["sha256"]
                or len(payload) != row["size_bytes"]
            ):
                raise RuntimeManifestBuildError(
                    "runtime discovery Python import changed"
                )
    if observed_runtime != set(runtime_expected) or observed_pyffish != 1:
        raise RuntimeManifestBuildError(
            "runtime discovery inventory omits a runtime module or pyffish"
        )
    return dict(receipt), inventory


def _validate_runtime_capture(
    runtime: object,
    *,
    source: SourceIdentity,
    native_rules: Mapping[str, object],
    inventory: Sequence[Mapping[str, object]],
    artifacts: Mapping[str, ArtifactSpec],
    payloads: Mapping[str, bytes],
) -> dict[str, object]:
    if not isinstance(runtime, Mapping):
        raise RuntimeManifestBuildError("runtime probe result is not an object")
    common.require_exact_fields(
        runtime,
        ("python", "modules", "native_rules", "source"),
        label="runtime probe result",
    )
    expected_source = {
        "root": str(source.root),
        "commit": source.commit,
        "tree": source.tree,
        "clean": True,
    }
    if runtime["source"] != expected_source:
        raise RuntimeManifestBuildError(
            "runtime probe source identity differs"
        )
    if runtime["native_rules"] != dict(native_rules):
        raise RuntimeManifestBuildError(
            "runtime probe native rules identity differs"
        )
    modules = runtime["modules"]
    expected_module_keys = {
        "tools_init": "tools_init",
        "atomic_mining_init": "atomic_mining_init",
        "common": "common",
        "schedule_builder": "schedule_builder",
        "wrapper": "runner",
        "uci_session": "uci_session",
        "atomic_outcome_helper": "atomic_outcome_helper",
        "binding_builder": "binding_builder",
        "owned_process": "owned_process",
    }
    if not isinstance(modules, Mapping) or set(modules) != set(
        expected_module_keys
    ):
        raise RuntimeManifestBuildError(
            "runtime probe module identity set differs"
        )
    for runtime_key, input_key in expected_module_keys.items():
        expected = {
            "path": str(artifacts[input_key].path.resolve()),
            "sha256": artifacts[input_key].sha256,
            "size_bytes": len(payloads[input_key]),
        }
        if modules[runtime_key] != expected:
            raise RuntimeManifestBuildError(
                f"runtime probe module {runtime_key} differs"
            )
    python = runtime["python"]
    if not isinstance(python, Mapping):
        raise RuntimeManifestBuildError(
            "runtime probe Python identity is absent"
        )
    common.require_exact_fields(
        python,
        (
            "executable",
            "executable_sha256",
            "executable_size_bytes",
            "runtime_libraries",
            "child_import_inventory",
            "version",
        ),
        label="runtime probe Python identity",
    )
    executable = Path(sys.executable).resolve()
    executable_payload = common.read_stable_file_bytes(
        executable, label="runtime probe Python executable"
    )
    expected_inventory = runner._validate_child_import_inventory(
        [dict(row) for row in inventory],
        label="runtime builder expected child import inventory",
    )
    if (
        python["executable"] != str(executable)
        or python["executable_sha256"]
        != common.sha256_bytes(executable_payload)
        or python["executable_size_bytes"] != len(executable_payload)
        or python["runtime_libraries"]
        != atomic_outcome_helper._python_runtime_artifacts()
        or python["child_import_inventory"] != expected_inventory
        or python["version"] != sys.version
    ):
        raise RuntimeManifestBuildError(
            "runtime probe Python identity differs"
        )
    return dict(runtime)


def _input_specs(
    *,
    source_root: Path,
    files: Mapping[str, ArtifactSpec],
    module_hashes: Mapping[str, str],
) -> dict[str, ArtifactSpec]:
    if set(files) != set(_FILE_ARGUMENT_KEYS):
        raise RuntimeManifestBuildError(
            "runtime builder file inputs are missing or unexpected"
        )
    if set(module_hashes) != set(_MODULE_INPUT_KEYS) - {"runner"}:
        raise RuntimeManifestBuildError(
            "runtime builder module hashes are missing or unexpected"
        )
    modules = _module_source_paths(source_root)
    result = dict(files)
    for key in _MODULE_INPUT_KEYS:
        if key == "runner":
            if files["runner"].path.resolve() != modules[key]:
                raise RuntimeManifestBuildError(
                    "requested runner is not the executing runner module"
                )
            continue
        result[key] = ArtifactSpec(
            path=modules[key],
            sha256=common.require_lower_hex_sha256(
                module_hashes[key],
                label=f"{key} expected SHA-256",
            ),
        )
    if set(result) != set(_INPUT_KEYS):
        raise RuntimeManifestBuildError(
            "runtime manifest input set differs from v2"
        )
    return result


def _default_probe_factory(
    native_rules: Mapping[str, object],
    inventory: Sequence[Mapping[str, object]],
) -> RuntimeProbeLike:
    return runner.DefaultRuntimeProbe(native_rules, inventory)


@runner._artifact_guard_scoped
def _prepare_runtime_manifest(
    output_dir: Path,
    *,
    source_root: Path,
    expected_source_commit: str,
    expected_source_tree: str,
    files: Mapping[str, ArtifactSpec],
    module_hashes: Mapping[str, str],
    execution: ExecutionSpec = ExecutionSpec(),
    discovery_timeout_seconds: float = 120.0,
    backend: RuntimeDiscoveryBackend | None = None,
    runtime_probe_factory: RuntimeProbeFactory | None = None,
) -> _PreparedRuntimeManifest:
    """Build and validate one runtime design before commit publication."""

    expected_commit = _expected_git_object(
        expected_source_commit, label="expected source commit"
    )
    expected_tree = _expected_git_object(
        expected_source_tree, label="expected source tree"
    )
    discovery_timeout = _finite_positive(
        discovery_timeout_seconds, label="discovery_timeout_seconds"
    )
    execution_value = _execution_value(execution)
    output = Path(output_dir).expanduser().resolve()
    if output.exists():
        raise FileExistsError(
            f"refusing to reuse runtime-manifest output: {output}"
        )
    before = _source_identity(source_root)
    if (
        before.commit != expected_commit
        or before.tree != expected_tree
        or not before.clean
    ):
        raise RuntimeManifestBuildError(
            "source commit/tree/clean state differs before runtime build"
        )
    if not _outside_root(output, before.root):
        raise RuntimeManifestBuildError(
            "runtime-manifest output must be outside the source root"
        )

    artifacts = _input_specs(
        source_root=before.root,
        files=files,
        module_hashes=module_hashes,
    )
    locked_inputs = runner._bind_artifact_specs(
        [
            (
                key,
                f"runtime builder input {key}",
                spec.path,
                spec.sha256,
            )
            for key, spec in sorted(artifacts.items())
        ]
    )
    payloads = {
        key: common.read_stable_file_bytes(
            binding.path, label=f"locked runtime input {key}"
        )
        for key, binding in sorted(locked_inputs.items())
    }
    _validate_schedule_binding(artifacts)
    build_contract = _read_canonical_object(
        artifacts["pyffish_build_manifest"].path,
        label="native binding build manifest",
    )
    try:
        atomic_outcome_helper._load_build_manifest(
            artifacts["pyffish_build_manifest"].path,
            artifacts["pyffish_build_manifest"].sha256,
            native_binding={
                "bytes": len(payloads["pyffish"]),
                "path": str(artifacts["pyffish"].path.resolve()),
                "sha256": artifacts["pyffish"].sha256,
            },
            source_root=before.root,
            source_commit=before.commit,
            manifest_binding_path=artifacts["pyffish"].path,
        )
    except atomic_outcome_helper.AtomicOutcomeHelperError as error:
        raise RuntimeManifestBuildError(
            "native build manifest did not authenticate"
        ) from error
    rules_source_bindings = runner._bind_artifact_specs(
        _rules_source_specs(build_contract, source_root=before.root)
    )

    output.mkdir(parents=True, exist_ok=False)
    runtime_root = output / "runtime-package"
    runtime_root.mkdir()
    staged_modules: dict[str, Path] = {}
    for key, relative in runner._RUNTIME_PACKAGE_LAYOUT.items():
        destination = runtime_root / relative
        common.write_new_bytes(destination, payloads[key])
        _make_read_only(destination)
        staged_modules[key] = destination.resolve()
    discovery_inputs = output / "discovery-inputs"
    discovery_inputs.mkdir()
    staged_pyffish = discovery_inputs / artifacts["pyffish"].path.name
    staged_manifest = discovery_inputs / "native-build-manifest.json"
    common.write_new_bytes(staged_pyffish, payloads["pyffish"])
    common.write_new_bytes(
        staged_manifest, payloads["pyffish_build_manifest"]
    )
    _make_read_only(staged_pyffish)
    _make_read_only(staged_manifest)
    staged_pyffish = staged_pyffish.resolve()
    staged_manifest = staged_manifest.resolve()

    staged_bindings = runner._bind_artifact_specs(
        [
            (
                f"staged_module_{key}",
                f"staged runtime module {key}",
                staged_modules[key],
                artifacts[key].sha256,
            )
            for key in sorted(staged_modules)
        ]
        + [
            (
                "staged_pyffish",
                "staged runtime pyffish",
                staged_pyffish,
                artifacts["pyffish"].sha256,
            ),
            (
                "staged_build_manifest",
                "staged native build manifest",
                staged_manifest,
                artifacts["pyffish_build_manifest"].sha256,
            ),
        ]
    )
    # File guards retain immutable content handles.  Parent namespace handles
    # are deliberately delegated before strict directory seals are acquired;
    # otherwise the two no-delete-share strategies would conflict on Windows.
    runner._delegate_parent_locks(staged_bindings)
    runner._delegate_parent_locks(
        {
            "pyffish": locked_inputs["pyffish"],
            "pyffish_build_manifest": locked_inputs[
                "pyffish_build_manifest"
            ],
        }
    )
    runner._delegate_parent_locks(rules_source_bindings)
    runtime_namespace = runner._directory_binding(
        "runtime builder executed package",
        runtime_root,
        strict_static=True,
    )
    runtime_inventory = runner._verify_directory_binding(
        runtime_namespace
    )
    discovery_namespace = runner._directory_binding(
        "runtime builder native snapshots",
        discovery_inputs,
        strict_static=True,
    )
    discovery_inventory = runner._verify_directory_binding(
        discovery_namespace
    )
    native_root = artifacts["pyffish_build_manifest"].path.resolve().parent
    try:
        artifacts["pyffish"].path.resolve().relative_to(native_root)
    except ValueError as error:
        raise RuntimeManifestBuildError(
            "native binding and build manifest do not share one build root"
        ) from error
    native_namespace = runner._directory_binding(
        "runtime builder original native bundle",
        native_root,
        strict_static=True,
    )
    native_inventory = runner._verify_directory_binding(native_namespace)
    source_namespace = runner._directory_binding(
        "runtime builder native rules source",
        before.root / "src",
        strict_static=True,
    )
    source_inventory = runner._verify_directory_binding(source_namespace)

    request = runner.build_runtime_discovery_request(
        runtime_package_root=runtime_root,
        pyffish=staged_pyffish,
        pyffish_sha256=artifacts["pyffish"].sha256,
        pyffish_manifest_binding_path=artifacts["pyffish"].path,
        pyffish_build_manifest=staged_manifest,
        pyffish_build_manifest_sha256=artifacts[
            "pyffish_build_manifest"
        ].sha256,
        source_root=before.root,
        source_commit=before.commit,
    )
    discovery_backend = backend or runner.DirectUciBackend()
    try:
        raw_receipt = discovery_backend.discover_runtime(
            request, deadline_seconds=discovery_timeout
        )
    except (
        runner.E00RunnerError,
        owned_process.OwnedProcessError,
        common.MiningArtifactError,
        OSError,
        ValueError,
    ) as error:
        raise RuntimeManifestBuildError(
            "isolated runtime discovery failed"
        ) from error
    discovery_receipt, inventory = _validate_discovery(
        raw_receipt,
        request=request,
        staged_modules=staged_modules,
        staged_pyffish=staged_pyffish,
        staged_build_manifest=staged_manifest,
        artifacts=artifacts,
        source=before,
    )
    runner._verify_directory_binding(
        runtime_namespace, expected_inventory=runtime_inventory
    )
    runner._verify_directory_binding(
        discovery_namespace, expected_inventory=discovery_inventory
    )
    runner._verify_directory_binding(
        native_namespace, expected_inventory=native_inventory
    )
    runner._verify_directory_binding(
        source_namespace, expected_inventory=source_inventory
    )
    try:
        atomic_outcome_helper._load_build_manifest(
            artifacts["pyffish_build_manifest"].path,
            artifacts["pyffish_build_manifest"].sha256,
            native_binding={
                "bytes": len(payloads["pyffish"]),
                "path": str(artifacts["pyffish"].path.resolve()),
                "sha256": artifacts["pyffish"].sha256,
            },
            source_root=before.root,
            source_commit=before.commit,
            manifest_binding_path=artifacts["pyffish"].path,
        )
    except atomic_outcome_helper.AtomicOutcomeHelperError as error:
        raise RuntimeManifestBuildError(
            "native build contract changed during discovery"
        ) from error
    discovery_path = output / DISCOVERY_RECEIPT_NAME
    common.write_new_json(discovery_path, discovery_receipt)
    discovery_payload = common.read_stable_file_bytes(
        discovery_path, label="published runtime discovery receipt"
    )

    native_rules = {
        "binding": {
            "path": str(artifacts["pyffish"].path.resolve()),
            "sha256": artifacts["pyffish"].sha256,
            "size_bytes": len(payloads["pyffish"]),
        },
        "build_contract": build_contract,
        "build_manifest": {
            "path": str(
                artifacts["pyffish_build_manifest"].path.resolve()
            ),
            "sha256": artifacts["pyffish_build_manifest"].sha256,
            "size_bytes": len(payloads["pyffish_build_manifest"]),
        },
        "source_commit": before.commit,
        "source_root": str(before.root),
    }
    probe_factory = runtime_probe_factory or _default_probe_factory
    probe = probe_factory(native_rules, inventory)
    runtime = _validate_runtime_capture(
        probe.capture(),
        source=before,
        native_rules=native_rules,
        inventory=inventory,
        artifacts=artifacts,
        payloads=payloads,
    )
    input_hashes = {
        key: artifacts[key].sha256 for key in sorted(artifacts)
    }
    manifest_value = {
        "schema": runner.RUNTIME_MANIFEST_SCHEMA,
        "runtime": runtime,
        "execution": execution_value,
        "inputs": input_hashes,
    }
    manifest_path = output / RUNTIME_MANIFEST_NAME
    common.write_new_json(manifest_path, manifest_value)
    manifest_payload = common.read_stable_file_bytes(
        manifest_path, label="published runtime manifest"
    )
    manifest_sha256 = common.sha256_bytes(manifest_payload)

    verification_probe = probe_factory(native_rules, inventory)
    value: dict[str, object] | None = None
    binding = None
    try:
        value, binding, observed = runner._runtime_manifest(
            manifest_path,
            manifest_sha256,
            expected_execution=execution_value,
            expected_inputs=input_hashes,
            runtime_probe=verification_probe,
        )
        if value != manifest_value or dict(observed) != runtime:
            raise RuntimeManifestBuildError(
                "runner reopened a different runtime manifest"
            )
    finally:
        if binding is not None:
            binding.close()

    after = _source_identity(before.root)
    if after != before:
        raise RuntimeManifestBuildError(
            "source commit/tree/clean state changed during runtime build"
        )
    for key, artifact in sorted(artifacts.items()):
        observed = _artifact_payload(
            artifact, label=f"postflight runtime input {key}"
        )
        if observed != payloads[key]:
            raise RuntimeManifestBuildError(
                f"runtime input {key} changed during build"
            )
        runner._verify_artifact_unchanged(locked_inputs[key])
    for binding in staged_bindings.values():
        runner._verify_artifact_unchanged(binding)
    for binding in rules_source_bindings.values():
        runner._verify_artifact_unchanged(binding)
    runner._verify_directory_binding(
        runtime_namespace, expected_inventory=runtime_inventory
    )
    runner._verify_directory_binding(
        discovery_namespace, expected_inventory=discovery_inventory
    )
    runner._verify_directory_binding(
        native_namespace, expected_inventory=native_inventory
    )
    runner._verify_directory_binding(
        source_namespace, expected_inventory=source_inventory
    )
    if (
        common.read_stable_file_bytes(
            manifest_path, label="postflight runtime manifest"
        )
        != manifest_payload
        or common.read_stable_file_bytes(
            discovery_path, label="postflight discovery receipt"
        )
        != discovery_payload
    ):
        raise RuntimeManifestBuildError(
            "runtime design artifacts changed before commit"
        )

    receipt_value = {
        "schema": BUILD_RECEIPT_SCHEMA,
        "source": {
            "root": str(before.root),
            "commit": before.commit,
            "tree": before.tree,
        },
        "inputs": input_hashes,
        "execution": execution_value,
        "runtime_manifest": {
            "path": str(manifest_path.resolve()),
            "sha256": manifest_sha256,
            "size_bytes": len(manifest_payload),
        },
        "runtime_discovery": {
            "path": str(discovery_path.resolve()),
            "sha256": common.sha256_bytes(discovery_payload),
            "size_bytes": len(discovery_payload),
        },
        "child_import_inventory": inventory,
    }
    build_receipt_path = output / BUILD_RECEIPT_NAME
    build_receipt_payload = common.canonical_json_bytes(receipt_value)
    try:
        reopened_receipt = json.loads(
            build_receipt_payload.decode("utf-8", errors="strict")
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise RuntimeManifestBuildError(
            "prepared runtime build receipt is not valid JSON"
        ) from error
    if (
        reopened_receipt != receipt_value
        or common.canonical_json_bytes(reopened_receipt)
        != build_receipt_payload
    ):
        raise RuntimeManifestBuildError(
            "prepared runtime build receipt is not canonical"
        )
    return _PreparedRuntimeManifest(
        result=RuntimeManifestBuild(
            output_dir=output,
            runtime_manifest_path=manifest_path.resolve(),
            runtime_manifest_sha256=manifest_sha256,
            runtime_manifest_size_bytes=len(manifest_payload),
            discovery_receipt_path=discovery_path.resolve(),
            discovery_receipt_sha256=common.sha256_bytes(
                discovery_payload
            ),
            discovery_receipt_size_bytes=len(discovery_payload),
            build_receipt_path=build_receipt_path.resolve(),
            build_receipt_sha256=common.sha256_bytes(
                build_receipt_payload
            ),
            build_receipt_size_bytes=len(build_receipt_payload),
            child_import_inventory=tuple(
                dict(row) for row in inventory
            ),
        ),
        build_receipt_payload=build_receipt_payload,
    )


def build_runtime_manifest(
    output_dir: Path,
    *,
    source_root: Path,
    expected_source_commit: str,
    expected_source_tree: str,
    files: Mapping[str, ArtifactSpec],
    module_hashes: Mapping[str, str],
    execution: ExecutionSpec = ExecutionSpec(),
    discovery_timeout_seconds: float = 120.0,
    backend: RuntimeDiscoveryBackend | None = None,
    runtime_probe_factory: RuntimeProbeFactory | None = None,
) -> RuntimeManifestBuild:
    """Build, validate, close all guards, then publish the commit marker."""

    prepared = _prepare_runtime_manifest(
        output_dir,
        source_root=source_root,
        expected_source_commit=expected_source_commit,
        expected_source_tree=expected_source_tree,
        files=files,
        module_hashes=module_hashes,
        execution=execution,
        discovery_timeout_seconds=discovery_timeout_seconds,
        backend=backend,
        runtime_probe_factory=runtime_probe_factory,
    )
    # This atomic create-new publication is deliberately the last fallible
    # operation.  The decorated preparation has already closed and verified
    # every immutable file/directory guard and the shared namespace registry.
    common.write_new_bytes(
        prepared.result.build_receipt_path,
        prepared.build_receipt_payload,
    )
    return prepared.result


def _artifact_arguments(
    arguments: argparse.Namespace,
) -> tuple[dict[str, ArtifactSpec], dict[str, str]]:
    files = {
        key: ArtifactSpec(
            path=getattr(arguments, key),
            sha256=getattr(arguments, f"{key}_sha256"),
        )
        for key in _FILE_ARGUMENT_KEYS
    }
    modules = {
        key: getattr(arguments, f"{key}_sha256")
        for key in _MODULE_INPUT_KEYS
        if key != "runner"
    }
    return files, modules


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Build a create-new E00 runtime-manifest-v2 through the exact "
            "isolated child bootstrap; no engine is started."
        )
    )
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--source-root", required=True, type=Path)
    parser.add_argument("--expected-source-commit", required=True)
    parser.add_argument("--expected-source-tree", required=True)
    for key in _FILE_ARGUMENT_KEYS:
        option = key.replace("_", "-")
        parser.add_argument(f"--{option}", required=True, type=Path)
        parser.add_argument(f"--{option}-sha256", required=True)
    for key in _MODULE_INPUT_KEYS:
        if key == "runner":
            continue
        option = key.replace("_", "-")
        parser.add_argument(f"--{option}-sha256", required=True)
    parser.add_argument("--threads", type=int, default=1)
    parser.add_argument("--maximum-plies", type=int, default=1024)
    parser.add_argument(
        "--command-timeout-seconds", type=float, default=120.0
    )
    parser.add_argument(
        "--maximum-wall-seconds", type=float, default=14_400.0
    )
    parser.add_argument(
        "--maximum-game-wall-seconds", type=float, default=1_800.0
    )
    parser.add_argument(
        "--discovery-timeout-seconds", type=float, default=120.0
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    files, modules = _artifact_arguments(arguments)
    try:
        result = build_runtime_manifest(
            arguments.output_dir,
            source_root=arguments.source_root,
            expected_source_commit=arguments.expected_source_commit,
            expected_source_tree=arguments.expected_source_tree,
            files=files,
            module_hashes=modules,
            execution=ExecutionSpec(
                threads=arguments.threads,
                maximum_plies=arguments.maximum_plies,
                command_timeout_seconds=(
                    arguments.command_timeout_seconds
                ),
                maximum_wall_seconds=arguments.maximum_wall_seconds,
                maximum_game_wall_seconds=(
                    arguments.maximum_game_wall_seconds
                ),
            ),
            discovery_timeout_seconds=(
                arguments.discovery_timeout_seconds
            ),
        )
    except (
        RuntimeManifestBuildError,
        runner.E00RunnerError,
        atomic_outcome_helper.AtomicOutcomeHelperError,
        build_e00_schedule.E00ScheduleError,
        common.MiningArtifactError,
        FileExistsError,
        OSError,
        ValueError,
    ) as error:
        print(f"runtime manifest NO-GO: {error}", file=sys.stderr)
        return 2
    summary = {
        "schema": BUILD_SUMMARY_SCHEMA,
        "output_dir": str(result.output_dir),
        "runtime_manifest": {
            "path": str(result.runtime_manifest_path),
            "sha256": result.runtime_manifest_sha256,
            "size_bytes": result.runtime_manifest_size_bytes,
        },
        "runtime_discovery": {
            "path": str(result.discovery_receipt_path),
            "sha256": result.discovery_receipt_sha256,
            "size_bytes": result.discovery_receipt_size_bytes,
        },
        "build_receipt": {
            "path": str(result.build_receipt_path),
            "sha256": result.build_receipt_sha256,
            "size_bytes": result.build_receipt_size_bytes,
        },
        "child_import_inventory_rows": len(
            result.child_import_inventory
        ),
    }
    sys.stdout.buffer.write(common.canonical_json_bytes(summary))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "ArtifactSpec",
    "BUILD_RECEIPT_SCHEMA",
    "ExecutionSpec",
    "RuntimeManifestBuild",
    "RuntimeManifestBuildError",
    "build_runtime_manifest",
    "main",
]
