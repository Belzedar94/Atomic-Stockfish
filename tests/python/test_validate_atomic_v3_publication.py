from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import subprocess
import sys
from dataclasses import replace
from pathlib import Path
from typing import Any, Optional

import pytest
from jsonschema.validators import Draft202012Validator


ROOT = Path(__file__).resolve().parents[2]
VALIDATOR_PATH = ROOT / "tools" / "validate_atomic_v3_bundle.py"
BASE_FIXTURE_PATH = ROOT / "tests" / "python" / "test_validate_atomic_v3_bundle.py"


def _import_file(name: str, path: Path) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


VALIDATOR = _import_file("atomic_v3_publication_validator", VALIDATOR_PATH)
BASE = _import_file("atomic_v3_base_fixtures", BASE_FIXTURE_PATH)


NEW_SCHEMA_NAMES = (
    "atomic-v3-dataset-campaign-v1.json",
    "atomic-v3-producer-attestation-v1.json",
    "atomic-v3-semantic-audit-v1.json",
    "atomic-v3-reachability-attestation-v1.json",
    "atomic-v3-training-environment-v1.json",
    "atomic-v3-training-run-manifest-v2.json",
)

NEW_SCHEMA_HASHES = {
    "atomic-v3-dataset-campaign-v1.json": "36a86983d63e71e20daa3bcf7a574dfc95abb544974e36c064445e79ad706517",
    "atomic-v3-producer-attestation-v1.json": "de55f384fdea56fdb28addd50b78da7e0256b5a8857d5aec856219a3e922193e",
    "atomic-v3-semantic-audit-v1.json": "e1aed04f4291f1ae514ba532b9a4c21fd926e41b7e29b7782a5222a85eda7810",
    "atomic-v3-reachability-attestation-v1.json": "fb1af7130a2fa74be0fadd721db980269e12c89204b627eec63e6074ed3983e8",
    "atomic-v3-training-environment-v1.json": "8e2f9b97183d3deedfbc1d03ac396ace7c069fc369af09db7e1ee693cd59f3d0",
    "atomic-v3-training-run-manifest-v2.json": "7703f038262cd4a69299aeaf3e0bb35c6d3181029fd448f91560807a0507d184",
}

FROZEN_SCHEMA_HASHES = {
    "atomic-nnue-v3.json": "9d3c77a58e5e55ac1bc798dab41977451eb523fce1d6fd3ec3f7c1e574a78750",
    "atomic-bin-v2.json": "0352b036f2a140c609e3eb9c9d635dc553e8d77253d8faa92437390f5cf93cb6",
    "atomic-bin-v2-manifest.json": "83d63922df3ac4a0c81a21ec9d9fd9e180efe50f26efee62fe01710e09da5b42",
    "atomic-trajectory-ledger-v1.json": "c2aaf1b2813b124a9daa2905a3dc277d635aabcd536b2677155933ef2bb18a3e",
    "atomic-v3-index-coverage-v1.json": "3fc2240c620cf0b636696c8ae0d7aa1f82cdd95a7af16bf0053759c0038fb1e8",
    "atomic-v3-dataset-stats-v1.json": "118c2faa32d71d3fc4fe0ba0dab3d698dc8e84d1399a8f69b39f77d6f629f6e5",
    "atomic-v3-coverage-policy-v1.json": "c496a694df56efd4d221e86c9772f79b02a48ef8955b41724804555abeba3b9d",
    "atomic-v3-split-audit-v1.json": "8fedd68cd724c4daf992a910ebaaad63b694f41a9aa4b4e9c5d7adede7435ff9",
    "atomic-v3-training-run-manifest-v1.json": "f4aca665ea815d4c1ebc1105a77259197df340717bd851bc50b2b35eb409fa47",
}


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _blob(path: Path) -> dict[str, Any]:
    payload = path.read_bytes()
    return {"file": path.name, "bytes": str(len(payload)), "sha256": hashlib.sha256(payload).hexdigest()}


def _typed(path: Path, schema_path: Path) -> dict[str, Any]:
    return {**_blob(path), "schema_sha256": _sha(schema_path)}


def _write_json(path: Path, value: dict[str, Any], schema_name: str) -> None:
    path.write_bytes(BASE._canonical_sidecar_payload(value, schema_name))


def _schema_digest(name: str) -> str:
    return _sha(ROOT / "schemas" / name)


def _verification(names: tuple[str, ...]) -> dict[str, bool]:
    return {name: True for name in names}


def _campaign_chunk(
    bundle: dict[str, Any], paths: dict[str, Path], producer_build_sha256: str
) -> dict[str, Any]:
    schemas = ROOT / "schemas"
    return {
        "index": "0",
        "generation_seed": "9",
        "producer_build_sha256": producer_build_sha256,
        "partition_config_sha256": bundle["policy"]["partition"]["config_sha256"],
        "coverage_policy": _typed(paths["policy"], schemas / "atomic-v3-coverage-policy-v1.json"),
        "train": {
            "first_record": "0",
            "records": str(BASE.RECORDS),
            "trajectories": "2",
            "moves": str(BASE.RECORDS),
            "manifest": copy.deepcopy(bundle["train"]["artifacts"]["atomic_bin_v2_manifest"]),
            "trajectory_ledger": copy.deepcopy(bundle["train"]["artifacts"]["trajectory_ledger"]),
            "index_coverage": copy.deepcopy(bundle["train"]["artifacts"]["index_coverage"]),
            "statistics": _typed(paths["train"], schemas / "atomic-v3-dataset-stats-v1.json"),
        },
        "validation": {
            "first_record": "0",
            "records": str(BASE.RECORDS),
            "trajectories": "2",
            "moves": str(BASE.RECORDS),
            "manifest": copy.deepcopy(bundle["validation"]["artifacts"]["atomic_bin_v2_manifest"]),
            "trajectory_ledger": copy.deepcopy(bundle["validation"]["artifacts"]["trajectory_ledger"]),
            "index_coverage": copy.deepcopy(bundle["validation"]["artifacts"]["index_coverage"]),
            "statistics": _typed(paths["validation"], schemas / "atomic-v3-dataset-stats-v1.json"),
        },
        "split_audit": _typed(paths["audit"], schemas / "atomic-v3-split-audit-v1.json"),
    }


