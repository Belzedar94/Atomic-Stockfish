#!/usr/bin/env python3
"""Black-box contract tests for Atomic-Stockfish NNUE mode selection.

The important failure property is deliberately tested in-process: an invalid
Legacy Atomic V1 network must reject ``go`` while leaving the protocol alive,
and switching to ``Use NNUE=false`` must make the same engine searchable again.
"""

from __future__ import annotations

import argparse
import hashlib
import queue
import struct
import subprocess
import tempfile
import threading
from pathlib import Path
from typing import Callable


REPO_ROOT = Path(__file__).resolve().parents[1]
EXPECTED_NET_SHA256 = "99dc67eabf26a64faeeca3a88b4c38597a840b8d4a874b9f2cf658c6f92a04a6"
EXPECTED_VERSION = 0x7AF32F20
EXPECTED_ARCHITECTURE_HASH = 0x3C103E72
EXPECTED_TRANSFORMER_HASH = 0x5F2348B8
EXPECTED_TRANSFORMER_OFFSET = 92
EXPECTED_FIRST_STACK_OFFSET = 47_580_256
EXPECTED_STACK_HASH = 0x633376CA
EXPECTED_STACK_SIZE = 17_640
EXPECTED_STACK_COUNT = 8


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
                raise TimeoutError(f"timed out; last output: {output[-10:]}") from exc
            if line is None:
                raise RuntimeError(
                    f"engine exited with {self.process.poll()}; last output: {output[-10:]}"
                )
            output.append(line)
            if predicate(line):
                return output

    def ready(self) -> list[str]:
        self.send("isready")
        return self.read_until(lambda line: line == "readyok")

    def setoption(self, name: str, value: str) -> None:
        self.send(f"setoption name {name} value {value}")
        self.ready()

    def search(self) -> list[str]:
        self.send("position startpos")
        self.send("go depth 1")
        return self.read_until(lambda line: line.startswith("bestmove "))

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


def require_search(output: list[str], mode: str) -> None:
    bestmove = output[-1].split(maxsplit=1)
    if len(bestmove) != 2 or bestmove[1] in {"(none)", "0000"}:
        raise AssertionError(f"Use NNUE={mode} did not search: {output}")


def require_rejected_network(engine: UciProcess, path: Path, label: str) -> None:
    engine.setoption("EvalFile", str(path))
    engine.setoption("Use NNUE", "true")
    rejected = engine.search()
    if rejected[-1] != "bestmove (none)":
        raise AssertionError(f"{label} network was not rejected: {rejected}")
    if not any("compatible Legacy Atomic V1" in line for line in rejected):
        raise AssertionError(f"{label} rejection omitted the architecture error: {rejected}")
    engine.ready()


