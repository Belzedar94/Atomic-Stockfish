from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
from pathlib import Path
import struct
import sys
from types import SimpleNamespace
from typing import Any, Mapping

import pytest


TESTS_DIR = Path(__file__).resolve().parents[1]
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))

import atomic_bin_v2_pipeline_e2e as gate


COMMIT = "a" * 40
CONTRACT_COMMIT = "b" * 40
SHA256 = "c" * 64


def config(tmp_path: Path, **overrides: object) -> gate.GateConfig:
    values: dict[str, object] = {
        "atomic": gate.CheckoutSpec(
            "Atomic", tmp_path / "atomic", COMMIT, "refs/heads/gate", gate.ATOMIC_REPOSITORY
        ),
        "tools": gate.CheckoutSpec(
            "tools", tmp_path / "tools", "d" * 40, "refs/heads/atomic", gate.TOOLS_REPOSITORY
        ),
        "trainer": gate.CheckoutSpec(
            "trainer", tmp_path / "trainer", "e" * 40, "refs/heads/atomic", gate.TRAINER_REPOSITORY
        ),
        "contract_engine_commit": CONTRACT_COMMIT,
        "engine": tmp_path / "atomic" / "src" / "engine.exe",
        "engine_sha256": SHA256,
        "data_generator": tmp_path / "atomic" / "src" / "generator.exe",
        "data_generator_sha256": SHA256,
        "data_tools": tmp_path / "atomic" / "src" / "data-tools.exe",
        "data_tools_sha256": SHA256,
        "tools_wrapper": tmp_path / "tools" / "script" / "atomic_bin_v2_tools.py",
        "wrapper_data_tools": (
            tmp_path
            / "tools"
            / "engine"
            / "Atomic-Stockfish"
            / "src"
            / (
                "atomic-stockfish-data-tools.exe"
                if os.name == "nt"
                else "atomic-stockfish-data-tools"
            )
        ),
        "wrapper_data_tools_sha256": SHA256,
        "trainer_loader": tmp_path / "trainer" / "build" / "nnue_dataset.dll",
        "trainer_loader_sha256": SHA256,
        "train_script": tmp_path / "trainer" / "train.py",
        "serialize_script": tmp_path / "trainer" / "serialize.py",
        "python": Path(sys.executable),
        "python_sha256": gate.sha256_file(Path(sys.executable)),
        "source_net": tmp_path / "networks" / "arbitrary-compatible-name.nnue",
        "source_net_sha256": "f" * 64,
        "output_dir": tmp_path / "audit",
        "train_seed": 2026071301,
        "validation_seed": 2026071302,
        "timeout_seconds": 60.0,
    }
    values.update(overrides)
    return gate.GateConfig(**values)  # type: ignore[arg-type]


