#!/usr/bin/env python3
"""Fail-closed structural authentication for an AtomicNNUEV3 dataset bundle.

JSON Schema validates the shape of the V3 sidecars.  It cannot validate sums,
cross-document hashes/identities, decimal uint64 bounds, or binary-layout
formulae expressed by the project's ``x-*`` annotations.  This module owns
those executable checks.  It also authenticates and parses the fixed-size
``.atcov`` files so declared physical reachability and observed coverage are
authenticated from binary evidence rather than trusted from JSON declarations.
It exactly derives HM training/virtual masks from physical HM.  Structural V1
validation deliberately remains structural-only.  When all four publication
roots are supplied, the same command also authenticates the distributed
campaign, producer build-set/policy, engine-backed semantic key sets and the
independent-oracle output before reporting publication readiness.
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
CAMPAIGN_PROFILE_DOMAIN = b"atomic-v3-campaign-profile-v1\0"
PRODUCER_BUILD_SET_DOMAIN = b"atomic-v3-producer-build-set-v1\0"
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
RUN_DEFINITION_V2_DOMAIN = b"atomic-v3-run-definition-v2\0"
INPUT_BUNDLE_V2_DOMAIN = b"atomic-v3-input-bundle-v2\0"
CAMPAIGN_COLLECTION_DOMAIN = b"atomic-v3-dataset-campaign-v1\0"
PRODUCER_ATTESTATION_DOMAIN = b"atomic-v3-producer-attestation-v1\0"
SEMANTIC_AUDIT_DOMAIN = b"atomic-v3-semantic-audit-v1\0"
REACHABILITY_ATTESTATION_DOMAIN = b"atomic-v3-reachability-attestation-v1\0"
ORDERED_SET_DOMAIN = b"atomic-ordered-set-v1\0"
PUBLICATION_SCHEMA_SHA256 = {
    "campaign": "36a86983d63e71e20daa3bcf7a574dfc95abb544974e36c064445e79ad706517",
    "producer_attestation": "de55f384fdea56fdb28addd50b78da7e0256b5a8857d5aec856219a3e922193e",
    "semantic_audit": "e1aed04f4291f1ae514ba532b9a4c21fd926e41b7e29b7782a5222a85eda7810",
    "reachability_attestation": "fb1af7130a2fa74be0fadd721db980269e12c89204b627eec63e6074ed3983e8",
    "training_run_v2": "7703f038262cd4a69299aeaf3e0bb35c6d3181029fd448f91560807a0507d184",
    "training_environment": "8e2f9b97183d3deedfbc1d03ac396ace7c069fc369af09db7e1ee693cd59f3d0",
}
TRANSITIVE_PUBLICATION_SCHEMA_SHA256 = {
    "feature": "9d3c77a58e5e55ac1bc798dab41977451eb523fce1d6fd3ec3f7c1e574a78750",
    "data": "0352b036f2a140c609e3eb9c9d635dc553e8d77253d8faa92437390f5cf93cb6",
    "manifest": "83d63922df3ac4a0c81a21ec9d9fd9e180efe50f26efee62fe01710e09da5b42",
    "trajectory_ledger": "c2aaf1b2813b124a9daa2905a3dc277d635aabcd536b2677155933ef2bb18a3e",
    "index_coverage": "3fc2240c620cf0b636696c8ae0d7aa1f82cdd95a7af16bf0053759c0038fb1e8",
    "statistics": "118c2faa32d71d3fc4fe0ba0dab3d698dc8e84d1399a8f69b39f77d6f629f6e5",
    "coverage_policy": "c496a694df56efd4d221e86c9772f79b02a48ef8955b41724804555abeba3b9d",
    "split_audit": "8fedd68cd724c4daf992a910ebaaad63b694f41a9aa4b4e9c5d7adede7435ff9",
}
CAMPAIGN_SCHEMA_KEYS = (
    "feature",
    "data",
    "manifest",
    "trajectory_ledger",
    "index_coverage",
    "statistics",
    "coverage_policy",
    "split_audit",
)
CAMPAIGN_TOTAL_KEYS = (
    "train_records",
    "validation_records",
    "train_trajectories",
    "validation_trajectories",
    "train_moves",
    "validation_moves",
    "records",
)
CAMPAIGN_PARTITION_ARTIFACT_KEYS = (
    "manifest",
    "trajectory_ledger",
    "index_coverage",
    "statistics",
)
PRODUCER_POLICY_KEYS = (
    "use_nnue",
    "adjudicate_draws_by_score",
    "adjudicate_resignations",
    "syzygy_disabled",
    "complete_trajectory_moves_preserved",
    "role_partition_before_publication",
)
PRODUCER_VERIFICATION_KEYS = (
    "campaign_authenticated",
    "all_chunk_manifests_authenticated",
    "all_chunk_ledgers_authenticated",
    "all_partition_hashes_recomputed",
    "all_stop_reasons_evidenced",
    "all_chunks_bound_to_authenticated_builds",
    "no_post_manifest_mutation",
    "transactional_publication",
    "strict_eof",
)
SEMANTIC_VERIFICATION_KEYS = (
    "campaign_authenticated",
    "producer_attestation_authenticated",
    "all_roots_legal",
    "all_played_moves_legal",
    "all_retained_positions_matched",
    "all_best_moves_legal",
    "all_results_matched",
    "all_terminals_matched",
    "all_feature_input_keys_recomputed",
    "validation_feature_inputs_decontaminated",
    "strict_eof",
)
REACHABILITY_VERIFICATION_KEYS = (
    "campaign_authenticated",
    "producer_attestation_authenticated",
    "feature_schema_authenticated",
    "oracle_binary_authenticated",
    "oracle_used_no_dataset_artifacts",
    "all_physical_masks_reproduced",
    "hm_training_projection_recomputed",
    "hm_virtual_projection_recomputed",
    "aggregate_hash_recomputed",
    "all_campaign_policy_masks_matched",
    "strict_eof",
)
INPUT_ARTIFACT_PATHS_V2 = (
    "feature_schema",
    "dataset_schema",
    "manifest_schema",
    "trajectory_ledger_schema",
    "index_coverage_schema",
    "statistics_schema",
    "coverage_policy_schema",
    "split_audit_schema",
    "training_environment_schema",
    "dataset_campaign_schema",
    "producer_attestation_schema",
    "semantic_audit_schema",
    "reachability_attestation_schema",
    "campaign",
    "producer_attestation",
    "semantic_audit",
    "reachability_attestation",
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
    pathname_stat = os.lstat(path)
    if stat.S_ISLNK(pathname_stat.st_mode):
        raise ValueError("{}: authenticated artifact must not be a symbolic link".format(path))
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    try:
        with os.fdopen(descriptor, "rb", closefd=False) as stream:
            before = os.fstat(stream.fileno())
            if not stat.S_ISREG(before.st_mode):
                raise ValueError("{}: authenticated artifact is not a regular file".format(path))
            if (int(pathname_stat.st_dev), int(pathname_stat.st_ino)) != (
                int(before.st_dev),
                int(before.st_ino),
            ):
                raise ValueError("{}: artifact path changed before authentication".format(path))
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
    finally:
        os.close(descriptor)
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


def _length_prefixed_ascii(value: Any, field: str) -> bytes:
    if not isinstance(value, str) or not value.isascii():
        raise ValueError(field + " must be ASCII")
    encoded = value.encode("ascii")
    if len(encoded) > 0xFFFF:
        raise ValueError(field + " exceeds uint16 length")
    return len(encoded).to_bytes(2, "little") + encoded


def _u64_bytes(value: Any, field: str) -> bytes:
    if not isinstance(value, str) or UINT64_RE.fullmatch(value) is None:
        raise ValueError(field + " must be a canonical uint64 decimal string")
    parsed = int(value)
    if parsed > MAX_U64:
        raise ValueError(field + " exceeds UINT64_MAX")
    return parsed.to_bytes(8, "little")


def _artifact_digest(value: Mapping[str, Any], field: str) -> bytes:
    digest = value.get("sha256")
    if not isinstance(digest, str) or SHA256_RE.fullmatch(digest) is None:
        raise ValueError(field + ".sha256 must be lowercase SHA-256")
    return bytes.fromhex(digest)


def compute_campaign_profile_sha256(
    policy: Mapping[str, Any], generation: Mapping[str, Any]
) -> str:
    """Hash all campaign-invariant producer/search inputs, excluding seed and volume."""

    if set(generation) != set(GENERATION_KEYS):
        raise ValueError("generation object does not have the frozen canonical field set")
    options = generation.get("options")
    if not isinstance(options, Mapping) or set(options) != set(GENERATION_OPTION_KEYS):
        raise ValueError("generation.options does not have the frozen canonical field set")
    partition = _nested_mapping(policy, "partition")
    provenance = _nested_mapping(partition, "provenance")
    profile = _nested_mapping(provenance, "generation_profile")
    normalized_generation = {
        key: (
            {
                name: options[name]
                for name in GENERATION_OPTION_KEYS
                if name not in GENERATION_VOLUME_OPTION_KEYS
            }
            if key == "options"
            else generation[key]
        )
        for key in GENERATION_KEYS
        if key != "resolved_seed"
    }
    generation_payload = json.dumps(
        normalized_generation,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
    ).encode("utf-8")
    payload = bytearray(CAMPAIGN_PROFILE_DOMAIN)
    payload += _length_prefixed_ascii(partition["method"], "partition.method")
    payload += _u64_bytes(partition["split_seed"], "partition.split_seed")
    payload += _u64_bytes(
        partition["validation_threshold_u64"],
        "partition.validation_threshold_u64",
    )
    for field in ("engine_commit", "generator_commit", "tools_commit"):
        value = provenance[field]
        if not isinstance(value, str) or GIT_COMMIT_RE.fullmatch(value) is None:
            raise ValueError("partition.provenance." + field + " must be lowercase 40-hex")
        payload += bytes.fromhex(value)
    payload += bytes.fromhex(provenance["teacher_network_sha256"])
    opening_book = provenance["opening_book_sha256"]
    payload.append(int(opening_book is not None))
    if opening_book is not None:
        payload += bytes.fromhex(opening_book)
    payload += _length_prefixed_ascii(profile["qsearch_mode"], "generation_profile.qsearch_mode")
    payload += bytes(
        int(profile[field])
        for field in ("exclude_captures", "exclude_promotions", "exclude_checks")
    )
    payload.append(0 if profile["opening_mode"] == "builtin-startpos" else 1)
    payload += bytes(
        int(profile[field])
        for field in ("adjudicate_draws_by_score", "adjudicate_resignations")
    )
    payload += len(generation_payload).to_bytes(4, "little")
    payload += generation_payload
    return hashlib.sha256(payload).hexdigest()


def compute_producer_build_set_sha256(binary_sha256_values: Sequence[str]) -> str:
    """Hash one exact, strictly sorted and unique set of producer binaries."""

    digests = list(binary_sha256_values)
    if not digests or len(digests) > (1 << 32) - 1:
        raise ValueError("producer build set must contain 1..UINT32_MAX entries")
    if digests != sorted(set(digests)):
        raise ValueError("producer build set must be strictly sorted and unique")
    payload = bytearray(PRODUCER_BUILD_SET_DOMAIN)
    payload += len(digests).to_bytes(4, "little")
    for index, digest in enumerate(digests):
        if not isinstance(digest, str) or SHA256_RE.fullmatch(digest) is None:
            raise ValueError("producer build digest {} is not lowercase SHA-256".format(index))
        payload += bytes.fromhex(digest)
    return hashlib.sha256(payload).hexdigest()


def compute_dataset_campaign_sha256(campaign: Mapping[str, Any]) -> str:
    """Hash an ordered multi-chunk campaign without self-reference."""

    payload = bytearray(CAMPAIGN_COLLECTION_DOMAIN)
    payload += _length_prefixed_ascii(campaign["campaign_id"], "campaign_id")
    schemas = _nested_mapping(campaign, "schemas")
    for name in CAMPAIGN_SCHEMA_KEYS:
        payload += bytes.fromhex(schemas[name])
    payload += bytes.fromhex(campaign["homogeneous_profile_sha256"])
    payload += bytes.fromhex(campaign["producer_build_set_sha256"])
    schedule = _nested_mapping(campaign, "seed_schedule")
    payload += _length_prefixed_ascii(schedule["method"], "seed_schedule.method")
    payload += _u64_bytes(schedule["base_seed"], "seed_schedule.base_seed")
    payload += _u64_bytes(
        schedule["first_chunk_index"], "seed_schedule.first_chunk_index"
    )
    chunk_count = schedule["chunk_count"]
    if isinstance(chunk_count, bool) or not isinstance(chunk_count, int):
        raise ValueError("seed_schedule.chunk_count must be an integer")
    payload += chunk_count.to_bytes(4, "little")
    for chunk_index, raw_chunk in enumerate(campaign["chunks"]):
        chunk = _nested_mapping({"chunk": raw_chunk}, "chunk")
        payload += _u64_bytes(chunk["index"], "chunks.index")
        payload += _u64_bytes(chunk["generation_seed"], "chunks.generation_seed")
        payload += bytes.fromhex(chunk["producer_build_sha256"])
        payload += bytes.fromhex(chunk["partition_config_sha256"])
        payload += _artifact_digest(chunk["coverage_policy"], "chunks.coverage_policy")
        for role in ("train", "validation"):
            partition = _nested_mapping(chunk, role)
            for name in ("first_record", "records", "trajectories", "moves"):
                payload += _u64_bytes(
                    partition[name], "chunks[{}].{}.{}".format(chunk_index, role, name)
                )
            for name in CAMPAIGN_PARTITION_ARTIFACT_KEYS:
                payload += _artifact_digest(
                    _nested_mapping(partition, name),
                    "chunks[{}].{}.{}".format(chunk_index, role, name),
                )
        payload += _artifact_digest(chunk["split_audit"], "chunks.split_audit")
    totals = _nested_mapping(campaign, "totals")
    for name in CAMPAIGN_TOTAL_KEYS:
        payload += _u64_bytes(totals[name], "totals." + name)
    return hashlib.sha256(payload).hexdigest()


def compute_producer_attestation_sha256(attestation: Mapping[str, Any]) -> str:
    payload = bytearray(PRODUCER_ATTESTATION_DOMAIN)
    payload += _artifact_digest(attestation["campaign"], "campaign")
    producer = _nested_mapping(attestation, "producer_builds")
    payload += _length_prefixed_ascii(
        producer["algorithm_version"], "producer.algorithm_version"
    )
    payload += _length_prefixed_ascii(producer["build_role"], "producer.build_role")
    payload += bytes.fromhex(producer["build_set_sha256"])
    builds = producer["builds"]
    if not isinstance(builds, list) or len(builds) > (1 << 32) - 1:
        raise ValueError("producer_builds.builds must be a bounded array")
    payload += len(builds).to_bytes(4, "little")
    for index, raw_build in enumerate(builds):
        build = _nested_mapping({"build": raw_build}, "build")
        payload += bytes.fromhex(build["commit"])
        payload += _artifact_digest(
            build["binary"], "producer_builds.builds[{}].binary".format(index)
        )
    chunk_builds = producer["chunk_builds"]
    if not isinstance(chunk_builds, list) or len(chunk_builds) > (1 << 32) - 1:
        raise ValueError("producer_builds.chunk_builds must be a bounded array")
    payload += len(chunk_builds).to_bytes(4, "little")
    for index, raw_mapping in enumerate(chunk_builds):
        mapping = _nested_mapping({"mapping": raw_mapping}, "mapping")
        payload += _u64_bytes(
            mapping["chunk_index"],
            "producer_builds.chunk_builds[{}].chunk_index".format(index),
        )
        payload += bytes.fromhex(mapping["binary_sha256"])
    policy = _nested_mapping(attestation, "generation_policy")
    payload += _length_prefixed_ascii(policy["use_nnue"], "generation_policy.use_nnue")
    for name in PRODUCER_POLICY_KEYS[1:]:
        value = policy[name]
        if not isinstance(value, bool):
            raise ValueError("generation_policy." + name + " must be boolean")
        payload.append(int(value))
    verification = _nested_mapping(attestation, "verification")
    for name in PRODUCER_VERIFICATION_KEYS:
        value = verification[name]
        if not isinstance(value, bool):
            raise ValueError("verification." + name + " must be boolean")
        payload.append(int(value))
    return hashlib.sha256(payload).hexdigest()


def compute_ordered_set_sha256(payload: bytes, unique_keys: int) -> str:
    if unique_keys < 0 or unique_keys > MAX_U64:
        raise ValueError("ordered-set key count exceeds uint64")
    if len(payload) != unique_keys * 32:
        raise ValueError("ordered-set payload length does not equal unique_keys * 32")
    return hashlib.sha256(
        ORDERED_SET_DOMAIN + unique_keys.to_bytes(8, "little") + payload
    ).hexdigest()


def compute_semantic_audit_sha256(audit: Mapping[str, Any]) -> str:
    payload = bytearray(SEMANTIC_AUDIT_DOMAIN)
    payload += _artifact_digest(audit["campaign"], "campaign")
    payload += _artifact_digest(audit["producer_attestation"], "producer_attestation")
    scanner = _nested_mapping(audit, "scanner")
    payload += bytes.fromhex(scanner["commit"])
    payload += _artifact_digest(scanner["binary"], "scanner.binary")
    payload += _length_prefixed_ascii(
        scanner["algorithm_version"], "scanner.algorithm_version"
    )
    roles = _nested_mapping(audit, "roles")
    for role_name in ("train", "validation"):
        role = _nested_mapping(roles, role_name)
        for name in ("records_replayed", "trajectories_replayed", "moves_replayed"):
            payload += _u64_bytes(role[name], "roles.{}.{}".format(role_name, name))
        keys = _nested_mapping(role, "feature_input_keys")
        payload += _artifact_digest(keys["artifact"], "roles.feature_input_keys.artifact")
        payload += _u64_bytes(keys["observations"], "feature_input_keys.observations")
        payload += _u64_bytes(keys["unique_keys"], "feature_input_keys.unique_keys")
        payload += bytes.fromhex(keys["ordered_set_sha256"])
        for count in role["stop_reasons"]:
            payload += _u64_bytes(count, "roles.stop_reasons")
    intersections = _nested_mapping(audit, "intersections")
    for name in ("raw_record_keys", "feature_input_keys", "split_group_ids"):
        payload += _u64_bytes(intersections[name], "intersections." + name)
    verification = _nested_mapping(audit, "verification")
    for name in SEMANTIC_VERIFICATION_KEYS:
        value = verification[name]
        if not isinstance(value, bool):
            raise ValueError("verification." + name + " must be boolean")
        payload.append(int(value))
    return hashlib.sha256(payload).hexdigest()


def compute_reachability_attestation_sha256(attestation: Mapping[str, Any]) -> str:
    payload = bytearray(REACHABILITY_ATTESTATION_DOMAIN)
    payload += _artifact_digest(attestation["campaign"], "campaign")
    payload += _artifact_digest(
        attestation["producer_attestation"], "producer_attestation"
    )
    payload += _artifact_digest(attestation["feature_schema"], "feature_schema")
    oracle = _nested_mapping(attestation, "oracle")
    payload += bytes.fromhex(oracle["commit"])
    payload += _artifact_digest(oracle["binary"], "oracle.binary")
    payload += _length_prefixed_ascii(
        oracle["algorithm_version"], "oracle.algorithm_version"
    )
    payload += _artifact_digest(attestation["oracle_output"], "oracle_output")
    roles = _nested_mapping(attestation, "roles")
    for perspective in ("WHITE", "BLACK"):
        role = _nested_mapping(roles, perspective)
        for field in MASK_FIELD_IDS:
            payload += bytes.fromhex(_nested_mapping(role, field)["sha256"])
    payload += bytes.fromhex(attestation["reachability_mask_sha256"])
    verification = _nested_mapping(attestation, "verification")
    for name in REACHABILITY_VERIFICATION_KEYS:
        value = verification[name]
        if not isinstance(value, bool):
            raise ValueError("verification." + name + " must be boolean")
        payload.append(int(value))
    return hashlib.sha256(payload).hexdigest()


def compute_run_definition_sha256_v2(manifest: Mapping[str, Any]) -> str:
    trainer = _nested_mapping(manifest, "trainer")
    schedule = _nested_mapping(manifest, "schedule")
    payload = bytearray(RUN_DEFINITION_V2_DOMAIN)
    payload += bytes.fromhex(trainer["commit"])
    payload += bytes.fromhex(trainer["artifact_sha256"])
    payload += _artifact_digest(trainer["dependency_lock"], "trainer.dependency_lock")
    payload += bytes.fromhex(_nested_mapping(trainer, "config")["sha256"])
    environment = _nested_mapping(trainer, "environment")
    payload += bytes.fromhex(environment["schema_sha256"])
    payload += _artifact_digest(environment, "trainer.environment")
    payload += _u64_bytes(trainer["training_seed"], "trainer.training_seed")
    deterministic = trainer["deterministic_algorithms"]
    if not isinstance(deterministic, bool):
        raise ValueError("trainer.deterministic_algorithms must be boolean")
    payload.append(int(deterministic))
    payload += int(schedule["batch_size"]).to_bytes(4, "little")
    payload += _u64_bytes(schedule["optimizer_steps"], "schedule.optimizer_steps")
    payload += int(schedule["epochs"]).to_bytes(4, "little")
    payload += _u64_bytes(
        schedule["validation_interval_steps"], "schedule.validation_interval_steps"
    )
    return hashlib.sha256(payload).hexdigest()


def compute_input_bundle_sha256_v2(manifest: Mapping[str, Any]) -> str:
    inputs = _nested_mapping(manifest, "inputs")
    payload = bytearray(INPUT_BUNDLE_V2_DOMAIN)
    for path in INPUT_ARTIFACT_PATHS_V2:
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


def _validate_typed_artifact(
    value: Any, path: str, checks: Checks, expected_schema: Optional[str] = None
) -> None:
    artifact = checks.mapping(value, path)
    _validate_run_artifact(artifact, path, checks)
    schema_sha256 = checks.sha256(artifact.get("schema_sha256"), path + ".schema_sha256")
    if expected_schema is not None and schema_sha256 != expected_schema:
        checks.error(path + ".schema_sha256", "does not bind the expected schema")


def validate_dataset_campaign(
    campaign: Mapping[str, Any], expected_schemas: Optional[Mapping[str, str]] = None
) -> List[str]:
    """Validate ordering, seed derivation, offsets, totals and the campaign hash."""

    checks = Checks()
    schemas = checks.mapping(campaign.get("schemas"), "campaign.schemas")
    for name in CAMPAIGN_SCHEMA_KEYS:
        digest = checks.sha256(schemas.get(name), "campaign.schemas." + name)
        if expected_schemas is not None and digest != expected_schemas.get(name):
            checks.error(
                "campaign.schemas." + name, "does not authenticate the loaded contract"
            )
    checks.sha256(
        campaign.get("homogeneous_profile_sha256"),
        "campaign.homogeneous_profile_sha256",
    )
    checks.sha256(
        campaign.get("producer_build_set_sha256"),
        "campaign.producer_build_set_sha256",
    )
    schedule = checks.mapping(campaign.get("seed_schedule"), "campaign.seed_schedule")
    base_seed = checks.u64(schedule.get("base_seed"), "campaign.seed_schedule.base_seed")
    first_index = checks.u64(
        schedule.get("first_chunk_index"), "campaign.seed_schedule.first_chunk_index"
    )
    declared_count = checks.plain_int(
        schedule.get("chunk_count"), "campaign.seed_schedule.chunk_count", 1, 100000
    )
    chunks = checks.sequence(campaign.get("chunks"), "campaign.chunks")
    if declared_count is not None and declared_count != len(chunks):
        checks.error("campaign.seed_schedule.chunk_count", "does not equal chunks length")

    expected_offset = {"train": 0, "validation": 0}
    summed = {
        "train_records": 0,
        "validation_records": 0,
        "train_trajectories": 0,
        "validation_trajectories": 0,
        "train_moves": 0,
        "validation_moves": 0,
    }
    seen_files: set[str] = set()
    seen_digests: set[str] = set()
    artifact_schema = {
        "coverage_policy": "coverage_policy",
        "manifest": "manifest",
        "trajectory_ledger": "trajectory_ledger",
        "index_coverage": "index_coverage",
        "statistics": "statistics",
        "split_audit": "split_audit",
    }

    def register_artifact(value: Any, path: str, schema_name: str) -> None:
        expected = schemas.get(artifact_schema[schema_name])
        _validate_typed_artifact(value, path, checks, expected if isinstance(expected, str) else None)
        artifact = checks.mapping(value, path)
        file_name = artifact.get("file")
        digest = artifact.get("sha256")
        if isinstance(file_name, str):
            if file_name in seen_files:
                checks.error(path + ".file", "duplicates another campaign artifact path")
            seen_files.add(file_name)
        if isinstance(digest, str):
            if digest in seen_digests:
                checks.error(path + ".sha256", "duplicates another campaign artifact digest")
            seen_digests.add(digest)

    for ordinal, raw_chunk in enumerate(chunks):
        path = "campaign.chunks[{}]".format(ordinal)
        chunk = checks.mapping(raw_chunk, path)
        index = checks.u64(chunk.get("index"), path + ".index")
        generation_seed = checks.u64(
            chunk.get("generation_seed"), path + ".generation_seed"
        )
        checks.sha256(
            chunk.get("producer_build_sha256"), path + ".producer_build_sha256"
        )
        if first_index is not None and index is not None and index != first_index + ordinal:
            checks.error(path + ".index", "must be strictly contiguous in array order")
        if base_seed is not None and index is not None:
            expected_seed = base_seed + index
            if expected_seed > MAX_U64:
                checks.error(path + ".generation_seed", "derived seed exceeds UINT64_MAX")
            elif generation_seed != expected_seed:
                checks.error(
                    path + ".generation_seed", "does not equal base_seed + chunk.index"
                )
        checks.sha256(
            chunk.get("partition_config_sha256"), path + ".partition_config_sha256"
        )
        register_artifact(chunk.get("coverage_policy"), path + ".coverage_policy", "coverage_policy")
        for role in ("train", "validation"):
            partition_path = path + "." + role
            partition = checks.mapping(chunk.get(role), partition_path)
            first_record = checks.u64(
                partition.get("first_record"), partition_path + ".first_record"
            )
            if first_record is not None and first_record != expected_offset[role]:
                checks.error(
                    partition_path + ".first_record",
                    "does not equal the preceding role end offset",
                )
            for counter in ("records", "trajectories", "moves"):
                value = checks.u64(
                    partition.get(counter), partition_path + "." + counter, positive=True
                )
                if value is not None:
                    summed[role + "_" + counter] += value
                    if counter == "records":
                        expected_offset[role] += value
            for name in CAMPAIGN_PARTITION_ARTIFACT_KEYS:
                register_artifact(partition.get(name), partition_path + "." + name, name)
        register_artifact(chunk.get("split_audit"), path + ".split_audit", "split_audit")

    totals = checks.mapping(campaign.get("totals"), "campaign.totals")
    for name in CAMPAIGN_TOTAL_KEYS[:-1]:
        value = checks.u64(totals.get(name), "campaign.totals." + name, positive=True)
        if value is not None and value != summed[name]:
            checks.error("campaign.totals." + name, "does not equal the chunk sum")
    records = checks.u64(totals.get("records"), "campaign.totals.records", positive=True)
    expected_records = summed["train_records"] + summed["validation_records"]
    if records is not None and records != expected_records:
        checks.error("campaign.totals.records", "does not equal train + validation records")
    try:
        expected_collection = compute_dataset_campaign_sha256(campaign)
    except (KeyError, TypeError, ValueError, OverflowError) as exc:
        checks.error("campaign.collection_sha256", "cannot recompute: {}".format(exc))
    else:
        if campaign.get("collection_sha256") != expected_collection:
            checks.error(
                "campaign.collection_sha256", "does not authenticate the ordered collection"
            )
    verification = checks.mapping(campaign.get("verification"), "campaign.verification")
    for name in (
        "chunk_indices_contiguous",
        "generation_seeds_recomputed",
        "role_offsets_contiguous",
        "totals_recomputed",
        "all_artifacts_distinct",
        "all_chunk_structural_bundles_validated",
        "homogeneous_profile_recomputed",
        "collection_hash_recomputed",
        "strict_eof",
    ):
        checks.truth(verification.get(name), "campaign.verification." + name)
    return checks.errors


def validate_producer_attestation(
    attestation: Mapping[str, Any],
    campaign_document: Mapping[str, Any],
    campaign_sha256: str,
    campaign_schema_sha256: str,
) -> List[str]:
    checks = Checks()
    _validate_typed_artifact(
        attestation.get("campaign"),
        "producer_attestation.campaign",
        checks,
        campaign_schema_sha256,
    )
    campaign = checks.mapping(attestation.get("campaign"), "producer_attestation.campaign")
    if campaign.get("sha256") != campaign_sha256:
        checks.error("producer_attestation.campaign.sha256", "does not bind the campaign")
    producer = checks.mapping(
        attestation.get("producer_builds"), "producer_attestation.producer_builds"
    )
    if producer.get("algorithm_version") != "atomic-v3-trajectory-producer-v1":
        checks.error(
            "producer_attestation.producer_builds.algorithm_version",
            "must be atomic-v3-trajectory-producer-v1",
        )
    if producer.get("build_role") != "data-generator":
        checks.error(
            "producer_attestation.producer_builds.build_role", "must be data-generator"
        )
    declared_build_set_sha256 = checks.sha256(
        producer.get("build_set_sha256"),
        "producer_attestation.producer_builds.build_set_sha256",
    )
    builds = checks.sequence(
        producer.get("builds"), "producer_attestation.producer_builds.builds"
    )
    build_commits: Dict[str, str] = {}
    previous_digest: Optional[str] = None
    for index, raw_build in enumerate(builds):
        path = "producer_attestation.producer_builds.builds[{}]".format(index)
        build = checks.mapping(raw_build, path)
        commit = build.get("commit")
        if not isinstance(commit, str) or GIT_COMMIT_RE.fullmatch(commit) is None:
            checks.error(path + ".commit", "must be lowercase 40-hex")
        _validate_run_artifact(build.get("binary"), path + ".binary", checks)
        binary = checks.mapping(build.get("binary"), path + ".binary")
        digest = binary.get("sha256")
        if isinstance(digest, str):
            if previous_digest is not None and digest <= previous_digest:
                checks.error(
                    path + ".binary.sha256",
                    "builds must be strictly sorted by binary SHA-256 and unique",
                )
            previous_digest = digest
            if isinstance(commit, str):
                build_commits[digest] = commit
    try:
        expected_build_set_sha256 = compute_producer_build_set_sha256(
            list(build_commits)
        )
    except (TypeError, ValueError) as exc:
        checks.error(
            "producer_attestation.producer_builds.build_set_sha256",
            "cannot recompute: {}".format(exc),
        )
    else:
        if declared_build_set_sha256 != expected_build_set_sha256:
            checks.error(
                "producer_attestation.producer_builds.build_set_sha256",
                "does not authenticate the exact producer build set",
            )
        if campaign_document.get("producer_build_set_sha256") != expected_build_set_sha256:
            checks.error(
                "campaign.producer_build_set_sha256",
                "does not bind the producer attestation build set",
            )
    chunk_builds = checks.sequence(
        producer.get("chunk_builds"),
        "producer_attestation.producer_builds.chunk_builds",
    )
    campaign_chunks = checks.sequence(campaign_document.get("chunks"), "campaign.chunks")
    if len(chunk_builds) != len(campaign_chunks):
        checks.error(
            "producer_attestation.producer_builds.chunk_builds",
            "must map every campaign chunk exactly once",
        )
    for ordinal, raw_mapping in enumerate(chunk_builds):
        path = "producer_attestation.producer_builds.chunk_builds[{}]".format(ordinal)
        mapping = checks.mapping(raw_mapping, path)
        mapped_index = checks.u64(mapping.get("chunk_index"), path + ".chunk_index")
        expected_index: Optional[int] = None
        if ordinal < len(campaign_chunks) and isinstance(campaign_chunks[ordinal], Mapping):
            expected_index = _canonical_u64(campaign_chunks[ordinal].get("index"))
        if mapped_index is not None and mapped_index != expected_index:
            checks.error(path + ".chunk_index", "does not follow campaign chunk order")
        digest = checks.sha256(mapping.get("binary_sha256"), path + ".binary_sha256")
        if digest is not None and digest not in build_commits:
            checks.error(path + ".binary_sha256", "does not name an authenticated build")
        if ordinal < len(campaign_chunks) and isinstance(campaign_chunks[ordinal], Mapping):
            campaign_digest = campaign_chunks[ordinal].get("producer_build_sha256")
            if digest is not None and digest != campaign_digest:
                checks.error(
                    path + ".binary_sha256",
                    "does not equal campaign chunk producer_build_sha256",
                )
    campaign_builds = {
        chunk.get("producer_build_sha256")
        for chunk in campaign_chunks
        if isinstance(chunk, Mapping)
        and isinstance(chunk.get("producer_build_sha256"), str)
    }
    if set(build_commits) != campaign_builds:
        checks.error(
            "producer_attestation.producer_builds.builds",
            "must equal the exact set of producer builds referenced by campaign chunks",
        )
    policy = checks.mapping(
        attestation.get("generation_policy"), "producer_attestation.generation_policy"
    )
    if policy.get("use_nnue") != "pure":
        checks.error("producer_attestation.generation_policy.use_nnue", "must be pure")
    for name, expected in (
        ("adjudicate_draws_by_score", False),
        ("adjudicate_resignations", False),
        ("syzygy_disabled", True),
        ("complete_trajectory_moves_preserved", True),
        ("role_partition_before_publication", True),
    ):
        if policy.get(name) is not expected:
            checks.error(
                "producer_attestation.generation_policy." + name,
                "must be {}".format(str(expected).lower()),
            )
    try:
        expected = compute_producer_attestation_sha256(attestation)
    except (KeyError, TypeError, ValueError, OverflowError) as exc:
        checks.error("producer_attestation.evidence_sha256", "cannot recompute: {}".format(exc))
    else:
        if attestation.get("evidence_sha256") != expected:
            checks.error(
                "producer_attestation.evidence_sha256",
                "does not authenticate producer policy evidence",
            )
    verification = checks.mapping(
        attestation.get("verification"), "producer_attestation.verification"
    )
    for name in PRODUCER_VERIFICATION_KEYS:
        checks.truth(verification.get(name), "producer_attestation.verification." + name)
    return checks.errors


def validate_semantic_audit(
    audit: Mapping[str, Any],
    campaign_sha256: str,
    campaign_schema_sha256: str,
    producer_sha256: str,
    producer_schema_sha256: str,
    totals: Mapping[str, Any],
) -> List[str]:
    checks = Checks()
    for field, expected_digest, expected_schema in (
        ("campaign", campaign_sha256, campaign_schema_sha256),
        ("producer_attestation", producer_sha256, producer_schema_sha256),
    ):
        _validate_typed_artifact(
            audit.get(field), "semantic_audit." + field, checks, expected_schema
        )
        descriptor = checks.mapping(audit.get(field), "semantic_audit." + field)
        if descriptor.get("sha256") != expected_digest:
            checks.error("semantic_audit." + field + ".sha256", "does not bind the subject")
    scanner = checks.mapping(audit.get("scanner"), "semantic_audit.scanner")
    commit = scanner.get("commit")
    if not isinstance(commit, str) or GIT_COMMIT_RE.fullmatch(commit) is None:
        checks.error("semantic_audit.scanner.commit", "must be lowercase 40-hex")
    _validate_run_artifact(scanner.get("binary"), "semantic_audit.scanner.binary", checks)
    roles = checks.mapping(audit.get("roles"), "semantic_audit.roles")
    for role_name in ("train", "validation"):
        role = checks.mapping(roles.get(role_name), "semantic_audit.roles." + role_name)
        for suffix, total_name in (
            ("records_replayed", role_name + "_records"),
            ("trajectories_replayed", role_name + "_trajectories"),
            ("moves_replayed", role_name + "_moves"),
        ):
            value = checks.u64(role.get(suffix), "semantic_audit.roles.{}.{}".format(role_name, suffix), positive=True)
            expected = totals.get(total_name)
            if value is not None and isinstance(expected, str) and value != int(expected):
                checks.error(
                    "semantic_audit.roles.{}.{}".format(role_name, suffix),
                    "does not cover the full campaign role",
                )
        key_set = checks.mapping(
            role.get("feature_input_keys"),
            "semantic_audit.roles." + role_name + ".feature_input_keys",
        )
        _validate_run_artifact(
            key_set.get("artifact"),
            "semantic_audit.roles." + role_name + ".feature_input_keys.artifact",
            checks,
        )
        observations = checks.u64(
            key_set.get("observations"),
            "semantic_audit.roles." + role_name + ".feature_input_keys.observations",
            positive=True,
        )
        expected_records = totals.get(role_name + "_records")
        if observations is not None and isinstance(expected_records, str) and observations != int(expected_records):
            checks.error(
                "semantic_audit.roles." + role_name + ".feature_input_keys.observations",
                "does not equal campaign role records",
            )
        unique_keys = checks.u64(
            key_set.get("unique_keys"),
            "semantic_audit.roles." + role_name + ".feature_input_keys.unique_keys",
            positive=True,
        )
        if (
            observations is not None
            and unique_keys is not None
            and unique_keys > observations
        ):
            checks.error(
                "semantic_audit.roles." + role_name + ".feature_input_keys.unique_keys",
                "cannot exceed observations",
            )
        checks.sha256(
            key_set.get("ordered_set_sha256"),
            "semantic_audit.roles." + role_name + ".feature_input_keys.ordered_set_sha256",
        )
        stop_reasons = checks.sequence(
            role.get("stop_reasons"), "semantic_audit.roles." + role_name + ".stop_reasons"
        )
        if len(stop_reasons) != 9:
            checks.error(
                "semantic_audit.roles." + role_name + ".stop_reasons",
                "must contain exactly nine counters",
            )
        for index, value in enumerate(stop_reasons):
            checks.u64(value, "semantic_audit.roles.{}.stop_reasons[{}]".format(role_name, index))
        parsed_stop_reasons = [
            int(value)
            for value in stop_reasons
            if isinstance(value, str) and UINT64_RE.fullmatch(value) is not None
        ]
        trajectories = role.get("trajectories_replayed")
        if (
            len(parsed_stop_reasons) == 9
            and isinstance(trajectories, str)
            and UINT64_RE.fullmatch(trajectories) is not None
            and sum(parsed_stop_reasons) != int(trajectories)
        ):
            checks.error(
                "semantic_audit.roles." + role_name + ".stop_reasons",
                "must sum to trajectories_replayed",
            )
        for index in (7, 8):
            if index < len(stop_reasons) and stop_reasons[index] != "0":
                checks.error(
                    "semantic_audit.roles.{}.stop_reasons[{}]".format(role_name, index),
                    "release publication forbids score-draw and resignation stops",
                )
    intersections = checks.mapping(audit.get("intersections"), "semantic_audit.intersections")
    for name in ("raw_record_keys", "feature_input_keys", "split_group_ids"):
        if intersections.get(name) != "0":
            checks.error("semantic_audit.intersections." + name, "must be zero")
    try:
        expected = compute_semantic_audit_sha256(audit)
    except (KeyError, TypeError, ValueError, OverflowError) as exc:
        checks.error("semantic_audit.evidence_sha256", "cannot recompute: {}".format(exc))
    else:
        if audit.get("evidence_sha256") != expected:
            checks.error(
                "semantic_audit.evidence_sha256", "does not authenticate semantic evidence"
            )
    verification = checks.mapping(audit.get("verification"), "semantic_audit.verification")
    for name in SEMANTIC_VERIFICATION_KEYS:
        checks.truth(verification.get(name), "semantic_audit.verification." + name)
    return checks.errors


def validate_reachability_attestation(
    attestation: Mapping[str, Any],
    oracle_payload: bytes,
    campaign_sha256: str,
    campaign_schema_sha256: str,
    producer_sha256: str,
    producer_schema_sha256: str,
    feature_schema_sha256: str,
    policy: Mapping[str, Any],
) -> List[str]:
    """Recompute all physical/derived masks from authenticated oracle bytes."""

    checks = Checks()
    for field, expected_digest, expected_schema in (
        ("campaign", campaign_sha256, campaign_schema_sha256),
        ("producer_attestation", producer_sha256, producer_schema_sha256),
    ):
        _validate_typed_artifact(
            attestation.get(field), "reachability_attestation." + field, checks, expected_schema
        )
        descriptor = checks.mapping(
            attestation.get(field), "reachability_attestation." + field
        )
        if descriptor.get("sha256") != expected_digest:
            checks.error(
                "reachability_attestation." + field + ".sha256",
                "does not bind the publication subject",
            )
    _validate_run_artifact(
        attestation.get("feature_schema"),
        "reachability_attestation.feature_schema",
        checks,
    )
    feature_descriptor = checks.mapping(
        attestation.get("feature_schema"), "reachability_attestation.feature_schema"
    )
    if feature_descriptor.get("sha256") != feature_schema_sha256:
        checks.error(
            "reachability_attestation.feature_schema.sha256",
            "does not bind the loaded feature schema",
        )
    oracle = checks.mapping(attestation.get("oracle"), "reachability_attestation.oracle")
    commit = oracle.get("commit")
    if not isinstance(commit, str) or GIT_COMMIT_RE.fullmatch(commit) is None:
        checks.error("reachability_attestation.oracle.commit", "must be lowercase 40-hex")
    _validate_run_artifact(
        oracle.get("binary"), "reachability_attestation.oracle.binary", checks
    )
    _validate_run_artifact(
        attestation.get("oracle_output"),
        "reachability_attestation.oracle_output",
        checks,
    )
    if len(oracle_payload) != 18772:
        checks.error(
            "reachability_attestation.oracle_output.bytes", "must contain exactly 18772 bytes"
        )
        return checks.errors

    physical_layout = (
        ("half_ka_v2_atomic_hm", 22528, 2816),
        ("atomic_capture_pair", 40012, 5002),
        ("atomic_king_blast_ep", 2304, 288),
        ("atomic_blast_ring", 10240, 1280),
    )
    roles = checks.mapping(attestation.get("roles"), "reachability_attestation.roles")
    calculated: Dict[str, Dict[str, str]] = {"WHITE": {}, "BLACK": {}}
    cursor = 0
    for perspective_id, perspective in enumerate(("WHITE", "BLACK")):
        declared_role = checks.mapping(
            roles.get(perspective), "reachability_attestation.roles." + perspective
        )
        physical_hm = b""
        for slice_id, (field, dimensions, byte_count) in enumerate(physical_layout):
            mask = oracle_payload[cursor : cursor + byte_count]
            cursor += byte_count
            if dimensions % 8 and mask[-1] & ~((1 << (dimensions % 8)) - 1):
                checks.error(
                    "reachability_attestation.roles.{}.{}.sha256".format(perspective, field),
                    "oracle bitmap has nonzero unused high bits",
                )
            digest = hashlib.sha256(
                MASK_DOMAIN
                + bytes.fromhex(feature_schema_sha256)
                + bytes((perspective_id, MASK_KIND_IDS["physical"], slice_id))
                + dimensions.to_bytes(4, "little")
                + byte_count.to_bytes(4, "little")
                + mask
            ).hexdigest()
            calculated[perspective][field] = digest
            declared = checks.mapping(
                declared_role.get(field),
                "reachability_attestation.roles.{}.{}".format(perspective, field),
            )
            if declared.get("indices") != dimensions:
                checks.error(
                    "reachability_attestation.roles.{}.{}.indices".format(perspective, field),
                    "does not match the frozen domain",
                )
            if declared.get("bytes") != byte_count:
                checks.error(
                    "reachability_attestation.roles.{}.{}.bytes".format(perspective, field),
                    "does not match the frozen bitmap size",
                )
            if declared.get("sha256") != digest:
                checks.error(
                    "reachability_attestation.roles.{}.{}.sha256".format(perspective, field),
                    "does not authenticate the oracle bitmap",
                )
            if slice_id == 0:
                physical_hm = mask
        training_mask, virtual_mask = derive_hm_reachability_masks(physical_hm)
        for field, kind, dimensions, mask in (
            ("hm_training", "training", 24576, training_mask),
            ("hm_virtual_factors", "virtual-factor", 768, virtual_mask),
        ):
            digest = hashlib.sha256(
                MASK_DOMAIN
                + bytes.fromhex(feature_schema_sha256)
                + bytes((perspective_id, MASK_KIND_IDS[kind], 0))
                + dimensions.to_bytes(4, "little")
                + len(mask).to_bytes(4, "little")
                + mask
            ).hexdigest()
            calculated[perspective][field] = digest
            declared = checks.mapping(
                declared_role.get(field),
                "reachability_attestation.roles.{}.{}".format(perspective, field),
            )
            if declared.get("indices") != dimensions or declared.get("bytes") != len(mask):
                checks.error(
                    "reachability_attestation.roles.{}.{}".format(perspective, field),
                    "does not match the exact derived-mask dimensions",
                )
            if declared.get("sha256") != digest:
                checks.error(
                    "reachability_attestation.roles.{}.{}.sha256".format(perspective, field),
                    "does not authenticate the derived HM bitmap",
                )
    aggregate = hashlib.sha256(
        MASK_AGGREGATE_DOMAIN
        + b"".join(
            bytes.fromhex(calculated[perspective][field])
            for perspective in ("WHITE", "BLACK")
            for field in MASK_FIELD_IDS
        )
    ).hexdigest()
    if attestation.get("reachability_mask_sha256") != aggregate:
        checks.error(
            "reachability_attestation.reachability_mask_sha256",
            "does not authenticate the twelve recomputed masks",
        )
    if policy.get("reachability_mask_sha256") != aggregate:
        checks.error(
            "reachability_attestation.reachability_mask_sha256",
            "does not equal the campaign coverage-policy aggregate",
        )
    if policy.get("reachability_masks") != calculated:
        checks.error(
            "reachability_attestation.roles",
            "recomputed masks do not equal the campaign coverage-policy masks",
        )
    try:
        expected_evidence = compute_reachability_attestation_sha256(attestation)
    except (KeyError, TypeError, ValueError, OverflowError) as exc:
        checks.error(
            "reachability_attestation.evidence_sha256", "cannot recompute: {}".format(exc)
        )
    else:
        if attestation.get("evidence_sha256") != expected_evidence:
            checks.error(
                "reachability_attestation.evidence_sha256",
                "does not authenticate oracle evidence",
            )
    verification = checks.mapping(
        attestation.get("verification"), "reachability_attestation.verification"
    )
    for name in REACHABILITY_VERIFICATION_KEYS:
        checks.truth(
            verification.get(name), "reachability_attestation.verification." + name
        )
    return checks.errors


def validate_training_run_manifest_v2(manifest: Mapping[str, Any]) -> List[str]:
    """Validate the publication-root training manifest without changing V1."""

    checks = Checks()
    inputs = checks.mapping(manifest.get("inputs"), "run.inputs")
    for path in INPUT_ARTIFACT_PATHS_V2:
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
    _validate_run_artifact(trainer.get("binary"), "run.trainer.binary", checks)
    binary = checks.mapping(trainer.get("binary"), "run.trainer.binary")
    if binary.get("sha256") != trainer.get("artifact_sha256"):
        checks.error(
            "run.trainer.binary.sha256", "must equal run.trainer.artifact_sha256"
        )
    _validate_run_artifact(
        trainer.get("dependency_lock"), "run.trainer.dependency_lock", checks
    )
    _validate_run_artifact(trainer.get("config"), "run.trainer.config", checks)
    checks.u64(trainer.get("training_seed"), "run.trainer.training_seed")
    if trainer.get("deterministic_algorithms") is not True:
        checks.error("run.trainer.deterministic_algorithms", "must be true")
    _validate_typed_artifact(
        trainer.get("environment"), "run.trainer.environment", checks
    )
    schedule = checks.mapping(manifest.get("schedule"), "run.schedule")
    checks.plain_int(schedule.get("batch_size"), "run.schedule.batch_size", 1, (1 << 32) - 1)
    checks.u64(schedule.get("optimizer_steps"), "run.schedule.optimizer_steps")
    checks.plain_int(schedule.get("epochs"), "run.schedule.epochs", 1, (1 << 32) - 1)
    checks.u64(schedule.get("validation_interval_steps"), "run.schedule.validation_interval_steps")
    outputs = checks.mapping(manifest.get("outputs"), "run.outputs")
    for name in ("checkpoint", "network", "training_log", "metrics"):
        _validate_run_artifact(outputs.get(name), "run.outputs." + name, checks)
    for field, compute in (
        ("run_definition_sha256", compute_run_definition_sha256_v2),
        ("input_bundle_sha256", compute_input_bundle_sha256_v2),
    ):
        try:
            expected = compute(manifest)
        except (KeyError, TypeError, ValueError, OverflowError) as exc:
            checks.error("run." + field, "cannot recompute: {}".format(exc))
        else:
            if manifest.get(field) != expected:
                checks.error("run." + field, "does not authenticate the V2 run inputs")
    verification = checks.mapping(manifest.get("verification"), "run.verification")
    for name in (
        "all_inputs_authenticated",
        "campaign_passed",
        "producer_attestation_passed",
        "semantic_audit_passed",
        "reachability_attestation_passed",
        "cross_artifact_hash_chain_passed",
        "run_definition_recomputed",
        "checkpoint_input_bundle_matches",
        "checkpoint_run_definition_matches",
        "checkpoint_campaign_matches",
        "checkpoint_producer_attestation_matches",
        "checkpoint_semantic_audit_matches",
        "checkpoint_reachability_attestation_matches",
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
        if not isinstance(file_name, str) or Path(file_name).name != file_name:
            errors.append(shard_label + ".file: must be a basename")
            continue
        candidate = base / file_name
        if candidate.parent != base:
            errors.append(shard_label + ".file: must resolve inside the manifest directory")
        if candidate in named_paths:
            errors.append(shard_label + ".file: manifest repeats a shard pathname")
        named_paths.add(candidate)
        shards.append(
            ManifestShard(
                index,
                candidate,
                shard_records,
                shard_bytes,
                str(sha256),
            )
        )
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
    global_identity_index: Optional["SplitGroupIndex"] = None,
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
            pathname_stat = os.lstat(shard.path)
            if stat.S_ISLNK(pathname_stat.st_mode):
                raise ValueError("shard symbolic links are forbidden")
            flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
            descriptor = os.open(shard.path, flags)
            try:
                stream = os.fdopen(descriptor, "rb", closefd=False)
                with stream:
                    before = os.fstat(stream.fileno())
                    if (
                        int(pathname_stat.st_dev),
                        int(pathname_stat.st_ino),
                        int(pathname_stat.st_size),
                    ) != (
                        int(before.st_dev),
                        int(before.st_ino),
                        int(before.st_size),
                    ):
                        raise ValueError("shard path changed before structural scan")
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
                    magic, version, header_size, endian, record_size, header_flags = struct.unpack_from(
                        "<8sHHIII", header, 0
                    )
                    header_records = struct.unpack_from("<Q", header, 56)[0]
                    if magic != b"ATBINV2\0":
                        raise ValueError("shard header magic mismatch")
                    if version != 2 or header_size != 96 or endian != 0x01020304:
                        raise ValueError("shard header capability fields mismatch")
                    if record_size != 64 or header_flags != 0:
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
                            raw_key = raw_digest.digest()
                            if not identity_index.add_raw_record(role_id, raw_key):
                                duplicate_raw_records += 1
                            if global_identity_index is not None:
                                global_identity_index.add_raw_record(role_id, raw_key)
                            wdl[result + 1] += 1
                            draws += int(result == 0)
                            best_move_types[move_type] += 1
                            local_index += 1
                        remaining -= chunk_records
                    if stream.read(1):
                        raise ValueError("shard has trailing bytes")
                    after = os.fstat(stream.fileno())
            finally:
                os.close(descriptor)
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
    if global_identity_index is not None:
        global_identity_index.finish_role()
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
    global_split_index: Optional[SplitGroupIndex] = None,
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
                    if global_split_index is not None:
                        global_split_index.add(expected_role, actual_group)
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
        if global_split_index is not None:
            global_split_index.finish_role()
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
    role: str,
    manifest: ManifestSummary,
    stats: Mapping[str, Any],
    policy: Mapping[str, Any],
    require_release_candidate: bool = False,
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
        is_release_candidate = policy.get("status") == "release-candidate"
        if require_release_candidate and not is_release_candidate:
            errors.append(
                role
                + "_manifest.generation: publication requires a release-candidate coverage policy"
            )
        if is_release_candidate or require_release_candidate:
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
    declared_campaign_chunk: Optional[Mapping[str, Any]] = None,
    global_identity_index: Optional[SplitGroupIndex] = None,
    global_split_index: Optional[SplitGroupIndex] = None,
    require_release_candidate: bool = False,
) -> Tuple[List[str], Dict[Path, StreamFileMetadata], Optional[str]]:
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
            errors += _validate_manifest_provenance(
                role,
                summary,
                stats_by_role[role],
                policy,
                require_release_candidate,
            )
    if len(manifests) != 2:
        return errors, metadata, None
    if manifests["train"].generation_sha256 != manifests["validation"].generation_sha256:
        errors.append(
            "train/validation normalized semantic generation profiles must be byte-identical"
        )
    campaign_profile_sha256: Optional[str] = None
    try:
        campaign_profile_sha256 = compute_campaign_profile_sha256(
            policy, manifests["train"].generation
        )
    except (KeyError, TypeError, ValueError, OverflowError) as exc:
        errors.append("campaign homogeneous profile cannot be recomputed: {}".format(exc))
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
                    manifests[role],
                    data_schema_sha256,
                    record_index,
                    global_identity_index,
                )
                errors += dataset_errors
                if summary is not None:
                    datasets[role] = summary
            if len(datasets) == 2:
                raw_intersection = record_index.intersection_count("raw_records")
        finally:
            record_index.close()
    if len(datasets) != 2:
        return errors, metadata, campaign_profile_sha256
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
                    global_split_index,
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
                    if declared_campaign_chunk is not None:
                        declared_role = declared_campaign_chunk.get(role)
                        if not isinstance(declared_role, Mapping):
                            errors.append(
                                "campaign.{}: role metadata is missing".format(role)
                            )
                        else:
                            expected_counts = {
                                "records": manifests[role].records,
                                "trajectories": ledgers[role].trajectories,
                                "moves": ledgers[role].moves,
                            }
                            for field, expected in expected_counts.items():
                                declared = _canonical_u64(declared_role.get(field))
                                if declared != expected:
                                    errors.append(
                                        "campaign.{}.{}: declared value does not match streamed artifact ({})".format(
                                            role, field, expected
                                        )
                                    )
        finally:
            split_index.close()
    return errors, metadata, campaign_profile_sha256


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
        if Path(file_name).name != file_name:
            errors.append(path + ".file: must be a basename")
            continue
        declared_candidate = base / file_name
        try:
            declared_stat = os.lstat(declared_candidate)
        except OSError as exc:
            errors.append(path + ".file: cannot inspect artifact: {}".format(exc))
            continue
        if stat.S_ISLNK(declared_stat.st_mode):
            errors.append(path + ".file: symbolic links are forbidden")
            continue
        candidate = declared_candidate
        if candidate.parent != base:
            errors.append(path + ".file: must resolve inside the sidecar directory")
            continue
        resolved[str(name)] = candidate
        try:
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
            initial = known_files.get(candidate)
            if initial is not None and initial != (byte_count, digest):
                errors.append(path + ".file: changed after initial authentication")
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
    artifact_paths: Sequence[str] = INPUT_ARTIFACT_PATHS,
) -> List[str]:
    """Open and authenticate every file named by a completed training run."""

    errors: List[str] = []
    base = manifest_path.resolve().parent
    inputs = manifest.get("inputs")
    if not isinstance(inputs, Mapping):
        return ["training_run.inputs: must be an object"]

    descriptors: List[Tuple[str, Any]] = []
    for dotted_path in artifact_paths:
        try:
            descriptor = _nested_mapping(inputs, dotted_path)
        except (KeyError, TypeError) as exc:
            errors.append("training_run.inputs.{}: {}".format(dotted_path, exc))
            continue
        descriptors.append(("inputs." + dotted_path, descriptor))
    try:
        if manifest.get("schema_version") == 2:
            descriptors.append(("trainer.binary", _nested_mapping(manifest, "trainer.binary")))
            descriptors.append(
                (
                    "trainer.dependency_lock",
                    _nested_mapping(manifest, "trainer.dependency_lock"),
                )
            )
            descriptors.append(
                ("trainer.environment", _nested_mapping(manifest, "trainer.environment"))
            )
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
        if Path(file_name).name != file_name:
            errors.append(label + ".file: must be a basename")
            continue
        declared_candidate = base / file_name
        try:
            declared_stat = os.lstat(declared_candidate)
        except OSError as exc:
            errors.append(label + ".file: cannot inspect artifact: {}".format(exc))
            continue
        if stat.S_ISLNK(declared_stat.st_mode):
            errors.append(label + ".file: symbolic links are forbidden")
            continue
        candidate = declared_candidate
        if candidate.parent != base:
            errors.append(label + ".file: must resolve inside the manifest directory")
            continue
        try:
            streamed, _ = stream_file_metadata(candidate)
            metadata = (streamed.byte_count, streamed.sha256)
            initial = known_files.get(candidate)
            if initial is not None and initial != metadata:
                errors.append(label + ".file: changed after initial authentication")
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
        if expected is None and dotted_path.startswith("inputs.") is False:
            expected = expected_sha256.get("inputs." + dotted_path)
        if expected is not None and digest != expected:
            errors.append(label + ".sha256: does not bind the validated bundle artifact")
    return errors


def authenticate_artifact_descriptor(
    descriptor: Any,
    owner_path: Path,
    label: str,
    expected_schema_sha256: Optional[str] = None,
) -> Tuple[List[str], Optional[Path], Optional[StreamFileMetadata]]:
    """Authenticate one basename artifact relative to its declaring document."""

    errors: List[str] = []
    if not isinstance(descriptor, Mapping):
        return [label + ": must be an object"], None, None
    file_name = descriptor.get("file")
    if not isinstance(file_name, str):
        return [label + ".file: must be a basename"], None, None
    if Path(file_name).name != file_name:
        return [label + ".file: must be a basename"], None, None
    base = owner_path.resolve().parent
    declared_candidate = base / file_name
    try:
        declared_stat = os.lstat(declared_candidate)
    except OSError as exc:
        return [label + ".file: cannot inspect artifact: {}".format(exc)], None, None
    if stat.S_ISLNK(declared_stat.st_mode):
        return [label + ".file: symbolic links are forbidden"], None, None
    candidate = declared_candidate
    if candidate.parent != base:
        return [label + ".file: must resolve inside the declaring document directory"], None, None
    try:
        metadata, _ = stream_file_metadata(candidate)
    except (OSError, ValueError) as exc:
        return [label + ".file: cannot authenticate artifact: {}".format(exc)], candidate, None
    declared_bytes = descriptor.get("bytes")
    if not isinstance(declared_bytes, str) or UINT64_RE.fullmatch(declared_bytes) is None:
        errors.append(label + ".bytes: must be a canonical uint64 decimal string")
    elif int(declared_bytes) > MAX_U64:
        errors.append(label + ".bytes: exceeds UINT64_MAX")
    elif int(declared_bytes) != metadata.byte_count:
        errors.append(label + ".bytes: does not match exact artifact bytes")
    if descriptor.get("sha256") != metadata.sha256:
        errors.append(label + ".sha256: does not authenticate exact artifact bytes")
    if expected_schema_sha256 is not None:
        if descriptor.get("schema_sha256") != expected_schema_sha256:
            errors.append(label + ".schema_sha256: does not bind the expected schema")
    return errors, candidate, metadata


def authenticate_campaign_artifacts(
    campaign: Mapping[str, Any], campaign_path: Path
) -> Tuple[List[str], Dict[str, Path]]:
    errors: List[str] = []
    resolved: Dict[str, Path] = {}
    schemas = campaign.get("schemas")
    if not isinstance(schemas, Mapping):
        return ["campaign.schemas: must be an object"], resolved
    chunks = campaign.get("chunks")
    if not isinstance(chunks, list):
        return ["campaign.chunks: must be an array"], resolved
    descriptors: List[Tuple[str, Any, str]] = []
    for ordinal, raw_chunk in enumerate(chunks):
        if not isinstance(raw_chunk, Mapping):
            errors.append("campaign.chunks[{}]: must be an object".format(ordinal))
            continue
        prefix = "campaign.chunks[{}]".format(ordinal)
        descriptors.append((prefix + ".coverage_policy", raw_chunk.get("coverage_policy"), "coverage_policy"))
        for role in ("train", "validation"):
            partition = raw_chunk.get(role)
            if not isinstance(partition, Mapping):
                errors.append(prefix + "." + role + ": must be an object")
                continue
            for name in CAMPAIGN_PARTITION_ARTIFACT_KEYS:
                descriptors.append((prefix + "." + role + "." + name, partition.get(name), name))
        descriptors.append((prefix + ".split_audit", raw_chunk.get("split_audit"), "split_audit"))
    schema_name = {
        "manifest": "manifest",
        "trajectory_ledger": "trajectory_ledger",
        "index_coverage": "index_coverage",
        "statistics": "statistics",
        "coverage_policy": "coverage_policy",
        "split_audit": "split_audit",
    }
    seen_identity: Dict[Tuple[int, int], str] = {}
    for label, descriptor, kind in descriptors:
        expected_schema = schemas.get(schema_name[kind])
        descriptor_errors, path, metadata = authenticate_artifact_descriptor(
            descriptor,
            campaign_path,
            label,
            expected_schema if isinstance(expected_schema, str) else None,
        )
        errors += descriptor_errors
        if path is not None:
            resolved[label] = path
        if metadata is not None:
            previous = seen_identity.get(metadata.identity)
            if previous is not None:
                errors.append(label + ".file: aliases artifact " + previous)
            else:
                seen_identity[metadata.identity] = label
    return errors, resolved


def load_campaign_duplicate_limits(
    campaign: Mapping[str, Any],
    resolved_campaign_artifacts: Mapping[str, Path],
) -> Tuple[List[str], Optional[Dict[str, Dict[str, int]]]]:
    """Load one authenticated, homogeneous duplicate policy for the campaign.

    Per-chunk structural validation applies each chunk's ceilings locally.  A
    distributed collection also needs one unambiguous ceiling for the union of
    all chunks; otherwise a repeated chunk can evade the policy while every
    chunk remains valid in isolation.  Publication therefore requires the four
    duplicate ceilings to be identical in every authenticated policy.
    """

    errors: List[str] = []
    expected: Dict[str, Dict[str, Optional[int]]] = {
        role: {"raw_records": None, "feature_inputs": None}
        for role in ("train", "validation")
    }
    chunks = campaign.get("chunks")
    if not isinstance(chunks, list) or not chunks:
        return ["campaign.chunks: duplicate-policy aggregation requires chunks"], None
    policy_fields = {
        "raw_records": "maximum_duplicate_raw_records_ppm",
        "feature_inputs": "maximum_duplicate_feature_inputs_ppm",
    }
    for ordinal, raw_chunk in enumerate(chunks):
        label = "campaign.chunks[{}].coverage_policy".format(ordinal)
        path = resolved_campaign_artifacts.get(label)
        descriptor = (
            raw_chunk.get("coverage_policy")
            if isinstance(raw_chunk, Mapping)
            else None
        )
        if path is None or not isinstance(descriptor, Mapping):
            errors.append(label + ": authenticated duplicate policy is unavailable")
            continue
        try:
            policy, byte_count, digest = load_json_with_metadata(path)
            if descriptor.get("sha256") != digest or descriptor.get("bytes") != str(
                byte_count
            ):
                raise ValueError("coverage policy changed after campaign authentication")
        except (OSError, UnicodeError, ValueError, TypeError) as exc:
            errors.append(label + ": cannot aggregate duplicate limits: {}".format(exc))
            continue
        for role in ("train", "validation"):
            role_policy = policy.get(role)
            if not isinstance(role_policy, Mapping):
                errors.append(label + "." + role + ": must be an object")
                continue
            for metric, field in policy_fields.items():
                value = role_policy.get(field)
                field_path = label + "." + role + "." + field
                if (
                    not isinstance(value, int)
                    or isinstance(value, bool)
                    or not 0 <= value <= PPM_SCALE
                ):
                    errors.append(field_path + ": must be an integer ppm in [0, 1000000]")
                    continue
                prior = expected[role][metric]
                if prior is None:
                    expected[role][metric] = value
                elif prior != value:
                    errors.append(
                        field_path
                        + ": must equal the duplicate ceiling in every campaign policy"
                    )
    if errors or any(
        value is None for role in expected.values() for value in role.values()
    ):
        return errors, None
    return (
        errors,
        {
            role: {metric: int(value) for metric, value in metrics.items()}
            for role, metrics in expected.items()
        },
    )


def _validate_global_duplicate_ppm(
    total: int, unique: int, maximum_ppm: int, path: str
) -> List[str]:
    errors: List[str] = []
    if total <= 0:
        return [path + ": campaign observation total must be positive"]
    if unique < 0 or unique > total:
        return [path + ": unique identities must lie within campaign observations"]
    duplicates = total - unique
    duplicate_ppm = PPM_SCALE * duplicates // total
    if duplicate_ppm > maximum_ppm:
        errors.append(
            path
            + ": global duplicate ppm {} exceeds campaign policy {}".format(
                duplicate_ppm, maximum_ppm
            )
        )
    return errors


def validate_campaign_feature_duplicate_limits(
    semantic_audit: Mapping[str, Any],
    duplicate_limits: Mapping[str, Mapping[str, int]],
) -> List[str]:
    """Apply the authenticated campaign ceiling to global V3 feature keys."""

    errors: List[str] = []
    roles = semantic_audit.get("roles")
    if not isinstance(roles, Mapping):
        return ["semantic_audit.roles: global feature duplicate accounting unavailable"]
    for role in ("train", "validation"):
        role_evidence = roles.get(role)
        key_set = (
            role_evidence.get("feature_input_keys")
            if isinstance(role_evidence, Mapping)
            else None
        )
        observations = (
            _canonical_u64(key_set.get("observations"))
            if isinstance(key_set, Mapping)
            else None
        )
        unique_keys = (
            _canonical_u64(key_set.get("unique_keys"))
            if isinstance(key_set, Mapping)
            else None
        )
        limit = duplicate_limits.get(role, {}).get("feature_inputs")
        path = "semantic_audit.roles.{}.feature_input_keys".format(role)
        if observations is None or unique_keys is None or limit is None:
            errors.append(path + ": global duplicate accounting is incomplete")
            continue
        errors += _validate_global_duplicate_ppm(
            observations, unique_keys, limit, path
        )
    return errors


def validate_campaign_raw_duplicate_limits(
    totals: Mapping[str, Any],
    global_identity_index: "SplitGroupIndex",
    duplicate_limits: Mapping[str, Mapping[str, int]],
) -> List[str]:
    """Apply the authenticated campaign ceiling to global raw-record keys."""

    errors: List[str] = []
    for role_id, role in enumerate(("train", "validation")):
        unique_raw_records, _ = global_identity_index.ordered_summary(
            role_id, "raw_records"
        )
        expected_records = _canonical_u64(totals.get(role + "_records"))
        raw_limit = duplicate_limits.get(role, {}).get("raw_records")
        path = "campaign.{}.raw_record_keys".format(role)
        if expected_records is None or raw_limit is None:
            errors.append(path + ": global duplicate accounting is incomplete")
            continue
        errors += _validate_global_duplicate_ppm(
            expected_records, unique_raw_records, raw_limit, path
        )
    return errors


def validate_campaign_chunk_bundles(
    campaign: Mapping[str, Any],
    resolved_campaign_artifacts: Mapping[str, Path],
    feature_contract: Mapping[str, Any],
    feature_schema_sha256: str,
    index_contract: Mapping[str, Any],
    index_schema_sha256: str,
    ledger_contract: Mapping[str, Any],
    ledger_schema_sha256: str,
    policy_schema: Mapping[str, Any],
    policy_schema_sha256: str,
    stats_schema: Mapping[str, Any],
    stats_schema_sha256: str,
    split_audit_schema: Mapping[str, Any],
    split_audit_schema_sha256: str,
    manifest_schema: Mapping[str, Any],
    manifest_schema_sha256: str,
    data_schema_sha256: str,
    duplicate_limits: Mapping[str, Mapping[str, int]],
) -> List[str]:
    """Re-run the unchanged V1 structural gate over every campaign chunk."""

    errors: List[str] = []
    profile_digests_seen = 0
    chunks = campaign.get("chunks")
    if not isinstance(chunks, list):
        return ["campaign.chunks: must be an array"]
    expected_atcov_size = index_contract.get("file_policy", {}).get("file_size")
    atcov_sizes = (
        {"index_coverage": expected_atcov_size}
        if isinstance(expected_atcov_size, int)
        else {}
    )
    stats_schema_bindings = {
        "atomic_bin_v2_manifest": manifest_schema_sha256,
        "trajectory_ledger": ledger_schema_sha256,
        "index_coverage": index_schema_sha256,
        "coverage_policy": policy_schema_sha256,
    }
    global_directory = tempfile.TemporaryDirectory(prefix="atomic-v3-campaign-index-")
    global_identity_index = SplitGroupIndex(Path(global_directory.name))

    for ordinal, raw_chunk in enumerate(chunks):
        if not isinstance(raw_chunk, Mapping):
            continue
        prefix = "campaign.chunks[{}]".format(ordinal)
        required_paths = {
            "policy": resolved_campaign_artifacts.get(prefix + ".coverage_policy"),
            "train": resolved_campaign_artifacts.get(prefix + ".train.statistics"),
            "validation": resolved_campaign_artifacts.get(
                prefix + ".validation.statistics"
            ),
            "audit": resolved_campaign_artifacts.get(prefix + ".split_audit"),
        }
        if any(path is None for path in required_paths.values()):
            errors.append(prefix + ": structural sidecars were not all authenticated")
            continue
        documents: Dict[str, Mapping[str, Any]] = {}
        document_metadata: Dict[str, Tuple[int, str]] = {}
        document_schemas = {
            "policy": policy_schema,
            "train": stats_schema,
            "validation": stats_schema,
            "audit": split_audit_schema,
        }
        train_chunk = raw_chunk.get("train")
        validation_chunk = raw_chunk.get("validation")
        declared_sidecars = {
            "policy": raw_chunk.get("coverage_policy"),
            "train": (
                train_chunk.get("statistics")
                if isinstance(train_chunk, Mapping)
                else None
            ),
            "validation": (
                validation_chunk.get("statistics")
                if isinstance(validation_chunk, Mapping)
                else None
            ),
            "audit": raw_chunk.get("split_audit"),
        }
        chunk_shape_errors: List[str] = []
        for name, raw_path in required_paths.items():
            assert raw_path is not None
            try:
                value, byte_count, digest = load_json_with_metadata(raw_path)
            except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
                chunk_shape_errors.append(
                    "{}.{}: cannot load canonical sidecar: {}".format(prefix, name, exc)
                )
                continue
            documents[name] = value
            document_metadata[name] = (byte_count, digest)
            declared_sidecar = declared_sidecars.get(name)
            if not isinstance(declared_sidecar, Mapping):
                chunk_shape_errors.append(
                    "{}.{}: campaign sidecar descriptor is missing".format(prefix, name)
                )
            else:
                if declared_sidecar.get("sha256") != digest:
                    chunk_shape_errors.append(
                        "{}.{}.sha256: changed after campaign authentication".format(
                            prefix, name
                        )
                    )
                if declared_sidecar.get("bytes") != str(byte_count):
                    chunk_shape_errors.append(
                        "{}.{}.bytes: changed after campaign authentication".format(
                            prefix, name
                        )
                    )
            shape = validate_json_schema(value, document_schemas[name], prefix + "." + name)
            chunk_shape_errors += shape
            if not shape:
                chunk_shape_errors += _validate_canonical_json_bytes(
                    value,
                    document_schemas[name],
                    byte_count,
                    digest,
                    prefix + "." + name,
                )
        errors += chunk_shape_errors
        if chunk_shape_errors or set(documents) != {"policy", "train", "validation", "audit"}:
            continue
        policy = documents["policy"]
        train_stats = documents["train"]
        validation_stats = documents["validation"]
        split_audit = documents["audit"]
        if policy.get("feature_schema_sha256") != feature_schema_sha256:
            errors.append(prefix + ".policy.feature_schema_sha256: contract mismatch")
        if policy.get("stats_schema_sha256") != stats_schema_sha256:
            errors.append(prefix + ".policy.stats_schema_sha256: contract mismatch")
        policy_partition = policy.get("partition")
        if not isinstance(policy_partition, Mapping):
            errors.append(prefix + ".policy.partition: must be an object")
        else:
            if policy_partition.get("config_sha256") != raw_chunk.get(
                "partition_config_sha256"
            ):
                errors.append(
                    prefix
                    + ".partition_config_sha256: does not bind the chunk coverage policy"
                )
            provenance = policy_partition.get("provenance")
            profile = (
                provenance.get("generation_profile")
                if isinstance(provenance, Mapping)
                else None
            )
            if not isinstance(profile, Mapping) or profile.get(
                "generation_seed"
            ) != raw_chunk.get("generation_seed"):
                errors.append(
                    prefix
                    + ".generation_seed: does not bind the chunk coverage-policy provenance"
                )
        chunk_errors = validate_bundle(
            policy,
            train_stats,
            validation_stats,
            split_audit,
            feature_contract,
            index_contract,
            ledger_contract,
        )
        errors += [prefix + ": " + error for error in chunk_errors]
        if chunk_errors:
            continue

        known_files = {
            required_paths[name]: document_metadata[name] for name in required_paths
        }
        payloads: Dict[Path, bytes] = {}
        metadata: Dict[Path, StreamFileMetadata] = {}
        train_auth_errors, train_artifacts = authenticate_declared_artifacts(
            train_stats,
            required_paths["train"],
            prefix + ".train",
            known_files,
            stats_schema_bindings,
            atcov_sizes,
            payloads,
            metadata,
        )
        validation_auth_errors, validation_artifacts = authenticate_declared_artifacts(
            validation_stats,
            required_paths["validation"],
            prefix + ".validation",
            known_files,
            stats_schema_bindings,
            atcov_sizes,
            payloads,
            metadata,
        )
        audit_auth_errors, audit_artifacts = authenticate_declared_artifacts(
            split_audit,
            required_paths["audit"],
            prefix + ".audit",
            known_files,
            {
                "train_stats": stats_schema_sha256,
                "validation_stats": stats_schema_sha256,
                "coverage_policy": policy_schema_sha256,
            },
            payload_cache=payloads,
            metadata_cache=metadata,
        )
        errors += train_auth_errors + validation_auth_errors + audit_auth_errors
        for role, role_artifacts in (
            ("train", train_artifacts),
            ("validation", validation_artifacts),
        ):
            expected_role_paths = {
                "coverage_policy": required_paths["policy"],
                "atomic_bin_v2_manifest": resolved_campaign_artifacts.get(
                    prefix + "." + role + ".manifest"
                ),
                "trajectory_ledger": resolved_campaign_artifacts.get(
                    prefix + "." + role + ".trajectory_ledger"
                ),
                "index_coverage": resolved_campaign_artifacts.get(
                    prefix + "." + role + ".index_coverage"
                ),
            }
            for name, resolved_expected in expected_role_paths.items():
                if role_artifacts.get(name) != resolved_expected:
                    errors.append(
                        "{}.{}.artifacts.{}.file: does not bind the campaign artifact".format(
                            prefix, role, name
                        )
                    )
        for name, expected in (
            ("train_stats", required_paths["train"]),
            ("validation_stats", required_paths["validation"]),
            ("coverage_policy", required_paths["policy"]),
        ):
            if audit_artifacts.get(name) != expected:
                errors.append(
                    "{}.audit.artifacts.{}.file: does not bind the campaign sidecar".format(
                        prefix, name
                    )
                )
        if train_auth_errors or validation_auth_errors or audit_auth_errors:
            continue
        streamed_errors, streamed_metadata, campaign_profile_sha256 = (
            validate_streamed_bundle_artifacts(
                policy,
                train_stats,
                validation_stats,
                split_audit,
                train_artifacts,
                validation_artifacts,
                manifest_schema,
                manifest_schema_sha256,
                data_schema_sha256,
                ledger_contract,
                ledger_schema_sha256,
                raw_chunk,
                global_identity_index,
                global_identity_index,
                True,
            )
        )
        errors += [prefix + ": " + error for error in streamed_errors]
        metadata.update(streamed_metadata)
        if campaign_profile_sha256 is not None:
            profile_digests_seen += 1
            if campaign_profile_sha256 != campaign.get("homogeneous_profile_sha256"):
                errors.append(
                    prefix
                    + ".homogeneous_profile_sha256: chunk producer/search profile differs from campaign"
                )
        for role, stats, artifacts in (
            ("train", train_stats, train_artifacts),
            ("validation", validation_stats, validation_artifacts),
        ):
            atcov_path = artifacts.get("index_coverage")
            atcov_payload = payloads.get(atcov_path) if atcov_path is not None else None
            if atcov_payload is None:
                errors.append(prefix + "." + role + ": index-coverage bytes unavailable")
            else:
                errors += [
                    prefix + ": " + error
                    for error in validate_atcov_file(
                        atcov_payload,
                        role,
                        stats,
                        feature_schema_sha256,
                        index_schema_sha256,
                        index_contract,
                    )
                ]
    if profile_digests_seen != len(chunks):
        errors.append(
            "campaign.homogeneous_profile_sha256: was not recomputed for every chunk"
        )
    totals = campaign.get("totals")
    if isinstance(totals, Mapping):
        for role_id, role in enumerate(("train", "validation")):
            split_count, _ = global_identity_index.ordered_summary(role_id, "split_groups")
            expected_trajectories = _canonical_u64(totals.get(role + "_trajectories"))
            if expected_trajectories != split_count:
                errors.append(
                    "campaign.{}.trajectories: global split-group identities contain duplicates or omissions".format(
                        role
                    )
                )
        errors += validate_campaign_raw_duplicate_limits(
            totals, global_identity_index, duplicate_limits
        )
        if global_identity_index.intersection_count("raw_records"):
            errors.append(
                "campaign.intersections.raw_record_keys: train and validation overlap across chunks"
            )
        if global_identity_index.intersection_count("split_groups"):
            errors.append(
                "campaign.intersections.split_group_ids: train and validation overlap across chunks"
            )
    global_identity_index.close()
    global_directory.cleanup()
    return errors


def validate_campaign_semantic_stop_reasons(
    campaign: Mapping[str, Any],
    campaign_artifacts: Mapping[str, Path],
    semantic_audit: Mapping[str, Any],
) -> List[str]:
    """Recompute campaign-wide stop counters from every authenticated stats sidecar."""

    errors: List[str] = []
    aggregate = {"train": [0] * 9, "validation": [0] * 9}
    chunks = campaign.get("chunks")
    if not isinstance(chunks, list):
        return ["semantic_audit.roles: campaign chunks are unavailable"]
    for ordinal, raw_chunk in enumerate(chunks):
        if not isinstance(raw_chunk, Mapping):
            continue
        for role in ("train", "validation"):
            label = "campaign.chunks[{}].{}.statistics".format(ordinal, role)
            path = campaign_artifacts.get(label)
            partition = raw_chunk.get(role)
            descriptor = (
                partition.get("statistics") if isinstance(partition, Mapping) else None
            )
            if path is None or not isinstance(descriptor, Mapping):
                errors.append(label + ": authenticated statistics are unavailable")
                continue
            try:
                stats, byte_count, digest = load_json_with_metadata(path)
                if descriptor.get("sha256") != digest or descriptor.get("bytes") != str(
                    byte_count
                ):
                    raise ValueError("statistics changed after campaign authentication")
                raw_stop_reasons = stats["trajectory_events"]["stop_reasons"]
                if not isinstance(raw_stop_reasons, list) or len(raw_stop_reasons) != 9:
                    raise ValueError("stop_reasons must contain nine counters")
                parsed = [_canonical_u64(value) for value in raw_stop_reasons]
                if any(value is None for value in parsed):
                    raise ValueError("stop_reasons contain a non-canonical uint64")
            except (OSError, UnicodeError, ValueError, KeyError, TypeError) as exc:
                errors.append(label + ": cannot aggregate stop reasons: {}".format(exc))
                continue
            for index, value in enumerate(parsed):
                assert value is not None
                if aggregate[role][index] > MAX_U64 - value:
                    errors.append(
                        "semantic_audit.roles.{}.stop_reasons[{}]: campaign aggregate overflows uint64".format(
                            role, index
                        )
                    )
                else:
                    aggregate[role][index] += value
    semantic_roles = semantic_audit.get("roles")
    if not isinstance(semantic_roles, Mapping):
        return errors + ["semantic_audit.roles: must be an object"]
    for role in ("train", "validation"):
        role_evidence = semantic_roles.get(role)
        declared = (
            role_evidence.get("stop_reasons")
            if isinstance(role_evidence, Mapping)
            else None
        )
        expected = [str(value) for value in aggregate[role]]
        if declared != expected:
            errors.append(
                "semantic_audit.roles.{}.stop_reasons: does not equal the authenticated campaign statistics aggregate".format(
                    role
                )
            )
    return errors


def validate_all_campaign_policy_reachability(
    campaign: Mapping[str, Any],
    campaign_artifacts: Mapping[str, Path],
    reachability_attestation: Mapping[str, Any],
) -> List[str]:
    """Bind every distributed chunk policy to the oracle-authenticated mask set."""

    errors: List[str] = []
    roles = reachability_attestation.get("roles")
    declared_masks: Dict[str, Dict[str, Any]] = {}
    if isinstance(roles, Mapping):
        for perspective in ("WHITE", "BLACK"):
            role = roles.get(perspective)
            if isinstance(role, Mapping):
                declared_masks[perspective] = {
                    field: (
                        role[field].get("sha256")
                        if isinstance(role.get(field), Mapping)
                        else None
                    )
                    for field in MASK_FIELD_IDS
                }
    expected_aggregate = reachability_attestation.get("reachability_mask_sha256")
    chunks = campaign.get("chunks")
    if not isinstance(chunks, list):
        return ["reachability_attestation.roles: campaign chunks are unavailable"]
    for ordinal, raw_chunk in enumerate(chunks):
        if not isinstance(raw_chunk, Mapping):
            continue
        label = "campaign.chunks[{}].coverage_policy".format(ordinal)
        path = campaign_artifacts.get(label)
        descriptor = raw_chunk.get("coverage_policy")
        if path is None or not isinstance(descriptor, Mapping):
            errors.append(label + ": authenticated coverage policy is unavailable")
            continue
        try:
            policy, byte_count, digest = load_json_with_metadata(path)
            if descriptor.get("sha256") != digest or descriptor.get("bytes") != str(
                byte_count
            ):
                raise ValueError("coverage policy changed after campaign authentication")
        except (OSError, UnicodeError, ValueError, TypeError) as exc:
            errors.append(label + ": cannot verify reachability masks: {}".format(exc))
            continue
        if policy.get("reachability_mask_sha256") != expected_aggregate:
            errors.append(
                label
                + ".reachability_mask_sha256: does not equal the oracle-authenticated aggregate"
            )
        if policy.get("reachability_masks") != declared_masks:
            errors.append(
                label
                + ".reachability_masks: does not equal all twelve oracle-authenticated masks"
            )
    return errors


def validate_ordered_key_artifact(
    descriptor: Mapping[str, Any], owner_path: Path, unique_keys: int, label: str
) -> Tuple[List[str], Optional[Path], Optional[str], Optional[StreamFileMetadata]]:
    errors, path, metadata = authenticate_artifact_descriptor(
        descriptor, owner_path, label
    )
    if path is None or metadata is None:
        return errors, path, None, metadata
    if metadata.byte_count != unique_keys * 32:
        errors.append(label + ".bytes: must equal unique_keys * 32")
        return errors, path, None, metadata
    try:
        pathname_stat = os.lstat(path)
        if stat.S_ISLNK(pathname_stat.st_mode):
            raise ValueError("symbolic links are forbidden")
        flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
        descriptor_fd = os.open(path, flags)
        ordered_digest = hashlib.sha256(
            ORDERED_SET_DOMAIN + unique_keys.to_bytes(8, "little")
        )
        raw_digest = hashlib.sha256()
        previous: Optional[bytes] = None
        ordering_failed = False
        try:
            with os.fdopen(descriptor_fd, "rb", closefd=False) as stream:
                before = os.fstat(stream.fileno())
                if not stat.S_ISREG(before.st_mode):
                    raise ValueError("ordered set is not a regular file")
                if (int(before.st_dev), int(before.st_ino)) != metadata.identity:
                    raise ValueError("ordered set identity changed before scan")
                if _stat_change_token(before) != metadata.change_token:
                    raise ValueError("ordered set changed after authentication")
                while True:
                    chunk = stream.read(STREAM_CHUNK_SIZE)
                    if not chunk:
                        break
                    if len(chunk) % 32:
                        raise ValueError("ordered-set chunk is not key aligned")
                    ordered_digest.update(chunk)
                    raw_digest.update(chunk)
                    for offset in range(0, len(chunk), 32):
                        current = chunk[offset : offset + 32]
                        if (
                            not ordering_failed
                            and previous is not None
                            and current <= previous
                        ):
                            errors.append(
                                label + ".file: keys must be strictly increasing and unique"
                            )
                            ordering_failed = True
                        previous = current
                after = os.fstat(stream.fileno())
        finally:
            os.close(descriptor_fd)
        if _stat_change_token(before) != _stat_change_token(after):
            raise ValueError("ordered set changed during scan")
        if raw_digest.hexdigest() != metadata.sha256:
            raise ValueError("ordered set bytes changed after authentication")
    except (OSError, ValueError) as exc:
        errors.append(label + ".file: cannot validate ordered set: {}".format(exc))
        return errors, path, None, metadata
    return errors, path, ordered_digest.hexdigest(), metadata


def ordered_key_sets_intersect(
    left: Path,
    right: Path,
    authenticated: Optional[Mapping[Path, StreamFileMetadata]] = None,
) -> bool:
    """Merge two authenticated ordered fixed-width sets in bounded memory."""

    descriptors: List[int] = []
    streams: List[BinaryIO] = []
    before_tokens: List[Tuple[int, int, int, int, int]] = []
    try:
        for path in (left, right):
            pathname_stat = os.lstat(path)
            if stat.S_ISLNK(pathname_stat.st_mode):
                raise ValueError("ordered key-set symbolic links are forbidden")
            flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
            descriptor = os.open(path, flags)
            descriptors.append(descriptor)
            stream = os.fdopen(descriptor, "rb", closefd=False)
            streams.append(stream)
            opened_stat = os.fstat(descriptor)
            if not stat.S_ISREG(opened_stat.st_mode):
                raise ValueError("ordered key set is not a regular file")
            if (int(pathname_stat.st_dev), int(pathname_stat.st_ino)) != (
                int(opened_stat.st_dev),
                int(opened_stat.st_ino),
            ):
                raise ValueError("ordered key-set path changed before intersection scan")
            expected = authenticated.get(path) if authenticated is not None else None
            if expected is not None and (
                (int(opened_stat.st_dev), int(opened_stat.st_ino)) != expected.identity
                or _stat_change_token(opened_stat) != expected.change_token
            ):
                raise ValueError("ordered key set changed after authentication")
            before_tokens.append(_stat_change_token(opened_stat))
        left_key = streams[0].read(32)
        right_key = streams[1].read(32)
        intersects = False
        while len(left_key) == 32 and len(right_key) == 32:
            if left_key == right_key:
                intersects = True
                break
            if left_key < right_key:
                left_key = streams[0].read(32)
            else:
                right_key = streams[1].read(32)
        for index, descriptor in enumerate(descriptors):
            if _stat_change_token(os.fstat(descriptor)) != before_tokens[index]:
                raise ValueError("ordered key set changed during intersection scan")
        return intersects
    finally:
        for stream in streams:
            stream.close()
        for descriptor in descriptors:
            os.close(descriptor)


def _parser() -> argparse.ArgumentParser:
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(
        description=(
            "Validate AtomicNNUEV3 structural bundles and authenticated publication evidence."
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
        "--training-run-v2-schema",
        type=Path,
        default=root / "schemas" / "atomic-v3-training-run-manifest-v2.json",
    )
    parser.add_argument(
        "--training-environment-schema",
        type=Path,
        default=root / "schemas" / "atomic-v3-training-environment-v1.json",
    )
    parser.add_argument(
        "--campaign",
        type=Path,
        help="ordered multi-chunk dataset campaign root",
    )
    parser.add_argument(
        "--producer-attestation",
        type=Path,
        help="authenticated producer policy and binary evidence",
    )
    parser.add_argument(
        "--semantic-audit",
        type=Path,
        help="engine-backed replay and feature-input-key evidence",
    )
    parser.add_argument(
        "--reachability-attestation",
        type=Path,
        help="independent physical-reachability oracle evidence",
    )
    parser.add_argument(
        "--campaign-schema",
        type=Path,
        default=root / "schemas" / "atomic-v3-dataset-campaign-v1.json",
    )
    parser.add_argument(
        "--producer-attestation-schema",
        type=Path,
        default=root / "schemas" / "atomic-v3-producer-attestation-v1.json",
    )
    parser.add_argument(
        "--semantic-audit-schema",
        type=Path,
        default=root / "schemas" / "atomic-v3-semantic-audit-v1.json",
    )
    parser.add_argument(
        "--reachability-attestation-schema",
        type=Path,
        default=root / "schemas" / "atomic-v3-reachability-attestation-v1.json",
    )
    parser.add_argument(
        "--trusted-producer-binary-sha256",
        action="append",
        help="optional repeatable defense-in-depth pin; when supplied, the exact set must cover every producer build",
    )
    parser.add_argument(
        "--trusted-producer-build-set-sha256",
        help="operator trust pin for the authenticated content-addressed producer build set",
    )
    parser.add_argument(
        "--trusted-semantic-scanner-binary-sha256",
        help="operator trust pin for the authenticated engine-backed scanner",
    )
    parser.add_argument(
        "--trusted-reachability-oracle-binary-sha256",
        help="operator trust pin for the authenticated independent oracle",
    )
    parser.add_argument(
        "--trusted-trainer-binary-sha256",
        help="operator trust pin required when validating a training-run V2",
    )
    parser.add_argument(
        "--require-publication-ready",
        action="store_true",
        help=(
            "require dataset publication when no run is supplied, otherwise require training publication"
        ),
    )
    parser.add_argument(
        "--require-dataset-publication-ready",
        action="store_true",
        help="require the authenticated dataset campaign and all publication evidence",
    )
    parser.add_argument(
        "--require-training-publication-ready",
        action="store_true",
        help="require a controlled, authenticated training execution (currently fail-closed)",
    )
    parser.add_argument("--json", action="store_true", help="emit a machine-readable result")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _parser().parse_args(argv)
    publication_errors: List[str] = []
    training_errors: List[str] = []
    publication_documents: Dict[str, Mapping[str, Any]] = {}
    publication_metadata: Dict[str, Tuple[int, str]] = {}
    training_run_version: Optional[int] = None
    publication_paths = {
        "campaign": args.campaign,
        "producer_attestation": args.producer_attestation,
        "semantic_audit": args.semantic_audit,
        "reachability_attestation": args.reachability_attestation,
    }
    trusted_producer_binary_sha256 = args.trusted_producer_binary_sha256 or []
    trusted_producer_build_set_sha256 = args.trusted_producer_build_set_sha256
    trusted_binary_sha256 = {
        "semantic": args.trusted_semantic_scanner_binary_sha256,
        "reachability": args.trusted_reachability_oracle_binary_sha256,
    }
    supplied_trust_pins = [
        name for name, digest in trusted_binary_sha256.items() if digest is not None
    ]
    complete_trust_pins = (
        trusted_producer_build_set_sha256 is not None
        and len(supplied_trust_pins) == len(trusted_binary_sha256)
    )
    trusted_trainer_binary_sha256 = args.trusted_trainer_binary_sha256
    for name, digest in trusted_binary_sha256.items():
        if digest is not None and SHA256_RE.fullmatch(digest) is None:
            publication_errors.append(
                "publication.trusted_{}_binary_sha256: must be lowercase SHA-256".format(
                    name
                )
            )
    if (
        trusted_producer_build_set_sha256 is not None
        and SHA256_RE.fullmatch(trusted_producer_build_set_sha256) is None
    ):
        publication_errors.append(
            "publication.trusted_producer_build_set_sha256: must be lowercase SHA-256"
        )
    for ordinal, digest in enumerate(trusted_producer_binary_sha256):
        if SHA256_RE.fullmatch(digest) is None:
            publication_errors.append(
                "publication.trusted_producer_binary_sha256[{}]: must be lowercase SHA-256".format(
                    ordinal
                )
            )
    if len(set(trusted_producer_binary_sha256)) != len(
        trusted_producer_binary_sha256
    ):
        publication_errors.append(
            "publication.trusted_producer_binary_sha256: duplicate pins are forbidden"
        )
    if supplied_trust_pins and not complete_trust_pins:
        publication_errors.append(
            "publication.trusted_binary_sha256: producer build-set, semantic scanner and reachability oracle pins are required together"
        )
    if (
        trusted_trainer_binary_sha256 is not None
        and SHA256_RE.fullmatch(trusted_trainer_binary_sha256) is None
    ):
        training_errors.append(
            "training_publication.trusted_trainer_binary_sha256: must be lowercase SHA-256"
        )
    supplied_publication = [name for name, path in publication_paths.items() if path is not None]
    complete_publication = len(supplied_publication) == len(publication_paths)
    if supplied_publication and not complete_publication:
        for name, path in publication_paths.items():
            if path is None:
                publication_errors.append(
                    "publication.{}: evidence document is required with the other publication roots".format(name)
                )
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
        training_run_schema, training_run_schema_bytes, training_run_schema_digest = (
            load_json_with_metadata(args.training_run_schema)
        )
        training_run_v2_schema, training_run_v2_schema_bytes, training_run_v2_schema_digest = (
            load_json_with_metadata(args.training_run_v2_schema)
        )
        (
            training_environment_schema,
            training_environment_schema_bytes,
            training_environment_schema_digest,
        ) = load_json_with_metadata(args.training_environment_schema)
        transitive_publication_schema_digests = {
            "feature": feature_digest,
            "data": dataset_schema_digest,
            "manifest": manifest_schema_digest,
            "trajectory_ledger": ledger_digest,
            "index_coverage": index_digest,
            "statistics": stats_schema_digest,
            "coverage_policy": policy_schema_digest,
            "split_audit": audit_schema_digest,
        }
        if supplied_publication:
            for name, digest in transitive_publication_schema_digests.items():
                if digest != TRANSITIVE_PUBLICATION_SCHEMA_SHA256[name]:
                    publication_errors.append(
                        "publication.{}_schema.sha256: does not equal the frozen transitive release schema".format(
                            name
                        )
                    )
        publication_schemas: Dict[str, Mapping[str, Any]] = {}
        publication_schema_metadata: Dict[str, Tuple[int, str]] = {}
        for name, path in (
            ("campaign", args.campaign_schema),
            ("producer_attestation", args.producer_attestation_schema),
            ("semantic_audit", args.semantic_audit_schema),
            ("reachability_attestation", args.reachability_attestation_schema),
        ):
            schema_value, schema_bytes, schema_digest = load_json_with_metadata(path)
            publication_schemas[name] = schema_value
            publication_schema_metadata[name] = (schema_bytes, schema_digest)
            if schema_digest != PUBLICATION_SCHEMA_SHA256[name]:
                publication_errors.append(
                    "publication.{}_schema.sha256: does not equal the frozen release schema".format(
                        name
                    )
                )
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
        for name, path in publication_paths.items():
            if path is None:
                continue
            value, value_bytes, value_digest = load_json_with_metadata(path)
            publication_documents[name] = value
            publication_metadata[name] = (value_bytes, value_digest)
            shape_errors = validate_json_schema(value, publication_schemas[name], name)
            publication_errors += shape_errors
            if not shape_errors:
                publication_errors += _validate_canonical_json_bytes(
                    value,
                    publication_schemas[name],
                    value_bytes,
                    value_digest,
                    name,
                )

        training_run: Optional[Mapping[str, Any]] = None
        if args.training_run_manifest is not None:
            training_run, run_bytes, run_digest = load_json_with_metadata(
                args.training_run_manifest
            )
            training_run_version = training_run.get("schema_version")
            if (
                training_run_version == 2
                and training_run_v2_schema_digest
                != PUBLICATION_SCHEMA_SHA256["training_run_v2"]
            ):
                training_errors.append(
                    "training_publication.training_run_v2_schema.sha256: does not equal the frozen release schema"
                )
            if (
                training_run_version == 2
                and training_environment_schema_digest
                != PUBLICATION_SCHEMA_SHA256["training_environment"]
            ):
                training_errors.append(
                    "training_publication.training_environment_schema.sha256: does not equal the frozen release schema"
                )
            selected_run_schema = (
                training_run_v2_schema if training_run_version == 2 else training_run_schema
            )
            run_shape_errors = validate_json_schema(
                training_run, selected_run_schema, "training_run"
            )
            run_error_target = training_errors
            run_error_target += run_shape_errors
            if not run_shape_errors:
                run_error_target += _validate_canonical_json_bytes(
                    training_run,
                    selected_run_schema,
                    run_bytes,
                    run_digest,
                    "training_run",
                )
                run_error_target += (
                    validate_training_run_manifest_v2(training_run)
                    if training_run_version == 2
                    else validate_training_run_manifest(training_run)
                )
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
                args.training_run_schema.resolve(): (
                    training_run_schema_bytes,
                    training_run_schema_digest,
                ),
                args.training_run_v2_schema.resolve(): (
                    training_run_v2_schema_bytes,
                    training_run_v2_schema_digest,
                ),
                args.training_environment_schema.resolve(): (
                    training_environment_schema_bytes,
                    training_environment_schema_digest,
                ),
            }
            for name, path in (
                ("campaign", args.campaign_schema),
                ("producer_attestation", args.producer_attestation_schema),
                ("semantic_audit", args.semantic_audit_schema),
                ("reachability_attestation", args.reachability_attestation_schema),
            ):
                known_files[path.resolve()] = publication_schema_metadata[name]
            for name, path in publication_paths.items():
                if path is not None and name in publication_metadata:
                    known_files[path.resolve()] = publication_metadata[name]
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
            if (
                training_run is not None
                and args.training_run_manifest is not None
                and training_run_version != 2
            ):
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
                training_errors += authenticate_training_run_artifacts(
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
                streamed_errors, streamed_metadata, _ = validate_streamed_bundle_artifacts(
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

            if complete_publication and not errors and not publication_errors:
                campaign = publication_documents["campaign"]
                producer_attestation = publication_documents["producer_attestation"]
                semantic_audit = publication_documents["semantic_audit"]
                expected_campaign_schemas = {
                    "feature": feature_digest,
                    "data": dataset_schema_digest,
                    "manifest": manifest_schema_digest,
                    "trajectory_ledger": ledger_digest,
                    "index_coverage": index_digest,
                    "statistics": stats_schema_digest,
                    "coverage_policy": policy_schema_digest,
                    "split_audit": audit_schema_digest,
                }
                campaign_contract_errors = validate_dataset_campaign(
                    campaign, expected_campaign_schemas
                )
                publication_errors += campaign_contract_errors
                campaign_artifact_errors, campaign_artifacts = (
                    authenticate_campaign_artifacts(campaign, args.campaign)
                )
                publication_errors += campaign_artifact_errors
                campaign_duplicate_limits: Optional[
                    Dict[str, Dict[str, int]]
                ] = None
                if not campaign_contract_errors and not campaign_artifact_errors:
                    duplicate_limit_errors, campaign_duplicate_limits = (
                        load_campaign_duplicate_limits(
                            campaign, campaign_artifacts
                        )
                    )
                    publication_errors += duplicate_limit_errors
                    if campaign_duplicate_limits is not None:
                        publication_errors += validate_campaign_chunk_bundles(
                            campaign,
                            campaign_artifacts,
                            feature_contract,
                            feature_digest,
                            index_contract,
                            index_digest,
                            ledger_contract,
                            ledger_digest,
                            policy_schema,
                            policy_schema_digest,
                            stats_schema,
                            stats_schema_digest,
                            split_audit_schema,
                            audit_schema_digest,
                            manifest_schema,
                            manifest_schema_digest,
                            dataset_schema_digest,
                            campaign_duplicate_limits,
                        )

                # The structural CLI bundle must be one exact chunk of the
                # authenticated distributed collection, never an unrelated sample.
                structural_binding_count = 0
                for raw_chunk in campaign.get("chunks", []):
                    if not isinstance(raw_chunk, Mapping):
                        continue
                    train = raw_chunk.get("train")
                    validation = raw_chunk.get("validation")
                    if not isinstance(train, Mapping) or not isinstance(validation, Mapping):
                        continue
                    if (
                        isinstance(raw_chunk.get("coverage_policy"), Mapping)
                        and raw_chunk["coverage_policy"].get("sha256") == policy_digest
                        and isinstance(train.get("statistics"), Mapping)
                        and train["statistics"].get("sha256") == train_digest
                        and isinstance(validation.get("statistics"), Mapping)
                        and validation["statistics"].get("sha256") == validation_digest
                        and isinstance(raw_chunk.get("split_audit"), Mapping)
                        and raw_chunk["split_audit"].get("sha256") == audit_digest
                    ):
                        structural_binding_count += 1
                if structural_binding_count != 1:
                    publication_errors.append(
                        "campaign.chunks: must bind the validated structural bundle exactly once"
                    )

                campaign_schema_digest = publication_schema_metadata["campaign"][1]
                producer_schema_digest = publication_schema_metadata[
                    "producer_attestation"
                ][1]
                semantic_schema_digest = publication_schema_metadata["semantic_audit"][1]
                publication_errors += validate_producer_attestation(
                    producer_attestation,
                    campaign,
                    publication_metadata["campaign"][1],
                    campaign_schema_digest,
                )
                producer_builds = producer_attestation.get("producer_builds", {})
                build_records = (
                    producer_builds.get("builds", [])
                    if isinstance(producer_builds, Mapping)
                    else []
                )
                commit_by_binary = {
                    build.get("binary", {}).get("sha256"): build.get("commit")
                    for build in build_records
                    if isinstance(build, Mapping)
                    and isinstance(build.get("binary"), Mapping)
                }
                declared_build_set = (
                    producer_builds.get("build_set_sha256")
                    if isinstance(producer_builds, Mapping)
                    else None
                )
                if (
                    trusted_producer_build_set_sha256 is not None
                    and declared_build_set != trusted_producer_build_set_sha256
                ):
                    publication_errors.append(
                        "producer_attestation.producer_builds.build_set_sha256: does not equal the operator trust pin"
                    )
                declared_binary_digests = sorted(commit_by_binary)
                if trusted_producer_binary_sha256 and sorted(
                    set(trusted_producer_binary_sha256)
                ) != declared_binary_digests:
                    publication_errors.append(
                        "producer_attestation.producer_builds.builds: does not equal the optional operator binary pin set"
                    )
                for ordinal, raw_chunk in enumerate(campaign.get("chunks", [])):
                    policy_path = campaign_artifacts.get(
                        "campaign.chunks[{}].coverage_policy".format(ordinal)
                    )
                    if policy_path is None:
                        continue
                    try:
                        chunk_policy = load_json(policy_path)
                        generator_commit = chunk_policy["partition"]["provenance"][
                            "generator_commit"
                        ]
                    except (OSError, ValueError, KeyError, TypeError) as exc:
                        publication_errors.append(
                            "producer_attestation.producer_builds: cannot inspect chunk {} provenance: {}".format(
                                ordinal, exc
                            )
                        )
                    else:
                        selected_digest = (
                            raw_chunk.get("producer_build_sha256")
                            if isinstance(raw_chunk, Mapping)
                            else None
                        )
                        if generator_commit != commit_by_binary.get(selected_digest):
                            publication_errors.append(
                                "producer_attestation.producer_builds: selected build commit does not equal chunk {} generator_commit".format(
                                    ordinal
                                )
                            )
                producer_campaign_errors, producer_campaign_path, _ = (
                    authenticate_artifact_descriptor(
                        producer_attestation.get("campaign"),
                        args.producer_attestation,
                        "producer_attestation.campaign",
                        campaign_schema_digest,
                    )
                )
                publication_errors += producer_campaign_errors
                if (
                    producer_campaign_path is not None
                    and producer_campaign_path != args.campaign.resolve()
                ):
                    publication_errors.append(
                        "producer_attestation.campaign.file: must resolve to --campaign"
                    )
                for ordinal, build in enumerate(build_records):
                    binary = build.get("binary") if isinstance(build, Mapping) else None
                    producer_binary_errors, _, _ = authenticate_artifact_descriptor(
                        binary,
                        args.producer_attestation,
                        "producer_attestation.producer_builds.builds[{}].binary".format(
                            ordinal
                        ),
                    )
                    publication_errors += producer_binary_errors

                publication_errors += validate_semantic_audit(
                    semantic_audit,
                    publication_metadata["campaign"][1],
                    campaign_schema_digest,
                    publication_metadata["producer_attestation"][1],
                    producer_schema_digest,
                    campaign.get("totals", {}),
                )
                if campaign_duplicate_limits is not None:
                    publication_errors += validate_campaign_feature_duplicate_limits(
                        semantic_audit, campaign_duplicate_limits
                    )
                publication_errors += validate_campaign_semantic_stop_reasons(
                    campaign, campaign_artifacts, semantic_audit
                )
                declared_scanner_commit = semantic_audit.get("scanner", {}).get("commit")
                for ordinal, _raw_chunk in enumerate(campaign.get("chunks", [])):
                    policy_path = campaign_artifacts.get(
                        "campaign.chunks[{}].coverage_policy".format(ordinal)
                    )
                    if policy_path is None:
                        continue
                    try:
                        chunk_policy = load_json(policy_path)
                        engine_commit = chunk_policy["partition"]["provenance"][
                            "engine_commit"
                        ]
                    except (OSError, ValueError, KeyError, TypeError) as exc:
                        publication_errors.append(
                            "semantic_audit.scanner.commit: cannot inspect chunk {} provenance: {}".format(
                                ordinal, exc
                            )
                        )
                    else:
                        if engine_commit != declared_scanner_commit:
                            publication_errors.append(
                                "semantic_audit.scanner.commit: does not equal chunk {} engine_commit".format(
                                    ordinal
                                )
                            )
                for field, expected_path, expected_schema in (
                    ("campaign", args.campaign, campaign_schema_digest),
                    (
                        "producer_attestation",
                        args.producer_attestation,
                        producer_schema_digest,
                    ),
                ):
                    subject_errors, subject_path, _ = authenticate_artifact_descriptor(
                        semantic_audit.get(field),
                        args.semantic_audit,
                        "semantic_audit." + field,
                        expected_schema,
                    )
                    publication_errors += subject_errors
                    if subject_path is not None and subject_path != expected_path.resolve():
                        publication_errors.append(
                            "semantic_audit.{}.file: must resolve to its CLI publication root".format(
                                field
                            )
                        )
                scanner_binary = semantic_audit.get("scanner", {}).get("binary")
                scanner_errors, _, _ = authenticate_artifact_descriptor(
                    scanner_binary,
                    args.semantic_audit,
                    "semantic_audit.scanner.binary",
                )
                publication_errors += scanner_errors
                if (
                    trusted_binary_sha256["semantic"] is not None
                    and isinstance(scanner_binary, Mapping)
                    and scanner_binary.get("sha256")
                    != trusted_binary_sha256["semantic"]
                ):
                    publication_errors.append(
                        "semantic_audit.scanner.binary.sha256: does not equal the operator trust pin"
                    )
                key_paths: Dict[str, Path] = {}
                key_metadata: Dict[Path, StreamFileMetadata] = {}
                for role in ("train", "validation"):
                    key_set = (
                        semantic_audit.get("roles", {})
                        .get(role, {})
                        .get("feature_input_keys", {})
                    )
                    try:
                        unique_keys = int(key_set.get("unique_keys", "-1"))
                    except (TypeError, ValueError):
                        unique_keys = -1
                    if unique_keys >= 0 and isinstance(key_set, Mapping):
                        (
                            key_errors,
                            key_path,
                            ordered_digest,
                            authenticated_metadata,
                        ) = validate_ordered_key_artifact(
                            key_set.get("artifact", {}),
                            args.semantic_audit,
                            unique_keys,
                            "semantic_audit.roles.{}.feature_input_keys.artifact".format(
                                role
                            ),
                        )
                        publication_errors += key_errors
                        if key_path is not None:
                            key_paths[role] = key_path
                            if authenticated_metadata is not None:
                                key_metadata[key_path] = authenticated_metadata
                        if (
                            ordered_digest is not None
                            and key_set.get("ordered_set_sha256") != ordered_digest
                        ):
                            publication_errors.append(
                                "semantic_audit.roles.{}.feature_input_keys.ordered_set_sha256: "
                                "does not authenticate the ordered key set".format(role)
                            )
                if set(key_paths) == {"train", "validation"}:
                    try:
                        intersects = ordered_key_sets_intersect(
                            key_paths["train"], key_paths["validation"], key_metadata
                        )
                    except (OSError, ValueError) as exc:
                        publication_errors.append(
                            "semantic_audit.intersections.feature_input_keys: cannot scan: {}".format(
                                exc
                            )
                        )
                    else:
                        if intersects:
                            publication_errors.append(
                                "semantic_audit.intersections.feature_input_keys: authenticated sets overlap"
                            )

                reachability = publication_documents["reachability_attestation"]
                reachability_schema_digest = publication_schema_metadata[
                    "reachability_attestation"
                ][1]
                for field, expected_path, expected_schema in (
                    ("campaign", args.campaign, campaign_schema_digest),
                    (
                        "producer_attestation",
                        args.producer_attestation,
                        producer_schema_digest,
                    ),
                ):
                    subject_errors, subject_path, _ = authenticate_artifact_descriptor(
                        reachability.get(field),
                        args.reachability_attestation,
                        "reachability_attestation." + field,
                        expected_schema,
                    )
                    publication_errors += subject_errors
                    if subject_path is not None and subject_path != expected_path.resolve():
                        publication_errors.append(
                            "reachability_attestation.{}.file: must resolve to its CLI root".format(
                                field
                            )
                        )
                feature_errors, feature_path, _ = authenticate_artifact_descriptor(
                    reachability.get("feature_schema"),
                    args.reachability_attestation,
                    "reachability_attestation.feature_schema",
                )
                publication_errors += feature_errors
                if feature_path is not None and feature_path != args.feature_contract.resolve():
                    publication_errors.append(
                        "reachability_attestation.feature_schema.file: must resolve to --feature-contract"
                    )
                oracle_binary_errors, _, _ = authenticate_artifact_descriptor(
                    reachability.get("oracle", {}).get("binary"),
                    args.reachability_attestation,
                    "reachability_attestation.oracle.binary",
                )
                publication_errors += oracle_binary_errors
                oracle_binary = reachability.get("oracle", {}).get("binary")
                if (
                    trusted_binary_sha256["reachability"] is not None
                    and isinstance(oracle_binary, Mapping)
                    and oracle_binary.get("sha256")
                    != trusted_binary_sha256["reachability"]
                ):
                    publication_errors.append(
                        "reachability_attestation.oracle.binary.sha256: does not equal the operator trust pin"
                    )
                (
                    oracle_output_errors,
                    oracle_output_path,
                    oracle_output_metadata,
                ) = authenticate_artifact_descriptor(
                    reachability.get("oracle_output"),
                    args.reachability_attestation,
                    "reachability_attestation.oracle_output",
                )
                publication_errors += oracle_output_errors
                oracle_payload = b""
                if oracle_output_path is not None and not oracle_output_errors:
                    try:
                        oracle_payload, second_oracle_metadata = _read_bounded_regular_file(
                            oracle_output_path,
                            18772,
                            "reachability oracle output",
                        )
                        if (
                            oracle_output_metadata is None
                            or second_oracle_metadata != oracle_output_metadata
                        ):
                            raise ValueError(
                                "oracle output changed after initial authentication"
                            )
                    except (OSError, ValueError) as exc:
                        publication_errors.append(
                            "reachability_attestation.oracle_output.file: {}".format(exc)
                        )
                if oracle_payload:
                    publication_errors += validate_reachability_attestation(
                        reachability,
                        oracle_payload,
                        publication_metadata["campaign"][1],
                        campaign_schema_digest,
                        publication_metadata["producer_attestation"][1],
                        producer_schema_digest,
                        feature_digest,
                        policy,
                    )
                publication_errors += validate_all_campaign_policy_reachability(
                    campaign, campaign_artifacts, reachability
                )

                if (
                    training_run is not None
                    and training_run_version == 2
                    and args.training_run_manifest is not None
                ):
                    run_expected_sha256_v2 = {
                        "inputs.feature_schema": feature_digest,
                        "inputs.dataset_schema": dataset_schema_digest,
                        "inputs.manifest_schema": manifest_schema_digest,
                        "inputs.trajectory_ledger_schema": ledger_digest,
                        "inputs.index_coverage_schema": index_digest,
                        "inputs.statistics_schema": stats_schema_digest,
                        "inputs.coverage_policy_schema": policy_schema_digest,
                        "inputs.split_audit_schema": audit_schema_digest,
                        "inputs.training_environment_schema": training_environment_schema_digest,
                        "inputs.dataset_campaign_schema": campaign_schema_digest,
                        "inputs.producer_attestation_schema": producer_schema_digest,
                        "inputs.semantic_audit_schema": semantic_schema_digest,
                        "inputs.reachability_attestation_schema": reachability_schema_digest,
                        "inputs.campaign": publication_metadata["campaign"][1],
                        "inputs.producer_attestation": publication_metadata[
                            "producer_attestation"
                        ][1],
                        "inputs.semantic_audit": publication_metadata["semantic_audit"][1],
                        "inputs.reachability_attestation": publication_metadata[
                            "reachability_attestation"
                        ][1],
                    }
                    run_known_files = dict(known_files)
                    run_known_files.update(
                        {
                            path: (metadata.byte_count, metadata.sha256)
                            for path, metadata in artifact_metadata.items()
                        }
                    )
                    training_errors += authenticate_training_run_artifacts(
                        training_run,
                        args.training_run_manifest,
                        run_known_files,
                        run_expected_sha256_v2,
                        INPUT_ARTIFACT_PATHS_V2,
                    )
                    run_inputs = training_run.get("inputs", {})
                    environment_schema_descriptor = (
                        run_inputs.get("training_environment_schema", {})
                        if isinstance(run_inputs, Mapping)
                        else {}
                    )
                    if (
                        environment_schema_descriptor.get("schema_sha256")
                        != training_environment_schema_digest
                    ):
                        training_errors.append(
                            "training_run.inputs.training_environment_schema.schema_sha256: does not equal the operator-pinned environment schema"
                        )
                    trainer = training_run.get("trainer", {})
                    environment_descriptor = (
                        trainer.get("environment", {})
                        if isinstance(trainer, Mapping)
                        else {}
                    )
                    if (
                        environment_descriptor.get("schema_sha256")
                        != training_environment_schema_digest
                    ):
                        training_errors.append(
                            "training_run.trainer.environment.schema_sha256: does not bind the authenticated environment schema"
                        )
                    (
                        environment_errors,
                        environment_path,
                        environment_metadata,
                    ) = authenticate_artifact_descriptor(
                        environment_descriptor,
                        args.training_run_manifest,
                        "training_run.trainer.environment",
                        training_environment_schema_digest,
                    )
                    training_errors += environment_errors
                    if environment_path is not None and not environment_errors:
                        try:
                            (
                                environment_document,
                                environment_bytes,
                                environment_digest,
                            ) = load_json_with_metadata(environment_path)
                        except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
                            training_errors.append(
                                "training_run.trainer.environment.file: cannot load canonical descriptor: {}".format(
                                    exc
                                )
                            )
                        else:
                            shape_errors = validate_json_schema(
                                environment_document,
                                training_environment_schema,
                                "training_environment",
                            )
                            training_errors += shape_errors
                            if not shape_errors:
                                training_errors += _validate_canonical_json_bytes(
                                    environment_document,
                                    training_environment_schema,
                                    environment_bytes,
                                    environment_digest,
                                    "training_environment",
                                )
                            if environment_metadata is None or (
                                environment_metadata.byte_count,
                                environment_metadata.sha256,
                            ) != (environment_bytes, environment_digest):
                                training_errors.append(
                                    "training_run.trainer.environment.file: changed after authentication"
                                )
                            if environment_document.get("trainer_binary_sha256") != trainer.get(
                                "artifact_sha256"
                            ):
                                training_errors.append(
                                    "training_environment.trainer_binary_sha256: does not bind the trainer binary"
                                )
                            dependency_lock = trainer.get("dependency_lock", {})
                            if environment_document.get(
                                "dependency_lock_sha256"
                            ) != (
                                dependency_lock.get("sha256")
                                if isinstance(dependency_lock, Mapping)
                                else None
                            ):
                                training_errors.append(
                                    "training_environment.dependency_lock_sha256: does not bind the dependency lock"
                                )
                            devices = environment_document.get("accelerator_devices")
                            if isinstance(devices, list) and devices != sorted(set(devices)):
                                training_errors.append(
                                    "training_environment.accelerator_devices: must be strictly sorted and unique"
                                )
                            determinism = environment_document.get("determinism", {})
                            cublas_config = (
                                determinism.get("cublas_workspace_config")
                                if isinstance(determinism, Mapping)
                                else None
                            )
                            cuda_version = environment_document.get("cuda_version")
                            if (cuda_version is None) != (cublas_config is None):
                                training_errors.append(
                                    "training_environment.determinism.cublas_workspace_config: must be set exactly when CUDA is present"
                                )
                    trainer_binary = training_run.get("trainer", {}).get("binary")
                    if (
                        trusted_trainer_binary_sha256 is not None
                        and isinstance(trainer_binary, Mapping)
                        and trainer_binary.get("sha256")
                        != trusted_trainer_binary_sha256
                    ):
                        training_errors.append(
                            "training_run.trainer.binary.sha256: does not equal the operator trust pin"
                        )
                    for field, schema_name in (
                        ("campaign", "dataset_campaign_schema"),
                        ("producer_attestation", "producer_attestation_schema"),
                        ("semantic_audit", "semantic_audit_schema"),
                        ("reachability_attestation", "reachability_attestation_schema"),
                    ):
                        descriptor = run_inputs.get(field, {}) if isinstance(run_inputs, Mapping) else {}
                        schema_descriptor = (
                            run_inputs.get(schema_name, {}) if isinstance(run_inputs, Mapping) else {}
                        )
                        if descriptor.get("schema_sha256") != schema_descriptor.get("sha256"):
                            training_errors.append(
                                "training_run.inputs.{}.schema_sha256: does not bind its schema input".format(
                                    field
                                )
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
    if training_run_version == 2 and not complete_publication:
        training_errors.append(
            "training_run: schema_version 2 requires all four CLI publication roots"
        )
    if training_run_version == 2 and trusted_trainer_binary_sha256 is None:
        training_errors.append(
            "training_run: schema_version 2 requires an operator-trusted trainer binary pin"
        )
    missing_gate_labels = [
        "engine-backed legal trajectory/result/terminal replay evidence",
        "engine-backed AtomicNNUEV3 feature_input_key recomputation and cross-split proof",
        "authenticated independent-oracle reproduction of all four physical reachability masks",
        "producer/manifest evidence that adjudicate_resignations is disabled",
    ]
    if (
        complete_publication
        and complete_trust_pins
        and not publication_errors
    ):
        pending_dataset_publication_gates: List[str] = []
    elif complete_publication and not publication_errors:
        pending_dataset_publication_gates = [
            "operator-trusted producer build-set, semantic-scanner and reachability-oracle pins"
        ]
    elif complete_publication:
        pending_dataset_publication_gates = [
            "authenticated publication evidence failed validation"
        ]
    else:
        pending_dataset_publication_gates = missing_gate_labels
    dataset_publication_ready = (
        structural_ok
        and complete_publication
        and complete_trust_pins
        and not publication_errors
    )
    training_run_supplied = args.training_run_manifest is not None
    training_run_structural_ok: Optional[bool] = (
        structural_ok
        and not training_errors
        and (training_run_version != 2 or dataset_publication_ready)
        if training_run_supplied
        else None
    )
    # V1 is a frozen structural compatibility contract.  V2 authenticates its
    # declared inputs and outputs, but neither is proof that the trainer ran in
    # a controlled execution.  Until that executor evidence exists, training
    # publication is deliberately fail-closed.
    controlled_execution_verified = False
    training_publication_ready: Optional[bool] = (
        False if training_run_supplied else None
    )
    publication_ready = (
        bool(training_publication_ready)
        if training_run_supplied
        else dataset_publication_ready
    )
    pending_training_publication_gates: List[str] = (
        []
        if not training_run_supplied
        else [
            "authenticated controlled trainer execution and output provenance"
        ]
        if training_run_structural_ok
        else ["training run structural/authentication validation"]
    )
    errors += publication_errors + training_errors
    if args.require_dataset_publication_ready and not dataset_publication_ready:
        errors.append(
            "dataset_publication: authenticated campaign evidence and all operator trust roots are required"
        )
    if args.require_training_publication_ready and not training_publication_ready:
        errors.append(
            "training_publication: controlled authenticated execution evidence is required"
        )
    if args.require_publication_ready and not publication_ready:
        errors.append(
            "publication: a supplied training run requires controlled authenticated execution evidence"
            if training_run_supplied
            else "publication: all campaign, semantic replay, feature-input proof, independent "
            "physical-mask oracle reproduction and producer resignation-policy evidence "
            "artifacts plus operator-trusted build-set/scanner/oracle pins must be supplied "
            "and authenticate successfully"
        )
    if not structural_ok:
        status = "structural-fail"
    elif training_run_supplied:
        if training_errors:
            status = "training-run-gate-fail"
        elif training_publication_ready:
            status = "training-publication-pass"
        elif args.require_publication_ready or args.require_training_publication_ready:
            status = "training-publication-gate-fail"
        else:
            status = "training-structural-pass-publication-pending"
    elif dataset_publication_ready:
        status = "dataset-publication-pass"
    elif errors:
        status = "publication-gate-fail"
    else:
        status = "structural-pass"
    if args.json:
        print(
            json.dumps(
                {
                    "ok": not errors,
                    "structural_ok": structural_ok,
                    "dataset_publication_ready": dataset_publication_ready,
                    "training_run_structural_ok": training_run_structural_ok,
                    "controlled_execution_verified": controlled_execution_verified,
                    "training_publication_ready": training_publication_ready,
                    "publication_ready": publication_ready,
                    "status": status,
                    "pending_dataset_publication_gates": pending_dataset_publication_gates,
                    "pending_training_publication_gates": pending_training_publication_gates,
                    "pending_publication_gates": (
                        pending_training_publication_gates
                        if training_run_supplied
                        else pending_dataset_publication_gates
                    ),
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
        if training_run_supplied:
            print(
                "Atomic V3 training run structural/authentication validation: PASS "
                "(training publication pending controlled execution evidence)"
            )
        elif dataset_publication_ready:
            print("Atomic V3 dataset publication validation: PASS")
        else:
            print(
                "Atomic V3 bundle structural validation: PASS "
                "(publication readiness pending authenticated evidence)"
            )
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
