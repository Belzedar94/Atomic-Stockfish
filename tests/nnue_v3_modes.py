#!/usr/bin/env python3
"""Black-box production acceptance gate for AtomicNNUEV3.

The frozen synthetic V3 fixture is authenticated before it reaches the engine.
One process then proves that the production dispatcher can search with the
exact network in ``true`` and data-generation-only ``pure`` mode at every
supported CI thread count, that classical evaluation remains available, and
that V3 export/import is byte exact.
"""

from __future__ import annotations

import argparse
import hashlib
from pathlib import Path
import queue
import re
import struct
import subprocess
import tempfile
import threading
from typing import Callable


V3_SHA256 = "00E46223822D06D7927E884EEC10739BA19EF8DD82A6E262F627D361658080C2"
V3_SIZE = 77_349_879
V3_VERSION = 0xA70C0003
V3_NETWORK_HASH = 0x0CF9A484
THREAD_COUNTS = (1, 2, 4, 8)
WIDE_TRACE_INTERNAL = 31_506
WIDE_TRACE_WHITE_PAWNS = 151.47
INTERNAL_TRACE_RE = re.compile(
    r"^NNUE evaluation\s+([+-]?\d+)\s+\(side to move, internal units\)$"
)
FINAL_TRACE_RE = re.compile(
    r"^Final evaluation\s+([+-]?(?:\d+(?:\.\d*)?|\.\d+))\s+"
    r"\(white side\)\s+\[Use NNUE=true\]$"
)


