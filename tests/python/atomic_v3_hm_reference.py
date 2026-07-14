#!/usr/bin/env python3
"""Independent scalar oracle for the AtomicNNUEV3 HalfKAv2Atomic_hm slice.

This module deliberately does not import engine code.  It implements the
provisional H9.3 contract directly from ``schemas/atomic-nnue-v3.json`` and
ADR 0004, using only rank-major square ordinals and integer arithmetic.  The
small deterministic weight functions at the bottom are test oracles, not a
network initialization scheme.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import struct
from typing import Iterable, Mapping, Optional, Sequence


WHITE = "WHITE"
BLACK = "BLACK"
COLORS = (WHITE, BLACK)

PIECE_KINDS = ("PAWN", "KNIGHT", "BISHOP", "ROOK", "QUEEN", "KING")
TRAINING_PLANES = tuple(
    relation + "_" + kind
    for kind in PIECE_KINDS
    for relation in ("OWN", "OPP")
)
PHYSICAL_PLANES = TRAINING_PLANES[:10] + ("MERGED_KING",)

SQUARES = 64
TRAINING_PLANE_COUNT = 12
PHYSICAL_PLANE_COUNT = 11
KING_BUCKETS = 32
TRAINING_BUCKET_WIDTH = TRAINING_PLANE_COUNT * SQUARES
PHYSICAL_BUCKET_WIDTH = PHYSICAL_PLANE_COUNT * SQUARES
TRAINING_DIMENSIONS = KING_BUCKETS * TRAINING_BUCKET_WIDTH
PHYSICAL_DIMENSIONS = KING_BUCKETS * PHYSICAL_BUCKET_WIDTH
VIRTUAL_DIMENSIONS = TRAINING_PLANE_COUNT * SQUARES
ACCUMULATOR_OUTPUTS = 1024
PSQT_OUTPUTS = 8
OUTPUTS = ACCUMULATOR_OUTPUTS + PSQT_OUTPUTS


class HMContractError(ValueError):
    """Raised when an input is outside the evaluable V3 HM domain."""


def _require_square(value: int, label: str = "square") -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value < 64:
        raise HMContractError(f"{label} must be an integer in 0..63")
    return value


def square(name: str) -> int:
    """Convert a lower-case algebraic square to the A1=0 rank-major ordinal."""

    if not isinstance(name, str) or len(name) != 2:
        raise HMContractError("square name must have the form a1..h8")
    file_index = ord(name[0]) - ord("a")
    rank_index = ord(name[1]) - ord("1")
    if not 0 <= file_index < 8 or not 0 <= rank_index < 8:
        raise HMContractError("square name must have the form a1..h8")
    return rank_index * 8 + file_index


def square_name(value: int) -> str:
    value = _require_square(value)
    return chr(ord("a") + value % 8) + chr(ord("1") + value // 8)


@dataclass(frozen=True)
class Piece:
    color: str
    kind: str
    square: int

    def __post_init__(self) -> None:
        if self.color not in COLORS:
            raise HMContractError(f"unknown piece color: {self.color!r}")
        if self.kind not in PIECE_KINDS:
            raise HMContractError(f"unknown piece kind: {self.kind!r}")
        _require_square(self.square, "piece square")

    @classmethod
    def from_wire(cls, value: Sequence[object]) -> "Piece":
        if not isinstance(value, Sequence) or isinstance(value, (str, bytes)) or len(value) != 3:
            raise HMContractError("piece wire value must be [color, kind, square]")
        color, kind, piece_square = value
        if not isinstance(color, str) or not isinstance(kind, str):
            raise HMContractError("piece color and kind must be strings")
        if isinstance(piece_square, str):
            piece_square = square(piece_square)
        return cls(color, kind, piece_square)  # type: ignore[arg-type]

    def to_wire(self) -> list[object]:
        return [self.color, self.kind, square_name(self.square)]


@dataclass(frozen=True)
class HMPosition:
    """Board material plus metadata that HM intentionally does not consume."""

    pieces: tuple[Piece, ...]
    side_to_move: str = WHITE
    ep_square: Optional[int] = None
    atomic960: bool = False
    castling_rights: str = "-"

    def __post_init__(self) -> None:
        if self.side_to_move not in COLORS:
            raise HMContractError("side_to_move must be WHITE or BLACK")
        if self.ep_square is not None:
            _require_square(self.ep_square, "ep_square")
        if not isinstance(self.atomic960, bool):
            raise HMContractError("atomic960 must be boolean")
        if not isinstance(self.castling_rights, str):
            raise HMContractError("castling_rights must be a string")
        validate_material(self.pieces)

    @classmethod
    def from_wire(cls, value: Mapping[str, object]) -> "HMPosition":
        raw_pieces = value.get("pieces")
        if not isinstance(raw_pieces, list):
            raise HMContractError("position.pieces must be a list")
        ep = value.get("ep_square")
        if isinstance(ep, str) and ep != "-":
            ep = square(ep)
        elif ep == "-":
            ep = None
        return cls(
            tuple(Piece.from_wire(item) for item in raw_pieces),  # type: ignore[arg-type]
            side_to_move=value.get("side_to_move", WHITE),  # type: ignore[arg-type]
            ep_square=ep,  # type: ignore[arg-type]
            atomic960=value.get("atomic960", False),  # type: ignore[arg-type]
            castling_rights=value.get("castling_rights", "-"),  # type: ignore[arg-type]
        )

    def to_wire(self) -> dict[str, object]:
        return {
            "pieces": [piece.to_wire() for piece in self.pieces],
            "side_to_move": self.side_to_move,
            "ep_square": "-" if self.ep_square is None else square_name(self.ep_square),
            "atomic960": self.atomic960,
            "castling_rights": self.castling_rights,
        }


def validate_material(pieces: Iterable[Piece]) -> tuple[Piece, ...]:
    material = tuple(pieces)
    if not 2 <= len(material) <= 32:
        raise HMContractError("evaluable HM material must contain 2..32 pieces")

    occupied: set[int] = set()
    color_counts = {WHITE: 0, BLACK: 0}
    king_counts = {WHITE: 0, BLACK: 0}
    for piece in material:
        if not isinstance(piece, Piece):
            raise HMContractError("material entries must be Piece instances")
        if piece.square in occupied:
            raise HMContractError(f"two pieces occupy {square_name(piece.square)}")
        occupied.add(piece.square)
        color_counts[piece.color] += 1
        if piece.kind == "KING":
            king_counts[piece.color] += 1

    if any(count > 16 for count in color_counts.values()):
        raise HMContractError("evaluable HM material allows at most 16 pieces per color")
    if king_counts != {WHITE: 1, BLACK: 1}:
        raise HMContractError("evaluable HM material requires exactly one king per color")
    return material


@dataclass(frozen=True)
class Orientation:
    perspective: str
    raw_own_king: int
    vertical_xor: int
    horizontal_xor: int
    oriented_own_king: int
    king_bucket: int

    @property
    def square_xor(self) -> int:
        return self.vertical_xor ^ self.horizontal_xor

    def orient(self, raw_square: int) -> int:
        return _require_square(raw_square) ^ self.square_xor


def orientation_for(perspective: str, own_king_square: int) -> Orientation:
    if perspective not in COLORS:
        raise HMContractError("perspective must be WHITE or BLACK")
    own_king_square = _require_square(own_king_square, "own king square")
    vertical = 56 if perspective == BLACK else 0
    pre_horizontal_king = own_king_square ^ vertical
    horizontal = 7 if pre_horizontal_king % 8 < 4 else 0
    oriented_king = pre_horizontal_king ^ horizontal
    file_index = oriented_king % 8
    rank_index = oriented_king // 8
    if file_index < 4:  # An executable assertion of the HM normalization.
        raise AssertionError("oriented own king did not normalize to files e-h")
    bucket = (7 - rank_index) * 4 + (7 - file_index)
    return Orientation(
        perspective,
        own_king_square,
        vertical,
        horizontal,
        oriented_king,
        bucket,
    )


def _own_king(position: HMPosition, perspective: str) -> Piece:
    if perspective not in COLORS:
        raise HMContractError("perspective must be WHITE or BLACK")
    # HMPosition validation makes this total and unique.
    return next(
        piece
        for piece in position.pieces
        if piece.color == perspective and piece.kind == "KING"
    )


def training_plane(piece: Piece, perspective: str) -> int:
    if perspective not in COLORS:
        raise HMContractError("perspective must be WHITE or BLACK")
    kind_index = PIECE_KINDS.index(piece.kind)
    return kind_index * 2 + int(piece.color != perspective)


def physical_plane(training_plane_index: int) -> int:
    if (
        isinstance(training_plane_index, bool)
        or not isinstance(training_plane_index, int)
        or not 0 <= training_plane_index < TRAINING_PLANE_COUNT
    ):
        raise HMContractError("training plane must be in 0..11")
    return 10 if training_plane_index >= 10 else training_plane_index


@dataclass(frozen=True)
class HMActivation:
    raw_square: int
    oriented_square: int
    training_plane: int
    physical_plane: int
    training_index: int
    virtual_index: int
    physical_index: int


def enumerate_hm(position: HMPosition, perspective: str) -> tuple[HMActivation, ...]:
    """Enumerate one bucket row, one slice-relative virtual row and one export row."""

    king = _own_king(position, perspective)
    orient = orientation_for(perspective, king.square)
    active: list[HMActivation] = []
    for piece in sorted(position.pieces, key=lambda item: item.square):
        piece_square = orient.orient(piece.square)
        t_plane = training_plane(piece, perspective)
        p_plane = physical_plane(t_plane)
        training_index = (
            orient.king_bucket * TRAINING_BUCKET_WIDTH
            + t_plane * SQUARES
            + piece_square
        )
        virtual_index = t_plane * SQUARES + piece_square
        physical_index = (
            orient.king_bucket * PHYSICAL_BUCKET_WIDTH
            + p_plane * SQUARES
            + piece_square
        )
        active.append(
            HMActivation(
                piece.square,
                piece_square,
                t_plane,
                p_plane,
                training_index,
                virtual_index,
                physical_index,
            )
        )
    return tuple(active)


@dataclass(frozen=True)
class ExportSource:
    physical_index: int
    king_bucket: int
    physical_plane: int
    oriented_square: int
    training_plane: int
    training_index: int
    virtual_index: int


def export_source(physical_index: int, oriented_own_king: int) -> ExportSource:
    """Return the factorized source selected by the 12-to-11 export mapping."""

    if (
        isinstance(physical_index, bool)
        or not isinstance(physical_index, int)
        or not 0 <= physical_index < PHYSICAL_DIMENSIONS
    ):
        raise HMContractError(f"physical index must be in 0..{PHYSICAL_DIMENSIONS - 1}")
    oriented_own_king = _require_square(oriented_own_king, "oriented own king")
    if oriented_own_king % 8 < 4:
        raise HMContractError("oriented own king must be on files e-h")

    bucket, inside_bucket = divmod(physical_index, PHYSICAL_BUCKET_WIDTH)
    own_king_bucket = (7 - oriented_own_king // 8) * 4 + (7 - oriented_own_king % 8)
    if bucket != own_king_bucket:
        raise HMContractError(
            "physical row bucket does not match the oriented own-king square"
        )
    p_plane, piece_square = divmod(inside_bucket, SQUARES)
    if p_plane < 10:
        t_plane = p_plane
    else:
        # The physical king plane is initialized from OPP_KING, then the own
        # king cell alone is overwritten from OWN_KING.
        t_plane = 10 if piece_square == oriented_own_king else 11
    training_index = bucket * TRAINING_BUCKET_WIDTH + t_plane * SQUARES + piece_square
    virtual_index = t_plane * SQUARES + piece_square
    return ExportSource(
        physical_index,
        bucket,
        p_plane,
        piece_square,
        t_plane,
        training_index,
        virtual_index,
    )


def network_bucket(piece_count: int) -> int:
    if isinstance(piece_count, bool) or not isinstance(piece_count, int):
        raise HMContractError("piece_count must be an integer")
    if not 2 <= piece_count <= 32:
        raise HMContractError("evaluable network bucket requires 2..32 pieces")
    return max(0, min(7, (piece_count - 1) // 4))


def synthetic_bucket_weight(training_index: int, output: int) -> int:
    """Deterministic signed-i16 bucket row used only by the golden oracle."""

    if (
        isinstance(training_index, bool)
        or not isinstance(training_index, int)
        or not 0 <= training_index < TRAINING_DIMENSIONS
    ):
        raise HMContractError("training index is outside the HM tensor")
    if (
        isinstance(output, bool)
        or not isinstance(output, int)
        or not 0 <= output < OUTPUTS
    ):
        raise HMContractError("output must be in 0..1031")
    return (training_index * 73 + output * 19 + 11) % 24001 - 12000


def synthetic_virtual_weight(virtual_index: int, output: int) -> int:
    """Deterministic virtual-factor weight used only by the golden oracle."""

    if (
        isinstance(virtual_index, bool)
        or not isinstance(virtual_index, int)
        or not 0 <= virtual_index < VIRTUAL_DIMENSIONS
    ):
        raise HMContractError("virtual index is outside the HM factor tensor")
    if (
        isinstance(output, bool)
        or not isinstance(output, int)
        or not 0 <= output < OUTPUTS
    ):
        raise HMContractError("output must be in 0..1031")
    return (virtual_index * 43 + output * 29 + 5) % 8001 - 4000


def coalesced_row(physical_index: int, oriented_own_king: int) -> tuple[int, ...]:
    """Coalesce all 1,032 columns before accumulator/PSQT separation."""

    source = export_source(physical_index, oriented_own_king)
    return tuple(
        synthetic_bucket_weight(source.training_index, output)
        + synthetic_virtual_weight(source.virtual_index, output)
        for output in range(OUTPUTS)
    )


def accumulated_outputs(position: HMPosition, perspective: str) -> tuple[int, ...]:
    """Independent i32 full refresh over the physical runtime activations."""

    king = _own_king(position, perspective)
    orient = orientation_for(perspective, king.square)
    totals = [0] * OUTPUTS
    for activation in enumerate_hm(position, perspective):
        row = coalesced_row(activation.physical_index, orient.oriented_own_king)
        for output, value in enumerate(row):
            totals[output] += value
    if any(not -(1 << 31) <= value < (1 << 31) for value in totals):
        raise HMContractError("synthetic full-refresh result exceeded signed i32")
    return tuple(totals)


def _u32_triplet_sha256(activations: Iterable[HMActivation]) -> str:
    digest = hashlib.sha256()
    rows = sorted(
        (item.training_index, item.virtual_index, item.physical_index)
        for item in activations
    )
    for training_index, virtual_index, physical_index in rows:
        digest.update(
            struct.pack("<III", training_index, virtual_index, physical_index)
        )
    return digest.hexdigest()


def i32le_sha256(values: Iterable[int]) -> str:
    """Hash a sequence using exact signed-i32 little-endian wire values."""

    digest = hashlib.sha256()
    for value in values:
        if isinstance(value, bool) or not isinstance(value, int):
            raise HMContractError("digest values must be integers")
        if not -(1 << 31) <= value < (1 << 31):
            raise HMContractError("digest value is outside signed i32")
        digest.update(struct.pack("<i", value))
    return digest.hexdigest()


def hm_snapshot(position: HMPosition, perspective: str) -> dict[str, object]:
    """Return the canonical compact payload authenticated by the golden fixture."""

    king = _own_king(position, perspective)
    orient = orientation_for(perspective, king.square)
    activations = enumerate_hm(position, perspective)
    outputs = accumulated_outputs(position, perspective)
    selected_bucket = network_bucket(len(position.pieces))
    sentinel_outputs = (0, 1023, *range(1024, 1032))
    return {
        "orientation": {
            "vertical_xor": orient.vertical_xor,
            "horizontal_xor": orient.horizontal_xor,
            "oriented_own_king": square_name(orient.oriented_own_king),
            "king_bucket": orient.king_bucket,
        },
        "piece_count": len(position.pieces),
        "network_bucket": selected_bucket,
        "training_indices": [item.training_index for item in activations],
        "virtual_indices": [item.virtual_index for item in activations],
        "physical_indices": [item.physical_index for item in activations],
        "activation_sha256": _u32_triplet_sha256(activations),
        "outputs_i32le_sha256": i32le_sha256(outputs),
        "output_sentinels": {str(index): outputs[index] for index in sentinel_outputs},
        "psqt": list(outputs[ACCUMULATOR_OUTPUTS:]),
        "selected_psqt": outputs[ACCUMULATOR_OUTPUTS + selected_bucket],
    }


def horizontally_mirrored(position: HMPosition) -> HMPosition:
    """Mirror board squares; metadata is preserved except for an EP square."""

    return HMPosition(
        tuple(Piece(piece.color, piece.kind, piece.square ^ 7) for piece in position.pieces),
        side_to_move=position.side_to_move,
        ep_square=None if position.ep_square is None else position.ep_square ^ 7,
        atomic960=position.atomic960,
        castling_rights=position.castling_rights,
    )
