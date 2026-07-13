#!/usr/bin/env python3
"""Reproducible final H7 Atomic BIN V2 generate/train/load gate.

This is a release gate, not a convenience launcher. Every checkout, ref,
artifact and digest is explicit. The output directory must not exist and is
retained as an audit archive on both success and failure.
"""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import struct
import subprocess
import sys
import traceback
from typing import Any, Mapping, Sequence

from legacy_pipeline_e2e import UciProcess


DATA_SCHEMA_SHA256 = "0352b036f2a140c609e3eb9c9d635dc553e8d77253d8faa92437390f5cf93cb6"
MANIFEST_SCHEMA_SHA256 = "83d63922df3ac4a0c81a21ec9d9fd9e180efe50f26efee62fe01710e09da5b42"
DECODE_SCHEMA_SHA256 = "5e3f8d7c6db6ee955b71747ee063859e15609adb557a3754228a606f3df2caad"
LEGACY_SCHEMA_SHA256 = "acca0f551f1c012c31a6c727dedccaebb7b5ebbc46810edb87e31bb208d5abe1"
LEGACY_NNUE_VERSION = 0x7AF32F20
LEGACY_NNUE_ARCHITECTURE = 0x3C103E72

RECORDS = 128
RECORDS_PER_SHARD = 64
SHARDS = 2
BATCH_SIZE = 96
EPOCH_SIZE = 96
VALIDATION_SIZE = 96
GENERATOR_DEPTH = 3
GENERATOR_HASH_MB = 512
GENERATOR_EVAL_LIMIT = 32000
DEFAULT_TIMEOUT_SECONDS = 1800.0

ATOMIC_REPOSITORY = "Belzedar94/Atomic-Stockfish"
TOOLS_REPOSITORY = "Belzedar94/variant-nnue-tools"
TRAINER_REPOSITORY = "Belzedar94/variant-nnue-pytorch"

