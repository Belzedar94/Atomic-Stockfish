"""Frozen schema and strict validator for Atomic BIN V2 sidecar manifests."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
from typing import Mapping


DEFAULT_SCHEMA_FILE = (
    Path(__file__).resolve().parents[1]
    / "schemas"
    / "atomic-bin-v2-manifest.json"
)
EXPECTED_SHA256 = "83d63922df3ac4a0c81a21ec9d9fd9e180efe50f26efee62fe01710e09da5b42"
DATA_SCHEMA_SHA256 = (
    "0352b036f2a140c609e3eb9c9d635dc553e8d77253d8faa92437390f5cf93cb6"
)

UINT32_MAX = 4_294_967_295
UINT64_MAX = 18_446_744_073_709_551_615
INT32_MIN = -2_147_483_648
INT32_MAX = 2_147_483_647

_LOWER_HEX_40 = re.compile(r"^[0-9a-f]{40}$")
_LOWER_HEX_64 = re.compile(r"^[0-9a-f]{64}$")
_UINT64_DECIMAL = re.compile(r"^(0|[1-9][0-9]{0,19})$")
_CANONICAL_PROBABILITY = re.compile(r"^(0|1|0\.[0-9]*[1-9])$")
_FORBIDDEN_BASENAME_CHARACTERS = frozenset('/\\:\x00<>"|?*')

TOP_LEVEL_KEYS = {
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
}

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
OPTION_UINT64_KEYS = (
    "nodes",
    "requested_records",
    "records_per_shard",
)
OPTION_BOOL_KEYS = (
    "adjudicate_draws_by_score",
    "adjudicate_insufficient",
    "filter_captures",
    "filter_checks",
    "filter_promotions",
    "random_file_name",
    "set_recommended_uci_options_seen",
)
OPTION_ORDER = (
    OPTION_INT32_KEYS[:2]
    + OPTION_UINT64_KEYS
    + OPTION_INT32_KEYS[2:]
    + ("keep_draws",)
    + OPTION_BOOL_KEYS
)
OPTION_KEYS = set(OPTION_ORDER)


def _no_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise AssertionError(f"duplicate JSON key {key!r}")
        result[key] = value
    return result


def _mapping(value: object, label: str) -> Mapping[str, object]:
    if type(value) is not dict:
        raise AssertionError(f"{label} must be an object")
    return value


def _exact_keys(value: Mapping[str, object], expected: set[str], label: str) -> None:
    if set(value) != expected:
        raise AssertionError(
            f"{label} keys mismatch: expected {sorted(expected)}, got {sorted(value)}"
        )


def _same_exact(actual: object, expected: object) -> bool:
    if type(actual) is not type(expected):
        return False
    if isinstance(expected, dict):
        if set(actual) != set(expected):
            return False
        return all(_same_exact(actual[key], value) for key, value in expected.items())
    if isinstance(expected, (list, tuple)):
        return len(actual) == len(expected) and all(
            _same_exact(actual_value, expected_value)
            for actual_value, expected_value in zip(actual, expected)
        )
    return actual == expected


def _require_exact_value(actual: object, expected: object, label: str) -> None:
    if not _same_exact(actual, expected):
        raise AssertionError(f"{label} must be exactly {expected!r}, got {actual!r}")


def _ref(name: str) -> dict[str, str]:
    return {"$ref": f"#/$defs/{name}"}


def _object_schema(properties: dict[str, object]) -> dict[str, object]:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": list(properties),
        "properties": properties,
    }


def _expected_schema_document() -> dict[str, object]:
    lower_hex_sha256 = _ref("lower-hex-sha256")
    uint64_decimal = _ref("uint64-decimal-string")
    int32 = _ref("int32")
    boolean = {"type": "boolean"}

    engine = _object_schema(
        {
            "commit": {
                "oneOf": [
                    {"pattern": "^[0-9a-f]{40}$", "type": "string"},
                    {"const": "unknown"},
                ]
            },
            "version": {"type": "string", "minLength": 1},
        }
    )
    network = _object_schema(
        {
            "file": _ref("portable-basename"),
            "sha256": lower_hex_sha256,
        }
    )
    book = {
        "oneOf": [
            _object_schema(
                {
                    "kind": {"const": "builtin-startpos"},
                    "file": {"type": "null"},
                    "sha256": {"type": "null"},
                }
            ),
            _object_schema(
                {
                    "kind": {"const": "file"},
                    "file": _ref("portable-basename"),
                    "sha256": lower_hex_sha256,
                }
            ),
        ]
    }

    option_properties: dict[str, object] = {}
    for key in OPTION_INT32_KEYS[:2]:
        option_properties[key] = int32
    for key in OPTION_UINT64_KEYS:
        option_properties[key] = uint64_decimal
    for key in OPTION_INT32_KEYS[2:]:
        option_properties[key] = int32
    option_properties["keep_draws"] = _ref(
        "canonical-decimal-string"
    )
    for key in OPTION_BOOL_KEYS:
        option_properties[key] = boolean

    generation = _object_schema(
        {
            "resolved_seed": uint64_decimal,
            "atomic960": boolean,
            "threads": _ref("positive-uint32"),
            "hash_mb": uint64_decimal,
            "use_nnue": {"const": "pure"},
            "options": _object_schema(option_properties),
        }
    )
    statistics = _object_schema(
        {"records": uint64_decimal, "draws": uint64_decimal}
    )
    shard = _object_schema(
        {
            "index": _ref("uint32"),
            "file": _ref("atbin-basename"),
            "records": uint64_decimal,
            "bytes": uint64_decimal,
            "sha256": lower_hex_sha256,
        }
    )
    shards = {
        "type": "array",
        "minItems": 1,
        "x-index-policy": "zero-based-contiguous-in-array-order",
        "items": shard,
    }

    properties: dict[str, object] = {
        "manifest_version": {"const": 1},
        "manifest_schema_sha256": {
            "$ref": "#/$defs/lower-hex-sha256",
            "x-value": "sha256-of-exact-manifest-schema-file",
        },
        "data_schema_sha256": {"const": DATA_SCHEMA_SHA256},
        "format": {"const": "atomic-bin-v2"},
        "engine": engine,
        "network": network,
        "book": book,
        "generation": generation,
        "statistics": statistics,
        "shards": shards,
    }
    definitions: dict[str, object] = {
        "lower-hex-sha256": {
            "type": "string",
            "pattern": "^[0-9a-f]{64}$",
        },
        "uint64-decimal-string": {
            "type": "string",
            "pattern": "^(0|[1-9][0-9]{0,19})$",
            "x-decimal-maximum": str(UINT64_MAX),
        },
        "canonical-decimal-string": {
            "type": "string",
            "pattern": "^(0|1|0\\.[0-9]*[1-9])$",
            "x-decimal-minimum": "0",
            "x-decimal-maximum": "1",
        },
        "int32": {
            "type": "integer",
            "minimum": INT32_MIN,
            "maximum": INT32_MAX,
        },
        "uint32": {
            "type": "integer",
            "minimum": 0,
            "maximum": UINT32_MAX,
        },
        "positive-uint32": {
            "type": "integer",
            "minimum": 1,
            "maximum": UINT32_MAX,
        },
        "portable-basename": {
            "type": "string",
            "minLength": 1,
            "pattern": "^(?!\\.{1,2}$)[^/\\\\:\\u0000<>\\\"|?*]+$",
        },
        "atbin-basename": {
            "type": "string",
            "pattern": "^(?!\\.{1,2}$)[^/\\\\:\\u0000<>\\\"|?*]+\\.atbin$",
        },
    }
    file_policy: dict[str, object] = {
        "encoding": "utf-8",
        "bom": "forbidden",
        "canonical_json": {
            "key_order": "schema-declaration-order",
            "separators": [",", ":"],
            "ensure_ascii": False,
            "allow_nan": False,
            "insignificant_whitespace": "forbidden",
        },
        "trailing_lf_count": 1,
        "absolute_paths": "forbidden",
        "timestamps": "forbidden",
        "sidecar_suffix": ".manifest.json",
        "append": "forbidden",
        "overwrite": "forbidden",
    }
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "urn:atomic-stockfish:schema:atomic-bin-v2-manifest:1",
        "title": "Atomic BIN V2 manifest",
        "schema_version": 1,
        "status": "frozen",
        "type": "object",
        "additionalProperties": False,
        "required": list(properties),
        "properties": properties,
        "$defs": definitions,
        "x-file-policy": file_policy,
    }


@dataclass(frozen=True)
class AtomicBinV2ManifestSchema:
    path: Path
    sha256: str


def validate_atomic_bin_v2_manifest_schema(
    document: Mapping[str, object],
) -> None:
    _require_exact_value(
        document,
        _expected_schema_document(),
        "Atomic BIN V2 manifest schema",
    )


def _decode_json(payload: bytes, label: str) -> dict[str, object]:
    try:
        document = json.loads(
            payload.decode("utf-8"), object_pairs_hook=_no_duplicate_keys
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise AssertionError(f"invalid {label} JSON: {error}") from error
    return dict(_mapping(document, label))


def load_atomic_bin_v2_manifest_schema(
    path: Path = DEFAULT_SCHEMA_FILE,
) -> AtomicBinV2ManifestSchema:
    resolved = path.resolve()
    payload = resolved.read_bytes()
    if payload.startswith(b"\xef\xbb\xbf"):
        raise AssertionError("manifest schema must be UTF-8 without a BOM")
    if b"\r" in payload:
        raise AssertionError("manifest schema must use LF line endings")
    if not payload.endswith(b"\n") or payload.endswith(b"\n\n"):
        raise AssertionError("manifest schema must end with exactly one LF")
    document = _decode_json(payload, "manifest schema")
    validate_atomic_bin_v2_manifest_schema(document)
    digest = hashlib.sha256(payload).hexdigest()
    if digest.encode("ascii") in payload:
        raise AssertionError("manifest schema must not contain its own SHA-256")
    if resolved == DEFAULT_SCHEMA_FILE.resolve() and digest != EXPECTED_SHA256:
        raise AssertionError(
            "Atomic BIN V2 manifest schema digest mismatch: "
            f"expected {EXPECTED_SHA256}, got {digest}"
        )
    return AtomicBinV2ManifestSchema(resolved, digest)


def _require_string(value: object, label: str) -> str:
    if type(value) is not str:
        raise AssertionError(f"{label} must be a string")
    return value


def _require_lower_hex(value: object, width: int, label: str) -> str:
    text = _require_string(value, label)
    pattern = _LOWER_HEX_40 if width == 40 else _LOWER_HEX_64
    if pattern.fullmatch(text) is None:
        raise AssertionError(f"{label} must be exactly {width} lower-case hex digits")
    return text


def _require_uint64_decimal(value: object, label: str) -> str:
    text = _require_string(value, label)
    if _UINT64_DECIMAL.fullmatch(text) is None or int(text) > UINT64_MAX:
        raise AssertionError(f"{label} must be a canonical uint64 decimal string")
    return text


def _require_int32(value: object, label: str) -> int:
    if type(value) is not int or not INT32_MIN <= value <= INT32_MAX:
        raise AssertionError(f"{label} must be an int32")
    return value


def _require_uint32(value: object, label: str, minimum: int = 0) -> int:
    if type(value) is not int or not minimum <= value <= UINT32_MAX:
        domain = "positive uint32" if minimum == 1 else "uint32"
        raise AssertionError(f"{label} must be a {domain}")
    return value


def _require_bool(value: object, label: str) -> bool:
    if type(value) is not bool:
        raise AssertionError(f"{label} must be a boolean")
    return value


def _require_basename(value: object, label: str, suffix: str = "") -> str:
    text = _require_string(value, label)
    if (
        not text
        or text in {".", ".."}
        or any(character in _FORBIDDEN_BASENAME_CHARACTERS for character in text)
    ):
        raise AssertionError(f"{label} must be a portable basename")
    if suffix and (not text.endswith(suffix) or len(text) == len(suffix)):
        raise AssertionError(f"{label} must be a basename ending in {suffix}")
    return text


def _require_canonical_probability(value: object, label: str) -> str:
    text = _require_string(value, label)
    if _CANONICAL_PROBABILITY.fullmatch(text) is None:
        raise AssertionError(
            f"{label} must be a canonical decimal string in the range 0..1"
        )
    return text


def validate_atomic_bin_v2_manifest(
    document: Mapping[str, object],
    manifest_schema_sha256: str = EXPECTED_SHA256,
) -> None:
    manifest = _mapping(document, "manifest")
    _exact_keys(manifest, TOP_LEVEL_KEYS, "manifest")
    _require_exact_value(manifest["manifest_version"], 1, "manifest_version")
    _require_lower_hex(
        manifest["manifest_schema_sha256"], 64, "manifest_schema_sha256"
    )
    _require_exact_value(
        manifest["manifest_schema_sha256"],
        manifest_schema_sha256,
        "manifest_schema_sha256",
    )
    _require_exact_value(
        manifest["data_schema_sha256"],
        DATA_SCHEMA_SHA256,
        "data_schema_sha256",
    )
    _require_exact_value(manifest["format"], "atomic-bin-v2", "format")

    engine = _mapping(manifest["engine"], "engine")
    _exact_keys(engine, {"commit", "version"}, "engine")
    commit = _require_string(engine["commit"], "engine.commit")
    if commit != "unknown" and _LOWER_HEX_40.fullmatch(commit) is None:
        raise AssertionError("engine.commit must be lower-case hex40 or 'unknown'")
    version = _require_string(engine["version"], "engine.version")
    if not version:
        raise AssertionError("engine.version must be nonempty")

    network = _mapping(manifest["network"], "network")
    _exact_keys(network, {"file", "sha256"}, "network")
    _require_basename(network["file"], "network.file")
    _require_lower_hex(network["sha256"], 64, "network.sha256")

    book = _mapping(manifest["book"], "book")
    _exact_keys(book, {"kind", "file", "sha256"}, "book")
    kind = _require_string(book["kind"], "book.kind")
    if kind == "builtin-startpos":
        _require_exact_value(book["file"], None, "book.file")
        _require_exact_value(book["sha256"], None, "book.sha256")
    elif kind == "file":
        _require_basename(book["file"], "book.file")
        _require_lower_hex(book["sha256"], 64, "book.sha256")
    else:
        raise AssertionError("book.kind must be 'builtin-startpos' or 'file'")

    generation = _mapping(manifest["generation"], "generation")
    _exact_keys(
        generation,
        {"resolved_seed", "atomic960", "threads", "hash_mb", "use_nnue", "options"},
        "generation",
    )
    _require_uint64_decimal(generation["resolved_seed"], "generation.resolved_seed")
    _require_bool(generation["atomic960"], "generation.atomic960")
    _require_uint32(generation["threads"], "generation.threads", minimum=1)
    _require_uint64_decimal(generation["hash_mb"], "generation.hash_mb")
    _require_exact_value(generation["use_nnue"], "pure", "generation.use_nnue")

    options = _mapping(generation["options"], "generation.options")
    _exact_keys(options, OPTION_KEYS, "generation.options")
    for key in OPTION_INT32_KEYS:
        _require_int32(options[key], f"generation.options.{key}")
    for key in OPTION_UINT64_KEYS:
        _require_uint64_decimal(options[key], f"generation.options.{key}")
    _require_canonical_probability(
        options["keep_draws"], "generation.options.keep_draws"
    )
    for key in OPTION_BOOL_KEYS:
        _require_bool(options[key], f"generation.options.{key}")

    statistics = _mapping(manifest["statistics"], "statistics")
    _exact_keys(statistics, {"records", "draws"}, "statistics")
    _require_uint64_decimal(statistics["records"], "statistics.records")
    _require_uint64_decimal(statistics["draws"], "statistics.draws")

    shards = manifest["shards"]
    if type(shards) is not list or not shards:
        raise AssertionError("shards must be a nonempty array")
    for expected_index, raw_shard in enumerate(shards):
        shard = _mapping(raw_shard, f"shards[{expected_index}]")
        _exact_keys(
            shard,
            {"index", "file", "records", "bytes", "sha256"},
            f"shards[{expected_index}]",
        )
        index = _require_uint32(shard["index"], f"shards[{expected_index}].index")
        if index != expected_index:
            raise AssertionError("shard indices must be contiguous from zero")
        _require_basename(
            shard["file"], f"shards[{expected_index}].file", suffix=".atbin"
        )
        _require_uint64_decimal(
            shard["records"], f"shards[{expected_index}].records"
        )
        _require_uint64_decimal(shard["bytes"], f"shards[{expected_index}].bytes")
        _require_lower_hex(shard["sha256"], 64, f"shards[{expected_index}].sha256")


def canonical_atomic_bin_v2_manifest_bytes(
    document: Mapping[str, object],
    manifest_schema_sha256: str = EXPECTED_SHA256,
) -> bytes:
    validate_atomic_bin_v2_manifest(document, manifest_schema_sha256)
    manifest = _mapping(document, "manifest")
    engine = _mapping(manifest["engine"], "engine")
    network = _mapping(manifest["network"], "network")
    book = _mapping(manifest["book"], "book")
    generation = _mapping(manifest["generation"], "generation")
    options = _mapping(generation["options"], "generation.options")
    statistics = _mapping(manifest["statistics"], "statistics")
    shards = manifest["shards"]
    ordered_document = {
        "manifest_version": manifest["manifest_version"],
        "manifest_schema_sha256": manifest["manifest_schema_sha256"],
        "data_schema_sha256": manifest["data_schema_sha256"],
        "format": manifest["format"],
        "engine": {
            "commit": engine["commit"],
            "version": engine["version"],
        },
        "network": {
            "file": network["file"],
            "sha256": network["sha256"],
        },
        "book": {
            "kind": book["kind"],
            "file": book["file"],
            "sha256": book["sha256"],
        },
        "generation": {
            "resolved_seed": generation["resolved_seed"],
            "atomic960": generation["atomic960"],
            "threads": generation["threads"],
            "hash_mb": generation["hash_mb"],
            "use_nnue": generation["use_nnue"],
            "options": {key: options[key] for key in OPTION_ORDER},
        },
        "statistics": {
            "records": statistics["records"],
            "draws": statistics["draws"],
        },
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
        text = json.dumps(
            ordered_document,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
        )
    except (TypeError, ValueError) as error:
        raise AssertionError(f"manifest cannot be serialized canonically: {error}") from error
    return text.encode("utf-8") + b"\n"


def load_atomic_bin_v2_manifest(
    path: Path,
    manifest_schema_sha256: str = EXPECTED_SHA256,
) -> dict[str, object]:
    payload = path.read_bytes()
    if payload.startswith(b"\xef\xbb\xbf"):
        raise AssertionError("manifest must be UTF-8 without a BOM")
    if b"\r" in payload:
        raise AssertionError("manifest must use LF line endings")
    if not payload.endswith(b"\n") or payload.endswith(b"\n\n"):
        raise AssertionError("manifest must end with exactly one LF")
    document = _decode_json(payload, "manifest")
    expected = canonical_atomic_bin_v2_manifest_bytes(
        document, manifest_schema_sha256
    )
    if payload != expected:
        raise AssertionError("manifest must be canonical minified UTF-8 JSON")
    return document


if __name__ == "__main__":
    schema = load_atomic_bin_v2_manifest_schema()
    print(f"Atomic BIN V2 manifest schema verified: sha256={schema.sha256}")
