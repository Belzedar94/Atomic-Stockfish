from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path
from typing import Callable

import pytest


ROOT = Path(__file__).resolve().parents[2]
TOOL_PATH = ROOT / "tools" / "validate_openbench_datagen_bundle.py"
SPEC = importlib.util.spec_from_file_location("openbench_bundle_validator", TOOL_PATH)
assert SPEC is not None and SPEC.loader is not None
VALIDATOR = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(VALIDATOR)


def put(header: bytearray, offset: int, value: int, size: int) -> None:
    header[offset : offset + size] = value.to_bytes(size, "little")


def build_bundle(
    path: Path,
    *,
    corrupt: bool = False,
    shard_mutator: Callable[[bytearray], None] | None = None,
    manifest_mutator: Callable[[dict[str, object]], None] | None = None,
    manifest_bytes_mutator: Callable[[bytes], bytes] | None = None,
) -> None:
    shard = bytearray(96 + 64)
    shard[:8] = b"ATBINV2\0"
    shard[8:10] = (2).to_bytes(2, "little")
    shard[10:12] = (96).to_bytes(2, "little")
    shard[12:16] = (0x01020304).to_bytes(4, "little")
    shard[16:20] = (64).to_bytes(4, "little")
    shard[20:24] = (0).to_bytes(4, "little")
    shard[24:56] = bytes.fromhex(VALIDATOR.DATA_SCHEMA_SHA256)
    shard[56:64] = (1).to_bytes(8, "little")
    if shard_mutator is not None:
        shard_mutator(shard)
    shard_sha = hashlib.sha256(shard).hexdigest()
    manifest = {
        "manifest_version": 1,
        "manifest_schema_sha256": VALIDATOR.MANIFEST_SCHEMA_SHA256,
        "data_schema_sha256": VALIDATOR.DATA_SCHEMA_SHA256,
        "format": "atomic-bin-v2",
        "engine": {"commit": "a" * 40, "version": "Atomic-Stockfish test"},
        "network": {"file": "atomic.nnue", "sha256": "b" * 64},
        "book": {"kind": "file", "file": "atomic.epd", "sha256": "c" * 64},
        "generation": {
            "resolved_seed": "202607150500000",
            "atomic960": False,
            "threads": 30,
            "hash_mb": "512",
            "use_nnue": "pure",
            "options": {
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
            },
        },
        "statistics": {"records": "1", "draws": "0"},
        "shards": [
            {
                "index": 0,
                "file": "dataset.atbin",
                "records": "1",
                "bytes": str(len(shard)),
                "sha256": shard_sha,
            }
        ],
    }
    if manifest_mutator is not None:
        manifest_mutator(manifest)
    manifest_bytes = (
        json.dumps(
            manifest,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
        )
        + "\n"
    ).encode()
    if manifest_bytes_mutator is not None:
        manifest_bytes = manifest_bytes_mutator(manifest_bytes)
    manifest_sha = hashlib.sha256(manifest_bytes).digest()
    payload_offset = (256 + len(manifest_bytes) + 63) & ~63

    header = bytearray(256)
    header[:8] = b"ATOBNDL1"
    put(header, 8, 1, 2)
    put(header, 10, 256, 2)
    put(header, 12, 0x01020304, 4)
    put(header, 20, 2, 4)
    header[24:56] = bytes.fromhex(VALIDATOR.BUNDLE_SCHEMA_SHA256)
    header[56:88] = bytes.fromhex(VALIDATOR.DATA_SCHEMA_SHA256)
    header[88:120] = bytes.fromhex(VALIDATOR.MANIFEST_SCHEMA_SHA256)
    put(header, 120, 256, 8)
    put(header, 128, len(manifest_bytes), 8)
    header[136:168] = manifest_sha
    put(header, 168, payload_offset, 8)
    put(header, 176, len(shard), 8)
    header[184:216] = hashlib.sha256(shard).digest()
    put(header, 216, 1, 8)

    wire_shard = bytearray(shard)
    if corrupt:
        wire_shard[-1] ^= 1

    with path.open("wb") as output:
        output.write(header)
        output.write(manifest_bytes)
        output.write(bytes(payload_offset - 256 - len(manifest_bytes)))
        output.write(wire_shard)


