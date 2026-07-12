from __future__ import annotations

import json
from pathlib import Path
import sys

import pytest


TESTS_DIR = Path(__file__).resolve().parents[1]
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))

import atomic_training_data_schema as schema_module


def checked_in_document() -> dict[str, object]:
    return json.loads(
        schema_module.DEFAULT_SCHEMA_FILE.read_text(encoding="utf-8")
    )


def write_schema(path: Path, document: dict[str, object]) -> Path:
    path.write_text(json.dumps(document) + "\n", encoding="utf-8", newline="\n")
    return path


def test_checked_in_schema_is_canonical_and_frozen() -> None:
    schema = schema_module.load_training_data_schema()
    document = checked_in_document()
    assert schema.schema_id == "legacy-atomic-v1"
    assert schema.record_size == 72
    assert schema.sha256 == (
        "acca0f551f1c012c31a6c727dedccaebb7b5ebbc46810edb87e31bb208d5abe1"
    )
    assert document["format"]["host_byte_order_required"] == "little-endian"
    assert document["packed_position"]["canonical"] is False
    assert document["packed_position"]["missing_king_supported"] is False
    assert document["packed_position"]["hand_count_fields"] == {
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
    }
    assert document["move_wire"]["none_allowed"] is False
    assert document["move_wire"]["forbidden_value"] == 0


def test_schema_rejects_duplicate_keys(tmp_path: Path) -> None:
    payload = schema_module.DEFAULT_SCHEMA_FILE.read_text(encoding="utf-8")
    payload = payload.replace(
        '"schema_id": "legacy-atomic-v1"',
        '"schema_id": "legacy-atomic-v1",\n  "schema_id": "legacy-atomic-v1"',
        1,
    )
    path = tmp_path / "schema.json"
    path.write_text(payload, encoding="utf-8", newline="\n")
    with pytest.raises(AssertionError, match="duplicate JSON key"):
        schema_module.load_training_data_schema(path)


@pytest.mark.parametrize("ending", (b"\r\n", b"", b"\n\n"))
def test_schema_rejects_noncanonical_line_endings(
    tmp_path: Path, ending: bytes
) -> None:
    payload = schema_module.DEFAULT_SCHEMA_FILE.read_bytes().rstrip(b"\n") + ending
    path = tmp_path / "schema.json"
    path.write_bytes(payload)
    with pytest.raises(AssertionError, match="LF line endings|exactly one LF"):
        schema_module.load_training_data_schema(path)


def test_schema_rejects_self_hash(tmp_path: Path) -> None:
    document = checked_in_document()
    document["schema_sha256"] = "0" * 64
    with pytest.raises(AssertionError, match="self hash"):
        schema_module.load_training_data_schema(
            write_schema(tmp_path / "schema.json", document)
        )


@pytest.mark.parametrize(
    ("field_index", "key", "value", "message"),
    (
        (1, "offset", 63, "field score offset"),
        (1, "size", 3, "field score size"),
        (5, "offset", 72, "field padding offset"),
    ),
)
def test_schema_rejects_field_layout_drift(
    tmp_path: Path,
    field_index: int,
    key: str,
    value: object,
    message: str,
) -> None:
    document = checked_in_document()
    document["format"]["fields"][field_index][key] = value
    with pytest.raises(AssertionError, match=message):
        schema_module.load_training_data_schema(
            write_schema(tmp_path / "schema.json", document)
        )


def test_schema_rejects_move_wire_drift(tmp_path: Path) -> None:
    document = checked_in_document()
    document["move_wire"]["type"]["lsb"] = 13
    with pytest.raises(AssertionError, match="move_wire.type.lsb"):
        schema_module.load_training_data_schema(
            write_schema(tmp_path / "schema.json", document)
        )