def _materialize_publication(tmp_path: Path) -> tuple[dict[str, Any], dict[str, Path], list[str]]:
    bundle, paths, command = BASE._materialize_cli_bundle(tmp_path)
    schemas = ROOT / "schemas"
    feature_copy = tmp_path / "atomic-nnue-v3.json"
    feature_copy.write_bytes((schemas / "atomic-nnue-v3.json").read_bytes())
    paths["feature_copy"] = feature_copy

    paths["producer_binary"] = tmp_path / "atomic-data-generator.bin"
    paths["producer_binary"].write_bytes(b"pinned atomic data generator\n")
    producer_binary = _blob(paths["producer_binary"])
    producer_build_set_sha256 = VALIDATOR.compute_producer_build_set_sha256(
        [producer_binary["sha256"]]
    )
    train_manifest = json.loads(paths["train_manifest"].read_text(encoding="utf-8"))
    homogeneous_profile_sha256 = VALIDATOR.compute_campaign_profile_sha256(
        bundle["policy"], train_manifest["generation"]
    )

    campaign = {
        "schema_version": 1,
        "campaign_id": "atomic-v3-publication-golden",
        "status": "completed",
        "schemas": {
            "feature": _schema_digest("atomic-nnue-v3.json"),
            "data": _schema_digest("atomic-bin-v2.json"),
            "manifest": _schema_digest("atomic-bin-v2-manifest.json"),
            "trajectory_ledger": _schema_digest("atomic-trajectory-ledger-v1.json"),
            "index_coverage": _schema_digest("atomic-v3-index-coverage-v1.json"),
            "statistics": _schema_digest("atomic-v3-dataset-stats-v1.json"),
            "coverage_policy": _schema_digest("atomic-v3-coverage-policy-v1.json"),
            "split_audit": _schema_digest("atomic-v3-split-audit-v1.json"),
        },
        "homogeneous_profile_sha256": homogeneous_profile_sha256,
        "producer_build_set_sha256": producer_build_set_sha256,
        "seed_schedule": {
            "method": "openbench-add-chunk-index-v1",
            "base_seed": "9",
            "first_chunk_index": "0",
            "chunk_count": 1,
        },
        "chunks": [_campaign_chunk(bundle, paths, producer_binary["sha256"])],
        "totals": {
            "train_records": str(BASE.RECORDS),
            "validation_records": str(BASE.RECORDS),
            "train_trajectories": "2",
            "validation_trajectories": "2",
            "train_moves": str(BASE.RECORDS),
            "validation_moves": str(BASE.RECORDS),
            "records": str(2 * BASE.RECORDS),
        },
        "collection_sha256": "0" * 64,
        "verification": _verification(
            (
                "chunk_indices_contiguous",
                "generation_seeds_recomputed",
                "role_offsets_contiguous",
                "totals_recomputed",
                "all_artifacts_distinct",
                "all_chunk_structural_bundles_validated",
                "homogeneous_profile_recomputed",
                "collection_hash_recomputed",
                "strict_eof",
            )
        ),
    }
    campaign["collection_sha256"] = VALIDATOR.compute_dataset_campaign_sha256(campaign)
    paths["campaign"] = tmp_path / "campaign.json"
    _write_json(paths["campaign"], campaign, "atomic-v3-dataset-campaign-v1.json")

    producer = {
        "schema_version": 1,
        "campaign": _typed(paths["campaign"], schemas / "atomic-v3-dataset-campaign-v1.json"),
        "producer_builds": {
            "algorithm_version": "atomic-v3-trajectory-producer-v1",
            "build_role": "data-generator",
            "build_set_sha256": producer_build_set_sha256,
            "builds": [
                {
                    "commit": "2" * 40,
                    "binary": producer_binary,
                }
            ],
            "chunk_builds": [
                {
                    "chunk_index": "0",
                    "binary_sha256": producer_binary["sha256"],
                }
            ],
        },
        "generation_policy": {
            "use_nnue": "pure",
            "adjudicate_draws_by_score": False,
            "adjudicate_resignations": False,
            "syzygy_disabled": True,
            "complete_trajectory_moves_preserved": True,
            "role_partition_before_publication": True,
        },
        "verification": _verification(VALIDATOR.PRODUCER_VERIFICATION_KEYS),
        "evidence_sha256": "0" * 64,
    }
    producer["evidence_sha256"] = VALIDATOR.compute_producer_attestation_sha256(producer)
    paths["producer"] = tmp_path / "producer-attestation.json"
    _write_json(paths["producer"], producer, "atomic-v3-producer-attestation-v1.json")

    paths["semantic_binary"] = tmp_path / "atomic-semantic-scanner.bin"
    paths["semantic_binary"].write_bytes(b"pinned atomic semantic scanner\n")
    paths["train_keys"] = tmp_path / "train.feature-keys"
    paths["validation_keys"] = tmp_path / "validation.feature-keys"
    train_keys = b"".join(
        bytes((0x10,)) + index.to_bytes(31, "big") for index in range(BASE.RECORDS)
    )
    validation_keys = b"".join(
        bytes((0x80,)) + index.to_bytes(31, "big") for index in range(BASE.RECORDS)
    )
    paths["train_keys"].write_bytes(train_keys)
    paths["validation_keys"].write_bytes(validation_keys)

    def role_evidence(role: str, key_payload: bytes) -> dict[str, Any]:
        return {
            "records_replayed": str(BASE.RECORDS),
            "trajectories_replayed": "2",
            "moves_replayed": str(BASE.RECORDS),
            "feature_input_keys": {
                "artifact": _blob(paths[role + "_keys"]),
                "observations": str(BASE.RECORDS),
                "unique_keys": str(BASE.RECORDS),
                "ordered_set_sha256": VALIDATOR.compute_ordered_set_sha256(
                    key_payload, BASE.RECORDS
                ),
            },
            "stop_reasons": ["2"] + ["0"] * 8,
        }

    semantic = {
        "schema_version": 1,
        "campaign": _typed(paths["campaign"], schemas / "atomic-v3-dataset-campaign-v1.json"),
        "producer_attestation": _typed(paths["producer"], schemas / "atomic-v3-producer-attestation-v1.json"),
        "scanner": {
            "commit": "1" * 40,
            "binary": _blob(paths["semantic_binary"]),
            "algorithm_version": "atomic-v3-engine-semantic-replay-v1",
        },
        "roles": {
            "train": role_evidence("train", train_keys),
            "validation": role_evidence("validation", validation_keys),
        },
        "intersections": {
            "raw_record_keys": "0",
            "feature_input_keys": "0",
            "split_group_ids": "0",
        },
        "verification": _verification(VALIDATOR.SEMANTIC_VERIFICATION_KEYS),
        "evidence_sha256": "0" * 64,
    }
    semantic["evidence_sha256"] = VALIDATOR.compute_semantic_audit_sha256(semantic)
    paths["semantic"] = tmp_path / "semantic-audit.json"
    _write_json(paths["semantic"], semantic, "atomic-v3-semantic-audit-v1.json")

    paths["oracle_binary"] = tmp_path / "atomic-reachability-oracle.bin"
    paths["oracle_binary"].write_bytes(b"independent symbolic reachability oracle\n")
    paths["oracle_output"] = tmp_path / "atomic-reachability.masks"
    oracle_output = b"".join(
        mask for _perspective in ("WHITE", "BLACK") for mask in BASE.PHYSICAL_MASKS
    )
    assert len(oracle_output) == 18772
    paths["oracle_output"].write_bytes(oracle_output)
    mask_dimensions = {
        "half_ka_v2_atomic_hm": (22528, 2816),
        "atomic_capture_pair": (40012, 5002),
        "atomic_king_blast_ep": (2304, 288),
        "atomic_blast_ring": (10240, 1280),
        "hm_training": (24576, 3072),
        "hm_virtual_factors": (768, 96),
    }
    reachability = {
        "schema_version": 1,
        "campaign": _typed(paths["campaign"], schemas / "atomic-v3-dataset-campaign-v1.json"),
        "producer_attestation": _typed(paths["producer"], schemas / "atomic-v3-producer-attestation-v1.json"),
        "feature_schema": _blob(feature_copy),
        "oracle": {
            "commit": "3" * 40,
            "binary": _blob(paths["oracle_binary"]),
            "algorithm_version": "atomic-v3-symbolic-reachability-v1",
        },
        "oracle_output": _blob(paths["oracle_output"]),
        "roles": {
            perspective: {
                field: {
                    "indices": mask_dimensions[field][0],
                    "bytes": mask_dimensions[field][1],
                    "sha256": BASE.MASKS[perspective][field],
                }
                for field in VALIDATOR.MASK_FIELD_IDS
            }
            for perspective in ("WHITE", "BLACK")
        },
        "reachability_mask_sha256": BASE.MASK_AGGREGATE,
        "verification": _verification(VALIDATOR.REACHABILITY_VERIFICATION_KEYS),
        "evidence_sha256": "0" * 64,
    }
    reachability["evidence_sha256"] = VALIDATOR.compute_reachability_attestation_sha256(
        reachability
    )
    paths["reachability"] = tmp_path / "reachability-attestation.json"
    _write_json(
        paths["reachability"],
        reachability,
        "atomic-v3-reachability-attestation-v1.json",
    )

    publication_command = command[:-1] + [
        "--feature-contract",
        str(feature_copy),
        "--campaign",
        str(paths["campaign"]),
        "--producer-attestation",
        str(paths["producer"]),
        "--semantic-audit",
        str(paths["semantic"]),
        "--reachability-attestation",
        str(paths["reachability"]),
        "--trusted-producer-binary-sha256",
        producer_binary["sha256"],
        "--trusted-producer-build-set-sha256",
        producer_build_set_sha256,
        "--trusted-semantic-scanner-binary-sha256",
        semantic["scanner"]["binary"]["sha256"],
        "--trusted-reachability-oracle-binary-sha256",
        reachability["oracle"]["binary"]["sha256"],
        "--require-publication-ready",
        "--json",
    ]
    documents = {
        "campaign": campaign,
        "producer": producer,
        "semantic": semantic,
        "reachability": reachability,
    }
    return documents, paths, publication_command


