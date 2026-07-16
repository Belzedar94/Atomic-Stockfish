#!/usr/bin/env python3
"""End-to-end contract for ``generate_atomic_v3_chunk``.

The C++ unit binary owns engine-backed replay of every Atomic move class. This
gate exercises the isolated CLI, two-role transaction, frozen ledger wire,
determinism and fail-clean policy boundaries with a real compatible network.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import re
import struct
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Optional


ROOT = Path(__file__).resolve().parents[1]
DATA_SCHEMA = (ROOT / "schemas" / "atomic-bin-v2.json").read_bytes()
TRAJECTORY_SCHEMA = (ROOT / "schemas" / "atomic-trajectory-ledger-v1.json").read_bytes()
DATA_SCHEMA_SHA256 = hashlib.sha256(DATA_SCHEMA).digest()
TRAJECTORY_SCHEMA_SHA256 = hashlib.sha256(TRAJECTORY_SCHEMA).digest()
MANIFEST_SCHEMA_SHA256 = hashlib.sha256(
    (ROOT / "schemas" / "atomic-bin-v2-manifest.json").read_bytes()
).hexdigest()
SPLIT_GROUP_DOMAIN = bytes.fromhex(
    json.loads(TRAJECTORY_SCHEMA)["split_group_id"]["domain_ascii_hex"]
)
PARTITION_DOMAIN = bytes.fromhex(
    json.loads(TRAJECTORY_SCHEMA)["partition"]["domain_ascii_hex"]
)
PROCESS_TIMEOUT = 90


class ProducerError(RuntimeError):
    pass


def require_file(path: Path, label: str) -> Path:
    resolved = path.expanduser().resolve()
    if not resolved.is_file():
        raise ProducerError(f"{label} is not a file: {resolved}")
    return resolved


def command(
    prefix: Path,
    network_sha256: str,
    *,
    max_games: int = 1000,
    book: Optional[Path] = None,
    book_sha256: str = "none",
) -> str:
    options = [
        "generate_atomic_v3_chunk",
        "depth 1",
        "train_count 1",
        "validation_count 1",
        f"out {prefix}",
        "eval_limit 32767",
        "eval_diff_limit 32000",
        "random_move_min_ply 1",
        "random_move_max_ply 4",
        "random_move_count 2",
        "random_multi_pv 0",
        "write_min_ply 0",
        "write_max_ply 4",
    ]
    if book is not None:
        options.append(f"book {book}")
    options.extend(
        (
            "keep_draws 1",
            "adjudicate_draws_by_score false",
            "adjudicate_draws_by_insufficient_material true",
            "adjudicate_resignations false",
            "filter_captures false",
            "filter_checks false",
            "filter_promotions false",
            "generation_seed 20260716",
            "split_seed 99",
            "validation_threshold_u64 9223372036854775808",
            f"network_sha256 {network_sha256}",
            f"book_sha256 {book_sha256}",
            f"max_games {max_games}",
            "set_recommended_uci_options",
        )
    )
    return " ".join(options)


def run_generator(
    generator: Path,
    network: Path,
    producer_command: str,
    *,
    threads: int = 1,
    atomic960: bool = False,
    use_nnue: str = "pure",
    syzygy: Optional[str] = None,
    expect_success: bool,
) -> str:
    commands = [
        f"setoption name Threads value {threads}",
        "setoption name Hash value 16",
        f"setoption name UCI_Chess960 value {'true' if atomic960 else 'false'}",
        f"setoption name Use NNUE value {use_nnue}",
        f"setoption name EvalFile value {network}",
    ]
    if syzygy is not None:
        commands.append(f"setoption name SyzygyPath value {syzygy}")
    commands.extend((producer_command, "quit"))
    try:
        result = subprocess.run(
            [str(generator)],
            input=("\n".join(commands) + "\n").encode("utf-8"),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=PROCESS_TIMEOUT,
            check=False,
        )
    except subprocess.TimeoutExpired as error:
        prefix = next(
            (token for token in producer_command.split() if token.endswith(".atbin")),
            producer_command,
        )
        partial = (error.stdout or b"").decode("utf-8", errors="replace")
        raise ProducerError(
            f"Atomic V3 producer timed out: {prefix}\nPartial output:\n{partial}"
        ) from error
    output = result.stdout.decode("utf-8", errors="replace")
    if expect_success != (result.returncode == 0):
        expectation = "success" if expect_success else "failure"
        raise ProducerError(
            f"Atomic V3 producer expected {expectation}, got {result.returncode}:\n{output}"
        )
    return output


def artifact_paths(prefix: Path, role: str) -> tuple[Path, Path, Path]:
    dataset = Path(f"{prefix}.{role}.atbin")
    return dataset, Path(f"{dataset}.manifest.json"), Path(f"{prefix}.{role}.attraj")


def canonical_manifest(
    path: Path, role: str, expected_threads: int, expected_atomic960: bool
) -> tuple[dict[str, Any], bytes]:
    raw = path.read_bytes()
    value = json.loads(raw)
    canonical = json.dumps(
        value, ensure_ascii=False, allow_nan=False, separators=(",", ":")
    ).encode("utf-8") + b"\n"
    if raw != canonical:
        raise ProducerError(f"{role} manifest is not canonical JSON")
    if value["manifest_schema_sha256"] != MANIFEST_SCHEMA_SHA256:
        raise ProducerError(f"{role} manifest schema pin differs")
    if not re.fullmatch(r"[0-9a-f]{40}", value["engine"]["commit"]):
        raise ProducerError(f"{role} manifest has no pinned producer commit")
    generation = value["generation"]
    if (
        generation["use_nnue"] != "pure"
        or generation["threads"] != expected_threads
        or generation["atomic960"] is not expected_atomic960
        or generation["options"]["adjudicate_draws_by_score"] is not False
        or generation["options"]["requested_records"] != "1"
    ):
        raise ProducerError(f"{role} manifest release policy differs")
    if value["statistics"]["records"] != "1" or len(value["shards"]) != 1:
        raise ProducerError(f"{role} manifest record framing differs")
    return value, raw


def validate_dataset(path: Path, manifest: dict[str, Any], role: str) -> bytes:
    raw = path.read_bytes()
    if len(raw) != 160 or raw[:8] != b"ATBINV2\0":
        raise ProducerError(f"{role} Atomic BIN V2 framing differs")
    if raw[24:56] != DATA_SCHEMA_SHA256 or struct.unpack_from("<Q", raw, 56)[0] != 1:
        raise ProducerError(f"{role} Atomic BIN V2 schema/count differs")
    shard = manifest["shards"][0]
    if shard["file"] != path.name or shard["bytes"] != str(len(raw)):
        raise ProducerError(f"{role} shard binding differs")
    if shard["sha256"] != hashlib.sha256(raw).hexdigest():
        raise ProducerError(f"{role} shard SHA-256 differs")
    return raw


def validate_ledger(
    path: Path,
    manifest_raw: bytes,
    role: str,
    dataset_raw: bytes,
    expected_atomic960: bool,
) -> bytes:
    raw = path.read_bytes()
    expected_role = 0 if role == "train" else 1
    if len(raw) < 276 or raw[:8] != b"ATTRAJ1\0":
        raise ProducerError(f"{role} trajectory ledger framing differs")
    version, header_size = struct.unpack_from("<HH", raw, 8)
    endian, entry_size, encoded_role = struct.unpack_from("<III", raw, 12)
    if (version, header_size, endian, entry_size, encoded_role) != (
        1,
        160,
        0x01020304,
        112,
        expected_role,
    ):
        raise ProducerError(f"{role} trajectory header constants differ")
    if raw[24:56] != TRAJECTORY_SCHEMA_SHA256:
        raise ProducerError(f"{role} trajectory schema hash differs")
    if raw[56:88] != hashlib.sha256(manifest_raw).digest():
        raise ProducerError(f"{role} manifest is not bound into trajectory header")
    if raw[88:120] != DATA_SCHEMA_SHA256:
        raise ProducerError(f"{role} data schema is not bound into trajectory header")
    record_count, trajectory_count, move_count, entries_offset, moves_offset = (
        struct.unpack_from("<QQQQQ", raw, 120)
    )
    if record_count != 1 or not trajectory_count or not move_count:
        raise ProducerError(f"{role} trajectory counts differ")
    if entries_offset != 160 or moves_offset != 160 + trajectory_count * 112:
        raise ProducerError(f"{role} trajectory offsets differ")
    if len(raw) != moves_offset + move_count * 4:
        raise ProducerError(f"{role} trajectory strict EOF differs")

    first_record = 0
    first_move = 0
    groups: set[bytes] = set()
    mapped_ranges: list[tuple[int, int, int]] = []
    for index in range(trajectory_count):
        offset = 160 + index * 112
        group = raw[offset : offset + 32]
        root = raw[offset + 32 : offset + 80]
        entry_first_record = struct.unpack_from("<Q", raw, offset + 80)[0]
        entry_records, entry_moves = struct.unpack_from("<II", raw, offset + 88)
        entry_first_move = struct.unpack_from("<Q", raw, offset + 96)[0]
        terminal_result = struct.unpack_from("<b", raw, offset + 104)[0]
        atomic960, stop_reason = struct.unpack_from("<BB", raw, offset + 105)
        if (
            entry_first_record != first_record
            or entry_first_move != first_move
            or not entry_records
            or not entry_moves
            or terminal_result not in (-1, 0, 1)
            or atomic960 != int(expected_atomic960)
            or stop_reason > 6
            or raw[offset + 107 : offset + 112] != b"\0" * 5
            or group in groups
        ):
            raise ProducerError(f"{role} trajectory entry {index} differs")
        moves = raw[
            moves_offset + entry_first_move * 4 : moves_offset
            + (entry_first_move + entry_moves) * 4
        ]
        expected_group = hashlib.sha256(
            SPLIT_GROUP_DOMAIN
            + root
            + bytes((atomic960,))
            + struct.pack("<Q", entry_moves)
            + moves
        ).digest()
        if group != expected_group:
            raise ProducerError(f"{role} split-group ID {index} differs")
        partition = hashlib.sha256(
            PARTITION_DOMAIN + struct.pack("<Q", 99) + group
        ).digest()
        is_validation = struct.unpack_from("<Q", partition)[0] < 9223372036854775808
        if is_validation != (role == "validation"):
            raise ProducerError(f"{role} trajectory {index} partitions to the other role")
        groups.add(group)
        mapped_ranges.append((first_record, entry_records, entry_moves))
        first_record += entry_records
        first_move += entry_moves
    if first_record != record_count or first_move != move_count:
        raise ProducerError(f"{role} trajectory ranges do not cover their streams")

    records = dataset_raw[96:]
    for start, count, entry_moves in mapped_ranges:
        plies = [struct.unpack_from("<I", records, (start + i) * 64 + 56)[0] for i in range(count)]
        if plies != sorted(set(plies)) or any(ply >= entry_moves for ply in plies):
            raise ProducerError(f"{role} retained pre-move mapping differs")
    return raw


def validate_output(
    prefix: Path, *, threads: int = 1, atomic960: bool = False
) -> dict[str, tuple[dict[str, Any], bytes, bytes, bytes]]:
    result: dict[str, tuple[dict[str, Any], bytes, bytes, bytes]] = {}
    for role in ("train", "validation"):
        dataset, manifest_path, ledger_path = artifact_paths(prefix, role)
        if not all(path.is_file() for path in (dataset, manifest_path, ledger_path)):
            raise ProducerError(f"{role} transaction did not publish all three artifacts")
        manifest, manifest_raw = canonical_manifest(
            manifest_path, role, threads, atomic960
        )
        dataset_raw = validate_dataset(dataset, manifest, role)
        ledger_raw = validate_ledger(
            ledger_path, manifest_raw, role, dataset_raw, atomic960
        )
        result[role] = manifest, manifest_raw, dataset_raw, ledger_raw
    return result


def assert_same_generation(
    first: dict[str, tuple[dict[str, Any], bytes, bytes, bytes]],
    second: dict[str, tuple[dict[str, Any], bytes, bytes, bytes]],
) -> None:
    for role in ("train", "validation"):
        first_manifest, _, first_data, first_ledger = first[role]
        second_manifest, _, second_data, second_ledger = second[role]
        if first_data != second_data or first_ledger[88:] != second_ledger[88:]:
            raise ProducerError(f"{role} fixed-seed data/trajectory payload is not deterministic")
        normalized_first = copy.deepcopy(first_manifest)
        normalized_second = copy.deepcopy(second_manifest)
        normalized_first["shards"][0]["file"] = "<ROLE>.atbin"
        normalized_second["shards"][0]["file"] = "<ROLE>.atbin"
        if normalized_first != normalized_second:
            raise ProducerError(f"{role} fixed-seed manifest semantics are not deterministic")


def prefix_artifacts(prefix: Path) -> list[Path]:
    return sorted(
        set(prefix.parent.glob(prefix.name + "*"))
        | set(prefix.parent.glob("." + prefix.name + "*"))
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--generator", type=Path, required=True)
    parser.add_argument("--net", type=Path, required=True)
    parser.add_argument("--timeout", type=int, default=90)
    args = parser.parse_args()
    global PROCESS_TIMEOUT
    if args.timeout <= 0:
        raise ProducerError("--timeout must be positive")
    PROCESS_TIMEOUT = args.timeout
    generator = require_file(args.generator, "data-generator")
    network = require_file(args.net, "Atomic NNUE network")
    network_sha256 = hashlib.sha256(network.read_bytes()).hexdigest()

    parent = Path(os.environ.get("ATOMIC_TEST_TEMP", r"C:\Temp" if os.name == "nt" else "/tmp"))
    parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="atomic-v3-producer-", dir=parent) as temporary:
        root = Path(temporary)
        if any(character.isspace() for character in str(root)):
            raise ProducerError(f"test staging path contains whitespace: {root}")

        first_prefix = root / "first"
        output = run_generator(
            generator, network, command(first_prefix, network_sha256), expect_success=True
        )
        if output.splitlines().count("INFO: generate_atomic_v3_chunk finished.") != 1:
            raise ProducerError("producer did not emit exactly one completion marker")
        first = validate_output(first_prefix)

        second_prefix = root / "second"
        run_generator(
            generator, network, command(second_prefix, network_sha256), expect_success=True
        )
        second = validate_output(second_prefix)
        assert_same_generation(first, second)

        opening_book = root / "opening.epd"
        with opening_book.open("w", encoding="ascii", newline="\n") as stream:
            stream.write("rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1\n")
        opening_sha256 = hashlib.sha256(opening_book.read_bytes()).hexdigest()
        book_prefix = root / "book"
        run_generator(
            generator,
            network,
            command(
                book_prefix,
                network_sha256,
                book=opening_book,
                book_sha256=opening_sha256,
            ),
            expect_success=True,
        )
        book_output = validate_output(book_prefix)
        for role in ("train", "validation"):
            manifest = book_output[role][0]
            if (
                manifest["book"]["kind"] != "file"
                or manifest["book"]["file"] != opening_book.name
                or manifest["book"]["sha256"] != opening_sha256
            ):
                raise ProducerError(f"{role} authenticated opening-book binding differs")

        # The deterministic contract is per complete configuration, including
        # Threads. game_id is internal scheduling state; retained bytes, pins,
        # split groups and artifact hashes are the public evidence.
        for threads in (2, 4):
            threaded_first_prefix = root / f"threads-{threads}-first"
            run_generator(
                generator,
                network,
                command(threaded_first_prefix, network_sha256),
                threads=threads,
                expect_success=True,
            )
            threaded_first = validate_output(threaded_first_prefix, threads=threads)
            threaded_second_prefix = root / f"threads-{threads}-second"
            run_generator(
                generator,
                network,
                command(threaded_second_prefix, network_sha256),
                threads=threads,
                expect_success=True,
            )
            threaded_second = validate_output(threaded_second_prefix, threads=threads)
            assert_same_generation(threaded_first, threaded_second)

        atomic960_book = root / "atomic960.epd"
        with atomic960_book.open("w", encoding="ascii", newline="\n") as stream:
            stream.write("bbqnnrkr/pppppppp/8/8/8/8/PPPPPPPP/BBQNNRKR w HFhf - 0 1\n")
        atomic960_book_sha256 = hashlib.sha256(atomic960_book.read_bytes()).hexdigest()
        atomic960_prefix = root / "atomic960"
        run_generator(
            generator,
            network,
            command(
                atomic960_prefix,
                network_sha256,
                book=atomic960_book,
                book_sha256=atomic960_book_sha256,
            ),
            atomic960=True,
            expect_success=True,
        )
        validate_output(atomic960_prefix, atomic960=True)

        # Linux CI always supports symlinks; Windows executes these checks when
        # Developer Mode/privileges permit creation and otherwise exercises the
        # same reparse rejection in the C++ Windows gate.
        try:
            network_link = root / "network-link.nnue"
            network_link.symlink_to(network)
        except OSError:
            network_link = None
        if network_link is not None:
            symlink_prefix = root / "network-symlink"
            run_generator(
                generator,
                network_link,
                command(symlink_prefix, network_sha256),
                expect_success=False,
            )
            if prefix_artifacts(symlink_prefix):
                raise ProducerError("network-symlink refusal created output")

        try:
            book_link = root / "book-link.epd"
            book_link.symlink_to(opening_book)
        except OSError:
            book_link = None
        if book_link is not None:
            book_symlink_prefix = root / "book-symlink"
            run_generator(
                generator,
                network,
                command(
                    book_symlink_prefix,
                    network_sha256,
                    book=book_link,
                    book_sha256=opening_sha256,
                ),
                expect_success=False,
            )
            if prefix_artifacts(book_symlink_prefix):
                raise ProducerError("book-symlink refusal created output")

        before = {path: hashlib.sha256(path.read_bytes()).digest() for path in prefix_artifacts(first_prefix)}
        run_generator(
            generator, network, command(first_prefix, network_sha256), expect_success=False
        )
        after = {path: hashlib.sha256(path.read_bytes()).digest() for path in prefix_artifacts(first_prefix)}
        if before != after:
            raise ProducerError("existing-output refusal modified a published artifact")

        rollback_prefix = root / "rollback"
        run_generator(
            generator,
            network,
            command(rollback_prefix, network_sha256, max_games=1),
            expect_success=False,
        )
        if prefix_artifacts(rollback_prefix):
            raise ProducerError("max_games failure left a final or private staging artifact")

        wrong_pin_prefix = root / "wrong-pin"
        run_generator(
            generator,
            network,
            command(wrong_pin_prefix, "0" * 64),
            expect_success=False,
        )
        if prefix_artifacts(wrong_pin_prefix):
            raise ProducerError("network-pin failure created output")

        wrong_book_prefix = root / "wrong-book-pin"
        run_generator(
            generator,
            network,
            command(
                wrong_book_prefix,
                network_sha256,
                book=opening_book,
                book_sha256="0" * 64,
            ),
            expect_success=False,
        )
        if prefix_artifacts(wrong_book_prefix):
            raise ProducerError("book-pin failure created output")

        policy_prefix = root / "policy"
        policy_command = command(policy_prefix, network_sha256).replace(
            "adjudicate_resignations false", "adjudicate_resignations true"
        )
        run_generator(generator, network, policy_command, expect_success=False)
        if prefix_artifacts(policy_prefix):
            raise ProducerError("resignation-policy failure created output")

        score_draw_prefix = root / "score-draw-policy"
        score_draw_command = command(score_draw_prefix, network_sha256).replace(
            "adjudicate_draws_by_score false", "adjudicate_draws_by_score true"
        )
        run_generator(generator, network, score_draw_command, expect_success=False)
        if prefix_artifacts(score_draw_prefix):
            raise ProducerError("score-draw policy failure created output")

        nnue_prefix = root / "mixed-nnue"
        run_generator(
            generator,
            network,
            command(nnue_prefix, network_sha256),
            use_nnue="true",
            expect_success=False,
        )
        if prefix_artifacts(nnue_prefix):
            raise ProducerError("mixed-NNUE failure created output")

        syzygy_prefix = root / "syzygy"
        run_generator(
            generator,
            network,
            command(syzygy_prefix, network_sha256),
            syzygy=str(root),
            expect_success=False,
        )
        if prefix_artifacts(syzygy_prefix):
            raise ProducerError("Syzygy-policy failure created output")

    print(
        "Atomic V3 trajectory producer tests passed "
        "roles=2 deterministic_threads=1,2,4 atomic960=1 rollback=1"
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ProducerError, subprocess.TimeoutExpired, ValueError) as error:
        print(f"ERROR: {error}")
        raise SystemExit(1)
