from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
from typing import Callable

import pytest


ROOT = Path(__file__).resolve().parents[2]
TOOL = ROOT / "tools" / "validate_authenticated_datagen_bundle_v2.py"
SPEC = importlib.util.spec_from_file_location("authenticated_bundle_v2", TOOL)
assert SPEC is not None and SPEC.loader is not None
VALIDATOR = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(VALIDATOR)


def put(header: bytearray, offset: int, value: int, size: int) -> None:
    header[offset:offset + size] = value.to_bytes(size, "little")


def canonical(document: dict[str, object]) -> bytes:
    return (json.dumps(document, ensure_ascii=False, separators=(",", ":")) + "\n").encode()


def build_bundle(
    bundle: Path,
    inventory: Path,
    *,
    teacher_mode: str = "pure",
    use_nnue: str = "pure",
    producer_sha256: str | None = None,
    mutate_manifest: Callable[[dict[str, object]], None] | None = None,
    mutate_attestation: Callable[[dict[str, object]], None] | None = None,
    mutate_header: Callable[[bytearray], None] | None = None,
) -> None:
    inventory.write_bytes(b"[]\n")
    inventory_sha = hashlib.sha256(inventory.read_bytes()).hexdigest()
    shard = bytearray(160)
    shard[:8] = b"ATBINV2\0"
    put(shard, 8, 2, 2)
    put(shard, 10, 96, 2)
    put(shard, 12, 0x01020304, 4)
    put(shard, 16, 64, 4)
    shard[24:56] = bytes.fromhex(VALIDATOR.DATA_SCHEMA_SHA256)
    put(shard, 56, 1, 8)
    shard_sha = hashlib.sha256(shard).hexdigest()
    options = {
        "search_depth_min": 6,
        "search_depth_max": 6,
        "nodes": "0",
        "requested_records": "1",
        "records_per_shard": "1",
        "eval_limit": 10000,
        "eval_diff_limit": 32000,
        "random_move_min_ply": 1,
        "random_move_max_ply": 20,
        "random_move_count": 8,
        "random_move_like_apery": 0,
        "random_multi_pv": 4,
        "random_multi_pv_diff": 200,
        "random_multi_pv_depth": 6,
        "write_min_ply": 5,
        "write_max_ply": 400,
        "keep_draws": "1",
        "adjudicate_draws_by_score": True,
        "adjudicate_insufficient": True,
        "filter_captures": True,
        "filter_checks": False,
        "filter_promotions": True,
        "random_file_name": False,
        "set_recommended_uci_options_seen": True,
    }
    engine = {"commit": "a" * 40, "version": "Atomic-Stockfish test"}
    if producer_sha256 is not None:
        engine["producer_sha256"] = producer_sha256
    manifest = {
        "manifest_version": 2,
        "manifest_schema_sha256": VALIDATOR.MANIFEST_SCHEMA_SHA256,
        "data_schema_sha256": VALIDATOR.DATA_SCHEMA_SHA256,
        "format": "atomic-bin-v2",
        "engine": engine,
        "network": {"file": "atomic.nnue", "sha256": "b" * 64},
        "book": {"kind": "file", "file": "atomic.epd", "sha256": "c" * 64},
        "generation": {
            "resolved_seed": "202607150500000",
            "atomic960": False,
            "threads": 30,
            "hash_mb": "512",
            "teacher_mode": teacher_mode,
            "use_nnue": use_nnue,
            "options": options,
            "syzygy": {
                "inventory_sha256": inventory_sha,
                "cardinality": 6,
                "probe_limit": 6,
                "probe_depth": 1,
                "rule50": True,
                "wdl_suffix": ".atbw",
                "dtz_suffix": ".atbz",
            },
        },
        "statistics": {"records": "1", "draws": "0", "tb_probes": "7", "tb_hits": "5"},
        "shards": [{
            "index": 0,
            "file": "dataset.atbin",
            "records": "1",
            "bytes": str(len(shard)),
            "sha256": shard_sha,
        }],
    }
    if mutate_manifest:
        mutate_manifest(manifest)
    manifest_bytes = canonical(manifest)
    manifest_sha = hashlib.sha256(manifest_bytes).hexdigest()
    attestation = {
        "attestation_version": 1,
        "attestation_schema_sha256": VALIDATOR.ATTESTATION_SCHEMA_SHA256,
        "contract": "atomic-openbench-authenticated-teacher-syzygy-v2",
        "manifest": {
            "file": "dataset.atbin.manifest-v2.json",
            "bytes": str(len(manifest_bytes)),
            "sha256": manifest_sha,
            "schema_sha256": VALIDATOR.MANIFEST_SCHEMA_SHA256,
        },
        "shard": {
            "file": "dataset.atbin",
            "bytes": str(len(shard)),
            "sha256": shard_sha,
            "schema_sha256": VALIDATOR.DATA_SCHEMA_SHA256,
        },
        "syzygy_inventory": {
            "sha256": inventory_sha,
            "cardinality": 6,
        },
        "teacher": {
            "mode": teacher_mode,
            "use_nnue": use_nnue,
            "syzygy_probe_limit": 6,
            "syzygy_probe_depth": 1,
            "syzygy_50_move_rule": True,
        },
        "counters": {"tb_probes": "7", "tb_hits": "5"},
    }
    if producer_sha256 is not None:
        # Rebuild in canonical producer order: immediately after contract.
        attestation = {
            "attestation_version": attestation["attestation_version"],
            "attestation_schema_sha256": attestation["attestation_schema_sha256"],
            "contract": attestation["contract"],
            "producer_sha256": producer_sha256,
            "manifest": attestation["manifest"],
            "shard": attestation["shard"],
            "syzygy_inventory": attestation["syzygy_inventory"],
            "teacher": attestation["teacher"],
            "counters": attestation["counters"],
        }
    if mutate_attestation:
        mutate_attestation(attestation)
    attestation_bytes = canonical(attestation)
    attestation_sha = hashlib.sha256(attestation_bytes).hexdigest()
    attestation_offset = (384 + len(manifest_bytes) + 63) & ~63
    shard_offset = (attestation_offset + len(attestation_bytes) + 63) & ~63
    header = bytearray(384)
    header[:8] = b"ATOBNDL2"
    put(header, 8, 2, 2)
    put(header, 10, 384, 2)
    put(header, 12, 0x01020304, 4)
    put(header, 20, 3, 4)
    header[24:56] = bytes.fromhex(VALIDATOR.BUNDLE_SCHEMA_SHA256)
    header[56:88] = bytes.fromhex(VALIDATOR.DATA_SCHEMA_SHA256)
    header[88:120] = bytes.fromhex(VALIDATOR.MANIFEST_SCHEMA_SHA256)
    header[120:152] = bytes.fromhex(VALIDATOR.ATTESTATION_SCHEMA_SHA256)
    put(header, 152, 384, 8)
    put(header, 160, len(manifest_bytes), 8)
    header[168:200] = bytes.fromhex(manifest_sha)
    put(header, 200, attestation_offset, 8)
    put(header, 208, len(attestation_bytes), 8)
    header[216:248] = bytes.fromhex(attestation_sha)
    put(header, 248, shard_offset, 8)
    put(header, 256, len(shard), 8)
    header[264:296] = bytes.fromhex(shard_sha)
    put(header, 296, 1, 8)
    put(header, 304, 7, 8)
    put(header, 312, 5, 8)
    header[320:352] = bytes.fromhex(inventory_sha)
    if mutate_header:
        mutate_header(header)
    with bundle.open("wb") as output:
        output.write(header)
        output.write(manifest_bytes)
        output.write(bytes(attestation_offset - 384 - len(manifest_bytes)))
        output.write(attestation_bytes)
        output.write(bytes(shard_offset - attestation_offset - len(attestation_bytes)))
        output.write(shard)


