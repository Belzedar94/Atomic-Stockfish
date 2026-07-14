from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import os
import struct
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable

import pytest
from jsonschema.validators import Draft202012Validator


ROOT = Path(__file__).resolve().parents[2]
TOOL_PATH = ROOT / "tools" / "validate_atomic_v3_bundle.py"
SPEC = importlib.util.spec_from_file_location("validate_atomic_v3_bundle", TOOL_PATH)
assert SPEC is not None and SPEC.loader is not None
VALIDATOR = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = VALIDATOR
SPEC.loader.exec_module(VALIDATOR)

SHA = "ab" * 32
FEATURE_SCHEMA_SHA = hashlib.sha256(
    (ROOT / "schemas" / "atomic-nnue-v3.json").read_bytes()
).hexdigest()
STATS_SCHEMA_SHA = hashlib.sha256(
    (ROOT / "schemas" / "atomic-v3-dataset-stats-v1.json").read_bytes()
).hexdigest()
RECORDS = 50_000
SEMANTIC_IDS = VALIDATOR.SEMANTIC_COUNTER_IDS
SLICE_IDS = VALIDATOR.SLICE_IDS
MASK_DIMENSIONS = (22_528, 40_012, 2_304, 10_240)
ROLE_TRAJECTORY_CACHE: dict[
    tuple[str, int, int, int, int, str, str], tuple[bytes, bytes, tuple[int, ...]]
] = {}
ATOMIC_BIN_V2_SHARD_CACHE: dict[str, bytes] = {}


def _all_reachable_bitmap(count: int) -> bytes:
    full_bytes, remainder = divmod(count, 8)
    tail = bytes(((1 << remainder) - 1,)) if remainder else b""
    return b"\xff" * full_bytes + tail


def _structural_mask_digest_bytes(
    perspective_id: int, kind_id: int, slice_id: int, count: int, mask: bytes
) -> str:
    payload = (
        VALIDATOR.MASK_DOMAIN
        + bytes.fromhex(FEATURE_SCHEMA_SHA)
        + bytes((perspective_id, kind_id, slice_id))
        + count.to_bytes(4, "little")
        + len(mask).to_bytes(4, "little")
        + mask
    )
    return hashlib.sha256(payload).hexdigest()


def _structural_mask_digest(
    perspective_id: int, kind_id: int, slice_id: int, count: int, mask: bytes
) -> str:
    return _structural_mask_digest_bytes(
        perspective_id, kind_id, slice_id, count, mask
    )


