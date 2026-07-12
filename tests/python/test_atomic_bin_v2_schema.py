from __future__ import annotations

import copy
import json
from pathlib import Path
import sys

import pytest


TESTS_DIR = Path(__file__).resolve().parents[1]
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))

import atomic_bin_v2_schema as schema_module


def checked_in_document() -> dict[str, object]:
    return json.loads(schema_module.DEFAULT_SCHEMA_FILE.read_text(encoding="utf-8"))


def write_schema(path: Path, document: dict[str, object]) -> Path:
    path.write_bytes((json.dumps(document) + "\n").encode("utf-8"))
    return path


def test_checked_in_atomic_bin_v2_schema_is_frozen() -> None:
    schema = schema_module.load_atomic_bin_v2_schema()
    assert schema.sha256 == schema_module.EXPECTED_SHA256
    assert (schema.header_size, schema.position_size, schema.record_size) == (96, 48, 64)
    document = checked_in_document()
    assert bytes.fromhex(document["header"]["magic_hex"]) == b"ATBINV2\0"
    assert document["position"]["fields"][6]["maximum"] == 32767
    assert document["position"]["fields"][7]["maximum"] == 100000
    assert document["record"]["fields"][1]["domain"] == [-2147483647, 2147483647]
    assert document["move_wire"]["none_allowed"] is False
    assert document["move_wire"]["forbidden_value"] == 0
    assert document["move_wire"]["from_to_must_differ"] is True
    assert document["move_wire"]["promotion_type_coupling"] == {
        "promotion_type_name": "promotion",
        "nonzero_promotion_iff_promotion_type": True,
    }


def test_schema_rejects_duplicate_keys(tmp_path: Path) -> None:
    payload = schema_module.DEFAULT_SCHEMA_FILE.read_text(encoding="utf-8")
    payload = payload.replace('"schema_version": 2', '"schema_version": 2,\n  "schema_version": 2', 1)
    path = tmp_path / "schema.json"
    path.write_bytes(payload.encode("utf-8"))
    with pytest.raises(AssertionError, match="duplicate JSON key"):
        schema_module.load_atomic_bin_v2_schema(path)


@pytest.mark.parametrize("ending", (b"\r\n", b"", b"\n\n"))
def test_schema_rejects_noncanonical_line_endings(tmp_path: Path, ending: bytes) -> None:
    payload = schema_module.DEFAULT_SCHEMA_FILE.read_bytes().rstrip(b"\n") + ending
    path = tmp_path / "schema.json"
    path.write_bytes(payload)
    with pytest.raises(AssertionError, match="LF line endings|exactly one LF"):
        schema_module.load_atomic_bin_v2_schema(path)


def test_schema_rejects_self_hash(tmp_path: Path) -> None:
    document = checked_in_document()
    document["sha256"] = "0" * 64
    with pytest.raises(AssertionError, match="self hash"):
        schema_module.load_atomic_bin_v2_schema(write_schema(tmp_path / "schema.json", document))


@pytest.mark.parametrize(
    ("mutation", "message"),
    (
        (("header", "magic_hex", "415442494e56325c"), "magic"),
        (("header", "fields", 2, "offset", 11), "header field layout"),
        (("position", "fields", 6, "size", 4), "position field layout"),
        (("move_wire", "type", "width", 3), "move_wire mapping"),
        (("move_wire", "none_allowed", 0), "move_wire mapping"),
        (("move_wire", "forbidden_value", False), "move_wire mapping"),
        (("move_wire", "from_to_must_differ", 1), "move_wire mapping"),
        (
            ("move_wire", "promotion_type_coupling", "nonzero_promotion_iff_promotion_type", 1),
            "move_wire mapping",
        ),
        (("record", "fields", 1, "offset", 49), "record field layout"),
        (("header", "fields", 7, "minimum", True), "record_count minimum"),
        (("position", "fields", 1, "domain", [False, 1]), "side-to-move domain"),
        (("position", "fields", 2, "mask", True), "castling-right mask"),
        (("piece_wire", "empty", False), "piece_wire mapping"),
        (("record", "fields", 5, "mask", True), "record flags mask"),
    ),
)
def test_schema_rejects_contract_drift(
    tmp_path: Path, mutation: tuple[object, ...], message: str
) -> None:
    document = copy.deepcopy(checked_in_document())
    target: object = document
    for key in mutation[:-2]:
        target = target[key]  # type: ignore[index]
    target[mutation[-2]] = mutation[-1]  # type: ignore[index]
    with pytest.raises(AssertionError, match=message):
        schema_module.load_atomic_bin_v2_schema(write_schema(tmp_path / "schema.json", document))
