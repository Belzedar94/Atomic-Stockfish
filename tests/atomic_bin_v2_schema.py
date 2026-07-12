"""Strict contract validator for the frozen Atomic BIN V2 schema."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
from typing import Mapping


DEFAULT_SCHEMA_FILE = (
    Path(__file__).resolve().parents[1] / "schemas" / "atomic-bin-v2.json"
)
EXPECTED_SHA256 = "0352b036f2a140c609e3eb9c9d635dc553e8d77253d8faa92437390f5cf93cb6"


def _no_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise AssertionError(f"duplicate JSON key {key!r}")
        result[key] = value
    return result


def _mapping(value: object, label: str) -> Mapping[str, object]:
    if not isinstance(value, dict):
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


def _reject_self_hash(value: object, label: str = "schema") -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            if key in {"schema_sha256", "sha256"}:
                raise AssertionError(f"{label} must not contain a self hash")
            _reject_self_hash(child, f"{label}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _reject_self_hash(child, f"{label}[{index}]")


def _fields(section: Mapping[str, object], label: str) -> list[Mapping[str, object]]:
    raw = section.get("fields")
    if not isinstance(raw, list):
        raise AssertionError(f"{label}.fields must be an array")
    result = [_mapping(value, f"{label}.fields[{index}]") for index, value in enumerate(raw)]
    names = [value.get("name") for value in result]
    if len(names) != len(set(names)):
        raise AssertionError(f"{label}.fields contains duplicate names")
    return result


def _validate_layout(
    section: Mapping[str, object],
    label: str,
    expected: list[tuple[str, int, int, str]],
) -> list[Mapping[str, object]]:
    fields = _fields(section, label)
    actual = [
        (field.get("name"), field.get("offset"), field.get("size"), field.get("storage"))
        for field in fields
    ]
    if not _same_exact(actual, expected):
        raise AssertionError(f"{label} field layout mismatch")
    cursor = 0
    for name, offset, size, _storage in expected:
        if offset != cursor:
            raise AssertionError(f"{label} field {name} is not contiguous")
        cursor += size
    _require_exact_value(section.get("size"), cursor, f"{label}.size")
    return fields


@dataclass(frozen=True)
class AtomicBinV2Schema:
    path: Path
    sha256: str
    header_size: int
    position_size: int
    record_size: int


def validate_atomic_bin_v2_schema(document: Mapping[str, object]) -> None:
    _reject_self_hash(document)
    _exact_keys(
        document,
        {
            "schema_version",
            "schema_id",
            "status",
            "variant",
            "byte_order",
            "header",
            "position",
            "piece_wire",
            "move_wire",
            "record",
            "semantics",
            "file_policy",
        },
        "schema",
    )
    for key, expected in (
        ("schema_version", 2),
        ("schema_id", "atomic-bin-v2"),
        ("status", "frozen"),
        ("variant", "atomic"),
        ("byte_order", "little-endian"),
    ):
        _require_exact_value(document.get(key), expected, f"schema.{key}")
    header = _mapping(document["header"], "header")
    _exact_keys(header, {"size", "magic_hex", "fields"}, "header")
    _require_exact_value(header.get("size"), 96, "header.size")
    magic_hex = header.get("magic_hex")
    if not _same_exact(magic_hex, "415442494e563200") or bytes.fromhex(str(magic_hex)) != b"ATBINV2\0":
        raise AssertionError("header.magic_hex must encode exactly ATBINV2 NUL")
    header_fields = _validate_layout(
        header,
        "header",
        [
            ("magic", 0, 8, "bytes"),
            ("version", 8, 2, "uint16"),
            ("header_size", 10, 2, "uint16"),
            ("endian_marker", 12, 4, "uint32"),
            ("record_size", 16, 4, "uint32"),
            ("flags", 20, 4, "uint32"),
            ("schema_sha256", 24, 32, "bytes"),
            ("record_count", 56, 8, "uint64"),
            ("reserved", 64, 32, "bytes"),
        ],
    )
    required = {
        "version": 2,
        "header_size": 96,
        "endian_marker": 0x01020304,
        "record_size": 64,
        "flags": 0,
        "reserved": 0,
    }
    for field in header_fields:
        name = str(field["name"])
        allowed = {"name", "offset", "size", "storage"}
        if name in required:
            allowed.add("required_value")
        if name == "schema_sha256":
            allowed.add("encoding")
        if name == "record_count":
            allowed.add("minimum")
        _exact_keys(field, allowed, f"header field {name}")
        if name in required:
            _require_exact_value(
                field.get("required_value"), required[name],
                f"header field {name} required_value",
            )
    _require_exact_value(
        header_fields[6].get("encoding"),
        "raw-sha256-of-exact-schema-file",
        "header schema digest encoding",
    )
    _require_exact_value(
        header_fields[7].get("minimum"), 1, "header record_count minimum"
    )

    position = _mapping(document["position"], "position")
    _exact_keys(position, {"size", "board_scan_order", "nibble_order", "fields"}, "position")
    _require_exact_value(position.get("size"), 48, "position.size")
    _require_exact_value(
        position.get("board_scan_order"), "a1-to-h8", "position board scan order"
    )
    _require_exact_value(
        position.get("nibble_order"),
        "even-square-low-nibble",
        "position nibble order",
    )
    position_fields = _validate_layout(
        position,
        "position",
        [
            ("board", 0, 32, "packed-nibbles"),
            ("side_to_move", 32, 1, "uint8"),
            ("castling_rights", 33, 1, "uint8"),
            ("castling_rook_origins", 34, 4, "uint8[4]"),
            ("en_passant_square", 38, 1, "uint8"),
            ("reserved", 39, 1, "uint8"),
            ("rule50", 40, 2, "uint16"),
            ("fullmove", 42, 4, "uint32"),
            ("reserved_tail", 46, 2, "bytes"),
        ],
    )
    _require_exact_value(
        position_fields[6].get("maximum"), 32767, "position rule50 maximum"
    )
    _require_exact_value(
        position_fields[7].get("minimum"), 1, "position fullmove minimum"
    )
    _require_exact_value(
        position_fields[7].get("maximum"), 100000, "position fullmove maximum"
    )
    position_allowed = {
        "board": {"name", "offset", "size", "storage"},
        "side_to_move": {"name", "offset", "size", "storage", "domain", "mapping"},
        "castling_rights": {"name", "offset", "size", "storage", "mask", "bits"},
        "castling_rook_origins": {
            "name", "offset", "size", "storage", "order", "square_domain",
            "absent_value", "present_iff_right",
        },
        "en_passant_square": {
            "name", "offset", "size", "storage", "square_domain", "absent_value",
        },
        "reserved": {"name", "offset", "size", "storage", "required_value"},
        "rule50": {"name", "offset", "size", "storage", "maximum"},
        "fullmove": {"name", "offset", "size", "storage", "minimum", "maximum"},
        "reserved_tail": {"name", "offset", "size", "storage", "required_value"},
    }
    for field in position_fields:
        name = str(field["name"])
        _exact_keys(field, position_allowed[name], f"position field {name}")
    _require_exact_value(
        position_fields[1].get("domain"), [0, 1], "position side-to-move domain"
    )
    _require_exact_value(
        position_fields[1].get("mapping"),
        {"white": 0, "black": 1},
        "position side-to-move mapping",
    )
    expected_castling_bits = {
        "white-king-side": 0,
        "white-queen-side": 1,
        "black-king-side": 2,
        "black-queen-side": 3,
    }
    _require_exact_value(position_fields[2].get("mask"), 15, "position castling-right mask")
    _require_exact_value(
        position_fields[2].get("bits"), expected_castling_bits,
        "position castling-right mapping",
    )
    _require_exact_value(
        position_fields[3].get("order"), list(expected_castling_bits),
        "position castling-rook-origin order",
    )
    _require_exact_value(
        position_fields[3].get("square_domain"), [0, 63],
        "position castling-rook-origin square domain",
    )
    _require_exact_value(
        position_fields[3].get("absent_value"), 255,
        "position castling-rook-origin absent value",
    )
    _require_exact_value(
        position_fields[3].get("present_iff_right"), True,
        "position castling-rook-origin presence rule",
    )
    _require_exact_value(
        position_fields[4].get("square_domain"), [0, 63],
        "position en-passant square domain",
    )
    _require_exact_value(
        position_fields[4].get("absent_value"), 255,
        "position en-passant absent value",
    )
    _require_exact_value(
        position_fields[5].get("required_value"), 0,
        "position reserved byte",
    )
    _require_exact_value(
        position_fields[8].get("required_value"), 0,
        "position reserved tail",
    )

    pieces = _mapping(document["piece_wire"], "piece_wire")
    expected_pieces = {
        "white-pawn": 1,
        "white-knight": 2,
        "white-bishop": 3,
        "white-rook": 4,
        "white-queen": 5,
        "white-king": 6,
        "black-pawn": 7,
        "black-knight": 8,
        "black-bishop": 9,
        "black-rook": 10,
        "black-queen": 11,
        "black-king": 12,
    }
    _require_exact_value(
        pieces,
        {"empty": 0, "mapping": expected_pieces, "reserved": [13, 14, 15]},
        "piece_wire mapping",
    )

    move = _mapping(document["move_wire"], "move_wire")
    expected_move = {
        "size": 4,
        "none_allowed": False,
        "forbidden_value": 0,
        "from_to_must_differ": True,
        "from": {"lsb": 0, "width": 6, "domain": [0, 63]},
        "to": {"lsb": 6, "width": 6, "domain": [0, 63]},
        "type": {
            "lsb": 12,
            "width": 4,
            "mapping": {"normal": 0, "promotion": 1, "en-passant": 2, "castling": 3},
        },
        "promotion": {
            "lsb": 16,
            "width": 4,
            "mapping": {"none": 0, "knight": 1, "bishop": 2, "rook": 3, "queen": 4},
        },
        "promotion_type_coupling": {
            "promotion_type_name": "promotion",
            "nonzero_promotion_iff_promotion_type": True,
        },
        "reserved": {"lsb": 20, "width": 12, "required_value": 0},
        "castling_to_semantics": "rook-origin-square",
    }
    _require_exact_value(move, expected_move, "move_wire mapping")

    record = _mapping(document["record"], "record")
    _exact_keys(record, {"size", "fields"}, "record")
    _require_exact_value(record.get("size"), 64, "record.size")
    record_fields = _validate_layout(
        record,
        "record",
        [
            ("position", 0, 48, "atomic-bin-v2-position"),
            ("score", 48, 4, "int32"),
            ("move", 52, 4, "uint32"),
            ("ply", 56, 4, "uint32"),
            ("result", 60, 1, "int8"),
            ("flags", 61, 1, "uint8"),
            ("reserved", 62, 2, "bytes"),
        ],
    )
    _require_exact_value(
        record_fields[1].get("domain"), [-2147483647, 2147483647],
        "record score domain",
    )
    _require_exact_value(
        record_fields[4].get("domain"), [-1, 0, 1], "record result domain"
    )
    _require_exact_value(record_fields[5].get("mask"), 1, "record flags mask")
    _require_exact_value(
        record_fields[5].get("bits"), {"atomic960": 0}, "record flags mapping"
    )
    record_allowed = {
        "position": {"name", "offset", "size", "storage"},
        "score": {"name", "offset", "size", "storage", "domain"},
        "move": {"name", "offset", "size", "storage", "encoding"},
        "ply": {"name", "offset", "size", "storage"},
        "result": {"name", "offset", "size", "storage", "domain"},
        "flags": {"name", "offset", "size", "storage", "mask", "bits"},
        "reserved": {"name", "offset", "size", "storage", "required_value"},
    }
    for field in record_fields:
        name = str(field["name"])
        _exact_keys(field, record_allowed[name], f"record field {name}")
    _require_exact_value(
        record_fields[2].get("encoding"), "atomic-bin-v2-move", "record move encoding"
    )
    _require_exact_value(
        record_fields[6].get("required_value"), 0, "record reserved bytes"
    )

    semantics = _mapping(document["semantics"], "semantics")
    _require_exact_value(semantics, {
        "score_perspective": "side-to-move",
        "result_perspective": "side-to-move",
        "position_requires_exactly_one_king_per_color": True,
        "atomic960_castling_rook_origins_preserved": True,
        "non_atomic960_castling_origins": "orthodox-king-and-rook-origins-while-right-active",
        "castling_origin_validation": "correct-color-home-rank-rook-on-indicated-side-of-king",
        "atomic960_flag_preserved_without_castling_rights": True,
        "en_passant_validation": "canonical-capturable-target-with-empty-vacated-start-square",
        "move_validation": "atomic-legal-in-decoded-position",
    }, "semantics")
    policy = _mapping(document["file_policy"], "file_policy")
    _require_exact_value(policy, {
        "extension": ".atbin",
        "empty": "invalid",
        "append": "forbidden",
        "overwrite": "forbidden",
        "file_size_formula": "header-size-plus-record-count-times-record-size",
        "manifest": "required-by-sink-layer",
    }, "file policy")


def load_atomic_bin_v2_schema(path: Path = DEFAULT_SCHEMA_FILE) -> AtomicBinV2Schema:
    resolved = path.resolve()
    payload = resolved.read_bytes()
    if payload.startswith(b"\xef\xbb\xbf"):
        raise AssertionError("Atomic BIN V2 schema must be UTF-8 without a BOM")
    if b"\r" in payload:
        raise AssertionError("Atomic BIN V2 schema must use LF line endings")
    if not payload.endswith(b"\n") or payload.endswith(b"\n\n"):
        raise AssertionError("Atomic BIN V2 schema must end with exactly one LF")
    try:
        document = json.loads(payload.decode("utf-8"), object_pairs_hook=_no_duplicate_keys)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise AssertionError(f"invalid Atomic BIN V2 schema JSON: {error}") from error
    mapping = _mapping(document, "schema")
    validate_atomic_bin_v2_schema(mapping)
    digest = hashlib.sha256(payload).hexdigest()
    if resolved == DEFAULT_SCHEMA_FILE.resolve() and digest != EXPECTED_SHA256:
        raise AssertionError(
            f"Atomic BIN V2 schema digest mismatch: expected {EXPECTED_SHA256}, got {digest}"
        )
    return AtomicBinV2Schema(resolved, digest, 96, 48, 64)


if __name__ == "__main__":
    schema = load_atomic_bin_v2_schema()
    print(
        f"Atomic BIN V2 schema verified: sha256={schema.sha256} "
        f"header={schema.header_size} position={schema.position_size} record={schema.record_size}"
    )