def _derived_hm_masks(physical: bytes) -> tuple[bytes, bytes]:
    training = bytearray(24_576 // 8)
    virtual = bytearray(768 // 8)
    for physical_index in range(22_528):
        if not ((physical[physical_index // 8] >> (physical_index % 8)) & 1):
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


PHYSICAL_MASKS = tuple(_all_reachable_bitmap(count) for count in MASK_DIMENSIONS)
HM_TRAINING_MASK, HM_VIRTUAL_MASK = _derived_hm_masks(PHYSICAL_MASKS[0])
MASK_SPECS = tuple(
    (field, 0, slice_id, count, PHYSICAL_MASKS[slice_id])
    for slice_id, (field, count) in enumerate(
        zip(VALIDATOR.MASK_FIELD_IDS[:4], MASK_DIMENSIONS)
    )
) + (
    ("hm_training", 1, 0, 24_576, HM_TRAINING_MASK),
    ("hm_virtual_factors", 2, 0, 768, HM_VIRTUAL_MASK),
)
MASKS = {
    perspective: {
        field: _structural_mask_digest(perspective_id, kind_id, slice_id, count, mask)
        for field, kind_id, slice_id, count, mask in MASK_SPECS
    }
    for perspective_id, perspective in enumerate(("WHITE", "BLACK"))
}
MASK_AGGREGATE = hashlib.sha256(
    VALIDATOR.MASK_AGGREGATE_DOMAIN
    + b"".join(
        bytes.fromhex(MASKS[perspective][field])
        for perspective in ("WHITE", "BLACK")
        for field in VALIDATOR.MASK_FIELD_IDS
    )
).hexdigest()


def _load(name: str) -> dict[str, Any]:
    return json.loads((ROOT / "schemas" / name).read_text(encoding="utf-8"))


def _artifact(name: str) -> dict[str, Any]:
    return {"file": name, "bytes": "1", "sha256": SHA, "schema_sha256": SHA}


def _run_artifact(name: str, digest: str = SHA) -> dict[str, Any]:
    return {"file": name, "bytes": "1", "sha256": digest}


def _manifest_generation() -> dict[str, Any]:
    return {
        "resolved_seed": "9",
        "atomic960": False,
        "threads": 1,
        "hash_mb": "16",
        "use_nnue": "pure",
        "options": {
            "search_depth_min": 3,
            "search_depth_max": 3,
            "nodes": "0",
            "requested_records": str(RECORDS),
            "records_per_shard": str(RECORDS),
            "eval_limit": 3000,
            "eval_diff_limit": 64000,
            "random_move_min_ply": 1,
            "random_move_max_ply": 24,
            "random_move_count": 5,
            "random_move_like_apery": 0,
            "random_multi_pv": 5,
            "random_multi_pv_diff": 100,
            "random_multi_pv_depth": 3,
            "write_min_ply": 0,
            "write_max_ply": 30_000,
            "keep_draws": "1",
            "adjudicate_draws_by_score": False,
            "adjudicate_insufficient": False,
            "filter_captures": False,
            "filter_checks": False,
            "filter_promotions": False,
            "random_file_name": False,
            "set_recommended_uci_options_seen": True,
        },
    }


def _provenance() -> dict[str, Any]:
    return {
        "engine_commit": "1" * 40,
        "generator_commit": "2" * 40,
        "tools_commit": "3" * 40,
        "teacher_network_sha256": "4" * 64,
        "opening_book_sha256": None,
        "generation_profile": {
            "generation_seed": "9",
            "qsearch_mode": "atomic-qsearch-v1",
            "exclude_captures": False,
            "exclude_promotions": False,
            "exclude_checks": False,
            "opening_mode": "builtin-startpos",
            "atomic_bin_v2_generation_sha256": (
                VALIDATOR.compute_atomic_bin_v2_generation_sha256(_manifest_generation())
            ),
            "adjudicate_draws_by_score": False,
            "adjudicate_resignations": False,
        },
    }


def _partition() -> dict[str, Any]:
    partition = {
        "config_sha256": "",
        "method": "content-hash-trajectory-v1",
        "split_seed": "7",
        "validation_threshold_u64": str(1 << 63),
        "provenance": _provenance(),
    }
    partition["config_sha256"] = VALIDATOR.compute_partition_config_sha256(partition)
    return partition


def _masks() -> dict[str, Any]:
    return copy.deepcopy(MASKS)


def _gate() -> dict[str, Any]:
    return {
        "minimum_records": "1",
        "minimum_trajectories": "1",
        "minimum_coverage_ppm_each_perspective": {
            name: 1 for name in VALIDATOR.POLICY_COVERAGE_IDS
        },
        "minimum_semantic_events_each_perspective": {
            name: "1" for name in SEMANTIC_IDS
        },
        "maximum_duplicate_raw_records_ppm": 0,
        "maximum_duplicate_feature_inputs_ppm": 0,
    }


def _policy() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "policy_id": "atomic-v3-test-policy",
        "status": "release-candidate",
        "feature_schema_sha256": FEATURE_SCHEMA_SHA,
        "stats_schema_sha256": STATS_SCHEMA_SHA,
        "partition": _partition(),
        "reachability_mask_sha256": MASK_AGGREGATE,
        "reachability_masks": _masks(),
        "histogram_boundaries": {
            name: [0, 10]
            for name in ("score_cp", "ply", "rule50", "piece_count", "material_cp")
        },
        "train": _gate(),
        "validation": _gate(),
        "global_gates": {
            "require_both_perspectives": True,
            "active_feature_capacity": 1024,
            "require_zero_invalid_records": True,
            "require_zero_truncation": True,
            "require_zero_overflow": True,
            "require_zero_cross_split_record_keys": True,
            "require_zero_cross_split_feature_input_keys": True,
            "require_zero_cross_split_group_ids": True,
        },
    }


def _active_distribution() -> dict[str, Any]:
    return {
        "minimum": 1,
        "maximum": 1,
        "sum": str(RECORDS),
        "observations": str(RECORDS),
        "p50": 1,
        "p95": 1,
        "p99": 1,
    }


def _perspective_coverage() -> dict[str, Any]:
    dimensions = {
        "half_ka_v2_atomic_hm": 22_528,
        "atomic_capture_pair": 40_012,
        "atomic_king_blast_ep": 2_304,
        "atomic_blast_ring": 10_240,
    }
    result: dict[str, Any] = {}
    for name, dimension in dimensions.items():
        result[name] = {
            "physical_dimensions": dimension,
            "structurally_reachable_indices": dimension,
            "structurally_unreachable_indices": 0,
            "observed_reachable_indices": dimension,
            "reachable_unobserved_indices": 0,
            "occurrence_count": str(RECORDS),
            "active_per_position": _active_distribution(),
        }
    result.update(
        {
            "hm_training": {
                "training_dimensions": 24_576,
                "structurally_reachable_training_indices": 22_528,
                "structurally_unreachable_training_indices": 2_048,
                "observed_reachable_training_indices": 22_528,
                "reachable_unobserved_training_indices": 0,
                "virtual_factor_dimensions": 768,
                "structurally_reachable_virtual_factor_indices": 736,
                "structurally_unreachable_virtual_factor_indices": 32,
                "observed_reachable_virtual_factor_indices": 736,
                "reachable_unobserved_virtual_factor_indices": 0,
            },
            "hm_king_buckets": [str(RECORDS)] + ["0"] * 31,
            "hm_mirror_branches": {"unmirrored": str(RECORDS), "mirrored": "0"},
            "capture_pair_classes": [[['0'] * 7 for _ in range(5)] for _ in range(2)],
            "king_blast_ep_classes": {
                "by_actor_class": [["0"] * 18 for _ in range(2)],
                "by_center": ["0"] * 64,
            },
            "blast_ring_classes": {
                "by_actor_collateral_offset_class": [
                    [[["0"] * 5 for _ in range(8)] for _ in range(2)]
                    for _ in range(2)
                ],
                "by_center": ["0"] * 64,
            },
            "semantic_counters": {name: "1" for name in SEMANTIC_IDS},
        }
    )
    result["capture_pair_classes"][0][0][0] = str(RECORDS)
    result["king_blast_ep_classes"]["by_actor_class"][0][0] = str(RECORDS)
    result["king_blast_ep_classes"]["by_center"][0] = str(RECORDS)
    result["blast_ring_classes"]["by_actor_collateral_offset_class"][0][0][0][
        0
    ] = str(RECORDS)
    result["blast_ring_classes"]["by_center"][0] = str(RECORDS)
    return result


def _stats(role: str, policy: dict[str, Any]) -> dict[str, Any]:
    partition = policy["partition"]
    histograms = {
        name: {"policy_name": name, "counts": [str(RECORDS), "0", "0"]}
        for name in ("score_cp", "ply", "rule50", "piece_count", "material_cp")
    }
    return {
        "schema_version": 1,
        "role": role,
        "provenance": copy.deepcopy(partition["provenance"]),
        "artifacts": {
            "atomic_bin_v2_manifest": _artifact(role + "-manifest.json"),
            "trajectory_ledger": _artifact(role + ".attraj"),
            "index_coverage": _artifact(role + ".atcov"),
            "coverage_policy": _artifact("policy.json"),
        },
        "backend": {
            "name": "AtomicNNUEV3",
            "file_version": "0xA70C0003",
            "feature_schema_sha256": policy["feature_schema_sha256"],
            "reachability_mask_sha256": policy["reachability_mask_sha256"],
            "reachability_masks": copy.deepcopy(policy["reachability_masks"]),
        },
        "scanner": {
            "commit": "8" * 40,
            "artifact_sha256": "9" * 64,
            "algorithm_version": "atomic-v3-full-refresh-stats-v1",
            "oracle": "independent-i32-full-refresh",
        },
        "scan": {
            "mode": "full",
            "records_scanned": str(RECORDS),
            "perspectives_scanned": str(2 * RECORDS),
            "strict_eof": True,
            "all_shards_authenticated": True,
            "all_ledger_entries_structurally_scanned": True,
            "invalid_records": "0",
            "truncated_active_lists": "0",
            "accumulator_overflows": "0",
            "max_active_observed": {"WHITE": 1, "BLACK": 1},
        },
        "split": {
            "partition_config_sha256": partition["config_sha256"],
            "method": partition["method"],
            "split_seed": partition["split_seed"],
            "validation_threshold_u64": partition["validation_threshold_u64"],
            "ledger_trajectory_count": "2",
            "ledger_record_count": str(RECORDS),
        },
        "distribution": {
            "wdl_side_to_move": {"win": str(RECORDS), "draw": "0", "loss": "0"},
            **histograms,
            "network_buckets": [str(RECORDS)] + ["0"] * 7,
        },
        "coverage_by_perspective": {
            "WHITE": _perspective_coverage(),
            "BLACK": _perspective_coverage(),
        },
        "record_events": {
            "en_passant_best_moves": "0",
            "promotion_best_moves": "0",
            "castling_best_moves": "0",
            "atomic960_records": "0",
        },
        "trajectory_events": {
            "en_passant_moves": "0",
            "promotion_moves": "0",
            "castling_moves": "0",
            "explosive_captures": "0",
            "stop_reasons": ["2"] + ["0"] * 8,
        },
        "deduplication": {
            "duplicate_raw_records": "0",
            "duplicate_feature_inputs": "0",
            "duplicate_split_groups": "0",
        },
    }


def _set_summary(observations: int, digest: str) -> dict[str, Any]:
    return {
        "observations": str(observations),
        "unique_keys": str(observations),
        "duplicate_observations": "0",
        "ordered_set_sha256": digest,
    }


def _audit(policy: dict[str, Any]) -> dict[str, Any]:
    train_group = {
        "raw_record_keys": _set_summary(RECORDS, "1" * 64),
        "feature_input_keys": _set_summary(RECORDS, "2" * 64),
        "split_group_ids": _set_summary(2, "3" * 64),
    }
    validation_group = {
        "raw_record_keys": _set_summary(RECORDS, "4" * 64),
        "feature_input_keys": _set_summary(RECORDS, "5" * 64),
        "split_group_ids": _set_summary(2, "6" * 64),
    }
    verification_names = (
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
    )
    return {
        "schema_version": 1,
        "artifacts": {
            "train_stats": _artifact("train-stats.json"),
            "validation_stats": _artifact("validation-stats.json"),
            "coverage_policy": _artifact("policy.json"),
        },
        "partition": copy.deepcopy(policy["partition"]),
        "auditor": {
            "commit": "a" * 40,
            "artifact_sha256": "b" * 64,
            "algorithm_version": "atomic-v3-split-audit-v1",
            "set_algorithm": (
                "bounded chunks of raw 32-byte keys, unsigned-byte lexicographic sort, "
                "per-chunk dedup, k-way merge, exact duplicate/intersection counts, "
                "domain-separated ordered-set SHA-256"
            ),
        },
        "identity_definitions": {
            "raw_record_key": (
                "SHA256(ascii('atomic-record-key-v1\\0') || raw-atomic-bin-v2-record-64)"
            ),
            "feature_input_key": VALIDATOR.FEATURE_INPUT_KEY_FORMULA,
            "split_group_id": (
                "the exact label-free 32-byte split_group_id authenticated by "
                "atomic-trajectory-ledger-v1"
            ),
            "ordered_set_digest": (
                "SHA256(ascii('atomic-ordered-set-v1\\0') || key-count-u64-le || "
                "strictly-increasing-unique-keys-32)"
            ),
        },
        "sets": {"train": train_group, "validation": validation_group},
        "intersections": {
            "raw_record_keys": "0",
            "feature_input_keys": "0",
            "split_group_ids": "0",
        },
        "verification": {name: True for name in verification_names},
    }


def _bundle() -> dict[str, dict[str, Any]]:
    policy = _policy()
    return {
        "policy": policy,
        "train": _stats("train", policy),
        "validation": _stats("validation", policy),
        "audit": _audit(policy),
        "feature": _load("atomic-nnue-v3.json"),
        "index": _load("atomic-v3-index-coverage-v1.json"),
        "ledger": _load("atomic-trajectory-ledger-v1.json"),
    }


def _validate(bundle: dict[str, dict[str, Any]]) -> list[str]:
    return VALIDATOR.validate_bundle(
        bundle["policy"],
        bundle["train"],
        bundle["validation"],
        bundle["audit"],
        bundle["feature"],
        bundle["index"],
        bundle["ledger"],
    )


def _training_run() -> dict[str, Any]:
    inputs: dict[str, Any] = {
        name: _run_artifact(name + ".json", format(index + 1, "064x"))
        for index, name in enumerate(VALIDATOR.INPUT_ARTIFACT_PATHS[:8])
    }
    cursor = 9
    for role in ("train", "validation"):
        inputs[role] = {}
        for name in ("manifest", "trajectory_ledger", "index_coverage", "statistics"):
            inputs[role][name] = _run_artifact(
                role + "-" + name, format(cursor, "064x")
            )
            cursor += 1
    inputs["coverage_policy"] = _run_artifact("policy.json", format(cursor, "064x"))
    cursor += 1
    inputs["split_audit"] = _run_artifact("audit.json", format(cursor, "064x"))
    manifest: dict[str, Any] = {
        "schema_version": 1,
        "run_id": "atomic-v3-test-run",
        "status": "completed",
        "run_definition_sha256": "0" * 64,
        "input_bundle_sha256": "0" * 64,
        "inputs": inputs,
        "trainer": {
            "commit": "a" * 40,
            "artifact_sha256": "b" * 64,
            "dependency_lock_sha256": "c" * 64,
            "config": _run_artifact("trainer-config.json", "d" * 64),
            "training_seed": "17",
            "deterministic_algorithms": True,
            "device_description": "test-cpu",
        },
        "schedule": {
            "batch_size": 16384,
            "optimizer_steps": "1000",
            "epochs": 10,
            "validation_interval_steps": "100",
        },
        "outputs": {
            name: _run_artifact(name + ".bin", format(100 + index, "064x"))
            for index, name in enumerate(
                ("checkpoint", "network", "training_log", "metrics")
            )
        },
        "verification": {
            name: True
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
            )
        },
    }
    manifest["run_definition_sha256"] = VALIDATOR.compute_run_definition_sha256(
        manifest
    )
    manifest["input_bundle_sha256"] = VALIDATOR.compute_input_bundle_sha256(manifest)
    return manifest


def test_schema_valid_positive_bundle_passes_contract_validation() -> None:
    bundle = _bundle()
    schema_instances = (
        ("atomic-v3-coverage-policy-v1.json", bundle["policy"]),
        ("atomic-v3-dataset-stats-v1.json", bundle["train"]),
        ("atomic-v3-dataset-stats-v1.json", bundle["validation"]),
        ("atomic-v3-split-audit-v1.json", bundle["audit"]),
    )
    for schema_name, instance in schema_instances:
        Draft202012Validator(_load(schema_name)).validate(instance)
    assert _validate(bundle) == []


def test_training_run_hashes_are_recomputed_and_uint64_is_bounded() -> None:
    manifest = _training_run()
    Draft202012Validator(_load("atomic-v3-training-run-manifest-v1.json")).validate(
        manifest
    )
    assert VALIDATOR.validate_training_run_manifest(manifest) == []

    bad_seed = copy.deepcopy(manifest)
    bad_seed["trainer"]["training_seed"] = "18"
    assert any(
        "does not authenticate trainer" in error
        for error in VALIDATOR.validate_training_run_manifest(bad_seed)
    )

    bad_input = copy.deepcopy(manifest)
    bad_input["inputs"]["train"]["manifest"]["sha256"] = "e" * 64
    assert any(
        "does not authenticate all 18 inputs" in error
        for error in VALIDATOR.validate_training_run_manifest(bad_input)
    )

    overflow = copy.deepcopy(manifest)
    overflow["schedule"]["optimizer_steps"] = str(1 << 64)
    assert any(
        "exceeds UINT64_MAX" in error
        for error in VALIDATOR.validate_training_run_manifest(overflow)
    )


def test_training_run_artifact_bytes_are_opened_and_authenticated(tmp_path: Path) -> None:
    manifest = _training_run()
    descriptors: list[tuple[str, dict[str, Any]]] = []
    for dotted_path in VALIDATOR.INPUT_ARTIFACT_PATHS:
        descriptor: Any = manifest["inputs"]
        for component in dotted_path.split("."):
            descriptor = descriptor[component]
        descriptors.append(("inputs." + dotted_path, descriptor))
    descriptors.append(("trainer.config", manifest["trainer"]["config"]))
    descriptors.extend(
        ("outputs." + name, manifest["outputs"][name])
        for name in ("checkpoint", "network", "training_log", "metrics")
    )

    expected_sha256: dict[str, str] = {}
    for dotted_path, descriptor in descriptors:
        payload = ("artifact:" + dotted_path).encode("ascii")
        artifact_path = tmp_path / descriptor["file"]
        artifact_path.write_bytes(payload)
        digest = hashlib.sha256(payload).hexdigest()
        descriptor.update({"bytes": str(len(payload)), "sha256": digest})
        expected_sha256[dotted_path] = digest

    manifest["run_definition_sha256"] = VALIDATOR.compute_run_definition_sha256(
        manifest
    )
    manifest["input_bundle_sha256"] = VALIDATOR.compute_input_bundle_sha256(manifest)
    assert VALIDATOR.validate_training_run_manifest(manifest) == []
    manifest_path = tmp_path / "training-run.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    assert (
        VALIDATOR.authenticate_training_run_artifacts(
            manifest, manifest_path, {}, expected_sha256
        )
        == []
    )

    first_path, first_descriptor = descriptors[0]
    artifact_path = tmp_path / first_descriptor["file"]
    original = artifact_path.read_bytes()
    artifact_path.write_bytes(bytes((original[0] ^ 1,)) + original[1:])
    errors = VALIDATOR.authenticate_training_run_artifacts(
        manifest, manifest_path, {}, expected_sha256
    )
    assert any(
        "does not authenticate exact artifact bytes" in error
        and first_path in error
        for error in errors
    ), errors


def test_frozen_sidecars_require_canonical_schema_order_and_whitespace() -> None:
    bundle = _bundle()
    instances = (
        (bundle["policy"], "atomic-v3-coverage-policy-v1.json", "policy"),
        (bundle["train"], "atomic-v3-dataset-stats-v1.json", "train_stats"),
        (bundle["audit"], "atomic-v3-split-audit-v1.json", "split_audit"),
        (
            _training_run(),
            "atomic-v3-training-run-manifest-v1.json",
            "training_run",
        ),
    )
    for instance, schema_name, label in instances:
        schema = _load(schema_name)
        canonical = VALIDATOR._canonical_json_bytes(instance, schema)
        assert VALIDATOR._validate_canonical_json_bytes(
            instance,
            schema,
            len(canonical),
            hashlib.sha256(canonical).hexdigest(),
            label,
        ) == []

        whitespace = canonical[:-1] + b" \n"
        assert any(
            "not canonical" in error
            for error in VALIDATOR._validate_canonical_json_bytes(
                instance,
                schema,
                len(whitespace),
                hashlib.sha256(whitespace).hexdigest(),
                label,
            )
        )

        reordered = dict(reversed(tuple(instance.items())))
        reordered_payload = (
            json.dumps(
                reordered,
                ensure_ascii=False,
                allow_nan=False,
                separators=(",", ":"),
            )
            + "\n"
        ).encode("utf-8")
        assert reordered_payload != canonical
        assert any(
            "not canonical" in error
            for error in VALIDATOR._validate_canonical_json_bytes(
                instance,
                schema,
                len(reordered_payload),
                hashlib.sha256(reordered_payload).hexdigest(),
                label,
            )
        )


def test_json_loader_rejects_oversized_sidecar(tmp_path: Path) -> None:
    path = tmp_path / "oversized.json"
    with path.open("wb") as stream:
        stream.truncate(VALIDATOR.MAX_JSON_DOCUMENT_BYTES + 1)
    with pytest.raises(ValueError, match="exceeds the .* byte limit"):
        VALIDATOR.load_json_with_metadata(path)


def test_json_loader_rejects_symlink_sidecar(tmp_path: Path) -> None:
    target = tmp_path / "target.json"
    target.write_bytes(b"{}\n")
    link = tmp_path / "link.json"
    try:
        link.symlink_to(target)
    except (NotImplementedError, OSError) as exc:
        pytest.skip("symbolic links unavailable on this platform: {}".format(exc))
    with pytest.raises(ValueError, match="must not be a symbolic link"):
        VALIDATOR.load_json_with_metadata(link)


def test_json_loader_rejects_unstable_same_handle_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "unstable.json"
    path.write_bytes(b"{}\n")
    real_change_token = VALIDATOR._stat_change_token
    calls = 0

    def unstable_change_token(value: os.stat_result) -> tuple[int, int, int, int, int]:
        nonlocal calls
        calls += 1
        token = real_change_token(value)
        if calls == 2:
            return token[:-1] + (token[-1] + 1,)
        return token

    monkeypatch.setattr(VALIDATOR, "_stat_change_token", unstable_change_token)
    with pytest.raises(ValueError, match="changed while being parsed"):
        VALIDATOR.load_json_with_metadata(path)


Mutation = Callable[[dict[str, dict[str, Any]]], None]


def _set(path: tuple[str, ...], value: Any) -> Mutation:
    def mutate(bundle: dict[str, dict[str, Any]]) -> None:
        target: Any = bundle[path[0]]
        for component in path[1:-1]:
            target = target[int(component)] if isinstance(target, list) else target[component]
        if isinstance(target, list):
            target[int(path[-1])] = value
        else:
            target[path[-1]] = value

    return mutate


def _impossible_active_quantiles(bundle: dict[str, dict[str, Any]]) -> None:
    distribution = bundle["train"]["coverage_by_perspective"]["WHITE"][
        "atomic_capture_pair"
    ]["active_per_position"]
    distribution["p99"] = 2
    distribution["maximum"] = 2


def _aggregate_maximum_below_slice(bundle: dict[str, dict[str, Any]]) -> None:
    bundle["train"]["scan"]["max_active_observed"]["WHITE"] = 0


def _aggregate_maximum_above_slice_sum(bundle: dict[str, dict[str, Any]]) -> None:
    bundle["train"]["scan"]["max_active_observed"]["WHITE"] = 5


@pytest.mark.parametrize(
    ("mutate", "message"),
    (
        (_set(("train", "scan", "records_scanned"), str(1 << 64)), "UINT64_MAX"),
        (
            _set(
                ("train", "artifacts", "trajectory_ledger", "bytes"), str(1 << 64)
            ),
            "UINT64_MAX",
        ),
        (
            _set(
                (
                    "policy",
                    "partition",
                    "provenance",
                    "generation_profile",
                    "generation_seed",
                ),
                "01",
            ),
            "canonical uint64",
        ),
        (_set(("train", "scan", "perspectives_scanned"), "3"), "2 * records_scanned"),
        (_set(("train", "distribution", "wdl_side_to_move", "win"), "1"), "counts must sum"),
        (
            _set(
                (
                    "train",
                    "coverage_by_perspective",
                    "WHITE",
                    "atomic_capture_pair",
                    "structurally_unreachable_indices",
                ),
                1,
            ),
            "reachable + unreachable",
        ),
        (_set(("policy", "histogram_boundaries", "ply"), [10, 0]), "strictly increasing"),
        (_set(("policy", "train", "minimum_records"), "0"), "must be non-zero"),
        (
            _set(("policy", "partition", "config_sha256"), "c" * 64),
            "canonical partition-config hash",
        ),
        (
            _set(
                (
                    "train",
                    "coverage_by_perspective",
                    "WHITE",
                    "atomic_blast_ring",
                    "observed_reachable_indices",
                ),
                0,
            ),
            "coverage ppm is below",
        ),
        (_set(("validation", "split", "split_seed"), "8"), "precommitted policy"),
        (
            _set(("validation", "split", "partition_config_sha256"), "c" * 64),
            "precommitted policy",
        ),
        (
            _set(("validation", "provenance", "engine_commit"), "d" * 40),
            "precommitted policy",
        ),
        (
            _set(("train", "distribution", "network_buckets"), ["0"] * 8),
            "must sum to records_scanned",
        ),
        (_set(("index", "segments",), []), "exactly 12 segments"),
        (
            _set(("index", "header", "fields"), []),
            "exactly 10 fields",
        ),
        (
            _set(("index", "header", "magic_hex"), "00" * 8),
            "must equal 4154434f56310000",
        ),
        (_set(("index", "file_policy", "file_size"), 1), "must equal 1632084"),
        (
            _set(("index", "reachability_masks", "storage"), "derived-not-stored"),
            "trailing-canonical-bitmaps",
        ),
        (
            _set(("index", "reachability_masks", "layout"), []),
            "exact twelve trailing structural bitmap ranges",
        ),
        (
            _set(("ledger", "header", "fields"), []),
            "exactly 14 fields",
        ),
        (
            _set(("ledger", "header", "magic_hex"), "00" * 8),
            "must equal 41545452414a3100",
        ),
        (
            _set(("ledger", "file_policy", "file_size_formula"), "160"),
            "trajectory_count * 112",
        ),
        (
            _set(("ledger", "partition", "formula"), "0"),
            "split_seed_u64_le",
        ),
        (_set(("audit", "intersections", "feature_input_keys"), "1"), "must equal zero"),
        (
            _set(
                (
                    "train",
                    "backend",
                    "reachability_masks",
                    "WHITE",
                    "atomic_capture_pair",
                ),
                "22" * 32,
            ),
            "twelve masks",
        ),
        (
            _set(("policy", "reachability_mask_sha256"), "0" * 64),
            "does not authenticate",
        ),
        (
            _set(("audit", "partition", "validation_threshold_u64"), "3"),
            "authenticated policy partition",
        ),
        (
            _set(
                ("audit", "identity_definitions", "feature_input_key"),
                VALIDATOR.FEATURE_INPUT_KEY_FORMULA.replace(
                    "bucket-u8)", "absolute-side-to-move-u8 || bucket-u8)"
                ),
            ),
            "color-agnostic",
        ),
        (
            _set(("audit", "verification", "same_reachability_masks"), False),
            "must be true",
        ),
        (
            _set(
                (
                    "audit",
                    "sets",
                    "validation",
                    "feature_input_keys",
                    "ordered_set_sha256",
                ),
                "2" * 64,
            ),
            "cannot have identical digests",
        ),
        (
            _set(
                (
                    "audit",
                    "sets",
                    "train",
                    "raw_record_keys",
                    "duplicate_observations",
                ),
                "1",
            ),
            "statistics deduplication count",
        ),
        (
            _set(
                (
                    "train",
                    "coverage_by_perspective",
                    "WHITE",
                    "capture_pair_classes",
                    "0",
                    "0",
                    "0",
                ),
                "0",
            ),
            "class counts must sum",
        ),
        (
            _set(("validation", "trajectory_events", "stop_reasons"), ["0"] * 9),
            "must sum to ledger_trajectory_count",
        ),
        (
            _set(("train", "record_events", "atomic960_records"), str(RECORDS + 1)),
            "cannot exceed records_scanned",
        ),
        (_impossible_active_quantiles, "impossible for the declared nearest-rank"),
        (_aggregate_maximum_below_slice, "at least every per-slice"),
        (_aggregate_maximum_above_slice_sum, "at most the sum of per-slice"),
        (
            _set(
                (
                    "train",
                    "coverage_by_perspective",
                    "WHITE",
                    "atomic_capture_pair",
                    "active_per_position",
                    "maximum",
                ),
                241,
            ),
            "must be <= 240",
        ),
    ),
)


def test_adversarial_mutations_are_rejected(mutate: Mutation, message: str) -> None:
    bundle = _bundle()
    mutate(bundle)
    errors = _validate(bundle)
    assert errors
    assert any(message in error for error in errors), errors


def _write_payload(path: Path, payload: bytes) -> tuple[int, str]:
    path.write_bytes(payload)
    return len(payload), hashlib.sha256(payload).hexdigest()


def _bind_artifact(
    artifact: dict[str, Any], path: Path, schema_path: Path, payload: bytes
) -> None:
    byte_count, digest = _write_payload(path, payload)
    artifact.update(
        {
            "file": path.name,
            "bytes": str(byte_count),
            "sha256": digest,
            "schema_sha256": hashlib.sha256(schema_path.read_bytes()).hexdigest(),
        }
    )


def _starting_position(fullmove: int = 1) -> bytes:
    pieces = (
        4,
        2,
        3,
        5,
        6,
        3,
        2,
        4,
        *([1] * 8),
        *([0] * 32),
        *([7] * 8),
        10,
        8,
        9,
        11,
        12,
        9,
        8,
        10,
    )
    assert len(pieces) == 64
    position = bytearray(48)
    for square, piece in enumerate(pieces):
        position[square // 2] |= piece << (4 * (square % 2))
    position[32] = 0
    position[33] = 0x0F
    position[34:38] = bytes((7, 0, 63, 56))
    position[38] = 0xFF
    struct.pack_into("<H", position, 40, 0)
    struct.pack_into("<I", position, 42, fullmove)
    return bytes(position)


def _overpopulated_position(white_count: int, black_count: int) -> bytes:
    assert white_count >= 1 and black_count >= 1
    assert white_count + black_count <= 64
    pieces = (
        [6]
        + [5] * (white_count - 1)
        + [12]
        + [11] * (black_count - 1)
        + [0] * (64 - white_count - black_count)
    )
    position = bytearray(48)
    for square, piece in enumerate(pieces):
        position[square // 2] |= piece << (4 * (square % 2))
    position[32] = 0
    position[33] = 0
    position[34:38] = b"\xff" * 4
    position[38] = 0xFF
    struct.pack_into("<H", position, 40, 0)
    struct.pack_into("<I", position, 42, 1)
    return bytes(position)


def _normal_move(from_square: int, to_square: int) -> int:
    assert from_square != to_square
    return from_square | (to_square << 6)


def _atomic_bin_v2_record(position: bytes, score: int, ply: int) -> bytes:
    assert len(position) == 48
    record = bytearray(64)
    record[:48] = position
    struct.pack_into("<i", record, 48, score)
    struct.pack_into("<I", record, 52, _normal_move(12, 28))  # e2-e4
    struct.pack_into("<I", record, 56, ply)
    struct.pack_into("<b", record, 60, 1)
    return bytes(record)


def _build_atomic_bin_v2_shard(role: str) -> bytes:
    cached = ATOMIC_BIN_V2_SHARD_CACHE.get(role)
    if cached is not None:
        return cached
    data_schema_sha256 = hashlib.sha256(
        (ROOT / "schemas" / "atomic-bin-v2.json").read_bytes()
    ).digest()
    header = struct.pack(
        "<8sHHIII32sQ32s",
        b"ATBINV2\0",
        2,
        96,
        0x01020304,
        64,
        0,
        data_schema_sha256,
        RECORDS,
        bytes(32),
    )
    records_per_trajectory = RECORDS // 2
    records = b"".join(
        _atomic_bin_v2_record(
            _starting_position(), 0 if role == "train" else 1, ply
        )
        for ply in range(records_per_trajectory)
    )
    second_records = b"".join(
        _atomic_bin_v2_record(
            _starting_position(2), 0 if role == "train" else 1, ply
        )
        for ply in range(records_per_trajectory)
    )
    payload = header + records + second_records
    ATOMIC_BIN_V2_SHARD_CACHE[role] = payload
    return payload


def _canonical_manifest_payload(manifest: dict[str, Any]) -> bytes:
    return (
        json.dumps(
            manifest,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")


def _canonical_sidecar_payload(value: dict[str, Any], schema_name: str) -> bytes:
    return VALIDATOR._canonical_json_bytes(value, _load(schema_name))


def _build_manifest(role: str, shard_path: Path, shard_payload: bytes) -> bytes:
    schema_root = ROOT / "schemas"
    manifest_schema = schema_root / "atomic-bin-v2-manifest.json"
    data_schema = schema_root / "atomic-bin-v2.json"
    manifest = {
        "manifest_version": 1,
        "manifest_schema_sha256": hashlib.sha256(
            manifest_schema.read_bytes()
        ).hexdigest(),
        "data_schema_sha256": hashlib.sha256(data_schema.read_bytes()).hexdigest(),
        "format": "atomic-bin-v2",
        "engine": {"commit": "1" * 40, "version": "Atomic-Stockfish V3 test"},
        "network": {"file": "atomic.nnue", "sha256": "4" * 64},
        "book": {"kind": "builtin-startpos", "file": None, "sha256": None},
        "generation": _manifest_generation(),
        "statistics": {"records": str(RECORDS), "draws": "0"},
        "shards": [
            {
                "index": 0,
                "file": shard_path.name,
                "records": str(RECORDS),
                "bytes": str(len(shard_payload)),
                "sha256": hashlib.sha256(shard_payload).hexdigest(),
            }
        ],
    }
    Draft202012Validator(_load("atomic-bin-v2-manifest.json")).validate(manifest)
    return _canonical_manifest_payload(manifest)


def test_manifest_summary_provenance_uses_authenticated_snapshot_without_reopen(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    shard_path = tmp_path / "train.atbin"
    shard_payload = _build_atomic_bin_v2_shard("train")
    manifest_path = Path(str(shard_path) + ".manifest.json")
    manifest_payload = _build_manifest("train", shard_path, shard_payload)
    manifest_path.write_bytes(manifest_payload)
    schema = _load("atomic-bin-v2-manifest.json")
    schema_digest = hashlib.sha256(
        (ROOT / "schemas" / "atomic-bin-v2-manifest.json").read_bytes()
    ).hexdigest()
    data_schema_digest = hashlib.sha256(
        (ROOT / "schemas" / "atomic-bin-v2.json").read_bytes()
    ).hexdigest()
    summary, errors = VALIDATOR.parse_atomic_bin_v2_manifest(
        "train",
        manifest_path,
        {
            "bytes": str(len(manifest_payload)),
            "sha256": hashlib.sha256(manifest_payload).hexdigest(),
        },
        schema,
        schema_digest,
        data_schema_digest,
    )
    assert errors == []
    assert summary is not None

    def unexpected_reopen(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("authenticated manifest must not be reopened")

    monkeypatch.setattr(VALIDATOR, "_read_bounded_json", unexpected_reopen)
    assert VALIDATOR._validate_manifest_provenance(
        "train", summary, {}, _policy()
    ) == []


def _split_group_id(
    contract: dict[str, Any], root_position: bytes, move_wires: tuple[int, ...]
) -> bytes:
    domain = bytes.fromhex(contract["split_group_id"]["domain_ascii_hex"])
    return hashlib.sha256(
        domain
        + root_position
        + b"\0"
        + len(move_wires).to_bytes(8, "little")
        + b"".join(move.to_bytes(4, "little") for move in move_wires)
    ).digest()


def _partition_is_validation(
    contract: dict[str, Any], policy: dict[str, Any], split_group_id: bytes
) -> bool:
    partition = policy["partition"]
    digest = hashlib.sha256(
        bytes.fromhex(contract["partition"]["domain_ascii_hex"])
        + int(partition["split_seed"]).to_bytes(8, "little")
        + split_group_id
    ).digest()
    return int.from_bytes(digest[:8], "little") < int(
        partition["validation_threshold_u64"]
    )


def _role_trajectory(
    role: str,
    contract: dict[str, Any],
    policy: dict[str, Any],
    move_count: int,
    start: int = 1,
) -> tuple[bytes, bytes, tuple[int, ...]]:
    partition = policy["partition"]
    key = (
        role,
        move_count,
        start,
        int(partition["split_seed"]),
        int(partition["validation_threshold_u64"]),
        contract["split_group_id"]["domain_ascii_hex"],
        contract["partition"]["domain_ascii_hex"],
    )
    cached = ROLE_TRAJECTORY_CACHE.get(key)
    if cached is not None:
        return cached
    moves = (_normal_move(12, 28),) * move_count
    for fullmove in range(start, 100_001):
        root = _starting_position(fullmove)
        split_group_id = _split_group_id(contract, root, moves)
        if _partition_is_validation(contract, policy, split_group_id) == (
            role == "validation"
        ):
            result = (root, split_group_id, moves)
            ROLE_TRAJECTORY_CACHE[key] = result
            return result
    raise AssertionError("could not find a trajectory for role " + role)


def _build_ledger(
    role: str,
    contract: dict[str, Any],
    policy: dict[str, Any],
    manifest_sha256: str,
) -> tuple[bytes, tuple[bytes, bytes]]:
    record_counts = (RECORDS // 2, RECORDS - RECORDS // 2)
    first = _role_trajectory(role, contract, policy, record_counts[0])
    second = _role_trajectory(
        role,
        contract,
        policy,
        record_counts[1],
        struct.unpack_from("<I", first[0], 42)[0] + 1,
    )
    trajectories = (first, second)
    moves_offset = 160 + len(trajectories) * 112
    schema_sha256 = hashlib.sha256(
        (ROOT / "schemas" / "atomic-trajectory-ledger-v1.json").read_bytes()
    ).digest()
    data_schema_sha256 = hashlib.sha256(
        (ROOT / "schemas" / "atomic-bin-v2.json").read_bytes()
    ).digest()
    header = struct.pack(
        "<8sHHIII32s32s32sQQQQQ",
        b"ATTRAJ1\0",
        1,
        160,
        0x01020304,
        112,
        {"train": 0, "validation": 1}[role],
        schema_sha256,
        bytes.fromhex(manifest_sha256),
        data_schema_sha256,
        RECORDS,
        len(trajectories),
        sum(len(item[2]) for item in trajectories),
        160,
        moves_offset,
    )
    entries = bytearray()
    first_record = 0
    first_move = 0
    for (root, split_group_id, moves), record_count in zip(
        trajectories, record_counts
    ):
        entries.extend(
            struct.pack(
                "<32s48sQIIQbBB5s",
                split_group_id,
                root,
                first_record,
                record_count,
                len(moves),
                first_move,
                1,
                0,
                0,
                bytes(5),
            )
        )
        first_record += record_count
        first_move += len(moves)
    move_stream = b"".join(
        struct.pack("<I", move)
        for _, _, moves in trajectories
        for move in moves
    )
    payload = header + bytes(entries) + move_stream
    assert len(payload) == 160 + 2 * 112 + RECORDS * 4
    return payload, (first[1], second[1])


def _ordered_set_sha256(keys: tuple[bytes, ...]) -> str:
    unique = tuple(sorted(set(keys)))
    return hashlib.sha256(
        b"atomic-ordered-set-v1\0"
        + len(unique).to_bytes(8, "little")
        + b"".join(unique)
    ).hexdigest()


def _raw_record_set_sha256(shard: bytes) -> str:
    keys = tuple(
        hashlib.sha256(b"atomic-record-key-v1\0" + shard[offset : offset + 64]).digest()
        for offset in range(96, len(shard), 64)
    )
    assert len(keys) == RECORDS
    assert len(set(keys)) == RECORDS
    return _ordered_set_sha256(keys)


def _hm_training_and_virtual_counters(
    physical: tuple[int, ...]
) -> tuple[tuple[int, ...], tuple[int, ...]]:
    assert len(physical) == 32 * 11 * 64
    training = [0] * (32 * 12 * 64)
    for bucket in range(32):
        own_king_square = (7 - bucket // 4) * 8 + (7 - bucket % 4)
        for physical_plane in range(11):
            for square in range(64):
                physical_index = bucket * 704 + physical_plane * 64 + square
                training_plane = (
                    physical_plane
                    if physical_plane < 10
                    else (10 if square == own_king_square else 11)
                )
                training_index = bucket * 768 + training_plane * 64 + square
                training[training_index] += physical[physical_index]

    virtual = [0] * (12 * 64)
    for bucket in range(32):
        for plane in range(12):
            for square in range(64):
                training_index = bucket * 768 + plane * 64 + square
                virtual[plane * 64 + square] += training[training_index]
    assert sum(value != 0 for value in training) == 22_528
    assert sum(value != 0 for value in virtual) == 736
    return tuple(training), tuple(virtual)


def _build_atcov(
    role: str,
    contract: dict[str, Any],
    index_schema_sha256: str,
    manifest_sha256: str,
) -> bytes:
    file_size = contract["file_policy"]["file_size"]
    payload = bytearray(file_size)
    role_id = {"train": 0, "validation": 1}[role]
    struct.pack_into(
        "<8sHHIII",
        payload,
        0,
        b"ATCOV1\0\0",
        1,
        128,
        0x01020304,
        8,
        role_id,
    )
    payload[24:56] = bytes.fromhex(index_schema_sha256)
    payload[56:88] = bytes.fromhex(FEATURE_SCHEMA_SHA)
    payload[88:120] = bytes.fromhex(manifest_sha256)
    counter_count = contract["header"]["fields"][-1]["required_value"]
    struct.pack_into("<Q", payload, 120, counter_count)

    physical_counts: dict[tuple[str, str], int] = {}
    hm_derived: dict[str, tuple[tuple[int, ...], tuple[int, ...]]] = {}
    for segment in contract["segments"]:
        count = segment["count"]
        begin = 128 + segment["offset"] * 8
        if segment["kind"] == "physical":
            assert count <= RECORDS
            first = RECORDS - count + 1
            counters = (first,) + (1,) * (count - 1)
            counter_bytes = b"".join(struct.pack("<Q", value) for value in counters)
            physical_counts[(segment["perspective"], segment["slice"])] = count
            if segment["slice"] == "half-ka-v2-atomic-hm":
                hm_derived[segment["perspective"]] = _hm_training_and_virtual_counters(
                    counters
                )
        elif segment["kind"] == "training":
            counters = hm_derived[segment["perspective"]][0]
            assert len(counters) == count
            counter_bytes = b"".join(struct.pack("<Q", value) for value in counters)
        else:
            counters = hm_derived[segment["perspective"]][1]
            assert len(counters) == count
            counter_bytes = b"".join(struct.pack("<Q", value) for value in counters)
        payload[begin : begin + count * 8] = counter_bytes

    perspective_ids = contract["reachability_masks"]["perspective_id"]
    kind_ids = contract["reachability_masks"]["kind_id"]
    slice_ids = contract["reachability_masks"]["slice_id"]
    for layout in contract["reachability_masks"]["layout"]:
        perspective = layout["perspective"]
        slice_name = layout["slice"]
        kind = layout["kind"]
        segment = next(
            item
            for item in contract["segments"]
            if item["perspective"] == perspective
            and item["kind"] == kind
            and item["slice"] == slice_name
        )
        count = segment["count"]
        if kind == "physical":
            mask = PHYSICAL_MASKS[slice_ids[slice_name]]
            field = slice_name.replace("-", "_")
        elif kind == "training":
            mask = HM_TRAINING_MASK
            field = "hm_training"
        else:
            mask = HM_VIRTUAL_MASK
            field = "hm_virtual_factors"
        assert len(mask) == layout["bytes"]
        assert (
            _structural_mask_digest_bytes(
                perspective_ids[perspective],
                kind_ids[kind],
                slice_ids[slice_name],
                count,
                mask,
            )
            == MASKS[perspective][field]
        )
        begin = layout["offset"]
        payload[begin : begin + len(mask)] = mask

    assert len(payload) == file_size
    return bytes(payload)


def _serialize_cli_sidecars(
    bundle: dict[str, dict[str, Any]], paths: dict[str, Path]
) -> None:
    schema_root = ROOT / "schemas"
    policy_schema = schema_root / "atomic-v3-coverage-policy-v1.json"
    stats_schema = schema_root / "atomic-v3-dataset-stats-v1.json"

    policy_payload = _canonical_sidecar_payload(
        bundle["policy"], "atomic-v3-coverage-policy-v1.json"
    )
    policy_bytes, policy_digest = _write_payload(paths["policy"], policy_payload)
    policy_artifact = {
        "file": paths["policy"].name,
        "bytes": str(policy_bytes),
        "sha256": policy_digest,
        "schema_sha256": hashlib.sha256(policy_schema.read_bytes()).hexdigest(),
    }
    for role in ("train", "validation"):
        bundle[role]["artifacts"]["coverage_policy"].update(policy_artifact)
        _write_payload(
            paths[role],
            _canonical_sidecar_payload(
                bundle[role], "atomic-v3-dataset-stats-v1.json"
            ),
        )

    for name, role in (("train_stats", "train"), ("validation_stats", "validation")):
        payload = paths[role].read_bytes()
        bundle["audit"]["artifacts"][name].update(
            {
                "file": paths[role].name,
                "bytes": str(len(payload)),
                "sha256": hashlib.sha256(payload).hexdigest(),
                "schema_sha256": hashlib.sha256(stats_schema.read_bytes()).hexdigest(),
            }
        )
    bundle["audit"]["artifacts"]["coverage_policy"].update(policy_artifact)
    _write_payload(
        paths["audit"],
        _canonical_sidecar_payload(
            bundle["audit"], "atomic-v3-split-audit-v1.json"
        ),
    )


def _materialize_cli_bundle(
    tmp_path: Path,
) -> tuple[dict[str, dict[str, Any]], dict[str, Path], list[str]]:
    bundle = _bundle()
    paths = {
        "policy": tmp_path / "policy.json",
        "train": tmp_path / "train-stats.json",
        "validation": tmp_path / "validation-stats.json",
        "audit": tmp_path / "split-audit.json",
        "train_manifest": tmp_path / "train.atbin.manifest.json",
        "validation_manifest": tmp_path / "validation.atbin.manifest.json",
        "train_ledger": tmp_path / "train.attraj",
        "validation_ledger": tmp_path / "validation.attraj",
        "train_atcov": tmp_path / "train.atcov",
        "validation_atcov": tmp_path / "validation.atcov",
        "train_shard": tmp_path / "train.atbin",
        "validation_shard": tmp_path / "validation.atbin",
    }
    schema_root = ROOT / "schemas"
    manifest_schema = schema_root / "atomic-bin-v2-manifest.json"
    ledger_schema = schema_root / "atomic-trajectory-ledger-v1.json"
    index_schema = schema_root / "atomic-v3-index-coverage-v1.json"
    index_schema_sha256 = hashlib.sha256(index_schema.read_bytes()).hexdigest()

    for role in ("train", "validation"):
        artifacts = bundle[role]["artifacts"]
        shard_payload = _build_atomic_bin_v2_shard(role)
        _write_payload(paths[role + "_shard"], shard_payload)
        bundle["audit"]["sets"][role]["raw_record_keys"][
            "ordered_set_sha256"
        ] = _raw_record_set_sha256(shard_payload)
        _bind_artifact(
            artifacts["atomic_bin_v2_manifest"],
            paths[role + "_manifest"],
            manifest_schema,
            _build_manifest(role, paths[role + "_shard"], shard_payload),
        )
        ledger_payload, split_group_ids = _build_ledger(
            role,
            bundle["ledger"],
            bundle["policy"],
            artifacts["atomic_bin_v2_manifest"]["sha256"],
        )
        _bind_artifact(
            artifacts["trajectory_ledger"],
            paths[role + "_ledger"],
            ledger_schema,
            ledger_payload,
        )
        split_groups = bundle["audit"]["sets"][role]["split_group_ids"]
        split_groups["ordered_set_sha256"] = _ordered_set_sha256(split_group_ids)
        _bind_artifact(
            artifacts["index_coverage"],
            paths[role + "_atcov"],
            index_schema,
            _build_atcov(
                role,
                bundle["index"],
                index_schema_sha256,
                artifacts["atomic_bin_v2_manifest"]["sha256"],
            ),
        )

    _serialize_cli_sidecars(bundle, paths)
    command = [
        sys.executable,
        str(TOOL_PATH),
        "--policy",
        str(paths["policy"]),
        "--train-stats",
        str(paths["train"]),
        "--validation-stats",
        str(paths["validation"]),
        "--split-audit",
        str(paths["audit"]),
        "--json",
    ]
    return bundle, paths, command


def _replace_atcov(
    bundle: dict[str, dict[str, Any]],
    paths: dict[str, Path],
    role: str,
    payload: bytes | bytearray,
) -> None:
    byte_count, digest = _write_payload(paths[role + "_atcov"], bytes(payload))
    bundle[role]["artifacts"]["index_coverage"].update(
        {"bytes": str(byte_count), "sha256": digest}
    )


def _replace_ledger(
    bundle: dict[str, dict[str, Any]],
    paths: dict[str, Path],
    role: str,
    payload: bytes | bytearray,
) -> None:
    byte_count, digest = _write_payload(paths[role + "_ledger"], bytes(payload))
    bundle[role]["artifacts"]["trajectory_ledger"].update(
        {"bytes": str(byte_count), "sha256": digest}
    )


def _replace_manifest_and_bindings(
    bundle: dict[str, dict[str, Any]],
    paths: dict[str, Path],
    role: str,
    payload: bytes,
) -> None:
    byte_count, digest = _write_payload(paths[role + "_manifest"], payload)
    bundle[role]["artifacts"]["atomic_bin_v2_manifest"].update(
        {"bytes": str(byte_count), "sha256": digest}
    )

    ledger = bytearray(paths[role + "_ledger"].read_bytes())
    ledger[56:88] = bytes.fromhex(digest)
    _replace_ledger(bundle, paths, role, ledger)
    atcov = bytearray(paths[role + "_atcov"].read_bytes())
    atcov[88:120] = bytes.fromhex(digest)
    _replace_atcov(bundle, paths, role, atcov)


def _rebind_manifest_shard(
    bundle: dict[str, dict[str, Any]], paths: dict[str, Path], role: str
) -> None:
    shard_payload = paths[role + "_shard"].read_bytes()
    manifest = json.loads(paths[role + "_manifest"].read_text(encoding="utf-8"))
    manifest["shards"][0].update(
        {
            "bytes": str(len(shard_payload)),
            "sha256": hashlib.sha256(shard_payload).hexdigest(),
        }
    )
    _replace_manifest_and_bindings(
        bundle, paths, role, _canonical_manifest_payload(manifest)
    )


def _recompute_declared_mask_aggregate(bundle: dict[str, dict[str, Any]]) -> None:
    aggregate = hashlib.sha256(
        VALIDATOR.MASK_AGGREGATE_DOMAIN
        + b"".join(
            bytes.fromhex(bundle["policy"]["reachability_masks"][perspective][field])
            for perspective in ("WHITE", "BLACK")
            for field in VALIDATOR.MASK_FIELD_IDS
        )
    ).hexdigest()
    bundle["policy"]["reachability_mask_sha256"] = aggregate
    for role in ("train", "validation"):
        bundle[role]["backend"]["reachability_mask_sha256"] = aggregate


def test_generation_profile_hash_excludes_only_volume_layout_fields() -> None:
    train = _manifest_generation()
    validation = copy.deepcopy(train)
    validation["options"].update(
        {
            "requested_records": "25000",
            "records_per_shard": "5000",
            "random_file_name": True,
        }
    )
    train_digest = VALIDATOR.compute_atomic_bin_v2_generation_sha256(train)
    assert VALIDATOR.compute_atomic_bin_v2_generation_sha256(validation) == train_digest

    changed_atomic960 = copy.deepcopy(validation)
    changed_atomic960["atomic960"] = True
    assert (
        VALIDATOR.compute_atomic_bin_v2_generation_sha256(changed_atomic960)
        != train_digest
    )

    changed_search = copy.deepcopy(validation)
    changed_search["options"]["search_depth_min"] = 2
    assert (
        VALIDATOR.compute_atomic_bin_v2_generation_sha256(changed_search)
        != train_digest
    )


@pytest.mark.parametrize(
    "field", ("adjudicate_draws_by_score", "adjudicate_resignations")
)
def test_release_candidate_rejects_positive_adjudication_attestations(field: str) -> None:
    bundle = _bundle()
    bundle["policy"]["partition"]["provenance"]["generation_profile"][field] = True
    errors = _validate(bundle)
    assert any(
        field in error and "release-candidate generation attestation must be false" in error
        for error in errors
    ), errors


def test_cli_is_fail_closed_and_machine_readable(tmp_path: Path) -> None:
    bundle, paths, command = _materialize_cli_bundle(tmp_path)
    passed = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, check=False)
    assert passed.returncode == 0, passed.stdout + passed.stderr
    passed_payload = json.loads(passed.stdout)
    assert passed_payload["errors"] == []
    assert passed_payload["ok"] is True
    assert passed_payload["structural_ok"] is True
    assert passed_payload["publication_ready"] is False
    assert passed_payload["status"] == "structural-pass"
    assert len(passed_payload["pending_publication_gates"]) == 4
    assert any(
        "independent-oracle reproduction" in gate
        for gate in passed_payload["pending_publication_gates"]
    )

    publication = subprocess.run(
        command[:-1] + ["--require-publication-ready", "--json"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    publication_payload = json.loads(publication.stdout)
    assert publication.returncode == 1
    assert publication_payload["structural_ok"] is True
    assert publication_payload["publication_ready"] is False
    assert publication_payload["status"] == "publication-gate-fail"
    assert any(
        "producer resignation-policy evidence" in error
        for error in publication_payload["errors"]
    )
    assert any(
        "physical-mask oracle reproduction" in error
        for error in publication_payload["errors"]
    )

    bundle["audit"]["intersections"]["raw_record_keys"] = "1"
    paths["audit"].write_bytes(
        _canonical_sidecar_payload(
            bundle["audit"], "atomic-v3-split-audit-v1.json"
        )
    )
    failed = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, check=False)
    payload = json.loads(failed.stdout)
    assert failed.returncode == 1
    assert payload["ok"] is False
    assert any(
        "must equal zero" in error or "'0' was expected" in error
        for error in payload["errors"]
    )

    bundle["audit"]["intersections"]["raw_record_keys"] = "0"
    bundle["policy"]["unexpected"] = True
    paths["audit"].write_bytes(
        _canonical_sidecar_payload(
            bundle["audit"], "atomic-v3-split-audit-v1.json"
        )
    )
    paths["policy"].write_text(json.dumps(bundle["policy"]), encoding="utf-8")
    malformed = subprocess.run(
        command, cwd=ROOT, capture_output=True, text=True, check=False
    )
    payload = json.loads(malformed.stdout)
    assert malformed.returncode == 1
    assert payload["ok"] is False
    assert any("JSON Schema" in error for error in payload["errors"])


@pytest.mark.parametrize("mutation", ("whitespace", "top_level_order"))
def test_cli_rejects_noncanonical_policy_wire(tmp_path: Path, mutation: str) -> None:
    _, paths, command = _materialize_cli_bundle(tmp_path)
    policy = json.loads(paths["policy"].read_text(encoding="utf-8"))
    if mutation == "whitespace":
        payload = paths["policy"].read_bytes()[:-1] + b" \n"
    else:
        payload = (
            json.dumps(
                dict(reversed(tuple(policy.items()))),
                ensure_ascii=False,
                allow_nan=False,
                separators=(",", ":"),
            )
            + "\n"
        ).encode("utf-8")
    paths["policy"].write_bytes(payload)
    failed = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, check=False)
    result = json.loads(failed.stdout)
    assert failed.returncode == 1
    assert any(
        error == "policy: JSON is not canonical in schema declaration order"
        for error in result["errors"]
    ), result["errors"]


@pytest.mark.parametrize(
    ("mutation", "expected_error"),
    (
        ("atomic960", "normalized semantic generation profiles must be byte-identical"),
        ("search_depth", "normalized semantic generation profiles must be byte-identical"),
        (
            "score_draw_adjudication",
            "release-candidate generation must disable score-draw adjudication",
        ),
    ),
)
def test_cli_rejects_manifest_generation_drift(
    tmp_path: Path, mutation: str, expected_error: str
) -> None:
    bundle, paths, command = _materialize_cli_bundle(tmp_path)
    manifest = json.loads(paths["validation_manifest"].read_text(encoding="utf-8"))
    if mutation == "atomic960":
        manifest["generation"]["atomic960"] = True
    elif mutation == "search_depth":
        manifest["generation"]["options"]["search_depth_min"] = 2
    elif mutation == "score_draw_adjudication":
        manifest["generation"]["options"]["adjudicate_draws_by_score"] = True
    else:  # pragma: no cover
        raise AssertionError("unknown mutation " + mutation)
    _replace_manifest_and_bindings(
        bundle, paths, "validation", _canonical_manifest_payload(manifest)
    )
    _serialize_cli_sidecars(bundle, paths)
    failed = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, check=False)
    payload = json.loads(failed.stdout)
    assert failed.returncode == 1
    assert any(expected_error in error for error in payload["errors"]), payload["errors"]


@pytest.mark.parametrize(
    ("mutation", "expected_error"),
    (
        ("magic", "does not equal contract magic"),
        ("header_size", "header.header_size: must equal 128"),
        ("role", "header.role: must equal 0"),
        ("counter_under_zero_mask", "nonzero counters have a structural zero bit"),
        ("bitmap_hash", "exact bytes do not match the declared mask hash"),
        ("occurrence", "occurrence_count: declared value does not match binary"),
        (
            "hm_training_derivation",
            "counters must be derived exactly from physical HM counters",
        ),
        (
            "hm_virtual_derivation",
            "counters must be derived exactly from physical HM counters",
        ),
        (
            "hm_training_bitmap_projection",
            "reachability bitmap must be derived exactly from physical HM mask",
        ),
        (
            "hm_virtual_bitmap_projection",
            "reachability bitmap must be derived exactly from physical HM mask",
        ),
    ),
)
def test_cli_rejects_authenticated_atcov_mutations(
    tmp_path: Path, mutation: str, expected_error: str
) -> None:
    bundle, paths, command = _materialize_cli_bundle(tmp_path)
    train = bytearray(paths["train_atcov"].read_bytes())
    first_layout = bundle["index"]["reachability_masks"]["layout"][0]

    if mutation == "magic":
        train[0] ^= 0x01
        _replace_atcov(bundle, paths, "train", train)
    elif mutation == "header_size":
        struct.pack_into("<H", train, 10, 127)
        _replace_atcov(bundle, paths, "train", train)
    elif mutation == "role":
        struct.pack_into("<I", train, 20, 1)
        _replace_atcov(bundle, paths, "train", train)
    elif mutation == "bitmap_hash":
        train[first_layout["offset"]] &= 0xFE
        _replace_atcov(bundle, paths, "train", train)
    elif mutation == "occurrence":
        first_counter = struct.unpack_from("<Q", train, 128)[0]
        struct.pack_into("<Q", train, 128, first_counter + 1)
        _replace_atcov(bundle, paths, "train", train)
    elif mutation in ("hm_training_derivation", "hm_virtual_derivation"):
        kind = "training" if mutation == "hm_training_derivation" else "virtual-factor"
        segment = next(
            item
            for item in bundle["index"]["segments"]
            if item["perspective"] == "WHITE" and item["kind"] == kind
        )
        counter_offset = 128 + segment["offset"] * 8
        counter = struct.unpack_from("<Q", train, counter_offset)[0]
        struct.pack_into("<Q", train, counter_offset, counter + 1)
        _replace_atcov(bundle, paths, "train", train)
    elif mutation in (
        "hm_training_bitmap_projection",
        "hm_virtual_bitmap_projection",
    ):
        kind = (
            "training"
            if mutation == "hm_training_bitmap_projection"
            else "virtual-factor"
        )
        field = "hm_training" if kind == "training" else "hm_virtual_factors"
        layout = next(
            item
            for item in bundle["index"]["reachability_masks"]["layout"]
            if item["perspective"] == "WHITE" and item["kind"] == kind
        )
        segment = next(
            item
            for item in bundle["index"]["segments"]
            if item["perspective"] == "WHITE" and item["kind"] == kind
        )
        original_mask = (
            HM_TRAINING_MASK if kind == "training" else HM_VIRTUAL_MASK
        )
        zero_index = next(
            index
            for index in range(segment["count"])
            if not ((original_mask[index // 8] >> (index % 8)) & 1)
        )
        mutated_payloads: dict[str, bytearray] = {"train": train}
        mutated_payloads["validation"] = bytearray(
            paths["validation_atcov"].read_bytes()
        )
        for role, atcov in mutated_payloads.items():
            atcov[layout["offset"] + zero_index // 8] |= 1 << (zero_index % 8)
            _replace_atcov(bundle, paths, role, atcov)

        mask = bytes(
            train[layout["offset"] : layout["offset"] + layout["bytes"]]
        )
        digest = _structural_mask_digest_bytes(
            0,
            VALIDATOR.MASK_KIND_IDS[kind],
            0,
            segment["count"],
            mask,
        )
        bundle["policy"]["reachability_masks"]["WHITE"][field] = digest
        for role in ("train", "validation"):
            bundle[role]["backend"]["reachability_masks"]["WHITE"][field] = digest
            hm = bundle[role]["coverage_by_perspective"]["WHITE"]["hm_training"]
            prefix = "training" if kind == "training" else "virtual_factor"
            hm["structurally_reachable_{}_indices".format(prefix)] += 1
            hm["structurally_unreachable_{}_indices".format(prefix)] -= 1
            hm["reachable_unobserved_{}_indices".format(prefix)] += 1
        _recompute_declared_mask_aggregate(bundle)
    elif mutation == "counter_under_zero_mask":
        validation = bytearray(paths["validation_atcov"].read_bytes())
        mask_offset = first_layout["offset"]
        train[mask_offset] &= 0xFE
        validation[mask_offset] &= 0xFE

        validation_first = struct.unpack_from("<Q", validation, 128)[0]
        validation_second = struct.unpack_from("<Q", validation, 136)[0]
        struct.pack_into("<Q", validation, 128, 0)
        struct.pack_into("<Q", validation, 136, validation_second + validation_first)
        _replace_atcov(bundle, paths, "train", train)
        _replace_atcov(bundle, paths, "validation", validation)

        mask = bytes(
            train[mask_offset : mask_offset + first_layout["bytes"]]
        )
        field = VALIDATOR.MASK_FIELD_IDS[0]
        digest = _structural_mask_digest_bytes(0, 0, 0, MASK_DIMENSIONS[0], mask)
        bundle["policy"]["reachability_masks"]["WHITE"][field] = digest
        for role in ("train", "validation"):
            bundle[role]["backend"]["reachability_masks"]["WHITE"][field] = digest
            coverage = bundle[role]["coverage_by_perspective"]["WHITE"][
                "half_ka_v2_atomic_hm"
            ]
            coverage.update(
                {
                    "structurally_reachable_indices": MASK_DIMENSIONS[0] - 1,
                    "structurally_unreachable_indices": 1,
                    "observed_reachable_indices": MASK_DIMENSIONS[0] - 1,
                    "reachable_unobserved_indices": 0,
                }
            )
        _recompute_declared_mask_aggregate(bundle)
    else:  # pragma: no cover - every parameter is named above
        raise AssertionError("unknown mutation " + mutation)

    _serialize_cli_sidecars(bundle, paths)
    failed = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, check=False)
    payload = json.loads(failed.stdout)
    assert failed.returncode == 1
    assert payload["ok"] is False
    assert any(expected_error in error for error in payload["errors"]), payload["errors"]


@pytest.mark.parametrize(
    ("mutation", "expected_error"),
    (
        ("same_manifest_digest", "manifest artifact hashes must be distinct"),
        ("same_ledger_digest", "ledger artifact hashes must be distinct"),
        ("same_shard_digest", "shard SHA-256 appears in both splits"),
        ("invalid_manifest_json", "manifest is not valid strict JSON"),
    ),
)
def test_cli_rejects_reauthenticated_cross_split_artifact_aliases(
    tmp_path: Path, mutation: str, expected_error: str
) -> None:
    bundle, paths, command = _materialize_cli_bundle(tmp_path)
    if mutation == "same_manifest_digest":
        assert paths["train_manifest"].resolve() != paths["validation_manifest"].resolve()
        _replace_manifest_and_bindings(
            bundle, paths, "validation", paths["train_manifest"].read_bytes()
        )
    elif mutation == "same_ledger_digest":
        assert paths["train_ledger"].resolve() != paths["validation_ledger"].resolve()
        _replace_ledger(
            bundle, paths, "validation", paths["train_ledger"].read_bytes()
        )
    elif mutation == "same_shard_digest":
        assert paths["train_shard"].resolve() != paths["validation_shard"].resolve()
        paths["validation_shard"].write_bytes(paths["train_shard"].read_bytes())
        _rebind_manifest_shard(bundle, paths, "validation")
    elif mutation == "invalid_manifest_json":
        _replace_manifest_and_bindings(bundle, paths, "train", b"{not-json}\n")
    else:  # pragma: no cover - every parameter is named above
        raise AssertionError("unknown mutation " + mutation)

    _serialize_cli_sidecars(bundle, paths)
    failed = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, check=False)
    payload = json.loads(failed.stdout)
    assert failed.returncode == 1
    assert payload["ok"] is False
    assert any(expected_error in error for error in payload["errors"]), payload["errors"]


def test_cli_rejects_cross_split_hardlink_identity(tmp_path: Path) -> None:
    bundle, paths, command = _materialize_cli_bundle(tmp_path)
    paths["validation_shard"].unlink()
    try:
        os.link(paths["train_shard"], paths["validation_shard"])
    except (NotImplementedError, OSError) as exc:
        pytest.skip("hard links unavailable on this platform: {}".format(exc))
    assert os.path.samefile(paths["train_shard"], paths["validation_shard"])
    _rebind_manifest_shard(bundle, paths, "validation")
    _serialize_cli_sidecars(bundle, paths)

    failed = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, check=False)
    payload = json.loads(failed.stdout)
    assert failed.returncode == 1
    assert payload["ok"] is False
    assert any("same file identity" in error for error in payload["errors"]), payload[
        "errors"
    ]


def test_cli_rejects_one_shared_raw_record_across_distinct_shards(tmp_path: Path) -> None:
    bundle, paths, command = _materialize_cli_bundle(tmp_path)
    train = paths["train_shard"].read_bytes()
    validation = bytearray(paths["validation_shard"].read_bytes())
    validation[96:160] = train[96:160]
    paths["validation_shard"].write_bytes(validation)
    _rebind_manifest_shard(bundle, paths, "validation")
    _serialize_cli_sidecars(bundle, paths)

    failed = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, check=False)
    payload = json.loads(failed.stdout)
    assert failed.returncode == 1
    assert any(
        "raw_record_key appears in both" in error for error in payload["errors"]
    ), payload["errors"]


@pytest.mark.parametrize(
    ("white_count", "black_count", "expected_error"),
    (
        (17, 16, "position has 17 WHITE pieces"),
        (16, 17, "position has 17 BLACK pieces"),
    ),
)
def test_cli_rejects_reauthenticated_shard_outside_piece_count_proof_domain(
    tmp_path: Path,
    white_count: int,
    black_count: int,
    expected_error: str,
) -> None:
    bundle, paths, command = _materialize_cli_bundle(tmp_path)
    shard = bytearray(paths["train_shard"].read_bytes())
    shard[96:144] = _overpopulated_position(white_count, black_count)
    paths["train_shard"].write_bytes(shard)
    _rebind_manifest_shard(bundle, paths, "train")
    _serialize_cli_sidecars(bundle, paths)

    failed = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, check=False)
    result = json.loads(failed.stdout)
    assert failed.returncode == 1
    assert any(expected_error in error for error in result["errors"]), result["errors"]


@pytest.mark.parametrize(
    ("mutation", "expected_error"),
    (
        ("role", "ledger header role does not match statistics role"),
        ("record_range", "ledger record ranges are not contiguous"),
        ("partition", "trajectory partitions to validation, not train"),
        (
            "score_draw_adjudication",
            "release-candidate ledger contains adjudicated stop reason 7",
        ),
        (
            "evaluation_resignation",
            "release-candidate ledger contains adjudicated stop reason 8",
        ),
    ),
)
def test_cli_rejects_reauthenticated_ledger_structural_mutations(
    tmp_path: Path, mutation: str, expected_error: str
) -> None:
    bundle, paths, command = _materialize_cli_bundle(tmp_path)
    ledger = bytearray(paths["train_ledger"].read_bytes())
    if mutation == "role":
        struct.pack_into("<I", ledger, 20, 1)
    elif mutation == "record_range":
        struct.pack_into("<Q", ledger, 160 + 112 + 80, RECORDS // 2 - 1)
    elif mutation == "partition":
        validation_ledger = paths["validation_ledger"].read_bytes()
        validation_second_fullmove = struct.unpack_from(
            "<I", validation_ledger, 160 + 112 + 32 + 42
        )[0]
        root, split_group_id, _ = _role_trajectory(
            "validation",
            bundle["ledger"],
            bundle["policy"],
            RECORDS // 2,
            validation_second_fullmove + 1,
        )
        ledger[160:192] = split_group_id
        ledger[192:240] = root
    elif mutation == "score_draw_adjudication":
        ledger[160 + 106] = 7
    elif mutation == "evaluation_resignation":
        ledger[160 + 106] = 8
    else:  # pragma: no cover - every parameter is named above
        raise AssertionError("unknown mutation " + mutation)
    _replace_ledger(bundle, paths, "train", ledger)
    _serialize_cli_sidecars(bundle, paths)

    failed = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, check=False)
    payload = json.loads(failed.stdout)
    assert failed.returncode == 1
    assert payload["ok"] is False
    assert any(expected_error in error for error in payload["errors"]), payload["errors"]
