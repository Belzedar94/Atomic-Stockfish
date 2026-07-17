#!/usr/bin/env python3
"""Run and verify the fail-closed external gates for the exact v1.0.3 tag.

The executable plan is a canonical JSON file tracked by the exact annotated
tag.  The command line supplies paths only; it cannot supply commands.  Every
gate is a tracked Python entry point, receives a deliberately small environment
and must produce an authenticated receipt proving a real pass with zero skips.
The evidence bundle is bounded, sealed, re-hashed and independently verified
before the canonical manifest is created.
"""

from __future__ import annotations

import argparse
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import hashlib
import importlib.util
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import subprocess
import sys
import threading
import time
from typing import Any, BinaryIO, Dict, Iterable, Mapping, Optional, Sequence, Tuple

try:
    from jsonschema import Draft202012Validator  # type: ignore[import-untyped]
    from jsonschema.exceptions import (  # type: ignore[import-untyped]
        SchemaError,
        ValidationError,
    )
except ImportError as error:  # pragma: no cover - exact-tag environment is locked
    raise RuntimeError("jsonschema is required by the exact-tag verifier") from error


def _load_containment_helper() -> Any:
    """Load only the sibling helper, independent of import search paths."""

    controller = Path(__file__).resolve(strict=True)
    expected = controller.with_name("atomic_process_containment.py")
    try:
        helper = expected.resolve(strict=True)
    except OSError as error:
        raise RuntimeError("exact-tag containment helper is absent") from error
    if helper != expected or not helper.is_file():
        raise RuntimeError("exact-tag containment helper is not a real sibling file")
    spec = importlib.util.spec_from_file_location(
        "_atomic_exact_tag_process_containment", helper
    )
    if spec is None or spec.loader is None or spec.origin is None:
        raise RuntimeError("could not create the exact-tag containment helper loader")
    if Path(spec.origin).resolve(strict=True) != helper:
        raise RuntimeError("exact-tag containment helper loader origin mismatch")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    origin = Path(str(getattr(module, "__file__", ""))).resolve(strict=True)
    if origin != helper:
        raise RuntimeError("loaded exact-tag containment helper origin mismatch")
    return module


_CONTAINMENT = _load_containment_helper()
ProcessContainmentError = _CONTAINMENT.ProcessContainmentError
launch_contained = _CONTAINMENT.launch_contained


SCHEMA_VERSION = 1
COMMAND_PLAN_VERSION = 1
PROJECT = "Atomic-Stockfish"
REPOSITORY = "Belzedar94/Atomic-Stockfish"
RELEASE_VERSION = "1.0.3"
RELEASE_TAG = "v1.0.3"
RELEASE_EVENT = "push"
RELEASE_REF = "refs/tags/v1.0.3"
SCHEMA_PATH = "schemas/atomic-release-exact-tag-gates-v1.json"
BENCH_GATE = "bmi2-vs-fairy"
BENCH_ARTIFACT = "candidate_bmi2"
BENCH_EVIDENCE_PLACEHOLDER = "@benchmark-evidence@"
PIPELINE_PYTHON_ARTIFACT = "pipeline_python"
PIPELINE_PYTHON_PLACEHOLDER = "@artifact:pipeline_python@"
PIPELINE_PYTHON_GATES = frozenset(
    {"legacy-v1-strong-local", "hito5-release", "atomic-bin-v2-strong-local"}
)
REQUIRED_GATES = (
    "hito4-release",
    "legacy-v1-strong-local",
    "hito5-release",
    "syzygy-real-3-to-6",
    "atomic-bin-v2-strong-local",
    BENCH_GATE,
)
GATE_TIMEOUT_SECONDS = {
    "hito4-release": 10_800,
    "legacy-v1-strong-local": 7_200,
    "hito5-release": 32_400,
    "syzygy-real-3-to-6": 1_800,
    "atomic-bin-v2-strong-local": 10_800,
    BENCH_GATE: 3_600,
}
GATE_TIMEOUT_BUDGET_SECONDS = 66_600
if (
    tuple(GATE_TIMEOUT_SECONDS) != REQUIRED_GATES
    or sum(GATE_TIMEOUT_SECONDS.values()) != GATE_TIMEOUT_BUDGET_SECONDS
):  # pragma: no cover - import-time release-contract invariant
    raise RuntimeError("exact-tag gate timeout budget is internally inconsistent")
FROZEN_EXTERNAL_SHA256 = {
    "legacy_net": "99dc67eabf26a64faeeca3a88b4c38597a840b8d4a874b9f2cf658c6f92a04a6",
    "fairy_h5_oracle": "1ae6d680f03128c8404f31a3f264f28b132b557ed3a91a6445ec563a7a33f623",
    "syzygy_inventory": "3d4b7fd0ab387f4f60da2078f612c9e8890e6026f551aebe8631efc157788f23",
    "kppppvk_wdl": "897a15846a4b027cbd0a31e425fdf0690f68c0bd4e62105cca2055678e2910f9",
    "kppppvk_dtz": "e740168d8cbb0bf662863f278ef470c5d7eb395ece0c8bf80fed004a47991bc6",
    "fairy_bmi2_baseline": "4eacaab40dca84f5a255ea57231f2795d43b5dda85ce50ebba1a1b2937b46331",
}
FROZEN_AUX_REPOSITORY_COMMITS = {
    "tools": "450049ee7a0ece32694b11f6c55deb7df1d42a84",
    "trainer": "3a19c16fc3d477b1ee7602ccc6510736bc7604cc",
    "fairy": "fb78cb561aa01708338e35b3dc3b65a42149a3c4",
}

BENCH_CORPUS_SHA256 = (
    "2738065a8a70d61da46fa3c75f95d645e50e601b43792df0e7b3cc97b1d891a1"
)
BENCH_POSITIONS = 13
BENCH_NODES_PER_FEN = 100_000
BENCH_THREADS = 1
BENCH_HASH_MB = 64
BENCH_SEARCH_TIMEOUT_SECONDS = 60
BENCH_WARMUPS = 1
BENCH_REPETITIONS = 5

SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
SHA1_RE = re.compile(r"^[0-9a-f]{40}$")
RUN_ID_RE = re.compile(r"^[1-9][0-9]*$")
NAME_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
DECIMAL6_RE = re.compile(r"^[1-9][0-9]*\.[0-9]{6}$")

MAX_GIT_OUTPUT_BYTES = 4 * 1024 * 1024
GIT_TIMEOUT_SECONDS = 60
MAX_JSON_BYTES = 4 * 1024 * 1024
MAX_SCHEMA_BYTES = 512 * 1024
MAX_PLAN_BYTES = 512 * 1024
MAX_COMMAND_ARGUMENTS = 128
MAX_COMMAND_CHARACTERS = 32 * 1024
MAX_GATE_OUTPUT_BYTES = 32 * 1024 * 1024
OUTPUT_READ_BYTES = 64 * 1024
PROCESS_POLL_SECONDS = 0.05
MAX_EVIDENCE_FILES = 256
MAX_EVIDENCE_DIRECTORIES = 256
MAX_EVIDENCE_DEPTH = 12
MAX_EVIDENCE_PATH_LENGTH = 240
MAX_EVIDENCE_FILE_BYTES = 512 * 1024 * 1024
MAX_EVIDENCE_TOTAL_BYTES = 2 * 1024 * 1024 * 1024
MAX_ARTIFACT_BYTES = 4 * 1024 * 1024 * 1024
MAX_EXTERNAL_BYTES = 16 * 1024 * 1024 * 1024
MAX_SUBMODULES = 64
MAX_SUBMODULE_DEPTH = 8

SAFE_INHERITED_ENVIRONMENT = frozenset(
    {
        "comspec",
        "lang",
        "lc_all",
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
        "windir",
    }
)

# Networks, executables, tables, credentials and archives stay outside the
# uploaded evidence.  Only their authenticated metadata belongs in the manifest.
FORBIDDEN_EVIDENCE_SUFFIXES = frozenset(
    {
        ".7z",
        ".bin",
        ".bz2",
        ".credentials",
        ".db",
        ".dll",
        ".dylib",
        ".env",
        ".exe",
        ".gz",
        ".key",
        ".keystore",
        ".nnue",
        ".p12",
        ".pdb",
        ".pem",
        ".pfx",
        ".rtbw",
        ".rtbz",
        ".secret",
        ".so",
        ".sqlite",
        ".sqlite3",
        ".tar",
        ".tgz",
        ".token",
        ".whl",
        ".xz",
        ".zip",
    }
)
FORBIDDEN_EVIDENCE_BASENAMES = frozenset(
    {
        ".env",
        ".git-credentials",
        ".netrc",
        ".npmrc",
        ".pypirc",
        "credentials.json",
        "id_dsa",
        "id_ecdsa",
        "id_ed25519",
        "id_rsa",
        "secrets.json",
    }
)


class ExactTagGateError(RuntimeError):
    """The exact-tag external gate contract was not proven."""


def canonical_json(value: Any) -> bytes:
    """Return the only accepted UTF-8 serialization for signed JSON values."""

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


def _reject_duplicate_keys(pairs: Sequence[Tuple[str, Any]]) -> Dict[str, Any]:
    value: Dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ExactTagGateError("duplicate JSON key: " + key)
        value[key] = item
    return value


def _reject_nonfinite_json(token: str) -> None:
    raise ExactTagGateError("non-finite JSON number is forbidden: " + token)


def _load_json_bytes(payload: bytes, context: str, maximum: int) -> Any:
    if len(payload) > maximum:
        raise ExactTagGateError(f"{context} exceeds its {maximum}-byte limit")
    try:
        return json.loads(
            payload.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_nonfinite_json,
        )
    except ExactTagGateError:
        raise
    except (UnicodeError, json.JSONDecodeError) as error:
        raise ExactTagGateError(f"invalid {context} JSON: {error}") from error


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _has_reparse_attribute(metadata: os.stat_result) -> bool:
    attributes = getattr(metadata, "st_file_attributes", 0)
    marker = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    return bool(attributes & marker)


