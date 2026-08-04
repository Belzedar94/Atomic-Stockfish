"""Build and validate the immutable Atomic E00-SRC paired schedule.

The schedule is deliberately independent of pathnames, filesystem order,
Python's RNG and wall-clock state.  It authenticates one stable snapshot of
the opening book, ranks every unique six-field FEN by a content-derived
SHA-256, and allocates disjoint roots to the requested time-control strata.

Both the JSONL schedule and its JSON receipt are canonical, create-new
artifacts.  The receipt is the commit marker: result-bearing runners should
always call :func:`load_schedule` with a receipt before starting an engine.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
from pathlib import Path
import re
import sys
from typing import Mapping, Sequence

from tools.atomic_mining import common


SCHEDULE_SCHEMA = "atomic-e00-schedule-v1"
RECEIPT_SCHEMA = "atomic-e00-schedule-receipt-v1"
ORDER_DERIVATION = "sha256-utf8-concat-v1"
CURRENT_ROLE = "current-v3"
TEACHER_ROLE = "run3b"
_TC_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_PIECES = frozenset("PNBRQKpnbrqk")


class E00ScheduleError(common.MiningArtifactError):
    """The E00 schedule or opening-book contract is invalid."""


@dataclass(frozen=True)
class TimeControl:
    """One named E00-SRC stratum."""

    name: str
    base_ms: int
    increment_ms: int
    pairs: int

    def schedule_value(self) -> dict[str, object]:
        return {
            "base_ms": self.base_ms,
            "increment_ms": self.increment_ms,
            "name": self.name,
        }

    def receipt_value(self) -> dict[str, object]:
        return {**self.schedule_value(), "pairs": self.pairs}


@dataclass(frozen=True)
class BookRoot:
    """One normalized, authenticated root from the stable book snapshot."""

    book_line: int
    fen: str
    fen_sha256: str
    root_identity: str
    order_sha256: str


@dataclass(frozen=True)
class ScheduleSnapshot:
    """A validated schedule and, when supplied, its commit-marker receipt."""

    path: Path
    rows: tuple[dict[str, object], ...]
    sha256: str
    size_bytes: int
    receipt: Mapping[str, object] | None
    receipt_sha256: str | None


@dataclass(frozen=True)
class ScheduleSummary:
    """Provenance returned after publishing a schedule and receipt."""

    book_sha256: str
    book_root_count: int
    pair_count: int
    game_count: int
    schedule_sha256: str
    schedule_size_bytes: int
    receipt_sha256: str
    receipt_size_bytes: int


def _digest(namespace: str, value: object) -> str:
    return common.sha256_bytes(
        common.canonical_json_bytes({"namespace": namespace, "value": value})
    )


def _require_plain_int(
    value: object,
    *,
    label: str,
    minimum: int,
) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise E00ScheduleError(f"{label} must be an integer >= {minimum}")
    return value


def _require_string(value: object, *, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise E00ScheduleError(f"{label} must be a non-empty string")
    return value


def _require_sha(value: object, *, label: str) -> str:
    try:
        return common.require_lower_hex_sha256(value, label=label)
    except common.MiningArtifactError as error:
        raise E00ScheduleError(str(error)) from error


def _validate_board(board: str, *, label: str) -> None:
    ranks = board.split("/")
    if len(ranks) != 8:
        raise E00ScheduleError(f"{label} board must contain eight ranks")
    for rank in ranks:
        squares = 0
        previous_was_digit = False
        for character in rank:
            if character in "12345678":
                if previous_was_digit:
                    raise E00ScheduleError(
                        f"{label} board has adjacent empty-square digits"
                    )
                squares += int(character)
                previous_was_digit = True
            elif character in _PIECES:
                squares += 1
                previous_was_digit = False
            else:
                raise E00ScheduleError(
                    f"{label} board contains an invalid piece"
                )
        if squares != 8:
            raise E00ScheduleError(
                f"{label} board rank does not contain eight squares"
            )


def normalize_fen(value: str, *, label: str = "book FEN") -> str:
    """Return the canonical whitespace/counter form of a six-field Atomic FEN."""

    if not isinstance(value, str):
        raise E00ScheduleError(f"{label} must be text")
    collapsed = " ".join(value.split())
    try:
        board, side, castling, en_passant, halfmove, fullmove = common.fen_fields(
            collapsed
        )
    except common.MiningArtifactError as error:
        raise E00ScheduleError(f"{label}: {error}") from error
    _validate_board(board, label=label)
    if castling != "-" and len(set(castling)) != len(castling):
        raise E00ScheduleError(f"{label} has duplicate castling rights")
    return " ".join(
        (
            board,
            side,
            castling,
            en_passant,
            str(halfmove),
            str(fullmove),
        )
    )


def _fen_sha256(fen: str) -> str:
    return common.derive_id("atomic-root-fen-v1", fen)


def _root_identity(fen: str) -> str:
    return common.derive_id("atomic-e00-root-identity-v1", fen)


def _order_sha256(*, seed: str, book_sha256: str, root_identity: str) -> str:
    # This is the exact concatenation frozen in docs/atomic/e00-source-contract.md.
    payload = (
        SCHEDULE_SCHEMA + seed + book_sha256 + root_identity
    ).encode("utf-8")
    return common.sha256_bytes(payload)


def parse_book_snapshot(
    payload: bytes,
    *,
    seed: str,
) -> tuple[tuple[BookRoot, ...], str]:
    """Parse every physical book line from one authenticated byte snapshot."""

    seed = _require_string(seed, label="seed")
    if "\x00" in seed:
        raise E00ScheduleError("seed must not contain NUL")
    if payload.startswith(b"\xef\xbb\xbf"):
        raise E00ScheduleError("opening book UTF-8 BOM is forbidden")
    try:
        text = payload.decode("utf-8", errors="strict")
    except UnicodeDecodeError as error:
        raise E00ScheduleError("opening book is not strict UTF-8") from error

    book_sha256 = common.sha256_bytes(payload)
    physical_lines = text.split("\n")
    if physical_lines and physical_lines[-1] == "":
        physical_lines.pop()
    if not physical_lines:
        raise E00ScheduleError("opening book must contain at least one root")

    roots: list[BookRoot] = []
    seen: dict[str, int] = {}
    for line_number, physical_line in enumerate(physical_lines, start=1):
        if physical_line.endswith("\r"):
            physical_line = physical_line[:-1]
        if "\r" in physical_line:
            raise E00ScheduleError(
                f"opening book line {line_number} contains a stray CR"
            )
        if not physical_line.strip():
            raise E00ScheduleError(
                f"opening book line {line_number} is empty"
            )
        fen = normalize_fen(
            physical_line,
            label=f"opening book line {line_number}",
        )
        if fen in seen:
            raise E00ScheduleError(
                "opening book contains duplicate normalized root at "
                f"lines {seen[fen]} and {line_number}"
            )
        seen[fen] = line_number
        identity = _root_identity(fen)
        roots.append(
            BookRoot(
                book_line=line_number,
                fen=fen,
                fen_sha256=_fen_sha256(fen),
                root_identity=identity,
                order_sha256=_order_sha256(
                    seed=seed,
                    book_sha256=book_sha256,
                    root_identity=identity,
                ),
            )
        )
    return tuple(roots), book_sha256


def parse_time_control_spec(specification: str) -> TimeControl:
    """Parse ``NAME:BASE_MS:INC_MS:PAIRS`` without permissive coercions."""

    fields = specification.split(":")
    if len(fields) != 4:
        raise E00ScheduleError(
            "time control must be NAME:BASE_MS:INC_MS:PAIRS"
        )
    name, base_text, increment_text, pairs_text = fields
    if _TC_NAME.fullmatch(name) is None:
        raise E00ScheduleError("time-control name is malformed")
    if not base_text.isascii() or not base_text.isdecimal():
        raise E00ScheduleError("time-control base_ms must be a decimal integer")
    if not increment_text.isascii() or not increment_text.isdecimal():
        raise E00ScheduleError(
            "time-control increment_ms must be a decimal integer"
        )
    if not pairs_text.isascii() or not pairs_text.isdecimal():
        raise E00ScheduleError("time-control pairs must be a decimal integer")
    base_ms = int(base_text)
    increment_ms = int(increment_text)
    pairs = int(pairs_text)
    if base_ms <= 0:
        raise E00ScheduleError("time-control base_ms must be > 0")
    if increment_ms < 0:
        raise E00ScheduleError("time-control increment_ms must be >= 0")
    if pairs <= 0:
        raise E00ScheduleError("time-control pairs must be > 0")
    return TimeControl(name, base_ms, increment_ms, pairs)


def _validate_time_controls(
    time_controls: Sequence[TimeControl],
) -> tuple[TimeControl, ...]:
    if not time_controls:
        raise E00ScheduleError("at least one time control is required")
    normalized: list[TimeControl] = []
    names: set[str] = set()
    for index, item in enumerate(time_controls, start=1):
        if not isinstance(item, TimeControl):
            raise E00ScheduleError(
                f"time control {index} must be a TimeControl"
            )
        # Reparse the public wire so programmatic callers get CLI-equivalent
        # validation, including the conservative name alphabet.
        validated = parse_time_control_spec(
            f"{item.name}:{item.base_ms}:{item.increment_ms}:{item.pairs}"
        )
        if validated.name in names:
            raise E00ScheduleError(
                f"duplicate time-control name: {validated.name}"
            )
        names.add(validated.name)
        normalized.append(validated)
    return tuple(normalized)


def _pair_id(
    *,
    seed: str,
    book_sha256: str,
    pair_ordinal: int,
    time_control: Mapping[str, object],
    root_identity: str,
) -> str:
    return _digest(
        "atomic-e00-pair-v1",
        {
            "book_sha256": book_sha256,
            "pair_ordinal": pair_ordinal,
            "root_identity": root_identity,
            "schedule_schema": SCHEDULE_SCHEMA,
            "seed": seed,
            "time_control": dict(time_control),
        },
    )


def build_schedule(
    roots: Sequence[BookRoot],
    *,
    seed: str,
    book_sha256: str,
    time_controls: Sequence[TimeControl],
) -> tuple[list[dict[str, object]], dict[str, object]]:
    """Build canonical rows and an unhashed receipt value in memory."""

    seed = _require_string(seed, label="seed")
    book_sha256 = _require_sha(book_sha256, label="book_sha256")
    controls = _validate_time_controls(time_controls)
    pair_count = sum(control.pairs for control in controls)
    if pair_count > len(roots):
        raise E00ScheduleError(
            "opening book has insufficient unique roots for disjoint strata: "
            f"required={pair_count}, available={len(roots)}"
        )
    expected_roots: dict[str, BookRoot] = {}
    for root in roots:
        if not isinstance(root, BookRoot):
            raise E00ScheduleError("roots must contain BookRoot values")
        if root.root_identity in expected_roots:
            raise E00ScheduleError("duplicate root identity in parsed book")
        if root.fen_sha256 != _fen_sha256(root.fen):
            raise E00ScheduleError("root FEN hash does not recompute")
        if root.root_identity != _root_identity(root.fen):
            raise E00ScheduleError("root identity does not recompute")
        if root.order_sha256 != _order_sha256(
            seed=seed,
            book_sha256=book_sha256,
            root_identity=root.root_identity,
        ):
            raise E00ScheduleError("root ordering hash does not recompute")
        expected_roots[root.root_identity] = root

    ordered = sorted(
        roots,
        key=lambda root: (
            root.order_sha256,
            root.root_identity,
        ),
    )
    selected = ordered[:pair_count]
    rows: list[dict[str, object]] = []
    selected_by_stratum: dict[str, list[str]] = {}
    cursor = 0
    pair_ordinal = 1
    for control in controls:
        selected_by_stratum[control.name] = []
        time_control = control.schedule_value()
        for root in selected[cursor : cursor + control.pairs]:
            selected_by_stratum[control.name].append(root.root_identity)
            pair_id = _pair_id(
                seed=seed,
                book_sha256=book_sha256,
                pair_ordinal=pair_ordinal,
                time_control=time_control,
                root_identity=root.root_identity,
            )
            root_value = {
                "book_line": root.book_line,
                "fen": root.fen,
                "fen_sha256": root.fen_sha256,
                "order_sha256": root.order_sha256,
                "root_identity": root.root_identity,
            }
            for leg, (white_role, black_role) in enumerate(
                (
                    (CURRENT_ROLE, TEACHER_ROLE),
                    (TEACHER_ROLE, CURRENT_ROLE),
                )
            ):
                rows.append(
                    {
                        "black_role": black_role,
                        "book_sha256": book_sha256,
                        "leg": leg,
                        "pair_id": pair_id,
                        "pair_ordinal": pair_ordinal,
                        "root": dict(root_value),
                        "schema": SCHEDULE_SCHEMA,
                        "seed": seed,
                        "time_control": dict(time_control),
                        "white_role": white_role,
                    }
                )
            pair_ordinal += 1
        cursor += control.pairs

    # This validation is intentionally applied before any bytes can be
    # published.  It is the same public validator used by the runner.
    validate_schedule(rows)
    receipt = {
        "book": {
            "root_count": len(roots),
            # Filled by the file wrapper from the exact same stable snapshot.
            "sha256": book_sha256,
            "size_bytes": None,
        },
        "counts": {
            "book_roots": len(roots),
            "games": len(rows),
            "pairs": pair_count,
            "strata": len(controls),
        },
        "derivation": {
            "algorithm": ORDER_DERIVATION,
            "expression": (
                "SHA256(schedule_schema || seed || book_sha256 "
                "|| root_identity)"
            ),
        },
        "schedule": {
            # Filled after canonical JSONL serialization.
            "row_count": len(rows),
            "sha256": None,
            "size_bytes": None,
        },
        "schedule_schema": SCHEDULE_SCHEMA,
        "schema": RECEIPT_SCHEMA,
        "seed": seed,
        "selected_root_identities": selected_by_stratum,
        "time_controls": [
            control.receipt_value() for control in controls
        ],
    }
    return rows, receipt


def _mapping(value: object, *, label: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise E00ScheduleError(f"{label} must be an object")
    return value


def _validate_time_control_wire(
    value: object, *, label: str
) -> dict[str, object]:
    mapping = _mapping(value, label=label)
    try:
        common.require_exact_fields(
            mapping,
            ("name", "base_ms", "increment_ms"),
            label=label,
        )
    except common.MiningArtifactError as error:
        raise E00ScheduleError(str(error)) from error
    name = _require_string(mapping["name"], label=f"{label}.name")
    if _TC_NAME.fullmatch(name) is None:
        raise E00ScheduleError(f"{label}.name is malformed")
    base_ms = _require_plain_int(
        mapping["base_ms"], label=f"{label}.base_ms", minimum=1
    )
    increment_ms = _require_plain_int(
        mapping["increment_ms"],
        label=f"{label}.increment_ms",
        minimum=0,
    )
    return {
        "base_ms": base_ms,
        "increment_ms": increment_ms,
        "name": name,
    }


def validate_schedule(
    rows: Sequence[Mapping[str, object]],
) -> tuple[dict[str, object], ...]:
    """Validate a complete E00 schedule and return normalized row copies.

    This is the public semantic validator for the result-bearing runner.  It
    recomputes every root/pair identity and rejects partial pairs, duplicate
    roots, role drift, non-consecutive ordinals or non-canonical row order.
    """

    if not rows:
        raise E00ScheduleError("schedule must contain at least one pair")
    normalized: list[dict[str, object]] = []
    for row_number, raw_row in enumerate(rows, start=1):
        row = _mapping(raw_row, label=f"schedule row {row_number}")
        try:
            common.require_exact_fields(
                row,
                (
                    "black_role",
                    "book_sha256",
                    "leg",
                    "pair_id",
                    "pair_ordinal",
                    "root",
                    "schema",
                    "seed",
                    "time_control",
                    "white_role",
                ),
                label=f"schedule row {row_number}",
            )
        except common.MiningArtifactError as error:
            raise E00ScheduleError(str(error)) from error
        if row["schema"] != SCHEDULE_SCHEMA:
            raise E00ScheduleError(
                f"schedule row {row_number}.schema must be {SCHEDULE_SCHEMA}"
            )
        seed = _require_string(
            row["seed"], label=f"schedule row {row_number}.seed"
        )
        if "\x00" in seed:
            raise E00ScheduleError("schedule seed must not contain NUL")
        book_sha256 = _require_sha(
            row["book_sha256"],
            label=f"schedule row {row_number}.book_sha256",
        )
        pair_ordinal = _require_plain_int(
            row["pair_ordinal"],
            label=f"schedule row {row_number}.pair_ordinal",
            minimum=1,
        )
        leg = _require_plain_int(
            row["leg"], label=f"schedule row {row_number}.leg", minimum=0
        )
        if leg not in (0, 1):
            raise E00ScheduleError(
                f"schedule row {row_number}.leg must be 0 or 1"
            )
        pair_id = _require_sha(
            row["pair_id"], label=f"schedule row {row_number}.pair_id"
        )
        time_control = _validate_time_control_wire(
            row["time_control"],
            label=f"schedule row {row_number}.time_control",
        )

        root = _mapping(
            row["root"], label=f"schedule row {row_number}.root"
        )
        try:
            common.require_exact_fields(
                root,
                (
                    "book_line",
                    "fen",
                    "fen_sha256",
                    "order_sha256",
                    "root_identity",
                ),
                label=f"schedule row {row_number}.root",
            )
        except common.MiningArtifactError as error:
            raise E00ScheduleError(str(error)) from error
        book_line = _require_plain_int(
            root["book_line"],
            label=f"schedule row {row_number}.root.book_line",
            minimum=1,
        )
        fen = _require_string(
            root["fen"], label=f"schedule row {row_number}.root.fen"
        )
        if normalize_fen(
            fen, label=f"schedule row {row_number}.root.fen"
        ) != fen:
            raise E00ScheduleError(
                f"schedule row {row_number}.root.fen is not normalized"
            )
        fen_sha256 = _require_sha(
            root["fen_sha256"],
            label=f"schedule row {row_number}.root.fen_sha256",
        )
        if fen_sha256 != _fen_sha256(fen):
            raise E00ScheduleError(
                f"schedule row {row_number}.root.fen_sha256 does not match"
            )
        root_identity = _require_sha(
            root["root_identity"],
            label=f"schedule row {row_number}.root.root_identity",
        )
        if root_identity != _root_identity(fen):
            raise E00ScheduleError(
                f"schedule row {row_number}.root.root_identity does not match"
            )
        order_sha256 = _require_sha(
            root["order_sha256"],
            label=f"schedule row {row_number}.root.order_sha256",
        )
        if order_sha256 != _order_sha256(
            seed=seed,
            book_sha256=book_sha256,
            root_identity=root_identity,
        ):
            raise E00ScheduleError(
                f"schedule row {row_number}.root.order_sha256 does not match"
            )

        white_role = _require_string(
            row["white_role"],
            label=f"schedule row {row_number}.white_role",
        )
        black_role = _require_string(
            row["black_role"],
            label=f"schedule row {row_number}.black_role",
        )
        expected_roles = (
            (CURRENT_ROLE, TEACHER_ROLE)
            if leg == 0
            else (TEACHER_ROLE, CURRENT_ROLE)
        )
        if (white_role, black_role) != expected_roles:
            raise E00ScheduleError(
                f"schedule row {row_number} roles do not match leg {leg}"
            )
        expected_pair_id = _pair_id(
            seed=seed,
            book_sha256=book_sha256,
            pair_ordinal=pair_ordinal,
            time_control=time_control,
            root_identity=root_identity,
        )
        if pair_id != expected_pair_id:
            raise E00ScheduleError(
                f"schedule row {row_number}.pair_id does not match"
            )
        normalized.append(
            {
                "black_role": black_role,
                "book_sha256": book_sha256,
                "leg": leg,
                "pair_id": pair_id,
                "pair_ordinal": pair_ordinal,
                "root": {
                    "book_line": book_line,
                    "fen": fen,
                    "fen_sha256": fen_sha256,
                    "order_sha256": order_sha256,
                    "root_identity": root_identity,
                },
                "schema": SCHEDULE_SCHEMA,
                "seed": seed,
                "time_control": time_control,
                "white_role": white_role,
            }
        )

    first = normalized[0]
    expected_seed = first["seed"]
    expected_book = first["book_sha256"]
    pairs: dict[int, list[dict[str, object]]] = {}
    pair_ids: set[str] = set()
    root_identities: set[str] = set()
    for row in normalized:
        if row["seed"] != expected_seed:
            raise E00ScheduleError("schedule rows have different seeds")
        if row["book_sha256"] != expected_book:
            raise E00ScheduleError(
                "schedule rows have different opening-book hashes"
            )
        ordinal = int(row["pair_ordinal"])
        pairs.setdefault(ordinal, []).append(row)

    if sorted(pairs) != list(range(1, len(pairs) + 1)):
        raise E00ScheduleError(
            "schedule pair ordinals must be consecutive from 1"
        )
    expected_order: list[dict[str, object]] = []
    for ordinal in sorted(pairs):
        legs = pairs[ordinal]
        if len(legs) != 2 or {row["leg"] for row in legs} != {0, 1}:
            raise E00ScheduleError(
                f"schedule pair {ordinal} must contain exactly legs 0 and 1"
            )
        legs.sort(key=lambda row: int(row["leg"]))
        left, right = legs
        invariant_fields = (
            "pair_id",
            "pair_ordinal",
            "root",
            "seed",
            "book_sha256",
            "time_control",
        )
        if any(left[field] != right[field] for field in invariant_fields):
            raise E00ScheduleError(
                f"schedule pair {ordinal} legs do not bind the same root"
            )
        pair_id = str(left["pair_id"])
        root_identity = str(
            _mapping(left["root"], label="root")["root_identity"]
        )
        if pair_id in pair_ids:
            raise E00ScheduleError("schedule contains a duplicate pair_id")
        if root_identity in root_identities:
            raise E00ScheduleError(
                "schedule reuses a root across time-control strata"
            )
        pair_ids.add(pair_id)
        root_identities.add(root_identity)
        expected_order.extend(legs)
    if normalized != expected_order:
        raise E00ScheduleError(
            "schedule rows must be ordered by pair_ordinal then leg"
        )
    return tuple(normalized)


def _load_canonical_json(path: Path) -> tuple[Mapping[str, object], str, int]:
    requested = Path(path).absolute()
    payload = common.read_stable_file_bytes(
        requested, label="E00 schedule receipt"
    )
    if payload.startswith(b"\xef\xbb\xbf") or b"\r" in payload:
        raise E00ScheduleError("schedule receipt is not canonical UTF-8 JSON")
    try:
        text = payload.decode("utf-8", errors="strict")
        value = json.loads(text)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise E00ScheduleError("schedule receipt is invalid JSON") from error
    if not isinstance(value, Mapping):
        raise E00ScheduleError("schedule receipt must be an object")
    if common.canonical_json_bytes(value) != payload:
        raise E00ScheduleError("schedule receipt is not canonical JSON")
    return value, common.sha256_bytes(payload), len(payload)


def _validate_receipt(
    receipt: Mapping[str, object],
    *,
    rows: Sequence[Mapping[str, object]],
    schedule_sha256: str,
    schedule_size_bytes: int,
) -> None:
    try:
        common.require_exact_fields(
            receipt,
            (
                "book",
                "counts",
                "derivation",
                "schedule",
                "schedule_schema",
                "schema",
                "seed",
                "selected_root_identities",
                "time_controls",
            ),
            label="schedule receipt",
        )
    except common.MiningArtifactError as error:
        raise E00ScheduleError(str(error)) from error
    if receipt["schema"] != RECEIPT_SCHEMA:
        raise E00ScheduleError(
            f"schedule receipt.schema must be {RECEIPT_SCHEMA}"
        )
    if receipt["schedule_schema"] != SCHEDULE_SCHEMA:
        raise E00ScheduleError(
            "schedule receipt.schedule_schema does not match"
        )
    validated_rows = validate_schedule(rows)
    first = validated_rows[0]
    if receipt["seed"] != first["seed"]:
        raise E00ScheduleError("schedule receipt seed does not match")

    book = _mapping(receipt["book"], label="schedule receipt.book")
    try:
        common.require_exact_fields(
            book,
            ("root_count", "sha256", "size_bytes"),
            label="schedule receipt.book",
        )
    except common.MiningArtifactError as error:
        raise E00ScheduleError(str(error)) from error
    book_sha256 = _require_sha(
        book["sha256"], label="schedule receipt.book.sha256"
    )
    if book_sha256 != first["book_sha256"]:
        raise E00ScheduleError("schedule receipt book hash does not match")
    book_root_count = _require_plain_int(
        book["root_count"],
        label="schedule receipt.book.root_count",
        minimum=1,
    )
    _require_plain_int(
        book["size_bytes"],
        label="schedule receipt.book.size_bytes",
        minimum=1,
    )

    schedule = _mapping(
        receipt["schedule"], label="schedule receipt.schedule"
    )
    try:
        common.require_exact_fields(
            schedule,
            ("row_count", "sha256", "size_bytes"),
            label="schedule receipt.schedule",
        )
    except common.MiningArtifactError as error:
        raise E00ScheduleError(str(error)) from error
    if (
        _require_sha(
            schedule["sha256"],
            label="schedule receipt.schedule.sha256",
        )
        != schedule_sha256
    ):
        raise E00ScheduleError("schedule receipt schedule hash does not match")
    if (
        _require_plain_int(
            schedule["size_bytes"],
            label="schedule receipt.schedule.size_bytes",
            minimum=1,
        )
        != schedule_size_bytes
    ):
        raise E00ScheduleError("schedule receipt schedule size does not match")
    if (
        _require_plain_int(
            schedule["row_count"],
            label="schedule receipt.schedule.row_count",
            minimum=1,
        )
        != len(validated_rows)
    ):
        raise E00ScheduleError(
            "schedule receipt schedule row count does not match"
        )

    derivation = _mapping(
        receipt["derivation"], label="schedule receipt.derivation"
    )
    try:
        common.require_exact_fields(
            derivation,
            ("algorithm", "expression"),
            label="schedule receipt.derivation",
        )
    except common.MiningArtifactError as error:
        raise E00ScheduleError(str(error)) from error
    if derivation != {
        "algorithm": ORDER_DERIVATION,
        "expression": (
            "SHA256(schedule_schema || seed || book_sha256 || root_identity)"
        ),
    }:
        raise E00ScheduleError("schedule receipt derivation does not match")

    raw_controls = receipt["time_controls"]
    if not isinstance(raw_controls, list) or not raw_controls:
        raise E00ScheduleError(
            "schedule receipt.time_controls must be a non-empty array"
        )
    controls: list[dict[str, object]] = []
    control_names: set[str] = set()
    for index, raw_control in enumerate(raw_controls, start=1):
        mapping = _mapping(
            raw_control,
            label=f"schedule receipt.time_controls[{index}]",
        )
        try:
            common.require_exact_fields(
                mapping,
                ("name", "base_ms", "increment_ms", "pairs"),
                label=f"schedule receipt.time_controls[{index}]",
            )
        except common.MiningArtifactError as error:
            raise E00ScheduleError(str(error)) from error
        schedule_control = _validate_time_control_wire(
            {
                "name": mapping["name"],
                "base_ms": mapping["base_ms"],
                "increment_ms": mapping["increment_ms"],
            },
            label=f"schedule receipt.time_controls[{index}]",
        )
        pairs = _require_plain_int(
            mapping["pairs"],
            label=f"schedule receipt.time_controls[{index}].pairs",
            minimum=1,
        )
        if str(schedule_control["name"]) in control_names:
            raise E00ScheduleError(
                "schedule receipt has duplicate time-control names"
            )
        control_names.add(str(schedule_control["name"]))
        controls.append({**schedule_control, "pairs": pairs})

    observed_controls: list[dict[str, object]] = []
    observed_by_name: dict[str, list[str]] = {}
    previous_name: str | None = None
    for row in validated_rows[::2]:
        control = _mapping(row["time_control"], label="schedule time control")
        name = str(control["name"])
        root = _mapping(row["root"], label="schedule root")
        if name != previous_name:
            if name in observed_by_name:
                raise E00ScheduleError(
                    "schedule time-control strata must be contiguous"
                )
            observed_controls.append({**dict(control), "pairs": 0})
            observed_by_name[name] = []
            previous_name = name
        observed_controls[-1]["pairs"] = (
            int(observed_controls[-1]["pairs"]) + 1
        )
        observed_by_name[name].append(str(root["root_identity"]))
    if controls != observed_controls:
        raise E00ScheduleError(
            "schedule receipt time controls do not match schedule"
        )

    selected = _mapping(
        receipt["selected_root_identities"],
        label="schedule receipt.selected_root_identities",
    )
    if set(selected) != set(observed_by_name):
        raise E00ScheduleError(
            "schedule receipt selected-root strata do not match"
        )
    for name, expected_identities in observed_by_name.items():
        raw_identities = selected[name]
        if not isinstance(raw_identities, list):
            raise E00ScheduleError(
                "schedule receipt selected roots must be arrays"
            )
        identities = [
            _require_sha(
                identity,
                label=(
                    "schedule receipt.selected_root_identities"
                    f".{name}"
                ),
            )
            for identity in raw_identities
        ]
        if identities != expected_identities:
            raise E00ScheduleError(
                f"schedule receipt selected roots for {name} do not match"
            )

    counts = _mapping(receipt["counts"], label="schedule receipt.counts")
    try:
        common.require_exact_fields(
            counts,
            ("book_roots", "games", "pairs", "strata"),
            label="schedule receipt.counts",
        )
    except common.MiningArtifactError as error:
        raise E00ScheduleError(str(error)) from error
    expected_counts = {
        "book_roots": book_root_count,
        "games": len(validated_rows),
        "pairs": len(validated_rows) // 2,
        "strata": len(controls),
    }
    for field, expected in expected_counts.items():
        observed = _require_plain_int(
            counts[field],
            label=f"schedule receipt.counts.{field}",
            minimum=1,
        )
        if observed != expected:
            raise E00ScheduleError(
                f"schedule receipt count {field} does not match"
            )
    if book_root_count < expected_counts["pairs"]:
        raise E00ScheduleError(
            "schedule receipt book root count is smaller than selected roots"
        )


def load_schedule(
    schedule_path: Path,
    receipt_path: Path | None = None,
) -> ScheduleSnapshot:
    """Load and semantically validate the schedule used by an E00 runner.

    Pass ``receipt_path`` for any scientific/result-bearing execution.  It
    authenticates the exact JSONL bytes and proves that the schedule builder
    published its commit marker.
    """

    snapshot = common.load_jsonl_snapshot(schedule_path)
    rows = validate_schedule(snapshot.rows)
    receipt: Mapping[str, object] | None = None
    receipt_sha256: str | None = None
    if receipt_path is not None:
        receipt, receipt_sha256, _receipt_size = _load_canonical_json(
            receipt_path
        )
        _validate_receipt(
            receipt,
            rows=rows,
            schedule_sha256=snapshot.sha256,
            schedule_size_bytes=snapshot.size_bytes,
        )
    return ScheduleSnapshot(
        path=snapshot.path,
        rows=rows,
        sha256=snapshot.sha256,
        size_bytes=snapshot.size_bytes,
        receipt=receipt,
        receipt_sha256=receipt_sha256,
    )


def build_schedule_file(
    book_path: Path,
    *,
    seed: str,
    time_controls: Sequence[TimeControl],
    output_path: Path,
    receipt_path: Path,
) -> ScheduleSummary:
    """Publish one complete schedule and its commit-marker receipt."""

    book_path = Path(book_path).absolute()
    output_path = Path(output_path).absolute()
    receipt_path = Path(receipt_path).absolute()
    if output_path == receipt_path:
        raise E00ScheduleError("schedule and receipt paths must differ")
    if output_path.exists():
        raise FileExistsError(
            f"refusing to overwrite mining artifact: {output_path}"
        )
    if receipt_path.exists():
        raise FileExistsError(
            f"refusing to overwrite mining artifact: {receipt_path}"
        )
    payload = common.read_stable_file_bytes(
        book_path, label="E00 opening book"
    )
    roots, book_sha256 = parse_book_snapshot(payload, seed=seed)
    rows, receipt = build_schedule(
        roots,
        seed=seed,
        book_sha256=book_sha256,
        time_controls=time_controls,
    )
    schedule_payload = b"".join(
        common.canonical_json_bytes(row) for row in rows
    )
    schedule_sha256 = common.sha256_bytes(schedule_payload)
    receipt["book"]["size_bytes"] = len(payload)
    receipt["schedule"].update(
        {
            "row_count": len(rows),
            "sha256": schedule_sha256,
            "size_bytes": len(schedule_payload),
        }
    )
    receipt_payload = common.canonical_json_bytes(receipt)
    try:
        prepared_receipt = json.loads(
            receipt_payload.decode("utf-8", errors="strict")
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise E00ScheduleError(
            "prepared schedule receipt is not valid JSON"
        ) from error
    if (
        not isinstance(prepared_receipt, dict)
        or prepared_receipt != receipt
        or common.canonical_json_bytes(prepared_receipt) != receipt_payload
    ):
        raise E00ScheduleError(
            "prepared schedule receipt is not canonical"
        )
    validated_rows = validate_schedule(rows)
    _validate_receipt(
        prepared_receipt,
        rows=validated_rows,
        schedule_sha256=schedule_sha256,
        schedule_size_bytes=len(schedule_payload),
    )
    summary = ScheduleSummary(
        book_sha256=book_sha256,
        book_root_count=len(roots),
        pair_count=len(rows) // 2,
        game_count=len(rows),
        schedule_sha256=schedule_sha256,
        schedule_size_bytes=len(schedule_payload),
        receipt_sha256=common.sha256_bytes(receipt_payload),
        receipt_size_bytes=len(receipt_payload),
    )

    common.write_new_bytes(output_path, schedule_payload)
    # Reopen and semantically verify the result-bearing schedule before its
    # commit marker exists.
    loaded = load_schedule(output_path)
    if (
        loaded.rows != validated_rows
        or loaded.sha256 != schedule_sha256
        or loaded.size_bytes != len(schedule_payload)
        or common.read_stable_file_bytes(
            book_path, label="postflight E00 opening book"
        )
        != payload
    ):
        raise E00ScheduleError(
            "published schedule or opening book changed before commit"
        )
    _validate_receipt(
        prepared_receipt,
        rows=loaded.rows,
        schedule_sha256=loaded.sha256,
        schedule_size_bytes=loaded.size_bytes,
    )
    # Atomic create-new publication is the final fallible operation.
    common.write_new_bytes(receipt_path, receipt_payload)
    return summary


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Build the deterministic Atomic E00-SRC paired schedule."
    )
    parser.add_argument("--book", required=True, type=Path)
    parser.add_argument("--seed", required=True)
    parser.add_argument(
        "--tc",
        action="append",
        required=True,
        metavar="NAME:BASE_MS:INC_MS:PAIRS",
        help="repeat once per disjoint time-control stratum",
    )
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--receipt", required=True, type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        controls = [
            parse_time_control_spec(specification)
            for specification in args.tc
        ]
        summary = build_schedule_file(
            args.book,
            seed=args.seed,
            time_controls=controls,
            output_path=args.output,
            receipt_path=args.receipt,
        )
    except (E00ScheduleError, FileExistsError, OSError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    sys.stdout.buffer.write(
        common.canonical_json_bytes(
            {
                "book_root_count": summary.book_root_count,
                "book_sha256": summary.book_sha256,
                "game_count": summary.game_count,
                "pair_count": summary.pair_count,
                "receipt_sha256": summary.receipt_sha256,
                "receipt_size_bytes": summary.receipt_size_bytes,
                "schedule_sha256": summary.schedule_sha256,
                "schedule_size_bytes": summary.schedule_size_bytes,
            }
        )
    )
    return 0


__all__ = [
    "BookRoot",
    "CURRENT_ROLE",
    "E00ScheduleError",
    "ORDER_DERIVATION",
    "RECEIPT_SCHEMA",
    "SCHEDULE_SCHEMA",
    "ScheduleSnapshot",
    "ScheduleSummary",
    "TEACHER_ROLE",
    "TimeControl",
    "build_schedule",
    "build_schedule_file",
    "load_schedule",
    "normalize_fen",
    "parse_book_snapshot",
    "parse_time_control_spec",
    "validate_schedule",
]


if __name__ == "__main__":
    raise SystemExit(main())
