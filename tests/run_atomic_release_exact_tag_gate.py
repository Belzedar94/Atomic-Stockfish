#!/usr/bin/env python3
"""Execute one fixed, fail-closed Atomic-Stockfish exact-tag release gate.

This entry point deliberately exposes five gate IDs rather than a generic
command runner.  Paths come from the tracked exact-tag command plan, are bound
again to the authenticated ``ATOMIC_*`` environment, and are passed to the
existing normative gate with a fixed mode and fixed workload.  A canonical
receipt is published only after the child succeeds, its positive markers are
validated, all repositories and inputs survive postflight, and any private H7
archive has been removed.

``--receipt`` is a safe relative path below
``ATOMIC_EXACT_GATE_EVIDENCE_ROOT``.  The productive plan uses the only paths
accepted by this program: ``receipts/<gate-id>.json``.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
import importlib.util
import json
import os
from pathlib import Path, PurePosixPath
import re
import secrets
import shutil
import stat
import subprocess
import sys
import threading
import time
from typing import Any, BinaryIO, Callable, Mapping, Sequence


REPO_ROOT = Path(__file__).resolve(strict=True).parents[1]


def _load_containment_helper() -> Any:
    """Load the exact checkout helper without consulting ``sys.path``."""

    expected = REPO_ROOT / "scripts" / "atomic_process_containment.py"
    try:
        helper = expected.resolve(strict=True)
    except OSError as error:
        raise RuntimeError("exact gate containment helper is absent") from error
    if helper != expected or not helper.is_file():
        raise RuntimeError("exact gate containment helper is not a real checkout file")
    spec = importlib.util.spec_from_file_location(
        "_atomic_exact_gate_process_containment", helper
    )
    if spec is None or spec.loader is None or spec.origin is None:
        raise RuntimeError("could not create the exact gate containment helper loader")
    if Path(spec.origin).resolve(strict=True) != helper:
        raise RuntimeError("exact gate containment helper loader origin mismatch")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    origin = Path(str(getattr(module, "__file__", ""))).resolve(strict=True)
    if origin != helper:
        raise RuntimeError("loaded exact gate containment helper origin mismatch")
    return module


_CONTAINMENT = _load_containment_helper()
ProcessContainmentError = _CONTAINMENT.ProcessContainmentError
launch_contained = _CONTAINMENT.launch_contained

REPOSITORY = "Belzedar94/Atomic-Stockfish"
RELEASE_REF = "refs/tags/v1.0.0"
TOOLS_REPOSITORY = "Belzedar94/variant-nnue-tools"
TRAINER_REPOSITORY = "Belzedar94/variant-nnue-pytorch"
FAIRY_REPOSITORY = "fairy-stockfish/Fairy-Stockfish"
TOOLS_COMMIT = "450049ee7a0ece32694b11f6c55deb7df1d42a84"
TRAINER_COMMIT = "3a19c16fc3d477b1ee7602ccc6510736bc7604cc"
FAIRY_COMMIT = "fb78cb561aa01708338e35b3dc3b65a42149a3c4"
TOOLS_REF = "refs/remotes/origin/atomic"
TRAINER_REF = "refs/remotes/origin/atomic"
TOOLS_ENGINE_COMMIT = "420c9f35266fbdc2167dc5b9d8d20d90281c60c9"
TRAINER_ENGINE_COMMIT = "420c9f35266fbdc2167dc5b9d8d20d90281c60c9"

NET_SHA256 = "99dc67eabf26a64faeeca3a88b4c38597a840b8d4a874b9f2cf658c6f92a04a6"
FAIRY_H5_SHA256 = "1ae6d680f03128c8404f31a3f264f28b132b557ed3a91a6445ec563a7a33f623"
SYZYGY_INVENTORY_SHA256 = (
    "3d4b7fd0ab387f4f60da2078f612c9e8890e6026f551aebe8631efc157788f23"
)
KPPPPVK_WDL_SHA256 = "897a15846a4b027cbd0a31e425fdf0690f68c0bd4e62105cca2055678e2910f9"
KPPPPVK_DTZ_SHA256 = "e740168d8cbb0bf662863f278ef470c5d7eb395ece0c8bf80fed004a47991bc6"
SYZYGY_TABLE_SHA256 = {
    "KBBBvK.atbw": "114f101f74ab1469d749777b5b7e8b2ada5f47d31627ff60031f4832e6bf76a8",
    "KBBBvK.atbz": "f731d407f3ad8a0368d7f29762d0a70e407ee791dc0f5dcb88fc94eba987e31f",
    "KRvK.atbw": "a17ff195ef2738f00f180e3dd8eb8bcd1d21e57642e78ff8f7b7ebffd233cceb",
    "KPPPPvK.atbw": KPPPPVK_WDL_SHA256,
    "KPPPPvK.atbz": KPPPPVK_DTZ_SHA256,
}

GATE_IDS = (
    "hito4-release",
    "legacy-v1-strong-local",
    "hito5-release",
    "syzygy-real-3-to-6",
    "atomic-bin-v2-strong-local",
)
EXPECTED_RECEIPTS = {gate: f"receipts/{gate}.json" for gate in GATE_IDS}

SHA1_RE = re.compile(r"^[0-9a-f]{40}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
MD5_RE = re.compile(r"^[0-9a-f]{32}$")
SAFE_RECEIPT_COMPONENT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
BESTMOVE_RE = re.compile(r"^[a-h][1-8][a-h][1-8][qrbn]?$|^0000$")

MAX_GIT_OUTPUT = 4 * 1024 * 1024
MAX_CHILD_OUTPUT = 32 * 1024 * 1024
MAX_INVENTORY_BYTES = 1024 * 1024
MAX_PYTHON_IDENTITY_OUTPUT = 4096
HASH_CHUNK = 1024 * 1024
OUTPUT_READ_BYTES = 64 * 1024
PROCESS_POLL_SECONDS = 0.05
FILE_ATTRIBUTE_REPARSE_POINT = 0x400

PIPELINE_PYTHON_IMPLEMENTATION = "cpython"
PIPELINE_PYTHON_VERSION = (3, 10, 18)

TIMEOUTS = {
    "hito4-release": 10_800,
    "legacy-v1-strong-local": 7_200,
    "hito5-release": 32_400,
    "syzygy-real-3-to-6": 1_800,
    "atomic-bin-v2-strong-local": 10_800,
}

SAFE_ENVIRONMENT = frozenset(
    {
        "appdata",
        "comspec",
        "home",
        "lang",
        "lc_all",
        "localappdata",
        "msystem",
        "number_of_processors",
        "path",
        "pathext",
        "processor_architecture",
        "systemdrive",
        "systemroot",
        "temp",
        "tmp",
        "tmpdir",
        "tz",
        "userprofile",
        "windir",
    }
)


class GateError(RuntimeError):
    """The selected exact-tag gate did not prove a release pass."""


@dataclass(frozen=True)
class ReleaseContext:
    repository: str
    ref: str
    commit: str
    gate: str
    evidence_root: Path


@dataclass(frozen=True)
class PreparedGate:
    command: tuple[str, ...]
    timeout_seconds: int
    validate: Callable[[bytes], int]
    launcher: Path
    private_root: Path | None = None


def canonical_json(value: Any) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=True,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        + "\n"
    ).encode("utf-8")


def _duplicate_key_rejector(pairs: Sequence[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise GateError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _reject_nonfinite(token: str) -> None:
    raise GateError(f"non-finite JSON number is forbidden: {token}")


def _load_json(payload: bytes, label: str) -> Any:
    try:
        return json.loads(
            payload.decode("utf-8"),
            object_pairs_hook=_duplicate_key_rejector,
            parse_constant=_reject_nonfinite,
        )
    except GateError:
        raise
    except (UnicodeError, json.JSONDecodeError) as error:
        raise GateError(f"{label} is not strict UTF-8 JSON: {error}") from error


def _minimal_environment(source: Mapping[str, str] | None = None) -> dict[str, str]:
    current = os.environ if source is None else source
    environment = {
        key: value
        for key, value in current.items()
        if key.casefold() in SAFE_ENVIRONMENT and "\x00" not in value
    }
    environment.update(
        {
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONHASHSEED": "0",
            "PYTHONSAFEPATH": "1",
        }
    )
    environment.pop("PYTHONHOME", None)
    environment.pop("PYTHONPATH", None)
    return environment


def _validate_pipeline_python(raw: Path) -> Path:
    """Authenticate the exact CPython runtime required by legacy native modules."""

    interpreter = _real_file(raw, "pipeline Python executable")
    probe = (
        "import json,os,sys;"
        "print(json.dumps({"
        "'executable':os.path.realpath(sys.executable),"
        "'implementation':sys.implementation.name,"
        "'version':[sys.version_info.major,sys.version_info.minor,sys.version_info.micro]"
        "},ensure_ascii=True,allow_nan=False,separators=(',',':'),sort_keys=True))"
    )
    try:
        completed = subprocess.run(
            [str(interpreter), "-I", "-S", "-c", probe],
            cwd=REPO_ROOT,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=_minimal_environment(),
            shell=False,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise GateError(f"pipeline Python identity probe failed: {error}") from error
    if (
        len(completed.stdout) > MAX_PYTHON_IDENTITY_OUTPUT
        or len(completed.stderr) > MAX_PYTHON_IDENTITY_OUTPUT
    ):
        raise GateError("pipeline Python identity output exceeded its release bound")
    if completed.returncode != 0:
        detail = completed.stderr.decode("utf-8", errors="replace").strip()
        raise GateError(
            f"pipeline Python identity probe exited {completed.returncode}: {detail}"
        )
    if completed.stderr:
        raise GateError("pipeline Python identity probe emitted stderr")
    normalized = completed.stdout.replace(b"\r\n", b"\n")
    if normalized.count(b"\n") != 1 or not normalized.endswith(b"\n"):
        raise GateError("pipeline Python identity probe did not emit one canonical line")
    identity = _load_json(normalized, "pipeline Python identity")
    if not isinstance(identity, dict) or set(identity) != {
        "executable",
        "implementation",
        "version",
    }:
        raise GateError("pipeline Python identity has an unexpected field set")
    if normalized != canonical_json(identity):
        raise GateError("pipeline Python identity is not canonical JSON")
    if identity["implementation"] != PIPELINE_PYTHON_IMPLEMENTATION:
        raise GateError("pipeline Python implementation must be CPython")
    version = identity["version"]
    if (
        not isinstance(version, list)
        or len(version) != 3
        or any(type(component) is not int for component in version)
        or tuple(version) != PIPELINE_PYTHON_VERSION
    ):
        rendered = ".".join(str(component) for component in version) if isinstance(version, list) else str(version)
        raise GateError(
            "pipeline Python version is "
            f"{rendered}; expected {'.'.join(map(str, PIPELINE_PYTHON_VERSION))}"
        )
    executable_value = identity["executable"]
    if not isinstance(executable_value, str):
        raise GateError("pipeline Python reported a non-string executable path")
    reported = _real_file(Path(executable_value), "reported pipeline Python executable")
    if not _same_path(reported, interpreter):
        raise GateError("pipeline Python executable identity differs from its binding")
    return interpreter


def _is_reparse(stat_result: os.stat_result) -> bool:
    return bool(
        int(getattr(stat_result, "st_file_attributes", 0))
        & FILE_ATTRIBUTE_REPARSE_POINT
    )


def _absolute_path(raw: Path | str, label: str) -> Path:
    path = Path(raw)
    if not path.is_absolute():
        raise GateError(f"{label} must be an absolute path")
    if any(part in {".", ".."} for part in path.parts):
        raise GateError(f"{label} contains a dot path component")
    return path


def _path_components(path: Path) -> tuple[Path, ...]:
    if not path.is_absolute():
        raise GateError(f"path is not absolute: {path}")
    current = Path(path.anchor)
    components = [current]
    for part in path.parts[1:]:
        current = current / part
        components.append(current)
    return tuple(components)


def _assert_no_link_components(path: Path, label: str) -> os.stat_result:
    last: os.stat_result | None = None
    for component in _path_components(path):
        try:
            last = os.lstat(component)
        except OSError as error:
            raise GateError(f"{label} is missing or unreadable: {component}") from error
        if stat.S_ISLNK(last.st_mode) or _is_reparse(last):
            raise GateError(f"{label} contains a symlink or reparse point: {component}")
    assert last is not None
    return last


def _real_file(raw: Path | str, label: str) -> Path:
    path = _absolute_path(raw, label)
    result = _assert_no_link_components(path, label)
    if not stat.S_ISREG(result.st_mode):
        raise GateError(f"{label} is not a regular file: {path}")
    try:
        resolved = path.resolve(strict=True)
    except OSError as error:
        raise GateError(f"{label} cannot be resolved: {path}") from error
    return resolved


def _real_directory(raw: Path | str, label: str) -> Path:
    path = _absolute_path(raw, label)
    result = _assert_no_link_components(path, label)
    if not stat.S_ISDIR(result.st_mode):
        raise GateError(f"{label} is not a real directory: {path}")
    try:
        resolved = path.resolve(strict=True)
    except OSError as error:
        raise GateError(f"{label} cannot be resolved: {path}") from error
    return resolved


def _same_path(left: Path, right: Path) -> bool:
    return os.path.normcase(str(left)) == os.path.normcase(str(right))


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while True:
            block = source.read(HASH_CHUNK)
            if not block:
                break
            digest.update(block)
    return digest.hexdigest()


def _md5(path: Path) -> str:
    try:
        digest = hashlib.md5(usedforsecurity=False)
    except TypeError:  # pragma: no cover - Python before usedforsecurity
        digest = hashlib.md5()
    with path.open("rb") as source:
        while True:
            block = source.read(HASH_CHUNK)
            if not block:
                break
            digest.update(block)
    return digest.hexdigest()


def _require_sha256(path: Path, expected: str, label: str) -> None:
    actual = _sha256(path)
    if actual != expected:
        raise GateError(f"{label} SHA-256 is {actual}; expected {expected}")


def _run_git(root: Path, *arguments: str) -> str:
    git = shutil.which("git", path=os.environ.get("PATH"))
    if git is None:
        raise GateError("git is unavailable on PATH")
    git_path = _real_file(Path(git).resolve(), "git executable")
    try:
        completed = subprocess.run(
            [str(git_path), "-C", str(root), *arguments],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=_minimal_environment(),
            shell=False,
            timeout=60,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise GateError(f"git {' '.join(arguments)} failed in {root}: {error}") from error
    if len(completed.stdout) > MAX_GIT_OUTPUT or len(completed.stderr) > MAX_GIT_OUTPUT:
        raise GateError("git output exceeded its release bound")
    if completed.returncode != 0:
        detail = completed.stderr.decode("utf-8", errors="replace").strip()
        raise GateError(f"git {' '.join(arguments)} failed in {root}: {detail}")
    try:
        return completed.stdout.decode("utf-8").strip()
    except UnicodeError as error:
        raise GateError("git output is not UTF-8") from error


def _canonical_github_remote(value: str) -> str:
    remote = value.strip().replace("\\", "/")
    patterns = (
        r"https?://github\.com/([^/]+/[^/]+?)(?:\.git)?/?$",
        r"git@github\.com:([^/]+/[^/]+?)(?:\.git)?$",
        r"ssh://git@github\.com/([^/]+/[^/]+?)(?:\.git)?/?$",
    )
    for pattern in patterns:
        match = re.fullmatch(pattern, remote, flags=re.IGNORECASE)
        if match:
            return match.group(1).removesuffix(".git").casefold()
    raise GateError(f"repository origin is not a canonical GitHub remote: {value}")


def _authenticate_git_repository(
    raw_root: Path,
    *,
    label: str,
    repository: str,
    commit: str,
    ref: str | None,
    clean: bool = True,
) -> Path:
    root = _real_directory(raw_root, f"{label} checkout")
    top = _real_directory(Path(_run_git(root, "rev-parse", "--show-toplevel")), f"{label} toplevel")
    if not _same_path(top, root):
        raise GateError(f"{label} path is not its Git toplevel")
    head = _run_git(root, "rev-parse", "HEAD").casefold()
    if head != commit:
        raise GateError(f"{label} HEAD is {head}; expected {commit}")
    if ref is not None:
        ref_commit = _run_git(root, "rev-parse", "--verify", f"{ref}^{{commit}}").casefold()
        if ref_commit != commit:
            raise GateError(f"{label} ref {ref} resolves to {ref_commit}; expected {commit}")
    actual_remote = _canonical_github_remote(_run_git(root, "remote", "get-url", "origin"))
    if actual_remote != repository.casefold():
        raise GateError(f"{label} origin is {actual_remote}; expected {repository.casefold()}")
    status_output = _run_git(
        root,
        "status",
        "--porcelain=v1",
        "--untracked-files=all",
        "--ignore-submodules=none" if clean else "--ignore-submodules=dirty",
    )
    if status_output:
        qualifier = "" if clean else " outside ignored build outputs"
        raise GateError(f"{label} checkout is dirty{qualifier}")
    return root


def _require_tracked_file(root: Path, relative: str, label: str) -> Path:
    pure = PurePosixPath(relative)
    if pure.is_absolute() or not pure.parts or any(part in {"", ".", ".."} for part in pure.parts):
        raise GateError(f"{label} has an unsafe tracked path")
    normalized = pure.as_posix()
    _run_git(root, "ls-files", "--error-unmatch", "--", normalized)
    _run_git(root, "cat-file", "-e", f"HEAD:{normalized}")
    return _real_file(root.joinpath(*pure.parts), label)


def _environment_value(environment: Mapping[str, str], name: str) -> str:
    value = environment.get(name)
    if value is None or not value or "\x00" in value or "\r" in value or "\n" in value:
        raise GateError(f"required environment binding {name} is missing or invalid")
    return value


def _authenticate_release_context(
    gate: str, environment: Mapping[str, str] | None = None
) -> ReleaseContext:
    env = os.environ if environment is None else environment
    if gate not in GATE_IDS:
        raise GateError(f"unknown exact-tag gate: {gate}")
    env_gate = _environment_value(env, "ATOMIC_EXACT_GATE_ID")
    if env_gate != gate:
        raise GateError(f"ATOMIC_EXACT_GATE_ID is {env_gate!r}; expected {gate!r}")
    repository = _environment_value(env, "ATOMIC_EXACT_GATE_REPOSITORY")
    ref = _environment_value(env, "ATOMIC_EXACT_GATE_REF")
    commit = _environment_value(env, "ATOMIC_EXACT_GATE_COMMIT").casefold()
    if repository != REPOSITORY or ref != RELEASE_REF or not SHA1_RE.fullmatch(commit):
        raise GateError("exact-tag repository/ref/commit identity is invalid")
    evidence_root = _real_directory(
        Path(_environment_value(env, "ATOMIC_EXACT_GATE_EVIDENCE_ROOT")),
        "exact-tag evidence root",
    )
    actual_root = _authenticate_git_repository(
        REPO_ROOT,
        label="Atomic-Stockfish",
        repository=REPOSITORY,
        commit=commit,
        ref=ref,
    )
    if not _same_path(actual_root, REPO_ROOT.resolve(strict=True)):
        raise GateError("orchestrator repository root differs from authenticated checkout")
    _require_tracked_file(
        actual_root,
        "tests/run_atomic_release_exact_tag_gate.py",
        "exact-tag gate orchestrator",
    )
    _require_tracked_file(
        actual_root,
        "scripts/atomic_process_containment.py",
        "exact-tag process containment helper",
    )
    return ReleaseContext(repository, ref, commit, gate, evidence_root)


def _safe_receipt_destination(context: ReleaseContext, raw: str) -> Path:
    if not raw or "\\" in raw or "\x00" in raw or "\r" in raw or "\n" in raw:
        raise GateError("--receipt must be a safe POSIX relative path")
    raw_parts = raw.split("/")
    if any(part in {"", ".", ".."} for part in raw_parts):
        raise GateError("--receipt contains an unsafe raw path component")
    pure = PurePosixPath(raw)
    if pure.is_absolute() or not pure.parts or len(pure.parts) > 3:
        raise GateError("--receipt must be a shallow relative path")
    if any(part in {"", ".", ".."} or not SAFE_RECEIPT_COMPONENT_RE.fullmatch(part) for part in pure.parts):
        raise GateError("--receipt contains an unsafe path component")
    expected = EXPECTED_RECEIPTS[context.gate]
    if pure.as_posix() != expected:
        raise GateError(f"--receipt must be exactly {expected}")
    target = context.evidence_root.joinpath(*pure.parts)
    try:
        target.resolve(strict=False).relative_to(context.evidence_root)
    except ValueError as error:
        raise GateError("--receipt escapes the evidence root") from error
    parent = _real_directory(target.parent, "receipt parent")
    if not _same_path(parent, target.parent.resolve(strict=True)):
        raise GateError("receipt parent identity changed")
    try:
        os.lstat(target)
    except FileNotFoundError:
        pass
    except OSError as error:
        raise GateError(f"receipt destination is unreadable: {target}") from error
    else:
        raise GateError(f"receipt already exists: {target}")
    return target


def _bound_file(
    raw: Path,
    *,
    environment: Mapping[str, str],
    binding: str,
    label: str,
    sha256: str | None = None,
) -> Path:
    requested = _real_file(raw, label)
    bound = _real_file(Path(_environment_value(environment, binding)), f"{binding} binding")
    if not _same_path(requested, bound):
        raise GateError(f"{label} differs from authenticated binding {binding}")
    if sha256 is not None:
        _require_sha256(requested, sha256, label)
    return requested


def _bound_directory(
    raw: Path,
    *,
    environment: Mapping[str, str],
    binding: str,
    label: str,
) -> Path:
    requested = _real_directory(raw, label)
    bound = _real_directory(Path(_environment_value(environment, binding)), f"{binding} binding")
    if not _same_path(requested, bound):
        raise GateError(f"{label} differs from authenticated binding {binding}")
    return requested


def _bound_repository(
    raw: Path,
    *,
    environment: Mapping[str, str],
    binding: str,
    label: str,
    repository: str,
    commit: str,
    ref: str | None,
) -> Path:
    requested = _real_directory(raw, label)
    bound = _real_directory(Path(_environment_value(environment, binding)), f"{binding} binding")
    if not _same_path(requested, bound):
        raise GateError(f"{label} differs from authenticated binding {binding}")
    return _authenticate_git_repository(
        requested,
        label=label,
        repository=repository,
        commit=commit,
        ref=ref,
    )


def _artifact(
    arguments: argparse.Namespace,
    name: str,
    environment: Mapping[str, str],
    *,
    label: str | None = None,
) -> Path:
    return _bound_file(
        Path(getattr(arguments, name)),
        environment=environment,
        binding="ATOMIC_ARTIFACT_" + name.upper(),
        label=label or name.replace("_", " "),
    )


def _external(
    arguments: argparse.Namespace,
    name: str,
    environment: Mapping[str, str],
    *,
    sha256: str,
    label: str | None = None,
) -> Path:
    return _bound_file(
        Path(getattr(arguments, name)),
        environment=environment,
        binding="ATOMIC_EXTERNAL_" + name.upper(),
        label=label or name.replace("_", " "),
        sha256=sha256,
    )


def _common_surface_paths(
    arguments: argparse.Namespace,
    context: ReleaseContext,
    environment: Mapping[str, str],
) -> dict[str, Path]:
    atomic_build_root = _bound_build_repository(
        Path(arguments.atomic_build_root),
        environment=environment,
        binding="ATOMIC_DIRECTORY_ATOMIC_BUILD_ROOT",
        label="Atomic build checkout",
        repository=REPOSITORY,
        commit=context.commit,
        ref=context.ref,
    )
    paths = {
        "native": _artifact(arguments, "candidate_bmi2", environment, label="BMI2 candidate"),
        "net": _external(
            arguments,
            "legacy_net",
            environment,
            sha256=NET_SHA256,
            label="Legacy Atomic V1 network",
        ),
        "pyffish": _artifact(arguments, "pyffish", environment),
        "cjs": _artifact(arguments, "cjs", environment),
        "esm": _artifact(arguments, "esm", environment),
        "wasm_wrapper": _artifact(arguments, "wasm_wrapper", environment),
        "cpp_unit": _artifact(arguments, "cpp_unit", environment),
        "cpp_api": _artifact(arguments, "cpp_api", environment),
        "syzygy_driver": _artifact(arguments, "syzygy_driver", environment),
        "tables": _bound_directory(
            Path(arguments.syzygy_combined),
            environment=environment,
            binding="ATOMIC_DIRECTORY_SYZYGY_COMBINED",
            label="combined Atomic Syzygy tables",
        ),
        "fairy_repo": _bound_repository(
            Path(arguments.fairy_repo),
            environment=environment,
            binding="ATOMIC_REPOSITORY_FAIRY",
            label="frozen Fairy-Stockfish",
            repository=FAIRY_REPOSITORY,
            commit=FAIRY_COMMIT,
            ref=None,
        ),
        "atomic_root": atomic_build_root,
    }
    _validate_common_surface_containment(paths, atomic_build_root)
    return paths


def _validate_common_surface_containment(
    paths: Mapping[str, Path], atomic_build_root: Path
) -> None:
    """Keep build-owned helpers inside Git; candidate_bmi2 is a packaged asset."""

    for name in ("cpp_unit", "cpp_api", "syzygy_driver"):
        _require_within(paths[name], atomic_build_root, name.replace("_", " "))


def _pipeline_roots(
    arguments: argparse.Namespace,
    context: ReleaseContext,
    environment: Mapping[str, str],
) -> dict[str, Path]:
    tools_authority = _bound_repository(
        Path(arguments.tools_authority),
        environment=environment,
        binding="ATOMIC_REPOSITORY_TOOLS",
        label="clean tools authority",
        repository=TOOLS_REPOSITORY,
        commit=TOOLS_COMMIT,
        ref=TOOLS_REF,
    )
    trainer_authority = _bound_repository(
        Path(arguments.trainer_authority),
        environment=environment,
        binding="ATOMIC_REPOSITORY_TRAINER",
        label="clean trainer authority",
        repository=TRAINER_REPOSITORY,
        commit=TRAINER_COMMIT,
        ref=TRAINER_REF,
    )
    atomic_root = _bound_build_repository(
        Path(arguments.atomic_build_root),
        environment=environment,
        binding="ATOMIC_DIRECTORY_ATOMIC_BUILD_ROOT",
        label="Atomic build checkout",
        repository=REPOSITORY,
        commit=context.commit,
        ref=context.ref,
    )
    tools_root = _bound_build_repository(
        Path(arguments.tools_build_root),
        environment=environment,
        binding="ATOMIC_DIRECTORY_TOOLS_BUILD_ROOT",
        label="tools build checkout",
        repository=TOOLS_REPOSITORY,
        commit=TOOLS_COMMIT,
        ref=TOOLS_REF,
    )
    trainer_root = _bound_build_repository(
        Path(arguments.trainer_build_root),
        environment=environment,
        binding="ATOMIC_DIRECTORY_TRAINER_BUILD_ROOT",
        label="trainer build checkout",
        repository=TRAINER_REPOSITORY,
        commit=TRAINER_COMMIT,
        ref=TRAINER_REF,
    )
    return {
        "trainer_root": trainer_root,
        "tools_root": tools_root,
        "atomic_root": atomic_root,
        "tools_authority": tools_authority,
        "trainer_authority": trainer_authority,
    }


def _legacy_pipeline_paths(
    arguments: argparse.Namespace,
    context: ReleaseContext,
    environment: Mapping[str, str],
) -> dict[str, Path]:
    paths = _pipeline_roots(arguments, context, environment)
    paths.update(
        {
            "pipeline_python": _validate_pipeline_python(
                _artifact(
                    arguments,
                    "pipeline_python",
                    environment,
                    label="CPython 3.10.18 pipeline runtime",
                )
            ),
            "tools_engine": _artifact(arguments, "tools_engine", environment),
            "atomic_pipeline_engine": _artifact(
                arguments, "atomic_pipeline_engine", environment
            ),
            "atomic_data_generator": _artifact(
                arguments, "atomic_data_generator", environment
            ),
            "tools_build_manifest": _artifact(
                arguments, "tools_build_manifest", environment
            ),
            "trainer_build_manifest": _artifact(
                arguments, "trainer_build_manifest", environment
            ),
            "atomic_build_manifest": _artifact(
                arguments, "atomic_build_manifest", environment
            ),
            "atomic_data_generator_build_manifest": _artifact(
                arguments, "atomic_data_generator_build_manifest", environment
            ),
        }
    )
    _require_within(paths["tools_engine"], paths["tools_root"], "tools engine")
    _require_within(
        paths["atomic_pipeline_engine"], paths["atomic_root"], "Atomic pipeline engine"
    )
    _require_within(
        paths["atomic_data_generator"], paths["atomic_root"], "Atomic data generator"
    )
    return paths


def _bound_build_repository(
    raw: Path,
    *,
    environment: Mapping[str, str],
    binding: str,
    label: str,
    repository: str,
    commit: str,
    ref: str,
) -> Path:
    requested = _bound_directory(
        raw, environment=environment, binding=binding, label=label
    )
    return _authenticate_git_repository(
        requested,
        label=label,
        repository=repository,
        commit=commit,
        ref=ref,
        clean=False,
    )


def _require_within(path: Path, root: Path, label: str) -> None:
    try:
        path.relative_to(root)
    except ValueError as error:
        raise GateError(f"{label} is outside its authenticated build checkout") from error


def _validate_and_bind_arguments(
    arguments: argparse.Namespace,
    context: ReleaseContext,
    environment: Mapping[str, str],
) -> dict[str, Path]:
    gate = context.gate
    if gate == "hito4-release":
        return _common_surface_paths(arguments, context, environment)
    if gate == "legacy-v1-strong-local":
        paths = _legacy_pipeline_paths(arguments, context, environment)
        paths["net"] = _external(
            arguments,
            "legacy_net",
            environment,
            sha256=NET_SHA256,
            label="Legacy Atomic V1 network",
        )
        return paths
    if gate == "hito5-release":
        paths = _common_surface_paths(arguments, context, environment)
        paths.update(_legacy_pipeline_paths(arguments, context, environment))
        paths["incremental_binary"] = _artifact(
            arguments, "incremental_binary", environment
        )
        paths["oracle"] = _external(
            arguments,
            "fairy_h5_oracle",
            environment,
            sha256=FAIRY_H5_SHA256,
            label="frozen Hito 5 Fairy oracle",
        )
        return paths
    if gate == "syzygy-real-3-to-6":
        atomic_root = _bound_build_repository(
            Path(arguments.atomic_build_root),
            environment=environment,
            binding="ATOMIC_DIRECTORY_ATOMIC_BUILD_ROOT",
            label="Atomic build checkout",
            repository=REPOSITORY,
            commit=context.commit,
            ref=context.ref,
        )
        paths = {
            "native": _artifact(
                arguments, "candidate_bmi2", environment, label="BMI2 candidate"
            ),
            "net": _external(
                arguments,
                "legacy_net",
                environment,
                sha256=NET_SHA256,
                label="Legacy Atomic V1 network",
            ),
            "syzygy_driver": _artifact(arguments, "syzygy_driver", environment),
            "tables": _bound_directory(
                Path(arguments.syzygy_combined),
                environment=environment,
                binding="ATOMIC_DIRECTORY_SYZYGY_COMBINED",
                label="combined Atomic Syzygy tables",
            ),
            "inventory": _external(
                arguments,
                "syzygy_inventory",
                environment,
                sha256=SYZYGY_INVENTORY_SHA256,
                label="Atomic Syzygy inventory",
            ),
            "kppppvk_wdl": _external(
                arguments,
                "kppppvk_wdl",
                environment,
                sha256=KPPPPVK_WDL_SHA256,
                label="KPPPPvK WDL external fixture",
            ),
            "kppppvk_dtz": _external(
                arguments,
                "kppppvk_dtz",
                environment,
                sha256=KPPPPVK_DTZ_SHA256,
                label="KPPPPvK DTZ external fixture",
            ),
            "atomic_root": atomic_root,
        }
        _validate_syzygy_artifact_containment(paths, atomic_root)
        _validate_syzygy_inventory(paths)
        return paths
    if gate == "atomic-bin-v2-strong-local":
        paths = _pipeline_roots(arguments, context, environment)
        paths.update(
            {
                "pipeline_python": _validate_pipeline_python(
                    _artifact(
                        arguments,
                        "pipeline_python",
                        environment,
                        label="CPython 3.10.18 pipeline runtime",
                    )
                ),
                "engine": _artifact(
                    arguments,
                    "atomic_pipeline_engine",
                    environment,
                    label="Atomic pipeline engine",
                ),
                "data_tools": _artifact(arguments, "data_tools", environment),
                "wrapper_data_tools": _artifact(
                    arguments, "wrapper_data_tools", environment
                ),
                "trainer_loader": _artifact(arguments, "trainer_loader", environment),
                "atomic_data_generator": _artifact(
                    arguments, "atomic_data_generator", environment
                ),
                "source_net": _external(
                    arguments,
                    "legacy_net",
                    environment,
                    sha256=NET_SHA256,
                    label="Legacy Atomic V1 network",
                ),
                "gate_workspace": _bound_directory(
                    Path(arguments.gate_workspace),
                    environment=environment,
                    binding="ATOMIC_DIRECTORY_GATE_WORKSPACE",
                    label="private exact-gate workspace",
                ),
            }
        )
        _require_within(
            paths["engine"], paths["atomic_root"], "Atomic pipeline engine"
        )
        _require_within(
            paths["atomic_data_generator"],
            paths["atomic_root"],
            "Atomic data generator",
        )
        _require_within(paths["data_tools"], paths["atomic_root"], "Atomic data tools")
        _require_within(
            paths["wrapper_data_tools"], paths["tools_root"], "wrapper data tools"
        )
        _require_within(
            paths["trainer_loader"], paths["trainer_root"], "trainer loader"
        )
        _require_empty_directory(paths["gate_workspace"], "private exact-gate workspace")
        return paths
    raise GateError(f"unsupported exact-tag gate: {gate}")


def _require_empty_directory(root: Path, label: str) -> None:
    try:
        entries = tuple(root.iterdir())
    except OSError as error:
        raise GateError(f"could not inspect {label}") from error
    if entries:
        raise GateError(f"{label} must be empty")


def _validate_syzygy_artifact_containment(
    paths: Mapping[str, Path], atomic_build_root: Path
) -> None:
    """The packaged candidate is external; the same-build probe driver is not."""

    _require_within(paths["syzygy_driver"], atomic_build_root, "Syzygy driver")


def _validate_syzygy_inventory(paths: Mapping[str, Path]) -> None:
    inventory_path = paths["inventory"]
    size = inventory_path.stat().st_size
    if size <= 0 or size > MAX_INVENTORY_BYTES:
        raise GateError("Atomic Syzygy inventory has an invalid size")
    payload = inventory_path.read_bytes()
    if len(payload) != size:
        raise GateError("Atomic Syzygy inventory changed while reading")
    value = _load_json(payload, "Atomic Syzygy inventory")
    if not isinstance(value, list):
        raise GateError("Atomic Syzygy inventory root must be a list")

    targets = {
        "KBBBvK.atbw": "3-4-5",
        "KBBBvK.atbz": "3-4-5",
        "KRvK.atbw": "3-4-5",
        "KPPPPvK.atbw": "6-wdl",
        "KPPPPvK.atbz": "6-dtz",
    }
    selected: dict[str, Mapping[str, Any]] = {}
    for item in value:
        if not isinstance(item, dict):
            continue
        name = item.get("name")
        if name not in targets:
            continue
        if name in selected:
            raise GateError(f"Atomic Syzygy inventory duplicates {name}")
        selected[str(name)] = item
    if set(selected) != set(targets):
        raise GateError("Atomic Syzygy inventory omits a table consumed by the gate")

    tables = paths["tables"]
    for name, expected_directory in targets.items():
        item = selected[name]
        if item.get("directory") != expected_directory:
            raise GateError(f"Atomic Syzygy inventory directory differs for {name}")
        expected_bytes = item.get("bytes")
        expected_md5 = item.get("md5")
        if (
            type(expected_bytes) is not int
            or expected_bytes <= 0
            or not isinstance(expected_md5, str)
            or not MD5_RE.fullmatch(expected_md5)
        ):
            raise GateError(f"Atomic Syzygy inventory metadata is invalid for {name}")
        consumed = _real_file(tables / name, f"consumed Atomic Syzygy table {name}")
        before = consumed.stat()
        actual_md5 = _md5(consumed)
        actual_sha256 = _sha256(consumed)
        if before.st_size != expected_bytes or actual_md5 != expected_md5:
            raise GateError(f"consumed Atomic Syzygy table differs from inventory: {name}")
        after = consumed.stat()
        if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
        ):
            raise GateError(f"consumed Atomic Syzygy table changed while hashing: {name}")
        expected_sha256 = SYZYGY_TABLE_SHA256[name]
        if actual_sha256 != expected_sha256:
            raise GateError(
                f"consumed Atomic Syzygy table SHA-256 differs for {name}: "
                f"{actual_sha256}; expected {expected_sha256}"
            )
        if name == "KPPPPvK.atbw":
            external = paths["kppppvk_wdl"]
        elif name == "KPPPPvK.atbz":
            external = paths["kppppvk_dtz"]
        else:
            continue
        if _sha256(external) != expected_sha256:
            raise GateError(f"consumed Atomic Syzygy six-man table SHA-256 differs: {name}")


def _resolve_program(name: str) -> Path:
    if name == "bash" and os.name == "nt":
        fixed = Path(r"C:\msys64\usr\bin\bash.exe")
        if fixed.is_file():
            return _real_file(fixed, "MSYS2 Bash")
    resolved = shutil.which(name, path=os.environ.get("PATH"))
    if resolved is None:
        raise GateError(f"required program is unavailable on PATH: {name}")
    return _real_file(Path(resolved).resolve(), f"{name} executable")


def _tracked_gate_script(relative: str) -> Path:
    return _require_tracked_file(REPO_ROOT, relative, f"tracked gate {relative}")


def _hito4_command(paths: Mapping[str, Path]) -> tuple[str, ...]:
    return (
        str(_real_file(Path(sys.executable).resolve(), "Python executable")),
        str(_tracked_gate_script("tests/run_hito4.py")),
        "--native",
        str(paths["native"]),
        "--net",
        str(paths["net"]),
        "--pyffish",
        str(paths["pyffish"]),
        "--cjs",
        str(paths["cjs"]),
        "--esm",
        str(paths["esm"]),
        "--tables",
        str(paths["tables"]),
        "--wasm-wrapper",
        str(paths["wasm_wrapper"]),
        "--cpp-unit",
        str(paths["cpp_unit"]),
        "--cpp-api",
        str(paths["cpp_api"]),
        "--syzygy-driver",
        str(paths["syzygy_driver"]),
        "--fairy-repo",
        str(paths["fairy_repo"]),
        "--python",
        str(_real_file(Path(sys.executable).resolve(), "Python executable")),
        "--node",
        str(_resolve_program("node")),
        "--bash",
        str(_resolve_program("bash")),
        "--timeout",
        "600",
    )


def _legacy_command(paths: Mapping[str, Path], context: ReleaseContext) -> tuple[str, ...]:
    return (
        str(paths["pipeline_python"]),
        str(_tracked_gate_script("tests/legacy_pipeline_e2e.py")),
        "--profile",
        "strong-local",
        "--tools-engine",
        str(paths["tools_engine"]),
        "--trainer-root",
        str(paths["trainer_root"]),
        "--engine",
        str(paths["atomic_pipeline_engine"]),
        "--atomic-data-generator",
        str(paths["atomic_data_generator"]),
        "--tools-build-manifest",
        str(paths["tools_build_manifest"]),
        "--trainer-build-manifest",
        str(paths["trainer_build_manifest"]),
        "--atomic-build-manifest",
        str(paths["atomic_build_manifest"]),
        "--atomic-data-generator-build-manifest",
        str(paths["atomic_data_generator_build_manifest"]),
        "--source-net",
        str(paths["net"]),
        "--atomic-root",
        str(paths["atomic_root"]),
        "--atomic-commit",
        context.commit,
    )


def _hito5_command(paths: Mapping[str, Path]) -> tuple[str, ...]:
    return (
        str(_real_file(Path(sys.executable).resolve(), "Python executable")),
        str(_tracked_gate_script("tests/run_hito5.py")),
        "--mode",
        "release",
        "--native",
        str(paths["native"]),
        "--net",
        str(paths["net"]),
        "--pyffish",
        str(paths["pyffish"]),
        "--cjs",
        str(paths["cjs"]),
        "--esm",
        str(paths["esm"]),
        "--tables",
        str(paths["tables"]),
        "--wasm-wrapper",
        str(paths["wasm_wrapper"]),
        "--incremental-binary",
        str(paths["incremental_binary"]),
        "--oracle",
        str(paths["oracle"]),
        "--cpp-unit",
        str(paths["cpp_unit"]),
        "--cpp-api",
        str(paths["cpp_api"]),
        "--syzygy-driver",
        str(paths["syzygy_driver"]),
        "--fairy-repo",
        str(paths["fairy_repo"]),
        "--pipeline-tools-engine",
        str(paths["tools_engine"]),
        "--pipeline-trainer-root",
        str(paths["trainer_root"]),
        "--pipeline-atomic-engine",
        str(paths["atomic_pipeline_engine"]),
        "--pipeline-atomic-root",
        str(paths["atomic_root"]),
        "--pipeline-atomic-commit",
        str(_run_git(paths["atomic_root"], "rev-parse", "HEAD")),
        "--pipeline-tools-build-manifest",
        str(paths["tools_build_manifest"]),
        "--pipeline-trainer-build-manifest",
        str(paths["trainer_build_manifest"]),
        "--pipeline-atomic-build-manifest",
        str(paths["atomic_build_manifest"]),
        "--pipeline-atomic-data-generator",
        str(paths["atomic_data_generator"]),
        "--pipeline-atomic-data-generator-build-manifest",
        str(paths["atomic_data_generator_build_manifest"]),
        "--pipeline-python",
        str(paths["pipeline_python"]),
        "--python",
        str(_real_file(Path(sys.executable).resolve(), "Python executable")),
        "--node",
        str(_resolve_program("node")),
        "--bash",
        str(_resolve_program("bash")),
        "--hito4-timeout",
        "600",
        "--incremental-timeout",
        "7200",
        "--differential-timeout",
        "7200",
        "--engine-timeout",
        "30",
        "--pipeline-timeout",
        "7200",
    )


def _syzygy_command(paths: Mapping[str, Path]) -> tuple[str, ...]:
    return (
        str(_real_file(Path(sys.executable).resolve(), "Python executable")),
        str(_tracked_gate_script("tests/atomic_syzygy_uci.py")),
        "--engine",
        str(paths["native"]),
        "--tables",
        str(paths["tables"]),
        "--require-six-man",
        "--syzygy-driver",
        str(paths["syzygy_driver"]),
        "--eval-file",
        str(paths["net"]),
        "--timeout",
        "60",
    )


def _create_h7_private_root(workspace: Path) -> tuple[Path, Path]:
    parent = _real_directory(workspace, "H7 private parent")
    _require_empty_directory(parent, "H7 private parent")
    if any(character.isspace() for character in str(parent)):
        raise GateError("H7 private parent path contains whitespace")
    name = f"atomic-h7-{os.getpid()}-{secrets.token_hex(8)}"
    private_root = parent / name
    try:
        private_root.mkdir(mode=0o700)
    except OSError as error:
        raise GateError("could not create exclusive H7 private root") from error
    private_root = _real_directory(private_root, "H7 private root")
    output = private_root / "archive"
    if output.exists() or output.is_symlink():
        raise GateError("H7 output unexpectedly exists")
    return private_root, output


def _h7_command(
    paths: Mapping[str, Path], context: ReleaseContext, output: Path
) -> tuple[str, ...]:
    tools_wrapper = _require_tracked_file(
        paths["tools_root"], "script/atomic_bin_v2_tools.py", "tools V2 wrapper"
    )
    train_script = _require_tracked_file(
        paths["trainer_root"], "train.py", "trainer train script"
    )
    serialize_script = _require_tracked_file(
        paths["trainer_root"], "serialize.py", "trainer serialize script"
    )
    python = paths["pipeline_python"]
    return (
        str(python),
        str(_tracked_gate_script("tests/atomic_bin_v2_pipeline_e2e.py")),
        "--profile",
        "strong-local",
        "--atomic-root",
        str(paths["atomic_root"]),
        "--atomic-commit",
        context.commit,
        "--atomic-ref",
        context.ref,
        "--tools-root",
        str(paths["tools_root"]),
        "--tools-commit",
        TOOLS_COMMIT,
        "--tools-ref",
        TOOLS_REF,
        "--trainer-root",
        str(paths["trainer_root"]),
        "--trainer-commit",
        TRAINER_COMMIT,
        "--trainer-ref",
        TRAINER_REF,
        "--tools-engine-commit",
        TOOLS_ENGINE_COMMIT,
        "--trainer-engine-commit",
        TRAINER_ENGINE_COMMIT,
        "--engine",
        str(paths["engine"]),
        "--engine-sha256",
        _sha256(paths["engine"]),
        "--data-generator",
        str(paths["atomic_data_generator"]),
        "--data-generator-sha256",
        _sha256(paths["atomic_data_generator"]),
        "--data-tools",
        str(paths["data_tools"]),
        "--data-tools-sha256",
        _sha256(paths["data_tools"]),
        "--tools-wrapper",
        str(tools_wrapper),
        "--wrapper-data-tools",
        str(paths["wrapper_data_tools"]),
        "--wrapper-data-tools-sha256",
        _sha256(paths["wrapper_data_tools"]),
        "--trainer-loader",
        str(paths["trainer_loader"]),
        "--trainer-loader-sha256",
        _sha256(paths["trainer_loader"]),
        "--train-script",
        str(train_script),
        "--serialize-script",
        str(serialize_script),
        "--python",
        str(python),
        "--python-sha256",
        _sha256(python),
        "--source-net",
        str(paths["source_net"]),
        "--source-net-sha256",
        NET_SHA256,
        "--output-dir",
        str(output),
        "--train-seed",
        "2026071301",
        "--validation-seed",
        "2026071302",
        "--timeout-seconds",
        "1800",
    )


def _marker_validator(
    gate: str, markers: Sequence[tuple[bytes, int]]
) -> Callable[[bytes], int]:
    expected_checks = sum(count for _, count in markers)

    def validate(output: bytes) -> int:
        for marker, expected in markers:
            actual = output.count(marker)
            if actual != expected:
                preview = marker.decode("ascii", errors="replace")
                raise GateError(
                    f"{gate} emitted marker {preview!r} {actual} times; expected {expected}"
                )
        return expected_checks

    return validate


HITO4_MARKERS = (
    (b"Atomic C++ unit tests passed: 88/88", 1),
    (b"Atomic API unit tests passed", 1),
    (b"binding fixture validation passed: 58 fixtures, 22 Python tests, 58 JavaScript tests, 8 perft vectors", 1),
    (b"Ran 22 tests", 1),
    (b"CommonJS/WASM: 58 fixtures and binding lifecycle checks passed", 1),
    (b"ES module/WASM: 58 fixtures and binding lifecycle checks passed", 1),
    (b"PASS Exact cross-surface parity 40 binding fixtures, 25 native intersections", 1),
    (b"Atomic perft and rule-transition suite passed", 1),
    (b"Atomic search regressions passed: 16/16", 2),
    (b"XBoard Atomic protocol passed", 1),
    (b"Atomic Syzygy tests passed: 5/5", 1),
    (b"NNUE mode contract passed: false, true, pure, and nonfatal invalid-net rejection", 1),
    (b"Atomic reprosearch passed: 12/12", 1),
    (b"signature OK: 338376", 1),
    (b"Atomic instrumented runtime passed:", 1),
    (f"WASM engine integration: PASS (net sha256={NET_SHA256})".encode("ascii"), 1),
    (b"\nHito 4 validation passed\n", 1),
)

HITO5_MARKERS = (
    (b"LEGACY PIPELINE E2E PASSED profile=strong-local records=32 ", 1),
    (b"Hito 4 validation passed", 1),
    (b"LegacyAtomicV1 incremental gate passed: mode=release requested-random-operations=1000000", 1),
    (b"Legacy Atomic V1 differential passed: 10000/10000 positions;", 1),
    (b"Hito 5 release validation passed: incremental-operations=1000000 structural-positions=10000 capture-forced-refresh=0", 1),
)

SYZYGY_MARKERS = (
    (b"Atomic Syzygy KPPPPvK direct probe: PASS wdl=2 dtz=1 pieces=6 root_cardinality=0", 1),
    (b"Atomic Syzygy UCI tests passed: max6, load, root ranking, interior WDL, terminal, castling, Atomic960, recoverable paths, six-man-limit=True, analysis=False, NNUE=false/true", 1),
)


def _validate_legacy_output(output: bytes, commit: str) -> int:
    text = output.decode("utf-8", errors="replace")
    expression = re.compile(
        r"^LEGACY PIPELINE E2E PASSED "
        r"profile=strong-local records=32 "
        rf"tools_commit={TOOLS_COMMIT} trainer_commit={TRAINER_COMMIT} "
        rf"atomic_commit={re.escape(commit)} source_sha256={NET_SHA256} "
        r"data_sha256=[0-9a-f]{64} nnue_sha256=[0-9a-f]{64} "
        r"loss=[^ ]+ ft_delta=[^ ]+ fc_delta=[^ ]+ bestmove=(?:[a-h][1-8][a-h][1-8][qrbn]?|0000)$",
        flags=re.MULTILINE,
    )
    matches = expression.findall(text)
    if len(matches) != 1:
        raise GateError("legacy-v1-strong-local did not emit exactly one locked E2E pass marker")
    if text.count("LEGACY PIPELINE ARTIFACT POSTFLIGHT") != 1:
        raise GateError("legacy-v1-strong-local did not emit exactly one artifact postflight")
    if text.count("LEGACY PIPELINE CHECKOUT POSTFLIGHT") != 1:
        raise GateError("legacy-v1-strong-local did not emit exactly one checkout postflight")
    return 3


def _validate_h7_output(output: bytes, output_dir: Path, context: ReleaseContext) -> int:
    value = _load_json(output, "H7 terminal result")
    if not isinstance(value, dict) or canonical_json(value) != output:
        raise GateError("H7 terminal result is not one canonical LF JSON object")
    required = {
        "schema_version",
        "status",
        "atomic_commit",
        "tools_commit",
        "trainer_commit",
        "profile",
        "tools_engine_commit",
        "trainer_engine_commit",
        "train_manifest_sha256",
        "validation_manifest_sha256",
        "candidate_sha256",
        "global_step",
        "bestmove_nodes_1",
    }
    if set(value) != required:
        raise GateError("H7 terminal result field set differs from the release contract")
    expected = {
        "schema_version": 2,
        "status": "passed",
        "atomic_commit": context.commit,
        "tools_commit": TOOLS_COMMIT,
        "trainer_commit": TRAINER_COMMIT,
        "profile": "strong-local",
        "tools_engine_commit": TOOLS_ENGINE_COMMIT,
        "trainer_engine_commit": TRAINER_ENGINE_COMMIT,
    }
    if any(value.get(key) != expected_value for key, expected_value in expected.items()):
        raise GateError("H7 terminal result identity/status differs from the release contract")
    for key in ("train_manifest_sha256", "validation_manifest_sha256", "candidate_sha256"):
        if not isinstance(value.get(key), str) or not SHA256_RE.fullmatch(str(value[key])):
            raise GateError(f"H7 terminal result has invalid {key}")
    if type(value.get("global_step")) is not int or int(value["global_step"]) < 1:
        raise GateError("H7 terminal result has no positive global step")
    if not isinstance(value.get("bestmove_nodes_1"), str) or not BESTMOVE_RE.fullmatch(
        str(value["bestmove_nodes_1"])
    ):
        raise GateError("H7 terminal result has an invalid bestmove")
    result_path = _real_file(output_dir / "result.json", "H7 archived result")
    archived = result_path.read_bytes()
    if archived != output:
        raise GateError("H7 archived result differs from its terminal result")
    return 1


def _prepare_gate(
    paths: Mapping[str, Path], context: ReleaseContext
) -> PreparedGate:
    gate = context.gate
    controller = _real_file(Path(sys.executable).resolve(), "Python executable")
    if gate == "hito4-release":
        return PreparedGate(
            _hito4_command(paths),
            TIMEOUTS[gate],
            _marker_validator(gate, HITO4_MARKERS),
            controller,
        )
    if gate == "legacy-v1-strong-local":
        return PreparedGate(
            _legacy_command(paths, context),
            TIMEOUTS[gate],
            lambda output: _validate_legacy_output(output, context.commit),
            paths["pipeline_python"],
        )
    if gate == "hito5-release":
        return PreparedGate(
            _hito5_command(paths),
            TIMEOUTS[gate],
            _marker_validator(gate, HITO5_MARKERS),
            controller,
        )
    if gate == "syzygy-real-3-to-6":
        return PreparedGate(
            _syzygy_command(paths),
            TIMEOUTS[gate],
            _marker_validator(gate, SYZYGY_MARKERS),
            controller,
        )
    if gate == "atomic-bin-v2-strong-local":
        private_root, output = _create_h7_private_root(paths["gate_workspace"])
        return PreparedGate(
            _h7_command(paths, context, output),
            TIMEOUTS[gate],
            lambda data: _validate_h7_output(data, output, context),
            paths["pipeline_python"],
            private_root,
        )
    raise GateError(f"unsupported exact-tag gate: {gate}")


def _capture_bounded_output(
    source: BinaryIO,
    output: bytearray,
    maximum: int,
    exceeded: threading.Event,
    failures: list[BaseException],
) -> None:
    try:
        while True:
            chunk = os.read(source.fileno(), OUTPUT_READ_BYTES)
            if not chunk:
                return
            remaining = maximum - len(output)
            accepted = chunk[: max(0, remaining)]
            if accepted:
                output.extend(accepted)
            if len(accepted) != len(chunk):
                exceeded.set()
                return
    except BaseException as error:  # pragma: no cover - OS pipe failures are rare
        failures.append(error)
    finally:
        try:
            source.close()
        except OSError:
            pass


def _join_output_reader(
    reader: threading.Thread,
    source: BinaryIO,
    *,
    timeout: float,
) -> None:
    deadline = time.monotonic() + timeout
    capture_deadline = deadline - min(1.0, max(0.0, timeout))
    reader.join(timeout=max(0.0, capture_deadline - time.monotonic()))
    if not reader.is_alive():
        return
    try:
        source.close()
    except OSError:
        pass
    reader.join(timeout=max(0.0, deadline - time.monotonic()))
    if reader.is_alive():
        raise GateError("gate output capture did not finish")


def _execute_command(
    command: Sequence[str],
    *,
    timeout_seconds: int,
    environment: Mapping[str, str],
    expected_launcher: Path | None = None,
) -> bytes:
    if not command:
        raise GateError("exact gate command is empty")
    actual_launcher = _real_file(Path(command[0]), "exact gate Python launcher")
    authenticated_launcher = _real_file(
        expected_launcher or Path(sys.executable).resolve(),
        "authenticated exact gate Python launcher",
    )
    if not _same_path(actual_launcher, authenticated_launcher):
        raise GateError("exact gate must use its authenticated Python launcher")
    if not 1 <= timeout_seconds <= 32_400:
        raise GateError("exact gate timeout is outside the fixed release bound")
    started = time.monotonic()
    output_buffer = bytearray()
    exceeded = threading.Event()
    reader_failures: list[BaseException] = []
    limit_error: str | None = None
    try:
        containment = launch_contained(
            command,
            cwd=REPO_ROOT,
            environment=environment,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
    except (OSError, ProcessContainmentError) as error:
        raise GateError(f"could not launch exact gate: {error}") from error
    process = containment.process
    source = process.stdout
    reader: threading.Thread | None = None
    reader_started = False
    operation_error: BaseException | None = None
    finalization_error: BaseException | None = None
    try:
        if source is None:  # pragma: no cover
            raise GateError("could not capture exact gate output")
        reader = threading.Thread(
            target=_capture_bounded_output,
            args=(
                source,
                output_buffer,
                MAX_CHILD_OUTPUT,
                exceeded,
                reader_failures,
            ),
            name="atomic-exact-gate-output",
            daemon=True,
        )
        reader.start()
        reader_started = True
        while process.poll() is None:
            elapsed = time.monotonic() - started
            if elapsed > timeout_seconds:
                limit_error = f"gate timed out after {timeout_seconds} seconds"
                break
            if exceeded.is_set():
                limit_error = (
                    f"gate output exceeded its {MAX_CHILD_OUTPUT}-byte release bound"
                )
                break
            if reader_failures:
                limit_error = "gate output capture failed"
                break
            exceeded.wait(PROCESS_POLL_SECONDS)
    except BaseException as error:
        operation_error = error
    finally:
        try:
            containment.terminate_tree(timeout=15)
        except ProcessContainmentError as error:
            finalization_error = error
        if reader_started and reader is not None and source is not None:
            try:
                _join_output_reader(reader, source, timeout=15)
            except GateError as error:
                if finalization_error is None:
                    finalization_error = error
        elif source is not None:
            try:
                source.close()
            except OSError:
                pass
    if finalization_error is not None:
        raise GateError(
            f"gate process tree did not terminate cleanly: {finalization_error}"
        ) from operation_error
    if operation_error is not None:
        if isinstance(operation_error, (KeyboardInterrupt, SystemExit)):
            raise operation_error
        if isinstance(operation_error, GateError):
            raise operation_error
        raise GateError(f"could not execute exact gate: {operation_error}") from operation_error
    return_code = process.returncode
    if return_code is None:  # pragma: no cover - containment verifies this
        raise GateError("gate root process was not reaped")
    if reader_failures:
        raise GateError(
            f"gate output capture failed: {reader_failures[0]}"
        ) from reader_failures[0]
    if exceeded.is_set() and limit_error is None:
        limit_error = f"gate output exceeded its {MAX_CHILD_OUTPUT}-byte release bound"
    if limit_error is not None:
        raise GateError(limit_error)
    output = bytes(output_buffer)
    if output:
        sys.stdout.buffer.write(output)
        if not output.endswith(b"\n"):
            sys.stdout.buffer.write(b"\n")
        sys.stdout.buffer.flush()
    duration = int((time.monotonic() - started) * 1000)
    if return_code != 0:
        raise GateError(f"gate child exited {return_code} after {duration} ms")
    if not output:
        raise GateError("gate child emitted no auditable output")
    return output


def _remove_private_tree(root: Path | None) -> None:
    if root is None:
        return
    validated = _real_directory(root, "private H7 root")
    try:
        shutil.rmtree(validated)
    except OSError as error:
        raise GateError("could not remove private H7 evidence") from error
    if validated.exists() or validated.is_symlink():
        raise GateError("private H7 evidence remains after cleanup")


def _write_receipt(path: Path, gate: str, passed: int) -> bytes:
    if type(passed) is not int or passed < 1:
        raise GateError("gate produced no positive verified check count")
    parent = _real_directory(path.parent, "receipt parent at publication")
    if path.exists() or path.is_symlink():
        raise GateError(f"receipt already exists at publication: {path}")
    payload = canonical_json(
        {
            "schemaVersion": 1,
            "gate": gate,
            "status": "pass",
            "passed": passed,
            "failed": 0,
            "skipped": 0,
        }
    )
    temporary = parent / f".{path.name}.{os.getpid()}.{secrets.token_hex(8)}.tmp"
    descriptor: int | None = None
    target_created = False
    try:
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "wb", closefd=True) as destination:
            descriptor = None
            destination.write(payload)
            destination.flush()
            os.fsync(destination.fileno())
        _real_file(temporary, "staged receipt")
        try:
            os.link(temporary, path)
        except FileExistsError as error:
            raise GateError(f"receipt was created concurrently: {path}") from error
        except OSError as error:
            raise GateError("filesystem cannot publish an exclusive atomic receipt") from error
        target_created = True
        temporary.unlink()
        published = _real_file(path, "published receipt")
        if published.read_bytes() != payload or published.stat().st_nlink != 1:
            raise GateError("published receipt differs from canonical staged bytes")
        if os.name != "nt":
            directory_fd = os.open(parent, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
    except BaseException:
        if descriptor is not None:
            os.close(descriptor)
        if target_created:
            try:
                path.unlink()
            except OSError:
                pass
        try:
            temporary.unlink()
        except OSError:
            pass
        raise
    return payload


def _reject_duplicate_options(argv: Sequence[str]) -> None:
    seen: set[str] = set()
    for token in argv[1:]:
        if not token.startswith("--"):
            continue
        option = token.split("=", 1)[0]
        if option in seen:
            raise GateError(f"duplicate option is forbidden: {option}")
        seen.add(option)


def _add_receipt(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--receipt",
        required=True,
        help="exact safe relative receipt path under ATOMIC_EXACT_GATE_EVIDENCE_ROOT",
    )


def _add_common_surface(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--atomic-build-root", type=Path, required=True)
    parser.add_argument("--candidate-bmi2", type=Path, required=True)
    parser.add_argument("--legacy-net", type=Path, required=True)
    parser.add_argument("--pyffish", type=Path, required=True)
    parser.add_argument("--cjs", type=Path, required=True)
    parser.add_argument("--esm", type=Path, required=True)
    parser.add_argument("--wasm-wrapper", type=Path, required=True)
    parser.add_argument("--cpp-unit", type=Path, required=True)
    parser.add_argument("--cpp-api", type=Path, required=True)
    parser.add_argument("--syzygy-driver", type=Path, required=True)
    parser.add_argument("--syzygy-combined", type=Path, required=True)
    parser.add_argument("--fairy-repo", type=Path, required=True)


def _add_pipeline_roots(
    parser: argparse.ArgumentParser, *, include_atomic_root: bool = True
) -> None:
    if include_atomic_root:
        parser.add_argument("--atomic-build-root", type=Path, required=True)
    parser.add_argument("--tools-build-root", type=Path, required=True)
    parser.add_argument("--trainer-build-root", type=Path, required=True)
    parser.add_argument("--tools-authority", type=Path, required=True)
    parser.add_argument("--trainer-authority", type=Path, required=True)


def _add_legacy_pipeline(
    parser: argparse.ArgumentParser, *, include_atomic_root: bool = True
) -> None:
    _add_pipeline_roots(parser, include_atomic_root=include_atomic_root)
    parser.add_argument("--pipeline-python", type=Path, required=True)
    parser.add_argument("--tools-engine", type=Path, required=True)
    parser.add_argument("--atomic-pipeline-engine", type=Path, required=True)
    parser.add_argument("--atomic-data-generator", type=Path, required=True)
    parser.add_argument("--tools-build-manifest", type=Path, required=True)
    parser.add_argument("--trainer-build-manifest", type=Path, required=True)
    parser.add_argument("--atomic-build-manifest", type=Path, required=True)
    parser.add_argument(
        "--atomic-data-generator-build-manifest", type=Path, required=True
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    subparsers = parser.add_subparsers(dest="gate", required=True)

    hito4 = subparsers.add_parser("hito4-release", allow_abbrev=False)
    _add_receipt(hito4)
    _add_common_surface(hito4)

    legacy = subparsers.add_parser("legacy-v1-strong-local", allow_abbrev=False)
    _add_receipt(legacy)
    _add_legacy_pipeline(legacy)
    legacy.add_argument("--legacy-net", type=Path, required=True)

    hito5 = subparsers.add_parser("hito5-release", allow_abbrev=False)
    _add_receipt(hito5)
    _add_common_surface(hito5)
    _add_legacy_pipeline(hito5, include_atomic_root=False)
    hito5.add_argument("--incremental-binary", type=Path, required=True)
    hito5.add_argument("--fairy-h5-oracle", type=Path, required=True)

    syzygy = subparsers.add_parser("syzygy-real-3-to-6", allow_abbrev=False)
    _add_receipt(syzygy)
    syzygy.add_argument("--atomic-build-root", type=Path, required=True)
    syzygy.add_argument("--candidate-bmi2", type=Path, required=True)
    syzygy.add_argument("--legacy-net", type=Path, required=True)
    syzygy.add_argument("--syzygy-driver", type=Path, required=True)
    syzygy.add_argument("--syzygy-combined", type=Path, required=True)
    syzygy.add_argument("--syzygy-inventory", type=Path, required=True)
    syzygy.add_argument("--kppppvk-wdl", type=Path, required=True)
    syzygy.add_argument("--kppppvk-dtz", type=Path, required=True)

    h7 = subparsers.add_parser("atomic-bin-v2-strong-local", allow_abbrev=False)
    _add_receipt(h7)
    _add_pipeline_roots(h7)
    h7.add_argument("--pipeline-python", type=Path, required=True)
    h7.add_argument("--atomic-data-generator", type=Path, required=True)
    h7.add_argument("--atomic-pipeline-engine", type=Path, required=True)
    h7.add_argument("--legacy-net", type=Path, required=True)
    h7.add_argument("--data-tools", type=Path, required=True)
    h7.add_argument("--wrapper-data-tools", type=Path, required=True)
    h7.add_argument("--trainer-loader", type=Path, required=True)
    h7.add_argument("--gate-workspace", type=Path, required=True)
    return parser


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    effective = list(sys.argv[1:] if argv is None else argv)
    if effective:
        _reject_duplicate_options(effective)
    return build_parser().parse_args(effective)


def run(arguments: argparse.Namespace, environment: Mapping[str, str] | None = None) -> bytes:
    env = os.environ if environment is None else environment
    context = _authenticate_release_context(str(arguments.gate), env)
    receipt = _safe_receipt_destination(context, str(arguments.receipt))
    paths = _validate_and_bind_arguments(arguments, context, env)
    prepared: PreparedGate | None = None
    primary_error: BaseException | None = None
    try:
        prepared = _prepare_gate(paths, context)
        output = _execute_command(
            prepared.command,
            timeout_seconds=prepared.timeout_seconds,
            environment=_minimal_environment(env),
            expected_launcher=prepared.launcher,
        )
        passed = prepared.validate(output)
    except BaseException as error:
        primary_error = error
        raise
    finally:
        if prepared is not None and prepared.private_root is not None:
            try:
                _remove_private_tree(prepared.private_root)
            except BaseException:
                if primary_error is None:
                    raise
    post_context = _authenticate_release_context(str(arguments.gate), env)
    if post_context != context:
        raise GateError("release identity changed during the exact gate")
    post_paths = _validate_and_bind_arguments(arguments, post_context, env)
    if post_paths != paths:
        raise GateError("authenticated gate paths changed during execution")
    payload = _write_receipt(receipt, context.gate, passed)
    print(
        f"EXACT TAG GATE PASS gate={context.gate} verified-checks={passed} "
        f"receipt={arguments.receipt}",
        flush=True,
    )
    return payload


def main(argv: Sequence[str] | None = None) -> int:
    try:
        arguments = parse_args(argv)
        run(arguments)
    except GateError as error:
        print(f"exact-tag gate orchestrator error: {error}", file=sys.stderr, flush=True)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
