#!/usr/bin/env python3
"""Prove that the production dispatcher still rejects private V3 wire files."""

from __future__ import annotations

import argparse
from pathlib import Path
import queue
import subprocess
import threading
from typing import Callable, List, Optional


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
        self.lines = queue.Queue()  # type: queue.Queue[Optional[str]]
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

    def read_until(self, predicate: Callable[[str], bool]) -> List[str]:
        output: List[str] = []
        while True:
            try:
                line = self.lines.get(timeout=self.timeout)
            except queue.Empty as exc:
                raise TimeoutError(f"timed out; last output: {output[-20:]}") from exc
            if line is None:
                raise RuntimeError(
                    f"engine exited with {self.process.poll()}; last output: {output[-20:]}"
                )
            output.append(line)
            if predicate(line):
                return output

    def ready(self) -> List[str]:
        self.send("isready")
        return self.read_until(lambda line: line == "readyok")

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


def run_engine(engine: Path, network: Path, mode: str, timeout: float) -> List[str]:
    output: List[str] = []
    with UciProcess(engine, timeout) as uci:
        uci.send("uci")
        output.extend(uci.read_until(lambda line: line == "uciok"))
        for name, value in (
            ("Threads", "1"),
            ("Hash", "16"),
            ("EvalFile", str(network)),
            ("Use NNUE", mode),
        ):
            uci.send(f"setoption name {name} value {value}")
            output.extend(uci.ready())
        uci.send("position startpos")
        uci.send("go nodes 1")
        output.extend(uci.read_until(lambda line: line.startswith("bestmove ")))
    return output


def require_private_v3_rejection(output: List[str], mode: str) -> None:
    if "bestmove (none)" not in output:
        raise AssertionError(
            f"private V3 network was not rejected in Use NNUE={mode}: {output[-30:]}"
        )
    accepted = "compatible Legacy Atomic V1 or AtomicNNUEV2"
    if not any(accepted in line for line in output):
        raise AssertionError(
            f"dispatcher did not report its two accepted backends in Use NNUE={mode}: "
            f"{output[-30:]}"
        )
    if any("NNUE evaluation using AtomicNNUEV3" in line for line in output):
        raise AssertionError(f"private V3 backend became active prematurely: {output[-30:]}")


def require_classical_fallback(output: List[str]) -> None:
    bestmoves = [line for line in output if line.startswith("bestmove ")]
    if len(bestmoves) != 1 or bestmoves[0] in {"bestmove (none)", "bestmove 0000"}:
        raise AssertionError(f"Use NNUE=false did not remain searchable: {output[-30:]}")
    if any("NNUE evaluation using" in line for line in output):
        raise AssertionError(f"Use NNUE=false unexpectedly activated NNUE: {output[-30:]}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--engine", required=True, type=Path)
    parser.add_argument("--v3-net", required=True, type=Path)
    parser.add_argument("--timeout", type=float, default=60.0)
    args = parser.parse_args()

    engine = args.engine.expanduser().resolve()
    network = args.v3_net.expanduser().resolve()
    for path, label in ((engine, "engine"), (network, "V3 fixture")):
        if not path.is_file():
            parser.error(f"{label} does not exist: {path}")

    for mode in ("true", "pure"):
        require_private_v3_rejection(
            run_engine(engine, network, mode, args.timeout), mode
        )
    require_classical_fallback(run_engine(engine, network, "false", args.timeout))

    print(
        "AtomicNNUEV3 dispatcher rejection passed: "
        "backend_count=2 modes=true,pure rejected false=searchable"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
