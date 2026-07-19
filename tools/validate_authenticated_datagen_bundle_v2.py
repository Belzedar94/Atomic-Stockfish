#!/usr/bin/env python3
"""Validate and optionally extract an authenticated ATOBNDL2 datagen bundle."""

from __future__ import annotations

import argparse
from decimal import Decimal, InvalidOperation
import hashlib
import json
import os
from pathlib import Path
import re
import sys
from typing import BinaryIO, Mapping


MAGIC = b"ATOBNDL2"
VERSION = 2
HEADER_BYTES = 384
ENDIAN_MARKER = 0x01020304
ALIGNMENT = 64
ENTRY_COUNT = 3
COPY_BLOCK = 1024 * 1024
MAX_JSON_BYTES = 64 * 1024 * 1024
BUNDLE_SCHEMA_SHA256 = "fac3b8fa1c31e543a6483c59f8f2a2d895ceb067e789daef580b8439849e6aca"
DATA_SCHEMA_SHA256 = "0352b036f2a140c609e3eb9c9d635dc553e8d77253d8faa92437390f5cf93cb6"
MANIFEST_SCHEMA_SHA256 = "a99e2fccaf9e01bdd391d1d16b432597ae7b6cdbbb02b4fc9e077dbeb3643b31"
ATTESTATION_SCHEMA_SHA256 = "38937506e50988317e3bf4cdd2c964e4934386123abf7dfd502af77ede6189d7"
ATBIN_VERSION = 2
ATBIN_HEADER_BYTES = 96
ATBIN_RECORD_BYTES = 64
MAX_PLY = 246
MAX_MOVES = 256
MAX_GENERATED_PLY = 4096
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
GENERATION_ORDER = (
    "resolved_seed",
    "atomic960",
    "threads",
    "hash_mb",
    "teacher_mode",
    "use_nnue",
    "options",
    "syzygy",
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
SYZYGY_ORDER = (
    "inventory_sha256",
    "cardinality",
    "probe_limit",
    "probe_depth",
    "rule50",
    "wdl_suffix",
    "dtz_suffix",
)
ATTESTATION_ORDER = (
    "attestation_version",
    "attestation_schema_sha256",
    "contract",
    "manifest",
    "shard",
    "syzygy_inventory",
    "teacher",
    "counters",
)
ATTESTATION_WITH_PRODUCER_ORDER = (
    "attestation_version",
    "attestation_schema_sha256",
    "contract",
    "producer_sha256",
    "manifest",
    "shard",
    "syzygy_inventory",
    "teacher",
    "counters",
)


class BundleError(ValueError):
    pass


def no_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise BundleError(f"duplicate JSON key {key!r}")
        result[key] = value
    return result


def mapping(value: object, label: str) -> Mapping[str, object]:
    if type(value) is not dict:
        raise BundleError(f"{label} must be an object")
    return value


def ordered_keys(value: Mapping[str, object], expected: tuple[str, ...], label: str) -> None:
    if tuple(value) != expected:
        raise BundleError(f"{label} does not match the canonical V2 contract")


def exact(actual: object, expected: object, label: str) -> None:
    if type(actual) is not type(expected) or actual != expected:
        raise BundleError(f"{label} must be exactly {expected!r}")


def string(value: object, label: str) -> str:
    if type(value) is not str:
        raise BundleError(f"{label} must be a string")
    return value


def lower_hex(value: object, width: int, label: str) -> str:
    text = string(value, label)
    pattern = LOWER_HEX_40 if width == 40 else LOWER_HEX_64
    if pattern.fullmatch(text) is None:
        raise BundleError(f"{label} must be {width} lower-case hexadecimal digits")
    return text


def uint_string(value: object, label: str, *, positive: bool = False) -> int:
    text = string(value, label)
    if UINT64_DECIMAL.fullmatch(text) is None:
        raise BundleError(f"{label} must be a canonical uint64 string")
    parsed = int(text)
    if parsed >= 1 << 64 or (positive and parsed == 0):
        raise BundleError(f"{label} is outside its uint64 domain")
    return parsed


def int32(value: object, label: str) -> int:
    if type(value) is not int or not INT32_MIN <= value <= INT32_MAX:
        raise BundleError(f"{label} must be an int32")
    return value


def boolean(value: object, label: str) -> bool:
    if type(value) is not bool:
        raise BundleError(f"{label} must be a boolean")
    return value


def keep_draws(value: object, label: str) -> str:
    text = string(value, label)
    if len(text) > 4096 or CANONICAL_PROBABILITY.fullmatch(text) is None:
        raise BundleError(f"{label} is not a canonical probability")
    try:
        effective = Decimal(repr(float(text)))
        requested = Decimal(text)
    except (InvalidOperation, OverflowError, ValueError) as exc:
        raise BundleError(f"{label} is not a finite binary64 value") from exc
    if effective != requested:
        raise BundleError(f"{label} does not round-trip through the producer")
    return text


def basename(value: object, label: str, suffix: str = "") -> str:
    text = string(value, label)
    if (
        not text
        or text in (".", "..")
        or any(character in FORBIDDEN_BASENAME_CHARACTERS for character in text)
        or (suffix and (not text.endswith(suffix) or len(text) == len(suffix)))
    ):
        raise BundleError(f"{label} must be a portable basename")
    return text


def parse_canonical_json(payload: bytes, label: str) -> dict[str, object]:
    if payload.startswith(b"\xef\xbb\xbf") or b"\r" in payload:
        raise BundleError(f"{label} is not canonical UTF-8 JSON")
    if not payload.endswith(b"\n") or payload.endswith(b"\n\n"):
        raise BundleError(f"{label} must have exactly one trailing LF")
    try:
        document = json.loads(payload.decode("utf-8"), object_pairs_hook=no_duplicate_keys)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BundleError(f"{label} is not valid UTF-8 JSON") from exc
    result = dict(mapping(document, label))
    try:
        canonical = (
            json.dumps(result, allow_nan=False, ensure_ascii=False, separators=(",", ":"))
            + "\n"
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeEncodeError) as exc:
        raise BundleError(f"{label} cannot be serialized canonically") from exc
    if canonical != payload:
        raise BundleError(f"{label} is not byte-exact canonical JSON")
    return result


def validate_manifest(
    payload: bytes,
    *,
    records: int,
    shard_bytes: int,
    shard_sha: str,
    tb_probes: int,
    tb_hits: int,
    inventory_sha: str,
) -> tuple[dict[str, object], str, str | None]:
    document = parse_canonical_json(payload, "manifest V2")
    ordered_keys(document, TOP_LEVEL_ORDER, "manifest V2 root")
    exact(document["manifest_version"], 2, "manifest_version")
    exact(document["manifest_schema_sha256"], MANIFEST_SCHEMA_SHA256, "manifest schema")
    exact(document["data_schema_sha256"], DATA_SCHEMA_SHA256, "data schema")
    exact(document["format"], "atomic-bin-v2", "format")

    engine = mapping(document["engine"], "engine")
    producer_sha: str | None = None
    if "producer_sha256" in engine:
        ordered_keys(engine, ("commit", "version", "producer_sha256"), "engine")
        producer_sha = lower_hex(engine["producer_sha256"], 64, "engine.producer_sha256")
    else:
        ordered_keys(engine, ("commit", "version"), "engine")
    lower_hex(engine["commit"], 40, "engine.commit")
    if not string(engine["version"], "engine.version"):
        raise BundleError("engine.version must be nonempty")

    network = mapping(document["network"], "network")
    ordered_keys(network, ("file", "sha256"), "network")
    basename(network["file"], "network.file")
    lower_hex(network["sha256"], 64, "network.sha256")

    book = mapping(document["book"], "book")
    ordered_keys(book, ("kind", "file", "sha256"), "book")
    if book["kind"] == "file":
        basename(book["file"], "book.file")
        lower_hex(book["sha256"], 64, "book.sha256")
    elif book["kind"] == "builtin-startpos":
        exact(book["file"], None, "book.file")
        exact(book["sha256"], None, "book.sha256")
    else:
        raise BundleError("book.kind is unsupported")

    generation = mapping(document["generation"], "generation")
    ordered_keys(generation, GENERATION_ORDER, "generation")
    uint_string(generation["resolved_seed"], "generation.resolved_seed")
    exact(generation["atomic960"], False, "generation.atomic960")
    if type(generation["threads"]) is not int or not 1 <= generation["threads"] < 1 << 32:
        raise BundleError("generation.threads is outside uint32")
    uint_string(generation["hash_mb"], "generation.hash_mb", positive=True)
    teacher_mode = string(generation["teacher_mode"], "generation.teacher_mode")
    use_nnue = string(generation["use_nnue"], "generation.use_nnue")
    if (teacher_mode, use_nnue) not in (("pure", "pure"), ("true", "true")):
        raise BundleError("teacher_mode does not match Use NNUE")

    options = mapping(generation["options"], "generation.options")
    ordered_keys(options, OPTION_ORDER, "generation.options")
    for key in OPTION_INT32_KEYS:
        int32(options[key], f"generation.options.{key}")
    parsed_u64 = {
        key: uint_string(options[key], f"generation.options.{key}")
        for key in OPTION_UINT64_KEYS
    }
    requested = parsed_u64["requested_records"]
    per_shard = parsed_u64["records_per_shard"]
    if requested != records or per_shard != records:
        raise BundleError("generation record counts differ from bundle")
    keep_draws(options["keep_draws"], "generation.options.keep_draws")
    for key in OPTION_BOOL_KEYS:
        boolean(options[key], f"generation.options.{key}")
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
        raise BundleError("generation options are outside producer domains")
    if options["random_file_name"] is not False:
        raise BundleError("random_file_name must be false")

    syzygy = mapping(generation["syzygy"], "generation.syzygy")
    ordered_keys(syzygy, SYZYGY_ORDER, "generation.syzygy")
    exact(syzygy["inventory_sha256"], inventory_sha, "inventory SHA")
    exact(syzygy["cardinality"], 6, "Syzygy cardinality")
    exact(syzygy["probe_limit"], 6, "Syzygy probe limit")
    exact(syzygy["probe_depth"], 1, "Syzygy probe depth")
    exact(syzygy["rule50"], True, "Syzygy rule50")
    exact(syzygy["wdl_suffix"], ".atbw", "WDL suffix")
    exact(syzygy["dtz_suffix"], ".atbz", "DTZ suffix")

    statistics = mapping(document["statistics"], "statistics")
    ordered_keys(statistics, ("records", "draws", "tb_probes", "tb_hits"), "statistics")
    exact(uint_string(statistics["records"], "statistics.records", positive=True), records,
          "statistics.records")
    draws = uint_string(statistics["draws"], "statistics.draws")
    exact(uint_string(statistics["tb_probes"], "statistics.tb_probes"), tb_probes,
          "statistics.tb_probes")
    exact(uint_string(statistics["tb_hits"], "statistics.tb_hits"), tb_hits,
          "statistics.tb_hits")
    if draws > records or tb_hits > tb_probes:
        raise BundleError("manifest counters are inconsistent")

    shards = document["shards"]
    if type(shards) is not list or len(shards) != 1:
        raise BundleError("manifest must describe exactly one shard")
    shard = mapping(shards[0], "shards[0]")
    ordered_keys(shard, ("index", "file", "records", "bytes", "sha256"), "shards[0]")
    exact(shard["index"], 0, "shards[0].index")
    shard_name = basename(shard["file"], "shards[0].file", ".atbin")
    exact(uint_string(shard["records"], "shards[0].records", positive=True), records,
          "shards[0].records")
    exact(uint_string(shard["bytes"], "shards[0].bytes", positive=True), shard_bytes,
          "shards[0].bytes")
    exact(shard["sha256"], shard_sha, "shards[0].sha256")
    return document, shard_name, producer_sha


def validate_attestation(
    payload: bytes,
    *,
    manifest_name: str,
    manifest_bytes: int,
    manifest_sha: str,
    shard_name: str,
    shard_bytes: int,
    shard_sha: str,
    inventory_sha: str,
    producer_sha: str | None,
    teacher_mode: str,
    use_nnue: str,
    tb_probes: int,
    tb_hits: int,
) -> dict[str, object]:
    document = parse_canonical_json(payload, "attestation V1")
    ordered_keys(
        document,
        ATTESTATION_WITH_PRODUCER_ORDER if producer_sha is not None else ATTESTATION_ORDER,
        "attestation root",
    )
    exact(document["attestation_version"], 1, "attestation_version")
    exact(document["attestation_schema_sha256"], ATTESTATION_SCHEMA_SHA256,
          "attestation schema")
    exact(document["contract"], "atomic-openbench-authenticated-teacher-syzygy-v2",
          "attestation contract")
    if producer_sha is not None:
        exact(document["producer_sha256"], producer_sha, "attestation producer_sha256")

    def artifact(name: str, expected_name: str, expected_bytes: int, expected_sha: str,
                 expected_schema: str) -> None:
        value = mapping(document[name], name)
        ordered_keys(value, ("file", "bytes", "sha256", "schema_sha256"), name)
        exact(value["file"], expected_name, f"{name}.file")
        exact(uint_string(value["bytes"], f"{name}.bytes", positive=True), expected_bytes,
              f"{name}.bytes")
        exact(value["sha256"], expected_sha, f"{name}.sha256")
        exact(value["schema_sha256"], expected_schema, f"{name}.schema_sha256")

    artifact("manifest", manifest_name, manifest_bytes, manifest_sha, MANIFEST_SCHEMA_SHA256)
    artifact("shard", shard_name, shard_bytes, shard_sha, DATA_SCHEMA_SHA256)
    inventory = mapping(document["syzygy_inventory"], "syzygy_inventory")
    ordered_keys(inventory, ("sha256", "cardinality"), "syzygy_inventory")
    exact(inventory["sha256"], inventory_sha, "syzygy_inventory.sha256")
    exact(inventory["cardinality"], 6, "syzygy_inventory.cardinality")
    teacher = mapping(document["teacher"], "teacher")
    ordered_keys(
        teacher,
        ("mode", "use_nnue", "syzygy_probe_limit", "syzygy_probe_depth",
         "syzygy_50_move_rule"),
        "teacher",
    )
    exact(teacher["mode"], teacher_mode, "teacher.mode")
    exact(teacher["use_nnue"], use_nnue, "teacher.use_nnue")
    exact(teacher["syzygy_probe_limit"], 6, "teacher.syzygy_probe_limit")
    exact(teacher["syzygy_probe_depth"], 1, "teacher.syzygy_probe_depth")
    exact(teacher["syzygy_50_move_rule"], True, "teacher.syzygy_50_move_rule")
    counters = mapping(document["counters"], "counters")
    ordered_keys(counters, ("tb_probes", "tb_hits"), "counters")
    exact(uint_string(counters["tb_probes"], "counters.tb_probes"), tb_probes,
          "counters.tb_probes")
    exact(uint_string(counters["tb_hits"], "counters.tb_hits"), tb_hits,
          "counters.tb_hits")
    return document


def read_exact(stream: BinaryIO, size: int, label: str) -> bytes:
    payload = stream.read(size)
    if len(payload) != size:
        raise BundleError(f"truncated {label}")
    return payload


def u16(value: bytes, offset: int) -> int:
    return int.from_bytes(value[offset:offset + 2], "little")


def u32(value: bytes, offset: int) -> int:
    return int.from_bytes(value[offset:offset + 4], "little")


def u64(value: bytes, offset: int) -> int:
    return int.from_bytes(value[offset:offset + 8], "little")


def file_sha256(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as stream:
        while block := stream.read(COPY_BLOCK):
            digest.update(block)
            size += len(block)
    return digest.hexdigest(), size


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


def validate_bundle(
    path: Path,
    extract_dir: Path | None = None,
    syzygy_inventory: Path | None = None,
) -> dict[str, object]:
    bundle = path.expanduser().resolve()
    if not bundle.is_file():
        raise BundleError(f"bundle does not exist: {bundle}")
    if syzygy_inventory is None:
        raise BundleError("ATOBNDL2 validation requires --syzygy-inventory")
    inventory_candidate = syzygy_inventory.expanduser()
    if inventory_candidate.is_symlink():
        raise BundleError("Syzygy inventory must be an existing regular non-symlink file")
    inventory_path = inventory_candidate.resolve()
    if not inventory_path.is_file():
        raise BundleError("Syzygy inventory must be an existing regular non-symlink file")
    inventory_actual_sha, _ = file_sha256(inventory_path)

    created: list[tuple[Path, tuple[int, int]]] = []
    try:
        with bundle.open("rb") as stream:
            header = read_exact(stream, HEADER_BYTES, "bundle V2 header")
            if header[:8] != MAGIC:
                raise BundleError("invalid ATOBNDL2 magic")
            if (
                u16(header, 8) != VERSION
                or u16(header, 10) != HEADER_BYTES
                or u32(header, 12) != ENDIAN_MARKER
                or u32(header, 16) != 0
                or u32(header, 20) != ENTRY_COUNT
            ):
                raise BundleError("unsupported ATOBNDL2 framing")
            for offset, expected, label in (
                (24, BUNDLE_SCHEMA_SHA256, "bundle"),
                (56, DATA_SCHEMA_SHA256, "data"),
                (88, MANIFEST_SCHEMA_SHA256, "manifest"),
                (120, ATTESTATION_SCHEMA_SHA256, "attestation"),
            ):
                if header[offset:offset + 32].hex() != expected:
                    raise BundleError(f"{label} schema SHA-256 mismatch")
            manifest_offset, manifest_bytes = u64(header, 152), u64(header, 160)
            manifest_sha = header[168:200].hex()
            attestation_offset, attestation_bytes = u64(header, 200), u64(header, 208)
            attestation_sha = header[216:248].hex()
            shard_offset, shard_bytes = u64(header, 248), u64(header, 256)
            shard_sha = header[264:296].hex()
            records, tb_probes, tb_hits = u64(header, 296), u64(header, 304), u64(header, 312)
            inventory_sha = header[320:352].hex()
            if any(header[352:384]):
                raise BundleError("ATOBNDL2 reserved bytes are nonzero")
            if manifest_offset != HEADER_BYTES or not 0 < manifest_bytes <= MAX_JSON_BYTES:
                raise BundleError("invalid manifest V2 offset or size")
            expected_attestation_offset = (manifest_offset + manifest_bytes + 63) & ~63
            if attestation_offset != expected_attestation_offset or not 0 < attestation_bytes <= MAX_JSON_BYTES:
                raise BundleError("invalid attestation V1 offset or size")
            expected_shard_offset = (attestation_offset + attestation_bytes + 63) & ~63
            if shard_offset != expected_shard_offset or shard_bytes == 0:
                raise BundleError("invalid shard offset or size")
            if records == 0 or tb_hits > tb_probes:
                raise BundleError("ATOBNDL2 counters are inconsistent")
            if inventory_sha != inventory_actual_sha:
                raise BundleError("Syzygy inventory SHA-256 mismatch")

            manifest_payload = read_exact(stream, manifest_bytes, "manifest V2")
            if hashlib.sha256(manifest_payload).hexdigest() != manifest_sha:
                raise BundleError("manifest V2 SHA-256 mismatch")
            manifest, shard_name, producer_sha = validate_manifest(
                manifest_payload,
                records=records,
                shard_bytes=shard_bytes,
                shard_sha=shard_sha,
                tb_probes=tb_probes,
                tb_hits=tb_hits,
                inventory_sha=inventory_sha,
            )
            if any(read_exact(stream, attestation_offset - manifest_offset - manifest_bytes,
                              "manifest padding")):
                raise BundleError("manifest alignment padding is nonzero")
            attestation_payload = read_exact(stream, attestation_bytes, "attestation V1")
            if hashlib.sha256(attestation_payload).hexdigest() != attestation_sha:
                raise BundleError("attestation V1 SHA-256 mismatch")
            generation = mapping(manifest["generation"], "generation")
            validate_attestation(
                attestation_payload,
                manifest_name=f"{shard_name}.manifest-v2.json",
                manifest_bytes=manifest_bytes,
                manifest_sha=manifest_sha,
                shard_name=shard_name,
                shard_bytes=shard_bytes,
                shard_sha=shard_sha,
                inventory_sha=inventory_sha,
                producer_sha=producer_sha,
                teacher_mode=string(generation["teacher_mode"], "teacher_mode"),
                use_nnue=string(generation["use_nnue"], "use_nnue"),
                tb_probes=tb_probes,
                tb_hits=tb_hits,
            )
            if any(read_exact(stream, shard_offset - attestation_offset - attestation_bytes,
                               "attestation padding")):
                raise BundleError("attestation alignment padding is nonzero")

            extraction_paths: tuple[Path, Path, Path] | None = None
            shard_output: BinaryIO | None = None
            if extract_dir is not None:
                directory = extract_dir.expanduser().resolve()
                directory.mkdir(parents=True, exist_ok=True)
                extraction_paths = (
                    directory / shard_name,
                    directory / f"{shard_name}.manifest-v2.json",
                    directory / f"{bundle.name}.attestation.json",
                )
                for destination in extraction_paths:
                    if destination.exists() or destination.is_symlink():
                        raise BundleError(f"extraction output already exists: {destination}")
                shard_output = extraction_paths[0].open("xb")
                created.append((extraction_paths[0], identity(shard_output)))

            shard_digest = hashlib.sha256()
            shard_prefix = bytearray()
            remaining = shard_bytes
            try:
                while remaining:
                    block = read_exact(
                        stream, min(remaining, COPY_BLOCK), "Atomic BIN V2 shard"
                    )
                    shard_digest.update(block)
                    if len(shard_prefix) < ATBIN_HEADER_BYTES:
                        needed = ATBIN_HEADER_BYTES - len(shard_prefix)
                        shard_prefix.extend(block[:needed])
                    if shard_output is not None and shard_output.write(block) != len(block):
                        raise OSError("short write while extracting Atomic BIN V2 shard")
                    remaining -= len(block)
                if shard_output is not None:
                    shard_output.flush()
                    os.fsync(shard_output.fileno())
            finally:
                if shard_output is not None:
                    shard_output.close()

            if shard_digest.hexdigest() != shard_sha:
                raise BundleError("Atomic BIN V2 shard SHA-256 mismatch")
            if stream.read(1):
                raise BundleError("ATOBNDL2 contains trailing bytes")

        if len(shard_prefix) != ATBIN_HEADER_BYTES or shard_prefix[:8] != b"ATBINV2\0":
            raise BundleError("embedded shard is not Atomic BIN V2")
        if (
            u16(shard_prefix, 8) != ATBIN_VERSION
            or u16(shard_prefix, 10) != ATBIN_HEADER_BYTES
            or u32(shard_prefix, 12) != ENDIAN_MARKER
            or u32(shard_prefix, 16) != ATBIN_RECORD_BYTES
            or u32(shard_prefix, 20) != 0
            or shard_prefix[24:56].hex() != DATA_SCHEMA_SHA256
            or u64(shard_prefix, 56) != records
            or any(shard_prefix[64:96])
            or shard_bytes != ATBIN_HEADER_BYTES + records * ATBIN_RECORD_BYTES
        ):
            raise BundleError("embedded Atomic BIN V2 framing/count is invalid")

        if extraction_paths is not None:
            # The shard is already durably streamed. Publish the manifest and
            # finally the attestation as the extraction commit marker.
            for destination, payload in (
                (extraction_paths[1], manifest_payload),
                (extraction_paths[2], attestation_payload),
            ):
                output = destination.open("xb")
                created.append((destination, identity(output)))
                try:
                    if output.write(payload) != len(payload):
                        raise OSError(f"short write while extracting {destination.name}")
                    output.flush()
                    os.fsync(output.fileno())
                finally:
                    output.close()

        return {
            "format": "atomic-openbench-datagen-bundle-v2",
            "records": records,
            "teacher_mode": generation["teacher_mode"],
            "use_nnue": generation["use_nnue"],
            "tb_probes": tb_probes,
            "tb_hits": tb_hits,
            "shard_file": shard_name,
            "shard_sha256": shard_sha,
            "manifest_sha256": manifest_sha,
            "attestation_sha256": attestation_sha,
            "syzygy_inventory_sha256": inventory_sha,
        }
    except Exception:
        for created_path, expected_identity in reversed(created):
            cleanup_owned(created_path, expected_identity)
        raise


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("bundle", type=Path)
    parser.add_argument("--syzygy-inventory", required=True, type=Path)
    parser.add_argument("--extract-dir", type=Path)
    args = parser.parse_args(argv)
    try:
        report = validate_bundle(args.bundle, args.extract_dir, args.syzygy_inventory)
    except (BundleError, OSError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(report, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
