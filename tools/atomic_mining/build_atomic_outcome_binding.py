"""Create-new, provenance-sealed build of the native Atomic rules binding."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import sysconfig
from typing import Mapping

from . import atomic_outcome_helper


MANIFEST_SCHEMA = "atomic-e00-native-outcome-build-v1"
_WINDOWS_TOOL = re.compile(r'"([^"\r\n]+\.exe)"', re.IGNORECASE)
_BUILD_ENVIRONMENT = (
    "CC",
    "CFLAGS",
    "CL",
    "CPPFLAGS",
    "CXX",
    "CXXFLAGS",
    "LDFLAGS",
    "_CL_",
)


class AtomicOutcomeBuildError(RuntimeError):
    """The native binding could not be built and sealed."""


@dataclass(frozen=True)
class NativeBindingBuild:
    binding_path: Path
    binding_sha256: str
    manifest_path: Path
    manifest_sha256: str
    source_commit: str


def _canonical_json_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(1 << 20):
            digest.update(block)
    return digest.hexdigest()


def _artifact(path: Path) -> dict[str, object]:
    resolved = path.resolve()
    if not resolved.is_file():
        raise AtomicOutcomeBuildError(f"build artifact is not a file: {resolved}")
    return {
        "bytes": resolved.stat().st_size,
        "path": str(resolved),
        "sha256": _sha256_file(resolved),
    }


def _distribution_version(name: str) -> str | None:
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return None


def _windows_toolchain(log: str) -> dict[str, dict[str, object]]:
    tools: dict[str, dict[str, object]] = {}
    for match in _WINDOWS_TOOL.finditer(log):
        candidate = Path(match.group(1))
        basename = candidate.name.lower()
        if basename not in {"cl.exe", "link.exe"} or basename in tools:
            continue
        tools[basename] = _artifact(candidate)
    return tools


def _posix_toolchain() -> dict[str, dict[str, object]]:
    tools: dict[str, dict[str, object]] = {}
    for label in ("CC", "CXX", "LDSHARED"):
        configured = sysconfig.get_config_var(label)
        if not isinstance(configured, str) or not configured.strip():
            continue
        executable = configured.split()[0]
        try:
            resolved = Path(
                subprocess.run(
                    ["sh", "-c", f"command -v -- {executable}"],
                    check=True,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="strict",
                    timeout=5.0,
                ).stdout.strip()
            )
        except (OSError, subprocess.SubprocessError):
            continue
        if resolved.is_file():
            tools[label.lower()] = _artifact(resolved)
    return tools


def _source_commit(source_root: Path) -> str:
    try:
        commit = subprocess.run(
            ["git", "-C", str(source_root), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="strict",
            timeout=5.0,
        ).stdout.strip().lower()
    except (OSError, subprocess.SubprocessError) as error:
        raise AtomicOutcomeBuildError("cannot read source commit") from error
    return commit


def build_native_rules_binding(
    source_root: Path,
    output_dir: Path,
    *,
    expected_source_commit: str | None = None,
    timeout_seconds: float = 300.0,
    environment: Mapping[str, str] | None = None,
) -> NativeBindingBuild:
    """Build the current binding serially into a new authenticated directory."""

    if (
        isinstance(timeout_seconds, bool)
        or not isinstance(timeout_seconds, (int, float))
        or timeout_seconds <= 0
    ):
        raise ValueError("timeout_seconds must be positive")
    source = source_root.expanduser().resolve()
    output = output_dir.expanduser().resolve()
    if output.exists():
        raise FileExistsError(f"refusing to reuse binding output: {output}")
    output.mkdir(parents=True, exist_ok=False)

    commit = _source_commit(source)
    if expected_source_commit is not None and commit != expected_source_commit.lower():
        raise AtomicOutcomeBuildError("source commit differs before build")
    try:
        _, source_files = atomic_outcome_helper._rules_source_provenance(
            source, commit
        )
    except atomic_outcome_helper.AtomicOutcomeHelperError as error:
        raise AtomicOutcomeBuildError(str(error)) from error

    binding_dir = output / "binding"
    temporary_dir = output / "objects"
    binding_dir.mkdir()
    temporary_dir.mkdir()
    command = [
        str(Path(sys.executable).resolve()),
        str(source / "setup.py"),
        "build_ext",
        "--build-lib",
        str(binding_dir),
        "--build-temp",
        str(temporary_dir),
    ]
    child_environment = dict(os.environ)
    if environment is not None:
        child_environment.update(environment)
    startup: dict[str, object] = {}
    if os.name == "nt":
        startup["creationflags"] = subprocess.CREATE_NO_WINDOW
    try:
        completed = subprocess.run(
            command,
            cwd=source,
            env=child_environment,
            capture_output=True,
            check=False,
            text=True,
            encoding="utf-8",
            errors="strict",
            timeout=float(timeout_seconds),
            **startup,
        )
    except subprocess.TimeoutExpired as error:
        raise AtomicOutcomeBuildError("native binding build timed out") from error

    stdout_path = output / "build.stdout.log"
    stderr_path = output / "build.stderr.log"
    stdout_path.write_text(completed.stdout, encoding="utf-8", newline="\n")
    stderr_path.write_text(completed.stderr, encoding="utf-8", newline="\n")
    if completed.returncode != 0:
        raise AtomicOutcomeBuildError(
            f"native binding build failed with exit {completed.returncode}"
        )

    candidates = sorted(
        path
        for path in binding_dir.iterdir()
        if path.is_file()
        and path.name.startswith("pyffish")
        and path.suffix in {".pyd", ".so"}
    )
    if len(candidates) != 1:
        raise AtomicOutcomeBuildError(
            "native binding build did not produce exactly one extension"
        )
    binding = candidates[0]
    combined_log = completed.stdout + "\n" + completed.stderr
    toolchain = (
        _windows_toolchain(combined_log) if os.name == "nt" else _posix_toolchain()
    )
    if os.name == "nt" and set(toolchain) != {"cl.exe", "link.exe"}:
        raise AtomicOutcomeBuildError("MSVC compiler/linker identity is incomplete")
    if not toolchain:
        raise AtomicOutcomeBuildError("build toolchain identity is empty")

    post_commit = _source_commit(source)
    try:
        _, post_source_files = atomic_outcome_helper._rules_source_provenance(
            source, commit
        )
    except atomic_outcome_helper.AtomicOutcomeHelperError as error:
        raise AtomicOutcomeBuildError(str(error)) from error
    if post_commit != commit or post_source_files != source_files:
        raise AtomicOutcomeBuildError("binding source changed during build")

    python = Path(sys.executable).resolve()
    manifest = {
        "binding": _artifact(binding),
        "build": {
            "command": command,
            "environment": {
                name: child_environment[name]
                for name in _BUILD_ENVIRONMENT
                if name in child_environment
            },
            "returncode": completed.returncode,
            "stderr": _artifact(stderr_path),
            "stdout": _artifact(stdout_path),
        },
        "python": {
            **_artifact(python),
            "cache_tag": sys.implementation.cache_tag,
            "compiler": sys.version,
            "platform": sysconfig.get_platform(),
            "runtime_libraries": (
                atomic_outcome_helper._python_runtime_artifacts()
            ),
        },
        "python_build_dependencies": {
            "setuptools": _distribution_version("setuptools"),
            "wheel": _distribution_version("wheel"),
        },
        "schema": MANIFEST_SCHEMA,
        "source": {
            "commit": commit,
            "files": source_files,
            "root": str(source),
        },
        "toolchain": toolchain,
    }
    manifest_path = output / "manifest.json"
    manifest_wire = _canonical_json_bytes(manifest) + b"\n"
    manifest_path.write_bytes(manifest_wire)
    return NativeBindingBuild(
        binding_path=binding,
        binding_sha256=manifest["binding"]["sha256"],  # type: ignore[index]
        manifest_path=manifest_path,
        manifest_sha256=hashlib.sha256(manifest_wire).hexdigest(),
        source_commit=commit,
    )


__all__ = [
    "AtomicOutcomeBuildError",
    "MANIFEST_SCHEMA",
    "NativeBindingBuild",
    "build_native_rules_binding",
]
