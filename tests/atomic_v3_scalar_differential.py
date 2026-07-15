#!/usr/bin/env python3
"""Strict independent full-forward differential for AtomicNNUEV3 scalar.

The C++ runner loads the authenticated network once, while Python derives all
expected parameters from an independent sparse second pass over the frozen
wire. Every published feature row, accumulator, PSQT value, transformed byte,
dense intermediate and final scale is compared exactly.
"""

from __future__ import annotations

import argparse
from pathlib import Path
import subprocess
import sys
from typing import Dict, Mapping, Optional, Sequence, Tuple


ROOT = Path(__file__).resolve().parents[1]
PYTHON_DIR = ROOT / "tests" / "python"
sys.path.insert(0, str(PYTHON_DIR))
sys.path.insert(0, str(ROOT / "tests"))

import atomic_v3_blast_ring_reference as blast_ring  # noqa: E402
import atomic_v3_capture_pair_reference as cp  # noqa: E402
import atomic_v3_full_refresh_differential as h93f  # noqa: E402
import atomic_v3_full_refresh_reference as full_refresh  # noqa: E402
import atomic_v3_king_blast_ep_reference as king_blast_ep  # noqa: E402
import atomic_v3_scalar_reference as reference  # noqa: E402


FROZEN_CORPUS_SHA256 = "22ae9a6188fa0ebdd0faff9b4a23c25d25380f9b47ebc0e9da2d1b28fe2441b6"


class DifferentialFailure(RuntimeError):
    pass


def _piece(color: str, kind: str, name: str) -> cp.Piece:
    return cp.Piece(color, kind, cp.square(name))


def _row_target_positions() -> Tuple[cp.CapturePosition, ...]:
    """Activate every row-zero sentinel and signed odd PSQT truncation."""

    return (
        cp.CapturePosition(
            (
                _piece(cp.WHITE, cp.KING, "h8"),
                _piece(cp.BLACK, cp.KING, "h1"),
                _piece(cp.WHITE, cp.PAWN, "a1"),
            ),
            side_to_move=cp.WHITE,
        ),
        cp.CapturePosition(
            (
                _piece(cp.WHITE, cp.KING, "h1"),
                _piece(cp.BLACK, cp.KING, "h8"),
                _piece(cp.WHITE, cp.PAWN, "a2"),
                _piece(cp.BLACK, cp.PAWN, "b3"),
            ),
            side_to_move=cp.WHITE,
        ),
        cp.CapturePosition(
            (
                _piece(cp.WHITE, cp.KING, "h8"),
                _piece(cp.BLACK, cp.KING, "a1"),
                _piece(cp.WHITE, cp.KNIGHT, "b3"),
                _piece(cp.WHITE, cp.KNIGHT, "a2"),
            ),
            side_to_move=cp.WHITE,
        ),
        cp.CapturePosition(
            (
                _piece(cp.WHITE, cp.KING, "h8"),
                _piece(cp.BLACK, cp.KING, "h1"),
                _piece(cp.WHITE, cp.PAWN, "h4"),
            ),
            side_to_move=cp.WHITE,
        ),
    )


def _standard_start() -> cp.CapturePosition:
    pieces = [
        _piece(cp.WHITE, cp.KING, "e1"),
        _piece(cp.WHITE, cp.QUEEN, "d1"),
        _piece(cp.WHITE, cp.ROOK, "a1"),
        _piece(cp.WHITE, cp.ROOK, "h1"),
        _piece(cp.WHITE, cp.BISHOP, "c1"),
        _piece(cp.WHITE, cp.BISHOP, "f1"),
        _piece(cp.WHITE, cp.KNIGHT, "b1"),
        _piece(cp.WHITE, cp.KNIGHT, "g1"),
        _piece(cp.BLACK, cp.KING, "e8"),
        _piece(cp.BLACK, cp.QUEEN, "d8"),
        _piece(cp.BLACK, cp.ROOK, "a8"),
        _piece(cp.BLACK, cp.ROOK, "h8"),
        _piece(cp.BLACK, cp.BISHOP, "c8"),
        _piece(cp.BLACK, cp.BISHOP, "f8"),
        _piece(cp.BLACK, cp.KNIGHT, "b8"),
        _piece(cp.BLACK, cp.KNIGHT, "g8"),
    ]
    for file_name in "abcdefgh":
        pieces.append(_piece(cp.WHITE, cp.PAWN, file_name + "2"))
        pieces.append(_piece(cp.BLACK, cp.PAWN, file_name + "7"))
    return cp.CapturePosition(tuple(pieces), side_to_move=cp.WHITE)


