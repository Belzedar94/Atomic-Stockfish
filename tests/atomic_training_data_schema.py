#!/usr/bin/env python3
"""Validate and identify the normative Atomic training-data schema."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
from typing import Mapping, Sequence


DEFAULT_SCHEMA_FILE = (
    Path(__file__).resolve().parents[1] / "schemas" / "atomic-schema.json"
)
EXPECTED_SCHEMA_ID = "legacy-atomic-v1"
EXPECTED_RECORD_SIZE = 72
EXPECTED_FIELDS = (
    ("packed_position", 0, 64, "bytes"),
    ("score", 64, 2, "int16"),
    ("move", 66, 2, "uint16"),
    ("ply", 68, 2, "uint16"),
    ("result", 70, 1, "int8"),
    ("padding", 71, 1, "uint8"),
)


class _DuplicateJsonKeyError(ValueError):
    pass


def _strict_json_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateJsonKeyError(f"duplicate JSON key {key!r}")
        result[key] = value
    return result


def _require_mapping(value: object, label: str) -> Mapping[str, object]:
    if not isinstance(value, dict):
        raise AssertionError(f"{label} must be a JSON object")
    return value


def _require_exact_keys(
    value: Mapping[str, object], expected: set[str], label: str
) -> None:
    if set(value) != expected:
        raise AssertionError(
            f"{label} keys must be exactly {sorted(expected)}, "
            f"got {sorted(value)}"
        )


def _require_exact_value(actual: object, expected: object, label: str) -> None:
    if type(actual) is not type(expected) or actual != expected:
        raise AssertionError(f"{label} must be exactly {expected!r}, got {actual!r}")


def _reject_self_hash(value: object, label: str = "schema") -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            if key in {"schema_sha256", "sha256"}:
                raise AssertionError(f"{label} must not contain a self hash key {key!r}")
            _reject_self_hash(child, f"{label}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _reject_self_hash(child, f"{label}[{index}]")


@dataclass(frozen=True)
class TrainingDataField:
    name: str
    offset: int
    size: int
    storage: str


@dataclass(frozen=True)
class AtomicTrainingDataSchema:
    path: Path
    schema_id: str
    sha256: str
    record_size: int
    fields: tuple[TrainingDataField, ...]

    def field(self, name: str) -> TrainingDataField:
        matches = tuple(field for field in self.fields if field.name == name)
        if len(matches) != 1:
            raise AssertionError(
                f"schema must contain exactly one field named {name!r}"
            )
        return matches[0]


def _read_canonical_schema(path: Path) -> tuple[bytes, Mapping[str, object]]:
    resolved = path.expanduser().resolve()
    try:
        payload = resolved.read_bytes()
    except OSError as error:
        raise AssertionError(f"cannot read training-data schema {resolved}: {error}") from error
    if payload.startswith(b"\xef\xbb\xbf"):
        raise AssertionError("training-data schema must be UTF-8 without a BOM")
    if b"\r" in payload:
        raise AssertionError("training-data schema must use LF line endings")
    if not payload.endswith(b"\n") or payload.endswith(b"\n\n"):
        raise AssertionError("training-data schema must end with exactly one LF")
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError as error:
        raise AssertionError("training-data schema must be valid UTF-8") from error
    try:
        document = json.loads(text, object_pairs_hook=_strict_json_object)
    except (json.JSONDecodeError, _DuplicateJsonKeyError) as error:
        raise AssertionError(f"invalid training-data schema JSON: {error}") from error
    return payload, _require_mapping(document, "schema")


def _validate_fields(format_entry: Mapping[str, object]) -> None:
    raw_fields = format_entry.get("fields")
    if not isinstance(raw_fields, list):
        raise AssertionError("format.fields must be a JSON array")
    if len(raw_fields) != len(EXPECTED_FIELDS):
        raise AssertionError(
            f"format.fields must contain {len(EXPECTED_FIELDS)} entries"
        )
    cursor = 0
    for index, (raw, expected) in enumerate(zip(raw_fields, EXPECTED_FIELDS)):
        field = _require_mapping(raw, f"format.fields[{index}]")
        required = {"name", "offset", "size", "storage"}
        if expected[0] == "packed_position":
            required.add("encoding")
        elif expected[0] == "move":
            required.add("encoding")
        _require_exact_keys(field, required, f"format.fields[{index}]")
        name, offset, size, storage = expected
        _require_exact_value(field.get("name"), name, f"field {index} name")
        _require_exact_value(field.get("offset"), offset, f"field {name} offset")
        _require_exact_value(field.get("size"), size, f"field {name} size")
        _require_exact_value(
            field.get("storage"), storage, f"field {name} storage"
        )
        if name == "packed_position":
            _require_exact_value(
                field.get("encoding"),
                "fairy-packed-sfen-512-atomic",
                "packed position field encoding",
            )
        elif name == "move":
            _require_exact_value(
                field.get("encoding"),
                "legacy-stockfish-16",
                "move field encoding",
            )
        if offset != cursor:
            raise AssertionError(
                f"field {name} starts at {offset}, expected contiguous offset {cursor}"
            )
        cursor += size
    if cursor != EXPECTED_RECORD_SIZE:
        raise AssertionError(
            f"fields cover {cursor} bytes, expected {EXPECTED_RECORD_SIZE}"
        )


def _validate_move_wire(raw: object) -> None:
    move = _require_mapping(raw, "move_wire")
    _require_exact_keys(
        move,
        {
            "encoding",
            "size",
            "none_allowed",
            "forbidden_value",
            "to",
            "from",
            "promotion",
            "type",
            "castling_to_semantics",
        },
        "move_wire",
    )
    _require_exact_value(move.get("encoding"), "legacy-stockfish-16", "move encoding")
    _require_exact_value(move.get("size"), 2, "move size")
    _require_exact_value(move.get("none_allowed"), False, "move none allowed")
    _require_exact_value(move.get("forbidden_value"), 0, "move forbidden value")
    expected_bits = {
        "to": (0, 6),
        "from": (6, 6),
        "promotion": (12, 2),
        "type": (14, 2),
    }
    cursor = 0
    for name, (lsb, width) in expected_bits.items():
        entry = _require_mapping(move.get(name), f"move_wire.{name}")
        expected_keys = {"lsb", "width"}
        if name in {"to", "from"}:
            expected_keys.add("domain")
        elif name == "promotion":
            expected_keys.update(("mapping", "active_when_type"))
        else:
            expected_keys.add("mapping")
        _require_exact_keys(entry, expected_keys, f"move_wire.{name}")
        _require_exact_value(entry.get("lsb"), lsb, f"move_wire.{name}.lsb")
        _require_exact_value(entry.get("width"), width, f"move_wire.{name}.width")
        if lsb != cursor:
            raise AssertionError(f"move wire has a gap before {name}")
        cursor += width
    if cursor != 16:
        raise AssertionError(f"move wire covers {cursor} bits, expected 16")
    for name in ("to", "from"):
        entry = _require_mapping(move[name], f"move_wire.{name}")
        _require_exact_value(entry.get("domain"), [0, 63], f"move_wire.{name}.domain")
    promotion = _require_mapping(move["promotion"], "move_wire.promotion")
    _require_exact_value(
        promotion.get("mapping"),
        ["knight", "bishop", "rook", "queen"],
        "move_wire.promotion.mapping",
    )
    _require_exact_value(
        promotion.get("active_when_type"), 1, "move_wire.promotion.active_when_type"
    )
    move_type = _require_mapping(move["type"], "move_wire.type")
    _require_exact_value(
        move_type.get("mapping"),
        ["normal", "promotion", "en-passant", "castling"],
        "move_wire.type.mapping",
    )
    _require_exact_value(
        move.get("castling_to_semantics"),
        "rook-origin-square",
        "move castling destination semantics",
    )


def validate_training_data_schema(document: Mapping[str, object]) -> None:
    _reject_self_hash(document)
    _require_exact_keys(
        document,
        {
            "schema_version",
            "schema_id",
            "status",
            "variant",
            "format",
            "packed_position",
            "move_wire",
            "semantics",
            "file_policy",
        },
        "schema",
    )
    for key, expected in (
        ("schema_version", 1),
        ("schema_id", EXPECTED_SCHEMA_ID),
        ("status", "frozen"),
        ("variant", "atomic"),
    ):
        _require_exact_value(document.get(key), expected, key)

    format_entry = _require_mapping(document.get("format"), "format")
    _require_exact_keys(
        format_entry,
        {
            "byte_order",
            "host_byte_order_required",
            "header_size",
            "record_size",
            "framing",
            "record_count",
            "atomic960",
            "fields",
        },
        "format",
    )
    for key, expected in (
        ("byte_order", "little-endian"),
        ("host_byte_order_required", "little-endian"),
        ("header_size", 0),
        ("record_size", EXPECTED_RECORD_SIZE),
        ("framing", "headerless-fixed-records"),
        ("record_count", "file-size-divided-by-record-size"),
        ("atomic960", False),
    ):
        _require_exact_value(format_entry.get(key), expected, f"format.{key}")
    _validate_fields(format_entry)

    packed = _require_mapping(document.get("packed_position"), "packed_position")
    expected_packed = {
        "encoding": "fairy-packed-sfen-512-atomic",
        "size": 64,
        "bit_order": "least-significant-bit-first",
        "canonical": False,
        "writer_zero_fills_storage": True,
        "side_to_move_bits": 1,
        "king_square_bits": 7,
        "king_square_domain": [0, 63],
        "missing_king_supported": False,
        "king_wire_mapping": "fairy-commoner-as-atomic-king",
        "board_scan_order": "rank-8-to-1-file-a-to-h",
        "board_piece_encoding": "fairy-huffman-v1",
        "hand_count_fields": {
            "count": 12,
            "bits_per_field": 5,
            "order": [
                "white-pawn",
                "white-knight",
                "white-bishop",
                "white-rook",
                "white-queen",
                "white-atomic-king",
                "black-pawn",
                "black-knight",
                "black-bishop",
                "black-rook",
                "black-queen",
                "black-atomic-king",
            ],
            "required_value": 0,
            "pocket_semantics": False,
        },
        "castling_rights_bits": 4,
        "castling_rook_origins": False,
        "en_passant_presence_bits": 1,
        "en_passant_square_bits": 7,
        "rule50_bits": 7,
        "fullmove_bits": 16,
        "clock_tail_order": [
            "rule50-low-6",
            "fullmove-low-8",
            "fullmove-high-8",
            "rule50-high-1",
        ],
    }
    _require_exact_keys(packed, set(expected_packed), "packed_position")
    for key, expected in expected_packed.items():
        _require_exact_value(packed.get(key), expected, f"packed_position.{key}")

    _validate_move_wire(document.get("move_wire"))

    semantics = _require_mapping(document.get("semantics"), "semantics")
    _require_exact_keys(
        semantics,
        {
            "score_perspective",
            "result_perspective",
            "result_domain",
            "padding_value",
        },
        "semantics",
    )
    _require_exact_value(
        semantics.get("score_perspective"), "side-to-move", "score perspective"
    )
    _require_exact_value(
        semantics.get("result_perspective"), "side-to-move", "result perspective"
    )
    _require_exact_value(
        semantics.get("result_domain"), [-1, 0, 1], "result domain"
    )
    _require_exact_value(semantics.get("padding_value"), 0, "padding value")

    file_policy = _require_mapping(document.get("file_policy"), "file_policy")
    expected_policy = {
        "empty": "invalid",
        "append": "forbidden",
        "overwrite": "forbidden",
        "new_fields": "require-new-versioned-format",
    }
    _require_exact_keys(file_policy, set(expected_policy), "file_policy")
    for key, expected in expected_policy.items():
        _require_exact_value(file_policy.get(key), expected, f"file_policy.{key}")


def load_training_data_schema(
    path: Path = DEFAULT_SCHEMA_FILE,
) -> AtomicTrainingDataSchema:
    payload, document = _read_canonical_schema(path)
    validate_training_data_schema(document)
    format_entry = _require_mapping(document["format"], "format")
    raw_fields = format_entry["fields"]
    assert isinstance(raw_fields, list)
    fields = tuple(
        TrainingDataField(
            name=str(field["name"]),
            offset=int(field["offset"]),
            size=int(field["size"]),
            storage=str(field["storage"]),
        )
        for field in raw_fields
        if isinstance(field, dict)
    )
    return AtomicTrainingDataSchema(
        path=path.expanduser().resolve(),
        schema_id=str(document["schema_id"]),
        sha256=hashlib.sha256(payload).hexdigest(),
        record_size=int(format_entry["record_size"]),
        fields=fields,
    )


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--schema-file", type=Path, default=DEFAULT_SCHEMA_FILE)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    schema = load_training_data_schema(args.schema_file)
    print(
        "ATOMIC TRAINING DATA SCHEMA VERIFIED "
        f"id={schema.schema_id} sha256={schema.sha256} "
        f"record_size={schema.record_size}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
