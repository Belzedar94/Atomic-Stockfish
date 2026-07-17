#!/usr/bin/env python3
"""Repeatable single-thread speed bench over the built-in Atomic corpus."""

from __future__ import annotations

import argparse
import hashlib
import re
import statistics
import struct
import subprocess
from dataclasses import dataclass
from pathlib import Path

import psutil


TIME_RE = re.compile(r"Total time \(ms\)\s*:\s*(\d+)")
NODES_RE = re.compile(r"Nodes searched\s*:\s*(\d+)")
NPS_RE = re.compile(r"Nodes/second\s*:\s*(\d+)")
EXPECTED_NET_SHA256 = "99dc67eabf26a64faeeca3a88b4c38597a840b8d4a874b9f2cf658c6f92a04a6"
NETWORK_BACKENDS = {
    0x7AF32F20: "Legacy Atomic V1",
    0xA70C0002: "AtomicNNUEV2",
    0xA70C0003: "AtomicNNUEV3",
}


@dataclass(frozen=True)
class Measurement:
    elapsed_ms: int
    nodes: int
    nps: int


def run_once(
    engine: Path,
    net: Path,
    depth: int,
    affinity: int,
    timeout: float,
    backend: str,
) -> Measurement:
    process = subprocess.Popen(
        [str(engine)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    psutil.Process(process.pid).cpu_affinity([affinity])
    commands = "\n".join(
        (
            f"setoption name EvalFile value {net}",
            "setoption name Use NNUE value true",
            f"bench 16 1 {depth} default depth",
            "quit",
            "",
        )
    )
    stdout, stderr = process.communicate(commands, timeout=timeout)
    if process.returncode != 0:
        raise RuntimeError(f"bench exited {process.returncode}\n{stdout}\n{stderr}")
    marker = f"NNUE evaluation using {backend}"
    if marker not in stdout and marker not in stderr:
        raise AssertionError(f"bench did not authenticate {backend}\n{stdout}\n{stderr}")

    elapsed = TIME_RE.search(stderr)
    nodes = NODES_RE.search(stderr)
    nps = NPS_RE.search(stderr)
    if not elapsed or not nodes or not nps:
        raise AssertionError(f"bench produced no complete measurement\n{stdout}\n{stderr}")
    return Measurement(int(elapsed.group(1)), int(nodes.group(1)), int(nps.group(1)))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--engine", type=Path, required=True)
    parser.add_argument("--eval-file", type=Path, required=True)
    parser.add_argument("--depth", type=int, default=13)
    parser.add_argument("--repetitions", type=int, default=5)
    parser.add_argument("--affinity", type=int)
    parser.add_argument("--timeout", type=float, default=300.0)
    parser.add_argument(
        "--expected-net-sha256",
        default=EXPECTED_NET_SHA256,
        help="exact SHA-256 of the requested external network",
    )
    args = parser.parse_args()

    engine = args.engine.resolve()
    net = args.eval_file.resolve()
    if not engine.is_file() or not net.is_file():
        parser.error("engine and eval file must exist")
    if args.depth <= 0 or args.repetitions <= 0:
        parser.error("depth and repetitions must be positive")
    expected_sha256 = args.expected_net_sha256.lower()
    if len(expected_sha256) != 64 or any(
        character not in "0123456789abcdef" for character in expected_sha256
    ):
        parser.error("--expected-net-sha256 must be 64 hexadecimal characters")
    digest = hashlib.sha256()
    with net.open("rb") as stream:
        version_bytes = stream.read(4)
        digest.update(version_bytes)
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    if digest.hexdigest() != expected_sha256:
        parser.error("unexpected network SHA-256")
    if len(version_bytes) != 4:
        parser.error("network has no complete version field")
    version = struct.unpack("<I", version_bytes)[0]
    try:
        backend = NETWORK_BACKENDS[version]
    except KeyError:
        parser.error(f"unsupported Atomic NNUE version 0x{version:08X}")

    allowed = psutil.Process().cpu_affinity()
    affinity = args.affinity if args.affinity is not None else allowed[0]
    if affinity not in allowed:
        parser.error(f"CPU {affinity} is outside this process affinity: {allowed}")

    warmup = run_once(engine, net, args.depth, affinity, args.timeout, backend)
    print(
        f"Warmup: nodes={warmup.nodes} time_ms={warmup.elapsed_ms} nps={warmup.nps} "
        f"cpu={affinity}"
    )

    measurements: list[Measurement] = []
    for index in range(1, args.repetitions + 1):
        measurement = run_once(
            engine, net, args.depth, affinity, args.timeout, backend
        )
        if measurement.nodes != warmup.nodes:
            raise AssertionError(
                f"non-deterministic Atomic signature: {measurement.nodes} != {warmup.nodes}"
            )
        measurements.append(measurement)
        print(
            f"Run {index}/{args.repetitions}: nodes={measurement.nodes} "
            f"time_ms={measurement.elapsed_ms} nps={measurement.nps}"
        )

    median_nps = int(statistics.median(item.nps for item in measurements))
    median_ms = int(statistics.median(item.elapsed_ms for item in measurements))
    print(
        f"Atomic bench: backend={backend} signature={warmup.nodes} median_nps={median_nps} "
        f"median_time_ms={median_ms} binary_bytes={engine.stat().st_size}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