def write_u32_mutation(source: bytes, destination: Path, offset: int) -> None:
    mutated = bytearray(source)
    original = struct.unpack_from("<I", mutated, offset)[0]
    struct.pack_into("<I", mutated, offset, original ^ 1)
    destination.write_bytes(mutated)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--engine", type=Path, default=REPO_ROOT / "src" / "atomic-stockfish.exe"
    )
    parser.add_argument("--eval-file", type=Path, required=True)
    parser.add_argument("--timeout", type=float, default=30.0)
    args = parser.parse_args()

    executable = args.engine.resolve()
    eval_file = args.eval_file.resolve()
    if not executable.is_file():
        parser.error(f"engine does not exist: {executable}")
    if not eval_file.is_file():
        parser.error(f"network does not exist: {eval_file}")
    network_bytes = eval_file.read_bytes()
    if hashlib.sha256(network_bytes).hexdigest() != EXPECTED_NET_SHA256:
        parser.error(
            "network SHA-256 does not match atomic_run3b_e202_l05.nnue: "
            f"expected {EXPECTED_NET_SHA256}"
        )

    if len(network_bytes) < 12:
        raise AssertionError("frozen Legacy Atomic V1 network has no complete header")
    version, architecture_hash, description_size = struct.unpack_from("<III", network_bytes)
    transformer_offset = 12 + description_size
    if version != EXPECTED_VERSION:
        raise AssertionError(f"unexpected Legacy Atomic V1 version: 0x{version:08X}")
    if architecture_hash != EXPECTED_ARCHITECTURE_HASH:
        raise AssertionError(
            f"unexpected Legacy Atomic V1 architecture hash: 0x{architecture_hash:08X}"
        )
    if transformer_offset != EXPECTED_TRANSFORMER_OFFSET:
        raise AssertionError(
            f"unexpected feature-transformer offset: {transformer_offset}, "
            f"expected {EXPECTED_TRANSFORMER_OFFSET}"
        )
    if len(network_bytes) < transformer_offset + 4:
        raise AssertionError("frozen Legacy Atomic V1 network has no transformer header")
    transformer_hash = struct.unpack_from("<I", network_bytes, transformer_offset)[0]
    if transformer_hash != EXPECTED_TRANSFORMER_HASH:
        raise AssertionError(
            f"unexpected Legacy Atomic V1 transformer hash: 0x{transformer_hash:08X}"
        )
    expected_size = EXPECTED_FIRST_STACK_OFFSET + EXPECTED_STACK_COUNT * EXPECTED_STACK_SIZE
    if len(network_bytes) != expected_size:
        raise AssertionError(
            f"unexpected Legacy Atomic V1 size: {len(network_bytes)}, expected {expected_size}"
        )
    for stack in range(EXPECTED_STACK_COUNT):
        stack_offset = EXPECTED_FIRST_STACK_OFFSET + stack * EXPECTED_STACK_SIZE
        stack_hash = struct.unpack_from("<I", network_bytes, stack_offset)[0]
        if stack_hash != EXPECTED_STACK_HASH:
            raise AssertionError(
                f"unexpected layer-stack {stack} hash at offset {stack_offset}: "
                f"0x{stack_hash:08X}"
            )

    with tempfile.TemporaryDirectory(prefix="atomic-nnue-invalid-") as temp_dir:
        temp_path = Path(temp_dir)
        invalid = temp_path / "not-a-network.nnue"
        invalid.write_bytes(b"not a Legacy Atomic V1 network\n")

        wrong_version = temp_path / "wrong-version.nnue"
        write_u32_mutation(network_bytes, wrong_version, 0)

        wrong_architecture = temp_path / "wrong-architecture.nnue"
        write_u32_mutation(network_bytes, wrong_architecture, 4)

        wrong_transformer = temp_path / "wrong-transformer.nnue"
        write_u32_mutation(network_bytes, wrong_transformer, transformer_offset)

        wrong_layer_stack = temp_path / "wrong-layer-stack.nnue"
        write_u32_mutation(
            network_bytes, wrong_layer_stack, EXPECTED_FIRST_STACK_OFFSET
        )

        trailing_byte = temp_path / "trailing-byte.nnue"
        trailing_byte.write_bytes(network_bytes + b"\0")

        directed_invalid_networks = (
            ("wrong version", wrong_version),
            ("wrong architecture hash", wrong_architecture),
            ("wrong transformer hash", wrong_transformer),
            ("wrong layer-stack hash", wrong_layer_stack),
            ("trailing byte", trailing_byte),
        )

        with UciProcess(executable, args.timeout) as engine:
            engine.send("uci")
            uci = engine.read_until(lambda line: line == "uciok")
            expected = (
                "option name Use NNUE type combo default true "
                "var false var true var pure"
            )
            if expected not in uci:
                raise AssertionError(f"missing exact Use NNUE option: {uci}")
            if "option name VariantPath type string default <empty>" not in uci:
                raise AssertionError(f"missing VariantPath compatibility option: {uci}")
            if "option name UCI_Variant type combo default atomic var atomic" not in uci:
                raise AssertionError(f"UCI_Variant is not fixed to atomic: {uci}")

            engine.setoption("Threads", "1")
            engine.setoption("Hash", "16")
            # variantfishtest_new1.py always sends this option when variants.ini
            # is supplied. Atomic-only rules are compiled in, so even a missing
            # path must be accepted as a harmless compatibility input.
            engine.setoption("VariantPath", str(Path(temp_dir) / "missing-variants.ini"))
            engine.setoption("UCI_Variant", "atomic")
            engine.setoption("EvalFile", str(invalid))

            for mode in ("true", "pure"):
                engine.setoption("Use NNUE", mode)
                rejected = engine.search()
                if rejected[-1] != "bestmove (none)":
                    raise AssertionError(f"invalid net was not rejected in {mode}: {rejected}")
                if not any("compatible Legacy Atomic V1" in line for line in rejected):
                    raise AssertionError(f"missing architecture error in {mode}: {rejected}")
                # The same process must remain responsive after the rejection.
                engine.ready()

            engine.setoption("Use NNUE", "false")
            require_search(engine.search(), "false")

            engine.setoption("EvalFile", str(eval_file))

            exported = Path(temp_dir) / "exported-legacy-atomic-v1.nnue"
            engine.send(f"export_net {exported}")
            export_output = engine.read_until(
                lambda line: "Network saved successfully" in line
                or "Failed to export" in line
            )
            if "Network saved successfully" not in export_output[-1]:
                raise AssertionError(f"network export failed: {export_output}")

            def sha256(path: Path) -> str:
                return hashlib.sha256(path.read_bytes()).hexdigest()

            if sha256(exported) != sha256(eval_file):
                raise AssertionError("exported Legacy Atomic V1 network is not byte-exact")

            # Exercise the exported bytes through the normal loader too.
            engine.setoption("EvalFile", str(exported))

            for label, mutated in directed_invalid_networks:
                require_rejected_network(engine, mutated, label)
                engine.setoption("EvalFile", str(exported))
                recovered = engine.search()
                require_search(recovered, f"true after {label}")
                if not any("NNUE evaluation using" in line for line in recovered):
                    raise AssertionError(
                        f"valid net was not reported after {label} rejection: {recovered}"
                    )

            truncated = Path(temp_dir) / "truncated-valid-header.nnue"
            with eval_file.open("rb") as source, truncated.open("wb") as destination:
                destination.write(source.read(eval_file.stat().st_size // 2))
            engine.setoption("EvalFile", str(truncated))
            engine.setoption("Use NNUE", "true")
            truncated_rejection = engine.search()
            if truncated_rejection[-1] != "bestmove (none)":
                raise AssertionError(
                    f"truncated network was not rejected: {truncated_rejection}"
                )

            # Returning to the previously loaded path does not reload it. A
            # successful search therefore proves the failed load was
            # transactional and did not overwrite the live network.
            engine.setoption("EvalFile", str(exported))
            for mode in ("true", "pure"):
                engine.setoption("Use NNUE", mode)
                output = engine.search()
                require_search(output, mode)
                if not any("NNUE evaluation using" in line for line in output):
                    raise AssertionError(f"valid net was not reported in {mode}: {output}")

    print("NNUE mode contract passed: false, true, pure, and nonfatal invalid-net rejection")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
