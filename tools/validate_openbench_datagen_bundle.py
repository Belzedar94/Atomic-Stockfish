#!/usr/bin/env python3
"""Validate the frozen one-file Atomic OpenBench datagen bundle."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
from typing import BinaryIO


MAGIC = b"ATOBNDL1"
VERSION = 1
HEADER_BYTES = 256
ENDIAN_MARKER = 0x01020304
ALIGNMENT = 64
ENTRY_COUNT = 2
COPY_BLOCK = 1024 * 1024
MAX_MANIFEST_BYTES = 64 * 1024 * 1024
BUNDLE_SCHEMA_SHA256 = "f8155e881b6d1de53341d5084a0e253c91318383bceea2c235e667893284b9dc"
DATA_SCHEMA_SHA256 = "0352b036f2a140c609e3eb9c9d635dc553e8d77253d8faa92437390f5cf93cb6"
MANIFEST_SCHEMA_SHA256 = "83d63922df3ac4a0c81a21ec9d9fd9e180efe50f26efee62fe01710e09da5b42"


class BundleError(ValueError):
    pass


def read_exact(stream: BinaryIO, size: int, label: str) -> bytes:
    payload = stream.read(size)
    if len(payload) != size:
        raise BundleError(f"truncated {label}")
    return payload


def u16(header: bytes, offset: int) -> int:
    return int.from_bytes(header[offset : offset + 2], "little")


def u32(header: bytes, offset: int) -> int:
    return int.from_bytes(header[offset : offset + 4], "little")


def u64(header: bytes, offset: int) -> int:
    return int.from_bytes(header[offset : offset + 8], "little")


def safe_entry_name(value: object) -> str:
    if not isinstance(value, str) or not value.endswith(".atbin"):
        raise BundleError("embedded manifest shard file is invalid")
    if not value or Path(value).name != value or value in (".", ".."):
        raise BundleError("embedded manifest shard file is not a safe basename")
    return value


def identity(file: BinaryIO) -> tuple[int, int]:
    status = os.fstat(file.fileno())
    return status.st_dev, status.st_ino


def cleanup_owned(path: Path, expected: tuple[int, int]) -> None:
    try:
        status = path.lstat()
        if (status.st_dev, status.st_ino) == expected:
            path.unlink()
    except FileNotFoundError:
        pass


def validate_bundle(path: Path, extract_dir: Path | None = None) -> dict[str, object]:
    bundle = path.expanduser().resolve()
    if not bundle.is_file():
        raise BundleError(f"bundle does not exist: {bundle}")

    created: list[tuple[Path, tuple[int, int]]] = []
    if extract_dir is not None:
        extract_dir = extract_dir.expanduser().resolve()
        extract_dir.mkdir(parents=True, exist_ok=True)

    try:
        with bundle.open("rb") as stream:
            header = read_exact(stream, HEADER_BYTES, "bundle header")
            if header[:8] != MAGIC:
                raise BundleError("invalid bundle magic")
            if (
                u16(header, 8) != VERSION
                or u16(header, 10) != HEADER_BYTES
                or u32(header, 12) != ENDIAN_MARKER
                or u32(header, 16) != 0
                or u32(header, 20) != ENTRY_COUNT
            ):
                raise BundleError("unsupported bundle framing")
            expected_schemas = (
                (24, BUNDLE_SCHEMA_SHA256, "bundle"),
                (56, DATA_SCHEMA_SHA256, "data"),
                (88, MANIFEST_SCHEMA_SHA256, "manifest"),
            )
            for offset, expected, label in expected_schemas:
                if header[offset : offset + 32].hex() != expected:
                    raise BundleError(f"{label} schema SHA-256 mismatch")

            manifest_offset = u64(header, 120)
            manifest_bytes = u64(header, 128)
            manifest_sha = header[136:168].hex()
            payload_offset = u64(header, 168)
            payload_bytes = u64(header, 176)
            payload_sha = header[184:216].hex()
            records = u64(header, 216)
            if any(header[224:256]):
                raise BundleError("bundle reserved header bytes are nonzero")
            if manifest_offset != HEADER_BYTES or manifest_bytes > MAX_MANIFEST_BYTES:
                raise BundleError("invalid manifest offset or size")
            expected_payload_offset = (
                manifest_offset + manifest_bytes + ALIGNMENT - 1
            ) & ~(ALIGNMENT - 1)
            if payload_offset != expected_payload_offset:
                raise BundleError("payload is not canonically 64-byte aligned")

            manifest_payload = read_exact(stream, manifest_bytes, "manifest payload")
            if hashlib.sha256(manifest_payload).hexdigest() != manifest_sha:
                raise BundleError("SHA-256 mismatch for dataset.atbin.manifest.json")
            if not manifest_payload.endswith(b"\n"):
                raise BundleError("manifest is not canonical newline-terminated JSON")

            try:
                manifest = json.loads(manifest_payload)
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise BundleError("embedded manifest is not valid UTF-8 JSON") from exc
            if manifest.get("format") != "atomic-bin-v2":
                raise BundleError("embedded manifest format is not atomic-bin-v2")
            generation = manifest.get("generation")
            if not isinstance(generation, dict) or generation.get("use_nnue") != "pure":
                raise BundleError("embedded manifest does not attest Use NNUE=pure")
            if generation.get("atomic960") is not False:
                raise BundleError("OpenBench bootstrap bundle must attest Atomic960=false")
            shards = manifest.get("shards")
            if not isinstance(shards, list) or len(shards) != 1:
                raise BundleError("embedded manifest must describe exactly one shard")
            descriptor = shards[0]
            if not isinstance(descriptor, dict):
                raise BundleError("embedded shard descriptor is invalid")
            shard_name = safe_entry_name(descriptor.get("file"))
            expected_descriptor = {
                "records": str(records),
                "bytes": str(payload_bytes),
                "sha256": payload_sha,
            }
            for key, expected in expected_descriptor.items():
                if descriptor.get(key) != expected:
                    raise BundleError(f"embedded manifest shard {key} mismatch")
            statistics = manifest.get("statistics")
            if not isinstance(statistics, dict) or statistics.get("records") != str(records):
                raise BundleError("embedded manifest statistics do not match the shard")

            payload_destination = None
            manifest_destination = None
            if extract_dir is not None:
                payload_destination = extract_dir / shard_name
                manifest_destination = extract_dir / f"{shard_name}.manifest.json"
                for destination in (payload_destination, manifest_destination):
                    if destination.exists() or destination.is_symlink():
                        raise BundleError(f"extraction output already exists: {destination}")

            padding = read_exact(
                stream,
                payload_offset - manifest_offset - manifest_bytes,
                "alignment padding",
            )
            if any(padding):
                raise BundleError("bundle alignment padding is nonzero")

            output = None
            if payload_destination is not None:
                output = payload_destination.open("xb")
                created.append((payload_destination, identity(output)))
            digest = hashlib.sha256()
            prefix = bytearray()
            remaining = payload_bytes
            try:
                while remaining:
                    block = read_exact(
                        stream, min(remaining, COPY_BLOCK), "dataset.atbin payload"
                    )
                    digest.update(block)
                    if len(prefix) < 96:
                        prefix.extend(block[: 96 - len(prefix)])
                    if output is not None:
                        output.write(block)
                    remaining -= len(block)
            finally:
                if output is not None:
                    output.flush()
                    os.fsync(output.fileno())
                    output.close()
            if digest.hexdigest() != payload_sha:
                raise BundleError("SHA-256 mismatch for dataset.atbin")
            if stream.read(1):
                raise BundleError("bundle contains trailing bytes")

        if len(prefix) < 96 or prefix[:8] != b"ATBINV2\0":
            raise BundleError("embedded shard is not Atomic BIN V2")
        shard_header_bytes = int.from_bytes(prefix[10:12], "little")
        record_bytes = int.from_bytes(prefix[16:20], "little")
        shard_records = int.from_bytes(prefix[56:64], "little")
        if shard_header_bytes != 96 or record_bytes != 64:
            raise BundleError("embedded shard has unsupported framing")
        if shard_records != records or payload_bytes != 96 + records * 64:
            raise BundleError("embedded shard count/size differs from bundle header")

        # The manifest is the extraction commit marker. Publish it only after
        # the entire payload has passed header, size and SHA authentication.
        if manifest_destination is not None:
            manifest_output = manifest_destination.open("xb")
            created.append((manifest_destination, identity(manifest_output)))
            try:
                manifest_output.write(manifest_payload)
                manifest_output.flush()
                os.fsync(manifest_output.fileno())
            finally:
                manifest_output.close()

        return {
            "format": "atomic-openbench-datagen-bundle-v1",
            "records": records,
            "shard_file": shard_name,
            "shard_sha256": payload_sha,
            "manifest_sha256": manifest_sha,
        }
    except Exception:
        for created_path, expected_identity in reversed(created):
            cleanup_owned(created_path, expected_identity)
        raise


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("bundle", type=Path)
    parser.add_argument("--extract-dir", type=Path)
    args = parser.parse_args(argv)
    try:
        report = validate_bundle(args.bundle, args.extract_dir)
    except (BundleError, OSError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(report, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
