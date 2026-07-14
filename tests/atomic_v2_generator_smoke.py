#!/usr/bin/env python3
"""Minimal fail-closed AtomicNNUEV2 data-generator integration gate.

The full generator suite is intentionally much broader.  This smoke gate answers one
small release-critical question quickly: can the isolated data-generator load an
AtomicNNUEV2 network, evaluate in ``Use NNUE=pure`` mode, and publish one authenticated
``atomic-bin-v2`` record with matching provenance?
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
import tempfile
from typing import Callable, Sequence


TIMEOUT_SECONDS = 120.0
HEADER_SIZE = 96
RECORD_SIZE = 64
EXPECTED_DATASET_SIZE = HEADER_SIZE + RECORD_SIZE
NETWORK_MARKER = "info string NNUE evaluation using AtomicNNUEV2"
FINAL_MARKER = "INFO: generate_training_data finished."
FINAL_SUMMARY_RE = re.compile(r"^INFO: records=1 draws=(?:0|1)$")
DETERMINISTIC_SEED = "atomic-v2-pure-smoke-v1"
SHA256_RE = re.compile(r"^[0-9a-fA-F]{64}$")


class SmokeError(RuntimeError):
    """Raised when the focused integration contract is not satisfied."""


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def require_file(path: Path, label: str) -> Path:
    resolved = path.expanduser().resolve()
    if not resolved.is_file():
        raise SmokeError(f"{label} does not exist or is not a regular file: {resolved}")
    return resolved


def normalize_sha256(value: str) -> str:
    if SHA256_RE.fullmatch(value) is None:
        raise SmokeError(
            "--expected-net-sha256 must contain exactly 64 hexadecimal characters"
        )
    return value.lower()


def generator_commands(net: Path, output_name: str) -> tuple[str, ...]:
    # Keep the output token a basename.  The generator's UCI grammar is
    # whitespace-delimited, while a private platform temp directory can contain
    # spaces.  Running in that directory preserves cross-platform path safety.
    if Path(output_name).name != output_name or output_name in {"", ".", ".."}:
        raise SmokeError("the smoke output must be a portable basename")

    generation = " ".join(
        (
            "generate_training_data",
            "depth 1",
            "count 1",
            "write_min_ply 0",
            "write_max_ply 2",
            "random_move_min_ply 1",
            "random_move_max_ply 1",
            "random_move_count 0",
            "random_multi_pv 0",
            "keep_draws 1",
            "eval_limit 32000",
            "filter_captures false",
            "filter_checks false",
            "filter_promotions false",
            f"output_file_name {output_name}",
            "data_format atomic-bin-v2",
            f"seed {DETERMINISTIC_SEED}",
        )
    )
    return (
        "uci",
        f"setoption name EvalFile value {net}",
        "setoption name Use NNUE value pure",
        "setoption name Threads value 1",
        "setoption name Hash value 16",
        "isready",
        generation,
        "quit",
    )


def _process_text(value: str | bytes | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value


def run_generator(
    generator: Path,
    commands: Sequence[str],
    *,
    cwd: Path,
    timeout: float,
    runner: Callable[..., subprocess.CompletedProcess[str]] | None = None,
) -> str:
    invoke = subprocess.run if runner is None else runner
    command_stream = "\n".join((*commands, ""))
    try:
        result = invoke(
            [str(generator)],
            input=command_stream,
            text=True,
            encoding="utf-8",
            errors="replace",
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=str(cwd),
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as error:
        partial = _process_text(error.stdout) + _process_text(error.stderr)
        detail = f"\nPartial output:\n{partial}" if partial else ""
        raise SmokeError(
            f"data-generator timed out after {timeout:g} seconds{detail}"
        ) from error
    except OSError as error:
        raise SmokeError(f"could not launch data-generator {generator}: {error}") from error

    output = _process_text(result.stdout) + _process_text(result.stderr)
    if result.returncode != 0:
        raise SmokeError(
            "data-generator failed with exit code "
            f"{result.returncode}:\n{output or '<no output>'}"
        )
    return output


def validate_markers(output: str) -> None:
    lines = [line.strip() for line in output.splitlines() if line.strip()]
    network_positions = [
        index for index, line in enumerate(lines) if NETWORK_MARKER in line
    ]
    final_positions = [
        index for index, line in enumerate(lines) if line == FINAL_MARKER
    ]
    if not network_positions:
        raise SmokeError(
            "data-generator did not report the AtomicNNUEV2 load marker: "
            f"{NETWORK_MARKER!r}"
        )
    if len(final_positions) != 1:
        raise SmokeError(
            "data-generator must report exactly one successful finalization marker; "
            f"found {len(final_positions)}"
        )
    if final_positions[0] < network_positions[0]:
        raise SmokeError("data-generator finalized before reporting the AtomicNNUEV2 load")
    trailing = lines[final_positions[0] + 1 :]
    if len(trailing) != 1 or FINAL_SUMMARY_RE.fullmatch(trailing[0]) is None:
        raise SmokeError(
            "the successful finalization marker must be followed only by the exact "
            "one-record summary"
        )


def _manifest_record_count(value: object) -> int:
    # The frozen manifest currently uses a uint64 decimal string.  Accept an
    # integer too so this focused gate validates the semantic value rather than
    # accidentally hard-coding the JSON representation.
    if isinstance(value, bool):
        raise SmokeError("manifest statistics.records must be an integer, not boolean")
    if isinstance(value, int):
        return value
    if isinstance(value, str) and re.fullmatch(r"0|[1-9][0-9]*", value):
        return int(value)
    raise SmokeError(
        "manifest statistics.records must be an integer or canonical decimal string"
    )


def _require_object(value: object, label: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise SmokeError(f"manifest {label} must be a JSON object")
    return value


def validate_artifacts(
    output_path: Path, manifest_path: Path, *, expected_net_sha256: str
) -> None:
    if not output_path.is_file():
        raise SmokeError(f"data-generator did not publish dataset: {output_path}")
    size = output_path.stat().st_size
    if size != EXPECTED_DATASET_SIZE:
        raise SmokeError(
            "atomic-bin-v2 framing mismatch: expected "
            f"{HEADER_SIZE}+{RECORD_SIZE}={EXPECTED_DATASET_SIZE} bytes, got {size}"
        )
    if not manifest_path.is_file():
        raise SmokeError(f"data-generator did not publish manifest: {manifest_path}")

    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise SmokeError(f"could not parse atomic-bin-v2 manifest: {error}") from error
    root = _require_object(manifest, "root")
    network = _require_object(root.get("network"), "network")
    generation = _require_object(root.get("generation"), "generation")
    statistics = _require_object(root.get("statistics"), "statistics")

    actual_sha256 = network.get("sha256")
    if actual_sha256 != expected_net_sha256:
        raise SmokeError(
            "manifest network.sha256 mismatch: expected "
            f"{expected_net_sha256}, got {actual_sha256!r}"
        )
    if generation.get("use_nnue") != "pure":
        raise SmokeError(
            "manifest generation.use_nnue mismatch: expected 'pure', got "
            f"{generation.get('use_nnue')!r}"
        )
    records = _manifest_record_count(statistics.get("records"))
    if records != 1:
        raise SmokeError(
            f"manifest statistics.records mismatch: expected 1, got {records}"
        )


def _require_output_paths_absent(output_path: Path, manifest_path: Path) -> None:
    existing = [path for path in (output_path, manifest_path) if path.exists()]
    if existing:
        rendered = ", ".join(str(path) for path in existing)
        raise SmokeError(f"refusing to overwrite smoke output path(s): {rendered}")


def run_smoke(
    generator: Path,
    net: Path,
    expected_net_sha256: str,
    *,
    timeout: float = TIMEOUT_SECONDS,
    runner: Callable[..., subprocess.CompletedProcess[str]] | None = None,
) -> str:
    if timeout <= 0:
        raise SmokeError("timeout must be greater than zero")
    generator = require_file(generator, "data-generator")
    net = require_file(net, "AtomicNNUEV2 network")
    expected_net_sha256 = normalize_sha256(expected_net_sha256)
    actual_net_sha256 = sha256(net)
    if actual_net_sha256 != expected_net_sha256:
        raise SmokeError(
            "AtomicNNUEV2 network SHA-256 mismatch before launch: expected "
            f"{expected_net_sha256}, got {actual_net_sha256}"
        )

    with tempfile.TemporaryDirectory(prefix="atomic-v2-generator-smoke-") as temporary:
        root = Path(temporary).resolve()
        output_path = root / "atomic-v2-pure-smoke.atbin"
        manifest_path = Path(f"{output_path}.manifest.json")
        _require_output_paths_absent(output_path, manifest_path)
        output = run_generator(
            generator,
            generator_commands(net, output_path.name),
            cwd=root,
            timeout=timeout,
            runner=runner,
        )
        validate_markers(output)
        validate_artifacts(
            output_path,
            manifest_path,
            expected_net_sha256=expected_net_sha256,
        )

    return (
        "AtomicNNUEV2 pure data-generator smoke passed: "
        f"records=1 bytes={EXPECTED_DATASET_SIZE} "
        f"network_sha256={expected_net_sha256}"
    )


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--generator", required=True, type=Path)
    parser.add_argument("--net", required=True, type=Path)
    parser.add_argument("--expected-net-sha256", required=True)
    parser.add_argument(
        "--timeout-seconds", type=float, default=TIMEOUT_SECONDS, help=argparse.SUPPRESS
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        sentinel = run_smoke(
            args.generator,
            args.net,
            args.expected_net_sha256,
            timeout=args.timeout_seconds,
        )
    except SmokeError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    print(sentinel)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
