#!/usr/bin/env python3
"""Validate Legacy Atomic V1 repository pins and clean release checkouts."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
from pathlib import Path
import re
import subprocess
from typing import Mapping, Sequence

from atomic_training_data_schema import (
    EXPECTED_RECORD_SIZE as EXPECTED_TRAINING_DATA_RECORD_SIZE,
    EXPECTED_SCHEMA_ID as EXPECTED_TRAINING_DATA_SCHEMA_ID,
    load_training_data_schema,
)


DEFAULT_LOCK_FILE = Path(__file__).with_name("legacy_pipeline.lock.json")
ZERO_COMMIT = "0" * 40
ZERO_SHA256 = "0" * 64
# Temporary, deliberately impossible-to-accept release fixture.  The first
# strong-local run prints the measured eight-record Atomic generator SHA-256;
# replace this sentinel in the checked-in lock before the release gate can pass.
PENDING_STRONG_ATOMIC_DATA_SHA256 = "f" * 64
COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
EXPECTED_REPOSITORIES = {
    "tools": "Belzedar94/variant-nnue-tools",
    "trainer": "Belzedar94/variant-nnue-pytorch",
}
EXPECTED_PROFILE_KINDS = {
    "strong-local": "external",
    "synthetic-ci": "trainer-generated-zero",
}
EXPECTED_BUILD_RECIPES = {
    "strong-local": {
        "tools": "strong-local-tools-windows-v1",
        "trainer": "strong-local-trainer-windows-v1",
        "atomic": "strong-local-atomic-windows-v2",
    },
    "synthetic-ci": {
        "tools": "synthetic-ci-tools-linux-v1",
        "trainer": "synthetic-ci-trainer-linux-v1",
        "atomic": "synthetic-ci-atomic-linux-v2",
    },
}
EXPECTED_TRAINING_DATA_SCHEMA_PATH = "schemas/atomic-schema.json"


@dataclass(frozen=True)
class RepositoryPin:
    name: str
    repository: str
    commit: str
    resolved: bool


@dataclass(frozen=True)
class PipelineProfile:
    name: str
    source_kind: str
    records: int
    seed: str
    source_net_sha256: str
    data_sha256: str
    atomic_data_sha256: str
    hashes_resolved: bool
    build_recipes: Mapping[str, str]
    synthetic_model_seed: int | None = None


@dataclass(frozen=True)
class TrainingDataSchemaPin:
    path: str
    schema_id: str
    sha256: str
    record_size: int


@dataclass(frozen=True)
class PipelineLock:
    training_data_schema: TrainingDataSchemaPin
    repositories: Mapping[str, RepositoryPin]
    profiles: Mapping[str, PipelineProfile]


@dataclass(frozen=True)
class CheckoutState:
    root: Path
    head: str


def _require_mapping(value: object, label: str) -> Mapping[str, object]:
    if not isinstance(value, dict):
        raise AssertionError(f"{label} must be a JSON object")
    return value


def _exact_keys(value: Mapping[str, object], expected: set[str], label: str) -> None:
    if set(value) != expected:
        raise AssertionError(
            f"{label} keys must be exactly {sorted(expected)}, got {sorted(value)}"
        )


class _DuplicateJsonKeyError(ValueError):
    pass


def _strict_json_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateJsonKeyError(f"duplicate JSON key {key!r}")
        result[key] = value
    return result


def load_json_file(path: Path, label: str) -> object:
    resolved_path = path.expanduser().resolve()
    try:
        return json.loads(
            resolved_path.read_text(encoding="utf-8"),
            object_pairs_hook=_strict_json_object,
        )
    except (OSError, json.JSONDecodeError, _DuplicateJsonKeyError) as error:
        raise AssertionError(
            f"cannot read {label} {resolved_path}: {error}"
        ) from error


def _require_bool(value: object, label: str) -> bool:
    if not isinstance(value, bool):
        raise AssertionError(f"{label} must be a JSON boolean")
    return value


def _require_string(value: object, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise AssertionError(f"{label} must be a non-empty JSON string")
    return value


def _parse_training_data_schema(raw: object) -> TrainingDataSchemaPin:
    entry = _require_mapping(raw, "training_data_schema")
    _exact_keys(
        entry,
        {"path", "schema_id", "sha256", "record_size"},
        "training_data_schema",
    )
    path = _require_string(entry.get("path"), "training_data_schema.path")
    if path != EXPECTED_TRAINING_DATA_SCHEMA_PATH:
        raise AssertionError(
            "training_data_schema.path must be exactly "
            f"{EXPECTED_TRAINING_DATA_SCHEMA_PATH!r}"
        )
    schema_id = _require_string(
        entry.get("schema_id"), "training_data_schema.schema_id"
    )
    if schema_id != EXPECTED_TRAINING_DATA_SCHEMA_ID:
        raise AssertionError(
            "training_data_schema.schema_id must be exactly "
            f"{EXPECTED_TRAINING_DATA_SCHEMA_ID!r}"
        )
    sha256 = _require_string(entry.get("sha256"), "training_data_schema.sha256")
    if not SHA256_RE.fullmatch(sha256):
        raise AssertionError(
            "training_data_schema.sha256 must be a lowercase SHA-256"
        )
    record_size = entry.get("record_size")
    if type(record_size) is not int or record_size != EXPECTED_TRAINING_DATA_RECORD_SIZE:
        raise AssertionError(
            "training_data_schema.record_size must be exactly "
            f"{EXPECTED_TRAINING_DATA_RECORD_SIZE}"
        )
    actual = load_training_data_schema()
    if actual.schema_id != schema_id:
        raise AssertionError(
            "training-data schema ID mismatch: "
            f"lock has {schema_id!r}, file has {actual.schema_id!r}"
        )
    if actual.sha256 != sha256:
        raise AssertionError(
            "training-data schema SHA-256 mismatch: "
            f"lock has {sha256}, file has {actual.sha256}"
        )
    if actual.record_size != record_size:
        raise AssertionError(
            "training-data schema record size mismatch: "
            f"lock has {record_size}, file has {actual.record_size}"
        )
    return TrainingDataSchemaPin(
        path=path,
        schema_id=schema_id,
        sha256=sha256,
        record_size=record_size,
    )


def _parse_repository_pin(
    name: str, raw: object, *, allow_placeholders: bool
) -> RepositoryPin:
    entry = _require_mapping(raw, f"repositories.{name}")
    repository = _require_string(
        entry.get("repository"), f"repositories.{name}.repository"
    )
    expected_repository = EXPECTED_REPOSITORIES[name]
    if repository != expected_repository:
        raise AssertionError(
            f"repositories.{name}.repository must be {expected_repository!r}, "
            f"got {repository!r}"
        )
    commit = _require_string(entry.get("commit"), f"repositories.{name}.commit")
    if not COMMIT_RE.fullmatch(commit):
        raise AssertionError(
            f"repositories.{name}.commit must be a lowercase full 40-character SHA"
        )
    resolved = _require_bool(
        entry.get("resolved"), f"repositories.{name}.resolved"
    )
    expected_keys = {"repository", "commit", "resolved"}
    if not resolved:
        expected_keys.add("placeholder")
    _exact_keys(entry, expected_keys, f"repositories.{name}")
    if resolved == (commit == ZERO_COMMIT):
        raise AssertionError(
            f"repositories.{name} has inconsistent commit/resolved fields"
        )
    if not resolved:
        placeholder = _require_string(
            entry.get("placeholder"), f"repositories.{name}.placeholder"
        )
        if not placeholder.startswith("REPLACE_WITH_"):
            raise AssertionError(
                f"repositories.{name}.placeholder is not explicit: {placeholder!r}"
            )
        if not allow_placeholders:
            raise AssertionError(
                f"repositories.{name} is unresolved ({placeholder}); "
                "release CI requires a full immutable commit"
            )
    elif "placeholder" in entry:
        raise AssertionError(
            f"repositories.{name}.placeholder must be removed once resolved"
        )
    return RepositoryPin(name, repository, commit, resolved)


def _parse_profile(
    name: str, raw: object, *, allow_placeholders: bool
) -> PipelineProfile:
    entry = _require_mapping(raw, f"profiles.{name}")
    source_kind = _require_string(
        entry.get("source_kind"), f"profiles.{name}.source_kind"
    )
    if source_kind != EXPECTED_PROFILE_KINDS[name]:
        raise AssertionError(
            f"profiles.{name}.source_kind must be "
            f"{EXPECTED_PROFILE_KINDS[name]!r}"
        )
    records = entry.get("records")
    if isinstance(records, bool) or not isinstance(records, int) or records <= 0:
        raise AssertionError(f"profiles.{name}.records must be a positive integer")
    seed = _require_string(entry.get("seed"), f"profiles.{name}.seed")
    if any(character.isspace() for character in seed):
        raise AssertionError(f"profiles.{name}.seed cannot contain whitespace")
    source_hash = _require_string(
        entry.get("source_net_sha256"),
        f"profiles.{name}.source_net_sha256",
    )
    data_hash = _require_string(
        entry.get("data_sha256"), f"profiles.{name}.data_sha256"
    )
    atomic_data_hash = _require_string(
        entry.get("atomic_data_sha256"),
        f"profiles.{name}.atomic_data_sha256",
    )
    for label, value in (
        ("source_net_sha256", source_hash),
        ("data_sha256", data_hash),
        ("atomic_data_sha256", atomic_data_hash),
    ):
        if not SHA256_RE.fullmatch(value):
            raise AssertionError(
                f"profiles.{name}.{label} must be a lowercase SHA-256"
            )
    hashes_resolved = _require_bool(
        entry.get("hashes_resolved"), f"profiles.{name}.hashes_resolved"
    )
    expected_keys = {
        "source_kind",
        "records",
        "seed",
        "source_net_sha256",
        "data_sha256",
        "atomic_data_sha256",
        "hashes_resolved",
        "build_recipes",
    }
    if source_kind == "trainer-generated-zero":
        expected_keys.add("synthetic_model_seed")
    if not hashes_resolved:
        expected_keys.add("placeholder")
    _exact_keys(entry, expected_keys, f"profiles.{name}")
    if source_hash == ZERO_SHA256 or data_hash == ZERO_SHA256:
        raise AssertionError(
            f"profiles.{name} source_net_sha256 and data_sha256 must remain "
            "measured while bootstrapping atomic_data_sha256"
        )
    if (
        atomic_data_hash == PENDING_STRONG_ATOMIC_DATA_SHA256
        and name != "strong-local"
    ):
        raise AssertionError(
            "the pending strong Atomic data sentinel is valid only for "
            "profiles.strong-local.atomic_data_sha256"
        )
    if hashes_resolved and atomic_data_hash == ZERO_SHA256:
        raise AssertionError(
            f"profiles.{name} is resolved but contains an all-zero hash"
        )
    if not hashes_resolved and atomic_data_hash != ZERO_SHA256:
        raise AssertionError(
            f"profiles.{name} is unresolved but atomic_data_sha256 is not all-zero"
        )
    if not hashes_resolved:
        placeholder = _require_string(
            entry.get("placeholder"), f"profiles.{name}.placeholder"
        )
        if not placeholder.startswith("REPLACE_WITH_"):
            raise AssertionError(
                f"profiles.{name}.placeholder is not explicit: {placeholder!r}"
            )
        if not allow_placeholders:
            raise AssertionError(
                f"profiles.{name} hashes are unresolved ({placeholder}); "
                "release CI requires measured immutable hashes"
            )
    elif "placeholder" in entry:
        raise AssertionError(
            f"profiles.{name}.placeholder must be removed once resolved"
        )
    raw_recipes = _require_mapping(
        entry.get("build_recipes"), f"profiles.{name}.build_recipes"
    )
    expected_recipes = EXPECTED_BUILD_RECIPES[name]
    if raw_recipes != expected_recipes:
        raise AssertionError(
            f"profiles.{name}.build_recipes must be exactly {expected_recipes}"
        )
    synthetic_seed = entry.get("synthetic_model_seed")
    if source_kind == "trainer-generated-zero":
        if (
            isinstance(synthetic_seed, bool)
            or not isinstance(synthetic_seed, int)
            or synthetic_seed < 0
            or synthetic_seed >= 2**64
        ):
            raise AssertionError(
                f"profiles.{name}.synthetic_model_seed must be an unsigned "
                "64-bit integer"
            )
    elif synthetic_seed is not None:
        raise AssertionError(
            f"profiles.{name}.synthetic_model_seed is only valid for a "
            "trainer-generated source"
        )
    return PipelineProfile(
        name=name,
        source_kind=source_kind,
        records=records,
        seed=seed,
        source_net_sha256=source_hash,
        data_sha256=data_hash,
        atomic_data_sha256=atomic_data_hash,
        hashes_resolved=hashes_resolved,
        build_recipes=dict(expected_recipes),
        synthetic_model_seed=synthetic_seed,
    )


def load_pipeline_lock(
    path: Path = DEFAULT_LOCK_FILE, *, allow_placeholders: bool = False
) -> PipelineLock:
    resolved_path = path.expanduser().resolve()
    document = load_json_file(resolved_path, "pipeline lock")
    root = _require_mapping(document, "pipeline lock")
    _exact_keys(
        root,
        {"schema_version", "training_data_schema", "repositories", "profiles"},
        "pipeline lock",
    )
    schema_version = root.get("schema_version")
    if type(schema_version) is not int or schema_version != 3:
        raise AssertionError("pipeline lock schema_version must be exactly 3")
    training_data_schema = _parse_training_data_schema(
        root.get("training_data_schema")
    )
    raw_repositories = _require_mapping(root.get("repositories"), "repositories")
    raw_profiles = _require_mapping(root.get("profiles"), "profiles")
    if set(raw_repositories) != set(EXPECTED_REPOSITORIES):
        raise AssertionError(
            "pipeline lock repositories must be exactly tools and trainer"
        )
    if set(raw_profiles) != set(EXPECTED_PROFILE_KINDS):
        raise AssertionError(
            "pipeline lock profiles must be exactly strong-local and synthetic-ci"
        )
    repositories = {
        name: _parse_repository_pin(
            name, raw_repositories[name], allow_placeholders=allow_placeholders
        )
        for name in EXPECTED_REPOSITORIES
    }
    profiles = {
        name: _parse_profile(
            name, raw_profiles[name], allow_placeholders=allow_placeholders
        )
        for name in EXPECTED_PROFILE_KINDS
    }
    return PipelineLock(
        training_data_schema=training_data_schema,
        repositories=repositories,
        profiles=profiles,
    )


def _run_git(root: Path, *arguments: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(root), *arguments],
        text=True,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if result.returncode != 0:
        raise AssertionError(
            f"git {' '.join(arguments)} failed in {root}:\n{result.stderr}"
        )
    return result.stdout.strip()


def require_path_within(path: Path, root: Path, label: str) -> Path:
    resolved_path = path.expanduser().resolve()
    resolved_root = root.expanduser().resolve()
    if not resolved_path.is_relative_to(resolved_root):
        raise AssertionError(
            f"{label} {resolved_path} is outside its pinned checkout {resolved_root}"
        )
    return resolved_path


def require_file_within(path: Path, root: Path, label: str) -> Path:
    resolved_path = require_path_within(path, root, label)
    if not resolved_path.is_file():
        raise AssertionError(f"{label} is not an existing file: {resolved_path}")
    return resolved_path


def find_checkout_root(path: Path, label: str) -> Path:
    resolved = path.expanduser().resolve()
    anchor = resolved if resolved.is_dir() else resolved.parent
    output = _run_git(anchor, "rev-parse", "--show-toplevel")
    root = Path(output).resolve()
    require_path_within(resolved, root, label)
    return root


def enforce_clean_checkout(
    root: Path, label: str, expected_commit: str | None = None
) -> CheckoutState:
    requested_root = root.expanduser().resolve()
    actual_root = Path(
        _run_git(requested_root, "rev-parse", "--show-toplevel")
    ).resolve()
    if actual_root != requested_root:
        raise AssertionError(
            f"{label} checkout root mismatch: requested {requested_root}, "
            f"git reports {actual_root}"
        )
    head = _run_git(actual_root, "rev-parse", "HEAD")
    if not COMMIT_RE.fullmatch(head):
        raise AssertionError(f"{label} HEAD is not a full commit SHA: {head!r}")
    if expected_commit is not None and head != expected_commit:
        raise AssertionError(
            f"{label} HEAD mismatch: expected {expected_commit}, got {head}"
        )
    dirty = _run_git(
        actual_root,
        "status",
        "--porcelain=v1",
        "--untracked-files=all",
        "--ignore-submodules=none",
    )
    if dirty:
        raise AssertionError(f"{label} checkout is not clean:\n{dirty}")
    return CheckoutState(root=actual_root, head=head)


def verify_release_checkouts(
    lock: PipelineLock,
    *,
    tools_root: Path,
    trainer_root: Path,
    atomic_root: Path,
    tools_engine: Path | None = None,
    engine: Path | None = None,
    atomic_commit: str | None = None,
    allow_unresolved_hashes: bool = False,
) -> Mapping[str, CheckoutState]:
    _require_resolution_policy(
        lock,
        operation="verify-checkouts",
        allow_unresolved_hashes=allow_unresolved_hashes,
    )
    tools_pin = lock.repositories["tools"]
    trainer_pin = lock.repositories["trainer"]
    if tools_engine is not None:
        require_file_within(tools_engine, tools_root, "tools engine")
    if engine is not None:
        require_file_within(engine, atomic_root, "Atomic-Stockfish engine")
    return {
        "tools": enforce_clean_checkout(
            tools_root, "variant-nnue-tools", tools_pin.commit
        ),
        "trainer": enforce_clean_checkout(
            trainer_root, "variant-nnue-pytorch", trainer_pin.commit
        ),
        "atomic": enforce_clean_checkout(
            atomic_root, "Atomic-Stockfish", atomic_commit
        ),
    }


def _require_resolution_policy(
    lock: PipelineLock,
    *,
    operation: str,
    allow_unresolved_hashes: bool = False,
) -> bool:
    """Enforce the single narrow bootstrap exception used before measurement."""
    unresolved_repositories = [
        name for name, pin in lock.repositories.items() if not pin.resolved
    ]
    if unresolved_repositories:
        raise AssertionError(
            f"{operation} requires resolved repository pins: "
            + ", ".join(sorted(unresolved_repositories))
        )
    unresolved_non_synthetic_profiles = [
        name
        for name, profile in lock.profiles.items()
        if name != "synthetic-ci" and not profile.hashes_resolved
    ]
    if unresolved_non_synthetic_profiles:
        raise AssertionError(
            f"{operation} only permits unresolved synthetic-ci hashes; "
            "unresolved profiles: "
            + ", ".join(sorted(unresolved_non_synthetic_profiles))
        )
    synthetic_hashes_resolved = lock.profiles["synthetic-ci"].hashes_resolved
    if not synthetic_hashes_resolved and not allow_unresolved_hashes:
        raise AssertionError(
            "synthetic-ci hashes are unresolved; pass "
            "--allow-unresolved-hashes only for the measurement bootstrap"
        )
    return synthetic_hashes_resolved


def _write_github_outputs(
    lock: PipelineLock,
    path: Path,
    *,
    allow_unresolved_hashes: bool = False,
) -> None:
    synthetic_hashes_resolved = _require_resolution_policy(
        lock,
        operation="github-outputs",
        allow_unresolved_hashes=allow_unresolved_hashes,
    )
    lines: list[str] = []
    lines.extend(
        (
            f"training_data_schema_id={lock.training_data_schema.schema_id}",
            f"training_data_schema_sha256={lock.training_data_schema.sha256}",
            f"training_data_record_size={lock.training_data_schema.record_size}",
        )
    )
    for name in ("tools", "trainer"):
        pin = lock.repositories[name]
        lines.extend(
            (
                f"{name}_repository={pin.repository}",
                f"{name}_commit={pin.commit}",
            )
        )
    lines.append(
        "synthetic_hashes_resolved="
        + str(synthetic_hashes_resolved).lower()
    )
    with path.open("a", encoding="utf-8", newline="\n") as output:
        output.write("\n".join(lines) + "\n")


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lock-file", type=Path, default=DEFAULT_LOCK_FILE)
    subparsers = parser.add_subparsers(dest="command", required=True)
    output_parser = subparsers.add_parser(
        "github-outputs", help="emit resolved repository pins for actions/checkout"
    )
    output_parser.add_argument("--output", type=Path, required=True)
    output_parser.add_argument(
        "--allow-unresolved-hashes",
        action="store_true",
        help=(
            "allow only unresolved synthetic-ci fixture hashes so CI can "
            "measure them; repository pins and every other profile remain "
            "fail-closed"
        ),
    )
    verify_parser = subparsers.add_parser(
        "verify-checkouts", help="verify exact HEADs and clean release worktrees"
    )
    verify_parser.add_argument("--tools-root", type=Path, required=True)
    verify_parser.add_argument("--trainer-root", type=Path, required=True)
    verify_parser.add_argument("--atomic-root", type=Path, required=True)
    verify_parser.add_argument("--tools-engine", type=Path)
    verify_parser.add_argument("--engine", type=Path)
    verify_parser.add_argument("--atomic-commit")
    verify_parser.add_argument(
        "--allow-unresolved-hashes",
        action="store_true",
        help=(
            "allow only unresolved synthetic-ci fixture hashes during the "
            "pre-measurement clean-checkout gate; repository pins and every "
            "other profile remain fail-closed"
        ),
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    allow_placeholders = args.allow_unresolved_hashes
    lock = load_pipeline_lock(
        args.lock_file, allow_placeholders=allow_placeholders
    )
    if args.command == "github-outputs":
        _write_github_outputs(
            lock,
            args.output,
            allow_unresolved_hashes=args.allow_unresolved_hashes,
        )
        return 0
    states = verify_release_checkouts(
        lock,
        tools_root=args.tools_root,
        trainer_root=args.trainer_root,
        atomic_root=args.atomic_root,
        tools_engine=args.tools_engine,
        engine=args.engine,
        atomic_commit=args.atomic_commit,
        allow_unresolved_hashes=args.allow_unresolved_hashes,
    )
    print(
        "LEGACY PIPELINE CHECKOUTS VERIFIED "
        + " ".join(f"{name}={state.head}" for name, state in states.items())
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