def test_validator_accepts_and_extracts_frozen_bundle(tmp_path: Path) -> None:
    bundle = tmp_path / "chunk.bin"
    build_bundle(bundle)
    report = VALIDATOR.validate_bundle(bundle, tmp_path / "extract")
    assert report["records"] == 1
    assert (tmp_path / "extract" / "dataset.atbin").stat().st_size == 160
    assert (tmp_path / "extract" / "dataset.atbin.manifest.json").is_file()


def test_validator_rejects_corrupt_entry_hash(tmp_path: Path) -> None:
    bundle = tmp_path / "corrupt.bin"
    build_bundle(bundle, corrupt=True)
    extract = tmp_path / "extract"
    with pytest.raises(VALIDATOR.BundleError, match="SHA-256 mismatch"):
        VALIDATOR.validate_bundle(bundle, extract)
    assert not (extract / "dataset.atbin").exists()
    assert not (extract / "dataset.atbin.manifest.json").exists()


def test_extractor_refuses_existing_output_without_modifying_it(tmp_path: Path) -> None:
    bundle = tmp_path / "chunk.bin"
    build_bundle(bundle)
    extract = tmp_path / "extract"
    extract.mkdir()
    existing = extract / "dataset.atbin"
    existing.write_bytes(b"owner-data")
    with pytest.raises(VALIDATOR.BundleError, match="already exists"):
        VALIDATOR.validate_bundle(bundle, extract)
    assert existing.read_bytes() == b"owner-data"
    assert not (extract / "dataset.atbin.manifest.json").exists()


def test_extractor_refuses_symlink_without_touching_target(tmp_path: Path) -> None:
    bundle = tmp_path / "chunk.bin"
    build_bundle(bundle)
    extract = tmp_path / "extract"
    extract.mkdir()
    target = tmp_path / "outside.atbin"
    target.write_bytes(b"outside-owner")
    link = extract / "dataset.atbin"
    try:
        os.symlink(target, link)
    except OSError as exc:
        pytest.skip(f"symlink creation unavailable: {exc}")
    with pytest.raises(VALIDATOR.BundleError, match="already exists"):
        VALIDATOR.validate_bundle(bundle, extract)
    assert target.read_bytes() == b"outside-owner"
    assert link.is_symlink()


def test_frozen_bundle_schema_hash_matches_tracked_file() -> None:
    schema = ROOT / "schemas" / "atomic-openbench-datagen-bundle-v1.json"
    assert hashlib.sha256(schema.read_bytes()).hexdigest() == VALIDATOR.BUNDLE_SCHEMA_SHA256


@pytest.mark.parametrize(
    "shard_mutator",
    [
        lambda shard: shard.__setitem__(slice(0, 8), b"BADBINV2"),
        lambda shard: put(shard, 8, 999, 2),
        lambda shard: put(shard, 10, 95, 2),
        lambda shard: put(shard, 12, 0x04030201, 4),
        lambda shard: put(shard, 16, 63, 4),
        lambda shard: put(shard, 20, 1, 4),
        lambda shard: shard.__setitem__(24, shard[24] ^ 1),
        lambda shard: shard.__setitem__(64, 1),
    ],
    ids=(
        "magic",
        "version-999",
        "header-size",
        "endian",
        "record-size",
        "flags",
        "schema",
        "reserved",
    ),
)
def test_validator_rejects_every_noncanonical_inner_header_field(
    tmp_path: Path, shard_mutator: Callable[[bytearray], None]
) -> None:
    bundle = tmp_path / "bad-inner-header.bin"
    build_bundle(bundle, shard_mutator=shard_mutator)
    with pytest.raises(VALIDATOR.BundleError, match="Atomic BIN V2|framing"):
        VALIDATOR.validate_bundle(bundle)


