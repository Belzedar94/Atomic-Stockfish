#!/usr/bin/env python3
"""Fail-closed structural authentication for an AtomicNNUEV3 dataset bundle.

JSON Schema validates the shape of the V3 sidecars.  It cannot validate sums,
cross-document hashes/identities, decimal uint64 bounds, or binary-layout
formulae expressed by the project's ``x-*`` annotations.  This module owns
those executable checks.  It also authenticates and parses the fixed-size
``.atcov`` files so declared physical reachability and observed coverage are
authenticated from binary evidence rather than trusted from JSON declarations.
It exactly derives HM training/virtual masks from physical HM, but independent
oracle reproduction of the four physical masks remains a publication gate.
Legal replay, terminal semantics and V3 feature-input identities require the
future engine-backed publication audit and are deliberately not claimed here.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import json
import re
import sqlite3
import stat
import struct
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, BinaryIO, Dict, Iterator, List, Mapping, Optional, Sequence, Tuple

from jsonschema.exceptions import SchemaError
from jsonschema.validators import Draft202012Validator


MAX_U64 = (1 << 64) - 1
PPM_SCALE = 1_000_000
SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")
GIT_COMMIT_RE = re.compile(r"[0-9a-f]{40}\Z")
UINT64_RE = re.compile(r"(?:0|[1-9][0-9]*)\Z")
BYTE_POPCOUNT = tuple(bin(value).count("1") for value in range(256))
STREAM_CHUNK_SIZE = 1024 * 1024
MAX_JSON_DOCUMENT_BYTES = 16 * 1024 * 1024
MAX_MANIFEST_BYTES = 64 * 1024 * 1024
MAX_MANIFEST_SHARDS = 100_000
ATOMIC_BIN_V2_HEADER_SIZE = 96
ATOMIC_BIN_V2_RECORD_SIZE = 64
ATOMIC_TRAJECTORY_HEADER_SIZE = 160
ATOMIC_TRAJECTORY_ENTRY_SIZE = 112

SLICE_IDS = (
    "half_ka_v2_atomic_hm",
    "atomic_capture_pair",
    "atomic_king_blast_ep",
    "atomic_blast_ring",
)
SLICE_MAX_ACTIVE = {
    "half_ka_v2_atomic_hm": 32,
    "atomic_capture_pair": 240,
    "atomic_king_blast_ep": 35,
    "atomic_blast_ring": 240,
}
ATOMIC_V3_MAX_ACTIVE = 547
MASK_SCHEMA_IDS = (
    "half-ka-v2-atomic-hm",
    "atomic-capture-pair",
    "atomic-king-blast-ep",
    "atomic-blast-ring",
)
MASK_FIELD_IDS = tuple(identifier.replace("-", "_") for identifier in MASK_SCHEMA_IDS) + (
    "hm_training",
    "hm_virtual_factors",
)
MASK_KIND_IDS = {"physical": 0, "training": 1, "virtual-factor": 2}
MASK_AGGREGATE_DOMAIN = b"atomic-v3-reachability-set-v2\0"
MASK_DOMAIN = b"atomic-v3-reachability-mask-v2\0"
PARTITION_CONFIG_DOMAIN = bytes.fromhex(
    "61746f6d69632d76332d706172746974696f6e2d636f6e6669672d763100"
)
ATOMIC_BIN_V2_GENERATION_DOMAIN = b"atomic-v3-generation-profile-v1\0"
GENERATION_KEYS = (
    "resolved_seed",
    "atomic960",
    "threads",
    "hash_mb",
    "use_nnue",
    "options",
)
GENERATION_OPTION_KEYS = (
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
)
GENERATION_VOLUME_OPTION_KEYS = (
    "requested_records",
    "records_per_shard",
    "random_file_name",
)
POLICY_COVERAGE_IDS = SLICE_IDS + ("hm_training", "hm_virtual_factors")
SEMANTIC_COUNTER_IDS = (
    "ep_marker",
    "ep_adjacent_enemy_king",
    "ep_adjacent_own_king",
    "target_king",
    "enemy_king_center",
    "enemy_king_blast",
    "own_self_blast",
    "simultaneous_enemy_and_own_blast",
    "touching_kings",
    "sole_origin_excluded",
    "multiple_origin_preserved",
    "adjacent_pawn_survives",
    "off_center_ep_pawn_excluded",
)

# Absolute color is deliberately absent.  Atomic V2/V3 feeds the dense head as
# {side-to-move perspective, opponent perspective}; absolute WHITE/BLACK is not
# a model input.  V2 also selects one shared PSQT/dense bucket.
FEATURE_INPUT_KEY_FORMULA = (
    "SHA256(ascii('atomic-v3-feature-input-v1\\0') || "
    "feature-schema-sha256-32 || stm-count-u32-le || "
    "sorted-stm-physical-indices-u32-le || opponent-count-u32-le || "
    "sorted-opponent-physical-indices-u32-le || bucket-u8)"
)
RUN_DEFINITION_DOMAIN = bytes.fromhex(
    "61746f6d69632d76332d72756e2d646566696e6974696f6e2d763100"
)
INPUT_BUNDLE_DOMAIN = bytes.fromhex(
    "61746f6d69632d76332d696e7075742d62756e646c652d763100"
)
INPUT_ARTIFACT_PATHS = (
    "feature_schema",
    "dataset_schema",
    "manifest_schema",
    "trajectory_ledger_schema",
    "index_coverage_schema",
    "statistics_schema",
    "coverage_policy_schema",
    "split_audit_schema",
    "train.manifest",
    "train.trajectory_ledger",
    "train.index_coverage",
    "train.statistics",
    "validation.manifest",
    "validation.trajectory_ledger",
    "validation.index_coverage",
    "validation.statistics",
    "coverage_policy",
    "split_audit",
)


class DuplicateKeyError(ValueError):
    """Raised by the strict JSON loader when an object repeats a key."""


def _reject_duplicate_keys(pairs: Sequence[Tuple[str, Any]]) -> Dict[str, Any]:
    result: Dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise DuplicateKeyError("duplicate JSON key: {}".format(key))
        result[key] = value
    return result


def _reject_non_json_number(value: str) -> None:
    raise ValueError("non-JSON numeric constant: {}".format(value))


def _decode_json(payload: bytes, path: Path) -> Dict[str, Any]:
    if payload.startswith(b"\xef\xbb\xbf"):
        raise ValueError("{}: UTF-8 BOM is forbidden".format(path))
    value = json.loads(
        payload.decode("utf-8"),
        object_pairs_hook=_reject_duplicate_keys,
        parse_constant=_reject_non_json_number,
    )
    if not isinstance(value, dict):
        raise ValueError("{}: top-level JSON value must be an object".format(path))
    return value


def load_json(path: Path) -> Dict[str, Any]:
    """Load one strict UTF-8 JSON object, rejecting duplicates and NaN/Inf."""

    value, _, _ = load_json_with_metadata(path)
    return value


def load_json_with_metadata(path: Path) -> Tuple[Dict[str, Any], int, str]:
    """Load one bounded, stable, non-symlink JSON file from a single handle."""

    payload, metadata = _read_bounded_regular_file(
        path, MAX_JSON_DOCUMENT_BYTES, "JSON document"
    )
    return _decode_json(payload, path), metadata.byte_count, metadata.sha256


@dataclass(frozen=True)
class StreamFileMetadata:
    """Stable metadata from one complete, bounded-memory file read."""

    byte_count: int
    sha256: str
    identity: Tuple[int, int]
    change_token: Tuple[int, int, int, int, int]


def _stat_change_token(value: os.stat_result) -> Tuple[int, int, int, int, int]:
    return (
        int(value.st_dev),
        int(value.st_ino),
        int(value.st_size),
        int(getattr(value, "st_mtime_ns", int(value.st_mtime * 1_000_000_000))),
        int(getattr(value, "st_ctime_ns", int(value.st_ctime * 1_000_000_000))),
    )


def _read_bounded_regular_file(
    path: Path, maximum: int, description: str
) -> Tuple[bytes, StreamFileMetadata]:
    """Read a small regular file once and reject symlinks or unstable snapshots."""

    try:
        pathname_stat = os.lstat(path)
    except OSError:
        raise
    if stat.S_ISLNK(pathname_stat.st_mode):
        raise ValueError("{}: {} must not be a symbolic link".format(path, description))

    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    try:
        with os.fdopen(descriptor, "rb", closefd=False) as stream:
            before = os.fstat(stream.fileno())
            if not stat.S_ISREG(before.st_mode):
                raise ValueError("{}: {} is not a regular file".format(path, description))
            pathname_identity = (
                int(pathname_stat.st_dev),
                int(pathname_stat.st_ino),
                int(pathname_stat.st_size),
                stat.S_IFMT(pathname_stat.st_mode),
            )
            handle_identity = (
                int(before.st_dev),
                int(before.st_ino),
                int(before.st_size),
                stat.S_IFMT(before.st_mode),
            )
            if pathname_identity != handle_identity:
                raise ValueError(
                    "{}: {} changed between pathname check and open".format(
                        path, description
                    )
                )
            if before.st_size > maximum:
                raise ValueError(
                    "{}: {} exceeds the {} byte limit".format(path, description, maximum)
                )
            payload = stream.read(maximum + 1)
            after = os.fstat(stream.fileno())
    finally:
        os.close(descriptor)

    if len(payload) > maximum:
        raise ValueError(
            "{}: {} exceeds the {} byte limit".format(path, description, maximum)
        )
    before_token = _stat_change_token(before)
    if before_token != _stat_change_token(after) or len(payload) != before.st_size:
        raise ValueError("{}: {} changed while being parsed".format(path, description))
    metadata = StreamFileMetadata(
        len(payload),
        hashlib.sha256(payload).hexdigest(),
        (int(before.st_dev), int(before.st_ino)),
        before_token,
    )
    return payload, metadata


def stream_file_metadata(
    path: Path, capture_limit: int = 0
) -> Tuple[StreamFileMetadata, Optional[bytes]]:
    """Hash one regular file incrementally and reject an unstable snapshot.

    ``capture_limit`` is reserved for genuinely fixed-size, small artifacts
    such as ``.atcov``.  Dataset shards, ledgers, checkpoints and networks are
    never retained in memory.
    """

    digest = hashlib.sha256()
    captured = bytearray() if capture_limit else None
    byte_count = 0
    with path.open("rb") as stream:
        before = os.fstat(stream.fileno())
        if not stat.S_ISREG(before.st_mode):
            raise ValueError("{}: authenticated artifact is not a regular file".format(path))
        if capture_limit and before.st_size > capture_limit:
            raise ValueError(
                "{}: fixed artifact exceeds capture limit {}".format(path, capture_limit)
            )
        while True:
            chunk = stream.read(STREAM_CHUNK_SIZE)
            if not chunk:
                break
            digest.update(chunk)
            byte_count += len(chunk)
            if captured is not None:
                captured.extend(chunk)
        after = os.fstat(stream.fileno())
    before_token = _stat_change_token(before)
    after_token = _stat_change_token(after)
    if before_token != after_token or byte_count != before.st_size:
        raise ValueError("{}: artifact changed while being authenticated".format(path))
    metadata = StreamFileMetadata(
        byte_count,
        digest.hexdigest(),
        (int(before.st_dev), int(before.st_ino)),
        before_token,
    )
    return metadata, bytes(captured) if captured is not None else None


def compute_partition_config_sha256(partition: Mapping[str, Any]) -> str:
    """Compute the canonical hash defined by coverage-policy x-partition-config-hash."""

    method = partition["method"]
    seed = partition["split_seed"]
    threshold = partition["validation_threshold_u64"]
    provenance = partition["provenance"]
    profile = provenance["generation_profile"]
    if not isinstance(method, str) or not method.isascii():
        raise ValueError("partition method must be ASCII")
    method_bytes = method.encode("ascii")
    qsearch_bytes = profile["qsearch_mode"].encode("utf-8")
    if len(method_bytes) > 0xFFFF or len(qsearch_bytes) > 0xFFFF:
        raise ValueError("partition variable-length field exceeds uint16")
    if profile["opening_mode"] not in ("builtin-startpos", "authenticated-book"):
        raise ValueError("unknown opening mode")
    for field in ("exclude_captures", "exclude_promotions", "exclude_checks"):
        if not isinstance(profile[field], bool):
            raise ValueError("partition boolean field is not boolean")

    opening_book = provenance["opening_book_sha256"]
    payload = bytearray(PARTITION_CONFIG_DOMAIN)
    payload += len(method_bytes).to_bytes(2, "little")
    payload += method_bytes
    payload += int(seed).to_bytes(8, "little")
    payload += int(threshold).to_bytes(8, "little")
    for field in ("engine_commit", "generator_commit", "tools_commit"):
        payload += bytes.fromhex(provenance[field])
    payload += bytes.fromhex(provenance["teacher_network_sha256"])
    payload.append(1 if opening_book is not None else 0)
    if opening_book is not None:
        payload += bytes.fromhex(opening_book)
    payload += int(profile["generation_seed"]).to_bytes(8, "little")
    payload += len(qsearch_bytes).to_bytes(2, "little")
    payload += qsearch_bytes
    payload += bytes(
        int(profile[field])
        for field in ("exclude_captures", "exclude_promotions", "exclude_checks")
    )
    payload.append(0 if profile["opening_mode"] == "builtin-startpos" else 1)
    payload += bytes.fromhex(profile["atomic_bin_v2_generation_sha256"])
    payload += bytes(
        int(profile[field])
        for field in ("adjudicate_draws_by_score", "adjudicate_resignations")
    )
    return hashlib.sha256(payload).hexdigest()


def compute_atomic_bin_v2_generation_sha256(generation: Mapping[str, Any]) -> str:
    """Hash the shared semantic V2 generation profile for V3 provenance.

    Dataset volume/layout fields are intentionally excluded so train and
    validation may contain different record counts and shard sizes.  Every
    search, randomization, adjudication, filter and Atomic960 option remains
    authenticated.
    """

    if set(generation) != set(GENERATION_KEYS):
        raise ValueError("generation object does not have the frozen canonical field set")
    options = generation.get("options")
    if not isinstance(options, Mapping) or set(options) != set(GENERATION_OPTION_KEYS):
        raise ValueError("generation.options does not have the frozen canonical field set")
    ordered_generation: Dict[str, Any] = {}
    for key in GENERATION_KEYS:
        if key == "options":
            ordered_generation[key] = {
                name: options[name]
                for name in GENERATION_OPTION_KEYS
                if name not in GENERATION_VOLUME_OPTION_KEYS
            }
        else:
            ordered_generation[key] = generation[key]
    payload = json.dumps(
        ordered_generation,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(ATOMIC_BIN_V2_GENERATION_DOMAIN + payload).hexdigest()


def _nested_mapping(root: Mapping[str, Any], dotted_path: str) -> Mapping[str, Any]:
    value: Any = root
    for component in dotted_path.split("."):
        value = value[component]
    if not isinstance(value, Mapping):
        raise TypeError(dotted_path + " must resolve to an object")
    return value


def compute_run_definition_sha256(manifest: Mapping[str, Any]) -> str:
    trainer = _nested_mapping(manifest, "trainer")
    schedule = _nested_mapping(manifest, "schedule")
    payload = bytearray(RUN_DEFINITION_DOMAIN)
    payload += bytes.fromhex(trainer["commit"])
    payload += bytes.fromhex(trainer["artifact_sha256"])
    payload += bytes.fromhex(trainer["dependency_lock_sha256"])
    payload += bytes.fromhex(_nested_mapping(trainer, "config")["sha256"])
    payload += int(trainer["training_seed"]).to_bytes(8, "little")
    deterministic = trainer["deterministic_algorithms"]
    if not isinstance(deterministic, bool):
        raise ValueError("trainer.deterministic_algorithms must be boolean")
    payload.append(int(deterministic))
    payload += int(schedule["batch_size"]).to_bytes(4, "little")
    payload += int(schedule["optimizer_steps"]).to_bytes(8, "little")
    payload += int(schedule["epochs"]).to_bytes(4, "little")
    payload += int(schedule["validation_interval_steps"]).to_bytes(8, "little")
    return hashlib.sha256(payload).hexdigest()


def compute_input_bundle_sha256(manifest: Mapping[str, Any]) -> str:
    inputs = _nested_mapping(manifest, "inputs")
    payload = bytearray(INPUT_BUNDLE_DOMAIN)
    for path in INPUT_ARTIFACT_PATHS:
        payload += bytes.fromhex(_nested_mapping(inputs, path)["sha256"])
    payload += bytes.fromhex(manifest["run_definition_sha256"])
    return hashlib.sha256(payload).hexdigest()


class Checks:
    def __init__(self) -> None:
        self.errors: List[str] = []

    def error(self, path: str, message: str) -> None:
        self.errors.append("{}: {}".format(path, message))

    def get(self, root: Any, path: str) -> Any:
        value = root
        traversed: List[str] = []
        for component in path.split(".") if path else ():
            traversed.append(component)
            if not isinstance(value, Mapping):
                self.error(".".join(traversed[:-1]) or "<root>", "must be an object")
                return None
            if component not in value:
                self.error(".".join(traversed), "is required")
                return None
            value = value[component]
        return value

    def mapping(self, value: Any, path: str) -> Mapping[str, Any]:
        if not isinstance(value, Mapping):
            self.error(path, "must be an object")
            return {}
        return value

    def sequence(self, value: Any, path: str) -> Sequence[Any]:
        if not isinstance(value, list):
            self.error(path, "must be an array")
            return []
        return value

    def plain_int(
        self,
        value: Any,
        path: str,
        minimum: Optional[int] = None,
        maximum: Optional[int] = None,
    ) -> Optional[int]:
        if isinstance(value, bool) or not isinstance(value, int):
            self.error(path, "must be an integer")
            return None
        if minimum is not None and value < minimum:
            self.error(path, "must be >= {}".format(minimum))
        if maximum is not None and value > maximum:
            self.error(path, "must be <= {}".format(maximum))
        return value

    def u64(
        self,
        value: Any,
        path: str,
        positive: bool = False,
        exclusive_maximum: bool = False,
    ) -> Optional[int]:
        if not isinstance(value, str) or UINT64_RE.fullmatch(value) is None:
            self.error(path, "must be a canonical uint64 decimal string")
            return None
        parsed = int(value)
        if parsed > MAX_U64:
            self.error(path, "exceeds UINT64_MAX")
            return None
        if positive and parsed == 0:
            self.error(path, "must be greater than zero")
        if exclusive_maximum and parsed == MAX_U64:
            self.error(path, "must be less than UINT64_MAX")
        return parsed

    def sha256(self, value: Any, path: str) -> Optional[str]:
        if not isinstance(value, str) or SHA256_RE.fullmatch(value) is None:
            self.error(path, "must be a lowercase SHA-256 hex string")
            return None
        return value

    def truth(self, value: Any, path: str) -> None:
        if value is not True:
            self.error(path, "must be true")


@dataclass
class Partition:
    config_sha256: str
    method: str
    seed_text: str
    seed: int
    threshold_text: str
    threshold: int
    provenance: Mapping[str, Any]


@dataclass
class Gate:
    minimum_records: int
    minimum_trajectories: int
    minimum_coverage_ppm: Dict[str, int]
    minimum_semantic_events: Dict[str, int]
    maximum_duplicate_raw_records_ppm: int
    maximum_duplicate_feature_inputs_ppm: int


@dataclass
class PolicySummary:
    partition: Partition
    boundaries: Dict[str, Sequence[int]]
    gates: Dict[str, Gate]
    active_capacity: int
    reachability_mask_sha256: str
    reachability_masks: Mapping[str, Mapping[str, str]]
    feature_schema_sha256: str


@dataclass
class StatsSummary:
    role: str
    records: int
    trajectories: int
    partition: Partition
    reachability_mask_sha256: str
    reachability_masks: Mapping[str, Mapping[str, str]]
    duplicate_raw_records: int
    duplicate_feature_inputs: int
    duplicate_split_groups: int


def _partition_mapping(
    document: Mapping[str, Any], path: str, stats: bool, checks: Checks
) -> Tuple[Mapping[str, Any], str]:
    field = "split" if stats else "partition"
    field_path = path + "." + field
    return checks.mapping(document.get(field), field_path), field_path


def _validate_partition(
    document: Mapping[str, Any], path: str, checks: Checks, stats: bool = False
) -> Partition:
    partition, partition_path = _partition_mapping(document, path, stats, checks)
    config_field = "partition_config_sha256" if stats else "config_sha256"
    config_sha256 = checks.sha256(
        partition.get(config_field), partition_path + "." + config_field
    ) or ""
    method_value = partition.get("method")
    if method_value != "content-hash-trajectory-v1":
        checks.error(partition_path + ".method", "must be content-hash-trajectory-v1")
    seed_value = partition.get("split_seed")
    threshold_value = partition.get("validation_threshold_u64")
    seed = checks.u64(seed_value, partition_path + ".split_seed")
    threshold = checks.u64(
        threshold_value,
        partition_path + ".validation_threshold_u64",
        positive=True,
        exclusive_maximum=True,
    )
    provenance_value = document.get("provenance") if stats else partition.get("provenance")
    provenance_path = path + ".provenance" if stats else partition_path + ".provenance"
    provenance = _validate_provenance(provenance_value, provenance_path, checks)
    try:
        expected_config_sha256 = compute_partition_config_sha256(
            {
                "method": method_value,
                "split_seed": seed_value,
                "validation_threshold_u64": threshold_value,
                "provenance": provenance,
            }
        )
    except (KeyError, TypeError, ValueError, OverflowError) as exc:
        expected_config_sha256 = None
        checks.error(
            partition_path + "." + config_field,
            "cannot compute canonical partition-config hash: {}".format(exc),
        )
    if expected_config_sha256 is not None and config_sha256 != expected_config_sha256:
        checks.error(
            partition_path + "." + config_field,
            "does not match the canonical partition-config hash",
        )
    return Partition(
        config_sha256=config_sha256,
        method=str(method_value or ""),
        seed_text=seed_value if isinstance(seed_value, str) else "",
        seed=seed or 0,
        threshold_text=threshold_value if isinstance(threshold_value, str) else "",
        threshold=threshold or 0,
        provenance=provenance,
    )


def _validate_provenance(
    value: Any, path: str, checks: Checks
) -> Mapping[str, Any]:
    provenance = checks.mapping(value, path)
    for field in ("engine_commit", "generator_commit", "tools_commit"):
        commit = provenance.get(field)
        if not isinstance(commit, str) or GIT_COMMIT_RE.fullmatch(commit) is None:
            checks.error(path + "." + field, "must be a lowercase 40-hex git commit")
    checks.sha256(provenance.get("teacher_network_sha256"), path + ".teacher_network_sha256")
    opening_book = provenance.get("opening_book_sha256")
    if opening_book is not None:
        checks.sha256(opening_book, path + ".opening_book_sha256")
    profile = checks.mapping(provenance.get("generation_profile"), path + ".generation_profile")
    checks.u64(profile.get("generation_seed"), path + ".generation_profile.generation_seed")
    qsearch_mode = profile.get("qsearch_mode")
    if not isinstance(qsearch_mode, str) or not qsearch_mode:
        checks.error(path + ".generation_profile.qsearch_mode", "must be a non-empty string")
    for field in ("exclude_captures", "exclude_promotions", "exclude_checks"):
        if not isinstance(profile.get(field), bool):
            checks.error(path + ".generation_profile." + field, "must be boolean")
    checks.sha256(
        profile.get("atomic_bin_v2_generation_sha256"),
        path + ".generation_profile.atomic_bin_v2_generation_sha256",
    )
    for field in ("adjudicate_draws_by_score", "adjudicate_resignations"):
        if not isinstance(profile.get(field), bool):
            checks.error(path + ".generation_profile." + field, "must be boolean")
    if profile.get("opening_mode") not in ("builtin-startpos", "authenticated-book"):
        checks.error(
            path + ".generation_profile.opening_mode",
            "must be builtin-startpos or authenticated-book",
        )
    elif profile.get("opening_mode") == "authenticated-book" and opening_book is None:
        checks.error(
            path + ".opening_book_sha256",
            "must be present when opening_mode is authenticated-book",
        )
    elif profile.get("opening_mode") == "builtin-startpos" and opening_book is not None:
        checks.error(
            path + ".opening_book_sha256",
            "must be null when opening_mode is builtin-startpos",
        )
    return provenance


def _validate_artifacts(value: Any, path: str, checks: Checks) -> None:
    artifacts = checks.mapping(value, path)
    for name, raw_artifact in artifacts.items():
        artifact_path = path + "." + str(name)
        artifact = checks.mapping(raw_artifact, artifact_path)
        checks.u64(artifact.get("bytes"), artifact_path + ".bytes")
        checks.sha256(artifact.get("sha256"), artifact_path + ".sha256")
        checks.sha256(artifact.get("schema_sha256"), artifact_path + ".schema_sha256")


def _validate_reachability_masks(
    value: Any, aggregate: str, path: str, checks: Checks
) -> Mapping[str, Mapping[str, str]]:
    masks = checks.mapping(value, path)
    normalized: Dict[str, Dict[str, str]] = {}
    raw_digests: List[bytes] = []
    complete = True
    for perspective in ("WHITE", "BLACK"):
        group = checks.mapping(masks.get(perspective), path + "." + perspective)
        normalized[perspective] = {}
        for field in MASK_FIELD_IDS:
            digest = checks.sha256(
                group.get(field), path + "." + perspective + "." + field
            )
            normalized[perspective][field] = digest or ""
            if digest is None:
                complete = False
            else:
                raw_digests.append(bytes.fromhex(digest))
    if complete and aggregate:
        expected = hashlib.sha256(MASK_AGGREGATE_DOMAIN + b"".join(raw_digests)).hexdigest()
        if aggregate != expected:
            checks.error(
                path,
                "aggregate reachability_mask_sha256 does not authenticate the twelve declared masks",
            )
    return normalized


def _validate_strict_boundaries(
    value: Any, path: str, checks: Checks
) -> Sequence[int]:
    items = checks.sequence(value, path)
    parsed: List[int] = []
    if not items:
        checks.error(path, "must contain at least one boundary")
        return parsed
    for index, item in enumerate(items):
        number = checks.plain_int(
            item, "{}[{}]".format(path, index), -(1 << 31), (1 << 31) - 1
        )
        if number is not None:
            parsed.append(number)
    if len(parsed) == len(items):
        for index in range(1, len(parsed)):
            if parsed[index] <= parsed[index - 1]:
                checks.error(path, "must be strictly increasing with no duplicates")
                break
    return parsed


def _validate_ppm(value: Any, path: str, checks: Checks) -> int:
    parsed = checks.plain_int(value, path, 0, PPM_SCALE)
    return parsed or 0


def _validate_gate(value: Any, path: str, release: bool, checks: Checks) -> Gate:
    gate = checks.mapping(value, path)
    minimum_records = checks.u64(gate.get("minimum_records"), path + ".minimum_records") or 0
    minimum_trajectories = (
        checks.u64(gate.get("minimum_trajectories"), path + ".minimum_trajectories") or 0
    )

    coverage_raw = checks.mapping(
        gate.get("minimum_coverage_ppm_each_perspective"),
        path + ".minimum_coverage_ppm_each_perspective",
    )
    coverage: Dict[str, int] = {}
    for name in POLICY_COVERAGE_IDS:
        coverage[name] = _validate_ppm(
            coverage_raw.get(name),
            path + ".minimum_coverage_ppm_each_perspective." + name,
            checks,
        )

    semantic_raw = checks.mapping(
        gate.get("minimum_semantic_events_each_perspective"),
        path + ".minimum_semantic_events_each_perspective",
    )
    semantic: Dict[str, int] = {}
    for name in SEMANTIC_COUNTER_IDS:
        semantic[name] = (
            checks.u64(
                semantic_raw.get(name),
                path + ".minimum_semantic_events_each_perspective." + name,
            )
            or 0
        )

    max_raw = _validate_ppm(
        gate.get("maximum_duplicate_raw_records_ppm"),
        path + ".maximum_duplicate_raw_records_ppm",
        checks,
    )
    max_features = _validate_ppm(
        gate.get("maximum_duplicate_feature_inputs_ppm"),
        path + ".maximum_duplicate_feature_inputs_ppm",
        checks,
    )

    if release:
        if minimum_records == 0:
            checks.error(path + ".minimum_records", "release-candidate gate must be non-zero")
        if minimum_trajectories == 0:
            checks.error(path + ".minimum_trajectories", "release-candidate gate must be non-zero")
        for name, threshold in coverage.items():
            if threshold == 0:
                checks.error(
                    path + ".minimum_coverage_ppm_each_perspective." + name,
                    "release-candidate gate must be non-zero",
                )
        for name, threshold in semantic.items():
            if threshold == 0:
                checks.error(
                    path + ".minimum_semantic_events_each_perspective." + name,
                    "release-candidate gate must be non-zero",
                )
        if max_raw == PPM_SCALE:
            checks.error(
                path + ".maximum_duplicate_raw_records_ppm",
                "release-candidate gate cannot allow 100% duplicates",
            )
        if max_features == PPM_SCALE:
            checks.error(
                path + ".maximum_duplicate_feature_inputs_ppm",
                "release-candidate gate cannot allow 100% duplicates",
            )

    return Gate(
        minimum_records=minimum_records,
        minimum_trajectories=minimum_trajectories,
        minimum_coverage_ppm=coverage,
        minimum_semantic_events=semantic,
        maximum_duplicate_raw_records_ppm=max_raw,
        maximum_duplicate_feature_inputs_ppm=max_features,
    )


def _validate_policy(policy: Mapping[str, Any], checks: Checks) -> PolicySummary:
    status = policy.get("status")
    if status not in ("pilot", "release-candidate"):
        checks.error("policy.status", "must be pilot or release-candidate")
    release = status == "release-candidate"

    partition = _validate_partition(policy, "policy", checks)
    if release:
        profile = partition.provenance.get("generation_profile")
        if not isinstance(profile, Mapping):
            checks.error("policy.partition.provenance.generation_profile", "must be an object")
        else:
            for field in ("adjudicate_draws_by_score", "adjudicate_resignations"):
                if profile.get(field) is not False:
                    checks.error(
                        "policy.partition.provenance.generation_profile." + field,
                        "release-candidate generation attestation must be false",
                    )
    feature_schema_sha256 = checks.sha256(
        policy.get("feature_schema_sha256"), "policy.feature_schema_sha256"
    ) or ""
    checks.sha256(policy.get("stats_schema_sha256"), "policy.stats_schema_sha256")
    mask = checks.sha256(
        policy.get("reachability_mask_sha256"), "policy.reachability_mask_sha256"
    ) or ""
    masks = _validate_reachability_masks(
        policy.get("reachability_masks"), mask, "policy.reachability_masks", checks
    )

    boundaries_raw = checks.mapping(
        policy.get("histogram_boundaries"), "policy.histogram_boundaries"
    )
    boundaries: Dict[str, Sequence[int]] = {}
    for name in ("score_cp", "ply", "rule50", "piece_count", "material_cp"):
        boundaries[name] = _validate_strict_boundaries(
            boundaries_raw.get(name), "policy.histogram_boundaries." + name, checks
        )

    gates = {
        role: _validate_gate(policy.get(role), "policy." + role, release, checks)
        for role in ("train", "validation")
    }

    global_gates = checks.mapping(policy.get("global_gates"), "policy.global_gates")
    active_capacity = (
        checks.plain_int(
            global_gates.get("active_feature_capacity"),
            "policy.global_gates.active_feature_capacity",
            1,
        )
        or 0
    )
    if active_capacity != 1024:
        checks.error("policy.global_gates.active_feature_capacity", "must equal 1024")
    for key in (
        "require_both_perspectives",
        "require_zero_invalid_records",
        "require_zero_truncation",
        "require_zero_overflow",
        "require_zero_cross_split_record_keys",
        "require_zero_cross_split_feature_input_keys",
        "require_zero_cross_split_group_ids",
    ):
        checks.truth(global_gates.get(key), "policy.global_gates." + key)

    return PolicySummary(
        partition,
        boundaries,
        gates,
        active_capacity,
        mask,
        masks,
        feature_schema_sha256,
    )


def _u64_array(
    value: Any, path: str, checks: Checks, expected_length: Optional[int] = None
) -> List[int]:
    items = checks.sequence(value, path)
    if expected_length is not None and len(items) != expected_length:
        checks.error(path, "must contain exactly {} values".format(expected_length))
    parsed: List[int] = []
    for index, item in enumerate(items):
        parsed.append(checks.u64(item, "{}[{}]".format(path, index)) or 0)
    return parsed


def _validate_count_tree(value: Any, path: str, checks: Checks) -> int:
    if isinstance(value, Mapping):
        return sum(
            _validate_count_tree(child, path + "." + str(key), checks)
            for key, child in value.items()
        )
    if isinstance(value, list):
        return sum(
            _validate_count_tree(child, "{}[{}]".format(path, index), checks)
            for index, child in enumerate(value)
        )
    return checks.u64(value, path) or 0


def _feature_dimensions(
    feature_contract: Mapping[str, Any], checks: Checks
) -> Tuple[Dict[str, int], int, int]:
    slices = checks.sequence(feature_contract.get("feature_slices"), "feature_contract.feature_slices")
    dimensions: Dict[str, int] = {}
    hm_training = 0
    hm_virtual = 0
    for index, raw_slice in enumerate(slices):
        item = checks.mapping(raw_slice, "feature_contract.feature_slices[{}]".format(index))
        identifier = item.get("id")
        physical = checks.plain_int(
            item.get("physical_dimensions"),
            "feature_contract.feature_slices[{}].physical_dimensions".format(index),
            1,
        )
        if isinstance(identifier, str) and physical is not None:
            dimensions[identifier.replace("-", "_")] = physical
        if identifier == "half-ka-v2-atomic-hm":
            hm_training = checks.plain_int(
                item.get("training_dimensions"),
                "feature_contract.feature_slices[{}].training_dimensions".format(index),
                1,
            ) or 0
            hm_virtual = checks.plain_int(
                item.get("virtual_factor_dimensions"),
                "feature_contract.feature_slices[{}].virtual_factor_dimensions".format(index),
                1,
            ) or 0
    for identifier in SLICE_IDS:
        if identifier not in dimensions:
            checks.error("feature_contract.feature_slices", "missing {}".format(identifier))
    return dimensions, hm_training, hm_virtual


def _nearest_rank_sum_bounds(
    observations: int,
    minimum: int,
    p50: int,
    p95: int,
    p99: int,
    maximum: int,
    path: str,
    checks: Checks,
) -> Tuple[int, int]:
    markers = (
        (1, minimum),
        ((50 * observations + 99) // 100, p50),
        ((95 * observations + 99) // 100, p95),
        ((99 * observations + 99) // 100, p99),
        (observations, maximum),
    )
    by_rank: Dict[int, int] = {}
    for rank, value in markers:
        prior = by_rank.get(rank)
        if prior is not None and prior != value:
            checks.error(
                path,
                "nearest-rank quantiles sharing rank {} must have the same value".format(rank),
            )
        by_rank[rank] = value
    ordered = sorted(by_rank.items())
    lower = 0
    for index, (rank, value) in enumerate(ordered):
        next_rank = ordered[index + 1][0] if index + 1 < len(ordered) else observations + 1
        lower += (next_rank - rank) * value
    upper = 0
    prior_rank = 0
    for rank, value in ordered:
        upper += (rank - prior_rank) * value
        prior_rank = rank
    return lower, upper


def _validate_active_distribution(
    value: Any,
    path: str,
    records: int,
    active_capacity: int,
    checks: Checks,
) -> Optional[int]:
    distribution = checks.mapping(value, path)
    ordered_names = ("minimum", "p50", "p95", "p99", "maximum")
    ordered: List[int] = []
    for name in ordered_names:
        parsed = checks.plain_int(distribution.get(name), path + "." + name, 0, active_capacity)
        ordered.append(parsed or 0)
    if any(ordered[index] > ordered[index + 1] for index in range(len(ordered) - 1)):
        checks.error(path, "must satisfy minimum <= p50 <= p95 <= p99 <= maximum")
    observations = checks.u64(distribution.get("observations"), path + ".observations") or 0
    total = checks.u64(distribution.get("sum"), path + ".sum") or 0
    if observations != records:
        checks.error(path + ".observations", "must equal scan.records_scanned")
    if observations and not (ordered[0] * observations <= total <= ordered[-1] * observations):
        checks.error(path + ".sum", "must lie between minimum*observations and maximum*observations")
    if observations:
        lower, upper = _nearest_rank_sum_bounds(
            observations,
            ordered[0],
            ordered[1],
            ordered[2],
            ordered[3],
            ordered[4],
            path,
            checks,
        )
        if not (lower <= total <= upper):
            checks.error(
                path + ".sum",
                "is impossible for the declared nearest-rank quantiles",
            )
    return total


def _coverage_ppm(numerator: int, denominator: int, path: str, checks: Checks) -> int:
    if denominator == 0:
        checks.error(path, "coverage denominator must be non-zero")
        return 0
    return PPM_SCALE * numerator // denominator


def _validate_stats(
    stats: Mapping[str, Any],
    expected_role: str,
    policy: PolicySummary,
    dimensions: Mapping[str, int],
    hm_training_dimensions: int,
    hm_virtual_dimensions: int,
    checks: Checks,
) -> StatsSummary:
    prefix = expected_role + "_stats"
    role = stats.get("role")
    if role != expected_role:
        checks.error(prefix + ".role", "must equal {}".format(expected_role))

    _validate_artifacts(stats.get("artifacts"), prefix + ".artifacts", checks)

    partition = _validate_partition(stats, prefix, checks, stats=True)
    if (
        partition.config_sha256,
        partition.method,
        partition.seed_text,
        partition.threshold_text,
        partition.provenance,
    ) != (
        policy.partition.config_sha256,
        policy.partition.method,
        policy.partition.seed_text,
        policy.partition.threshold_text,
        policy.partition.provenance,
    ):
        checks.error(
            prefix + ".split/provenance",
            "must exactly match the precommitted policy partition and provenance",
        )

    backend = checks.mapping(stats.get("backend"), prefix + ".backend")
    feature_schema_sha256 = checks.sha256(
        backend.get("feature_schema_sha256"), prefix + ".backend.feature_schema_sha256"
    ) or ""
    if feature_schema_sha256 != policy.feature_schema_sha256:
        checks.error(
            prefix + ".backend.feature_schema_sha256",
            "must match policy.feature_schema_sha256",
        )
    mask = checks.sha256(
        backend.get("reachability_mask_sha256"),
        prefix + ".backend.reachability_mask_sha256",
    ) or ""
    if mask and mask != policy.reachability_mask_sha256:
        checks.error(
            prefix + ".backend.reachability_mask_sha256",
            "must match policy.reachability_mask_sha256",
        )
    masks = _validate_reachability_masks(
        backend.get("reachability_masks"),
        mask,
        prefix + ".backend.reachability_masks",
        checks,
    )
    if masks != policy.reachability_masks:
        checks.error(
            prefix + ".backend.reachability_masks",
            "all twelve masks must exactly match policy.reachability_masks",
        )

    scan = checks.mapping(stats.get("scan"), prefix + ".scan")
    records = checks.u64(scan.get("records_scanned"), prefix + ".scan.records_scanned", positive=True) or 0
    perspectives = checks.u64(
        scan.get("perspectives_scanned"), prefix + ".scan.perspectives_scanned"
    ) or 0
    if perspectives != 2 * records:
        checks.error(prefix + ".scan.perspectives_scanned", "must equal 2 * records_scanned")
    for field in ("invalid_records", "truncated_active_lists", "accumulator_overflows"):
        parsed = checks.u64(scan.get(field), prefix + ".scan." + field)
        if parsed not in (None, 0):
            checks.error(prefix + ".scan." + field, "must equal zero")
    for field in (
        "strict_eof",
        "all_shards_authenticated",
        "all_ledger_entries_structurally_scanned",
    ):
        checks.truth(scan.get(field), prefix + ".scan." + field)

    maximums = checks.mapping(scan.get("max_active_observed"), prefix + ".scan.max_active_observed")
    scan_maxima: Dict[str, int] = {}
    for perspective in ("WHITE", "BLACK"):
        scan_maxima[perspective] = checks.plain_int(
            maximums.get(perspective),
            prefix + ".scan.max_active_observed." + perspective,
            0,
            min(policy.active_capacity, ATOMIC_V3_MAX_ACTIVE),
        ) or 0

    partition_doc, partition_path = _partition_mapping(stats, prefix, True, checks)
    trajectories = checks.u64(
        partition_doc.get("ledger_trajectory_count"),
        partition_path + ".ledger_trajectory_count",
        positive=True,
    ) or 0
    ledger_records = checks.u64(
        partition_doc.get("ledger_record_count"),
        partition_path + ".ledger_record_count",
    ) or 0
    if ledger_records != records:
        checks.error(partition_path + ".ledger_record_count", "must equal records_scanned")

    gate = policy.gates[expected_role]
    if records < gate.minimum_records:
        checks.error(prefix + ".scan.records_scanned", "is below policy minimum_records")
    if trajectories < gate.minimum_trajectories:
        checks.error(
            partition_path + ".ledger_trajectory_count",
            "is below policy minimum_trajectories",
        )

    distribution = checks.mapping(stats.get("distribution"), prefix + ".distribution")
    wdl = checks.mapping(distribution.get("wdl_side_to_move"), prefix + ".distribution.wdl_side_to_move")
    wdl_total = sum(
        checks.u64(wdl.get(name), prefix + ".distribution.wdl_side_to_move." + name) or 0
        for name in ("win", "draw", "loss")
    )
    if wdl_total != records:
        checks.error(prefix + ".distribution.wdl_side_to_move", "counts must sum to records_scanned")

    for name, boundaries in policy.boundaries.items():
        histogram = checks.mapping(distribution.get(name), prefix + ".distribution." + name)
        if histogram.get("policy_name") != name:
            checks.error(prefix + ".distribution." + name + ".policy_name", "must equal {}".format(name))
        counts = _u64_array(histogram.get("counts"), prefix + ".distribution." + name + ".counts", checks)
        if len(counts) != len(boundaries) + 1:
            checks.error(
                prefix + ".distribution." + name + ".counts",
                "must contain len(policy boundaries) + 1 bins",
            )
        if sum(counts) != records:
            checks.error(prefix + ".distribution." + name + ".counts", "must sum to records_scanned")

    network_buckets = _u64_array(
        distribution.get("network_buckets"),
        prefix + ".distribution.network_buckets",
        checks,
        8,
    )
    if sum(network_buckets) != records:
        checks.error(prefix + ".distribution.network_buckets", "must sum to records_scanned")

    coverage_by_perspective = checks.mapping(
        stats.get("coverage_by_perspective"), prefix + ".coverage_by_perspective"
    )
    for perspective in ("WHITE", "BLACK"):
        coverage = checks.mapping(
            coverage_by_perspective.get(perspective),
            prefix + ".coverage_by_perspective." + perspective,
        )
        occurrences: Dict[str, int] = {}
        slice_maxima: List[int] = []
        for slice_id in SLICE_IDS:
            slice_path = prefix + ".coverage_by_perspective." + perspective + "." + slice_id
            item = checks.mapping(coverage.get(slice_id), slice_path)
            physical = checks.plain_int(item.get("physical_dimensions"), slice_path + ".physical_dimensions", 1) or 0
            if physical != dimensions.get(slice_id, 0):
                checks.error(slice_path + ".physical_dimensions", "does not match feature contract")
            structurally_reachable = checks.plain_int(
                item.get("structurally_reachable_indices"),
                slice_path + ".structurally_reachable_indices",
                0,
            ) or 0
            structurally_unreachable = checks.plain_int(
                item.get("structurally_unreachable_indices"),
                slice_path + ".structurally_unreachable_indices",
                0,
            ) or 0
            observed = checks.plain_int(
                item.get("observed_reachable_indices"),
                slice_path + ".observed_reachable_indices",
                0,
            ) or 0
            unobserved = checks.plain_int(
                item.get("reachable_unobserved_indices"),
                slice_path + ".reachable_unobserved_indices",
                0,
            ) or 0
            if structurally_reachable + structurally_unreachable != physical:
                checks.error(slice_path, "reachable + unreachable must equal physical_dimensions")
            if observed + unobserved != structurally_reachable:
                checks.error(slice_path, "observed + reachable_unobserved must equal structurally_reachable")
            ppm = _coverage_ppm(observed, structurally_reachable, slice_path, checks)
            if ppm < gate.minimum_coverage_ppm[slice_id]:
                checks.error(slice_path, "coverage ppm is below the policy minimum")

            occurrence = checks.u64(item.get("occurrence_count"), slice_path + ".occurrence_count") or 0
            occurrences[slice_id] = occurrence
            active_distribution = item.get("active_per_position")
            active_sum = _validate_active_distribution(
                active_distribution,
                slice_path + ".active_per_position",
                records,
                SLICE_MAX_ACTIVE[slice_id],
                checks,
            )
            if isinstance(active_distribution, Mapping):
                maximum_value = active_distribution.get("maximum")
                if isinstance(maximum_value, int) and not isinstance(maximum_value, bool):
                    slice_maxima.append(maximum_value)
                else:
                    slice_maxima.append(0)
            else:
                slice_maxima.append(0)
            if active_sum is not None and occurrence != active_sum:
                checks.error(slice_path + ".occurrence_count", "must equal active_per_position.sum")

        aggregate_maximum = scan_maxima[perspective]
        if slice_maxima and aggregate_maximum < max(slice_maxima):
            checks.error(
                prefix + ".scan.max_active_observed." + perspective,
                "must be at least every per-slice active_per_position.maximum",
            )
        if aggregate_maximum > sum(slice_maxima):
            checks.error(
                prefix + ".scan.max_active_observed." + perspective,
                "must be at most the sum of per-slice active_per_position.maximum values",
            )

        hm_path = prefix + ".coverage_by_perspective." + perspective + ".hm_training"
        hm = checks.mapping(coverage.get("hm_training"), hm_path)
        training_dimensions = checks.plain_int(hm.get("training_dimensions"), hm_path + ".training_dimensions", 1) or 0
        reachable_training = checks.plain_int(
            hm.get("structurally_reachable_training_indices"),
            hm_path + ".structurally_reachable_training_indices",
            0,
        ) or 0
        unreachable_training = checks.plain_int(
            hm.get("structurally_unreachable_training_indices"),
            hm_path + ".structurally_unreachable_training_indices",
            0,
        ) or 0
        observed_training = checks.plain_int(
            hm.get("observed_reachable_training_indices"),
            hm_path + ".observed_reachable_training_indices",
            0,
        ) or 0
        unobserved_training = checks.plain_int(
            hm.get("reachable_unobserved_training_indices"),
            hm_path + ".reachable_unobserved_training_indices",
            0,
        ) or 0
        if training_dimensions != hm_training_dimensions:
            checks.error(hm_path + ".training_dimensions", "does not match feature contract")
        if reachable_training + unreachable_training != training_dimensions:
            checks.error(
                hm_path,
                "reachable + unreachable training indices must equal training_dimensions",
            )
        if observed_training + unobserved_training != reachable_training:
            checks.error(
                hm_path,
                "observed + reachable_unobserved training indices must equal structurally reachable",
            )
        training_ppm = _coverage_ppm(observed_training, reachable_training, hm_path, checks)
        if training_ppm < gate.minimum_coverage_ppm["hm_training"]:
            checks.error(hm_path, "training coverage ppm is below the policy minimum")

        virtual_dimensions = checks.plain_int(
            hm.get("virtual_factor_dimensions"), hm_path + ".virtual_factor_dimensions", 1
        ) or 0
        reachable_virtual = checks.plain_int(
            hm.get("structurally_reachable_virtual_factor_indices"),
            hm_path + ".structurally_reachable_virtual_factor_indices",
            0,
        ) or 0
        unreachable_virtual = checks.plain_int(
            hm.get("structurally_unreachable_virtual_factor_indices"),
            hm_path + ".structurally_unreachable_virtual_factor_indices",
            0,
        ) or 0
        observed_virtual = checks.plain_int(
            hm.get("observed_reachable_virtual_factor_indices"),
            hm_path + ".observed_reachable_virtual_factor_indices",
            0,
        ) or 0
        unobserved_virtual = checks.plain_int(
            hm.get("reachable_unobserved_virtual_factor_indices"),
            hm_path + ".reachable_unobserved_virtual_factor_indices",
            0,
        ) or 0
        if virtual_dimensions != hm_virtual_dimensions:
            checks.error(hm_path + ".virtual_factor_dimensions", "does not match feature contract")
        if reachable_virtual + unreachable_virtual != virtual_dimensions:
            checks.error(
                hm_path,
                "reachable + unreachable virtual indices must equal virtual_factor_dimensions",
            )
        if observed_virtual + unobserved_virtual != reachable_virtual:
            checks.error(
                hm_path,
                "observed + reachable_unobserved virtual indices must equal structurally reachable",
            )
        virtual_ppm = _coverage_ppm(observed_virtual, reachable_virtual, hm_path, checks)
        if virtual_ppm < gate.minimum_coverage_ppm["hm_virtual_factors"]:
            checks.error(hm_path, "virtual-factor coverage ppm is below the policy minimum")

        buckets = _u64_array(
            coverage.get("hm_king_buckets"),
            prefix + ".coverage_by_perspective." + perspective + ".hm_king_buckets",
            checks,
            32,
        )
        if sum(buckets) != records:
            checks.error(
                prefix + ".coverage_by_perspective." + perspective + ".hm_king_buckets",
                "must sum to records_scanned",
            )
        mirror = checks.mapping(
            coverage.get("hm_mirror_branches"),
            prefix + ".coverage_by_perspective." + perspective + ".hm_mirror_branches",
        )
        mirror_sum = sum(
            checks.u64(
                mirror.get(name),
                prefix + ".coverage_by_perspective." + perspective + ".hm_mirror_branches." + name,
            )
            or 0
            for name in ("unmirrored", "mirrored")
        )
        if mirror_sum != records:
            checks.error(
                prefix + ".coverage_by_perspective." + perspective + ".hm_mirror_branches",
                "must sum to records_scanned",
            )

        semantic = checks.mapping(
            coverage.get("semantic_counters"),
            prefix + ".coverage_by_perspective." + perspective + ".semantic_counters",
        )
        for name in SEMANTIC_COUNTER_IDS:
            count = checks.u64(
                semantic.get(name),
                prefix + ".coverage_by_perspective." + perspective + ".semantic_counters." + name,
            ) or 0
            if count < gate.minimum_semantic_events[name]:
                checks.error(
                    prefix + ".coverage_by_perspective." + perspective + ".semantic_counters." + name,
                    "is below the policy minimum",
                )

        class_prefix = prefix + ".coverage_by_perspective." + perspective
        capture_total = _validate_count_tree(
            coverage.get("capture_pair_classes"),
            class_prefix + ".capture_pair_classes",
            checks,
        )
        if capture_total != occurrences.get("atomic_capture_pair", 0):
            checks.error(
                class_prefix + ".capture_pair_classes",
                "class counts must sum to atomic_capture_pair.occurrence_count",
            )
        king_classes = checks.mapping(
            coverage.get("king_blast_ep_classes"),
            class_prefix + ".king_blast_ep_classes",
        )
        for name in ("by_actor_class", "by_center"):
            total = _validate_count_tree(
                king_classes.get(name),
                class_prefix + ".king_blast_ep_classes." + name,
                checks,
            )
            if total != occurrences.get("atomic_king_blast_ep", 0):
                checks.error(
                    class_prefix + ".king_blast_ep_classes." + name,
                    "class counts must sum to atomic_king_blast_ep.occurrence_count",
                )
        ring_classes = checks.mapping(
            coverage.get("blast_ring_classes"),
            class_prefix + ".blast_ring_classes",
        )
        for name in ("by_actor_collateral_offset_class", "by_center"):
            total = _validate_count_tree(
                ring_classes.get(name),
                class_prefix + ".blast_ring_classes." + name,
                checks,
            )
            if total != occurrences.get("atomic_blast_ring", 0):
                checks.error(
                    class_prefix + ".blast_ring_classes." + name,
                    "class counts must sum to atomic_blast_ring.occurrence_count",
                )

    record_events = checks.mapping(stats.get("record_events"), prefix + ".record_events")
    for name, value in record_events.items():
        count = checks.u64(value, prefix + ".record_events." + str(name)) or 0
        if count > records:
            checks.error(prefix + ".record_events." + str(name), "cannot exceed records_scanned")
    trajectory_events = checks.mapping(
        stats.get("trajectory_events"), prefix + ".trajectory_events"
    )
    for name in ("en_passant_moves", "promotion_moves", "castling_moves", "explosive_captures"):
        checks.u64(trajectory_events.get(name), prefix + ".trajectory_events." + name)
    stop_reasons = _u64_array(
        trajectory_events.get("stop_reasons"),
        prefix + ".trajectory_events.stop_reasons",
        checks,
        9,
    )
    if sum(stop_reasons) != trajectories:
        checks.error(
            prefix + ".trajectory_events.stop_reasons",
            "must sum to ledger_trajectory_count",
        )

    dedup = checks.mapping(stats.get("deduplication"), prefix + ".deduplication")
    duplicate_raw = checks.u64(
        dedup.get("duplicate_raw_records"), prefix + ".deduplication.duplicate_raw_records"
    ) or 0
    duplicate_features = checks.u64(
        dedup.get("duplicate_feature_inputs"), prefix + ".deduplication.duplicate_feature_inputs"
    ) or 0
    duplicate_groups = checks.u64(
        dedup.get("duplicate_split_groups"), prefix + ".deduplication.duplicate_split_groups"
    )
    if duplicate_groups not in (None, 0):
            checks.error(prefix + ".deduplication.duplicate_split_groups", "must equal zero")
    if duplicate_raw > records:
        checks.error(prefix + ".deduplication.duplicate_raw_records", "cannot exceed records_scanned")
    if duplicate_features > records:
        checks.error(prefix + ".deduplication.duplicate_feature_inputs", "cannot exceed records_scanned")
    if records:
        raw_ppm = PPM_SCALE * duplicate_raw // records
        feature_ppm = PPM_SCALE * duplicate_features // records
        if raw_ppm > gate.maximum_duplicate_raw_records_ppm:
            checks.error(prefix + ".deduplication.duplicate_raw_records", "duplicate ppm exceeds policy")
        if feature_ppm > gate.maximum_duplicate_feature_inputs_ppm:
            checks.error(prefix + ".deduplication.duplicate_feature_inputs", "duplicate ppm exceeds policy")

    return StatsSummary(
        str(role or ""),
        records,
        trajectories,
        partition,
        mask,
        masks,
        duplicate_raw,
        duplicate_features,
        duplicate_groups or 0,
    )


def _validate_contiguous_fields(
    value: Any,
    path: str,
    expected: Sequence[Tuple[str, int, int]],
    checks: Checks,
) -> Mapping[str, Mapping[str, Any]]:
    fields = checks.sequence(value, path)
    if len(fields) != len(expected):
        checks.error(path, "must contain exactly {} fields".format(len(expected)))
    by_name: Dict[str, Mapping[str, Any]] = {}
    next_offset = 0
    for index, raw in enumerate(fields):
        field = checks.mapping(raw, "{}[{}]".format(path, index))
        name = field.get("name")
        offset = checks.plain_int(field.get("offset"), "{}[{}].offset".format(path, index), 0)
        size = checks.plain_int(field.get("size"), "{}[{}].size".format(path, index), 1)
        if offset is not None and offset != next_offset:
            checks.error("{}[{}].offset".format(path, index), "must equal prior offset + size")
        if size is not None:
            next_offset += size
        if isinstance(name, str):
            by_name[name] = field
        if index < len(expected):
            expected_name, expected_offset, expected_size = expected[index]
            if (name, offset, size) != (expected_name, expected_offset, expected_size):
                checks.error(
                    "{}[{}]".format(path, index),
                    "expected {} at offset {} size {}".format(
                        expected_name, expected_offset, expected_size
                    ),
                )
    return by_name


def _validate_index_coverage_contract(
    contract: Mapping[str, Any],
    dimensions: Mapping[str, int],
    hm_training: int,
    hm_virtual: int,
    checks: Checks,
) -> None:
    for field, expected in (
        ("schema_version", 1),
        ("schema_id", "atomic-v3-index-coverage-v1"),
        ("variant", "atomic"),
        ("backend", "AtomicNNUEV3"),
        ("byte_order", "little-endian"),
    ):
        if contract.get(field) != expected:
            checks.error("index_contract." + field, "must equal {}".format(expected))
    header = checks.mapping(contract.get("header"), "index_contract.header")
    header_size = checks.plain_int(header.get("size"), "index_contract.header.size", 1) or 0
    if header_size != 128:
        checks.error("index_contract.header.size", "must equal 128")
    if header.get("magic_hex") != "4154434f56310000":
        checks.error(
            "index_contract.header.magic_hex", "must equal 4154434f56310000"
        )
    expected_fields = (
        ("magic", 0, 8),
        ("version", 8, 2),
        ("header_size", 10, 2),
        ("endian_marker", 12, 4),
        ("counter_size", 16, 4),
        ("role", 20, 4),
        ("schema_sha256", 24, 32),
        ("feature_schema_sha256", 56, 32),
        ("dataset_manifest_sha256", 88, 32),
        ("counter_count", 120, 8),
    )
    fields = _validate_contiguous_fields(
        header.get("fields"), "index_contract.header.fields", expected_fields, checks
    )
    for name, expected_value in (
        ("version", 1),
        ("header_size", 128),
        ("endian_marker", 16909060),
        ("counter_size", 8),
    ):
        if fields.get(name, {}).get("required_value") != expected_value:
            checks.error(
                "index_contract.header.fields." + name + ".required_value",
                "must equal {}".format(expected_value),
            )

    counter = checks.mapping(contract.get("counter"), "index_contract.counter")
    counter_size = checks.plain_int(counter.get("size"), "index_contract.counter.size", 1) or 0
    if counter_size != 8 or counter.get("storage") != "uint64":
        checks.error("index_contract.counter", "must be an 8-byte uint64")

    expected_segments: List[Tuple[str, str, str, int, int]] = []
    offset = 0
    for perspective in ("WHITE", "BLACK"):
        for field_id, schema_id in zip(SLICE_IDS, MASK_SCHEMA_IDS):
            count = dimensions.get(field_id, 0)
            expected_segments.append((perspective, "physical", schema_id, offset, count))
            offset += count
        expected_segments.append(
            (perspective, "training", MASK_SCHEMA_IDS[0], offset, hm_training)
        )
        offset += hm_training
        expected_segments.append(
            (perspective, "virtual-factor", MASK_SCHEMA_IDS[0], offset, hm_virtual)
        )
        offset += hm_virtual

    segments = checks.sequence(contract.get("segments"), "index_contract.segments")
    if len(segments) != len(expected_segments):
        checks.error("index_contract.segments", "must contain exactly 12 segments")
    for index, expected in enumerate(expected_segments):
        if index >= len(segments):
            break
        segment = checks.mapping(segments[index], "index_contract.segments[{}]".format(index))
        actual = (
            segment.get("perspective"),
            segment.get("kind"),
            segment.get("slice"),
            segment.get("offset"),
            segment.get("count"),
        )
        if actual != expected:
            checks.error(
                "index_contract.segments[{}]".format(index),
                "expected {}".format(expected),
            )

    counter_count = fields.get("counter_count", {}).get("required_value")
    if counter_count != offset:
        checks.error(
            "index_contract.header.fields.counter_count.required_value",
            "must equal sum of all exact segments ({})".format(offset),
        )
    file_policy = checks.mapping(contract.get("file_policy"), "index_contract.file_policy")
    counter_region_end = header_size + offset * counter_size

    reachability = checks.mapping(
        contract.get("reachability_masks"), "index_contract.reachability_masks"
    )
    if reachability.get("storage") != "trailing-canonical-bitmaps":
        checks.error(
            "index_contract.reachability_masks.storage",
            "must equal trailing-canonical-bitmaps",
        )
    if reachability.get("offset") != counter_region_end:
        checks.error(
            "index_contract.reachability_masks.offset",
            "must start immediately after the counter region",
        )
    if reachability.get("perspective_id") != {"WHITE": 0, "BLACK": 1}:
        checks.error(
            "index_contract.reachability_masks.perspective_id",
            "must preserve the canonical WHITE=0, BLACK=1 mapping",
        )
    if reachability.get("kind_id") != MASK_KIND_IDS:
        checks.error(
            "index_contract.reachability_masks.kind_id",
            "must preserve physical=0, training=1, virtual-factor=2",
        )
    expected_slice_ids = {name: index for index, name in enumerate(MASK_SCHEMA_IDS)}
    if reachability.get("slice_id") != expected_slice_ids:
        checks.error(
            "index_contract.reachability_masks.slice_id",
            "must preserve the canonical physical-slice mapping",
        )
    expected_mask_bytes = {
        name: (dimensions.get(name.replace("-", "_"), 0) + 7) // 8
        for name in MASK_SCHEMA_IDS
    }
    expected_mask_bytes["hm-training"] = (hm_training + 7) // 8
    expected_mask_bytes["hm-virtual-factors"] = (hm_virtual + 7) // 8
    if reachability.get("mask_byte_count") != expected_mask_bytes:
        checks.error(
            "index_contract.reachability_masks.mask_byte_count",
            "must equal ceil(index_dimensions / 8) for all six mask classes",
        )
    mask_specs = [
        ("physical", slice_id, expected_mask_bytes[slice_id])
        for slice_id in MASK_SCHEMA_IDS
    ] + [
        ("training", MASK_SCHEMA_IDS[0], expected_mask_bytes["hm-training"]),
        (
            "virtual-factor",
            MASK_SCHEMA_IDS[0],
            expected_mask_bytes["hm-virtual-factors"],
        ),
    ]
    expected_mask_layout: List[Mapping[str, Any]] = []
    mask_offset = counter_region_end
    for perspective in ("WHITE", "BLACK"):
        for kind, slice_id, byte_count in mask_specs:
            expected_mask_layout.append(
                {
                    "perspective": perspective,
                    "kind": kind,
                    "slice": slice_id,
                    "offset": mask_offset,
                    "bytes": byte_count,
                }
            )
            mask_offset += byte_count
    if reachability.get("layout") != expected_mask_layout:
        checks.error(
            "index_contract.reachability_masks.layout",
            "must contain the exact twelve trailing structural bitmap ranges",
        )
    aggregate = checks.mapping(
        reachability.get("aggregate_hash"),
        "index_contract.reachability_masks.aggregate_hash",
    )
    expected_order = [
        perspective + "." + kind + "." + slice_id
        for perspective in ("WHITE", "BLACK")
        for kind, slice_id, _ in mask_specs
    ]
    if aggregate.get("algorithm") != "SHA-256":
        checks.error(
            "index_contract.reachability_masks.aggregate_hash.algorithm",
            "must equal SHA-256",
        )
    if aggregate.get("domain_ascii_hex") != MASK_AGGREGATE_DOMAIN.hex():
        checks.error(
            "index_contract.reachability_masks.aggregate_hash.domain_ascii_hex",
            "must equal the atomic-v3-reachability-set-v2 domain",
        )
    if aggregate.get("per_mask_digest_order") != expected_order:
        checks.error(
            "index_contract.reachability_masks.aggregate_hash.per_mask_digest_order",
            "must list the canonical twelve-mask digest order",
        )
    per_mask = checks.mapping(
        reachability.get("per_mask_hash"),
        "index_contract.reachability_masks.per_mask_hash",
    )
    if per_mask.get("algorithm") != "SHA-256":
        checks.error(
            "index_contract.reachability_masks.per_mask_hash.algorithm",
            "must equal SHA-256",
        )
    if per_mask.get("domain_ascii_hex") != MASK_DOMAIN.hex():
        checks.error(
            "index_contract.reachability_masks.per_mask_hash.domain_ascii_hex",
            "must equal the atomic-v3-reachability-mask-v2 domain",
        )
    expected_components = [
        "feature_schema_sha256_raw32",
        "perspective_id_u8",
        "kind_id_u8",
        "slice_id_u8",
        "index_count_u32_le",
        "mask_byte_count_u32_le",
        "mask_bytes",
    ]
    if per_mask.get("component_order") != expected_components:
        checks.error(
            "index_contract.reachability_masks.per_mask_hash.component_order",
            "must include the canonical kind-disambiguated component order",
        )
    expected_per_mask_formula = (
        "SHA256(domain || feature_schema_sha256_raw32 || perspective_id_u8 || "
        "kind_id_u8 || slice_id_u8 || index_count_u32_le || "
        "mask_byte_count_u32_le || mask_bytes)"
    )
    if per_mask.get("formula") != expected_per_mask_formula:
        checks.error(
            "index_contract.reachability_masks.per_mask_hash.formula",
            "must equal " + expected_per_mask_formula,
        )
    expected_size = mask_offset
    if file_policy.get("file_size") != expected_size:
        checks.error("index_contract.file_policy.file_size", "must equal {}".format(expected_size))
    expected_formula = "{} + {} * {} + {}".format(
        header_size, offset, counter_size, mask_offset - counter_region_end
    )
    if file_policy.get("formula") != expected_formula:
        checks.error("index_contract.file_policy.formula", "must equal " + expected_formula)


def _validate_ledger_contract(contract: Mapping[str, Any], checks: Checks) -> None:
    for field, expected in (
        ("schema_version", 1),
        ("schema_id", "atomic-trajectory-ledger-v1"),
        ("variant", "atomic"),
        ("byte_order", "little-endian"),
    ):
        if contract.get(field) != expected:
            checks.error("ledger_contract." + field, "must equal {}".format(expected))
    header = checks.mapping(contract.get("header"), "ledger_contract.header")
    entry = checks.mapping(contract.get("entry"), "ledger_contract.entry")
    header_size = checks.plain_int(header.get("size"), "ledger_contract.header.size", 1) or 0
    entry_size = checks.plain_int(entry.get("size"), "ledger_contract.entry.size", 1) or 0
    if header_size != 160:
        checks.error("ledger_contract.header.size", "must equal 160")
    if entry_size != 112:
        checks.error("ledger_contract.entry.size", "must equal 112")
    if header.get("magic_hex") != "41545452414a3100":
        checks.error(
            "ledger_contract.header.magic_hex", "must equal 41545452414a3100"
        )

    header_expected = (
        ("magic", 0, 8),
        ("version", 8, 2),
        ("header_size", 10, 2),
        ("endian_marker", 12, 4),
        ("entry_size", 16, 4),
        ("role", 20, 4),
        ("schema_sha256", 24, 32),
        ("dataset_manifest_sha256", 56, 32),
        ("data_schema_sha256", 88, 32),
        ("record_count", 120, 8),
        ("trajectory_count", 128, 8),
        ("move_count", 136, 8),
        ("entries_offset", 144, 8),
        ("moves_offset", 152, 8),
    )
    header_fields = _validate_contiguous_fields(
        header.get("fields"), "ledger_contract.header.fields", header_expected, checks
    )
    for name, expected_value in (
        ("version", 1),
        ("header_size", 160),
        ("entry_size", 112),
        ("entries_offset", 160),
    ):
        if header_fields.get(name, {}).get("required_value") != expected_value:
            checks.error(
                "ledger_contract.header.fields." + name + ".required_value",
                "must equal {}".format(expected_value),
            )
    moves_formula = "160 + trajectory_count * 112"
    if header_fields.get("moves_offset", {}).get("formula") != moves_formula:
        checks.error("ledger_contract.header.fields.moves_offset.formula", "must equal " + moves_formula)

    entry_expected = (
        ("split_group_id", 0, 32),
        ("root_position", 32, 48),
        ("first_record", 80, 8),
        ("record_count", 88, 4),
        ("move_count", 92, 4),
        ("first_move", 96, 8),
        ("terminal_result", 104, 1),
        ("atomic960", 105, 1),
        ("stop_reason", 106, 1),
        ("reserved", 107, 5),
    )
    _validate_contiguous_fields(entry.get("fields"), "ledger_contract.entry.fields", entry_expected, checks)

    move_stream = checks.mapping(contract.get("move_stream"), "ledger_contract.move_stream")
    if move_stream.get("element_size") != 4:
        checks.error("ledger_contract.move_stream.element_size", "must equal 4")
    file_policy = checks.mapping(contract.get("file_policy"), "ledger_contract.file_policy")
    file_formula = "160 + trajectory_count * 112 + move_count * 4"
    if file_policy.get("file_size_formula") != file_formula:
        checks.error("ledger_contract.file_policy.file_size_formula", "must equal " + file_formula)
    split_group = checks.mapping(contract.get("split_group_id"), "ledger_contract.split_group_id")
    if split_group.get("hash") != "SHA-256":
        checks.error("ledger_contract.split_group_id.hash", "must equal SHA-256")
    if split_group.get("domain_ascii_hex") != "61746f6d69632d73706c69742d67726f75702d763100":
        checks.error(
            "ledger_contract.split_group_id.domain_ascii_hex",
            "must equal the atomic-split-group-v1 NUL-terminated domain",
        )
    expected_group_formula = (
        "SHA256(domain || root_position[48] || atomic960_u8 || move_count_u64_le || "
        "complete_move_wires_u32_le)"
    )
    if split_group.get("formula") != expected_group_formula:
        checks.error(
            "ledger_contract.split_group_id.formula",
            "must equal " + expected_group_formula,
        )
    partition = checks.mapping(contract.get("partition"), "ledger_contract.partition")
    if partition.get("hash") != "SHA-256":
        checks.error("ledger_contract.partition.hash", "must equal SHA-256")
    if partition.get("domain_ascii_hex") != "61746f6d69632d73706c69742d763100":
        checks.error(
            "ledger_contract.partition.domain_ascii_hex",
            "must equal the atomic-split-v1 NUL-terminated domain",
        )
    expected_partition_formula = (
        "u64_le(SHA256(domain || split_seed_u64_le || split_group_id)[0:8])"
    )
    if partition.get("formula") != expected_partition_formula:
        checks.error(
            "ledger_contract.partition.formula",
            "must equal " + expected_partition_formula,
        )
    if partition.get("validation") != "partition_hash < validation_threshold":
        checks.error(
            "ledger_contract.partition.validation",
            "must use strict partition_hash < validation_threshold",
        )
    if partition.get("train") != "partition_hash >= validation_threshold":
        checks.error(
            "ledger_contract.partition.train",
            "must use partition_hash >= validation_threshold",
        )


def _validate_set_summary(
    value: Any,
    path: str,
    expected_observations: int,
    checks: Checks,
) -> Tuple[int, int, str]:
    summary = checks.mapping(value, path)
    observations = checks.u64(summary.get("observations"), path + ".observations") or 0
    unique = checks.u64(summary.get("unique_keys"), path + ".unique_keys") or 0
    duplicates = checks.u64(
        summary.get("duplicate_observations"), path + ".duplicate_observations"
    ) or 0
    digest = checks.sha256(
        summary.get("ordered_set_sha256"), path + ".ordered_set_sha256"
    ) or ""
    if observations != expected_observations:
        checks.error(path + ".observations", "does not match the authenticated source count")
    if unique + duplicates != observations:
        checks.error(path, "unique_keys + duplicate_observations must equal observations")
    return unique, duplicates, digest


def _validate_split_audit(
    audit: Mapping[str, Any],
    policy: PolicySummary,
    train: StatsSummary,
    validation: StatsSummary,
    checks: Checks,
) -> None:
    _validate_artifacts(audit.get("artifacts"), "split_audit.artifacts", checks)
    audit_partition = _validate_partition(audit, "split_audit", checks)
    if audit_partition != policy.partition:
        checks.error(
            "split_audit.partition",
            "must exactly repeat the authenticated policy partition",
        )

    identities = checks.mapping(audit.get("identity_definitions"), "split_audit.identity_definitions")
    formula = identities.get("feature_input_key")
    if formula != FEATURE_INPUT_KEY_FORMULA:
        checks.error(
            "split_audit.identity_definitions.feature_input_key",
            "must use the canonical color-agnostic, single-bucket formula",
        )

    intersections = checks.mapping(audit.get("intersections"), "split_audit.intersections")
    for name in ("raw_record_keys", "feature_input_keys", "split_group_ids"):
        value = checks.u64(intersections.get(name), "split_audit.intersections." + name)
        if value not in (None, 0):
            checks.error("split_audit.intersections." + name, "must equal zero")

    sets = checks.mapping(audit.get("sets"), "split_audit.sets")
    set_summaries: Dict[str, Dict[str, Tuple[int, int, str]]] = {}
    for role, summary in (("train", train), ("validation", validation)):
        group = checks.mapping(sets.get(role), "split_audit.sets." + role)
        set_summaries[role] = {}
        set_summaries[role]["raw_record_keys"] = _validate_set_summary(
            group.get("raw_record_keys"),
            "split_audit.sets." + role + ".raw_record_keys",
            summary.records,
            checks,
        )
        set_summaries[role]["feature_input_keys"] = _validate_set_summary(
            group.get("feature_input_keys"),
            "split_audit.sets." + role + ".feature_input_keys",
            summary.records,
            checks,
        )
        set_summaries[role]["split_group_ids"] = _validate_set_summary(
            group.get("split_group_ids"),
            "split_audit.sets." + role + ".split_group_ids",
            summary.trajectories,
            checks,
        )
        expected_duplicates = {
            "raw_record_keys": summary.duplicate_raw_records,
            "feature_input_keys": summary.duplicate_feature_inputs,
            "split_group_ids": summary.duplicate_split_groups,
        }
        for name, expected in expected_duplicates.items():
            if set_summaries[role][name][1] != expected:
                checks.error(
                    "split_audit.sets." + role + "." + name + ".duplicate_observations",
                    "must match the authenticated statistics deduplication count",
                )

    for name in ("raw_record_keys", "feature_input_keys", "split_group_ids"):
        train_unique, _, train_digest = set_summaries["train"][name]
        validation_unique, _, validation_digest = set_summaries["validation"][name]
        if train_unique and validation_unique and train_digest == validation_digest:
            checks.error(
                "split_audit.sets." + name,
                "non-empty disjoint train and validation ordered sets cannot have identical digests",
            )

    verification = checks.mapping(audit.get("verification"), "split_audit.verification")
    for name in (
        "train_role_verified",
        "validation_role_verified",
        "same_feature_schema",
        "same_coverage_policy",
        "same_partition_config",
        "same_split_method",
        "same_split_seed",
        "same_validation_threshold",
        "same_generation_provenance",
        "same_reachability_masks",
        "manifest_hashes_distinct",
        "ledger_hashes_distinct",
        "no_shared_shard_sha256",
        "no_shared_path_or_file_identity",
        "full_record_scans",
        "full_ledger_structural_scans",
        "partition_hashes_recomputed",
        "validation_feature_inputs_decontaminated",
        "strict_eof",
    ):
        checks.truth(verification.get(name), "split_audit.verification." + name)


def validate_bundle(
    policy: Mapping[str, Any],
    train_stats: Mapping[str, Any],
    validation_stats: Mapping[str, Any],
    split_audit: Mapping[str, Any],
    feature_contract: Mapping[str, Any],
    index_coverage_contract: Mapping[str, Any],
    trajectory_ledger_contract: Mapping[str, Any],
) -> List[str]:
    """Return every schema/cross-document contract violation."""

    checks = Checks()
    dimensions, hm_training, hm_virtual = _feature_dimensions(feature_contract, checks)
    policy_summary = _validate_policy(policy, checks)
    train_summary = _validate_stats(
        train_stats,
        "train",
        policy_summary,
        dimensions,
        hm_training,
        hm_virtual,
        checks,
    )
    validation_summary = _validate_stats(
        validation_stats,
        "validation",
        policy_summary,
        dimensions,
        hm_training,
        hm_virtual,
        checks,
    )
    if train_summary.partition != validation_summary.partition:
        checks.error(
            "train_stats/validation_stats.split/provenance",
            "partition config, method, seed, threshold and provenance must match",
        )
    if not policy_summary.reachability_mask_sha256:
        checks.error(
            "policy.reachability_mask_sha256",
            "a precommitted reachability mask hash is required; the bundle cannot pass without it",
        )
    if train_summary.reachability_mask_sha256 != validation_summary.reachability_mask_sha256:
        checks.error(
            "train_stats/validation_stats.backend.reachability_mask_sha256",
            "both partitions must use the same reachability mask",
        )
    if train_summary.reachability_masks != validation_summary.reachability_masks:
        checks.error(
            "train_stats/validation_stats.backend.reachability_masks",
            "both partitions must use the same twelve reachability masks",
        )
    _validate_split_audit(
        split_audit, policy_summary, train_summary, validation_summary, checks
    )
    _validate_index_coverage_contract(
        index_coverage_contract, dimensions, hm_training, hm_virtual, checks
    )
    _validate_ledger_contract(trajectory_ledger_contract, checks)
    return checks.errors


def _validate_run_artifact(value: Any, path: str, checks: Checks) -> None:
    artifact = checks.mapping(value, path)
    file_name = artifact.get("file")
    if not isinstance(file_name, str) or not file_name:
        checks.error(path + ".file", "must be a non-empty basename")
    checks.u64(artifact.get("bytes"), path + ".bytes")
    checks.sha256(artifact.get("sha256"), path + ".sha256")


def validate_training_run_manifest(manifest: Mapping[str, Any]) -> List[str]:
    """Validate completed-run uint64 bounds and both acyclic provenance hashes."""

    checks = Checks()
    checks.sha256(manifest.get("run_definition_sha256"), "run.run_definition_sha256")
    checks.sha256(manifest.get("input_bundle_sha256"), "run.input_bundle_sha256")
    inputs = checks.mapping(manifest.get("inputs"), "run.inputs")
    for path in INPUT_ARTIFACT_PATHS:
        try:
            artifact = _nested_mapping(inputs, path)
        except (KeyError, TypeError) as exc:
            checks.error("run.inputs." + path, "is required: {}".format(exc))
            continue
        _validate_run_artifact(artifact, "run.inputs." + path, checks)

    trainer = checks.mapping(manifest.get("trainer"), "run.trainer")
    commit = trainer.get("commit")
    if not isinstance(commit, str) or GIT_COMMIT_RE.fullmatch(commit) is None:
        checks.error("run.trainer.commit", "must be a lowercase 40-hex git commit")
    checks.sha256(trainer.get("artifact_sha256"), "run.trainer.artifact_sha256")
    checks.sha256(
        trainer.get("dependency_lock_sha256"), "run.trainer.dependency_lock_sha256"
    )
    _validate_run_artifact(trainer.get("config"), "run.trainer.config", checks)
    checks.u64(trainer.get("training_seed"), "run.trainer.training_seed")
    if not isinstance(trainer.get("deterministic_algorithms"), bool):
        checks.error("run.trainer.deterministic_algorithms", "must be boolean")

    schedule = checks.mapping(manifest.get("schedule"), "run.schedule")
    checks.plain_int(schedule.get("batch_size"), "run.schedule.batch_size", 1, (1 << 32) - 1)
    checks.u64(schedule.get("optimizer_steps"), "run.schedule.optimizer_steps")
    checks.plain_int(schedule.get("epochs"), "run.schedule.epochs", 1, (1 << 32) - 1)
    checks.u64(
        schedule.get("validation_interval_steps"),
        "run.schedule.validation_interval_steps",
    )
    outputs = checks.mapping(manifest.get("outputs"), "run.outputs")
    for name in ("checkpoint", "network", "training_log", "metrics"):
        _validate_run_artifact(outputs.get(name), "run.outputs." + name, checks)

    try:
        expected_run = compute_run_definition_sha256(manifest)
    except (KeyError, TypeError, ValueError, OverflowError) as exc:
        checks.error("run.run_definition_sha256", "cannot recompute: {}".format(exc))
    else:
        if manifest.get("run_definition_sha256") != expected_run:
            checks.error(
                "run.run_definition_sha256",
                "does not authenticate trainer, config, seed and schedule",
            )
    try:
        expected_bundle = compute_input_bundle_sha256(manifest)
    except (KeyError, TypeError, ValueError, OverflowError) as exc:
        checks.error("run.input_bundle_sha256", "cannot recompute: {}".format(exc))
    else:
        if manifest.get("input_bundle_sha256") != expected_bundle:
            checks.error(
                "run.input_bundle_sha256",
                "does not authenticate all 18 inputs and run_definition_sha256",
            )
    verification = checks.mapping(manifest.get("verification"), "run.verification")
    for name in (
        "all_inputs_authenticated",
        "coverage_policy_passed",
        "split_audit_passed",
        "run_definition_recomputed",
        "checkpoint_run_definition_matches",
        "checkpoint_input_bundle_matches",
        "network_strict_reimport_passed",
        "network_engine_load_passed",
        "strict_eof",
    ):
        checks.truth(verification.get(name), "run.verification." + name)
    return checks.errors


def validate_json_schema(
    instance: Mapping[str, Any], schema: Mapping[str, Any], label: str
) -> List[str]:
    """Return deterministic Draft 2020-12 shape errors for one sidecar."""

    Draft202012Validator.check_schema(schema)
    validator = Draft202012Validator(schema)
    result: List[str] = []
    for error in sorted(validator.iter_errors(instance), key=lambda item: list(item.path)):
        path = ".".join(str(component) for component in error.path)
        result.append(
            "{}{}: JSON Schema: {}".format(label, "." + path if path else "", error.message)
        )
    return result


def derive_hm_reachability_masks(physical_mask: bytes) -> Tuple[bytes, bytes]:
    """Project the 22,528 physical HM mask to training and virtual domains."""

    if len(physical_mask) != 22_528 // 8:
        raise ValueError("physical HM reachability mask must contain 22528 bits")
    training = bytearray(24_576 // 8)
    virtual = bytearray(768 // 8)
    for physical_index in range(22_528):
        if not ((physical_mask[physical_index // 8] >> (physical_index % 8)) & 1):
            continue
        bucket, remainder_index = divmod(physical_index, 11 * 64)
        physical_plane, square = divmod(remainder_index, 64)
        training_plane = physical_plane
        if physical_plane == 10:
            own_king_square = (7 - bucket // 4) * 8 + (7 - bucket % 4)
            training_plane = 10 if square == own_king_square else 11
        training_index = bucket * 12 * 64 + training_plane * 64 + square
        virtual_index = training_plane * 64 + square
        training[training_index // 8] |= 1 << (training_index % 8)
        virtual[virtual_index // 8] |= 1 << (virtual_index % 8)
    return bytes(training), bytes(virtual)


def validate_atcov_file(
    payload: bytes,
    role: str,
    stats: Mapping[str, Any],
    feature_schema_sha256: str,
    index_schema_sha256: str,
    contract: Mapping[str, Any],
) -> List[str]:
    """Authenticate one complete ``.atcov`` payload against its stats sidecar.

    The JSON documents only declare coverage.  This parser authenticates binary
    counters and physical bitmap bytes, derives the HM training/virtual bitmaps,
    and rejects disagreement including a nonzero counter under a structural zero
    bit.  It does not reproduce the four physical masks with an independent
    symbolic oracle; that evidence remains a publication gate.
    """

    errors: List[str] = []
    label = role + "_stats.artifacts.index_coverage"
    role_ids = {"train": 0, "validation": 1}
    if role not in role_ids:
        return [label + ": unsupported role"]

    try:
        header = contract["header"]
        header_size = int(header["size"])
        expected_magic = bytes.fromhex(header["magic_hex"])
        counter_size = int(contract["counter"]["size"])
        segments = contract["segments"]
        reachability = contract["reachability_masks"]
        layouts = reachability["layout"]
        expected_file_size = int(contract["file_policy"]["file_size"])
    except (KeyError, TypeError, ValueError) as exc:
        return [label + ": invalid index-coverage contract: {}".format(exc)]

    if len(payload) != expected_file_size:
        return [
            label
            + ": binary size {} does not equal contract size {}".format(
                len(payload), expected_file_size
            )
        ]
    if header_size != 128 or counter_size != 8 or len(payload) < header_size:
        return [label + ": unsupported header or counter size in contract"]

    def uint_at(offset: int, size: int) -> int:
        return int.from_bytes(payload[offset : offset + size], "little")

    if payload[0:8] != expected_magic:
        errors.append(label + ".header.magic: does not equal contract magic")
    header_scalars = (
        ("version", 8, 2, 1),
        ("header_size", 10, 2, 128),
        ("endian_marker", 12, 4, 0x01020304),
        ("counter_size", 16, 4, 8),
        ("role", 20, 4, role_ids[role]),
    )
    for name, offset, size, expected in header_scalars:
        if uint_at(offset, size) != expected:
            errors.append(
                label + ".header.{}: must equal {}".format(name, expected)
            )

    digest_fields = (
        ("schema_sha256", 24, index_schema_sha256),
        ("feature_schema_sha256", 56, feature_schema_sha256),
    )
    for name, offset, expected in digest_fields:
        if SHA256_RE.fullmatch(expected or "") is None:
            errors.append(label + ".header.{}: invalid expected digest".format(name))
        elif payload[offset : offset + 32] != bytes.fromhex(expected):
            errors.append(
                label + ".header.{}: does not authenticate exact schema bytes".format(name)
            )

    artifacts = stats.get("artifacts")
    manifest_digest: Any = None
    if isinstance(artifacts, Mapping):
        manifest = artifacts.get("atomic_bin_v2_manifest")
        if isinstance(manifest, Mapping):
            manifest_digest = manifest.get("sha256")
    if not isinstance(manifest_digest, str) or SHA256_RE.fullmatch(manifest_digest) is None:
        errors.append(label + ".header.dataset_manifest_sha256: invalid stats digest")
    elif payload[88:120] != bytes.fromhex(manifest_digest):
        errors.append(
            label
            + ".header.dataset_manifest_sha256: does not bind the authenticated manifest"
        )

    segment_lookup: Dict[Tuple[str, str, str], Mapping[str, Any]] = {}
    expected_counter_count = 0
    if not isinstance(segments, list):
        return errors + [label + ": contract segments must be an array"]
    for raw_segment in segments:
        if not isinstance(raw_segment, Mapping):
            return errors + [label + ": contract segment must be an object"]
        try:
            key = (
                str(raw_segment["perspective"]),
                str(raw_segment["kind"]),
                str(raw_segment["slice"]),
            )
            offset = int(raw_segment["offset"])
            count = int(raw_segment["count"])
        except (KeyError, TypeError, ValueError) as exc:
            return errors + [label + ": invalid contract segment: {}".format(exc)]
        segment_lookup[key] = raw_segment
        expected_counter_count = max(expected_counter_count, offset + count)
    if uint_at(120, 8) != expected_counter_count:
        errors.append(
            label
            + ".header.counter_count: must equal {}".format(expected_counter_count)
        )

    records_raw = stats.get("scan", {}).get("records_scanned")
    if not isinstance(records_raw, str) or UINT64_RE.fullmatch(records_raw) is None:
        return errors + [label + ": stats records_scanned is not a canonical uint64"]
    records = int(records_raw)

    coverage_by_perspective = stats.get("coverage_by_perspective")
    backend = stats.get("backend")
    if not isinstance(coverage_by_perspective, Mapping) or not isinstance(backend, Mapping):
        return errors + [label + ": stats coverage/backend must be objects"]
    declared_masks = backend.get("reachability_masks")
    if not isinstance(declared_masks, Mapping):
        return errors + [label + ": backend reachability_masks must be an object"]

    layout_lookup: Dict[Tuple[str, str, str], Mapping[str, Any]] = {}
    if not isinstance(layouts, list):
        return errors + [label + ": contract bitmap layout must be an array"]
    for raw_layout in layouts:
        if not isinstance(raw_layout, Mapping):
            return errors + [label + ": contract bitmap layout entry must be an object"]
        layout_lookup[
            (
                str(raw_layout.get("perspective")),
                str(raw_layout.get("kind")),
                str(raw_layout.get("slice")),
            )
        ] = raw_layout

    def segment_values(segment: Mapping[str, Any]) -> Sequence[int]:
        offset = int(segment["offset"])
        count = int(segment["count"])
        begin = header_size + offset * counter_size
        view = memoryview(payload)[begin : begin + count * counter_size]
        return tuple(
            int.from_bytes(view[index : index + counter_size], "little")
            for index in range(0, len(view), counter_size)
        )

    for perspective_id, perspective in enumerate(("WHITE", "BLACK")):
        raw_coverage = coverage_by_perspective.get(perspective)
        raw_mask_group = declared_masks.get(perspective)
        if not isinstance(raw_coverage, Mapping) or not isinstance(raw_mask_group, Mapping):
            errors.append(label + ".{}: missing coverage or mask group".format(perspective))
            continue
        expected_hm_training = [0] * (32 * 12 * 64)
        expected_hm_virtual = [0] * (12 * 64)
        physical_hm_mask: Optional[bytes] = None

        for slice_id, (slice_name, stats_name) in enumerate(
            zip(MASK_SCHEMA_IDS, SLICE_IDS)
        ):
            segment = segment_lookup.get((perspective, "physical", slice_name))
            layout = layout_lookup.get((perspective, "physical", slice_name))
            coverage = raw_coverage.get(stats_name)
            if segment is None or layout is None or not isinstance(coverage, Mapping):
                errors.append(
                    label + ".{}.{}: missing segment, bitmap or stats".format(
                        perspective, stats_name
                    )
                )
                continue
            count = int(segment["count"])
            mask_offset = int(layout["offset"])
            mask_size = int(layout["bytes"])
            mask = payload[mask_offset : mask_offset + mask_size]
            if mask_size != (count + 7) // 8:
                errors.append(
                    label + ".{}.{}: bitmap byte count is not ceil(dimensions/8)".format(
                        perspective, stats_name
                    )
                )
                continue
            remainder = count % 8
            if remainder and mask and mask[-1] & ~((1 << remainder) - 1):
                errors.append(
                    label + ".{}.{}: unused high bitmap bits must be zero".format(
                        perspective, stats_name
                    )
                )

            mask_digest = hashlib.sha256(
                MASK_DOMAIN
                + bytes.fromhex(feature_schema_sha256)
                + bytes((perspective_id, MASK_KIND_IDS["physical"], slice_id))
                + count.to_bytes(4, "little")
                + mask_size.to_bytes(4, "little")
                + mask
            ).hexdigest()
            mask_field = MASK_FIELD_IDS[slice_id]
            if raw_mask_group.get(mask_field) != mask_digest:
                errors.append(
                    label
                    + ".{}.{}.bitmap: exact bytes do not match the declared mask hash".format(
                        perspective, stats_name
                    )
                )

            if slice_id == 0:
                physical_hm_mask = mask

            structurally_reachable = sum(BYTE_POPCOUNT[byte] for byte in mask)
            observed = 0
            occurrence = 0
            nonzero_under_zero = 0
            counters_over_records = 0
            for index, counter in enumerate(segment_values(segment)):
                occurrence += counter
                if counter > records:
                    counters_over_records += 1
                bit = (mask[index // 8] >> (index % 8)) & 1
                if counter:
                    if bit:
                        observed += 1
                    else:
                        nonzero_under_zero += 1
                if slice_id == 0:
                    king_bucket, remainder_index = divmod(index, 11 * 64)
                    physical_plane, piece_square = divmod(remainder_index, 64)
                    training_plane = physical_plane
                    if physical_plane == 10:
                        own_king_square = (7 - king_bucket // 4) * 8 + (
                            7 - king_bucket % 4
                        )
                        training_plane = 10 if piece_square == own_king_square else 11
                    training_index = (
                        king_bucket * 12 * 64 + training_plane * 64 + piece_square
                    )
                    virtual_index = training_plane * 64 + piece_square
                    expected_hm_training[training_index] += counter
                    expected_hm_virtual[virtual_index] += counter
            if nonzero_under_zero:
                errors.append(
                    label
                    + ".{}.{}: {} nonzero counters have a structural zero bit".format(
                        perspective, stats_name, nonzero_under_zero
                    )
                )
            if counters_over_records:
                errors.append(
                    label
                    + ".{}.{}: {} counters exceed records_scanned".format(
                        perspective, stats_name, counters_over_records
                    )
                )
            actual_values: Mapping[str, Any] = {
                "structurally_reachable_indices": structurally_reachable,
                "structurally_unreachable_indices": count - structurally_reachable,
                "observed_reachable_indices": observed,
                "reachable_unobserved_indices": structurally_reachable - observed,
                "occurrence_count": str(occurrence),
            }
            for field, actual in actual_values.items():
                if coverage.get(field) != actual:
                    errors.append(
                        label
                        + ".{}.{}.{}: declared value does not match binary ({})".format(
                            perspective, stats_name, field, actual
                        )
                    )

        hm = raw_coverage.get("hm_training")
        if not isinstance(hm, Mapping):
            errors.append(label + ".{}.hm_training: missing stats".format(perspective))
            continue
        if physical_hm_mask is None:
            errors.append(label + ".{}.hm_training: missing physical HM mask".format(perspective))
            continue
        expected_training_mask, expected_virtual_mask = derive_hm_reachability_masks(
            physical_hm_mask
        )
        for kind, mask_field, expected_mask, field_prefix in (
            ("training", "hm_training", expected_training_mask, "training"),
            (
                "virtual-factor",
                "hm_virtual_factors",
                expected_virtual_mask,
                "virtual_factor",
            ),
        ):
            segment = segment_lookup.get((perspective, kind, MASK_SCHEMA_IDS[0]))
            layout = layout_lookup.get((perspective, kind, MASK_SCHEMA_IDS[0]))
            if segment is None or layout is None:
                errors.append(
                    label + ".{}.{}: missing counter segment or bitmap".format(
                        perspective, kind
                    )
                )
                continue
            values = segment_values(segment)
            expected_values = (
                expected_hm_training if kind == "training" else expected_hm_virtual
            )
            if tuple(expected_values) != tuple(values):
                mismatches = sum(
                    1
                    for expected, actual in zip(expected_values, values)
                    if expected != actual
                )
                errors.append(
                    label
                    + ".{}.{}: {} counters must be derived exactly from physical HM counters".format(
                        perspective, kind, mismatches
                    )
                )
            count = int(segment["count"])
            mask_offset = int(layout["offset"])
            mask_size = int(layout["bytes"])
            mask = payload[mask_offset : mask_offset + mask_size]
            if mask_size != (count + 7) // 8:
                errors.append(
                    label
                    + ".{}.{}: bitmap byte count is not ceil(dimensions/8)".format(
                        perspective, kind
                    )
                )
                continue
            if mask != expected_mask:
                errors.append(
                    label
                    + ".{}.{}: reachability bitmap must be derived exactly from physical HM mask".format(
                        perspective, kind
                    )
                )
            mask_digest = hashlib.sha256(
                MASK_DOMAIN
                + bytes.fromhex(feature_schema_sha256)
                + bytes(
                    (
                        perspective_id,
                        MASK_KIND_IDS[kind],
                        0,
                    )
                )
                + count.to_bytes(4, "little")
                + mask_size.to_bytes(4, "little")
                + mask
            ).hexdigest()
            if raw_mask_group.get(mask_field) != mask_digest:
                errors.append(
                    label
                    + ".{}.{}.bitmap: exact bytes do not match the declared mask hash".format(
                        perspective, kind
                    )
                )
            structurally_reachable = sum(BYTE_POPCOUNT[byte] for byte in mask)
            observed = 0
            nonzero_under_zero = 0
            for index, counter in enumerate(values):
                bit = (mask[index // 8] >> (index % 8)) & 1
                if counter:
                    if bit:
                        observed += 1
                    else:
                        nonzero_under_zero += 1
            if nonzero_under_zero:
                errors.append(
                    label
                    + ".{}.{}: {} nonzero counters have a structural zero bit".format(
                        perspective, kind, nonzero_under_zero
                    )
                )
            over_records = sum(1 for counter in values if counter > records)
            if over_records:
                errors.append(
                    label
                    + ".{}.{}: {} counters exceed records_scanned".format(
                        perspective, kind, over_records
                    )
                )
            actual_fields = {
                "structurally_reachable_{}_indices".format(field_prefix): structurally_reachable,
                "structurally_unreachable_{}_indices".format(field_prefix): count
                - structurally_reachable,
                "observed_reachable_{}_indices".format(field_prefix): observed,
                "reachable_unobserved_{}_indices".format(field_prefix): structurally_reachable
                - observed,
            }
            for field, actual in actual_fields.items():
                if hm.get(field) != actual:
                    errors.append(
                        label
                        + ".{}.hm_training.{}: declared value does not match binary ({})".format(
                            perspective, field, actual
                        )
                    )

    return errors


@dataclass(frozen=True)
class ManifestShard:
    index: int
    path: Path
    records: int
    byte_count: int
    sha256: str


@dataclass(frozen=True)
class ManifestSummary:
    role: str
    path: Path
    byte_count: int
    sha256: str
    records: int
    draws: int
    atomic960: bool
    generation_sha256: str
    engine_commit: str
    network_sha256: str
    book_kind: str
    book_sha256: Optional[str]
    generation: Mapping[str, Any]
    shards: Tuple[ManifestShard, ...]


@dataclass(frozen=True)
class DatasetStructuralSummary:
    records: int
    draws: int
    wdl: Tuple[int, int, int]
    best_move_types: Tuple[int, int, int, int]
    duplicate_raw_records: int
    ordered_raw_record_sha256: str
    shard_sha256: Tuple[str, ...]
    shard_identities: Tuple[Tuple[int, int], ...]


@dataclass(frozen=True)
class LedgerStructuralSummary:
    records: int
    trajectories: int
    moves: int
    stop_reasons: Tuple[int, ...]
    move_types: Tuple[int, int, int, int]
    duplicate_split_groups: int
    ordered_split_group_sha256: str
    file_metadata: StreamFileMetadata


def _resolve_local_schema(root: Mapping[str, Any], schema: Mapping[str, Any]) -> Mapping[str, Any]:
    reference = schema.get("$ref")
    if not isinstance(reference, str):
        return schema
    if not reference.startswith("#/"):
        raise ValueError("external JSON-Schema references are not canonical-manifest inputs")
    value: Any = root
    for component in reference[2:].split("/"):
        key = component.replace("~1", "/").replace("~0", "~")
        if not isinstance(value, Mapping) or key not in value:
            raise ValueError("unresolved JSON-Schema reference {}".format(reference))
        value = value[key]
    if not isinstance(value, Mapping):
        raise ValueError("JSON-Schema reference {} is not an object".format(reference))
    return value


def _canonicalize_json_by_schema(
    value: Any, schema: Mapping[str, Any], root: Mapping[str, Any]
) -> Any:
    schema = _resolve_local_schema(root, schema)
    branches = schema.get("oneOf")
    if isinstance(branches, list):
        if not isinstance(value, Mapping):
            return value
        matches: List[Mapping[str, Any]] = []
        for branch in branches:
            if not isinstance(branch, Mapping):
                continue
            properties = branch.get("properties")
            if not isinstance(properties, Mapping):
                continue
            constants_match = True
            for key, child in properties.items():
                if (
                    key in value
                    and isinstance(child, Mapping)
                    and "const" in child
                    and value[key] != child["const"]
                ):
                    constants_match = False
                    break
            if constants_match:
                matches.append(branch)
        if len(matches) != 1:
            raise ValueError("oneOf does not select exactly one canonical branch")
        schema = _resolve_local_schema(root, matches[0])
    if isinstance(value, Mapping):
        property_items: List[Tuple[str, Mapping[str, Any]]] = []
        seen: set[str] = set()

        def add_properties(candidate: Mapping[str, Any]) -> None:
            candidate = _resolve_local_schema(root, candidate)
            properties = candidate.get("properties")
            if isinstance(properties, Mapping):
                for key, child in properties.items():
                    key_string = str(key)
                    if key_string in seen:
                        continue
                    if not isinstance(child, Mapping):
                        raise ValueError("property schema is not an object")
                    seen.add(key_string)
                    property_items.append((key_string, child))
            branches = candidate.get("allOf")
            if isinstance(branches, list):
                for branch in branches:
                    if isinstance(branch, Mapping):
                        add_properties(branch)

        add_properties(schema)
        if not property_items:
            return dict(value)
        result: Dict[str, Any] = {}
        for key, child_schema in property_items:
            if key in value:
                result[str(key)] = _canonicalize_json_by_schema(value[key], child_schema, root)
        return result
    if isinstance(value, list):
        item_schema = schema.get("items")
        if isinstance(item_schema, Mapping):
            return [_canonicalize_json_by_schema(item, item_schema, root) for item in value]
    return value


def _canonical_json_bytes(value: Mapping[str, Any], schema: Mapping[str, Any]) -> bytes:
    ordered = _canonicalize_json_by_schema(value, schema, schema)
    return (
        json.dumps(ordered, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")


def _validate_canonical_json_bytes(
    value: Mapping[str, Any],
    schema: Mapping[str, Any],
    byte_count: int,
    sha256: str,
    label: str,
) -> List[str]:
    """Require minified UTF-8 JSON + LF in schema declaration order."""

    try:
        canonical = _canonical_json_bytes(value, schema)
    except (TypeError, ValueError) as exc:
        return [label + ": cannot canonicalize JSON: {}".format(exc)]
    if byte_count != len(canonical) or sha256 != hashlib.sha256(canonical).hexdigest():
        return [label + ": JSON is not canonical in schema declaration order"]
    return []


def _read_bounded_json(path: Path, maximum: int) -> Tuple[Dict[str, Any], bytes]:
    payload, _ = _read_bounded_regular_file(path, maximum, "manifest")
    return _decode_json(payload, path), payload


def parse_atomic_bin_v2_manifest(
    role: str,
    path: Path,
    descriptor: Mapping[str, Any],
    manifest_schema: Mapping[str, Any],
    manifest_schema_sha256: str,
    data_schema_sha256: str,
) -> Tuple[Optional[ManifestSummary], List[str]]:
    """Parse the frozen canonical manifest without touching shard payloads."""

    label = role + "_manifest"
    errors: List[str] = []
    try:
        manifest, payload = _read_bounded_json(path, MAX_MANIFEST_BYTES)
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
        return None, [label + ": manifest is not valid strict JSON: {}".format(exc)]
    digest = hashlib.sha256(payload).hexdigest()
    if descriptor.get("sha256") != digest:
        errors.append(label + ": external descriptor SHA-256 does not match parsed bytes")
    declared_bytes = descriptor.get("bytes")
    if not isinstance(declared_bytes, str) or UINT64_RE.fullmatch(declared_bytes) is None:
        errors.append(label + ": external descriptor byte count is invalid")
    elif int(declared_bytes) != len(payload):
        errors.append(label + ": external descriptor byte count does not match parsed bytes")
    shape_errors = validate_json_schema(manifest, manifest_schema, label)
    errors += shape_errors
    if shape_errors:
        return None, errors
    try:
        if payload != _canonical_json_bytes(manifest, manifest_schema):
            errors.append(label + ": manifest is not canonical JSON in schema declaration order")
    except (TypeError, ValueError) as exc:
        errors.append(label + ": cannot canonicalize manifest: {}".format(exc))
    if manifest.get("manifest_schema_sha256") != manifest_schema_sha256:
        errors.append(label + ".manifest_schema_sha256: does not bind the loaded schema")
    if manifest.get("data_schema_sha256") != data_schema_sha256:
        errors.append(label + ".data_schema_sha256: does not bind the loaded data schema")

    try:
        statistics = manifest["statistics"]
        generation = manifest["generation"]
        options = generation["options"]
        records = int(statistics["records"])
        draws = int(statistics["draws"])
        requested_records = int(options["requested_records"])
        records_per_shard = int(options["records_per_shard"])
        generation_sha256 = compute_atomic_bin_v2_generation_sha256(generation)
    except (KeyError, TypeError, ValueError) as exc:
        errors.append(label + ": invalid manifest numeric field: {}".format(exc))
        return None, errors
    for name, value in (
        ("statistics.records", records),
        ("statistics.draws", draws),
        ("generation.options.requested_records", requested_records),
        ("generation.options.records_per_shard", records_per_shard),
    ):
        if value < 0 or value > MAX_U64:
            errors.append(label + ".{}: outside uint64 domain".format(name))
    if records == 0 or draws > records:
        errors.append(label + ".statistics: record/draw totals are inconsistent")
    if requested_records != records or records_per_shard == 0:
        errors.append(label + ".generation.options: requested/shard record totals are inconsistent")

    raw_shards = manifest.get("shards")
    if not isinstance(raw_shards, list) or not raw_shards:
        errors.append(label + ".shards: must contain at least one shard")
        return None, errors
    if len(raw_shards) > MAX_MANIFEST_SHARDS:
        errors.append(label + ".shards: exceeds the 100000-shard limit")
        return None, errors
    base = path.resolve().parent
    shards: List[ManifestShard] = []
    summed_records = 0
    named_paths: set[Path] = set()
    for expected_index, raw in enumerate(raw_shards):
        shard_label = "{}.shards[{}]".format(label, expected_index)
        if not isinstance(raw, Mapping):
            errors.append(shard_label + ": must be an object")
            continue
        try:
            index = int(raw["index"])
            shard_records = int(raw["records"])
            shard_bytes = int(raw["bytes"])
            file_name = raw["file"]
            sha256 = raw["sha256"]
        except (KeyError, TypeError, ValueError) as exc:
            errors.append(shard_label + ": invalid shard metadata: {}".format(exc))
            continue
        if index != expected_index:
            errors.append(shard_label + ".index: indices must be zero-based and contiguous")
        if shard_records <= 0 or shard_records > (MAX_U64 - ATOMIC_BIN_V2_HEADER_SIZE) // ATOMIC_BIN_V2_RECORD_SIZE:
            errors.append(shard_label + ".records: outside finalized file-size domain")
            continue
        expected_bytes = ATOMIC_BIN_V2_HEADER_SIZE + shard_records * ATOMIC_BIN_V2_RECORD_SIZE
        if shard_bytes != expected_bytes:
            errors.append(shard_label + ".bytes: must equal 96 + records * 64")
        if summed_records > MAX_U64 - shard_records:
            errors.append(shard_label + ".records: aggregate record count overflows uint64")
        else:
            summed_records += shard_records
        if expected_index + 1 < len(raw_shards) and shard_records != records_per_shard:
            errors.append(shard_label + ".records: non-final shard is not full")
        if expected_index + 1 == len(raw_shards) and shard_records > records_per_shard:
            errors.append(shard_label + ".records: final shard exceeds records_per_shard")
        candidate = (base / str(file_name)).resolve()
        if candidate.parent != base:
            errors.append(shard_label + ".file: must resolve inside the manifest directory")
        if candidate in named_paths:
            errors.append(shard_label + ".file: manifest repeats a shard pathname")
        named_paths.add(candidate)
        shards.append(ManifestShard(index, candidate, shard_records, shard_bytes, str(sha256)))
    if summed_records != records:
        errors.append(label + ".shards: shard records do not match manifest statistics")
    if shards:
        expected_manifest = Path(str(shards[0].path) + ".manifest.json")
        if path.resolve() != expected_manifest:
            errors.append(label + ": sidecar path does not match its first shard")
    if errors:
        return None, errors
    return (
        ManifestSummary(
            role,
            path.resolve(),
            len(payload),
            digest,
            records,
            draws,
            bool(generation["atomic960"]),
            generation_sha256,
            str(manifest["engine"]["commit"]),
            str(manifest["network"]["sha256"]),
            str(manifest["book"]["kind"]),
            manifest["book"]["sha256"],
            generation,
            tuple(shards),
        ),
        errors,
    )


def _piece_side(piece: int) -> Optional[int]:
    if 1 <= piece <= 6:
        return 0
    if 7 <= piece <= 12:
        return 1
    return None


def _pawn_code(side: int) -> int:
    return 1 if side == 0 else 7


def _rook_code(side: int) -> int:
    return 4 if side == 0 else 10


def _king_code(side: int) -> int:
    return 6 if side == 0 else 12


def _decode_structural_position(
    payload: Any, offset: int, atomic960: bool
) -> Tuple[Optional[Tuple[List[int], int, int, Tuple[int, ...], int]], Optional[str]]:
    if payload[offset + 39] != 0 or payload[offset + 46] != 0 or payload[offset + 47] != 0:
        return None, "position reserved bytes are nonzero"
    board: List[int] = []
    for packed in payload[offset : offset + 32]:
        board.extend((int(packed) & 0x0F, (int(packed) >> 4) & 0x0F))
    if any(piece > 12 for piece in board):
        return None, "position contains a reserved piece code"
    for color, name in ((0, "WHITE"), (1, "BLACK")):
        piece_count = sum(_piece_side(piece) == color for piece in board)
        if piece_count > 16:
            return (
                None,
                "position has {} {} pieces; dataset proof domain permits at most "
                "16 per color".format(piece_count, name),
            )
    side = int(payload[offset + 32])
    if side not in (0, 1):
        return None, "side-to-move is outside its enum domain"
    if board.count(6) != 1 or board.count(12) != 1:
        return None, "position requires exactly one king per color"
    rights = int(payload[offset + 33])
    if rights & ~0x0F:
        return None, "castling rights contain reserved bits"
    origins = tuple(int(value) for value in payload[offset + 34 : offset + 38])
    kings = (board.index(6), board.index(12))
    standard_rooks = (7, 0, 63, 56)
    for index, origin in enumerate(origins):
        enabled = bool(rights & (1 << index))
        if not enabled:
            if origin != 0xFF:
                return None, "castling rook origin exists without its right"
            continue
        if origin >= 64:
            return None, "castling rook origin is out of range"
        piece_side = 0 if index < 2 else 1
        king_square = kings[piece_side]
        home_rank = 0 if piece_side == 0 else 7
        if king_square // 8 != home_rank or origin // 8 != home_rank:
            return None, "castling pieces are not on their home rank"
        if board[origin] != _rook_code(piece_side):
            return None, "castling origin does not contain the right rook"
        king_side = index % 2 == 0
        if (king_side and origin % 8 <= king_square % 8) or (
            not king_side and origin % 8 >= king_square % 8
        ):
            return None, "castling rook is on the wrong side of its king"
        if not atomic960 and (
            origin != standard_rooks[index]
            or king_square != (4 if piece_side == 0 else 60)
        ):
            return None, "non-960 castling does not use orthodox origins"
    if origins[0] != 0xFF and origins[0] == origins[1]:
        return None, "white castling origins are duplicated"
    if origins[2] != 0xFF and origins[2] == origins[3]:
        return None, "black castling origins are duplicated"

    ep_square = int(payload[offset + 38])
    if ep_square != 0xFF:
        if ep_square >= 64:
            return None, "en-passant square is out of range"
        expected_rank = 5 if side == 0 else 2
        if ep_square // 8 != expected_rank or board[ep_square] != 0:
            return None, "en-passant square has invalid rank or occupancy"
        captured_square = ep_square + (-8 if side == 0 else 8)
        if not 0 <= captured_square < 64 or board[captured_square] != _pawn_code(side ^ 1):
            return None, "en-passant target has no capturable pawn"
        source_rank = expected_rank + (-1 if side == 0 else 1)
        has_capturer = any(
            0 <= ep_square % 8 + delta < 8
            and board[source_rank * 8 + ep_square % 8 + delta] == _pawn_code(side)
            for delta in (-1, 1)
        )
        if not has_capturer:
            return None, "en-passant target has no capturing pawn"
        vacated_square = ep_square + (8 if side == 0 else -8)
        if not 0 <= vacated_square < 64 or board[vacated_square] != 0:
            return None, "en-passant pawn start square is not empty"
    rule50 = struct.unpack_from("<H", payload, offset + 40)[0]
    fullmove = struct.unpack_from("<I", payload, offset + 42)[0]
    if fullmove == 0:
        return None, "fullmove must be at least one"
    if rule50 > 32767 or fullmove > 100000:
        return None, "position clocks exceed engine-origin limits"
    return (board, side, rights, origins, ep_square), None


def _decode_structural_move(wire: int) -> Tuple[Optional[Tuple[int, int, int, int]], Optional[str]]:
    if wire >> 20:
        return None, "move reserved bits are nonzero"
    from_square = wire & 0x3F
    to_square = (wire >> 6) & 0x3F
    move_type = (wire >> 12) & 0x0F
    promotion = (wire >> 16) & 0x0F
    if from_square == to_square:
        return None, "move squares are invalid"
    if move_type > 3:
        return None, "move type is outside its enum domain"
    if promotion > 4:
        return None, "promotion is outside its enum domain"
    if (move_type == 1) != (promotion != 0):
        return None, "move type and promotion are inconsistent"
    return (from_square, to_square, move_type, promotion), None


def _validate_move_for_position(
    move: Tuple[int, int, int, int],
    position: Tuple[List[int], int, int, Tuple[int, ...], int],
) -> Optional[str]:
    from_square, to_square, move_type, _ = move
    board, side, rights, origins, ep_square = position
    moving = board[from_square]
    target = board[to_square]
    if moving == 0 or _piece_side(moving) != side:
        return "move source has no side-to-move piece"
    if target != 0 and _piece_side(target) == side and move_type != 3:
        return "move captures a friendly piece"
    from_rank, to_rank = from_square // 8, to_square // 8
    from_file, to_file = from_square % 8, to_square % 8
    forward = 1 if side == 0 else -1
    if move_type == 0:
        if moving == _pawn_code(side) and to_rank in (0, 7):
            return "last-rank pawn move must be a promotion"
    elif move_type == 1:
        if (
            moving != _pawn_code(side)
            or to_rank - from_rank != forward
            or to_rank not in (0, 7)
            or abs(to_file - from_file) > 1
        ):
            return "promotion geometry is invalid"
    elif move_type == 2:
        if (
            moving != _pawn_code(side)
            or to_square != ep_square
            or target != 0
            or to_rank - from_rank != forward
            or abs(to_file - from_file) != 1
        ):
            return "en-passant move is inconsistent with position"
    elif move_type == 3:
        if moving != _king_code(side) or target != _rook_code(side):
            return "castling move does not target its own rook"
        first = 0 if side == 0 else 2
        if not any(
            bool(rights & (1 << index)) and origins[index] == to_square
            for index in range(first, first + 2)
        ):
            return "castling move has no matching right"
    return None


def _validate_structural_record(
    record: Any, atomic960: bool
) -> Tuple[Optional[Tuple[int, int]], Optional[str]]:
    if len(record) != ATOMIC_BIN_V2_RECORD_SIZE:
        return None, "record is truncated"
    if record[62] != 0 or record[63] != 0:
        return None, "record reserved bytes are nonzero"
    flags = int(record[61])
    if flags & ~1:
        return None, "record flags contain reserved bits"
    if bool(flags & 1) != atomic960:
        return None, "record Atomic960 flag differs from manifest generation mode"
    position, error = _decode_structural_position(record, 0, bool(flags & 1))
    if error is not None or position is None:
        return None, error
    score = struct.unpack_from("<i", record, 48)[0]
    if score == -(1 << 31):
        return None, "score is outside the initial domain"
    wire = struct.unpack_from("<I", record, 52)[0]
    move, error = _decode_structural_move(wire)
    if error is not None or move is None:
        return None, error
    error = _validate_move_for_position(move, position)
    if error is not None:
        return None, error
    result_wire = int(record[60])
    if result_wire == 0xFF:
        result = -1
    elif result_wire in (0, 1):
        result = result_wire
    else:
        return None, "result must be -1, 0, or 1"
    return (result, move[2]), None


def _same_open_file(before: os.stat_result, after: os.stat_result, byte_count: int) -> bool:
    return _stat_change_token(before) == _stat_change_token(after) and byte_count == before.st_size


def scan_atomic_bin_v2_dataset(
    manifest: ManifestSummary,
    data_schema_sha256: str,
    identity_index: "SplitGroupIndex",
) -> Tuple[Optional[DatasetStructuralSummary], List[str]]:
    """Authenticate every shard and structurally scan every 64-byte record."""

    errors: List[str] = []
    draws = 0
    wdl = [0, 0, 0]  # loss, draw, win; indexed with result + 1
    best_move_types = [0, 0, 0, 0]
    shard_digests: List[str] = []
    shard_identities: List[Tuple[int, int]] = []
    seen_identities: set[Tuple[int, int]] = set()
    expected_schema = bytes.fromhex(data_schema_sha256)
    records_seen = 0
    duplicate_raw_records = 0
    role_id = 0 if manifest.role == "train" else 1
    for shard in manifest.shards:
        label = "{}_manifest.shards[{}]".format(manifest.role, shard.index)
        try:
            digest = hashlib.sha256()
            byte_count = 0
            with shard.path.open("rb") as stream:
                before = os.fstat(stream.fileno())
                if not stat.S_ISREG(before.st_mode):
                    raise ValueError("shard is not a regular file")
                identity = (int(before.st_dev), int(before.st_ino))
                if identity in seen_identities:
                    raise ValueError("manifest repeats a shard file identity")
                seen_identities.add(identity)
                if before.st_size != shard.byte_count:
                    raise ValueError("shard size differs from manifest")
                header = stream.read(ATOMIC_BIN_V2_HEADER_SIZE)
                digest.update(header)
                byte_count += len(header)
                if len(header) != ATOMIC_BIN_V2_HEADER_SIZE:
                    raise ValueError("shard header is truncated")
                magic, version, header_size, endian, record_size, flags = struct.unpack_from(
                    "<8sHHIII", header, 0
                )
                header_records = struct.unpack_from("<Q", header, 56)[0]
                if magic != b"ATBINV2\0":
                    raise ValueError("shard header magic mismatch")
                if version != 2 or header_size != 96 or endian != 0x01020304:
                    raise ValueError("shard header capability fields mismatch")
                if record_size != 64 or flags != 0:
                    raise ValueError("shard record-size/flags mismatch")
                if header[24:56] != expected_schema:
                    raise ValueError("shard data schema SHA-256 mismatch")
                if header_records != shard.records:
                    raise ValueError("shard header record count differs from manifest")
                if any(header[64:96]):
                    raise ValueError("shard header reserved bytes are nonzero")

                remaining = shard.records
                local_index = 0
                records_per_chunk = max(1, STREAM_CHUNK_SIZE // ATOMIC_BIN_V2_RECORD_SIZE)
                while remaining:
                    chunk_records = min(remaining, records_per_chunk)
                    requested = chunk_records * ATOMIC_BIN_V2_RECORD_SIZE
                    chunk = stream.read(requested)
                    digest.update(chunk)
                    byte_count += len(chunk)
                    if len(chunk) != requested:
                        raise ValueError("shard record stream is truncated")
                    view = memoryview(chunk)
                    for offset in range(0, requested, ATOMIC_BIN_V2_RECORD_SIZE):
                        record = view[offset : offset + ATOMIC_BIN_V2_RECORD_SIZE]
                        outcome, error = _validate_structural_record(record, manifest.atomic960)
                        if error is not None or outcome is None:
                            raise ValueError(
                                "record {} is structurally invalid: {}".format(local_index, error)
                            )
                        result, move_type = outcome
                        raw_digest = hashlib.sha256(b"atomic-record-key-v1\0")
                        raw_digest.update(record)
                        if not identity_index.add_raw_record(role_id, raw_digest.digest()):
                            duplicate_raw_records += 1
                        wdl[result + 1] += 1
                        draws += int(result == 0)
                        best_move_types[move_type] += 1
                        local_index += 1
                    remaining -= chunk_records
                if stream.read(1):
                    raise ValueError("shard has trailing bytes")
                after = os.fstat(stream.fileno())
            if not _same_open_file(before, after, byte_count):
                raise ValueError("shard changed while being authenticated and scanned")
            actual_digest = digest.hexdigest()
            if actual_digest != shard.sha256:
                raise ValueError("shard SHA-256 differs from manifest")
            shard_digests.append(actual_digest)
            shard_identities.append(identity)
            records_seen += shard.records
        except (OSError, ValueError) as exc:
            errors.append(label + ": " + str(exc))
            return None, errors
    if records_seen != manifest.records:
        errors.append(manifest.role + "_dataset: streamed record count differs from manifest")
    if draws != manifest.draws:
        errors.append(manifest.role + "_dataset: streamed draw count differs from manifest")
    if errors:
        return None, errors
    identity_index.finish_role()
    unique_raw_records, ordered_raw_digest = identity_index.ordered_summary(
        role_id, "raw_records"
    )
    if unique_raw_records + duplicate_raw_records != records_seen:
        return None, [manifest.role + "_dataset: raw-record identity accounting is inconsistent"]
    return (
        DatasetStructuralSummary(
            records_seen,
            draws,
            tuple(wdl),
            tuple(best_move_types),
            duplicate_raw_records,
            ordered_raw_digest,
            tuple(shard_digests),
            tuple(shard_identities),
        ),
        errors,
    )


class SplitGroupIndex:
    """Disk-backed exact sets used for multi-billion-record identities."""

    def __init__(self, directory: Path) -> None:
        self.path = directory / "split-groups.sqlite3"
        self.connection = sqlite3.connect(str(self.path))
        self.connection.execute("PRAGMA journal_mode=OFF")
        self.connection.execute("PRAGMA synchronous=OFF")
        self.connection.execute("PRAGMA temp_store=FILE")
        self.connection.execute(
            "CREATE TABLE split_groups ("
            "role INTEGER NOT NULL, key BLOB NOT NULL, "
            "PRIMARY KEY (role, key)) WITHOUT ROWID"
        )
        self.connection.execute(
            "CREATE TABLE raw_records ("
            "role INTEGER NOT NULL, key BLOB NOT NULL, "
            "PRIMARY KEY (role, key)) WITHOUT ROWID"
        )

    def _add(self, table: str, role: int, key: bytes) -> bool:
        if table not in ("split_groups", "raw_records"):
            raise ValueError("unknown exact identity table")
        cursor = self.connection.execute(
            "INSERT OR IGNORE INTO {}(role, key) VALUES (?, ?)".format(table),
            (role, sqlite3.Binary(key)),
        )
        return cursor.rowcount == 1

    def add(self, role: int, key: bytes) -> bool:
        return self._add("split_groups", role, key)

    def add_raw_record(self, role: int, key: bytes) -> bool:
        return self._add("raw_records", role, key)

    def finish_role(self) -> None:
        self.connection.commit()

    def ordered_summary(self, role: int, table: str = "split_groups") -> Tuple[int, str]:
        if table not in ("split_groups", "raw_records"):
            raise ValueError("unknown exact identity table")
        count = int(
            self.connection.execute(
                "SELECT COUNT(*) FROM {} WHERE role = ?".format(table), (role,)
            ).fetchone()[0]
        )
        digest = hashlib.sha256(b"atomic-ordered-set-v1\0" + count.to_bytes(8, "little"))
        cursor = self.connection.execute(
            "SELECT key FROM {} WHERE role = ? ORDER BY key".format(table), (role,)
        )
        for (key,) in cursor:
            digest.update(bytes(key))
        return count, digest.hexdigest()

    def intersection_count(self, table: str = "split_groups") -> int:
        if table not in ("split_groups", "raw_records"):
            raise ValueError("unknown exact identity table")
        return int(
            self.connection.execute(
                "SELECT COUNT(*) FROM {0} AS train "
                "JOIN {0} AS validation ON train.key = validation.key "
                "WHERE train.role = 0 AND validation.role = 1".format(table)
            ).fetchone()[0]
        )

    def close(self) -> None:
        self.connection.close()


def _read_exact_and_hash(stream: BinaryIO, size: int, digest: Any) -> bytes:
    payload = stream.read(size)
    digest.update(payload)
    if len(payload) != size:
        raise ValueError("unexpected EOF")
    return payload


def scan_atomic_trajectory_ledger(
    role: str,
    path: Path,
    descriptor: Mapping[str, Any],
    manifest: ManifestSummary,
    policy: Mapping[str, Any],
    ledger_contract: Mapping[str, Any],
    ledger_schema_sha256: str,
    data_schema_sha256: str,
    split_index: SplitGroupIndex,
) -> Tuple[Optional[LedgerStructuralSummary], List[str]]:
    """Authenticate and structurally scan an ATTRAJ1 ledger in bounded memory.

    The two file handles advance sequentially: the primary handle authenticates
    every byte and consumes the move stream, while the second re-reads one fixed
    entry at a time to seed its split-group hash.  This deliberately does *not*
    claim legal position replay or terminal/adjudication semantics.
    """

    label = role + "_ledger"
    errors: List[str] = []
    expected_role = 0 if role == "train" else 1
    digest = hashlib.sha256()
    byte_count = 0
    duplicate_split_groups = 0
    stop_reasons = [0] * 9
    move_types = [0] * 4
    try:
        with path.open("rb") as stream:
            before = os.fstat(stream.fileno())
            if not stat.S_ISREG(before.st_mode):
                raise ValueError("ledger is not a regular file")
            declared_bytes = descriptor.get("bytes")
            if not isinstance(declared_bytes, str) or UINT64_RE.fullmatch(declared_bytes) is None:
                raise ValueError("external ledger byte count is invalid")
            if int(declared_bytes) != before.st_size:
                raise ValueError("external ledger byte count differs from file")
            header = _read_exact_and_hash(stream, ATOMIC_TRAJECTORY_HEADER_SIZE, digest)
            byte_count += len(header)
            (
                magic,
                version,
                header_size,
                endian,
                entry_size,
                header_role,
                schema_sha,
                manifest_sha,
                data_sha,
                record_count,
                trajectory_count,
                move_count,
                entries_offset,
                moves_offset,
            ) = struct.unpack("<8sHHIII32s32s32sQQQQQ", header)
            if magic != b"ATTRAJ1\0":
                raise ValueError("ledger magic mismatch")
            if version != 1 or header_size != 160 or endian != 0x01020304:
                raise ValueError("ledger header capability fields mismatch")
            if entry_size != 112:
                raise ValueError("ledger entry size mismatch")
            if header_role != expected_role:
                raise ValueError("ledger header role does not match statistics role")
            if schema_sha != bytes.fromhex(ledger_schema_sha256):
                raise ValueError("ledger schema SHA-256 mismatch")
            if manifest_sha != bytes.fromhex(manifest.sha256):
                raise ValueError("ledger does not bind the authenticated manifest")
            if data_sha != bytes.fromhex(data_schema_sha256):
                raise ValueError("ledger data schema SHA-256 mismatch")
            if record_count != manifest.records:
                raise ValueError("ledger record count differs from manifest")
            if trajectory_count == 0 or move_count == 0:
                raise ValueError("finalized ledger counts must be nonzero")
            if trajectory_count > (MAX_U64 - 160) // 112:
                raise ValueError("ledger trajectory count overflows file layout")
            expected_moves_offset = 160 + trajectory_count * 112
            if entries_offset != 160 or moves_offset != expected_moves_offset:
                raise ValueError("ledger entries/moves offsets do not match contract")
            if move_count > (MAX_U64 - moves_offset) // 4:
                raise ValueError("ledger move count overflows file layout")
            expected_file_size = moves_offset + move_count * 4
            if before.st_size != expected_file_size:
                raise ValueError("ledger file size does not match header counts")

            expected_record = 0
            expected_move = 0
            release_candidate = policy.get("status") == "release-candidate"
            for index in range(trajectory_count):
                entry = _read_exact_and_hash(stream, 112, digest)
                byte_count += 112
                (
                    _,
                    root_position,
                    first_record,
                    entry_records,
                    entry_moves,
                    first_move,
                    terminal_result,
                    atomic960,
                    stop_reason,
                    reserved,
                ) = struct.unpack("<32s48sQIIQbBB5s", entry)
                if first_record != expected_record:
                    raise ValueError("ledger record ranges are not contiguous")
                if entry_records == 0 or expected_record > MAX_U64 - entry_records:
                    raise ValueError("ledger record range is empty or overflows")
                expected_record += entry_records
                if first_move != expected_move:
                    raise ValueError("ledger move ranges are not contiguous")
                if entry_moves == 0 or expected_move > MAX_U64 - entry_moves:
                    raise ValueError("ledger move range is empty or overflows")
                expected_move += entry_moves
                if terminal_result not in (-1, 0, 1):
                    raise ValueError("ledger terminal result is outside its domain")
                if atomic960 not in (0, 1) or bool(atomic960) != manifest.atomic960:
                    raise ValueError("ledger Atomic960 mode differs from manifest")
                if stop_reason > 8:
                    raise ValueError("ledger stop reason is outside its domain")
                if release_candidate and stop_reason in (7, 8):
                    raise ValueError(
                        "release-candidate ledger contains adjudicated stop reason {} "
                        "without engine-backed semantic replay evidence".format(stop_reason)
                    )
                if reserved != bytes(5):
                    raise ValueError("ledger entry reserved bytes are nonzero")
                _, position_error = _decode_structural_position(root_position, 0, bool(atomic960))
                if position_error is not None:
                    raise ValueError(
                        "ledger root position {} is structurally invalid: {}".format(
                            index, position_error
                        )
                    )
                stop_reasons[stop_reason] += 1
            if expected_record != record_count:
                raise ValueError("ledger record ranges do not cover the manifest dataset")
            if expected_move != move_count:
                raise ValueError("ledger move ranges do not cover the move stream")

            with path.open("rb") as entry_stream:
                entry_before = os.fstat(entry_stream.fileno())
                if _stat_change_token(entry_before) != _stat_change_token(before):
                    raise ValueError("ledger identity changed before move scan")
                entry_stream.seek(160)
                group_domain = bytes.fromhex(
                    str(ledger_contract["split_group_id"]["domain_ascii_hex"])
                )
                partition_domain = bytes.fromhex(
                    str(ledger_contract["partition"]["domain_ascii_hex"])
                )
                partition = policy["partition"]
                split_seed = int(partition["split_seed"])
                validation_threshold = int(partition["validation_threshold_u64"])
                for index in range(trajectory_count):
                    entry = entry_stream.read(112)
                    if len(entry) != 112:
                        raise ValueError("ledger entry re-read was truncated")
                    (
                        expected_group,
                        root_position,
                        _,
                        _,
                        entry_moves,
                        _,
                        _,
                        atomic960,
                        _,
                        _,
                    ) = struct.unpack("<32s48sQIIQbBB5s", entry)
                    group_digest = hashlib.sha256(
                        group_domain
                        + root_position
                        + bytes((atomic960,))
                        + int(entry_moves).to_bytes(8, "little")
                    )
                    remaining_moves = entry_moves
                    while remaining_moves:
                        chunk_moves = min(remaining_moves, STREAM_CHUNK_SIZE // 4)
                        payload = _read_exact_and_hash(stream, chunk_moves * 4, digest)
                        byte_count += len(payload)
                        group_digest.update(payload)
                        for (wire,) in struct.iter_unpack("<I", payload):
                            move, move_error = _decode_structural_move(wire)
                            if move_error is not None or move is None:
                                raise ValueError(
                                    "trajectory {} contains invalid move wire: {}".format(
                                        index, move_error
                                    )
                                )
                            move_types[move[2]] += 1
                        remaining_moves -= chunk_moves
                    actual_group = group_digest.digest()
                    if actual_group != expected_group:
                        raise ValueError(
                            "ledger split_group_id does not match trajectory {} bytes".format(index)
                        )
                    partition_digest = hashlib.sha256(
                        partition_domain
                        + split_seed.to_bytes(8, "little")
                        + actual_group
                    ).digest()
                    is_validation = (
                        int.from_bytes(partition_digest[:8], "little") < validation_threshold
                    )
                    if is_validation != (role == "validation"):
                        target = "validation" if is_validation else "train"
                        raise ValueError(
                            "trajectory partitions to {}, not {}".format(target, role)
                        )
                    if not split_index.add(expected_role, actual_group):
                        duplicate_split_groups += 1
                entry_after = os.fstat(entry_stream.fileno())
                if _stat_change_token(entry_before) != _stat_change_token(entry_after):
                    raise ValueError("ledger changed while entries were re-read")
            if stream.read(1):
                raise ValueError("ledger has trailing bytes")
            after = os.fstat(stream.fileno())
        if not _same_open_file(before, after, byte_count):
            raise ValueError("ledger changed while being authenticated and scanned")
        actual_digest = digest.hexdigest()
        if descriptor.get("sha256") != actual_digest:
            raise ValueError("external ledger SHA-256 does not authenticate scanned bytes")
        split_index.finish_role()
        unique_groups, ordered_digest = split_index.ordered_summary(expected_role)
        if unique_groups + duplicate_split_groups != trajectory_count:
            raise ValueError("ledger split-group identity accounting is inconsistent")
        return (
            LedgerStructuralSummary(
                record_count,
                trajectory_count,
                move_count,
                tuple(stop_reasons),
                tuple(move_types),
                duplicate_split_groups,
                ordered_digest,
                StreamFileMetadata(
                    byte_count,
                    actual_digest,
                    (int(before.st_dev), int(before.st_ino)),
                    _stat_change_token(before),
                ),
            ),
            errors,
        )
    except (OSError, ValueError, KeyError, TypeError, OverflowError, sqlite3.Error) as exc:
        errors.append(label + ": " + str(exc))
        return None, errors


def _canonical_u64(value: Any) -> Optional[int]:
    if not isinstance(value, str) or UINT64_RE.fullmatch(value) is None:
        return None
    parsed = int(value)
    return parsed if parsed <= MAX_U64 else None


def _reconcile_streamed_role(
    role: str,
    stats: Mapping[str, Any],
    manifest: ManifestSummary,
    dataset: DatasetStructuralSummary,
    ledger: LedgerStructuralSummary,
    audit: Mapping[str, Any],
) -> List[str]:
    errors: List[str] = []

    def require_u64(value: Any, expected: int, path: str) -> None:
        actual = _canonical_u64(value)
        if actual != expected:
            errors.append(path + ": declared value does not match streamed artifacts ({})".format(expected))

    scan = stats.get("scan")
    split = stats.get("split")
    distribution = stats.get("distribution")
    record_events = stats.get("record_events")
    trajectory_events = stats.get("trajectory_events")
    deduplication = stats.get("deduplication")
    for value, name in (
        (scan, "scan"),
        (split, "split"),
        (distribution, "distribution"),
        (record_events, "record_events"),
        (trajectory_events, "trajectory_events"),
        (deduplication, "deduplication"),
    ):
        if not isinstance(value, Mapping):
            errors.append("{}_stats.{}: must be an object".format(role, name))
    if errors:
        return errors
    assert isinstance(scan, Mapping)
    assert isinstance(split, Mapping)
    assert isinstance(distribution, Mapping)
    assert isinstance(record_events, Mapping)
    assert isinstance(trajectory_events, Mapping)
    assert isinstance(deduplication, Mapping)

    require_u64(scan.get("records_scanned"), dataset.records, role + "_stats.scan.records_scanned")
    require_u64(split.get("ledger_record_count"), ledger.records, role + "_stats.split.ledger_record_count")
    require_u64(
        split.get("ledger_trajectory_count"),
        ledger.trajectories,
        role + "_stats.split.ledger_trajectory_count",
    )
    wdl = distribution.get("wdl_side_to_move")
    if isinstance(wdl, Mapping):
        require_u64(wdl.get("loss"), dataset.wdl[0], role + "_stats.distribution.wdl_side_to_move.loss")
        require_u64(wdl.get("draw"), dataset.wdl[1], role + "_stats.distribution.wdl_side_to_move.draw")
        require_u64(wdl.get("win"), dataset.wdl[2], role + "_stats.distribution.wdl_side_to_move.win")
    else:
        errors.append(role + "_stats.distribution.wdl_side_to_move: must be an object")
    for field, move_type in (
        ("promotion_best_moves", 1),
        ("en_passant_best_moves", 2),
        ("castling_best_moves", 3),
    ):
        require_u64(
            record_events.get(field),
            dataset.best_move_types[move_type],
            "{}_stats.record_events.{}".format(role, field),
        )
    require_u64(
        record_events.get("atomic960_records"),
        dataset.records if manifest.atomic960 else 0,
        role + "_stats.record_events.atomic960_records",
    )
    for field, move_type in (
        ("promotion_moves", 1),
        ("en_passant_moves", 2),
        ("castling_moves", 3),
    ):
        require_u64(
            trajectory_events.get(field),
            ledger.move_types[move_type],
            "{}_stats.trajectory_events.{}".format(role, field),
        )
    declared_stop_reasons = trajectory_events.get("stop_reasons")
    if not isinstance(declared_stop_reasons, list) or len(declared_stop_reasons) != 9:
        errors.append(role + "_stats.trajectory_events.stop_reasons: must contain nine counters")
    else:
        for index, expected in enumerate(ledger.stop_reasons):
            require_u64(
                declared_stop_reasons[index],
                expected,
                "{}_stats.trajectory_events.stop_reasons[{}]".format(role, index),
            )
    require_u64(
        deduplication.get("duplicate_raw_records"),
        dataset.duplicate_raw_records,
        role + "_stats.deduplication.duplicate_raw_records",
    )
    require_u64(
        deduplication.get("duplicate_split_groups"),
        ledger.duplicate_split_groups,
        role + "_stats.deduplication.duplicate_split_groups",
    )

    sets = audit.get("sets")
    role_sets = sets.get(role) if isinstance(sets, Mapping) else None
    raw_records = role_sets.get("raw_record_keys") if isinstance(role_sets, Mapping) else None
    if isinstance(raw_records, Mapping):
        unique_raw = dataset.records - dataset.duplicate_raw_records
        require_u64(
            raw_records.get("observations"),
            dataset.records,
            "split_audit.sets.{}.raw_record_keys.observations".format(role),
        )
        require_u64(
            raw_records.get("unique_keys"),
            unique_raw,
            "split_audit.sets.{}.raw_record_keys.unique_keys".format(role),
        )
        require_u64(
            raw_records.get("duplicate_observations"),
            dataset.duplicate_raw_records,
            "split_audit.sets.{}.raw_record_keys.duplicate_observations".format(role),
        )
        if raw_records.get("ordered_set_sha256") != dataset.ordered_raw_record_sha256:
            errors.append(
                "split_audit.sets.{}.raw_record_keys.ordered_set_sha256: "
                "does not match the streamed dataset set".format(role)
            )
    else:
        errors.append("split_audit.sets.{}.raw_record_keys: must be an object".format(role))
    split_groups = role_sets.get("split_group_ids") if isinstance(role_sets, Mapping) else None
    if isinstance(split_groups, Mapping):
        unique = ledger.trajectories - ledger.duplicate_split_groups
        require_u64(
            split_groups.get("observations"),
            ledger.trajectories,
            "split_audit.sets.{}.split_group_ids.observations".format(role),
        )
        require_u64(
            split_groups.get("unique_keys"),
            unique,
            "split_audit.sets.{}.split_group_ids.unique_keys".format(role),
        )
        require_u64(
            split_groups.get("duplicate_observations"),
            ledger.duplicate_split_groups,
            "split_audit.sets.{}.split_group_ids.duplicate_observations".format(role),
        )
        if split_groups.get("ordered_set_sha256") != ledger.ordered_split_group_sha256:
            errors.append(
                "split_audit.sets.{}.split_group_ids.ordered_set_sha256: "
                "does not match the streamed ledger set".format(role)
            )
    else:
        errors.append("split_audit.sets.{}.split_group_ids: must be an object".format(role))
    return errors


def _validate_manifest_provenance(
    role: str, manifest: ManifestSummary, stats: Mapping[str, Any], policy: Mapping[str, Any]
) -> List[str]:
    errors: List[str] = []
    try:
        provenance = policy["partition"]["provenance"]
        profile = provenance["generation_profile"]
        generation = manifest.generation
        options = generation["options"]
        actual_generation_sha256 = compute_atomic_bin_v2_generation_sha256(generation)
        if profile["atomic_bin_v2_generation_sha256"] != actual_generation_sha256:
            errors.append(
                role
                + "_manifest.generation: canonical generation hash differs from partition provenance"
            )
        if manifest.engine_commit != provenance["engine_commit"]:
            errors.append(role + "_manifest.engine.commit: differs from partition provenance")
        if manifest.network_sha256 != provenance["teacher_network_sha256"]:
            errors.append(role + "_manifest.network.sha256: differs from partition provenance")
        if manifest.book_sha256 != provenance["opening_book_sha256"]:
            errors.append(role + "_manifest.book.sha256: differs from partition provenance")
        if int(generation["resolved_seed"]) != int(profile["generation_seed"]):
            errors.append(role + "_manifest.generation.resolved_seed: differs from partition provenance")
        expected_opening_mode = (
            "builtin-startpos" if manifest.book_kind == "builtin-startpos" else "authenticated-book"
        )
        if profile["opening_mode"] != expected_opening_mode:
            errors.append(role + "_manifest.book.kind: differs from partition generation profile")
        for profile_field, option_field in (
            ("exclude_captures", "filter_captures"),
            ("exclude_promotions", "filter_promotions"),
            ("exclude_checks", "filter_checks"),
        ):
            if bool(profile[profile_field]) != bool(options[option_field]):
                errors.append(
                    "{}_manifest.generation.options.{}: differs from partition generation profile".format(
                        role, option_field
                    )
                )
        if policy.get("status") == "release-candidate":
            if options.get("adjudicate_draws_by_score") is not False:
                errors.append(
                    role
                    + "_manifest.generation.options.adjudicate_draws_by_score: "
                    "release-candidate generation must disable score-draw adjudication"
                )
            if profile.get("adjudicate_resignations") is not False:
                errors.append(
                    role
                    + "_manifest.generation: release-candidate V3 resignation attestation must be false"
                )
    except (ValueError, KeyError, TypeError) as exc:
        errors.append(role + "_manifest.provenance: cannot reconcile manifest: {}".format(exc))
    return errors


def validate_streamed_bundle_artifacts(
    policy: Mapping[str, Any],
    train_stats: Mapping[str, Any],
    validation_stats: Mapping[str, Any],
    split_audit: Mapping[str, Any],
    train_artifacts: Mapping[str, Path],
    validation_artifacts: Mapping[str, Path],
    manifest_schema: Mapping[str, Any],
    manifest_schema_sha256: str,
    data_schema_sha256: str,
    ledger_contract: Mapping[str, Any],
    ledger_schema_sha256: str,
) -> Tuple[List[str], Dict[Path, StreamFileMetadata]]:
    """Authenticate and structurally scan all unbounded dataset artifacts.

    Legal trajectory replay, terminal verification and V3 feature extraction
    intentionally remain an engine-backed release gate.  This function proves
    byte authentication, canonical layouts, structural wires, ranges, hashes,
    partitions and cross-split file/trajectory identities only.
    """

    errors: List[str] = []
    metadata: Dict[Path, StreamFileMetadata] = {}
    manifests: Dict[str, ManifestSummary] = {}
    stats_by_role = {"train": train_stats, "validation": validation_stats}
    artifacts_by_role = {"train": train_artifacts, "validation": validation_artifacts}
    train_manifest_descriptor = train_stats.get("artifacts", {}).get(
        "atomic_bin_v2_manifest"
    )
    validation_manifest_descriptor = validation_stats.get("artifacts", {}).get(
        "atomic_bin_v2_manifest"
    )
    if isinstance(train_manifest_descriptor, Mapping) and isinstance(
        validation_manifest_descriptor, Mapping
    ) and train_manifest_descriptor.get("sha256") == validation_manifest_descriptor.get(
        "sha256"
    ):
        errors.append("train/validation manifest artifact hashes must be distinct")
    for role in ("train", "validation"):
        artifacts = artifacts_by_role[role]
        manifest_path = artifacts.get("atomic_bin_v2_manifest")
        descriptor = stats_by_role[role].get("artifacts", {}).get("atomic_bin_v2_manifest")
        if manifest_path is None or not isinstance(descriptor, Mapping):
            errors.append(role + "_manifest: authenticated manifest artifact is missing")
            continue
        summary, manifest_errors = parse_atomic_bin_v2_manifest(
            role,
            manifest_path,
            descriptor,
            manifest_schema,
            manifest_schema_sha256,
            data_schema_sha256,
        )
        errors += manifest_errors
        if summary is not None:
            manifests[role] = summary
            errors += _validate_manifest_provenance(role, summary, stats_by_role[role], policy)
    if len(manifests) != 2:
        return errors, metadata
    if manifests["train"].generation_sha256 != manifests["validation"].generation_sha256:
        errors.append(
            "train/validation normalized semantic generation profiles must be byte-identical"
        )
    train_ledger_descriptor = train_stats.get("artifacts", {}).get("trajectory_ledger")
    validation_ledger_descriptor = validation_stats.get("artifacts", {}).get("trajectory_ledger")
    if isinstance(train_ledger_descriptor, Mapping) and isinstance(
        validation_ledger_descriptor, Mapping
    ) and train_ledger_descriptor.get("sha256") == validation_ledger_descriptor.get("sha256"):
        errors.append("train/validation ledger artifact hashes must be distinct")

    datasets: Dict[str, DatasetStructuralSummary] = {}
    raw_intersection: Optional[int] = None
    with tempfile.TemporaryDirectory(prefix="atomic-v3-record-index-") as directory:
        record_index = SplitGroupIndex(Path(directory))
        try:
            for role in ("train", "validation"):
                summary, dataset_errors = scan_atomic_bin_v2_dataset(
                    manifests[role], data_schema_sha256, record_index
                )
                errors += dataset_errors
                if summary is not None:
                    datasets[role] = summary
            if len(datasets) == 2:
                raw_intersection = record_index.intersection_count("raw_records")
        finally:
            record_index.close()
    if len(datasets) != 2:
        return errors, metadata
    shared_sha = set(datasets["train"].shard_sha256) & set(
        datasets["validation"].shard_sha256
    )
    if shared_sha:
        errors.append("shard SHA-256 appears in both splits")
    shared_identity = set(datasets["train"].shard_identities) & set(
        datasets["validation"].shard_identities
    )
    if shared_identity:
        errors.append("train/validation shard paths resolve to the same file identity")
    declared_intersections = split_audit.get("intersections")
    declared_raw = (
        _canonical_u64(declared_intersections.get("raw_record_keys"))
        if isinstance(declared_intersections, Mapping)
        else None
    )
    if raw_intersection is not None and declared_raw != raw_intersection:
        errors.append(
            "split_audit.intersections.raw_record_keys: declared value does not match "
            "streamed datasets ({})".format(raw_intersection)
        )
    if raw_intersection:
        errors.append("raw_record_key appears in both train and validation datasets")

    ledgers: Dict[str, LedgerStructuralSummary] = {}
    with tempfile.TemporaryDirectory(prefix="atomic-v3-split-index-") as directory:
        split_index = SplitGroupIndex(Path(directory))
        try:
            for role in ("train", "validation"):
                ledger_path = artifacts_by_role[role].get("trajectory_ledger")
                descriptor = stats_by_role[role].get("artifacts", {}).get(
                    "trajectory_ledger"
                )
                if ledger_path is None or not isinstance(descriptor, Mapping):
                    errors.append(role + "_ledger: authenticated ledger artifact is missing")
                    continue
                ledger, ledger_errors = scan_atomic_trajectory_ledger(
                    role,
                    ledger_path,
                    descriptor,
                    manifests[role],
                    policy,
                    ledger_contract,
                    ledger_schema_sha256,
                    data_schema_sha256,
                    split_index,
                )
                errors += ledger_errors
                if ledger is not None:
                    ledgers[role] = ledger
                    metadata[ledger_path] = ledger.file_metadata
            if len(ledgers) == 2:
                intersection = split_index.intersection_count()
                declared = (
                    _canonical_u64(declared_intersections.get("split_group_ids"))
                    if isinstance(declared_intersections, Mapping)
                    else None
                )
                if declared != intersection:
                    errors.append(
                        "split_audit.intersections.split_group_ids: declared value does not "
                        "match streamed ledgers ({})".format(intersection)
                    )
                if intersection:
                    errors.append("split_group_id appears in both train and validation ledgers")
                for role in ("train", "validation"):
                    errors += _reconcile_streamed_role(
                        role,
                        stats_by_role[role],
                        manifests[role],
                        datasets[role],
                        ledgers[role],
                        split_audit,
                    )
        finally:
            split_index.close()
    return errors, metadata


def authenticate_declared_artifacts(
    document: Mapping[str, Any],
    sidecar_path: Path,
    label: str,
    known_files: Mapping[Path, Tuple[int, str]],
    expected_schema_sha256: Mapping[str, str],
    expected_sizes: Optional[Mapping[str, int]] = None,
    payload_cache: Optional[Dict[Path, bytes]] = None,
    metadata_cache: Optional[Dict[Path, StreamFileMetadata]] = None,
) -> Tuple[List[str], Dict[str, Path]]:
    """Authenticate every sidecar artifact against one same-directory file."""

    errors: List[str] = []
    resolved: Dict[str, Path] = {}
    base = sidecar_path.resolve().parent
    artifacts = document.get("artifacts")
    if not isinstance(artifacts, Mapping):
        return [label + ".artifacts: must be an object"], resolved
    for name, raw in artifacts.items():
        path = "{}.artifacts.{}".format(label, name)
        if not isinstance(raw, Mapping):
            errors.append(path + ": must be an object")
            continue
        file_name = raw.get("file")
        if not isinstance(file_name, str):
            errors.append(path + ".file: must be a basename")
            continue
        candidate = (base / file_name).resolve()
        if candidate.parent != base:
            errors.append(path + ".file: must resolve inside the sidecar directory")
            continue
        resolved[str(name)] = candidate
        try:
            if candidate in known_files:
                byte_count, digest = known_files[candidate]
            else:
                expected_size = (expected_sizes or {}).get(str(name))
                capture_limit = (
                    expected_size
                    if payload_cache is not None
                    and isinstance(expected_size, int)
                    and expected_size > 0
                    else 0
                )
                metadata, payload = stream_file_metadata(candidate, capture_limit)
                byte_count, digest = metadata.byte_count, metadata.sha256
                if metadata_cache is not None:
                    metadata_cache[candidate] = metadata
                if payload_cache is not None and payload is not None:
                    payload_cache[candidate] = payload
        except (OSError, ValueError) as exc:
            errors.append(path + ".file: cannot read authenticated artifact: {}".format(exc))
            continue
        declared_bytes = raw.get("bytes")
        if not isinstance(declared_bytes, str) or UINT64_RE.fullmatch(declared_bytes) is None:
            errors.append(path + ".bytes: must be a canonical uint64 decimal string")
        elif int(declared_bytes) != byte_count:
            errors.append(path + ".bytes: does not match exact artifact bytes")
        if raw.get("sha256") != digest:
            errors.append(path + ".sha256: does not authenticate exact artifact bytes")
        expected_schema = expected_schema_sha256.get(str(name))
        if expected_schema is not None and raw.get("schema_sha256") != expected_schema:
            errors.append(path + ".schema_sha256: does not authenticate the expected schema")
        expected_size = (expected_sizes or {}).get(str(name))
        if expected_size is not None and byte_count != expected_size:
            errors.append(path + ".bytes: artifact contract requires {} bytes".format(expected_size))
    return errors, resolved


def authenticate_training_run_artifacts(
    manifest: Mapping[str, Any],
    manifest_path: Path,
    known_files: Mapping[Path, Tuple[int, str]],
    expected_sha256: Mapping[str, str],
) -> List[str]:
    """Open and authenticate every file named by a completed training run."""

    errors: List[str] = []
    base = manifest_path.resolve().parent
    cached: Dict[Path, Tuple[int, str]] = dict(known_files)
    inputs = manifest.get("inputs")
    if not isinstance(inputs, Mapping):
        return ["training_run.inputs: must be an object"]

    descriptors: List[Tuple[str, Any]] = []
    for dotted_path in INPUT_ARTIFACT_PATHS:
        try:
            descriptor = _nested_mapping(inputs, dotted_path)
        except (KeyError, TypeError) as exc:
            errors.append("training_run.inputs.{}: {}".format(dotted_path, exc))
            continue
        descriptors.append(("inputs." + dotted_path, descriptor))
    try:
        descriptors.append(("trainer.config", _nested_mapping(manifest, "trainer.config")))
        for name in ("checkpoint", "network", "training_log", "metrics"):
            descriptors.append(
                ("outputs." + name, _nested_mapping(manifest, "outputs." + name))
            )
    except (KeyError, TypeError) as exc:
        errors.append("training_run: {}".format(exc))

    for dotted_path, descriptor in descriptors:
        label = "training_run." + dotted_path
        file_name = descriptor.get("file")
        if not isinstance(file_name, str):
            errors.append(label + ".file: must be a basename")
            continue
        candidate = (base / file_name).resolve()
        if candidate.parent != base:
            errors.append(label + ".file: must resolve inside the manifest directory")
            continue
        try:
            metadata = cached.get(candidate)
            if metadata is None:
                streamed, _ = stream_file_metadata(candidate)
                metadata = (streamed.byte_count, streamed.sha256)
                cached[candidate] = metadata
        except (OSError, ValueError) as exc:
            errors.append(label + ".file: cannot read artifact: {}".format(exc))
            continue
        byte_count, digest = metadata
        declared_bytes = descriptor.get("bytes")
        if not isinstance(declared_bytes, str) or UINT64_RE.fullmatch(declared_bytes) is None:
            errors.append(label + ".bytes: must be a canonical uint64 decimal string")
        elif int(declared_bytes) > MAX_U64:
            errors.append(label + ".bytes: exceeds UINT64_MAX")
        elif int(declared_bytes) != byte_count:
            errors.append(label + ".bytes: does not match exact artifact bytes")
        if descriptor.get("sha256") != digest:
            errors.append(label + ".sha256: does not authenticate exact artifact bytes")
        expected = expected_sha256.get(dotted_path)
        if expected is not None and digest != expected:
            errors.append(label + ".sha256: does not bind the validated bundle artifact")
    return errors


def _parser() -> argparse.ArgumentParser:
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(
        description=(
            "Validate AtomicNNUEV3 bundle authentication and structural contracts. "
            "H9.3a does not claim engine-backed semantic replay or publication readiness."
        )
    )
    parser.add_argument("--policy", required=True, type=Path)
    parser.add_argument("--train-stats", required=True, type=Path)
    parser.add_argument("--validation-stats", required=True, type=Path)
    parser.add_argument("--split-audit", required=True, type=Path)
    parser.add_argument(
        "--feature-contract",
        type=Path,
        default=root / "schemas" / "atomic-nnue-v3.json",
    )
    parser.add_argument(
        "--index-coverage-contract",
        type=Path,
        default=root / "schemas" / "atomic-v3-index-coverage-v1.json",
    )
    parser.add_argument(
        "--trajectory-ledger-contract",
        type=Path,
        default=root / "schemas" / "atomic-trajectory-ledger-v1.json",
    )
    parser.add_argument(
        "--coverage-policy-schema",
        type=Path,
        default=root / "schemas" / "atomic-v3-coverage-policy-v1.json",
    )
    parser.add_argument(
        "--stats-schema",
        type=Path,
        default=root / "schemas" / "atomic-v3-dataset-stats-v1.json",
    )
    parser.add_argument(
        "--split-audit-schema",
        type=Path,
        default=root / "schemas" / "atomic-v3-split-audit-v1.json",
    )
    parser.add_argument(
        "--atomic-bin-v2-schema",
        type=Path,
        default=root / "schemas" / "atomic-bin-v2.json",
    )
    parser.add_argument(
        "--atomic-bin-v2-manifest-schema",
        type=Path,
        default=root / "schemas" / "atomic-bin-v2-manifest.json",
    )
    parser.add_argument(
        "--training-run-manifest",
        type=Path,
        help="optionally validate one completed Atomic V3 training-run manifest",
    )
    parser.add_argument(
        "--training-run-schema",
        type=Path,
        default=root / "schemas" / "atomic-v3-training-run-manifest-v1.json",
    )
    parser.add_argument(
        "--require-publication-ready",
        action="store_true",
        help=(
            "fail closed until engine-backed legal replay and V3 feature-input-key "
            "evidence are authenticated (intentionally unavailable in H9.3a)"
        ),
    )
    parser.add_argument("--json", action="store_true", help="emit a machine-readable result")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _parser().parse_args(argv)
    try:
        policy, policy_bytes, policy_digest = load_json_with_metadata(args.policy)
        train_stats, train_bytes, train_digest = load_json_with_metadata(args.train_stats)
        validation_stats, validation_bytes, validation_digest = load_json_with_metadata(
            args.validation_stats
        )
        split_audit, audit_bytes, audit_digest = load_json_with_metadata(args.split_audit)
        feature_contract, feature_bytes, feature_digest = load_json_with_metadata(
            args.feature_contract
        )
        index_contract, index_bytes, index_digest = load_json_with_metadata(
            args.index_coverage_contract
        )
        ledger_contract, ledger_bytes, ledger_digest = load_json_with_metadata(
            args.trajectory_ledger_contract
        )
        policy_schema, policy_schema_bytes, policy_schema_digest = load_json_with_metadata(
            args.coverage_policy_schema
        )
        stats_schema, stats_schema_bytes, stats_schema_digest = load_json_with_metadata(
            args.stats_schema
        )
        split_audit_schema, audit_schema_bytes, audit_schema_digest = load_json_with_metadata(
            args.split_audit_schema
        )
        _, dataset_schema_bytes, dataset_schema_digest = load_json_with_metadata(
            args.atomic_bin_v2_schema
        )
        manifest_schema, manifest_schema_bytes, manifest_schema_digest = load_json_with_metadata(
            args.atomic_bin_v2_manifest_schema
        )
        training_run_schema, _, _ = load_json_with_metadata(args.training_run_schema)
        policy_shape_errors = validate_json_schema(policy, policy_schema, "policy")
        train_shape_errors = validate_json_schema(train_stats, stats_schema, "train_stats")
        validation_shape_errors = validate_json_schema(
            validation_stats, stats_schema, "validation_stats"
        )
        audit_shape_errors = validate_json_schema(
            split_audit, split_audit_schema, "split_audit"
        )
        errors = (
            policy_shape_errors
            + train_shape_errors
            + validation_shape_errors
            + audit_shape_errors
        )
        if not policy_shape_errors:
            errors += _validate_canonical_json_bytes(
                policy, policy_schema, policy_bytes, policy_digest, "policy"
            )
        if not train_shape_errors:
            errors += _validate_canonical_json_bytes(
                train_stats, stats_schema, train_bytes, train_digest, "train_stats"
            )
        if not validation_shape_errors:
            errors += _validate_canonical_json_bytes(
                validation_stats,
                stats_schema,
                validation_bytes,
                validation_digest,
                "validation_stats",
            )
        if not audit_shape_errors:
            errors += _validate_canonical_json_bytes(
                split_audit, split_audit_schema, audit_bytes, audit_digest, "split_audit"
            )
        training_run: Optional[Mapping[str, Any]] = None
        if args.training_run_manifest is not None:
            training_run, run_bytes, run_digest = load_json_with_metadata(
                args.training_run_manifest
            )
            run_shape_errors = validate_json_schema(
                training_run, training_run_schema, "training_run"
            )
            errors += run_shape_errors
            if not run_shape_errors:
                errors += _validate_canonical_json_bytes(
                    training_run,
                    training_run_schema,
                    run_bytes,
                    run_digest,
                    "training_run",
                )
                errors += validate_training_run_manifest(training_run)
        if not errors:
            if policy.get("feature_schema_sha256") != feature_digest:
                errors.append(
                    "policy.feature_schema_sha256: does not authenticate --feature-contract bytes"
                )
            if policy.get("stats_schema_sha256") != stats_schema_digest:
                errors.append(
                    "policy.stats_schema_sha256: does not authenticate --stats-schema bytes"
                )
            known_files = {
                args.policy.resolve(): (policy_bytes, policy_digest),
                args.train_stats.resolve(): (train_bytes, train_digest),
                args.validation_stats.resolve(): (validation_bytes, validation_digest),
                args.split_audit.resolve(): (audit_bytes, audit_digest),
                args.feature_contract.resolve(): (feature_bytes, feature_digest),
                args.index_coverage_contract.resolve(): (index_bytes, index_digest),
                args.trajectory_ledger_contract.resolve(): (ledger_bytes, ledger_digest),
                args.coverage_policy_schema.resolve(): (
                    policy_schema_bytes,
                    policy_schema_digest,
                ),
                args.stats_schema.resolve(): (stats_schema_bytes, stats_schema_digest),
                args.split_audit_schema.resolve(): (
                    audit_schema_bytes,
                    audit_schema_digest,
                ),
                args.atomic_bin_v2_schema.resolve(): (
                    dataset_schema_bytes,
                    dataset_schema_digest,
                ),
                args.atomic_bin_v2_manifest_schema.resolve(): (
                    manifest_schema_bytes,
                    manifest_schema_digest,
                ),
            }
            stats_schema_bindings = {
                "atomic_bin_v2_manifest": manifest_schema_digest,
                "trajectory_ledger": ledger_digest,
                "index_coverage": index_digest,
                "coverage_policy": policy_schema_digest,
            }
            expected_atcov_size = index_contract.get("file_policy", {}).get("file_size")
            atcov_sizes = (
                {"index_coverage": expected_atcov_size}
                if isinstance(expected_atcov_size, int)
                else {}
            )
            artifact_payloads: Dict[Path, bytes] = {}
            artifact_metadata: Dict[Path, StreamFileMetadata] = {}
            train_artifact_errors, train_artifacts = authenticate_declared_artifacts(
                train_stats,
                args.train_stats,
                "train_stats",
                known_files,
                stats_schema_bindings,
                atcov_sizes,
                artifact_payloads,
                artifact_metadata,
            )
            validation_artifact_errors, validation_artifacts = authenticate_declared_artifacts(
                validation_stats,
                args.validation_stats,
                "validation_stats",
                known_files,
                stats_schema_bindings,
                atcov_sizes,
                artifact_payloads,
                artifact_metadata,
            )
            audit_artifact_errors, audit_artifacts = authenticate_declared_artifacts(
                split_audit,
                args.split_audit,
                "split_audit",
                known_files,
                {
                    "train_stats": stats_schema_digest,
                    "validation_stats": stats_schema_digest,
                    "coverage_policy": policy_schema_digest,
                },
                payload_cache=artifact_payloads,
                metadata_cache=artifact_metadata,
            )
            errors += train_artifact_errors + validation_artifact_errors + audit_artifact_errors
            for role, artifacts in (
                ("train_stats", train_artifacts),
                ("validation_stats", validation_artifacts),
            ):
                if artifacts.get("coverage_policy") != args.policy.resolve():
                    errors.append(
                        role + ".artifacts.coverage_policy.file: must bind the loaded policy"
                    )
            expected_audit_paths = {
                "train_stats": args.train_stats.resolve(),
                "validation_stats": args.validation_stats.resolve(),
                "coverage_policy": args.policy.resolve(),
            }
            for name, expected_path in expected_audit_paths.items():
                if audit_artifacts.get(name) != expected_path:
                    errors.append(
                        "split_audit.artifacts.{}.file: must bind the loaded sidecar".format(name)
                    )
            for name in ("atomic_bin_v2_manifest", "trajectory_ledger", "index_coverage"):
                if train_artifacts.get(name) == validation_artifacts.get(name):
                    errors.append(
                        "train_stats/validation_stats.artifacts.{}: paths must be distinct".format(
                            name
                        )
                    )
            if training_run is not None and args.training_run_manifest is not None:
                run_expected_sha256 = {
                    "inputs.feature_schema": feature_digest,
                    "inputs.dataset_schema": dataset_schema_digest,
                    "inputs.manifest_schema": manifest_schema_digest,
                    "inputs.trajectory_ledger_schema": ledger_digest,
                    "inputs.index_coverage_schema": index_digest,
                    "inputs.statistics_schema": stats_schema_digest,
                    "inputs.coverage_policy_schema": policy_schema_digest,
                    "inputs.split_audit_schema": audit_schema_digest,
                    "inputs.train.manifest": train_stats["artifacts"][
                        "atomic_bin_v2_manifest"
                    ]["sha256"],
                    "inputs.train.trajectory_ledger": train_stats["artifacts"][
                        "trajectory_ledger"
                    ]["sha256"],
                    "inputs.train.index_coverage": train_stats["artifacts"][
                        "index_coverage"
                    ]["sha256"],
                    "inputs.train.statistics": train_digest,
                    "inputs.validation.manifest": validation_stats["artifacts"][
                        "atomic_bin_v2_manifest"
                    ]["sha256"],
                    "inputs.validation.trajectory_ledger": validation_stats["artifacts"][
                        "trajectory_ledger"
                    ]["sha256"],
                    "inputs.validation.index_coverage": validation_stats["artifacts"][
                        "index_coverage"
                    ]["sha256"],
                    "inputs.validation.statistics": validation_digest,
                    "inputs.coverage_policy": policy_digest,
                    "inputs.split_audit": audit_digest,
                }
                run_known_files = dict(known_files)
                run_known_files.update(
                    {
                        path: (metadata.byte_count, metadata.sha256)
                        for path, metadata in artifact_metadata.items()
                    }
                )
                errors += authenticate_training_run_artifacts(
                    training_run,
                    args.training_run_manifest,
                    run_known_files,
                    run_expected_sha256,
                )
            errors += validate_bundle(
                policy,
                train_stats,
                validation_stats,
                split_audit,
                feature_contract,
                index_contract,
                ledger_contract,
            )
            if not errors:
                streamed_errors, streamed_metadata = validate_streamed_bundle_artifacts(
                    policy,
                    train_stats,
                    validation_stats,
                    split_audit,
                    train_artifacts,
                    validation_artifacts,
                    manifest_schema,
                    manifest_schema_digest,
                    dataset_schema_digest,
                    ledger_contract,
                    ledger_digest,
                )
                errors += streamed_errors
                artifact_metadata.update(streamed_metadata)
            if not errors:
                for role, stats, artifacts in (
                    ("train", train_stats, train_artifacts),
                    ("validation", validation_stats, validation_artifacts),
                ):
                    atcov_path = artifacts.get("index_coverage")
                    atcov_payload = artifact_payloads.get(atcov_path) if atcov_path else None
                    if atcov_payload is None:
                        errors.append(
                            role
                            + "_stats.artifacts.index_coverage.file: authenticated bytes are unavailable"
                        )
                    else:
                        errors += validate_atcov_file(
                            atcov_payload,
                            role,
                            stats,
                            feature_digest,
                            index_digest,
                            index_contract,
                        )
    except (
        OSError,
        UnicodeError,
        ValueError,
        json.JSONDecodeError,
        SchemaError,
        AttributeError,
        KeyError,
        TypeError,
    ) as exc:
        errors = ["input: {}".format(exc)]

    structural_ok = not errors
    pending_publication_gates = [
        "engine-backed legal trajectory/result/terminal replay evidence",
        "engine-backed AtomicNNUEV3 feature_input_key recomputation and cross-split proof",
        "authenticated independent-oracle reproduction of all four physical reachability masks",
        "producer/manifest evidence that adjudicate_resignations is disabled",
    ]
    if args.require_publication_ready and structural_ok:
        errors.append(
            "publication: H9.3a is structural-only; missing authenticated engine-backed "
            "legal replay, V3 feature-input-key, physical-mask oracle reproduction, "
            "and producer resignation-policy evidence"
        )
    publication_ready = structural_ok and not pending_publication_gates
    if args.json:
        print(
            json.dumps(
                {
                    "ok": not errors,
                    "structural_ok": structural_ok,
                    "publication_ready": publication_ready,
                    "status": (
                        "structural-pass"
                        if structural_ok and not args.require_publication_ready
                        else "publication-gate-fail"
                        if structural_ok
                        else "structural-fail"
                    ),
                    "pending_publication_gates": pending_publication_gates,
                    "errors": errors,
                },
                sort_keys=True,
            )
        )
    elif errors:
        print("Atomic V3 bundle structural validation: FAIL", file=sys.stderr)
        for error in errors:
            print("- " + error, file=sys.stderr)
    else:
        print(
            "Atomic V3 bundle structural validation: PASS "
            "(publication readiness pending engine-backed gates)"
        )
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
