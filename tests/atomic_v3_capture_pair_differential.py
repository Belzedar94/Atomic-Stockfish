#!/usr/bin/env python3
"""Strict cross-language differential for the AtomicNNUEV3 CapturePair slice.

The C++ diagnostic CLI is treated as a wire protocol.  Every key, value, field
order, feature order, orientation decision, piece code, and compact index is
checked against the independent scalar Python reference.
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

import atomic_v3_capture_pair_reference as reference  # noqa: E402


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
    "relation",
    "target",
    "en_passant",
    "actor",
    "captured",
    "raw_from",
    "raw_center",
    "raw_captured",
    "oriented_from",
    "oriented_center",
    "oriented_captured",
    "edge",
    "ep_ordinal",
)
HEADER_INTEGER_KEYS = frozenset(HEADER_KEYS[3:4] + HEADER_KEYS[5:])
FEATURE_INTEGER_KEYS = frozenset(
    key for key in FEATURE_KEYS if key not in {"relation", "target"}
)
CANONICAL_UNSIGNED = re.compile(r"(?:0|[1-9][0-9]*)\Z")
DEFAULT_RANDOM_CASES = 160
DEFAULT_CORPUS_SHA256 = "39ceda2ddda1224671a2efffff556531c67435e3bd5d9dd02dd56b65ab44dc64"

FEN_PIECES = {
    "P": (reference.WHITE, reference.PAWN),
    "N": (reference.WHITE, reference.KNIGHT),
    "B": (reference.WHITE, reference.BISHOP),
    "R": (reference.WHITE, reference.ROOK),
    "Q": (reference.WHITE, reference.QUEEN),
    "K": (reference.WHITE, reference.KING),
    "p": (reference.BLACK, reference.PAWN),
    "n": (reference.BLACK, reference.KNIGHT),
    "b": (reference.BLACK, reference.BISHOP),
    "r": (reference.BLACK, reference.ROOK),
    "q": (reference.BLACK, reference.QUEEN),
    "k": (reference.BLACK, reference.KING),
}
PIECE_TO_FEN = {value: key for key, value in FEN_PIECES.items()}
PIECE_KIND_ORDINAL = {
    reference.PAWN: 1,
    reference.KNIGHT: 2,
    reference.BISHOP: 3,
    reference.ROOK: 4,
    reference.QUEEN: 5,
    reference.KING: 6,
}


class DifferentialFailure(RuntimeError):
    """Raised for any protocol or semantic mismatch."""


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

    def identity(self) -> Mapping[str, object]:
        return {
            "name": self.name,
            "mode": "snapshot",
            "perspective": self.perspective,
            "side_to_move": self.side_to_move,
            "ep": self.ep_text,
            "placement": self.placement,
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
        raise fail(context, "record must use one ASCII space between non-empty key=value tokens")

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

    integer_key_set = set(integer_keys)
    result = dict(pairs)
    for key in integer_key_set:
        value = result[key]
        if CANONICAL_UNSIGNED.fullmatch(value) is None:
            raise fail(context, f"{key} is not a canonical unsigned decimal: {value!r}")
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
            lines[cursor], HEADER_KEYS, HEADER_INTEGER_KEYS, f"{context}.header[{len(groups)}]"
        )
        cursor += 1
        feature_count = int(header["features"])
        if feature_count > reference.MAX_ACTIVE_FEATURES:
            raise fail(context, f"declared feature count exceeds {reference.MAX_ACTIVE_FEATURES}")
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


def parse_placement(placement: str, context: str) -> Tuple[reference.Piece, ...]:
    ranks = placement.split("/")
    if len(ranks) != 8:
        raise fail(context, "placement must contain exactly eight ranks")

    pieces: List[reference.Piece] = []
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
                pieces.append(reference.Piece(color, kind, rank_index * 8 + file_index))
                file_index += 1
            else:
                raise fail(context, f"unsupported placement token {token!r}")
            if file_index > 8:
                raise fail(context, "placement rank contains more than eight squares")
        if file_index != 8:
            raise fail(context, f"rank {8 - encoded_rank} expands to {file_index} squares")
    return tuple(pieces)


def parse_fen(case: FenCase) -> reference.CapturePosition:
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
        ep_square = None if ep_text == "-" else reference.square(ep_text)
        return reference.CapturePosition(
            pieces,
            side_to_move=reference.WHITE if active == "w" else reference.BLACK,
            ep_square=ep_square,
            atomic960=case.chess960,
            castling_rights=castling,
        )
    except reference.CapturePairContractError as exc:
        raise fail(case.name, f"FEN is outside the CapturePair reference domain: {exc}") from exc


def parse_snapshot(case: SnapshotCase) -> reference.CapturePosition:
    if case.perspective not in reference.COLORS:
        raise fail(case.name, f"invalid perspective {case.perspective!r}")
    if case.side_to_move not in reference.COLORS:
        raise fail(case.name, f"invalid side to move {case.side_to_move!r}")
    try:
        pieces = parse_placement(case.placement, f"{case.name}.placement")
        ep_square = None if case.ep_text == "-" else reference.square(case.ep_text)
        return reference.CapturePosition(
            pieces,
            side_to_move=case.side_to_move,
            ep_square=ep_square,
        )
    except reference.CapturePairContractError as exc:
        raise fail(case.name, f"snapshot is outside the CapturePair reference domain: {exc}") from exc


def piece_code(piece: reference.Piece) -> int:
    base = PIECE_KIND_ORDINAL[piece.kind]
    return base if piece.color == reference.WHITE else 8 + base


def expected_group(
    position: reference.CapturePosition, perspective: str
) -> Tuple[Mapping[str, str], Tuple[Mapping[str, str], ...]]:
    occupied = {piece.square: piece for piece in position.pieces}
    own_king = next(
        piece
        for piece in position.pieces
        if piece.color == perspective and piece.kind == reference.KING
    )
    orientation = reference.orientation_for(position, perspective)
    oriented_file = orientation.oriented_own_king % 8
    oriented_rank = orientation.oriented_own_king // 8
    king_bucket = (7 - oriented_rank) * 4 + (7 - oriented_file)
    activations = reference.enumerate_capture_pairs(position, perspective)

    header = {
        "record": "capture_pair",
        "perspective": perspective.lower(),
        "side_to_move": position.side_to_move.lower(),
        "ep_square": str(64 if position.ep_square is None else position.ep_square),
        "error": "none",
        "error_code": "0",
        "vertical_xor": str(orientation.vertical_xor),
        "horizontal_xor": str(orientation.horizontal_xor),
        "own_king": str(own_king.square),
        "oriented_own_king": str(orientation.oriented_own_king),
        "king_bucket": str(king_bucket),
        "features": str(len(activations)),
    }

    features: List[Mapping[str, str]] = []
    for sequence, activation in enumerate(activations):
        actor = occupied.get(activation.raw_from)
        captured = occupied.get(activation.raw_captured)
        if actor is None or captured is None:
            raise DifferentialFailure(
                "Python reference emitted a feature whose actor/captured square is empty"
            )
        if activation.en_passant:
            try:
                ep_ordinal = reference.ep_edges(activation.actor_rel).index(
                    (activation.oriented_from, activation.oriented_to)
                )
            except ValueError as exc:
                raise DifferentialFailure("Python reference emitted a non-canonical EP edge") from exc
            edge_ordinal = activation.edge_ordinal
            target = "en_passant"
        else:
            ep_ordinal = 0
            edge_ordinal = activation.edge_ordinal
            target = activation.target_class.lower()

        features.append(
            {
                "feature": str(sequence),
                "local": str(activation.local_index),
                "physical": str(activation.physical_index),
                "relation": activation.actor_rel.lower(),
                "target": target,
                "en_passant": "1" if activation.en_passant else "0",
                "actor": str(piece_code(actor)),
                "captured": str(piece_code(captured)),
                "raw_from": str(activation.raw_from),
                "raw_center": str(activation.raw_to),
                "raw_captured": str(activation.raw_captured),
                "oriented_from": str(activation.oriented_from),
                "oriented_center": str(activation.oriented_to),
                "oriented_captured": str(activation.oriented_captured),
                "edge": str(edge_ordinal),
                "ep_ordinal": str(ep_ordinal),
            }
        )
    return header, tuple(features)


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
    position: reference.CapturePosition,
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
    for index, (expected, actual) in enumerate(zip(expected_features, observed.features)):
        compare_record(expected, actual, f"{context}.feature[{index}]")
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
    for group_index, perspective in enumerate((reference.WHITE, reference.BLACK)):
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
    position = parse_snapshot(case)
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
    feature_count = compare_group(position, case.perspective, groups[0], case.name)
    return 1, feature_count


def make_piece(color: str, kind: str, name: str) -> reference.Piece:
    return reference.Piece(color, kind, reference.square(name))


def encode_placement(pieces: Sequence[reference.Piece]) -> str:
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
    pieces: Sequence[reference.Piece],
    side_to_move: str = reference.WHITE,
    castling: str = "-",
    ep_text: str = "-",
    chess960: bool = False,
    halfmove: int = 0,
    fullmove: int = 1,
) -> FenCase:
    active = "w" if side_to_move == reference.WHITE else "b"
    fen = (
        f"{encode_placement(pieces)} {active} {castling} {ep_text} "
        f"{halfmove} {fullmove}"
    )
    return FenCase(name, fen, chess960)


def kings(white: str = "h1", black: str = "h8") -> List[reference.Piece]:
    return [
        make_piece(reference.WHITE, reference.KING, white),
        make_piece(reference.BLACK, reference.KING, black),
    ]


def build_curated_fens() -> Tuple[FenCase, ...]:
    white = reference.WHITE
    black = reference.BLACK
    p = make_piece
    cases = [
        FenCase(
            "start-position-boundary",
            "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1",
        ),
        make_fen_case("actor-pawn", kings() + [p(white, reference.PAWN, "e4"), p(black, reference.PAWN, "d5")]),
        make_fen_case("actor-knight", kings() + [p(white, reference.KNIGHT, "c3"), p(black, reference.PAWN, "d5")]),
        make_fen_case("actor-bishop", kings() + [p(white, reference.BISHOP, "b1"), p(black, reference.PAWN, "e4")]),
        make_fen_case("actor-rook", kings() + [p(white, reference.ROOK, "a1"), p(black, reference.PAWN, "a6")]),
        make_fen_case("actor-queen", kings() + [p(white, reference.QUEEN, "d1"), p(black, reference.PAWN, "h5")]),
    ]

    for target_kind in reference.TARGET_CLASSES:
        target_pieces = [p(white, reference.KNIGHT, "c3")]
        if target_kind == reference.KING:
            target_pieces += [p(white, reference.KING, "h1"), p(black, reference.KING, "d5")]
        else:
            target_pieces += kings() + [p(black, target_kind, "d5")]
        cases.append(make_fen_case(f"target-{target_kind.lower()}", target_pieces))

    cases.extend(
        [
            make_fen_case(
                "slider-enemy-blocker",
                kings()
                + [
                    p(white, reference.ROOK, "a1"),
                    p(black, reference.BISHOP, "a4"),
                    p(black, reference.QUEEN, "a6"),
                ],
            ),
            make_fen_case(
                "slider-friendly-blocker",
                kings()
                + [
                    p(white, reference.ROOK, "a1"),
                    p(white, reference.BISHOP, "a4"),
                    p(black, reference.QUEEN, "a6"),
                ],
            ),
            make_fen_case(
                "promoted-material",
                kings()
                + [
                    p(white, reference.QUEEN, "d4"),
                    p(white, reference.QUEEN, "a4"),
                    p(black, reference.ROOK, "d7"),
                    p(black, reference.KNIGHT, "a7"),
                ],
                side_to_move=black,
                halfmove=17,
                fullmove=42,
            ),
            make_fen_case(
                "pinned-pseudocapture",
                [
                    p(white, reference.KING, "e1"),
                    p(black, reference.KING, "h8"),
                    p(white, reference.KNIGHT, "e2"),
                    p(black, reference.ROOK, "e8"),
                    p(black, reference.PAWN, "f4"),
                ],
            ),
            make_fen_case(
                "atomic-self-blast-pseudocapture",
                [
                    p(white, reference.KING, "f3"),
                    p(black, reference.KING, "h8"),
                    p(white, reference.QUEEN, "d4"),
                    p(black, reference.PAWN, "e4"),
                ],
            ),
            make_fen_case(
                "valid-white-ep-two-origins",
                [
                    p(white, reference.KING, "h1"),
                    p(black, reference.KING, "a8"),
                    p(white, reference.PAWN, "d5"),
                    p(white, reference.PAWN, "f5"),
                    p(black, reference.PAWN, "e5"),
                    p(white, reference.KNIGHT, "c3"),
                    p(black, reference.ROOK, "b5"),
                ],
                ep_text="e6",
            ),
            make_fen_case(
                "valid-black-ep-two-origins",
                [
                    p(white, reference.KING, "a1"),
                    p(black, reference.KING, "h8"),
                    p(black, reference.PAWN, "d4"),
                    p(black, reference.PAWN, "f4"),
                    p(white, reference.PAWN, "e4"),
                    p(white, reference.KNIGHT, "c3"),
                    p(black, reference.ROOK, "b5"),
                ],
                side_to_move=black,
                ep_text="e3",
            ),
            make_fen_case(
                "horizontal-branch-left",
                kings("c2", "b7")
                + [p(white, reference.ROOK, "a4"), p(black, reference.ROOK, "a7")],
            ),
            make_fen_case(
                "horizontal-branch-right",
                kings("f2", "g7")
                + [p(white, reference.ROOK, "h4"), p(black, reference.ROOK, "h7")],
                side_to_move=black,
            ),
            FenCase(
                "standard-castling-metadata",
                "r3k2r/8/8/3q4/8/4Q3/8/R3K2R w KQkq - 0 1",
            ),
            FenCase(
                "atomic960-castling-white",
                "r1k4r/8/8/8/3Q4/8/8/R1K4R w AHah - 0 1",
                chess960=True,
            ),
            FenCase(
                "atomic960-castling-black",
                "r1k4r/8/8/8/3Q4/8/8/R1K4R b AHah - 23 57",
                chess960=True,
            ),
        ]
    )

    # Exercise every raw own-king square in both C++ perspective groups.  The
    # opposite-corner king keeps each synthetic position unambiguous and valid.
    for raw_king in range(64):
        cases.append(
            make_fen_case(
                f"orientation-all-king-squares-{raw_king:02d}",
                [
                    reference.Piece(white, reference.KING, raw_king),
                    reference.Piece(black, reference.KING, raw_king ^ 63),
                ],
            )
        )

    # Eight authenticated white-to-move EP positions cover all fourteen EP
    # edges.  Because each FEN emits both perspectives, they also cover both
    # actor-relation tails (OWN and OPP), i.e. all 28 compact EP rows.
    for center_file in range(8):
        center_name = chr(ord("a") + center_file) + "6"
        captured_name = chr(ord("a") + center_file) + "5"
        ep_pieces = kings("h1", "a8") + [
            p(black, reference.PAWN, captured_name)
        ]
        for origin_file in (center_file - 1, center_file + 1):
            if 0 <= origin_file < 8:
                ep_pieces.append(
                    p(
                        white,
                        reference.PAWN,
                        chr(ord("a") + origin_file) + "5",
                    )
                )
        cases.append(
            make_fen_case(
                f"all-ep-edges-file-{chr(ord('a') + center_file)}",
                ep_pieces,
                ep_text=center_name,
            )
        )
    return tuple(cases)


def chebyshev_distance(lhs: int, rhs: int) -> int:
    return max(abs(lhs % 8 - rhs % 8), abs(lhs // 8 - rhs // 8))


def build_random_fens(count: int) -> Tuple[FenCase, ...]:
    rng = random.Random(0xA70C3C0DE)
    cases = []
    for case_index in range(count):
        occupied = set()
        white_king = rng.randrange(64)
        occupied.add(white_king)
        black_king_candidates = [
            square_index
            for square_index in range(64)
            if square_index not in occupied
            and chebyshev_distance(square_index, white_king) > 1
        ]
        black_king = rng.choice(black_king_candidates)
        occupied.add(black_king)
        pieces = [
            reference.Piece(reference.WHITE, reference.KING, white_king),
            reference.Piece(reference.BLACK, reference.KING, black_king),
        ]

        for color in reference.COLORS:
            requested = [
                (reference.PAWN, rng.randint(0, 8)),
                (reference.KNIGHT, rng.randint(0, 2)),
                (reference.BISHOP, rng.randint(0, 2)),
                (reference.ROOK, rng.randint(0, 2)),
                (reference.QUEEN, rng.randint(0, 1)),
            ]
            for kind, amount in requested:
                for _ in range(amount):
                    candidates = [
                        square_index
                        for square_index in range(64)
                        if square_index not in occupied
                        and (kind != reference.PAWN or 1 <= square_index // 8 <= 6)
                    ]
                    if not candidates:
                        raise AssertionError("deterministic random corpus ran out of legal squares")
                    chosen = rng.choice(candidates)
                    occupied.add(chosen)
                    pieces.append(reference.Piece(color, kind, chosen))

        side_to_move = rng.choice(reference.COLORS)
        cases.append(
            make_fen_case(
                f"random-{case_index:04d}",
                pieces,
                side_to_move=side_to_move,
                chess960=(case_index % 5 == 0),
                halfmove=rng.randint(0, 99),
                fullmove=rng.randint(1, 200),
            )
        )
    return tuple(cases)


def make_snapshot_cases() -> Tuple[SnapshotCase, ...]:
    white = reference.WHITE
    black = reference.BLACK
    p = make_piece
    base = kings("h1", "a8") + [
        p(white, reference.KNIGHT, "c3"),
        p(black, reference.ROOK, "b5"),
    ]
    definitions = [
        ("malformed-ep-wrong-rank-white", white, "e5", base + [p(black, reference.PAWN, "e4"), p(white, reference.PAWN, "d4")]),
        ("malformed-ep-occupied-center-white", white, "e6", base + [p(black, reference.BISHOP, "e6"), p(black, reference.PAWN, "e5"), p(white, reference.PAWN, "d5")]),
        ("malformed-ep-missing-off-center-pawn-white", white, "e6", base + [p(white, reference.PAWN, "d5")]),
        ("malformed-ep-off-center-nonpawn-white", white, "e6", base + [p(black, reference.ROOK, "e5"), p(white, reference.PAWN, "d5")]),
        ("malformed-ep-friendly-off-center-pawn-white", white, "e6", base + [p(white, reference.PAWN, "e5"), p(white, reference.PAWN, "d5")]),
        ("malformed-ep-no-side-to-move-attacker-white", white, "e6", base + [p(black, reference.PAWN, "e5")]),
        ("valid-ep-snapshot-white", white, "e6", base + [p(black, reference.PAWN, "e5"), p(white, reference.PAWN, "d5"), p(white, reference.PAWN, "f5")]),
        ("no-ep-snapshot-white", white, "-", base + [p(black, reference.PAWN, "e5"), p(white, reference.PAWN, "d5")]),
        ("malformed-ep-wrong-rank-black", black, "e4", base + [p(white, reference.PAWN, "e5"), p(black, reference.PAWN, "d5")]),
        ("malformed-ep-occupied-center-black", black, "e3", base + [p(white, reference.BISHOP, "e3"), p(white, reference.PAWN, "e4"), p(black, reference.PAWN, "d4")]),
        ("malformed-ep-missing-off-center-pawn-black", black, "e3", base + [p(black, reference.PAWN, "d4")]),
        ("malformed-ep-off-center-nonpawn-black", black, "e3", base + [p(white, reference.ROOK, "e4"), p(black, reference.PAWN, "d4")]),
        ("malformed-ep-friendly-off-center-pawn-black", black, "e3", base + [p(black, reference.PAWN, "e4"), p(black, reference.PAWN, "d4")]),
        ("malformed-ep-no-side-to-move-attacker-black", black, "e3", base + [p(white, reference.PAWN, "e4")]),
        ("valid-ep-snapshot-black", black, "e3", base + [p(white, reference.PAWN, "e4"), p(black, reference.PAWN, "d4"), p(black, reference.PAWN, "f4")]),
        ("no-ep-snapshot-black", black, "-", base + [p(white, reference.PAWN, "e4"), p(black, reference.PAWN, "d4")]),
    ]
    result = []
    for name, side_to_move, ep_text, pieces in definitions:
        placement = encode_placement(pieces)
        for perspective in reference.COLORS:
            result.append(
                SnapshotCase(
                    f"{name}-{perspective.lower()}",
                    perspective,
                    side_to_move,
                    ep_text,
                    placement,
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


def check_python39_grammar() -> None:
    source = Path(__file__).read_text(encoding="utf-8")
    try:
        ast.parse(source, filename=str(Path(__file__)), feature_version=9)
    except SyntaxError as exc:
        raise DifferentialFailure(f"script is not valid Python 3.9 syntax: {exc}") from exc


def verify_corpus_coverage(fen_cases: Sequence[FenCase]) -> None:
    king_squares = {reference.WHITE: set(), reference.BLACK: set()}
    actor_kinds = set()
    actor_relations = set()
    target_classes = set()
    ep_rows = set()
    for case in fen_cases:
        position = parse_fen(case)
        for perspective in reference.COLORS:
            own_king = next(
                piece
                for piece in position.pieces
                if piece.color == perspective and piece.kind == reference.KING
            )
            king_squares[perspective].add(own_king.square)
            for activation in reference.enumerate_capture_pairs(position, perspective):
                actor_kinds.add(activation.actor_kind)
                actor_relations.add(activation.actor_rel)
                if activation.en_passant:
                    ep_rows.add(activation.local_index)
                else:
                    target_classes.add(activation.target_class)

    all_squares = set(range(64))
    for perspective in reference.COLORS:
        if king_squares[perspective] != all_squares:
            missing = sorted(all_squares - king_squares[perspective])
            raise DifferentialFailure(
                f"corpus misses raw {perspective.lower()} king squares: {missing}"
            )
    if actor_kinds != set(reference.ACTOR_KINDS):
        raise DifferentialFailure(
            f"corpus actor-kind coverage mismatch: {sorted(actor_kinds)!r}"
        )
    if actor_relations != set(reference.ACTOR_RELATIONS):
        raise DifferentialFailure(
            f"corpus actor-relation coverage mismatch: {sorted(actor_relations)!r}"
        )
    if target_classes != set(reference.TARGET_CLASSES):
        raise DifferentialFailure(
            f"corpus target-class coverage mismatch: {sorted(target_classes)!r}"
        )
    expected_ep_rows = set(range(reference.NORMAL_DIMENSIONS, reference.PHYSICAL_DIMENSIONS))
    if ep_rows != expected_ep_rows:
        missing = sorted(expected_ep_rows - ep_rows)
        unexpected = sorted(ep_rows - expected_ep_rows)
        raise DifferentialFailure(
            f"corpus EP-tail coverage mismatch: missing={missing}, unexpected={unexpected}"
        )


def run(oracle: Path, timeout: float, random_cases: int) -> None:
    check_python39_grammar()
    fen_cases = build_curated_fens() + build_random_fens(random_cases)
    snapshot_cases = make_snapshot_cases()
    verify_corpus_coverage(fen_cases)
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

    digest = corpus_digest(fen_cases, snapshot_cases)
    if random_cases == DEFAULT_RANDOM_CASES and digest != DEFAULT_CORPUS_SHA256:
        raise DifferentialFailure(
            "default CapturePair corpus identity changed; "
            f"expected={DEFAULT_CORPUS_SHA256}, observed={digest}"
        )
    malformed = sum(case.name.startswith("malformed-ep-") for case in snapshot_cases)
    print(
        "Atomic V3 CapturePair cross-language differential passed: "
        f"{len(fen_cases)} FENs ({sum(case.chess960 for case in fen_cases)} Atomic960), "
        f"{len(snapshot_cases)} snapshots ({malformed} malformed-EP), "
        f"{emissions} emissions, {features} feature records, "
        f"corpus_sha256={digest}"
    )


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--oracle", type=Path, required=True, help="path to the C++ CapturePair CLI"
    )
    parser.add_argument(
        "--timeout", type=float, default=15.0, help="seconds per isolated CLI invocation"
    )
    parser.add_argument(
        "--random-cases",
        type=int,
        default=DEFAULT_RANDOM_CASES,
        help="number of deterministic broad-material FEN cases",
    )
    args = parser.parse_args(argv)
    args.oracle = args.oracle.resolve()
    if not args.oracle.is_file():
        parser.error(f"--oracle is not a regular file: {args.oracle}")
    if args.timeout <= 0:
        parser.error("--timeout must be positive")
    if args.random_cases < 0 or args.random_cases > 10_000:
        parser.error("--random-cases must be in 0..10000")
    return args


def main() -> int:
    args = parse_args()
    try:
        run(args.oracle, args.timeout, args.random_cases)
    except DifferentialFailure as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
