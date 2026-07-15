#!/usr/bin/env python3
"""Strict cross-language differential for AtomicNNUEV3 scalar composition."""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import random
import subprocess
import sys
from typing import Dict, Iterable, List, Mapping, Optional, Sequence, Tuple


ROOT = Path(__file__).resolve().parents[1]
PYTHON_DIR = ROOT / "tests" / "python"
sys.path.insert(0, str(PYTHON_DIR))

import atomic_v3_capture_pair_reference as cp  # noqa: E402
import atomic_v3_full_refresh_reference as reference  # noqa: E402
import atomic_v3_hm_reference as hm  # noqa: E402


FROZEN_CORPUS_SHA256 = "22ae9a6188fa0ebdd0faff9b4a23c25d25380f9b47ebc0e9da2d1b28fe2441b6"
RANDOM_SEED = 0xA703F001


class DifferentialFailure(RuntimeError):
    pass


def _piece(color: str, kind: str, name: str) -> cp.Piece:
    return cp.Piece(color, kind, cp.square(name))


def _position(
    pieces: Sequence[cp.Piece],
    *,
    side_to_move: str = cp.WHITE,
    ep_square: Optional[str] = None,
    atomic960: bool = False,
    castling_rights: str = "-",
) -> cp.CapturePosition:
    return cp.CapturePosition(
        tuple(pieces),
        side_to_move=side_to_move,
        ep_square=None if ep_square is None else cp.square(ep_square),
        atomic960=atomic960,
        castling_rights=castling_rights,
    )


def _fixed_positions() -> List[cp.CapturePosition]:
    return [
        _position(
            [
                _piece(cp.WHITE, cp.KING, "c1"),
                _piece(cp.BLACK, cp.KING, "c8"),
                _piece(cp.WHITE, cp.QUEEN, "d4"),
                _piece(cp.BLACK, cp.ROOK, "d5"),
                _piece(cp.BLACK, cp.KNIGHT, "e5"),
                _piece(cp.WHITE, cp.PAWN, "e4"),
                _piece(cp.WHITE, cp.BISHOP, "c5"),
                _piece(cp.BLACK, cp.PAWN, "c4"),
            ]
        ),
        _position(
            [
                _piece(cp.WHITE, cp.KING, "a1"),
                _piece(cp.BLACK, cp.KING, "h8"),
                _piece(cp.WHITE, cp.PAWN, "e5"),
                _piece(cp.BLACK, cp.PAWN, "d5"),
                _piece(cp.WHITE, cp.ROOK, "c5"),
                _piece(cp.BLACK, cp.BISHOP, "d7"),
            ],
            ep_square="d6",
        ),
        _position(
            [
                _piece(cp.WHITE, cp.KING, "a1"),
                _piece(cp.BLACK, cp.KING, "h8"),
                _piece(cp.WHITE, cp.PAWN, "e5"),
                _piece(cp.WHITE, cp.ROOK, "c5"),
                _piece(cp.BLACK, cp.BISHOP, "d7"),
            ],
            ep_square="d6",  # malformed metadata must preserve normal rows
        ),
        _position(
            [
                _piece(cp.WHITE, cp.KING, "d4"),
                _piece(cp.BLACK, cp.KING, "e4"),
                _piece(cp.WHITE, cp.ROOK, "d2"),
                _piece(cp.BLACK, cp.QUEEN, "e2"),
                _piece(cp.WHITE, cp.KNIGHT, "c3"),
            ]
        ),
        _position(
            [
                _piece(cp.WHITE, cp.KING, "a1"),
                _piece(cp.BLACK, cp.KING, "h7"),
                _piece(cp.WHITE, cp.PAWN, "g7"),
                _piece(cp.BLACK, cp.ROOK, "h8"),
                _piece(cp.BLACK, cp.BISHOP, "f8"),
                _piece(cp.WHITE, cp.KNIGHT, "g6"),
            ]
        ),
        _position(
            [
                _piece(cp.WHITE, cp.KING, "c1"),
                _piece(cp.BLACK, cp.KING, "c8"),
                _piece(cp.WHITE, cp.ROOK, "a1"),
                _piece(cp.WHITE, cp.ROOK, "h1"),
                _piece(cp.BLACK, cp.ROOK, "a8"),
                _piece(cp.BLACK, cp.ROOK, "h8"),
                _piece(cp.WHITE, cp.QUEEN, "d4"),
                _piece(cp.BLACK, cp.BISHOP, "e4"),
            ],
            atomic960=True,
            castling_rights="AHah",
        ),
    ]


