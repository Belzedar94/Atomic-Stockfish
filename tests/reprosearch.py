#!/usr/bin/env python3
"""Verify deterministic Atomic search with the frozen Legacy Atomic V1 net."""

from __future__ import annotations

import argparse
import queue
import re
import subprocess
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Callable


VOLATILE = re.compile(r"\s(?:time|nps|hashfull|tbhits)\s+\d+")


@dataclass(frozen=True)
class Result:
    info: str
    bestmove: str


class Engine:
    def __init__(self, executable: Path, net: Path, mode: str, timeout: float) -> None:
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
        assert self.process.stdin is not None and self.process.stdout is not None
        self.lines: queue.Queue[str | None] = queue.Queue()
        threading.Thread(target=self._reader, daemon=True).start()
        self.send("uci")
        self.read_until(lambda line: line == "uciok")
        self.send("setoption name Threads value 1")
        self.send("setoption name Hash value 16")
        self.send(f"setoption name EvalFile value {net}")
        self.send(f"setoption name Use NNUE value {mode}")
        self.ready()

    def _reader(self) -> None:
        assert self.process.stdout is not None
        for line in self.process.stdout:
            self.lines.put(line.rstrip("\r\n"))
        self.lines.put(None)

    def send(self, command: str) -> None:
        assert self.process.stdin is not None
        self.process.stdin.write(command + "\n")
        self.process.stdin.flush()

    def read_until(self, predicate: Callable[[str], bool]) -> list[str]:
        output: list[str] = []
        while True:
            try:
                line = self.lines.get(timeout=self.timeout)
            except queue.Empty as exc:
                raise TimeoutError(f"search timed out; output={output[-10:]}") from exc
            if line is None:
                raise RuntimeError(f"engine exited with {self.process.poll()}: {output[-10:]}")
            output.append(line)
            if predicate(line):
                return output

    def ready(self) -> None:
        self.send("isready")
        self.read_until(lambda line: line == "readyok")

    def clear(self) -> None:
        self.send("ucinewgame")
        self.ready()

    def search(self, position: str, nodes: int) -> Result:
        self.send(f"position {position}")
        self.send(f"go nodes {nodes}")
        output = self.read_until(lambda line: line.startswith("bestmove "))
        scored = [line for line in output if line.startswith("info ") and " score " in line]
        if not scored:
            raise AssertionError(f"no scored info line: {output}")
        normalized = VOLATILE.sub("", scored[-1])
        return Result(normalized, output[-1])

    def close(self) -> None:
        if self.process.poll() is None:
            self.send("quit")
            self.process.wait(timeout=self.timeout)


def run_sequence(engine: Engine, nodes: int) -> tuple[Result, ...]:
    engine.clear()
    return (
        engine.search("startpos", nodes),
        engine.search("startpos moves e2e4 e7e6", nodes),
        engine.search(
            "fen rn2kb1r/1pp1p2p/p2q1pp1/3P4/2P3b1/4PN2/PP3PPP/R2QKB1R b KQkq - 0 1",
            nodes,
        ),
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--engine", type=Path, required=True)
    parser.add_argument("--eval-file", type=Path, required=True)
    parser.add_argument("--use-nnue", choices=("true", "pure"), default="true")
    parser.add_argument("--iterations", type=int, default=12)
    parser.add_argument("--timeout", type=float, default=60.0)
    args = parser.parse_args()

    engine_path = args.engine.resolve()
    net_path = args.eval_file.resolve()
    if not engine_path.is_file() or not net_path.is_file():
        parser.error("engine and eval file must exist")
    if args.iterations <= 0:
        parser.error("iterations must be positive")

    engine = Engine(engine_path, net_path, args.use_nnue, args.timeout)
    try:
        for index in range(1, args.iterations + 1):
            nodes = 100 * 3**index // 2**index
            first = run_sequence(engine, nodes)
            second = run_sequence(engine, nodes)
            if first != second:
                raise AssertionError(
                    f"non-reproducible search at {nodes} nodes\nfirst={first}\nsecond={second}"
                )
            print(f"PASS reproducible Atomic search: nodes={nodes}")
    finally:
        engine.close()

    print(f"Atomic reprosearch passed: {args.iterations}/{args.iterations}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
