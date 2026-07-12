#!/usr/bin/env python3
"""Integration gate for Atomic-Stockfish's isolated Legacy Atomic V1 generator."""

from __future__ import annotations

import argparse
import hashlib
from pathlib import Path
import re
import subprocess
import sys
import tempfile
from typing import Sequence


SCHEMA_SHA256 = "acca0f551f1c012c31a6c727dedccaebb7b5ebbc46810edb87e31bb208d5abe1"
CAPABILITY_JSON = (
    '{"schema_sha256":"' + SCHEMA_SHA256 + '",'
    '"formats":{"legacy-atomic-v1":{"read":false,"write":true,"record_size":72}}}'
)
RECORD_SIZE = 72
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
                generation_command(first),
                generation_command(second),
                "quit",
            ),
            expect_success=True,
        )
        output_lines = output.splitlines()
        if output_lines.count(CAPABILITY_JSON) != 1:
            raise AssertionError(f"schema capability handshake mismatch:\n{output}")
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

        # This is a valid-net/valid-pure setup. Only the isolated generator may
        # acknowledge or execute the two generator-only commands.
        if normal_engine is not None:
            isolated = root / "normal-engine.bin"
            normal_output = run_engine(
                normal_engine,
                (
                    *setup_commands(net),
                    "atomic_data_schema",
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

        print(
            "Atomic data-generator tests passed "
            f"fixtures=7 data_sha256={data_sha256} net_sha256={net_sha256}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