def _random_positions(count: int = 96) -> List[cp.CapturePosition]:
    rng = random.Random(RANDOM_SEED)
    kinds = (cp.PAWN, cp.KNIGHT, cp.BISHOP, cp.ROOK, cp.QUEEN)
    result: List[cp.CapturePosition] = []
    for sample in range(count):
        squares = list(range(64))
        rng.shuffle(squares)
        white_king = squares.pop()
        black_king = squares.pop()
        white_extra = sample % 15
        black_extra = (sample * 7 + 3) % 15
        pieces = [
            cp.Piece(cp.WHITE, cp.KING, white_king),
            cp.Piece(cp.BLACK, cp.KING, black_king),
        ]
        for _ in range(white_extra):
            pieces.append(cp.Piece(cp.WHITE, rng.choice(kinds), squares.pop()))
        for _ in range(black_extra):
            pieces.append(cp.Piece(cp.BLACK, rng.choice(kinds), squares.pop()))
        result.append(
            cp.CapturePosition(
                tuple(pieces), side_to_move=cp.WHITE if sample % 2 == 0 else cp.BLACK
            )
        )
    return result


def corpus() -> Tuple[cp.CapturePosition, ...]:
    return tuple(_fixed_positions() + _random_positions())


def _identity(position: cp.CapturePosition, perspective: str) -> Mapping[str, object]:
    return {
        "pieces": sorted(
            ([piece.color, piece.kind, piece.square] for piece in position.pieces),
            key=lambda row: int(row[2]),
        ),
        "side_to_move": position.side_to_move,
        "ep_square": position.ep_square,
        "atomic960": position.atomic960,
        "castling_rights": position.castling_rights,
        "perspective": perspective,
    }


def corpus_digest(values: Iterable[cp.CapturePosition]) -> str:
    payload = [
        _identity(position, perspective)
        for position in values
        for perspective in cp.COLORS
    ]
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _placement(position: cp.CapturePosition) -> str:
    tokens = {
        (cp.WHITE, cp.PAWN): "P",
        (cp.WHITE, cp.KNIGHT): "N",
        (cp.WHITE, cp.BISHOP): "B",
        (cp.WHITE, cp.ROOK): "R",
        (cp.WHITE, cp.QUEEN): "Q",
        (cp.WHITE, cp.KING): "K",
        (cp.BLACK, cp.PAWN): "p",
        (cp.BLACK, cp.KNIGHT): "n",
        (cp.BLACK, cp.BISHOP): "b",
        (cp.BLACK, cp.ROOK): "r",
        (cp.BLACK, cp.QUEEN): "q",
        (cp.BLACK, cp.KING): "k",
    }
    occupied = {piece.square: tokens[(piece.color, piece.kind)] for piece in position.pieces}
    ranks: List[str] = []
    for rank in range(7, -1, -1):
        row = ""
        empty = 0
        for file_index in range(8):
            token = occupied.get(rank * 8 + file_index)
            if token is None:
                empty += 1
                continue
            if empty:
                row += str(empty)
                empty = 0
            row += token
        if empty:
            row += str(empty)
        ranks.append(row)
    return "/".join(ranks)


def _parse_tokens(line: str) -> Dict[str, str]:
    result: Dict[str, str] = {}
    for token in line.split():
        if "=" not in token:
            continue
        key, value = token.split("=", 1)
        result[key] = value
    return result


def _parse_output(stdout: str) -> Mapping[str, object]:
    header: Optional[Dict[str, str]] = None
    slices: Dict[str, List[int]] = {
        "hm": [],
        "capture_pair": [],
        "king_blast_ep": [],
        "blast_ring": [],
    }
    for line in stdout.splitlines():
        tokens = _parse_tokens(line)
        if tokens.get("record") == "full_refresh":
            if header is not None:
                raise DifferentialFailure("snapshot oracle returned multiple records")
            header = tokens
        elif "slice" in tokens and "physical" in tokens:
            slice_name = tokens["slice"]
            if slice_name not in slices:
                raise DifferentialFailure(f"unknown C++ slice {slice_name!r}")
            slices[slice_name].append(int(tokens["physical"]))
    if header is None:
        raise DifferentialFailure(f"C++ oracle emitted no full-refresh header:\n{stdout}")
    return {"header": header, "slices": {key: tuple(value) for key, value in slices.items()}}


def _run_oracle(
    oracle: Path, position: cp.CapturePosition, perspective: str
) -> Mapping[str, object]:
    command = [
        str(oracle),
        "--snapshot",
        perspective.lower(),
        position.side_to_move.lower(),
        "-" if position.ep_square is None else cp.square_name(position.ep_square),
        _placement(position),
    ]
    completed = subprocess.run(command, check=False, capture_output=True, text=True)
    if completed.returncode != 0:
        raise DifferentialFailure(
            f"C++ oracle failed ({completed.returncode}): {completed.stderr.strip()}"
        )
    return _parse_output(completed.stdout)