def test_validator_rejects_inner_record_count_mismatch(tmp_path: Path) -> None:
    bundle = tmp_path / "bad-count.bin"
    build_bundle(bundle, shard_mutator=lambda shard: put(shard, 56, 2, 8))
    with pytest.raises(VALIDATOR.BundleError, match="count/size"):
        VALIDATOR.validate_bundle(bundle)


def test_validator_rejects_duplicate_manifest_keys(tmp_path: Path) -> None:
    bundle = tmp_path / "duplicate-manifest-key.bin"

    def duplicate_format(payload: bytes) -> bytes:
        needle = b'"format":"atomic-bin-v2"'
        return payload.replace(needle, needle + b',"format":"atomic-bin-v2"', 1)

    build_bundle(bundle, manifest_bytes_mutator=duplicate_format)
    with pytest.raises(VALIDATOR.BundleError, match="duplicate JSON key"):
        VALIDATOR.validate_bundle(bundle)


def test_validator_rejects_noncanonical_manifest_bytes(tmp_path: Path) -> None:
    bundle = tmp_path / "noncanonical-manifest.bin"
    build_bundle(
        bundle,
        manifest_bytes_mutator=lambda payload: payload.replace(b',"engine"', b', "engine"', 1),
    )
    with pytest.raises(VALIDATOR.BundleError, match="byte-exact canonical JSON"):
        VALIDATOR.validate_bundle(bundle)


@pytest.mark.parametrize(
    "manifest_mutator",
    [
        lambda manifest: manifest.pop("engine"),
        lambda manifest: manifest["network"].pop("sha256"),
        lambda manifest: manifest["book"].pop("sha256"),
        lambda manifest: manifest["generation"]["options"].pop("random_multi_pv_diff"),
    ],
    ids=("source", "teacher", "book", "generation-option"),
)
def test_validator_rejects_incomplete_provenance_manifest(
    tmp_path: Path, manifest_mutator: Callable[[dict[str, object]], None]
) -> None:
    bundle = tmp_path / "incomplete-manifest.bin"
    build_bundle(bundle, manifest_mutator=manifest_mutator)
    with pytest.raises(VALIDATOR.BundleError, match="frozen contract"):
        VALIDATOR.validate_bundle(bundle)


def test_validator_rejects_unauthenticated_source_commit(tmp_path: Path) -> None:
    bundle = tmp_path / "unknown-source.bin"
    build_bundle(
        bundle,
        manifest_mutator=lambda manifest: manifest["engine"].__setitem__("commit", "unknown"),
    )
    with pytest.raises(VALIDATOR.BundleError, match="engine.commit"):
        VALIDATOR.validate_bundle(bundle)


@pytest.mark.parametrize(
    "keep_draws",
    (
        "0.10000000000000001",
        "0.333333333333333333333333333333333333",
    ),
)
def test_validator_rejects_keep_draws_that_cpp_cannot_round_trip(
    tmp_path: Path, keep_draws: str
) -> None:
    bundle = tmp_path / "non-round-tripping-keep-draws.bin"
    build_bundle(
        bundle,
        manifest_mutator=lambda manifest: manifest["generation"]["options"].__setitem__(
            "keep_draws", keep_draws
        ),
    )
    with pytest.raises(VALIDATOR.BundleError, match="does not round-trip exactly"):
        VALIDATOR.validate_bundle(bundle)


def test_validator_reports_escaped_lone_surrogate_as_bundle_error(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    bundle = tmp_path / "lone-surrogate.bin"

    def inject_surrogate(payload: bytes) -> bytes:
        return payload.replace(
            b'"version":"Atomic-Stockfish test"', b'"version":"\\ud800"', 1
        )

    build_bundle(bundle, manifest_bytes_mutator=inject_surrogate)
    with pytest.raises(VALIDATOR.BundleError, match="cannot be serialized canonically"):
        VALIDATOR.validate_bundle(bundle)

    assert VALIDATOR.main([str(bundle)]) == 2
    captured = capsys.readouterr()
    assert "ERROR: embedded manifest cannot be serialized canonically" in captured.err
    assert "Traceback" not in captured.err
