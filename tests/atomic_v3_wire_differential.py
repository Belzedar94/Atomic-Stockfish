#!/usr/bin/env python3
"""Cross-language differential for the frozen AtomicNNUEV3 mixed wire."""

from __future__ import annotations

import argparse
import filecmp
from pathlib import Path
import shutil
import subprocess
import sys
from tempfile import TemporaryDirectory
from typing import Mapping, Optional, Sequence


ROOT = Path(__file__).resolve().parents[1]
PYTHON_DIR = ROOT / "tests" / "python"
sys.path.insert(0, str(PYTHON_DIR))
sys.path.insert(0, str(ROOT / "tests"))

import atomic_v3_wire_reference as wire  # noqa: E402
import create_synthetic_atomic_v3_nnue as generator  # noqa: E402


class DifferentialFailure(RuntimeError):
    pass


def _run(
    oracle: Path,
    *arguments: str,
    expect_success: bool = True,
    expected_stderr: Optional[str] = None,
) -> str:
    completed = subprocess.run(
        [str(oracle), *arguments],
        check=False,
        capture_output=True,
        text=True,
        timeout=300,
    )
    if expect_success and completed.returncode != 0:
        raise DifferentialFailure(
            f"C++ {' '.join(arguments)} failed ({completed.returncode}):\n"
            f"stdout:\n{completed.stdout}\nstderr:\n{completed.stderr}"
        )
    if not expect_success and completed.returncode == 0:
        raise DifferentialFailure(
            f"C++ {' '.join(arguments)} unexpectedly succeeded:\n{completed.stdout}"
        )
    if expected_stderr is not None and completed.stderr != expected_stderr:
        raise DifferentialFailure(
            f"C++ {' '.join(arguments)} stderr differs:\n"
            f"actual={completed.stderr!r}\nexpected={expected_stderr!r}"
        )
    return completed.stdout


