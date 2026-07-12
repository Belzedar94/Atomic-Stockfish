#!/usr/bin/env python3
"""Clean-build and verify commit-bound Legacy Atomic pipeline artifacts."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
from typing import Callable, Mapping, Sequence, Tuple

from atomic_compiler_preflight import (
    CompilerPreflightError,
    FileFingerprint,
    fingerprint_file,
    parse_compilation_settings,
)
from legacy_pipeline_lock import (
    enforce_clean_checkout,
    load_json_file,
    require_path_within,
)


SCHEMA = "legacy-atomic-v1-clean-build-v1"
UPPER_SHA256_RE = re.compile(r"^[0-9A-F]{64}$")
X86_64_RELEASE_TARGET = "64bit SSE2"
X86_64_BMI2_RELEASE_TARGET = "64bit BMI2 AVX2 SSE41 SSSE3 SSE2 POPCNT"
ENGINE_ROLES = frozenset({"tools", "atomic", "atomic-data-generator"})
CMAKE_SET_RE = re.compile(r"^set\((?P<name>[A-Za-z0-9_]+)\s+(?P<value>.*)\)$")
MSVC_TARGET_RE = re.compile(
    r"\bfor\s+(?P<target>[A-Za-z0-9_.-]+)\s*$", re.IGNORECASE | re.MULTILINE
)
CMAKE_ARCH_FIELDS = (
    "CMAKE_CXX_COMPILER_TARGET",
    "CMAKE_CXX_COMPILER_ARCHITECTURE_ID",
    "CMAKE_CXX_LIBRARY_ARCHITECTURE",
    "CMAKE_GENERATOR_PLATFORM",
)


@dataclass(frozen=True)
class BuildRecipe:
    name: str
    role: str
    platform: str
    artifact_relative: str
    commands: Callable[[Path], Tuple[Tuple[str, ...], ...]]
    clean_directory_relative: str | None = None
    expected_engine_target: str | None = None


@dataclass(frozen=True)
class BuildManifest:
    role: str
    recipe: str
    repository_root: Path
    repository_commit: str
    artifact_path: Path
    artifact_bytes: int
    artifact_sha256: str
    build_log_sha256: str
    toolchain: str
    toolchain_artifacts: Mapping[str, FileFingerprint]


@dataclass(frozen=True)
class ToolchainObservation:
    output: str
    artifacts: Mapping[str, FileFingerprint]


def _msys_path(path: Path) -> str:
    resolved = path.resolve()
    drive = resolved.drive.rstrip(":").lower()
    tail = resolved.as_posix().split(":", 1)[1]
    return f"/{drive}{tail}"


def _msys_make(root: Path, arguments: str) -> Tuple[str, ...]:
    command = (
        "export PATH=/mingw64/bin:$PATH; "
        f"cd {shlex.quote(_msys_path(root))} && make -C src {arguments}"
    )
    return (r"C:\msys64\usr\bin\bash.exe", "-lc", command)


def _msys_root_make(root: Path, arguments: str) -> Tuple[str, ...]:
    command = (
        "export PATH=/mingw64/bin:$PATH; "
        f"cd {shlex.quote(_msys_path(root))} && make {arguments}"
    )
    return (r"C:\msys64\usr\bin\bash.exe", "-lc", command)


def _msys_copy(root: Path, source: str, destination: str) -> Tuple[str, ...]:
    command = (
        "export PATH=/mingw64/bin:$PATH; "
        f"cd {shlex.quote(_msys_path(root))} && "
        f"cp {shlex.quote(source)} {shlex.quote(destination)}"
    )
    return (r"C:\msys64\usr\bin\bash.exe", "-lc", command)


def _windows_tools(root: Path) -> Tuple[Tuple[str, ...], ...]:
    return (
        _msys_root_make(root, "ARCH=x86-64 COMP=mingw verify-engine-pin"),
        _msys_root_make(root, "-j2 ARCH=x86-64 COMP=mingw data-tools"),
    )


def _windows_atomic(root: Path) -> Tuple[Tuple[str, ...], ...]:
    return (
        _msys_make(root, "ARCH=x86-64-bmi2 COMP=mingw clean"),
        _msys_make(root, "-j2 ARCH=x86-64-bmi2 COMP=mingw build"),
        _msys_copy(
            root,
            "src/atomic-stockfish.exe",
            "src/atomic-stockfish-pipeline.exe",
        ),
    )


def _windows_atomic_data_generator(root: Path) -> Tuple[Tuple[str, ...], ...]:
    return (
        _msys_make(root, "ARCH=x86-64-bmi2 COMP=mingw clean"),
        _msys_make(root, "-j2 ARCH=x86-64-bmi2 COMP=mingw data-generator"),
        _msys_copy(
            root,
            "src/atomic-stockfish-data-generator.exe",
            "src/atomic-stockfish-data-generator-pipeline.exe",
        ),
        # The clean generator recipe runs after the normal-engine recipe in the
        # strong-local gate. Restore the public playing artifact without
        # replacing either separately authenticated pipeline copy.
        _msys_make(root, "-j2 ARCH=x86-64-bmi2 COMP=mingw build"),
    )


def _windows_trainer(root: Path) -> Tuple[Tuple[str, ...], ...]:
    build = root / "build" / "pipeline-manifest-release"
    return (
        (
            "cmake",
            "-S",
            str(root),
            "-B",
            str(build),
            "-G",
            "Visual Studio 17 2022",
            "-A",
            "x64",
            "-DCMAKE_BUILD_TYPE=Release",
            f"-DCMAKE_INSTALL_PREFIX={root}",
            "-DBUILD_TESTING=ON",
        ),
        (
            "cmake",
            "--build",
            str(build),
            "--config",
            "Release",
            "--target",
            "install",
            "--parallel",
            "2",
        ),
    )


def _linux_tools(root: Path) -> Tuple[Tuple[str, ...], ...]:
    return (
        ("make", "ARCH=x86-64", "COMP=gcc", "verify-engine-pin"),
        ("make", "-j2", "ARCH=x86-64", "COMP=gcc", "data-tools"),
    )


def _linux_atomic(root: Path) -> Tuple[Tuple[str, ...], ...]:
    return (
        ("make", "-C", "src", "ARCH=x86-64", "clean"),
        ("make", "-C", "src", "-j2", "ARCH=x86-64", "build"),
        ("cp", "src/atomic-stockfish", "src/atomic-stockfish-pipeline"),
    )


def _linux_atomic_data_generator(root: Path) -> Tuple[Tuple[str, ...], ...]:
    return (
        ("make", "-C", "src", "ARCH=x86-64", "clean"),
        (
            "make",
            "-C",
            "src",
            "-j2",
            "ARCH=x86-64",
            "data-generator",
        ),
        (
            "cp",
            "src/atomic-stockfish-data-generator",
            "src/atomic-stockfish-data-generator-pipeline",
        ),
        # Restore the public playing artifact while preserving both immutable
        # pipeline copies, mirroring the strong-local Windows recipe.
        ("make", "-C", "src", "-j2", "ARCH=x86-64", "build"),
    )


def _linux_trainer(root: Path) -> Tuple[Tuple[str, ...], ...]:
    build = root / "build" / "pipeline-manifest-release"
    return (
        (
            "cmake",
            "-S",
            str(root),
            "-B",
            str(build),
            "-DCMAKE_BUILD_TYPE=Release",
            f"-DCMAKE_INSTALL_PREFIX={root}",
            "-DBUILD_TESTING=ON",
        ),
        (
            "cmake",
            "--build",
            str(build),
            "--config",
            "Release",
            "--target",
            "install",
            "-j2",
        ),
    )


RECIPES = {
    recipe.name: recipe
    for recipe in (
        BuildRecipe(
            "strong-local-tools-windows-v2",
            "tools",
            "win32",
            "src/atomic-data-tools.exe",
            _windows_tools,
            expected_engine_target=X86_64_RELEASE_TARGET,
        ),
        BuildRecipe(
            "strong-local-trainer-windows-v1",
            "trainer",
            "win32",
            "training_data_loader.dll",
            _windows_trainer,
            "build/pipeline-manifest-release",
        ),
        BuildRecipe(
            "strong-local-atomic-windows-v2",
            "atomic",
            "win32",
            "src/atomic-stockfish-pipeline.exe",
            _windows_atomic,
            expected_engine_target=X86_64_BMI2_RELEASE_TARGET,
        ),
        BuildRecipe(
            "strong-local-atomic-data-generator-windows-v2",
            "atomic-data-generator",
            "win32",
            "src/atomic-stockfish-data-generator-pipeline.exe",
            _windows_atomic_data_generator,
            expected_engine_target=X86_64_BMI2_RELEASE_TARGET,
        ),
        BuildRecipe(
            "synthetic-ci-tools-linux-v2",
            "tools",
            "linux",
            "src/atomic-data-tools",
            _linux_tools,
            expected_engine_target=X86_64_RELEASE_TARGET,
        ),
        BuildRecipe(
            "synthetic-ci-trainer-linux-v1",
            "trainer",
            "linux",
            "libtraining_data_loader.so",
            _linux_trainer,
            "build/pipeline-manifest-release",
        ),
        BuildRecipe(
            "synthetic-ci-atomic-linux-v2",
            "atomic",
            "linux",
            "src/atomic-stockfish-pipeline",
            _linux_atomic,
            expected_engine_target=X86_64_RELEASE_TARGET,
        ),
        BuildRecipe(
            "synthetic-ci-atomic-data-generator-linux-v2",
            "atomic-data-generator",
            "linux",
            "src/atomic-stockfish-data-generator-pipeline",
            _linux_atomic_data_generator,
            expected_engine_target=X86_64_RELEASE_TARGET,
        ),
    )
}


ATOMIC_DATA_GENERATOR_RECIPES = {
    "strong-local-atomic-windows-v2": (
        "strong-local-atomic-data-generator-windows-v2"
    ),
    "synthetic-ci-atomic-linux-v2": (
        "synthetic-ci-atomic-data-generator-linux-v2"
    ),
}


def atomic_data_generator_recipe_for(atomic_recipe: str) -> str:
    """Return the separately authenticated generator recipe for an Atomic build."""

    try:
        generator_recipe = ATOMIC_DATA_GENERATOR_RECIPES[atomic_recipe]
    except KeyError as exc:
        raise AssertionError(
            f"Atomic build recipe {atomic_recipe!r} has no data-generator recipe"
        ) from exc
    recipe = RECIPES.get(generator_recipe)
    if recipe is None or recipe.role != "atomic-data-generator":
        raise AssertionError(
            f"invalid Atomic data-generator recipe mapping for {atomic_recipe!r}"
        )
    return generator_recipe


def _mapping(value: object, label: str) -> Mapping[str, object]:
    if not isinstance(value, dict):
        raise AssertionError(f"{label} must be a JSON object")
    return value


def _exact_keys(value: Mapping[str, object], expected: set[str], label: str) -> None:
    if set(value) != expected:
        raise AssertionError(
            f"{label} keys must be exactly {sorted(expected)}, got {sorted(value)}"
        )


def _text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise AssertionError(f"{label} must be a non-empty string")
    return value.strip()


def _verbatim_text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise AssertionError(f"{label} must be a non-empty string")
    return value


def _uppercase_sha256(value: object, label: str) -> str:
    if not isinstance(value, str) or not UPPER_SHA256_RE.fullmatch(value):
        raise AssertionError(
            f"{label} must be exactly 64 uppercase hexadecimal characters"
        )
    return value


def _utc_timestamp(value: object, label: str) -> str:
    text = _text(value, label)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise AssertionError(f"{label} must be a parseable UTC timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() != timedelta(0):
        raise AssertionError(f"{label} must be a timezone-aware UTC timestamp")
    return text


def _remove_clean_directory(root: Path, relative: str) -> None:
    target = (root / relative).resolve()
    require_path_within(target, root, "recipe clean directory")
    if target == root or not relative.startswith("build/"):
        raise AssertionError(f"unsafe recipe clean directory: {target}")
    if target.exists():
        shutil.rmtree(target)


def _run_toolchain_command(
    command: Sequence[str],
    *,
    cwd: Path,
    label: str,
    allow_nonzero: bool = False,
) -> str:
    try:
        completed = subprocess.run(
            list(command),
            cwd=cwd,
            text=True,
            encoding="utf-8",
            errors="replace",
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=30.0,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise AssertionError(f"could not probe {label}: {exc}") from exc
    output = completed.stdout.strip()
    if (completed.returncode != 0 and not allow_nonzero) or not output:
        raise AssertionError(
            f"could not probe {label}: exit={completed.returncode} output={output!r}"
        )
    return output


def _cmake_set_values(path: Path) -> Mapping[str, str]:
    try:
        lines = path.read_text(encoding="utf-8", errors="strict").splitlines()
    except OSError as exc:
        raise AssertionError(f"cannot read CMake compiler metadata {path}: {exc}") from exc
    values: dict[str, str] = {}
    for line in lines:
        match = CMAKE_SET_RE.fullmatch(line.strip())
        if match is None:
            continue
        name = match.group("name")
        value = match.group("value").strip()
        if len(value) >= 2 and value.startswith('"') and value.endswith('"'):
            value = value[1:-1]
        values[name] = value
    return values


def _cmake_cache_values(path: Path) -> Mapping[str, str]:
    try:
        lines = path.read_text(encoding="utf-8", errors="strict").splitlines()
    except OSError as exc:
        raise AssertionError(f"cannot read CMake cache {path}: {exc}") from exc
    values: dict[str, str] = {}
    for line in lines:
        if not line or line.startswith(("#", "//")) or "=" not in line:
            continue
        key_and_type, value = line.split("=", 1)
        key = key_and_type.split(":", 1)[0]
        values[key] = value
    return values


def _required_value(values: Mapping[str, str], name: str, label: str) -> str:
    value = values.get(name, "").strip()
    if not value:
        raise AssertionError(f"{label} has no {name}")
    return value


def _cmake_tool_path(value: str, build: Path, label: str) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = build / path
    path = path.resolve()
    if not path.is_file():
        raise AssertionError(f"{label} is not an existing file: {path}")
    return path


def _trainer_toolchain_observation(
    recipe: BuildRecipe, root: Path
) -> ToolchainObservation:
    if recipe.clean_directory_relative is None:
        raise AssertionError(
            f"trainer recipe {recipe.name} has no CMake build directory"
        )
    build = require_path_within(
        root / recipe.clean_directory_relative,
        root,
        "trainer CMake build directory",
    )
    compiler_files = tuple(
        (build / "CMakeFiles").glob("*/CMakeCXXCompiler.cmake")
    )
    if len(compiler_files) != 1:
        raise AssertionError(
            f"trainer CMake provenance requires exactly one "
            f"CMakeCXXCompiler.cmake, got {len(compiler_files)}"
        )
    compiler_metadata = _cmake_set_values(compiler_files[0])
    cache = _cmake_cache_values(build / "CMakeCache.txt")
    compiler_value = _required_value(
        compiler_metadata, "CMAKE_CXX_COMPILER", "CMake compiler metadata"
    )
    compiler = _cmake_tool_path(
        compiler_value, build, "CMAKE_CXX_COMPILER"
    )
    linker_value = _required_value(
        compiler_metadata, "CMAKE_LINKER", "CMake compiler metadata"
    )
    linker = _cmake_tool_path(linker_value, build, "CMAKE_LINKER")
    compiler_id = _required_value(
        compiler_metadata, "CMAKE_CXX_COMPILER_ID", "CMake compiler metadata"
    )
    compiler_version = _required_value(
        compiler_metadata,
        "CMAKE_CXX_COMPILER_VERSION",
        "CMake compiler metadata",
    )
    generator = _required_value(cache, "CMAKE_GENERATOR", "CMake cache")
    provenance = dict(compiler_metadata)
    provenance["CMAKE_GENERATOR"] = generator
    provenance["CMAKE_GENERATOR_PLATFORM"] = cache.get(
        "CMAKE_GENERATOR_PLATFORM", ""
    ).strip()
    architecture_values = [
        provenance.get(name, "").strip() for name in CMAKE_ARCH_FIELDS
    ]
    architecture_values = [value for value in architecture_values if value]
    if not architecture_values:
        raise AssertionError(
            "CMake compiler provenance has no compiler target or architecture"
        )

    before_artifacts = {
        "compiler": fingerprint_file(compiler, label="trainer_compiler"),
        "linker": fingerprint_file(linker, label="trainer_linker"),
    }

    cmake_output = _run_toolchain_command(
        ("cmake", "--version"), cwd=root, label="CMake version"
    )
    if compiler_id == "MSVC":
        compiler_probe = _run_toolchain_command(
            (str(compiler), "/Bv"),
            cwd=root,
            label="C++ compiler identity",
            allow_nonzero=True,
        )
        target_match = MSVC_TARGET_RE.search(compiler_probe)
        if target_match is None:
            raise AssertionError("MSVC compiler probe has no target architecture")
        target_probe = target_match.group("target")
        identity_markers = ("microsoft", "msvc")
    else:
        compiler_probe = _run_toolchain_command(
            (str(compiler), "--version"),
            cwd=root,
            label="C++ compiler identity",
        )
        target_probe = _run_toolchain_command(
            (str(compiler), "-dumpmachine"),
            cwd=root,
            label="C++ compiler target",
        )
        identity_markers = {
            "GNU": ("c++", "g++", "gcc", "gnu"),
            "Clang": ("clang",),
            "AppleClang": ("clang",),
        }.get(compiler_id, (compiler_id.casefold(),))

    combined_probe = compiler_probe.casefold()
    version_candidates = (compiler_version, compiler_version.removesuffix(".0"))
    if not any(candidate.casefold() in combined_probe for candidate in version_candidates):
        raise AssertionError(
            f"C++ compiler probe does not confirm CMake version {compiler_version}"
        )
    if not any(marker.casefold() in combined_probe for marker in identity_markers):
        raise AssertionError(
            f"C++ compiler probe does not confirm CMake ID {compiler_id}"
        )
    normalized_target = target_probe.casefold()
    if not any(
        value.casefold() in normalized_target for value in architecture_values
    ):
        raise AssertionError(
            "C++ compiler probe does not confirm the CMake target/architecture"
        )

    artifacts = {
        "compiler": fingerprint_file(compiler, label="trainer_compiler"),
        "linker": fingerprint_file(linker, label="trainer_linker"),
    }
    if artifacts != before_artifacts:
        raise AssertionError("trainer compiler/linker changed during provenance probe")

    metadata_lines = [
        f"CMAKE_CXX_COMPILER={compiler}",
        f"CMAKE_CXX_COMPILER_ID={compiler_id}",
        f"CMAKE_CXX_COMPILER_VERSION={compiler_version}",
        f"CMAKE_CXX_COMPILER_BYTES={artifacts['compiler'].size}",
        f"CMAKE_CXX_COMPILER_SHA256={artifacts['compiler'].sha256}",
        f"CMAKE_LINKER={linker}",
        f"CMAKE_LINKER_BYTES={artifacts['linker'].size}",
        f"CMAKE_LINKER_SHA256={artifacts['linker'].sha256}",
        f"CMAKE_GENERATOR={generator}",
    ]
    metadata_lines.extend(
        f"{name}={provenance.get(name, '').strip() or '<empty>'}"
        for name in CMAKE_ARCH_FIELDS
    )
    output = "\n".join(
        (
            cmake_output,
            *metadata_lines,
            "CMAKE_CXX_COMPILER_PROBE:",
            compiler_probe,
            "CMAKE_CXX_TARGET_PROBE:",
            target_probe,
        )
    )
    return ToolchainObservation(output, artifacts)


def _toolchain_output(
    recipe: BuildRecipe, artifact: Path, root: Path
) -> str:
    role = recipe.role
    if role == "trainer":
        return _trainer_toolchain_observation(recipe, root).output
    if role not in ENGINE_ROLES:
        raise AssertionError(f"unknown build recipe role {role!r}")
    completed = subprocess.run(
        [str(artifact)],
        input="compiler\nquit\n",
        text=True,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=30.0,
        check=False,
    )
    if completed.returncode != 0:
        raise AssertionError(f"could not record {role} toolchain output")
    return completed.stdout.strip()


def _observe_toolchain(
    recipe: BuildRecipe, artifact: Path, root: Path
) -> ToolchainObservation:
    if recipe.role == "trainer":
        observation = _trainer_toolchain_observation(recipe, root)
    else:
        observation = ToolchainObservation(
            _toolchain_output(recipe, artifact, root), {}
        )
    output = observation.output
    if not isinstance(output, str) or not output.strip():
        raise AssertionError(f"{recipe.name} produced empty toolchain provenance")
    if recipe.role in ENGINE_ROLES:
        if recipe.expected_engine_target is None:
            raise AssertionError(
                f"engine recipe {recipe.name} has no expected release target"
            )
        try:
            signature = parse_compilation_settings(
                output, label=f"{recipe.name} artifact"
            )
        except CompilerPreflightError as exc:
            raise AssertionError(
                f"could not validate {recipe.name} artifact target: {exc}"
            ) from exc
        actual_target = signature.display()
        if actual_target != recipe.expected_engine_target:
            raise AssertionError(
                f"{recipe.name} artifact target mismatch: "
                f"actual=[{actual_target}] "
                f"expected=[{recipe.expected_engine_target}]"
            )
    elif recipe.expected_engine_target is not None:
        raise AssertionError(
            f"non-engine recipe {recipe.name} cannot declare an engine target"
        )
    return observation


def _toolchain_artifacts_document(
    artifacts: Mapping[str, FileFingerprint],
) -> Mapping[str, Mapping[str, object]]:
    if set(artifacts) != {"compiler", "linker"}:
        raise AssertionError(
            "trainer toolchain artifacts must be exactly compiler and linker"
        )
    return {
        name: {
            "path": str(fingerprint.path),
            "bytes": fingerprint.size,
            "sha256": fingerprint.sha256,
        }
        for name, fingerprint in artifacts.items()
    }


def _validated_toolchain_artifacts(value: object) -> Mapping[str, object]:
    artifacts = _mapping(value, "build.toolchain_artifacts")
    _exact_keys(
        artifacts,
        {"compiler", "linker"},
        "build.toolchain_artifacts",
    )
    for name in ("compiler", "linker"):
        fingerprint = _mapping(
            artifacts.get(name), f"build.toolchain_artifacts.{name}"
        )
        _exact_keys(
            fingerprint,
            {"path", "bytes", "sha256"},
            f"build.toolchain_artifacts.{name}",
        )
        _verbatim_text(
            fingerprint.get("path"),
            f"build.toolchain_artifacts.{name}.path",
        )
        byte_count = fingerprint.get("bytes")
        if type(byte_count) is not int or byte_count <= 0:
            raise AssertionError(
                f"build.toolchain_artifacts.{name}.bytes must be a "
                "positive JSON integer"
            )
        _uppercase_sha256(
            fingerprint.get("sha256"),
            f"build.toolchain_artifacts.{name}.sha256",
        )
    return artifacts


def clean_build_manifest(
    *, recipe_name: str, repository_root: Path, output: Path
) -> BuildManifest:
    try:
        recipe = RECIPES[recipe_name]
    except KeyError as exc:
        raise AssertionError(f"unknown build recipe {recipe_name!r}") from exc
    if sys.platform != recipe.platform:
        raise AssertionError(
            f"recipe {recipe.name} requires {recipe.platform}, got {sys.platform}"
        )
    root = repository_root.expanduser().resolve()
    state = enforce_clean_checkout(root, f"{recipe.role} clean build")
    artifact = require_path_within(
        root / recipe.artifact_relative, root, f"{recipe.role} artifact"
    )
    output_path = output.expanduser().resolve()
    if output_path.is_relative_to(root):
        raise AssertionError("build manifest output must be outside its checkout")
    if output_path.exists():
        raise AssertionError(
            f"refusing to overwrite existing build manifest: {output_path}"
        )
    if recipe.clean_directory_relative is not None:
        _remove_clean_directory(root, recipe.clean_directory_relative)
    if artifact.exists():
        artifact.unlink()
    rendered_commands = recipe.commands(root)
    build_output = []
    for command in rendered_commands:
        completed = subprocess.run(
            list(command),
            cwd=root,
            text=True,
            encoding="utf-8",
            errors="replace",
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=1800.0,
            check=False,
        )
        build_output.append(completed.stdout)
        if completed.returncode != 0:
            raise AssertionError(
                f"clean-build recipe {recipe.name} failed with "
                f"{completed.returncode}:\n{completed.stdout[-4000:]}"
            )
    if not artifact.is_file():
        raise AssertionError(f"clean build produced no artifact: {artifact}")
    post_build_state = enforce_clean_checkout(
        root, f"{recipe.role} clean build", state.head
    )
    log_sha = hashlib.sha256("".join(build_output).encode("utf-8")).hexdigest().upper()
    pre_probe_fingerprint = fingerprint_file(
        artifact, label=f"{recipe.role}_artifact"
    )
    toolchain = _observe_toolchain(recipe, artifact, root)
    fingerprint = fingerprint_file(artifact, label=f"{recipe.role}_artifact")
    if fingerprint != pre_probe_fingerprint:
        raise AssertionError(
            f"{recipe.role} artifact changed during toolchain probe: "
            f"before_bytes={pre_probe_fingerprint.size} "
            f"before_sha256={pre_probe_fingerprint.sha256} "
            f"after_bytes={fingerprint.size} "
            f"after_sha256={fingerprint.sha256}"
        )
    post_state = enforce_clean_checkout(
        root, f"{recipe.role} clean build", post_build_state.head
    )
    build_document: dict[str, object] = {
        "commands": [list(command) for command in rendered_commands],
        "log_sha256": log_sha,
        "toolchain": toolchain.output,
        "recorded_utc": datetime.now(timezone.utc).isoformat(),
    }
    if recipe.role == "trainer":
        build_document["toolchain_artifacts"] = _toolchain_artifacts_document(
            toolchain.artifacts
        )
    elif toolchain.artifacts:
        raise AssertionError("engine toolchain observation cannot contain artifacts")
    document = {
        "schema": SCHEMA,
        "role": recipe.role,
        "recipe": recipe.name,
        "repository": {"root": str(root), "commit": post_state.head, "clean": True},
        "artifact": {
            "path": str(fingerprint.path),
            "bytes": fingerprint.size,
            "sha256": fingerprint.sha256,
        },
        "build": build_document,
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8", newline="\n") as destination:
        json.dump(document, destination, indent=2, sort_keys=True)
        destination.write("\n")
    return BuildManifest(
        recipe.role,
        recipe.name,
        root,
        post_state.head,
        fingerprint.path,
        fingerprint.size,
        fingerprint.sha256,
        log_sha,
        toolchain.output,
        dict(toolchain.artifacts),
    )


def verify_build_manifest(
    path: Path,
    *,
    expected_recipe: str,
    repository_root: Path,
    artifact: Path,
    expected_commit: str,
) -> BuildManifest:
    manifest_path = path.expanduser().resolve()
    try:
        recipe = RECIPES[expected_recipe]
    except KeyError as exc:
        raise AssertionError(f"unknown expected recipe {expected_recipe!r}") from exc
    if sys.platform != recipe.platform:
        raise AssertionError(
            f"recipe {recipe.name} requires {recipe.platform}, got {sys.platform}"
        )
    root = repository_root.expanduser().resolve()
    if manifest_path.is_relative_to(root):
        raise AssertionError("build manifest input must be outside its checkout")
    document = load_json_file(manifest_path, "build manifest")
    root_document = _mapping(document, "build manifest")
    _exact_keys(
        root_document,
        {"schema", "role", "recipe", "repository", "artifact", "build"},
        "build manifest",
    )
    if root_document.get("schema") != SCHEMA:
        raise AssertionError("unexpected build manifest schema")
    if root_document.get("role") != recipe.role or root_document.get("recipe") != recipe.name:
        raise AssertionError("build manifest role/recipe mismatch")
    repository = _mapping(root_document.get("repository"), "repository")
    artifact_document = _mapping(root_document.get("artifact"), "artifact")
    build = _mapping(root_document.get("build"), "build")
    _exact_keys(repository, {"root", "commit", "clean"}, "repository")
    _exact_keys(artifact_document, {"path", "bytes", "sha256"}, "artifact")
    expected_build_keys = {
        "commands",
        "log_sha256",
        "toolchain",
        "recorded_utc",
    }
    if recipe.role == "trainer":
        expected_build_keys.add("toolchain_artifacts")
    _exact_keys(build, expected_build_keys, "build")
    artifact_path = require_path_within(
        root / recipe.artifact_relative, root, f"{recipe.role} recipe artifact"
    )
    supplied_artifact = require_path_within(
        artifact, root, f"{recipe.role} artifact argument"
    )
    if supplied_artifact != artifact_path:
        raise AssertionError(
            "artifact argument does not match the tracked recipe artifact path"
        )
    if Path(_text(repository.get("root"), "repository.root")).resolve() != root:
        raise AssertionError("build manifest repository root mismatch")
    if repository.get("commit") != expected_commit or repository.get("clean") is not True:
        raise AssertionError("build manifest commit/clean state mismatch")
    if Path(_text(artifact_document.get("path"), "artifact.path")).resolve() != artifact_path:
        raise AssertionError("build manifest artifact path mismatch")
    expected_commands = [list(command) for command in recipe.commands(root)]
    if build.get("commands") != expected_commands:
        raise AssertionError("build manifest commands do not match tracked recipe")
    log_sha256 = _uppercase_sha256(
        build.get("log_sha256"), "build.log_sha256"
    )
    recorded_toolchain = _verbatim_text(
        build.get("toolchain"), "build.toolchain"
    )
    recorded_toolchain_artifacts = (
        _validated_toolchain_artifacts(build.get("toolchain_artifacts"))
        if recipe.role == "trainer"
        else None
    )
    _utc_timestamp(build.get("recorded_utc"), "build.recorded_utc")
    observed_toolchain = _observe_toolchain(recipe, artifact_path, root)
    if recorded_toolchain != observed_toolchain.output:
        raise AssertionError("build manifest toolchain provenance mismatch")
    if recipe.role == "trainer":
        observed_artifacts = _toolchain_artifacts_document(
            observed_toolchain.artifacts
        )
        if recorded_toolchain_artifacts != observed_artifacts:
            raise AssertionError(
                "build manifest toolchain artifact provenance mismatch"
            )
    fingerprint = fingerprint_file(artifact_path, label=f"{recipe.role}_artifact")
    artifact_bytes = artifact_document.get("bytes")
    if type(artifact_bytes) is not int or artifact_bytes <= 0:
        raise AssertionError("artifact.bytes must be a positive JSON integer")
    if artifact_bytes != fingerprint.size:
        raise AssertionError("build manifest artifact byte count mismatch")
    if artifact_document.get("sha256") != fingerprint.sha256:
        raise AssertionError("build manifest artifact SHA-256 mismatch")
    state = enforce_clean_checkout(root, f"{recipe.role} clean build", expected_commit)
    return BuildManifest(
        recipe.role,
        recipe.name,
        root,
        state.head,
        fingerprint.path,
        fingerprint.size,
        fingerprint.sha256,
        log_sha256,
        recorded_toolchain,
        dict(observed_toolchain.artifacts),
    )


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--recipe", choices=sorted(RECIPES), required=True)
    parser.add_argument("--repository-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    manifest = clean_build_manifest(
        recipe_name=args.recipe,
        repository_root=args.repository_root,
        output=args.output,
    )
    print(
        "LEGACY PIPELINE CLEAN BUILD RECORDED "
        f"role={manifest.role} recipe={manifest.recipe} "
        f"commit={manifest.repository_commit} bytes={manifest.artifact_bytes} "
        f"sha256={manifest.artifact_sha256}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