SHA1_RE = re.compile(r"^[0-9a-f]{40}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
PORTABLE_BASENAME_RE = re.compile(r"^[^/\\\x00]+$")
SQUARE_RE = re.compile(r"^[a-h][1-8]$")


class GateError(RuntimeError):
    """A release invariant failed."""


class DuplicateJsonKey(ValueError):
    """Strict JSON parsing found a duplicate object member."""


@dataclass(frozen=True)
class CheckoutSpec:
    label: str
    root: Path
    commit: str
    ref: str
    repository: str


@dataclass(frozen=True)
class CheckoutState:
    label: str
    root: str
    commit: str
    ref: str
    repository: str


@dataclass(frozen=True)
class Fingerprint:
    label: str
    path: str
    bytes: int
    sha256: str


@dataclass(frozen=True)
class CommandResult:
    label: str
    argv: tuple[str, ...]
    cwd: str
    returncode: int
    stdout: bytes
    stderr: bytes


@dataclass(frozen=True)
class DatasetResult:
    label: str
    manifest: Path
    manifest_sha256: str
    shard_paths: tuple[Path, ...]
    shard_sha256: tuple[str, ...]
    validation: Mapping[str, Any]
    decode_sha256: str


@dataclass(frozen=True)
class GateConfig:
    atomic: CheckoutSpec
    tools: CheckoutSpec
    trainer: CheckoutSpec
    contract_engine_commit: str
    engine: Path
    engine_sha256: str
    data_generator: Path
    data_generator_sha256: str
    data_tools: Path
    data_tools_sha256: str
    tools_wrapper: Path
    wrapper_data_tools: Path
    wrapper_data_tools_sha256: str
    trainer_loader: Path
    trainer_loader_sha256: str
    train_script: Path
    serialize_script: Path
    python: Path
    python_sha256: str
    source_net: Path
    source_net_sha256: str
    output_dir: Path
    train_seed: int
    validation_seed: int
    timeout_seconds: float


@dataclass(frozen=True)
class Preflight:
    checkouts: Mapping[str, CheckoutState]
    fingerprints: Mapping[str, Fingerprint]


def require(condition: bool, message: str) -> None:
    if not condition:
        raise GateError(message)


def require_sha1(value: str, label: str) -> str:
    if SHA1_RE.fullmatch(value) is None:
        raise GateError(f"{label} must be one lowercase 40-character Git SHA")
    return value


def require_sha256(value: str, label: str) -> str:
    if SHA256_RE.fullmatch(value) is None:
        raise GateError(f"{label} must be one lowercase SHA-256")
    return value


def parse_uint64(value: str) -> int:
    if re.fullmatch(r"0|[1-9][0-9]*", value) is None:
        raise argparse.ArgumentTypeError("must be a canonical unsigned decimal integer")
    parsed = int(value)
    if parsed > 2**64 - 1:
        raise argparse.ArgumentTypeError("must fit uint64")
    return parsed


def parse_positive_float(value: str) -> float:
    try:
        parsed = float(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("must be a number") from error
    if not 0 < parsed <= 24 * 60 * 60:
        raise argparse.ArgumentTypeError("must be in (0, 86400]")
    return parsed


def canonical_json(value: object) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")


def canonical_json_preserving_order(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=False, separators=(",", ":")) + "\n").encode(
        "utf-8"
    )


def python_json_line_statement(expression: str) -> str:
    return (
        "sys.stdout.buffer.write((json.dumps("
        + expression
        + ",separators=(',',':'))+'\\n').encode('utf-8'))"
    )


def strict_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise DuplicateJsonKey(f"duplicate JSON key {key!r}")
        result[key] = value
    return result


def parse_strict_json(payload: bytes, label: str) -> Any:
    try:
        text = payload.decode("utf-8", errors="strict")
        return json.loads(text, object_pairs_hook=strict_object)
    except (UnicodeDecodeError, json.JSONDecodeError, DuplicateJsonKey) as error:
        raise GateError(f"{label} is not strict UTF-8 JSON: {error}") from error


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def require_regular_file(path: Path, label: str) -> Path:
    resolved = path.expanduser().resolve()
    require(resolved.is_file(), f"{label} is not a regular file: {resolved}")
    require(not resolved.is_symlink(), f"{label} must not be a symlink: {resolved}")
    stat = resolved.stat()
    reparse = getattr(stat, "st_file_attributes", 0) & getattr(
        os, "FILE_ATTRIBUTE_REPARSE_POINT", 0
    )
    require(not reparse, f"{label} must not be a reparse point: {resolved}")
    return resolved


def require_directory(path: Path, label: str) -> Path:
    resolved = path.expanduser().resolve()
    require(resolved.is_dir(), f"{label} is not a directory: {resolved}")
    require(not resolved.is_symlink(), f"{label} must not be a symlink: {resolved}")
    return resolved


def require_within(path: Path, root: Path, label: str) -> None:
    try:
        path.relative_to(root)
    except ValueError as error:
        raise GateError(f"{label} is outside its authenticated checkout: {path}") from error


def fingerprint(path: Path, label: str, expected_sha256: str | None = None) -> Fingerprint:
    resolved = require_regular_file(path, label)
    digest = sha256_file(resolved)
    if expected_sha256 is not None:
        expected = require_sha256(expected_sha256, f"{label} expected SHA-256")
        require(
            digest == expected,
            f"{label} SHA-256 mismatch: expected {expected}, got {digest}",
        )
    return Fingerprint(label, str(resolved), resolved.stat().st_size, digest)


def run_raw(
    argv: Sequence[str],
    *,
    cwd: Path | None = None,
    input_bytes: bytes | None = None,
    timeout: float = 60.0,
) -> subprocess.CompletedProcess[bytes]:
    env = dict(os.environ)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    arguments: dict[str, object] = {
        "cwd": str(cwd) if cwd is not None else None,
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
        "timeout": timeout,
        "check": False,
        "shell": False,
        "env": env,
    }
    if input_bytes is None:
        arguments["stdin"] = subprocess.DEVNULL
    else:
        # subprocess.run() creates a PIPE for input=. Passing stdin= as well
        # raises ValueError and would make every generator invocation fail.
        arguments["input"] = input_bytes
    try:
        return subprocess.run([str(value) for value in argv], **arguments)  # type: ignore[arg-type]
    except (OSError, ValueError, subprocess.TimeoutExpired) as error:
        raise GateError(f"cannot execute {list(argv)!r}: {error}") from error


def git_output(root: Path, *arguments: str) -> bytes:
    completed = run_raw(("git", "-C", str(root), *arguments))
    if completed.returncode != 0:
        detail = (completed.stdout + completed.stderr).decode("utf-8", errors="replace")
        raise GateError(f"git {' '.join(arguments)} failed in {root}: {detail.strip()}")
    return completed.stdout


def normalize_github_repository(url: str) -> str | None:
    value = url.strip()
    match = re.fullmatch(r"https://github\.com/([^/]+/[^/]+?)(?:\.git)?/?", value)
    if match is None:
        match = re.fullmatch(r"git@github\.com:([^/]+/[^/]+?)(?:\.git)?", value)
    return match.group(1) if match is not None else None


def verify_checkout(spec: CheckoutSpec) -> CheckoutState:
    root = require_directory(spec.root, f"{spec.label} checkout")
    commit = require_sha1(spec.commit, f"{spec.label} commit")
    top = Path(git_output(root, "rev-parse", "--show-toplevel").decode().strip()).resolve()
    require(top == root, f"{spec.label} path is not its Git toplevel: {root}")
    head = git_output(root, "rev-parse", "HEAD").decode().strip()
    require(head == commit, f"{spec.label} HEAD mismatch: expected {commit}, got {head}")
    ref = git_output(root, "rev-parse", spec.ref).decode().strip()
    require(ref == commit, f"{spec.label} ref {spec.ref} is {ref}, expected {commit}")
    status = git_output(root, "status", "--porcelain=v1", "--untracked-files=all")
    require(status == b"", f"{spec.label} checkout is dirty:\n{status.decode(errors='replace')}")
    remote_lines = git_output(root, "config", "--get-regexp", r"^remote\..*\.url$").decode(
        "utf-8", errors="strict"
    )
    repositories = {
        normalized
        for line in remote_lines.splitlines()
        if " " in line
        for normalized in (normalize_github_repository(line.split(" ", 1)[1]),)
        if normalized is not None
    }
    require(
        spec.repository in repositories,
        f"{spec.label} has no authenticated remote for {spec.repository}",
    )
    return CheckoutState(spec.label, str(root), commit, spec.ref, spec.repository)


def verify_submodule_pin(
    parent: Path,
    relative: str,
    expected_commit: str,
    *,
    label: str,
) -> Path:
    expected = require_sha1(expected_commit, f"{label} commit")
    gitlink = git_output(parent, "rev-parse", f":{relative}").decode().strip()
    require(gitlink == expected, f"{label} gitlink is {gitlink}, expected {expected}")
    root = require_directory(parent / relative, label)
    head = git_output(root, "rev-parse", "HEAD").decode().strip()
    require(head == expected, f"{label} checkout is {head}, expected {expected}")
    status = git_output(root, "status", "--porcelain=v1", "--untracked-files=all")
    require(status == b"", f"{label} checkout is dirty")
    return root


def verify_schema_tree(root: Path, label: str) -> None:
    expected = {
        "schemas/atomic-bin-v2.json": DATA_SCHEMA_SHA256,
        "schemas/atomic-bin-v2-manifest.json": MANIFEST_SCHEMA_SHA256,
        "schemas/atomic-data-tools-decode-v1.json": DECODE_SCHEMA_SHA256,
    }
    for relative, digest in expected.items():
        fingerprint(root / relative, f"{label} {relative}", digest)


def expected_data_tools_capabilities() -> Mapping[str, object]:
    return {
        "type": "atomic-data-tools-capabilities",
        "contract_version": 1,
        "formats": {
            "atomic-bin-v2": {
                "data_schema_sha256": DATA_SCHEMA_SHA256,
                "manifest_schema_sha256": MANIFEST_SCHEMA_SHA256,
                "decode_schema_sha256": DECODE_SCHEMA_SHA256,
                "entrypoint": "manifest",
                "read": True,
                "write": False,
                "operations": ["validate", "decode"],
            }
        },
    }


def expected_data_tools_capabilities_bytes() -> bytes:
    return canonical_json_preserving_order(expected_data_tools_capabilities())


def verify_tools_lock(tools_root: Path, contract_engine_commit: str) -> None:
    path = require_regular_file(tools_root / "atomic-engine.lock.json", "tools engine lock")
    payload = path.read_bytes()
    lock = parse_strict_json(payload, "tools engine lock")
    require(isinstance(lock, dict), "tools engine lock root must be an object")
    submodule = lock.get("submodule")
    require(isinstance(submodule, dict), "tools engine lock has no submodule object")
    require(
        submodule.get("commit") == contract_engine_commit,
        "tools engine lock does not pin the requested contract engine commit",
    )
    contract = lock.get("data_tools_contract")
    require(isinstance(contract, dict), "tools engine lock has no data_tools_contract")
    decode_schema = contract.get("decode_schema")
    require(isinstance(decode_schema, dict), "tools lock has no decode_schema")
    require(
        decode_schema.get("path") == "schemas/atomic-data-tools-decode-v1.json"
        and decode_schema.get("sha256") == DECODE_SCHEMA_SHA256,
        "tools lock decode schema does not match H7.5",
    )
    require(
        contract.get("capabilities")
        == expected_data_tools_capabilities_bytes().decode("utf-8"),
        "tools lock capabilities do not match the frozen validate/decode contract",
    )


def private_work_path(output_dir: Path) -> Path:
    output = output_dir.expanduser().resolve()
    return output.with_name(f".{output.name}.private-work")


def archive_redactions(config: GateConfig) -> tuple[tuple[Path, str], ...]:
    return (
        (config.engine, "<PLAYING_ENGINE>"),
        (config.data_generator, "<DATA_GENERATOR>"),
        (config.data_tools, "<DIRECT_DATA_TOOLS>"),
        (config.tools_wrapper, "<TOOLS_WRAPPER>"),
        (config.wrapper_data_tools, "<WRAPPER_DATA_TOOLS>"),
        (config.trainer_loader, "<TRAINER_LOADER>"),
        (config.train_script, "<TRAIN_SCRIPT>"),
        (config.serialize_script, "<SERIALIZE_SCRIPT>"),
        (config.python, "<PYTHON>"),
        (config.source_net, "<SOURCE_NET>"),
        (config.atomic.root, "<ATOMIC_ROOT>"),
        (config.tools.root, "<TOOLS_ROOT>"),
        (config.trainer.root, "<TRAINER_ROOT>"),
    )


class Archive:
    def __init__(self, root: Path, redactions: Sequence[tuple[Path, str]] = ()):
        self.root = root.expanduser().resolve()
        self.private_root = private_work_path(self.root)
        require(not self.root.exists(), f"output directory already exists: {self.root}")
        require(
            not self.private_root.exists(),
            f"private work directory already exists: {self.private_root}",
        )
        require(
            not any(character.isspace() for character in str(self.root)),
            "output directory cannot contain whitespace because the generator wire is unquoted",
        )
        replacements = [
            (self.root, "<ARCHIVE>"),
            (self.private_root, "<PRIVATE_WORK>"),
            *redactions,
        ]
        variants: dict[str, str] = {}
        for path, token in replacements:
            resolved = path.expanduser().resolve()
            for value in (str(resolved), resolved.as_posix()):
                if value:
                    variants[value] = token
        self.redactions = tuple(
            sorted(variants.items(), key=lambda item: len(item[0]), reverse=True)
        )
        self.root.mkdir(parents=True, exist_ok=False)
        self.private_root.mkdir(parents=False, exist_ok=False)
        (self.root / "logs").mkdir()
        self.command_index = 0

    def path(self, relative: str) -> Path:
        path = (self.root / relative).resolve()
        require_within(path, self.root, f"archive path {relative}")
        return path

    def write(self, relative: str, payload: bytes) -> Path:
        path = self.path(relative)
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with path.open("xb") as output:
                output.write(payload)
        except FileExistsError as error:
            raise GateError(f"archive refuses overwrite: {path}") from error
        return path

    def redact_text(self, value: str) -> str:
        redacted = value
        for source, token in self.redactions:
            redacted = re.sub(
                re.escape(source), lambda unused: token, redacted, flags=re.IGNORECASE
            )
        return redacted

    def redact_value(self, value: object) -> object:
        if isinstance(value, str):
            return self.redact_text(value)
        if isinstance(value, dict):
            return {key: self.redact_value(item) for key, item in value.items()}
        if isinstance(value, list):
            return [self.redact_value(item) for item in value]
        if isinstance(value, tuple):
            return [self.redact_value(item) for item in value]
        return value

    def write_log(self, relative: str, payload: bytes) -> Path:
        text = payload.decode("utf-8", errors="replace")
        return self.write(relative, self.redact_text(text).encode("utf-8"))

    def write_json(self, relative: str, value: object) -> Path:
        return self.write(relative, canonical_json(self.redact_value(value)))

    def private_path(self, relative: str) -> Path:
        path = (self.private_root / relative).resolve()
        require_within(path, self.private_root, f"private work path {relative}")
        return path

    def cleanup_private(self) -> None:
        if not self.private_root.exists():
            return
        require(
            self.private_root.parent == self.root.parent,
            "private work directory parent changed",
        )
        try:
            shutil.rmtree(self.private_root)
        except OSError as error:
            raise GateError(f"cannot remove private work directory: {error}") from error

    def run(
        self,
        label: str,
        argv: Sequence[str],
        *,
        cwd: Path,
        timeout: float,
        input_bytes: bytes | None = None,
        expected_returncodes: frozenset[int] = frozenset({0}),
    ) -> CommandResult:
        self.command_index += 1
        slug = re.sub(r"[^a-z0-9]+", "-", label.lower()).strip("-")
        prefix = f"logs/{self.command_index:02d}-{slug}"
        command = {
            "argv": [str(value) for value in argv],
            "cwd": str(cwd.resolve()),
            "stdin_bytes": 0 if input_bytes is None else len(input_bytes),
            "timeout_seconds": timeout,
        }
        self.write_json(prefix + ".command.json", command)
        if input_bytes is not None:
            self.write_log(prefix + ".stdin.log", input_bytes)
        completed = run_raw(argv, cwd=cwd, input_bytes=input_bytes, timeout=timeout)
        self.write_log(prefix + ".stdout.log", completed.stdout)
        self.write_log(prefix + ".stderr.log", completed.stderr)
        if completed.returncode not in expected_returncodes:
            raise GateError(
                f"{label} returned {completed.returncode}, expected "
                f"{sorted(expected_returncodes)}; see {prefix}.*"
            )
        return CommandResult(
            label,
            tuple(str(value) for value in argv),
            str(cwd.resolve()),
            completed.returncode,
            completed.stdout,
            completed.stderr,
        )


def preflight(config: GateConfig, *, allow_output: bool = False) -> Preflight:
    if allow_output:
        require(config.output_dir.resolve().is_dir(), "postflight output directory disappeared")
    else:
        require(not config.output_dir.resolve().exists(), "output directory already exists")
    require(
        not private_work_path(config.output_dir).exists(),
        "private work directory exists before or after the gate",
    )
    require(
        not any(character.isspace() for character in str(config.output_dir.resolve())),
        "output directory cannot contain whitespace",
    )
    require(config.train_seed != config.validation_seed, "train and validation seeds must differ")
    contract_commit = require_sha1(config.contract_engine_commit, "contract engine commit")

    states = {
        "atomic": verify_checkout(config.atomic),
        "tools": verify_checkout(config.tools),
        "trainer": verify_checkout(config.trainer),
    }
    roots = tuple(Path(state.root) for state in states.values())
    output = config.output_dir.expanduser().resolve()
    private_output = private_work_path(output)
    for root in roots:
        require(
            not output.is_relative_to(root),
            f"output directory must stay outside authenticated checkout {root}",
        )
        require(
            not private_output.is_relative_to(root),
            f"private work directory must stay outside authenticated checkout {root}",
        )

    tools_engine = verify_submodule_pin(
        config.tools.root.resolve(),
        "engine/Atomic-Stockfish",
        contract_commit,
        label="tools Atomic-Stockfish submodule",
    )
    trainer_engine = verify_submodule_pin(
        config.trainer.root.resolve(),
        "external/Atomic-Stockfish",
        contract_commit,
        label="trainer Atomic-Stockfish submodule",
    )
    verify_schema_tree(config.atomic.root.resolve(), "Atomic checkout")
    verify_schema_tree(tools_engine, "tools engine submodule")
    verify_schema_tree(trainer_engine, "trainer engine submodule")
    verify_tools_lock(config.tools.root.resolve(), contract_commit)

    canonical_sources = {
        "tools_wrapper": (config.tools_wrapper, config.tools.root, "script/atomic_bin_v2_tools.py"),
        "train_script": (config.train_script, config.trainer.root, "train.py"),
        "serialize_script": (config.serialize_script, config.trainer.root, "serialize.py"),
    }
    for label, (path, root, relative) in canonical_sources.items():
        resolved = require_regular_file(path, label)
        expected = (root.resolve() / relative).resolve()
        require(resolved == expected, f"{label} must be the canonical checkout path {expected}")
        require_within(resolved, root.resolve(), label)

    executable_owners = {
        "engine": (config.engine, config.atomic.root),
        "data_generator": (config.data_generator, config.atomic.root),
        "data_tools": (config.data_tools, config.atomic.root),
        "wrapper_data_tools": (config.wrapper_data_tools, tools_engine),
        "trainer_loader": (config.trainer_loader, config.trainer.root),
    }
    wrapper_artifact_name = (
        "atomic-stockfish-data-tools.exe" if os.name == "nt" else "atomic-stockfish-data-tools"
    )
    expected_wrapper_artifact = (tools_engine / "src" / wrapper_artifact_name).resolve()
    require(
        config.wrapper_data_tools.resolve() == expected_wrapper_artifact,
        f"wrapper_data_tools must be the canonical pinned artifact {expected_wrapper_artifact}",
    )
    for label, (path, owner) in executable_owners.items():
        resolved = require_regular_file(path, label)
        require_within(resolved, owner.resolve(), label)

    paths = {
        "engine": (config.engine, config.engine_sha256),
        "data_generator": (config.data_generator, config.data_generator_sha256),
        "data_tools": (config.data_tools, config.data_tools_sha256),
        "wrapper_data_tools": (
            config.wrapper_data_tools,
            config.wrapper_data_tools_sha256,
        ),
        "trainer_loader": (config.trainer_loader, config.trainer_loader_sha256),
        "source_net": (config.source_net, config.source_net_sha256),
    }
    fingerprints = {
        label: fingerprint(path, label, expected)
        for label, (path, expected) in paths.items()
    }
    fingerprints.update(
        {
            "tools_wrapper": fingerprint(config.tools_wrapper, "tools_wrapper"),
            "train_script": fingerprint(config.train_script, "train_script"),
            "serialize_script": fingerprint(config.serialize_script, "serialize_script"),
            "python": fingerprint(config.python, "python", config.python_sha256),
        }
    )
    verify_nnue_header(config.source_net)
    return Preflight(states, fingerprints)


def require_clean_success(result: CommandResult, label: str) -> bytes:
    require(result.returncode == 0, f"{label} did not succeed")
    require(result.stderr == b"", f"{label} wrote unexpected stderr")
    require(result.stdout.endswith(b"\n"), f"{label} stdout is not LF terminated")
    require(b"\r" not in result.stdout, f"{label} stdout contains CR bytes")
    require(not result.stdout.startswith(b"\xef\xbb\xbf"), f"{label} stdout has a BOM")
    return result.stdout


def verify_capabilities(config: GateConfig, archive: Archive) -> Mapping[str, object]:
    direct = archive.run(
        "direct data-tools capabilities",
        (str(config.data_tools), "capabilities"),
        cwd=config.atomic.root,
        timeout=config.timeout_seconds,
    )
    wrapper = archive.run(
        "wrapper data-tools capabilities",
        (str(config.python), "-B", str(config.tools_wrapper), "capabilities"),
        cwd=config.tools.root,
        timeout=config.timeout_seconds,
    )
    expected = expected_data_tools_capabilities_bytes()
    direct_bytes = require_clean_success(direct, "direct capabilities")
    wrapper_bytes = require_clean_success(wrapper, "wrapper capabilities")
    require(direct_bytes == expected, "direct data-tools capabilities changed")
    require(wrapper_bytes == expected, "wrapper data-tools capabilities changed")
    require(wrapper_bytes == direct_bytes, "wrapper capabilities are not byte-exact")

    capability_probe = "import json,nnue_dataset,sys;" + python_json_line_statement(
        "{'dll':nnue_dataset.dllpath,'capability':"
        "nnue_dataset.atomic_training_data_schemas()}"
    )
    trainer = archive.run(
        "trainer capabilities",
        (str(config.python), "-B", "-c", capability_probe),
        cwd=config.trainer.root,
        timeout=config.timeout_seconds,
    )
    trainer_bytes = require_clean_success(trainer, "trainer capabilities")
    require(trainer_bytes.count(b"\n") == 1, "trainer emitted multiple capability lines")
    trainer_payload = parse_strict_json(trainer_bytes, "trainer capabilities")
    require(isinstance(trainer_payload, dict), "trainer capabilities root is not an object")
    require(
        Path(str(trainer_payload.get("dll", ""))).resolve()
        == config.trainer_loader.resolve(),
        "trainer imported a different native loader",
    )
    expected_trainer = {
        "capability_version": 2,
        "formats": {
            "legacy-atomic-v1": {
                "schema_sha256": LEGACY_SCHEMA_SHA256,
                "read": True,
                "write": False,
                "header_size": 0,
                "record_size": 72,
            },
            "atomic-bin-v2": {
                "read": True,
                "write": False,
                "entrypoint": "manifest",
                "header_size": 96,
                "record_size": 64,
                "schema_sha256": DATA_SCHEMA_SHA256,
                "manifest_schema_sha256": MANIFEST_SCHEMA_SHA256,
            },
        },
    }
    require(
        trainer_payload.get("capability") == expected_trainer,
        "trainer manifest-reader capability changed",
    )
    return {
        "data_tools": expected_data_tools_capabilities(),
        "trainer": expected_trainer,
    }


def generation_command(output_stem: Path, seed: int) -> str:
    values: tuple[tuple[str, object], ...] = (
        ("depth", GENERATOR_DEPTH),
        ("nodes", 0),
        ("count", RECORDS),
        ("save_every", RECORDS_PER_SHARD),
        ("eval_limit", GENERATOR_EVAL_LIMIT),
        ("eval_diff_limit", 64000),
        ("random_move_min_ply", 1),
        ("random_move_max_ply", 24),
        ("random_move_count", 5),
        ("random_move_like_apery", 0),
        ("random_multi_pv", 5),
        ("random_multi_pv_diff", 100),
        ("random_multi_pv_depth", GENERATOR_DEPTH),
        ("write_min_ply", 5),
        ("write_max_ply", 400),
        ("keep_draws", 1),
        ("adjudicate_draws_by_score", "true"),
        ("adjudicate_draws_by_insufficient_material", "true"),
        ("filter_captures", "true"),
        ("filter_checks", "false"),
        ("filter_promotions", "true"),
        ("random_file_name", "false"),
        ("output_file_name", output_stem),
        ("data_format", "atomic-bin-v2"),
        ("seed", seed),
    )
    return "generate_training_data " + " ".join(
        f"{name} {value}" for name, value in values
    )


def generator_stdin(config: GateConfig, output_stem: Path, seed: int) -> bytes:
    commands = (
        "uci",
        "setoption name UCI_Variant value atomic",
        "setoption name UCI_Chess960 value false",
        "setoption name Threads value 1",
        f"setoption name Hash value {GENERATOR_HASH_MB}",
        f"setoption name EvalFile value {config.source_net.resolve()}",
        "setoption name Use NNUE value pure",
        "isready",
        generation_command(output_stem, seed),
        "quit",
        "",
    )
    return "\n".join(commands).encode("utf-8")


def parse_manifest(path: Path) -> Mapping[str, Any]:
    payload = require_regular_file(path, "Atomic BIN V2 manifest").read_bytes()
    require(payload.endswith(b"\n"), "manifest is not LF terminated")
    require(payload.count(b"\n") == 1 and b"\r" not in payload, "manifest is not one LF line")
    require(not payload.startswith(b"\xef\xbb\xbf"), "manifest has a BOM")
    manifest = parse_strict_json(payload, "Atomic BIN V2 manifest")
    require(isinstance(manifest, dict), "manifest root is not an object")
    require(
        canonical_json_preserving_order(manifest) == payload,
        "manifest bytes are not canonical minified JSON",
    )
    return manifest


def require_exact_keys(value: object, keys: Sequence[str], label: str) -> Mapping[str, Any]:
    require(isinstance(value, dict), f"{label} must be an object")
    require(set(value) == set(keys), f"{label} has missing or unknown fields")
    return value


def require_ordered_keys(value: object, keys: Sequence[str], label: str) -> Mapping[str, Any]:
    checked = require_exact_keys(value, keys, label)
    require(tuple(checked) == tuple(keys), f"{label} key order changed")
    return checked


def validate_manifest(
    manifest_path: Path,
    *,
    config: GateConfig,
    seed: int,
) -> tuple[Mapping[str, Any], tuple[Path, ...], tuple[str, ...]]:
    manifest = parse_manifest(manifest_path)
    require_exact_keys(
        manifest,
        (
            "manifest_version",
            "manifest_schema_sha256",
            "data_schema_sha256",
            "format",
            "engine",
            "network",
            "book",
            "generation",
            "statistics",
            "shards",
        ),
        "manifest",
    )
    require(manifest["manifest_version"] == 1, "manifest version changed")
    require(
        manifest["manifest_schema_sha256"] == MANIFEST_SCHEMA_SHA256,
        "manifest schema digest changed",
    )
    require(manifest["data_schema_sha256"] == DATA_SCHEMA_SHA256, "data schema changed")
    require(manifest["format"] == "atomic-bin-v2", "manifest format changed")

    engine = require_exact_keys(manifest["engine"], ("commit", "version"), "engine")
    require(engine["commit"] == config.atomic.commit, "generator embedded the wrong engine commit")
    require(isinstance(engine["version"], str) and engine["version"], "engine version is empty")
    network = require_exact_keys(manifest["network"], ("file", "sha256"), "network")
    # The basename is provenance only. Compatibility is determined by the
    # authenticated bytes/header, never by a special filename.
    require(network["file"] == config.source_net.name, "manifest network basename mismatch")
    require(network["sha256"] == config.source_net_sha256, "manifest network digest mismatch")
    require(
        manifest["book"] == {"kind": "builtin-startpos", "file": None, "sha256": None},
        "final H7 gate must use the built-in Atomic start position",
    )

    generation = require_exact_keys(
        manifest["generation"],
        ("resolved_seed", "atomic960", "threads", "hash_mb", "use_nnue", "options"),
        "generation",
    )
    require(generation["resolved_seed"] == str(seed), "resolved generator seed mismatch")
    require(generation["atomic960"] is False, "final H7 dataset unexpectedly uses Atomic960")
    require(generation["threads"] == 1, "generator threads changed")
    require(generation["hash_mb"] == str(GENERATOR_HASH_MB), "generator hash changed")
    require(generation["use_nnue"] == "pure", "generator did not record Use NNUE=pure")
    options = require_exact_keys(
        generation["options"],
        (
            "search_depth_min",
            "search_depth_max",
            "nodes",
            "requested_records",
            "records_per_shard",
            "eval_limit",
            "eval_diff_limit",
            "random_move_min_ply",
            "random_move_max_ply",
            "random_move_count",
            "random_move_like_apery",
            "random_multi_pv",
            "random_multi_pv_diff",
            "random_multi_pv_depth",
            "write_min_ply",
            "write_max_ply",
            "keep_draws",
            "adjudicate_draws_by_score",
            "adjudicate_insufficient",
            "filter_captures",
            "filter_checks",
            "filter_promotions",
            "random_file_name",
            "set_recommended_uci_options_seen",
        ),
        "generation options",
    )
    required_options = {
        "search_depth_min": GENERATOR_DEPTH,
        "search_depth_max": GENERATOR_DEPTH,
        "nodes": "0",
        "requested_records": str(RECORDS),
        "records_per_shard": str(RECORDS_PER_SHARD),
        "eval_limit": GENERATOR_EVAL_LIMIT,
        "eval_diff_limit": 64000,
        "random_move_min_ply": 1,
        "random_move_max_ply": 24,
        "random_move_count": 5,
        "random_move_like_apery": 0,
        "random_multi_pv": 5,
        "random_multi_pv_diff": 100,
        "random_multi_pv_depth": GENERATOR_DEPTH,
        "write_min_ply": 5,
        "write_max_ply": 400,
        "keep_draws": "1",
        "adjudicate_draws_by_score": True,
        "adjudicate_insufficient": True,
        "filter_captures": True,
        "filter_checks": False,
        "filter_promotions": True,
        "random_file_name": False,
        "set_recommended_uci_options_seen": False,
    }
    for key, expected in required_options.items():
        require(options[key] == expected, f"generator policy {key} is {options[key]!r}")

    statistics = require_exact_keys(manifest["statistics"], ("records", "draws"), "statistics")
    require(statistics["records"] == str(RECORDS), "manifest record total changed")
    require(
        isinstance(statistics["draws"], str)
        and re.fullmatch(r"0|[1-9][0-9]*", statistics["draws"]) is not None
        and int(statistics["draws"]) <= RECORDS,
        "manifest draw total is invalid",
    )

    shards = manifest["shards"]
    require(isinstance(shards, list) and len(shards) == SHARDS, "dataset must have two shards")
    shard_paths: list[Path] = []
    shard_hashes: list[str] = []
    for index, raw in enumerate(shards):
        shard = require_exact_keys(raw, ("index", "file", "records", "bytes", "sha256"), "shard")
        require(shard["index"] == index, f"shard {index} index changed")
        require(shard["records"] == str(RECORDS_PER_SHARD), f"shard {index} record count changed")
        expected_bytes = 96 + 64 * RECORDS_PER_SHARD
        require(shard["bytes"] == str(expected_bytes), f"shard {index} byte count changed")
        name = shard["file"]
        require(
            isinstance(name, str)
            and PORTABLE_BASENAME_RE.fullmatch(name) is not None
            and name not in {".", ".."},
            f"shard {index} has a nonportable basename",
        )
        shard_path = (manifest_path.parent / name).resolve()
        require_within(shard_path, manifest_path.parent.resolve(), f"shard {index}")
        actual = fingerprint(shard_path, f"shard {index}")
        require(actual.bytes == expected_bytes, f"shard {index} filesystem size changed")
        require(actual.sha256 == shard["sha256"], f"shard {index} checksum mismatch")
        shard_paths.append(shard_path)
        shard_hashes.append(actual.sha256)
    require(len(set(shard_hashes)) == SHARDS, "a dataset contains duplicate shard bytes")
    return manifest, tuple(shard_paths), tuple(shard_hashes)


def generate_dataset(
    config: GateConfig,
    archive: Archive,
    *,
    label: str,
    seed: int,
) -> tuple[Path, Mapping[str, Any], tuple[Path, ...], tuple[str, ...]]:
    directory = archive.path(f"datasets/{label}")
    directory.mkdir(parents=True, exist_ok=False)
    stem = directory / label
    result = archive.run(
        f"generate {label}",
        (str(config.data_generator),),
        cwd=config.atomic.root,
        timeout=config.timeout_seconds,
        input_bytes=generator_stdin(config, stem, seed),
    )
    output = (result.stdout + result.stderr).decode("utf-8", errors="replace")
    lines = output.splitlines()
    require("readyok" in lines, f"{label} generator did not acknowledge isready")
    require(
        lines.count("INFO: generate_training_data finished.") == 1,
        f"{label} generator emitted no unique completion marker",
    )
    require(not any("ERROR" in line.upper() for line in lines), f"{label} generator reported error")
    require(
        lines.count(f"INFO: schema_sha256 = {DATA_SCHEMA_SHA256.upper()}") == 1
        or lines.count(f"INFO: schema_sha256 = {DATA_SCHEMA_SHA256}") == 1,
        f"{label} generator did not authenticate the V2 schema",
    )
    manifest_path = Path(str(stem) + ".atbin.manifest.json")
    manifest, shards, hashes = validate_manifest(
        manifest_path,
        config=config,
        seed=seed,
    )
    return manifest_path, manifest, shards, hashes


def validate_success_json(payload: bytes, label: str) -> Mapping[str, Any]:
    require(payload.count(b"\n") == 1, f"{label} must emit exactly one line")
    value = parse_strict_json(payload, label)
    require(isinstance(value, dict), f"{label} root is not an object")
    require(canonical_json_preserving_order(value) == payload, f"{label} JSON is not canonical")
    expected_keys = (
        "type",
        "contract_version",
        "status",
        "format",
        "entrypoint",
        "shards",
        "records",
        "side_to_move_wins",
        "draws",
        "side_to_move_losses",
        "atomic960_records",
    )
    require_exact_keys(value, expected_keys, label)
    require(value["type"] == "atomic-data-tools-validation", f"{label} type changed")
    require(value["contract_version"] == 1 and value["status"] == "ok", f"{label} failed")
    require(value["format"] == "atomic-bin-v2", f"{label} format changed")
    require(value["entrypoint"] == "manifest", f"{label} entrypoint changed")
    require(
        value["shards"] == SHARDS and value["records"] == str(RECORDS),
        f"{label} totals changed",
    )
    totals = sum(
        int(value[field])
        for field in ("side_to_move_wins", "draws", "side_to_move_losses")
    )
    require(totals == RECORDS, f"{label} WDL totals do not reconcile")
    require(value["atomic960_records"] == "0", f"{label} unexpectedly contains Atomic960")
    return value


def require_bounded_int(value: object, minimum: int, maximum: int, label: str) -> int:
    require(
        type(value) is int and minimum <= value <= maximum,
        f"{label} is outside [{minimum}, {maximum}]",
    )
    return value


def require_uint32_string(value: object, label: str, *, allow_zero: bool = True) -> int:
    require(
        isinstance(value, str) and re.fullmatch(r"0|[1-9][0-9]{0,9}", value) is not None,
        f"{label} is not a canonical uint32 string",
    )
    parsed = int(value)
    require(parsed <= 0xFFFFFFFF, f"{label} exceeds uint32")
    require(allow_zero or parsed != 0, f"{label} cannot be zero")
    return parsed


def require_square(value: object, label: str, *, nullable: bool = False) -> str | None:
    if nullable and value is None:
        return None
    require(
        isinstance(value, str) and SQUARE_RE.fullmatch(value) is not None,
        f"{label} is invalid",
    )
    return value


def square_index(square: str) -> int:
    return ord(square[0]) - ord("a") + 8 * (ord(square[1]) - ord("1"))


def validate_decoded_position(value: object, *, atomic960: bool, label: str) -> None:
    position = require_ordered_keys(
        value,
        (
            "fen",
            "fen_notation",
            "side_to_move",
            "rule50",
            "fullmove",
            "castling_rights",
            "castling_rook_origins",
            "en_passant",
        ),
        label,
    )
    fen = position["fen"]
    require(isinstance(fen, str) and fen, f"{label}.fen is empty")
    fields = fen.split(" ")
    require(len(fields) == 6 and all(fields), f"{label}.fen has no six canonical fields")
    board, side, castling_fen, ep_fen, rule50_fen, fullmove_fen = fields
    ranks = board.split("/")
    require(len(ranks) == 8, f"{label}.fen board has no eight ranks")
    pieces: list[str] = []
    for rank_index, rank in enumerate(ranks):
        require(re.search(r"[1-8][1-8]", rank) is None, f"{label}.fen rank is noncanonical")
        files = 0
        for token in rank:
            if token in "12345678":
                files += int(token)
            else:
                require(token in "pnbrqkPNBRQK", f"{label}.fen has an invalid piece")
                pieces.append(token)
                files += 1
        require(files == 8, f"{label}.fen rank {8 - rank_index} does not span eight files")
    require(pieces.count("K") == 1 and pieces.count("k") == 1, f"{label}.fen king count changed")

    require(side in {"w", "b"}, f"{label}.fen side-to-move is invalid")
    expected_side = "white" if side == "w" else "black"
    require(position["side_to_move"] == expected_side, f"{label}.side_to_move differs from FEN")
    expected_notation = "shredder-fen" if atomic960 else "fen"
    require(position["fen_notation"] == expected_notation, f"{label}.fen_notation changed")
    rule50 = require_bounded_int(position["rule50"], 0, 32767, f"{label}.rule50")
    fullmove = require_bounded_int(position["fullmove"], 1, 100000, f"{label}.fullmove")
    require(rule50_fen == str(rule50), f"{label}.rule50 differs from FEN")
    require(fullmove_fen == str(fullmove), f"{label}.fullmove differs from FEN")

    rights = require_ordered_keys(
        position["castling_rights"], ("wire", "fen"), f"{label}.castling_rights"
    )
    rights_wire = require_bounded_int(
        rights["wire"], 0, 15, f"{label}.castling_rights.wire"
    )
    require(
        isinstance(rights["fen"], str) and 1 <= len(rights["fen"]) <= 4,
        f"{label}.castling_rights.fen is invalid",
    )
    require(rights["fen"] == castling_fen, f"{label}.castling rights differ from FEN")
    origins = require_ordered_keys(
        position["castling_rook_origins"],
        ("white_kingside", "white_queenside", "black_kingside", "black_queenside"),
        f"{label}.castling_rook_origins",
    )
    origin_names = tuple(origins)
    for bit, name in enumerate(origin_names):
        origin = require_square(
            origins[name], f"{label}.castling_rook_origins.{name}", nullable=True
        )
        require(
            (origin is not None) == bool(rights_wire & (1 << bit)),
            f"{label}.{name} presence changed",
        )
    if not atomic960:
        expected_origins = ("h1", "a1", "h8", "a8")
        for bit, (name, expected) in enumerate(zip(origin_names, expected_origins)):
            if rights_wire & (1 << bit):
                require(origins[name] == expected, f"{label}.{name} is not orthodox")
        expected_castling = "".join(
            token for bit, token in enumerate("KQkq") if rights_wire & (1 << bit)
        ) or "-"
        require(castling_fen == expected_castling, f"{label}.castling FEN is noncanonical")

    en_passant = require_square(position["en_passant"], f"{label}.en_passant", nullable=True)
    require(ep_fen == (en_passant or "-"), f"{label}.en_passant differs from FEN")
    if en_passant is not None:
        expected_rank = "6" if side == "w" else "3"
        require(en_passant[1] == expected_rank, f"{label}.en_passant rank is noncanonical")


def validate_decoded_move(value: object, label: str) -> None:
    move = require_ordered_keys(
        value, ("wire", "from", "to", "type", "promotion"), label
    )
    wire = require_uint32_string(move["wire"], f"{label}.wire", allow_zero=False)
    from_square = require_square(move["from"], f"{label}.from")
    to_square = require_square(move["to"], f"{label}.to")
    require(from_square != to_square, f"{label} has equal from/to squares")
    move_types = {"normal": 0, "promotion": 1, "en-passant": 2, "castling": 3}
    promotions = {"none": 0, "knight": 1, "bishop": 2, "rook": 3, "queen": 4}
    require(
        isinstance(move["type"], str) and move["type"] in move_types,
        f"{label}.type is invalid",
    )
    require(
        isinstance(move["promotion"], str) and move["promotion"] in promotions,
        f"{label}.promotion is invalid",
    )
    promotion_code = promotions[str(move["promotion"])]
    require(
        (move["type"] == "promotion") == (promotion_code != 0),
        f"{label} promotion/type coupling changed",
    )
    expected_wire = (
        square_index(str(from_square))
        | (square_index(str(to_square)) << 6)
        | (move_types[str(move["type"])] << 12)
        | (promotion_code << 16)
    )
    require(wire == expected_wire, f"{label}.wire differs from decoded move fields")


def validate_decoded_record(value: object, *, index: int, atomic960: bool) -> int:
    label = f"record {index}"
    record = require_ordered_keys(
        value,
        (
            "type",
            "contract_version",
            "global_index",
            "shard_index",
            "local_index",
            "position",
            "score_stm",
            "ply",
            "result_stm",
            "flags",
            "atomic960",
            "move",
        ),
        label,
    )
    require(record["type"] == "atomic-data-tools-decode-record", f"{label} type changed")
    require(
        type(record["contract_version"]) is int and record["contract_version"] == 1,
        f"{label} contract changed",
    )
    require(record["global_index"] == str(index), f"{label} global index changed")
    expected_shard = index // RECORDS_PER_SHARD
    expected_local = index % RECORDS_PER_SHARD
    require(record["shard_index"] == str(expected_shard), f"{label} shard index changed")
    require(record["local_index"] == str(expected_local), f"{label} local index changed")
    validate_decoded_position(record["position"], atomic960=atomic960, label=f"{label}.position")
    require_bounded_int(record["score_stm"], -2147483647, 2147483647, f"{label}.score_stm")
    require_uint32_string(record["ply"], f"{label}.ply")
    require(
        type(record["result_stm"]) is int and record["result_stm"] in {-1, 0, 1},
        f"{label}.result_stm is invalid",
    )
    flags = require_bounded_int(record["flags"], 0, 1, f"{label}.flags")
    require(type(record["atomic960"]) is bool, f"{label}.atomic960 is not boolean")
    require(record["atomic960"] is atomic960, f"{label}.atomic960 differs from header")
    require(flags == int(atomic960), f"{label}.flags differs from Atomic960 mode")
    validate_decoded_move(record["move"], f"{label}.move")
    return int(record["result_stm"])


def validate_decode_jsonl(
    payload: bytes,
    *,
    manifest: Mapping[str, Any],
    validation: Mapping[str, Any],
) -> None:
    require(payload.endswith(b"\n") and b"\r" not in payload, "decode output is not raw LF JSONL")
    require(not payload.startswith(b"\xef\xbb\xbf"), "decode output has a BOM")
    raw_lines = payload.splitlines()
    require(len(raw_lines) == RECORDS + 2, "decode JSONL has the wrong number of lines")
    values: list[Mapping[str, Any]] = []
    for index, line in enumerate(raw_lines):
        value = parse_strict_json(line, f"decode line {index}")
        require(isinstance(value, dict), f"decode line {index} is not an object")
        require(
            canonical_json_preserving_order(value).rstrip(b"\n") == line,
            f"decode line {index} is not canonical JSON",
        )
        values.append(value)

    header = values[0]
    expected_header = {
        "type": "atomic-data-tools-decode-header",
        "contract_version": 1,
        "status": "ok",
        "format": "atomic-bin-v2",
        "entrypoint": "manifest",
        "decode_schema_sha256": DECODE_SCHEMA_SHA256,
        "data_schema_sha256": DATA_SCHEMA_SHA256,
        "manifest_schema_sha256": MANIFEST_SCHEMA_SHA256,
        "slice": {"offset": "0", "limit": RECORDS},
        "dataset": {"records": str(RECORDS), "shards": SHARDS, "atomic960": False},
        "provenance": {
            "engine": manifest["engine"],
            "network": manifest["network"],
            "book": manifest["book"],
            "generation": manifest["generation"],
        },
    }
    require_ordered_keys(header, tuple(expected_header), "decode header")
    require(header == expected_header, "decode provenance header changed")
    result_counts = {-1: 0, 0: 0, 1: 0}
    for index, record in enumerate(values[1:-1]):
        result = validate_decoded_record(record, index=index, atomic960=False)
        result_counts[result] += 1

    footer = values[-1]
    expected_footer = {
        "type": "atomic-data-tools-decode-footer",
        "contract_version": 1,
        "status": "ok",
        "format": "atomic-bin-v2",
        "slice": {"offset": "0", "limit": RECORDS, "records": str(RECORDS)},
        "validation": {
            "status": "ok",
            "shards": validation["shards"],
            "records": validation["records"],
            "side_to_move_wins": validation["side_to_move_wins"],
            "draws": validation["draws"],
            "side_to_move_losses": validation["side_to_move_losses"],
            "atomic960_records": validation["atomic960_records"],
        },
    }
    require_ordered_keys(footer, tuple(expected_footer), "decode footer")
    require(footer == expected_footer, "decode validation footer changed")
    expected_counts = {
        1: int(validation["side_to_move_wins"]),
        0: int(validation["draws"]),
        -1: int(validation["side_to_move_losses"]),
    }
    require(
        result_counts == expected_counts,
        f"decoded result_stm WDL {result_counts} differs from validation {expected_counts}",
    )


def validate_and_decode(
    config: GateConfig,
    archive: Archive,
    *,
    label: str,
    manifest_path: Path,
    manifest: Mapping[str, Any],
) -> tuple[Mapping[str, Any], str]:
    validate_args = (
        "validate",
        "--format",
        "atomic-bin-v2",
        "--manifest",
        str(manifest_path),
    )
    direct_validation = archive.run(
        f"direct validate {label}",
        (str(config.data_tools), *validate_args),
        cwd=config.atomic.root,
        timeout=config.timeout_seconds,
    )
    wrapper_validation = archive.run(
        f"wrapper validate {label}",
        (str(config.python), "-B", str(config.tools_wrapper), *validate_args),
        cwd=config.tools.root,
        timeout=config.timeout_seconds,
    )
    direct_bytes = require_clean_success(direct_validation, f"direct validate {label}")
    wrapper_bytes = require_clean_success(wrapper_validation, f"wrapper validate {label}")
    require(direct_bytes == wrapper_bytes, f"{label} wrapper validation is not byte-exact")
    validation = validate_success_json(direct_bytes, f"{label} validation")

    decode_args = (
        "decode",
        "--format",
        "atomic-bin-v2",
        "--manifest",
        str(manifest_path),
        "--offset",
        "0",
        "--limit",
        str(RECORDS),
    )
    direct_decode = archive.run(
        f"direct decode {label}",
        (str(config.data_tools), *decode_args),
        cwd=config.atomic.root,
        timeout=config.timeout_seconds,
    )
    wrapper_decode = archive.run(
        f"wrapper decode {label}",
        (str(config.python), "-B", str(config.tools_wrapper), *decode_args),
        cwd=config.tools.root,
        timeout=config.timeout_seconds,
    )
    direct_decode_bytes = require_clean_success(direct_decode, f"direct decode {label}")
    wrapper_decode_bytes = require_clean_success(wrapper_decode, f"wrapper decode {label}")
    require(
        direct_decode_bytes == wrapper_decode_bytes,
        f"{label} wrapper decode is not byte-exact",
    )
    validate_decode_jsonl(direct_decode_bytes, manifest=manifest, validation=validation)
    return validation, hashlib.sha256(direct_decode_bytes).hexdigest()


def training_command(
    config: GateConfig,
    train_manifest: Path,
    validation_manifest: Path,
    trainer_output: Path,
) -> tuple[str, ...]:
    return (
        str(config.python),
        "-B",
        str(config.train_script),
        str(train_manifest),
        str(validation_manifest),
        "--accelerator=cpu",
        "--devices=1",
        "--threads=1",
        f"--batch-size={BATCH_SIZE}",
        "--num-workers=1",
        "--no-smart-fen-skipping",
        "--random-fen-skipping=0",
        "--features=HalfKAv2^",
        "--lambda=1.0",
        f"--seed={config.train_seed}",
        f"--epoch-size={EPOCH_SIZE}",
        f"--validation-size={VALIDATION_SIZE}",
        "--max_epochs=1",
        "--num_sanity_val_steps=0",
        f"--default_root_dir={trainer_output}",
        f"--resume-from-model={config.source_net.resolve()}",
    )


def parse_checkpoint_metadata(payload: bytes) -> Mapping[str, Any]:
    require(payload.count(b"\n") == 1, "checkpoint metadata must be one JSON line")
    value = parse_strict_json(payload, "checkpoint metadata")
    require(isinstance(value, dict), "checkpoint metadata root is not an object")
    require_exact_keys(value, ("global_step", "epoch"), "checkpoint metadata")
    require(
        type(value["global_step"]) is int and value["global_step"] == 1,
        f"trainer checkpoint global_step is {value['global_step']!r}, expected 1",
    )
    require(type(value["epoch"]) is int and value["epoch"] == 0, "checkpoint epoch changed")
    return value


def run_training(
    config: GateConfig,
    archive: Archive,
    train_manifest: Path,
    validation_manifest: Path,
) -> tuple[Path, Mapping[str, Any]]:
    trainer_output = archive.private_path("trainer")
    trainer_output.mkdir(exist_ok=False)
    command = training_command(config, train_manifest, validation_manifest, trainer_output)
    result = archive.run(
        "trainer one update",
        command,
        cwd=config.trainer.root,
        timeout=config.timeout_seconds,
    )
    combined = (result.stdout + result.stderr).decode("utf-8", errors="replace")
    require("Using batch size 96" in combined, "trainer did not use batch size 96")
    require("Random fen skipping: 0" in combined, "trainer did not disable random skipping")
    require(
        "bypassed for Atomic BIN V2 train and validation" in combined,
        "trainer did not report V2 smart-filter bypass for both datasets",
    )
    checkpoints = tuple(trainer_output.rglob("last.ckpt"))
    require(
        len(checkpoints) == 1,
        f"trainer must produce exactly one last.ckpt, found {len(checkpoints)}",
    )
    checkpoint = require_regular_file(checkpoints[0], "trainer last checkpoint")

    metadata_probe = (
        "import json,sys,torch;"
        "c=torch.load(sys.argv[1],map_location='cpu',weights_only=False);"
        + python_json_line_statement(
            "{'global_step':c.get('global_step'),'epoch':c.get('epoch')}"
        )
    )
    metadata_result = archive.run(
        "checkpoint metadata",
        (str(config.python), "-B", "-c", metadata_probe, str(checkpoint)),
        cwd=config.trainer.root,
        timeout=config.timeout_seconds,
    )
    metadata_bytes = require_clean_success(metadata_result, "checkpoint metadata")
    metadata = parse_checkpoint_metadata(metadata_bytes)
    return checkpoint, metadata


def verify_nnue_header(path: Path) -> tuple[int, int]:
    payload = require_regular_file(path, "serialized NNUE").read_bytes()
    require(len(payload) >= 12, "serialized NNUE is truncated")
    version, architecture = struct.unpack_from("<II", payload, 0)
    require(version == LEGACY_NNUE_VERSION, f"NNUE version is 0x{version:08X}")
    require(
        architecture == LEGACY_NNUE_ARCHITECTURE,
        f"NNUE architecture is 0x{architecture:08X}",
    )
    return version, architecture


def serialize_and_reimport(
    config: GateConfig,
    archive: Archive,
    checkpoint: Path,
) -> tuple[Path, str]:
    network_dir = archive.path("networks")
    network_dir.mkdir(exist_ok=False)
    candidate = network_dir / "candidate.nnue"
    imported = network_dir / "reimported.pt"
    roundtrip = network_dir / "roundtrip.nnue"
    description = "Atomic-Stockfish Atomic BIN V2 H7 pipeline E2E"

    archive.run(
        "serialize checkpoint",
        (
            str(config.python),
            "-B",
            str(config.serialize_script),
            str(checkpoint),
            str(candidate),
            "--features=HalfKAv2^",
            f"--description={description}",
        ),
        cwd=config.trainer.root,
        timeout=config.timeout_seconds,
    )
    verify_nnue_header(candidate)
    archive.run(
        "reimport candidate",
        (
            str(config.python),
            "-B",
            str(config.serialize_script),
            str(candidate),
            str(imported),
            "--features=HalfKAv2",
        ),
        cwd=config.trainer.root,
        timeout=config.timeout_seconds,
    )
    archive.run(
        "reserialize candidate",
        (
            str(config.python),
            "-B",
            str(config.serialize_script),
            str(imported),
            str(roundtrip),
            f"--description={description}",
        ),
        cwd=config.trainer.root,
        timeout=config.timeout_seconds,
    )
    candidate_bytes = candidate.read_bytes()
    roundtrip_bytes = roundtrip.read_bytes()
    require(candidate_bytes == roundtrip_bytes, "NNUE reimport/reserialization is not byte-exact")
    return candidate, hashlib.sha256(candidate_bytes).hexdigest()


def load_candidate_in_engine(config: GateConfig, archive: Archive, candidate: Path) -> str:
    resolved = candidate.resolve()
    transcript: list[str] = []
    with UciProcess(config.engine, timeout=min(config.timeout_seconds, 120.0)) as uci:
        uci.send("uci")
        uci.read_until(lambda line: line == "uciok")
        for name, value in (
            ("UCI_Variant", "atomic"),
            ("UCI_Chess960", "false"),
            ("Threads", "1"),
            ("Hash", "16"),
            ("EvalFile", str(resolved)),
            ("Use NNUE", "true"),
        ):
            uci.send(f"setoption name {name} value {value}")
        uci.send("isready")
        ready = uci.read_until(lambda line: line == "readyok")
        require(
            not any("error" in line.lower() for line in ready),
            "engine rejected candidate NNUE",
        )
        uci.send("position startpos")
        uci.send("eval")
        evaluation = uci.read_until(lambda line: line.startswith("Final evaluation"))
        load_markers = [
            line
            for line in evaluation
            if line.startswith("info string NNUE evaluation using ")
        ]
        require(len(load_markers) == 1, "engine did not report exactly one NNUE load marker")
        require(str(resolved) in load_markers[0], "engine loaded a different NNUE path")
        require(
            not any("ERROR" in line.upper() for line in evaluation),
            "engine eval reported error",
        )
        uci.send("go nodes 1")
        search = uci.read_until(lambda line: line.startswith("bestmove "))
        require(not any("ERROR" in line.upper() for line in search), "engine search reported error")
        bestmove = search[-1].split()
        require(
            len(bestmove) >= 2 and bestmove[1] not in {"0000", "(none)"},
            "go nodes 1 returned no move",
        )
        transcript = list(uci.transcript)
    archive.write_log(
        "logs/engine-candidate-uci.log",
        ("\n".join(transcript) + "\n").encode("utf-8"),
    )
    return bestmove[1]


def python_environment(config: GateConfig, archive: Archive) -> Mapping[str, object]:
    probe = (
        "import json,platform,sys,numpy,torch,pytorch_lightning;"
        + python_json_line_statement(
            "{'python':platform.python_version(),"
            "'implementation':platform.python_implementation(),"
            "'platform':platform.platform(),"
            "'numpy':numpy.__version__,'torch':torch.__version__,"
            "'pytorch_lightning':pytorch_lightning.__version__,"
            "'executable':sys.executable}"
        )
    )
    result = archive.run(
        "python environment",
        (str(config.python), "-B", "-c", probe),
        cwd=config.trainer.root,
        timeout=config.timeout_seconds,
    )
    payload = require_clean_success(result, "Python environment")
    require(payload.count(b"\n") == 1, "Python environment emitted multiple lines")
    value = parse_strict_json(payload, "Python environment")
    require(isinstance(value, dict), "Python environment root is not an object")
    require(
        Path(str(value.get("executable", ""))).resolve() == config.python.resolve(),
        "Python executable drifted",
    )
    return value


def fingerprints_equal(before: Preflight, after: Preflight) -> None:
    require(before.checkouts == after.checkouts, "checkout state changed during the gate")
    require(before.fingerprints == after.fingerprints, "input artifact changed during the gate")


def inventory_archive(archive: Archive) -> Mapping[str, object]:
    entries: dict[str, object] = {}
    for path in sorted(archive.root.rglob("*")):
        if path.is_dir():
            continue
        require(not path.is_symlink(), f"archive contains a symlink: {path}")
        relative = path.relative_to(archive.root).as_posix()
        require(relative != "hashes.json", "hashes.json already exists")
        entries[relative] = {
            "bytes": path.stat().st_size,
            "sha256": sha256_file(path),
        }
    return {"schema_version": 1, "files": entries}


def verify_public_text_redaction(archive: Archive) -> None:
    windows_absolute = re.compile(r"(?:^|[\"'\s])(?:[A-Za-z]:[\\/]|\\\\)")
    posix_private = re.compile(r"(?:^|[\"'\s])/(?:home|Users|tmp|private|var/tmp)/")
    for path in sorted(archive.root.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in {
            ".json",
            ".log",
            ".jsonl",
            ".txt",
        }:
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="strict")
        except UnicodeError as error:
            raise GateError(f"public text evidence is not UTF-8: {path.name}") from error
        folded = text.casefold()
        for source, unused_token in archive.redactions:
            if source.casefold() in folded:
                raise GateError(f"public evidence retains a host-local path: {path.name}")
        require(
            windows_absolute.search(text) is None and posix_private.search(text) is None,
            f"public evidence contains an unredacted absolute path: {path.name}",
        )


def run_gate(config: GateConfig) -> Mapping[str, object]:
    before = preflight(config)
    archive = Archive(config.output_dir, archive_redactions(config))
    try:
        capabilities = verify_capabilities(config, archive)
        environment = python_environment(config, archive)
        train_manifest, train_json, train_shards, train_hashes = generate_dataset(
            config,
            archive,
            label="train",
            seed=config.train_seed,
        )
        (
            validation_manifest,
            validation_json,
            validation_shards,
            validation_hashes,
        ) = generate_dataset(
            config, archive, label="validation", seed=config.validation_seed
        )
        require(
            set(train_hashes).isdisjoint(validation_hashes),
            "training and validation datasets share shard bytes",
        )
        train_validation, train_decode_hash = validate_and_decode(
            config,
            archive,
            label="train",
            manifest_path=train_manifest,
            manifest=train_json,
        )
        validation_validation, validation_decode_hash = validate_and_decode(
            config,
            archive,
            label="validation",
            manifest_path=validation_manifest,
            manifest=validation_json,
        )
        checkpoint, checkpoint_metadata = run_training(
            config,
            archive,
            train_manifest,
            validation_manifest,
        )
        checkpoint_evidence = {
            "sha256": sha256_file(checkpoint),
            "bytes": checkpoint.stat().st_size,
            **checkpoint_metadata,
            "retained": False,
            "retention_reason": "private trainer artifact may contain host-local paths",
        }
        candidate, candidate_hash = serialize_and_reimport(config, archive, checkpoint)
        archive.cleanup_private()
        bestmove = load_candidate_in_engine(config, archive, candidate)

        after = preflight(config, allow_output=True)
        fingerprints_equal(before, after)
        datasets = {
            "train": asdict(
                DatasetResult(
                    "train",
                    train_manifest,
                    sha256_file(train_manifest),
                    train_shards,
                    train_hashes,
                    train_validation,
                    train_decode_hash,
                )
            ),
            "validation": asdict(
                DatasetResult(
                    "validation",
                    validation_manifest,
                    sha256_file(validation_manifest),
                    validation_shards,
                    validation_hashes,
                    validation_validation,
                    validation_decode_hash,
                )
            ),
        }
        for dataset in datasets.values():
            dataset["manifest"] = Path(dataset["manifest"]).relative_to(archive.root).as_posix()
            dataset["shard_paths"] = [
                Path(path).relative_to(archive.root).as_posix()
                for path in dataset["shard_paths"]
            ]
        provenance = {
            "schema_version": 1,
            "contract": {
                "records_per_dataset": RECORDS,
                "records_per_shard": RECORDS_PER_SHARD,
                "shards_per_dataset": SHARDS,
                "batch_size": BATCH_SIZE,
                "epoch_size": EPOCH_SIZE,
                "validation_size": VALIDATION_SIZE,
                "generator_depth": GENERATOR_DEPTH,
                "eval_limit": GENERATOR_EVAL_LIMIT,
                "filter_captures": True,
                "filter_checks": False,
                "filter_promotions": True,
                "generator_use_nnue": "pure",
                "engine_use_nnue": "true",
            },
            "schemas": {
                "data": DATA_SCHEMA_SHA256,
                "manifest": MANIFEST_SCHEMA_SHA256,
                "decode": DECODE_SCHEMA_SHA256,
            },
            "contract_engine_commit": config.contract_engine_commit,
            "checkouts": {name: asdict(state) for name, state in before.checkouts.items()},
            "inputs": {name: asdict(item) for name, item in before.fingerprints.items()},
            "seeds": {"train": str(config.train_seed), "validation": str(config.validation_seed)},
            "capabilities": capabilities,
            "python_environment": environment,
            "datasets": datasets,
            "checkpoint": checkpoint_evidence,
            "candidate": {
                "path": candidate.relative_to(archive.root).as_posix(),
                "sha256": candidate_hash,
                "version": f"0x{LEGACY_NNUE_VERSION:08X}",
                "architecture": f"0x{LEGACY_NNUE_ARCHITECTURE:08X}",
            },
            "engine": {"bestmove_nodes_1": bestmove},
        }
        archive.write_json("provenance.json", provenance)
        result = {
            "schema_version": 1,
            "status": "passed",
            "atomic_commit": config.atomic.commit,
            "tools_commit": config.tools.commit,
            "trainer_commit": config.trainer.commit,
            "contract_engine_commit": config.contract_engine_commit,
            "train_manifest_sha256": datasets["train"]["manifest_sha256"],
            "validation_manifest_sha256": datasets["validation"]["manifest_sha256"],
            "candidate_sha256": candidate_hash,
            "global_step": checkpoint_metadata["global_step"],
            "bestmove_nodes_1": bestmove,
        }
        archive.write_json("result.json", result)
        verify_public_text_redaction(archive)
        archive.write_json("hashes.json", inventory_archive(archive))
        return result
    except BaseException as error:
        failure = {
            "schema_version": 1,
            "status": "failed",
            "error_type": type(error).__name__,
            "message": str(error),
            "traceback": traceback.format_exc(),
        }
        try:
            archive.cleanup_private()
        except BaseException:
            pass
        try:
            archive.write_json("failure.json", failure)
        except BaseException:
            pass
        raise


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Run the final, fail-closed H7 Atomic BIN V2 generate/train/load gate. "
            "The output directory must not exist and its absolute path cannot contain whitespace."
        )
    )
    checkout = parser.add_argument_group("authenticated checkouts")
    for label in ("atomic", "tools", "trainer"):
        checkout.add_argument(
            f"--{label}-root",
            type=Path,
            required=True,
            help=f"clean {label} Git toplevel",
        )
        checkout.add_argument(
            f"--{label}-commit",
            required=True,
            help=f"exact lowercase 40-character {label} commit",
        )
        checkout.add_argument(
            f"--{label}-ref",
            required=True,
            help=f"Git ref that must resolve to --{label}-commit",
        )
    checkout.add_argument(
        "--contract-engine-commit",
        required=True,
        help="exact Atomic commit pinned by both tools and trainer submodules",
    )

    artifacts = parser.add_argument_group("authenticated executable artifacts")
    for option, description in (
        ("engine", "current Atomic playing engine"),
        ("data-generator", "current Atomic data-generator build"),
        ("data-tools", "current Atomic direct data-tools build"),
        ("wrapper-data-tools", "data-tools build used by the pinned tools wrapper"),
        ("trainer-loader", "trainer native loader imported by nnue_dataset"),
    ):
        artifacts.add_argument(f"--{option}", type=Path, required=True, help=description)
        artifacts.add_argument(
            f"--{option}-sha256",
            required=True,
            help=f"expected SHA-256 for --{option}",
        )
    artifacts.add_argument(
        "--tools-wrapper",
        type=Path,
        required=True,
        help="canonical tools script/atomic_bin_v2_tools.py",
    )
    artifacts.add_argument(
        "--train-script",
        type=Path,
        required=True,
        help="canonical trainer train.py",
    )
    artifacts.add_argument(
        "--serialize-script",
        type=Path,
        required=True,
        help="canonical trainer serialize.py",
    )
    artifacts.add_argument("--python", type=Path, required=True, help="exact Python executable")
    artifacts.add_argument(
        "--python-sha256",
        required=True,
        help="expected SHA-256 for --python",
    )
    artifacts.add_argument(
        "--source-net",
        type=Path,
        required=True,
        help="compatible legacy HalfKAv2 source network; its filename is not interpreted",
    )
    artifacts.add_argument(
        "--source-net-sha256",
        required=True,
        help="expected SHA-256 for --source-net",
    )

    execution = parser.add_argument_group("gate execution")
    execution.add_argument(
        "--output-dir",
        type=Path,
        required=True,
        help="new audit archive outside all three checkouts, with no whitespace in its path",
    )
    execution.add_argument("--train-seed", type=parse_uint64, required=True)
    execution.add_argument("--validation-seed", type=parse_uint64, required=True)
    execution.add_argument(
        "--timeout-seconds",
        type=parse_positive_float,
        default=DEFAULT_TIMEOUT_SECONDS,
        help=f"per-command timeout (default: {DEFAULT_TIMEOUT_SECONDS:g})",
    )
    return parser