def _pairs(output: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for line in output.splitlines():
        stripped = line.strip()
        if not stripped or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        if key in result:
            raise DifferentialFailure(f"duplicate C++ output key: {key}")
        result[key] = value
    return result


def _require_pairs(actual: Mapping[str, str], expected: Mapping[str, str]) -> None:
    for key, value in expected.items():
        if actual.get(key) != value:
            raise DifferentialFailure(
                f"C++ {key} differs: {actual.get(key)!r} != {value!r}"
            )


def _hash_contract(oracle: Path) -> None:
    wire.assert_hash_contract()
    output = _pairs(_run(oracle, "hash-contract"))
    expected = {
        "file_version": f"0x{wire.FILE_VERSION:08X}",
        "hm_hash": f"0x{wire.SLICE_HASHES[0]:08X}",
        "capture_pair_hash": f"0x{wire.SLICE_HASHES[1]:08X}",
        "king_blast_ep_hash": f"0x{wire.SLICE_HASHES[2]:08X}",
        "blast_ring_hash": f"0x{wire.SLICE_HASHES[3]:08X}",
        "feature_hash": f"0x{wire.FEATURE_HASH:08X}",
        "descriptor_bytes": str(len(wire.TRANSFORMER_DESCRIPTOR)),
        "descriptor_hash": f"0x{wire.TRANSFORMER_DESCRIPTOR_HASH:08X}",
        "feature_transformer_hash": f"0x{wire.FEATURE_TRANSFORMER_HASH:08X}",
        "architecture_hash": f"0x{wire.ARCHITECTURE_HASH:08X}",
        "network_hash": f"0x{wire.NETWORK_HASH:08X}",
    }
    _require_pairs(output, expected)


def _compare_parsed(
    first: wire.ParsedNetwork, second: wire.ParsedNetwork, context: str
) -> None:
    attributes = (
        "description",
        "size",
        "sha256",
        "selected",
        "psqt_top32_sums",
        "dense_stacks",
    )
    for attribute in attributes:
        if getattr(first, attribute) != getattr(second, attribute):
            raise DifferentialFailure(
                f"{context}: Python parsed {attribute} changed across round-trip"
            )


def _race_roundtrips(
    oracle: Path,
    sources: Sequence[Path],
    target: Path,
) -> int:
    """Race valid writers and return the sole successful source index."""

    processes = [
        subprocess.Popen(
            [str(oracle), "roundtrip", str(source), str(target)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        for source in sources
    ]
    results: list[tuple[int, str, str]] = []
    try:
        for process in processes:
            stdout, stderr = process.communicate(timeout=300)
            results.append((process.returncode, stdout, stderr))
    except BaseException:
        for process in processes:
            if process.poll() is None:
                process.kill()
        for process in processes:
            process.communicate()
        raise

    winners = [index for index, result in enumerate(results) if result[0] == 0]
    if len(winners) != 1:
        raise DifferentialFailure(
            "concurrent C++ no-replace race did not produce exactly one winner: "
            f"results={results!r}"
        )
    winner = winners[0]
    if results[winner][1] != "roundtrip=ok\n" or results[winner][2]:
        raise DifferentialFailure(
            f"concurrent C++ winner produced unexpected output: {results[winner]!r}"
        )
    for index, result in enumerate(results):
        if index == winner:
            continue
        if result != (1, "", "error=roundtrip output already exists\n"):
            raise DifferentialFailure(
                f"concurrent C++ loser did not fail as no-replace: {result!r}"
            )
    return winner


def run(oracle: Path, fixture: Path) -> None:
    if generator.EXPECTED_SIZE is None or not generator.EXPECTED_SHA256:
        raise DifferentialFailure(
            "synthetic V3 fixture size/SHA must be measured and frozen first"
        )
    _hash_contract(oracle)

    python_fixture = wire.parse_network(
        fixture, selected_indices=generator.SELECTED_INDICES
    )
    if python_fixture.size != generator.EXPECTED_SIZE:
        raise DifferentialFailure("Python fixture size differs from the frozen identity")
    if python_fixture.sha256 != generator.EXPECTED_SHA256:
        raise DifferentialFailure("Python fixture SHA-256 differs from the frozen identity")
    if python_fixture.selected != generator.expected_selected_values():
        raise DifferentialFailure("Python fixture sentinel values differ")

    inspected = _pairs(_run(oracle, "inspect", str(fixture)))
    _require_pairs(
        inspected,
        {
            "description_size": str(len(generator.DESCRIPTION)),
            "simd_permuted": "true",
            "layer_stacks": str(wire.LAYER_STACKS),
        },
    )
    expected_selected = {
        f"selected.{name}": str(value)
        for name, value in generator.expected_selected_values().items()
    }
    published_selected = {
        name: value for name, value in inspected.items() if name.startswith("selected.")
    }
    if published_selected != expected_selected:
        missing = sorted(expected_selected.keys() - published_selected.keys())
        extra = sorted(published_selected.keys() - expected_selected.keys())
        mismatched = sorted(
            key
            for key in expected_selected.keys() & published_selected.keys()
            if expected_selected[key] != published_selected[key]
        )
        raise DifferentialFailure(
            "C++ selected internal values differ from the Python fixture: "
            f"missing={missing[:8]} extra={extra[:8]} mismatched={mismatched[:8]}"
        )

    with TemporaryDirectory(prefix="atomic-v3-wire-") as directory:
        temp = Path(directory)
        cpp_output = temp / "cpp-roundtrip.nnue"
        roundtrip_pairs = _pairs(
            _run(oracle, "roundtrip", str(fixture), str(cpp_output))
        )
        _require_pairs(roundtrip_pairs, {"roundtrip": "ok"})
        cpp_parsed = wire.parse_network(
            cpp_output, selected_indices=generator.SELECTED_INDICES
        )
        _compare_parsed(python_fixture, cpp_parsed, "C++")
        if not filecmp.cmp(fixture, cpp_output, shallow=False):
            raise DifferentialFailure("C++ round-trip was not byte exact")

        protected_output = temp / "protected-destination.nnue"
        protected_bytes = b"foreign destination must remain byte exact\x00\xff"
        protected_output.write_bytes(protected_bytes)
        _run(
            oracle,
            "roundtrip",
            str(fixture),
            str(protected_output),
            expect_success=False,
            expected_stderr="error=roundtrip output already exists\n",
        )
        if protected_output.read_bytes() != protected_bytes:
            raise DifferentialFailure(
                "C++ no-overwrite rejection changed the existing destination bytes"
            )

        alternate_input = temp / "alternate-input.nnue"
        shutil.copyfile(fixture, alternate_input)
        with alternate_input.open("r+b") as stream:
            stream.seek(12)
            original = stream.read(1)
            if not original:
                raise DifferentialFailure("fixture has no description byte to vary")
            stream.seek(12)
            stream.write(bytes((original[0] ^ 0x01,)))
        alternate_parsed = wire.parse_network(
            alternate_input, selected_indices=generator.SELECTED_INDICES
        )
        if alternate_parsed.sha256 == python_fixture.sha256:
            raise DifferentialFailure("race inputs must have distinct canonical bytes")

        race_output = temp / "concurrent-roundtrip.nnue"
        race_sources = (fixture, alternate_input)
        winner = _race_roundtrips(oracle, race_sources, race_output)
        race_parsed = wire.parse_network(
            race_output, selected_indices=generator.SELECTED_INDICES
        )
        expected_winner = python_fixture if winner == 0 else alternate_parsed
        _compare_parsed(expected_winner, race_parsed, "C++ concurrent no-replace winner")
        if not filecmp.cmp(race_sources[winner], race_output, shallow=False):
            raise DifferentialFailure(
                "C++ concurrent no-replace winner was not preserved byte exactly"
            )

        python_output = temp / "python-roundtrip.nnue"
        wire.roundtrip_network(fixture, python_output)
        python_parsed = wire.parse_network(
            python_output, selected_indices=generator.SELECTED_INDICES
        )
        _compare_parsed(python_fixture, python_parsed, "Python")

    print(
        "Atomic V3 mixed-wire differential passed: "
        f"size={python_fixture.size} sha256={python_fixture.sha256} "
        f"stacks={len(python_fixture.dense_stacks)}"
    )


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--oracle", type=Path, required=True)
    parser.add_argument("--fixture", type=Path, required=True)
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    run(args.oracle.resolve(), args.fixture.resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
