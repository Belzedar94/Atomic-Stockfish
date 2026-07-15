#!/usr/bin/env python3
"""Strict cross-language differential for AtomicNNUEV3 BlastRing.

The C++ diagnostic executable is treated as a wire protocol.  Every key,
value, field order, feature order, orientation decision and compact row is
checked against the independent Python projection sourced solely from
CapturePair.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
from pathlib import Path
import random
import re
import subprocess
import sys
from dataclasses import dataclass
from typing import Dict, Iterable, List, Mapping, Optional, Sequence, Tuple


REPO_ROOT = Path(__file__).resolve().parents[1]
PYTHON_TESTS = REPO_ROOT / "tests" / "python"
if str(PYTHON_TESTS) not in sys.path:
    sys.path.insert(0, str(PYTHON_TESTS))

import atomic_v3_blast_ring_reference as reference  # noqa: E402
import atomic_v3_capture_pair_reference as capture_pair  # noqa: E402


HEADER_KEYS = (
    "record",
    "perspective",
    "side_to_move",
    "ep_square",
    "error",
    "error_code",
    "vertical_xor",
    "horizontal_xor",
    "own_king",
    "oriented_own_king",
    "king_bucket",
    "features",
)
FEATURE_KEYS = (
    "feature",
    "local",
    "physical",
    "actor_relation",
    "collateral_relation",
    "class",
    "offset",
    "pawn_survives",
    "raw_center",
    "oriented_center",
    "raw_collateral",
    "oriented_collateral",
)
HEADER_INTEGER_KEYS = frozenset(HEADER_KEYS[3:4] + HEADER_KEYS[5:])
FEATURE_INTEGER_KEYS = frozenset(
    key
    for key in FEATURE_KEYS
    if key not in {"actor_relation", "collateral_relation", "class", "offset"}
)
CANONICAL_UNSIGNED = re.compile(r"(?:0|[1-9][0-9]*)\Z")
DEFAULT_RANDOM_CASES = 160

# Freeze after the deliberately reviewed first corpus construction.  The
# lightweight --print-corpus-digest mode exists solely for that review step.
DEFAULT_CORPUS_SHA256 = "ed5ef5c5cb6389724253ad9cd7d2d4aaf9f0053fecdb2842f16d0864cf0affa4"

FEN_PIECES = {
    "P": (capture_pair.WHITE, capture_pair.PAWN),
    "N": (capture_pair.WHITE, capture_pair.KNIGHT),
    "B": (capture_pair.WHITE, capture_pair.BISHOP),
    "R": (capture_pair.WHITE, capture_pair.ROOK),
    "Q": (capture_pair.WHITE, capture_pair.QUEEN),
    "K": (capture_pair.WHITE, capture_pair.KING),
    "p": (capture_pair.BLACK, capture_pair.PAWN),
    "n": (capture_pair.BLACK, capture_pair.KNIGHT),
    "b": (capture_pair.BLACK, capture_pair.BISHOP),
    "r": (capture_pair.BLACK, capture_pair.ROOK),
    "q": (capture_pair.BLACK, capture_pair.QUEEN),
    "k": (capture_pair.BLACK, capture_pair.KING),
}
PIECE_TO_FEN = {value: key for key, value in FEN_PIECES.items()}


class DifferentialFailure(RuntimeError):
    """Raised for a wire, corpus, or semantic mismatch."""


@dataclass(frozen=True)
class FenCase:
    name: str
    fen: str
    chess960: bool = False

    def identity(self) -> Mapping[str, object]:
        return {
            "name": self.name,
            "mode": "chess960-fen" if self.chess960 else "fen",
            "fen": self.fen,
        }


@dataclass(frozen=True)
class SnapshotCase:
    name: str
    perspective: str
    side_to_move: str
    ep_text: str
    placement: str
    expected_error: str = "none"
    expected_error_code: int = 0

    def identity(self) -> Mapping[str, object]:
        return {
            "name": self.name,
            "mode": "snapshot",
            "perspective": self.perspective,
            "side_to_move": self.side_to_move,
            "ep": self.ep_text,
            "placement": self.placement,
            "expected_error": self.expected_error,
            "expected_error_code": self.expected_error_code,
        }


@dataclass(frozen=True)
class ParsedGroup:
    header: Mapping[str, str]
    features: Tuple[Mapping[str, str], ...]


def fail(context: str, message: str) -> DifferentialFailure:
    return DifferentialFailure(f"{context}: {message}")


def parse_record(
    line: str, expected_keys: Sequence[str], integer_keys: Iterable[str], context: str
) -> Mapping[str, str]:
    if not line or line.startswith(" ") or line.endswith(" ") or "  " in line:
        raise fail(context, "record must use one ASCII space between key=value tokens")
    pairs: List[Tuple[str, str]] = []
    for token in line.split(" "):
        if token.count("=") != 1:
            raise fail(context, f"token is not exactly one key=value pair: {token!r}")
        key, value = token.split("=", 1)
        if not key or not value:
            raise fail(context, f"empty key or value in token: {token!r}")
        pairs.append((key, value))

    keys = tuple(key for key, _ in pairs)
    if keys != tuple(expected_keys):
        raise fail(
            context,
            f"schema/order mismatch; expected={tuple(expected_keys)!r}, observed={keys!r}",
        )
    if len(set(keys)) != len(keys):
        raise fail(context, "duplicate keys are forbidden")
    result = dict(pairs)
    for key in integer_keys:
        if CANONICAL_UNSIGNED.fullmatch(result[key]) is None:
            raise fail(context, f"{key} is not canonical unsigned decimal: {result[key]!r}")
    return result


def parse_output(output: str, context: str) -> Tuple[ParsedGroup, ...]:
    if not output:
        raise fail(context, "C++ oracle produced empty stdout")
    if "\r" in output:
        raise fail(context, "stdout contains a non-canonical carriage return")
    if not output.endswith("\n") or output.endswith("\n\n"):
        raise fail(context, "stdout must end in exactly one LF")
    body = output[:-1]
    if not body or body.startswith("\n") or "\n\n" in body:
        raise fail(context, "stdout contains an empty record")

    lines = body.split("\n")
    groups: List[ParsedGroup] = []
    cursor = 0
    while cursor < len(lines):
        header = parse_record(
            lines[cursor],
            HEADER_KEYS,
            HEADER_INTEGER_KEYS,
            f"{context}.header[{len(groups)}]",
        )
        cursor += 1
        feature_count = int(header["features"])
        if feature_count > reference.MAX_ACTIVE_FEATURES:
            raise fail(context, f"declared feature count exceeds {reference.MAX_ACTIVE_FEATURES}")
        if header["error"] != "none" and feature_count:
            raise fail(context, "error record must not contain partial features")
        if cursor + feature_count > len(lines):
            raise fail(context, "declared feature count runs beyond stdout")
        features = []
        for feature_index in range(feature_count):
            features.append(
                parse_record(
                    lines[cursor],
                    FEATURE_KEYS,
                    FEATURE_INTEGER_KEYS,
                    f"{context}.group[{len(groups)}].feature[{feature_index}]",
                )
            )
            cursor += 1
        groups.append(ParsedGroup(header, tuple(features)))
    return tuple(groups)


def parse_placement(
    placement: str, context: str, require_kings: bool = True
) -> Tuple[capture_pair.Piece, ...]:
    ranks = placement.split("/")
    if len(ranks) != 8:
        raise fail(context, "placement must contain exactly eight ranks")
    pieces: List[capture_pair.Piece] = []
    for encoded_rank, rank_text in enumerate(ranks):
        if not rank_text:
            raise fail(context, "placement rank must not be empty")
        file_index = 0
        rank_index = 7 - encoded_rank
        for token in rank_text:
            if token in "12345678":
                file_index += int(token)
            elif token in FEN_PIECES:
                if file_index >= 8:
                    raise fail(context, "placement rank contains more than eight squares")
                color, kind = FEN_PIECES[token]
                pieces.append(capture_pair.Piece(color, kind, rank_index * 8 + file_index))
                file_index += 1
            else:
                raise fail(context, f"unsupported placement token {token!r}")
            if file_index > 8:
                raise fail(context, "placement rank contains more than eight squares")
        if file_index != 8:
            raise fail(context, f"rank {8 - encoded_rank} expands to {file_index} squares")
    if require_kings:
        counts = {
            color: sum(
                piece.color == color and piece.kind == capture_pair.KING for piece in pieces
            )
            for color in capture_pair.COLORS
        }
        if counts != {capture_pair.WHITE: 1, capture_pair.BLACK: 1}:
            raise fail(context, f"placement king counts are invalid: {counts!r}")
    return tuple(pieces)


def parse_fen(case: FenCase) -> capture_pair.CapturePosition:
    fields = case.fen.split(" ")
    if len(fields) != 6 or any(not field for field in fields):
        raise fail(case.name, "FEN must contain exactly six single-spaced fields")
    placement, active, castling, ep_text, halfmove, fullmove = fields
    if active not in {"w", "b"}:
        raise fail(case.name, f"invalid active color {active!r}")
    if CANONICAL_UNSIGNED.fullmatch(halfmove) is None:
        raise fail(case.name, f"invalid halfmove clock {halfmove!r}")
    if CANONICAL_UNSIGNED.fullmatch(fullmove) is None or int(fullmove) < 1:
        raise fail(case.name, f"invalid fullmove number {fullmove!r}")
    if not castling:
        raise fail(case.name, "castling field must not be empty")
    try:
        pieces = parse_placement(placement, f"{case.name}.placement")
        ep_square = None if ep_text == "-" else capture_pair.square(ep_text)
        return capture_pair.CapturePosition(
            pieces,
            side_to_move=capture_pair.WHITE if active == "w" else capture_pair.BLACK,
            ep_square=ep_square,
            atomic960=case.chess960,
            castling_rights=castling,
        )
    except capture_pair.CapturePairContractError as exc:
        raise fail(case.name, f"FEN is outside the reference domain: {exc}") from exc


def parse_snapshot(case: SnapshotCase) -> capture_pair.CapturePosition:
    if case.expected_error != "none":
        raise fail(case.name, "error snapshot has no evaluable Python position")
    try:
        pieces = parse_placement(case.placement, f"{case.name}.placement")
        ep_square = None if case.ep_text == "-" else capture_pair.square(case.ep_text)
        return capture_pair.CapturePosition(
            pieces,
            side_to_move=case.side_to_move,
            ep_square=ep_square,
        )
    except capture_pair.CapturePairContractError as exc:
        raise fail(case.name, f"snapshot is outside the reference domain: {exc}") from exc


def expected_group(
    position: capture_pair.CapturePosition, perspective: str
) -> Tuple[Mapping[str, str], Tuple[Mapping[str, str], ...]]:
    kings = {
        color: next(
            piece
            for piece in position.pieces
            if piece.color == color and piece.kind == capture_pair.KING
        )
        for color in capture_pair.COLORS
    }
    orientation = capture_pair.orientation_for(position, perspective)
    oriented_file = orientation.oriented_own_king % 8
    oriented_rank = orientation.oriented_own_king // 8
    king_bucket = (7 - oriented_rank) * 4 + (7 - oriented_file)
    activations = reference.enumerate_blast_ring(position, perspective)
    header = {
        "record": "blast_ring",
        "perspective": perspective.lower(),
        "side_to_move": position.side_to_move.lower(),
        "ep_square": str(64 if position.ep_square is None else position.ep_square),
        "error": "none",
        "error_code": "0",
        "vertical_xor": str(orientation.vertical_xor),
        "horizontal_xor": str(orientation.horizontal_xor),
        "own_king": str(kings[perspective].square),
        "oriented_own_king": str(orientation.oriented_own_king),
        "king_bucket": str(king_bucket),
        "features": str(len(activations)),
    }
    features: List[Mapping[str, str]] = []
    for sequence, activation in enumerate(activations):
        features.append(
            {
                "feature": str(sequence),
                "local": str(activation.local_index),
                "physical": str(activation.physical_index),
                "actor_relation": activation.actor_rel.lower(),
                "collateral_relation": activation.collateral_rel.lower(),
                "class": activation.collateral_class.lower(),
                "offset": activation.offset.lower(),
                "pawn_survives": "1" if activation.pawn_survives else "0",
                "raw_center": str(activation.raw_center),
                "oriented_center": str(activation.oriented_center),
                "raw_collateral": str(activation.raw_collateral),
                "oriented_collateral": str(activation.oriented_collateral),
            }
        )
    return header, tuple(features)


def expected_error_group(case: SnapshotCase) -> Mapping[str, str]:
    ep_square = 64 if case.ep_text == "-" else capture_pair.square(case.ep_text)
    return {
        "record": "blast_ring",
        "perspective": case.perspective.lower(),
        "side_to_move": case.side_to_move.lower(),
        "ep_square": str(ep_square),
        "error": case.expected_error,
        "error_code": str(case.expected_error_code),
        "vertical_xor": "0",
        "horizontal_xor": "0",
        "own_king": "64",
        "oriented_own_king": "64",
        "king_bucket": "0",
        "features": "0",
    }


def compare_record(
    expected: Mapping[str, str], observed: Mapping[str, str], context: str
) -> None:
    for key in expected:
        if observed[key] != expected[key]:
            raise fail(
                context,
                f"{key} mismatch; expected={expected[key]!r}, observed={observed[key]!r}",
            )


def compare_group(
    position: capture_pair.CapturePosition,
    perspective: str,
    observed: ParsedGroup,
    context: str,
) -> int:
    expected_header, expected_features = expected_group(position, perspective)
    compare_record(expected_header, observed.header, f"{context}.header")
    if len(observed.features) != len(expected_features):
        raise fail(
            context,
            f"feature count mismatch; expected={len(expected_features)}, "
            f"observed={len(observed.features)}",
        )
    local_indices = [int(feature["local"]) for feature in observed.features]
    if local_indices != sorted(set(local_indices)):
        raise fail(context, "feature rows must be strictly increasing and unique")
    if len(local_indices) > reference.MAX_ACTIVE_FEATURES:
        raise fail(context, "feature rows exceed the 240-row bound")
    for index, (expected, actual) in enumerate(zip(expected_features, observed.features)):
        compare_record(expected, actual, f"{context}.feature[{index}]")
        local = int(actual["local"])
        physical = int(actual["physical"])
        if not 0 <= local < reference.PHYSICAL_DIMENSIONS:
            raise fail(context, f"feature[{index}] local row is out of range")
        if physical != reference.PHYSICAL_OFFSET + local:
            raise fail(context, f"feature[{index}] physical offset is not exact")
    return len(expected_features)


def invoke(oracle: Path, arguments: Sequence[str], timeout: float, context: str) -> str:
    argv = [str(oracle)] + list(arguments)
    options: Dict[str, object] = {}
    if os.name == "nt":
        options["creationflags"] = subprocess.CREATE_NO_WINDOW
    try:
        completed = subprocess.run(
            argv,
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="strict",
            timeout=timeout,
            check=False,
            **options,
        )
    except (OSError, subprocess.SubprocessError, UnicodeError) as exc:
        raise fail(context, f"could not execute C++ oracle: {exc}") from exc
    if completed.returncode != 0:
        raise fail(
            context,
            f"C++ oracle exited {completed.returncode}; "
            f"stderr={completed.stderr!r}; stdout={completed.stdout!r}",
        )
    if completed.stderr:
        raise fail(context, f"successful C++ oracle wrote stderr: {completed.stderr!r}")
    return completed.stdout


def run_fen_case(oracle: Path, case: FenCase, timeout: float) -> Tuple[int, int]:
    position = parse_fen(case)
    mode = "--chess960-fen" if case.chess960 else "--fen"
    groups = parse_output(invoke(oracle, (mode, case.fen), timeout, case.name), case.name)
    if len(groups) != 2:
        raise fail(case.name, f"FEN mode must emit two groups, observed {len(groups)}")
    total_features = 0
    for group_index, perspective in enumerate(capture_pair.COLORS):
        total_features += compare_group(
            position,
            perspective,
            groups[group_index],
            f"{case.name}.{perspective.lower()}",
        )
    return 2, total_features


def run_snapshot_case(
    oracle: Path, case: SnapshotCase, timeout: float
) -> Tuple[int, int]:
    arguments = (
        "--snapshot",
        case.perspective.lower(),
        case.side_to_move.lower(),
        case.ep_text,
        case.placement,
    )
    groups = parse_output(invoke(oracle, arguments, timeout, case.name), case.name)
    if len(groups) != 1:
        raise fail(case.name, f"snapshot mode must emit one group, observed {len(groups)}")
    if case.expected_error != "none":
        compare_record(expected_error_group(case), groups[0].header, f"{case.name}.header")
        if groups[0].features:
            raise fail(case.name, "error snapshot returned partial features")
        return 1, 0
    position = parse_snapshot(case)
    count = compare_group(position, case.perspective, groups[0], case.name)
    return 1, count


def make_piece(color: str, kind: str, name: str) -> capture_pair.Piece:
    return capture_pair.Piece(color, kind, capture_pair.square(name))


def encode_placement(pieces: Sequence[capture_pair.Piece]) -> str:
    board: Dict[int, str] = {}
    for piece in pieces:
        if piece.square in board:
            raise AssertionError("fixture places two pieces on one square")
        board[piece.square] = PIECE_TO_FEN[(piece.color, piece.kind)]
    ranks = []
    for rank_index in range(7, -1, -1):
        encoded = []
        empty = 0
        for file_index in range(8):
            token = board.get(rank_index * 8 + file_index)
            if token is None:
                empty += 1
            else:
                if empty:
                    encoded.append(str(empty))
                    empty = 0
                encoded.append(token)
        if empty:
            encoded.append(str(empty))
        ranks.append("".join(encoded))
    return "/".join(ranks)


def make_fen_case(
    name: str,
    pieces: Sequence[capture_pair.Piece],
    side_to_move: str = capture_pair.WHITE,
    castling: str = "-",
    ep_text: str = "-",
    chess960: bool = False,
    halfmove: int = 0,
    fullmove: int = 1,
) -> FenCase:
    active = "w" if side_to_move == capture_pair.WHITE else "b"
    return FenCase(
        name,
        f"{encode_placement(pieces)} {active} {castling} {ep_text} {halfmove} {fullmove}",
        chess960,
    )


def kings(white: str = "h1", black: str = "h8") -> List[capture_pair.Piece]:
    return [
        make_piece(capture_pair.WHITE, capture_pair.KING, white),
        make_piece(capture_pair.BLACK, capture_pair.KING, black),
    ]


def chebyshev_distance(lhs: int, rhs: int) -> int:
    return max(abs(lhs % 8 - rhs % 8), abs(lhs // 8 - rhs // 8))


def knight_origins(center: int) -> Tuple[int, ...]:
    center_file, center_rank = center % 8, center // 8
    return tuple(
        square_index
        for square_index in range(64)
        if (
            abs(square_index % 8 - center_file),
            abs(square_index // 8 - center_rank),
        )
        in {(1, 2), (2, 1)}
    )


def direct_center_case(center: int) -> FenCase:
    origins = knight_origins(center)
    if not origins:
        raise AssertionError("every center must have a knight origin")
    origin = origins[0]
    anchors = (
        capture_pair.square("h1"),
        capture_pair.square("h8"),
        capture_pair.square("g1"),
        capture_pair.square("g8"),
        capture_pair.square("f1"),
        capture_pair.square("f8"),
        capture_pair.square("e1"),
        capture_pair.square("e8"),
        capture_pair.square("h4"),
        capture_pair.square("h5"),
    )
    anchor = next(
        square_index
        for square_index in anchors
        if square_index not in {center, origin}
        and chebyshev_distance(square_index, center) > 1
    )
    collateral = next(
        square_index
        for offset in reference.DIRECTION_ORDER
        for square_index in (reference.directional_square(center, offset),)
        if square_index is not None and square_index not in {origin, anchor}
    )
    return make_fen_case(
        f"direct-center-{center:02d}",
        [
            capture_pair.Piece(capture_pair.WHITE, capture_pair.KING, anchor),
            capture_pair.Piece(capture_pair.BLACK, capture_pair.KING, center),
            capture_pair.Piece(capture_pair.WHITE, capture_pair.KNIGHT, origin),
            capture_pair.Piece(capture_pair.BLACK, capture_pair.BISHOP, collateral),
        ],
    )


def build_curated_fens() -> Tuple[FenCase, ...]:
    white = capture_pair.WHITE
    black = capture_pair.BLACK
    p = make_piece
    cases: List[FenCase] = [
        FenCase(
            "start-position-boundary",
            "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1",
        )
    ]
    cases.extend(direct_center_case(center) for center in range(64))

    for offset in reference.DIRECTION_ORDER:
        collateral = reference.directional_square(capture_pair.square("d4"), offset)
        if collateral is None:
            raise AssertionError("d4 must have all eight adjacent offsets")
        cases.append(
            make_fen_case(
                f"offset-{offset.lower()}",
                [
                    p(white, capture_pair.KING, "h1"),
                    p(black, capture_pair.KING, "h8"),
                    p(white, capture_pair.KNIGHT, "b3"),
                    p(black, capture_pair.PAWN, "d4"),
                    capture_pair.Piece(black, capture_pair.BISHOP, collateral),
                ],
            )
        )

    for collateral_kind in (
        capture_pair.KNIGHT,
        capture_pair.BISHOP,
        capture_pair.ROOK,
        capture_pair.QUEEN,
        capture_pair.PAWN,
    ):
        cases.append(
            make_fen_case(
                f"class-{collateral_kind.lower()}",
                kings()
                + [
                    p(white, capture_pair.KNIGHT, "b3"),
                    p(black, capture_pair.PAWN, "d4"),
                    p(black, collateral_kind, "d5"),
                ],
            )
        )

    for actor_rel in reference.ACTOR_RELATIONS:
        for collateral_rel in reference.COLLATERAL_RELATIONS:
            actor_color = white if actor_rel == reference.OWN else black
            target_color = black if actor_color == white else white
            collateral_color = white if collateral_rel == reference.OWN else black
            cases.append(
                make_fen_case(
                    f"relations-{actor_rel.lower()}-{collateral_rel.lower()}",
                    kings()
                    + [
                        p(actor_color, capture_pair.KNIGHT, "b3"),
                        p(target_color, capture_pair.PAWN, "d4"),
                        p(collateral_color, capture_pair.BISHOP, "d5"),
                    ],
                )
            )

    cases.extend(
        [
            make_fen_case(
                "edge-a1",
                [
                    p(white, capture_pair.KING, "h1"),
                    p(black, capture_pair.KING, "h8"),
                    p(white, capture_pair.KNIGHT, "c2"),
                    p(black, capture_pair.QUEEN, "a1"),
                    p(black, capture_pair.KNIGHT, "a2"),
                    p(black, capture_pair.BISHOP, "b2"),
                    p(black, capture_pair.ROOK, "b1"),
                ],
            ),
            make_fen_case(
                "sole-adjacent-origin",
                kings()
                + [
                    p(white, capture_pair.ROOK, "d3"),
                    p(black, capture_pair.PAWN, "d4"),
                    p(black, capture_pair.KNIGHT, "e4"),
                ],
            ),
            make_fen_case(
                "multiple-adjacent-origins",
                kings()
                + [
                    p(white, capture_pair.ROOK, "d3"),
                    p(white, capture_pair.ROOK, "c4"),
                    p(black, capture_pair.PAWN, "d4"),
                ],
            ),
            make_fen_case(
                "sole-pawn-origin",
                [
                    p(white, capture_pair.KING, "h1"),
                    p(black, capture_pair.KING, "a8"),
                    p(white, capture_pair.PAWN, "d5"),
                    p(black, capture_pair.ROOK, "e6"),
                    p(white, capture_pair.PAWN, "f6"),
                ],
            ),
            make_fen_case(
                "adjacent-kings-separated",
                [
                    p(white, capture_pair.KING, "e4"),
                    p(black, capture_pair.KING, "d5"),
                    p(white, capture_pair.KNIGHT, "b3"),
                    p(black, capture_pair.PAWN, "d4"),
                    p(black, capture_pair.BISHOP, "e5"),
                ],
            ),
            make_fen_case(
                "promotion-pawn-current-type",
                [
                    p(white, capture_pair.KING, "h1"),
                    p(black, capture_pair.KING, "g8"),
                    p(white, capture_pair.PAWN, "g7"),
                    p(black, capture_pair.ROOK, "h8"),
                    p(white, capture_pair.KNIGHT, "h7"),
                ],
            ),
            make_fen_case(
                "promotion-queen-current-type",
                [
                    p(white, capture_pair.KING, "h1"),
                    p(black, capture_pair.KING, "g8"),
                    p(white, capture_pair.QUEEN, "g7"),
                    p(black, capture_pair.ROOK, "h8"),
                    p(white, capture_pair.KNIGHT, "h7"),
                ],
            ),
            make_fen_case(
                "horizontal-mirror-left",
                [
                    p(white, capture_pair.KING, "c1"),
                    p(black, capture_pair.KING, "b7"),
                    p(white, capture_pair.ROOK, "a2"),
                    p(black, capture_pair.PAWN, "a5"),
                    p(black, capture_pair.BISHOP, "b5"),
                    p(white, capture_pair.PAWN, "a6"),
                ],
            ),
            make_fen_case(
                "horizontal-mirror-right",
                [
                    p(white, capture_pair.KING, "f1"),
                    p(black, capture_pair.KING, "g7"),
                    p(white, capture_pair.ROOK, "h2"),
                    p(black, capture_pair.PAWN, "h5"),
                    p(black, capture_pair.BISHOP, "g5"),
                    p(white, capture_pair.PAWN, "h6"),
                ],
            ),
            make_fen_case(
                "color-vertical-original",
                [
                    p(white, capture_pair.KING, "f1"),
                    p(black, capture_pair.KING, "e6"),
                    p(white, capture_pair.KNIGHT, "b3"),
                    p(black, capture_pair.ROOK, "d4"),
                    p(white, capture_pair.BISHOP, "d5"),
                ],
            ),
            make_fen_case(
                "color-vertical-mirrored",
                [
                    p(black, capture_pair.KING, "f8"),
                    p(white, capture_pair.KING, "e3"),
                    p(black, capture_pair.KNIGHT, "b6"),
                    p(white, capture_pair.ROOK, "d5"),
                    p(black, capture_pair.BISHOP, "d4"),
                ],
                side_to_move=black,
            ),
            FenCase(
                "atomic960-castling-white",
                "r1k4r/8/8/3r4/3Qb3/8/8/R1K4R w AHah - 0 1",
                chess960=True,
            ),
            FenCase(
                "atomic960-castling-black",
                "r1k4r/8/8/3r4/3Qb3/8/8/R1K4R b AHah - 23 57",
                chess960=True,
            ),
            make_fen_case(
                "valid-white-ep-one-origin",
                [
                    p(white, capture_pair.KING, "h1"),
                    p(black, capture_pair.KING, "a8"),
                    p(white, capture_pair.PAWN, "d5"),
                    p(black, capture_pair.PAWN, "e5"),
                    p(white, capture_pair.PAWN, "f6"),
                    p(black, capture_pair.KNIGHT, "f7"),
                ],
                ep_text="e6",
            ),
            make_fen_case(
                "valid-white-ep-two-origins",
                [
                    p(white, capture_pair.KING, "h1"),
                    p(black, capture_pair.KING, "a8"),
                    p(white, capture_pair.PAWN, "d5"),
                    p(white, capture_pair.PAWN, "f5"),
                    p(black, capture_pair.PAWN, "e5"),
                ],
                ep_text="e6",
            ),
            make_fen_case(
                "valid-black-ep-two-origins",
                [
                    p(white, capture_pair.KING, "a1"),
                    p(black, capture_pair.KING, "h8"),
                    p(black, capture_pair.PAWN, "d4"),
                    p(black, capture_pair.PAWN, "f4"),
                    p(white, capture_pair.PAWN, "e4"),
                ],
                side_to_move=black,
                ep_text="e3",
            ),
        ]
    )

    # Cover all 14+14 compact CapturePair EP source rows.  BlastRing itself
    # remains a center/collateral tensor and never exposes CP traversal order.
    for center_file in range(8):
        center = chr(ord("a") + center_file) + "6"
        captured = chr(ord("a") + center_file) + "5"
        ep_pieces = kings("h1", "a8") + [p(black, capture_pair.PAWN, captured)]
        for origin_file in (center_file - 1, center_file + 1):
            if 0 <= origin_file < 8:
                ep_pieces.append(
                    p(white, capture_pair.PAWN, chr(ord("a") + origin_file) + "5")
                )
        # An always-adjacent non-pawn gives edge files an observable ring row.
        adjacent_file = center_file + 1 if center_file < 7 else center_file - 1
        ep_pieces.append(
            p(black, capture_pair.KNIGHT, chr(ord("a") + adjacent_file) + "6")
        )
        cases.append(
            make_fen_case(
                f"all-ep-edges-file-{chr(ord('a') + center_file)}",
                ep_pieces,
                ep_text=center,
            )
        )
    return tuple(cases)


def build_random_fens(count: int) -> Tuple[FenCase, ...]:
    rng = random.Random(0xA70C3E40)
    cases = []
    for case_index in range(count):
        occupied = set()
        white_king = rng.randrange(64)
        occupied.add(white_king)
        black_king = rng.choice(
            [
                square_index
                for square_index in range(64)
                if square_index not in occupied
                and chebyshev_distance(square_index, white_king) > 1
            ]
        )
        occupied.add(black_king)
        pieces = [
            capture_pair.Piece(capture_pair.WHITE, capture_pair.KING, white_king),
            capture_pair.Piece(capture_pair.BLACK, capture_pair.KING, black_king),
        ]
        for color in capture_pair.COLORS:
            requested = [
                (capture_pair.PAWN, rng.randint(0, 8)),
                (capture_pair.KNIGHT, rng.randint(0, 2)),
                (capture_pair.BISHOP, rng.randint(0, 2)),
                (capture_pair.ROOK, rng.randint(0, 2)),
                (capture_pair.QUEEN, rng.randint(0, 1)),
            ]
            for kind, amount in requested:
                for _ in range(amount):
                    candidates = [
                        square_index
                        for square_index in range(64)
                        if square_index not in occupied
                        and (kind != capture_pair.PAWN or 1 <= square_index // 8 <= 6)
                    ]
                    if not candidates:
                        raise AssertionError("random corpus ran out of material squares")
                    chosen = rng.choice(candidates)
                    occupied.add(chosen)
                    pieces.append(capture_pair.Piece(color, kind, chosen))
        cases.append(
            make_fen_case(
                f"random-{case_index:04d}",
                pieces,
                side_to_move=rng.choice(capture_pair.COLORS),
                chess960=(case_index % 5 == 0),
                halfmove=rng.randint(0, 99),
                fullmove=rng.randint(1, 200),
            )
        )
    return tuple(cases)


def make_snapshot_cases() -> Tuple[SnapshotCase, ...]:
    white = capture_pair.WHITE
    black = capture_pair.BLACK
    p = make_piece
    definitions: List[Tuple[str, str, str, Sequence[capture_pair.Piece]]] = []
    white_base = [
        p(white, capture_pair.KING, "h1"),
        p(black, capture_pair.KING, "a8"),
        p(white, capture_pair.KNIGHT, "c3"),
        p(black, capture_pair.ROOK, "b5"),
        p(black, capture_pair.BISHOP, "b6"),
    ]
    definitions.extend(
        [
            (
                "malformed-ep-wrong-rank-white",
                white,
                "e3",
                white_base
                + [
                    p(white, capture_pair.PAWN, "d5"),
                    p(black, capture_pair.PAWN, "e5"),
                ],
            ),
            (
                "malformed-ep-occupied-center-white",
                white,
                "e6",
                white_base
                + [
                    p(white, capture_pair.PAWN, "d5"),
                    p(black, capture_pair.PAWN, "e5"),
                    p(black, capture_pair.KNIGHT, "e6"),
                ],
            ),
            (
                "malformed-ep-missing-off-center-white",
                white,
                "e6",
                white_base + [p(white, capture_pair.PAWN, "d5")],
            ),
            (
                "malformed-ep-off-center-nonpawn-white",
                white,
                "e6",
                white_base
                + [
                    p(white, capture_pair.PAWN, "d5"),
                    p(black, capture_pair.ROOK, "e5"),
                ],
            ),
            (
                "malformed-ep-friendly-off-center-white",
                white,
                "e6",
                white_base
                + [
                    p(white, capture_pair.PAWN, "d5"),
                    p(white, capture_pair.PAWN, "e5"),
                ],
            ),
            (
                "malformed-ep-no-attacker-white",
                white,
                "e6",
                white_base + [p(black, capture_pair.PAWN, "e5")],
            ),
            (
                "valid-ep-snapshot-white",
                white,
                "e6",
                white_base
                + [
                    p(white, capture_pair.PAWN, "d5"),
                    p(white, capture_pair.PAWN, "f5"),
                    p(black, capture_pair.PAWN, "e5"),
                ],
            ),
            (
                "no-ep-snapshot-white",
                white,
                "-",
                white_base
                + [
                    p(white, capture_pair.PAWN, "d5"),
                    p(black, capture_pair.PAWN, "e5"),
                ],
            ),
        ]
    )
    black_base = [
        p(white, capture_pair.KING, "a1"),
        p(black, capture_pair.KING, "h8"),
        p(black, capture_pair.KNIGHT, "c6"),
        p(white, capture_pair.ROOK, "b4"),
        p(white, capture_pair.BISHOP, "b3"),
    ]
    definitions.extend(
        [
            (
                "malformed-ep-wrong-rank-black",
                black,
                "e6",
                black_base
                + [
                    p(black, capture_pair.PAWN, "d4"),
                    p(white, capture_pair.PAWN, "e4"),
                ],
            ),
            (
                "malformed-ep-occupied-center-black",
                black,
                "e3",
                black_base
                + [
                    p(black, capture_pair.PAWN, "d4"),
                    p(white, capture_pair.PAWN, "e4"),
                    p(white, capture_pair.KNIGHT, "e3"),
                ],
            ),
            (
                "malformed-ep-missing-off-center-black",
                black,
                "e3",
                black_base + [p(black, capture_pair.PAWN, "d4")],
            ),
            (
                "malformed-ep-off-center-nonpawn-black",
                black,
                "e3",
                black_base
                + [
                    p(black, capture_pair.PAWN, "d4"),
                    p(white, capture_pair.ROOK, "e4"),
                ],
            ),
            (
                "malformed-ep-friendly-off-center-black",
                black,
                "e3",
                black_base
                + [
                    p(black, capture_pair.PAWN, "d4"),
                    p(black, capture_pair.PAWN, "e4"),
                ],
            ),
            (
                "malformed-ep-no-attacker-black",
                black,
                "e3",
                black_base + [p(white, capture_pair.PAWN, "e4")],
            ),
            (
                "valid-ep-snapshot-black",
                black,
                "e3",
                black_base
                + [
                    p(black, capture_pair.PAWN, "d4"),
                    p(black, capture_pair.PAWN, "f4"),
                    p(white, capture_pair.PAWN, "e4"),
                ],
            ),
            (
                "no-ep-snapshot-black",
                black,
                "-",
                black_base
                + [
                    p(black, capture_pair.PAWN, "d4"),
                    p(white, capture_pair.PAWN, "e4"),
                ],
            ),
        ]
    )
    result = []
    for name, side_to_move, ep_text, pieces in definitions:
        placement = encode_placement(pieces)
        for perspective in capture_pair.COLORS:
            result.append(
                SnapshotCase(
                    f"{name}-{perspective.lower()}",
                    perspective,
                    side_to_move,
                    ep_text,
                    placement,
                )
            )

    missing_white = encode_placement(
        [
            p(black, capture_pair.KING, "e8"),
            p(black, capture_pair.KNIGHT, "c6"),
        ]
    )
    missing_black = encode_placement(
        [
            p(white, capture_pair.KING, "e1"),
            p(white, capture_pair.KNIGHT, "c3"),
        ]
    )
    for perspective in capture_pair.COLORS:
        result.append(
            SnapshotCase(
                f"missing-white-king-{perspective.lower()}",
                perspective,
                black,
                "-",
                missing_white,
                "missing_white_king",
                3,
            )
        )
        result.append(
            SnapshotCase(
                f"missing-black-king-{perspective.lower()}",
                perspective,
                white,
                "-",
                missing_black,
                "missing_black_king",
                4,
            )
        )
    return tuple(result)


def corpus_digest(
    fen_cases: Sequence[FenCase], snapshot_cases: Sequence[SnapshotCase]
) -> str:
    identities = [case.identity() for case in fen_cases]
    identities.extend(case.identity() for case in snapshot_cases)
    encoded = json.dumps(
        identities, ensure_ascii=True, sort_keys=True, separators=(",", ":")
    ).encode("ascii")
    return hashlib.sha256(encoded).hexdigest()


def compact_signature(
    position: capture_pair.CapturePosition, perspective: str
) -> Tuple[Tuple[object, ...], ...]:
    return tuple(
        (
            row.local_index,
            row.oriented_center,
            row.actor_rel,
            row.oriented_collateral,
            row.collateral_rel,
            row.offset,
            row.collateral_class,
        )
        for row in reference.enumerate_blast_ring(position, perspective)
    )


def rows_at(
    position: capture_pair.CapturePosition,
    perspective: str,
    center: str,
    actor_rel: Optional[str] = None,
) -> Tuple[reference.BlastRingActivation, ...]:
    raw_center = capture_pair.square(center)
    return tuple(
        row
        for row in reference.enumerate_blast_ring(position, perspective)
        if row.raw_center == raw_center
        and (actor_rel is None or row.actor_rel == actor_rel)
    )


def verify_index_rectangle() -> None:
    first = reference.blast_ring_index(
        0, reference.OWN, reference.OWN, "N", reference.KNIGHT
    )
    last = reference.blast_ring_index(
        63,
        reference.OPP,
        reference.OPP,
        "NW",
        reference.ADJACENT_PAWN_SURVIVES,
    )
    if first != 0 or last != reference.PHYSICAL_DIMENSIONS - 1 or last != 10239:
        raise DifferentialFailure(
            f"BlastRing rectangle endpoints drifted: first={first}, last={last}"
        )
    if reference.PHYSICAL_OFFSET + first != 64844:
        raise DifferentialFailure("BlastRing physical start offset drifted")
    if reference.PHYSICAL_OFFSET + last != 75083:
        raise DifferentialFailure("BlastRing physical end offset drifted")


def verify_corpus_coverage(fen_cases: Sequence[FenCase]) -> None:
    raw_centers = set()
    oriented_centers = set()
    actor_relations = set()
    collateral_relations = set()
    relation_pairs = set()
    offsets = set()
    collateral_classes = set()
    ep_rows = set()
    horizontal_branches = set()
    maximum = 0
    for case in fen_cases:
        position = parse_fen(case)
        for perspective in capture_pair.COLORS:
            orientation = capture_pair.orientation_for(position, perspective)
            horizontal_branches.add(orientation.horizontal_xor)
            ep_rows.update(
                row.local_index
                for row in capture_pair.enumerate_capture_pairs(position, perspective)
                if row.en_passant
            )
            rows = reference.enumerate_blast_ring(position, perspective)
            maximum = max(maximum, len(rows))
            indices = [row.local_index for row in rows]
            if indices != sorted(set(indices)):
                raise DifferentialFailure(f"{case.name}: Python rows are not sorted/unique")
            if len(rows) > reference.MAX_ACTIVE_FEATURES:
                raise DifferentialFailure(
                    f"{case.name}: Python rows exceed {reference.MAX_ACTIVE_FEATURES}"
                )
            for row in rows:
                if not 0 <= row.local_index < reference.PHYSICAL_DIMENSIONS:
                    raise DifferentialFailure(f"{case.name}: local row escaped its tensor")
                if row.physical_index != reference.PHYSICAL_OFFSET + row.local_index:
                    raise DifferentialFailure(f"{case.name}: physical row offset drifted")
                raw_centers.add(row.raw_center)
                oriented_centers.add(row.oriented_center)
                actor_relations.add(row.actor_rel)
                collateral_relations.add(row.collateral_rel)
                relation_pairs.add((row.actor_rel, row.collateral_rel))
                offsets.add(row.offset)
                collateral_classes.add(row.collateral_class)

    all_squares = set(range(64))
    if raw_centers != all_squares:
        raise DifferentialFailure(
            "corpus raw-center coverage mismatch; "
            f"missing={sorted(all_squares - raw_centers)}"
        )
    if oriented_centers != all_squares:
        raise DifferentialFailure(
            "corpus oriented-center coverage mismatch; "
            f"missing={sorted(all_squares - oriented_centers)}"
        )
    if actor_relations != set(reference.ACTOR_RELATIONS):
        raise DifferentialFailure(
            f"corpus actor-relation coverage mismatch: {sorted(actor_relations)!r}"
        )
    if collateral_relations != set(reference.COLLATERAL_RELATIONS):
        raise DifferentialFailure(
            "corpus collateral-relation coverage mismatch: "
            f"{sorted(collateral_relations)!r}"
        )
    expected_relation_pairs = {
        (actor, collateral)
        for actor in reference.ACTOR_RELATIONS
        for collateral in reference.COLLATERAL_RELATIONS
    }
    if relation_pairs != expected_relation_pairs:
        raise DifferentialFailure(
            "corpus relation-pair coverage mismatch; "
            f"missing={sorted(expected_relation_pairs - relation_pairs)!r}"
        )
    if offsets != set(reference.DIRECTION_ORDER):
        raise DifferentialFailure(
            f"corpus offset coverage mismatch: {sorted(offsets)!r}"
        )
    if collateral_classes != set(reference.CLASS_ORDER):
        raise DifferentialFailure(
            "corpus class coverage mismatch; "
            f"missing={sorted(set(reference.CLASS_ORDER) - collateral_classes)!r}"
        )
    expected_ep_rows = set(
        range(capture_pair.NORMAL_DIMENSIONS, capture_pair.PHYSICAL_DIMENSIONS)
    )
    if ep_rows != expected_ep_rows:
        raise DifferentialFailure(
            "corpus CapturePair 28-row EP tail mismatch; "
            f"missing={sorted(expected_ep_rows - ep_rows)}, "
            f"unexpected={sorted(ep_rows - expected_ep_rows)}"
        )
    if horizontal_branches != {0, 7}:
        raise DifferentialFailure(
            f"corpus horizontal orientation branches mismatch: {horizontal_branches!r}"
        )
    if not any(case.chess960 for case in fen_cases):
        raise DifferentialFailure("corpus contains no Atomic960 FEN")
    if maximum <= 0:
        raise DifferentialFailure("corpus emitted no BlastRing rows")


def verify_named_semantics(
    fen_cases: Sequence[FenCase], snapshot_cases: Sequence[SnapshotCase]
) -> None:
    positions = {case.name: parse_fen(case) for case in fen_cases}

    edge = rows_at(positions["edge-a1"], capture_pair.WHITE, "a1", reference.OWN)
    if {row.raw_collateral for row in edge} != {
        capture_pair.square("a2"),
        capture_pair.square("b2"),
        capture_pair.square("b1"),
    } or {row.offset for row in edge} != {"N", "NE", "E"}:
        raise DifferentialFailure("a1 edge geometry wrapped or omitted a valid neighbor")

    direct_king = rows_at(
        positions["direct-center-27"], capture_pair.WHITE, "d4", reference.OWN
    )
    if not direct_king:
        raise DifferentialFailure("direct king target did not project non-king collateral")

    sole = rows_at(
        positions["sole-adjacent-origin"], capture_pair.WHITE, "d4", reference.OWN
    )
    if any(row.raw_collateral == capture_pair.square("d3") for row in sole):
        raise DifferentialFailure("sole adjacent capture origin leaked into collateral")
    if not any(row.raw_collateral == capture_pair.square("e4") for row in sole):
        raise DifferentialFailure("ordinary collateral beside sole origin was lost")

    multiple_position = positions["multiple-adjacent-origins"]
    multiple_candidates = [
        row
        for row in capture_pair.enumerate_capture_pairs(
            multiple_position, capture_pair.WHITE
        )
        if row.raw_to == capture_pair.square("d4") and row.actor_rel == reference.OWN
    ]
    multiple = rows_at(multiple_position, capture_pair.WHITE, "d4", reference.OWN)
    retained_origins = {
        row.raw_collateral
        for row in multiple
        if row.raw_collateral
        in {capture_pair.square("d3"), capture_pair.square("c4")}
    }
    if {row.raw_from for row in multiple_candidates} != {
        capture_pair.square("d3"),
        capture_pair.square("c4"),
    } or retained_origins != {capture_pair.square("d3"), capture_pair.square("c4")}:
        raise DifferentialFailure("multiple adjacent origins were not retained as collateral")

    pawn_origin = rows_at(
        positions["sole-pawn-origin"], capture_pair.WHITE, "e6", reference.OWN
    )
    if any(row.raw_collateral == capture_pair.square("d5") for row in pawn_origin):
        raise DifferentialFailure("sole pawn capture origin was labeled as surviving")
    if not any(
        row.raw_collateral == capture_pair.square("f6")
        and row.collateral_class == reference.ADJACENT_PAWN_SURVIVES
        for row in pawn_origin
    ):
        raise DifferentialFailure("non-origin adjacent pawn survival row is absent")

    adjacent_kings = rows_at(
        positions["adjacent-kings-separated"],
        capture_pair.WHITE,
        "d4",
        reference.OWN,
    )
    if any(
        row.raw_collateral
        in {capture_pair.square("e4"), capture_pair.square("d5")}
        for row in adjacent_kings
    ):
        raise DifferentialFailure("a king leaked from the dedicated king slice")
    if not any(row.raw_collateral == capture_pair.square("e5") for row in adjacent_kings):
        raise DifferentialFailure("non-king collateral beside adjacent kings was lost")

    white_one = positions["valid-white-ep-one-origin"]
    for perspective in capture_pair.COLORS:
        actor_rel = reference.OWN if perspective == capture_pair.WHITE else reference.OPP
        selected = rows_at(white_one, perspective, "e6", actor_rel)
        if any(
            row.raw_collateral
            in {capture_pair.square("d5"), capture_pair.square("e5")}
            for row in selected
        ):
            raise DifferentialFailure("one-origin white EP leaked origin/captured pawn")
        if not any(
            row.raw_collateral == capture_pair.square("f6")
            and row.collateral_class == reference.ADJACENT_PAWN_SURVIVES
            for row in selected
        ) or not any(
            row.raw_collateral == capture_pair.square("f7")
            and row.collateral_class == reference.KNIGHT
            for row in selected
        ):
            raise DifferentialFailure("one-origin white EP lost surviving collateral")

    for name, center, origins, captured, actor_color in (
        (
            "valid-white-ep-two-origins",
            "e6",
            {capture_pair.square("d5"), capture_pair.square("f5")},
            capture_pair.square("e5"),
            capture_pair.WHITE,
        ),
        (
            "valid-black-ep-two-origins",
            "e3",
            {capture_pair.square("d4"), capture_pair.square("f4")},
            capture_pair.square("e4"),
            capture_pair.BLACK,
        ),
    ):
        position = positions[name]
        for perspective in capture_pair.COLORS:
            actor_rel = reference.OWN if perspective == actor_color else reference.OPP
            cp_ep = [
                row
                for row in capture_pair.enumerate_capture_pairs(position, perspective)
                if row.en_passant
            ]
            selected = rows_at(position, perspective, center, actor_rel)
            retained = {
                row.raw_collateral
                for row in selected
                if row.raw_collateral in origins
                and row.collateral_class == reference.ADJACENT_PAWN_SURVIVES
            }
            if {row.raw_from for row in cp_ep} != origins or retained != origins:
                raise DifferentialFailure(f"{name}: two EP origins were not retained")
            if any(row.raw_collateral == captured for row in selected):
                raise DifferentialFailure(f"{name}: off-center captured pawn leaked")

    pawn = positions["promotion-pawn-current-type"]
    queen = positions["promotion-queen-current-type"]
    for perspective in capture_pair.COLORS:
        pawn_rows = tuple(
            (
                row.raw_collateral,
                row.collateral_rel,
                row.offset,
                row.collateral_class,
            )
            for row in rows_at(pawn, perspective, "h8")
        )
        queen_rows = tuple(
            (
                row.raw_collateral,
                row.collateral_rel,
                row.offset,
                row.collateral_class,
            )
            for row in rows_at(queen, perspective, "h8")
        )
        if pawn_rows != queen_rows or not any(
            row[-1] == reference.KNIGHT for row in pawn_rows
        ):
            raise DifferentialFailure("promotion/current actor type changed BlastRing")

    left = positions["horizontal-mirror-left"]
    right = positions["horizontal-mirror-right"]
    for perspective in capture_pair.COLORS:
        if compact_signature(left, perspective) != compact_signature(right, perspective):
            raise DifferentialFailure("horizontal mirror changed joint-oriented signature")

    original = positions["color-vertical-original"]
    color_mirrored = positions["color-vertical-mirrored"]
    if compact_signature(original, capture_pair.WHITE) != compact_signature(
        color_mirrored, capture_pair.BLACK
    ):
        raise DifferentialFailure("color/vertical mirror changed perspective-swapped signature")

    for case in snapshot_cases:
        if case.expected_error != "none":
            if case.expected_error_code not in {3, 4}:
                raise DifferentialFailure(f"{case.name}: unsupported error snapshot code")
            continue
        position = parse_snapshot(case)
        rows = reference.enumerate_blast_ring(position, case.perspective)
        if case.name.startswith("malformed-ep-"):
            baseline = capture_pair.CapturePosition(
                position.pieces,
                side_to_move=position.side_to_move,
                ep_square=None,
            )
            if compact_signature(position, case.perspective) != compact_signature(
                baseline, case.perspective
            ):
                raise DifferentialFailure(
                    f"{case.name}: malformed EP changed the normal projection"
                )
            if not rows:
                raise DifferentialFailure(
                    f"{case.name}: malformed EP discarded the normal projection"
                )
        if case.name.startswith("valid-ep-"):
            ep_candidates = [
                row
                for row in capture_pair.enumerate_capture_pairs(
                    position, case.perspective
                )
                if row.en_passant
            ]
            if len(ep_candidates) != 2:
                raise DifferentialFailure(f"{case.name}: expected two authenticated origins")
            landing = position.ep_square
            if landing is None:
                raise DifferentialFailure(f"{case.name}: valid EP lost its landing square")
            actor_rel = (
                reference.OWN
                if case.perspective == position.side_to_move
                else reference.OPP
            )
            selected = tuple(
                row
                for row in rows
                if row.raw_center == landing and row.actor_rel == actor_rel
            )
            origins = {row.raw_from for row in ep_candidates}
            retained = {
                row.raw_collateral
                for row in selected
                if row.raw_collateral in origins
                and row.collateral_class == reference.ADJACENT_PAWN_SURVIVES
            }
            captured = landing + (
                -8 if position.side_to_move == capture_pair.WHITE else 8
            )
            if retained != origins:
                raise DifferentialFailure(f"{case.name}: valid EP origins were not retained")
            if any(row.raw_collateral == captured for row in selected):
                raise DifferentialFailure(f"{case.name}: captured EP pawn leaked")


def check_python39_grammar() -> None:
    source = Path(__file__).read_text(encoding="utf-8")
    try:
        ast.parse(source, filename=str(Path(__file__)), feature_version=9)
    except SyntaxError as exc:
        raise DifferentialFailure(f"script is not valid Python 3.9 syntax: {exc}") from exc


def build_corpus(random_cases: int) -> Tuple[Tuple[FenCase, ...], Tuple[SnapshotCase, ...]]:
    return build_curated_fens() + build_random_fens(random_cases), make_snapshot_cases()


def validate_corpus(
    fen_cases: Sequence[FenCase], snapshot_cases: Sequence[SnapshotCase]
) -> None:
    verify_index_rectangle()
    verify_corpus_coverage(fen_cases)
    verify_named_semantics(fen_cases, snapshot_cases)


def run(oracle: Path, timeout: float, random_cases: int) -> None:
    check_python39_grammar()
    fen_cases, snapshot_cases = build_corpus(random_cases)
    validate_corpus(fen_cases, snapshot_cases)
    digest = corpus_digest(fen_cases, snapshot_cases)
    if random_cases == DEFAULT_RANDOM_CASES:
        if not DEFAULT_CORPUS_SHA256:
            raise DifferentialFailure(
                "default BlastRing corpus digest has not been frozen; "
                f"review and set DEFAULT_CORPUS_SHA256={digest}"
            )
        if digest != DEFAULT_CORPUS_SHA256:
            raise DifferentialFailure(
                "default BlastRing corpus identity changed; "
                f"expected={DEFAULT_CORPUS_SHA256}, observed={digest}"
            )

    emissions = 0
    features = 0
    for case in fen_cases:
        case_emissions, case_features = run_fen_case(oracle, case, timeout)
        emissions += case_emissions
        features += case_features
    for case in snapshot_cases:
        case_emissions, case_features = run_snapshot_case(oracle, case, timeout)
        emissions += case_emissions
        features += case_features

    malformed = sum(case.name.startswith("malformed-ep-") for case in snapshot_cases)
    errors = sum(case.expected_error != "none" for case in snapshot_cases)
    print(
        "Atomic V3 BlastRing cross-language differential passed: "
        f"{len(fen_cases)} FENs ({sum(case.chess960 for case in fen_cases)} Atomic960), "
        f"{len(snapshot_cases)} snapshots ({malformed} malformed-EP, {errors} errors), "
        f"{emissions} emissions, {features} feature records, "
        f"corpus_sha256={digest}"
    )


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--oracle", type=Path, help="path to the C++ BlastRing CLI")
    parser.add_argument(
        "--timeout", type=float, default=15.0, help="seconds per isolated CLI invocation"
    )
    parser.add_argument(
        "--random-cases",
        type=int,
        default=DEFAULT_RANDOM_CASES,
        help="number of deterministic broad-material FEN cases",
    )
    parser.add_argument(
        "--print-corpus-digest",
        action="store_true",
        help="validate the corpus and print its identity without invoking C++",
    )
    args = parser.parse_args(argv)
    if args.timeout <= 0:
        parser.error("--timeout must be positive")
    if args.random_cases < 0 or args.random_cases > 10_000:
        parser.error("--random-cases must be in 0..10000")
    if args.oracle is None and not args.print_corpus_digest:
        parser.error("--oracle is required unless --print-corpus-digest is used")
    if args.oracle is not None:
        args.oracle = args.oracle.resolve()
        if not args.oracle.is_file():
            parser.error(f"--oracle is not a regular file: {args.oracle}")
    return args


def main() -> int:
    args = parse_args()
    try:
        if args.print_corpus_digest:
            check_python39_grammar()
            fen_cases, snapshot_cases = build_corpus(args.random_cases)
            validate_corpus(fen_cases, snapshot_cases)
            print(corpus_digest(fen_cases, snapshot_cases))
            return 0
        run(args.oracle, args.timeout, args.random_cases)
    except DifferentialFailure as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