class UciProcess:
    def __init__(self, executable: Path, timeout: float) -> None:
        self.timeout = timeout
        self.process = subprocess.Popen(
            [str(executable)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
        )
        assert self.process.stdin is not None
        assert self.process.stdout is not None
        self.lines: queue.Queue[str | None] = queue.Queue()
        self.reader = threading.Thread(target=self._read_output, daemon=True)
        self.reader.start()

    def _read_output(self) -> None:
        assert self.process.stdout is not None
        for line in self.process.stdout:
            self.lines.put(line.rstrip("\r\n"))
        self.lines.put(None)

    def send(self, command: str) -> None:
        if self.process.poll() is not None:
            raise RuntimeError(f"engine exited with {self.process.returncode}")
        assert self.process.stdin is not None
        self.process.stdin.write(command + "\n")
        self.process.stdin.flush()

    def read_until(self, predicate: Callable[[str], bool]) -> list[str]:
        output: list[str] = []
        while True:
            try:
                line = self.lines.get(timeout=self.timeout)
            except queue.Empty as exc:
                raise TimeoutError(
                    f"timed out waiting for engine; last output: {output[-20:]}"
                ) from exc
            if line is None:
                raise RuntimeError(
                    f"engine exited with {self.process.poll()}; "
                    f"last output: {output[-20:]}"
                )
            output.append(line)
            if predicate(line):
                return output

    def ready(self) -> list[str]:
        self.send("isready")
        return self.read_until(lambda line: line == "readyok")

    def setoption(self, name: str, value: str) -> list[str]:
        self.send(f"setoption name {name} value {value}")
        return self.ready()

    def search(self) -> list[str]:
        self.send("position startpos")
        self.send("go nodes 1")
        return self.read_until(lambda line: line.startswith("bestmove "))

    def trace(self) -> list[str]:
        self.send("position startpos")
        self.send("eval")
        return self.read_until(lambda line: line.startswith("Final evaluation"))

    def export(self, destination: Path) -> list[str]:
        self.send(f"export_net {destination}")
        return self.read_until(
            lambda line: "Network saved successfully" in line
            or "Failed to export" in line
        )

    def close(self) -> None:
        if self.process.poll() is not None:
            return
        try:
            self.send("quit")
            self.process.wait(timeout=self.timeout)
        except (BrokenPipeError, subprocess.TimeoutExpired):
            self.process.kill()
            self.process.wait()

    def __enter__(self) -> "UciProcess":
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest().upper()


def authenticate_v3(path: Path, expected_sha256: str) -> None:
    if path.stat().st_size != V3_SIZE:
        raise AssertionError(
            f"AtomicNNUEV3 fixture size mismatch: {path.stat().st_size} != {V3_SIZE}"
        )
    actual_sha256 = sha256(path)
    if actual_sha256 != expected_sha256:
        raise AssertionError(
            f"AtomicNNUEV3 fixture SHA-256 mismatch: {actual_sha256} != "
            f"{expected_sha256}"
        )
    with path.open("rb") as stream:
        header = stream.read(12)
    if len(header) != 12:
        raise AssertionError("AtomicNNUEV3 fixture has no complete header")
    version, network_hash, _description_size = struct.unpack("<III", header)
    if (version, network_hash) != (V3_VERSION, V3_NETWORK_HASH):
        raise AssertionError(
            "AtomicNNUEV3 fixture identity mismatch: "
            f"0x{version:08X}/0x{network_hash:08X}"
        )


def require_v3_search(output: list[str], *, mode: str, threads: int) -> None:
    bestmoves = [line for line in output if line.startswith("bestmove ")]
    if len(bestmoves) != 1 or bestmoves[0] in {"bestmove (none)", "bestmove 0000"}:
        raise AssertionError(
            f"AtomicNNUEV3 did not search in Use NNUE={mode}, Threads={threads}: "
            f"{output[-30:]}"
        )
    if not any("NNUE evaluation using AtomicNNUEV3" in line for line in output):
        raise AssertionError(
            f"dispatcher did not report AtomicNNUEV3 in Use NNUE={mode}, "
            f"Threads={threads}: {output[-30:]}"
        )


def require_classical_search(output: list[str]) -> None:
    bestmoves = [line for line in output if line.startswith("bestmove ")]
    if len(bestmoves) != 1 or bestmoves[0] in {"bestmove (none)", "bestmove 0000"}:
        raise AssertionError(f"Use NNUE=false did not search: {output[-30:]}")
    if any("NNUE evaluation using" in line for line in output):
        raise AssertionError(f"Use NNUE=false activated an NNUE backend: {output[-30:]}")


def require_wide_v3_trace(output: list[str]) -> None:
    internal = [
        int(match.group(1))
        for line in output
        if (match := INTERNAL_TRACE_RE.fullmatch(line)) is not None
    ]
    final = [
        float(match.group(1))
        for line in output
        if (match := FINAL_TRACE_RE.fullmatch(line)) is not None
    ]
    if internal != [WIDE_TRACE_INTERNAL]:
        raise AssertionError(
            "AtomicNNUEV3 wide trace did not saturate without wrapping: "
            f"internal={internal}, output={output[-30:]}"
        )
    if final != [WIDE_TRACE_WHITE_PAWNS]:
        raise AssertionError(
            "AtomicNNUEV3 trace and search evaluation disagree: "
            f"final={final}, output={output[-30:]}"
        )


def require_export(engine: UciProcess, destination: Path, expected_sha256: str) -> None:
    output = engine.export(destination)
    if "Network saved successfully" not in output[-1]:
        raise AssertionError(f"AtomicNNUEV3 export failed: {output[-20:]}")
    authenticate_v3(destination, expected_sha256)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--engine", required=True, type=Path)
    parser.add_argument("--v3-net", required=True, type=Path)
    parser.add_argument("--v3-sha256", default=V3_SHA256)
    parser.add_argument("--timeout", type=float, default=180.0)
    args = parser.parse_args()

    engine_path = args.engine.expanduser().resolve()
    network_path = args.v3_net.expanduser().resolve()
    for path, label in ((engine_path, "engine"), (network_path, "V3 fixture")):
        if not path.is_file():
            parser.error(f"{label} does not exist: {path}")
    expected_sha256 = args.v3_sha256.upper()
    if len(expected_sha256) != 64 or any(
        character not in "0123456789ABCDEF" for character in expected_sha256
    ):
        parser.error("--v3-sha256 must be exactly 64 hexadecimal characters")
    authenticate_v3(network_path, expected_sha256)

    with tempfile.TemporaryDirectory(prefix="atomic-v3-modes-") as temp_dir:
        root = Path(temp_dir)
        first_export = root / "atomic-v3-export.nnue"
        second_export = root / "atomic-v3-reimport-export.nnue"

        with UciProcess(engine_path, args.timeout) as engine:
            engine.send("uci")
            uci = engine.read_until(lambda line: line == "uciok")
            expected_option = (
                "option name Use NNUE type combo default true "
                "var false var true var pure"
            )
            if expected_option not in uci:
                raise AssertionError(f"missing exact Use NNUE option: {uci}")

            engine.setoption("Hash", "16")
            engine.setoption("EvalFile", str(network_path))
            engine.setoption("Use NNUE", "true")
            require_wide_v3_trace(engine.trace())
            for threads in THREAD_COUNTS:
                engine.setoption("Threads", str(threads))
                for mode in ("true", "pure"):
                    engine.setoption("Use NNUE", mode)
                    require_v3_search(engine.search(), mode=mode, threads=threads)

            engine.setoption("Use NNUE", "false")
            require_classical_search(engine.search())

            engine.setoption("Use NNUE", "true")
            require_export(engine, first_export, expected_sha256)

            # A different path forces the normal transactional loader to parse
            # the exported bytes instead of relying on the live in-memory net.
            engine.setoption("EvalFile", str(first_export))
            require_v3_search(engine.search(), mode="true", threads=THREAD_COUNTS[-1])
            require_export(engine, second_export, expected_sha256)

    print(
        "AtomicNNUEV3 production gate passed: exact load/export/import, "
        "wide trace/search saturation, modes=false,true,pure, "
        "threads=1,2,4,8, backend_count=3"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
