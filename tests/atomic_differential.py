#!/usr/bin/env python3
"""Deterministic UCI differential test against the frozen Fairy Atomic oracle."""

from __future__ import annotations

import argparse
import queue
import random
import re
import subprocess
import threading
from pathlib import Path


MOVE_COUNT_RE = re.compile(r"^(\S+):\s+(\d+)$")
NODES_RE = re.compile(r"^Nodes searched:\s+(\d+)$")
FEN_RE = re.compile(r"^Fen:\s+(.+)$")


class UciProcess:
    def __init__(self, executable: Path, timeout: float) -> None:
        self.executable = executable
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

        self.send("uci")
        self.read_until(lambda line: line == "uciok")
        self.send("setoption name UCI_Variant value atomic")
        self.send("setoption name Use NNUE value false")
        self.send("setoption name UCI_Chess960 value false")
        self.send("isready")
        self.read_until(lambda line: line == "readyok")

    def _read_output(self) -> None:
        assert self.process.stdout is not None
        for line in self.process.stdout:
            self.lines.put(line.rstrip("\r\n"))
        self.lines.put(None)

    def send(self, command: str) -> None:
        if self.process.poll() is not None:
            raise RuntimeError(f"{self.executable} exited with {self.process.returncode}")
        assert self.process.stdin is not None
        self.process.stdin.write(command + "\n")
        self.process.stdin.flush()

    def read_until(self, predicate) -> list[str]:
        output: list[str] = []
        while True:
            try:
                line = self.lines.get(timeout=self.timeout)
            except queue.Empty as exc:
                raise TimeoutError(
                    f"Timed out waiting for {self.executable}; last output: {output[-8:]}"
                ) from exc
            if line is None:
                raise RuntimeError(
                    f"{self.executable} exited with {self.process.poll()}; last output: {output[-8:]}"
                )
            output.append(line)
            if predicate(line):
                return output

    def set_position(self, moves: list[str]) -> None:
        suffix = " moves " + " ".join(moves) if moves else ""
        self.send("position startpos" + suffix)

    def perft(self, depth: int) -> tuple[dict[str, int], int]:
        self.send(f"go perft {depth}")
        output = self.read_until(lambda line: NODES_RE.match(line) is not None)
        divide: dict[str, int] = {}
        nodes = -1
        for line in output:
            if match := MOVE_COUNT_RE.match(line):
                divide[match.group(1)] = int(match.group(2))
            if match := NODES_RE.match(line):
                nodes = int(match.group(1))
        if nodes < 0:
            raise AssertionError(f"Missing perft total from {self.executable}")
        return divide, nodes

    def fen(self) -> str:
        self.send("d")
        self.send("isready")
        output = self.read_until(lambda line: line == "readyok")
        for line in output:
            if match := FEN_RE.match(line):
                return match.group(1)
        raise AssertionError(f"Missing FEN from {self.executable}: {output[-8:]}")

    def close(self) -> None:
        if self.process.poll() is None:
            self.send("quit")
            try:
                self.process.wait(timeout=self.timeout)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait()


def difference(left: dict[str, int], right: dict[str, int]) -> str:
    keys = sorted(set(left) | set(right))
    return "\n".join(
        f"{move}: candidate={left.get(move)} oracle={right.get(move)}"
        for move in keys
        if left.get(move) != right.get(move)
    )


def run(args: argparse.Namespace) -> None:
    candidate = UciProcess(args.candidate.resolve(), args.timeout)
    oracle = UciProcess(args.oracle.resolve(), args.timeout)
    rng = random.Random(args.seed)
    checked = 0

    try:
        for game in range(args.games):
            moves: list[str] = []
            for ply in range(args.plies):
                candidate.set_position(moves)
                oracle.set_position(moves)

                candidate_fen = candidate.fen()
                oracle_fen = oracle.fen()
                if candidate_fen != oracle_fen:
                    raise AssertionError(
                        f"FEN mismatch seed={args.seed} game={game} ply={ply}\n"
                        f"moves={' '.join(moves)}\n"
                        f"candidate={candidate_fen}\noracle={oracle_fen}"
                    )

                candidate_divide, candidate_nodes = candidate.perft(args.depth)
                oracle_divide, oracle_nodes = oracle.perft(args.depth)
                checked += 1

                if candidate_nodes != oracle_nodes or candidate_divide != oracle_divide:
                    raise AssertionError(
                        f"Perft mismatch seed={args.seed} game={game} ply={ply} depth={args.depth}\n"
                        f"moves={' '.join(moves)}\n"
                        f"candidate_nodes={candidate_nodes} oracle_nodes={oracle_nodes}\n"
                        f"{difference(candidate_divide, oracle_divide)}"
                    )

                legal_moves = sorted(candidate_divide)
                if not legal_moves:
                    break
                moves.append(rng.choice(legal_moves))
    finally:
        candidate.close()
        oracle.close()

    print(
        f"Atomic differential passed: {checked} positions, seed={args.seed}, "
        f"depth={args.depth}"
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("candidate", type=Path)
    parser.add_argument("oracle", type=Path)
    parser.add_argument("--games", type=int, default=4)
    parser.add_argument("--plies", type=int, default=40)
    parser.add_argument("--depth", type=int, default=2)
    parser.add_argument("--seed", type=int, default=20260710)
    parser.add_argument("--timeout", type=float, default=30.0)
    args = parser.parse_args()
    if args.games <= 0 or args.plies <= 0 or args.depth <= 0 or args.timeout <= 0:
        parser.error("games, plies, depth and timeout must be positive")
    return args


if __name__ == "__main__":
    run(parse_args())