def _assert_real_directory(path: Path, context: str) -> Path:
    try:
        metadata = path.lstat()
    except OSError as error:
        raise ExactTagGateError(f"{context} is not readable: {path}") from error
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
        or _has_reparse_attribute(metadata)
    ):
        raise ExactTagGateError(
            f"{context} must be a real non-reparse directory: {path}"
        )
    try:
        return path.resolve(strict=True)
    except OSError as error:
        raise ExactTagGateError(f"{context} cannot be resolved: {path}") from error


def _regular_file_digest(
    path: Path,
    context: str,
    *,
    maximum: int,
    allow_empty: bool = False,
) -> Tuple[int, str]:
    """Hash one stable regular file while detecting links and replacement races."""

    try:
        before = path.lstat()
    except OSError as error:
        raise ExactTagGateError(f"{context} is not readable: {path}") from error
    if (
        not stat.S_ISREG(before.st_mode)
        or stat.S_ISLNK(before.st_mode)
        or _has_reparse_attribute(before)
        or before.st_nlink != 1
    ):
        raise ExactTagGateError(
            f"{context} must be a single-link regular non-reparse file: {path}"
        )
    if before.st_size > maximum:
        raise ExactTagGateError(f"{context} exceeds its {maximum}-byte limit")
    if before.st_size == 0 and not allow_empty:
        raise ExactTagGateError(f"{context} is empty")

    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(str(path), flags)
    except OSError as error:
        raise ExactTagGateError(f"could not open {context}: {path}") from error
    digest = hashlib.sha256()
    try:
        opened = os.fstat(descriptor)
        if (
            not stat.S_ISREG(opened.st_mode)
            or _has_reparse_attribute(opened)
            or opened.st_nlink != 1
            or (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino)
            or opened.st_size != before.st_size
        ):
            raise ExactTagGateError(f"{context} changed before hashing: {path}")
        total = 0
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            total += len(chunk)
            if total > maximum:
                raise ExactTagGateError(f"{context} grew beyond its byte limit")
            digest.update(chunk)
    finally:
        os.close(descriptor)
    try:
        after = path.lstat()
    except OSError as error:
        raise ExactTagGateError(f"{context} disappeared while hashing: {path}") from error
    identity_before = (
        before.st_dev,
        before.st_ino,
        before.st_size,
        before.st_mtime_ns,
        before.st_nlink,
    )
    identity_after = (
        after.st_dev,
        after.st_ino,
        after.st_size,
        after.st_mtime_ns,
        after.st_nlink,
    )
    if (
        identity_before != identity_after
        or stat.S_ISLNK(after.st_mode)
        or _has_reparse_attribute(after)
    ):
        raise ExactTagGateError(f"{context} changed while hashing: {path}")
    return before.st_size, digest.hexdigest()


def _safe_process_environment() -> Dict[str, str]:
    environment: Dict[str, str] = {}
    for key, value in os.environ.items():
        if key.casefold() in SAFE_INHERITED_ENVIRONMENT:
            environment[key] = value
    environment.update(
        {
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_PAGER": "cat",
            "GIT_TERMINAL_PROMPT": "0",
            "PAGER": "cat",
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONHASHSEED": "0",
            "PYTHONNOUSERSITE": "1",
        }
    )
    return environment


