#!/usr/bin/env python3
"""Integration gate for Atomic-Stockfish's isolated Atomic data generator."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
from typing import Sequence

from atomic_bin_v2_manifest_schema import load_atomic_bin_v2_manifest


SCHEMA_SHA256 = "acca0f551f1c012c31a6c727dedccaebb7b5ebbc46810edb87e31bb208d5abe1"
CAPABILITY_JSON = (
    '{"schema_sha256":"' + SCHEMA_SHA256 + '",'
    '"formats":{"legacy-atomic-v1":{"read":false,"write":true,"record_size":72}}}'
)
V2_SCHEMA_FILE = Path(__file__).resolve().parents[1] / "schemas" / "atomic-bin-v2.json"
FROZEN_V2_SCHEMA_SHA256 = "0352b036f2a140c609e3eb9c9d635dc553e8d77253d8faa92437390f5cf93cb6"
V2_SCHEMA_SHA256 = hashlib.sha256(V2_SCHEMA_FILE.read_bytes()).hexdigest()
if V2_SCHEMA_SHA256 != FROZEN_V2_SCHEMA_SHA256:
    raise AssertionError(
        "Atomic BIN V2 schema hash drift: "
        f"expected {FROZEN_V2_SCHEMA_SHA256}, got {V2_SCHEMA_SHA256}"
    )
V2_MANIFEST_SCHEMA_FILE = (
    Path(__file__).resolve().parents[1] / "schemas" / "atomic-bin-v2-manifest.json"
)
FROZEN_V2_MANIFEST_SCHEMA_SHA256 = (
    "83d63922df3ac4a0c81a21ec9d9fd9e180efe50f26efee62fe01710e09da5b42"
)
V2_MANIFEST_SCHEMA_SHA256 = hashlib.sha256(
    V2_MANIFEST_SCHEMA_FILE.read_bytes()
).hexdigest()
if V2_MANIFEST_SCHEMA_SHA256 != FROZEN_V2_MANIFEST_SCHEMA_SHA256:
    raise AssertionError(
        "Atomic BIN V2 manifest schema hash drift: "
        f"expected {FROZEN_V2_MANIFEST_SCHEMA_SHA256}, "
        f"got {V2_MANIFEST_SCHEMA_SHA256}"
    )
PLURAL_CAPABILITY_JSON = (
    '{"capability_version":2,"formats":{'
    '"legacy-atomic-v1":{"schema_sha256":"' + SCHEMA_SHA256 + '",'
    '"read":false,"write":true,"header_size":0,"record_size":72},'
    '"atomic-bin-v2":{"schema_sha256":"' + V2_SCHEMA_SHA256 + '",'
    '"read":false,"write":true,"header_size":96,"record_size":64}}}'
)
RECORD_SIZE = 72
V2_HEADER_SIZE = 96
V2_RECORD_SIZE = 64
REPLAY_SEED = "tools-wire-test"
RESOLVED_SEED = 4843478989694531390
SYNTHETIC_NET_SHA256 = "9CF054CA00B82AB53A34473DE52D1104AEDDAA19B2E7B24091B5E613AF485985"
SYNTHETIC_REPLAY_DATA_SHA256 = (
    "762555D8C054B8CED4FE1A18397711F2E6E10EB55397EA242DC9479BBC1F339A"
)
SYNTHETIC_APERY_DATA_SHA256 = (
    "CF2B0F7BF071B0AFB8CDA892F07A71434395830101073AD1B4E5BE04476D91F0"
)
SYNTHETIC_RANDOM_MULTIPV_DATA_SHA256 = (
    "2EDCD6829E95D055A87A506BB97F44654B4AD8632E84DD41D1438D657F809A3D"
)
SYNTHETIC_EXTREME_MULTIPV_DATA_SHA256 = (
    "2EDCD6829E95D055A87A506BB97F44654B4AD8632E84DD41D1438D657F809A3D"
)
SYNTHETIC_MINUS_ONE_DATA_SHA256 = (
    "2EDCD6829E95D055A87A506BB97F44654B4AD8632E84DD41D1438D657F809A3D"
)
SYNTHETIC_MULTI_BOOK_DATA_SHA256 = (
    "B77E197E730052BE8BAE7AB9E24EEB211523F95223B22B284F66BD1DC9C750D1"
)
TIMEOUT_SECONDS = 120.0
REPO_ROOT = Path(__file__).resolve().parents[1]
UPPER_SHA256_RE = re.compile(r"^[0-9A-F]{64}$")
INT_MAX = 2**31 - 1
MASK64 = 2**64 - 1

START_FEN = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"
CASTLING_FEN = "r3k2r/8/8/8/8/8/8/R3K2R w KQkq - 0 1"

# Exact 64-byte historical PackedSfen prefixes from the Fairy oracle fixture
# in tests/legacy_atomic_v1.cpp. They let the integration test prove which
# opening started each game without depending on tools or trainer decoders.
PACKED_SFEN_BY_FEN = {
    START_FEN: bytes.fromhex(
        "08BC732CD3723CC3300CC3300C010000008220088220088EA1488AE100000000"
        "0000008007040000000000000000000000000000000000000000000000000000"
    ),
    CASTLING_FEN: bytes.fromhex(
        "08BC139C000000000000073800000000000000E0010100000000000000000000"
        "0000000000000000000000000000000000000000000000000000000000000000"
    ),
}


def require_file(path: Path, label: str) -> Path:
    resolved = path.expanduser().resolve()
    if not resolved.is_file():
        raise AssertionError(f"{label} does not exist: {resolved}")
    return resolved


def run_engine(
    binary: Path,
    commands: Sequence[str],
    *,
    expect_success: bool,
    timeout: float = TIMEOUT_SECONDS,
) -> str:
    result = subprocess.run(
        [str(binary)],
        input="\n".join((*commands, "")),
        text=True,
        encoding="utf-8",
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout,
        check=False,
    )
    output = result.stdout + result.stderr
    if expect_success and result.returncode != 0:
        raise AssertionError(
            f"{binary.name} failed with exit code {result.returncode}:\n{output}"
        )
    if not expect_success and result.returncode == 0:
        raise AssertionError(
            f"{binary.name} unexpectedly accepted an invalid command:\n{output}"
        )
    return output


def setup_commands(net: Path, *, threads: int = 1, use_nnue: str = "pure") -> list[str]:
    return [
        "uci",
        f"setoption name EvalFile value {net}",
        f"setoption name Use NNUE value {use_nnue}",
        f"setoption name Threads value {threads}",
        "setoption name Hash value 16",
        "isready",
    ]


def generation_command(output: Path, records: int = 2, **overrides: object) -> str:
    values: dict[str, object] = {
        "depth": 1,
        "count": records,
        "write_min_ply": 0,
        "write_max_ply": 2,
        "random_move_count": 0,
        "keep_draws": 1,
        "eval_limit": 32000,
        "filter_captures": "false",
        "filter_checks": "false",
        "filter_promotions": "false",
        "output_file_name": output,
        "data_format": "bin",
        "seed": REPLAY_SEED,
    }
    values.update(overrides)
    fields = " ".join(f"{name} {value}" for name, value in values.items())
    return f"generate_training_data {fields}"


def assert_dataset(path: Path, records: int) -> bytes:
    if not path.is_file():
        raise AssertionError(f"generator did not create {path}")
    data = path.read_bytes()
    expected = records * RECORD_SIZE
    if len(data) != expected:
        raise AssertionError(
            f"dataset framing mismatch: expected {expected} bytes, got {len(data)}"
        )
    if any(data[offset + 71] != 0 for offset in range(0, len(data), RECORD_SIZE)):
        raise AssertionError("Legacy Atomic V1 padding byte is not deterministically zero")
    return data


def assert_atomic_bin_v2_dataset(
    path: Path, records: int, *, atomic960: bool
) -> bytes:
    if path.suffix != ".atbin":
        raise AssertionError(f"Atomic BIN V2 output does not use .atbin: {path}")
    if not path.is_file():
        raise AssertionError(f"generator did not create {path}")

    data = path.read_bytes()
    expected_size = V2_HEADER_SIZE + records * V2_RECORD_SIZE
    if len(data) != expected_size:
        raise AssertionError(
            "Atomic BIN V2 framing mismatch: "
            f"expected {expected_size} bytes, got {len(data)}"
        )

    if data[:8] != b"ATBINV2\0":
        raise AssertionError("Atomic BIN V2 magic is invalid")
    header_fields = (
        ("version", 8, 10, 2),
        ("header size", 10, 12, V2_HEADER_SIZE),
        ("endian marker", 12, 16, 0x01020304),
        ("record size", 16, 20, V2_RECORD_SIZE),
        ("header flags", 20, 24, 0),
        ("record count", 56, 64, records),
    )
    for label, start, end, expected in header_fields:
        actual = int.from_bytes(data[start:end], "little")
        if actual != expected:
            raise AssertionError(
                f"Atomic BIN V2 {label} mismatch: expected {expected}, got {actual}"
            )
    if data[24:56] != bytes.fromhex(V2_SCHEMA_SHA256):
        raise AssertionError("Atomic BIN V2 header contains the wrong schema SHA-256")
    if data[64:V2_HEADER_SIZE] != bytes(V2_HEADER_SIZE - 64):
        raise AssertionError("Atomic BIN V2 header reserved bytes are nonzero")

    expected_flags = 1 if atomic960 else 0
    for record_index in range(records):
        offset = V2_HEADER_SIZE + record_index * V2_RECORD_SIZE
        if data[offset + 61] != expected_flags:
            raise AssertionError(
                "Atomic BIN V2 record flags mismatch at record "
                f"{record_index}: expected {expected_flags}, got {data[offset + 61]}"
            )
        if data[offset + 62 : offset + 64] != b"\0\0":
            raise AssertionError(
                f"Atomic BIN V2 record {record_index} reserved bytes are nonzero"
            )
    return data


def current_repository_commit() -> str | None:
    pinned_commit = os.environ.get("GIT_SHA_FULL")
    if pinned_commit is not None:
        pinned_commit = pinned_commit.strip()
        if re.fullmatch(r"[0-9a-f]{40}", pinned_commit) is None:
            raise AssertionError(
                "GIT_SHA_FULL must be exactly 40 lower-case hexadecimal digits"
            )
        return pinned_commit

    try:
        result = subprocess.run(
            ["git", "-C", str(REPO_ROOT), "rev-parse", "HEAD"],
            text=True,
            encoding="utf-8",
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=10.0,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    commit = result.stdout.strip().lower()
    if result.returncode != 0 or re.fullmatch(r"[0-9a-f]{40}", commit) is None:
        return None
    try:
        status = subprocess.run(
            [
                "git",
                "-C",
                str(REPO_ROOT),
                "status",
                "--porcelain",
                "--untracked-files=normal",
            ],
            text=True,
            encoding="utf-8",
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=10.0,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if status.returncode != 0:
        return None
    if status.stdout:
        return "unknown"
    return commit


def assert_json_keys(value: object, expected: Sequence[str], label: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise AssertionError(f"Atomic BIN V2 manifest {label} is not an object")
    if list(value) != list(expected):
        raise AssertionError(
            f"Atomic BIN V2 manifest {label} key order mismatch: "
            f"expected {list(expected)}, got {list(value)}"
        )
    return value


def assert_manifest_has_no_absolute_paths_or_timestamps(value: object) -> None:
    timestamp_keys = {
        "created_at",
        "date",
        "datetime",
        "generated_at",
        "timestamp",
        "updated_at",
    }

    def visit(item: object) -> None:
        if isinstance(item, dict):
            for key, child in item.items():
                if key.lower() in timestamp_keys:
                    raise AssertionError(
                        f"Atomic BIN V2 manifest contains timestamp key {key!r}"
                    )
                visit(child)
        elif isinstance(item, list):
            for child in item:
                visit(child)
        elif isinstance(item, str):
            windows_absolute = re.match(r"^[A-Za-z]:[\\/]", item) is not None
            if Path(item).is_absolute() or windows_absolute or item.startswith("\\\\"):
                raise AssertionError(
                    f"Atomic BIN V2 manifest leaks absolute path {item!r}"
                )

    visit(value)


def assert_atomic_bin_v2_manifest(
    manifest_path: Path,
    shard_fixtures: Sequence[tuple[Path, bytes, int]],
    *,
    records_per_shard: int | None = None,
    atomic960: bool,
    net: Path,
    net_sha256: str,
    root: Path,
    book_path: Path | None = None,
) -> dict[str, object]:
    if not shard_fixtures:
        raise AssertionError("Atomic BIN V2 test manifest needs at least one shard fixture")
    first_shard_path = shard_fixtures[0][0]
    records = sum(shard_records for _, _, shard_records in shard_fixtures)
    if records_per_shard is None:
        records_per_shard = shard_fixtures[0][2]
    expected_path = Path(str(first_shard_path) + ".manifest.json")
    if manifest_path != expected_path:
        raise AssertionError(
            f"Atomic BIN V2 sidecar path mismatch: expected {expected_path}, got {manifest_path}"
        )
    if not manifest_path.is_file():
        raise AssertionError(f"generator did not create {manifest_path}")

    raw = manifest_path.read_bytes()
    if raw.startswith(b"\xef\xbb\xbf"):
        raise AssertionError("Atomic BIN V2 manifest contains a UTF-8 BOM")
    if not raw.endswith(b"\n") or raw.endswith(b"\n\n") or b"\r" in raw:
        raise AssertionError("Atomic BIN V2 manifest must end in exactly one LF")
    try:
        text = raw.decode("utf-8")
        parsed = json.loads(text)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise AssertionError(
            f"Atomic BIN V2 manifest is not canonical UTF-8 JSON: {error}"
        ) from error
    try:
        canonical = json.dumps(
            parsed, ensure_ascii=False, allow_nan=False, separators=(",", ":")
        ).encode("utf-8") + b"\n"
    except (TypeError, ValueError) as error:
        raise AssertionError(
            f"Atomic BIN V2 manifest contains invalid JSON values: {error}"
        ) from error
    if raw != canonical:
        raise AssertionError("Atomic BIN V2 manifest is not canonical minified JSON")
    if load_atomic_bin_v2_manifest(manifest_path) != parsed:
        raise AssertionError("Atomic BIN V2 strict manifest validation changed the document")

    manifest = assert_json_keys(
        parsed,
        (
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
        ),
        "root",
    )
    assert_manifest_has_no_absolute_paths_or_timestamps(manifest)
    leaked_paths = (root, net, *(path for path, _, _ in shard_fixtures))
    if book_path is not None:
        leaked_paths += (book_path,)
    for leaked_path in leaked_paths:
        encoded = str(leaked_path).encode("utf-8")
        normalized = str(leaked_path).replace("\\", "/").encode("utf-8")
        if encoded in raw or normalized in raw:
            raise AssertionError(
                f"Atomic BIN V2 manifest leaks absolute path {leaked_path}"
            )

    if manifest["manifest_version"] != 1:
        raise AssertionError("Atomic BIN V2 manifest version is not 1")
    if manifest["manifest_schema_sha256"] != V2_MANIFEST_SCHEMA_SHA256:
        raise AssertionError("Atomic BIN V2 manifest contains the wrong manifest schema hash")
    if manifest["data_schema_sha256"] != V2_SCHEMA_SHA256:
        raise AssertionError("Atomic BIN V2 manifest contains the wrong data schema hash")
    if manifest["format"] != "atomic-bin-v2":
        raise AssertionError("Atomic BIN V2 manifest format is invalid")

    engine = assert_json_keys(manifest["engine"], ("commit", "version"), "engine")
    commit = engine["commit"]
    if not isinstance(commit, str) or (
        commit != "unknown" and re.fullmatch(r"[0-9a-f]{40}", commit) is None
    ):
        raise AssertionError(f"Atomic BIN V2 manifest commit is invalid: {commit!r}")
    expected_commit = current_repository_commit()
    if expected_commit is not None and commit != expected_commit:
        raise AssertionError(
            "Atomic BIN V2 manifest commit does not match this checkout: "
            f"expected {expected_commit}, got {commit}"
        )
    if not isinstance(engine["version"], str) or not engine["version"]:
        raise AssertionError("Atomic BIN V2 manifest engine version is empty")

    network = assert_json_keys(manifest["network"], ("file", "sha256"), "network")
    if network != {"file": net.name, "sha256": net_sha256.lower()}:
        raise AssertionError(
            f"Atomic BIN V2 manifest network metadata mismatch: {network!r}"
        )
    book = assert_json_keys(manifest["book"], ("kind", "file", "sha256"), "book")
    expected_book = (
        {"kind": "builtin-startpos", "file": None, "sha256": None}
        if book_path is None
        else {
            "kind": "file",
            "file": book_path.name,
            "sha256": hashlib.sha256(book_path.read_bytes()).hexdigest(),
        }
    )
    if book != expected_book:
        raise AssertionError(f"Atomic BIN V2 manifest book metadata mismatch: {book!r}")

    generation = assert_json_keys(
        manifest["generation"],
        ("resolved_seed", "atomic960", "threads", "hash_mb", "use_nnue", "options"),
        "generation",
    )
    expected_generation = {
        "resolved_seed": str(RESOLVED_SEED),
        "atomic960": atomic960,
        "threads": 1,
        "hash_mb": "16",
        "use_nnue": "pure",
    }
    for key, expected in expected_generation.items():
        if generation[key] != expected:
            raise AssertionError(
                f"Atomic BIN V2 manifest generation.{key} mismatch: "
                f"expected {expected!r}, got {generation[key]!r}"
            )
    options = assert_json_keys(
        generation["options"],
        (
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
        ),
        "generation.options",
    )
    expected_options = {
        "search_depth_min": 1,
        "search_depth_max": 1,
        "nodes": "0",
        "requested_records": str(records),
        "records_per_shard": str(records_per_shard),
        "eval_limit": 32000,
        "eval_diff_limit": 64000,
        "random_move_min_ply": 1,
        "random_move_max_ply": 24,
        "random_move_count": 0,
        "random_move_like_apery": 0,
        "random_multi_pv": 5,
        "random_multi_pv_diff": 100,
        "random_multi_pv_depth": 1,
        "write_min_ply": 0,
        "write_max_ply": 2,
        "keep_draws": "1",
        "adjudicate_draws_by_score": True,
        "adjudicate_insufficient": True,
        "filter_captures": False,
        "filter_checks": False,
        "filter_promotions": False,
        "random_file_name": False,
        "set_recommended_uci_options_seen": False,
    }
    if options != expected_options:
        raise AssertionError(
            "Atomic BIN V2 manifest effective options mismatch: "
            f"expected {expected_options!r}, got {options!r}"
        )

    statistics = assert_json_keys(manifest["statistics"], ("records", "draws"), "statistics")
    if statistics["records"] != str(records):
        raise AssertionError("Atomic BIN V2 manifest record statistic is incorrect")
    draws = statistics["draws"]
    if not isinstance(draws, str) or re.fullmatch(r"0|[1-9][0-9]*", draws) is None:
        raise AssertionError("Atomic BIN V2 manifest draw statistic is not canonical")
    if int(draws) > records:
        raise AssertionError("Atomic BIN V2 manifest draw statistic exceeds its record count")

    shards = manifest["shards"]
    if not isinstance(shards, list) or len(shards) != len(shard_fixtures):
        raise AssertionError(
            "Atomic BIN V2 manifest shard count mismatch: "
            f"expected {len(shard_fixtures)}, got {len(shards) if isinstance(shards, list) else 'non-list'}"
        )
    for index, (shard_path, shard_data, shard_records) in enumerate(shard_fixtures):
        shard = assert_json_keys(
            shards[index],
            ("index", "file", "records", "bytes", "sha256"),
            f"shards[{index}]",
        )
        expected_shard = {
            "index": index,
            "file": shard_path.name,
            "records": str(shard_records),
            "bytes": str(len(shard_data)),
            "sha256": hashlib.sha256(shard_data).hexdigest(),
        }
        if shard != expected_shard:
            raise AssertionError(
                "Atomic BIN V2 manifest shard metadata mismatch: "
                f"expected {expected_shard!r}, got {shard!r}"
            )
    return manifest


def packed_positions(data: bytes) -> tuple[bytes, ...]:
    if len(data) % RECORD_SIZE != 0:
        raise AssertionError("cannot split a dataset with partial records")
    return tuple(
        data[offset : offset + 64] for offset in range(0, len(data), RECORD_SIZE)
    )


class HistoricalPrng:
    """Minimal independent model of the frozen xorshift64* command RNG."""

    def __init__(self, seed: int):
        if not 0 < seed <= MASK64:
            raise AssertionError(f"historical PRNG seed is out of range: {seed}")
        self.state = seed

    def rand(self, bound: int) -> int:
        if bound <= 0:
            raise AssertionError(f"historical PRNG bound is invalid: {bound}")
        self.state ^= self.state >> 12
        self.state &= MASK64
        self.state ^= (self.state << 25) & MASK64
        self.state &= MASK64
        self.state ^= self.state >> 27
        self.state &= MASK64
        return ((self.state * 2685821657736338717) & MASK64) % bound


def historical_book_order(fens: Sequence[str], seed: int) -> tuple[str, ...]:
    order = list(fens)
    rng = HistoricalPrng(seed)
    for index in range(len(order)):
        selected = index + rng.rand(len(order) - index)
        order[index], order[selected] = order[selected], order[index]
    return tuple(order)


def apery_reply_seed() -> int:
    """Choose a seed that exposes insert-versus-overwrite reply semantics."""
    # Calls before the first reply coin flip: singleton-book shuffle,
    # resignation, two flag selections, depth selection, Apery gate and king
    # selection. Then model the equivalent four calls at ply 1. Bounds do not
    # affect state advancement, so a unit bound models unknown king count. The
    # first move must insert a response while the second must not; otherwise a
    # second insertion could mask whether the original ply-1 flag was shifted
    # to ply 2.
    for seed in range(1, 10000):
        rng = HistoricalPrng(seed)
        for bound in (1, 10, 2, 1, 2, 1, 1):
            rng.rand(bound)
        first_reply = rng.rand(2)
        for bound in (2, 1, 1):
            rng.rand(bound)
        second_reply = rng.rand(2)
        if first_reply == 0 and second_reply != 0:
            return seed
    raise AssertionError("could not select a deterministic insert-versus-overwrite Apery seed")


def verify_synthetic_fixture_sha(
    *,
    net_sha256: str,
    constant_name: str,
    expected: str,
    actual: str,
    unresolved: list[str],
) -> None:
    if net_sha256 != SYNTHETIC_NET_SHA256:
        return
    if expected.startswith("MEASURE_"):
        unresolved.append(f'{constant_name} = "{actual}"')
        return
    if UPPER_SHA256_RE.fullmatch(expected) is None:
        raise AssertionError(
            f"{constant_name} must be an uppercase 64-digit SHA-256 or MEASURE_ marker"
        )
    if actual != expected:
        raise AssertionError(
            f"{constant_name} mismatch: expected {expected}, got {actual}"
        )


def assert_failed_without_output(
    generator: Path,
    commands: Sequence[str],
    output_path: Path,
    marker: str,
) -> None:
    output = run_engine(generator, (*commands, "quit"), expect_success=False)
    if marker not in output:
        raise AssertionError(f"failure was not explicit ({marker!r}):\n{output}")
    if output_path.exists():
        raise AssertionError(f"failed generator command created {output_path}")


def controlled_temp_parent() -> Path:
    """Return a writable no-whitespace parent for tokenized generator paths."""
    configured = Path(tempfile.gettempdir()).expanduser().resolve()
    if not any(character.isspace() for character in str(configured)):
        return configured

    # output_file_name is a historical whitespace-delimited field. If TEMP has
    # spaces, use a stable directory at the same filesystem root so the test is
    # independent of the caller's TEMP spelling and drive.
    anchor = Path(configured.anchor)
    fallback = anchor / "atomic-sf-test-tmp"
    try:
        fallback.mkdir(parents=True, exist_ok=True)
    except OSError as error:
        raise AssertionError(
            "TEMP contains whitespace and a controlled no-space generator test "
            f"directory could not be created at {fallback}: {error}"
        ) from error
    if any(character.isspace() for character in str(fallback)):
        raise AssertionError(f"controlled generator test path contains whitespace: {fallback}")
    return fallback


def validate_with_tools(tools_engine: Path, dataset: Path, records: int) -> None:
    output = run_engine(
        tools_engine,
        (
            "uci",
            "setoption name UCI_Variant value atomic",
            "setoption name Threads value 1",
            "setoption name Use NNUE value false",
            f"validate_training_data {dataset}",
            "quit",
        ),
        expect_success=True,
    )
    marker = (
        f"Validation passed: {records} canonical legacy v1 records "
        "(72 bytes each)."
    )
    if output.splitlines().count(marker) != 1:
        raise AssertionError(
            f"tools did not emit the exact positive validation marker {marker!r}:\n{output}"
        )


def validate_with_trainer(trainer_root: Path, dataset: Path, records: int) -> None:
    tests_path = str(REPO_ROOT / "tests")
    sys.path.insert(0, tests_path)
    try:
        import legacy_pipeline_e2e as pipeline

        nnue_dataset, *_ = pipeline.import_trainer_modules(trainer_root)
        batch = pipeline.decode_batch(nnue_dataset, dataset, records, RESOLVED_SEED)
    finally:
        if sys.path and sys.path[0] == tests_path:
            sys.path.pop(0)
    if not batch:
        raise AssertionError("trainer native loader returned an empty batch")


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--generator", type=Path, required=True)
    parser.add_argument("--net", type=Path, required=True)
    parser.add_argument("--normal-engine", type=Path)
    parser.add_argument("--tools-engine", type=Path)
    parser.add_argument("--trainer-root", type=Path)
    parser.add_argument("--expected-net-sha256")
    parser.add_argument(
        "--smoke-only",
        action="store_true",
        help=(
            "run every valid deterministic generator fixture and normal-binary "
            "isolation; skip invalid-command and cross-repository checks"
        ),
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    generator = require_file(args.generator, "data-generator binary")
    net = require_file(args.net, "Legacy Atomic V1 network")
    normal_engine = (
        require_file(args.normal_engine, "normal engine") if args.normal_engine else None
    )
    tools_engine = (
        require_file(args.tools_engine, "tools engine") if args.tools_engine else None
    )
    trainer_root = args.trainer_root.expanduser().resolve() if args.trainer_root else None
    if trainer_root is not None and not trainer_root.is_dir():
        raise AssertionError(f"trainer root does not exist: {trainer_root}")

    net_sha256 = hashlib.sha256(net.read_bytes()).hexdigest().upper()
    if args.expected_net_sha256 and net_sha256 != args.expected_net_sha256.upper():
        raise AssertionError(
            f"network SHA-256 mismatch: expected {args.expected_net_sha256}, got {net_sha256}"
        )

    unresolved_hashes: list[str] = []
    with tempfile.TemporaryDirectory(
        prefix="atomic-data-generator-", dir=controlled_temp_parent()
    ) as raw_root:
        root = Path(raw_root).resolve()
        if any(character.isspace() for character in str(root)):
            raise AssertionError(f"controlled generator test path contains whitespace: {root}")

        first = root / "first.bin"
        second = root / "second.bin"
        output = run_engine(
            generator,
            (
                *setup_commands(net),
                "atomic_data_schema",
                "atomic_data_schemas",
                generation_command(first),
                generation_command(second),
                "quit",
            ),
            expect_success=True,
        )
        output_lines = output.splitlines()
        if output_lines.count(CAPABILITY_JSON) != 1:
            raise AssertionError(f"schema capability handshake mismatch:\n{output}")
        if output_lines.count(PLURAL_CAPABILITY_JSON) != 1:
            raise AssertionError(f"plural schema capability handshake mismatch:\n{output}")
        if output_lines.count("INFO: generate_training_data finished.") != 2:
            raise AssertionError(f"generator completion markers are incomplete:\n{output}")
        if output_lines.count(f"PRNG::initial_seed = {RESOLVED_SEED}") != 2:
            raise AssertionError(f"resolved seed markers are incomplete:\n{output}")
        if output_lines.count("INFO: threads = 1") != 2:
            raise AssertionError(f"Threads=1 generator marker is incomplete:\n{output}")

        first_data = assert_dataset(first, 2)
        if assert_dataset(second, 2) != first_data:
            raise AssertionError(
                "same-process fixed-seed Threads=1 generation is not byte-reproducible"
            )

        fresh = root / "fresh.bin"
        fresh_output = run_engine(
            generator,
            (*setup_commands(net), generation_command(fresh), "quit"),
            expect_success=True,
        )
        fresh_lines = fresh_output.splitlines()
        if fresh_lines.count("INFO: generate_training_data finished.") != 1:
            raise AssertionError(f"fresh-process generator did not finish:\n{fresh_output}")
        if fresh_lines.count(f"PRNG::initial_seed = {RESOLVED_SEED}") != 1:
            raise AssertionError(f"fresh-process seed marker is incomplete:\n{fresh_output}")
        if fresh_lines.count("INFO: threads = 1") != 1:
            raise AssertionError(f"fresh-process thread marker is incomplete:\n{fresh_output}")
        if assert_dataset(fresh, 2) != first_data:
            raise AssertionError(
                "fresh-process fixed-seed Threads=1 generation is not byte-reproducible"
            )

        data_sha256 = hashlib.sha256(first_data).hexdigest().upper()
        verify_synthetic_fixture_sha(
            net_sha256=net_sha256,
            constant_name="SYNTHETIC_REPLAY_DATA_SHA256",
            expected=SYNTHETIC_REPLAY_DATA_SHA256,
            actual=data_sha256,
            unresolved=unresolved_hashes,
        )

        multi = root / "multi.bin"
        multi_output = run_engine(
            generator,
            (*setup_commands(net, threads=2), generation_command(multi), "quit"),
            expect_success=True,
        )
        multi_lines = multi_output.splitlines()
        if multi_lines.count("INFO: generate_training_data finished.") != 1:
            raise AssertionError(f"multi-thread generator did not finish once:\n{multi_output}")
        if multi_lines.count(f"PRNG::initial_seed = {RESOLVED_SEED}") != 1:
            raise AssertionError(f"multi-thread seed marker is incomplete:\n{multi_output}")
        if multi_lines.count("INFO: threads = 2") != 1:
            raise AssertionError(f"generator did not run with exactly two threads:\n{multi_output}")
        assert_dataset(multi, 2)

        # Force the true Apery branch (random_multi_pv=0) and a scheduled
        # opponent reply. Two original flags at plies 0/1 make vector insertion
        # shift the latter to ply 2; four records make that extra move observable
        # in the position at ply 3. Overwriting flags[ply + 1] cannot pass.
        apery_book = root / "apery-book.epd"
        apery_book.write_bytes((CASTLING_FEN + "\n").encode("utf-8"))
        apery_seed = apery_reply_seed()
        apery_first = root / "apery-first.bin"
        apery_second = root / "apery-second.bin"
        apery_overrides = {
            "min_depth": 1,
            "max_depth": 2,
            "write_max_ply": 4,
            "random_move_min_ply": 1,
            "random_move_max_ply": 2,
            "random_move_count": 2,
            "random_move_like_apery": 1,
            "random_multi_pv": 0,
            "book": apery_book,
            "seed": apery_seed,
        }
        apery_output = run_engine(
            generator,
            (
                *setup_commands(net),
                generation_command(apery_first, records=4, **apery_overrides),
                generation_command(apery_second, records=4, **apery_overrides),
                "quit",
            ),
            expect_success=True,
        )
        apery_lines = apery_output.splitlines()
        if apery_lines.count("INFO: generate_training_data finished.") != 2:
            raise AssertionError(f"Apery generator did not finish twice:\n{apery_output}")
        if apery_lines.count(f"PRNG::initial_seed = {apery_seed}") != 2:
            raise AssertionError(f"Apery seed marker is incomplete:\n{apery_output}")
        if apery_lines.count("INFO: threads = 1") != 2:
            raise AssertionError(f"Apery thread marker is incomplete:\n{apery_output}")
        apery_data = assert_dataset(apery_first, 4)
        if assert_dataset(apery_second, 4) != apery_data:
            raise AssertionError("same-process Apery generation is not byte-reproducible")
        if packed_positions(apery_data)[0] != PACKED_SFEN_BY_FEN[CASTLING_FEN]:
            raise AssertionError("Apery fixture did not start from its requested book FEN")
        apery_sha256 = hashlib.sha256(apery_data).hexdigest().upper()
        verify_synthetic_fixture_sha(
            net_sha256=net_sha256,
            constant_name="SYNTHETIC_APERY_DATA_SHA256",
            expected=SYNTHETIC_APERY_DATA_SHA256,
            actual=apery_sha256,
            unresolved=unresolved_hashes,
        )

        random_multipv_first = root / "random-multipv-first.bin"
        random_multipv_second = root / "random-multipv-second.bin"
        multipv_overrides = {
            "random_move_min_ply": 1,
            "random_move_max_ply": 1,
            "random_move_count": 1,
            "random_multi_pv": 2,
            "random_multi_pv_depth": 1,
        }
        random_multipv_output = run_engine(
            generator,
            (
                *setup_commands(net),
                generation_command(random_multipv_first, **multipv_overrides),
                generation_command(random_multipv_second, **multipv_overrides),
                "quit",
            ),
            expect_success=True,
        )
        random_multipv_lines = random_multipv_output.splitlines()
        if random_multipv_lines.count("INFO: generate_training_data finished.") != 2:
            raise AssertionError(
                f"random-MultiPV generator did not finish twice:\n{random_multipv_output}"
            )
        if random_multipv_lines.count(f"PRNG::initial_seed = {RESOLVED_SEED}") != 2:
            raise AssertionError(f"random-MultiPV seed markers are incomplete:\n{random_multipv_output}")
        if random_multipv_lines.count("INFO: threads = 1") != 2:
            raise AssertionError(f"random-MultiPV thread markers are incomplete:\n{random_multipv_output}")
        random_multipv_data = assert_dataset(random_multipv_first, 2)
        if assert_dataset(random_multipv_second, 2) != random_multipv_data:
            raise AssertionError(
                "same-process fixed-seed random-MultiPV generation is not byte-reproducible"
            )
        random_multipv_sha256 = hashlib.sha256(random_multipv_data).hexdigest().upper()
        verify_synthetic_fixture_sha(
            net_sha256=net_sha256,
            constant_name="SYNTHETIC_RANDOM_MULTIPV_DATA_SHA256",
            expected=SYNTHETIC_RANDOM_MULTIPV_DATA_SHA256,
            actual=random_multipv_sha256,
            unresolved=unresolved_hashes,
        )

        extreme_multipv_diff = root / "extreme-multipv-diff.bin"
        # Seed 1 selects the second PV. A signed-overflow regression in
        # value + INT_MAX truncates the candidate set to the first PV and
        # changes this frozen dataset to the basic replay fixture.
        extreme_multipv_output = run_engine(
            generator,
            (
                *setup_commands(net),
                generation_command(
                    extreme_multipv_diff,
                    **multipv_overrides,
                    random_multi_pv_diff=INT_MAX,
                    seed=1,
                ),
                "quit",
            ),
            expect_success=True,
        )
        extreme_lines = extreme_multipv_output.splitlines()
        if extreme_lines.count("INFO: generate_training_data finished.") != 1:
            raise AssertionError(
                "INT_MAX random-MultiPV diff did not finish exactly once without signed overflow:\n"
                f"{extreme_multipv_output}"
            )
        if extreme_lines.count("INFO: threads = 1") != 1:
            raise AssertionError(
                f"INT_MAX fixture thread marker is incomplete:\n{extreme_multipv_output}"
            )
        if extreme_lines.count("PRNG::initial_seed = 1") != 1:
            raise AssertionError(
                f"INT_MAX fixture seed marker is incomplete:\n{extreme_multipv_output}"
            )
        extreme_multipv_data = assert_dataset(extreme_multipv_diff, 2)
        verify_synthetic_fixture_sha(
            net_sha256=net_sha256,
            constant_name="SYNTHETIC_EXTREME_MULTIPV_DATA_SHA256",
            expected=SYNTHETIC_EXTREME_MULTIPV_DATA_SHA256,
            actual=hashlib.sha256(extreme_multipv_data).hexdigest().upper(),
            unresolved=unresolved_hashes,
        )

        # random_move_min_ply=-1 still constructs and shuffles the historical
        # flags vector before the always-random prefix. The frozen bytes catch
        # accidental removal of that otherwise non-obvious RNG consumption.
        minus_one = root / "minus-one.bin"
        minus_one_output = run_engine(
            generator,
            (
                *setup_commands(net),
                generation_command(
                    minus_one,
                    random_move_min_ply=-1,
                    random_move_max_ply=1,
                    random_move_count=1,
                    random_multi_pv=0,
                ),
                "quit",
            ),
            expect_success=True,
        )
        minus_one_lines = minus_one_output.splitlines()
        if minus_one_lines.count("INFO: generate_training_data finished.") != 1:
            raise AssertionError(f"random_move_min_ply=-1 fixture did not finish:\n{minus_one_output}")
        if minus_one_lines.count(f"PRNG::initial_seed = {RESOLVED_SEED}") != 1:
            raise AssertionError(
                f"random_move_min_ply=-1 seed marker is incomplete:\n{minus_one_output}"
            )
        if minus_one_lines.count("INFO: threads = 1") != 1:
            raise AssertionError(
                f"random_move_min_ply=-1 thread marker is incomplete:\n{minus_one_output}"
            )
        minus_one_data = assert_dataset(minus_one, 2)
        minus_one_sha256 = hashlib.sha256(minus_one_data).hexdigest().upper()
        verify_synthetic_fixture_sha(
            net_sha256=net_sha256,
            constant_name="SYNTHETIC_MINUS_ONE_DATA_SHA256",
            expected=SYNTHETIC_MINUS_ONE_DATA_SHA256,
            actual=minus_one_sha256,
            unresolved=unresolved_hashes,
        )

        # One record per game makes two records cross exactly one game boundary.
        # Variable depth and one random move make the inherited RNG state affect
        # the next game's bytes. Verify both openings against an independent
        # historical book shuffle and exact Fairy PackedSfen prefixes.
        book_fens = (START_FEN, CASTLING_FEN)
        multi_book = root / "multi-book.epd"
        multi_book.write_bytes(("\n".join(book_fens) + "\n").encode("utf-8"))
        multi_book_output_path = root / "multi-book.bin"
        multi_book_output = run_engine(
            generator,
            (
                *setup_commands(net),
                generation_command(
                    multi_book_output_path,
                    records=2,
                    min_depth=1,
                    max_depth=3,
                    write_max_ply=1,
                    random_move_min_ply=1,
                    random_move_max_ply=1,
                    random_move_count=1,
                    random_multi_pv=0,
                    book=multi_book,
                ),
                "quit",
            ),
            expect_success=True,
        )
        multi_book_lines = multi_book_output.splitlines()
        if multi_book_lines.count("INFO: generate_training_data finished.") != 1:
            raise AssertionError(f"multi-book generator did not finish:\n{multi_book_output}")
        if multi_book_lines.count(f"PRNG::initial_seed = {RESOLVED_SEED}") != 1:
            raise AssertionError(f"multi-book seed marker is incomplete:\n{multi_book_output}")
        if multi_book_lines.count("INFO: threads = 1") != 1:
            raise AssertionError(f"multi-book thread marker is incomplete:\n{multi_book_output}")
        multi_book_data = assert_dataset(multi_book_output_path, 2)
        opening_order = historical_book_order(book_fens, RESOLVED_SEED)
        positions = packed_positions(multi_book_data)
        for game_index, record_index in enumerate((0, 1)):
            expected_position = PACKED_SFEN_BY_FEN[opening_order[game_index]]
            if positions[record_index] != expected_position:
                raise AssertionError(
                    "multi-book fixture did not preserve historical shuffled round-robin "
                    f"order at game {game_index}"
                )
        multi_book_sha256 = hashlib.sha256(multi_book_data).hexdigest().upper()
        verify_synthetic_fixture_sha(
            net_sha256=net_sha256,
            constant_name="SYNTHETIC_MULTI_BOOK_DATA_SHA256",
            expected=SYNTHETIC_MULTI_BOOK_DATA_SHA256,
            actual=multi_book_sha256,
            unresolved=unresolved_hashes,
        )

        v2 = root / "atomic-v2.atbin"
        v2_manifest = Path(str(v2) + ".manifest.json")
        v2_output = run_engine(
            generator,
            (
                *setup_commands(net),
                generation_command(v2, data_format="atomic-bin-v2"),
                "quit",
            ),
            expect_success=True,
        )
        v2_lines = v2_output.splitlines()
        if v2_lines.count("INFO: generate_training_data finished.") != 1:
            raise AssertionError(f"Atomic BIN V2 generator did not finish once:\n{v2_output}")
        if v2_lines.count(f"INFO: schema_sha256 = {V2_SCHEMA_SHA256}") != 1:
            raise AssertionError(f"Atomic BIN V2 schema marker is incomplete:\n{v2_output}")
        if (
            v2_lines.count(
                f"INFO: manifest_schema_sha256 = {V2_MANIFEST_SCHEMA_SHA256}"
            )
            != 1
        ):
            raise AssertionError(
                f"Atomic BIN V2 manifest schema marker is incomplete:\n{v2_output}"
            )
        if v2_lines.count(f"INFO: manifest = {v2_manifest}") != 1:
            raise AssertionError(f"Atomic BIN V2 manifest path marker is incomplete:\n{v2_output}")
        v2_data = assert_atomic_bin_v2_dataset(v2, 2, atomic960=False)
        assert_atomic_bin_v2_manifest(
            v2_manifest,
            ((v2, v2_data, 2),),
            atomic960=False,
            net=net,
            net_sha256=net_sha256,
            root=root,
        )

        multi_v2_base = root / "multi-v2"
        multi_v2_first = root / "multi-v2.atbin"
        multi_v2_second = root / "multi-v2_1.atbin"
        multi_v2_manifest = Path(str(multi_v2_first) + ".manifest.json")
        multi_v2_output = run_engine(
            generator,
            (
                *setup_commands(net),
                generation_command(
                    multi_v2_base,
                    records=4,
                    data_format="atomic-bin-v2",
                    save_every=2,
                    book=multi_book,
                ),
                "quit",
            ),
            expect_success=True,
        )
        if multi_v2_output.splitlines().count("INFO: generate_training_data finished.") != 1:
            raise AssertionError(
                f"multi-shard Atomic BIN V2 generator did not finish once:\n{multi_v2_output}"
            )
        multi_v2_first_data = assert_atomic_bin_v2_dataset(
            multi_v2_first, 2, atomic960=False
        )
        multi_v2_second_data = assert_atomic_bin_v2_dataset(
            multi_v2_second, 2, atomic960=False
        )
        assert_atomic_bin_v2_manifest(
            multi_v2_manifest,
            (
                (multi_v2_first, multi_v2_first_data, 2),
                (multi_v2_second, multi_v2_second_data, 2),
            ),
            records_per_shard=2,
            atomic960=False,
            net=net,
            net_sha256=net_sha256,
            root=root,
            book_path=multi_book,
        )

        atomic960_book = root / "atomic960-book.epd"
        atomic960_book.write_bytes(b"7k/8/8/8/8/8/8/1RK5 w B - 0 1\n")
        atomic960_v2 = root / "atomic960-v2.atbin"
        atomic960_manifest = Path(str(atomic960_v2) + ".manifest.json")
        atomic960_output = run_engine(
            generator,
            (
                *setup_commands(net),
                "setoption name UCI_Chess960 value true",
                generation_command(
                    atomic960_v2,
                    data_format="atomic-bin-v2",
                    book=atomic960_book,
                ),
                "quit",
            ),
            expect_success=True,
        )
        atomic960_lines = atomic960_output.splitlines()
        if atomic960_lines.count("INFO: generate_training_data finished.") != 1:
            raise AssertionError(
                f"Atomic960 BIN V2 generator did not finish once:\n{atomic960_output}"
            )
        if atomic960_lines.count(f"INFO: schema_sha256 = {V2_SCHEMA_SHA256}") != 1:
            raise AssertionError(
                f"Atomic960 BIN V2 schema marker is incomplete:\n{atomic960_output}"
            )
        if (
            atomic960_lines.count(
                f"INFO: manifest_schema_sha256 = {V2_MANIFEST_SCHEMA_SHA256}"
            )
            != 1
        ):
            raise AssertionError(
                f"Atomic960 BIN V2 manifest schema marker is incomplete:\n{atomic960_output}"
            )
        atomic960_data = assert_atomic_bin_v2_dataset(
            atomic960_v2, 2, atomic960=True
        )
        first_position = V2_HEADER_SIZE
        if (
            atomic960_data[first_position + 33] != 2
            or atomic960_data[first_position + 35] != 1
        ):
            raise AssertionError(
                "Atomic960 BIN V2 did not preserve the non-orthodox b1 queenside rook origin"
            )
        atomic960_manifest_data = assert_atomic_bin_v2_manifest(
            atomic960_manifest,
            ((atomic960_v2, atomic960_data, 2),),
            atomic960=True,
            net=net,
            net_sha256=net_sha256,
            root=root,
            book_path=atomic960_book,
        )
        if atomic960_manifest_data["generation"]["atomic960"] is not True:
            raise AssertionError("Atomic960 BIN V2 manifest did not preserve atomic960=true")

        # This is a valid-net/valid-pure setup. Only the isolated generator may
        # acknowledge or execute the two generator-only commands.
        if normal_engine is not None:
            isolated = root / "normal-engine.bin"
            normal_output = run_engine(
                normal_engine,
                (
                    *setup_commands(net),
                    "atomic_data_schema",
                    "atomic_data_schemas",
                    generation_command(isolated),
                    "quit",
                ),
                expect_success=True,
            )
            normal_lines = normal_output.splitlines()
            if normal_lines.count("readyok") != 1:
                raise AssertionError(
                    f"normal engine did not acknowledge valid NNUE setup:\n{normal_output}"
                )
            if (
                SCHEMA_SHA256 in normal_output
                or V2_SCHEMA_SHA256 in normal_output
                or "INFO: generate_training_data finished." in normal_lines
                or any(line.startswith("PRNG::initial_seed = ") for line in normal_lines)
                or isolated.exists()
            ):
                raise AssertionError(
                    "normal playing binary exposes or executes data-generator commands"
                )

        if unresolved_hashes:
            measurement = "\n".join(unresolved_hashes)
            message = "freeze these measured synthetic fixture SHA-256 values:\n" + measurement
            print(message, file=sys.stderr)
            raise AssertionError(message)

        if not args.smoke_only:
            no_pure = root / "no-pure.bin"
            assert_failed_without_output(
                generator,
                ("uci", "setoption name Use NNUE value false", generation_command(no_pure)),
                no_pure,
                "requires Use NNUE=pure",
            )

            missing_net = root / "missing.nnue"
            invalid_network_output = root / "invalid-network.bin"
            assert_failed_without_output(
                generator,
                (*setup_commands(missing_net), generation_command(invalid_network_output)),
                invalid_network_output,
                "requires a valid compatible Atomic NNUE network",
            )

            chess960 = root / "chess960.bin"
            assert_failed_without_output(
                generator,
                (
                    *setup_commands(net),
                    "setoption name UCI_Chess960 value true",
                    generation_command(chess960),
                ),
                chess960,
                "cannot encode Atomic960",
            )

            depth_zero = root / "depth-zero.bin"
            assert_failed_without_output(
                generator,
                ("uci", generation_command(depth_zero, depth=0)),
                depth_zero,
                "Invalid generate_training_data parameter range",
            )

            zero_write_window = root / "zero-write-window.bin"
            assert_failed_without_output(
                generator,
                (
                    "uci",
                    generation_command(
                        zero_write_window,
                        write_min_ply=0,
                        write_max_ply=0,
                    ),
                ),
                zero_write_window,
                "Invalid generate_training_data parameter range",
            )

            zero_v2_shard = root / "zero-v2-shard.atbin"
            assert_failed_without_output(
                generator,
                (
                    *setup_commands(net),
                    generation_command(
                        zero_v2_shard,
                        data_format="atomic-bin-v2",
                        save_every=0,
                    ),
                ),
                zero_v2_shard,
                "Invalid generate_training_data parameter range",
            )
            if Path(str(zero_v2_shard) + ".manifest.json").exists():
                raise AssertionError("save_every=0 rejection created a V2 manifest")

            rounded_keep_draws = root / "rounded-keep-draws.atbin"
            assert_failed_without_output(
                generator,
                (
                    *setup_commands(net),
                    generation_command(
                        rounded_keep_draws,
                        data_format="atomic-bin-v2",
                        keep_draws="0.99999999999999999",
                    ),
                ),
                rounded_keep_draws,
                "keep_draws must round-trip exactly",
            )
            if Path(str(rounded_keep_draws) + ".manifest.json").exists():
                raise AssertionError("non-round-trippable keep_draws created a V2 manifest")

            syzygy_v2 = root / "syzygy-v2.atbin"
            assert_failed_without_output(
                generator,
                (
                    *setup_commands(net),
                    f"setoption name SyzygyPath value {root}",
                    generation_command(syzygy_v2, data_format="atomic-bin-v2"),
                ),
                syzygy_v2,
                "requires an empty SyzygyPath",
            )
            if Path(str(syzygy_v2) + ".manifest.json").exists():
                raise AssertionError("SyzygyPath rejection created a V2 manifest")

            inverted_depth = root / "inverted-depth.bin"
            assert_failed_without_output(
                generator,
                (
                    "uci",
                    generation_command(
                        inverted_depth,
                        min_depth=8,
                        max_depth=4,
                    ),
                ),
                inverted_depth,
                "Invalid generate_training_data parameter range",
            )

            terminal_book_path = root / "terminal-book.epd"
            terminal_book_path.write_text(
                "7k/8/8/8/8/8/8/K7 w - - 0 1\n",
                encoding="utf-8",
            )
            terminal_book_output = root / "terminal-book.bin"
            assert_failed_without_output(
                generator,
                (
                    *setup_commands(net),
                    generation_command(
                        terminal_book_output,
                        book=terminal_book_path,
                    ),
                ),
                terminal_book_output,
                "Opening book contains an invalid or unsupported Atomic FEN",
            )

            negative_count = root / "negative-count.bin"
            assert_failed_without_output(
                generator,
                (
                    "uci",
                    generation_command(negative_count).replace("count 2", "count -1"),
                ),
                negative_count,
                "must contain decimal digits only",
            )

            existing = root / "existing.bin"
            sentinel = b"atomic-output-must-not-change"
            existing.write_bytes(sentinel)
            existing_output = run_engine(
                generator,
                (*setup_commands(net), generation_command(existing), "quit"),
                expect_success=False,
            )
            if "output already exists" not in existing_output:
                raise AssertionError(
                    f"existing output refusal was not explicit:\n{existing_output}"
                )
            if existing.read_bytes() != sentinel:
                raise AssertionError("existing output was modified by a rejected generation")

            existing_v2 = root / "existing-v2.atbin"
            existing_v2_manifest = Path(str(existing_v2) + ".manifest.json")
            existing_v2.write_bytes(sentinel)
            existing_v2_output = run_engine(
                generator,
                (
                    *setup_commands(net),
                    generation_command(existing_v2, data_format="atomic-bin-v2"),
                    "quit",
                ),
                expect_success=False,
            )
            if "output already exists" not in existing_v2_output:
                raise AssertionError(
                    "existing Atomic BIN V2 shard refusal was not explicit:\n"
                    f"{existing_v2_output}"
                )
            if existing_v2.read_bytes() != sentinel:
                raise AssertionError(
                    "existing Atomic BIN V2 shard was modified by rejected generation"
                )
            if existing_v2_manifest.exists():
                raise AssertionError(
                    "rejected Atomic BIN V2 shard overwrite created a manifest"
                )

            sidecar_v2 = root / "existing-sidecar-v2.atbin"
            sidecar_v2_manifest = Path(str(sidecar_v2) + ".manifest.json")
            sidecar_v2_manifest.write_bytes(sentinel)
            sidecar_v2_output = run_engine(
                generator,
                (
                    *setup_commands(net),
                    generation_command(sidecar_v2, data_format="atomic-bin-v2"),
                    "quit",
                ),
                expect_success=False,
            )
            if "output already exists" not in sidecar_v2_output:
                raise AssertionError(
                    "existing Atomic BIN V2 sidecar refusal was not explicit:\n"
                    f"{sidecar_v2_output}"
                )
            if sidecar_v2_manifest.read_bytes() != sentinel:
                raise AssertionError(
                    "existing Atomic BIN V2 sidecar was modified by rejected generation"
                )
            if sidecar_v2.exists():
                raise AssertionError(
                    "rejected Atomic BIN V2 sidecar overwrite created a shard"
                )

            rollback_v2_first = root / ".atbin"
            rollback_v2_second = root / ".atbin_1.atbin"
            rollback_v2_manifest = Path(str(rollback_v2_first) + ".manifest.json")
            rollback_v2_output = run_engine(
                generator,
                (
                    *setup_commands(net),
                    generation_command(
                        rollback_v2_first,
                        records=4,
                        data_format="atomic-bin-v2",
                        save_every=2,
                        book=multi_book,
                    ),
                    "quit",
                ),
                expect_success=False,
            )
            if "shard metadata is invalid" not in rollback_v2_output:
                raise AssertionError(
                    "Atomic BIN V2 post-finalization rollback was not explicit:\n"
                    f"{rollback_v2_output}"
                )
            for rolled_back in (
                rollback_v2_first,
                rollback_v2_second,
                rollback_v2_manifest,
            ):
                if rolled_back.exists():
                    raise AssertionError(
                        "Atomic BIN V2 rollback retained an owned output: "
                        f"{rolled_back}"
                    )

            datasets = (
                (first, 2),
                (multi, 2),
                (apery_first, 4),
                (random_multipv_first, 2),
                (extreme_multipv_diff, 2),
                (minus_one, 2),
                (multi_book_output_path, 2),
            )
            if tools_engine is not None:
                for dataset, records in datasets:
                    validate_with_tools(tools_engine, dataset, records)
            if trainer_root is not None:
                for dataset, records in datasets:
                    validate_with_trainer(trainer_root, dataset, records)

            # OpenBench sends exactly one line and uploads exactly {OUT}. Prove
            # the bridge can bootstrap from its embedded bench network, reload
            # an external full-SHA teacher, map book NONE to startpos, publish
            # one authenticated bundle, and clean the worker-visible sidecars.
            bridge_net = root / "openbench-teacher.nnue"
            shutil.copyfile(net, bridge_net)
            bridge_bundle = root / "openbench-chunk.bin"
            bridge_command = (
                "openbench_generate_training_data threads 1 hash 16 "
                f"network {bridge_net} network_sha256 {net_sha256} "
                "count 2 seed openbench-integration book NONE "
                f"out {bridge_bundle} depth 1 write_min_ply 0 write_max_ply 2 "
                "random_move_count 0 keep_draws 1 eval_limit 32000 "
                "eval_diff_limit 32000 filter_captures false "
                "filter_checks false filter_promotions false"
            )

            # SHA gates are a preflight: a typo must not spend a long chunk on
            # self-play or leave even a transient generated sidecar behind.
            for label, gated_book, sha_option in (
                ("network", "NONE", "network_sha256 " + "0" * 64),
                (
                    "book",
                    str(multi_book),
                    "network_sha256 "
                    + net_sha256
                    + " book_sha256 "
                    + "0" * 64,
                ),
            ):
                rejected_bundle = root / f"openbench-bad-{label}-sha.bin"
                rejected_command = (
                    "openbench_generate_training_data threads 1 hash 16 "
                    f"network {bridge_net} {sha_option} count 2 "
                    f"seed openbench-bad-{label}-sha book {gated_book} "
                    f"out {rejected_bundle} depth 1 write_min_ply 0 write_max_ply 2 "
                    "random_move_count 0 keep_draws 1 eval_limit 32000 "
                    "eval_diff_limit 32000 filter_captures false "
                    "filter_checks false filter_promotions false"
                )
                rejected_output = run_engine(
                    generator, (rejected_command, "quit"), expect_success=False
                )
                if "supplied pre-generation gate" not in rejected_output:
                    raise AssertionError(
                        f"OpenBench {label} SHA was not rejected in preflight:\n"
                        f"{rejected_output}"
                    )
                if "generate_training_data finished" in rejected_output:
                    raise AssertionError(
                        f"OpenBench {label} SHA rejection ran self-play before failing"
                    )
                for rejected_output_path in (
                    rejected_bundle,
                    Path(str(rejected_bundle) + ".atbin"),
                    Path(str(rejected_bundle) + ".atbin.manifest.json"),
                ):
                    if rejected_output_path.exists() or rejected_output_path.is_symlink():
                        raise AssertionError(
                            "OpenBench SHA preflight retained an output: "
                            f"{rejected_output_path}"
                        )

            bridge_output = run_engine(
                generator, (bridge_command, "quit"), expect_success=True
            )
            if "openbench_generate_training_data finished" not in bridge_output:
                raise AssertionError(
                    f"OpenBench bridge did not report completion:\n{bridge_output}"
                )
            if not bridge_bundle.is_file() or bridge_bundle.read_bytes()[:8] != b"ATOBNDL1":
                raise AssertionError("OpenBench bridge did not publish the frozen bundle")
            for sidecar in (
                Path(str(bridge_bundle) + ".atbin"),
                Path(str(bridge_bundle) + ".atbin.manifest.json"),
            ):
                if sidecar.exists() or sidecar.is_symlink():
                    raise AssertionError(f"OpenBench bridge retained sidecar {sidecar}")
            validator = REPO_ROOT / "tools" / "validate_openbench_datagen_bundle.py"
            validated = subprocess.run(
                [sys.executable, str(validator), str(bridge_bundle)],
                text=True,
                encoding="utf-8",
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=TIMEOUT_SECONDS,
                check=False,
            )
            if validated.returncode != 0 or '"records":2' not in validated.stdout:
                raise AssertionError(
                    "OpenBench bundle validator rejected bridge output:\n"
                    + validated.stdout
                    + validated.stderr
                )

        print(
            "Atomic data-generator tests passed "
            f"fixtures=7 data_sha256={data_sha256} net_sha256={net_sha256}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
