from __future__ import annotations

import argparse
import copy
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import stat as stat_module
import struct
import subprocess
import sys
from types import SimpleNamespace
from typing import Any, Mapping

import pytest


TESTS_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = TESTS_DIR.parent
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))

import atomic_bin_v2_pipeline_e2e as gate


COMMIT = "a" * 40
TOOLS_COMMIT = gate.NORMATIVE_TOOLS_COMMIT
TOOLS_ENGINE_COMMIT = gate.NORMATIVE_TOOLS_ENGINE_COMMIT
TRAINER_COMMIT = gate.NORMATIVE_TRAINER_COMMIT
TRAINER_ENGINE_COMMIT = gate.NORMATIVE_TRAINER_ENGINE_COMMIT
SHA256 = "c" * 64
DELETE = object()


class FakeScandir:
    def __init__(self, entries: list[object]):
        self.entries = entries

    def __enter__(self) -> object:
        return iter(self.entries)

    def __exit__(self, *unused: object) -> None:
        return None


class FakeDirEntry:
    def __init__(self, name: str, stat_result: object | BaseException):
        self.name = name
        self.stat_result = stat_result
        self.is_dir_calls = 0

    def stat(self, *, follow_symlinks: bool = True) -> object:
        assert follow_symlinks is False
        if isinstance(self.stat_result, BaseException):
            raise self.stat_result
        return self.stat_result

    def is_dir(self, *, follow_symlinks: bool = True) -> bool:
        self.is_dir_calls += 1
        raise AssertionError("safe walker must classify the lstat result, not call is_dir")


def invoke_public_tree_consumer(operation: str, archive: gate.Archive) -> object:
    if operation == "sanitize":
        return archive.sanitize_public_text()
    if operation == "verify":
        return gate.verify_public_text_redaction(archive)
    if operation == "discard":
        return gate.discard_public_text_evidence(archive)
    if operation == "inventory":
        return gate.inventory_archive(archive)
    raise AssertionError(f"unknown public tree consumer {operation}")


