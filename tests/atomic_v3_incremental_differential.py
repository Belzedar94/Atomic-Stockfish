#!/usr/bin/env python3
"""Independent cross-language differential for AtomicNNUEV3 H9.3i-a.

The C++ runner owns only the observed sequence.  Python authenticates the
frozen H9.3h fixture, reconstructs every immutable snapshot from FEN, and uses
the independent H9.3i reference for HM transitions and complete scalar
composition.  Stack/source selection is modeled here from the public event
actions so no C++ feature, move, or accumulator implementation is reused.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path
import subprocess
import sys
from typing import Dict, Mapping, Optional, Sequence, Tuple


ROOT = Path(__file__).resolve().parents[1]
PYTHON_DIR = ROOT / "tests" / "python"
sys.path.insert(0, str(PYTHON_DIR))

import atomic_v3_capture_pair_reference as cp  # noqa: E402
import atomic_v3_full_refresh_reference as full_refresh  # noqa: E402
import atomic_v3_incremental_reference as incremental  # noqa: E402
import atomic_v3_scalar_reference as scalar  # noqa: E402


SEQUENCE_EVENTS = 39
SQ_NONE = 64

EXPECTED_SEQUENCE = (
    ("quiet-root", "reset_root", 0),
    ("quiet-child", "push_eval", 1),
    ("quiet-restored", "pop_eval", 0),
    ("lazy-root", "reset_root", 0),
    ("lazy-leaf", "lazy_push_eval", 4),
    ("lazy-undo", "undo_eval", 3),
    ("lazy-branch", "branch_eval", 4),
    ("lazy-branch-undo", "branch_pop_eval", 3),
    ("lazy-root-restored", "multi_pop_eval", 0),
    ("relation-blocker-root", "reset_root", 0),
    ("relation-unblocked", "push_eval", 1),
    ("relation-reblocked", "pop_eval", 0),
    ("king-mirror-root", "reset_root", 0),
    ("king-mirror-cross", "push_eval", 1),
    ("king-mirror-restored", "pop_eval", 0),
    ("bucket-root", "reset_root", 0),
    ("bucket-transition", "push_eval", 1),
    ("bucket-restored", "pop_eval", 0),
    ("en-passant-root", "reset_root", 0),
    ("en-passant-child", "push_eval", 1),
    ("en-passant-restored", "pop_eval", 0),
    ("promotion-root", "reset_root", 0),
    ("promotion-child", "push_eval", 1),
    ("promotion-restored", "pop_eval", 0),
    ("atomic-explosion-root", "reset_root", 0),
    ("atomic-explosion-child", "push_eval", 1),
    ("atomic-explosion-restored", "pop_eval", 0),
    ("null-ep-parent", "reset_root", 0),
    ("null-ep-child", "do_null_eval_no_push", 0),
    ("null-ep-restored", "undo_null_eval_no_pop", 0),
    ("failure-root", "reset_root", 0),
    ("fault-after-white", "injected_failure", 0),
    ("fault-after-white-restored", "post_failure_eval", 0),
    ("fault-before-composition", "injected_failure", 0),
    ("fault-before-composition-restored", "post_failure_eval", 0),
    ("failure-missing-black-king", "feature_failure", 0),
    ("failure-feature-restored", "post_failure_eval", 0),
    ("failure-network-identity", "network_mismatch", 0),
    ("failure-network-restored", "post_failure_eval", 0),
)

EXPECTED_MOVES = (
    "-",
    "e2e4",
    "e2e4",
    "-",
    "e2e4,e7e5,g1f3,b8c6",
    "b8c6",
    "g8f6",
    "g8f6",
    "-",
    "-",
    "a2b4",
    "a2b4",
    "-",
    "d1e1",
    "d1e1",
    "-",
    "a2a7",
    "a2a7",
    "-",
    "e5d6",
    "e5d6",
    "-",
    "a7a8q",
    "a7a8q",
    "-",
    "d3d4",
    "d3d4",
    "-",
    "null",
    "null",
    "-",
    "-",
    "-",
    "-",
    "-",
    "-",
    "-",
    "-",
    "-",
)

SYNTHETIC_FENS = {
    # The runner deliberately labels its malformed immutable snapshot rather
    # than asking Position to accept it.  Freeze the independent FEN here.
    "failure-missing-black-king": "8/8/8/8/8/8/8/K7 w - - 0 1",
}

FEN_PIECES = {
    "P": (cp.WHITE, cp.PAWN, 1),
    "N": (cp.WHITE, cp.KNIGHT, 2),
    "B": (cp.WHITE, cp.BISHOP, 3),
    "R": (cp.WHITE, cp.ROOK, 4),
    "Q": (cp.WHITE, cp.QUEEN, 5),
    "K": (cp.WHITE, cp.KING, 6),
    "p": (cp.BLACK, cp.PAWN, 9),
    "n": (cp.BLACK, cp.KNIGHT, 10),
    "b": (cp.BLACK, cp.BISHOP, 11),
    "r": (cp.BLACK, cp.ROOK, 12),
    "q": (cp.BLACK, cp.QUEEN, 13),
    "k": (cp.BLACK, cp.KING, 14),
}
PIECE_CODES = {(color, kind): code for color, kind, code in FEN_PIECES.values()}

SUCCESS_STATUS_KEYS = {
    "fresh.code": 0,
    "fresh.feature_error": 0,
    "fresh.numeric_error": 0,
    "incremental.error": 0,
    "incremental.feature_error": 0,
    "incremental.scalar_code": 0,
    "incremental.scalar_feature_error": 0,
    "incremental.scalar_numeric_error": 0,
    "comparison.exact": 1,
}

FAILURE_STATUS = {
    "injected_failure": {
        **SUCCESS_STATUS_KEYS,
        "incremental.error": 7,
        "incremental.error_text": "private incremental transactional fault was injected",
    },
    "feature_failure": {
        **SUCCESS_STATUS_KEYS,
        "fresh.code": 1,
        "fresh.feature_error": 4,
        "incremental.error": 2,
        "incremental.feature_error": 4,
        "incremental.error_text": "full-refresh feature oracle rejected the snapshot",
    },
    "network_mismatch": {
        **SUCCESS_STATUS_KEYS,
        "incremental.error": 1,
        "incremental.error_text": "incremental stack is bound to a different network object",
    },
}


class DifferentialFailure(RuntimeError):
    pass


@dataclass(frozen=True)
class DumpEvent:
    index: int
    fields: Mapping[str, str]

    @property
    def label(self) -> str:
        return self.fields["label"]

    @property
    def action(self) -> str:
        return self.fields["action"]


@dataclass
class ReferenceFrame:
    states: Dict[str, Optional[incremental.HmPerspectiveState]]
    snapshot: Optional[Tuple[Tuple[int, ...], int, int]] = None

    @classmethod
    def empty(cls) -> "ReferenceFrame":
        return cls({cp.WHITE: None, cp.BLACK: None})


@dataclass(frozen=True)
class ExpectedUpdate:
    transition: incremental.HmTransition
    source: str
    source_ply: int
    source_distance: int
    removed_rows: int
    added_rows: int


def _expected_keys() -> set[str]:
    keys = {
        "label",
        "action",
        "move",
        "fen",
        "chess960",
        "snapshot.side_to_move",
        "snapshot.ep_square",
        "snapshot.ep_name",
        "snapshot.board",
        "fresh.code",
        "fresh.feature_error",
        "fresh.numeric_error",
        "incremental.error",
        "incremental.error_text",
        "incremental.feature_error",
        "incremental.scalar_code",
        "incremental.scalar_feature_error",
        "incremental.scalar_numeric_error",
        "comparison.exact",
        "incremental.ply",
        "incremental.same_frame_snapshot_mismatch",
        "incremental.ep_square_mismatch",
        "incremental.previous_ep_square",
        "incremental.previous_ep_name",
        "incremental.current_ep_square",
        "incremental.current_ep_name",
        "incremental.previous_side_to_move",
        "incremental.current_side_to_move",
        "incremental.counters.hm_refreshes",
        "incremental.counters.hm_deltas",
        "incremental.counters.hm_reuses",
        "incremental.counters.relation_refreshes",
        "incremental.counters.snapshot_mismatches",
        "incremental.counters.ep_square_mismatches",
        "dirty.present",
        "dirty.pc",
        "dirty.from",
        "dirty.from_name",
        "dirty.to",
        "dirty.to_name",
        "dirty.requires_refresh",
        "dirty.remove_square",
        "dirty.remove_piece",
        "dirty.add_square",
        "dirty.add_piece",
        "dirty.atomic_blast_size",
        "dirty.atomic_blast_pieces",
        "dirty.atomic_blast_squares",
        "incremental.scalar.side_to_move",
        "incremental.scalar.network_bucket",
        "incremental.scalar.transformed",
        "incremental.scalar.fc0",
        "incremental.scalar.fc0_squared",
        "incremental.scalar.fc0_clipped",
        "incremental.scalar.fc1",
        "incremental.scalar.fc1_squared",
        "incremental.scalar.fc1_clipped",
        "incremental.scalar.fc2",
        "incremental.scalar.psqt_difference",
        "incremental.scalar.psqt_value",
        "incremental.scalar.raw_output",
        "incremental.scalar.scaled_output",
        "incremental.scalar.positional_value",
    }
    orientation = {
        "perspective",
        "own_king",
        "oriented_own_king",
        "vertical_xor",
        "horizontal_xor",
        "king_bucket",
    }
    for side in ("white", "black"):
        update = f"incremental.{side}.hm_update"
        keys.update(
            {
                f"{update}.source",
                f"{update}.source_ply",
                f"{update}.source_distance",
                f"{update}.removed_rows",
                f"{update}.added_rows",
                f"incremental.{side}.hm_only.accumulator",
                f"incremental.{side}.hm_only.psqt",
                f"incremental.scalar.{side}.perspective",
                f"incremental.scalar.{side}.accumulator",
                f"incremental.scalar.{side}.psqt",
            }
        )
        for slice_name in ("hm", "capture_pair", "king_blast_ep", "blast_ring"):
            base = f"incremental.scalar.{side}.{slice_name}"
            keys.update({f"{base}.size", f"{base}.rows"})
            keys.update(f"{base}.orientation.{name}" for name in orientation)
        keys.add(f"incremental.scalar.{side}.hm.network_bucket")
    return keys


EXPECTED_KEYS = _expected_keys()


def _parse_dump(output: str) -> Tuple[Tuple[DumpEvent, ...], int]:
    lines = output.splitlines()
    events = []
    cursor = 0
    while cursor < len(lines) and lines[cursor] == "record=incremental_event":
        cursor += 1
        if cursor >= len(lines) or not lines[cursor].startswith("event="):
            raise DifferentialFailure("C++ sequence lost its event header")
        index = int(lines[cursor].split("=", 1)[1])
        if index != len(events):
            raise DifferentialFailure(
                f"C++ event numbering is noncanonical: {index} != {len(events)}"
            )
        cursor += 1
        fields: Dict[str, str] = {}
        end_marker = f"end_event={index}"
        while cursor < len(lines) and lines[cursor] != end_marker:
            key, separator, value = lines[cursor].partition("=")
            if not separator or not key or key in fields:
                raise DifferentialFailure(
                    f"event {index} emitted malformed/duplicate field: {lines[cursor]!r}"
                )
            fields[key] = value
            cursor += 1
        if cursor >= len(lines):
            raise DifferentialFailure(f"event {index} is truncated")
        cursor += 1
        if set(fields) != EXPECTED_KEYS:
            raise DifferentialFailure(
                f"event {index} field inventory differs: "
                f"missing={sorted(EXPECTED_KEYS - set(fields))} "
                f"extra={sorted(set(fields) - EXPECTED_KEYS)}"
            )
        events.append(DumpEvent(index, fields))

    if cursor >= len(lines) or not lines[cursor].startswith("sequence_events="):
        trailing = lines[cursor : cursor + 3]
        raise DifferentialFailure(f"C++ sequence count is missing; trailing={trailing!r}")
    count = int(lines[cursor].split("=", 1)[1])
    cursor += 1
    if cursor != len(lines):
        raise DifferentialFailure(f"C++ sequence has trailing output: {lines[cursor:]!r}")
    if count != len(events):
        raise DifferentialFailure(f"C++ sequence count differs: {count} != {len(events)}")
    return tuple(events), count


def _integer(event: DumpEvent, key: str) -> int:
    value = event.fields[key]
    try:
        parsed = int(value)
    except ValueError as error:
        raise DifferentialFailure(
            f"event {event.index} {event.label} {key} is not an integer: {value!r}"
        ) from error
    if str(parsed) != value:
        raise DifferentialFailure(
            f"event {event.index} {event.label} {key} is noncanonical: {value!r}"
        )
    return parsed


def _array(event: DumpEvent, key: str) -> Tuple[int, ...]:
    value = event.fields[key]
    if not value:
        return ()
    result = []
    for item in value.split(","):
        try:
            parsed = int(item)
        except ValueError as error:
            raise DifferentialFailure(
                f"event {event.index} {event.label} {key} has invalid CSV: {value!r}"
            ) from error
        if str(parsed) != item:
            raise DifferentialFailure(
                f"event {event.index} {event.label} {key} has noncanonical CSV"
            )
        result.append(parsed)
    return tuple(result)


def _first_difference(actual: object, expected: object) -> str:
    if isinstance(actual, tuple) and isinstance(expected, tuple):
        if len(actual) != len(expected):
            return f"length {len(actual)} != {len(expected)}"
        for index, (left, right) in enumerate(zip(actual, expected)):
            if left != right:
                return f"index {index}: {left!r} != {right!r}"
    return f"{actual!r} != {expected!r}"


def _expect_int(event: DumpEvent, key: str, expected: int) -> None:
    actual = _integer(event, key)
    if actual != expected:
        raise DifferentialFailure(
            f"event {event.index} {event.label} {key}: {actual} != {expected}"
        )


def _expect_text(event: DumpEvent, key: str, expected: str) -> None:
    actual = event.fields[key]
    if actual != expected:
        raise DifferentialFailure(
            f"event {event.index} {event.label} {key}: {actual!r} != {expected!r}"
        )


def _expect_array(event: DumpEvent, key: str, expected: Sequence[int]) -> None:
    actual = _array(event, key)
    frozen = tuple(expected)
    if actual != frozen:
        raise DifferentialFailure(
            f"event {event.index} {event.label} {key} differs at "
            f"{_first_difference(actual, frozen)}"
        )


def _unchecked_position(
    pieces: Tuple[cp.Piece, ...],
    side_to_move: str,
    ep_square: Optional[int],
    atomic960: bool,
    castling_rights: str,
) -> cp.CapturePosition:
    value = object.__new__(cp.CapturePosition)
    object.__setattr__(value, "pieces", pieces)
    object.__setattr__(value, "side_to_move", side_to_move)
    object.__setattr__(value, "ep_square", ep_square)
    object.__setattr__(value, "atomic960", atomic960)
    object.__setattr__(value, "castling_rights", castling_rights)
    return value


def _parse_fen(
    fen: str, *, chess960: bool, context: str, allow_invalid_material: bool = False
) -> cp.CapturePosition:
    fields = fen.split(" ")
    if len(fields) != 6 or any(not field for field in fields):
        raise DifferentialFailure(f"{context}: FEN must contain six single-spaced fields")
    placement, active, castling, ep_text, halfmove, fullmove = fields
    if active not in {"w", "b"}:
        raise DifferentialFailure(f"{context}: invalid active color {active!r}")
    if not halfmove.isdigit() or not fullmove.isdigit() or int(fullmove) < 1:
        raise DifferentialFailure(f"{context}: invalid FEN clocks")
    if len(placement.split("/")) != 8:
        raise DifferentialFailure(f"{context}: placement must contain eight ranks")

    pieces = []
    for encoded_rank, rank_text in enumerate(placement.split("/")):
        file_index = 0
        rank_index = 7 - encoded_rank
        if not rank_text:
            raise DifferentialFailure(f"{context}: empty placement rank")
        for token in rank_text:
            if token in "12345678":
                file_index += int(token)
            elif token in FEN_PIECES:
                if file_index >= 8:
                    raise DifferentialFailure(f"{context}: placement rank overflow")
                color, kind, _ = FEN_PIECES[token]
                pieces.append(cp.Piece(color, kind, rank_index * 8 + file_index))
                file_index += 1
            else:
                raise DifferentialFailure(f"{context}: unsupported FEN token {token!r}")
            if file_index > 8:
                raise DifferentialFailure(f"{context}: placement rank overflow")
        if file_index != 8:
            raise DifferentialFailure(
                f"{context}: rank {8 - encoded_rank} expands to {file_index} squares"
            )

    side_to_move = cp.WHITE if active == "w" else cp.BLACK
    try:
        ep_square = None if ep_text == "-" else cp.square(ep_text)
    except ValueError as error:
        raise DifferentialFailure(f"{context}: invalid EP square {ep_text!r}") from error
    material = tuple(pieces)
    if allow_invalid_material:
        return _unchecked_position(
            material, side_to_move, ep_square, chess960, castling
        )
    try:
        return cp.CapturePosition(
            material,
            side_to_move=side_to_move,
            ep_square=ep_square,
            atomic960=chess960,
            castling_rights=castling,
        )
    except ValueError as error:
        raise DifferentialFailure(f"{context}: FEN is outside V3: {error}") from error


def _board(position: cp.CapturePosition) -> Tuple[int, ...]:
    result = [0] * 64
    for piece in position.pieces:
        result[piece.square] = PIECE_CODES[(piece.color, piece.kind)]
    return tuple(result)


def _side_index(color: str) -> int:
    return 0 if color == cp.WHITE else 1


def _ep_index(position: cp.CapturePosition) -> int:
    return SQ_NONE if position.ep_square is None else position.ep_square


def _ep_name(square: int) -> str:
    return "-" if square == SQ_NONE else cp.square_name(square)


def _snapshot(position: cp.CapturePosition) -> Tuple[Tuple[int, ...], int, int]:
    return _board(position), _side_index(position.side_to_move), _ep_index(position)


def _position_for_event(event: DumpEvent) -> cp.CapturePosition:
    chess960 = _integer(event, "chess960")
    if chess960 not in (0, 1):
        raise DifferentialFailure(f"event {event.index} has invalid chess960 flag")
    dumped_fen = event.fields["fen"]
    if event.label in SYNTHETIC_FENS:
        if dumped_fen != "snapshot":
            raise DifferentialFailure(
                f"event {event.index} malformed snapshot label unexpectedly has FEN"
            )
        fen = SYNTHETIC_FENS[event.label]
        invalid = True
    else:
        fen = dumped_fen
        invalid = False
    return _parse_fen(
        fen,
        chess960=bool(chess960),
        context=f"event {event.index} {event.label}",
        allow_invalid_material=invalid,
    )


def _verify_snapshot(event: DumpEvent, position: cp.CapturePosition) -> None:
    board, side, ep_square = _snapshot(position)
    _expect_array(event, "snapshot.board", board)
    _expect_int(event, "snapshot.side_to_move", side)
    _expect_int(event, "snapshot.ep_square", ep_square)
    _expect_text(event, "snapshot.ep_name", _ep_name(ep_square))


def _prepare_stack(
    event: DumpEvent, expected_ply: int, frames: list[ReferenceFrame]
) -> list[ReferenceFrame]:
    action = event.action
    current_ply = len(frames) - 1
    if action == "reset_root":
        if expected_ply != 0:
            raise DifferentialFailure(f"event {event.index} reset root has nonzero ply")
        return [ReferenceFrame.empty()]
    if not frames:
        raise DifferentialFailure(f"event {event.index} has no reference stack")

    if action in {"push_eval", "branch_eval"}:
        if expected_ply != current_ply + 1:
            raise DifferentialFailure(f"event {event.index} single push depth differs")
        frames.append(ReferenceFrame.empty())
    elif action == "lazy_push_eval":
        if expected_ply <= current_ply:
            raise DifferentialFailure(f"event {event.index} lazy push did not advance")
        frames.extend(ReferenceFrame.empty() for _ in range(expected_ply - current_ply))
    elif action in {"pop_eval", "undo_eval", "branch_pop_eval"}:
        if current_ply == 0 or expected_ply != current_ply - 1:
            raise DifferentialFailure(f"event {event.index} single pop depth differs")
        frames.pop()
    elif action == "multi_pop_eval":
        if not 0 <= expected_ply < current_ply:
            raise DifferentialFailure(f"event {event.index} multi-pop depth differs")
        del frames[expected_ply + 1 :]
    elif action not in {
        "do_null_eval_no_push",
        "undo_null_eval_no_pop",
        "injected_failure",
        "post_failure_eval",
        "feature_failure",
        "network_mismatch",
    }:
        raise DifferentialFailure(f"event {event.index} has unknown action {action!r}")

    if len(frames) - 1 != expected_ply:
        raise DifferentialFailure(
            f"event {event.index} reference ply {len(frames) - 1} != {expected_ply}"
        )
    return frames


def _orientation_equal(
    left: cp.Orientation, right: cp.Orientation
) -> bool:
    return left == right


def _expected_update(
    network: scalar.SparseNetwork,
    frames: Sequence[ReferenceFrame],
    perspective: str,
    emission: full_refresh.FullRefreshEmission,
) -> ExpectedUpdate:
    current_ply = len(frames) - 1
    rows = incremental.canonical_hm_rows(emission)
    current = frames[-1].states[perspective]
    if (
        current is not None
        and _orientation_equal(current.orientation, emission.orientation)
        and current.rows == rows
    ):
        transition = incremental.transition_hm(
            network, emission, perspective, current
        )
        return ExpectedUpdate(
            transition, "same_frame_reuse", current_ply, 0, 0, 0
        )

    for source_ply in range(current_ply, -1, -1):
        source = frames[source_ply].states[perspective]
        if source is not None and _orientation_equal(
            source.orientation, emission.orientation
        ):
            transition = incremental.transition_hm(
                network, emission, perspective, source
            )
            if transition.rebuilt:
                raise DifferentialFailure("H9.3i reference rebuilt a same-orientation source")
            return ExpectedUpdate(
                transition,
                "stack_delta",
                source_ply,
                current_ply - source_ply,
                len(transition.removed),
                len(transition.added),
            )

    transition = incremental.transition_hm(network, emission, perspective, None)
    if not transition.rebuilt:
        raise DifferentialFailure("H9.3i reference failed to rebuild without a source")
    return ExpectedUpdate(transition, "full_refresh", current_ply, 0, 0, 0)


def _own_king(position: cp.CapturePosition, perspective: str) -> int:
    kings = [
        piece.square
        for piece in position.pieces
        if piece.color == perspective and piece.kind == cp.KING
    ]
    if len(kings) != 1:
        raise DifferentialFailure("successful V3 event does not have exactly one own king")
    return kings[0]


def _verify_orientation(
    event: DumpEvent,
    base: str,
    position: cp.CapturePosition,
    perspective: str,
    orientation: cp.Orientation,
) -> None:
    own_king = _own_king(position, perspective)
    oriented = orientation.oriented_own_king
    king_bucket = (7 - oriented // 8) * 4 + (7 - oriented % 8)
    expected = {
        "perspective": _side_index(perspective),
        "own_king": own_king,
        "oriented_own_king": oriented,
        "vertical_xor": orientation.vertical_xor,
        "horizontal_xor": orientation.horizontal_xor,
        "king_bucket": king_bucket,
    }
    for name, value in expected.items():
        _expect_int(event, f"{base}.orientation.{name}", value)


def _verify_scalar(
    event: DumpEvent,
    position: cp.CapturePosition,
    emissions: Mapping[str, full_refresh.FullRefreshEmission],
    result: Mapping[str, object],
) -> None:
    integer_keys = (
        "side_to_move",
        "network_bucket",
        "psqt_difference",
        "psqt_value",
        "raw_output",
        "scaled_output",
        "positional_value",
    )
    array_keys = (
        "transformed",
        "fc0",
        "fc0_squared",
        "fc0_clipped",
        "fc1",
        "fc1_squared",
        "fc1_clipped",
        "fc2",
    )
    for key in integer_keys:
        _expect_int(event, f"incremental.scalar.{key}", int(result[key]))
    for key in array_keys:
        _expect_array(
            event, f"incremental.scalar.{key}", result[key]  # type: ignore[arg-type]
        )

    for perspective, side in ((cp.WHITE, "white"), (cp.BLACK, "black")):
        emission = emissions[perspective]
        prefix = f"incremental.scalar.{side}"
        _expect_int(event, f"{prefix}.perspective", _side_index(perspective))
        indices = emission.physical_indices()
        for slice_name, result_name in (
            ("hm", "hm"),
            ("capture_pair", "capture_pair"),
            ("king_blast_ep", "king_blast_ep"),
            ("blast_ring", "blast_ring"),
        ):
            base = f"{prefix}.{slice_name}"
            rows = indices[result_name]
            _verify_orientation(
                event, base, position, perspective, emission.orientation
            )
            _expect_int(event, f"{base}.size", len(rows))
            _expect_array(event, f"{base}.rows", rows)
        _expect_int(event, f"{prefix}.hm.network_bucket", emission.network_bucket)
        _expect_array(event, f"{prefix}.accumulator", result[f"{side}.accumulator"])
        _expect_array(event, f"{prefix}.psqt", result[f"{side}.psqt"])


def _verify_updates_and_counters(
    event: DumpEvent,
    updates: Mapping[str, ExpectedUpdate],
    previous_snapshot: Optional[Tuple[Tuple[int, ...], int, int]],
    current_snapshot: Tuple[Tuple[int, ...], int, int],
) -> None:
    source_counts = {"full_refresh": 0, "stack_delta": 0, "same_frame_reuse": 0}
    current_ply = _integer(event, "incremental.ply")
    for perspective, side in ((cp.WHITE, "white"), (cp.BLACK, "black")):
        update = updates[perspective]
        base = f"incremental.{side}.hm_update"
        _expect_text(event, f"{base}.source", update.source)
        _expect_int(event, f"{base}.source_ply", update.source_ply)
        _expect_int(event, f"{base}.source_distance", update.source_distance)
        _expect_int(event, f"{base}.removed_rows", update.removed_rows)
        _expect_int(event, f"{base}.added_rows", update.added_rows)
        _expect_array(
            event,
            f"incremental.{side}.hm_only.accumulator",
            update.transition.state.accumulator,
        )
        _expect_array(
            event,
            f"incremental.{side}.hm_only.psqt",
            update.transition.state.psqt,
        )
        if update.source_ply + update.source_distance != current_ply:
            raise DifferentialFailure(
                f"event {event.index} {side} HM source depth is inconsistent"
            )
        source_counts[update.source] += 1

    _expect_int(
        event, "incremental.counters.hm_refreshes", source_counts["full_refresh"]
    )
    _expect_int(event, "incremental.counters.hm_deltas", source_counts["stack_delta"])
    _expect_int(
        event, "incremental.counters.hm_reuses", source_counts["same_frame_reuse"]
    )
    _expect_int(event, "incremental.counters.relation_refreshes", 2)

    if previous_snapshot is None:
        previous_ep = SQ_NONE
        previous_side = 0
        snapshot_mismatch = False
        ep_mismatch = False
    else:
        previous_ep = previous_snapshot[2]
        previous_side = previous_snapshot[1]
        snapshot_mismatch = previous_snapshot != current_snapshot
        ep_mismatch = previous_ep != current_snapshot[2]
    _expect_int(event, "incremental.same_frame_snapshot_mismatch", int(snapshot_mismatch))
    _expect_int(event, "incremental.ep_square_mismatch", int(ep_mismatch))
    _expect_int(event, "incremental.previous_ep_square", previous_ep)
    _expect_text(event, "incremental.previous_ep_name", _ep_name(previous_ep))
    _expect_int(event, "incremental.current_ep_square", current_snapshot[2])
    _expect_text(event, "incremental.current_ep_name", _ep_name(current_snapshot[2]))
    _expect_int(event, "incremental.previous_side_to_move", previous_side)
    _expect_int(event, "incremental.current_side_to_move", current_snapshot[1])
    _expect_int(event, "incremental.counters.snapshot_mismatches", int(snapshot_mismatch))
    _expect_int(event, "incremental.counters.ep_square_mismatches", int(ep_mismatch))


def _verify_dirty(event: DumpEvent) -> None:
    present = _integer(event, "dirty.present")
    if present not in (0, 1):
        raise DifferentialFailure(f"event {event.index} dirty.present is not boolean")
    scalar_fields = (
        "pc",
        "from",
        "to",
        "requires_refresh",
        "remove_square",
        "remove_piece",
        "add_square",
        "add_piece",
        "atomic_blast_size",
    )
    values = {name: _integer(event, f"dirty.{name}") for name in scalar_fields}
    for square_key, name_key in (("from", "from_name"), ("to", "to_name")):
        square = values[square_key]
        if not 0 <= square <= SQ_NONE:
            raise DifferentialFailure(f"event {event.index} dirty square escaped board")
        _expect_text(event, f"dirty.{name_key}", _ep_name(square))
    for square_key in ("remove_square", "add_square"):
        if not 0 <= values[square_key] <= SQ_NONE:
            raise DifferentialFailure(f"event {event.index} dirty square escaped board")
    blast_pieces = _array(event, "dirty.atomic_blast_pieces")
    blast_squares = _array(event, "dirty.atomic_blast_squares")
    if len(blast_pieces) != values["atomic_blast_size"] or len(blast_squares) != len(
        blast_pieces
    ):
        raise DifferentialFailure(f"event {event.index} dirty blast vectors differ")
    if any(piece not in PIECE_CODES.values() for piece in blast_pieces):
        raise DifferentialFailure(f"event {event.index} dirty blast has invalid piece")
    if any(not 0 <= square < SQ_NONE for square in blast_squares):
        raise DifferentialFailure(f"event {event.index} dirty blast has invalid square")

    if not present:
        expected = {
            "pc": 0,
            "from": SQ_NONE,
            "to": SQ_NONE,
            "requires_refresh": 0,
            "remove_square": SQ_NONE,
            "remove_piece": 0,
            "add_square": SQ_NONE,
            "add_piece": 0,
            "atomic_blast_size": 0,
        }
        if values != expected or blast_pieces or blast_squares:
            raise DifferentialFailure(f"event {event.index} absent DirtyPiece is not clear")
    elif event.action not in {"push_eval", "lazy_push_eval", "branch_eval"}:
        raise DifferentialFailure(f"event {event.index} unexpected DirtyPiece action")

    if event.label == "en-passant-child" and values["atomic_blast_size"] == 0:
        raise DifferentialFailure("en-passant child lost its Atomic blast delta")
    if event.label == "promotion-child":
        if values["add_square"] != cp.square("a8") or values["add_piece"] != 5:
            raise DifferentialFailure("promotion child lost its promoted queen delta")
    if event.label == "atomic-explosion-child" and values["atomic_blast_size"] < 3:
        raise DifferentialFailure("Atomic explosion child lost collateral removals")


def _verify_clear_diagnostic(event: DumpEvent) -> None:
    zero_scalar_ints = (
        "side_to_move",
        "network_bucket",
        "psqt_difference",
        "psqt_value",
        "raw_output",
        "scaled_output",
        "positional_value",
    )
    for name in zero_scalar_ints:
        _expect_int(event, f"incremental.scalar.{name}", 0)
    array_lengths = {
        "transformed": 1024,
        "fc0": 32,
        "fc0_squared": 32,
        "fc0_clipped": 32,
        "fc1": 32,
        "fc1_squared": 32,
        "fc1_clipped": 32,
        "fc2": 1,
    }
    for name, length in array_lengths.items():
        _expect_array(event, f"incremental.scalar.{name}", (0,) * length)

    for side in ("white", "black"):
        _expect_text(event, f"incremental.{side}.hm_update.source", "none")
        for name in ("source_ply", "source_distance", "removed_rows", "added_rows"):
            _expect_int(event, f"incremental.{side}.hm_update.{name}", 0)
        _expect_array(event, f"incremental.{side}.hm_only.accumulator", (0,) * 1024)
        _expect_array(event, f"incremental.{side}.hm_only.psqt", (0,) * 8)
        _expect_int(event, f"incremental.scalar.{side}.perspective", 0)
        _expect_array(event, f"incremental.scalar.{side}.accumulator", (0,) * 1024)
        _expect_array(event, f"incremental.scalar.{side}.psqt", (0,) * 8)
        for slice_name in ("hm", "capture_pair", "king_blast_ep", "blast_ring"):
            base = f"incremental.scalar.{side}.{slice_name}"
            _expect_int(event, f"{base}.orientation.perspective", 0)
            _expect_int(event, f"{base}.orientation.own_king", SQ_NONE)
            _expect_int(event, f"{base}.orientation.oriented_own_king", SQ_NONE)
            for name in ("vertical_xor", "horizontal_xor", "king_bucket"):
                _expect_int(event, f"{base}.orientation.{name}", 0)
            _expect_int(event, f"{base}.size", 0)
            _expect_array(event, f"{base}.rows", ())
        _expect_int(event, f"incremental.scalar.{side}.hm.network_bucket", 0)

    for name in (
        "ply",
        "same_frame_snapshot_mismatch",
        "ep_square_mismatch",
        "previous_side_to_move",
        "current_side_to_move",
    ):
        _expect_int(event, f"incremental.{name}", 0)
    for name in ("previous_ep", "current_ep"):
        _expect_int(event, f"incremental.{name}_square", SQ_NONE)
        _expect_text(event, f"incremental.{name}_name", "-")
    for name in (
        "hm_refreshes",
        "hm_deltas",
        "hm_reuses",
        "relation_refreshes",
        "snapshot_mismatches",
        "ep_square_mismatches",
    ):
        _expect_int(event, f"incremental.counters.{name}", 0)


def _verify_status(event: DumpEvent, success: bool) -> None:
    expected: Mapping[str, object]
    if success:
        expected = {**SUCCESS_STATUS_KEYS, "incremental.error_text": "none"}
    else:
        expected = FAILURE_STATUS[event.action]
    for key, value in expected.items():
        if isinstance(value, int):
            _expect_int(event, key, value)
        else:
            _expect_text(event, key, value)


def _reference_success(
    network: scalar.SparseNetwork,
    event: DumpEvent,
    position: cp.CapturePosition,
    frames: list[ReferenceFrame],
) -> None:
    frame = frames[-1]
    previous_snapshot = frame.snapshot
    emissions = {
        perspective: full_refresh.enumerate_full_refresh(position, perspective)
        for perspective in cp.COLORS
    }
    updates = {
        perspective: _expected_update(
            network, frames, perspective, emissions[perspective]
        )
        for perspective in cp.COLORS
    }
    transitions = {
        perspective: updates[perspective].transition for perspective in cp.COLORS
    }
    # This is the independent H9.3i composition, including relation refresh,
    # transform, dense intermediates, PSQT and final scale.
    result = incremental._compose_result(  # type: ignore[attr-defined]
        network, position, emissions, transitions
    )
    full_result = scalar.evaluate(network, position)
    if result != full_result:
        differing = next(
            key for key in full_result if result.get(key) != full_result[key]
        )
        raise DifferentialFailure(
            f"event {event.index} Python H9.3i/H9.3h differ at {differing}"
        )

    current_snapshot = _snapshot(position)
    _verify_scalar(event, position, emissions, result)
    _verify_updates_and_counters(
        event, updates, previous_snapshot, current_snapshot
    )
    frame.states = {
        perspective: updates[perspective].transition.state
        for perspective in cp.COLORS
    }
    frame.snapshot = current_snapshot


def _reference_failure(
    network: scalar.SparseNetwork,
    event: DumpEvent,
    position: cp.CapturePosition,
) -> None:
    if event.action == "feature_failure":
        try:
            scalar.evaluate(network, position)
        except (StopIteration, ValueError):
            pass
        else:
            raise DifferentialFailure("malformed feature-failure snapshot was accepted")
    else:
        # Fault injection and pointer identity are incremental-only failures;
        # the immutable snapshot itself must remain a valid H9.3h input.
        scalar.evaluate(network, position)
    _verify_clear_diagnostic(event)


def _verify_null_contract(
    events: Sequence[DumpEvent], positions: Sequence[cp.CapturePosition]
) -> None:
    parent, child, restored = events[27:30]
    parent_pos, child_pos, restored_pos = positions[27:30]
    if (parent.label, child.label, restored.label) != (
        "null-ep-parent",
        "null-ep-child",
        "null-ep-restored",
    ):
        raise DifferentialFailure("null sequence labels drifted")
    if _board(parent_pos) != _board(child_pos) or _board(parent_pos) != _board(restored_pos):
        raise DifferentialFailure("null move changed board material")
    if parent_pos.side_to_move == child_pos.side_to_move:
        raise DifferentialFailure("null move did not toggle side to move")
    if parent_pos.ep_square is None or child_pos.ep_square is not None:
        raise DifferentialFailure("null move did not clear a valid EP square")
    if _snapshot(parent_pos) != _snapshot(restored_pos):
        raise DifferentialFailure("null undo did not restore the immutable snapshot")
    for event in (child, restored):
        _expect_int(event, "incremental.ply", 0)
        _expect_int(event, "incremental.same_frame_snapshot_mismatch", 1)
        _expect_int(event, "incremental.ep_square_mismatch", 1)
        for side in ("white", "black"):
            _expect_text(
                event, f"incremental.{side}.hm_update.source", "same_frame_reuse"
            )


def run(oracle: Path, fixture: Path) -> None:
    oracle = oracle.expanduser().resolve()
    fixture = fixture.expanduser().resolve()
    if not oracle.is_file():
        raise DifferentialFailure(f"incremental runner does not exist: {oracle}")
    if not fixture.is_file():
        raise DifferentialFailure(f"AtomicNNUEV3 fixture does not exist: {fixture}")

    # This authenticates exact size/SHA and independently parses every V3
    # tensor before the C++ process is allowed to contribute observations.
    network = scalar.load_frozen_fixture(fixture)
    completed = subprocess.run(
        [str(oracle), str(fixture), "--dump-sequence"],
        text=True,
        capture_output=True,
        check=False,
        timeout=300,
    )
    if completed.returncode != 0:
        raise DifferentialFailure(
            f"C++ incremental sequence failed ({completed.returncode}):\n"
            f"stdout:\n{completed.stdout}\nstderr:\n{completed.stderr}"
        )
    events, sequence_count = _parse_dump(completed.stdout)
    if sequence_count != SEQUENCE_EVENTS or len(events) != SEQUENCE_EVENTS:
        raise DifferentialFailure(
            f"sequence_events differs: published={sequence_count} parsed={len(events)}"
        )
    if len(EXPECTED_SEQUENCE) != SEQUENCE_EVENTS or len(EXPECTED_MOVES) != SEQUENCE_EVENTS:
        raise AssertionError("frozen Python sequence inventory drifted")

    frames: list[ReferenceFrame] = []
    positions = []
    successful = 0
    failures = 0
    for event, (label, action, expected_ply), move in zip(
        events, EXPECTED_SEQUENCE, EXPECTED_MOVES
    ):
        _expect_text(event, "label", label)
        _expect_text(event, "action", action)
        _expect_text(event, "move", move)
        position = _position_for_event(event)
        positions.append(position)
        _verify_snapshot(event, position)
        frames = _prepare_stack(event, expected_ply, frames)
        success = action not in FAILURE_STATUS
        _verify_status(event, success)
        _verify_dirty(event)
        if success:
            _expect_int(event, "incremental.ply", expected_ply)
            _reference_success(network, event, position, frames)
            successful += 1
        else:
            before_states = tuple(frame.states.copy() for frame in frames)
            before_snapshots = tuple(frame.snapshot for frame in frames)
            _reference_failure(network, event, position)
            current_states = tuple(frame.states for frame in frames)
            current_snapshots = tuple(frame.snapshot for frame in frames)
            if before_states != current_states or before_snapshots != current_snapshots:
                raise DifferentialFailure(f"event {event.index} failure mutated Python frames")
            failures += 1

    _verify_null_contract(events, positions)
    if successful != 35 or failures != 4:
        raise DifferentialFailure(
            f"sequence outcome inventory differs: success={successful} failures={failures}"
        )
    print(
        "Atomic V3 incremental differential passed: "
        f"sequence_events={sequence_count} successful={successful} failures={failures} "
        f"fixture_sha256={network.sha256}"
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--oracle", type=Path, required=True)
    parser.add_argument("--fixture", type=Path, required=True)
    args = parser.parse_args()
    run(args.oracle, args.fixture)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