def _materialize_training_run_v2(
    tmp_path: Path, documents: dict[str, Any], paths: dict[str, Path]
) -> Path:
    schemas = ROOT / "schemas"
    input_schema_names = {
        "feature_schema": "atomic-nnue-v3.json",
        "dataset_schema": "atomic-bin-v2.json",
        "manifest_schema": "atomic-bin-v2-manifest.json",
        "trajectory_ledger_schema": "atomic-trajectory-ledger-v1.json",
        "index_coverage_schema": "atomic-v3-index-coverage-v1.json",
        "statistics_schema": "atomic-v3-dataset-stats-v1.json",
        "coverage_policy_schema": "atomic-v3-coverage-policy-v1.json",
        "split_audit_schema": "atomic-v3-split-audit-v1.json",
        "training_environment_schema": "atomic-v3-training-environment-v1.json",
        "dataset_campaign_schema": "atomic-v3-dataset-campaign-v1.json",
        "producer_attestation_schema": "atomic-v3-producer-attestation-v1.json",
        "semantic_audit_schema": "atomic-v3-semantic-audit-v1.json",
        "reachability_attestation_schema": "atomic-v3-reachability-attestation-v1.json",
    }
    schema_inputs: dict[str, Any] = {}
    for field, name in input_schema_names.items():
        destination = tmp_path / name
        if not destination.exists():
            destination.write_bytes((schemas / name).read_bytes())
        schema_inputs[field] = (
            _typed(destination, destination)
            if field == "training_environment_schema"
            else _blob(destination)
        )
    paths["trainer_config"] = tmp_path / "trainer-config.json"
    paths["trainer_config"].write_text("{}\n", encoding="utf-8")
    paths["trainer_binary"] = tmp_path / "atomic-v3-trainer.bin"
    paths["trainer_binary"].write_bytes(b"trusted atomic v3 trainer fixture\n")
    paths["dependency_lock"] = tmp_path / "requirements.lock"
    paths["dependency_lock"].write_bytes(
        b"torch==2.7.1 --hash=sha256:fixture-only-not-a-real-lock\n"
    )
    trainer_binary = _blob(paths["trainer_binary"])
    dependency_lock = _blob(paths["dependency_lock"])
    environment = {
        "schema_version": 1,
        "trainer_binary_sha256": trainer_binary["sha256"],
        "dependency_lock_sha256": dependency_lock["sha256"],
        "platform": "linux-6.8.0-fixture",
        "architecture": "x86_64",
        "python_version": "3.12.10",
        "pytorch_version": "2.7.1+cpu",
        "cuda_version": None,
        "cudnn_version": None,
        "driver_version": None,
        "accelerator_devices": ["CPU:generic-x86_64"],
        "container_image_sha256": None,
        "determinism": {
            "deterministic_algorithms": True,
            "warn_only": False,
            "cudnn_benchmark": False,
            "cudnn_deterministic": True,
            "allow_tf32": False,
            "cublas_workspace_config": None,
        },
    }
    paths["training_environment"] = tmp_path / "training-environment.json"
    _write_json(
        paths["training_environment"],
        environment,
        "atomic-v3-training-environment-v1.json",
    )
    outputs: dict[str, Any] = {}
    for name in ("checkpoint", "network", "training_log", "metrics"):
        path = tmp_path / (name + ".bin")
        path.write_bytes((name + "\n").encode("ascii"))
        outputs[name] = _blob(path)
    run = {
        "schema_version": 2,
        "run_id": "atomic-v3-publication-training-golden",
        "status": "completed",
        "run_definition_sha256": "0" * 64,
        "input_bundle_sha256": "0" * 64,
        "inputs": {
            **schema_inputs,
            "campaign": _typed(paths["campaign"], schemas / "atomic-v3-dataset-campaign-v1.json"),
            "producer_attestation": _typed(paths["producer"], schemas / "atomic-v3-producer-attestation-v1.json"),
            "semantic_audit": _typed(paths["semantic"], schemas / "atomic-v3-semantic-audit-v1.json"),
            "reachability_attestation": _typed(paths["reachability"], schemas / "atomic-v3-reachability-attestation-v1.json"),
        },
        "trainer": {
            "commit": "4" * 40,
            "artifact_sha256": trainer_binary["sha256"],
            "binary": trainer_binary,
            "dependency_lock": dependency_lock,
            "config": _blob(paths["trainer_config"]),
            "training_seed": "17",
            "deterministic_algorithms": True,
            "environment": _typed(
                paths["training_environment"],
                schemas / "atomic-v3-training-environment-v1.json",
            ),
        },
        "schedule": {
            "batch_size": 16384,
            "optimizer_steps": "10",
            "epochs": 1,
            "validation_interval_steps": "5",
        },
        "outputs": outputs,
        "verification": _verification(
            (
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
            )
        ),
    }
    run["run_definition_sha256"] = VALIDATOR.compute_run_definition_sha256_v2(run)
    run["input_bundle_sha256"] = VALIDATOR.compute_input_bundle_sha256_v2(run)
    path = tmp_path / "training-run-v2.json"
    _write_json(path, run, "atomic-v3-training-run-manifest-v2.json")
    return path


def _materialize_training_run_v1(tmp_path: Path, paths: dict[str, Path]) -> Path:
    schemas = ROOT / "schemas"
    schema_names = {
        "feature_schema": "atomic-nnue-v3.json",
        "dataset_schema": "atomic-bin-v2.json",
        "manifest_schema": "atomic-bin-v2-manifest.json",
        "trajectory_ledger_schema": "atomic-trajectory-ledger-v1.json",
        "index_coverage_schema": "atomic-v3-index-coverage-v1.json",
        "statistics_schema": "atomic-v3-dataset-stats-v1.json",
        "coverage_policy_schema": "atomic-v3-coverage-policy-v1.json",
        "split_audit_schema": "atomic-v3-split-audit-v1.json",
    }
    schema_inputs: dict[str, Any] = {}
    for field, name in schema_names.items():
        destination = tmp_path / ("v1-" + name)
        destination.write_bytes((schemas / name).read_bytes())
        schema_inputs[field] = _blob(destination)

    paths["v1_trainer_config"] = tmp_path / "v1-trainer-config.json"
    paths["v1_trainer_config"].write_text("{}\n", encoding="utf-8")
    outputs: dict[str, Any] = {}
    for name in ("checkpoint", "network", "training_log", "metrics"):
        output = tmp_path / ("v1-" + name + ".bin")
        output.write_bytes(("opaque-v1-" + name + "\n").encode("ascii"))
        outputs[name] = _blob(output)

    run = {
        "schema_version": 1,
        "run_id": "atomic-v3-frozen-v1-structural-only",
        "status": "completed",
        "run_definition_sha256": "0" * 64,
        "input_bundle_sha256": "0" * 64,
        "inputs": {
            **schema_inputs,
            "train": {
                "manifest": _blob(paths["train_manifest"]),
                "trajectory_ledger": _blob(paths["train_ledger"]),
                "index_coverage": _blob(paths["train_atcov"]),
                "statistics": _blob(paths["train"]),
            },
            "validation": {
                "manifest": _blob(paths["validation_manifest"]),
                "trajectory_ledger": _blob(paths["validation_ledger"]),
                "index_coverage": _blob(paths["validation_atcov"]),
                "statistics": _blob(paths["validation"]),
            },
            "coverage_policy": _blob(paths["policy"]),
            "split_audit": _blob(paths["audit"]),
        },
        "trainer": {
            "commit": "4" * 40,
            "artifact_sha256": "5" * 64,
            "dependency_lock_sha256": "6" * 64,
            "config": _blob(paths["v1_trainer_config"]),
            "training_seed": "17",
            "deterministic_algorithms": True,
            "device_description": "frozen-v1-structural-fixture",
        },
        "schedule": {
            "batch_size": 16384,
            "optimizer_steps": "10",
            "epochs": 1,
            "validation_interval_steps": "5",
        },
        "outputs": outputs,
        "verification": _verification(
            (
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
        ),
    }
    run["run_definition_sha256"] = VALIDATOR.compute_run_definition_sha256(run)
    run["input_bundle_sha256"] = VALIDATOR.compute_input_bundle_sha256(run)
    path = tmp_path / "training-run-v1.json"
    _write_json(path, run, "atomic-v3-training-run-manifest-v1.json")
    return path


def _training_command(
    command: list[str], run_path: Path, trainer_pin: Optional[str]
) -> list[str]:
    result = list(command)
    result.remove("--require-publication-ready")
    result.remove("--json")
    result += ["--training-run-manifest", str(run_path)]
    if trainer_pin is not None:
        result += ["--trusted-trainer-binary-sha256", trainer_pin]
    return result + ["--json"]


def _run(command: list[str]) -> tuple[subprocess.CompletedProcess[str], dict[str, Any]]:
    completed = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, check=False)
    return completed, json.loads(completed.stdout)


