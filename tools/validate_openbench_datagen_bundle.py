#!/usr/bin/env python3
"""Validate the frozen one-file Atomic OpenBench datagen bundle."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import sys
from typing import BinaryIO, Mapping


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
ATBIN_VERSION = 2
ATBIN_HEADER_BYTES = 96
ATBIN_ENDIAN_MARKER = 0x01020304
ATBIN_RECORD_BYTES = 64
ATBIN_FLAGS = 0
MAX_PLY = 246
MAX_MOVES = 256
MAX_GENERATED_PLY = 4096
UINT32_MAX = (1 << 32) - 1
UINT64_MAX = (1 << 64) - 1
INT32_MIN = -(1 << 31)
INT32_MAX = (1 << 31) - 1
LOWER_HEX_40 = re.compile(r"^[0-9a-f]{40}$")
LOWER_HEX_64 = re.compile(r"^[0-9a-f]{64}$")
UINT64_DECIMAL = re.compile(r"^(0|[1-9][0-9]{0,19})$")
CANONICAL_PROBABILITY = re.compile(r"^(0|1|0\.[0-9]*[1-9])$")
FORBIDDEN_BASENAME_CHARACTERS = frozenset('/\\:\x00<>"|?*')

TOP_LEVEL_ORDER = (
    "manifest_version",
    "manifest_schema_sha256",
    "data_schema_sha256",
    "format",
    "engine",
    "network",
    "book",
    "generation",
    "statistics",
    "shards",
)
OPTION_ORDER = (
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
OPTION_INT32_KEYS = (
    "search_depth_min",
    "search_depth_max",
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
)
OPTION_UINT64_KEYS = ("nodes", "requested_records", "records_per_shard")
OPTION_BOOL_KEYS = (
    "adjudicate_draws_by_score",
    "adjudicate_insufficient",
    "filter_captures",
    "filter_checks",
    "filter_promotions",
    "random_file_name",
    "set_recommended_uci_options_seen",
)


class BundleError(ValueError):
    pass


def no_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise BundleError(f"duplicate JSON key {key!r} in embedded manifest")
        result[key] = value
    return result


def mapping(value: object, label: str) -> Mapping[str, object]:
    if type(value) is not dict:
        raise BundleError(f"embedded manifest {label} must be an object")
    return value


def exact_keys(value: Mapping[str, object], expected: tuple[str, ...], label: str) -> None:
    if set(value) != set(expected):
        raise BundleError(f"embedded manifest {label} does not match the frozen contract")


def require_exact(actual: object, expected: object, label: str) -> None:
    if type(actual) is not type(expected) or actual != expected:
        raise BundleError(f"embedded manifest {label} must be exactly {expected!r}")


def require_string(value: object, label: str) -> str:
    if type(value) is not str:
        raise BundleError(f"embedded manifest {label} must be a string")
    return value


def require_lower_hex(value: object, width: int, label: str) -> str:
    text = require_string(value, label)
    pattern = LOWER_HEX_40 if width == 40 else LOWER_HEX_64
    if pattern.fullmatch(text) is None:
        raise BundleError(
            f"embedded manifest {label} must be exactly {width} lower-case hex digits"
        )
    return text


def require_uint64_decimal(value: object, label: str) -> int:
    text = require_string(value, label)
    if UINT64_DECIMAL.fullmatch(text) is None:
        raise BundleError(f"embedded manifest {label} must be a canonical uint64 string")
    parsed = int(text)
    if parsed > UINT64_MAX:
        raise BundleError(f"embedded manifest {label} exceeds uint64")
    return parsed


def require_int32(value: object, label: str) -> int:
    if type(value) is not int or not INT32_MIN <= value <= INT32_MAX:
        raise BundleError(f"embedded manifest {label} must be an int32")
    return value


def require_uint32(value: object, label: str, minimum: int = 0) -> int:
    if type(value) is not int or not minimum <= value <= UINT32_MAX:
        raise BundleError(f"embedded manifest {label} must be a uint32")
    return value


def require_bool(value: object, label: str) -> bool:
    if type(value) is not bool:
        raise BundleError(f"embedded manifest {label} must be a boolean")
    return value


def require_basename(value: object, label: str, suffix: str = "") -> str:
    text = require_string(value, label)
    if (
        not text
        or text in (".", "..")
        or any(character in FORBIDDEN_BASENAME_CHARACTERS for character in text)
    ):
        raise BundleError(f"embedded manifest {label} must be a portable basename")
    if suffix and (not text.endswith(suffix) or len(text) == len(suffix)):
        raise BundleError(f"embedded manifest {label} must end in {suffix}")
    return text


def canonical_manifest_bytes(manifest: Mapping[str, object]) -> bytes:
    engine = mapping(manifest["engine"], "engine")
    network = mapping(manifest["network"], "network")
    book = mapping(manifest["book"], "book")
    generation = mapping(manifest["generation"], "generation")
    options = mapping(generation["options"], "generation.options")
    statistics = mapping(manifest["statistics"], "statistics")
    shards = manifest["shards"]
    assert type(shards) is list
    ordered = {
        "manifest_version": manifest["manifest_version"],
        "manifest_schema_sha256": manifest["manifest_schema_sha256"],
        "data_schema_sha256": manifest["data_schema_sha256"],
        "format": manifest["format"],
        "engine": {"commit": engine["commit"], "version": engine["version"]},
        "network": {"file": network["file"], "sha256": network["sha256"]},
        "book": {"kind": book["kind"], "file": book["file"], "sha256": book["sha256"]},
        "generation": {
            "resolved_seed": generation["resolved_seed"],
            "atomic960": generation["atomic960"],
            "threads": generation["threads"],
            "hash_mb": generation["hash_mb"],
            "use_nnue": generation["use_nnue"],
            "options": {key: options[key] for key in OPTION_ORDER},
        },
        "statistics": {"records": statistics["records"], "draws": statistics["draws"]},
        "shards": [
            {
                "index": shard["index"],
                "file": shard["file"],
                "records": shard["records"],
                "bytes": shard["bytes"],
                "sha256": shard["sha256"],
            }
            for shard in shards
        ],
    }
    try:
        rendered = json.dumps(
            ordered, allow_nan=False, ensure_ascii=False, separators=(",", ":")
        )
    except (TypeError, ValueError) as exc:
        raise BundleError("embedded manifest cannot be serialized canonically") from exc
    return rendered.encode("utf-8") + b"\n"


def validate_manifest_payload(
    payload: bytes, records: int, payload_bytes: int, payload_sha: str
) -> tuple[dict[str, object], str]:
    if payload.startswith(b"\xef\xbb\xbf") or b"\r" in payload:
        raise BundleError("embedded manifest is not canonical UTF-8 JSON")
    if not payload.endswith(b"\n") or payload.endswith(b"\n\n"):
        raise BundleError("manifest is not canonical newline-terminated JSON")
    try:
        decoded = payload.decode("utf-8")
        manifest = json.loads(decoded, object_pairs_hook=no_duplicate_keys)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BundleError("embedded manifest is not valid UTF-8 JSON") from exc

    manifest_map = mapping(manifest, "root")
    exact_keys(manifest_map, TOP_LEVEL_ORDER, "root")
    require_exact(manifest_map["manifest_version"], 1, "manifest_version")
    require_exact(
        manifest_map["manifest_schema_sha256"],
        MANIFEST_SCHEMA_SHA256,
        "manifest_schema_sha256",
    )
    require_exact(manifest_map["data_schema_sha256"], DATA_SCHEMA_SHA256, "data_schema_sha256")
    require_exact(manifest_map["format"], "atomic-bin-v2", "format")

    engine = mapping(manifest_map["engine"], "engine")
    exact_keys(engine, ("commit", "version"), "engine")
    # OpenBench supplies GIT_SHA_FULL at build time. Unlike the generic V2
    # schema, a distributable bundle must not have an unauthenticated source.
    require_lower_hex(engine["commit"], 40, "engine.commit")
    if not require_string(engine["version"], "engine.version"):
        raise BundleError("embedded manifest engine.version must be nonempty")

    network = mapping(manifest_map["network"], "network")
    exact_keys(network, ("file", "sha256"), "network")
    require_basename(network["file"], "network.file")
    require_lower_hex(network["sha256"], 64, "network.sha256")

    book = mapping(manifest_map["book"], "book")
    exact_keys(book, ("kind", "file", "sha256"), "book")
    kind = require_string(book["kind"], "book.kind")
    if kind == "file":
        require_basename(book["file"], "book.file")
        require_lower_hex(book["sha256"], 64, "book.sha256")
    elif kind == "builtin-startpos":
        require_exact(book["file"], None, "book.file")
        require_exact(book["sha256"], None, "book.sha256")
    else:
        raise BundleError("embedded manifest book.kind is unsupported")

    generation = mapping(manifest_map["generation"], "generation")
    exact_keys(
        generation,
        ("resolved_seed", "atomic960", "threads", "hash_mb", "use_nnue", "options"),
        "generation",
    )
    require_uint64_decimal(generation["resolved_seed"], "generation.resolved_seed")
    require_exact(generation["atomic960"], False, "generation.atomic960")
    require_uint32(generation["threads"], "generation.threads", minimum=1)
    if require_uint64_decimal(generation["hash_mb"], "generation.hash_mb") == 0:
        raise BundleError("embedded manifest generation.hash_mb must be positive")
    require_exact(generation["use_nnue"], "pure", "generation.use_nnue")

    options = mapping(generation["options"], "generation.options")
    exact_keys(options, OPTION_ORDER, "generation.options")
    for key in OPTION_INT32_KEYS:
        require_int32(options[key], f"generation.options.{key}")
    parsed_u64 = {
        key: require_uint64_decimal(options[key], f"generation.options.{key}")
        for key in OPTION_UINT64_KEYS
    }
    keep_draws = require_string(options["keep_draws"], "generation.options.keep_draws")
    if len(keep_draws) > 4096 or CANONICAL_PROBABILITY.fullmatch(keep_draws) is None:
        raise BundleError("embedded manifest generation.options.keep_draws is not canonical")
    for key in OPTION_BOOL_KEYS:
        require_bool(options[key], f"generation.options.{key}")

    if (
        options["search_depth_min"] <= 0
        or options["search_depth_max"] < options["search_depth_min"]
        or options["search_depth_max"] >= MAX_PLY
        or options["eval_limit"] <= 0
        or options["eval_limit"] > 32767
        or options["eval_diff_limit"] < 0
        or options["random_move_min_ply"] < -1
        or options["random_move_max_ply"] < 0
        or options["random_move_max_ply"] > MAX_GENERATED_PLY
        or (
            options["random_move_min_ply"] != -1
            and options["random_move_max_ply"] < options["random_move_min_ply"]
        )
        or options["random_move_count"] < 0
        or options["random_move_count"] > MAX_GENERATED_PLY
        or options["random_move_like_apery"] < 0
        or options["random_multi_pv"] < 0
        or options["random_multi_pv"] > MAX_MOVES
        or options["random_multi_pv_diff"] < 0
        or options["random_multi_pv_depth"] < options["search_depth_max"]
        or options["random_multi_pv_depth"] >= MAX_PLY
        or options["write_min_ply"] < 0
        or options["write_max_ply"] <= options["write_min_ply"]
        or options["write_max_ply"] > MAX_GENERATED_PLY
    ):
        raise BundleError("embedded manifest generation options are outside producer domains")
    if options["random_file_name"] is not False:
        raise BundleError("OpenBench bundle must attest random_file_name=false")

    statistics = mapping(manifest_map["statistics"], "statistics")
    exact_keys(statistics, ("records", "draws"), "statistics")
    manifest_records = require_uint64_decimal(statistics["records"], "statistics.records")
    draws = require_uint64_decimal(statistics["draws"], "statistics.draws")
    if manifest_records != records or manifest_records == 0 or draws > manifest_records:
        raise BundleError("embedded manifest statistics do not match the bundle")
    if (
        parsed_u64["requested_records"] != records
        or parsed_u64["records_per_shard"] != records
    ):
        raise BundleError("embedded manifest generation record counts do not match the bundle")

    shards = manifest_map["shards"]
    if type(shards) is not list or len(shards) != 1:
        raise BundleError("embedded manifest must describe exactly one shard")
    descriptor = mapping(shards[0], "shards[0]")
    exact_keys(descriptor, ("index", "file", "records", "bytes", "sha256"), "shards[0]")
    require_exact(descriptor["index"], 0, "shards[0].index")
    shard_name = require_basename(descriptor["file"], "shards[0].file", suffix=".atbin")
    if (
        require_uint64_decimal(descriptor["records"], "shards[0].records") != records
        or require_uint64_decimal(descriptor["bytes"], "shards[0].bytes") != payload_bytes
        or require_lower_hex(descriptor["sha256"], 64, "shards[0].sha256") != payload_sha
    ):
        raise BundleError("embedded manifest shard descriptor differs from the bundle")

    if payload != canonical_manifest_bytes(manifest_map):
        raise BundleError("embedded manifest is not byte-exact canonical JSON")
    return dict(manifest_map), shard_name


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
            manifest, shard_name = validate_manifest_payload(
                manifest_payload, records, payload_bytes, payload_sha
            )

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

        if len(prefix) < ATBIN_HEADER_BYTES or prefix[:8] != b"ATBINV2\0":
            raise BundleError("embedded shard is not Atomic BIN V2")
        shard_version = u16(prefix, 8)
        shard_header_bytes = u16(prefix, 10)
        shard_endian_marker = u32(prefix, 12)
        record_bytes = u32(prefix, 16)
        shard_flags = u32(prefix, 20)
        shard_schema_sha256 = prefix[24:56].hex()
        shard_records = u64(prefix, 56)
        if (
            shard_version != ATBIN_VERSION
            or shard_header_bytes != ATBIN_HEADER_BYTES
            or shard_endian_marker != ATBIN_ENDIAN_MARKER
            or record_bytes != ATBIN_RECORD_BYTES
            or shard_flags != ATBIN_FLAGS
            or shard_schema_sha256 != DATA_SCHEMA_SHA256
            or any(prefix[64:96])
        ):
            raise BundleError("embedded shard has unsupported or noncanonical framing")
        if (
            shard_records != records
            or shard_records == 0
            or payload_bytes != ATBIN_HEADER_BYTES + records * ATBIN_RECORD_BYTES
        ):
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