@pytest.mark.parametrize(
    ("teacher_mode", "use_nnue"),
    (("pure", "pure"), ("true", "true")),
)
def test_validator_accepts_both_explicit_teacher_modes(
    tmp_path: Path, teacher_mode: str, use_nnue: str
) -> None:
    bundle = tmp_path / f"{teacher_mode}.bin"
    inventory = tmp_path / "remote-inventory.json"
    build_bundle(bundle, inventory, teacher_mode=teacher_mode, use_nnue=use_nnue)
    report = VALIDATOR.validate_bundle(bundle, tmp_path / "extract", inventory)
    assert report["teacher_mode"] == teacher_mode
    assert report["use_nnue"] == use_nnue
    assert report["tb_probes"] == 7
    assert report["tb_hits"] == 5


def test_validator_binds_optional_v39_producer_sha256(tmp_path: Path) -> None:
    bundle = tmp_path / "producer.bin"
    inventory = tmp_path / "remote-inventory.json"
    producer = "d" * 64
    build_bundle(bundle, inventory, producer_sha256=producer)
    VALIDATOR.validate_bundle(bundle, syzygy_inventory=inventory)

    tampered = tmp_path / "producer-tampered.bin"

    def mutate(attestation: dict[str, object]) -> None:
        attestation["producer_sha256"] = "e" * 64

    build_bundle(
        tampered,
        inventory,
        producer_sha256=producer,
        mutate_attestation=mutate,
    )
    with pytest.raises(VALIDATOR.BundleError, match="producer_sha256"):
        VALIDATOR.validate_bundle(tampered, syzygy_inventory=inventory)


def test_validator_rejects_crossed_teacher_mode(tmp_path: Path) -> None:
    bundle = tmp_path / "crossed.bin"
    inventory = tmp_path / "remote-inventory.json"
    build_bundle(bundle, inventory, teacher_mode="pure", use_nnue="true")
    with pytest.raises(VALIDATOR.BundleError, match="does not match"):
        VALIDATOR.validate_bundle(bundle, syzygy_inventory=inventory)


