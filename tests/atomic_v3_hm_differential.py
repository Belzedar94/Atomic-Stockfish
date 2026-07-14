#!/usr/bin/env python3
"""Cross-language differential for the provisional AtomicNNUEV3 HM oracle.

The C++ executable is intentionally treated as an isolated command-line
oracle: every position is passed in one ``--fen`` or ``--chess960-fen``
invocation.  Expected orientations and features come exclusively from the
independent Python contract in ``tests/python/atomic_v3_hm_reference.py``.

The compact deterministic corpus covers the 32-piece start position, both
horizontal-orientation branches with every piece class, legal en-passant and
Atomic960 castling metadata, and every raw own-king square for both
perspectives.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from typing import Mapping, Optional, Sequence, Union


REPO_ROOT = Path(__file__).resolve().parents[1]
REFERENCE_DIR = REPO_ROOT / "tests" / "python"
sys.path.insert(0, str(REFERENCE_DIR))

import atomic_v3_hm_reference as reference  # noqa: E402


FIXTURE_PATH = REPO_ROOT / "tests" / "fixtures" / "atomic-nnue-v3" / "hm-oracle-v1.json"
STARTPOS_FEN = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"
ALL_CLASSES_FEN = "6k1/3r4/q3bn1p/8/8/P2NB2Q/5R2/1K6 w - - 0 1"
ALL_CLASSES_MIRRORED_FEN = "1k6/4r3/p1nb3q/8/8/Q2BN2P/2R5/6K1 w - - 0 1"
ATOMIC960_EP_FEN = "r1k4r/8/8/3pP3/8/8/8/R1K4R w AHah d6 0 1"
ATOMIC960_METADATA_MUTATED_FEN = (
    "r1k4r/8/8/3pP3/8/8/8/R1K4R b - - 37 19"
)

HEADER_KEYS = frozenset(
    {
        "perspective",
        "vertical_xor",
        "horizontal_xor",
        "own_king",
        "oriented_own_king",
        "king_bucket",
        "network_bucket",
        "features",
    }
)
FEATURE_KEYS = frozenset(
    {
        "square",
        "piece",
        "oriented",
        "training_plane",
        "training",
        "virtual",
        "physical_plane",
        "physical",
        "psqt_row",
    }
)
EXPORT_HEADER_KEYS = frozenset(
    {
        "export",
        "physical_index",
        "king_bucket",
        "physical_plane",
        "oriented_square",
        "training_plane",
        "training_index",
        "virtual_index",
        "outputs",
    }
)
EXPORT_VALUE_KEYS = frozenset(
    {"output", "source_output", "destination_kind", "destination_output", "value"}
)
EXPORT_CASE_KEYS = frozenset(
    {
        "id",
        "physical_index",
        "oriented_own_king",
        "expected_source",
        "outputs_i32le_sha256",
        "output_sentinels",
    }
)
EXPORT_SOURCE_KEYS = frozenset(
    {
        "king_bucket",
        "physical_plane",
        "oriented_square",
        "training_plane",
        "training_index",
        "virtual_index",
    }
)
EXPORT_SENTINEL_KEYS = frozenset(
    {"0", "1023", "1024", "1025", "1026", "1027", "1028", "1029", "1030", "1031"}
)
UNSIGNED_DECIMAL_RE = re.compile(r"(?:0|[1-9][0-9]*)\Z", re.ASCII)
SIGNED_DECIMAL_RE = re.compile(r"(?:0|-?[1-9][0-9]*)\Z", re.ASCII)
SHA256_RE = re.compile(r"[0-9a-f]{64}\Z", re.ASCII)

FEN_PIECES = {
    "P": (reference.WHITE, "PAWN"),
    "N": (reference.WHITE, "KNIGHT"),
    "B": (reference.WHITE, "BISHOP"),
    "R": (reference.WHITE, "ROOK"),
    "Q": (reference.WHITE, "QUEEN"),
    "K": (reference.WHITE, "KING"),
    "p": (reference.BLACK, "PAWN"),
    "n": (reference.BLACK, "KNIGHT"),
    "b": (reference.BLACK, "BISHOP"),
    "r": (reference.BLACK, "ROOK"),
    "q": (reference.BLACK, "QUEEN"),
    "k": (reference.BLACK, "KING"),
}

# Stockfish's public Piece wire ordinals.  These values are compared as CLI
# serialization, while all feature-address expectations come from the Python
# reference rather than being reimplemented here.
PIECE_WIRE = {
    (reference.WHITE, "PAWN"): 1,
    (reference.WHITE, "KNIGHT"): 2,
    (reference.WHITE, "BISHOP"): 3,
    (reference.WHITE, "ROOK"): 4,
    (reference.WHITE, "QUEEN"): 5,
    (reference.WHITE, "KING"): 6,
    (reference.BLACK, "PAWN"): 9,
    (reference.BLACK, "KNIGHT"): 10,
    (reference.BLACK, "BISHOP"): 11,
    (reference.BLACK, "ROOK"): 12,
    (reference.BLACK, "QUEEN"): 13,
    (reference.BLACK, "KING"): 14,
}


class DifferentialFailure(RuntimeError):
    """Raised when the executable output violates or disagrees with the contract."""


@dataclass(frozen=True)
class CorpusCase:
    name: str
    fen: str
    chess960: bool = False


@dataclass(frozen=True)
class ObservedFeature:
    square: int
    piece: int
    oriented: int
    training_plane: int
    training: int
    virtual: int
    physical_plane: int
    physical: int
    psqt_row: int


@dataclass(frozen=True)
class ObservedEmission:
    perspective: str
    vertical_xor: int
    horizontal_xor: int
    own_king: int
    oriented_own_king: int
    king_bucket: int
    network_bucket: int
    declared_features: int
    features: tuple[ObservedFeature, ...]


@dataclass(frozen=True)
class ExportMappingCase:
    name: str
    physical_index: int
    oriented_own_king: int
    expected_source: Mapping[str, int]
    outputs_i32le_sha256: str
    output_sentinels: Mapping[str, int]


@dataclass(frozen=True)
class ObservedExportSource:
    king_bucket: int
    physical_plane: int
    oriented_square: int
    training_plane: int
    training_index: int
    virtual_index: int


@dataclass(frozen=True)
class ObservedExportRow:
    physical_index: int
    source: ObservedExportSource
    values: tuple[int, ...]


def parse_unsigned(value: str, *, context: str) -> int:
    if not UNSIGNED_DECIMAL_RE.fullmatch(value):
        raise DifferentialFailure(f"{context}: expected canonical unsigned decimal, got {value!r}")
    return int(value)


def parse_signed(value: str, *, context: str) -> int:
    if not SIGNED_DECIMAL_RE.fullmatch(value):
        raise DifferentialFailure(f"{context}: expected canonical signed decimal, got {value!r}")
    return int(value)


def parse_key_values(line: str, *, expected: frozenset[str], context: str) -> dict[str, str]:
    if not line or line != line.strip():
        raise DifferentialFailure(f"{context}: blank or whitespace-padded output line")

    parsed: dict[str, str] = {}
    for token in line.split(" "):
        if not token or token.count("=") != 1:
            raise DifferentialFailure(f"{context}: malformed key=value token {token!r}")
        key, value = token.split("=", 1)
        if not key or not value:
            raise DifferentialFailure(f"{context}: empty key or value in token {token!r}")
        if key in parsed:
            raise DifferentialFailure(f"{context}: duplicate output key {key!r}")
        parsed[key] = value

    actual = frozenset(parsed)
    if actual != expected:
        missing = sorted(expected - actual)
        extra = sorted(actual - expected)
        raise DifferentialFailure(
            f"{context}: output schema mismatch; missing={missing}, extra={extra}"
        )
    return parsed


def _fixture_integer(value: object, *, context: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise DifferentialFailure(f"{context}: expected JSON integer, got {value!r}")
    return value


def load_fixture() -> dict[str, object]:
    try:
        raw = FIXTURE_PATH.read_bytes()
    except OSError as exc:
        raise DifferentialFailure(f"could not read HM fixture {FIXTURE_PATH}: {exc}") from exc
    if raw.startswith(b"\xef\xbb\xbf") or b"\r" in raw or not raw.endswith(b"\n"):
        raise DifferentialFailure("HM fixture must be UTF-8 without BOM/CR and end in one LF")

    def reject_duplicates(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise DifferentialFailure(f"HM fixture contains duplicate JSON key {key!r}")
            result[key] = value
        return result

    try:
        fixture = json.loads(raw, object_pairs_hook=reject_duplicates)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise DifferentialFailure(f"HM fixture is not strict JSON: {exc}") from exc
    if not isinstance(fixture, dict) or fixture.get("schema") != "atomic-nnue-v3-hm-oracle-v1":
        raise DifferentialFailure("HM fixture has an unknown top-level schema")
    return fixture


def load_fixture_positions() -> dict[str, reference.HMPosition]:
    raw_positions = load_fixture().get("positions")
    if not isinstance(raw_positions, list) or not raw_positions:
        raise DifferentialFailure("HM fixture positions must be a non-empty array")

    positions: dict[str, reference.HMPosition] = {}
    for index, raw_case in enumerate(raw_positions):
        context = f"fixture.positions[{index}]"
        if not isinstance(raw_case, dict) or frozenset(raw_case) != {
            "id",
            "position",
            "expected",
        }:
            raise DifferentialFailure(f"{context}: position case schema mismatch")
        name = raw_case["id"]
        if not isinstance(name, str) or not name or name in positions:
            raise DifferentialFailure(f"{context}.id: expected a unique non-empty string")
        raw_position = raw_case["position"]
        if not isinstance(raw_position, dict):
            raise DifferentialFailure(f"{context}.position: expected an object")
        try:
            positions[name] = reference.HMPosition.from_wire(raw_position)
        except (reference.HMContractError, TypeError) as exc:
            raise DifferentialFailure(f"{context}.position: {exc}") from exc
    return positions


def load_export_mapping_cases() -> tuple[ExportMappingCase, ...]:
    fixture = load_fixture()
    raw_cases = fixture.get("export_mapping_cases")
    if not isinstance(raw_cases, list) or not raw_cases:
        raise DifferentialFailure("HM fixture export_mapping_cases must be a non-empty array")

    cases: list[ExportMappingCase] = []
    seen_ids: set[str] = set()
    for index, raw_case in enumerate(raw_cases):
        context = f"fixture.export_mapping_cases[{index}]"
        if not isinstance(raw_case, dict) or frozenset(raw_case) != EXPORT_CASE_KEYS:
            raise DifferentialFailure(f"{context}: export case schema mismatch")
        name = raw_case["id"]
        if not isinstance(name, str) or not name or name in seen_ids:
            raise DifferentialFailure(f"{context}.id: expected a unique non-empty string")
        seen_ids.add(name)

        physical_index = _fixture_integer(
            raw_case["physical_index"], context=f"{context}.physical_index"
        )
        if not 0 <= physical_index < reference.PHYSICAL_DIMENSIONS:
            raise DifferentialFailure(f"{context}.physical_index: outside the HM physical tensor")
        own_king_name = raw_case["oriented_own_king"]
        if not isinstance(own_king_name, str):
            raise DifferentialFailure(f"{context}.oriented_own_king: expected square string")
        try:
            oriented_own_king = reference.square(own_king_name)
        except reference.HMContractError as exc:
            raise DifferentialFailure(f"{context}.oriented_own_king: {exc}") from exc
        if oriented_own_king % 8 < 4:
            raise DifferentialFailure(f"{context}.oriented_own_king: must be on files e-h")

        raw_source = raw_case["expected_source"]
        if not isinstance(raw_source, dict) or frozenset(raw_source) != EXPORT_SOURCE_KEYS:
            raise DifferentialFailure(f"{context}.expected_source: source schema mismatch")
        expected_source = {
            key: _fixture_integer(value, context=f"{context}.expected_source.{key}")
            for key, value in raw_source.items()
        }

        digest = raw_case["outputs_i32le_sha256"]
        if not isinstance(digest, str) or not SHA256_RE.fullmatch(digest):
            raise DifferentialFailure(f"{context}.outputs_i32le_sha256: invalid SHA-256")
        raw_sentinels = raw_case["output_sentinels"]
        if not isinstance(raw_sentinels, dict) or frozenset(raw_sentinels) != EXPORT_SENTINEL_KEYS:
            raise DifferentialFailure(f"{context}.output_sentinels: sentinel schema mismatch")
        output_sentinels = {
            key: _fixture_integer(value, context=f"{context}.output_sentinels.{key}")
            for key, value in raw_sentinels.items()
        }
        cases.append(
            ExportMappingCase(
                name,
                physical_index,
                oriented_own_king,
                expected_source,
                digest,
                output_sentinels,
            )
        )
    return tuple(cases)


def parse_feature(values: Mapping[str, str], *, context: str) -> ObservedFeature:
    numeric = {key: parse_unsigned(value, context=f"{context}.{key}") for key, value in values.items()}
    return ObservedFeature(
        square=numeric["square"],
        piece=numeric["piece"],
        oriented=numeric["oriented"],
        training_plane=numeric["training_plane"],
        training=numeric["training"],
        virtual=numeric["virtual"],
        physical_plane=numeric["physical_plane"],
        physical=numeric["physical"],
        psqt_row=numeric["psqt_row"],
    )


def parse_oracle_output(stdout: str, *, case_name: str) -> dict[str, ObservedEmission]:
    if not stdout:
        raise DifferentialFailure(f"{case_name}: oracle emitted no stdout")
    if not stdout.endswith("\n"):
        raise DifferentialFailure(f"{case_name}: oracle stdout is truncated (missing final newline)")

    emissions: dict[str, ObservedEmission] = {}
    perspective_order: list[str] = []
    current_header: Optional[dict[str, str]] = None
    current_features: list[ObservedFeature] = []
    current_squares: set[int] = set()

    def finish_current() -> None:
        nonlocal current_header, current_features, current_squares
        if current_header is None:
            return

        wire_perspective = current_header["perspective"]
        perspective = {
            "white": reference.WHITE,
            "black": reference.BLACK,
        }.get(wire_perspective)
        if perspective is None:
            raise DifferentialFailure(
                f"{case_name}: unknown perspective {wire_perspective!r}"
            )
        if perspective in emissions:
            raise DifferentialFailure(f"{case_name}: duplicate {perspective} emission")

        declared = parse_unsigned(
            current_header["features"], context=f"{case_name}.{perspective}.features"
        )
        if declared != len(current_features):
            raise DifferentialFailure(
                f"{case_name}.{perspective}: declared {declared} features but emitted "
                f"{len(current_features)}"
            )

        numeric_header = {
            key: parse_unsigned(value, context=f"{case_name}.{perspective}.{key}")
            for key, value in current_header.items()
            if key not in {"perspective", "features"}
        }
        emissions[perspective] = ObservedEmission(
            perspective=perspective,
            vertical_xor=numeric_header["vertical_xor"],
            horizontal_xor=numeric_header["horizontal_xor"],
            own_king=numeric_header["own_king"],
            oriented_own_king=numeric_header["oriented_own_king"],
            king_bucket=numeric_header["king_bucket"],
            network_bucket=numeric_header["network_bucket"],
            declared_features=declared,
            features=tuple(current_features),
        )
        perspective_order.append(perspective)
        current_header = None
        current_features = []
        current_squares = set()

    for line_number, line in enumerate(stdout.splitlines(), start=1):
        context = f"{case_name}:line {line_number}"
        if line.startswith("perspective="):
            finish_current()
            current_header = parse_key_values(line, expected=HEADER_KEYS, context=context)
        elif line.startswith("square="):
            if current_header is None:
                raise DifferentialFailure(f"{context}: feature appeared before a perspective header")
            values = parse_key_values(line, expected=FEATURE_KEYS, context=context)
            feature = parse_feature(values, context=context)
            if feature.square in current_squares:
                raise DifferentialFailure(
                    f"{context}: duplicate feature for raw square {feature.square}"
                )
            current_squares.add(feature.square)
            current_features.append(feature)
        else:
            raise DifferentialFailure(f"{context}: unexpected output line {line!r}")

    finish_current()
    required_order = [reference.WHITE, reference.BLACK]
    if perspective_order != required_order:
        raise DifferentialFailure(
            f"{case_name}: expected exactly WHITE then BLACK, got {perspective_order}"
        )
    return emissions


def export_source_mapping(
    source: Union[reference.ExportSource, ObservedExportSource],
) -> dict[str, int]:
    return {
        "king_bucket": source.king_bucket,
        "physical_plane": source.physical_plane,
        "oriented_square": source.oriented_square,
        "training_plane": source.training_plane,
        "training_index": source.training_index,
        "virtual_index": source.virtual_index,
    }


def parse_export_output(stdout: str, *, case_name: str) -> ObservedExportRow:
    if not stdout or not stdout.endswith("\n"):
        raise DifferentialFailure(f"{case_name}: export stdout is empty or missing its final LF")
    lines = stdout.splitlines()
    if len(lines) != reference.OUTPUTS + 1:
        raise DifferentialFailure(
            f"{case_name}: expected one source plus {reference.OUTPUTS} values, got {len(lines)} lines"
        )

    header = parse_key_values(
        lines[0], expected=EXPORT_HEADER_KEYS, context=f"{case_name}:export-source"
    )
    if header["export"] != "source":
        raise DifferentialFailure(f"{case_name}: first line is not an export source record")
    numeric_header = {
        key: parse_unsigned(value, context=f"{case_name}.source.{key}")
        for key, value in header.items()
        if key != "export"
    }
    if numeric_header["physical_index"] >= reference.PHYSICAL_DIMENSIONS:
        raise DifferentialFailure(f"{case_name}: physical_index is outside the HM tensor")
    if numeric_header["king_bucket"] >= reference.KING_BUCKETS:
        raise DifferentialFailure(f"{case_name}: king_bucket is outside the HM tensor")
    if numeric_header["physical_plane"] >= len(reference.PHYSICAL_PLANES):
        raise DifferentialFailure(f"{case_name}: physical_plane is outside the HM tensor")
    if numeric_header["oriented_square"] >= 64:
        raise DifferentialFailure(f"{case_name}: oriented_square is outside the board")
    if numeric_header["training_plane"] >= len(reference.TRAINING_PLANES):
        raise DifferentialFailure(f"{case_name}: training_plane is outside the HM tensor")
    if numeric_header["training_index"] >= reference.TRAINING_DIMENSIONS:
        raise DifferentialFailure(f"{case_name}: training_index is outside the HM tensor")
    if numeric_header["virtual_index"] >= reference.VIRTUAL_DIMENSIONS:
        raise DifferentialFailure(f"{case_name}: virtual_index is outside the HM factor tensor")
    if numeric_header["outputs"] != reference.OUTPUTS:
        raise DifferentialFailure(
            f"{case_name}: declared {numeric_header['outputs']} outputs, expected {reference.OUTPUTS}"
        )

    values: list[int] = []
    for expected_output, line in enumerate(lines[1:]):
        context = f"{case_name}:export-output[{expected_output}]"
        record = parse_key_values(line, expected=EXPORT_VALUE_KEYS, context=context)
        output = parse_unsigned(record["output"], context=f"{context}.output")
        source_output = parse_unsigned(
            record["source_output"], context=f"{context}.source_output"
        )
        destination_output = parse_unsigned(
            record["destination_output"], context=f"{context}.destination_output"
        )
        if output != expected_output or source_output != expected_output:
            raise DifferentialFailure(f"{context}: output sequence/source_output mismatch")
        expected_kind = "accumulator" if expected_output < reference.ACCUMULATOR_OUTPUTS else "psqt"
        expected_destination = (
            expected_output
            if expected_output < reference.ACCUMULATOR_OUTPUTS
            else expected_output - reference.ACCUMULATOR_OUTPUTS
        )
        if (
            record["destination_kind"] != expected_kind
            or destination_output != expected_destination
        ):
            raise DifferentialFailure(f"{context}: accumulator/PSQT destination mismatch")
        value = parse_signed(record["value"], context=f"{context}.value")
        if not -16000 <= value <= 16000:
            raise DifferentialFailure(f"{context}: synthetic coalesced value is out of range")
        values.append(value)

    return ObservedExportRow(
        physical_index=numeric_header["physical_index"],
        source=ObservedExportSource(
            king_bucket=numeric_header["king_bucket"],
            physical_plane=numeric_header["physical_plane"],
            oriented_square=numeric_header["oriented_square"],
            training_plane=numeric_header["training_plane"],
            training_index=numeric_header["training_index"],
            virtual_index=numeric_header["virtual_index"],
        ),
        values=tuple(values),
    )


def parse_fen(case: CorpusCase) -> reference.HMPosition:
    fields = case.fen.split(" ")
    if len(fields) != 6 or any(not field for field in fields):
        raise DifferentialFailure(f"{case.name}: FEN must contain exactly six single-spaced fields")
    placement, active, castling, ep, halfmove, fullmove = fields

    if active not in {"w", "b"}:
        raise DifferentialFailure(f"{case.name}: invalid FEN active color {active!r}")
    if not UNSIGNED_DECIMAL_RE.fullmatch(halfmove):
        raise DifferentialFailure(f"{case.name}: invalid FEN halfmove clock {halfmove!r}")
    if not UNSIGNED_DECIMAL_RE.fullmatch(fullmove) or int(fullmove) < 1:
        raise DifferentialFailure(f"{case.name}: invalid FEN fullmove number {fullmove!r}")

    ranks = placement.split("/")
    if len(ranks) != 8:
        raise DifferentialFailure(f"{case.name}: FEN placement must contain eight ranks")

    pieces: list[reference.Piece] = []
    for encoded_rank, rank_text in enumerate(ranks):
        file_index = 0
        rank_index = 7 - encoded_rank
        for token in rank_text:
            if token in "12345678":
                file_index += int(token)
            elif token in FEN_PIECES:
                if file_index >= 8:
                    raise DifferentialFailure(f"{case.name}: too many squares in FEN rank")
                color, kind = FEN_PIECES[token]
                pieces.append(reference.Piece(color, kind, rank_index * 8 + file_index))
                file_index += 1
            else:
                raise DifferentialFailure(f"{case.name}: unsupported FEN piece token {token!r}")
            if file_index > 8:
                raise DifferentialFailure(f"{case.name}: too many squares in FEN rank")
        if file_index != 8:
            raise DifferentialFailure(
                f"{case.name}: FEN rank {8 - encoded_rank} expands to {file_index} squares"
            )

    try:
        ep_square = None if ep == "-" else reference.square(ep)
        return reference.HMPosition(
            tuple(pieces),
            side_to_move=reference.WHITE if active == "w" else reference.BLACK,
            ep_square=ep_square,
            atomic960=case.chess960,
            castling_rights=castling,
        )
    except reference.HMContractError as exc:
        raise DifferentialFailure(f"{case.name}: FEN is outside the Python HM domain: {exc}") from exc


def own_king(position: reference.HMPosition, perspective: str) -> reference.Piece:
    return next(
        piece
        for piece in position.pieces
        if piece.color == perspective and piece.kind == "KING"
    )


def expected_features(
    position: reference.HMPosition, perspective: str
) -> tuple[ObservedFeature, ...]:
    pieces_by_square = {piece.square: piece for piece in position.pieces}
    expected: list[ObservedFeature] = []
    for activation in reference.enumerate_hm(position, perspective):
        piece = pieces_by_square[activation.raw_square]
        expected.append(
            ObservedFeature(
                square=activation.raw_square,
                piece=PIECE_WIRE[(piece.color, piece.kind)],
                oriented=activation.oriented_square,
                training_plane=activation.training_plane,
                training=activation.training_index,
                virtual=activation.virtual_index,
                physical_plane=activation.physical_plane,
                physical=activation.physical_index,
                psqt_row=activation.physical_index,
            )
        )
    return tuple(expected)


def describe_difference(label: str, expected: object, observed: object) -> str:
    return f"{label}: expected {expected!r}, observed {observed!r}"


def compare_emission(
    case: CorpusCase,
    position: reference.HMPosition,
    perspective: str,
    observed: ObservedEmission,
) -> None:
    king = own_king(position, perspective)
    orientation = reference.orientation_for(perspective, king.square)
    expected_header = {
        "vertical_xor": orientation.vertical_xor,
        "horizontal_xor": orientation.horizontal_xor,
        "own_king": king.square,
        "oriented_own_king": orientation.oriented_own_king,
        "king_bucket": orientation.king_bucket,
        "network_bucket": reference.network_bucket(len(position.pieces)),
        "declared_features": len(position.pieces),
    }
    for field, expected_value in expected_header.items():
        observed_value = getattr(observed, field)
        if observed_value != expected_value:
            raise DifferentialFailure(
                f"{case.name}.{perspective}: "
                + describe_difference(field, expected_value, observed_value)
            )

    expected = expected_features(position, perspective)
    if len(observed.features) != len(expected):
        raise DifferentialFailure(
            f"{case.name}.{perspective}: "
            + describe_difference("feature count", len(expected), len(observed.features))
        )
    for index, (expected_feature, observed_feature) in enumerate(
        zip(expected, observed.features)
    ):
        if observed_feature != expected_feature:
            raise DifferentialFailure(
                f"{case.name}.{perspective}.feature[{index}]: "
                + describe_difference("record", expected_feature, observed_feature)
            )


def invoke_oracle(oracle: Path, case: CorpusCase, timeout: float) -> dict[str, ObservedEmission]:
    mode = "--chess960-fen" if case.chess960 else "--fen"
    argv = [str(oracle), mode, case.fen]
    if len(argv) != 3 or argv[2] != case.fen:
        raise AssertionError("FEN must remain one subprocess argument")

    options: dict[str, object] = {}
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
        raise DifferentialFailure(f"{case.name}: could not execute C++ oracle: {exc}") from exc

    if completed.returncode != 0:
        raise DifferentialFailure(
            f"{case.name}: C++ oracle exited {completed.returncode}; "
            f"stderr={completed.stderr!r}; stdout={completed.stdout!r}"
        )
    if completed.stderr:
        raise DifferentialFailure(
            f"{case.name}: successful C++ oracle wrote unexpected stderr: {completed.stderr!r}"
        )
    return parse_oracle_output(completed.stdout, case_name=case.name)


def invoke_export_row(
    oracle: Path, case: ExportMappingCase, timeout: float
) -> ObservedExportRow:
    argv = [
        str(oracle),
        "--export-row",
        str(case.physical_index),
        str(case.oriented_own_king),
    ]
    options: dict[str, object] = {}
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
        raise DifferentialFailure(f"{case.name}: could not execute C++ export oracle: {exc}") from exc

    if completed.returncode != 0:
        raise DifferentialFailure(
            f"{case.name}: C++ export oracle exited {completed.returncode}; "
            f"stderr={completed.stderr!r}; stdout={completed.stdout!r}"
        )
    if completed.stderr:
        raise DifferentialFailure(
            f"{case.name}: successful C++ export oracle wrote stderr: {completed.stderr!r}"
        )
    return parse_export_output(completed.stdout, case_name=case.name)


def king_only_fen(white_king: int, black_king: int) -> str:
    if white_king == black_king or not 0 <= white_king < 64 or not 0 <= black_king < 64:
        raise AssertionError("king-only corpus requires two distinct in-range squares")
    board = {white_king: "K", black_king: "k"}
    ranks: list[str] = []
    for rank_index in range(7, -1, -1):
        encoded: list[str] = []
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
    return "/".join(ranks) + " w - - 0 1"


def build_corpus() -> tuple[CorpusCase, ...]:
    cases = [
        CorpusCase("thirty-two-piece-boundary", STARTPOS_FEN),
        CorpusCase("opposite-mirror-all-classes", ALL_CLASSES_FEN),
        CorpusCase("opposite-mirror-all-classes-horizontal", ALL_CLASSES_MIRRORED_FEN),
        CorpusCase("atomic960-ep-metadata", ATOMIC960_EP_FEN, chess960=True),
        CorpusCase("same-material-metadata-mutated", ATOMIC960_METADATA_MUTATED_FEN),
    ]
    cases.extend(
        CorpusCase(
            f"all-raw-king-squares-{raw_square:02d}",
            king_only_fen(raw_square, raw_square ^ 63),
        )
        for raw_square in range(64)
    )
    return tuple(cases)


def position_identity(position: reference.HMPosition) -> tuple[object, ...]:
    """Order-neutral identity used to replay fixture material from canonical FEN."""

    material = tuple(
        sorted((piece.color, piece.kind, piece.square) for piece in position.pieces)
    )
    return (
        material,
        position.side_to_move,
        position.ep_square,
        position.atomic960,
        position.castling_rights,
    )


def compare_export_row(case: ExportMappingCase, observed: ObservedExportRow) -> None:
    try:
        expected_source = reference.export_source(case.physical_index, case.oriented_own_king)
    except reference.HMContractError as exc:
        raise DifferentialFailure(
            f"{case.name}: fixture export row is outside the HM domain: {exc}"
        ) from exc
    independent_mapping = export_source_mapping(expected_source)
    if independent_mapping != case.expected_source:
        raise DifferentialFailure(
            f"{case.name}: independent Python source mapping disagrees with fixture; "
            f"expected={case.expected_source!r}, python={independent_mapping!r}"
        )
    if observed.physical_index != case.physical_index:
        raise DifferentialFailure(
            f"{case.name}: C++ export reported physical row {observed.physical_index}, "
            f"expected {case.physical_index}"
        )
    observed_mapping = export_source_mapping(observed.source)
    if observed_mapping != independent_mapping:
        raise DifferentialFailure(
            f"{case.name}: C++ source mapping disagrees with Python/fixture; "
            f"expected={independent_mapping!r}, observed={observed_mapping!r}"
        )

    expected_values = reference.coalesced_row(case.physical_index, case.oriented_own_king)
    if len(observed.values) != reference.OUTPUTS or len(expected_values) != reference.OUTPUTS:
        raise DifferentialFailure(f"{case.name}: export row does not contain 1,032 values")
    for output, (expected, actual) in enumerate(zip(expected_values, observed.values)):
        if actual != expected:
            raise DifferentialFailure(
                f"{case.name}: coalesced output {output} differs; "
                f"expected {expected}, observed {actual}"
            )

    expected_digest = reference.i32le_sha256(expected_values)
    observed_digest = reference.i32le_sha256(observed.values)
    if expected_digest != case.outputs_i32le_sha256:
        raise DifferentialFailure(
            f"{case.name}: independent Python SHA-256 disagrees with fixture; "
            f"fixture={case.outputs_i32le_sha256}, python={expected_digest}"
        )
    if observed_digest != case.outputs_i32le_sha256:
        raise DifferentialFailure(
            f"{case.name}: C++ output SHA-256 disagrees with fixture; "
            f"fixture={case.outputs_i32le_sha256}, observed={observed_digest}"
        )

    independent_sentinels = {
        key: expected_values[int(key)] for key in sorted(case.output_sentinels, key=int)
    }
    observed_sentinels = {
        key: observed.values[int(key)] for key in sorted(case.output_sentinels, key=int)
    }
    if independent_sentinels != case.output_sentinels:
        raise DifferentialFailure(
            f"{case.name}: independent Python sentinels disagree with fixture"
        )
    if observed_sentinels != case.output_sentinels:
        raise DifferentialFailure(f"{case.name}: C++ sentinels disagree with fixture")


def run(oracle: Path, timeout: float) -> None:
    covered_king_squares = {
        reference.WHITE: set(),
        reference.BLACK: set(),
    }
    corpus = build_corpus()
    fixture_positions = load_fixture_positions()
    for case in corpus:
        position = parse_fen(case)
        if case.name in fixture_positions:
            expected_fixture_position = fixture_positions[case.name]
            if position_identity(position) != position_identity(expected_fixture_position):
                raise DifferentialFailure(
                    f"{case.name}: canonical FEN replay disagrees with the golden fixture"
                )
        emissions = invoke_oracle(oracle, case, timeout)
        for perspective in reference.COLORS:
            observed = emissions[perspective]
            compare_emission(case, position, perspective, observed)
            if case.name.startswith("all-raw-king-squares-"):
                covered_king_squares[perspective].add(observed.own_king)

    all_squares = set(range(64))
    for perspective in reference.COLORS:
        if covered_king_squares[perspective] != all_squares:
            missing = sorted(all_squares - covered_king_squares[perspective])
            raise DifferentialFailure(
                f"raw king-square corpus is incomplete for {perspective}: missing={missing}"
            )

    export_cases = load_export_mapping_cases()
    for case in export_cases:
        compare_export_row(case, invoke_export_row(oracle, case, timeout))

    print(
        "Atomic V3 HM cross-language differential passed: "
        f"{len(corpus)} FENs, {2 * len(corpus)} emissions, "
        "5 golden fixture positions replayed through strict engine FEN loading, "
        f"all 64 raw king squares per perspective, {len(export_cases)} export rows, "
        f"{len(export_cases) * reference.OUTPUTS} coalesced scalar values"
    )


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--oracle", type=Path, required=True, help="path to the C++ HM CLI")
    parser.add_argument("--timeout", type=float, default=15.0, help="seconds per isolated FEN")
    args = parser.parse_args(argv)
    args.oracle = args.oracle.resolve()
    if not args.oracle.is_file():
        parser.error(f"--oracle is not a regular file: {args.oracle}")
    if args.timeout <= 0:
        parser.error("--timeout must be positive")
    return args


def main() -> int:
    args = parse_args()
    try:
        run(args.oracle, args.timeout)
    except DifferentialFailure as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
