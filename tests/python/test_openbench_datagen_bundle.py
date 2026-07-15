from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
TOOL_PATH = ROOT / "tools" / "validate_openbench_datagen_bundle.py"
SPEC = importlib.util.spec_from_file_location("openbench_bundle_validator", TOOL_PATH)
assert SPEC is not None and SPEC.loader is not None
VALIDATOR = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(VALIDATOR)


def put(header: bytearray, offset: int, value: int, size: int) -> None:
    header[offset : offset + size] = value.to_bytes(size, "little")


def build_bundle(path: Path, *, corrupt: bool = False) -> None:
    shard = bytearray(96 + 64)
    shard[:8] = b"ATBINV2\0"
    shard[8:10] = (2).to_bytes(2, "little")
    shard[10:12] = (96).to_bytes(2, "little")
    shard[12:16] = (0x01020304).to_bytes(4, "little")
    shard[16:20] = (64).to_bytes(4, "little")
    shard[56:64] = (1).to_bytes(8, "little")
    shard_sha = hashlib.sha256(shard).hexdigest()
    manifest = {
        "format": "atomic-bin-v2",
        "generation": {"use_nnue": "pure", "atomic960": False},
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
    manifest_bytes = (
        json.dumps(manifest, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode()
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
