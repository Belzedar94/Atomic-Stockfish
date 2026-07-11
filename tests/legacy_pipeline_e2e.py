#!/usr/bin/env python3
"""Exercise the complete Legacy Atomic V1 data-to-engine pipeline.

This is intentionally an end-to-end gate rather than a collection of mocks.  It
connects the historical 72-byte generator wire, the native trainer loader, one
real optimizer update, the v1 serializer, and Atomic-Stockfish's NNUE loader.
"""

from __future__ import annotations

import argparse
from contextlib import redirect_stdout
from dataclasses import dataclass
import hashlib
import importlib
from importlib import metadata as importlib_metadata
import io
import json
import math
import os
from pathlib import Path
import queue
import shutil
import struct
import subprocess
import sys
import sysconfig
import tempfile
import threading
import time
from types import ModuleType
from typing import Callable, Mapping, Sequence

from atomic_compiler_preflight import (
    CompilerPreflightError,
    FileFingerprint,
    fingerprint_file,
    fingerprint_files,
    verify_file_fingerprints,
)
from legacy_pipeline_build_manifest import verify_build_manifest
from legacy_pipeline_lock import (
    CheckoutState,
    DEFAULT_LOCK_FILE,
    PipelineLock,
    PipelineProfile,
    find_checkout_root,
    load_pipeline_lock,
    verify_release_checkouts,
)


RECORD_SIZE = 72
MOVE_OFFSET = 66
PLY_OFFSET = 68
RESULT_OFFSET = 70
PADDING_OFFSET = 71
LEGACY_NNUE_VERSION = 0x7AF32F20
LEGACY_NNUE_ARCHITECTURE = 0x3C103E72
TOOLS_PURE_OPTION = "option name Use NNUE type combo default true var true var false var pure"
TOOLS_PURE_LOAD_SUFFIX = " enabled (Use NNUE=pure)"
TOOLS_TIMEOUT_SECONDS = 180.0
ENGINE_TIMEOUT_SECONDS = 60.0
REPO_ROOT = Path(__file__).resolve().parents[1]
SYNTHETIC_DESCRIPTION = "Atomic-Stockfish LegacyAtomicV1 synthetic CI source"
PYTHON_DEPENDENCIES = (
    ("torch", "torch"),
    ("numpy", "numpy"),
    ("pytorch_lightning", "pytorch-lightning"),
    ("torchmetrics", "torchmetrics"),
)


@dataclass(frozen=True)
class PlainRecord:
    fen: str
    move: str
    score: int
    ply: int
    result: int


@dataclass(frozen=True)
class PythonDependencyProvenance:
    """Exact imported identity of one trainer runtime dependency."""

    module_name: str
    distribution_name: str
    version: str
    module_origin: Path
    metadata_origin: Path
    installed_files: int
    installed_files_sha256: str
    fingerprints: tuple[FileFingerprint, ...]

    @property
    def manifest_sha256(self) -> str:
        return fingerprint_manifest_sha256(self.fingerprints)


@dataclass(frozen=True)
class PythonEnvironmentProvenance:
    """Preflight snapshot of CPython and every imported dependency artifact."""

    implementation: str
    version: str
    version_info: tuple[int, int, int, str, int]
    cache_tag: str
    hexversion: int
    executable: Path
    runtime_fingerprints: tuple[FileFingerprint, ...]
    dependencies: tuple[PythonDependencyProvenance, ...]

    @property
    def fingerprints(self) -> tuple[FileFingerprint, ...]:
        return self.runtime_fingerprints + tuple(
            fingerprint
            for dependency in self.dependencies
            for fingerprint in dependency.fingerprints
        )

    @property
    def manifest_sha256(self) -> str:
        return fingerprint_manifest_sha256(self.fingerprints)


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def fingerprint_manifest_sha256(
    fingerprints: Sequence[FileFingerprint],
) -> str:
    """Hash paths and file identities into one compact, auditable manifest ID."""

    digest = hashlib.sha256()
    for fingerprint in fingerprints:
        row = json.dumps(
            (
                fingerprint.label,
                str(fingerprint.path),
                fingerprint.size,
                fingerprint.sha256,
            ),
            ensure_ascii=False,
            separators=(",", ":"),
        )
        digest.update(row.encode("utf-8"))
        digest.update(b"\n")
    return digest.hexdigest().upper()


def path_manifest_sha256(paths: Sequence[Path]) -> str:
    digest = hashlib.sha256()
    for path in paths:
        digest.update(str(path).encode("utf-8"))
        digest.update(b"\n")
    return digest.hexdigest().upper()