def create_directory_link(link: Path, target: Path) -> None:
    if os.name == "nt":
        subprocess.run(
            ("cmd.exe", "/d", "/c", "mklink", "/J", str(link), str(target)),
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        assert not link.is_symlink()
        assert gate.is_reparse_point(link)
        return
    link.symlink_to(target, target_is_directory=True)
    assert link.is_symlink()


def remove_directory_link(link: Path) -> None:
    if os.name == "nt":
        os.rmdir(link)
    else:
        link.unlink()


def config(tmp_path: Path, **overrides: object) -> gate.GateConfig:
    values: dict[str, object] = {
        "atomic": gate.CheckoutSpec(
            "Atomic", tmp_path / "atomic", COMMIT, "refs/heads/gate", gate.ATOMIC_REPOSITORY
        ),
        "tools": gate.CheckoutSpec(
            "tools", tmp_path / "tools", TOOLS_COMMIT, "refs/heads/atomic", gate.TOOLS_REPOSITORY
        ),
        "trainer": gate.CheckoutSpec(
            "trainer",
            tmp_path / "trainer",
            TRAINER_COMMIT,
            "refs/heads/atomic",
            gate.TRAINER_REPOSITORY,
        ),
        "profile": "strong-local",
        "tools_engine_commit": TOOLS_ENGINE_COMMIT,
        "trainer_engine_commit": TRAINER_ENGINE_COMMIT,
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
        "trainer_loader": (
            tmp_path
            / "trainer"
            / (
                "training_data_loader.dll"
                if sys.platform == "win32"
                else (
                    "libtraining_data_loader.dylib"
                    if sys.platform == "darwin"
                    else "libtraining_data_loader.so"
                )
            )
        ),
        "trainer_loader_sha256": SHA256,
        "train_script": tmp_path / "trainer" / "train.py",
        "serialize_script": tmp_path / "trainer" / "serialize.py",
        "python": Path(sys.executable).resolve(),
        "python_sha256": gate.sha256_file(Path(sys.executable).resolve()),
        "source_net": tmp_path / "networks" / "arbitrary-compatible-name.nnue",
        "source_net_sha256": gate.NORMATIVE_STRONG_LOCAL_SOURCE_NET_SHA256,
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
        payload = bytearray(bytes([index + 1]) * (96 + 64 * gate.RECORDS_PER_SHARD))
        for local_index in range(gate.RECORDS_PER_SHARD):
            struct.pack_into("<I", payload, 96 + local_index * 64 + 52, 0x70C)
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


def raw_move_wires() -> tuple[int, ...]:
    return (0x70C,) * gate.RECORDS


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
        if index < 40:
            result_stm = 1
        elif index < 60:
            result_stm = 0
        else:
            result_stm = -1
        values.append(
            {
                "type": "atomic-data-tools-decode-record",
                "contract_version": 1,
                "global_index": str(index),
                "shard_index": str(index // gate.RECORDS_PER_SHARD),
                "local_index": str(index % gate.RECORDS_PER_SHARD),
                "position": {
                    "fen": "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1",
                    "fen_notation": "fen",
                    "side_to_move": "white",
                    "rule50": 0,
                    "fullmove": 1,
                    "castling_rights": {"wire": 15, "fen": "KQkq"},
                    "castling_rook_origins": {
                        "white_kingside": "h1",
                        "white_queenside": "a1",
                        "black_kingside": "h8",
                        "black_queenside": "a8",
                    },
                    "en_passant": None,
                },
                "score_stm": 0,
                "ply": str(index),
                "result_stm": result_stm,
                "flags": 0,
                "atomic960": False,
                "move": {
                    "wire": str(0x70C),
                    "from": "e2",
                    "to": "e4",
                    "type": "normal",
                    "promotion": "none",
                },
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


def mutate_decode_record(
    payload: bytes,
    path: tuple[str, ...],
    value: object,
    *,
    record_index: int = 0,
) -> bytes:
    lines = payload.splitlines()
    record = json.loads(lines[record_index + 1])
    target = record
    for key in path[:-1]:
        target = target[key]
    if value is DELETE:
        del target[path[-1]]
    else:
        target[path[-1]] = value
    lines[record_index + 1] = gate.canonical_json_preserving_order(record).rstrip(b"\n")
    return b"\n".join(lines) + b"\n"


@pytest.mark.parametrize("value", ("0", "1", str(2**64 - 1)))
def test_uint64_parser_accepts_canonical_domain(value: str) -> None:
    assert gate.parse_uint64(value) == int(value)


@pytest.mark.parametrize("value", ("", "00", "+1", "-1", "1.0", str(2**64)))
def test_uint64_parser_rejects_noncanonical_or_out_of_range(value: str) -> None:
    with pytest.raises(argparse.ArgumentTypeError):
        gate.parse_uint64(value)


@pytest.mark.parametrize("value", ("1", str(2**64 - 1)))
def test_generator_seed_parser_accepts_nonzero_uint64(value: str) -> None:
    assert gate.parse_generator_seed(value) == int(value)


@pytest.mark.parametrize("value", ("0", str(2**64)))
def test_generator_seed_parser_rejects_zero_or_overflow(value: str) -> None:
    with pytest.raises(argparse.ArgumentTypeError):
        gate.parse_generator_seed(value)


@pytest.mark.parametrize("value", ("1", str(2**32 - 1)))
def test_training_seed_parser_accepts_nonzero_uint32(value: str) -> None:
    assert gate.parse_training_seed(value) == int(value)


@pytest.mark.parametrize("value", ("0", str(2**32)))
def test_training_seed_parser_rejects_zero_or_overflow(value: str) -> None:
    with pytest.raises(argparse.ArgumentTypeError):
        gate.parse_training_seed(value)


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


def test_owned_python_json_probe_emits_frozen_lf_bytes_on_windows() -> None:
    probe = "import json,sys;" + gate.python_json_line_statement("{'value':'atomic'}")
    completed = gate.run_raw((sys.executable, "-B", "-c", probe))
    assert completed.returncode == 0
    assert completed.stdout == b'{"value":"atomic"}\n'
    assert b"\r" not in completed.stdout
    result = gate.CommandResult("probe", (), str(Path.cwd()), 0, b'{}\r\n', b"")
    with pytest.raises(gate.GateError, match="CR bytes"):
        gate.require_clean_success(result, "probe")


def test_archive_is_exclusive_and_rejects_whitespace(tmp_path: Path) -> None:
    archive = gate.Archive(tmp_path / "audit")
    assert archive.write("evidence.bin", b"one").read_bytes() == b"one"
    with pytest.raises(gate.GateError, match="refuses overwrite"):
        archive.write("evidence.bin", b"two")
    with pytest.raises(gate.GateError, match="whitespace"):
        gate.Archive(tmp_path / "contains space" / "audit")


def test_serializer_stages_privately_and_refuses_precreated_public_candidate(
    tmp_path: Path,
) -> None:
    cfg = config(tmp_path)

    class SerializationArchive(gate.Archive):
        def run(
            self,
            label: str,
            argv: object,
            *,
            cwd: Path,
            timeout: float,
            **unused: object,
        ) -> gate.CommandResult:
            del timeout, unused
            command = tuple(argv)  # type: ignore[arg-type]
            target = Path(command[4])
            if label == "reimport candidate":
                target.write_bytes(b"private imported model")
            else:
                target.write_bytes(
                    struct.pack(
                        "<III", gate.LEGACY_NNUE_VERSION, gate.LEGACY_NNUE_ARCHITECTURE, 0
                    )
                )
            return gate.CommandResult(label, command, str(cwd), 0, b"", b"")

    archive = SerializationArchive(cfg.output_dir)
    archive.write("networks/candidate.nnue", b"occupied")
    checkpoint = tmp_path / "checkpoint.ckpt"
    checkpoint.write_bytes(b"checkpoint")
    with pytest.raises(gate.GateError, match="refuses overwrite"):
        gate.serialize_and_reimport(cfg, archive, checkpoint)
    assert (archive.root / "networks" / "candidate.nnue").read_bytes() == b"occupied"
    archive.cleanup_private()


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


@pytest.mark.parametrize("count", (0, 1, 3))
def test_trainer_policy_requires_exactly_two_authenticated_manifest_lines(count: int) -> None:
    output = "\n".join([gate.TRAINER_POLICY_MARKER] * count)
    with pytest.raises(gate.GateError, match="train and validation manifest policy"):
        gate.validate_trainer_policy_output(output)
    gate.validate_trainer_policy_output(
        f"prefix\n{gate.TRAINER_POLICY_MARKER}\n{gate.TRAINER_POLICY_MARKER}\nsuffix\n"
    )


def test_manifest_gate_accepts_nonstandard_network_filename_and_two_exact_shards(
    tmp_path: Path,
) -> None:
    cfg = config(tmp_path)
    path, expected = manifest_fixture(tmp_path / "dataset", cfg)
    actual, shards, digests = gate.validate_manifest(path, config=cfg, seed=cfg.train_seed)
    assert actual == expected
    assert tuple(item.name for item in shards) == ("train.atbin", "train_1.atbin")
    assert len(set(digests)) == 2


@pytest.mark.parametrize("target", ("manifest", "shard"))
def test_frozen_dataset_rejects_manifest_or_shard_mutation(
    tmp_path: Path, target: str
) -> None:
    cfg = config(tmp_path)
    manifest_path, _ = manifest_fixture(tmp_path / "dataset", cfg)
    _, shards, digests = gate.validate_manifest(
        manifest_path, config=cfg, seed=cfg.train_seed
    )
    frozen = gate.freeze_dataset("train", manifest_path, shards, digests)
    path = manifest_path if target == "manifest" else shards[1]
    path.write_bytes(path.read_bytes() + b"mutation")
    with pytest.raises(gate.GateError, match="SHA-256 mismatch"):
        gate.authenticate_dataset(frozen, "regression")


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


@pytest.mark.parametrize(
    ("path", "value", "message"),
    (
        (("manifest_version",), True, "manifest version"),
        (("generation", "threads"), True, "threads"),
        (("generation", "options", "search_depth_min"), True, "search_depth_min"),
        (("shards", "0", "index"), False, "index"),
    ),
)
def test_manifest_gate_rejects_bool_for_integer_fields(
    tmp_path: Path, path: tuple[str, ...], value: object, message: str
) -> None:
    cfg = config(tmp_path)
    manifest_path, manifest = manifest_fixture(tmp_path / "dataset", cfg)
    target: Any = manifest
    for key in path[:-1]:
        target = target[int(key)] if isinstance(target, list) else target[key]
    final = path[-1]
    if isinstance(target, list):
        target[int(final)] = value
    else:
        target[final] = value
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


@pytest.mark.parametrize(
    ("field", "value", "message"),
    (
        ("contract_version", True, "failed"),
        ("shards", True, "shards"),
        ("records", 128, "canonical uint64"),
        ("side_to_move_wins", "+40", "canonical uint64"),
        ("side_to_move_wins", "040", "canonical uint64"),
        ("side_to_move_wins", "-1", "canonical uint64"),
        ("side_to_move_wins", str(2**64), "exceeds uint64"),
        ("atomic960_records", "00", "canonical uint64"),
    ),
)
def test_validation_summary_rejects_type_and_canonical_counter_drift(
    field: str, value: object, message: str
) -> None:
    validation = validation_fixture()
    validation[field] = value
    with pytest.raises(gate.GateError, match=message):
        gate.validate_success_json(
            gate.canonical_json_preserving_order(validation), "fixture"
        )


def test_decode_gate_authenticates_provenance_indexes_and_footer(tmp_path: Path) -> None:
    cfg = config(tmp_path)
    _, manifest = manifest_fixture(tmp_path / "dataset", cfg)
    validation = validation_fixture()
    payload = decode_fixture(manifest, validation)
    gate.validate_decode_jsonl(
        payload,
        manifest=manifest,
        validation=validation,
        raw_move_wires=raw_move_wires(),
    )

    lines = payload.splitlines()
    record = json.loads(lines[1])
    record["global_index"] = "1"
    lines[1] = gate.canonical_json_preserving_order(record).rstrip(b"\n")
    with pytest.raises(gate.GateError, match="global index"):
        gate.validate_decode_jsonl(
            b"\n".join(lines) + b"\n",
            manifest=manifest,
            validation=validation,
            raw_move_wires=raw_move_wires(),
        )


def test_decode_gate_authenticates_json_move_against_raw_shard_word(tmp_path: Path) -> None:
    cfg = config(tmp_path)
    manifest_path, manifest = manifest_fixture(tmp_path / "dataset", cfg)
    shard_paths = tuple(
        manifest_path.parent / str(item["file"]) for item in manifest["shards"]
    )
    assert gate.read_raw_move_wires(shard_paths) == raw_move_wires()
    mismatched = list(raw_move_wires())
    mismatched[gate.RECORDS_PER_SHARD] = 0x70D
    with pytest.raises(gate.GateError, match="raw shard word"):
        gate.validate_decode_jsonl(
            decode_fixture(manifest, validation_fixture()),
            manifest=manifest,
            validation=validation_fixture(),
            raw_move_wires=mismatched,
        )


@pytest.mark.parametrize(
    ("location", "value"),
    (
        (("contract_version",), True),
        (("slice", "limit"), True),
        (("dataset", "shards"), True),
    ),
)
def test_decode_gate_rejects_bool_for_header_integer_fields(
    tmp_path: Path, location: tuple[str, ...], value: object
) -> None:
    cfg = config(tmp_path)
    _, manifest = manifest_fixture(tmp_path / "dataset", cfg)
    validation = validation_fixture()
    lines = decode_fixture(manifest, validation).splitlines()
    header = json.loads(lines[0])
    target = header
    for key in location[:-1]:
        target = target[key]
    target[location[-1]] = value
    lines[0] = gate.canonical_json_preserving_order(header).rstrip(b"\n")
    with pytest.raises(gate.GateError, match="header changed"):
        gate.validate_decode_jsonl(
            b"\n".join(lines) + b"\n",
            manifest=manifest,
            validation=validation,
            raw_move_wires=raw_move_wires(),
        )


@pytest.mark.parametrize(
    ("path", "value", "message"),
    (
        (("score_stm",), DELETE, "missing or unknown"),
        (("score_stm",), True, "score_stm"),
        (("score_stm",), -2147483648, "score_stm"),
        (("ply",), "00", "canonical uint32"),
        (("ply",), "4294967296", "exceeds uint32"),
        (("result_stm",), 2, "result_stm"),
        (("position", "fen"), "8/8/8/8/8/8/8/8 w - - 0 1", "king count"),
        (("position", "fen_notation"), "shredder-fen", "fen_notation"),
        (("position", "side_to_move"), "black", "differs from FEN"),
        (("position", "rule50"), -1, "rule50"),
        (("position", "fullmove"), 0, "fullmove"),
        (("position", "castling_rights", "wire"), 14, "presence changed"),
        (
            ("position", "castling_rook_origins", "white_kingside"),
            None,
            "presence changed",
        ),
        (("position", "en_passant"), "e3", "differs from FEN"),
        (("move", "wire"), str(0x70D), "differs from decoded move"),
        (("move", "from"), "z9", "move.from is invalid"),
        (("move", "to"), "e2", "equal from/to"),
        (("move", "type"), "promotion", "promotion/type coupling"),
        (("move", "promotion"), "queen", "promotion/type coupling"),
        (("move", "future"), 1, "missing or unknown"),
    ),
)
def test_decode_gate_rejects_every_record_field_family_mutation(
    tmp_path: Path, path: tuple[str, ...], value: object, message: str
) -> None:
    cfg = config(tmp_path)
    _, manifest = manifest_fixture(tmp_path / "dataset", cfg)
    validation = validation_fixture()
    payload = mutate_decode_record(decode_fixture(manifest, validation), path, value)
    with pytest.raises(gate.GateError, match=message):
        gate.validate_decode_jsonl(
            payload,
            manifest=manifest,
            validation=validation,
            raw_move_wires=raw_move_wires(),
        )


def test_decode_gate_reconciles_record_results_with_validation_wdl(tmp_path: Path) -> None:
    cfg = config(tmp_path)
    _, manifest = manifest_fixture(tmp_path / "dataset", cfg)
    validation = validation_fixture()
    payload = mutate_decode_record(
        decode_fixture(manifest, validation), ("result_stm",), 1, record_index=100
    )
    with pytest.raises(gate.GateError, match="differs from validation"):
        gate.validate_decode_jsonl(
            payload,
            manifest=manifest,
            validation=validation,
            raw_move_wires=raw_move_wires(),
        )


def test_decode_gate_rejects_record_key_order_drift(tmp_path: Path) -> None:
    cfg = config(tmp_path)
    _, manifest = manifest_fixture(tmp_path / "dataset", cfg)
    validation = validation_fixture()
    lines = decode_fixture(manifest, validation).splitlines()
    record = json.loads(lines[1])
    reordered = {"contract_version": record.pop("contract_version"), **record}
    lines[1] = gate.canonical_json_preserving_order(reordered).rstrip(b"\n")
    with pytest.raises(gate.GateError, match="key order"):
        gate.validate_decode_jsonl(
            b"\n".join(lines) + b"\n",
            manifest=manifest,
            validation=validation,
            raw_move_wires=raw_move_wires(),
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


@pytest.mark.parametrize("score", ("0", "+0.00", "-1.5", ".25", "3."))
def test_engine_evaluation_requires_exact_loaded_candidate_and_true_mode(
    tmp_path: Path, score: str
) -> None:
    candidate = (tmp_path / "candidate.nnue").resolve()
    marker = gate.LEGACY_NNUE_LOAD_PREFIX + str(candidate) + gate.LEGACY_NNUE_LOAD_SUFFIX
    assert gate.validate_engine_evaluation(
        (marker, f"Final evaluation {score} (white side) [Use NNUE=true]"), candidate
    ) == float(score)


@pytest.mark.parametrize(
    "final",
    (
        "Final evaluation nan (white side) [Use NNUE=true]",
        "Final evaluation inf (white side) [Use NNUE=true]",
        "Final evaluation 0.0 (white side) [Use NNUE=pure]",
        "Final evaluation 0.0 (white side) [Use NNUE=false]",
    ),
)
def test_engine_evaluation_rejects_nonfinite_or_wrong_nnue_mode(
    tmp_path: Path, final: str
) -> None:
    candidate = (tmp_path / "candidate.nnue").resolve()
    marker = gate.LEGACY_NNUE_LOAD_PREFIX + str(candidate) + gate.LEGACY_NNUE_LOAD_SUFFIX
    with pytest.raises(gate.GateError, match="finite Use NNUE=true"):
        gate.validate_engine_evaluation((marker, final), candidate)


def test_engine_evaluation_rejects_candidate_path_suffix(tmp_path: Path) -> None:
    candidate = (tmp_path / "candidate.nnue").resolve()
    marker = (
        gate.LEGACY_NNUE_LOAD_PREFIX
        + f"{candidate}.backup"
        + gate.LEGACY_NNUE_LOAD_SUFFIX
    )
    with pytest.raises(gate.GateError, match="load marker"):
        gate.validate_engine_evaluation(
            (marker, "Final evaluation 0 (white side) [Use NNUE=true]"), candidate
        )


def test_engine_evaluation_rejects_marker_without_backend_identity(
    tmp_path: Path,
) -> None:
    candidate = (tmp_path / "candidate.nnue").resolve()
    marker = (
        f"info string NNUE evaluation using {candidate}" + gate.LEGACY_NNUE_LOAD_SUFFIX
    )
    with pytest.raises(gate.GateError, match="load marker"):
        gate.validate_engine_evaluation(
            (marker, "Final evaluation 0 (white side) [Use NNUE=true]"), candidate
        )


def test_engine_load_rechecks_candidate_after_uci(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = config(tmp_path)
    archive = gate.Archive(cfg.output_dir)
    candidate = archive.write("networks/candidate.nnue", b"original")
    expected = hashlib.sha256(b"original").hexdigest()

    class MutatingUci:
        def __init__(self, engine: Path, timeout: float) -> None:
            del engine, timeout
            self.index = 0
            self.transcript: list[str] = []

        def __enter__(self) -> "MutatingUci":
            return self

        def __exit__(self, *unused: object) -> None:
            return None

        def send(self, command: str) -> None:
            self.transcript.append(f"> {command}")

        def read_until(self, unused: object) -> list[str]:
            del unused
            responses = (
                ["uciok"],
                [
                    gate.LEGACY_NNUE_LOAD_PREFIX
                    + str(candidate.resolve())
                    + gate.LEGACY_NNUE_LOAD_SUFFIX,
                    "readyok",
                ],
                ["Final evaluation 0 (white side) [Use NNUE=true]"],
                ["bestmove e2e4"],
            )
            response = responses[self.index]
            self.index += 1
            self.transcript.extend(response)
            if self.index == len(responses):
                candidate.write_bytes(b"mutated")
            return response

    monkeypatch.setattr(gate, "UciProcess", MutatingUci)
    with pytest.raises(gate.GateError, match="candidate after UCI SHA-256 mismatch"):
        gate.load_candidate_in_engine(cfg, archive, candidate, expected)
    archive.cleanup_private()


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
    monkeypatch.setattr(
        gate,
        "NORMATIVE_SOURCE_NET_SHA256_BY_PROFILE",
        {**gate.NORMATIVE_SOURCE_NET_SHA256_BY_PROFILE, cfg.profile: cfg.source_net_sha256},
    )

    def checkout(spec: gate.CheckoutSpec) -> gate.CheckoutState:
        return gate.CheckoutState(
            spec.label, str(spec.root.resolve()), spec.commit, spec.ref, spec.repository
        )

    submodule_pins: list[tuple[str, str]] = []
    tools_lock_pins: list[str] = []

    def submodule(parent: Path, relative: str, commit: str, *, label: str) -> Path:
        del label
        submodule_pins.append((relative, commit))
        return (parent / relative).resolve()

    def tools_lock(root: Path, commit: str) -> None:
        del root
        tools_lock_pins.append(commit)

    monkeypatch.setattr(gate, "verify_checkout", checkout)
    monkeypatch.setattr(gate, "verify_submodule_pin", submodule)
    monkeypatch.setattr(gate, "verify_schema_tree", lambda root, label: None)
    monkeypatch.setattr(gate, "verify_tools_lock", tools_lock)
    state = gate.preflight(cfg)
    assert submodule_pins == [
        ("engine/Atomic-Stockfish", TOOLS_ENGINE_COMMIT),
        ("external/Atomic-Stockfish", TRAINER_ENGINE_COMMIT),
    ]
    assert tools_lock_pins == [TOOLS_ENGINE_COMMIT]
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
    (archive.root / "logs" / "leak.log").write_bytes(
        b'leak "C:\\Users\\example\\secret.nnue"\n'
    )
    with pytest.raises(gate.GateError, match="unredacted absolute path"):
        gate.verify_public_text_redaction(archive)
    archive.cleanup_private()


def test_public_archive_sanitizes_unknown_windows_posix_and_root_paths(tmp_path: Path) -> None:
    archive = gate.Archive(tmp_path / "audit")
    unknown = archive.root / "logs" / "unknown.log"
    json_escaped_uri = json.dumps(
        {"windows_uri_with_space": "file:///C:/Users/Jane Doe/private model.bin"}
    )
    json_escaped_windows_uri = json.dumps(
        {"escaped_windows_uri": r"file:\\C:\Users\Jane Doe\private model.bin"}
    )
    unknown.write_bytes(
        (
            "win=C:\\Python\\Lib\\site-packages\\torch.py\n"
            + json.dumps({"cache": r"C:\Users\me\x"})
            + "\n"
            "cache=/root/.cache/pytorch/model.bin\n"
            "temp=/workspace/build/output.txt\n"
            "uri=file:///root/.cache/private.bin\n"
            + json.dumps({"windows_uri": "file:///C:/Users/me/private.bin"})
            + "\n"
            "windows_space=file:///C:/Users/Jane Doe/private model.bin\n"
            "posix_space=file:///home/jane doe/private model.bin\n"
            + json_escaped_uri
            + "\n"
            + json_escaped_windows_uri
            + "\n"
            "url=https://github.com/Belzedar94/Atomic-Stockfish\n"
            "fen=rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1\n"
        ).encode("utf-8")
    )
    assert archive.sanitize_public_text() == ("logs/unknown.log",)
    payload = unknown.read_text(encoding="utf-8")
    assert payload.count("<HOST_PATH>") == 10
    assert "Jane Doe" not in payload
    assert "jane doe" not in payload
    assert "private model.bin" not in payload
    assert "https://github.com/Belzedar94/Atomic-Stockfish" in payload
    assert "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR" in payload
    gate.verify_public_text_redaction(archive)
    archive.cleanup_private()


def test_run_gate_failure_archive_is_sanitized_and_inventoried(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = config(tmp_path)
    state = gate.Preflight({}, {})
    monkeypatch.setattr(
        gate, "preflight", lambda unused, allow_output=False: state
    )
    monkeypatch.setattr(gate, "capture_gate_python_environment", lambda: object())

    def fail_capabilities(unused: gate.GateConfig, archive: gate.Archive) -> Mapping[str, object]:
        (archive.root / "logs" / "external.log").write_bytes(
            b"C:\\Unknown\\site-packages\\shadow.py /root/.cache/private.bin\n"
        )
        raise RuntimeError("loader failed at /root/.cache/private.bin")

    monkeypatch.setattr(gate, "verify_capabilities", fail_capabilities)
    with pytest.raises(RuntimeError, match="loader failed"):
        gate.run_gate(cfg)

    assert not gate.private_work_path(cfg.output_dir).exists()
    failure = json.loads((cfg.output_dir / "failure.json").read_text(encoding="utf-8"))
    assert failure["message"] == "loader failed at <HOST_PATH>"
    assert (cfg.output_dir / "failure-hashes.json").is_file()
    combined = "\n".join(
        path.read_text(encoding="utf-8")
        for path in cfg.output_dir.rglob("*")
        if path.is_file() and path.suffix in {".json", ".log"}
    )
    assert "C:\\Unknown" not in combined
    assert "/root/" not in combined
    gate.verify_public_text_redaction(
        SimpleNamespace(root=cfg.output_dir, redactions=())  # type: ignore[arg-type]
    )


def test_run_gate_keyboard_interrupt_cleans_private_and_archives_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = config(tmp_path)
    state = gate.Preflight({}, {})
    monkeypatch.setattr(
        gate, "preflight", lambda unused, allow_output=False: state
    )
    monkeypatch.setattr(gate, "capture_gate_python_environment", lambda: object())

    def interrupt(unused: gate.GateConfig, archive: gate.Archive) -> Mapping[str, object]:
        archive.private_path("partial.bin").write_bytes(b"private")
        raise KeyboardInterrupt("stopped at file:///root/.cache/private.bin")

    monkeypatch.setattr(gate, "verify_capabilities", interrupt)
    with pytest.raises(KeyboardInterrupt, match="stopped"):
        gate.run_gate(cfg)
    assert not gate.private_work_path(cfg.output_dir).exists()
    failure_bytes = (cfg.output_dir / "failure.json").read_bytes()
    assert b'"error_type":"KeyboardInterrupt"' in failure_bytes
    assert b"<HOST_PATH>" in failure_bytes
    assert b"Traceback" not in failure_bytes and b"/root/" not in failure_bytes
    assert (cfg.output_dir / "failure-hashes.json").is_file()


def test_main_emits_only_lf_and_no_host_traceback(tmp_path: Path) -> None:
    cfg = config(tmp_path)
    stdout = io.BytesIO()
    stderr = io.BytesIO()

    def fail(unused: gate.GateConfig) -> Mapping[str, object]:
        raise RuntimeError("failed in C:\\Python\\Lib\\site-packages\\shadow.py")

    result = gate.main(
        (),
        _config_loader=lambda unused: cfg,
        _runner=fail,
        _stdout=stdout,
        _stderr=stderr,
    )
    assert result == 1 and stdout.getvalue() == b""
    assert stderr.getvalue().endswith(b"\n") and b"\r" not in stderr.getvalue()
    assert b"<HOST_PATH>" in stderr.getvalue()
    assert b"Traceback" not in stderr.getvalue()


def test_main_success_is_one_canonical_lf_json_line(tmp_path: Path) -> None:
    cfg = config(tmp_path)
    stdout = io.BytesIO()
    stderr = io.BytesIO()
    expected = {"status": "passed", "schema_version": 1}
    result = gate.main(
        (),
        _config_loader=lambda unused: cfg,
        _runner=lambda unused: expected,
        _stdout=stdout,
        _stderr=stderr,
    )
    assert result == 0
    assert stdout.getvalue() == gate.canonical_json(expected)
    assert stdout.getvalue().count(b"\n") == 1 and b"\r" not in stdout.getvalue()
    assert stderr.getvalue() == b""


def test_main_keyboard_interrupt_is_redacted_lf_exit_130_and_system_exit_propagates(
    tmp_path: Path,
) -> None:
    cfg = config(tmp_path)
    stdout = io.BytesIO()
    stderr = io.BytesIO()

    def interrupt(unused: gate.GateConfig) -> Mapping[str, object]:
        raise KeyboardInterrupt("file:///root/.cache/private.bin")

    assert gate.main(
        (),
        _config_loader=lambda unused: cfg,
        _runner=interrupt,
        _stdout=stdout,
        _stderr=stderr,
    ) == 130
    assert stdout.getvalue() == b""
    assert stderr.getvalue().endswith(b"\n") and b"\r" not in stderr.getvalue()
    assert b"<HOST_PATH>" in stderr.getvalue() and b"Traceback" not in stderr.getvalue()

    with pytest.raises(SystemExit):
        gate.main(
            (),
            _config_loader=lambda unused: cfg,
            _runner=lambda unused: (_ for _ in ()).throw(SystemExit(7)),
            _stdout=io.BytesIO(),
            _stderr=io.BytesIO(),
        )


def test_python_environment_wrappers_capture_verify_and_reject_path_overrides(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    expected = object()
    verified: list[object] = []
    monkeypatch.setattr(
        gate, "capture_python_environment_provenance", lambda: expected
    )
    monkeypatch.setattr(
        gate,
        "verify_python_environment_provenance",
        lambda actual, emit: verified.append(actual),
    )
    assert gate.capture_gate_python_environment() is expected
    gate.verify_gate_python_environment(expected)  # type: ignore[arg-type]
    assert verified == [expected]

    monkeypatch.setenv("PYTHONPATH", "shadow")
    with pytest.raises(gate.GateError, match="PYTHONPATH"):
        gate.verify_gate_python_environment(expected)  # type: ignore[arg-type]
    monkeypatch.delenv("PYTHONPATH")
    monkeypatch.setenv("PYTHONHOME", "shadow")
    with pytest.raises(gate.GateError, match="PYTHONHOME"):
        gate.verify_gate_python_environment(expected)  # type: ignore[arg-type]


def test_python_environment_suppresses_only_the_known_pkg_resources_warning(
    tmp_path: Path,
) -> None:
    cfg = config(tmp_path)
    commands: list[tuple[str, ...]] = []
    payload = gate.canonical_json(
        {
            "python": "3.9.13",
            "implementation": "CPython",
            "platform": "test-platform",
            "numpy": "1.26.4",
            "torch": "2.0.0",
            "pytorch_lightning": "1.9.5",
            "executable": str(cfg.python),
        }
    )

    class FakeArchive:
        def run(
            self,
            label: str,
            argv: tuple[str, ...],
            *,
            cwd: Path,
            timeout: float,
        ) -> gate.CommandResult:
            assert label == "python environment"
            assert cwd == cfg.trainer.root
            assert timeout == cfg.timeout_seconds
            commands.append(argv)
            return gate.CommandResult(label, argv, str(cwd), 0, payload, b"")

    environment = gate.python_environment(cfg, FakeArchive())  # type: ignore[arg-type]
    assert environment["pytorch_lightning"] == "1.9.5"
    assert len(commands) == 1
    assert commands[0][:5] == (
        str(cfg.python),
        "-B",
        "-W",
        "ignore:pkg_resources is deprecated as an API:UserWarning",
        "-c",
    )
    assert "import json,platform,sys,numpy,torch,pytorch_lightning" in commands[0][5]


def test_inventory_reconciles_candidate_and_roundtrip_hashes(tmp_path: Path) -> None:
    archive = gate.Archive(tmp_path / "audit")
    payload = b"same network bytes"
    candidate = archive.write("networks/candidate.nnue", payload)
    roundtrip = archive.write("networks/roundtrip.nnue", payload)
    digest = hashlib.sha256(payload).hexdigest()
    inventory = gate.inventory_archive(archive)
    expected = {
        candidate.relative_to(archive.root).as_posix(): digest,
        roundtrip.relative_to(archive.root).as_posix(): digest,
    }
    gate.verify_inventory_artifacts(inventory, expected)
    with pytest.raises(gate.GateError, match="does not reconcile"):
        gate.verify_inventory_artifacts(
            inventory, {roundtrip.relative_to(archive.root).as_posix(): "0" * 64}
        )
    archive.cleanup_private()


@pytest.mark.parametrize("operation", ("sanitize", "verify", "discard", "inventory"))
def test_public_tree_consumers_reject_linked_directories_without_touching_outside(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, operation: str
) -> None:
    archive = gate.Archive(tmp_path / "audit")
    target = tmp_path / "external"
    target.mkdir()
    outside = target / "secret.log"
    original = b"outside C:\\Users\\someone\\secret.nnue\n"
    outside.write_bytes(original)
    linked = archive.root / "linked-directory"
    create_directory_link(linked, target)

    real_scandir = gate.os.scandir
    scanned: list[Path] = []

    def tracked_scandir(path: object) -> object:
        scanned.append(Path(path))  # type: ignore[arg-type]
        return real_scandir(path)  # type: ignore[arg-type]

    monkeypatch.setattr(gate.os, "scandir", tracked_scandir)
    try:
        with pytest.raises(gate.GateError, match="(symlink|reparse point)"):
            invoke_public_tree_consumer(operation, archive)

        assert outside.read_bytes() == original
        assert linked not in scanned
        assert target not in scanned
    finally:
        remove_directory_link(linked)


@pytest.mark.parametrize("operation", ("sanitize", "verify", "discard", "inventory"))
def test_public_tree_consumers_reject_parent_swap_before_touching_external_leaf(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, operation: str
) -> None:
    archive = gate.Archive(tmp_path / "audit")
    trigger = archive.root / "a-trigger.log"
    trigger.write_bytes(b"safe trigger\n")
    nested = archive.root / "z-nested"
    nested.mkdir()
    inside = nested / "secret.log"
    inside.write_bytes(b"authenticated inside\n")
    external = tmp_path / "external"
    external.mkdir()
    external_secret = external / "secret.log"
    external_payload = b"EXTERNAL LEAF MUST NOT BE TOUCHED\n"
    external_secret.write_bytes(external_payload)
    swapped = False
    external_leaf_operations: list[str] = []

    def swap_parent_once() -> None:
        nonlocal swapped
        if swapped:
            return
        shutil.rmtree(nested)
        create_directory_link(nested, external)
        swapped = True

    real_read_bytes = Path.read_bytes

    def tracked_read_bytes(path: Path) -> bytes:
        if path == inside:
            external_leaf_operations.append("read_bytes")
        return real_read_bytes(path)

    monkeypatch.setattr(Path, "read_bytes", tracked_read_bytes)

    if operation == "sanitize":
        original_redact = archive.redact_text

        def redact_and_swap(value: str) -> str:
            redacted = original_redact(value)
            swap_parent_once()
            return redacted

        monkeypatch.setattr(archive, "redact_text", redact_and_swap)
    elif operation == "verify":
        real_read_text = Path.read_text

        def read_text_and_swap(path: Path, *args: object, **kwargs: object) -> str:
            if path == inside:
                external_leaf_operations.append("read_text")
            text = real_read_text(path, *args, **kwargs)  # type: ignore[arg-type]
            if path == trigger:
                swap_parent_once()
            return text

        monkeypatch.setattr(Path, "read_text", read_text_and_swap)
    elif operation == "inventory":
        real_sha256_file = gate.sha256_file

        def hash_and_swap(path: Path) -> str:
            if path == inside:
                external_leaf_operations.append("hash")
            digest = real_sha256_file(path)
            if path == trigger:
                swap_parent_once()
            return digest

        monkeypatch.setattr(gate, "sha256_file", hash_and_swap)
    else:
        real_unlink = Path.unlink

        def unlink_and_swap(path: Path, *args: object, **kwargs: object) -> None:
            if path == inside:
                external_leaf_operations.append("unlink")
            real_unlink(path, *args, **kwargs)  # type: ignore[arg-type]
            if path == trigger:
                swap_parent_once()

        monkeypatch.setattr(Path, "unlink", unlink_and_swap)

    try:
        with pytest.raises(gate.GateError, match="(parent|symlink|reparse point)"):
            invoke_public_tree_consumer(operation, archive)

        assert swapped
        assert external_leaf_operations == []
        assert external_secret.read_bytes() == external_payload
    finally:
        if swapped:
            remove_directory_link(nested)


def test_require_directory_rejects_symlink_before_resolution(tmp_path: Path) -> None:
    target = tmp_path / "external"
    target.mkdir()
    linked = tmp_path / "linked-directory"
    linked.symlink_to(target, target_is_directory=True)

    with pytest.raises(gate.GateError, match="must not be a symlink"):
        gate.require_directory(linked, "atomic root")


def test_safe_tree_rejects_reparse_metadata_before_directory_classification(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    archive = gate.Archive(tmp_path / "audit")
    reparse_flag = 0x400
    monkeypatch.setattr(
        gate.stat_module, "FILE_ATTRIBUTE_REPARSE_POINT", reparse_flag, raising=False
    )
    junction = FakeDirEntry(
        "junction",
        SimpleNamespace(
            st_mode=stat_module.S_IFDIR,
            st_file_attributes=reparse_flag,
            st_nlink=1,
        ),
    )
    real_scandir = gate.os.scandir
    scanned: list[Path] = []

    def fake_scandir(path: object) -> object:
        inspected = Path(path)  # type: ignore[arg-type]
        scanned.append(inspected)
        if inspected == archive.root:
            return FakeScandir([junction])
        return real_scandir(path)  # type: ignore[arg-type]

    monkeypatch.setattr(gate.os, "scandir", fake_scandir)
    with pytest.raises(gate.GateError, match="contains a reparse point"):
        gate.safe_tree_entries(archive.root, "archive")

    assert junction.is_dir_calls == 0
    assert scanned == [archive.root]


def test_safe_tree_fails_closed_when_scandir_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "archive"
    root.mkdir()

    def denied_scandir(unused: object) -> object:
        raise PermissionError("denied")

    monkeypatch.setattr(gate.os, "scandir", denied_scandir)
    with pytest.raises(gate.GateError, match="cannot inspect archive directory"):
        gate.safe_tree_entries(root, "archive")


def test_safe_tree_fails_closed_when_entry_lstat_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "archive"
    root.mkdir()
    broken = FakeDirEntry("broken.log", PermissionError("denied"))
    monkeypatch.setattr(gate.os, "scandir", lambda unused: FakeScandir([broken]))

    with pytest.raises(gate.GateError, match="cannot lstat archive entry"):
        gate.safe_tree_entries(root, "archive")


def test_is_reparse_point_fails_closed_on_lstat_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    broken = tmp_path / "broken"
    real_lstat = Path.lstat

    def denied_lstat(path: Path) -> os.stat_result:
        if path == broken:
            raise PermissionError("denied")
        return real_lstat(path)

    monkeypatch.setattr(Path, "lstat", denied_lstat)
    with pytest.raises(gate.GateError, match="cannot inspect path for reparse points"):
        gate.is_reparse_point(broken)


def test_safe_tree_rejects_unknown_node_types(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "archive"
    root.mkdir()
    unknown = FakeDirEntry(
        "pipe",
        SimpleNamespace(
            st_mode=stat_module.S_IFIFO,
            st_file_attributes=0,
            st_nlink=1,
        ),
    )
    monkeypatch.setattr(gate.os, "scandir", lambda unused: FakeScandir([unknown]))

    with pytest.raises(gate.GateError, match="contains a non-regular entry"):
        gate.safe_tree_entries(root, "archive")


def test_safe_tree_normal_flow_is_deterministic(tmp_path: Path) -> None:
    root = tmp_path / "archive"
    (root / "a").mkdir(parents=True)
    (root / "a" / "nested.log").write_bytes(b"nested\n")
    (root / "z.json").write_bytes(b"{}\n")

    entries = gate.safe_tree_entries(root, "archive")

    assert tuple(entry.relative for entry in entries) == (
        "a",
        "a/nested.log",
        "z.json",
    )
    assert entries[0].is_directory
    assert entries[1].is_regular_file
    assert entries[2].is_regular_file


def test_sanitizer_rejects_hardlink_without_overwriting_external_file(
    tmp_path: Path,
) -> None:
    archive = gate.Archive(tmp_path / "audit")
    outside = tmp_path / "outside.log"
    original = b"C:\\Users\\someone\\secret.nnue\n"
    outside.write_bytes(original)
    os.link(outside, archive.root / "linked.log")

    with pytest.raises(gate.GateError, match="multiply-linked regular file"):
        archive.sanitize_public_text()

    assert outside.read_bytes() == original


def test_sanitizer_hardlink_swap_during_redaction_never_truncates_external_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    archive = gate.Archive(tmp_path / "audit")
    target = archive.root / "logs" / "evidence.log"
    target.write_bytes(b"C:\\Users\\inside\\private.nnue\n")
    outside = tmp_path / "outside.log"
    outside_payload = b"EXTERNAL BYTES MUST REMAIN UNCHANGED\n"
    outside.write_bytes(outside_payload)
    original_redact = archive.redact_text
    swapped = False

    def swap_target_for_hardlink(value: str) -> str:
        nonlocal swapped
        redacted = original_redact(value)
        target.unlink()
        os.link(outside, target)
        swapped = True
        return redacted

    monkeypatch.setattr(archive, "redact_text", swap_target_for_hardlink)
    with pytest.raises(gate.GateError, match="(multiply-linked|changed)"):
        archive.sanitize_public_text()

    assert swapped
    assert outside.read_bytes() == outside_payload
    assert target.read_bytes() == outside_payload
    assert not tuple(target.parent.glob(f".{target.name}.rewrite-*"))


def test_require_regular_file_rejects_symlink_before_resolution(tmp_path: Path) -> None:
    target = tmp_path / "engine-target"
    target.write_bytes(b"authenticated bytes")
    linked = tmp_path / "engine-link"
    linked.symlink_to(target)

    with pytest.raises(gate.GateError, match="must not be a symlink"):
        gate.require_regular_file(linked, "engine")


def namespace(tmp_path: Path, **overrides: object) -> SimpleNamespace:
    values: dict[str, object] = {
        "atomic_root": tmp_path / "atomic",
        "atomic_commit": COMMIT,
        "atomic_ref": "refs/heads/gate",
        "tools_root": tmp_path / "tools",
        "tools_commit": TOOLS_COMMIT,
        "tools_ref": "refs/heads/atomic",
        "trainer_root": tmp_path / "trainer",
        "trainer_commit": TRAINER_COMMIT,
        "trainer_ref": "refs/heads/atomic",
        "profile": "strong-local",
        "tools_engine_commit": TOOLS_ENGINE_COMMIT,
        "trainer_engine_commit": TRAINER_ENGINE_COMMIT,
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
        "python": Path(sys.executable).resolve(),
        "python_sha256": gate.sha256_file(Path(sys.executable).resolve()),
        "source_net": tmp_path / "network.nnue",
        "source_net_sha256": gate.NORMATIVE_STRONG_LOCAL_SOURCE_NET_SHA256,
        "output_dir": tmp_path / "output",
        "train_seed": 1,
        "validation_seed": 2,
        "timeout_seconds": 60.0,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_cli_config_requires_exact_hashes_commits_and_distinct_seeds(tmp_path: Path) -> None:
    cfg = gate.config_from_args(namespace(tmp_path))
    assert cfg.profile == "strong-local"
    assert cfg.atomic.repository == gate.ATOMIC_REPOSITORY
    assert cfg.tools.repository == gate.TOOLS_REPOSITORY
    assert cfg.trainer.repository == gate.TRAINER_REPOSITORY
    assert gate.pipeline_identity(cfg) == {
        "profile": "strong-local",
        "tools_engine_commit": TOOLS_ENGINE_COMMIT,
        "trainer_engine_commit": TRAINER_ENGINE_COMMIT,
    }
    assert cfg.train_seed == 1 and cfg.validation_seed == 2
    with pytest.raises(gate.GateError, match="seeds must differ"):
        gate.config_from_args(namespace(tmp_path, validation_seed=1))
    with pytest.raises(gate.GateError, match="lowercase 40-character"):
        gate.config_from_args(namespace(tmp_path, atomic_commit="A" * 40))
    with pytest.raises(gate.GateError, match="lowercase SHA-256"):
        gate.config_from_args(namespace(tmp_path, python_sha256="C" * 64))


def test_cli_config_accepts_the_exact_synthetic_ci_identity(tmp_path: Path) -> None:
    cfg = gate.config_from_args(
        namespace(
            tmp_path,
            profile="synthetic-ci",
            source_net_sha256=gate.NORMATIVE_SYNTHETIC_CI_SOURCE_NET_SHA256,
        )
    )
    assert cfg.profile == "synthetic-ci"
    assert cfg.source_net_sha256 == gate.NORMATIVE_SYNTHETIC_CI_SOURCE_NET_SHA256


@pytest.mark.parametrize(
    ("field", "value", "message"),
    (
        ("tools_engine_commit", "d" * 40, "tools engine pin"),
        ("trainer_engine_commit", "d" * 40, "trainer engine pin"),
        ("tools_commit", "d" * 40, "tools atomic merge pin"),
        ("trainer_commit", "d" * 40, "trainer atomic merge pin"),
        ("source_net_sha256", "d" * 64, "source network pin"),
        ("train_seed", 0, "train seed"),
        ("train_seed", 2**32, "train seed"),
        ("validation_seed", 0, "validation seed"),
        ("validation_seed", 2**64, "validation seed"),
    ),
)
def test_cli_config_rejects_non_normative_pins_and_seed_domains(
    tmp_path: Path, field: str, value: object, message: str
) -> None:
    with pytest.raises(gate.GateError, match=message):
        gate.config_from_args(namespace(tmp_path, **{field: value}))


@pytest.mark.parametrize(
    ("profile", "source_sha256"),
    (
        ("strong-local", gate.NORMATIVE_SYNTHETIC_CI_SOURCE_NET_SHA256),
        ("synthetic-ci", gate.NORMATIVE_STRONG_LOCAL_SOURCE_NET_SHA256),
    ),
)
def test_cli_config_rejects_cross_profile_networks(
    tmp_path: Path, profile: str, source_sha256: str
) -> None:
    with pytest.raises(gate.GateError, match="source network pin does not match profile"):
        gate.config_from_args(
            namespace(tmp_path, profile=profile, source_net_sha256=source_sha256)
        )


def test_profile_is_required_and_closed_over_two_named_identities() -> None:
    action = next(
        item for item in gate.build_parser()._actions if item.dest == "profile"
    )
    assert action.required is True
    assert tuple(action.choices or ()) == ("strong-local", "synthetic-ci")
    with pytest.raises(gate.GateError, match="profile must be exactly"):
        gate.normative_source_net_sha256("unreviewed")


def test_atomic_ci_runs_the_real_synthetic_v2_gate_with_final_pins() -> None:
    workflow = (REPO_ROOT / ".github" / "workflows" / "atomic.yml").read_text(
        encoding="utf-8"
    )
    required = (
        "Synthetic Atomic BIN V2 generate-train-load E2E",
        "python -B tests/create_synthetic_zero_nnue.py",
        "python -B tests/atomic_bin_v2_pipeline_e2e.py",
        "make -C .pipeline/tools -j2 ARCH=x86-64 COMP=gcc v2-data-tools",
        "make -C src -j2 ARCH=x86-64 data-tools",
        "--profile synthetic-ci",
        f"--tools-commit {TOOLS_COMMIT}",
        f"--tools-engine-commit {TOOLS_ENGINE_COMMIT}",
        f"--trainer-commit {TRAINER_COMMIT}",
        f"--trainer-engine-commit {TRAINER_ENGINE_COMMIT}",
        (
            "--source-net-sha256 "
            f"{gate.NORMATIVE_SYNTHETIC_CI_SOURCE_NET_SHA256}"
        ),
        (
            "--wrapper-data-tools "
            ".pipeline/tools/engine/Atomic-Stockfish/src/atomic-stockfish-data-tools"
        ),
        "--trainer-loader .pipeline/trainer/libtraining_data_loader.so",
        "name: atomic-bin-v2-e2e-evidence",
    )
    for marker in required:
        assert marker in workflow
    assert workflow.index("Enforce locked clean checkouts") < workflow.index(
        "Synthetic Atomic BIN V2 generate-train-load E2E"
    )
    assert "--contract-engine-commit" not in workflow


def test_fingerprints_must_remain_identical_after_gate() -> None:
    checkout = {"atomic": gate.CheckoutState("Atomic", "/a", COMMIT, "ref", gate.ATOMIC_REPOSITORY)}
    before = gate.Preflight(checkout, {"engine": gate.Fingerprint("engine", "/e", 1, SHA256)})
    gate.fingerprints_equal(before, before)
    after = gate.Preflight(checkout, {"engine": gate.Fingerprint("engine", "/e", 2, SHA256)})
    with pytest.raises(gate.GateError, match="input artifact changed"):
        gate.fingerprints_equal(before, after)
