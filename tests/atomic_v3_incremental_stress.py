#!/usr/bin/env python3
"""Run the private AtomicNNUEV3 incremental stress gate fail closed.

The wrapper authenticates the frozen H9.3g mixed-wire fixture before starting
the isolated C++ runner.  A zero exit status is accepted only when the final
non-empty output line is the exact stress sentinel for the requested profile
and required ISA, every published delta counter is internally consistent, and
the child reports zero fallback calls.

AtomicNNUEV3 remains private at this milestone.  This script does not load the
network through the production dispatcher and does not run search or training.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
import math
import os
from pathlib import Path
import re
import subprocess
import sys
from typing import Dict, Optional, Tuple


FROZEN_NET_BYTES = 77_349_879
FROZEN_NET_SHA256 = (
    "00e46223822d06d7927e884eec10739ba19ef8dd82a6e262f627d361658080c2"
)
DIRECTED_COUNTS = {
    "moves": 32,
    "captures": 23,
    "terminal_failures": 8,
    "promotions": 19,
    "en_passants": 6,
    "standard_castles": 4,
    "atomic960_castles": 11,
    "max_blast": 9,
}
REQUIRED_ISAS = ("scalar", "sse41", "avx2")
ACCUMULATOR_DIMENSIONS = 1024


@dataclass(frozen=True)
class Profile:
    operations: int
    full_refresh_interval: int
    threads: int
    timeout: float
    # Named default profiles freeze their reviewed 16-hex-digit signature.
    # ``None`` remains available only while a new profile is being calibrated;
    # structural/accounting invariants still fail closed during that window.
    expected_signature: Optional[str] = None


DEFAULT_PROFILES: Dict[str, Profile] = {
    "smoke": Profile(4_096, 1, 1, 300.0, "45D43FB02CAA9A3D"),
    "release": Profile(65_536, 1, 8, 1_200.0, "E86C39BDF8187078"),
    "soak": Profile(1_048_576, 1, 8, 7_200.0, "AF6B51180815972B"),
}


FINAL_SENTINEL_RE = re.compile(
    r"^AtomicNNUEV3 incremental stress gate passed: "
    r"mode=(?P<mode>smoke|release|soak) "
    r"isa-requested=(?P<isa_requested>scalar|sse41|avx2) "
    r"isa-executed=(?P<isa_executed>scalar|sse41|avx2) "
    r"requested-operations=(?P<requested>\d+) "
    r"actual-operations=(?P<actual>\d+) "
    r"makes=(?P<makes>\d+) "
    r"undos=(?P<undos>\d+) "
    r"evaluations=(?P<evaluations>\d+) "
    r"full-refresh-comparisons=(?P<full_refresh>\d+) "
    r"random-captures=(?P<random_captures>\d+) "
    r"random-terminal-failures=(?P<random_terminal_failures>\d+) "
    r"random-standard-castles=(?P<random_standard_castles>\d+) "
    r"random-atomic960-castles=(?P<random_atomic960_castles>\d+) "
    r"directed-moves=(?P<directed_moves>\d+) "
    r"directed-captures=(?P<directed_captures>\d+) "
    r"directed-terminal-failures=(?P<directed_terminal_failures>\d+) "
    r"directed-promotions=(?P<directed_promotions>\d+) "
    r"directed-en-passants=(?P<directed_en_passants>\d+) "
    r"directed-standard-castles=(?P<directed_standard_castles>\d+) "
    r"directed-atomic960-castles=(?P<directed_atomic960_castles>\d+) "
    r"directed-max-blast=(?P<directed_max_blast>\d+) "
    r"threads=(?P<threads>\d+) "
    r"hm-delta-perspectives=(?P<hm_delta_perspectives>\d+) "
    r"hm-delta-removed-rows=(?P<hm_delta_removed_rows>\d+) "
    r"hm-delta-added-rows=(?P<hm_delta_added_rows>\d+) "
    r"hm-delta-i16-lanes=(?P<hm_delta_i16_lanes>\d+) "
    r"hm-delta-source-permutation-lanes=(?P<hm_delta_source_permutation_lanes>\d+) "
    r"hm-delta-publish-permutation-lanes=(?P<hm_delta_publish_permutation_lanes>\d+) "
    r"hm-delta-kernel-calls=(?P<hm_delta_kernel_calls>\d+) "
    r"hm-delta-scalar-kernel-calls=(?P<hm_delta_scalar_kernel_calls>\d+) "
    r"hm-delta-sse41-kernel-calls=(?P<hm_delta_sse41_kernel_calls>\d+) "
    r"hm-delta-avx2-kernel-calls=(?P<hm_delta_avx2_kernel_calls>\d+) "
    r"hm-delta-fallback-calls=(?P<hm_delta_fallback_calls>\d+) "
    r"state-signature=0x(?P<signature>[0-9A-F]{16})$"
)


class FixtureAuthenticationError(ValueError):
    """The supplied file is not the frozen H9.3g AtomicNNUEV3 fixture."""


class GateOutputError(ValueError):
    """The child exited successfully without proving the requested gate."""


@dataclass(frozen=True)
class RunConfiguration:
    mode: str
    operations: int
    full_refresh_interval: int
    threads: int
    timeout: float


def _stat_identity(value: os.stat_result) -> Tuple[int, int, int, int]:
    modified_ns = getattr(value, "st_mtime_ns", int(value.st_mtime * 1_000_000_000))
    return value.st_dev, value.st_ino, value.st_size, modified_ns


def authenticate_fixture(path: Path) -> str:
    """Authenticate size and SHA-256 from one stable, already-open handle."""

    digest = hashlib.sha256()
    try:
        with path.open("rb") as stream:
            before = os.fstat(stream.fileno())
            if before.st_size != FROZEN_NET_BYTES:
                raise FixtureAuthenticationError(
                    "AtomicNNUEV3 fixture size mismatch: "
                    f"expected={FROZEN_NET_BYTES} actual={before.st_size} file={path}"
                )
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
            after = os.fstat(stream.fileno())
    except OSError as error:
        raise FixtureAuthenticationError(
            f"could not read AtomicNNUEV3 fixture {path}: {error}"
        ) from error

    if _stat_identity(before) != _stat_identity(after):
        raise FixtureAuthenticationError(
            f"AtomicNNUEV3 fixture changed while it was authenticated: {path}"
        )

    actual_sha = digest.hexdigest()
    if actual_sha != FROZEN_NET_SHA256:
        raise FixtureAuthenticationError(
            "AtomicNNUEV3 fixture SHA-256 mismatch: "
            f"expected={FROZEN_NET_SHA256.upper()} "
            f"actual={actual_sha.upper()} file={path}"
        )
    return actual_sha


def resolve_configuration(
    *,
    mode: str,
    operations: Optional[int],
    full_refresh_interval: Optional[int],
    threads: Optional[int],
    timeout: Optional[float],
) -> RunConfiguration:
    """Apply one named profile and reject ambiguous or impossible overrides."""

    if mode not in DEFAULT_PROFILES:
        raise ValueError(f"unknown stress mode: {mode}")
    profile = DEFAULT_PROFILES[mode]
    actual_operations = profile.operations if operations is None else operations
    actual_interval = (
        profile.full_refresh_interval
        if full_refresh_interval is None
        else full_refresh_interval
    )
    actual_threads = profile.threads if threads is None else threads
    actual_timeout = profile.timeout if timeout is None else timeout

    if actual_operations <= 0:
        raise ValueError("--operations must be positive")
    if actual_threads not in (1, 2, 4, 8):
        raise ValueError("--threads must be one of 1, 2, 4 or 8")
    if actual_operations % 16 != 0:
        raise ValueError("--operations must be a multiple of 16")
    if actual_interval <= 0:
        raise ValueError("--full-refresh-interval must be positive")
    if actual_interval > actual_operations:
        raise ValueError("--full-refresh-interval cannot exceed --operations")
    if not math.isfinite(actual_timeout) or actual_timeout <= 0:
        raise ValueError("--timeout must be finite and positive")

    return RunConfiguration(
        mode,
        actual_operations,
        actual_interval,
        actual_threads,
        actual_timeout,
    )


def _default_signature(configuration: RunConfiguration) -> Optional[str]:
    profile = DEFAULT_PROFILES[configuration.mode]
    if (
        configuration.operations == profile.operations
        and configuration.full_refresh_interval == profile.full_refresh_interval
    ):
        return profile.expected_signature
    return None


def validate_gate_output(
    output: str,
    *,
    mode: str,
    operations: int,
    full_refresh_interval: int,
    threads: int,
    required_isa: str,
) -> str:
    """Validate the final C++ sentinel and return its canonical signature."""

    if required_isa not in REQUIRED_ISAS:
        raise ValueError(
            f"--require-isa must be one of {', '.join(REQUIRED_ISAS)}"
        )
    configuration = resolve_configuration(
        mode=mode,
        operations=operations,
        full_refresh_interval=full_refresh_interval,
        threads=threads,
        timeout=1.0,
    )
    lines = [line.strip() for line in output.splitlines() if line.strip()]
    if not lines:
        raise GateOutputError("child emitted no output")

    match = FINAL_SENTINEL_RE.fullmatch(lines[-1])
    if match is None:
        raise GateOutputError(
            "final non-empty line is not the AtomicNNUEV3 stress success sentinel: "
            f"{lines[-1]!r}"
        )

    child_mode = match.group("mode")
    isa_requested = match.group("isa_requested")
    isa_executed = match.group("isa_executed")
    requested = int(match.group("requested"))
    actual = int(match.group("actual"))
    makes = int(match.group("makes"))
    undos = int(match.group("undos"))
    evaluations = int(match.group("evaluations"))
    full_refresh = int(match.group("full_refresh"))
    random_captures = int(match.group("random_captures"))
    random_terminal_failures = int(match.group("random_terminal_failures"))
    random_standard_castles = int(match.group("random_standard_castles"))
    random_atomic960_castles = int(match.group("random_atomic960_castles"))
    directed = {
        "moves": int(match.group("directed_moves")),
        "captures": int(match.group("directed_captures")),
        "terminal_failures": int(match.group("directed_terminal_failures")),
        "promotions": int(match.group("directed_promotions")),
        "en_passants": int(match.group("directed_en_passants")),
        "standard_castles": int(match.group("directed_standard_castles")),
        "atomic960_castles": int(match.group("directed_atomic960_castles")),
        "max_blast": int(match.group("directed_max_blast")),
    }
    child_threads = int(match.group("threads"))
    hm_delta_perspectives = int(match.group("hm_delta_perspectives"))
    hm_delta_removed_rows = int(match.group("hm_delta_removed_rows"))
    hm_delta_added_rows = int(match.group("hm_delta_added_rows"))
    hm_delta_i16_lanes = int(match.group("hm_delta_i16_lanes"))
    hm_delta_source_permutation_lanes = int(
        match.group("hm_delta_source_permutation_lanes")
    )
    hm_delta_publish_permutation_lanes = int(
        match.group("hm_delta_publish_permutation_lanes")
    )
    hm_delta_kernel_calls = int(match.group("hm_delta_kernel_calls"))
    hm_delta_kernel_calls_by_isa = {
        "scalar": int(match.group("hm_delta_scalar_kernel_calls")),
        "sse41": int(match.group("hm_delta_sse41_kernel_calls")),
        "avx2": int(match.group("hm_delta_avx2_kernel_calls")),
    }
    hm_delta_fallback_calls = int(match.group("hm_delta_fallback_calls"))
    signature = match.group("signature")

    if child_mode != configuration.mode:
        raise GateOutputError(
            f"child reported mode={child_mode}, expected mode={configuration.mode}"
        )
    if isa_requested != required_isa or isa_executed != required_isa:
        raise GateOutputError(
            "child did not execute the exact required ISA: "
            f"requested={isa_requested} executed={isa_executed} "
            f"required={required_isa}"
        )
    if requested != configuration.operations or actual != configuration.operations:
        raise GateOutputError(
            "child operation count mismatch: "
            f"requested={requested} actual={actual} expected={configuration.operations}"
        )
    if child_threads != configuration.threads:
        raise GateOutputError(
            f"child reported threads={child_threads}, expected threads={configuration.threads}"
        )
    if makes + undos != actual:
        raise GateOutputError(
            "child operation accounting mismatch: "
            f"makes={makes} undos={undos} actual={actual}"
        )
    if makes != undos:
        raise GateOutputError(
            f"child did not unwind to every root: makes={makes} undos={undos}"
        )

    # The eight immutable roots/seeds are fixed independently of the scheduling
    # thread count, and every random operation is evaluated exactly once.
    expected_evaluations = actual + 8
    if evaluations != expected_evaluations:
        raise GateOutputError(
            "child evaluation count mismatch: "
            f"evaluations={evaluations} expected={expected_evaluations}"
        )
    per_seed_operations = actual // 8
    sampled_per_seed = (
        per_seed_operations + configuration.full_refresh_interval - 1
    ) // configuration.full_refresh_interval
    expected_full_refresh = 8 + 8 * sampled_per_seed
    if full_refresh != expected_full_refresh:
        raise GateOutputError(
            "child full-refresh accounting mismatch: "
            f"comparisons={full_refresh} expected={expected_full_refresh} "
            f"evaluations={evaluations}"
        )
    if random_captures > makes:
        raise GateOutputError(
            "child reported random captures greater than random makes: "
            f"captures={random_captures} makes={makes}"
        )
    if random_terminal_failures > random_captures:
        raise GateOutputError(
            "child random terminal failure accounting mismatch: "
            f"terminal-failures={random_terminal_failures} "
            f"captures={random_captures}"
        )
    if random_standard_castles + random_atomic960_castles > makes:
        raise GateOutputError(
            "child random castling accounting mismatch: "
            f"standard={random_standard_castles} "
            f"atomic960={random_atomic960_castles} makes={makes}"
        )
    if directed != DIRECTED_COUNTS:
        raise GateOutputError(
            f"child directed fixture accounting mismatch: actual={directed} "
            f"expected={DIRECTED_COUNTS}"
        )

    expected_kernel_calls = hm_delta_removed_rows + hm_delta_added_rows
    if (
        hm_delta_perspectives <= 0
        or hm_delta_removed_rows <= 0
        or hm_delta_added_rows <= 0
    ):
        raise GateOutputError(
            "child incremental HM delta coverage was incomplete: "
            f"perspectives={hm_delta_perspectives} "
            f"removed={hm_delta_removed_rows} added={hm_delta_added_rows}"
        )
    if (
        hm_delta_kernel_calls != expected_kernel_calls
        or hm_delta_i16_lanes
        != expected_kernel_calls * ACCUMULATOR_DIMENSIONS
    ):
        raise GateOutputError(
            "child incremental HM delta kernel/lane accounting mismatch: "
            f"kernels={hm_delta_kernel_calls} expected={expected_kernel_calls} "
            f"lanes={hm_delta_i16_lanes}"
        )
    expected_permutation_lanes = (
        hm_delta_perspectives * ACCUMULATOR_DIMENSIONS
    )
    if (
        hm_delta_source_permutation_lanes != expected_permutation_lanes
        or hm_delta_publish_permutation_lanes != expected_permutation_lanes
    ):
        raise GateOutputError(
            "child incremental HM delta permutation accounting mismatch: "
            f"source={hm_delta_source_permutation_lanes} "
            f"publish={hm_delta_publish_permutation_lanes} "
            f"expected={expected_permutation_lanes}"
        )
    expected_kernel_calls_by_isa = {isa: 0 for isa in REQUIRED_ISAS}
    expected_kernel_calls_by_isa[required_isa] = expected_kernel_calls
    if hm_delta_kernel_calls_by_isa != expected_kernel_calls_by_isa:
        raise GateOutputError(
            "child incremental HM delta kernel inventory was not exact-ISA only: "
            f"actual={hm_delta_kernel_calls_by_isa} "
            f"expected={expected_kernel_calls_by_isa}"
        )
    if hm_delta_fallback_calls != 0:
        raise GateOutputError(
            "child incremental HM delta execution used fallback calls: "
            f"fallbacks={hm_delta_fallback_calls}"
        )

    expected_signature = _default_signature(configuration)
    if expected_signature is not None and signature != expected_signature:
        raise GateOutputError(
            f"default {configuration.mode} signature mismatch: "
            f"expected=0x{expected_signature} actual=0x{signature}"
        )
    return signature


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Verify private AtomicNNUEV3 HM incrementals under deterministic stress"
    )
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--net", type=Path, required=True)
    parser.add_argument("--require-isa", choices=REQUIRED_ISAS, required=True)
    parser.add_argument("--mode", choices=tuple(DEFAULT_PROFILES), default="smoke")
    parser.add_argument("--operations", type=int)
    parser.add_argument("--full-refresh-interval", type=int)
    parser.add_argument("--threads", type=int, choices=(1, 2, 4, 8))
    parser.add_argument("--timeout", type=float)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    binary = args.binary.expanduser().resolve()
    net = args.net.expanduser().resolve()

    if not binary.is_file():
        raise SystemExit(f"AtomicNNUEV3 stress binary not found: {binary}")
    if not net.is_file():
        raise SystemExit(f"AtomicNNUEV3 fixture not found: {net}")

    try:
        configuration = resolve_configuration(
            mode=args.mode,
            operations=args.operations,
            full_refresh_interval=args.full_refresh_interval,
            threads=args.threads,
            timeout=args.timeout,
        )
        actual_sha = authenticate_fixture(net)
    except (ValueError, FixtureAuthenticationError) as error:
        raise SystemExit(str(error)) from error

    command = [
        str(binary),
        "--net",
        str(net),
        "--require-isa",
        args.require_isa,
        "--mode",
        configuration.mode,
        "--operations",
        str(configuration.operations),
        "--full-refresh-interval",
        str(configuration.full_refresh_interval),
        "--threads",
        str(configuration.threads),
    ]
    startup: Dict[str, int] = {}
    if os.name == "nt":
        startup["creationflags"] = subprocess.CREATE_NO_WINDOW

    signature_state = (
        "frozen"
        if _default_signature(configuration) is not None
        else "measurement-pending"
    )
    print(
        "AtomicNNUEV3 frozen fixture verified: "
        f"bytes={FROZEN_NET_BYTES} SHA-256={actual_sha.upper()} "
        f"mode={configuration.mode} operations={configuration.operations} "
        f"interval={configuration.full_refresh_interval} "
        f"threads={configuration.threads} isa={args.require_isa} "
        f"signature={signature_state}",
        flush=True,
    )
    try:
        completed = subprocess.run(
            command,
            timeout=configuration.timeout,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            **startup,
        )
    except subprocess.TimeoutExpired as error:
        raise SystemExit(
            f"AtomicNNUEV3 stress gate timed out after {configuration.timeout:g}s"
        ) from error
    except OSError as error:
        raise SystemExit(f"could not start AtomicNNUEV3 stress gate: {error}") from error

    output = completed.stdout or ""
    if output:
        print(output, end="" if output.endswith("\n") else "\n")
    if completed.returncode:
        return completed.returncode

    try:
        validate_gate_output(
            output,
            mode=configuration.mode,
            operations=configuration.operations,
            full_refresh_interval=configuration.full_refresh_interval,
            threads=configuration.threads,
            required_isa=args.require_isa,
        )
    except GateOutputError as error:
        print(f"AtomicNNUEV3 stress output validation failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