def test_validator_rejects_descriptive_teacher_alias(tmp_path: Path) -> None:
    bundle = tmp_path / "alias.bin"
    inventory = tmp_path / "remote-inventory.json"
    build_bundle(bundle, inventory, teacher_mode="legacy-playing", use_nnue="true")
    with pytest.raises(VALIDATOR.BundleError, match="does not match"):
        VALIDATOR.validate_bundle(bundle, syzygy_inventory=inventory)


@pytest.mark.parametrize(
    ("key", "value", "message"),
    (
        ("search_depth_min", 0, "outside producer domains"),
        ("keep_draws", "0.10", "canonical probability"),
        ("filter_checks", 0, "must be a boolean"),
    ),
)
def test_validator_rejects_generation_options_outside_producer_contract(
    tmp_path: Path, key: str, value: object, message: str
) -> None:
    bundle = tmp_path / f"bad-{key}.bin"
    inventory = tmp_path / "remote-inventory.json"

    def mutate(manifest: dict[str, object]) -> None:
        generation = manifest["generation"]
        assert isinstance(generation, dict)
        options = generation["options"]
        assert isinstance(options, dict)
        options[key] = value

    build_bundle(bundle, inventory, mutate_manifest=mutate)
    with pytest.raises(VALIDATOR.BundleError, match=message):
        VALIDATOR.validate_bundle(bundle, syzygy_inventory=inventory)


def test_validator_requires_exact_inventory_before_extraction(tmp_path: Path) -> None:
    bundle = tmp_path / "inventory.bin"
    inventory = tmp_path / "remote-inventory.json"
    build_bundle(bundle, inventory)
    inventory.write_bytes(b"changed\n")
    extract = tmp_path / "extract"
    with pytest.raises(VALIDATOR.BundleError, match="inventory SHA-256 mismatch"):
        VALIDATOR.validate_bundle(bundle, extract, inventory)
    assert not extract.exists()


@pytest.mark.parametrize(
    "mutator, message",
    (
        (lambda header: header.__setitem__(352, 1), "reserved"),
        (lambda header: put(header, 312, 8, 8), "counters"),
        (lambda header: header.__setitem__(24, header[24] ^ 1), "bundle schema"),
    ),
)
def test_validator_rejects_header_tampering_before_extraction(
    tmp_path: Path, mutator: Callable[[bytearray], None], message: str
) -> None:
    bundle = tmp_path / "tampered.bin"
    inventory = tmp_path / "remote-inventory.json"
    build_bundle(bundle, inventory, mutate_header=mutator)
    with pytest.raises(VALIDATOR.BundleError, match=message):
        VALIDATOR.validate_bundle(bundle, tmp_path / "extract", inventory)


def test_extraction_never_overwrites_existing_output(tmp_path: Path) -> None:
    bundle = tmp_path / "chunk.bin"
    inventory = tmp_path / "remote-inventory.json"
    build_bundle(bundle, inventory)
    extract = tmp_path / "extract"
    extract.mkdir()
    existing = extract / "dataset.atbin"
    existing.write_bytes(b"owner")
    with pytest.raises(VALIDATOR.BundleError, match="already exists"):
        VALIDATOR.validate_bundle(bundle, extract, inventory)
    assert existing.read_bytes() == b"owner"


def test_validator_streams_shard_in_bounded_blocks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bundle = tmp_path / "streamed.bin"
    inventory = tmp_path / "remote-inventory.json"
    build_bundle(bundle, inventory)
    original = VALIDATOR.read_exact

    def guarded(stream: object, size: int, label: str) -> bytes:
        if label == "Atomic BIN V2 shard":
            assert size <= 64
        return original(stream, size, label)

    monkeypatch.setattr(VALIDATOR, "COPY_BLOCK", 64)
    monkeypatch.setattr(VALIDATOR, "read_exact", guarded)
    VALIDATOR.validate_bundle(bundle, tmp_path / "extract", inventory)


def test_schema_hashes_match_tracked_contract_bytes() -> None:
    expected = {
        "atomic-bin-v2-manifest-v2.json": VALIDATOR.MANIFEST_SCHEMA_SHA256,
        "atomic-datagen-attestation-v1.json": VALIDATOR.ATTESTATION_SCHEMA_SHA256,
        "atomic-openbench-datagen-bundle-v2.json": VALIDATOR.BUNDLE_SCHEMA_SHA256,
    }
    for filename, digest in expected.items():
        assert hashlib.sha256((ROOT / "schemas" / filename).read_bytes()).hexdigest() == digest


def test_v2_validator_rejects_v1_magic(tmp_path: Path) -> None:
    bundle = tmp_path / "legacy.bin"
    bundle.write_bytes(b"ATOBNDL1" + bytes(376))
    inventory = tmp_path / "remote-inventory.json"
    inventory.write_bytes(b"[]\n")
    with pytest.raises(VALIDATOR.BundleError, match="magic"):
        VALIDATOR.validate_bundle(bundle, syzygy_inventory=inventory)