def _bucket_probe_positions() -> Tuple[cp.CapturePosition, ...]:
    """Supply the 0/2/7 dense buckets absent from the frozen corpus."""

    bucket_zero = cp.CapturePosition(
        (
            _piece(cp.WHITE, cp.KING, "a1"),
            _piece(cp.BLACK, cp.KING, "h8"),
        ),
        side_to_move=cp.BLACK,
    )
    bucket_two = cp.CapturePosition(
        (
            _piece(cp.WHITE, cp.KING, "a1"),
            _piece(cp.BLACK, cp.KING, "h8"),
            _piece(cp.WHITE, cp.PAWN, "a2"),
            _piece(cp.WHITE, cp.PAWN, "b2"),
            _piece(cp.WHITE, cp.PAWN, "c2"),
            _piece(cp.WHITE, cp.PAWN, "d2"),
            _piece(cp.BLACK, cp.PAWN, "e7"),
            _piece(cp.BLACK, cp.PAWN, "f7"),
            _piece(cp.BLACK, cp.PAWN, "g7"),
        ),
        side_to_move=cp.WHITE,
    )
    return bucket_zero, bucket_two, _standard_start()


def _batch_input(positions: Sequence[cp.CapturePosition]) -> str:
    return "".join(
        f"{position.side_to_move.lower()} "
        f"{'-' if position.ep_square is None else cp.square_name(position.ep_square)} "
        f"{h93f._placement(position)}\n"
        for position in positions
    )


def _parse_array(value: str) -> Tuple[int, ...]:
    return () if not value else tuple(int(item) for item in value.split(","))


_SCALAR_KEYS = {
    "side_to_move",
    "network_bucket",
    "psqt_difference",
    "psqt_value",
    "raw_output",
    "scaled_output",
    "positional_value",
}


def _parse_batch(output: str, expected_cases: int) -> Tuple[Dict[str, object], ...]:
    cases = []
    current: Optional[Dict[str, object]] = None
    saw_count = False
    for line in output.splitlines():
        if line.startswith("case="):
            if current is not None or int(line.split("=", 1)[1]) != len(cases):
                raise DifferentialFailure("C++ scalar batch case framing is noncanonical")
            current = {}
            continue
        if line.startswith("end_case="):
            if current is None or int(line.split("=", 1)[1]) != len(cases):
                raise DifferentialFailure("C++ scalar batch end framing is noncanonical")
            cases.append(current)
            current = None
            continue
        if line.startswith("batch_cases="):
            if (
                current is not None
                or saw_count
                or int(line.split("=", 1)[1]) != expected_cases
            ):
                raise DifferentialFailure("C++ scalar batch count differs")
            saw_count = True
            continue
        if current is None:
            raise DifferentialFailure(f"C++ scalar emitted unframed output: {line!r}")
        key, separator, value = line.partition("=")
        if not separator or key in current:
            raise DifferentialFailure(
                f"C++ scalar emitted malformed/duplicate key: {line!r}"
            )
        if key == "fingerprint":
            current[key] = value
        elif key in _SCALAR_KEYS:
            current[key] = int(value)
        else:
            current[key] = _parse_array(value)
    if current is not None or len(cases) != expected_cases or not saw_count:
        raise DifferentialFailure("C++ scalar batch output was truncated")
    return tuple(cases)


def _first_difference(actual: object, expected: object) -> str:
    if isinstance(actual, tuple) and isinstance(expected, tuple):
        if len(actual) != len(expected):
            return f"length {len(actual)} != {len(expected)}"
        for index, (actual_value, expected_value) in enumerate(zip(actual, expected)):
            if actual_value != expected_value:
                return f"index {index}: {actual_value} != {expected_value}"
    return f"{actual!r} != {expected!r}"


def _compare_mapping(
    label: str, actual: Mapping[str, object], expected: Mapping[str, object]
) -> None:
    ignored = {"fingerprint", "dense_fingerprint"}
    actual_keys = set(actual) - ignored
    if actual_keys != set(expected):
        raise DifferentialFailure(
            f"{label}: diagnostic keys differ: "
            f"actual={sorted(actual_keys)} expected={sorted(expected)}"
        )
    for key, expected_value in expected.items():
        actual_value = actual.get(key)
        if actual_value != expected_value:
            raise DifferentialFailure(
                f"{label} {key} differs at "
                f"{_first_difference(actual_value, expected_value)}"
            )


