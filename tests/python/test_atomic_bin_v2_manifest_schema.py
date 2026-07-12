from __future__ import annotations

import copy
import json
from pathlib import Path
import sys

import pytest


TESTS_DIR = Path(__file__).resolve().parents[1]
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))

import atomic_bin_v2_manifest_schema as schema_module


def checked_in_schema_document() -> dict[str, object]:
    return json.loads(schema_module.DEFAULT_SCHEMA_FILE.read_bytes().decode("utf-8"))


def write_schema(path: Path, document: dict[str, object]) -> Path:
    path.write_bytes((json.dumps(document) + "\n").encode("utf-8"))
    return path


def valid_manifest() -> dict[str, object]:
    return {
        "manifest_version": 1,
        "manifest_schema_sha256": schema_module.EXPECTED_SHA256,
        "data_schema_sha256": schema_module.DATA_SCHEMA_SHA256,
        "format": "atomic-bin-v2",
        "engine": {
            "commit": "0123456789abcdef0123456789abcdef01234567",
            "version": "Atomic-Stockfish H7.3-B",
        },
        "network": {
            "file": "atomic_run3b_e202_l05.nnue",
            "sha256": "a" * 64,
        },
        "book": {
            "kind": "builtin-startpos",
            "file": None,
            "sha256": None,
        },
        "generation": {
            "resolved_seed": "18446744073709551615",
            "atomic960": True,
            "threads": 4,
            "hash_mb": "512",
            "use_nnue": "pure",
            "options": {
                "search_depth_min": 1,
                "search_depth_max": 3,
                "nodes": "0",
                "requested_records": "2",
                "records_per_shard": "1",
                "eval_limit": 32000,
                "eval_diff_limit": 100,
                "random_move_min_ply": -1,
                "random_move_max_ply": 0,
                "random_move_count": 0,
                "random_move_like_apery": 0,
                "random_multi_pv": 1,
                "random_multi_pv_diff": 0,
                "random_multi_pv_depth": 0,
                "write_min_ply": 0,
                "write_max_ply": 200,
                "keep_draws": "1",
                "adjudicate_draws_by_score": False,
                "adjudicate_insufficient": True,
                "filter_captures": False,
                "filter_checks": False,
                "filter_promotions": False,
                "random_file_name": False,
                "set_recommended_uci_options_seen": True,
            },
        },
        "statistics": {
            "records": "2",
            "draws": "1",
        },
        "shards": [
            {
                "index": 0,
                "file": "training-00000.atbin",
                "records": "1",
                "bytes": "160",
                "sha256": "b" * 64,
            },
            {
                "index": 1,
                "file": "training-00001.atbin",
                "records": "1",
                "bytes": "160",
                "sha256": "c" * 64,
            },
        ],
    }


def replace_path(document: dict[str, object], path: tuple[object, ...], value: object) -> None:
    target: object = document
    for key in path[:-1]:
        target = target[key]  # type: ignore[index]
    target[path[-1]] = value  # type: ignore[index]


def mutated_manifest(path: tuple[object, ...], value: object) -> dict[str, object]:
    document = copy.deepcopy(valid_manifest())
    replace_path(document, path, value)
    return document