def _run_bounded_command(
    command: Sequence[str], repo_root: Path, *, timeout: int, context: str
) -> bytes:
    try:
        result = subprocess.run(
            list(command),
            cwd=repo_root,
            env=_safe_process_environment(),
            check=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as error:
        raise ExactTagGateError(f"{context} timed out after {timeout}s") from error
    except OSError as error:
        raise ExactTagGateError(f"could not execute {context}") from error
    if len(result.stdout) + len(result.stderr) > MAX_GIT_OUTPUT_BYTES:
        raise ExactTagGateError(f"{context} output exceeded its limit")
    if result.returncode:
        detail_bytes = result.stderr or result.stdout or b"command failed"
        detail = detail_bytes[:4096].decode("utf-8", errors="replace").strip()
        raise ExactTagGateError(f"{context}: {detail}")
    return result.stdout


def _git_bytes(repo_root: Path, *arguments: str) -> bytes:
    command = [
        "git",
        "-c",
        "core.quotePath=false",
        "-C",
        str(repo_root),
        *arguments,
    ]
    return _run_bounded_command(
        command,
        repo_root,
        timeout=GIT_TIMEOUT_SECONDS,
        context="git " + " ".join(arguments),
    )


def _git(repo_root: Path, *arguments: str) -> str:
    try:
        return _git_bytes(repo_root, *arguments).decode("utf-8").strip()
    except UnicodeError as error:
        raise ExactTagGateError("git produced non-UTF-8 output") from error


def _same_path(left: Path, right: Path) -> bool:
    return os.path.normcase(str(left.resolve(strict=True))) == os.path.normcase(
        str(right.resolve(strict=True))
    )


def _safe_repo_relative(raw: str, context: str) -> str:
    if not raw or "\\" in raw or "\x00" in raw or len(raw) > MAX_EVIDENCE_PATH_LENGTH:
        raise ExactTagGateError(f"unsafe {context} path: {raw!r}")
    path = PurePosixPath(raw)
    if path.is_absolute() or path.as_posix() != raw or any(
        part in ("", ".", "..") for part in path.parts
    ):
        raise ExactTagGateError(f"unsafe {context} path: {raw!r}")
    if ":" in path.parts[0]:
        raise ExactTagGateError(f"unsafe {context} path: {raw!r}")
    return path.as_posix()


def _parse_gitlinks(repo_root: Path) -> list[Tuple[str, str]]:
    payload = _git_bytes(repo_root, "ls-files", "--stage", "-z")
    records: list[Tuple[str, str]] = []
    for raw in payload.split(b"\x00"):
        if not raw:
            continue
        try:
            metadata, raw_path = raw.split(b"\t", 1)
            mode, object_id, stage = metadata.decode("ascii").split(" ")
            path = raw_path.decode("utf-8")
        except (UnicodeError, ValueError) as error:
            raise ExactTagGateError("could not parse git index entry") from error
        if mode != "160000":
            continue
        if stage != "0" or not SHA1_RE.fullmatch(object_id):
            raise ExactTagGateError("submodule gitlink is not a canonical stage-0 SHA-1")
        records.append((_safe_repo_relative(path, "submodule"), object_id))
    if len(records) > MAX_SUBMODULES:
        raise ExactTagGateError("repository has too many submodules")
    return records


def _validate_repo_tree(
    repo_root: Path,
    expected_commit: str,
    *,
    prefix: str = "",
    depth: int = 0,
) -> Tuple[str, list[Dict[str, str]]]:
    if depth > MAX_SUBMODULE_DEPTH:
        raise ExactTagGateError("submodule recursion exceeds its depth limit")
    root = _assert_real_directory(repo_root, "repository")
    if not _same_path(Path(_git(root, "rev-parse", "--show-toplevel")), root):
        raise ExactTagGateError(f"repository root mismatch: {root}")
    if _git(root, "rev-parse", "--verify", "HEAD") != expected_commit:
        raise ExactTagGateError(f"repository HEAD mismatch: {root}")
    expected_tree = _git(root, "rev-parse", "HEAD^{tree}")
    if not SHA1_RE.fullmatch(expected_tree):
        raise ExactTagGateError("repository tree is not a full SHA-1")
    if _git(root, "write-tree") != expected_tree:
        raise ExactTagGateError("repository index tree differs from HEAD")
    # Force Git to hash racy-clean entries before asking for porcelain state.
    # This avoids accepting stat-only assumptions and also makes a real tracked
    # modification fail before the manifest can be considered.
    try:
        _git(root, "update-index", "--refresh", "--ignore-submodules")
    except ExactTagGateError as error:
        raise ExactTagGateError("repository worktree is not exact and clean") from error
    status_output = _git(
        root,
        "status",
        "--porcelain=v1",
        "--untracked-files=all",
        "--ignore-submodules=none",
    )
    if status_output:
        raise ExactTagGateError("repository worktree is not exact and clean")
    _git(root, "diff", "--no-ext-diff", "--quiet", "--ignore-submodules=none")
    _git(
        root,
        "diff",
        "--cached",
        "--no-ext-diff",
        "--quiet",
        "--ignore-submodules=none",
    )

    submodules: list[Dict[str, str]] = []
    for relative, commit in _parse_gitlinks(root):
        child = root.joinpath(*PurePosixPath(relative).parts)
        child_root = _assert_real_directory(child, f"submodule {relative}")
        try:
            child_root.relative_to(root)
        except ValueError as error:
            raise ExactTagGateError(f"submodule escapes repository: {relative}") from error
        child_prefix = f"{prefix}/{relative}" if prefix else relative
        tree, nested = _validate_repo_tree(
            child_root,
            commit,
            prefix=child_prefix,
            depth=depth + 1,
        )
        submodules.append({"path": child_prefix, "commit": commit, "tree": tree})
        submodules.extend(nested)
    return expected_tree, submodules


def validate_identity(
    repo_root: Path,
    *,
    repository: str,
    version: str,
    event: str,
    ref: str,
    commit: str,
    tag_object: str,
    run_id: str,
    run_attempt: int,
) -> Dict[str, Any]:
    expected = {
        "repository": REPOSITORY,
        "version": RELEASE_VERSION,
        "event": RELEASE_EVENT,
        "ref": RELEASE_REF,
    }
    actual = {
        "repository": repository,
        "version": version,
        "event": event,
        "ref": ref,
    }
    for key, wanted in expected.items():
        if actual[key] != wanted:
            raise ExactTagGateError(
                f"release identity {key} mismatch: {actual[key]!r} != {wanted!r}"
            )
    if not SHA1_RE.fullmatch(commit):
        raise ExactTagGateError("commit must be one full lowercase SHA-1")
    if not SHA1_RE.fullmatch(tag_object):
        raise ExactTagGateError("tag object must be one full lowercase SHA-1")
    if tag_object == commit:
        raise ExactTagGateError("annotated tag object must differ from its peeled commit")
    if not RUN_ID_RE.fullmatch(run_id):
        raise ExactTagGateError("run id must be a positive decimal string")
    if not _is_int(run_attempt) or run_attempt < 1:
        raise ExactTagGateError("run attempt must be a positive integer")

    root = _assert_real_directory(repo_root, "repo root")
    tree, submodules = _validate_repo_tree(root, commit)
    tag_ref = f"refs/tags/{RELEASE_TAG}"
    if _git(root, "rev-parse", f"{tag_ref}^{{tag}}") != tag_object:
        raise ExactTagGateError("annotated tag object does not equal --tag-object")
    if _git(root, "cat-file", "-t", tag_object) != "tag":
        raise ExactTagGateError("--tag-object is not an annotated Git tag object")
    if _git(root, "rev-parse", f"{tag_ref}^{{}}") != commit:
        raise ExactTagGateError("annotated tag does not peel directly to release commit")
    return {
        "repository": repository,
        "version": version,
        "tag": RELEASE_TAG,
        "event": event,
        "ref": ref,
        "commit": commit,
        "tree": tree,
        "submodules": submodules,
        "tagObject": tag_object,
        "runId": run_id,
        "runAttempt": run_attempt,
    }


def _parse_name_path_specs(
    specifications: Sequence[str], expected_names: Iterable[str], label: str
) -> Dict[str, Path]:
    expected = set(expected_names)
    parsed: Dict[str, Path] = {}
    for specification in specifications:
        if "=" not in specification:
            raise ExactTagGateError(f"{label} must use NAME=PATH: {specification!r}")
        name, raw_path = specification.split("=", 1)
        if name not in expected:
            raise ExactTagGateError(f"unknown {label} name: {name!r}")
        if name in parsed:
            raise ExactTagGateError(f"duplicate {label} name: {name}")
        if not raw_path or "\x00" in raw_path:
            raise ExactTagGateError(f"empty {label} path for {name}")
        parsed[name] = Path(raw_path)
    missing = sorted(expected - set(parsed))
    if missing:
        raise ExactTagGateError(f"missing {label} entries: {missing}")
    return parsed


def _file_record(name: str, path: Path, context: str, maximum: int) -> Dict[str, Any]:
    size, digest = _regular_file_digest(path, context, maximum=maximum)
    return {"name": name, "fileName": path.name, "bytes": size, "sha256": digest}


def authenticate_external_inputs(paths: Mapping[str, Path]) -> list[Dict[str, Any]]:
    if set(paths) != set(FROZEN_EXTERNAL_SHA256):
        raise ExactTagGateError("external input names differ from frozen contract")
    records = []
    for name, expected in FROZEN_EXTERNAL_SHA256.items():
        record = _file_record(
            name,
            paths[name],
            f"external input {name}",
            MAX_EXTERNAL_BYTES,
        )
        if record["sha256"] != expected:
            raise ExactTagGateError(
                f"external input {name} SHA-256 mismatch: "
                f"{record['sha256']} != {expected}"
            )
        records.append(record)
    return records


def authenticate_artifacts(
    paths: Mapping[str, Path], expected_names: Iterable[str]
) -> list[Dict[str, Any]]:
    expected = tuple(expected_names)
    if set(paths) != set(expected):
        raise ExactTagGateError("artifact names differ from command plan")
    return [
        _file_record(name, paths[name], f"release artifact {name}", MAX_ARTIFACT_BYTES)
        for name in expected
    ]


def authenticate_aux_repositories(
    paths: Mapping[str, Path]
) -> list[Dict[str, Any]]:
    if set(paths) != set(FROZEN_AUX_REPOSITORY_COMMITS):
        raise ExactTagGateError("auxiliary repository names differ from frozen contract")
    records: list[Dict[str, Any]] = []
    for name, commit in FROZEN_AUX_REPOSITORY_COMMITS.items():
        root = _assert_real_directory(paths[name], f"auxiliary repository {name}")
        tree, submodules = _validate_repo_tree(root, commit)
        records.append(
            {
                "name": name,
                "commit": commit,
                "tree": tree,
                "submodules": submodules,
            }
        )
    return records


def authenticate_directories(
    paths: Mapping[str, Path], expected_names: Iterable[str]
) -> list[Dict[str, str]]:
    expected = tuple(expected_names)
    if set(paths) != set(expected):
        raise ExactTagGateError("directory binding names differ from command plan")
    records = []
    for name in expected:
        root = _assert_real_directory(paths[name], f"directory binding {name}")
        records.append(
            {
                "name": name,
                "baseName": root.name,
                "pathSha256": hashlib.sha256(str(root).encode("utf-8")).hexdigest(),
            }
        )
    return records


def _tracked_file_record(
    repo_root: Path, relative: str, context: str, maximum: int
) -> Tuple[bytes, Dict[str, Any]]:
    safe = _safe_repo_relative(relative, context)
    path = repo_root.joinpath(*PurePosixPath(safe).parts)
    try:
        resolved = path.resolve(strict=True)
        resolved.relative_to(repo_root.resolve(strict=True))
    except (OSError, ValueError) as error:
        raise ExactTagGateError(f"{context} escapes repository: {safe}") from error
    size, digest = _regular_file_digest(path, context, maximum=maximum)
    stage = _git(repo_root, "ls-files", "--stage", "--", safe)
    match = re.fullmatch(r"(100644|100755) ([0-9a-f]{40}) 0\t(.+)", stage)
    if match is None or match.group(3) != safe:
        raise ExactTagGateError(f"{context} is not one stage-0 tracked regular file")
    blob = match.group(2)
    committed = _git_bytes(repo_root, "cat-file", "blob", f"HEAD:{safe}")
    try:
        raw = path.read_bytes()
    except OSError as error:
        raise ExactTagGateError(f"could not read {context}: {safe}") from error
    if raw != committed or len(raw) != size or hashlib.sha256(raw).hexdigest() != digest:
        raise ExactTagGateError(f"{context} bytes differ from exact HEAD")
    return raw, {
        "path": safe,
        "bytes": size,
        "sha256": digest,
        "gitBlob": blob,
    }


def _safe_evidence_relative(raw: str) -> str:
    relative = _safe_repo_relative(raw, "evidence")
    path = PurePosixPath(relative)
    if len(path.parts) > MAX_EVIDENCE_DEPTH:
        raise ExactTagGateError(f"evidence path exceeds depth limit: {raw}")
    lowered_name = path.name.casefold()
    if lowered_name in FORBIDDEN_EVIDENCE_BASENAMES or lowered_name.startswith(
        ".env."
    ):
        raise ExactTagGateError(f"sensitive evidence filename is forbidden: {raw}")
    suffixes = {suffix.casefold() for suffix in path.suffixes}
    forbidden = sorted(suffixes & FORBIDDEN_EVIDENCE_SUFFIXES)
    if forbidden:
        raise ExactTagGateError(
            f"sensitive evidence extension is forbidden for {raw}: {forbidden}"
        )
    return relative


def _expect_exact_keys(value: Any, keys: set[str], context: str) -> Dict[str, Any]:
    if not isinstance(value, dict) or set(value) != keys:
        raise ExactTagGateError(f"{context} has unexpected keys")
    return value


def _validate_name_list(value: Any, context: str) -> Tuple[str, ...]:
    if not isinstance(value, list) or not value:
        raise ExactTagGateError(f"{context} must be a non-empty list")
    names: list[str] = []
    for item in value:
        if not isinstance(item, str) or not NAME_RE.fullmatch(item):
            raise ExactTagGateError(f"{context} contains an invalid name")
        if item in names:
            raise ExactTagGateError(f"{context} contains duplicate {item}")
        names.append(item)
    return tuple(names)


def _validate_command_template(
    repo_root: Path,
    gate_id: str,
    argv: Any,
    artifact_names: Iterable[str],
    directory_names: Iterable[str],
) -> Tuple[str, ...]:
    if not isinstance(argv, list) or not (2 <= len(argv) <= MAX_COMMAND_ARGUMENTS):
        raise ExactTagGateError(f"{gate_id} argv has invalid length")
    if any(
        not isinstance(argument, str)
        or not argument
        or "\x00" in argument
        or "\r" in argument
        or "\n" in argument
        for argument in argv
    ):
        raise ExactTagGateError(f"{gate_id} argv contains an invalid argument")
    command = tuple(argv)
    if sum(len(argument) for argument in command) > MAX_COMMAND_CHARACTERS:
        raise ExactTagGateError(f"{gate_id} argv exceeds character limit")
    if command[0] != "@python@":
        raise ExactTagGateError(f"{gate_id} must use the authenticated Python launcher")
    script = _safe_repo_relative(command[1], f"{gate_id} script")
    if not script.endswith(".py"):
        raise ExactTagGateError(f"{gate_id} entry point must be a tracked .py file")
    _tracked_file_record(repo_root, script, f"{gate_id} script", MAX_PLAN_BYTES)

    allowed = {
        "@python@",
        "@repo@",
        "@evidence@",
        *{"@external:" + name + "@" for name in FROZEN_EXTERNAL_SHA256},
        *{"@artifact:" + name + "@" for name in artifact_names},
        *{"@directory:" + name + "@" for name in directory_names},
        *{"@repository:" + name + "@" for name in FROZEN_AUX_REPOSITORY_COMMITS},
    }
    if gate_id == BENCH_GATE:
        allowed.add(BENCH_EVIDENCE_PLACEHOLDER)
    for argument in command[2:]:
        if "@" in argument and argument not in allowed:
            raise ExactTagGateError(
                f"{gate_id} contains an unknown or embedded placeholder: {argument}"
            )
    benchmark_placeholders = command.count(BENCH_EVIDENCE_PLACEHOLDER)
    if gate_id == BENCH_GATE and benchmark_placeholders != 1:
        raise ExactTagGateError(
            f"{BENCH_GATE} must contain exactly one {BENCH_EVIDENCE_PLACEHOLDER}"
        )
    pipeline_python_placeholders = command.count(PIPELINE_PYTHON_PLACEHOLDER)
    expected_pipeline_python = 1 if gate_id in PIPELINE_PYTHON_GATES else 0
    if pipeline_python_placeholders != expected_pipeline_python:
        raise ExactTagGateError(
            f"{gate_id} must contain exactly {expected_pipeline_python} "
            f"{PIPELINE_PYTHON_PLACEHOLDER} binding"
        )
    return command


def load_command_plan(
    repo_root: Path, plan_path: str
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    raw, record = _tracked_file_record(
        repo_root, plan_path, "exact-tag command plan", MAX_PLAN_BYTES
    )
    value = _load_json_bytes(raw, "exact-tag command plan", MAX_PLAN_BYTES)
    if raw != canonical_json(value):
        raise ExactTagGateError("exact-tag command plan is not canonical JSON")
    plan = _expect_exact_keys(
        value,
        {
            "schemaVersion",
            "project",
            "releaseTag",
            "artifacts",
            "directories",
            "gates",
            "benchmarkEvidence",
        },
        "command plan",
    )
    if (
        plan["schemaVersion"] != COMMAND_PLAN_VERSION
        or plan["project"] != PROJECT
        or plan["releaseTag"] != RELEASE_TAG
    ):
        raise ExactTagGateError("command plan identity mismatch")
    artifact_names = _validate_name_list(plan["artifacts"], "command plan artifacts")
    if BENCH_ARTIFACT not in artifact_names:
        raise ExactTagGateError(f"command plan must declare {BENCH_ARTIFACT}")
    if PIPELINE_PYTHON_ARTIFACT not in artifact_names:
        raise ExactTagGateError(
            f"command plan must declare {PIPELINE_PYTHON_ARTIFACT}"
        )
    directories_raw = plan["directories"]
    if not isinstance(directories_raw, list):
        raise ExactTagGateError("command plan directories must be a list")
    directory_names: Tuple[str, ...]
    if directories_raw:
        directory_names = _validate_name_list(
            directories_raw, "command plan directories"
        )
    else:
        directory_names = ()
    benchmark_evidence = _safe_evidence_relative(plan["benchmarkEvidence"])
    if not benchmark_evidence.endswith(".json"):
        raise ExactTagGateError("benchmark evidence must be JSON")

    gates = plan["gates"]
    if not isinstance(gates, list) or len(gates) != len(REQUIRED_GATES):
        raise ExactTagGateError("command plan must contain exactly six gates")
    seen_paths: set[str] = set()
    for raw_gate, expected_id in zip(gates, REQUIRED_GATES):
        gate = _expect_exact_keys(
            raw_gate,
            {"id", "timeoutSeconds", "argv", "evidence", "receiptEvidence"},
            f"command plan gate {expected_id}",
        )
        if gate["id"] != expected_id:
            raise ExactTagGateError(f"command plan gate order/ID mismatch: {expected_id}")
        if (
            not _is_int(gate["timeoutSeconds"])
            or gate["timeoutSeconds"] != GATE_TIMEOUT_SECONDS[expected_id]
        ):
            raise ExactTagGateError(
                f"{expected_id} timeout must be exactly "
                f"{GATE_TIMEOUT_SECONDS[expected_id]} seconds"
            )
        _validate_command_template(
            repo_root,
            expected_id,
            gate["argv"],
            artifact_names,
            directory_names,
        )
        evidence = gate["evidence"]
        if not isinstance(evidence, list) or not (1 <= len(evidence) <= 32):
            raise ExactTagGateError(f"{expected_id} evidence list is invalid")
        canonical_paths = []
        for raw_path in evidence:
            if not isinstance(raw_path, str):
                raise ExactTagGateError(f"{expected_id} evidence path is invalid")
            relative = _safe_evidence_relative(raw_path)
            folded = relative.casefold()
            if folded in seen_paths:
                raise ExactTagGateError(f"duplicate command-plan evidence: {relative}")
            seen_paths.add(folded)
            canonical_paths.append(relative)
        if canonical_paths != sorted(canonical_paths):
            raise ExactTagGateError(f"{expected_id} evidence must be sorted")
        receipt = _safe_evidence_relative(gate["receiptEvidence"])
        if receipt not in canonical_paths:
            raise ExactTagGateError(f"{expected_id} receipt is not declared evidence")
        if not receipt.endswith(".json"):
            raise ExactTagGateError(f"{expected_id} receipt must be JSON")
        if expected_id == BENCH_GATE:
            if receipt != benchmark_evidence:
                raise ExactTagGateError("BMI2 receipt must be benchmark evidence")
        elif receipt == benchmark_evidence:
            raise ExactTagGateError("only BMI2 may use benchmark evidence")
    return plan, record


def _expand_command(
    template: Sequence[str],
    *,
    repo_root: Path,
    evidence_root: Path,
    benchmark_evidence: str,
    external_paths: Mapping[str, Path],
    artifact_paths: Mapping[str, Path],
    directory_paths: Mapping[str, Path],
    repository_paths: Mapping[str, Path],
) -> Tuple[str, ...]:
    safe_benchmark_evidence = _safe_evidence_relative(benchmark_evidence)
    evidence_base = evidence_root.resolve(strict=True)
    benchmark_path = evidence_base.joinpath(
        *PurePosixPath(safe_benchmark_evidence).parts
    )
    try:
        benchmark_path.resolve(strict=False).relative_to(evidence_base)
    except ValueError as error:
        raise ExactTagGateError("benchmark evidence escapes its root") from error
    bindings = {
        "@python@": str(Path(sys.executable).resolve(strict=True)),
        "@repo@": str(repo_root.resolve(strict=True)),
        "@evidence@": str(evidence_base),
        BENCH_EVIDENCE_PLACEHOLDER: str(benchmark_path),
    }
    bindings.update(
        {"@external:" + name + "@": str(path.resolve(strict=True)) for name, path in external_paths.items()}
    )
    bindings.update(
        {"@artifact:" + name + "@": str(path.resolve(strict=True)) for name, path in artifact_paths.items()}
    )
    bindings.update(
        {"@directory:" + name + "@": str(path.resolve(strict=True)) for name, path in directory_paths.items()}
    )
    bindings.update(
        {"@repository:" + name + "@": str(path.resolve(strict=True)) for name, path in repository_paths.items()}
    )
    resolved = tuple(bindings.get(argument, argument) for argument in template)
    # The authenticated controller runtime is always isolated from cwd, user
    # site packages and every PYTHON* override, including for nested gate
    # controllers declared by the exact-tag plan.
    return (resolved[0], "-I", *resolved[1:])


def _command_record(template: Sequence[str], resolved: Sequence[str], timeout: int) -> Dict[str, Any]:
    return {
        "script": template[1],
        "argumentCount": len(template) - 1,
        "timeoutSeconds": timeout,
        "planArgvSha256": hashlib.sha256(canonical_json(list(template))).hexdigest(),
        "resolvedArgvSha256": hashlib.sha256(canonical_json(list(resolved))).hexdigest(),
    }


def _evidence_path(root: Path, relative: str) -> Path:
    safe = _safe_evidence_relative(relative)
    candidate = root.joinpath(*PurePosixPath(safe).parts)
    try:
        resolved = candidate.resolve(strict=True)
    except OSError as error:
        raise ExactTagGateError(
            f"evidence is missing or unreadable: {relative}"
        ) from error
    try:
        resolved.relative_to(root.resolve(strict=True))
    except ValueError as error:
        raise ExactTagGateError(f"evidence escapes its root: {relative}") from error
    current = root
    for part in PurePosixPath(safe).parts:
        current = current / part
        try:
            metadata = current.lstat()
        except OSError as error:
            raise ExactTagGateError(f"evidence component is unreadable: {relative}") from error
        if stat.S_ISLNK(metadata.st_mode) or _has_reparse_attribute(metadata):
            raise ExactTagGateError(f"evidence contains link/reparse component: {relative}")
    return candidate


def _evidence_record(root: Path, relative: str) -> Dict[str, Any]:
    path = _evidence_path(root, relative)
    size, digest = _regular_file_digest(
        path,
        f"evidence {relative}",
        maximum=MAX_EVIDENCE_FILE_BYTES,
        allow_empty=True,
    )
    return {"path": relative, "bytes": size, "sha256": digest}


def _scan_evidence_tree(root: Path) -> Tuple[set[str], int]:
    root = _assert_real_directory(root, "evidence root")
    files: set[str] = set()
    total = 0
    directories = 0
    stack = [root]
    while stack:
        directory = stack.pop()
        try:
            entries = list(os.scandir(directory))
        except OSError as error:
            raise ExactTagGateError(f"could not scan evidence directory: {directory}") from error
        for entry in entries:
            path = Path(entry.path)
            try:
                # Do not trust the cached DirEntry stat on Windows immediately
                # after a child closes a newly-created file: NTFS can briefly
                # expose a stale link count through that cache.
                metadata = path.lstat()
            except OSError as error:
                raise ExactTagGateError(f"could not stat evidence entry: {path}") from error
            relative = path.relative_to(root).as_posix()
            if entry.is_symlink() or _has_reparse_attribute(metadata):
                raise ExactTagGateError(f"evidence tree contains link/reparse: {relative}")
            if stat.S_ISDIR(metadata.st_mode):
                directories += 1
                if directories > MAX_EVIDENCE_DIRECTORIES:
                    raise ExactTagGateError("evidence tree has too many directories")
                if len(PurePosixPath(relative).parts) > MAX_EVIDENCE_DEPTH:
                    raise ExactTagGateError("evidence directory exceeds depth limit")
                stack.append(path)
            elif stat.S_ISREG(metadata.st_mode):
                if metadata.st_nlink != 1:
                    raise ExactTagGateError(f"evidence file is hard-linked: {relative}")
                safe = _safe_evidence_relative(relative)
                files.add(safe)
                total += metadata.st_size
                if metadata.st_size > MAX_EVIDENCE_FILE_BYTES:
                    raise ExactTagGateError(f"evidence file exceeds limit: {relative}")
                if len(files) > MAX_EVIDENCE_FILES or total > MAX_EVIDENCE_TOTAL_BYTES:
                    raise ExactTagGateError("evidence bundle exceeds size/count limits")
            else:
                raise ExactTagGateError(f"evidence contains a special file: {relative}")
    return files, total


def _validate_evidence_tree(root: Path, expected_paths: Iterable[str]) -> None:
    expected = set(expected_paths)
    actual, _ = _scan_evidence_tree(root)
    if actual != expected:
        raise ExactTagGateError(
            "evidence file set differs from manifest: "
            f"missing={sorted(expected - actual)} extra={sorted(actual - expected)}"
        )


def _seal_evidence_tree(root: Path) -> None:
    root = _assert_real_directory(root, "evidence root")
    directories = [root]
    files: list[Path] = []
    for path in root.rglob("*"):
        metadata = path.lstat()
        if stat.S_ISDIR(metadata.st_mode):
            directories.append(path)
        elif stat.S_ISREG(metadata.st_mode):
            files.append(path)
        else:
            raise ExactTagGateError(f"cannot seal special evidence entry: {path}")
    try:
        for path in files:
            path.chmod(stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH)
        for path in sorted(directories, key=lambda item: len(item.parts), reverse=True):
            path.chmod(
                stat.S_IRUSR
                | stat.S_IXUSR
                | stat.S_IRGRP
                | stat.S_IXGRP
                | stat.S_IROTH
                | stat.S_IXOTH
            )
    except OSError as error:
        raise ExactTagGateError("could not seal evidence tree read-only") from error


def _parse_gate_receipt(path: Path, gate_id: str) -> Dict[str, Any]:
    size, _ = _regular_file_digest(
        path, f"{gate_id} receipt", maximum=MAX_JSON_BYTES
    )
    raw = path.read_bytes()
    if len(raw) != size:
        raise ExactTagGateError(f"{gate_id} receipt changed while reading")
    value = _expect_exact_keys(
        _load_json_bytes(raw, f"{gate_id} receipt", MAX_JSON_BYTES),
        {"schemaVersion", "gate", "status", "passed", "failed", "skipped"},
        f"{gate_id} receipt",
    )
    if raw != canonical_json(value):
        raise ExactTagGateError(f"{gate_id} receipt is not canonical JSON")
    if (
        value["schemaVersion"] != 1
        or value["gate"] != gate_id
        or value["status"] != "pass"
        or not _is_int(value["passed"])
        or value["passed"] < 1
        or value["failed"] != 0
        or value["skipped"] != 0
    ):
        raise ExactTagGateError(f"{gate_id} receipt does not prove pass/no-skips")
    return value


def _decimal6(value: Decimal) -> str:
    return format(value.quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP), "f")


def _validate_sample_list(value: Any, label: str) -> list[Decimal]:
    if not isinstance(value, list) or len(value) != BENCH_REPETITIONS:
        raise ExactTagGateError(f"BMI2 {label} samples must contain exactly five runs")
    nps_values: list[Decimal] = []
    minimum_nodes = BENCH_POSITIONS * BENCH_NODES_PER_FEN
    for index, raw in enumerate(value):
        sample = _expect_exact_keys(raw, {"nodes", "timeMillis"}, f"BMI2 {label} sample {index}")
        nodes = sample["nodes"]
        milliseconds = sample["timeMillis"]
        if (
            not _is_int(nodes)
            or nodes < minimum_nodes
            or not _is_int(milliseconds)
            or milliseconds < 1
            or milliseconds > 24 * 60 * 60 * 1000
        ):
            raise ExactTagGateError(f"BMI2 {label} sample {index} is invalid")
        nps_values.append(Decimal(nodes) * Decimal(1000) / Decimal(milliseconds))
    return nps_values


def parse_benchmark(
    path: Path,
    *,
    candidate_record: Mapping[str, Any],
    external_records: Sequence[Mapping[str, Any]],
) -> Dict[str, Any]:
    size, _ = _regular_file_digest(path, "BMI2 benchmark evidence", maximum=MAX_JSON_BYTES)
    raw = path.read_bytes()
    if len(raw) != size:
        raise ExactTagGateError("BMI2 benchmark changed while reading")
    value = _expect_exact_keys(
        _load_json_bytes(raw, "BMI2 benchmark evidence", MAX_JSON_BYTES),
        {
            "schemaVersion",
            "gate",
            "metric",
            "candidate",
            "baseline",
            "evalFileSha256",
            "corpusSha256",
            "positions",
            "nodesPerFen",
            "threads",
            "hashMb",
            "cpuAffinity",
            "searchTimeoutSeconds",
            "warmups",
            "repetitions",
            "candidateSamples",
            "baselineSamples",
            "candidateMedianNps",
            "baselineMedianNps",
            "ratio",
            "pass",
        },
        "BMI2 benchmark evidence",
    )
    if raw != canonical_json(value):
        raise ExactTagGateError("BMI2 benchmark evidence is not canonical JSON")
    external_map = {str(record["name"]): record for record in external_records}
    baseline_expected = external_map["fairy_bmi2_baseline"]
    candidate_expected = {
        key: candidate_record[key] for key in ("fileName", "bytes", "sha256")
    }
    baseline_expected_file = {
        key: baseline_expected[key] for key in ("fileName", "bytes", "sha256")
    }
    if value["candidate"] != candidate_expected:
        raise ExactTagGateError("BMI2 benchmark candidate artifact mismatch")
    if value["baseline"] != baseline_expected_file:
        raise ExactTagGateError("BMI2 benchmark baseline artifact mismatch")
    fixed = {
        "schemaVersion": 1,
        "gate": BENCH_GATE,
        "metric": "median-nps",
        "evalFileSha256": FROZEN_EXTERNAL_SHA256["legacy_net"],
        "corpusSha256": BENCH_CORPUS_SHA256,
        "positions": BENCH_POSITIONS,
        "nodesPerFen": BENCH_NODES_PER_FEN,
        "threads": BENCH_THREADS,
        "hashMb": BENCH_HASH_MB,
        "searchTimeoutSeconds": BENCH_SEARCH_TIMEOUT_SECONDS,
        "warmups": BENCH_WARMUPS,
        "repetitions": BENCH_REPETITIONS,
        "pass": True,
    }
    for key, expected in fixed.items():
        if value[key] != expected:
            raise ExactTagGateError(f"BMI2 benchmark {key} mismatch")
    if not _is_int(value["cpuAffinity"]) or value["cpuAffinity"] < 0:
        raise ExactTagGateError("BMI2 benchmark CPU affinity is invalid")
    candidate_values = _validate_sample_list(value["candidateSamples"], "candidate")
    baseline_values = _validate_sample_list(value["baselineSamples"], "baseline")
    candidate_median = sorted(candidate_values)[BENCH_REPETITIONS // 2]
    baseline_median = sorted(baseline_values)[BENCH_REPETITIONS // 2]
    try:
        ratio = candidate_median / baseline_median
    except (InvalidOperation, ZeroDivisionError) as error:
        raise ExactTagGateError("BMI2 benchmark ratio is invalid") from error
    expected_candidate = _decimal6(candidate_median)
    expected_baseline = _decimal6(baseline_median)
    expected_ratio = _decimal6(ratio)
    declared = (
        value["candidateMedianNps"],
        value["baselineMedianNps"],
        value["ratio"],
    )
    if any(not isinstance(item, str) or not DECIMAL6_RE.fullmatch(item) for item in declared):
        raise ExactTagGateError("BMI2 derived values must be positive six-decimal strings")
    if declared != (expected_candidate, expected_baseline, expected_ratio):
        raise ExactTagGateError("BMI2 benchmark derived median/ratio mismatch")
    if candidate_median <= baseline_median or ratio <= Decimal(1):
        raise ExactTagGateError("BMI2 benchmark requires candidate ratio > 1.0")
    return value


def _build_gate_environment(
    *,
    identity: Mapping[str, Any],
    evidence_root: Path,
    external_paths: Mapping[str, Path],
    artifact_paths: Mapping[str, Path],
    directory_paths: Mapping[str, Path],
    repository_paths: Mapping[str, Path],
) -> Dict[str, str]:
    environment = _safe_process_environment()
    environment.update(
        {
            "ATOMIC_EXACT_GATE_EVIDENCE_ROOT": str(evidence_root.resolve(strict=True)),
            "ATOMIC_EXACT_GATE_REPOSITORY": str(identity["repository"]),
            "ATOMIC_EXACT_GATE_REF": str(identity["ref"]),
            "ATOMIC_EXACT_GATE_COMMIT": str(identity["commit"]),
            "ATOMIC_EXACT_GATE_TAG_OBJECT": str(identity["tagObject"]),
        }
    )
    for prefix, paths in (
        ("ATOMIC_EXTERNAL_", external_paths),
        ("ATOMIC_ARTIFACT_", artifact_paths),
        ("ATOMIC_DIRECTORY_", directory_paths),
        ("ATOMIC_REPOSITORY_", repository_paths),
    ):
        for name, path in paths.items():
            environment[prefix + name.upper()] = str(path.resolve(strict=True))
    return environment


class _BoundedGateOutput:
    """Copy two child pipes to evidence logs under one strict byte budget."""

    def __init__(self, maximum: int) -> None:
        self.maximum = maximum
        self.total = 0
        self.exceeded = threading.Event()
        self.failed = threading.Event()
        self.failure: Optional[BaseException] = None
        self._lock = threading.Lock()

    def copy(self, source: BinaryIO, destination: BinaryIO) -> None:
        try:
            while True:
                chunk = os.read(source.fileno(), OUTPUT_READ_BYTES)
                if not chunk:
                    return
                with self._lock:
                    remaining = self.maximum - self.total
                    accepted = chunk[: max(0, remaining)]
                    if accepted:
                        destination.write(accepted)
                        destination.flush()
                        self.total += len(accepted)
                    if len(accepted) != len(chunk):
                        self.exceeded.set()
                        return
        except BaseException as error:  # pragma: no cover - OS pipe failures are rare
            self.failure = error
            self.failed.set()
        finally:
            try:
                source.close()
            except OSError:
                pass


def _join_reader_threads(
    readers: Sequence[threading.Thread],
    sources: Sequence[BinaryIO],
    *,
    timeout: float,
    gate_id: str,
) -> None:
    deadline = time.monotonic() + timeout
    capture_deadline = deadline - min(1.0, max(0.0, timeout))
    for reader in readers:
        reader.join(timeout=max(0.0, capture_deadline - time.monotonic()))
    alive = [reader for reader in readers if reader.is_alive()]
    if not alive:
        return
    for source in sources:
        try:
            source.close()
        except OSError:
            pass
    for reader in alive:
        reader.join(timeout=max(0.0, deadline - time.monotonic()))
    if any(reader.is_alive() for reader in alive):
        raise ExactTagGateError(f"{gate_id} output capture did not finish")


def _close_output_sources(sources: Sequence[BinaryIO]) -> None:
    for source in sources:
        try:
            source.close()
        except OSError:
            pass


def _execute_gate(
    command: Sequence[str],
    *,
    cwd: Path,
    environment: Mapping[str, str],
    timeout_seconds: int,
    stdout_path: Path,
    stderr_path: Path,
    gate_id: str,
) -> Tuple[int, int]:
    started = time.monotonic()
    limit_error: Optional[str] = None
    try:
        with stdout_path.open("xb") as stdout, stderr_path.open("xb") as stderr:
            containment = launch_contained(
                command,
                cwd=cwd,
                environment=environment,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            process = containment.process
            sources: tuple[BinaryIO, ...] = tuple(
                source
                for source in (process.stdout, process.stderr)
                if source is not None
            )
            capture = _BoundedGateOutput(MAX_GATE_OUTPUT_BYTES)
            readers: tuple[threading.Thread, ...] = ()
            started_readers: list[threading.Thread] = []
            operation_error: Optional[BaseException] = None
            finalization_error: Optional[BaseException] = None
            try:
                if process.stdout is None or process.stderr is None:  # pragma: no cover
                    raise ExactTagGateError(f"could not capture {gate_id} output")
                readers = (
                    threading.Thread(
                        target=capture.copy,
                        args=(process.stdout, stdout),
                        name=f"{gate_id}-stdout",
                        daemon=True,
                    ),
                    threading.Thread(
                        target=capture.copy,
                        args=(process.stderr, stderr),
                        name=f"{gate_id}-stderr",
                        daemon=True,
                    ),
                )
                for reader in readers:
                    reader.start()
                    started_readers.append(reader)
                while process.poll() is None:
                    elapsed = time.monotonic() - started
                    if elapsed > timeout_seconds:
                        limit_error = f"{gate_id} timed out after {timeout_seconds}s"
                        break
                    if capture.exceeded.is_set():
                        limit_error = (
                            f"{gate_id} output exceeded {MAX_GATE_OUTPUT_BYTES} bytes"
                        )
                        break
                    if capture.failed.is_set():
                        limit_error = f"{gate_id} output capture failed"
                        break
                    capture.exceeded.wait(PROCESS_POLL_SECONDS)
            except BaseException as error:
                operation_error = error
            finally:
                try:
                    containment.terminate_tree(timeout=15)
                except ProcessContainmentError as error:
                    finalization_error = error
                if len(started_readers) != len(sources):
                    _close_output_sources(sources[len(started_readers) :])
                try:
                    _join_reader_threads(
                        started_readers,
                        sources[: len(started_readers)],
                        timeout=10,
                        gate_id=gate_id,
                    )
                except ExactTagGateError as error:
                    if finalization_error is None:
                        finalization_error = error
                finally:
                    _close_output_sources(sources)
            if finalization_error is not None:
                raise ExactTagGateError(
                    f"could not terminate {gate_id} process tree: {finalization_error}"
                ) from operation_error
            if operation_error is not None:
                if isinstance(operation_error, (KeyboardInterrupt, SystemExit)):
                    raise operation_error
                if isinstance(operation_error, ExactTagGateError):
                    raise operation_error
                raise ExactTagGateError(
                    f"could not execute {gate_id}: {operation_error}"
                ) from operation_error
            return_code = process.returncode
            if return_code is None:  # pragma: no cover - containment verifies this
                raise ExactTagGateError(f"{gate_id} root process was not reaped")
            if capture.failure is not None:
                raise ExactTagGateError(
                    f"{gate_id} output capture failed: {capture.failure}"
                ) from capture.failure
            if capture.exceeded.is_set() and limit_error is None:
                limit_error = f"{gate_id} output exceeded {MAX_GATE_OUTPUT_BYTES} bytes"
            stdout.flush()
            stderr.flush()
            os.fsync(stdout.fileno())
            os.fsync(stderr.fileno())
    except (OSError, ProcessContainmentError) as error:
        raise ExactTagGateError(f"could not execute {gate_id}: {error}") from error
    if limit_error is not None:
        raise ExactTagGateError(limit_error)
    duration_ms = max(0, int((time.monotonic() - started) * 1000))
    return return_code, duration_ms


def _write_exclusive(path: Path, payload: bytes) -> None:
    parent = _assert_real_directory(path.parent, "manifest parent")
    if path.parent.resolve(strict=True) != parent:
        raise ExactTagGateError("manifest parent resolution mismatch")
    if path.exists() or path.is_symlink():
        raise ExactTagGateError(f"refusing to overwrite manifest: {path}")
    flags = (
        os.O_WRONLY
        | os.O_CREAT
        | os.O_EXCL
        | getattr(os, "O_BINARY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    try:
        descriptor = os.open(str(path), flags, 0o444)
        try:
            view = memoryview(payload)
            while view:
                written = os.write(descriptor, view)
                if written <= 0:
                    raise OSError("short manifest write")
                view = view[written:]
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    except OSError as error:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass
        raise ExactTagGateError(f"could not write manifest: {path}") from error


def _load_runtime_schema(repo_root: Path) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    raw, record = _tracked_file_record(
        repo_root, SCHEMA_PATH, "exact-tag manifest schema", MAX_SCHEMA_BYTES
    )
    schema = _load_json_bytes(raw, "exact-tag manifest schema", MAX_SCHEMA_BYTES)
    if not isinstance(schema, dict):
        raise ExactTagGateError("exact-tag manifest schema must be an object")
    try:
        Draft202012Validator.check_schema(schema)
    except SchemaError as error:
        raise ExactTagGateError(f"invalid exact-tag JSON Schema: {error.message}") from error
    return schema, record


def _schema_validate(schema: Mapping[str, Any], manifest: Mapping[str, Any]) -> None:
    try:
        Draft202012Validator(schema).validate(manifest)
    except ValidationError as error:
        location = "/".join(str(part) for part in error.absolute_path) or "<root>"
        raise ExactTagGateError(
            f"exact-tag manifest JSON Schema violation at {location}: {error.message}"
        ) from error


def validate_manifest_value(value: Any) -> Dict[str, Any]:
    manifest = _expect_exact_keys(
        value,
        {
            "schemaVersion",
            "project",
            "schema",
            "commandPlan",
            "identity",
            "externalInputs",
            "releaseArtifacts",
            "directoryBindings",
            "auxiliaryRepositories",
            "gates",
        },
        "manifest",
    )
    if manifest["schemaVersion"] != SCHEMA_VERSION or manifest["project"] != PROJECT:
        raise ExactTagGateError("manifest schema/project mismatch")
    for key in ("schema", "commandPlan"):
        record = _expect_exact_keys(
            manifest[key], {"path", "bytes", "sha256", "gitBlob"}, key
        )
        if (
            not isinstance(record["path"], str)
            or not _is_int(record["bytes"])
            or record["bytes"] < 1
            or not isinstance(record["sha256"], str)
            or not SHA256_RE.fullmatch(record["sha256"])
            or not isinstance(record["gitBlob"], str)
            or not SHA1_RE.fullmatch(record["gitBlob"])
        ):
            raise ExactTagGateError(f"manifest {key} record is invalid")
    identity = manifest["identity"]
    if not isinstance(identity, dict):
        raise ExactTagGateError("manifest identity is invalid")
    if identity.get("repository") != REPOSITORY or identity.get("version") != RELEASE_VERSION:
        raise ExactTagGateError("manifest identity mismatch")
    if identity.get("tag") != RELEASE_TAG or identity.get("ref") != RELEASE_REF:
        raise ExactTagGateError("manifest tag identity mismatch")
    if identity.get("event") != RELEASE_EVENT:
        raise ExactTagGateError("manifest event mismatch")
    if not SHA1_RE.fullmatch(str(identity.get("commit", ""))) or not SHA1_RE.fullmatch(
        str(identity.get("tree", ""))
    ):
        raise ExactTagGateError("manifest source identity is invalid")
    if not isinstance(identity.get("submodules"), list):
        raise ExactTagGateError("manifest submodules are invalid")

    external = manifest["externalInputs"]
    if not isinstance(external, list) or len(external) != len(FROZEN_EXTERNAL_SHA256):
        raise ExactTagGateError("manifest external input count mismatch")
    for record, (name, expected_sha) in zip(external, FROZEN_EXTERNAL_SHA256.items()):
        if record.get("name") != name or record.get("sha256") != expected_sha:
            raise ExactTagGateError(f"manifest external input {name} mismatch")
    artifacts = manifest["releaseArtifacts"]
    if not isinstance(artifacts, list) or not artifacts:
        raise ExactTagGateError("manifest release artifacts are invalid")
    artifact_names = [record.get("name") for record in artifacts if isinstance(record, dict)]
    if len(artifact_names) != len(artifacts) or BENCH_ARTIFACT not in artifact_names:
        raise ExactTagGateError("manifest release artifact names are invalid")

    gates = manifest["gates"]
    if not isinstance(gates, list) or len(gates) != len(REQUIRED_GATES):
        raise ExactTagGateError("manifest gate count mismatch")
    global_paths: set[str] = set()
    for gate, expected_id in zip(gates, REQUIRED_GATES):
        required = {"id", "status", "command", "exitCode", "durationMillis", "evidence"}
        required.add("benchmark" if expected_id == BENCH_GATE else "receipt")
        item = _expect_exact_keys(gate, required, f"gate {expected_id}")
        if item["id"] != expected_id or item["status"] != "pass" or item["exitCode"] != 0:
            raise ExactTagGateError(f"gate {expected_id} result is invalid")
        if not _is_int(item["durationMillis"]) or item["durationMillis"] < 0:
            raise ExactTagGateError(f"gate {expected_id} duration is invalid")
        evidence = item["evidence"]
        if not isinstance(evidence, list) or len(evidence) < 3:
            raise ExactTagGateError(f"gate {expected_id} has insufficient evidence")
        paths = []
        for record in evidence:
            relative = _safe_evidence_relative(str(record.get("path", "")))
            folded = relative.casefold()
            if folded in global_paths:
                raise ExactTagGateError(f"duplicate manifest evidence: {relative}")
            global_paths.add(folded)
            paths.append(relative)
        if paths != sorted(paths):
            raise ExactTagGateError(f"gate {expected_id} evidence is not sorted")
        for stream in ("stdout", "stderr"):
            if f"_runner/{expected_id}.{stream}.log" not in paths:
                raise ExactTagGateError(f"gate {expected_id} omits runner {stream}")
        runner_bytes = sum(
            record["bytes"]
            for record in evidence
            if record["path"].startswith(f"_runner/{expected_id}.")
        )
        if runner_bytes <= 0:
            raise ExactTagGateError(f"gate {expected_id} has no auditable output")
        if expected_id != BENCH_GATE:
            receipt = item["receipt"]
            if receipt.get("status") != "pass" or receipt.get("skipped") != 0:
                raise ExactTagGateError(f"gate {expected_id} receipt is not pass/no-skip")
        elif item["benchmark"].get("pass") is not True:
            raise ExactTagGateError("BMI2 benchmark does not declare pass")
    return manifest


def _verify_sources(
    manifest: Mapping[str, Any],
    *,
    repo_root: Path,
    plan_path: str,
    evidence_root: Path,
    expected_identity: Mapping[str, Any],
    external_paths: Mapping[str, Path],
    artifact_paths: Mapping[str, Path],
    directory_paths: Mapping[str, Path],
    repository_paths: Mapping[str, Path],
) -> None:
    identity = validate_identity(
        repo_root,
        repository=str(expected_identity.get("repository", "")),
        version=str(expected_identity.get("version", "")),
        event=str(expected_identity.get("event", "")),
        ref=str(expected_identity.get("ref", "")),
        commit=str(expected_identity.get("commit", "")),
        tag_object=str(expected_identity.get("tagObject", "")),
        run_id=str(expected_identity.get("runId", "")),
        run_attempt=expected_identity.get("runAttempt", 0),
    )
    if identity != dict(expected_identity) or manifest["identity"] != identity:
        raise ExactTagGateError("manifest identity differs from authenticated tag")
    schema, schema_record = _load_runtime_schema(repo_root)
    plan, plan_record = load_command_plan(repo_root, plan_path)
    if manifest["schema"] != schema_record or manifest["commandPlan"] != plan_record:
        raise ExactTagGateError("manifest schema/command-plan metadata mismatch")
    _schema_validate(schema, manifest)
    artifact_names = tuple(plan["artifacts"])
    directory_names = tuple(plan["directories"])
    external = authenticate_external_inputs(external_paths)
    artifacts = authenticate_artifacts(artifact_paths, artifact_names)
    directories = authenticate_directories(directory_paths, directory_names)
    repositories = authenticate_aux_repositories(repository_paths)
    if manifest["externalInputs"] != external:
        raise ExactTagGateError("manifest external inputs differ from actual files")
    if manifest["releaseArtifacts"] != artifacts:
        raise ExactTagGateError("manifest release artifacts differ from actual files")
    if manifest["directoryBindings"] != directories:
        raise ExactTagGateError("manifest directory bindings differ from actual paths")
    if manifest["auxiliaryRepositories"] != repositories:
        raise ExactTagGateError("manifest auxiliary repositories differ from exact trees")

    root = _assert_real_directory(evidence_root, "evidence root")
    expected_paths: set[str] = set()
    artifact_map = {str(record["name"]): record for record in artifacts}
    for gate, plan_gate in zip(manifest["gates"], plan["gates"]):
        template = _validate_command_template(
            repo_root,
            gate["id"],
            plan_gate["argv"],
            artifact_names,
            directory_names,
        )
        resolved = _expand_command(
            template,
            repo_root=repo_root,
            evidence_root=root,
            benchmark_evidence=plan["benchmarkEvidence"],
            external_paths=external_paths,
            artifact_paths=artifact_paths,
            directory_paths=directory_paths,
            repository_paths=repository_paths,
        )
        expected_command = _command_record(
            template, resolved, plan_gate["timeoutSeconds"]
        )
        if gate["command"] != expected_command:
            raise ExactTagGateError(f"{gate['id']} command differs from tracked plan")
        for record in gate["evidence"]:
            actual = _evidence_record(root, record["path"])
            if actual != record:
                raise ExactTagGateError(f"evidence metadata mismatch: {record['path']}")
            expected_paths.add(record["path"])
        receipt_path = _evidence_path(root, plan_gate["receiptEvidence"])
        if gate["id"] == BENCH_GATE:
            benchmark = parse_benchmark(
                receipt_path,
                candidate_record=artifact_map[BENCH_ARTIFACT],
                external_records=external,
            )
            if gate["benchmark"] != benchmark:
                raise ExactTagGateError("BMI2 benchmark differs from manifest")
        else:
            receipt = _parse_gate_receipt(receipt_path, gate["id"])
            if gate["receipt"] != receipt:
                raise ExactTagGateError(f"{gate['id']} receipt differs from manifest")
    _validate_evidence_tree(root, expected_paths)
    if authenticate_external_inputs(external_paths) != external:
        raise ExactTagGateError("external inputs changed during verification")
    if authenticate_artifacts(artifact_paths, artifact_names) != artifacts:
        raise ExactTagGateError("release artifacts changed during verification")


def run_gates(
    *,
    repo_root: Path,
    plan_path: str,
    evidence_root: Path,
    manifest_path: Path,
    identity: Mapping[str, Any],
    external_paths: Mapping[str, Path],
    artifact_paths: Mapping[str, Path],
    directory_paths: Mapping[str, Path],
    repository_paths: Mapping[str, Path],
) -> Dict[str, Any]:
    root = _assert_real_directory(repo_root, "repo root")
    if validate_identity(
        root,
        repository=str(identity.get("repository", "")),
        version=str(identity.get("version", "")),
        event=str(identity.get("event", "")),
        ref=str(identity.get("ref", "")),
        commit=str(identity.get("commit", "")),
        tag_object=str(identity.get("tagObject", "")),
        run_id=str(identity.get("runId", "")),
        run_attempt=identity.get("runAttempt", 0),
    ) != dict(identity):
        raise ExactTagGateError("runner identity differs from authenticated tag")
    schema, schema_record = _load_runtime_schema(root)
    plan, plan_record = load_command_plan(root, plan_path)
    artifact_names = tuple(plan["artifacts"])
    directory_names = tuple(plan["directories"])
    external_records = authenticate_external_inputs(external_paths)
    artifact_records = authenticate_artifacts(artifact_paths, artifact_names)
    directory_records = authenticate_directories(directory_paths, directory_names)
    repository_records = authenticate_aux_repositories(repository_paths)

    evidence_parent = _assert_real_directory(evidence_root.parent, "evidence parent")
    evidence_candidate = evidence_parent / evidence_root.name
    if evidence_candidate.exists() or evidence_candidate.is_symlink():
        raise ExactTagGateError("evidence root must not already exist")
    try:
        manifest_path.resolve(strict=False).relative_to(evidence_candidate.resolve(strict=False))
    except ValueError:
        pass
    else:
        raise ExactTagGateError("manifest must be outside evidence root")
    try:
        evidence_candidate.mkdir()
        (evidence_candidate / "_runner").mkdir()
    except OSError as error:
        raise ExactTagGateError("could not create exclusive evidence root") from error
    evidence_root = _assert_real_directory(evidence_candidate, "evidence root")

    base_environment = _build_gate_environment(
        identity=identity,
        evidence_root=evidence_root,
        external_paths=external_paths,
        artifact_paths=artifact_paths,
        directory_paths=directory_paths,
        repository_paths=repository_paths,
    )
    artifact_map = {str(record["name"]): record for record in artifact_records}
    gate_records: list[Dict[str, Any]] = []
    for plan_gate in plan["gates"]:
        gate_id = plan_gate["id"]
        template = _validate_command_template(
            root, gate_id, plan_gate["argv"], artifact_names, directory_names
        )
        resolved = _expand_command(
            template,
            repo_root=root,
            evidence_root=evidence_root,
            benchmark_evidence=plan["benchmarkEvidence"],
            external_paths=external_paths,
            artifact_paths=artifact_paths,
            directory_paths=directory_paths,
            repository_paths=repository_paths,
        )
        declared = list(plan_gate["evidence"])
        stdout_relative = f"_runner/{gate_id}.stdout.log"
        stderr_relative = f"_runner/{gate_id}.stderr.log"
        all_paths = sorted([*declared, stdout_relative, stderr_relative])
        for relative in all_paths:
            path = evidence_root.joinpath(*PurePosixPath(relative).parts)
            if path.exists() or path.is_symlink():
                raise ExactTagGateError(f"evidence already exists before {gate_id}: {relative}")
            path.parent.mkdir(parents=True, exist_ok=True)
        gate_environment = dict(base_environment)
        gate_environment["ATOMIC_EXACT_GATE_ID"] = gate_id
        return_code, duration_ms = _execute_gate(
            resolved,
            cwd=root,
            environment=gate_environment,
            timeout_seconds=plan_gate["timeoutSeconds"],
            stdout_path=evidence_root / stdout_relative,
            stderr_path=evidence_root / stderr_relative,
            gate_id=gate_id,
        )
        if return_code != 0:
            raise ExactTagGateError(f"{gate_id} failed with exit code {return_code}")
        evidence_records = [_evidence_record(evidence_root, path) for path in all_paths]
        runner_bytes = sum(
            record["bytes"]
            for record in evidence_records
            if record["path"] in {stdout_relative, stderr_relative}
        )
        if runner_bytes <= 0:
            raise ExactTagGateError(f"{gate_id} produced no auditable output")
        gate_record: Dict[str, Any] = {
            "id": gate_id,
            "status": "pass",
            "command": _command_record(template, resolved, plan_gate["timeoutSeconds"]),
            "exitCode": 0,
            "durationMillis": duration_ms,
            "evidence": evidence_records,
        }
        receipt_path = _evidence_path(evidence_root, plan_gate["receiptEvidence"])
        if gate_id == BENCH_GATE:
            gate_record["benchmark"] = parse_benchmark(
                receipt_path,
                candidate_record=artifact_map[BENCH_ARTIFACT],
                external_records=external_records,
            )
        else:
            gate_record["receipt"] = _parse_gate_receipt(receipt_path, gate_id)
        gate_records.append(gate_record)

    expected_paths = {
        record["path"] for gate in gate_records for record in gate["evidence"]
    }
    _validate_evidence_tree(evidence_root, expected_paths)
    _seal_evidence_tree(evidence_root)
    # Re-hash after sealing; later gates and chmod must not have changed evidence.
    for gate in gate_records:
        for record in gate["evidence"]:
            if _evidence_record(evidence_root, record["path"]) != record:
                raise ExactTagGateError(
                    f"evidence changed before manifest: {record['path']}"
                )
    manifest = {
        "schemaVersion": SCHEMA_VERSION,
        "project": PROJECT,
        "schema": schema_record,
        "commandPlan": plan_record,
        "identity": dict(identity),
        "externalInputs": external_records,
        "releaseArtifacts": artifact_records,
        "directoryBindings": directory_records,
        "auxiliaryRepositories": repository_records,
        "gates": gate_records,
    }
    validate_manifest_value(manifest)
    _schema_validate(schema, manifest)
    # Independent source/evidence verification happens before any manifest byte exists.
    _verify_sources(
        manifest,
        repo_root=root,
        plan_path=plan_path,
        evidence_root=evidence_root,
        expected_identity=identity,
        external_paths=external_paths,
        artifact_paths=artifact_paths,
        directory_paths=directory_paths,
        repository_paths=repository_paths,
    )
    payload = canonical_json(manifest)
    _write_exclusive(manifest_path, payload)
    try:
        verify_manifest(
            repo_root=root,
            plan_path=plan_path,
            manifest_path=manifest_path,
            evidence_root=evidence_root,
            expected_identity=identity,
            external_paths=external_paths,
            artifact_paths=artifact_paths,
            directory_paths=directory_paths,
            repository_paths=repository_paths,
        )
    except ExactTagGateError:
        try:
            manifest_path.chmod(stat.S_IWRITE | stat.S_IREAD)
            manifest_path.unlink()
        except OSError:
            pass
        raise
    return manifest


def verify_manifest(
    *,
    repo_root: Path,
    plan_path: str,
    manifest_path: Path,
    evidence_root: Path,
    expected_identity: Mapping[str, Any],
    external_paths: Mapping[str, Path],
    artifact_paths: Mapping[str, Path],
    directory_paths: Mapping[str, Path],
    repository_paths: Mapping[str, Path],
) -> Dict[str, Any]:
    size, digest = _regular_file_digest(
        manifest_path, "exact-tag manifest", maximum=MAX_JSON_BYTES
    )
    raw = manifest_path.read_bytes()
    if len(raw) != size or hashlib.sha256(raw).hexdigest() != digest:
        raise ExactTagGateError("exact-tag manifest changed while reading")
    manifest = validate_manifest_value(
        _load_json_bytes(raw, "exact-tag manifest", MAX_JSON_BYTES)
    )
    if raw != canonical_json(manifest):
        raise ExactTagGateError("exact-tag manifest is not canonical JSON")
    _verify_sources(
        manifest,
        repo_root=repo_root,
        plan_path=plan_path,
        evidence_root=evidence_root,
        expected_identity=expected_identity,
        external_paths=external_paths,
        artifact_paths=artifact_paths,
        directory_paths=directory_paths,
        repository_paths=repository_paths,
    )
    return manifest


def _add_identity_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--repo-root", type=Path, required=True)
    parser.add_argument(
        "--command-plan",
        required=True,
        help="canonical repo-relative JSON plan tracked by the exact tag",
    )
    parser.add_argument("--repository", dest="github_repository", required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--event", required=True)
    parser.add_argument("--ref", required=True)
    parser.add_argument("--commit", required=True)
    parser.add_argument("--tag-object", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--run-attempt", type=int, required=True)
    parser.add_argument(
        "--external",
        action="append",
        default=[],
        metavar="NAME=PATH",
        help="each of the six frozen external files",
    )
    parser.add_argument(
        "--artifact",
        action="append",
        default=[],
        metavar="NAME=PATH",
        help="same-run release file named by the tracked command plan",
    )
    parser.add_argument(
        "--directory",
        action="append",
        default=[],
        metavar="NAME=PATH",
        help="real directory root named by the tracked command plan",
    )
    parser.add_argument(
        "--aux-repository",
        action="append",
        default=[],
        metavar="tools|trainer|fairy=PATH",
        help="frozen clean auxiliary repository",
    )


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="operation", required=True)
    run = subparsers.add_parser("run", help="execute the exact tracked six-gate plan")
    _add_identity_arguments(run)
    run.add_argument("--evidence-root", type=Path, required=True)
    run.add_argument("--manifest", type=Path, required=True)
    verify = subparsers.add_parser(
        "verify", help="re-authenticate a completed exact-tag evidence bundle"
    )
    _add_identity_arguments(verify)
    verify.add_argument("--evidence-root", type=Path, required=True)
    verify.add_argument("--manifest", type=Path, required=True)
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    try:
        identity = validate_identity(
            args.repo_root,
            repository=args.github_repository,
            version=args.version,
            event=args.event,
            ref=args.ref,
            commit=args.commit,
            tag_object=args.tag_object,
            run_id=args.run_id,
            run_attempt=args.run_attempt,
        )
        plan, _ = load_command_plan(args.repo_root, args.command_plan)
        external_paths = _parse_name_path_specs(
            args.external, FROZEN_EXTERNAL_SHA256, "external input"
        )
        artifact_paths = _parse_name_path_specs(
            args.artifact, plan["artifacts"], "release artifact"
        )
        directory_paths = _parse_name_path_specs(
            args.directory, plan["directories"], "directory binding"
        )
        repository_paths = _parse_name_path_specs(
            args.aux_repository,
            FROZEN_AUX_REPOSITORY_COMMITS,
            "auxiliary repository",
        )
        common = {
            "repo_root": args.repo_root,
            "plan_path": args.command_plan,
            "manifest_path": args.manifest,
            "evidence_root": args.evidence_root,
            "external_paths": external_paths,
            "artifact_paths": artifact_paths,
            "directory_paths": directory_paths,
            "repository_paths": repository_paths,
        }
        if args.operation == "run":
            manifest = run_gates(identity=identity, **common)
            action = "ran"
        else:
            manifest = verify_manifest(expected_identity=identity, **common)
            action = "verified"
    except ExactTagGateError as error:
        print(f"exact-tag gate error: {error}", file=sys.stderr)
        return 2
    digest = hashlib.sha256(canonical_json(manifest)).hexdigest()
    print(
        f"{action} {len(manifest['gates'])} exact-tag gates: "
        f"version={manifest['identity']['version']} "
        f"commit={manifest['identity']['commit']} manifest_sha256={digest}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