def _check_supplemental(
    network: reference.SparseNetwork,
    row_targets: Sequence[cp.CapturePosition],
    bucket_probes: Sequence[cp.CapturePosition],
    frozen: Sequence[cp.CapturePosition],
) -> None:
    hm = full_refresh.enumerate_full_refresh(row_targets[0], cp.WHITE)
    capture = full_refresh.enumerate_full_refresh(row_targets[1], cp.WHITE)
    relation = full_refresh.enumerate_full_refresh(row_targets[2], cp.WHITE)
    if 0 not in hm.physical_indices()["hm"] or not network.hm.get(0):
        raise DifferentialFailure("targeted corpus no longer activates nonzero HM row zero")
    if not network.hm_psqt.get(0):
        raise DifferentialFailure("targeted corpus lost nonzero HM PSQT row zero")
    if (
        cp.PHYSICAL_OFFSET not in capture.physical_indices()["capture_pair"]
        or not network.capture_pair.get(0)
    ):
        raise DifferentialFailure(
            "targeted corpus no longer activates nonzero CapturePair row zero"
        )
    if (
        king_blast_ep.PHYSICAL_OFFSET
        not in relation.physical_indices()["king_blast_ep"]
        or not network.king_blast_ep.get(0)
    ):
        raise DifferentialFailure(
            "targeted corpus no longer activates nonzero KingBlastEP row zero"
        )
    if (
        blast_ring.PHYSICAL_OFFSET not in relation.physical_indices()["blast_ring"]
        or not network.blast_ring.get(0)
    ):
        raise DifferentialFailure(
            "targeted corpus no longer activates nonzero BlastRing row zero"
        )

    odd = reference.evaluate(network, row_targets[3])
    odd_emission = full_refresh.enumerate_full_refresh(row_targets[3], cp.WHITE)
    if (
        31 not in odd_emission.physical_indices()["hm"]
        or odd["psqt_difference"] != -33_554_431
        or odd["psqt_value"] != -2_097_151
    ):
        raise DifferentialFailure(
            "negative odd PSQT /2 then /16 truncation probe drifted"
        )

    probe_buckets = {
        reference.evaluate(network, position)["network_bucket"]
        for position in bucket_probes
    }
    if probe_buckets != {0, 2, 7}:
        raise DifferentialFailure(f"supplemental dense buckets differ: {probe_buckets}")
    all_buckets = probe_buckets | {
        reference.evaluate(network, position)["network_bucket"]
        for position in frozen
    }
    if all_buckets != set(range(8)):
        raise DifferentialFailure(f"scalar differential misses buckets: {all_buckets}")


def _parse_dense_output(output: str) -> Dict[str, object]:
    result: Dict[str, object] = {}
    for line in output.splitlines():
        key, separator, value = line.partition("=")
        if not separator or key in result:
            raise DifferentialFailure(
                f"C++ adversarial dense output is malformed/duplicate: {line!r}"
            )
        if key == "dense_fingerprint":
            result[key] = value
        elif key in {"raw_output", "scaled_output", "positional_value"}:
            result[key] = int(value)
        else:
            result[key] = _parse_array(value)
    return result


def _check_adversarial_dense(oracle: Path, fixture: Path) -> None:
    for negative, name in ((False, "positive"), (True, "negative")):
        transformed, stack = reference.adversarial_dense_vector(negative)
        expected = {"dense_input": transformed}
        expected.update(reference.propagate_dense(transformed, stack))
        completed = subprocess.run(
            [str(oracle), str(fixture), "--dense-vector", name],
            text=True,
            capture_output=True,
            check=False,
            timeout=60,
        )
        if completed.returncode != 0:
            raise DifferentialFailure(
                f"C++ adversarial dense {name} failed ({completed.returncode}):\n"
                f"stdout:\n{completed.stdout}\nstderr:\n{completed.stderr}"
            )
        _compare_mapping(
            f"adversarial dense {name}",
            _parse_dense_output(completed.stdout),
            expected,
        )


def run(oracle: Path, fixture: Path) -> None:
    oracle = oracle.expanduser().resolve()
    fixture = fixture.expanduser().resolve()
    network = reference.load_frozen_fixture(fixture)

    frozen = h93f.corpus()
    digest = h93f.corpus_digest(frozen)
    if len(frozen) != 102 or digest != FROZEN_CORPUS_SHA256:
        raise DifferentialFailure(
            f"H9.3f corpus identity differs: positions={len(frozen)} digest={digest}"
        )
    row_targets = _row_target_positions()
    bucket_probes = _bucket_probe_positions()
    _check_supplemental(network, row_targets, bucket_probes, frozen)
    positions = tuple(frozen) + row_targets + bucket_probes

    completed = subprocess.run(
        [str(oracle), str(fixture), "--batch"],
        input=_batch_input(positions),
        text=True,
        capture_output=True,
        check=False,
        timeout=300,
    )
    if completed.returncode != 0:
        raise DifferentialFailure(
            f"C++ scalar batch failed ({completed.returncode}):\n"
            f"stdout:\n{completed.stdout}\nstderr:\n{completed.stderr}"
        )
    actual = _parse_batch(completed.stdout, len(positions))
    for index, (position, diagnostic) in enumerate(zip(positions, actual)):
        _compare_mapping(
            f"case {index}", diagnostic, reference.evaluate(network, position)
        )

    _check_adversarial_dense(oracle, fixture)
    print(
        "Atomic V3 scalar differential passed: "
        f"frozen_positions={len(frozen)} row_targets={len(row_targets)} "
        f"bucket_probes={len(bucket_probes)} dense_vectors=2 "
        f"corpus_digest={digest}"
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