def test_checked_in_manifest_schema_is_frozen_and_has_no_self_hash() -> None:
    schema = schema_module.load_atomic_bin_v2_manifest_schema()
    assert schema.sha256 == schema_module.EXPECTED_SHA256
    payload = schema_module.DEFAULT_SCHEMA_FILE.read_bytes()
    document = checked_in_schema_document()
    assert document["schema_version"] == 1
    assert document["status"] == "frozen"
    assert "schema_sha256" not in document
    assert schema.sha256.encode("ascii") not in payload
    assert document["properties"]["manifest_schema_sha256"] == {
        "$ref": "#/$defs/lower-hex-sha256",
        "x-value": "sha256-of-exact-manifest-schema-file",
    }
    assert document["properties"]["data_schema_sha256"] == {
        "const": schema_module.DATA_SCHEMA_SHA256
    }
    assert document["properties"]["shards"]["x-index-policy"] == (
        "zero-based-contiguous-in-array-order"
    )
    assert document["x-file-policy"] == {
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


def test_manifest_schema_rejects_duplicate_keys(tmp_path: Path) -> None:
    payload = schema_module.DEFAULT_SCHEMA_FILE.read_bytes().decode("utf-8")
    payload = payload.replace(
        '"schema_version": 1',
        '"schema_version": 1,\n  "schema_version": 1',
        1,
    )
    path = tmp_path / "manifest-schema.json"
    path.write_bytes(payload.encode("utf-8"))
    with pytest.raises(AssertionError, match="duplicate JSON key"):
        schema_module.load_atomic_bin_v2_manifest_schema(path)


@pytest.mark.parametrize("ending", (b"\r\n", b"", b"\n\n"))
def test_manifest_schema_rejects_noncanonical_line_endings(
    tmp_path: Path, ending: bytes
) -> None:
    payload = schema_module.DEFAULT_SCHEMA_FILE.read_bytes().rstrip(b"\n") + ending
    path = tmp_path / "manifest-schema.json"
    path.write_bytes(payload)
    with pytest.raises(AssertionError, match="LF line endings|exactly one LF"):
        schema_module.load_atomic_bin_v2_manifest_schema(path)


def test_manifest_schema_rejects_utf8_bom(tmp_path: Path) -> None:
    path = tmp_path / "manifest-schema.json"
    path.write_bytes(b"\xef\xbb\xbf" + schema_module.DEFAULT_SCHEMA_FILE.read_bytes())
    with pytest.raises(AssertionError, match="without a BOM"):
        schema_module.load_atomic_bin_v2_manifest_schema(path)


@pytest.mark.parametrize(
    ("path", "value"),
    (
        (("schema_version",), True),
        (("status",), "draft"),
        (("additionalProperties",), 0),
        (("properties", "manifest_version", "const"), True),
        (("properties", "data_schema_sha256", "const"), "0" * 64),
        (("properties", "generation", "properties", "atomic960", "type"), "integer"),
        (("$defs", "positive-uint32", "minimum"), True),
        (("properties", "shards", "x-index-policy"), "unique-only"),
        (("x-file-policy", "trailing_lf_count"), True),
    ),
)
def test_manifest_schema_rejects_contract_or_type_drift(
    tmp_path: Path, path: tuple[object, ...], value: object
) -> None:
    document = copy.deepcopy(checked_in_schema_document())
    replace_path(document, path, value)
    with pytest.raises(AssertionError, match="manifest schema"):
        schema_module.load_atomic_bin_v2_manifest_schema(
            write_schema(tmp_path / "manifest-schema.json", document)
        )


def test_valid_manifest_and_canonical_file_round_trip(tmp_path: Path) -> None:
    document = valid_manifest()
    schema_module.validate_atomic_bin_v2_manifest(document)
    payload = schema_module.canonical_atomic_bin_v2_manifest_bytes(document)
    assert payload.endswith(b"\n") and not payload.endswith(b"\n\n")
    assert b"\r" not in payload
    assert b"\n" not in payload[:-1]
    path = tmp_path / "training.atbin.manifest.json"
    path.write_bytes(payload)
    assert schema_module.load_atomic_bin_v2_manifest(path) == document


@pytest.mark.parametrize(
    ("path", "value", "message"),
    (
        (("manifest_version",), True, "manifest_version"),
        (("generation", "atomic960"), 1, "boolean"),
        (("generation", "threads"), True, "positive uint32"),
        (("generation", "options", "search_depth_min"), False, "int32"),
        (("generation", "options", "nodes"), 0, "string"),
        (("generation", "options", "filter_checks"), 0, "boolean"),
        (("statistics", "records"), 2, "string"),
        (("shards", 0, "index"), False, "uint32"),
        (("shards", 0, "records"), 1, "string"),
    ),
)
def test_manifest_validation_is_type_exact(
    path: tuple[object, ...], value: object, message: str
) -> None:
    with pytest.raises(AssertionError, match=message):
        schema_module.validate_atomic_bin_v2_manifest(mutated_manifest(path, value))


@pytest.mark.parametrize(
    ("path", "value", "message"),
    (
        (("generation", "resolved_seed"), "18446744073709551616", "uint64"),
        (("generation", "resolved_seed"), "00", "uint64"),
        (("generation", "hash_mb"), "-1", "uint64"),
        (("generation", "threads"), 0, "positive uint32"),
        (("generation", "threads"), 4294967296, "positive uint32"),
        (("generation", "options", "eval_limit"), 2147483648, "int32"),
        (("generation", "options", "write_min_ply"), -2147483649, "int32"),
        (("generation", "options", "keep_draws"), "1.0", "canonical decimal"),
        (("generation", "options", "keep_draws"), "0.50", "canonical decimal"),
        (("generation", "options", "keep_draws"), "1e0", "canonical decimal"),
        (("statistics", "draws"), "01", "uint64"),
    ),
)
def test_manifest_rejects_noncanonical_numbers_and_out_of_range_values(
    path: tuple[object, ...], value: object, message: str
) -> None:
    with pytest.raises(AssertionError, match=message):
        schema_module.validate_atomic_bin_v2_manifest(mutated_manifest(path, value))


@pytest.mark.parametrize(
    ("path", "value", "message"),
    (
        (("manifest_schema_sha256",), "A" * 64, "lower-case hex"),
        (("data_schema_sha256",), "0" * 64, "data_schema_sha256"),
        (("engine", "commit"), "A" * 40, "lower-case hex40"),
        (("engine", "commit"), "unknown ", "lower-case hex40"),
        (("engine", "version"), "", "nonempty"),
        (("network", "sha256"), "A" * 64, "lower-case hex"),
        (("network", "file"), "/tmp/net.nnue", "basename"),
        (("network", "file"), "C:\\nets\\net.nnue", "basename"),
        (("book", "kind"), "builtin", "book.kind"),
        (("shards", 0, "file"), "subdir/training.atbin", "basename"),
        (("shards", 0, "file"), "training.bin", "ending in .atbin"),
        (("shards", 0, "sha256"), "g" * 64, "lower-case hex"),
    ),
)
def test_manifest_rejects_identity_hash_and_path_drift(
    path: tuple[object, ...], value: object, message: str
) -> None:
    with pytest.raises(AssertionError, match=message):
        schema_module.validate_atomic_bin_v2_manifest(mutated_manifest(path, value))


def test_engine_unknown_and_file_book_are_valid() -> None:
    document = valid_manifest()
    document["engine"]["commit"] = "unknown"  # type: ignore[index]
    document["book"] = {
        "kind": "file",
        "file": "atomic-openings.epd",
        "sha256": "d" * 64,
    }
    schema_module.validate_atomic_bin_v2_manifest(document)


@pytest.mark.parametrize(
    "book",
    (
        {"kind": "builtin-startpos", "file": "book.epd", "sha256": "a" * 64},
        {"kind": "file", "file": None, "sha256": None},
        {"kind": "file", "file": "../book.epd", "sha256": "a" * 64},
    ),
)
def test_book_kind_controls_file_and_hash_nullability(book: dict[str, object]) -> None:
    document = valid_manifest()
    document["book"] = book
    with pytest.raises(AssertionError):
        schema_module.validate_atomic_bin_v2_manifest(document)


def test_shards_must_be_nonempty_and_contiguous_from_zero() -> None:
    document = valid_manifest()
    document["shards"] = []
    with pytest.raises(AssertionError, match="nonempty"):
        schema_module.validate_atomic_bin_v2_manifest(document)

    document = valid_manifest()
    document["shards"][1]["index"] = 2  # type: ignore[index]
    with pytest.raises(AssertionError, match="contiguous from zero"):
        schema_module.validate_atomic_bin_v2_manifest(document)


def test_manifest_rejects_missing_extra_and_timestamp_fields() -> None:
    document = valid_manifest()
    del document["statistics"]
    with pytest.raises(AssertionError, match="keys mismatch"):
        schema_module.validate_atomic_bin_v2_manifest(document)

    document = valid_manifest()
    document["timestamp"] = "2026-07-12T00:00:00Z"
    with pytest.raises(AssertionError, match="keys mismatch"):
        schema_module.validate_atomic_bin_v2_manifest(document)


@pytest.mark.parametrize("ending", (b"\r\n", b"", b"\n\n"))
def test_manifest_file_rejects_noncanonical_line_endings(
    tmp_path: Path, ending: bytes
) -> None:
    payload = schema_module.canonical_atomic_bin_v2_manifest_bytes(
        valid_manifest()
    ).rstrip(b"\n") + ending
    path = tmp_path / "training.atbin.manifest.json"
    path.write_bytes(payload)
    with pytest.raises(AssertionError, match="LF line endings|exactly one LF"):
        schema_module.load_atomic_bin_v2_manifest(path)


def test_manifest_file_rejects_bom_pretty_unsorted_and_duplicate_json(
    tmp_path: Path,
) -> None:
    document = valid_manifest()
    canonical = schema_module.canonical_atomic_bin_v2_manifest_bytes(document)
    variants = {
        "bom": b"\xef\xbb\xbf" + canonical,
        "pretty": (json.dumps(document, indent=2) + "\n").encode("utf-8"),
        "wrong-order": (
            json.dumps(document, separators=(",", ":"), sort_keys=True) + "\n"
        ).encode("utf-8"),
        "duplicate": canonical.replace(
            b'"format":"atomic-bin-v2"',
            b'"format":"atomic-bin-v2","format":"atomic-bin-v2"',
            1,
        ),
    }
    for name, payload in variants.items():
        path = tmp_path / f"{name}.manifest.json"
        path.write_bytes(payload)
        with pytest.raises(AssertionError):
            schema_module.load_atomic_bin_v2_manifest(path)