def exact_options() -> dict[str, object]:
    return {
        "search_depth_min": gate.GENERATOR_DEPTH,
        "search_depth_max": gate.GENERATOR_DEPTH,
        "nodes": "0",
        "requested_records": str(gate.RECORDS),
        "records_per_shard": str(gate.RECORDS_PER_SHARD),
        "eval_limit": gate.GENERATOR_EVAL_LIMIT,
        "eval_diff_limit": 64000,
        "random_move_min_ply": 1,
        "random_move_max_ply": 24,
        "random_move_count": 5,
        "random_move_like_apery": 0,
        "random_multi_pv": 5,
        "random_multi_pv_diff": 100,
        "random_multi_pv_depth": gate.GENERATOR_DEPTH,
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


def manifest_fixture(root: Path, cfg: gate.GateConfig) -> tuple[Path, dict[str, Any]]:
    root.mkdir(parents=True)
    shards: list[dict[str, object]] = []
    for index, name in enumerate(("train.atbin", "train_1.atbin")):
        payload = bytes([index + 1]) * (96 + 64 * gate.RECORDS_PER_SHARD)
        path = root / name
        path.write_bytes(payload)
        shards.append(
            {
                "index": index,
                "file": name,
                "records": str(gate.RECORDS_PER_SHARD),
                "bytes": str(len(payload)),
                "sha256": hashlib.sha256(payload).hexdigest(),
            }
        )
    value: dict[str, Any] = {
        "manifest_version": 1,
        "manifest_schema_sha256": gate.MANIFEST_SCHEMA_SHA256,
        "data_schema_sha256": gate.DATA_SCHEMA_SHA256,
        "format": "atomic-bin-v2",
        "engine": {"commit": cfg.atomic.commit, "version": "Atomic-Stockfish fixture"},
        "network": {"file": cfg.source_net.name, "sha256": cfg.source_net_sha256},
        "book": {"kind": "builtin-startpos", "file": None, "sha256": None},
        "generation": {
            "resolved_seed": str(cfg.train_seed),
            "atomic960": False,
            "threads": 1,
            "hash_mb": str(gate.GENERATOR_HASH_MB),
            "use_nnue": "pure",
            "options": exact_options(),
        },
        "statistics": {"records": str(gate.RECORDS), "draws": "20"},
        "shards": shards,
    }
    path = root / "train.atbin.manifest.json"
    path.write_bytes(gate.canonical_json_preserving_order(value))
    return path, value


def validation_fixture() -> dict[str, object]:
    return {
        "type": "atomic-data-tools-validation",
        "contract_version": 1,
        "status": "ok",
        "format": "atomic-bin-v2",
        "entrypoint": "manifest",
        "shards": gate.SHARDS,
        "records": str(gate.RECORDS),
        "side_to_move_wins": "40",
        "draws": "20",
        "side_to_move_losses": "68",
        "atomic960_records": "0",
    }


def decode_fixture(manifest: Mapping[str, Any], validation: Mapping[str, Any]) -> bytes:
    values: list[Mapping[str, object]] = [
        {
            "type": "atomic-data-tools-decode-header",
            "contract_version": 1,
            "status": "ok",
            "format": "atomic-bin-v2",
            "entrypoint": "manifest",
            "decode_schema_sha256": gate.DECODE_SCHEMA_SHA256,
            "data_schema_sha256": gate.DATA_SCHEMA_SHA256,
            "manifest_schema_sha256": gate.MANIFEST_SCHEMA_SHA256,
            "slice": {"offset": "0", "limit": gate.RECORDS},
            "dataset": {"records": str(gate.RECORDS), "shards": gate.SHARDS, "atomic960": False},
            "provenance": {
                "engine": manifest["engine"],
                "network": manifest["network"],
                "book": manifest["book"],
                "generation": manifest["generation"],
            },
        }
    ]
    for index in range(gate.RECORDS):
        values.append(
            {
                "type": "atomic-data-tools-decode-record",
                "contract_version": 1,
                "global_index": str(index),
                "shard_index": str(index // gate.RECORDS_PER_SHARD),
                "local_index": str(index % gate.RECORDS_PER_SHARD),
                "atomic960": False,
                "flags": 0,
                "position": {"side_to_move": "white", "pieces": []},
                "move": {"from": "a2", "to": "a3", "type": "normal"},
            }
        )
    values.append(
        {
            "type": "atomic-data-tools-decode-footer",
            "contract_version": 1,
            "status": "ok",
            "format": "atomic-bin-v2",
            "slice": {"offset": "0", "limit": gate.RECORDS, "records": str(gate.RECORDS)},
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
    )
    return b"".join(gate.canonical_json_preserving_order(value) for value in values)


@pytest.mark.parametrize("value", ("0", "1", str(2**64 - 1)))
def test_uint64_parser_accepts_canonical_domain(value: str) -> None:
    assert gate.parse_uint64(value) == int(value)


@pytest.mark.parametrize("value", ("", "00", "+1", "-1", "1.0", str(2**64)))
def test_uint64_parser_rejects_noncanonical_or_out_of_range(value: str) -> None:
    with pytest.raises(argparse.ArgumentTypeError):
        gate.parse_uint64(value)


@pytest.mark.parametrize(
    ("url", "repository"),
    (
        ("https://github.com/Belzedar94/Atomic-Stockfish.git", gate.ATOMIC_REPOSITORY),
        ("git@github.com:Belzedar94/variant-nnue-tools.git", gate.TOOLS_REPOSITORY),
        ("ssh://github.com/Belzedar94/Atomic-Stockfish", None),
    ),
)
def test_github_remote_normalization(url: str, repository: str | None) -> None:
    assert gate.normalize_github_repository(url) == repository


def test_run_raw_supports_binary_stdin_without_conflicting_stdin_argument() -> None:
    completed = gate.run_raw(
        (sys.executable, "-B", "-c", "import sys;sys.stdout.buffer.write(sys.stdin.buffer.read())"),
        input_bytes=b"atomic\x00dataset\n",
    )
    assert completed.returncode == 0
    assert completed.stdout == b"atomic\x00dataset\n"
    assert completed.stderr == b""


def test_archive_is_exclusive_and_rejects_whitespace(tmp_path: Path) -> None:
    archive = gate.Archive(tmp_path / "audit")
    assert archive.write("evidence.bin", b"one").read_bytes() == b"one"
    with pytest.raises(gate.GateError, match="refuses overwrite"):
        archive.write("evidence.bin", b"two")
    with pytest.raises(gate.GateError, match="whitespace"):
        gate.Archive(tmp_path / "contains space" / "audit")


def test_generation_wire_freezes_all_data_affecting_options_and_accepts_arbitrary_net_name(
    tmp_path: Path,
) -> None:
    cfg = config(tmp_path)
    stem = tmp_path / "output" / "train"
    command = gate.generation_command(stem, cfg.train_seed)
    for fragment in (
        "count 128",
        "save_every 64",
        "eval_limit 32000",
        "filter_captures true",
        "filter_checks false",
        "filter_promotions true",
        "adjudicate_draws_by_insufficient_material true",
        f"seed {cfg.train_seed}",
    ):
        assert fragment in command
    wire = gate.generator_stdin(cfg, stem, cfg.train_seed).decode("utf-8")
    assert f"setoption name EvalFile value {cfg.source_net.resolve()}" in wire
    assert "setoption name Use NNUE value pure" in wire
    assert wire.endswith("quit\n")


def test_training_command_is_exactly_one_cpu_update_without_skipping(tmp_path: Path) -> None:
    cfg = config(tmp_path)
    command = gate.training_command(
        cfg, tmp_path / "train.json", tmp_path / "val.json", tmp_path / "out"
    )
    assert "--accelerator=cpu" in command
    assert "--devices=1" in command
    assert "--batch-size=96" in command
    assert "--epoch-size=96" in command
    assert "--validation-size=96" in command
    assert "--max_epochs=1" in command
    assert "--no-smart-fen-skipping" in command
    assert "--random-fen-skipping=0" in command
    assert f"--resume-from-model={cfg.source_net.resolve()}" in command


def test_manifest_gate_accepts_nonstandard_network_filename_and_two_exact_shards(
    tmp_path: Path,
) -> None:
    cfg = config(tmp_path)
    path, expected = manifest_fixture(tmp_path / "dataset", cfg)
    actual, shards, digests = gate.validate_manifest(path, config=cfg, seed=cfg.train_seed)
    assert actual == expected
    assert tuple(item.name for item in shards) == ("train.atbin", "train_1.atbin")
    assert len(set(digests)) == 2


@pytest.mark.parametrize(
    ("path", "value", "message"),
    (
        (("generation", "options", "filter_checks"), True, "filter_checks"),
        (("generation", "options", "filter_promotions"), False, "filter_promotions"),
        (("generation", "use_nnue"), "true", "Use NNUE=pure"),
        (("network", "sha256"), "0" * 64, "network digest"),
        (("generation", "resolved_seed"), "9", "seed"),
    ),
)
def test_manifest_gate_rejects_policy_drift(
    tmp_path: Path, path: tuple[str, ...], value: object, message: str
) -> None:
    cfg = config(tmp_path)
    manifest_path, manifest = manifest_fixture(tmp_path / "dataset", cfg)
    target: dict[str, Any] = manifest
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    manifest_path.write_bytes(gate.canonical_json_preserving_order(manifest))
    with pytest.raises(gate.GateError, match=message):
        gate.validate_manifest(manifest_path, config=cfg, seed=cfg.train_seed)


@pytest.mark.parametrize("option", tuple(exact_options()))
def test_manifest_gate_rejects_drift_in_every_effective_generation_option(
    tmp_path: Path, option: str
) -> None:
    cfg = config(tmp_path)
    manifest_path, manifest = manifest_fixture(tmp_path / "dataset", cfg)
    current = manifest["generation"]["options"][option]
    if type(current) is bool:
        replacement: object = not current
    elif type(current) is int:
        replacement = current + 1
    else:
        replacement = "999" if current != "999" else "998"
    manifest["generation"]["options"][option] = replacement
    manifest_path.write_bytes(gate.canonical_json_preserving_order(manifest))
    with pytest.raises(gate.GateError, match=option):
        gate.validate_manifest(manifest_path, config=cfg, seed=cfg.train_seed)


def test_manifest_gate_rejects_missing_or_unknown_generation_options(tmp_path: Path) -> None:
    cfg = config(tmp_path)
    manifest_path, manifest = manifest_fixture(tmp_path / "dataset", cfg)
    del manifest["generation"]["options"]["nodes"]
    manifest["generation"]["options"]["future_option"] = 1
    manifest_path.write_bytes(gate.canonical_json_preserving_order(manifest))
    with pytest.raises(gate.GateError, match="missing or unknown"):
        gate.validate_manifest(manifest_path, config=cfg, seed=cfg.train_seed)


def test_validation_summary_reconciles_wdl_and_rejects_drift() -> None:
    value = validation_fixture()
    payload = gate.canonical_json_preserving_order(value)
    assert gate.validate_success_json(payload, "fixture") == value
    bad = dict(value)
    bad["draws"] = "19"
    with pytest.raises(gate.GateError, match="WDL totals"):
        gate.validate_success_json(gate.canonical_json_preserving_order(bad), "fixture")


def test_decode_gate_authenticates_provenance_indexes_and_footer(tmp_path: Path) -> None:
    cfg = config(tmp_path)
    _, manifest = manifest_fixture(tmp_path / "dataset", cfg)
    validation = validation_fixture()
    payload = decode_fixture(manifest, validation)
    gate.validate_decode_jsonl(payload, manifest=manifest, validation=validation)

    lines = payload.splitlines()
    record = json.loads(lines[1])
    record["global_index"] = "1"
    lines[1] = gate.canonical_json_preserving_order(record).rstrip(b"\n")
    with pytest.raises(gate.GateError, match="global index"):
        gate.validate_decode_jsonl(
            b"\n".join(lines) + b"\n", manifest=manifest, validation=validation
        )


def test_checkpoint_metadata_requires_exactly_one_update() -> None:
    assert gate.parse_checkpoint_metadata(b'{"global_step":1,"epoch":0}\n') == {
        "global_step": 1,
        "epoch": 0,
    }
    with pytest.raises(gate.GateError, match="expected 1"):
        gate.parse_checkpoint_metadata(b'{"global_step":2,"epoch":0}\n')
    with pytest.raises(gate.GateError, match="global_step"):
        gate.parse_checkpoint_metadata(b'{"global_step":true,"epoch":0}\n')


def test_nnue_header_checks_version_and_architecture_not_filename(tmp_path: Path) -> None:
    path = tmp_path / "definitely-not-the-playing-net-name.bin"
    path.write_bytes(
        struct.pack("<III", gate.LEGACY_NNUE_VERSION, gate.LEGACY_NNUE_ARCHITECTURE, 0)
    )
    assert gate.verify_nnue_header(path) == (
        gate.LEGACY_NNUE_VERSION,
        gate.LEGACY_NNUE_ARCHITECTURE,
    )
    path.write_bytes(struct.pack("<III", gate.LEGACY_NNUE_VERSION, 0, 0))
    with pytest.raises(gate.GateError, match="architecture"):
        gate.verify_nnue_header(path)


def trainer_capability() -> dict[str, object]:
    return {
        "capability_version": 2,
        "formats": {
            "legacy-atomic-v1": {
                "schema_sha256": gate.LEGACY_SCHEMA_SHA256,
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
                "schema_sha256": gate.DATA_SCHEMA_SHA256,
                "manifest_schema_sha256": gate.MANIFEST_SCHEMA_SHA256,
            },
        },
    }


class CapabilityArchive:
    def __init__(self, cfg: gate.GateConfig, *, corrupt_wrapper: bool = False) -> None:
        self.cfg = cfg
        self.corrupt_wrapper = corrupt_wrapper

    def run(self, label: str, argv: object, *, cwd: Path, timeout: float) -> gate.CommandResult:
        del argv, timeout
        if label == "trainer capabilities":
            payload = gate.canonical_json_preserving_order(
                {"dll": str(self.cfg.trainer_loader.resolve()), "capability": trainer_capability()}
            )
        else:
            payload = gate.expected_data_tools_capabilities_bytes()
            if label == "wrapper data-tools capabilities" and self.corrupt_wrapper:
                payload = payload.replace(b'"decode"', b'"other"')
        return gate.CommandResult(label, (), str(cwd), 0, payload, b"")


def test_capability_handshake_is_byte_exact_across_direct_wrapper_and_trainer(
    tmp_path: Path,
) -> None:
    cfg = config(tmp_path)
    capabilities = gate.verify_capabilities(cfg, CapabilityArchive(cfg))  # type: ignore[arg-type]
    assert capabilities["data_tools"] == gate.expected_data_tools_capabilities()
    assert capabilities["trainer"] == trainer_capability()
    with pytest.raises(gate.GateError, match="wrapper data-tools capabilities"):
        gate.verify_capabilities(
            cfg, CapabilityArchive(cfg, corrupt_wrapper=True)  # type: ignore[arg-type]
        )


def write_artifact(path: Path, payload: bytes) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    return hashlib.sha256(payload).hexdigest()


def preflight_fixture(tmp_path: Path) -> gate.GateConfig:
    cfg = config(tmp_path)
    for root in (cfg.atomic.root, cfg.tools.root, cfg.trainer.root):
        root.mkdir(parents=True)
    wrapper_engine = cfg.tools.root / "engine" / "Atomic-Stockfish"
    trainer_engine = cfg.trainer.root / "external" / "Atomic-Stockfish"
    wrapper_engine.mkdir(parents=True)
    trainer_engine.mkdir(parents=True)
    hashes = {
        "engine_sha256": write_artifact(cfg.engine, b"engine"),
        "data_generator_sha256": write_artifact(cfg.data_generator, b"generator"),
        "data_tools_sha256": write_artifact(cfg.data_tools, b"direct tools"),
        "wrapper_data_tools_sha256": write_artifact(cfg.wrapper_data_tools, b"wrapper tools"),
        "trainer_loader_sha256": write_artifact(cfg.trainer_loader, b"loader"),
        "source_net_sha256": write_artifact(
            cfg.source_net,
            struct.pack("<III", gate.LEGACY_NNUE_VERSION, gate.LEGACY_NNUE_ARCHITECTURE, 0),
        ),
    }
    write_artifact(cfg.tools_wrapper, b"wrapper script")
    write_artifact(cfg.train_script, b"train script")
    write_artifact(cfg.serialize_script, b"serialize script")
    return config(tmp_path, **hashes)


def test_preflight_authenticates_every_executed_artifact_and_canonical_wrapper_child(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = preflight_fixture(tmp_path)

    def checkout(spec: gate.CheckoutSpec) -> gate.CheckoutState:
        return gate.CheckoutState(
            spec.label, str(spec.root.resolve()), spec.commit, spec.ref, spec.repository
        )

    def submodule(parent: Path, relative: str, commit: str, *, label: str) -> Path:
        del commit, label
        return (parent / relative).resolve()

    monkeypatch.setattr(gate, "verify_checkout", checkout)
    monkeypatch.setattr(gate, "verify_submodule_pin", submodule)
    monkeypatch.setattr(gate, "verify_schema_tree", lambda root, label: None)
    monkeypatch.setattr(gate, "verify_tools_lock", lambda root, commit: None)
    state = gate.preflight(cfg)
    assert set(state.fingerprints) == {
        "engine",
        "data_generator",
        "data_tools",
        "wrapper_data_tools",
        "trainer_loader",
        "source_net",
        "tools_wrapper",
        "train_script",
        "serialize_script",
        "python",
    }
    alien = write_artifact(tmp_path / "alien-tools.exe", b"wrapper tools")
    with pytest.raises(gate.GateError, match="canonical pinned artifact"):
        gate.preflight(
            config(
                tmp_path,
                **{
                    **{
                        name: getattr(cfg, name)
                        for name in (
                            "engine_sha256",
                            "data_generator_sha256",
                            "data_tools_sha256",
                            "trainer_loader_sha256",
                            "source_net_sha256",
                        )
                    },
                    "wrapper_data_tools": tmp_path / "alien-tools.exe",
                    "wrapper_data_tools_sha256": alien,
                },
            )
        )


def test_public_archive_redacts_checkout_artifact_network_and_python_paths(tmp_path: Path) -> None:
    cfg = config(tmp_path)
    archive = gate.Archive(cfg.output_dir, gate.archive_redactions(cfg))
    archive.write_json(
        "provenance.json",
        {
            "checkout": str(cfg.atomic.root.resolve()),
            "fingerprint": str(cfg.engine.resolve()),
            "python": str(cfg.python.resolve()),
            "network": str(cfg.source_net.resolve()),
            "private": str(archive.private_root),
        },
    )
    archive.write_log(
        "logs/generator.stdin.log",
        gate.generator_stdin(cfg, archive.path("datasets/train/train"), cfg.train_seed),
    )
    payload = (archive.root / "provenance.json").read_text(encoding="utf-8")
    assert "<ATOMIC_ROOT>" in payload
    assert "<PLAYING_ENGINE>" in payload
    assert "<PYTHON>" in payload
    assert "<SOURCE_NET>" in payload
    assert "<PRIVATE_WORK>" in payload
    combined = "\n".join(
        path.read_text(encoding="utf-8")
        for path in archive.root.rglob("*")
        if path.is_file()
    )
    assert str(tmp_path.resolve()).casefold() not in combined.casefold()
    gate.verify_public_text_redaction(archive)
    archive.cleanup_private()


def test_public_archive_scan_rejects_unknown_absolute_host_path(tmp_path: Path) -> None:
    archive = gate.Archive(tmp_path / "audit")
    archive.write_log("logs/leak.log", b'leak "C:\\Users\\example\\secret.nnue"\n')
    with pytest.raises(gate.GateError, match="unredacted absolute path"):
        gate.verify_public_text_redaction(archive)
    archive.cleanup_private()


def namespace(tmp_path: Path, **overrides: object) -> SimpleNamespace:
    values: dict[str, object] = {
        "atomic_root": tmp_path / "atomic",
        "atomic_commit": COMMIT,
        "atomic_ref": "refs/heads/gate",
        "tools_root": tmp_path / "tools",
        "tools_commit": "d" * 40,
        "tools_ref": "refs/heads/atomic",
        "trainer_root": tmp_path / "trainer",
        "trainer_commit": "e" * 40,
        "trainer_ref": "refs/heads/atomic",
        "contract_engine_commit": CONTRACT_COMMIT,
        "engine": tmp_path / "engine",
        "engine_sha256": SHA256,
        "data_generator": tmp_path / "generator",
        "data_generator_sha256": SHA256,
        "data_tools": tmp_path / "tools-bin",
        "data_tools_sha256": SHA256,
        "tools_wrapper": tmp_path / "wrapper.py",
        "wrapper_data_tools": tmp_path / "wrapper-tools",
        "wrapper_data_tools_sha256": SHA256,
        "trainer_loader": tmp_path / "loader",
        "trainer_loader_sha256": SHA256,
        "train_script": tmp_path / "train.py",
        "serialize_script": tmp_path / "serialize.py",
        "python": Path(sys.executable),
        "python_sha256": gate.sha256_file(Path(sys.executable)),
        "source_net": tmp_path / "network.nnue",
        "source_net_sha256": SHA256,
        "output_dir": tmp_path / "output",
        "train_seed": 1,
        "validation_seed": 2,
        "timeout_seconds": 60.0,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_cli_config_requires_exact_hashes_commits_and_distinct_seeds(tmp_path: Path) -> None:
    cfg = gate.config_from_args(namespace(tmp_path))
    assert cfg.atomic.repository == gate.ATOMIC_REPOSITORY
    assert cfg.tools.repository == gate.TOOLS_REPOSITORY
    assert cfg.trainer.repository == gate.TRAINER_REPOSITORY
    assert cfg.train_seed == 1 and cfg.validation_seed == 2
    with pytest.raises(gate.GateError, match="seeds must differ"):
        gate.config_from_args(namespace(tmp_path, validation_seed=1))
    with pytest.raises(gate.GateError, match="lowercase 40-character"):
        gate.config_from_args(namespace(tmp_path, atomic_commit="A" * 40))
    with pytest.raises(gate.GateError, match="lowercase SHA-256"):
        gate.config_from_args(namespace(tmp_path, python_sha256="C" * 64))


def test_fingerprints_must_remain_identical_after_gate() -> None:
    checkout = {"atomic": gate.CheckoutState("Atomic", "/a", COMMIT, "ref", gate.ATOMIC_REPOSITORY)}
    before = gate.Preflight(checkout, {"engine": gate.Fingerprint("engine", "/e", 1, SHA256)})
    gate.fingerprints_equal(before, before)
    after = gate.Preflight(checkout, {"engine": gate.Fingerprint("engine", "/e", 2, SHA256)})
    with pytest.raises(gate.GateError, match="input artifact changed"):
        gate.fingerprints_equal(before, after)
