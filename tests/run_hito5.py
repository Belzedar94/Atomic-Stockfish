#!/usr/bin/env python3
"""Consolidated LegacyAtomicV1 (Hito 5) release gate.

The default mode is the release contract: it wraps the complete Hito 4 release
runner, executes exactly 1,000,000 deterministic make/undo operations while
comparing incremental accumulators with full refreshes, and runs a 10,000
position structural/protocol differential against frozen Fairy.

Fairy's printed evaluation can contain small non-deterministic or formatting
differences. The differential therefore treats small numeric deltas as
diagnostic, while retaining a deliberately loose finite bound that catches a
gross feature-orientation, king-plane, bucket, or scaling regression.
"""

from __future__ import annotations

import argparse
import hashlib
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Iterable, Sequence


REPO_ROOT = Path(__file__).resolve().parents[1]
WORKSPACE_ROOT = REPO_ROOT.parent
EXPECTED_NET_SHA256 = (
    "99dc67eabf26a64faeeca3a88b4c38597a840b8d4a874b9f2cf658c6f92a04a6"
)
EXPECTED_ORACLE_SHA256 = (
    "1ae6d680f03128c8404f31a3f264f28b132b557ed3a91a6445ec563a7a33f623"
)
RELEASE_INCREMENTAL_OPERATIONS = 1_000_000
RELEASE_DIFFERENTIAL_POSITIONS = 10_000
RELEASE_CORPUS_SHA256 = (
    "46c96f405bc15d468d94bc1e2186b577ce55128832e1108066581d35037fa2de"
)
DIAGNOSTIC_SANITY_TOLERANCE = 0.10

CAPTURE_REFRESH_RE = re.compile(r"\bcapture-forced-refresh=(\d+)\b")
CAPTURE_COUNT_RE = re.compile(r"\bcaptures=(\d+)\b")
CORPUS_SHA_RE = re.compile(r"^Corpus SHA-256: ([0-9A-Fa-f]{64})$", re.MULTILINE)
RULE50_DAMPED_RE = re.compile(r"\brule50-damped=(\d+)\b")
LEGACY_CAPTURE_REFRESH_RE = re.compile(
    r"capture[^\r\n]*requires\s*refresh\s*=\s*(?:true|[1-9]\d*)",
    re.IGNORECASE,
)


class GateFailure(RuntimeError):
    """A release contract was not satisfied."""


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def require_file(path: Path, label: str) -> Path:
    resolved = path.expanduser().resolve()
    if not resolved.is_file():
        raise GateFailure(f"{label} does not exist: {resolved}")
    return resolved


def command_path(value: str, label: str) -> str:
    resolved = shutil.which(value)
    if resolved:
        return resolved
    candidate = Path(value).expanduser().resolve()
    if candidate.is_file():
        return str(candidate)
    raise GateFailure(f"{label} executable was not found: {value}")


