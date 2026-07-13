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
GOLDEN_RECORD = bytes.fromhex(
    "2453364211111111"
    "00000000000000000000000000000000"
    "777777778ab99ca8"
    "000f07003f38ff00"
    "0000010000000000"
    "85ffffff0c0700002a000000ff000000"
)
ATOMIC960_RECORD = GOLDEN_RECORD[:61] + b"\x01" + GOLDEN_RECORD[62:]


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


def shard_bytes(record: bytes) -> bytes:
    assert len(record) == 64
    header = struct.pack(
        "<8sHHIII32sQ32s",
        b"ATBINV2\0",
        2,
        96,
        0x01020304,
        64,
        0,
        bytes.fromhex(DATA_SCHEMA),
        1,
        bytes(32),
    )
    assert len(header) == 96
    return header + record


def manifest_for(
    root: Path, records: tuple[bytes, ...], *, atomic960: bool = False
) -> tuple[Path, list[Path]]:
    assert records
    root.mkdir(parents=True)
    shards: list[Path] = []
    shard_metadata: list[dict[str, Any]] = []
    for index, record in enumerate(records):
        filename = "dataset.atbin" if index == 0 else f"dataset-{index:06d}.atbin"
        path = root / filename
        payload = shard_bytes(record)
        path.write_bytes(payload)
        shards.append(path)
        shard_metadata.append(
            {
                "index": index,
                "file": filename,
                "records": "1",
                "bytes": str(len(payload)),
                "sha256": hashlib.sha256(payload).hexdigest(),
            }
        )

    count = len(records)
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
                "records_per_shard": "1",
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
        "statistics": {"records": str(count), "draws": "0"},
        "shards": shard_metadata,
    }
    manifest_path = root / "dataset.atbin.manifest.json"
    manifest_path.write_bytes(canonical_json(manifest).encode("utf-8"))
    return manifest_path, shards


def test_capabilities(tools: Path) -> None:
    expect_success(
        run(tools, "capabilities"),
        {
            "type": "atomic-data-tools-capabilities",
            "contract_version": 1,
            "formats": {
                "atomic-bin-v2": {
                    "data_schema_sha256": DATA_SCHEMA,
                    "manifest_schema_sha256": MANIFEST_SCHEMA,
                    "entrypoint": "manifest",
                    "read": True,
                    "write": False,
                    "operations": ["validate"],
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

    print("Atomic data-tools CLI contract tests passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
