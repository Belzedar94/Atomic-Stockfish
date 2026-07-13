#!/usr/bin/env python3
"""Compare two commit-bound Atomic-Stockfish builds on the fixed corpus.

Unlike ``atomic_bench_compare.py``, this runner compares an Atomic candidate
with an Atomic control. It deliberately does not accept Fairy-Stockfish as the
control and does not replace the normative frozen-Fairy performance gate.
"""

from __future__ import annotations

import argparse
import statistics
from contextlib import ExitStack
from pathlib import Path
from typing import Mapping, Optional

import psutil

from atomic_bench_compare import (
    CORPUS,
    EXPECTED_NET_SHA256,
    MEASURED_REPETITIONS,
    NORMATIVE_HASH_MB,
    NORMATIVE_NODES_PER_FEN,
    Measurement,
    UciEngine,
    corpus_sha256,
    print_measurement,
)
from atomic_compiler_preflight import (
    CompilerPreflightError,
    FileFingerprint,
    fingerprint_files,
    require_matching_compilation_settings,
    require_sha256,
    verify_file_fingerprints,
)
from atomic_los_gate import (
    EXPECTED_PSUTIL_VERSION,
    GateConfigurationError,
    normative_psutil_fingerprints,
)


def require_distinct_engine_artifacts(
    artifact_map: Mapping[str, FileFingerprint],
) -> None:
    candidate_sha = artifact_map["candidate"].sha256
    control_sha = artifact_map["baseline"].sha256
    if candidate_sha == control_sha:
        raise GateConfigurationError(
            "candidate and control have identical SHA-256; a commit A/B gate "
            "requires different engine artifacts"
        )


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Strict Atomic-Stockfish commit A/B speed gate"
    )
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--control", type=Path, required=True)
    parser.add_argument("--eval-file", type=Path, required=True)
    parser.add_argument("--nodes", type=int, default=NORMATIVE_NODES_PER_FEN)
    parser.add_argument("--hash", type=int, default=NORMATIVE_HASH_MB, dest="hash_mb")
    parser.add_argument(
        "--affinity",
        type=int,
        required=True,
        help="explicit logical CPU used for every serialized search",
    )
    parser.add_argument("--timeout", type=float, default=60.0, help="per-search seconds")
    args = parser.parse_args(argv)

    candidate = args.candidate.resolve()
    control = args.control.resolve()
    net = args.eval_file.resolve()
    for label, path in (
        ("candidate", candidate),
        ("control", control),
        ("eval file", net),
    ):
        if not path.is_file():
            parser.error(f"{label} does not exist: {path}")
    if candidate == control:
        parser.error("candidate and control must be different executables")
    if args.nodes <= 0 or args.hash_mb <= 0 or args.timeout <= 0:
        parser.error("--nodes, --hash, and --timeout must be positive")
    if args.nodes != NORMATIVE_NODES_PER_FEN:
        parser.error(
            f"--nodes must be exactly {NORMATIVE_NODES_PER_FEN} for the "
            "commit A/B gate"
        )
    if args.hash_mb != NORMATIVE_HASH_MB:
        parser.error(
            f"--hash must be exactly {NORMATIVE_HASH_MB} for the commit A/B gate"
        )

    try:
        # The compiler preflight uses the historical key ``baseline`` for the
        # second build. The user-facing role remains ``control`` throughout the
        # benchmark and NNUE handshake.
        artifacts = normative_psutil_fingerprints() + fingerprint_files(
            (
                ("candidate", candidate),
                ("baseline", control),
                ("eval_file", net),
            )
        )
        artifact_map = {artifact.label: artifact for artifact in artifacts}
        require_distinct_engine_artifacts(artifact_map)
        require_sha256(
            artifact_map["eval_file"],
            EXPECTED_NET_SHA256,
            description="Legacy Atomic V1 network",
        )
        require_matching_compilation_settings(
            candidate,
            control,
            expected_fingerprints=artifact_map,
        )
    except (CompilerPreflightError, GateConfigurationError) as exc:
        parser.error(str(exc))

    allowed = psutil.Process().cpu_affinity()
    if args.affinity not in allowed:
        parser.error(
            f"CPU {args.affinity} is outside this process affinity: {allowed}"
        )

    atomic_count = sum(not position.chess960 for position in CORPUS)
    atomic960_count = len(CORPUS) - atomic_count
    print(
        f"Corpus: positions={len(CORPUS)} atomic={atomic_count} "
        f"atomic960={atomic960_count} sha256={corpus_sha256()}"
    )
    print(
        f"Configuration: net_sha256={artifact_map['eval_file'].sha256} "
        f"threads=1 hash_mb={args.hash_mb} cpu={args.affinity} "
        f"psutil={EXPECTED_PSUTIL_VERSION} nodes_per_fen={args.nodes} "
        f"warmups=1 repetitions={MEASURED_REPETITIONS}"
    )

    samples: dict[str, list[Measurement]] = {"candidate": [], "control": []}
    try:
        try:
            with ExitStack() as stack:
                engines = {
                    "candidate": stack.enter_context(
                        UciEngine(
                            "candidate",
                            candidate,
                            net,
                            args.hash_mb,
                            args.affinity,
                            args.timeout,
                        )
                    ),
                    "control": stack.enter_context(
                        UciEngine(
                            "control",
                            control,
                            net,
                            args.hash_mb,
                            args.affinity,
                            args.timeout,
                        )
                    ),
                }

                for label in ("candidate", "control"):
                    print_measurement(
                        f"Warm-up {label}", engines[label].run_corpus(args.nodes)
                    )
                for index in range(MEASURED_REPETITIONS):
                    order = (
                        ("candidate", "control")
                        if index % 2 == 0
                        else ("control", "candidate")
                    )
                    for label in order:
                        measurement = engines[label].run_corpus(args.nodes)
                        samples[label].append(measurement)
                        print_measurement(
                            f"Run {index + 1}/{MEASURED_REPETITIONS} {label}",
                            measurement,
                        )
        finally:
            verify_file_fingerprints(
                artifacts,
                emit=print,
                pass_label="A/B artifact postflight",
            )
    except CompilerPreflightError as exc:
        parser.error(str(exc))
    except Exception as exc:
        parser.error(f"benchmark infrastructure failure: {exc}")

    candidate_median = statistics.median(
        measurement.nps for measurement in samples["candidate"]
    )
    control_median = statistics.median(
        measurement.nps for measurement in samples["control"]
    )
    ratio = candidate_median / control_median
    candidate_size = artifact_map["candidate"].size
    control_size = artifact_map["baseline"].size
    print(f"Candidate: median_nps={candidate_median:.0f} binary_bytes={candidate_size}")
    print(f"Control: median_nps={control_median:.0f} binary_bytes={control_size}")
    print(
        f"Comparison: nps_ratio={ratio:.4f} "
        f"speed_delta_pct={(ratio - 1.0) * 100:.2f} "
        f"size_ratio={candidate_size / control_size:.4f} "
        f"size_delta_bytes={candidate_size - control_size:+d}"
    )

    if candidate_median <= control_median:
        print("COMMIT A/B GATE: FAIL (candidate median NPS is not strictly higher)")
        return 1

    print("COMMIT A/B GATE: PASS (candidate median NPS is strictly higher)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