def config_from_args(arguments: argparse.Namespace) -> GateConfig:
    def resolved(name: str) -> Path:
        return Path(getattr(arguments, name)).expanduser().resolve()

    atomic_commit = require_sha1(arguments.atomic_commit, "Atomic commit")
    tools_commit = require_sha1(arguments.tools_commit, "tools commit")
    trainer_commit = require_sha1(arguments.trainer_commit, "trainer commit")
    contract_commit = require_sha1(arguments.contract_engine_commit, "contract engine commit")
    hashes = {
        name: require_sha256(getattr(arguments, name), name.replace("_", " "))
        for name in (
            "engine_sha256",
            "data_generator_sha256",
            "data_tools_sha256",
            "wrapper_data_tools_sha256",
            "trainer_loader_sha256",
            "python_sha256",
            "source_net_sha256",
        )
    }
    require(
        arguments.train_seed != arguments.validation_seed,
        "train and validation seeds must differ",
    )
    return GateConfig(
        atomic=CheckoutSpec(
            "Atomic",
            resolved("atomic_root"),
            atomic_commit,
            arguments.atomic_ref,
            ATOMIC_REPOSITORY,
        ),
        tools=CheckoutSpec(
            "tools",
            resolved("tools_root"),
            tools_commit,
            arguments.tools_ref,
            TOOLS_REPOSITORY,
        ),
        trainer=CheckoutSpec(
            "trainer",
            resolved("trainer_root"),
            trainer_commit,
            arguments.trainer_ref,
            TRAINER_REPOSITORY,
        ),
        contract_engine_commit=contract_commit,
        engine=resolved("engine"),
        engine_sha256=hashes["engine_sha256"],
        data_generator=resolved("data_generator"),
        data_generator_sha256=hashes["data_generator_sha256"],
        data_tools=resolved("data_tools"),
        data_tools_sha256=hashes["data_tools_sha256"],
        tools_wrapper=resolved("tools_wrapper"),
        wrapper_data_tools=resolved("wrapper_data_tools"),
        wrapper_data_tools_sha256=hashes["wrapper_data_tools_sha256"],
        trainer_loader=resolved("trainer_loader"),
        trainer_loader_sha256=hashes["trainer_loader_sha256"],
        train_script=resolved("train_script"),
        serialize_script=resolved("serialize_script"),
        python=resolved("python"),
        python_sha256=hashes["python_sha256"],
        source_net=resolved("source_net"),
        source_net_sha256=hashes["source_net_sha256"],
        output_dir=resolved("output_dir"),
        train_seed=arguments.train_seed,
        validation_seed=arguments.validation_seed,
        timeout_seconds=arguments.timeout_seconds,
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    arguments = parser.parse_args(argv)
    try:
        config = config_from_args(arguments)
        result = run_gate(config)
    except GateError as error:
        print(f"H7 FINAL E2E FAILED: {error}", file=sys.stderr, flush=True)
        return 1
    print(canonical_json(result).decode("utf-8"), end="", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