def _rewrite_training_run_v2(path: Path, run: dict[str, Any]) -> None:
    run["run_definition_sha256"] = VALIDATOR.compute_run_definition_sha256_v2(run)
    run["input_bundle_sha256"] = VALIDATOR.compute_input_bundle_sha256_v2(run)
    _write_json(path, run, "atomic-v3-training-run-manifest-v2.json")


def _rewrite_producer_chain(documents: dict[str, Any], paths: dict[str, Path]) -> None:
    schemas = ROOT / "schemas"
    producer = documents["producer"]
    producer["evidence_sha256"] = VALIDATOR.compute_producer_attestation_sha256(producer)
    _write_json(paths["producer"], producer, "atomic-v3-producer-attestation-v1.json")
    for name, schema_name, compute in (
        (
            "semantic",
            "atomic-v3-semantic-audit-v1.json",
            VALIDATOR.compute_semantic_audit_sha256,
        ),
        (
            "reachability",
            "atomic-v3-reachability-attestation-v1.json",
            VALIDATOR.compute_reachability_attestation_sha256,
        ),
    ):
        document = documents[name]
        document["producer_attestation"] = _typed(
            paths["producer"], schemas / "atomic-v3-producer-attestation-v1.json"
        )
        document["evidence_sha256"] = compute(document)
        _write_json(paths[name], document, schema_name)


def test_new_schemas_are_valid_and_frozen_v1_hashes_are_unchanged() -> None:
    for name in NEW_SCHEMA_NAMES:
        schema = json.loads((ROOT / "schemas" / name).read_text(encoding="utf-8"))
        Draft202012Validator.check_schema(schema)
        assert _schema_digest(name) == NEW_SCHEMA_HASHES[name]
    for name, expected in FROZEN_SCHEMA_HASHES.items():
        assert _schema_digest(name) == expected
    assert VALIDATOR.PUBLICATION_SCHEMA_SHA256 == {
        "campaign": NEW_SCHEMA_HASHES["atomic-v3-dataset-campaign-v1.json"],
        "producer_attestation": NEW_SCHEMA_HASHES[
            "atomic-v3-producer-attestation-v1.json"
        ],
        "semantic_audit": NEW_SCHEMA_HASHES["atomic-v3-semantic-audit-v1.json"],
        "reachability_attestation": NEW_SCHEMA_HASHES[
            "atomic-v3-reachability-attestation-v1.json"
        ],
        "training_environment": NEW_SCHEMA_HASHES[
            "atomic-v3-training-environment-v1.json"
        ],
        "training_run_v2": NEW_SCHEMA_HASHES[
            "atomic-v3-training-run-manifest-v2.json"
        ],
    }
    assert VALIDATOR.TRANSITIVE_PUBLICATION_SCHEMA_SHA256 == {
        "feature": FROZEN_SCHEMA_HASHES["atomic-nnue-v3.json"],
        "data": FROZEN_SCHEMA_HASHES["atomic-bin-v2.json"],
        "manifest": FROZEN_SCHEMA_HASHES["atomic-bin-v2-manifest.json"],
        "trajectory_ledger": FROZEN_SCHEMA_HASHES["atomic-trajectory-ledger-v1.json"],
        "index_coverage": FROZEN_SCHEMA_HASHES["atomic-v3-index-coverage-v1.json"],
        "statistics": FROZEN_SCHEMA_HASHES["atomic-v3-dataset-stats-v1.json"],
        "coverage_policy": FROZEN_SCHEMA_HASHES["atomic-v3-coverage-policy-v1.json"],
        "split_audit": FROZEN_SCHEMA_HASHES["atomic-v3-split-audit-v1.json"],
    }


def test_complete_authenticated_publication_closes_all_gates(tmp_path: Path) -> None:
    _documents, _paths, command = _materialize_publication(tmp_path)
    completed, payload = _run(command)
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert payload["ok"] is True
    assert payload["structural_ok"] is True
    assert payload["dataset_publication_ready"] is True
    assert payload["training_run_structural_ok"] is None
    assert payload["training_publication_ready"] is None
    assert payload["controlled_execution_verified"] is False
    assert payload["publication_ready"] is True
    assert payload["status"] == "dataset-publication-pass"
    assert payload["pending_publication_gates"] == []


def test_attestations_without_operator_binary_trust_pins_cannot_publish(
    tmp_path: Path,
) -> None:
    _documents, _paths, command = _materialize_publication(tmp_path)
    for flag in (
        "--trusted-producer-binary-sha256",
        "--trusted-producer-build-set-sha256",
        "--trusted-semantic-scanner-binary-sha256",
        "--trusted-reachability-oracle-binary-sha256",
    ):
        index = command.index(flag)
        del command[index : index + 2]
    command.remove("--require-publication-ready")
    completed, payload = _run(command)
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert payload["structural_ok"] is True
    assert payload["dataset_publication_ready"] is False
    assert payload["publication_ready"] is False
    assert payload["status"] == "structural-pass"
    assert any("operator-trusted" in gate for gate in payload["pending_publication_gates"])


def test_training_run_v2_authenticates_roots_but_cannot_publish_without_execution(
    tmp_path: Path,
) -> None:
    documents, paths, command = _materialize_publication(tmp_path)
    run_path = _materialize_training_run_v2(tmp_path, documents, paths)
    training_command = _training_command(
        command, run_path, _sha(paths["trainer_binary"])
    )
    training_command.insert(-1, "--require-dataset-publication-ready")
    completed, payload = _run(training_command)
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert payload["dataset_publication_ready"] is True
    assert payload["training_run_structural_ok"] is True
    assert payload["controlled_execution_verified"] is False
    assert payload["training_publication_ready"] is False
    assert payload["publication_ready"] is False
    assert payload["status"] == "training-structural-pass-publication-pending"
    assert payload["pending_training_publication_gates"] == [
        "authenticated controlled trainer execution and output provenance"
    ]


def test_training_run_v2_rejects_substituted_root_after_bundle_rehash(
    tmp_path: Path,
) -> None:
    documents, paths, command = _materialize_publication(tmp_path)
    run_path = _materialize_training_run_v2(tmp_path, documents, paths)
    run = json.loads(run_path.read_text(encoding="utf-8"))
    substitute = tmp_path / "substituted-campaign.json"
    substitute.write_bytes(b"{}\n")
    run["inputs"]["campaign"].update(_blob(substitute))
    run["input_bundle_sha256"] = VALIDATOR.compute_input_bundle_sha256_v2(run)
    _write_json(run_path, run, "atomic-v3-training-run-manifest-v2.json")
    completed, payload = _run(
        _training_command(command, run_path, _sha(paths["trainer_binary"]))
    )
    assert completed.returncode == 1
    assert payload["publication_ready"] is False
    assert any(
        "training_run.inputs.campaign.sha256" in error
        and "validated bundle artifact" in error
        for error in payload["errors"]
    )


def test_training_run_v2_requires_operator_trainer_binary_pin(tmp_path: Path) -> None:
    documents, paths, command = _materialize_publication(tmp_path)
    run_path = _materialize_training_run_v2(tmp_path, documents, paths)
    completed, payload = _run(_training_command(command, run_path, None))
    assert completed.returncode == 1
    assert payload["publication_ready"] is False
    assert any(
        "operator-trusted trainer binary pin" in error for error in payload["errors"]
    )


def test_v1_without_publication_roots_remains_structural_only(tmp_path: Path) -> None:
    _bundle, _paths, legacy_command = BASE._materialize_cli_bundle(tmp_path)
    legacy, legacy_payload = _run(legacy_command)
    assert legacy.returncode == 0
    assert legacy_payload["status"] == "structural-pass"
    assert legacy_payload["publication_ready"] is False
    assert len(legacy_payload["pending_publication_gates"]) == 4


def test_supplied_v1_with_complete_dataset_roots_never_publishes_training(
    tmp_path: Path,
) -> None:
    _documents, paths, command = _materialize_publication(tmp_path)
    run_path = _materialize_training_run_v1(tmp_path, paths)
    completed, payload = _run(_training_command(command, run_path, None))
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert payload["dataset_publication_ready"] is True
    assert payload["training_run_structural_ok"] is True
    assert payload["controlled_execution_verified"] is False
    assert payload["training_publication_ready"] is False
    assert payload["publication_ready"] is False
    assert payload["status"] == "training-structural-pass-publication-pending"


