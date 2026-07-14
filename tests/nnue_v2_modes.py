#!/usr/bin/env python3
"""Black-box dual-backend and transactional-load gate for AtomicNNUEV2."""

from __future__ import annotations

import argparse
import hashlib
import queue
import shutil
import struct
import subprocess
import tempfile
import threading
from pathlib import Path
from typing import Callable


REPO_ROOT = Path(__file__).resolve().parents[1]
LEGACY_SHA256 = "99DC67EABF26A64FAEECA3A88B4C38597A840B8D4A874B9F2CF658C6F92A04A6"
V2_SHA256 = "A910EFADDC3450FC7D690C9BB1FC3EF70DDE02F96AD320304668F2B03D868053"
V2_VERSION = 0xA70C0002
V2_NETWORK_HASH = 0x3C1035AE
V2_TRANSFORMER_HASH = 0x5F2344B8
V2_STACK_HASH = 0x63337116
LEB128_MAGIC = b"COMPRESSED_LEB128"


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

    def export(self, destination: Path) -> list[str]:
        self.send(f"export_net {destination}")
        return self.read_until(
            lambda line: "Network saved successfully" in line or "Failed to export" in line
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


def require_search(output: list[str], label: str, backend: str) -> None:
    if output[-1] in {"bestmove (none)", "bestmove 0000"}:
        raise AssertionError(f"{label} did not search: {output}")
    if not any("NNUE evaluation using" in line and backend in line for line in output):
        raise AssertionError(f"{label} did not report backend {backend}: {output}")


def require_rejection(output: list[str], label: str) -> None:
    if output[-1] != "bestmove (none)":
        raise AssertionError(f"{label} was not rejected: {output}")
    if not any("compatible Legacy Atomic V1 or AtomicNNUEV2" in line for line in output):
        raise AssertionError(f"{label} omitted the dual-backend error: {output}")


def read_v2_offsets(path: Path) -> tuple[int, int, int, int]:
    with path.open("rb") as stream:
        version, network_hash, description_size = struct.unpack("<III", stream.read(12))
        if (version, network_hash) != (V2_VERSION, V2_NETWORK_HASH):
            raise AssertionError(
                f"wrong synthetic V2 identity: 0x{version:08X}/0x{network_hash:08X}"
            )
        transformer_offset = 12 + description_size
        stream.seek(transformer_offset)
        if struct.unpack("<I", stream.read(4))[0] != V2_TRANSFORMER_HASH:
            raise AssertionError("wrong synthetic V2 feature-transformer hash")
        first_leb_offset = stream.tell()
        first_payload_offset = -1
        first_count_offset = -1
        for block in range(3):
            if stream.read(len(LEB128_MAGIC)) != LEB128_MAGIC:
                raise AssertionError(f"wrong synthetic V2 LEB128 magic in block {block}")
            count_offset = stream.tell()
            byte_count = struct.unpack("<I", stream.read(4))[0]
            if block == 0:
                first_count_offset = count_offset
                first_payload_offset = stream.tell()
            stream.seek(byte_count, 1)
        first_stack_offset = stream.tell()
        if struct.unpack("<I", stream.read(4))[0] != V2_STACK_HASH:
            raise AssertionError("wrong synthetic V2 first layer-stack hash")
    return transformer_offset, first_leb_offset, first_count_offset, first_payload_offset


def mutate_u32(source: Path, destination: Path, offset: int) -> None:
    shutil.copyfile(source, destination)
    with destination.open("r+b") as stream:
        stream.seek(offset)
        value = struct.unpack("<I", stream.read(4))[0]
        stream.seek(offset)
        stream.write(struct.pack("<I", value ^ 1))


def mutate_byte(source: Path, destination: Path, offset: int, value: int) -> None:
    shutil.copyfile(source, destination)
    with destination.open("r+b") as stream:
        stream.seek(offset)
        stream.write(bytes((value,)))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--engine", type=Path, required=True)
    parser.add_argument("--legacy-net", type=Path, required=True)
    parser.add_argument("--v2-net", type=Path, required=True)
    parser.add_argument("--timeout", type=float, default=60.0)
    args = parser.parse_args()

    engine_path = args.engine.resolve()
    legacy_net = args.legacy_net.resolve()
    v2_net = args.v2_net.resolve()
    for path, label in (
        (engine_path, "engine"),
        (legacy_net, "Legacy network"),
        (v2_net, "V2 network"),
    ):
        if not path.is_file():
            parser.error(f"{label} does not exist: {path}")
    if sha256(legacy_net) != LEGACY_SHA256:
        parser.error("Legacy network SHA-256 mismatch")
    if sha256(v2_net) != V2_SHA256:
        parser.error("V2 network SHA-256 mismatch")

    transformer_offset, leb_offset, leb_count_offset, leb_payload_offset = read_v2_offsets(v2_net)
    with tempfile.TemporaryDirectory(prefix="atomic-v2-modes-") as temp_dir:
        root = Path(temp_dir)
        with UciProcess(engine_path, args.timeout) as engine:
            engine.send("uci")
            engine.read_until(lambda line: line == "uciok")
            engine.setoption("Threads", "4")
            engine.setoption("Hash", "16")

            engine.setoption("EvalFile", str(legacy_net))
            engine.setoption("Use NNUE", "true")
            require_search(engine.search(), "initial Legacy load", "Legacy Atomic V1")
            legacy_export = root / "legacy-export.nnue"
            if "Network saved successfully" not in engine.export(legacy_export)[-1]:
                raise AssertionError("Legacy export failed")
            if sha256(legacy_export) != LEGACY_SHA256:
                raise AssertionError("Legacy export changed bytes")

            engine.setoption("EvalFile", str(v2_net))
            for mode in ("true", "pure"):
                engine.setoption("Use NNUE", mode)
                require_search(engine.search(), f"V2 {mode}", "AtomicNNUEV2")
            v2_export = root / "v2-export.nnue"
            if "Network saved successfully" not in engine.export(v2_export)[-1]:
                raise AssertionError("V2 export failed")
            if sha256(v2_export) != V2_SHA256:
                raise AssertionError("V2 export changed bytes")

            mutations: tuple[tuple[str, Callable[[Path], None]], ...] = (
                ("wrong V2 version", lambda path: mutate_u32(v2_net, path, 0)),
                ("wrong V2 network hash", lambda path: mutate_u32(v2_net, path, 4)),
                (
                    "wrong V2 transformer hash",
                    lambda path: mutate_u32(v2_net, path, transformer_offset),
                ),
                (
                    "wrong V2 first stack hash",
                    lambda path: mutate_u32(
                        v2_net,
                        path,
                        _first_stack_offset(v2_net),
                    ),
                ),
                (
                    "wrong V2 LEB128 magic",
                    lambda path: mutate_byte(v2_net, path, leb_offset, ord("X")),
                ),
                (
                    "wrong V2 LEB128 byte count",
                    lambda path: mutate_u32(v2_net, path, leb_count_offset),
                ),
                (
                    "non-canonical V2 SLEB128",
                    lambda path: mutate_byte(v2_net, path, leb_payload_offset, 0x80),
                ),
            )

            for index, (label, create) in enumerate(mutations):
                invalid = root / f"invalid-{index}.nnue"
                create(invalid)
                engine.setoption("EvalFile", str(invalid))
                engine.setoption("Use NNUE", "true")
                require_rejection(engine.search(), label)

                if index == 0:
                    rollback = root / "rollback-export.nnue"
                    if "Network saved successfully" not in engine.export(rollback)[-1]:
                        raise AssertionError("active V2 could not export after rejected load")
                    if sha256(rollback) != V2_SHA256:
                        raise AssertionError("rejected load mutated the active V2 backend")

                invalid.unlink()
                engine.setoption("EvalFile", str(v2_net))
                require_search(engine.search(), f"V2 recovery after {label}", "AtomicNNUEV2")

            trailing = root / "trailing.nnue"
            shutil.copyfile(v2_net, trailing)
            with trailing.open("ab") as stream:
                stream.write(b"\0")
            engine.setoption("EvalFile", str(trailing))
            require_rejection(engine.search(), "trailing V2 byte")

            truncated = root / "truncated.nnue"
            with v2_net.open("rb") as source, truncated.open("xb") as destination:
                shutil.copyfileobj(source, destination, length=1024 * 1024)
            with truncated.open("r+b") as stream:
                stream.truncate(v2_net.stat().st_size // 2)
            engine.setoption("EvalFile", str(truncated))
            require_rejection(engine.search(), "truncated V2 payload")

            for path, backend in (
                (v2_net, "AtomicNNUEV2"),
                (legacy_net, "Legacy Atomic V1"),
                (v2_net, "AtomicNNUEV2"),
            ):
                engine.setoption("EvalFile", str(path))
                engine.setoption("Use NNUE", "true")
                require_search(engine.search(), f"backend switch to {backend}", backend)

    print(
        "AtomicNNUEV2 mode gate passed: byte-exact V1/V2 export, strict rejection, "
        "transactional rollback, Threads=4, and V1->V2->V1->V2 switching"
    )
    return 0


def _first_stack_offset(path: Path) -> int:
    with path.open("rb") as stream:
        _version, _network_hash, description_size = struct.unpack("<III", stream.read(12))
        stream.seek(description_size + 4, 1)
        for _ in range(3):
            if stream.read(len(LEB128_MAGIC)) != LEB128_MAGIC:
                raise AssertionError("invalid V2 LEB block while locating first stack")
            byte_count = struct.unpack("<I", stream.read(4))[0]
            stream.seek(byte_count, 1)
        return stream.tell()


if __name__ == "__main__":
    raise SystemExit(main())