def _require_version(value: object, label: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise AssertionError(f"{label} has no exact non-empty version string")
    return value


def _python_runtime_paths() -> tuple[Path, ...]:
    candidates: list[Path] = []

    def add(raw_path: object) -> None:
        if not isinstance(raw_path, (str, os.PathLike)) or not raw_path:
            return
        path = Path(raw_path).expanduser()
        if path.is_file():
            resolved = path.resolve()
            if resolved not in candidates:
                candidates.append(resolved)

    add(sys.executable)
    add(getattr(sys, "_base_executable", None))

    library_names = {
        value
        for key in ("LDLIBRARY", "INSTSONAME")
        if isinstance((value := sysconfig.get_config_var(key)), str) and value
    }
    library_roots = {
        Path(value)
        for value in (
            sysconfig.get_config_var("LIBDIR"),
            sys.prefix,
            sys.base_prefix,
        )
        if isinstance(value, str) and value
    }
    for library_name in library_names:
        library = Path(library_name)
        if library.is_absolute():
            add(library)
        else:
            for root in library_roots:
                add(root / library)

    if sys.platform == "win32":
        dll_name = f"python{sys.version_info.major}{sys.version_info.minor}.dll"
        for root in (Path(sys.prefix), Path(sys.base_prefix)):
            add(root / dll_name)
            add(root / "DLLs" / dll_name)

    executable = Path(sys.executable).expanduser().resolve()
    if not executable.is_file() or executable not in candidates:
        raise AssertionError(f"CPython executable does not exist: {executable}")
    return tuple(sorted(candidates, key=lambda path: os.path.normcase(str(path))))


def _distribution_artifacts(
    distribution: object, distribution_name: str
) -> tuple[set[Path], Path, Path]:
    entries = getattr(distribution, "files", None)
    if entries is None:
        raise AssertionError(
            f"Python distribution {distribution_name} exposes no installed file list"
        )
    entries = tuple(entries)
    if not entries:
        raise AssertionError(
            f"Python distribution {distribution_name} has an empty installed file list"
        )

    located: list[tuple[Path, Path]] = []
    for entry in entries:
        try:
            installed = Path(distribution.locate_file(entry)).resolve()
        except (AttributeError, OSError, TypeError, ValueError) as exc:
            raise AssertionError(
                f"could not resolve {distribution_name} distribution file {entry!r}: "
                f"{exc}"
            ) from exc
        located.append((Path(str(entry)), installed))

    def unique_metadata_file(filename: str) -> Path:
        matches = [
            installed
            for entry, installed in located
            if entry.name == filename and entry.parent.name.endswith(".dist-info")
        ]
        if len(matches) != 1:
            raise AssertionError(
                f"Python distribution {distribution_name} must expose exactly one "
                f"{filename} file, found {len(matches)}"
            )
        if not matches[0].is_file():
            raise AssertionError(
                f"Python distribution {distribution_name} {filename} does not exist: "
                f"{matches[0]}"
            )
        return matches[0]

    return (
        {installed for _, installed in located},
        unique_metadata_file("METADATA"),
        unique_metadata_file("RECORD"),
    )


def _capture_dependency_provenance(
    module_name: str,
    distribution_name: str,
    module: ModuleType,
    module_table: Mapping[str, object],
    distribution_resolver: Callable[[str], object],
) -> PythonDependencyProvenance:
    module_version = _require_version(
        getattr(module, "__version__", None), f"Python module {module_name}"
    )
    module_file = getattr(module, "__file__", None)
    if not isinstance(module_file, (str, os.PathLike)) or not module_file:
        raise AssertionError(f"Python module {module_name} has no filesystem origin")
    module_origin = Path(module_file).expanduser().resolve()
    if not module_origin.is_file():
        raise AssertionError(
            f"Python module {module_name} origin does not exist: {module_origin}"
        )

    try:
        distribution = distribution_resolver(distribution_name)
    except Exception as exc:
        raise AssertionError(
            f"Python distribution metadata is unavailable for {distribution_name}: {exc}"
        ) from exc
    distribution_version = _require_version(
        getattr(distribution, "version", None),
        f"Python distribution {distribution_name}",
    )
    if module_version != distribution_version:
        raise AssertionError(
            f"Python dependency {module_name} version mismatch: "
            f"module={module_version!r} distribution={distribution_version!r}"
        )

    installed_files, metadata_origin, record_origin = _distribution_artifacts(
        distribution, distribution_name
    )
    if module_origin not in installed_files:
        raise AssertionError(
            f"Python module {module_name} came from {module_origin}, which is not "
            f"owned by distribution {distribution_name} at {metadata_origin.parent}"
        )

    package_root = module_origin.parent
    imported_files = {module_origin, metadata_origin, record_origin}
    for loaded_name, loaded_module in tuple(module_table.items()):
        if loaded_name != module_name and not loaded_name.startswith(module_name + "."):
            continue
        loaded_file = getattr(loaded_module, "__file__", None)
        # Namespace and synthetic package nodes have no executable file. Their
        # concrete child modules are still captured independently below.
        if loaded_file is None:
            continue
        if not isinstance(loaded_file, (str, os.PathLike)) or not loaded_file:
            raise AssertionError(
                f"loaded Python module {loaded_name} has an invalid origin"
            )
        loaded_path = Path(loaded_file).expanduser()
        if not loaded_path.is_absolute():
            spec_origin = getattr(getattr(loaded_module, "__spec__", None), "origin", None)
            if not isinstance(spec_origin, (str, os.PathLike)) or not Path(
                spec_origin
            ).is_absolute():
                # torch.ops/torch.classes are dynamic ModuleType proxies. They
                # advertise a relative pseudo-file, while their implementation
                # lives in ordinary torch._ops/torch._classes modules that are
                # captured separately.
                proxy_owner = type(loaded_module).__module__
                if (
                    proxy_owner == loaded_name
                    or (
                        proxy_owner != module_name
                        and not proxy_owner.startswith(module_name + ".")
                    )
                    or proxy_owner not in module_table
                ):
                    raise AssertionError(
                        f"loaded Python module {loaded_name} has unresolved relative "
                        f"origin {loaded_file!r}"
                    )
                continue
            loaded_path = Path(spec_origin).expanduser()
        origin = loaded_path.resolve()
        if not origin.is_file():
            raise AssertionError(
                f"loaded Python module {loaded_name} origin does not exist: {origin}"
            )
        if not origin.is_relative_to(package_root):
            raise AssertionError(
                f"loaded Python module {loaded_name} came from mixed origin {origin}; "
                f"expected it below {package_root}"
            )
        if origin not in installed_files:
            raise AssertionError(
                f"loaded Python module {loaded_name} came from unowned file {origin}; "
                f"distribution={distribution_name}"
            )
        imported_files.add(origin)

    safe_label = module_name.replace(".", "_")
    try:
        fingerprints = fingerprint_files(
            (
                (f"python_{safe_label}_file_{index:04d}", path)
                for index, path in enumerate(
                    sorted(imported_files, key=lambda item: os.path.normcase(str(item)))
                )
            )
        )
    except CompilerPreflightError as exc:
        raise AssertionError(str(exc)) from exc
    return PythonDependencyProvenance(
        module_name=module_name,
        distribution_name=distribution_name,
        version=module_version,
        module_origin=module_origin,
        metadata_origin=metadata_origin,
        installed_files=len(installed_files),
        installed_files_sha256=path_manifest_sha256(
            tuple(sorted(installed_files, key=lambda path: os.path.normcase(str(path))))
        ),
        fingerprints=fingerprints,
    )


def capture_python_environment_provenance(
    *,
    dependency_modules: Mapping[str, ModuleType] | None = None,
    module_table: Mapping[str, object] | None = None,
    distribution_resolver: Callable[[str], object] = importlib_metadata.distribution,
) -> PythonEnvironmentProvenance:
    """Capture exact CPython and imported trainer dependency provenance."""

    implementation = getattr(sys.implementation, "name", None)
    if implementation != "cpython":
        raise AssertionError(
            f"legacy pipeline requires CPython, got {implementation!r}"
        )
    cache_tag = getattr(sys.implementation, "cache_tag", None)
    if not isinstance(cache_tag, str) or not cache_tag:
        raise AssertionError("CPython implementation has no cache tag")

    if dependency_modules is None:
        dependency_modules = {
            module_name: importlib.import_module(module_name)
            for module_name, _ in PYTHON_DEPENDENCIES
        }
    expected_names = {module_name for module_name, _ in PYTHON_DEPENDENCIES}
    if set(dependency_modules) != expected_names:
        raise AssertionError(
            "Python dependency snapshot must contain exactly "
            + ", ".join(sorted(expected_names))
        )
    loaded_modules = sys.modules if module_table is None else module_table

    try:
        runtime_fingerprints = fingerprint_files(
            (
                (f"python_runtime_file_{index:04d}", path)
                for index, path in enumerate(_python_runtime_paths())
            )
        )
    except CompilerPreflightError as exc:
        raise AssertionError(str(exc)) from exc

    dependencies = tuple(
        _capture_dependency_provenance(
            module_name,
            distribution_name,
            dependency_modules[module_name],
            loaded_modules,
            distribution_resolver,
        )
        for module_name, distribution_name in PYTHON_DEPENDENCIES
    )
    version_info = sys.version_info
    return PythonEnvironmentProvenance(
        implementation=implementation,
        version=sys.version,
        version_info=(
            version_info.major,
            version_info.minor,
            version_info.micro,
            version_info.releaselevel,
            version_info.serial,
        ),
        cache_tag=cache_tag,
        hexversion=sys.hexversion,
        executable=Path(sys.executable).expanduser().resolve(),
        runtime_fingerprints=runtime_fingerprints,
        dependencies=dependencies,
    )


def emit_python_environment_provenance(
    provenance: PythonEnvironmentProvenance,
    *,
    emit: Callable[[str], None] = print,
) -> None:
    emit(
        "LEGACY PIPELINE PYTHON RUNTIME PREFLIGHT "
        f"implementation={provenance.implementation} "
        f"version={json.dumps(provenance.version)} "
        f"version_info={provenance.version_info} "
        f"cache_tag={provenance.cache_tag} "
        f"hexversion=0x{provenance.hexversion:08X} "
        f"executable={json.dumps(str(provenance.executable))} "
        f"files={len(provenance.runtime_fingerprints)} "
        f"manifest_sha256={fingerprint_manifest_sha256(provenance.runtime_fingerprints)}"
    )
    for dependency in provenance.dependencies:
        emit(
            "LEGACY PIPELINE PYTHON DEPENDENCY PREFLIGHT "
            f"module={dependency.module_name} "
            f"distribution={dependency.distribution_name} "
            f"version={json.dumps(dependency.version)} "
            f"origin={json.dumps(str(dependency.module_origin))} "
            f"metadata={json.dumps(str(dependency.metadata_origin))} "
            f"installed_files={dependency.installed_files} "
            f"installed_files_sha256={dependency.installed_files_sha256} "
            f"imported_files={len(dependency.fingerprints)} "
            f"imported_manifest_sha256={dependency.manifest_sha256}"
        )
    emit(
        "LEGACY PIPELINE PYTHON ENVIRONMENT PREFLIGHT "
        f"files={len(provenance.fingerprints)} "
        f"manifest_sha256={provenance.manifest_sha256}"
    )


def _fingerprints_by_path(
    fingerprints: Sequence[FileFingerprint],
) -> dict[Path, FileFingerprint]:
    return {fingerprint.path: fingerprint for fingerprint in fingerprints}


def verify_python_environment_provenance(
    expected: PythonEnvironmentProvenance,
    *,
    emit: Callable[[str], None] = print,
    recapture: Callable[[], PythonEnvironmentProvenance] = (
        capture_python_environment_provenance
    ),
) -> None:
    """Re-import and rehash Python runtime inputs after the E2E workload."""

    actual = recapture()
    expected_runtime = (
        expected.implementation,
        expected.version,
        expected.version_info,
        expected.cache_tag,
        expected.hexversion,
        expected.executable,
    )
    actual_runtime = (
        actual.implementation,
        actual.version,
        actual.version_info,
        actual.cache_tag,
        actual.hexversion,
        actual.executable,
    )
    if actual_runtime != expected_runtime:
        raise AssertionError(
            "CPython runtime identity changed after preflight: "
            f"before={expected_runtime!r} after={actual_runtime!r}"
        )

    expected_dependencies = {
        dependency.module_name: dependency for dependency in expected.dependencies
    }
    actual_dependencies = {
        dependency.module_name: dependency for dependency in actual.dependencies
    }
    if actual_dependencies.keys() != expected_dependencies.keys():
        raise AssertionError(
            "Python dependency set changed after preflight: "
            f"before={sorted(expected_dependencies)} "
            f"after={sorted(actual_dependencies)}"
        )
    for module_name, before in expected_dependencies.items():
        after = actual_dependencies[module_name]
        before_identity = (
            before.distribution_name,
            before.version,
            before.module_origin,
            before.metadata_origin,
            before.installed_files,
            before.installed_files_sha256,
        )
        after_identity = (
            after.distribution_name,
            after.version,
            after.module_origin,
            after.metadata_origin,
            after.installed_files,
            after.installed_files_sha256,
        )
        if after_identity != before_identity:
            raise AssertionError(
                f"Python dependency {module_name} identity changed after preflight: "
                f"before={before_identity!r} after={after_identity!r}"
            )

    before_files = _fingerprints_by_path(expected.fingerprints)
    after_files = _fingerprints_by_path(actual.fingerprints)
    removed_paths = before_files.keys() - after_files.keys()
    if removed_paths:
        removed = sorted(str(path) for path in removed_paths)
        raise AssertionError(
            "Python imported/runtime preflight artifacts disappeared: "
            f"removed={removed}"
        )
    for path, before in before_files.items():
        after = after_files[path]
        if before.size != after.size or before.sha256 != after.sha256:
            raise AssertionError(
                f"Python artifact changed after preflight: {path} "
                f"before_bytes={before.size} before_sha256={before.sha256} "
                f"after_bytes={after.size} after_sha256={after.sha256}"
            )

    # PyTorch legitimately imports some modules lazily during optimizer and
    # serialization calls. The recapture above validates each added file as an
    # installed member of the same unchanged distribution and package root.
    # Every file that existed at preflight remains mandatory and is rehashed.
    added_files = after_files.keys() - before_files.keys()
    emit(
        "LEGACY PIPELINE PYTHON ENVIRONMENT POSTFLIGHT: PASS "
        f"preflight_files={len(expected.fingerprints)} "
        f"postflight_files={len(actual.fingerprints)} "
        f"lazy_added_files={len(added_files)} "
        f"preflight_manifest_sha256={expected.manifest_sha256} "
        f"postflight_manifest_sha256={actual.manifest_sha256}"
    )


def require_file(path: Path, label: str) -> Path:
    resolved = path.expanduser().resolve()
    if not resolved.is_file():
        raise AssertionError(f"{label} does not exist: {resolved}")
    return resolved


def require_directory(path: Path, label: str) -> Path:
    resolved = path.expanduser().resolve()
    if not resolved.is_dir():
        raise AssertionError(f"{label} does not exist: {resolved}")
    return resolved


def run_tools_command(engine: Path, command: str) -> str:
    commands = "\n".join(
        (
            "uci",
            "setoption name UCI_Variant value atomic",
            "setoption name Threads value 1",
            "setoption name Hash value 16",
            "setoption name Use NNUE value false",
            "isready",
            command,
            "quit",
            "",
        )
    )
    try:
        result = subprocess.run(
            [str(engine)],
            input=commands,
            text=True,
            encoding="utf-8",
            errors="replace",
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=TOOLS_TIMEOUT_SECONDS,
            check=False,
        )
    except subprocess.TimeoutExpired as error:
        partial = error.stdout or ""
        raise AssertionError(
            f"tools command timed out after {TOOLS_TIMEOUT_SECONDS}s:\n"
            f"command: {command}\n{partial}"
        ) from error
    output = result.stdout
    if result.returncode != 0:
        raise AssertionError(
            f"tools command failed with exit code {result.returncode}:\n"
            f"command: {command}\n{output}"
        )
    if "readyok" not in output:
        raise AssertionError(f"tools engine did not acknowledge isready:\n{output}")
    return output


def run_generation_command(
    engine: Path, source_net: Path, command: str
) -> str:
    commands = "\n".join(
        (
            "uci",
            "setoption name UCI_Variant value atomic",
            "setoption name Threads value 1",
            "setoption name Hash value 16",
            f"setoption name EvalFile value {source_net}",
            "setoption name Use NNUE value pure",
            "isready",
            command,
            "quit",
            "",
        )
    )
    try:
        result = subprocess.run(
            [str(engine)],
            input=commands,
            text=True,
            encoding="utf-8",
            errors="replace",
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=TOOLS_TIMEOUT_SECONDS,
            check=False,
        )
    except subprocess.TimeoutExpired as error:
        partial = error.stdout or ""
        raise AssertionError(
            f"generator timed out after {TOOLS_TIMEOUT_SECONDS}s:\n"
            f"command: {command}\n{partial}"
        ) from error
    output = result.stdout
    if result.returncode != 0:
        raise AssertionError(
            f"generator failed with exit code {result.returncode}:\n"
            f"command: {command}\n{output}"
        )
    if "readyok" not in output:
        raise AssertionError(f"generator did not acknowledge isready:\n{output}")
    if any("ERROR" in line.upper() for line in output.splitlines()):
        raise AssertionError(f"generator reported an error:\n{output}")
    if TOOLS_PURE_OPTION not in output.splitlines():
        raise AssertionError(
            "tools engine does not expose the required pure NNUE mode:\n" + output
        )
    expected_marker = (
        f"info string NNUE evaluation using {source_net}{TOOLS_PURE_LOAD_SUFFIX}"
    )
    if output.splitlines().count(expected_marker) != 1:
        raise AssertionError(
            "generator did not load the selected source network in pure mode; expected "
            f"one {expected_marker!r}:\n{output}"
        )
    if "INFO: generate_training_data finished." not in output.splitlines():
        raise AssertionError(f"generator emitted no exact completion marker:\n{output}")
    return output


def generate_data(
    tools_engine: Path,
    source_net: Path,
    output: Path,
    records: int,
    seed: str,
) -> bytes:
    command = (
        "generate_training_data depth 1 count {records} write_min_ply 1 "
        "random_move_count 0 keep_draws 1 eval_limit 30000 "
        "output_file_name {output} data_format bin seed {seed}"
    ).format(records=records, output=output, seed=seed)
    run_generation_command(tools_engine, source_net, command)
    if not output.is_file():
        raise AssertionError(f"generator did not create {output}")
    return output.read_bytes()


def validate_legacy_records(data: bytes, records: int) -> None:
    expected_size = records * RECORD_SIZE
    if len(data) != expected_size:
        raise AssertionError(
            f"legacy framing mismatch: expected {expected_size} bytes "
            f"({records} x {RECORD_SIZE}), got {len(data)}"
        )

    for index in range(records):
        offset = index * RECORD_SIZE
        move = struct.unpack_from("<H", data, offset + MOVE_OFFSET)[0]
        ply = struct.unpack_from("<H", data, offset + PLY_OFFSET)[0]
        result = struct.unpack_from("<b", data, offset + RESULT_OFFSET)[0]
        padding = data[offset + PADDING_OFFSET]
        if move == 0:
            raise AssertionError(f"record {index} contains the MOVE_NONE wire value")
        if ply == 0:
            raise AssertionError(f"record {index} contains a zero game ply")
        if result not in (-1, 0, 1):
            raise AssertionError(f"record {index} has invalid result {result}")
        if padding != 0:
            raise AssertionError(
                f"record {index} has nondeterministic padding byte {padding}"
            )


def parse_plain_records(path: Path, expected_count: int) -> list[PlainRecord]:
    required_fields = ("fen", "move", "score", "ply", "result")
    records: list[PlainRecord] = []
    fields: dict[str, str] = {}
    for line_number, raw_line in enumerate(
        path.read_text(encoding="utf-8").splitlines(), start=1
    ):
        line = raw_line.strip()
        if not line:
            continue
        if line == "e":
            missing = [field for field in required_fields if field not in fields]
            if missing:
                raise AssertionError(
                    f"plain record ending at line {line_number} lacks {missing}"
                )
            records.append(
                PlainRecord(
                    fen=fields["fen"],
                    move=fields["move"],
                    score=int(fields["score"]),
                    ply=int(fields["ply"]),
                    result=int(fields["result"]),
                )
            )
            fields = {}
            continue

        try:
            name, value = line.split(maxsplit=1)
        except ValueError as error:
            raise AssertionError(
                f"malformed plain record line {line_number}: {raw_line!r}"
            ) from error
        if name not in required_fields or name in fields:
            raise AssertionError(
                f"unexpected or duplicate field {name!r} at plain line {line_number}"
            )
        fields[name] = value

    if fields:
        raise AssertionError("plain conversion ended without an 'e' record terminator")
    if len(records) != expected_count:
        raise AssertionError(
            f"plain conversion contains {len(records)} records, expected {expected_count}"
        )
    return records


def convert_roundtrip(
    tools_engine: Path, source: Path, root: Path
) -> tuple[Path, Path]:
    plain = root / "generated.txt"
    roundtrip = root / "roundtrip.bin"
    run_tools_command(
        tools_engine,
        f"convert_plain targetfile {source} output_file_name {plain}",
    )
    if not plain.is_file() or not plain.read_text(encoding="utf-8").strip():
        raise AssertionError("convert_plain produced no decoded records")

    conversion_output = run_tools_command(
        tools_engine,
        (
            f"convert_bin targetfile {plain} output_file_name {roundtrip} "
            "check_invalid_fen 1 check_illegal_move 1"
        ),
    )
    expected_summary = (
        f"done {len(source.read_bytes()) // RECORD_SIZE} parsed 0 is filtered"
    )
    if expected_summary not in conversion_output:
        raise AssertionError(
            "strict Atomic round-trip rejected or omitted records:\n"
            + conversion_output
        )
    if not roundtrip.is_file():
        raise AssertionError("convert_bin produced no round-trip file")

    source_bytes = source.read_bytes()
    roundtrip_bytes = roundtrip.read_bytes()
    if len(roundtrip_bytes) != len(source_bytes):
        raise AssertionError(
            "bin -> plain -> bin changed the legacy record count: "
            f"source={len(source_bytes)}, roundtrip={len(roundtrip_bytes)}"
        )
    # PackedSfen has spare/non-canonical bits, so its raw 64-byte encoding is
    # not an identity wire after decode/repack.  The scored payload must remain
    # exact and a second plain decode must prove position/move equivalence.
    for index in range(len(source_bytes) // RECORD_SIZE):
        payload_start = index * RECORD_SIZE + 64
        payload_end = (index + 1) * RECORD_SIZE
        if source_bytes[payload_start:payload_end] != roundtrip_bytes[
            payload_start:payload_end
        ]:
            raise AssertionError(
                f"bin -> plain -> bin changed record {index}'s "
                "score/move/ply/result payload"
            )

    roundtrip_plain = root / "roundtrip.txt"
    run_tools_command(
        tools_engine,
        f"convert_plain targetfile {roundtrip} output_file_name {roundtrip_plain}",
    )
    if roundtrip_plain.read_text(encoding="utf-8") != plain.read_text(encoding="utf-8"):
        raise AssertionError(
            "bin -> plain -> bin changed decoded positions, moves, or labels"
        )
    return roundtrip, plain


def loader_seed(seed: str) -> int:
    try:
        value = int(seed, 0)
    except ValueError:
        value = int.from_bytes(
            hashlib.sha256(seed.encode("utf-8")).digest()[:8], "little"
        )
    if value < 0 or value >= 2**64:
        raise AssertionError("--seed must map to an unsigned 64-bit value")
    return value


def import_trainer_modules(trainer_root: Path) -> tuple[ModuleType, ...]:
    # nnue_dataset discovers the native loader relative to the process CWD at
    # import time.  Import from the trainer root, then restore the caller's CWD.
    original_cwd = Path.cwd()
    trainer_path = str(trainer_root)
    sys.path.insert(0, trainer_path)
    try:
        os.chdir(trainer_root)
        nnue_dataset = importlib.import_module("nnue_dataset")
        features = importlib.import_module("features")
        model = importlib.import_module("model")
        ranger = importlib.import_module("ranger")
        serialize = importlib.import_module("serialize")
        torch = importlib.import_module("torch")

        for module in (nnue_dataset, features, model, ranger, serialize):
            module_file = getattr(module, "__file__", None)
            if module_file is None:
                raise AssertionError(
                    f"trainer module {module.__name__} has no filesystem origin"
                )
            origin = Path(module_file).resolve()
            if not origin.is_relative_to(trainer_root):
                raise AssertionError(
                    f"trainer module {module.__name__} came from {origin}, "
                    f"outside requested checkout {trainer_root}"
                )
    finally:
        os.chdir(original_cwd)
        # Remove the exact string object inserted above, without disturbing a
        # pre-existing equal path if an imported package reordered sys.path.
        for index, entry in enumerate(sys.path):
            if entry is trainer_path:
                sys.path.pop(index)
                break
    return nnue_dataset, features, model, ranger, serialize, torch


def decode_batch(
    nnue_dataset: ModuleType,
    data_file: Path,
    records: int,
    seed: int,
) -> tuple[object, ...]:
    provider = nnue_dataset.SparseBatchProvider(
        "HalfKAv2^",
        str(data_file),
        records,
        cyclic=False,
        num_workers=1,
        filtered=False,
        random_fen_skipping=0,
        device="cpu",
        seed=seed,
    )
    try:
        batch = next(provider)
        try:
            next(provider)
        except StopIteration:
            pass
        else:
            raise AssertionError("native loader emitted records past end-of-file")
    finally:
        # Do not rely on cyclic GC here.  On Windows the native stream owns an
        # open handle to the .bin until it is explicitly destroyed.
        if provider.stream:
            provider.destroy_stream(provider.stream)
            provider.stream = None

    if len(batch) != 10:
        raise AssertionError(f"native loader returned {len(batch)} tensors, expected 10")
    if batch[0].shape[0] != records:
        raise AssertionError(
            f"native loader decoded {batch[0].shape[0]} records, expected {records}"
        )
    for index, tensor in enumerate(batch):
        if tensor.shape[0] != records:
            raise AssertionError(
                f"batch tensor {index} has leading dimension {tensor.shape[0]}, "
                f"expected {records}"
            )
    return tuple(batch)


def parse_fen_position(fen: str) -> tuple[bool, list[tuple[int, int, bool]]]:
    fields = fen.split()
    if len(fields) != 6 or fields[1] not in ("w", "b"):
        raise AssertionError(f"unsupported generated FEN: {fen!r}")
    piece_types = {"p": 0, "n": 1, "b": 2, "r": 3, "q": 4, "k": 5}
    pieces: list[tuple[int, int, bool]] = []
    ranks = fields[0].split("/")
    if len(ranks) != 8:
        raise AssertionError(f"generated FEN has {len(ranks)} ranks: {fen!r}")
    for fen_rank, encoded_rank in enumerate(ranks):
        board_rank = 7 - fen_rank
        board_file = 0
        for symbol in encoded_rank:
            if symbol.isdigit():
                board_file += int(symbol)
                continue
            piece_type = piece_types.get(symbol.lower())
            if piece_type is None or board_file >= 8:
                raise AssertionError(f"invalid generated FEN placement: {fen!r}")
            pieces.append(
                (board_rank * 8 + board_file, piece_type, symbol.isupper())
            )
            board_file += 1
        if board_file != 8:
            raise AssertionError(f"incomplete generated FEN rank: {fen!r}")
    if len(pieces) > 32:
        raise AssertionError(f"generated FEN exceeds the 32-piece loader ABI: {fen!r}")
    for white in (True, False):
        if sum(piece_type == 5 and color == white for _, piece_type, color in pieces) != 1:
            raise AssertionError(
                "generated training positions must retain exactly one king per side"
            )
    return fields[1] == "w", sorted(pieces)


def expected_halfkav2_factorized_features(
    pieces: list[tuple[int, int, bool]], white_pov: bool
) -> list[int]:
    # This is the native ABI formula from training_data_loader.cpp.  Atomic's
    # 8x8 configuration has 11 real HalfKAv2 planes, 64 king buckets and 12
    # virtual piece planes: 64*11*64 = 45056 real inputs, followed by 768
    # factorizer inputs.  Black POV vertically flips squares (sq ^ 56).  The
    # real feature merges both kings into plane 10, while the virtual factor
    # preserves own/opponent king planes 10/11.
    num_squares = 64
    num_real_planes = 11
    num_real_features = num_squares * num_real_planes * num_squares
    king_square = next(
        square
        for square, piece_type, is_white in pieces
        if piece_type == 5 and is_white == white_pov
    )

    def orient(square: int) -> int:
        return square if white_pov else square ^ 56

    oriented_king = orient(king_square)
    real: list[int] = []
    virtual: list[int] = []
    for square, piece_type, is_white in pieces:
        piece_plane = piece_type * 2 + int(is_white != white_pov)
        real_plane = 10 if piece_plane == 11 else piece_plane
        real.append(
            orient(square)
            + real_plane * num_squares
            + oriented_king * num_real_planes * num_squares
        )
        virtual.append(
            num_real_features + piece_plane * num_squares + orient(square)
        )
    return real + virtual


def validate_batch_semantics(
    batch: tuple[object, ...],
    records: list[PlainRecord],
    binary: bytes,
    factorized_feature_set: object,
    torch: ModuleType,
) -> None:
    (
        us,
        them,
        white_indices,
        white_values,
        black_indices,
        black_values,
        outcome,
        scores,
        psqt_indices,
        layer_stack_indices,
    ) = batch
    if factorized_feature_set.name != "HalfKAv2^":
        raise AssertionError("semantic validation received the wrong feature set")
    if factorized_feature_set.num_real_features != 45056:
        raise AssertionError("HalfKAv2 real feature count changed from 45056")
    if factorized_feature_set.num_features != 45824:
        raise AssertionError("HalfKAv2^ feature count changed from 45824")
    if white_indices.shape[1] != 64 or black_indices.shape[1] != 64:
        raise AssertionError("HalfKAv2^ sparse padding width changed from 64")

    float_tensors = (us, them, white_values, black_values, outcome, scores)
    if any(not torch.isfinite(tensor).all() for tensor in float_tensors):
        raise AssertionError("native loader returned a non-finite batch tensor")

    for index, record in enumerate(records):
        offset = index * RECORD_SIZE
        binary_side = binary[offset] & 1
        binary_score = struct.unpack_from("<h", binary, offset + 64)[0]
        binary_ply = struct.unpack_from("<H", binary, offset + PLY_OFFSET)[0]
        binary_result = struct.unpack_from("<b", binary, offset + RESULT_OFFSET)[0]
        side_is_white, pieces = parse_fen_position(record.fen)
        expected_us = 1.0 if side_is_white else 0.0
        if binary_side != (0 if side_is_white else 1):
            raise AssertionError(f"record {index} FEN/binary side-to-move mismatch")
        if (binary_score, binary_ply, binary_result) != (
            record.score,
            record.ply,
            record.result,
        ):
            raise AssertionError(f"record {index} plain/binary label mismatch")
        if float(us[index].item()) != expected_us:
            raise AssertionError(f"record {index} loader us tensor mismatch")
        if float(them[index].item()) != 1.0 - expected_us:
            raise AssertionError(f"record {index} loader them tensor mismatch")
        if float(scores[index].item()) != float(record.score):
            raise AssertionError(f"record {index} loader score mismatch")
        expected_outcome = (record.result + 1.0) / 2.0
        if float(outcome[index].item()) != expected_outcome:
            raise AssertionError(f"record {index} loader outcome mismatch")

        # Native ABI: (pieceCount - 1) * PSQT_BUCKETS / PIECE_COUNT, with
        # PSQT_BUCKETS=8 and PIECE_COUNT=32.  The same bucket selects the layer
        # stack in this legacy architecture.
        expected_bucket = (len(pieces) - 1) * 8 // 32
        if int(psqt_indices[index].item()) != expected_bucket:
            raise AssertionError(f"record {index} PSQT bucket mismatch")
        if int(layer_stack_indices[index].item()) != expected_bucket:
            raise AssertionError(f"record {index} layer-stack bucket mismatch")

        active_count = 2 * len(pieces)
        for label, tensor_indices, tensor_values, white_pov in (
            ("white", white_indices, white_values, True),
            ("black", black_indices, black_values, False),
        ):
            actual_indices = [
                int(value) for value in tensor_indices[index].detach().cpu().tolist()
            ]
            actual_values = [
                float(value) for value in tensor_values[index].detach().cpu().tolist()
            ]
            expected_indices = expected_halfkav2_factorized_features(
                pieces, white_pov
            )
            if actual_indices[:active_count] != expected_indices:
                raise AssertionError(
                    f"record {index} {label} active HalfKAv2^ features mismatch"
                )
            if any(value != 1.0 for value in actual_values[:active_count]):
                raise AssertionError(
                    f"record {index} {label} active feature arity is not one"
                )
            if any(
                feature < 0 or feature >= factorized_feature_set.num_features
                for feature in actual_indices[:active_count]
            ):
                raise AssertionError(
                    f"record {index} {label} active feature is out of range"
                )
            if any(value != -1 for value in actual_indices[active_count:]):
                raise AssertionError(
                    f"record {index} {label} sparse index padding is not -1"
                )
            if any(value != 0.0 for value in actual_values[active_count:]):
                raise AssertionError(
                    f"record {index} {label} sparse value padding is not zero"
                )


def train_one_step(
    batch: tuple[object, ...],
    features: ModuleType,
    model_module: ModuleType,
    ranger_module: ModuleType,
    torch: ModuleType,
    seed: int,
) -> tuple[object, float, float, float]:
    torch.manual_seed(seed)
    feature_set = features.get_feature_set_from_name("HalfKAv2^")
    network = model_module.NNUE(feature_set, lambda_=1.0)
    network.train()

    optimizers, _ = network.configure_optimizers()
    if len(optimizers) != 1 or not isinstance(optimizers[0], ranger_module.Ranger):
        raise AssertionError("NNUE.configure_optimizers did not return one Ranger optimizer")
    optimizer = optimizers[0]

    optimizer.zero_grad(set_to_none=True)
    loss = network.training_step(batch, 0)
    if loss.ndim != 0 or not torch.isfinite(loss):
        raise AssertionError(f"training_step returned invalid loss {loss}")
    loss.backward()

    missing_gradients: list[str] = []
    for name, parameter in network.named_parameters():
        if parameter.grad is None:
            missing_gradients.append(name)
        elif not torch.isfinite(parameter.grad).all():
            raise AssertionError(f"backward produced a non-finite gradient in {name}")
    if missing_gradients:
        raise AssertionError(
            "backward produced no gradient for parameters: "
            + ", ".join(missing_gradients)
        )

    active_feature_indices = torch.unique(
        torch.cat((batch[2].reshape(-1), batch[4].reshape(-1)))
    )
    active_feature_indices = active_feature_indices[
        active_feature_indices >= 0
    ].to(dtype=torch.long)
    ft_gradient = network.input.weight.grad.index_select(
        0, active_feature_indices
    )
    ft_row_gradient = torch.amax(torch.abs(ft_gradient), dim=1)
    if not torch.count_nonzero(ft_row_gradient).item():
        raise AssertionError("no active HalfKAv2^ feature row received a gradient")
    ft_row = int(active_feature_indices[torch.argmax(ft_row_gradient)].item())
    ft_before = network.input.weight.detach()[ft_row].clone()

    fc_name = ""
    fc_parameter = None
    fc_flat_index = -1
    fc_gradient_max = -1.0
    for name, parameter in network.named_parameters():
        if not name.startswith("layer_stacks."):
            continue
        gradient = torch.abs(parameter.grad.detach()).reshape(-1)
        candidate_max, candidate_index = torch.max(gradient, dim=0)
        candidate_value = float(candidate_max.item())
        if candidate_value > fc_gradient_max:
            fc_name = name
            fc_parameter = parameter
            fc_flat_index = int(candidate_index.item())
            fc_gradient_max = candidate_value
    if fc_parameter is None or fc_gradient_max <= 0.0:
        raise AssertionError("no fully-connected parameter received a gradient")
    fc_before = float(fc_parameter.detach().reshape(-1)[fc_flat_index].item())

    optimizer.step()
    for name, parameter in network.named_parameters():
        if not torch.isfinite(parameter.detach()).all():
            raise AssertionError(
                f"Ranger produced a non-finite value in parameter {name}"
            )
    ft_delta = float(
        torch.max(torch.abs(network.input.weight.detach()[ft_row] - ft_before)).item()
    )
    fc_after = float(fc_parameter.detach().reshape(-1)[fc_flat_index].item())
    fc_delta = abs(fc_after - fc_before)
    loss_value = float(loss.detach().cpu().item())
    if not math.isfinite(loss_value):
        raise AssertionError(f"training produced non-finite loss {loss_value}")
    if not math.isfinite(ft_delta) or ft_delta <= 0.0:
        raise AssertionError(
            f"Ranger did not update active FT row {ft_row}: delta={ft_delta}"
        )
    if not math.isfinite(fc_delta) or fc_delta <= 0.0:
        raise AssertionError(
            f"Ranger did not update FC parameter {fc_name}[{fc_flat_index}]: "
            f"delta={fc_delta}"
        )
    optimizer.zero_grad(set_to_none=True)
    return network, loss_value, ft_delta, fc_delta


def writer_bytes(serialize: ModuleType, network: object, description: str) -> bytes:
    # The legacy serializer prints diagnostic histograms.  They are useful for
    # manual conversion, but would hide this gate's concise final marker.
    with redirect_stdout(io.StringIO()):
        return bytes(serialize.NNUEWriter(network, description).buf)


def generate_synthetic_source_network(
    root: Path,
    profile: PipelineProfile,
    features: ModuleType,
    model_module: ModuleType,
    serialize: ModuleType,
    torch: ModuleType,
    *,
    verify_hash: bool = True,
) -> tuple[Path, bytes]:
    """Create a redistributable, deterministic zero-weight Legacy V1 net.

    The trainer defines the model, feature hashes and wire serializer.  Zeroing
    every parameter after construction avoids depending on a PyTorch RNG or
    BLAS implementation while still exercising the pinned trainer's complete
    LegacyAtomicV1 HalfKAv2 serialization path.
    """

    if profile.source_kind != "trainer-generated-zero":
        raise AssertionError(
            f"profile {profile.name!r} is not a synthetic trainer profile"
        )
    if profile.synthetic_model_seed is None:
        raise AssertionError("synthetic profile has no model seed")
    torch.manual_seed(profile.synthetic_model_seed)
    factorized_features = features.get_feature_set_from_name("HalfKAv2^")
    network = model_module.NNUE(factorized_features, lambda_=1.0)
    with torch.no_grad():
        for parameter in network.parameters():
            parameter.zero_()
    network.eval()
    serialized = writer_bytes(serialize, network, SYNTHETIC_DESCRIPTION)
    version, architecture = struct.unpack_from("<II", serialized, 0)
    if version != LEGACY_NNUE_VERSION or architecture != LEGACY_NNUE_ARCHITECTURE:
        raise AssertionError(
            "synthetic trainer network has the wrong LegacyAtomicV1 header: "
            f"version=0x{version:08X} architecture=0x{architecture:08X}"
        )
    source_hash = sha256(serialized)
    if verify_hash and source_hash != profile.source_net_sha256:
        raise AssertionError(
            "synthetic source network fixture changed: "
            f"expected {profile.source_net_sha256}, got {source_hash}"
        )
    # Fairy's Atomic Legacy V1 selector intentionally requires an Atomic net
    # basename (or alias); a generic filename would silently leave NNUE off.
    output = root / "atomic-synthetic-source.nnue"
    output.write_bytes(serialized)
    return output, serialized


def serialize_and_reimport(
    network: object,
    root: Path,
    features: ModuleType,
    model_module: ModuleType,
    serialize: ModuleType,
) -> tuple[Path, bytes]:
    description = "Atomic-Stockfish Legacy Atomic V1 pipeline E2E"
    serialized = writer_bytes(serialize, network, description)
    if len(serialized) < 16:
        raise AssertionError("NNUEWriter emitted a truncated network")

    version, architecture, description_length = struct.unpack_from(
        "<III", serialized, 0
    )
    expected_architecture = (
        serialize.NNUEWriter.fc_hash(network)
        ^ network.feature_set.hash
        ^ (model_module.L1 * 2)
    ) & 0xFFFFFFFF
    if version != LEGACY_NNUE_VERSION or version != serialize.VERSION:
        raise AssertionError(f"unexpected Legacy Atomic V1 version 0x{version:08X}")
    if architecture != LEGACY_NNUE_ARCHITECTURE:
        raise AssertionError(
            f"unexpected Legacy Atomic V1 architecture 0x{architecture:08X}; "
            f"expected 0x{LEGACY_NNUE_ARCHITECTURE:08X}"
        )
    if architecture != expected_architecture:
        raise AssertionError(
            f"unexpected Legacy Atomic V1 architecture 0x{architecture:08X}; "
            f"expected 0x{expected_architecture:08X}"
        )
    encoded_description = description.encode("utf-8")
    if description_length != len(encoded_description):
        raise AssertionError("NNUEWriter header contains the wrong description length")
    if serialized[12 : 12 + description_length] != encoded_description:
        raise AssertionError("NNUEWriter header contains the wrong description")
    transformer_hash = struct.unpack_from(
        "<I", serialized, 12 + description_length
    )[0]
    expected_transformer_hash = (
        network.feature_set.hash ^ (model_module.L1 * 2)
    ) & 0xFFFFFFFF
    if transformer_hash != expected_transformer_hash:
        raise AssertionError(
            f"unexpected feature-transformer hash 0x{transformer_hash:08X}; "
            f"expected 0x{expected_transformer_hash:08X}"
        )

    nnue_file = root / "trained.nnue"
    nnue_file.write_bytes(serialized)

    real_features = features.get_feature_set_from_name("HalfKAv2")
    with nnue_file.open("rb") as source:
        reimported = serialize.NNUEReader(source, real_features).model
        if source.read(1):
            raise AssertionError("NNUEReader did not consume the complete v1 network")
    if writer_bytes(serialize, reimported, description) != serialized:
        raise AssertionError("HalfKAv2 reimport/reserialization was not byte-exact")

    factorized_features = features.get_feature_set_from_name("HalfKAv2^")
    reimported.set_feature_set(factorized_features)
    if reimported.feature_set.name != "HalfKAv2^":
        raise AssertionError("HalfKAv2 model did not expand to HalfKAv2^")
    if writer_bytes(serialize, reimported, description) != serialized:
        raise AssertionError(
            "HalfKAv2 -> HalfKAv2^ expansion changed serialized network bytes"
        )
    return nnue_file, serialized


class UciProcess:
    def __init__(self, executable: Path, timeout: float = ENGINE_TIMEOUT_SECONDS):
        self.timeout = timeout
        self.lines: queue.Queue[str | None] = queue.Queue()
        self.transcript: list[str] = []
        self.process = subprocess.Popen(
            [str(executable)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
        )
        self.reader = threading.Thread(target=self._read, daemon=True)
        self.reader.start()

    def _read(self) -> None:
        assert self.process.stdout is not None
        try:
            for raw_line in self.process.stdout:
                line = raw_line.rstrip("\r\n")
                self.transcript.append(line)
                self.lines.put(line)
        finally:
            self.lines.put(None)

    def send(self, command: str) -> None:
        if self.process.poll() is not None:
            raise AssertionError(
                "Atomic-Stockfish exited unexpectedly:\n" + "\n".join(self.transcript)
            )
        assert self.process.stdin is not None
        self.process.stdin.write(command + "\n")
        self.process.stdin.flush()

    def read_until(self, predicate: Callable[[str], bool]) -> list[str]:
        deadline = time.monotonic() + self.timeout
        output: list[str] = []
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise AssertionError(
                    "timed out waiting for Atomic-Stockfish output:\n"
                    + "\n".join(self.transcript)
                )
            try:
                line = self.lines.get(timeout=remaining)
            except queue.Empty as error:
                raise AssertionError(
                    "timed out waiting for Atomic-Stockfish output:\n"
                    + "\n".join(self.transcript)
                ) from error
            if line is None:
                raise AssertionError(
                    "Atomic-Stockfish closed its output unexpectedly:\n"
                    + "\n".join(self.transcript)
                )
            output.append(line)
            if predicate(line):
                return output

    def close(self) -> None:
        if self.process.poll() is None:
            try:
                self.send("quit")
                self.process.wait(timeout=10)
            except (AssertionError, subprocess.TimeoutExpired):
                self.process.kill()
                self.process.wait(timeout=10)
        self.reader.join(timeout=10)
        for stream in (self.process.stdin, self.process.stdout):
            if stream is not None:
                try:
                    stream.close()
                except (OSError, ValueError):
                    # Do not mask the test's primary assertion when a failed
                    # child process has already torn down one of its pipes.
                    pass

    def __enter__(self) -> "UciProcess":
        return self

    def __exit__(self, *unused: object) -> None:
        self.close()


def load_in_engine(engine: Path, network: Path) -> str:
    resolved_network = network.resolve()
    with UciProcess(engine) as uci:
        uci.send("uci")
        uci.read_until(lambda line: line == "uciok")
        for name, value in (
            ("UCI_Variant", "atomic"),
            ("Threads", "1"),
            ("Hash", "16"),
            ("EvalFile", str(resolved_network)),
            ("Use NNUE", "true"),
        ):
            uci.send(f"setoption name {name} value {value}")
        uci.send("isready")
        ready_output = uci.read_until(lambda line: line == "readyok")
        if any("error" in line.lower() for line in ready_output):
            raise AssertionError("engine rejected serialized NNUE:\n" + "\n".join(ready_output))

        uci.send("position startpos")
        uci.send("eval")
        evaluation = uci.read_until(lambda line: line.startswith("Final evaluation"))
        expected_load_marker = (
            f"info string NNUE evaluation using {resolved_network} "
            "(45MiB, (45056, 1024, 16, 32, 1))"
        )
        if expected_load_marker not in evaluation:
            raise AssertionError(
                "eval did not report the exact newly serialized network path; "
                f"expected {expected_load_marker!r}:\n"
                + "\n".join(evaluation)
            )
        if any("ERROR" in line.upper() for line in evaluation):
            raise AssertionError("eval reported an NNUE error:\n" + "\n".join(evaluation))

        uci.send("go depth 1")
        search = uci.read_until(lambda line: line.startswith("bestmove "))
        if any("ERROR" in line.upper() for line in search):
            raise AssertionError("search reported an NNUE error:\n" + "\n".join(search))
        bestmove_line = search[-1]
        fields = bestmove_line.split()
        if len(fields) < 2 or fields[1] in ("(none)", "0000"):
            raise AssertionError(f"depth-1 search returned no move: {bestmove_line}")
        return fields[1]


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run the complete Legacy Atomic V1 generator/trainer/engine gate"
    )
    parser.add_argument("--tools-engine", type=Path, required=True)
    parser.add_argument("--trainer-root", type=Path, required=True)
    parser.add_argument("--engine", type=Path, required=True)
    parser.add_argument("--tools-build-manifest", type=Path, required=True)
    parser.add_argument("--trainer-build-manifest", type=Path, required=True)
    parser.add_argument("--atomic-build-manifest", type=Path, required=True)
    parser.add_argument(
        "--profile",
        choices=("strong-local", "synthetic-ci"),
        default="strong-local",
    )
    parser.add_argument("--lock-file", type=Path, default=DEFAULT_LOCK_FILE)
    parser.add_argument(
        "--source-net",
        type=Path,
        help="required only by strong-local; synthetic-ci creates its own network",
    )
    parser.add_argument(
        "--records",
        type=int,
        help="must equal the selected profile's locked record count",
    )
    parser.add_argument(
        "--seed", help="must equal the selected profile's locked generator seed"
    )
    parser.add_argument("--atomic-root", type=Path, default=REPO_ROOT)
    parser.add_argument(
        "--atomic-commit",
        help="optional exact Atomic-Stockfish HEAD expected by release automation",
    )
    parser.add_argument(
        "--measure-synthetic-fixture",
        action="store_true",
        help=(
            "NON-RELEASE: print the synthetic source/data hashes needed to "
            "finalize the lock; valid only with synthetic-ci"
        ),
    )
    args = parser.parse_args(argv)
    if args.records is not None and args.records <= 0:
        parser.error("--records must be greater than zero")
    if args.seed is not None and any(character.isspace() for character in args.seed):
        parser.error("--seed cannot contain whitespace in the tools command wire")
    if args.measure_synthetic_fixture and args.profile != "synthetic-ci":
        parser.error("--measure-synthetic-fixture requires --profile synthetic-ci")
    return args


def resolve_profile_arguments(
    args: argparse.Namespace, profile: PipelineProfile
) -> tuple[int, str]:
    records = profile.records if args.records is None else args.records
    seed = profile.seed if args.seed is None else args.seed
    if records != profile.records:
        raise AssertionError(
            f"--records {records} does not match locked {profile.name} value "
            f"{profile.records}"
        )
    if seed != profile.seed:
        raise AssertionError(
            f"--seed {seed!r} does not match locked {profile.name} value "
            f"{profile.seed!r}"
        )
    if profile.source_kind == "external" and args.source_net is None:
        raise AssertionError("strong-local requires --source-net")
    if profile.source_kind != "external" and args.source_net is not None:
        raise AssertionError("synthetic-ci generates its source net; omit --source-net")
    return records, seed


def verify_pipeline_checkouts(
    lock: PipelineLock,
    *,
    tools_root: Path,
    trainer_root: Path,
    atomic_root: Path,
    tools_engine: Path,
    engine: Path,
    atomic_commit: str | None,
    measure_synthetic_fixture: bool,
) -> Mapping[str, CheckoutState]:
    """Apply the same narrow measurement exception before and after E2E."""
    return verify_release_checkouts(
        lock,
        tools_root=tools_root,
        trainer_root=trainer_root,
        atomic_root=atomic_root,
        tools_engine=tools_engine,
        engine=engine,
        atomic_commit=atomic_commit,
        allow_unresolved_hashes=measure_synthetic_fixture,
    )


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    tools_engine = require_file(args.tools_engine, "tools engine")
    trainer_root = require_directory(args.trainer_root, "trainer root")
    engine = require_file(args.engine, "Atomic-Stockfish engine")
    build_manifest_paths = {
        "tools": require_file(args.tools_build_manifest, "tools build manifest"),
        "trainer": require_file(
            args.trainer_build_manifest, "trainer build manifest"
        ),
        "atomic": require_file(args.atomic_build_manifest, "Atomic build manifest"),
    }
    atomic_root = require_directory(args.atomic_root, "Atomic-Stockfish checkout")
    lock = load_pipeline_lock(
        args.lock_file, allow_placeholders=args.measure_synthetic_fixture
    )
    profile = lock.profiles[args.profile]
    records, seed = resolve_profile_arguments(args, profile)
    tools_root = find_checkout_root(tools_engine, "tools engine")
    checkouts = verify_pipeline_checkouts(
        lock,
        tools_root=tools_root,
        trainer_root=trainer_root,
        atomic_root=atomic_root,
        tools_engine=tools_engine,
        engine=engine,
        atomic_commit=args.atomic_commit,
        measure_synthetic_fixture=args.measure_synthetic_fixture,
    )
    library_suffix = ".dll" if sys.platform == "win32" else (
        ".dylib" if sys.platform == "darwin" else ".so"
    )
    native_loaders = tuple(
        sorted(trainer_root.glob(f"*training_data_loader*{library_suffix}"))
    )
    if len(native_loaders) != 1:
        raise AssertionError(
            "trainer checkout must contain exactly one platform native loader; "
            f"found {len(native_loaders)} in {trainer_root}"
        )
    try:
        artifact_fingerprints = fingerprint_files(
            (
                ("tools_engine", tools_engine),
                ("trainer_native_loader", native_loaders[0]),
                ("atomic_engine", engine),
                ("tools_build_manifest", build_manifest_paths["tools"]),
                ("trainer_build_manifest", build_manifest_paths["trainer"]),
                ("atomic_build_manifest", build_manifest_paths["atomic"]),
            )
        )
    except CompilerPreflightError as exc:
        raise AssertionError(str(exc)) from exc
    print(
        "LEGACY PIPELINE ARTIFACTS PREFLIGHT "
        + " ".join(item.display() for item in artifact_fingerprints)
    )
    build_manifests = {
        "tools": verify_build_manifest(
            build_manifest_paths["tools"],
            expected_recipe=profile.build_recipes["tools"],
            repository_root=tools_root,
            artifact=tools_engine,
            expected_commit=checkouts["tools"].head,
        ),
        "trainer": verify_build_manifest(
            build_manifest_paths["trainer"],
            expected_recipe=profile.build_recipes["trainer"],
            repository_root=trainer_root,
            artifact=native_loaders[0],
            expected_commit=checkouts["trainer"].head,
        ),
        "atomic": verify_build_manifest(
            build_manifest_paths["atomic"],
            expected_recipe=profile.build_recipes["atomic"],
            repository_root=atomic_root,
            artifact=engine,
            expected_commit=checkouts["atomic"].head,
        ),
    }
    print(
        "LEGACY PIPELINE CLEAN BUILDS VERIFIED "
        + " ".join(
            f"{name}={manifest.recipe}:{manifest.artifact_sha256}"
            for name, manifest in build_manifests.items()
        )
    )
    source_fingerprint: FileFingerprint | None = None
    python_environment: PythonEnvironmentProvenance | None = None

    temp_name = tempfile.mkdtemp(prefix="atomic-pipeline-e2e-")
    root = Path(temp_name).resolve()
    if any(character.isspace() for character in str(root)):
        shutil.rmtree(root)
        raise AssertionError(
            f"tools command paths must not contain whitespace; temporary root was {root}"
        )

    try:
        modules = import_trainer_modules(trainer_root)
        nnue_dataset, features, model_module, ranger_module, serialize, torch = modules
        python_environment = capture_python_environment_provenance()
        emit_python_environment_provenance(python_environment)
        if profile.source_kind == "external":
            assert args.source_net is not None
            source_net = require_file(args.source_net, "frozen source network")
            source_net_bytes = source_net.read_bytes()
        else:
            source_net, source_net_bytes = generate_synthetic_source_network(
                root,
                profile,
                features,
                model_module,
                serialize,
                torch,
                verify_hash=not args.measure_synthetic_fixture,
            )
        source_net_hash = sha256(source_net_bytes)
        try:
            source_fingerprint = fingerprint_file(
                source_net, label="source_network"
            )
        except CompilerPreflightError as exc:
            raise AssertionError(str(exc)) from exc
        if (
            not args.measure_synthetic_fixture
            and source_net_hash != profile.source_net_sha256
        ):
            raise AssertionError(
                f"{profile.name} source network SHA-256 mismatch: "
                f"expected {profile.source_net_sha256}, got {source_net_hash}"
            )

        first_path = root / "generated-a.bin"
        second_path = root / "generated-b.bin"
        first = generate_data(
            tools_engine, source_net, first_path, records, seed
        )
        second = generate_data(
            tools_engine, source_net, second_path, records, seed
        )
        validate_legacy_records(first, records)
        validate_legacy_records(second, records)
        if first != second:
            raise AssertionError(
                "seeded generation is not byte-identical: "
                f"first={sha256(first)}, second={sha256(second)}"
            )
        data_hash = sha256(first)
        if not args.measure_synthetic_fixture and data_hash != profile.data_sha256:
            raise AssertionError(
                f"{profile.name} pure generation fixture changed or "
                "Use NNUE=pure was not applied: "
                f"expected {profile.data_sha256}, got {data_hash}"
            )
        if args.measure_synthetic_fixture:
            print(
                "SYNTHETIC PIPELINE FIXTURE MEASURED (NON-RELEASE) "
                f"source_sha256={source_net_hash} data_sha256={data_hash}"
            )
            return 0

        roundtrip, plain = convert_roundtrip(tools_engine, first_path, root)
        plain_records = parse_plain_records(plain, records)
        batch = decode_batch(
            nnue_dataset,
            roundtrip,
            records,
            loader_seed(seed),
        )
        factorized_feature_set = features.get_feature_set_from_name("HalfKAv2^")
        validate_batch_semantics(
            batch,
            plain_records,
            roundtrip.read_bytes(),
            factorized_feature_set,
            torch,
        )
        network, loss, ft_delta, fc_delta = train_one_step(
            batch,
            features,
            model_module,
            ranger_module,
            torch,
            loader_seed(seed),
        )
        network_file, network_bytes = serialize_and_reimport(
            network, root, features, model_module, serialize
        )
        network_hash = sha256(network_bytes)
        bestmove = load_in_engine(engine, network_file)

        print(
            "LEGACY PIPELINE E2E PASSED "
            f"profile={profile.name} records={records} "
            f"tools_commit={checkouts['tools'].head} "
            f"trainer_commit={checkouts['trainer'].head} "
            f"atomic_commit={checkouts['atomic'].head} "
            f"source_sha256={source_net_hash} "
            f"data_sha256={data_hash} "
            f"nnue_sha256={network_hash} loss={loss:.9g} "
            f"ft_delta={ft_delta:.9g} fc_delta={fc_delta:.9g} "
            f"bestmove={bestmove}"
        )
        return 0
    finally:
        try:
            if python_environment is not None:
                verify_python_environment_provenance(python_environment)
            postflight = artifact_fingerprints
            if source_fingerprint is not None:
                postflight += (source_fingerprint,)
            try:
                verify_file_fingerprints(
                    postflight,
                    emit=print,
                    pass_label="LEGACY PIPELINE ARTIFACT POSTFLIGHT",
                )
            except CompilerPreflightError as exc:
                raise AssertionError(str(exc)) from exc
            post_checkouts = verify_pipeline_checkouts(
                lock,
                tools_root=tools_root,
                trainer_root=trainer_root,
                atomic_root=atomic_root,
                tools_engine=tools_engine,
                engine=engine,
                atomic_commit=args.atomic_commit,
                measure_synthetic_fixture=args.measure_synthetic_fixture,
            )
            print(
                "LEGACY PIPELINE CHECKOUT POSTFLIGHT "
                + " ".join(
                    f"{name}={state.head}"
                    for name, state in post_checkouts.items()
                )
            )
        finally:
            # All native streams are destroyed before this point. Keep cleanup
            # strict so a Windows handle leak makes the gate fail visibly.
            shutil.rmtree(root)


if __name__ == "__main__":
    raise SystemExit(main())