def test_opaque_v2_outputs_cannot_satisfy_overall_publication_requirement(
    tmp_path: Path,
) -> None:
    documents, paths, command = _materialize_publication(tmp_path)
    run_path = _materialize_training_run_v2(tmp_path, documents, paths)
    required = _training_command(command, run_path, _sha(paths["trainer_binary"]))
    required.insert(-1, "--require-publication-ready")
    required.insert(-1, "--require-training-publication-ready")
    completed, payload = _run(required)
    assert completed.returncode == 1
    assert payload["dataset_publication_ready"] is True
    assert payload["training_run_structural_ok"] is True
    assert payload["controlled_execution_verified"] is False
    assert payload["training_publication_ready"] is False
    assert payload["publication_ready"] is False
    assert any("controlled authenticated execution" in error for error in payload["errors"])


@pytest.mark.parametrize(
    "binding",
    (
        "input_bundle_sha256",
        "run_definition_sha256",
        "feature_schema_sha256",
        "campaign_sha256",
        "producer_attestation_sha256",
        "semantic_audit_sha256",
        "reachability_attestation_sha256",
    ),
)
def test_each_opaque_checkpoint_binding_remains_fail_closed_for_publication(
    tmp_path: Path, binding: str
) -> None:
    documents, paths, command = _materialize_publication(tmp_path)
    run_path = _materialize_training_run_v2(tmp_path, documents, paths)
    run = json.loads(run_path.read_text(encoding="utf-8"))
    checkpoint_bindings = {
        "input_bundle_sha256": run["input_bundle_sha256"],
        "run_definition_sha256": run["run_definition_sha256"],
        "feature_schema_sha256": run["inputs"]["feature_schema"]["sha256"],
        "campaign_sha256": run["inputs"]["campaign"]["sha256"],
        "producer_attestation_sha256": run["inputs"]["producer_attestation"][
            "sha256"
        ],
        "semantic_audit_sha256": run["inputs"]["semantic_audit"]["sha256"],
        "reachability_attestation_sha256": run["inputs"][
            "reachability_attestation"
        ]["sha256"],
    }
    checkpoint_bindings[binding] = "f" * 64
    checkpoint_path = tmp_path / "checkpoint.bin"
    checkpoint_path.write_text(
        json.dumps(checkpoint_bindings, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    run["outputs"]["checkpoint"] = _blob(checkpoint_path)
    _rewrite_training_run_v2(run_path, run)
    completed, payload = _run(
        _training_command(command, run_path, _sha(paths["trainer_binary"]))
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert payload["dataset_publication_ready"] is True
    assert payload["training_run_structural_ok"] is True
    assert payload["controlled_execution_verified"] is False
    assert payload["training_publication_ready"] is False
    assert payload["publication_ready"] is False


@pytest.mark.parametrize("artifact", ("dependency_lock", "training_environment"))
@pytest.mark.parametrize("failure", ("missing", "mutated"))
def test_training_run_v2_reauthenticates_lock_and_environment_artifacts(
    tmp_path: Path, artifact: str, failure: str
) -> None:
    documents, paths, command = _materialize_publication(tmp_path)
    run_path = _materialize_training_run_v2(tmp_path, documents, paths)
    artifact_path = paths[artifact]
    if failure == "missing":
        artifact_path.unlink()
    else:
        artifact_path.write_bytes(artifact_path.read_bytes() + b"tampered")
    completed, payload = _run(
        _training_command(command, run_path, _sha(paths["trainer_binary"]))
    )
    assert completed.returncode == 1
    assert payload["dataset_publication_ready"] is True
    assert payload["training_run_structural_ok"] is False
    assert payload["training_publication_ready"] is False
    assert payload["publication_ready"] is False
    assert any(artifact.replace("training_", "") in error for error in payload["errors"])


@pytest.mark.parametrize(
    ("field", "replacement", "needle"),
    (
        ("trainer_binary_sha256", "a" * 64, "trainer binary"),
        ("dependency_lock_sha256", "b" * 64, "dependency lock"),
    ),
)
def test_training_environment_must_cross_bind_trainer_and_dependency_lock(
    tmp_path: Path, field: str, replacement: str, needle: str
) -> None:
    documents, paths, command = _materialize_publication(tmp_path)
    run_path = _materialize_training_run_v2(tmp_path, documents, paths)
    environment = json.loads(paths["training_environment"].read_text(encoding="utf-8"))
    environment[field] = replacement
    _write_json(
        paths["training_environment"],
        environment,
        "atomic-v3-training-environment-v1.json",
    )
    run = json.loads(run_path.read_text(encoding="utf-8"))
    run["trainer"]["environment"] = _typed(
        paths["training_environment"],
        ROOT / "schemas" / "atomic-v3-training-environment-v1.json",
    )
    _rewrite_training_run_v2(run_path, run)
    completed, payload = _run(
        _training_command(command, run_path, _sha(paths["trainer_binary"]))
    )
    assert completed.returncode == 1
    assert payload["training_run_structural_ok"] is False
    assert payload["publication_ready"] is False
    assert any(needle in error for error in payload["errors"]), payload["errors"]


def test_training_run_v2_rejects_non_deterministic_execution_claim(
    tmp_path: Path,
) -> None:
    documents, paths, command = _materialize_publication(tmp_path)
    run_path = _materialize_training_run_v2(tmp_path, documents, paths)
    run = json.loads(run_path.read_text(encoding="utf-8"))
    run["trainer"]["deterministic_algorithms"] = False
    _rewrite_training_run_v2(run_path, run)
    completed, payload = _run(
        _training_command(command, run_path, _sha(paths["trainer_binary"]))
    )
    assert completed.returncode == 1
    assert payload["training_run_structural_ok"] is False
    assert payload["publication_ready"] is False
    assert any("deterministic_algorithms" in error for error in payload["errors"])


@pytest.mark.parametrize(
    ("mutation", "needle"),
    (
        (
            lambda producer: producer["producer_builds"]["chunk_builds"][0].__setitem__(
                "binary_sha256", "f" * 64
            ),
            "authenticated build",
        ),
        (
            lambda producer: producer["producer_builds"].__setitem__(
                "build_set_sha256", "e" * 64
            ),
            "exact producer build set",
        ),
        (
            lambda producer: producer["producer_builds"]["builds"][0].__setitem__(
                "commit", "d" * 40
            ),
            "generator_commit",
        ),
    ),
)
def test_producer_attestation_rejects_mapping_root_and_commit_substitution(
    tmp_path: Path, mutation: Any, needle: str
) -> None:
    documents, paths, command = _materialize_publication(tmp_path)
    mutation(documents["producer"])
    _rewrite_producer_chain(documents, paths)
    completed, payload = _run(command)
    assert completed.returncode == 1
    assert payload["dataset_publication_ready"] is False
    assert payload["publication_ready"] is False
    assert any(needle in error for error in payload["errors"]), payload["errors"]


def test_producer_build_set_operator_pin_is_not_self_asserted(tmp_path: Path) -> None:
    _documents, _paths, command = _materialize_publication(tmp_path)
    index = command.index("--trusted-producer-build-set-sha256")
    command[index + 1] = "f" * 64
    completed, payload = _run(command)
    assert completed.returncode == 1
    assert payload["dataset_publication_ready"] is False
    assert payload["publication_ready"] is False
    assert any("operator trust pin" in error for error in payload["errors"])


def test_campaign_profile_ignores_only_seed_and_volume_layout_fields() -> None:
    policy = BASE._policy()
    generation = BASE._manifest_generation()
    baseline = VALIDATOR.compute_campaign_profile_sha256(policy, generation)

    seed_and_volume = copy.deepcopy(generation)
    seed_and_volume["resolved_seed"] = "18446744073709551615"
    seed_and_volume["options"]["requested_records"] = "500000000"
    seed_and_volume["options"]["records_per_shard"] = "250000"
    seed_and_volume["options"]["random_file_name"] = True
    assert VALIDATOR.compute_campaign_profile_sha256(policy, seed_and_volume) == baseline

    semantic_mutations = []
    changed_teacher = copy.deepcopy(policy)
    changed_teacher["partition"]["provenance"]["teacher_network_sha256"] = "a" * 64
    semantic_mutations.append((changed_teacher, generation))
    changed_book = copy.deepcopy(policy)
    changed_book["partition"]["provenance"]["opening_book_sha256"] = "b" * 64
    semantic_mutations.append((changed_book, generation))
    changed_tool = copy.deepcopy(policy)
    changed_tool["partition"]["provenance"]["tools_commit"] = "c" * 40
    semantic_mutations.append((changed_tool, generation))
    changed_split_seed = copy.deepcopy(policy)
    changed_split_seed["partition"]["split_seed"] = "8"
    semantic_mutations.append((changed_split_seed, generation))
    changed_threshold = copy.deepcopy(policy)
    changed_threshold["partition"]["validation_threshold_u64"] = str((1 << 63) + 1)
    semantic_mutations.append((changed_threshold, generation))
    changed_search = copy.deepcopy(generation)
    changed_search["options"]["random_multi_pv_diff"] += 1
    semantic_mutations.append((policy, changed_search))
    for changed_policy, changed_generation in semantic_mutations:
        assert (
            VALIDATOR.compute_campaign_profile_sha256(
                changed_policy, changed_generation
            )
            != baseline
        )


@pytest.mark.parametrize(
    "missing_flag",
    ("--producer-attestation", "--semantic-audit", "--reachability-attestation"),
)
def test_partial_publication_evidence_is_rejected(tmp_path: Path, missing_flag: str) -> None:
    _documents, _paths, command = _materialize_publication(tmp_path)
    index = command.index(missing_flag)
    del command[index : index + 2]
    completed, payload = _run(command)
    assert completed.returncode == 1
    assert payload["structural_ok"] is True
    assert payload["publication_ready"] is False
    assert payload["status"] == "publication-gate-fail"
    assert any("evidence document is required" in error for error in payload["errors"])


def test_substituted_attestation_bytes_are_rejected(tmp_path: Path) -> None:
    _documents, paths, command = _materialize_publication(tmp_path)
    paths["producer"].write_bytes(paths["producer"].read_bytes() + b" ")
    completed, payload = _run(command)
    assert completed.returncode == 1
    assert payload["publication_ready"] is False
    assert any("does not bind the subject" in error or "canonical" in error for error in payload["errors"])


def test_publication_rejects_semantically_equivalent_schema_override(
    tmp_path: Path,
) -> None:
    _documents, _paths, command = _materialize_publication(tmp_path)
    schema = json.loads(
        (ROOT / "schemas" / "atomic-v3-dataset-campaign-v1.json").read_text(
            encoding="utf-8"
        )
    )
    schema["$comment"] = "digest substitution must not redefine publication readiness"
    substitute = tmp_path / "substitute-campaign-schema.json"
    substitute.write_text(json.dumps(schema), encoding="utf-8")
    completed, payload = _run(
        command[:-1] + ["--campaign-schema", str(substitute), "--json"]
    )
    assert completed.returncode == 1
    assert payload["publication_ready"] is False
    assert any(
        "campaign_schema.sha256" in error and "frozen release schema" in error
        for error in payload["errors"]
    )


def test_publication_rejects_transitive_v1_schema_override(tmp_path: Path) -> None:
    _documents, _paths, command = _materialize_publication(tmp_path)
    schema = json.loads(
        (ROOT / "schemas" / "atomic-bin-v2.json").read_text(encoding="utf-8")
    )
    schema["$comment"] = "transitive schema substitution must not redefine publication"
    substitute = tmp_path / "substitute-atomic-bin-v2-schema.json"
    substitute.write_text(json.dumps(schema), encoding="utf-8")
    completed, payload = _run(
        command[:-1] + ["--atomic-bin-v2-schema", str(substitute), "--json"]
    )
    assert completed.returncode == 1
    assert payload["publication_ready"] is False
    assert any(
        "publication.data_schema.sha256" in error
        and "frozen transitive release schema" in error
        for error in payload["errors"]
    )


def test_cached_artifacts_are_reauthenticated_before_use(tmp_path: Path) -> None:
    sidecar = tmp_path / "stats.json"
    artifact = tmp_path / "subject.bin"
    artifact.write_bytes(b"OLD")
    descriptor = _blob(artifact)
    initial = (3, descriptor["sha256"])
    artifact.write_bytes(b"NEW")
    errors, _resolved = VALIDATOR.authenticate_declared_artifacts(
        {"artifacts": {"subject": descriptor}},
        sidecar,
        "stats",
        {artifact: initial},
        {},
    )
    assert any("changed after initial authentication" in error for error in errors)
    assert any("does not authenticate exact artifact bytes" in error for error in errors)


def test_training_run_cached_inputs_are_reauthenticated_before_use(
    tmp_path: Path,
) -> None:
    run_path = tmp_path / "run.json"
    subject = tmp_path / "subject.bin"
    subject.write_bytes(b"OLD")
    subject_descriptor = _blob(subject)
    initial = (3, subject_descriptor["sha256"])
    subject.write_bytes(b"NEW")
    config = tmp_path / "config.bin"
    config.write_bytes(b"config")
    outputs: dict[str, Any] = {}
    for name in ("checkpoint", "network", "training_log", "metrics"):
        output = tmp_path / (name + ".bin")
        output.write_bytes(name.encode("ascii"))
        outputs[name] = _blob(output)
    manifest = {
        "schema_version": 1,
        "inputs": {"subject": subject_descriptor},
        "trainer": {"config": _blob(config)},
        "outputs": outputs,
    }
    errors = VALIDATOR.authenticate_training_run_artifacts(
        manifest,
        run_path,
        {subject: initial},
        {},
        ("subject",),
    )
    assert any("changed after initial authentication" in error for error in errors)
    assert any("does not authenticate exact artifact bytes" in error for error in errors)


def test_publication_provenance_rejects_pilot_policy_and_adjudications(
    tmp_path: Path,
) -> None:
    bundle, paths, _command = BASE._materialize_cli_bundle(tmp_path)
    manifest_schema_path = ROOT / "schemas" / "atomic-bin-v2-manifest.json"
    dataset_schema_path = ROOT / "schemas" / "atomic-bin-v2.json"
    manifest_schema = json.loads(manifest_schema_path.read_text(encoding="utf-8"))
    summary, parse_errors = VALIDATOR.parse_atomic_bin_v2_manifest(
        "train",
        paths["train_manifest"],
        bundle["train"]["artifacts"]["atomic_bin_v2_manifest"],
        manifest_schema,
        _sha(manifest_schema_path),
        _sha(dataset_schema_path),
    )
    assert not parse_errors and summary is not None
    generation = copy.deepcopy(summary.generation)
    generation["options"]["adjudicate_draws_by_score"] = True
    policy = copy.deepcopy(bundle["policy"])
    policy["status"] = "pilot"
    profile = policy["partition"]["provenance"]["generation_profile"]
    profile["adjudicate_resignations"] = True
    profile["atomic_bin_v2_generation_sha256"] = (
        VALIDATOR.compute_atomic_bin_v2_generation_sha256(generation)
    )
    modified_summary = replace(
        summary,
        generation=generation,
        generation_sha256=VALIDATOR.compute_atomic_bin_v2_generation_sha256(generation),
    )
    errors = VALIDATOR._validate_manifest_provenance(
        "train", modified_summary, {}, policy, require_release_candidate=True
    )
    assert any("requires a release-candidate" in error for error in errors)
    assert any("adjudicate_draws_by_score" in error for error in errors)
    assert any("resignation attestation must be false" in error for error in errors)


def test_semantic_audit_rejects_impossible_key_and_stop_counts(tmp_path: Path) -> None:
    documents, paths, _command = _materialize_publication(tmp_path)
    audit = copy.deepcopy(documents["semantic"])
    audit["roles"]["train"]["feature_input_keys"]["unique_keys"] = str(
        BASE.RECORDS + 1
    )
    audit["roles"]["train"]["stop_reasons"] = ["3"] + ["0"] * 8
    audit["evidence_sha256"] = VALIDATOR.compute_semantic_audit_sha256(audit)
    errors = VALIDATOR.validate_semantic_audit(
        audit,
        _sha(paths["campaign"]),
        _schema_digest("atomic-v3-dataset-campaign-v1.json"),
        _sha(paths["producer"]),
        _schema_digest("atomic-v3-producer-attestation-v1.json"),
        documents["campaign"]["totals"],
    )
    assert any("unique_keys" in error and "observations" in error for error in errors)
    assert any("stop_reasons" in error and "trajectories_replayed" in error for error in errors)


def test_semantic_stop_reasons_must_equal_campaign_statistics(tmp_path: Path) -> None:
    documents, paths, command = _materialize_publication(tmp_path)
    semantic = documents["semantic"]
    semantic["roles"]["train"]["stop_reasons"] = ["1", "1"] + ["0"] * 7
    semantic["evidence_sha256"] = VALIDATOR.compute_semantic_audit_sha256(semantic)
    _write_json(paths["semantic"], semantic, "atomic-v3-semantic-audit-v1.json")
    completed, payload = _run(command)
    assert completed.returncode == 1
    assert any(
        "semantic_audit.roles.train.stop_reasons" in error
        and "statistics aggregate" in error
        for error in payload["errors"]
    )


def test_every_campaign_policy_must_match_oracle_reachability(tmp_path: Path) -> None:
    matching = {
        "reachability_mask_sha256": BASE.MASK_AGGREGATE,
        "reachability_masks": copy.deepcopy(BASE.MASKS),
    }
    mismatching = copy.deepcopy(matching)
    mismatching["reachability_masks"]["BLACK"][VALIDATOR.MASK_FIELD_IDS[0]] = "f" * 64
    paths = []
    chunks = []
    artifacts: dict[str, Path] = {}
    for index, policy in enumerate((matching, mismatching)):
        path = tmp_path / "policy-{}.json".format(index)
        path.write_text(json.dumps(policy), encoding="utf-8")
        paths.append(path)
        descriptor = _blob(path)
        chunks.append({"coverage_policy": descriptor})
        artifacts["campaign.chunks[{}].coverage_policy".format(index)] = path
    reachability = {
        "reachability_mask_sha256": BASE.MASK_AGGREGATE,
        "roles": {
            perspective: {
                field: {"sha256": BASE.MASKS[perspective][field]}
                for field in VALIDATOR.MASK_FIELD_IDS
            }
            for perspective in ("WHITE", "BLACK")
        },
    }
    errors = VALIDATOR.validate_all_campaign_policy_reachability(
        {"chunks": chunks}, artifacts, reachability
    )
    assert any("chunks[1]" in error and "twelve" in error for error in errors)


def _synthetic_campaign() -> dict[str, Any]:
    schemas = {name: hashlib.sha256(name.encode()).hexdigest() for name in VALIDATOR.CAMPAIGN_SCHEMA_KEYS}
    producer_build_sha256 = hashlib.sha256(b"synthetic-producer-build").hexdigest()
    producer_build_set_sha256 = VALIDATOR.compute_producer_build_set_sha256(
        [producer_build_sha256]
    )
    digest_counter = 0

    def artifact(schema_name: str) -> dict[str, Any]:
        nonlocal digest_counter
        digest_counter += 1
        return {
            "file": "artifact-{}.bin".format(digest_counter),
            "bytes": "1",
            "sha256": hashlib.sha256(str(digest_counter).encode()).hexdigest(),
            "schema_sha256": schemas[schema_name],
        }

    chunks = []
    for index in range(2):
        chunk: dict[str, Any] = {
            "index": str(index),
            "generation_seed": str(100 + index),
            "producer_build_sha256": producer_build_sha256,
            "partition_config_sha256": hashlib.sha256(("partition" + str(index)).encode()).hexdigest(),
            "coverage_policy": artifact("coverage_policy"),
        }
        for role in ("train", "validation"):
            chunk[role] = {
                "first_record": str(index * 10),
                "records": "10",
                "trajectories": "2",
                "moves": "20",
                "manifest": artifact("manifest"),
                "trajectory_ledger": artifact("trajectory_ledger"),
                "index_coverage": artifact("index_coverage"),
                "statistics": artifact("statistics"),
            }
        chunk["split_audit"] = artifact("split_audit")
        chunks.append(chunk)
    campaign = {
        "schema_version": 1,
        "campaign_id": "two-chunk-contract-golden",
        "status": "completed",
        "schemas": schemas,
        "homogeneous_profile_sha256": hashlib.sha256(
            b"synthetic-homogeneous-profile"
        ).hexdigest(),
        "producer_build_set_sha256": producer_build_set_sha256,
        "seed_schedule": {
            "method": "openbench-add-chunk-index-v1",
            "base_seed": "100",
            "first_chunk_index": "0",
            "chunk_count": 2,
        },
        "chunks": chunks,
        "totals": {
            "train_records": "20",
            "validation_records": "20",
            "train_trajectories": "4",
            "validation_trajectories": "4",
            "train_moves": "40",
            "validation_moves": "40",
            "records": "40",
        },
        "collection_sha256": "0" * 64,
        "verification": _verification(
            (
                "chunk_indices_contiguous",
                "generation_seeds_recomputed",
                "role_offsets_contiguous",
                "totals_recomputed",
                "all_artifacts_distinct",
                "all_chunk_structural_bundles_validated",
                "homogeneous_profile_recomputed",
                "collection_hash_recomputed",
                "strict_eof",
            )
        ),
    }
    campaign["collection_sha256"] = VALIDATOR.compute_dataset_campaign_sha256(campaign)
    assert VALIDATOR.validate_dataset_campaign(campaign) == []
    return campaign


def _synthetic_multi_build_producer_attestation(
    campaign: dict[str, Any], campaign_sha256: str, campaign_schema_sha256: str
) -> dict[str, Any]:
    build_digests = sorted(
        hashlib.sha256(label).hexdigest()
        for label in (b"synthetic-producer-a", b"synthetic-producer-b")
    )
    assert len(campaign["chunks"]) == len(build_digests)
    for chunk, digest in zip(campaign["chunks"], build_digests):
        chunk["producer_build_sha256"] = digest
    campaign["producer_build_set_sha256"] = VALIDATOR.compute_producer_build_set_sha256(
        build_digests
    )
    campaign["collection_sha256"] = VALIDATOR.compute_dataset_campaign_sha256(campaign)
    attestation = {
        "schema_version": 1,
        "campaign": {
            "file": "synthetic-campaign.json",
            "bytes": "1",
            "sha256": campaign_sha256,
            "schema_sha256": campaign_schema_sha256,
        },
        "producer_builds": {
            "algorithm_version": "atomic-v3-trajectory-producer-v1",
            "build_role": "data-generator",
            "build_set_sha256": campaign["producer_build_set_sha256"],
            "builds": [
                {
                    "commit": format(index + 1, "040x"),
                    "binary": {
                        "file": "producer-{}.bin".format(index),
                        "bytes": "1",
                        "sha256": digest,
                    },
                }
                for index, digest in enumerate(build_digests)
            ],
            "chunk_builds": [
                {
                    "chunk_index": chunk["index"],
                    "binary_sha256": chunk["producer_build_sha256"],
                }
                for chunk in campaign["chunks"]
            ],
        },
        "generation_policy": {
            "use_nnue": "pure",
            "adjudicate_draws_by_score": False,
            "adjudicate_resignations": False,
            "syzygy_disabled": True,
            "complete_trajectory_moves_preserved": True,
            "role_partition_before_publication": True,
        },
        "verification": _verification(VALIDATOR.PRODUCER_VERIFICATION_KEYS),
        "evidence_sha256": "0" * 64,
    }
    attestation["evidence_sha256"] = VALIDATOR.compute_producer_attestation_sha256(
        attestation
    )
    return attestation


@pytest.mark.parametrize(
    ("mutation", "needle"),
    (
        ("missing-mapping", "map every campaign chunk exactly once"),
        ("extra-mapping", "map every campaign chunk exactly once"),
        ("extra-unreferenced-build", "exact set of producer builds"),
        ("unsorted-builds", "strictly sorted"),
        ("duplicate-build", "strictly sorted"),
    ),
)
def test_producer_build_set_requires_exact_sorted_builds_and_chunk_mapping(
    mutation: str, needle: str
) -> None:
    campaign = _synthetic_campaign()
    campaign_sha256 = hashlib.sha256(b"synthetic-campaign-document").hexdigest()
    campaign_schema_sha256 = hashlib.sha256(b"synthetic-campaign-schema").hexdigest()
    attestation = _synthetic_multi_build_producer_attestation(
        campaign, campaign_sha256, campaign_schema_sha256
    )
    assert (
        VALIDATOR.validate_producer_attestation(
            attestation, campaign, campaign_sha256, campaign_schema_sha256
        )
        == []
    )
    producer_builds = attestation["producer_builds"]
    if mutation == "missing-mapping":
        producer_builds["chunk_builds"].pop()
    elif mutation == "extra-mapping":
        producer_builds["chunk_builds"].append(
            {
                "chunk_index": "2",
                "binary_sha256": producer_builds["builds"][0]["binary"]["sha256"],
            }
        )
    elif mutation == "extra-unreferenced-build":
        digest = hashlib.sha256(b"synthetic-producer-extra").hexdigest()
        producer_builds["builds"].append(
            {
                "commit": "3" * 40,
                "binary": {"file": "producer-extra.bin", "bytes": "1", "sha256": digest},
            }
        )
        producer_builds["builds"].sort(key=lambda item: item["binary"]["sha256"])
        producer_builds["build_set_sha256"] = VALIDATOR.compute_producer_build_set_sha256(
            [item["binary"]["sha256"] for item in producer_builds["builds"]]
        )
    elif mutation == "unsorted-builds":
        producer_builds["builds"].reverse()
    elif mutation == "duplicate-build":
        producer_builds["builds"].append(copy.deepcopy(producer_builds["builds"][-1]))
    attestation["evidence_sha256"] = VALIDATOR.compute_producer_attestation_sha256(
        attestation
    )
    errors = VALIDATOR.validate_producer_attestation(
        attestation, campaign, campaign_sha256, campaign_schema_sha256
    )
    assert any(needle in error for error in errors), errors


@pytest.mark.parametrize(
    ("mutation", "needle"),
    (
        (lambda value: value["chunks"].reverse(), "contiguous"),
        (lambda value: value["chunks"][1].__setitem__("index", "0"), "contiguous"),
        (lambda value: value["chunks"][1].__setitem__("generation_seed", "999"), "base_seed"),
        (lambda value: value["chunks"][1]["train"].__setitem__("first_record", "0"), "preceding role end"),
        (lambda value: value["totals"].__setitem__("train_records", "21"), "chunk sum"),
        (lambda value: value["chunks"][1]["train"]["manifest"].__setitem__("sha256", value["chunks"][0]["train"]["manifest"]["sha256"]), "duplicates"),
        (lambda value: value.__setitem__("collection_sha256", "f" * 64), "ordered collection"),
    ),
)
def test_campaign_rejects_order_duplicates_seeds_offsets_totals_and_hashes(
    mutation: Any, needle: str
) -> None:
    campaign = _synthetic_campaign()
    mutation(campaign)
    errors = VALIDATOR.validate_dataset_campaign(campaign)
    assert any(needle in error for error in errors), errors


def test_publication_dag_schemas_reject_backward_edges() -> None:
    schemas = {name: json.loads((ROOT / "schemas" / name).read_text()) for name in NEW_SCHEMA_NAMES}
    campaign = _synthetic_campaign()
    campaign["semantic_audit"] = {"sha256": "0" * 64}
    assert list(Draft202012Validator(schemas["atomic-v3-dataset-campaign-v1.json"]).iter_errors(campaign))


def test_semantic_ordered_key_set_rejects_duplicates_and_detects_intersection(
    tmp_path: Path,
) -> None:
    owner = tmp_path / "semantic-audit.json"
    duplicate_set = tmp_path / "duplicate.keys"
    duplicate_set.write_bytes(bytes((0x21,)) * 64)
    errors, _path, _digest, _metadata = VALIDATOR.validate_ordered_key_artifact(
        _blob(duplicate_set), owner, 2, "semantic.keys"
    )
    assert any("strictly increasing and unique" in error for error in errors)

    train = tmp_path / "train.keys"
    validation = tmp_path / "validation.keys"
    shared = bytes((0x42,)) * 32
    train.write_bytes(bytes((0x10,)) * 32 + shared)
    validation.write_bytes(shared + bytes((0x80,)) * 32)
    assert VALIDATOR.ordered_key_sets_intersect(train, validation) is True


def test_ordered_key_intersection_rejects_swap_after_authentication(
    tmp_path: Path,
) -> None:
    owner = tmp_path / "semantic-audit.json"
    train = tmp_path / "train.keys"
    validation = tmp_path / "validation.keys"
    train.write_bytes(bytes((0x10,)) * 32 + bytes((0x20,)) * 32)
    validation.write_bytes(bytes((0x80,)) * 32 + bytes((0x90,)) * 32)
    train_errors, train_path, _digest, train_metadata = (
        VALIDATOR.validate_ordered_key_artifact(
            _blob(train), owner, 2, "semantic.train.keys"
        )
    )
    validation_errors, validation_path, _digest, validation_metadata = (
        VALIDATOR.validate_ordered_key_artifact(
            _blob(validation), owner, 2, "semantic.validation.keys"
        )
    )
    assert not train_errors and not validation_errors
    assert train_path is not None and validation_path is not None
    assert train_metadata is not None and validation_metadata is not None
    replacement = tmp_path / "replacement.keys"
    replacement.write_bytes(bytes((0x30,)) * 32 + bytes((0x40,)) * 32)
    replacement.replace(train)
    with pytest.raises(ValueError, match="changed after authentication"):
        VALIDATOR.ordered_key_sets_intersect(
            train_path,
            validation_path,
            {train_path: train_metadata, validation_path: validation_metadata},
        )


def _duplicate_policy(
    path: Path,
    train_raw: int,
    train_features: int,
    validation_raw: int,
    validation_features: int,
) -> tuple[dict[str, Any], Path]:
    value = {
        "train": {
            "maximum_duplicate_raw_records_ppm": train_raw,
            "maximum_duplicate_feature_inputs_ppm": train_features,
        },
        "validation": {
            "maximum_duplicate_raw_records_ppm": validation_raw,
            "maximum_duplicate_feature_inputs_ppm": validation_features,
        },
    }
    path.write_text(json.dumps(value, separators=(",", ":")) + "\n", encoding="utf-8")
    return {"coverage_policy": _blob(path)}, path


def test_campaign_duplicate_limits_must_be_homogeneous(tmp_path: Path) -> None:
    first_chunk, first_path = _duplicate_policy(
        tmp_path / "policy-0.json", 1000, 2000, 3000, 4000
    )
    second_chunk, second_path = _duplicate_policy(
        tmp_path / "policy-1.json", 1000, 2001, 3000, 4000
    )
    errors, limits = VALIDATOR.load_campaign_duplicate_limits(
        {"chunks": [first_chunk, second_chunk]},
        {
            "campaign.chunks[0].coverage_policy": first_path,
            "campaign.chunks[1].coverage_policy": second_path,
        },
    )
    assert limits is None
    assert any("must equal the duplicate ceiling" in error for error in errors)


def test_global_raw_duplicate_ppm_catches_cross_chunk_repetition(
    tmp_path: Path,
) -> None:
    identity_index = VALIDATOR.SplitGroupIndex(tmp_path)
    try:
        chunk_zero = (bytes((0x10,)) * 32, bytes((0x20,)) * 32)
        chunk_one = (bytes((0x20,)) * 32, bytes((0x30,)) * 32)
        assert len(set(chunk_zero)) == len(chunk_zero)
        assert len(set(chunk_one)) == len(chunk_one)
        for key in chunk_zero + chunk_one:
            identity_index.add_raw_record(0, key)
        for key in (bytes((0x80,)) * 32, bytes((0x90,)) * 32):
            identity_index.add_raw_record(1, key)
        identity_index.finish_role()
        errors = VALIDATOR.validate_campaign_raw_duplicate_limits(
            {"train_records": "4", "validation_records": "2"},
            identity_index,
            {
                "train": {"raw_records": 249999},
                "validation": {"raw_records": 0},
            },
        )
    finally:
        identity_index.close()
    assert any("campaign.train.raw_record_keys" in error for error in errors)
    assert any("250000" in error and "249999" in error for error in errors)


def test_global_feature_duplicate_ppm_accepts_boundary_and_rejects_above() -> None:
    semantic_audit = {
        "roles": {
            "train": {
                "feature_input_keys": {"observations": "10", "unique_keys": "9"}
            },
            "validation": {
                "feature_input_keys": {"observations": "10", "unique_keys": "10"}
            },
        }
    }
    exact_limits = {
        "train": {"feature_inputs": 100000},
        "validation": {"feature_inputs": 0},
    }
    assert (
        VALIDATOR.validate_campaign_feature_duplicate_limits(
            semantic_audit, exact_limits
        )
        == []
    )
    above_errors = VALIDATOR.validate_campaign_feature_duplicate_limits(
        semantic_audit,
        {
            "train": {"feature_inputs": 99999},
            "validation": {"feature_inputs": 0},
        },
    )
    assert any("100000" in error and "99999" in error for error in above_errors)