def run_step(
    label: str,
    command: Sequence[str],
    *,
    timeout: float,
    required_markers: Iterable[str] = (),
) -> str:
    print(f"\n=== {label} ===", flush=True)
    try:
        completed = subprocess.run(
            list(command),
            cwd=REPO_ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as error:
        raise GateFailure(f"{label} timed out after {timeout:g}s") from error
    except OSError as error:
        raise GateFailure(f"{label} could not start: {error}") from error

    output = completed.stdout or ""
    if output:
        print(output, end="" if output.endswith("\n") else "\n", flush=True)
    if completed.returncode:
        raise GateFailure(f"{label} exited with code {completed.returncode}")
    for marker in required_markers:
        if marker not in output:
            raise GateFailure(f"{label} did not emit required marker: {marker!r}")
    return output


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--native", type=Path, required=True)
    parser.add_argument("--net", type=Path, required=True)
    parser.add_argument("--pyffish", type=Path, required=True)
    parser.add_argument("--cjs", type=Path, required=True)
    parser.add_argument("--esm", type=Path, required=True)
    parser.add_argument("--tables", type=Path, required=True)
    parser.add_argument(
        "--wasm-wrapper",
        type=Path,
        required=True,
        help="supported Node UCI/NNUE launcher; Hito 5 never permits WASM omission",
    )
    parser.add_argument(
        "--incremental-binary",
        type=Path,
        help="defaults to atomic-nnue-incremental-tests beside --native",
    )
    parser.add_argument(
        "--oracle",
        type=Path,
        default=WORKSPACE_ROOT / "baseline-artifacts" / "FSF_Atomic_baseline.exe",
        help="frozen Fairy-Stockfish executable used only as a structural oracle",
    )
    parser.add_argument(
        "--mode",
        choices=("release", "smoke"),
        default="release",
        help="release is normative; smoke is a reduced, explicitly non-releasable run",
    )
    parser.add_argument(
        "--smoke-operations",
        type=int,
        default=4_096,
        help="incremental operations in smoke mode (must be a positive multiple of 8)",
    )
    parser.add_argument(
        "--smoke-positions",
        type=int,
        default=64,
        help="structural differential positions in smoke mode",
    )
    parser.add_argument("--seed", type=int, default=20260710)
    parser.add_argument("--cpp-unit", type=Path)
    parser.add_argument("--cpp-api", type=Path)
    parser.add_argument("--syzygy-driver", type=Path)
    parser.add_argument("--fairy-repo", type=Path)
    parser.add_argument("--python", default=sys.executable)
    parser.add_argument("--node", default="node")
    parser.add_argument("--bash")
    parser.add_argument(
        "--hito4-timeout",
        type=float,
        default=600.0,
        help="per-step timeout forwarded to the Hito 4 runner",
    )
    parser.add_argument(
        "--incremental-timeout",
        type=float,
        help="defaults to 7200 seconds in release mode and 300 in smoke mode",
    )
    parser.add_argument(
        "--differential-timeout",
        type=float,
        help="whole-process timeout; defaults to 7200 seconds in release and 900 in smoke",
    )
    parser.add_argument(
        "--engine-timeout",
        type=float,
        default=30.0,
        help="per-response timeout forwarded to the structural differential",
    )
    return parser.parse_args()


def validate_positive(value: float | int, option: str) -> None:
    if value <= 0:
        raise GateFailure(f"{option} must be positive")


def main() -> int:
    args = parse_args()
    try:
        for value, option in (
            (args.smoke_operations, "--smoke-operations"),
            (args.smoke_positions, "--smoke-positions"),
            (args.hito4_timeout, "--hito4-timeout"),
            (args.engine_timeout, "--engine-timeout"),
        ):
            validate_positive(value, option)
        if args.smoke_operations % 8:
            raise GateFailure("--smoke-operations must be a multiple of 8")
        if args.incremental_timeout is not None:
            validate_positive(args.incremental_timeout, "--incremental-timeout")
        if args.differential_timeout is not None:
            validate_positive(args.differential_timeout, "--differential-timeout")

        python = command_path(args.python, "Python")
        native = require_file(args.native, "native engine")
        net = require_file(args.net, "Legacy Atomic V1 network")
        net_sha = sha256(net)
        if net_sha != EXPECTED_NET_SHA256:
            raise GateFailure(
                f"network SHA-256 is {net_sha}; expected {EXPECTED_NET_SHA256}"
            )
        pyffish = args.pyffish.expanduser().resolve()
        if not (pyffish.is_file() or pyffish.is_dir()):
            raise GateFailure(f"pyffish artifact does not exist: {pyffish}")
        cjs = require_file(args.cjs, "CommonJS artifact")
        esm = require_file(args.esm, "ES module artifact")
        tables = args.tables.expanduser().resolve()
        if not tables.is_dir():
            raise GateFailure(f"Atomic Syzygy table directory does not exist: {tables}")
        wasm_wrapper = require_file(args.wasm_wrapper, "WASM UCI/NNUE wrapper")
        oracle = require_file(args.oracle, "frozen Fairy oracle")
        oracle_sha = sha256(oracle)
        if oracle_sha != EXPECTED_ORACLE_SHA256:
            raise GateFailure(
                f"Fairy oracle SHA-256 is {oracle_sha}; expected {EXPECTED_ORACLE_SHA256}"
            )

        suffix = ".exe" if native.suffix.lower() == ".exe" else ".bin"
        incremental_binary = require_file(
            args.incremental_binary
            or native.with_name(f"atomic-nnue-incremental-tests{suffix}"),
            "incremental NNUE test binary",
        )

        hito4_command = [
            python,
            str(REPO_ROOT / "tests" / "run_hito4.py"),
            "--native",
            str(native),
            "--net",
            str(net),
            "--pyffish",
            str(pyffish),
            "--cjs",
            str(cjs),
            "--esm",
            str(esm),
            "--tables",
            str(tables),
            "--wasm-wrapper",
            str(wasm_wrapper),
            "--python",
            python,
            "--node",
            args.node,
            "--timeout",
            str(args.hito4_timeout),
        ]
        for option, value in (
            ("--cpp-unit", args.cpp_unit),
            ("--cpp-api", args.cpp_api),
            ("--syzygy-driver", args.syzygy_driver),
            ("--fairy-repo", args.fairy_repo),
        ):
            if value is not None:
                hito4_command.extend((option, str(value.expanduser().resolve())))
        if args.bash is not None:
            hito4_command.extend(("--bash", args.bash))

        run_step(
            "complete Hito 4 release gate (includes modes/export and UCI/NNUE WASM)",
            hito4_command,
            timeout=max(args.hito4_timeout * 20, 3_600.0),
            required_markers=(
                "=== Legacy Atomic V1 NNUE modes and byte-exact export ===",
                "NNUE mode contract passed: false, true, pure, and nonfatal invalid-net rejection",
                "=== Node UCI/NNUE WASM integration ===",
                f"WASM engine integration: PASS (net sha256={EXPECTED_NET_SHA256})",
                "Hito 4 validation passed",
            ),
        )

        operations = (
            RELEASE_INCREMENTAL_OPERATIONS
            if args.mode == "release"
            else args.smoke_operations
        )
        incremental_timeout = args.incremental_timeout or (
            7_200.0 if args.mode == "release" else 300.0
        )
        incremental_output = run_step(
            "LegacyAtomicV1 incremental/full-refresh equivalence",
            [
                python,
                str(REPO_ROOT / "tests" / "atomic_nnue_incremental.py"),
                "--binary",
                str(incremental_binary),
                "--net",
                str(net),
                "--mode",
                args.mode,
                "--operations",
                str(operations),
            ],
            timeout=incremental_timeout,
            required_markers=(
                "PASS rule50 evaluation damping at and beyond draw boundary",
                f"LegacyAtomicV1 incremental gate passed: mode={args.mode} "
                f"requested-random-operations={operations}",
                "capture-forced-refresh=0",
            ),
        )
        forced_refresh_counts = [
            int(value) for value in CAPTURE_REFRESH_RE.findall(incremental_output)
        ]
        if not forced_refresh_counts or any(forced_refresh_counts):
            raise GateFailure(
                "incremental gate did not prove capture-forced-refresh=0"
            )
        capture_counts = [int(value) for value in CAPTURE_COUNT_RE.findall(incremental_output)]
        if not capture_counts or max(capture_counts) <= 0:
            raise GateFailure("incremental gate exercised no Atomic captures")
        if LEGACY_CAPTURE_REFRESH_RE.search(incremental_output):
            raise GateFailure("incremental gate reported requiresRefresh for a capture")

        run_step(
            "rule-50-aware NNUE differential units",
            [
                python,
                "-m",
                "pytest",
                "-q",
                str(REPO_ROOT / "tests" / "python" / "test_atomic_nnue_differential.py"),
            ],
            timeout=300.0,
            required_markers=("3 passed",),
        )

        positions = (
            RELEASE_DIFFERENTIAL_POSITIONS
            if args.mode == "release"
            else args.smoke_positions
        )
        differential_timeout = args.differential_timeout or (
            7_200.0 if args.mode == "release" else 900.0
        )
        differential_output = run_step(
            "Legacy Atomic V1 diagnostic differential (structural checks are hard)",
            [
                python,
                str(REPO_ROOT / "tests" / "atomic_nnue_differential.py"),
                str(native),
                str(oracle),
                "--net",
                str(net),
                "--positions",
                str(positions),
                "--seed",
                str(args.seed),
                "--timeout",
                str(args.engine_timeout),
                "--true-tolerance",
                f"{DIAGNOSTIC_SANITY_TOLERANCE:g}",
                "--pure-tolerance",
                f"{DIAGNOSTIC_SANITY_TOLERANCE:g}",
                "--progress",
                "250" if args.mode == "release" else "0",
            ],
            timeout=differential_timeout,
            required_markers=(
                f"Legacy Atomic V1 differential passed: {positions}/{positions} positions;",
                "Pure limitation: frozen Fairy exposes only a two-decimal unadjusted NNUE trace;",
            ),
        )
        corpus_hashes = [value.lower() for value in CORPUS_SHA_RE.findall(differential_output)]
        if len(corpus_hashes) != 1:
            raise GateFailure(
                "diagnostic differential did not emit exactly one corpus SHA-256"
            )
        if args.mode == "release" and corpus_hashes[0] != RELEASE_CORPUS_SHA256:
            raise GateFailure(
                f"release corpus SHA-256 is {corpus_hashes[0]}; "
                f"expected {RELEASE_CORPUS_SHA256}"
            )
        rule50_counts = [int(value) for value in RULE50_DAMPED_RE.findall(differential_output)]
        if len(rule50_counts) != 1:
            raise GateFailure(
                "diagnostic differential did not emit exactly one rule50-damped count"
            )
        if args.mode == "release" and rule50_counts[0] <= 0:
            raise GateFailure(
                "release differential exercised no positions at the Atomic rule-50 boundary"
            )

        if args.mode == "release":
            print(
                "\nHito 5 release validation passed: "
                f"incremental-operations={operations} structural-positions={positions} "
                "capture-forced-refresh=0",
                flush=True,
            )
        else:
            print(
                "\nHito 5 smoke validation passed (NON-RELEASE): "
                f"incremental-operations={operations} structural-positions={positions}",
                flush=True,
            )
        return 0
    except GateFailure as error:
        print(f"\nHito 5 validation FAILED: {error}", file=sys.stderr, flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