def _check_error_snapshots(oracle: Path) -> int:
    cases = (
        ("white", "white", "-", "7k/8/8/8/8/8/8/8", "missing_white_king", 3),
        ("white", "white", "-", "8/8/8/8/8/8/8/K7", "missing_black_king", 4),
        ("white", "white", "-", "7k/8/8/8/8/8/8/KK6", "multiple_white_kings", 5),
        ("black", "black", "-", "kk6/8/8/8/8/8/8/K7", "multiple_black_kings", 6),
    )
    for perspective, side_to_move, ep_square, placement, error_name, error_code in cases:
        completed = subprocess.run(
            [
                str(oracle),
                "--snapshot",
                perspective,
                side_to_move,
                ep_square,
                placement,
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        if completed.returncode != 0:
            raise DifferentialFailure(
                f"C++ error snapshot failed ({completed.returncode}): "
                f"{completed.stderr.strip()}"
            )
        actual = _parse_output(completed.stdout)
        header = actual["header"]
        if not isinstance(header, dict):
            raise DifferentialFailure("error snapshot header has the wrong type")
        if header.get("error") != error_name or int(header["error_code"]) != error_code:
            raise DifferentialFailure(
                f"error snapshot mapped incorrectly: {header}"
            )
        if any(int(header[key]) != 0 for key in (*actual["slices"], "total")):
            raise DifferentialFailure(f"error snapshot leaked a partial slice: {header}")
        if any(actual["slices"].values()):
            raise DifferentialFailure("error snapshot emitted feature records")
    return len(cases)


def _check_case(oracle: Path, position: cp.CapturePosition, perspective: str) -> None:
    expected = reference.enumerate_full_refresh(position, perspective)
    actual = _run_oracle(oracle, position, perspective)
    header = actual["header"]
    if not isinstance(header, dict):
        raise DifferentialFailure("parsed header has the wrong type")
    if header.get("error") != "none" or int(header["error_code"]) != 0:
        raise DifferentialFailure(f"C++ rejected valid corpus position: {header}")

    expected_indices = expected.physical_indices()
    if actual["slices"] != expected_indices:
        raise DifferentialFailure(
            "slice indices differ for "
            + json.dumps(_identity(position, perspective), sort_keys=True)
        )

    counts = {
        "hm": len(expected.hm),
        "capture_pair": len(expected.capture_pairs),
        "king_blast_ep": len(expected.king_blast_ep),
        "blast_ring": len(expected.blast_ring),
    }
    for key, value in counts.items():
        if int(header[key]) != value:
            raise DifferentialFailure(f"C++ {key} count differs: {header[key]} != {value}")
    if int(header["total"]) != expected.active_feature_count:
        raise DifferentialFailure("C++ aggregate active count differs")
    if int(header["vertical_xor"]) != expected.orientation.vertical_xor:
        raise DifferentialFailure("vertical orientation differs")
    if int(header["horizontal_xor"]) != expected.orientation.horizontal_xor:
        raise DifferentialFailure("horizontal orientation differs")
    if int(header["oriented_own_king"]) != expected.orientation.oriented_own_king:
        raise DifferentialFailure("oriented own king differs")
    raw_own_king = next(
        piece.square
        for piece in position.pieces
        if piece.color == perspective and piece.kind == cp.KING
    )
    if int(header["king_bucket"]) != hm.orientation_for(
        perspective, raw_own_king
    ).king_bucket:
        raise DifferentialFailure("HM king bucket differs")
    if int(header["network_bucket"]) != expected.network_bucket:
        raise DifferentialFailure("network bucket differs")


def run(oracle: Path) -> None:
    values = corpus()
    digest = corpus_digest(values)
    if digest != FROZEN_CORPUS_SHA256:
        raise DifferentialFailure(
            f"full-refresh corpus identity changed: {digest} != {FROZEN_CORPUS_SHA256}"
        )

    cases = tuple((position, perspective) for position in values for perspective in cp.COLORS)
    for position, perspective in cases:
        _check_case(oracle, position, perspective)

    error_cases = _check_error_snapshots(oracle)

    # Process-level replay complements the in-process 1/2/4/8-thread C++ gate
    # and proves that the CLI surface has no hidden shared scratch state.
    replay = cases[:8]
    for workers in (1, 2, 4, 8):
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = [pool.submit(_check_case, oracle, *case) for case in replay]
            for future in futures:
                future.result()

    print(
        "Atomic V3 full-refresh differential passed: "
        f"positions={len(values)} perspectives={len(cases)} errors={error_cases} digest={digest}"
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--oracle", type=Path, help="path to the C++ full-refresh CLI")
    parser.add_argument("--print-corpus-digest", action="store_true")
    args = parser.parse_args()
    if args.print_corpus_digest:
        print(corpus_digest(corpus()))
        return 0
    if args.oracle is None:
        parser.error("--oracle is required unless --print-corpus-digest is used")
    run(args.oracle.resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
