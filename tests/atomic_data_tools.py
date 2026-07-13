#!/usr/bin/env python3
"""Black-box contract tests for the Atomic BIN V2 production data-tools CLI."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import struct
import subprocess
import tempfile
from typing import Any


DATA_SCHEMA = "0352b036f2a140c609e3eb9c9d635dc553e8d77253d8faa92437390f5cf93cb6"
MANIFEST_SCHEMA = (
    "83d63922df3ac4a0c81a21ec9d9fd9e180efe50f26efee62fe01710e09da5b42"
)
DECODE_SCHEMA = "5e3f8d7c6db6ee955b71747ee063859e15609adb557a3754228a606f3df2caad"
DECODE_SCHEMA_PATH = (
    Path(__file__).resolve().parents[1] / "schemas" / "atomic-data-tools-decode-v1.json"
)
GOLDEN_RECORD = bytes.fromhex(
    "2453364211111111"
    "00000000000000000000000000000000"
    "777777778ab99ca8"
    "000f07003f38ff00"
    "0000010000000000"
    "85ffffff0c0700002a000000ff000000"
)
ATOMIC960_RECORD = GOLDEN_RECORD[:61] + b"\x01" + GOLDEN_RECORD[62:]

PIECE_CODES = {
    "P": 1,
    "N": 2,
    "B": 3,
    "R": 4,
    "Q": 5,
    "K": 6,
    "p": 7,
    "n": 8,
    "b": 9,
    "r": 10,
    "q": 11,
    "k": 12,
}
MOVE_TYPES = {"normal": 0, "promotion": 1, "en-passant": 2, "castling": 3}
PROMOTIONS = {"none": 0, "knight": 1, "bishop": 2, "rook": 3, "queen": 4}


def canonical_json(value: dict[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")) + "\n"


def run(tools: Path, *arguments: str) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        [str(tools), *arguments],
        check=False,
        capture_output=True,
    )


def expect_success(
    completed: subprocess.CompletedProcess[bytes], expected: dict[str, Any]
) -> None:
    assert completed.returncode == 0, completed
    assert completed.stderr == b"", completed.stderr
    assert b"\r" not in completed.stdout
    assert completed.stdout == canonical_json(expected).encode("utf-8"), completed.stdout


def expect_error(
    completed: subprocess.CompletedProcess[bytes],
    exit_code: int,
    *,
    operation: str,
    format_name: str | None,
    code: str,
) -> dict[str, Any]:
    assert completed.returncode == exit_code, completed
    assert completed.stdout == b"", completed.stdout
    assert b"\r" not in completed.stderr
    assert completed.stderr.endswith(b"\n") and completed.stderr.count(b"\n") == 1
    decoded = completed.stderr.decode("utf-8", errors="strict")
    error = json.loads(decoded)
    assert completed.stderr == canonical_json(error).encode("utf-8"), completed.stderr
    assert error == {
        "type": "atomic-data-tools-error",
        "contract_version": 1,
        "status": "error",
        "operation": operation,
        "format": format_name,
        "code": code,
        "message": error["message"],
    }
    assert isinstance(error["message"], str) and error["message"]
    return error


def square_index(square: str) -> int:
    assert len(square) == 2 and square[0] in "abcdefgh" and square[1] in "12345678"
    return ord(square[0]) - ord("a") + 8 * (ord(square[1]) - ord("1"))


def encode_record(
    fen: str,
    *,
    move_from: str,
    move_to: str,
    move_type: str = "normal",
    promotion: str = "none",
    score: int = -123,
    ply: int = 42,
    result: int = -1,
    atomic960: bool = False,
) -> bytes:
    board_field, stm, castling, ep, rule50, fullmove = fen.split(" ")
    board = [0] * 64
    ranks = board_field.split("/")
    assert len(ranks) == 8
    for rank_index, encoded_rank in enumerate(reversed(ranks)):
        file_index = 0
        for token in encoded_rank:
            if token.isdigit():
                file_index += int(token)
            else:
                board[rank_index * 8 + file_index] = PIECE_CODES[token]
                file_index += 1
        assert file_index == 8

    position = bytearray(48)
    for square, piece in enumerate(board):
        position[square // 2] |= piece << ((square & 1) * 4)
    position[32] = 0 if stm == "w" else 1

    rights = 0
    origins = [0xFF] * 4
    if castling != "-":
        if not atomic960:
            orthodox = {"K": (0, "h1"), "Q": (1, "a1"), "k": (2, "h8"), "q": (3, "a8")}
            for token in castling:
                index, origin = orthodox[token]
                rights |= 1 << index
                origins[index] = square_index(origin)
        else:
            white_king = board.index(PIECE_CODES["K"])
            black_king = board.index(PIECE_CODES["k"])
            for token in castling:
                white = token.isupper()
                file_index = ord(token.lower()) - ord("a")
                origin = file_index + (0 if white else 56)
                king = white_king if white else black_king
                index = (0 if file_index > king % 8 else 1) + (0 if white else 2)
                rights |= 1 << index
                origins[index] = origin
    position[33] = rights
    position[34:38] = bytes(origins)
    position[38] = 0xFF if ep == "-" else square_index(ep)
    struct.pack_into("<H", position, 40, int(rule50))
    struct.pack_into("<I", position, 42, int(fullmove))

    move_wire = (
        square_index(move_from)
        | (square_index(move_to) << 6)
        | (MOVE_TYPES[move_type] << 12)
        | (PROMOTIONS[promotion] << 16)
    )
    record = bytearray(64)
    record[:48] = position
    struct.pack_into("<i", record, 48, score)
    struct.pack_into("<I", record, 52, move_wire)
    struct.pack_into("<I", record, 56, ply)
    struct.pack_into("<b", record, 60, result)
    record[61] = int(atomic960)
    return bytes(record)


def shard_bytes(*records: bytes) -> bytes:
    assert records and all(len(record) == 64 for record in records)
    header = struct.pack(
        "<8sHHIII32sQ32s",
        b"ATBINV2\0",
        2,
        96,
        0x01020304,
        64,
        0,
        bytes.fromhex(DATA_SCHEMA),
        len(records),
        bytes(32),
    )
    assert len(header) == 96
    return header + b"".join(records)


def manifest_for(
    root: Path, records: tuple[bytes, ...], *, atomic960: bool = False
) -> tuple[Path, list[Path]]:
    return manifest_for_shards(root, tuple((record,) for record in records), atomic960=atomic960)


def manifest_for_shards(
    root: Path,
    record_shards: tuple[tuple[bytes, ...], ...],
    *,
    atomic960: bool = False,
) -> tuple[Path, list[Path]]:
    assert record_shards and all(record_shards)
    records = tuple(record for shard in record_shards for record in shard)
    assert records
    root.mkdir(parents=True)
    shards: list[Path] = []
    shard_metadata: list[dict[str, Any]] = []
    for index, shard_records in enumerate(record_shards):
        filename = "dataset.atbin" if index == 0 else f"dataset-{index:06d}.atbin"
        path = root / filename
        payload = shard_bytes(*shard_records)
        path.write_bytes(payload)
        shards.append(path)
        shard_metadata.append(
            {
                "index": index,
                "file": filename,
                "records": str(len(shard_records)),
                "bytes": str(len(payload)),
                "sha256": hashlib.sha256(payload).hexdigest(),
            }
        )

    count = len(records)
    records_per_shard = len(record_shards[0])
    assert all(len(shard) == records_per_shard for shard in record_shards[:-1])
    assert len(record_shards[-1]) <= records_per_shard
    manifest = {
        "manifest_version": 1,
        "manifest_schema_sha256": MANIFEST_SCHEMA,
        "data_schema_sha256": DATA_SCHEMA,
        "format": "atomic-bin-v2",
        "engine": {
            "commit": "0123456789abcdef0123456789abcdef01234567",
            "version": "Atomic-Stockfish data-tools test",
        },
        "network": {"file": "atomic.nnue", "sha256": "1" * 64},
        "book": {"kind": "builtin-startpos", "file": None, "sha256": None},
        "generation": {
            "resolved_seed": "9",
            "atomic960": atomic960,
            "threads": 1,
            "hash_mb": "16",
            "use_nnue": "pure",
            "options": {
                "search_depth_min": 3,
                "search_depth_max": 3,
                "nodes": "0",
                "requested_records": str(count),
                "records_per_shard": str(records_per_shard),
                "eval_limit": 3000,
                "eval_diff_limit": 64000,
                "random_move_min_ply": 1,
                "random_move_max_ply": 24,
                "random_move_count": 5,
                "random_move_like_apery": 0,
                "random_multi_pv": 5,
                "random_multi_pv_diff": 100,
                "random_multi_pv_depth": 3,
                "write_min_ply": 5,
                "write_max_ply": 400,
                "keep_draws": "0.5",
                "adjudicate_draws_by_score": False,
                "adjudicate_insufficient": False,
                "filter_captures": False,
                "filter_checks": False,
                "filter_promotions": False,
                "random_file_name": False,
                "set_recommended_uci_options_seen": False,
            },
        },
        "statistics": {
            "records": str(count),
            "draws": str(sum(record[60] == 0 for record in records)),
        },
        "shards": shard_metadata,
    }
    manifest_path = root / "dataset.atbin.manifest.json"
    manifest_path.write_bytes(canonical_json(manifest).encode("utf-8"))
    return manifest_path, shards


def test_capabilities(tools: Path) -> None:
    schema_bytes = DECODE_SCHEMA_PATH.read_bytes()
    assert schema_bytes.startswith(b"{") and schema_bytes.endswith(b"\n")
    assert b"\r" not in schema_bytes and not schema_bytes.startswith(b"\xef\xbb\xbf")
    assert hashlib.sha256(schema_bytes).hexdigest() == DECODE_SCHEMA
    json.loads(schema_bytes.decode("utf-8", errors="strict"))
    expect_success(
        run(tools, "capabilities"),
        {
            "type": "atomic-data-tools-capabilities",
            "contract_version": 1,
            "formats": {
                "atomic-bin-v2": {
                    "data_schema_sha256": DATA_SCHEMA,
                    "manifest_schema_sha256": MANIFEST_SCHEMA,
                    "decode_schema_sha256": DECODE_SCHEMA,
                    "entrypoint": "manifest",
                    "read": True,
                    "write": False,
                    "operations": ["validate", "decode"],
                }
            },
        },
    )
    expect_error(
        run(tools, "capabilities", "extra"),
        2,
        operation="capabilities",
        format_name=None,
        code="unexpected_argument",
    )
    unicode_error = expect_error(
        run(tools, "waté"),
        2,
        operation="cli",
        format_name=None,
        code="unknown_command",
    )
    assert unicode_error["message"] == "unknown command: waté"


def test_valid_multishard(tools: Path, root: Path) -> tuple[Path, list[Path]]:
    manifest, shards = manifest_for(root, (GOLDEN_RECORD, GOLDEN_RECORD))
    expect_success(
        run(
            tools,
            "validate",
            "--manifest",
            str(manifest),
            "--format",
            "atomic-bin-v2",
        ),
        {
            "type": "atomic-data-tools-validation",
            "contract_version": 1,
            "status": "ok",
            "format": "atomic-bin-v2",
            "entrypoint": "manifest",
            "shards": 2,
            "records": "2",
            "side_to_move_wins": "0",
            "draws": "0",
            "side_to_move_losses": "2",
            "atomic960_records": "0",
        },
    )
    return manifest, shards


def test_valid_atomic960_stats(tools: Path, root: Path) -> None:
    manifest, _ = manifest_for(root, (ATOMIC960_RECORD,), atomic960=True)
    expect_success(
        run(
            tools,
            "validate",
            "--format",
            "atomic-bin-v2",
            "--manifest",
            str(manifest),
        ),
        {
            "type": "atomic-data-tools-validation",
            "contract_version": 1,
            "status": "ok",
            "format": "atomic-bin-v2",
            "entrypoint": "manifest",
            "shards": 1,
            "records": "1",
            "side_to_move_wins": "0",
            "draws": "0",
            "side_to_move_losses": "1",
            "atomic960_records": "1",
        },
    )


def test_manifest_only_and_contract_errors(
    tools: Path, manifest: Path, shards: list[Path]
) -> None:
    raw = expect_error(
        run(
            tools,
            "validate",
            "--format",
            "atomic-bin-v2",
            "--manifest",
            str(shards[0]),
        ),
        3,
        operation="validate",
        format_name="atomic-bin-v2",
        code="invalid_manifest",
    )
    assert ".atbin.manifest.json sidecar" in raw["message"]

    missing = expect_error(
        run(
            tools,
            "validate",
            "--format",
            "atomic-bin-v2",
            "--manifest",
            str(manifest.with_name("missing-é.atbin.manifest.json")),
        ),
        3,
        operation="validate",
        format_name="atomic-bin-v2",
        code="open_failed",
    )
    assert "Cannot open Atomic BIN V2 manifest" in missing["message"]

    cases = (
        ((), "cli", None, "missing_command"),
        (("unknown",), "cli", None, "unknown_command"),
        (("validate", "--manifest", str(manifest)), "validate", None, "missing_format"),
        (
            ("validate", "--format", "legacy-atomic-v1", "--manifest", str(manifest)),
            "validate",
            "legacy-atomic-v1",
            "unsupported_format",
        ),
        (
            ("validate", "--format", "atomic-bin-v2"),
            "validate",
            "atomic-bin-v2",
            "missing_manifest",
        ),
        (
            ("validate", "--format", "atomic-bin-v2", "--manifest"),
            "validate",
            "atomic-bin-v2",
            "missing_value",
        ),
        (
            ("validate", "--manifest", "--format"),
            "validate",
            None,
            "missing_value",
        ),
        (
            ("validate", "--format", "--manifest", str(manifest)),
            "validate",
            None,
            "missing_value",
        ),
        (
            (
                "validate",
                "--format",
                "atomic-bin-v2",
                "--format",
                "atomic-bin-v2",
            ),
            "validate",
            "atomic-bin-v2",
            "duplicate_argument",
        ),
        (
            ("validate", "--format", "atomic-bin-v2", "--input", str(manifest)),
            "validate",
            "atomic-bin-v2",
            "unknown_argument",
        ),
    )
    for arguments, operation, format_name, code in cases:
        expect_error(
            run(tools, *arguments),
            2,
            operation=operation,
            format_name=format_name,
            code=code,
        )


def test_streamed_indexed_corruption(tools: Path, root: Path) -> None:
    corrupted = bytearray(GOLDEN_RECORD)
    corrupted[62] = 1
    manifest, _ = manifest_for(root, (GOLDEN_RECORD, bytes(corrupted)))
    error = expect_error(
        run(
            tools,
            "validate",
            "--format",
            "atomic-bin-v2",
            "--manifest",
            str(manifest),
        ),
        3,
        operation="validate",
        format_name="atomic-bin-v2",
        code="invalid_record",
    )
    assert "shard=1 local=0 global=1" in error["message"]
    assert "reserved bytes are nonzero" in error["message"]


def test_authenticated_corruption(tools: Path, root: Path) -> None:
    manifest, shards = manifest_for(root, (GOLDEN_RECORD, GOLDEN_RECORD))
    payload = bytearray(shards[1].read_bytes())
    payload[-1] ^= 1
    shards[1].write_bytes(payload)
    error = expect_error(
        run(
            tools,
            "validate",
            "--format",
            "atomic-bin-v2",
            "--manifest",
            str(manifest),
        ),
        3,
        operation="validate",
        format_name="atomic-bin-v2",
        code="schema_mismatch",
    )
    assert "shard=1" in error["message"]
    assert "shard SHA-256 differs from manifest" in error["message"]


def decode_command(
    tools: Path,
    manifest: Path,
    limit: str,
    *,
    offset: str | None = None,
) -> subprocess.CompletedProcess[bytes]:
    arguments = [
        "decode",
        "--format",
        "atomic-bin-v2",
        "--manifest",
        str(manifest),
    ]
    if offset is not None:
        arguments.extend(("--offset", offset))
    arguments.extend(("--limit", limit))
    return run(tools, *arguments)


def decode_jsonl(completed: subprocess.CompletedProcess[bytes]) -> list[dict[str, Any]]:
    assert completed.returncode == 0, completed
    assert completed.stderr == b""
    assert completed.stdout.endswith(b"\n") and b"\r" not in completed.stdout
    assert not completed.stdout.startswith(b"\xef\xbb\xbf")
    return [
        json.loads(line.decode("utf-8", errors="strict"))
        for line in completed.stdout.splitlines()
    ]


def expected_decode_header(
    manifest: dict[str, Any], *, offset: str, limit: int
) -> dict[str, Any]:
    generation = manifest["generation"]
    return {
        "type": "atomic-data-tools-decode-header",
        "contract_version": 1,
        "status": "ok",
        "format": "atomic-bin-v2",
        "entrypoint": "manifest",
        "decode_schema_sha256": DECODE_SCHEMA,
        "data_schema_sha256": DATA_SCHEMA,
        "manifest_schema_sha256": MANIFEST_SCHEMA,
        "slice": {"offset": offset, "limit": limit},
        "dataset": {
            "records": manifest["statistics"]["records"],
            "shards": len(manifest["shards"]),
            "atomic960": generation["atomic960"],
        },
        "provenance": {
            "engine": manifest["engine"],
            "network": manifest["network"],
            "book": manifest["book"],
            "generation": generation,
        },
    }


def expected_validation_footer(
    *,
    offset: str,
    limit: int,
    shards: int,
    records: int,
    losses: int,
    wins: int = 0,
    draws: int = 0,
    atomic960_records: int = 0,
) -> dict[str, Any]:
    return {
        "type": "atomic-data-tools-decode-footer",
        "contract_version": 1,
        "status": "ok",
        "format": "atomic-bin-v2",
        "slice": {"offset": offset, "limit": limit, "records": str(limit)},
        "validation": {
            "status": "ok",
            "shards": shards,
            "records": str(records),
            "side_to_move_wins": str(wins),
            "draws": str(draws),
            "side_to_move_losses": str(losses),
            "atomic960_records": str(atomic960_records),
        },
    }


def test_decode_golden_byte_exact(tools: Path, root: Path) -> tuple[Path, list[Path]]:
    start_fen = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"
    normal = encode_record(start_fen, move_from="e2", move_to="e4")
    assert normal == GOLDEN_RECORD
    manifest_path, shards = manifest_for(root, (normal,))
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    expected_record = {
        "type": "atomic-data-tools-decode-record",
        "contract_version": 1,
        "global_index": "0",
        "shard_index": "0",
        "local_index": "0",
        "position": {
            "fen": start_fen,
            "fen_notation": "fen",
            "side_to_move": "white",
            "rule50": 0,
            "fullmove": 1,
            "castling_rights": {"wire": 15, "fen": "KQkq"},
            "castling_rook_origins": {
                "white_kingside": "h1",
                "white_queenside": "a1",
                "black_kingside": "h8",
                "black_queenside": "a8",
            },
            "en_passant": None,
        },
        "score_stm": -123,
        "ply": "42",
        "result_stm": -1,
        "flags": 0,
        "atomic960": False,
        "move": {
            "wire": str(0x70C),
            "from": "e2",
            "to": "e4",
            "type": "normal",
            "promotion": "none",
        },
    }
    expected = (
        canonical_json(expected_decode_header(manifest, offset="0", limit=1))
        + canonical_json(expected_record)
        + canonical_json(
            expected_validation_footer(
                offset="0", limit=1, shards=1, records=1, losses=1
            )
        )
    ).encode("utf-8")

    completed = decode_command(tools, manifest_path, "1")
    assert completed.returncode == 0 and completed.stderr == b"", completed
    assert completed.stdout == expected
    assert b"\r" not in completed.stdout and not completed.stdout.startswith(b"\xef\xbb\xbf")
    return manifest_path, shards


def test_decode_all_move_types_and_second_shard(tools: Path, root: Path) -> None:
    normal = encode_record(
        "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1",
        move_from="e2",
        move_to="e4",
    )
    en_passant = encode_record(
        "7k/8/8/3pP3/8/8/8/K7 w - d6 0 1",
        move_from="e5",
        move_to="d6",
        move_type="en-passant",
    )
    promotion = encode_record(
        "7k/P7/8/8/8/8/8/K7 w - - 0 1",
        move_from="a7",
        move_to="a8",
        move_type="promotion",
        promotion="queen",
    )
    castling = encode_record(
        "r3k2r/8/8/8/8/8/8/R3K2R w KQkq - 0 1",
        move_from="e1",
        move_to="h1",
        move_type="castling",
    )
    manifest, _ = manifest_for_shards(
        root, ((normal, en_passant), (promotion, castling))
    )
    lines = decode_jsonl(decode_command(tools, manifest, "3", offset="1"))
    assert len(lines) == 5
    assert lines[0]["slice"] == {"offset": "1", "limit": 3}
    records = lines[1:-1]
    assert [record["global_index"] for record in records] == ["1", "2", "3"]
    assert [record["shard_index"] for record in records] == ["0", "1", "1"]
    assert [record["local_index"] for record in records] == ["1", "0", "1"]
    assert [record["move"]["type"] for record in records] == [
        "en-passant",
        "promotion",
        "castling",
    ]
    assert records[0]["move"] == {
        "wire": str(0x2AE4),
        "from": "e5",
        "to": "d6",
        "type": "en-passant",
        "promotion": "none",
    }
    assert records[0]["position"]["en_passant"] == "d6"
    assert records[1]["move"]["wire"] == str(0x41E30)
    assert records[1]["move"]["promotion"] == "queen"
    assert records[2]["move"]["wire"] == str(0x31C4)
    assert records[2]["move"]["to"] == "h1"
    assert records[2]["position"]["castling_rook_origins"] == {
        "white_kingside": "h1",
        "white_queenside": "a1",
        "black_kingside": "h8",
        "black_queenside": "a8",
    }
    assert lines[-1] == expected_validation_footer(
        offset="1", limit=3, shards=2, records=4, losses=4
    )


def test_decode_atomic960_and_uint32_max_ply(tools: Path, root: Path) -> None:
    fen = "7k/8/8/8/8/8/8/1RK5 w B - 17 29"
    record = encode_record(
        fen,
        move_from="c1",
        move_to="b1",
        move_type="castling",
        ply=0xFFFFFFFF,
        atomic960=True,
    )
    manifest, _ = manifest_for(root, (record,), atomic960=True)
    lines = decode_jsonl(decode_command(tools, manifest, "1", offset="0"))
    decoded = lines[1]
    assert decoded["position"] == {
        "fen": fen,
        "fen_notation": "shredder-fen",
        "side_to_move": "white",
        "rule50": 17,
        "fullmove": 29,
        "castling_rights": {"wire": 2, "fen": "B"},
        "castling_rook_origins": {
            "white_kingside": None,
            "white_queenside": "b1",
            "black_kingside": None,
            "black_queenside": None,
        },
        "en_passant": None,
    }
    assert decoded["ply"] == "4294967295"
    assert decoded["flags"] == 1 and decoded["atomic960"] is True
    assert decoded["move"] == {
        "wire": str(0x3042),
        "from": "c1",
        "to": "b1",
        "type": "castling",
        "promotion": "none",
    }
    assert lines[-1]["validation"]["atomic960_records"] == "1"


def test_decode_stm_score_clock_and_result_boundaries(tools: Path, root: Path) -> None:
    black = encode_record(
        "7k/8/8/8/8/8/4P3/K7 b - - 32767 100000",
        move_from="h8",
        move_to="h7",
        score=2147483647,
        ply=0,
        result=1,
    )
    white = encode_record(
        "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1",
        move_from="e2",
        move_to="e4",
        score=-2147483647,
        result=0,
    )
    manifest, _ = manifest_for_shards(root, ((black, white),))
    lines = decode_jsonl(decode_command(tools, manifest, "2"))
    first, second = lines[1:3]
    assert first["position"]["side_to_move"] == "black"
    assert first["position"]["rule50"] == 32767
    assert first["position"]["fullmove"] == 100000
    assert first["score_stm"] == 2147483647
    assert first["ply"] == "0" and first["result_stm"] == 1
    assert second["score_stm"] == -2147483647 and second["result_stm"] == 0
    assert lines[-1] == expected_validation_footer(
        offset="0", limit=2, shards=1, records=2, losses=0, wins=1, draws=1
    )


def test_decode_limit_maximum(tools: Path, root: Path) -> None:
    normal = encode_record(
        "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1",
        move_from="e2",
        move_to="e4",
    )
    manifest, _ = manifest_for_shards(root, ((normal,) * 4096,))
    lines = decode_jsonl(decode_command(tools, manifest, "4096"))
    assert len(lines) == 4098
    assert lines[1]["global_index"] == "0"
    assert lines[-2]["global_index"] == "4095"
    assert lines[-1] == expected_validation_footer(
        offset="0", limit=4096, shards=1, records=4096, losses=4096
    )


def test_decode_contract_bounds_raw_and_no_output_files(
    tools: Path, manifest: Path, shards: list[Path]
) -> None:
    base = ("decode", "--format", "atomic-bin-v2", "--manifest", str(manifest))
    cases = (
        (base, "missing_limit"),
        (base + ("--limit", "0"), "limit_out_of_range"),
        (base + ("--limit", "4097"), "limit_out_of_range"),
        (base + ("--limit", "one"), "invalid_limit"),
        (base + ("--offset", "-1", "--limit", "1"), "invalid_offset"),
        (
            base
            + ("--offset", "18446744073709551616", "--limit", "1"),
            "invalid_offset",
        ),
        (base + ("--offset", "1", "--limit", "1"), "range_out_of_bounds"),
        (
            base
            + ("--offset", "18446744073709551615", "--limit", "1"),
            "range_out_of_bounds",
        ),
        (base + ("--limit", "1", "--output", "decoded.jsonl"), "unknown_argument"),
        (base + ("--limit", "1", "--overwrite"), "unknown_argument"),
        (base + ("--limit", "1", "--limit", "1"), "duplicate_argument"),
        (base + ("--offset", "0", "--offset", "0", "--limit", "1"), "duplicate_argument"),
    )
    for arguments, code in cases:
        expect_error(
            run(tools, *arguments),
            2,
            operation="decode",
            format_name="atomic-bin-v2",
            code=code,
        )

    raw = expect_error(
        decode_command(tools, shards[0], "1"),
        3,
        operation="decode",
        format_name="atomic-bin-v2",
        code="invalid_manifest",
    )
    assert ".atbin.manifest.json sidecar" in raw["message"]

    before = sorted(path.relative_to(manifest.parent) for path in manifest.parent.rglob("*"))
    decode_jsonl(decode_command(tools, manifest, "1"))
    after = sorted(path.relative_to(manifest.parent) for path in manifest.parent.rglob("*"))
    assert after == before
    assert not (manifest.parent / "decoded.jsonl").exists()


def test_decode_required_argument_contract(tools: Path, manifest: Path) -> None:
    cases = (
        (
            ("decode", "--manifest", str(manifest), "--limit", "1"),
            None,
            "missing_format",
        ),
        (
            (
                "decode",
                "--format",
                "legacy-atomic-v1",
                "--manifest",
                str(manifest),
                "--limit",
                "1",
            ),
            "legacy-atomic-v1",
            "unsupported_format",
        ),
        (
            ("decode", "--format", "atomic-bin-v2", "--limit", "1"),
            "atomic-bin-v2",
            "missing_manifest",
        ),
        (
            (
                "decode",
                "--format",
                "atomic-bin-v2",
                "--manifest",
                "",
                "--limit",
                "1",
            ),
            "atomic-bin-v2",
            "empty_manifest",
        ),
        (("decode", "--format"), None, "missing_value"),
        (("decode", "--manifest"), None, "missing_value"),
        (
            ("decode", "--format", "atomic-bin-v2", "--manifest"),
            "atomic-bin-v2",
            "missing_value",
        ),
        (
            (
                "decode",
                "--format",
                "atomic-bin-v2",
                "--manifest",
                str(manifest),
                "--offset",
            ),
            "atomic-bin-v2",
            "missing_value",
        ),
        (
            (
                "decode",
                "--format",
                "atomic-bin-v2",
                "--manifest",
                str(manifest),
                "--limit",
            ),
            "atomic-bin-v2",
            "missing_value",
        ),
    )
    for arguments, format_name, code in cases:
        expect_error(
            run(tools, *arguments),
            2,
            operation="decode",
            format_name=format_name,
            code=code,
        )


def test_decode_corruption_after_slice_has_empty_stdout(tools: Path, root: Path) -> None:
    normal = encode_record(
        "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1",
        move_from="e2",
        move_to="e4",
    )
    corrupt = bytearray(normal)
    corrupt[62] = 1
    manifest, _ = manifest_for(root, (normal, bytes(corrupt)))
    error = expect_error(
        decode_command(tools, manifest, "1", offset="0"),
        3,
        operation="decode",
        format_name="atomic-bin-v2",
        code="invalid_record",
    )
    assert "shard=1 local=0 global=1" in error["message"]
    assert "reserved bytes are nonzero" in error["message"]


def test_decode_checksum_corruption_after_slice_has_empty_stdout(
    tools: Path, root: Path
) -> None:
    manifest, shards = manifest_for(root, (GOLDEN_RECORD, GOLDEN_RECORD))
    payload = bytearray(shards[1].read_bytes())
    payload[-1] ^= 1
    shards[1].write_bytes(payload)
    error = expect_error(
        decode_command(tools, manifest, "1", offset="0"),
        3,
        operation="decode",
        format_name="atomic-bin-v2",
        code="schema_mismatch",
    )
    assert "shard=1" in error["message"]
    assert "shard SHA-256 differs from manifest" in error["message"]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tools", required=True, type=Path)
    arguments = parser.parse_args()
    tools = arguments.tools.resolve()
    if not tools.is_file():
        parser.error(f"data-tools executable does not exist: {tools}")

    test_capabilities(tools)
    with tempfile.TemporaryDirectory(prefix="atomic-data-tools-") as temporary:
        root = Path(temporary)
        manifest, shards = test_valid_multishard(tools, root / "valid-é")
        test_valid_atomic960_stats(tools, root / "atomic960")
        test_manifest_only_and_contract_errors(tools, manifest, shards)
        test_streamed_indexed_corruption(tools, root / "semantic-corruption")
        test_authenticated_corruption(tools, root / "authenticated-corruption")
        decode_manifest, decode_shards = test_decode_golden_byte_exact(
            tools, root / "decode-golden-é"
        )
        test_decode_all_move_types_and_second_shard(tools, root / "decode-moves")
        test_decode_atomic960_and_uint32_max_ply(tools, root / "decode-960")
        test_decode_stm_score_clock_and_result_boundaries(
            tools, root / "decode-boundaries"
        )
        test_decode_limit_maximum(tools, root / "decode-limit-maximum")
        test_decode_contract_bounds_raw_and_no_output_files(
            tools, decode_manifest, decode_shards
        )
        test_decode_required_argument_contract(tools, decode_manifest)
        test_decode_corruption_after_slice_has_empty_stdout(
            tools, root / "decode-late-corruption"
        )
        test_decode_checksum_corruption_after_slice_has_empty_stdout(
            tools, root / "decode-late-checksum-corruption"
        )

    print("Atomic data-tools CLI contract tests passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
